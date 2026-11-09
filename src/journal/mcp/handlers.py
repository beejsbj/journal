"""The tools of ``journal mcp``, one function each, with no MCP in them: ``server`` registers them with the SDK, and
the tests call them directly.

Each tool is a call into what the engine and the other faces already do, never a second model of the journal:

* reads are ``journal serve``'s (``today.collect``, ``serve.api.filter_entries``, ``serve.document``), so an MCP
  client sees what the web app sees, and nothing the web app may not: no chat transcript, no privacy results, no
  packs, no notebook (``tests/test_mcp_fence.py`` fences the imports; ``tests/test_mcp.py`` runs every tool with
  ``sqlite3.connect`` made to fail);
* his words are only appended, through ``actions`` (``answer`` for a practice, ``note`` otherwise; ``via="mcp"``)
  under the ledger lock, as the web app's and the CLI's are: words for a practice fill the slot ``slots.resolve``
  gives (its open prompt, which then folds in the chat, its due or missed slot, the next one later today, else an
  extra entry of that practice), never a /j note;
* ``/x`` holds: text that starts with the off-the-record command, or has a line that does, is refused and nothing
  is written (as the chat takes it); a line shaped like the engine's own marker (``%% journal … %%``) is refused
  too, since it could pass for the companion's voice, and so is the transcript placeholder (``actions.refuse``);
* nothing here posts into the chat: ``journal_start`` returns the practice to answer (``actions.view``), the view
  the web app's Write now shows, and spawns nothing (OPEN-PROMPTS, choice C);
* the companion's voice stays hers: ``journal_entry`` labels every block with whose it is, and ``journal_add_note``
  files a note under the companion's notes heading (``capture.add_companion_note``), never among his words.

A refusal is ``Refused`` with the line the client is told; nothing has been written when it is raised.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from .. import actions, audio, capture, commands, entries, ledger, library, programview, today
from ..config import Config
from ..serve import api, document
from ..serve.writes import TEXT_LIMIT

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


def _his_or_hers(cfg: Config, text: str) -> None:
    """The refusals every face has (``actions.refuse``): ``/x`` holds here as in the chat (text that starts with the
    command, or has a line that does, is refused whole), a line shaped like the engine's own marker could pass for
    the other voice or a program's phase, and the transcript-pending line is no one's words."""
    try:
        actions.refuse(cfg, text)
    except actions.Refused as e:
        raise Refused(str(e)) from None


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
    """Append his words, verbatim, through ``actions``. With ``practice``, into that practice's slot
    (``actions.answer``: its open prompt, which then folds in the chat as a reply from the web app does; its due or
    missed slot; the next one later today, filled early; else an extra entry of that practice), never a /j note;
    without one, a /j note of its own (``actions.note``). Returns where it went (``stem``, ``path``), how (``rule``:
    ``mcp`` for a practice, ``/j`` for a note), and for a practice the slot and the step of ``slots.resolve`` that
    chose it."""
    words = _text(text, "text")
    pid = (practice or "").strip() or None
    if pid in entries.FREEFORM_IDS:
        pid = None
    try:
        if pid is None:
            info = _bounded(lambda: actions.note(cfg, words, via=capture.MCP_VIA, now=now))
        else:
            info = _bounded(lambda: actions.answer(cfg, pid, words, via=capture.MCP_VIA, now=now))
    except actions.Refused as e:
        if e.code == "not-a-practice":
            raise Refused(f"no practice {pid!r} (journal_practices lists them; programs are answered in the chat or "
                          "the web app): nothing was saved") from None
        raise Refused(str(e)) from None
    path = Path(info["path"])
    return {"stem": path.stem, "path": str(path), "rule": info["rule"], "answered": pid,
            **({"slot": info["slot"], "step": info["step"]} if pid else {})}


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
    _his_or_hers(cfg, text)
    if day not in (None, ""):
        _date(day, "date")
    try:
        path = _bounded(lambda: capture.add_companion_note(cfg, text, now, day or None, kind or None,
                                                           check=_not_his_page(cfg)))
    except ValueError as e:
        raise Refused(f"{e}: nothing was saved") from None
    return {"stem": Path(path).stem, "path": path, "voice": "companion"}


def start(cfg: Config, practice: str, now: datetime) -> dict:
    """The practice to answer, as the web app's Write now shows it (``actions.view``): its title, its opener and
    questions (``prompt``), and the slot words saved now would fill (``slot``, ``fills``). Nothing is sent, spawned or
    written: this face never posts into the chat (OPEN-PROMPTS, choice C), and opening a practice is a view. His
    answer goes through ``journal_save`` with ``practice``. Ids and the legacy aliases are resolved as ``/start``
    resolves them (``commands.start``); a program is not done from here (its runs move on his own words, in the chat
    or the web app: PRACTICES.md decision 9), so the answer is the line he can send."""
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
        shown = actions.view(cfg, found.practice, now=now)
    except actions.Refused as e:
        raise Refused(str(e).replace(": nothing was saved", "")) from None
    return {**shown, "next": f"When {cfg.names.user or 'he'} has answered, save the words, verbatim, with "
                             f"journal_save(text=…, practice=\"{shown['practice']}\")."}


# ── resources ────────────────────────────────────────────────────────────────

def recent_text(cfg: Config, now: datetime, limit: int = 20) -> str:
    """The newest entries (``entry_list``) as JSON text, for the ``journal://entries/recent`` resource."""
    return json.dumps(entry_list(cfg, now, limit=limit), ensure_ascii=False, indent=1)
