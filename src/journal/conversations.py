"""Conversation threads: what the user says to the companion in the chat, and her text replies, saved into
the journal verbatim by code, as part of the entries rather than as a chat log.

**Threads.** Messages in the companion chat (both sides) form a thread while the silence between them stays
under ``conversations.quiet_minutes`` (45); a journal prompt send (and a Hermes cron delivery) also ends one.
Every tick reads the chat transcript (``cfg.transcript()``: Hermes' sessions database, read-only) from a row-id
cursor and writes what is new; a
thread simply ends once it has been quiet that long. Rows are taken only once they are ``SETTLE`` old, so
the gateway hook (which saves journal replies and ``/j`` notes as they arrive) and the busy-path sweep have
always run first.

**Where a thread goes.** Into the entry of a journal-relevant message it holds (a captured journal reply or
a ``/j`` note): from that message on, the rest of the thread goes into that entry. A thread with none becomes
a note entry of its own, ``YYYY-MM-DD-j.md`` (``-j-2`` …) exactly like a ``/j`` note (front matter
``capture: auto``), with the user's first message as the note's words. A later journal-relevant message for
another entry moves the rest of the thread there; nothing already written moves.

**Capture mode** (``capture.mode``, ``config.capture_mode``). The above is ``everything``, the default. Under
``arm`` the journal is one arm of a general agent: a thread with no journal-relevant message is not written at all
(no auto note, its stashed audio dropped). Journal-relevant is a prompt reply, a program answer or a /j note (saved
by the hook or the sweep), a /j the hook and sweep missed (he asked for it to be kept), and a reply to the journal's
own words (an annotation reply, a day reflection, a week review). A thread that holds one is saved from that message
on exactly as above: the conversation that follows a reply, a /j note or a program answer is part of answering it.
The writer never judges a message the sweep has not passed yet (it may be a reply the hook missed): it waits a tick,
or, with the sweep switched off (or for a message older than it looks, or in a write backfill, which it never read), files such a reply where the sweep would.

**What it looks like.** Under ``## Conversation`` (after the user's words, before the annotations): each later
message of the user's as a paragraph with a small time marker (``**23:20** — …``; voice notes as transcript,
the audio kept beside the entry and embedded), and each companion reply folded directly under the message it
answers as an Obsidian callout (``> [!quote]- Ada · 23:21``). Her reply to the first message comes first.

**Never saved:** slash commands and their replies (``/j <text>`` stays a note), Hermes and gateway rows (cron
deliveries, compaction summaries, system notes, document placeholders), the journal's own mirrored sends,
failed turns, tool steps, and anything off the record: a message starting with ``conversations.opt_out_prefix``
(``/x``), and with the bare command the rest of that thread. The gateway hook strips the command and marks the
message for the companion; its id goes into this module's state so every reader can skip it.

**Never twice:** each written message is keyed (the user's by Telegram message id, hers by row uid) in the
state file, and a block already present in the file is never appended again. ``backfill`` applies the same
rules to past messages (``journal backfill-conversations``), with the same keys.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import ClassVar

from . import audio, capture, entries, images, ledger, sweep
from .config import Config
from .ports import Row

STATE = "conversations.json"
STASH = "audio-stash"
SETTLE = timedelta(minutes=2)    # rows younger than this wait for the next tick (the hook and sweep go first)
STASH_DAYS = 3                   # stashed chat audio not written by then is deleted
KEEP_DAYS = 400                  # how long written-message keys are remembered
PREVIEW = 60                     # characters of a first line shown by a dry run
LEGACY_MATCH = 80                # characters of a message found inside an old capture that make it that capture
_BLOCK_START = re.compile(r"^(?:#|>|[-*+] |\d+[.)] |```|\||!\[\[)")
_NOTE_PATH = re.compile(r"\[Journal (?:reply|note) — [^\n]*?(?:verbatim to |\()(/[^\n]+?\.md)")


def enabled(cfg: Config) -> bool:
    return cfg.conversations.get("enabled", True) is not False and bool(cfg.chat_id)


def quiet(cfg: Config) -> timedelta:
    from .config import quiet_minutes
    return timedelta(minutes=quiet_minutes(cfg))


def journals_all_chat(cfg: Config) -> bool:
    """Whether a thread that answers nothing still becomes a note of its own (``capture.mode`` everything)."""
    from .config import capture_mode
    return capture_mode(cfg) == "everything"


QUOTE_WORDS = 6  # a quote must be at least this long to count as a reply to one of the journal's own messages


def annotate_auto(cfg: Config) -> bool:
    return cfg.conversations.get("annotate_auto") is True


def is_auto(prompt: dict) -> bool:
    """A note entry the conversation writer started (not a /j the user sent)."""
    return prompt.get("capture") == "auto"


# ── state shared by the gateway hook and the tick ────────────────────────────

def _path(cfg: Config) -> Path:
    return cfg.state_dir / STATE


def snapshot(cfg: Config) -> dict:
    return ledger.read_state(_path(cfg))


def locked(cfg: Config):
    return ledger.locked_state(_path(cfg))


def off_ids(state: dict) -> set[str]:
    return set((state.get("off") or {}).keys())


def note_off(cfg: Config, mid: str | None, at: datetime, thread_start: bool = False) -> None:
    """The hook's records, inputs to the privacy pass (``privacy``): this message id is off the record; with
    ``thread_start``, a bare /x the gateway answered itself (it stores no row), at this time."""
    with locked(cfg) as st:
        if mid:
            st.setdefault("off", {})[str(mid)] = at.isoformat()
        if thread_start:
            st.setdefault("off_threads", {})[str(mid or int(at.timestamp()))] = at.isoformat()


def row_is_off(cfg: Config, pv, row: Row, state: dict | None = None) -> bool:
    """THE off-the-record test for a stored row, shared by every reader (writer, sweep, packs, verifier,
    annotation alignment, outage catch-up, tool guard): the privacy pass's answer for that row (``pv`` is
    ``privacy.view``; a closed view says yes to everything), and for one of his rows outside its horizon, the
    row's own marks (``privacy.row_marked``: the hook's marker, the opt-out command, an id the hook recorded)."""
    if pv.row(row.id):
        return True
    if row.role != "user":
        return False
    from . import privacy
    return privacy.row_marked(cfg, row, off_ids(snapshot(cfg) if state is None else state))


def stash_audio(cfg: Config, mid: str, media: str, ext: str, meta: dict, at: datetime) -> str | None:
    """The hook: keep a copy of chat audio (Hermes' cache is not ours) until the tick writes its message."""
    if not mid or not media or not os.path.isfile(media):
        return None
    d = cfg.state_dir / STASH
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    dest = d / (re.sub(r"[^0-9A-Za-z_-]", "_", str(mid)) + (ext or ".ogg"))
    shutil.copy2(media, dest)
    with locked(cfg) as st:
        st.setdefault("audio", {})[str(mid)] = {"file": str(dest), "ext": ext or ".ogg", "meta": meta or {},
                                                "at": at.isoformat()}
    return str(dest)


def stash_images(cfg: Config, mid: str, files: list[tuple[str, str]], at: datetime,
                 max_bytes: int | None = None) -> list[str]:
    """The hook: keep a copy of the pictures of a chat message that answers no prompt, until the tick writes it
    (``files``: (path in Hermes' cache, suffix)). One over ``max_bytes`` is not stashed. Returns the copies."""
    if not mid:
        return []
    d = cfg.state_dir / STASH
    d.mkdir(parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    stem = re.sub(r"[^0-9A-Za-z_-]", "_", str(mid))
    got = []
    for n, (media, ext) in enumerate(files, 1):
        if not os.path.isfile(media) or (max_bytes and os.path.getsize(media) > max_bytes):
            continue
        dest = d / f"{stem}-img{n}{ext}"
        shutil.copy2(media, dest)
        got.append({"file": str(dest), "ext": ext})
    if got:
        with locked(cfg) as st:
            st.setdefault("images", {})[str(mid)] = {"files": got, "at": at.isoformat()}
    return [g["file"] for g in got]


# ── reading the chat ─────────────────────────────────────────────────────────

@dataclass
class Event:
    at: datetime
    order: int                 # row id; pseudo-events sort before rows at the same time
    kind: str                  # his | captured | excluded | system | boundary | off_thread | hers | tool_step | turn_end
    key: str = ""              # the user's message id, or "uid:<row>" for her replies
    mid: str | None = None
    text: str = ""
    source: str = "text"
    quote: str | None = None
    audio_path: str | None = None
    home: str | None = None    # captured: the entry that holds it
    off: bool = False          # excluded because it is off the record
    why: str = ""              # what an excluded or system row was
    meta: dict | None = None   # audio: what Telegram said about it (file name …)
    # his: a /j the hook and sweep never saved (he asked for it to be kept). Not a field: the transcript golden
    # (tests/golden/hermes_rows.json) pins the fields of every classified row, and this is the writer's business.
    asked: ClassVar[bool] = False
    # one of his rows steered into her running turn (``Row.meta["steer"]``): her turn goes on after it, so the text
    # she held before it is not yet her reply. Not a field, for the same reason.
    steer: ClassVar[bool] = False


def same_db(t, cursor: dict) -> bool:
    """Whether transcript ``t`` is the one the cursor was taken in: its anchor row is still there, unchanged (a
    restored or rebuilt database at the same path has other rows under those ids)."""
    a = cursor.get("anchor")
    if not a:
        return int(cursor.get("last_id") or 0) <= t.max_id()
    return t.anchor(int(a["id"])) == a


def rows(cfg: Config, db: Path, after_id: int, since_ts: float = 0.0, upto_id: int | None = None) -> list[tuple]:
    """(id, ts, role, content, platform id, display kind, tool calls, uid, copy) for the chat, in stored order,
    as Hermes stored them (``journal.hermes.transcript.raw_rows``). The engine reads ``cfg.transcript().rows()``;
    this is the old shape, kept for callers of it."""
    from .hermes.transcript import raw_rows
    return [(r["id"], r["ts"], r["role"], r["content"], r["pmid"], r["display_kind"], r["tool_calls"], r["uid"],
             r["copy"]) for r in raw_rows(db, cfg.chat_id, after_id, since_ts, upto_id, platform=cfg.platform,
                                         users=cfg.owner_ids)]


def _send_times(cfg: Config) -> list[str]:
    """When journal prompts went out (ledger, and every send in ``sent.jsonl``, re-sends included)."""
    out = set()
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        if p.get("kind") not in entries.FREEFORM_IDS and p.get("sent_at") \
                and (p.get("message_id") or p.get("backfilled")):
            out.add(p["sent_at"])
    try:
        for line in (cfg.state_dir / "sent.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict) and rec.get("sent_at"):
                out.add(rec["sent_at"])
    except OSError:
        pass
    return sorted(out)


class _Saved:
    """Journal replies saved before message ids were kept (an old backfill, or entry files older than the
    ledger): a message whose words equal one such captured block exactly is that capture. Nothing else is
    matched by text."""

    def __init__(self, cfg: Config, data: dict):
        self.cfg, self.cache = cfg, {}
        self.known = {str(Path(p.get("entry_path") or "").name) for p in data["prompts"] if p.get("entry_path")}
        self.known |= {f"{p['stem']}.md" for p in data["prompts"] if p.get("stem")}
        self.days: set[str] = set()
        self.legacy: dict[str, list[str]] = {}  # local date → entry paths with a capture that has no message id
        for p in data["prompts"]:
            if p.get("local_date") and any(not c.get("platform_message_id") and c.get("rule") not in
                                           ("auto", "conversation") for c in p.get("captures") or []):
                path = in_root(cfg, p.get("entry_path") or str(entries.entry_path(
                    cfg.root, p.get("stem") or entries.stem_for(p["local_date"], p["kind"]))))
                if path:
                    self.legacy.setdefault(p["local_date"], []).append(path)

    def _for(self, day: str) -> list[str]:
        if day not in self.days:  # an engine entry no ledger record knows (written before it): all legacy
            self.days.add(day)
            for p in entries.entries_on(self.cfg.root, day):
                if p.name not in self.known and entries.kind_of(p) not in ("note",) and str(p) not in \
                        self.legacy.get(day, []):
                    self.legacy.setdefault(day, []).append(str(p))
        return self.legacy.get(day, [])

    def find(self, text: str, at: datetime) -> str | None:
        w = sweep.words(text)
        if not w:
            return None
        local = at.astimezone(self.cfg.tz).date()
        for d in (local, local - timedelta(days=1)):
            for path in self._for(d.isoformat()):
                for block in self._blocks(path):
                    # the whole block, or most of it (old files hold a line or two besides his words), or a
                    # passage long enough not to be said twice by chance; never a short phrase inside a block
                    if w in block and (len(w) >= 0.8 * len(block) or len(w) >= LEGACY_MATCH):
                        return path
        return None

    def _blocks(self, path: str) -> set[str]:
        if path not in self.cache:
            try:
                words = entries.raw_entry(Path(path).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                words = ""
            out = set()
            for block in re.split(r"(?m)^### [^\n]*\n", words):
                block = re.sub(r"(?m)^!\[\[[^\]]*\]\]\s*$", "", block)  # an audio embed is not his words
                if block.strip():
                    out.add(sweep.words(block))
            self.cache[path] = out
        return self.cache[path]


def in_root(cfg: Config, path: str | None) -> str | None:
    """An entry path inside the journal root, or None. Entries are flat at the root, so a recorded path from
    elsewhere (a moved journal, a copied ledger) is read as that name in this root; the writer never touches
    a file outside it."""
    if not path:
        return None
    p = Path(path)
    root = Path(cfg.root).resolve()
    if p.is_absolute() and p.resolve().parent == root:
        return str(p)
    q = root / p.name
    return str(q) if entries.kind_of(q) and q.is_file() else None


def _by_key(cfg: Config, data: dict, key: str) -> str | None:
    """The note a message without a Telegram id started (its capture carries the writer's key)."""
    for p in data["prompts"]:
        if any(c.get("key") == key for c in p.get("captures") or []):
            return in_root(cfg, p.get("entry_path"))
    return None


def _home_of(cfg: Config, data: dict, mid: str | None) -> str | None:
    done = ledger.capture_of(data, mid) if mid else None
    if not done:
        return None
    p = done[0]
    return in_root(cfg, p.get("entry_path") or str(entries.entry_path(cfg.root, p.get("stem") or
                                                                      entries.stem_for(p["local_date"], p["kind"]))))


def classify(cfg: Config, row: Row, data: dict, pv, saved: _Saved, off: dict | None = None) -> Event | None:
    """One row, as the writer sees it. ``pv`` is the privacy pass's ``View``: its private rows are never
    written (his are skipped; hers are only an end of turn). ``off`` is this module's state (the hook's records),
    read once per walk. What the row is (an envelope, a notice, a frame, a failed turn) the transcript decided
    (``ports.Row``); what it means for the journal (captured already, a command, whose entry) is decided here."""
    rid, at = row.id, row.at
    if row.copy:
        return None  # stored again by compaction: the first copy was (or will be) read
    if row.role == "assistant" and pv.row(rid):
        return Event(at, rid, "private", why="off")
    if row.role == "assistant":
        # Only her replies: not a failed turn, a typed row or a compaction frame, and not a tool step. Hermes does
        # send a tool step's text to the chat as an interim message (display.interim_assistant_messages), but that
        # is narration of the work ("let me look…"); the reply is the turn's final text, so only rows without tool
        # calls are kept.
        if row.kind == "tool_step":
            return Event(at, rid, "tool_step")  # the turn went on working: any text she held before is not the reply
        if row.kind in ("failed_turn", "turn_end"):
            return Event(at, rid, "turn_end")  # the turn ended with nothing she said: not a reply, but an end
        if row.kind != "hers":
            return None
        return Event(at, rid, "hers", key=f"uid:{row.meta.get('uid') or rid}", text=row.text)
    if row.meta.get("notice"):  # notifications, process completions, skill invocations …
        return Event(at, rid, "system", why="system")
    mid, kind, text = row.platform_message_id, row.kind, row.text
    if row_is_off(cfg, pv, row, off):  # the pass's answer, and the row's own marks besides
        return Event(at, rid, "excluded", key=mid or "", mid=mid, off=True, why="off")
    pictured = bool(mid and ((off or {}).get("images") or {}).get(mid))  # the hook stashed his picture
    if kind == "empty" and pictured:
        kind = "his"  # native image input stores no placeholder: a captionless photo is a row with no text at all
    if kind in ("mirror", "empty"):
        return Event(at, rid, "system", why="journal" if kind == "mirror" else "system")
    if kind == "boundary":
        return Event(at, rid, "boundary", why="cron")
    if kind == "system":
        caption = row.meta.get("caption") or ""
        if not caption and not pictured:
            return Event(at, rid, "system", why="system")
        kind, text = "his", caption  # a document or image he sent: the placeholder goes, his words stay
    if kind == "handled":
        home = _home_of(cfg, data, mid)
        if not home:
            m = _NOTE_PATH.search(row.stored)
            home = in_root(cfg, m.group(1)) if m else None
        return Event(at, rid, "captured", key=mid or f"row:{rid}", mid=mid, home=home, why="captured")
    uid = row.meta.get("uid")
    if capture.opt_out(cfg, text) is not None or capture.opt_out_inside(cfg, text):  # (the pass marks these; never written either way)
        return Event(at, rid, "excluded", key=mid or "", mid=mid, off=True, why="off")
    home = _home_of(cfg, data, mid) or (None if mid else _by_key(cfg, data, f"row:{uid or rid}"))
    if home:
        return Event(at, rid, "captured", key=mid or f"row:{uid or rid}", mid=mid, home=home, why="captured")
    source = row.source
    after_j = capture.strip_command(text)
    if after_j is not None:
        if not after_j.strip():
            return Event(at, rid, "excluded", key=mid or "", mid=mid, why="command")
        text = after_j.strip()  # a /j the hook and sweep never saved: keep the words as conversation
    elif text.startswith("/"):
        return Event(at, rid, "excluded", key=mid or "", mid=mid, why="command")
    home = saved.find(text, at) if not mid or not ledger.capture_of(data, mid) else None
    if home:
        return Event(at, rid, "captured", key=mid or f"row:{rid}", mid=mid, home=home, why="captured")
    if not text and source != "audio" and not pictured:
        return Event(at, rid, "system", why="system")
    file_name = row.meta.get("file_name")
    if pictured and source == "text":
        source = "photo"
    ev = Event(at, rid, "his", key=mid or f"row:{uid or rid}", mid=mid, text=text, source=source,
               quote=row.quote, audio_path=row.audio_path, meta={"file_name": file_name} if file_name else {})
    if after_j is not None:
        ev.asked = True
    return ev


# ── rendering ────────────────────────────────────────────────────────────────

def _label(cfg: Config, at: datetime, entry_date: str | None) -> str:
    local = at.astimezone(cfg.tz)
    if entry_date and local.date().isoformat() != entry_date:
        return local.strftime("%a %-d %b %H:%M")
    return local.strftime("%H:%M")


def entry_date_of(path) -> str | None:
    """The date an entry file is named for (``2026-10-15-evening.md``), or None for a name that has none."""
    m = re.match(r"^(\d{4}-\d{2}-\d{2})-", Path(path).name)
    return m.group(1) if m else None


def marker(cfg: Config, at: datetime, source: str, meta: dict | None, entry_date: str | None) -> str:
    """The line that opens one of the user's messages in the conversation: ``**23:20**`` (text) or
    ``**23:20** · voice transcript · 2:31`` (audio)."""
    head = f"**{_label(cfg, at, entry_date)}**"
    if source in ("voice", "audio", "photo"):
        return f"{head} · {audio.label(source, meta, cfg.tz)}"
    return head


def his_block(cfg: Config, at: datetime, text: str, source: str = "text", kept: str | None = None,
              meta: dict | None = None, entry_date: str | None = None, quote: str | None = None,
              key: str | None = None, pictures: list[str] | None = None) -> str:
    """One of the user's messages, verbatim, as a paragraph with a small time marker (and, with ``key``, the
    hidden line that keys it)."""
    out = entries.block_mark("his", key) + "\n" if key else ""
    if quote:
        q = " ".join(quote.split())
        q = q if len(q) <= 200 else q[:200].rsplit(" ", 1)[0] + " …"
        out += f"> [!info]- ↩ replying to\n> {q}\n\n"
    head = marker(cfg, at, source, meta, entry_date)
    body = (text or "").strip()
    if source in ("voice", "audio", "photo"):
        embed = f"![[{kept}]]\n\n" if kept else "".join(f"![[{name}]]\n\n" for name in pictures or ())
        return out + f"{head}\n\n{embed}{body}".rstrip() + "\n"
    if _BLOCK_START.match(body):
        return out + f"{head} —\n\n{body}\n"
    return out + f"{head} — {body}\n"


def her_block(cfg: Config, at: datetime, text: str, entry_date: str | None = None, key: str | None = None) -> str:
    """One companion reply, folded: an Obsidian callout, every line prefixed ``> `` (and, with ``key``, the
    hidden line that keys it)."""
    lines = ([entries.block_mark("her", key)] if key else []) + [
        f"> [!quote]- {cfg.names.companion} · {_label(cfg, at, entry_date)}"]
    lines += [f"> {l}" if l.strip() else ">" for l in (text or "").strip().splitlines()]
    return "\n".join(lines) + "\n"


def _preview(text: str) -> str:
    line = next((l.strip() for l in (text or "").splitlines() if l.strip()), "")
    return line if len(line) <= PREVIEW else line[:PREVIEW].rsplit(" ", 1)[0] + " …"


# ── writing (or planning) ────────────────────────────────────────────────────

class _Writer:
    """Applies placement decisions: writes files (``write``) or only counts what it would do (a dry run)."""

    def __init__(self, cfg: Config, state: dict, write: bool, annotate: bool, via: str):
        self.cfg, self.state, self.write, self.annotate, self.via = cfg, state, write, annotate, via
        self.files: dict[str, dict] = {}    # path → {"new", "his", "hers", "preview"}
        self.reserved: set[str] = set()     # stems a dry run would take
        self.reserved_paths: set[str] = set()
        self.consumed: list[str] = []       # stashed audio written
        self.consumed_images: list[str] = []  # message ids whose stashed pictures were written
        self.days: dict[str, str] = {}      # path → local date of the last block written (date labels)
        self.planned: set[str] = set()      # (dry run) prompts a preview has already answered
        self._ours: list | None = None      # the journal's own sends he may reply to (``_sends``), read once

    def _stat(self, path: str, new: bool = False) -> dict:
        return self.files.setdefault(path, {"new": new, "his": 0, "hers": 0, "preview": "", "order": len(self.files)})

    def written(self, key: str) -> str | None:
        rec = (self.state.get("written") or {}).get(key)
        return rec.get("entry") if isinstance(rec, dict) else None

    def her_written(self, key: str) -> bool:
        return key in (self.state.get("her") or {})

    def _remember(self, key: str, at: datetime, path: str) -> None:
        if key:
            self.state.setdefault("written", {})[key] = {"at": at.isoformat(), "entry": Path(path).stem}

    def _entry_date(self, path: str, at: datetime | None = None) -> str | None:
        """The date a block's time is read against: the entry's own date for the first block of another day
        (its marker then carries the date), the block's own date for the ones after it (time only)."""
        entry = entry_date_of(path)
        if at is None or entry is None:
            return entry
        day = at.astimezone(self.cfg.tz).date().isoformat()
        seen = self.days.get(path)
        self.days[path] = day
        return entry if day != entry and seen != day else day

    def _keep(self, ev: Event, entry_path: str) -> tuple[str | None, dict]:
        """Move the message's audio beside its entry: the stash (the hook's copy) or the cached path the row names.
        Once per message: a replay finds the name it was kept under, so it builds the same block."""
        stash = (self.state.get("audio") or {}).get(ev.mid or "")
        done = (snapshot(self.cfg).get("kept") or {}).get(ev.key) if self.write else None
        if done and (Path(self.cfg.root) / done["name"]).is_file():
            return done["name"], dict(done.get("meta") or {})
        src, ext, meta = None, None, {}
        if stash and os.path.isfile(stash.get("file") or ""):
            src, ext, meta = stash["file"], stash.get("ext") or ".ogg", dict(stash.get("meta") or {})
        elif ev.audio_path and os.path.isfile(ev.audio_path):
            src, ext = ev.audio_path, os.path.splitext(ev.audio_path)[1].lower() or ".ogg"
        meta = {**(ev.meta or {}), **meta}
        if not src:
            return None, meta
        if not self.write:
            return f"{Path(entry_path).stem}{ext}", meta
        try:
            kept = audio.keep(self.cfg, src, entry_path, ext)
        except OSError:
            return None, meta
        with locked(self.cfg) as st:  # at once: a crash before the commit must not keep it twice
            st.setdefault("kept", {})[ev.key] = {"name": kept, "meta": meta, "at": ev.at.isoformat()}
        if stash:
            self.consumed.append(ev.mid or "")
        return kept, meta

    def _keep_images(self, ev: Event, entry_path: str) -> list[str]:
        """Move the message's stashed pictures beside its entry (once per message, like ``_keep``: a replay finds the
        names it was kept under and builds the same block). A picture that cannot be kept is left out."""
        done = (snapshot(self.cfg).get("kept") or {}).get(f"{ev.key}|images") if self.write else None
        if done and all((Path(self.cfg.root) / n).is_file() for n in done["names"]):
            return list(done["names"])  # (before the stash: a commit drops the stash, the record stays)
        stash = (self.state.get("images") or {}).get(ev.mid or "")
        if not stash:
            return []
        stem = Path(entry_path).stem
        if not self.write:
            return [f"{stem}{rec.get('ext') or '.jpg'}" for rec in stash.get("files") or []]
        names = []
        for rec in stash.get("files") or []:
            if not os.path.isfile(rec.get("file") or ""):
                continue
            try:
                names.append(images.keep(self.cfg, rec["file"], entry_path, rec.get("ext") or ".jpg"))
            except (OSError, ValueError, images.ImageTooLarge):
                continue
        if names:
            with locked(self.cfg) as st:  # at once: a crash before the commit must not keep them twice
                st.setdefault("kept", {})[f"{ev.key}|images"] = {"names": names, "at": ev.at.isoformat()}
            self.consumed_images.append(ev.mid or "")
        return names

    def new_note(self, ev: Event) -> str:
        """Start an auto note entry with this message as its words."""
        local_date = ev.at.astimezone(self.cfg.tz).date().isoformat()
        if not self.write:
            n, stem = 1, entries.stem_for(local_date, "j")
            taken = {p.get("stem") for p in ledger.snapshot(self.cfg.ledger_path)["prompts"]} | self.reserved
            while stem in taken or entries.entry_path(self.cfg.root, stem).exists():
                n += 1
                stem = entries.stem_for(local_date, "j", n)
            self.reserved.add(stem)
            path = str(entries.entry_path(self.cfg.root, stem))
            self.reserved_paths.add(path)
            self._stat(path, new=True)["preview"] = _preview(ev.text) or f"({ev.source})"
            self._stat(path)["his"] += 1
            return path
        with ledger.locked(self.cfg.ledger_path) as data:
            done = ledger.capture_of(data, ev.mid) if ev.mid else None
            if done:
                return _home_of(self.cfg, data, ev.mid)  # None (it lives outside this journal): not placed
            again = _by_key(self.cfg, data, ev.key)
            if again:
                return again  # a replay after a crash: that note exists already
            p = capture.new_note_prompt(data, self.cfg, ev.at)
            p.update(capture="auto", opener_source="auto",
                     annotate=self.annotate or (ev.asked and not journals_all_chat(self.cfg)))  # (arm: a /j he asked
            # for is annotated like one the hook saved)
            if ev.asked:
                p["asked"] = True  # a /j the hook and sweep missed: he asked for it, so it answers no prompt
            if not journals_all_chat(self.cfg):
                p["kept"] = True  # (arm) only journal material starts a note; a replay after a crash must know
            path = str(entries.entry_path(self.cfg.root, p["stem"]))
            kept, meta = self._keep(ev, path)
            pics = self._keep_images(ev, path) if ev.source == "photo" else []
            pending = ev.source == "audio" and bool(kept) and audio.transcription_enabled(self.cfg)
            words = ev.text or (audio.TRANSCRIBING if pending else audio.NOT_TRANSCRIBED if ev.source == "audio" else "")
            capture.record(self.cfg, p, "auto", words, ev.at, ev.source if ev.source != "photo" or pics else "text",
                           ev.mid, kept, meta or None, via=self.via, transcript="pending" if pending else None,
                           images=pics, describe=images.describing(self.cfg))
            p["captures"][-1]["key"] = ev.key  # rows without a Telegram id are found again by this
            p["needs_edit"] = False
        self._stat(path, new=True)["preview"] = _preview(ev.text) or f"({ev.source})"
        self._stat(path)["his"] += 1
        self._remember(ev.key, ev.at, path)
        return path

    def is_auto_note(self, path: str) -> bool:
        """Whether ``path`` is a note this writer started from ordinary chat (``capture: auto``, not ``kept`` as journal
        material under arm), as opposed to an entry he answered a prompt into or a /j note he sent."""
        name = Path(path).name
        return any(p.get("capture") == "auto" and not p.get("kept") and Path(p.get("entry_path") or "").name == name
                   for p in ledger.snapshot(self.cfg.ledger_path)["prompts"])

    def _sends(self) -> list[tuple[datetime, str, str | None]]:
        """What the journal itself sent to the chat that he can reply to besides a prompt, as (sent, words, entry):
        an annotation reply (the entry it annotates is its home) and a day reflection or week review (no home)."""
        if self._ours is None:
            from . import compose, reflect
            names, out = self.cfg.names, []
            data = ledger.snapshot(self.cfg.ledger_path)
            for p in data["prompts"]:
                r = p.get("annotation_reply") or {}
                path = Path(in_root(self.cfg, p.get("entry_path")) or "")  # (the journal may have moved)
                if r.get("message_id") and r.get("sent_at") and path.is_file():
                    text = entries.annotations(path.read_text(encoding="utf-8", errors="replace"))
                    out.append((datetime.fromisoformat(r["sent_at"]),
                                sweep.words(compose.annotation_html(p.get("kind") or "", text, names)),
                                in_root(self.cfg, str(path))))
            for r in data.get("reflections") or []:
                tg, path = r.get("telegram") or {}, Path(in_root(self.cfg, r.get("path")) or "")
                if tg.get("message_id") and tg.get("sent_at") and path.is_file():
                    try:
                        title, _ = reflect._label(self.cfg, r["kind"], date.fromisoformat(r["local_date"]))
                    except (KeyError, ValueError):
                        continue
                    body = reflect.body_of(path.read_text(encoding="utf-8", errors="replace"))
                    out.append((datetime.fromisoformat(tg["sent_at"]),
                                sweep.words(compose.reflection_html(title, body, names)), None))
            self._ours = out
        return self._ours

    def answers_ours(self, ev: Event) -> tuple[bool, str | None]:
        """Whether his message replies to one of the journal's own messages other than a prompt (the platform stores
        the quoted text): an annotation reply, a day reflection, a week review. Returns (yes, the entry it
        belongs to, when that is an entry of his). A reply to the journal's words is journal material."""
        q = sweep.words(ev.quote or "")
        if len(q.split()) < QUOTE_WORDS:  # (a short quote such as "thank you" says nothing of what it answers)
            return False, None
        found = [(sent, home) for sent, text, home in self._sends() if sent <= ev.at and q in text]
        if not found:
            return False, None
        homes = {h for _, h in found}
        home = homes.pop() if len(homes) == 1 else None  # (a quote that fits several sends, e.g. only the title of
        return True, home if home and self.present(home) else None  # an annotation, names no entry: a note of its own)

    def drop(self, ev: Event) -> None:
        """A message that is not journaled (``capture.mode`` arm, or off the record): the hook's copies of its audio
        and pictures go at the commit, not three days later."""
        if self.write and ev.mid and ev.mid in (self.state.get("audio") or {}):
            self.consumed.append(ev.mid)
        if self.write and ev.mid and ev.mid in (self.state.get("images") or {}):
            self.consumed_images.append(ev.mid)

    def present(self, path: str) -> bool:
        p = Path(path)
        return p.resolve().parent == Path(self.cfg.root).resolve() and (p.is_file() or path in self.reserved_paths)

    def _append(self, path: str, block: str, mark: str) -> str:
        """"written", "present" (its key line is already in the file: a crash between the file and the state)
        or "missing" (the entry is gone, or outside the journal root)."""
        p = Path(path)
        if p.resolve().parent != Path(self.cfg.root).resolve():
            return "missing"  # never outside the journal root
        with ledger.locked(self.cfg.ledger_path):  # the hook writes entries under this lock too
            if not p.is_file():
                return "missing"
            if mark in p.read_text(encoding="utf-8"):
                return "present"
            entries.append_conversation(p, block, self.cfg.names)
        return "written"

    def his(self, ev: Event, path: str, quote: str | None) -> str:
        """Append one of his messages; returns what ``_append`` did ("written" in a dry run)."""
        if not self.present(path):
            return "missing"
        kept, meta = self._keep(ev, path) if (ev.source in ("voice", "audio") or ev.audio_path) else (None, {})
        pics = self._keep_images(ev, path) if ev.source == "photo" else []
        source = ev.source if ev.source != "photo" or pics else "text"  # (a picture that could not be kept is text)
        pending = ev.source == "audio" and bool(kept) and audio.transcription_enabled(self.cfg)
        text = ev.text or (audio.TRANSCRIBING if pending else audio.NOT_TRANSCRIBED if ev.source == "audio" else "")
        block = his_block(self.cfg, ev.at, text, source, kept, meta, self._entry_date(path, ev.at), quote,
                          key=ev.key, pictures=pics)
        rec = self._stat(path)
        if not rec["preview"]:
            rec["preview"] = _preview(ev.text) or f"({ev.source})"
        rec["his"] += 1
        if not self.write:
            return "written"
        done = self._append(path, block, entries.block_mark("his", ev.key))
        if done in ("written", "present") and pending:  # a replay makes sure the transcription is still queued
            self._pending_transcript(ev, path, kept, meta)
        if done in ("written", "present") and pics and images.describing(self.cfg):
            self._pending_description(ev, path, pics)
        if done != "missing":
            self._remember(ev.key, ev.at, path)
        else:
            rec["his"] -= 1
        return done

    def _conversation_prompt(self, data: dict, path: str) -> tuple[dict | None, list[dict]]:
        """The ledger prompt a conversation's recording or picture is filed on (the tick's audio and images steps
        find it there), and every capture of that entry (to file it once across replays). A practice's or a note's
        prompt of the entry when there is one, as always; in a program session's entry, where every prompt is a
        phase, the newest phase's: not phase 1's, the first by ledger order, which the capture was filed on before.
        Either way it is ``rule: conversation``, which ``ledger.answers`` leaves out of a phase's answers.

        A recorded path is read through ``in_root``, as the writer read it to append there (a moved journal, a copied
        ledger); the prompt filed on then records this path, so the tick's steps fill the entry that holds the block."""
        mine = [q for q in data["prompts"] if in_root(self.cfg, q.get("entry_path")) == path]
        p = next((q for q in mine if not q.get("run")), mine[-1] if mine else None)
        if p is not None and p.get("entry_path") != path:
            p["entry_path"] = path
        return p, [c for q in mine for c in q.get("captures") or []]

    def _pending_transcript(self, ev: Event, path: str, kept: str, meta: dict) -> None:
        """An audio file in the conversation: the tick's audio step transcribes it like a reply's."""
        with ledger.locked(self.cfg.ledger_path) as data:
            p, caps = self._conversation_prompt(data, path)
            if p is not None and not any(c.get("audio") == kept for c in caps):
                p.setdefault("captures", []).append({
                    "at": ev.at.isoformat(), "rule": "conversation", "source": "audio",
                    "platform_message_id": ev.mid, "audio": kept, **({"meta": meta} if meta else {}),
                    "via": self.via, "transcript": "pending"})

    def _pending_description(self, ev: Event, path: str, pics: list[str]) -> None:
        """Pictures in the conversation: the tick's images step describes them like a reply's (``images.describe``)."""
        with ledger.locked(self.cfg.ledger_path) as data:
            p, caps = self._conversation_prompt(data, path)
            if p is not None and not any(set(pics) & set(c.get("images") or ()) for c in caps):
                p.setdefault("captures", []).append({
                    "at": ev.at.isoformat(), "rule": "conversation", "source": "photo",
                    "platform_message_id": ev.mid, "images": list(pics), "via": self.via, "describe": "pending"})

    def hers(self, ev: Event, path: str) -> None:
        if not self.present(path):
            return
        self._stat(path)["hers"] += 1
        if not self.write:
            return
        done = self._append(path, her_block(self.cfg, ev.at, ev.text, self._entry_date(path, ev.at), key=ev.key),
                            entries.block_mark("her", ev.key))
        if done != "missing":
            self.state.setdefault("her", {})[ev.key] = ev.at.isoformat()
        else:
            self._stat(path)["hers"] -= 1


# ── threading ────────────────────────────────────────────────────────────────

class _Threads:
    """Walks events in order and decides, for each, which thread and entry it belongs to.

    Her reply to a message is the last text she sent before his next message: Hermes stores other text rows
    in a turn too (probes, notices), so a reply is held (``pending``) until something else happens, a later
    reply in the same turn replaces it, and more tool calls, a failed turn or an interruption discard it.
    What is private is not decided here: the privacy pass (``privacy``) marked those rows, and they arrive as
    ``excluded`` (his) and ``private`` (hers): never written, but still activity of the thread."""

    def __init__(self, cfg: Config, writer: _Writer, thread: dict | None, draining: dict | None = None):
        self.cfg, self.w, self.th = cfg, writer, thread
        self.draining = draining  # a thread a prompt send ended while her turn in it was still running
        self.quiet = quiet(cfg)
        self.skipped: dict[str, int] = {}
        self.threads = 0
        self.unswept_before: datetime | None = None  # (arm) older than the sweep still looks: nobody else files it

    def _close(self) -> None:
        self.flush()
        self.th = None

    def _open(self, at: datetime) -> dict:
        self.th = {"started": at.isoformat(), "last": at.isoformat(), "home": None, "reply": None,
                   "her_last": "", "pending": None}
        self.threads += 1
        return self.th

    def _skip(self, why: str) -> None:
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def _touch(self, at: datetime) -> None:
        """Activity: the thread's last moment only moves forward (a queued row can carry an older time)."""
        if self.th and at > datetime.fromisoformat(self.th["last"]):
            self.th["last"] = at.isoformat()

    def flush(self, older_than: datetime | None = None) -> None:
        """Write the held reply (only if it was sent before ``older_than``, when given)."""
        th = self.th
        pend = th.get("pending") if th else None
        if not pend:
            return
        at = datetime.fromisoformat(pend["at"])
        if older_than is not None and at > older_than:
            return
        th["pending"] = None
        home = pend.get("home") or th.get("home")  # the entry she held it for (a steered message may have moved on)
        if not home or self._ordinary_home(home, pend.get("kept") if "home" in pend else None):
            self._skip("reply to something not saved")  # (arm, switched to while the reply was held)
        elif not self.w.her_written(pend["key"]):
            self.w.hers(Event(at, -1, "hers", key=pend["key"], text=pend["text"]), home)
        th["her_last"] = pend["text"][:300]  # only to tell whether his next message quotes it

    def tick_to(self, at: datetime) -> None:
        """Close the open thread if it has been quiet since its last message."""
        if self.th and at - datetime.fromisoformat(self.th["last"]) >= self.quiet:
            self._close()

    def _in_flight(self) -> bool:
        th = self.th
        return bool(th) and (bool(th.get("owed")) or bool(th.get("pending")))

    def _with_draining(self, ev: Event) -> bool:
        """Her rows while a thread ended by a prompt send still owes its reply: they belong to that thread, and so does
        a message of his steered into that turn (kept or off the record), which stays with her reply. A journal reply
        the hook captured, steered in or not, ends it and moves on."""
        steered = ev.steer and ev.kind in ("his", "excluded")
        if not self.draining or (ev.kind not in ("hers", "tool_step", "turn_end", "private") and not steered):
            return False
        if ev.kind == "his" and self._filed_apart(ev):
            return False  # (arm) filed elsewhere: it ends the draining thread and goes its own way, as unsteered
        live, self.th = self.th, self.draining
        try:
            self._feed(ev)
            if self.th and ev.kind == "turn_end":
                self._close()  # her turn ended: the old thread is done
            self.draining = self.th
        finally:
            self.th = live
        return True

    def end_draining(self) -> None:
        """His next message (or quiet) ends the old thread's turn: write its held reply and let it go."""
        if self.draining:
            live, self.th = self.th, self.draining
            try:
                self._close()
            finally:
                self.th, self.draining = live, None

    def feed(self, ev: Event) -> None:
        if self._with_draining(ev):
            return
        if ev.kind not in ("hers", "tool_step", "turn_end", "private"):
            self.end_draining()
        self._feed(ev)

    def _admit(self, ev: Event) -> tuple[str, str | None]:
        """What becomes of his message in a thread with no entry. Under ``capture.mode`` everything it may start a
        note of its own (``"start"``). Under arm only journal material may: a /j he sent (the hook and sweep missed
        it), or a reply to one of the journal's own messages (an annotation reply: ``"home"``, its entry; a day
        reflection or week review: ``"start"``). With the sweep switched off the writer takes the sweep's place and
        files a prompt reply the hook missed where the sweep would (``"done"``: the thread's entry is set). Anything
        else is ordinary chat (``"drop"``): not written, nor her reply to it (the thread stays open: a journal reply
        later in it takes the rest, as in everything)."""
        if ev.asked or journals_all_chat(self.cfg):
            return "start", None
        if self._in_sweeps_place(ev):
            # (first: the hook's own order, ``capture.match``, files a reply on a prompt before anything else)
            path = self._as_sweep_would(ev)
            if path:
                self.th.update(home=path, reply="in", her_last="", owed=True)
                self._skip("captured")
                return "done", None
        ours, home = self.w.answers_ours(ev)
        if ours:
            return ("home", home) if home else ("start", None)
        self.th["reply"] = "out"
        self.w.drop(ev)
        self._skip("ordinary chat (capture: arm)")
        return "drop", None

    def _in_sweeps_place(self, ev: Event) -> bool:
        """(arm) Whether this writer files his message as the sweep would, if it answers a prompt: the sweep is off, a
        backfill (which the sweep never read), or a message older than the sweep still looks at."""
        return bool(ev.mid) and (self.cfg.sweep.get("enabled") is False or self.w.via == "backfill"
                                 or (self.unswept_before is not None and ev.at <= self.unswept_before))

    def _prompt_for(self, ev: Event, data: dict) -> dict | None:
        """The prompt his message answers by the sweep's rules (a quote of it, else the one open then), read only."""
        prompt = sweep.by_quote(self.cfg, data, ev.quote, ev.at) if ev.quote else None
        if prompt is None:  # a typed message answers a prompt once; a direct reply to it, as often as it is sent
            prompt = sweep.visible_at(self.cfg, data, ev.at)
            if prompt is not None and prompt["id"] in self.w.planned:
                return None  # (answered already in this preview: the ledger does not show it)
        if prompt is None or not (ev.text or ev.source == "audio"):
            return None
        return prompt

    def _filed_apart(self, ev: Event) -> bool:
        """(arm) Whether a message of his would be filed apart from the thread it is in, so it cannot simply join one:
        a /j he asked for, a prompt reply this writer files where the sweep would, or a reply to the journal's own
        words. Read only (``_admit`` decides, and files)."""
        if journals_all_chat(self.cfg):
            return False
        if ev.asked:
            return True
        if self._in_sweeps_place(ev) and self._prompt_for(ev, ledger.snapshot(self.cfg.ledger_path)) is not None:
            return True
        return self.w.answers_ours(ev)[0]

    def _as_sweep_would(self, ev: Event) -> str | None:
        """The entry a prompt reply goes into, filed by the sweep's own rules (``sweep.reconcile``), or None when it
        answers no prompt. A dry run (backfill) files nothing: it plans the same entry, so the preview matches the
        run that writes."""
        msg = {"message_id": ev.mid, "quote": ev.quote, "text": ev.text, "source": ev.source,
               "audio_path": ev.audio_path}
        if self.w.write:
            info = sweep.reconcile(self.cfg, msg, ev.at)
            if not info or not info.get("path"):
                return None
            self.w._remember(ev.key, ev.at, info["path"])  # (a copy of the row stored again is this message, once)
            stat = self.w._stat(info["path"], new=len(info["prompt"].get("captures") or []) == 1)
            stat["his"] += 1  # (the backfill's report counts it, as the dry run did)
            stat["preview"] = stat["preview"] or _preview(ev.text) or f"({ev.source})"
            return info["path"]
        data = ledger.snapshot(self.cfg.ledger_path)
        prompt = self._prompt_for(ev, data)
        if prompt is None:
            return None
        self.w.planned.add(prompt["id"])
        path = capture.planned_info(self.cfg, prompt, data)["path"]
        stat = self.w._stat(path, new=not Path(path).exists())
        stat["his"] += 1
        stat["preview"] = stat["preview"] or _preview(ev.text) or f"({ev.source})"
        self.w.reserved_paths.add(path)
        return path

    def _ordinary_home(self, home: str | None = None, kept: bool | None = None) -> bool:
        """Under arm, whether the thread's entry (or ``home``, held text's entry, with ``kept`` as it was then) is a note
        this writer started from chat (before the mode was switched to arm): it does not make the rest of the thread
        journal material. A note that journal material started (a /j he asked for, a reply to the journal's own words)
        and any entry a journal reply went into do."""
        th = self.th
        home = home or th.get("home")
        kept = th.get("kept") if kept is None else kept
        return not journals_all_chat(self.cfg) and bool(home) and not kept and self.w.is_auto_note(home)

    def _feed(self, ev: Event) -> None:
        if ev.kind in ("turn_end", "tool_step", "private"):
            # a failed or interrupted turn, more tool calls, or text of hers the pass marked private: none of it
            # is the reply, and any text held before it was not the reply either
            if self.th:
                self._touch(ev.at)
                if self.th.get("pending"):
                    self.th["pending"] = None
                    self._skip("earlier text in the same turn")
                if ev.kind != "tool_step":
                    self.th["owed"] = False
            if ev.kind == "private":
                self._skip("off")
            return
        self.tick_to(ev.at)
        if ev.kind == "hers":
            th = self.th
            if not th:
                return self._skip("unattached reply")
            self._touch(ev.at)
            if th["reply"] != "in" or not th["home"] or self._ordinary_home():
                if th.get("pending"):  # (held across a steered command: this later text says it was not the reply)
                    th["pending"] = None
                    self._skip("earlier text in the same turn")
                return self._skip("reply to something not saved")
            if th.get("pending"):
                self._skip("earlier text in the same turn")
            th["pending"] = {"at": ev.at.isoformat(), "key": ev.key, "text": ev.text, "home": th["home"],
                             "kept": bool(th.get("kept"))}
            th["owed"] = False
            return
        if ev.kind == "boundary" and self._in_flight():
            # her turn is still running: its reply stays with this thread; his next message starts the new one
            self.end_draining()
            self.draining, self.th = self.th, None
            if ev.order >= 0:
                self._skip(ev.why or "system")
            return
        held = (self.th or {}).get("pending")
        if not (ev.steer and ev.kind in ("his", "excluded")) or (held and "home" not in held):
            # (a message steered into her running turn ends nothing: what she held before it is still provisional,
            # and her turn's later rows decide it, a tool step discarding it and a later text replacing it. Text held
            # by older code does not say which entry it was held for: it is written now, as it was then.)
            self.flush()
        if ev.kind == "boundary":
            self._close()
            if ev.order >= 0:
                self._skip(ev.why or "system")
            return
        th = self.th
        if ev.kind == "system":
            if ev.order >= 0:
                self._skip(ev.why or "system")
            if th:
                th["reply"] = "out"  # whatever answers a system row is not part of the conversation
            return
        th = th or self._open(ev.at)
        self._touch(ev.at)
        if ev.kind == "excluded":
            th["reply"] = "out"
            if ev.off:  # a private message: the hook's copy of its picture or audio (it can stash one before a
                self.w.drop(ev)  # busy-path /x is seen) goes at this tick's commit, not STASH_DAYS later
            return self._skip(ev.why or "command")
        if ev.kind == "captured":
            self._skip("captured")
            if ev.home and Path(ev.home).is_file():
                th.update(home=ev.home, reply="in", owed=True)
            else:
                th["reply"] = "out"
            return
        # his
        done = self.w.written(ev.key)
        if done:
            home = str(entries.entry_path(self.cfg.root, done))
            th["home"] = home if self.w.present(home) else th["home"]
            th["reply"] = "in"
            return self._skip("captured")
        if th["home"] and not self.w.present(th["home"]):
            th["home"] = None  # the entry was moved or deleted: this message starts a note of its own
        if th["home"] and self._ordinary_home():
            th["home"] = None  # arm: the chat-started note it joined (before the switch) is not a journal thread
        placed = "written"
        if not th["home"]:
            verdict, home = self._admit(ev)
            if verdict in ("done", "drop"):
                return
            th["home"] = home
            if not journals_all_chat(self.cfg):
                th["kept"] = True  # (arm) journal material started this note: the rest of the thread is its own
        if not th["home"]:
            th["home"] = self.w.new_note(ev)
            placed = "written" if th["home"] else "missing"
        else:
            quote = ev.quote
            if quote and th["her_last"] and sweep.words(th["her_last"]).startswith(sweep.words(quote)[:40]):
                quote = None  # it answers her message just above: no need to repeat it
            placed = self.w.his(ev, th["home"], quote)
            if placed == "missing":  # gone since the check above
                verdict, home = self._admit(ev)
                if verdict in ("done", "drop"):
                    return
                th["home"] = home or self.w.new_note(ev)
                placed = "written" if th["home"] else "missing"
        if placed == "missing":
            th["reply"] = "out"
            return self._skip("not placed")
        th.update(reply="in", her_last="", owed=True)


def _pseudo(cfg: Config, after: datetime | None, upto: datetime) -> list[Event]:
    """Prompt sends (thread boundaries) in (after, upto]."""
    from . import privacy
    return [Event(at, -1, "boundary", why="prompt") for at in privacy.send_times(cfg)
            if (after is None or at > after) and at <= upto]


def _walk(cfg: Config, pv, found: list[Row], extra: list[Event], threads: _Threads) -> tuple[int, str | None]:
    """Classify and feed rows in the order Hermes stored them (row id: the order the model saw them; a user
    row's time is the Telegram send time, so a queued message can carry an earlier one), with prompt sends
    merged in by time. ``pv`` is the privacy pass's view. Returns the last row id and the latest time handled."""
    data = ledger.snapshot(cfg.ledger_path)
    off = snapshot(cfg)
    saved = _Saved(cfg, data)
    extra = sorted(extra, key=lambda e: e.at)
    last_id = 0
    last_at = datetime.fromisoformat(threads.th["last"]) if threads.th else None
    for row in found:
        at = row.at
        while extra and extra[0].at <= at:
            threads.feed(extra.pop(0))
        ev = classify(cfg, row, data, pv, saved, off)
        if ev and row.role == "user" and row.meta.get("steer"):
            ev.steer = True
        last_id = max(last_id, row.id)
        if ev and ev.kind == "boundary" and last_at is not None and last_at - at > quiet(cfg):
            ev.kind = "system"  # an old cron row stored again later: it can't end the thread going on now
        last_at = at if last_at is None or at > last_at else last_at
        if ev:
            threads.feed(ev)
    for ev in extra:
        threads.feed(ev)
    return last_id, last_at.isoformat() if last_at else None


def _commit(cfg: Config, writer: _Writer, threads: _Threads, cursor: dict | None, now: datetime,
            handoff: dict | None = None) -> None:
    """Persist what was written (and the cursor), merged into what the hook may have added meanwhile, in one
    locked write; ``handoff`` (a backfill's db marker and start id) goes in the same write."""
    with locked(cfg) as st:
        if handoff:
            st.update(handoff)
        for key in ("written", "her"):
            st.setdefault(key, {}).update(writer.state.get(key) or {})
        for mid in writer.consumed:
            rec = (st.get("audio") or {}).pop(mid, None)
            if rec:
                Path(rec.get("file") or "").unlink(missing_ok=True)
        for mid in writer.consumed_images:
            _drop_images(st, mid)
        if cursor is not None:
            st["cursor"] = cursor
        _prune(st, now)


def _prune(st: dict, now: datetime) -> None:
    """Forget old written-message keys and stale stashes. The hook's privacy records (``off``, ``off_threads``) are
    never pruned: for a message steered in on the busy path inside a bare-/x window they are the only sign that it
    is private, and the chat they cover can be read for as long as the sessions database keeps it (a backfill from
    any ``--since``, the quote verifier over the whole chat). Like the prompt sends (``privacy.SENDS``), they are
    the privacy pass's inputs for any horizon; one small entry per off-the-record message."""
    keep = (now - timedelta(days=KEEP_DAYS)).isoformat()

    def when(v) -> str:
        return (v.get("at") if isinstance(v, dict) else v) or ""
    for key in ("written", "her", "kept"):
        if isinstance(st.get(key), dict):
            st[key] = {k: v for k, v in st[key].items() if when(v) >= keep}
    for old in ("x_mode", "off_ranges"):  # (state of an earlier build of this branch; the privacy pass replaced it)
        st.pop(old, None)
    stale = (now - timedelta(days=STASH_DAYS)).isoformat()
    for mid, rec in list((st.get("audio") or {}).items()):
        if (rec.get("at") or "") < stale:
            Path(rec.get("file") or "").unlink(missing_ok=True)
            st["audio"].pop(mid, None)
    for mid, rec in list((st.get("images") or {}).items()):
        if (rec.get("at") or "") < stale:
            _drop_images(st, mid)


def _drop_images(st: dict, mid: str) -> None:
    """Forget a message's stashed pictures and delete the copies."""
    rec = (st.get("images") or {}).pop(mid, None)
    for f in (rec or {}).get("files") or []:
        Path(f.get("file") or "").unlink(missing_ok=True)


def _prune_stash(cfg: Config, now: datetime) -> None:
    """Age the audio stash on a tick that reads no chat (no transcript, or an unreadable one): the stash is
    otherwise pruned only by a commit, and a hook that stashed audio would keep it for ever."""
    st = snapshot(cfg)
    if not (st.get("audio") or {}) and not (st.get("images") or {}):
        return
    with locked(cfg) as st:
        _prune(st, now)


def _log_lines(writer: _Writer, cfg: Config) -> list[str]:
    out = []
    for path, rec in writer.files.items():
        if not rec["his"] and not rec["hers"]:
            continue
        what = "new note " if rec["new"] else ""
        out.append(f"conversation → {what}{Path(path).name}: +{rec['his']} from {cfg.names.user_or_them}, "
                   f"+{rec['hers']} from {cfg.names.companion}")
    return out


def _swept_horizon(cfg: Config, t, now: datetime) -> tuple[int, datetime] | None:
    """(arm, with the sweep on) the last row the sweep has passed and the oldest time it still looks at: a message
    of his after the one and not older than the other is not yet known to be ordinary chat. None: no waiting (the
    default mode, where this writer is the safety net for what the sweep misses; or a sweep that is off, where it
    takes the sweep's place itself, ``_Threads._admit``)."""
    if journals_all_chat(cfg) or not sweep.active(cfg):
        return None
    return sweep.swept_upto(cfg, t.source), now - sweep.lookback(cfg)


def run(cfg: Config, now: datetime, log: list[str], db: Path | None = None) -> int:
    """One tick: write what was said since the last one. Returns how many messages were written."""
    if not enabled(cfg):
        return 0
    t = cfg.transcript(db)
    if t.source is None:
        _prune_stash(cfg, now)
        return 0  # standalone: no chat history to read
    state = snapshot(cfg)
    try:
        top = t.max_id()
        cursor = state.get("cursor") if state.get("db") == t.source else None
        if cursor is None or not same_db(t, cursor):
            # First run (or a replaced database): start from now. What came before is the backfill's.
            with locked(cfg) as st:
                st.update(db=t.source, start_id=top, cursor={"last_id": top, "last_at": now.isoformat(), "thread": None,
                                                             "anchor": t.anchor(top)})
            if cursor is not None:
                log.append("conversations: the sessions database was replaced; starting again from now")
            return 0
        found = t.rows(after_id=int(cursor.get("last_id") or 0))
    except t.errors as e:
        log.append(f"conversations skipped: sessions database unavailable ({type(e).__name__}: {str(e)[:120]})")
        _prune_stash(cfg, now)
        return 0
    cutoff = now - SETTLE
    swept = _swept_horizon(cfg, t, now)
    settled = []
    for r in found:
        if r.at > cutoff:
            break  # ids are in order: everything after waits too
        if swept is not None and r.role == "user" and r.id > swept[0] and r.at > swept[1]:
            # (arm) the sweep has not passed this message of his yet: it may be a reply the hook missed, which the
            # sweep files and this writer must then follow. Dropping it as ordinary chat now would lose it for good
            # (the cursor moves on), so wait for the next tick, with the prompt sends that came after it.
            cutoff = min(cutoff, r.at - timedelta(microseconds=1))
            log.append(f"conversations: waiting for the sweep to pass row {r.id}")
            break
        settled.append(r)
    after = datetime.fromisoformat(cursor["last_at"]) if cursor.get("last_at") else None
    writer = _Writer(cfg, state, write=True, annotate=annotate_auto(cfg), via="conversation")
    threads = _Threads(cfg, writer, json.loads(json.dumps(cursor.get("thread"))),
                       json.loads(json.dumps(cursor.get("draining"))))
    threads.unswept_before = swept[1] if swept else None
    last_id = int(cursor.get("last_id") or 0)
    ok = False
    try:
        from . import privacy
        # the rows walked are read here, so the pass must have read them too: a saved pass's stamp is no proof
        oldest = min((r.at for r in settled), default=None)
        pv = privacy.covering(cfg, oldest, now, db=db)
        if pv.closed:
            raise RuntimeError(f"privacy pass unavailable ({getattr(pv, 'why', '')}): nothing written")
        walked, _ = _walk(cfg, pv, settled, _pseudo(cfg, after, cutoff), threads)
        last_id = max(last_id, walked)
        # Her held reply is final once nothing of that turn is still on its way: no row waiting to settle, and
        # no turn running in the chat (its lease). Only then may the thread close for quiet, too.
        if len(settled) == len(found) and not t.busy():
            threads.end_draining()
            threads.tick_to(cutoff)
            threads.flush()
        ok = True
    finally:
        # What was written is always remembered; the cursor only moves when the whole batch went through, so a
        # failed tick is replayed (the keys, and the key line in each block, keep the replay from writing twice).
        new_cursor = ({"last_id": last_id, "last_at": max(cutoff, after).isoformat() if after else cutoff.isoformat(),
                       "thread": threads.th, "draining": threads.draining, "anchor": t.anchor(last_id)}
                      if ok else None)
        _commit(cfg, writer, threads, new_cursor, now)
    log.extend(_log_lines(writer, cfg))
    return sum(r["his"] + r["hers"] for r in writer.files.values())


def backfill(cfg: Config, since: date, write: bool, now: datetime | None = None, db: Path | None = None) -> list[str]:
    """Apply the same threading to past messages from ``since`` (local midnight). A dry run (``write`` False)
    changes nothing and reports per file what would be created or appended, with counts and first-line
    previews. Messages already in an entry (by message id, the state's keys, or their words in that day's
    entries) are never written again. Stops where the live writer started; if it never ran, the live writer
    continues from where the backfill stops."""
    now = now or datetime.now(timezone.utc)
    t = cfg.transcript(db)
    if t.source is None:
        raise FileNotFoundError("no Hermes sessions database (hermes.state_db) to read")
    state = snapshot(cfg)
    live = state.get("cursor") if state.get("db") == t.source else None
    if live is not None and not same_db(t, live):
        live = None  # the database was replaced since: its old cursor and start mean nothing here
    upto = int(state.get("start_id") or 0) if live else None
    start = datetime.combine(since, time(0), tzinfo=cfg.tz)
    cutoff = now - SETTLE
    found = [r for r in t.rows(since=start, upto_id=upto) if r.at <= cutoff]
    writer = _Writer(cfg, json.loads(json.dumps(state)), write=write, annotate=False, via="backfill")
    threads = _Threads(cfg, writer, None)
    end = max((r.at for r in found), default=start)  # not the last row's: a queued row can carry an earlier time
    from . import privacy
    pv = privacy.view(cfg, now, since=start, db=db)
    if pv.closed:
        raise RuntimeError(f"privacy pass unavailable ({getattr(pv, 'why', '')}): nothing to show or write")
    last_id, last_at = _walk(cfg, pv, found, _pseudo(cfg, start - timedelta(microseconds=1), end), threads)
    if live is not None or not write:
        threads.end_draining()
        threads.flush()  # the live writer won't continue this thread: its last reply is final
    if write:
        cursor = None
        if live is None and found:  # the live writer picks up exactly here, mid-thread if need be
            cursor = {"last_id": last_id, "last_at": last_at, "thread": threads.th, "draining": threads.draining,
                      "anchor": t.anchor(last_id)}
        elif live is None:  # nothing settled yet: the live writer starts at the first row since --since
            first = t.first_id(start)
            last_id = first - 1 if first else t.max_id()
            cursor = {"last_id": last_id, "last_at": start.isoformat(), "thread": None, "anchor": t.anchor(last_id)}
        _commit(cfg, writer, threads, cursor, now,
                handoff={"db": t.source, "start_id": last_id} if cursor is not None else None)
    n = cfg.names
    head = (f"Backfill of conversations since {since.isoformat()} "
            + ("(written)" if write else "(dry run: nothing written; add --write to apply)"))
    lines = [head]
    if live:
        lines.append(f"  (up to row {upto}, where the live writer started)")
    width = max([len(Path(p).name) for p in writer.files] or [0])
    for path, rec in sorted(writer.files.items(), key=lambda kv: kv[1]["order"]):  # in the order first written
        verb = "create" if rec["new"] else "append"
        preview = f"  “{rec['preview']}”" if rec["preview"] else f"  ({n.companion}'s replies only)"
        lines.append(f"  {verb} {Path(path).name:<{width}}  {rec['his']:>3} from {n.user_or_them}, "
                     f"{rec['hers']:>3} from {n.companion}{preview}")
    created = sum(1 for r in writer.files.values() if r["new"])
    his = sum(r["his"] for r in writer.files.values())
    hers = sum(r["hers"] for r in writer.files.values())
    lines.append(f"  {threads.threads} thread(s): {created} new note entr{'y' if created == 1 else 'ies'}, "
                 f"{len(writer.files) - created} existing entr{'y' if len(writer.files) - created == 1 else 'ies'} "
                 f"appended; {his} message(s) from {n.user_or_them}, {hers} repl{'y' if hers == 1 else 'ies'} "
                 f"from {n.companion}")
    if threads.skipped:
        lines.append("  not written: " + ", ".join(f"{v} {k}" for k, v in sorted(threads.skipped.items())))
    return lines
