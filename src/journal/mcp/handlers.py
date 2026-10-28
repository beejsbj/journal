"""The tools of ``journal mcp``, one function each, with no MCP in them: ``server`` registers them with the SDK, and
the tests call them directly.

Each tool is a call into what the engine and the other faces already do, never a second model of the journal:

* reads are ``journal serve``'s (``today.collect``, ``serve.api.filter_entries``, ``serve.document``), so an MCP
  client sees what the web app sees, and nothing the web app may not: no chat transcript, no privacy results, no
  packs, no notebook (``tests/test_mcp_fence.py`` fences the imports; ``tests/test_mcp.py`` runs every tool with
  ``sqlite3.connect`` made to fail);
* his words are only appended, through ``capture.capture_web`` under the ledger lock (the web app's path, with
  ``via="mcp"``), so a reply to an open prompt folds the chat's prompt as the web's does, and anything else becomes
  a /j note of its own;
* ``/x`` holds: text that starts with the off-the-record command, or has a line that does, is refused and nothing
  is written (as the chat takes it); a line shaped like the engine's own marker (``%% journal … %%``) is refused
  too, since it could pass for the companion's voice;
* a send is a spawned ``journal send <id>`` (what ``/start <id>`` and the web app's Send do), never run here;
* the companion's voice stays hers: ``journal_entry`` labels every block with whose it is, and ``journal_add_note``
  files a note under the companion's notes heading (``capture.add_companion_note``), never among his words.

A refusal is ``Refused`` with the line the client is told; nothing has been written when it is raised.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .. import audio, capture, commands, entries, ledger, library, programview, today
from ..config import Config
from ..serve import api, document
from ..serve.writes import TEXT_LIMIT, spawn_journal

LOCK_WAIT = 5.0      # seconds a write waits for the ledger lock (as a web write), then it is refused as busy
TODAY_CHARS = 100_000   # the today block here is not cut to ``today.max_chars``: that is the chat's turn budget
LIST_LIMIT = api.LIST_LIMIT
QUERY_LIMIT = 200    # characters of a search (as ``/entries?q``)


class Refused(Exception):
    """A tool call the journal turns away; ``str(e)`` is what the client is told. Nothing was written."""


# ── shared checks ────────────────────────────────────────────────────────────

def _text(value, what: str) -> str:
    """A text argument with one line ending (``\\n``): the engine's marker pattern knows only that one, and a file
    is read back with universal newlines, so a ``\\r`` the checks passed over would become a line."""
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"{what} is empty: nothing was saved")
    if len(value) > TEXT_LIMIT:
        raise Refused(f"{what} is at most {TEXT_LIMIT:,} characters: nothing was saved")
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _not_off_record(cfg: Config, text: str) -> None:
    """``/x`` holds here as in the chat: text that starts with the command, or has a line that does (the chat takes a
    merged burst with a ``/x`` on a later line as off the record, ``capture.opt_out_inside``), is refused whole."""
    if capture.opt_out(cfg, text) is not None:
        raise Refused("this starts with the off-the-record command, so it stays out of the journal: nothing was saved")
    if capture.opt_out_inside(cfg, text):
        raise Refused("a line of this starts with the off-the-record command, so none of it goes into the journal: "
                      "nothing was saved")


def _no_placeholder(text: str, what: str) -> None:
    """The engine's transcript-pending line is not his words (the web refuses it too): no recording will fill it,
    and the editor would lock the block for good as one still being transcribed."""
    if audio.TRANSCRIBING in text:
        raise Refused(f"{what} has the journal's line for a recording still being transcribed, which cannot be part "
                      "of his words: nothing was saved")


def _no_marker(text: str, what: str) -> None:
    if entries.ENGINE_MARK.search(text):
        raise Refused(f"{what} has a line shaped like `%% journal … %%`, the engine's own marker (it could pass for "
                      "the other voice or a program's phase): nothing was saved")


def _date(value, name: str) -> date | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise Refused(f"{name} must be a date like 2026-10-14 (got {value!r})") from None


def _bounded(write: Callable):
    """Run ``write()`` under the lock deadline: a lock not had in ``LOCK_WAIT`` seconds, or a filesystem that refuses
    the write, is a refusal (the journal is as it was)."""
    try:
        with ledger.deadline(LOCK_WAIT):
            return write()
    except ledger.LockBusy:
        raise Refused("the journal is busy (a tick or another write holds it): nothing was saved; try again "
                      "in a moment") from None
    except OSError as e:
        raise Refused(f"the journal could not be written ({type(e).__name__}): nothing was saved") from None


# ── reads ────────────────────────────────────────────────────────────────────

def today_text(cfg: Config, now: datetime) -> str:
    """The today block (``today.collect`` rendered, as the companion gets it at a thread's start), with the entry
    stems ``journal_entry`` opens. ``today.enabled`` and ``today.max_chars`` are the chat's switch and turn budget for
    injecting it; asking for it here is always answered, whole (``render_data`` returns "" for a day with nothing to
    say and for a block cut to nothing, which only the first is: so the empty day is decided here)."""
    data = today.collect(cfg, now)
    if not today.lines_of(data):
        return today.header_of(data) + "\n- nothing recorded yet today"
    return today.render_data(data, TODAY_CHARS)


def entry_list(cfg: Config, now: datetime, *, query: str | None = None, kind: str | None = None,
               date_from: str | None = None, date_to: str | None = None, limit: int = 20, cursor: int = 0) -> dict:
    """His entries, newest first, as ``GET /entries`` gives them (EntrySummary: stem, date, kind, title, the first
    line of his words, flags). ``query`` is a case-insensitive substring of **his words only**: the companion's
    replies are never searched. ``kind`` is the entry's id: a practice (``evening``), ``j`` (a /j note), ``note``
    (an entry the companion started), or a program's id for its sessions."""
    start, end = _date(date_from, "date_from"), _date(date_to, "date_to")
    if start and end and start > end:
        raise Refused("date_from is after date_to")
    q = (query or "").strip()
    if len(q) > QUERY_LIMIT:
        raise Refused(f"query is at most {QUERY_LIMIT} characters")
    try:
        limit, cursor = max(1, min(LIST_LIMIT, int(limit))), max(0, int(cursor))
    except (TypeError, ValueError):
        raise Refused("limit and cursor are whole numbers") from None
    found = api.filter_entries(cfg, start, end, kind=(kind or "").strip() or None, q=q)
    lib = library.load(cfg)
    items = [s for s in (document.summary(cfg, path, lib) for path, _ in found[cursor:cursor + limit]) if s]
    return {"items": items, "next": cursor + limit if cursor + limit < len(found) else None}


def _entry_path(cfg: Config, stem) -> Path:
    path = document.entry_file(cfg, stem) if isinstance(stem, str) else None
    if path is None:
        raise Refused(f"no journal entry {stem!r} (stems look like 2026-10-14-evening; journal_entries lists them)")
    return path


def entry_document(cfg: Config, stem: str) -> dict:
    """One entry as the web app's two-voice document (``serve.document.document``): every block says whose it is
    (``kind``: ``his`` or ``hers``)."""
    return document.document(cfg, _entry_path(cfg, stem), library.load(cfg))


def _quoted(text: str) -> list[str]:
    """A block's text, every line behind ``> ``: a label line is never quoted, so nothing inside a block (her reply
    included) can pass for a label of the other voice."""
    return [f"> {line}" if line else ">" for line in (text or "").split("\n")] or [">"]


def render_document(doc: dict) -> str:
    """The two-voice document as text for a model to read: each block under a label line that names its voice,
    ``**<his name> (his words)**`` or ``**<her name> (companion, not his words)**``, its text quoted below it as
    written. The engine's own parts (the prompt, the summary) are labelled as the engine's."""
    his, hers = doc["voices"]["his"], doc["voices"]["hers"]
    he, she = f"**{his} (his words)**", f"**{hers} (companion, not his words)**"
    out = [f"# {doc['title']}", "",
           f"Entry {doc['stem']} · {doc['date']}" + (f" · practice {doc['practice']['id']}" if doc.get("practice")
                                                    else "") + (f" · session {doc['session']}" if doc.get("session")
                                                                else ""),
           f"Two voices, kept apart: blocks under {he} are {his}'s own words, verbatim; blocks under {she} are "
           f"{hers}'s. Each block's text is quoted below its label line exactly as the entry has it.", ""]
    prompt = doc.get("prompt") or {}
    asked, bank = prompt.get("asked") or [], prompt.get("questions") or []
    if prompt.get("opener") or asked or bank:
        out += ["## Prompt (sent by the journal)", ""]
        if prompt.get("opener"):
            by = f", written by {hers}" if str(prompt.get("opener_source") or "").lower() == hers.lower() else ""
            out += [f"Opener{by} ({prompt.get('sent_at_local') or 'time unknown'}):", *_quoted(prompt["opener"]), ""]
        # what was sent is the ledger's ``asked``; the practice's bank is what it asks today, not what this prompt did
        out += [f"- {q['id']}: {q['question']}" for q in asked]
        if asked:
            out.append("")
        elif bank:   # a heading of its own: it is not the section of what was sent
            out += ["## The practice's questions (not necessarily asked)", "",
                    *[f"- {q['id']}: {q['text']}" for q in bank], ""]
    words = [b for b in doc["blocks"] if b["key"].startswith("words:")]
    talk = [b for b in doc["blocks"] if not b["key"].startswith("words:")]
    if words:
        out += [f"## {his}'s words", ""]
    for b in words:
        where = " · ".join(x for x in (b.get("at"), b.get("source"),
                                       f"phase {b['phase']}" if b.get("phase") else "",
                                       f"interrupt {b['interrupt']}" if b.get("interrupt") else "") if x)
        out += [f"{he} · {where}" if where else he, *_quoted(b["text"])]
        if b.get("audio"):
            out.append(f"(recording: {b['audio']})")
        out.append("")
    if talk:
        out += ["## Conversation", ""]
    for b in talk:
        label = he if b["kind"] == "his" else she
        if b.get("reply_to"):   # the line of hers his message answered: hers, so quoted and attributed to her, apart
            out += [f"(replying to {hers}'s message:)", *_quoted(b["reply_to"]), ""]
        out.append(f"{label} · {b['at']}" if b.get("at") else label)
        out += [*_quoted(b["text"]), ""]
    if doc.get("summary"):
        out += [f"## Summary (written by the journal from {his}'s answers)", "", *_quoted(doc["summary"]), ""]
    analysis = doc.get("analysis") or {}
    if analysis.get("annotations"):
        out += [f"## Annotations — {she}", "", *_quoted(analysis["annotations"]), ""]
    for note in analysis.get("notes") or []:
        out += [f"## Note — {she}" + (f" · {note['at']}" if note.get("at") else ""), "", *_quoted(note["text"]), ""]
    if analysis.get("reflection"):
        out += [f"Day reflection ({hers}'s): {analysis['reflection']}", ""]
    return "\n".join(out).rstrip() + "\n"


def entry_text(cfg: Config, stem: str) -> str:
    return render_document(entry_document(cfg, stem))


def practice_list(cfg: Config) -> dict:
    """The practice library: every practice with its schedule, whether it is on, and where it comes from."""
    lib = library.load(cfg)
    rows = [{"id": p.id, "title": p.title, "emoji": p.emoji, "enabled": p.enabled,
             "schedule": library.describe_schedule(p.schedule), "source": lib.source(p), "closes": p.closes,
             "questions": sum(len(qs) for _, qs in p.sections)} for p in lib.practices.values()]
    return {"practices": rows, "problems": list(lib.problems)}


def program_status(cfg: Config, now: datetime) -> dict:
    """The programs and the runs in progress: what ``/program`` shows (``programview.status``), read only."""
    return programview.status(cfg, now)


# ── writes ───────────────────────────────────────────────────────────────────

def save(cfg: Config, now: datetime, text: str, practice: str | None = None) -> dict:
    """Append his words, verbatim. With ``practice``, as his reply to that practice's open prompt (the chat's prompt
    then folds to "answered", as for a reply from the web app); without one, or when that practice has no open
    prompt, as a /j note of its own. Returns where it went (``stem``, ``path``) and how (``rule``: ``mcp`` for a
    reply, ``/j`` for a note)."""
    words = _text(text, "text")
    _not_off_record(cfg, words)
    _no_marker(words, "text")
    _no_placeholder(words, "text")
    pid = (practice or "").strip() or None
    if pid is not None:
        lib = library.load(cfg)
        if pid not in lib.practices and pid not in entries.FREEFORM_IDS:
            raise Refused(f"no practice {pid!r} (journal_practices lists them; programs are answered in the chat or "
                          "the web app)")
    info = _bounded(lambda: capture.capture_web(cfg, pid, words, now, via=capture.MCP_VIA))
    path = Path(info["path"])
    return {"stem": path.stem, "path": str(path), "rule": info["rule"],
            "answered": pid if info["rule"] == capture.MCP_VIA else None}


def _not_his_page(cfg: Config) -> Callable[[Path], None]:
    """The check of ``add_companion_note`` for this face: a note lands only in a file the engine wrote, or in a
    note entry that does not exist yet. A page of his own named like an entry (``2026-10-13-evening.md``) is
    readable but is his, and the web app answers 409 to a write on it (``serve.writes._engine_entry``)."""
    def check(path: Path) -> None:
        if path.exists() and document.engine_file(cfg, path.stem) is None:
            raise ValueError("this file is yours, not one the journal wrote, so this face does not write to it")
    return check


def add_note(cfg: Config, now: datetime, note: str, day: str | None = None, kind: str | None = None) -> dict:
    """The companion's note, appended under her notes heading in that day's entry (``capture.add_companion_note``,
    the companion's own tool); it never touches his words, nor a page of his own that only looks like an entry.
    ``kind`` picks the entry (a practice id, or ``note``). Off the record holds as for ``save``: this face cannot see
    a chat turn's state, so a note that starts with the command is refused."""
    text = _text(note, "note")
    _not_off_record(cfg, text)
    _no_marker(text, "note")
    _no_placeholder(text, "note")
    if day not in (None, ""):
        _date(day, "date")
    try:
        path = _bounded(lambda: capture.add_companion_note(cfg, text, now, day or None, kind or None,
                                                           check=_not_his_page(cfg)))
    except ValueError as e:
        raise Refused(f"{e}: nothing was saved") from None
    return {"stem": Path(path).stem, "path": path, "voice": "companion"}


HELD_NOTE = ("It goes out in the background unless the chat is off the record right now (a bare /x is holding the "
             "conversation, or the journal cannot tell): then nothing is sent, as with the journal's own automatic "
             "sends, and the log says so. Ask again once the conversation has gone quiet.")


def start(cfg: Config, practice: str, spawn: Callable[[Config, list[str]], str] = spawn_journal) -> dict:
    """Send a practice now: ``journal send <id> --hold-off-record`` spawned, as ``/start <id>`` does from the chat (ids
    and the legacy aliases resolved by ``commands.start``), except that it holds while a bare ``/x`` window is open:
    a model asks for it, not he, and a prompt send would end the window and take his next private message as the
    answer, which is why the automatic sends hold too. This face does not read the chat (its fence), so the spawned
    send asks the privacy pass and the answer says what it will do. A program is not started from here: its runs
    move on his own words, from the chat or the web app (PRACTICES.md decision 9), so the answer is the line he can
    send."""
    word = (practice or "").strip() if isinstance(practice, str) else ""
    if len(word.split()) != 1:
        raise Refused("name one practice by its id (journal_practices lists them)")
    found = commands.start(cfg, word)
    if found.program is not None:
        prog = found.program
        raise Refused(f"{prog.title} is a program; this face does not start programs. It begins with "
                      f"`/start {prog.aliases[0] if prog.aliases else prog.id}` in the chat, or from the web app.")
    if found.practice is None:
        raise Refused(found.reply)
    try:
        log = spawn(cfg, ["send", found.practice.id, "--hold-off-record"])
    except OSError as e:
        raise Refused(f"could not start the send ({type(e).__name__}); `journal doctor` says why") from None
    return {"spawned": True, "practice": found.practice.id, "title": found.practice.title, "log": log,
            "note": HELD_NOTE}


# ── resources ────────────────────────────────────────────────────────────────

def recent_text(cfg: Config, now: datetime, limit: int = 20) -> str:
    """The newest entries (``entry_list``) as JSON text, for the ``journal://entries/recent`` resource."""
    return json.dumps(entry_list(cfg, now, limit=limit), ensure_ascii=False, indent=1)
