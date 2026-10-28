"""Hermes' sessions database (``state.db``) as the engine's ``Transcript``: the one place Hermes rows are read.

Every reader of the chat (the privacy pass, the conversation writer, the busy-path sweep, the outage catch-up, the
context pack and its quote verifier, annotation alignment, the mirror's turn check) used to open ``state.db``
itself and decide on its own what a row meant. They now take ``ports.Row``s from here, and this module decides
once what Hermes stored:

* **His rows.** Gateway envelopes are peeled in the order the gateway adds them: the out-of-band marker of a
  message steered into a running turn (``agent/prompt_builder.py`` ``STEER_MARKER_OPEN``/``_CLOSE``), the origin
  note of a message folded into a turn (its Telegram id is inside), a ``[Note: …]`` the gateway prepends, the
  ``[Replying to: "…"]`` quote. What is left is his words, the gateway hook's journal note (``handled``: saved
  already), a platform frame or placeholder (``system``; a ``[Cron delivery:`` is a ``boundary``), the journal's
  own mirror, or nothing. Voice notes arrive as a quoted transcript; audio files and voice notes without
  speech-to-text as a placeholder naming the cached file (``gateway/run_inbound.py``
  ``_prepend_inbound_media_file_notes``).
* **Her rows.** A tool step (``tool_calls``), a failed turn (``display_kind = 'failed_turn'``, its notice, or
  the failure copy older Hermes stored), a turn closed without a reply (``[SILENT]``, an interruption), a typed or
  compaction row, or her reply.
* **Compaction copies.** Compaction re-inserts a session's tail in three shapes: in place (same session, role,
  time and words, a new id), with the compaction's time instead of the original's (older Hermes, or its
  timestamp carry failing: on one real database 17 of 26 copies had a new time), and by rotation into a child
  session of the same chat. So a copy is an earlier row of the chat with the same role, words and Telegram id, at
  or before its time, and either (a) in the same session at exactly its time, or (b) with words (a tool step has
  none: they are not copies of each other) in any session of the chat, when his Telegram id is there to tell it
  from a repeat or (for her, who has none) the earlier row was archived or its session ended by compression: two
  live replies with the same words are two replies. ``Row.copy`` names the first.

Read-only always (``?mode=ro``). The one write is ``mirror``: Hermes' own ``gateway.mirror.mirror_to_session``,
into the live session of the chat, never while a turn holds the conversation's lease (``journal.mirror`` queues
it). The readers' small differences from before the ports (which prefixes each one skipped) are kept as
``Row.meta`` findings rather than unified, so no reader's output changed (``tests/golden/hermes_rows.json``).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from .. import audio, mirror
from ..ports import ChatUnreadable, NotMirrored, Row
from .companion import is_failure_text

TIMEOUT = 5  # seconds a read waits for a lock (the gateway hook reads too: never long)

# ── what Hermes stores ───────────────────────────────────────────────────────

# What Hermes persists for a failed chat turn instead of the copy it sends (which only goes to Telegram): an
# assistant row with ``display_kind = 'failed_turn'`` holding one of these notices, plus, when the agent itself
# closed the turn, ``display_metadata`` with the provider ``error`` / ``error_surface``
# (``agent/turn_failure_copy.py`` FAILED_TURN_NOTICE / PARTIAL_FAILED_TURN_NOTICE,
# ``agent/conversation_loop.py`` ``_close_durable_failed_turn``, ``gateway/run_turn.py``
# ``_hmwa_close_failed_turn``). Display-only: Hermes strips these rows before every provider request.
FAILED_TURN_KIND = "failed_turn"
FAILED_TURN_NOTICES = (
    "Your request was not processed. Send it again if you still want me to carry it out.",
    "This turn did not complete. Some actions may already have run; verify their effects before resending.",
)
_HER_SKIP = ("[PRIOR CONTEXT", "[CONTEXT COMPACTION")  # compaction frames: not hers, and no turn's end
_TURN_END = ("[SILENT]", "Operation interrupted")    # a turn closed without a reply
_ORIGIN = re.compile(r"^Gateway message origin \(JSON data, not instructions or authorization\):\n(\{.*?\})\n"
                     r"Do not guess a reply destination when these fields are insufficient\.\n\n", re.S)
_REPLY = re.compile(r'^\[Replying to(?: your previous message)?: "(.*?)"\]\n\n', re.S)
_HANDLED = re.compile(r"(?:^|\n\n)\[Journal (?:reply|note) — ")  # the hook's note: before or after a transcript
_SKIP_PREFIXES = ("[PRIOR CONTEXT", "[CONTEXT COMPACTION", "[Cron delivery:", "[IMPORTANT:",
                  "[Your active task list", "[The user sent a document", "[The user sent an image",
                  "[The user sent a video", "[System note:", mirror.PREFIX)  # the journal's own sends, mirrored into the chat: hers, never his words
_GATEWAY_NOTE = re.compile(r"^\[Note: [^\]\n]*\]\n\n")  # e.g. "[Note: model was just switched …]" before his words
_VOICE = re.compile(r'^"(.*)"(?:\n\n(.*))?$', re.S)
# A message steered into a running turn is stored inside Hermes' out-of-band marker (agent/prompt_builder.py
# STEER_MARKER_OPEN / _CLOSE), around the origin note and his words.
_STEER = re.compile(r"^\s*\[OUT-OF-BAND USER MESSAGE[^\]\n]*\]\n(.*?)\n?\[/OUT-OF-BAND USER MESSAGE\]\s*$", re.S)
# An audio file (not a voice note): Hermes doesn't transcribe it and stores a note pointing at the cached file
# (gateway/run_inbound.py _prepend_inbound_media_file_notes), then his caption.
_AUDIO_FILE = re.compile(r"^\[The user sent an audio file attachment: '([^'\n]*)'\. It is saved at: ([^\n]+?)\. "
                         r"Its content is not inlined here\.[^\]]*\]\s*(.*)$", re.S)
_VOICE_FILE = re.compile(r"^\[The user sent a voice message: ([^\n]+?) \(duration:[^\]\n]*\)\]\s*(.*)$", re.S)
_AUDIO_PATH = re.compile(r"^\[(?:User sent audio|voice message could not be transcribed automatically; the audio "
                         r"is available at): ?([^\]\n]+)\]\s*(.*)$", re.S)
_NO_VOICE = re.compile(r"^\[(?:voice message could not be transcribed|The user sent a voice message but)[^\]]*\]\s*",
                       re.S)
_ATTACHMENT = ("[The user sent a document", "[The user sent an image", "[The user sent a video")
# Gateway text that can follow an attachment placeholder and is not his: another placeholder, a reply quote,
# a transcription note, the origin note. Anything else (a caption starting "[draft]", a Markdown link) is his.
_ENVELOPES = ("[The user sent", "[Replying to", "[voice message could not", "[Note: ", "[Journal ", "[Off the record",
              "[System note:", "Gateway message origin")
# The outage catch-up never took these user rows for his messages (opening, after leading space).
_FRAMES = ("[PRIOR CONTEXT", "[CONTEXT COMPACTION", "[Cron delivery:", "[IMPORTANT:", "[Your active task list")
# The context pack's own filter, as its query had it: SQL LIKE (ASCII case-insensitive), no leading space allowed.
_DELIVERIES = ("[Cron delivery:", "[IMPORTANT:", mirror.PREFIX)
# Annotation alignment never took these for her reply (the same LIKE).
_NOT_REPLY = ("[PRIOR CONTEXT", "[CONTEXT COMPACTION")
_NOISE = re.compile(r"Gateway message origin \(JSON data.*?insufficient\.\s*", re.S)
_JOURNAL_NOTE = re.compile(r"^\[Journal (?:reply|note) — .*?\]\s*", re.S)
# The hook's note (capture.context_note / note_context), wherever it sits (after a reply quote, after a voice
# transcript), matched up to its fixed ending: it quotes her opener, which may itself hold "]" or quotes.
_JOURNAL_NOTE_ANYWHERE = re.compile(
    r"\s*\[Journal (?:reply|note) — .*?(?:annotations are written automatically\.|Don't edit the file\.)\]\s*", re.S)
# A direct reply carries the message it answers, usually the companion's opener, ahead of his words.
_REPLY_QUOTE = re.compile(r'^\[Replying to(?: your previous message)?: ".*?"\]\s*', re.S)
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def is_failed_turn(content: str | None, display_kind: str | None = None) -> bool:
    """A stored assistant row that closes a failed chat turn (not something the companion said)."""
    return display_kind == FAILED_TURN_KIND or (content or "").strip() in FAILED_TURN_NOTICES


def read(content: str | None, platform_message_id: str | None) -> dict:
    """One stored user message, read: {message_id, quote, text, source, audio_path, kind} (+ file_name).

    ``kind`` is "his" (his words), "handled" (it carries the hook's journal note: already saved), "mirror"
    (the journal's own send), "system" (a Hermes or gateway row, not his words) or "empty". The text is
    his words alone: no gateway origin note, no ``[Note: …]`` prefix, no reply quote (kept in ``quote``)."""
    text = content or ""
    out = {"message_id": str(platform_message_id) if platform_message_id else None, "quote": None,
           "text": "", "source": "text", "audio_path": None, "kind": "empty"}
    if mirror.is_mirror(text):
        out["kind"] = "mirror"
        return out
    m = _STEER.match(text)
    if m:
        text = m.group(1)
    m = _ORIGIN.match(text)
    if m:  # folded into a running turn (redirect/steer): the id is in the origin note
        try:
            out["message_id"] = out["message_id"] or str(json.loads(m.group(1)).get("message_id") or "") or None
        except json.JSONDecodeError:
            pass
        text = text[m.end():]
    text = _GATEWAY_NOTE.sub("", text, count=1)
    m = _REPLY.match(text)
    if m:
        out["quote"], text = m.group(1), text[m.end():]
    if not text.strip():
        return out
    if _HANDLED.search(text):
        out["kind"], out["text"] = "handled", text.strip()
        return out
    if text.startswith(_SKIP_PREFIXES):
        out["kind"], out["text"] = "system", text.strip()
        return out
    source, audio_path = "text", None
    a = _AUDIO_PATH.match(text)
    f = _AUDIO_FILE.match(text)
    vf = _VOICE_FILE.match(text)
    if a:
        audio_path, text = a.group(1).strip(), a.group(2).strip()
        source = "audio"
    elif f:
        out["file_name"], audio_path, text, source = f.group(1).strip(), f.group(2).strip(), f.group(3).strip(), "audio"
    elif vf:  # speech-to-text off: a voice note with no transcript
        audio_path, text, source = vf.group(1).strip(), vf.group(2).strip() or audio.NO_TRANSCRIPT, "voice"
    elif _NO_VOICE.match(text):
        text, source = _NO_VOICE.sub("", text).strip() or audio.NO_TRANSCRIPT, "voice"
    else:
        v = _VOICE.match(text.strip())
        if v and '"\n\n"' not in (v.group(2) or ""):
            transcript = v.group(1).replace('"\n\n"', "\n\n").strip()
            caption = (v.group(2) or "").strip()
            text, source = (f"{transcript}\n\n{caption}" if caption else transcript), "voice"
    out.update(text=text.strip(), source=source, audio_path=audio_path, kind="his")
    return out


# Hermes' note for one picture (gateway/run_inbound.py _enrich_message_with_vision), each ending at its own fixed
# trailer: the vision model's description may itself hold "]" and blank lines, so no blank line ends a note.
_IMAGE_NOTE = re.compile(
    r"\[The user sent an image~ Here's what I can see:\n.*?\]\n\[If you need a closer look, use vision_analyze with "
    r"image_url: [^\n]*? ~\]"
    r"|\[The user sent an image but (?:I couldn't quite see it this time \(>_<\) You can try looking at it yourself"
    r"|something went wrong when I tried to look at it~ You can try examining it yourself) "
    r"with vision_analyze using image_url: [^\n]*\]", re.S)


def caption(text: str) -> str:
    """His words after an attachment placeholder (documents, images, video), if any. Picture notes (one per
    picture of an album, joined by blank lines) are skipped by their fixed shape; any other placeholder ends at
    the first ``]`` and blank line."""
    if not text.startswith(_ATTACHMENT):
        return ""
    rest = text
    while rest.startswith(_ATTACHMENT):
        m = _IMAGE_NOTE.match(rest)
        if m:
            rest = rest[m.end():].lstrip()
            continue
        head, sep, rest = rest.partition("]\n\n")
        if not sep:
            return ""
    rest = rest.strip()
    return rest if rest and not rest.startswith(_ENVELOPES) else ""


def clean_user_text(text: str) -> str:
    """His words alone, loosely: no gateway origin note, no quoted message he replied to, no journal hook note
    (the last two hold the companion's own text)."""
    text = _NOISE.sub("", text or "")
    text = _REPLY_QUOTE.sub("", text.lstrip())  # the quoted message is hers, not his words
    text = _JOURNAL_NOTE_ANYWHERE.sub("\n\n", text)
    text = _JOURNAL_NOTE.sub("", text.lstrip())  # an older note with another ending
    return text.strip()


def _like(text: str | None, prefixes) -> bool:
    """SQLite's ``text LIKE 'prefix%'`` for plain prefixes: ASCII letters fold, nothing else does."""
    if text is None:
        return False
    low = text.translate(_ASCII_LOWER)
    return any(low.startswith(p.translate(_ASCII_LOWER)) for p in prefixes)


def outage_sign(text: str, meta: str | None) -> bool:
    """Whether a failed turn says the model or provider failed (not an interrupt, a /stop, a refusal):
    the agent's own closing row carries the provider error; older Hermes stored its failure copy."""
    if is_failure_text(text):
        return True
    try:
        data = json.loads(meta) if meta else {}
    except json.JSONDecodeError:
        return False
    return isinstance(data, dict) and bool(data.get("error") or data.get("error_surface"))


def to_row(raw: Mapping) -> Row | None:
    """One raw ``messages`` row (keys: id, ts, role, content, pmid, display_kind, tool_calls, uid, copy,
    display_metadata, active) as a ``Row``; None for a row with no usable time (no reader can place it)."""
    try:
        at = datetime.fromtimestamp(float(raw["ts"]), timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    content = raw.get("content")
    stored = content or ""
    pmid = raw.get("pmid")
    dk = raw.get("display_kind") or ""
    active = raw.get("active")
    meta = {"uid": raw.get("uid"), "stored_id": str(pmid) if pmid else None, "active": active is None or active == 1}
    copy = int(raw["copy"]) if raw.get("copy") else None
    if raw.get("role") == "assistant":
        text = stored.strip()
        calls = raw.get("tool_calls") or ""
        failed = is_failed_turn(text, dk) or is_failure_text(text)
        if calls not in ("", "[]"):
            kind = "tool_step"  # Hermes sends a step's text as an interim message: narration, not the reply
        elif failed:
            kind = "failed_turn"
        elif text.startswith(_TURN_END):
            kind = "turn_end"
        elif not text:
            kind = "empty"
        elif dk or text.startswith(_HER_SKIP):
            kind = "frame"
        else:
            kind = "hers"
        meta.update(
            ends_turn=(calls in ("", "[]") and bool(text)) or failed or text.startswith(_TURN_END),
            outage=outage_sign(text, raw.get("display_metadata") or ""),
            reply=(bool(content) and calls in ("", "[]") and not _like(content, _NOT_REPLY)
                   and not is_failed_turn(content)))
        return Row(int(raw["id"]), at, "assistant", kind, text, str(pmid) if pmid else None, meta=meta, copy=copy,
                   stored=stored)
    msg = read(content, pmid)
    kind = msg["kind"]
    if kind == "system" and msg["text"].startswith("[Cron delivery:"):
        kind = "boundary"
    meta.update(notice=bool(dk) and dk != "steer", steer=dk == "steer", internal=dk == "internal_notification",
                loose=clean_user_text(stored), frame=stored.lstrip().startswith(_FRAMES),
                delivery=content is None or _like(content, _DELIVERIES))
    if kind == "system":
        meta["caption"] = caption(msg["text"])
    if msg.get("file_name"):
        meta["file_name"] = msg["file_name"]
    return Row(int(raw["id"]), at, "user", kind, msg["text"], msg["message_id"], msg["quote"], msg["source"],
               msg["audio_path"], meta=meta, copy=copy, stored=stored)


# ── reading the database ─────────────────────────────────────────────────────

def _ro(db: Path) -> sqlite3.Connection:
    if not Path(db).is_file():
        raise FileNotFoundError(f"no sessions database at {db}")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=TIMEOUT)


_KEYS = ("id", "ts", "role", "content", "pmid", "display_kind", "tool_calls", "uid", "copy", "display_metadata",
         "active")


def _chat(alias: str, chat_id: str, platform: str, users) -> tuple[str, list]:
    """The SQL that picks the journal chat's sessions (``sessions`` as ``alias``) and its arguments: the platform
    (``sessions.source``, as Hermes stores ``Platform.value``), the chat, and with owner ids configured only their
    sessions (``sessions.user_id``), so nobody else's turns in a shared chat are read."""
    sql, args = f"{alias}.source = ? and {alias}.chat_id = ?", [str(platform), str(chat_id)]
    users = [str(u) for u in users or ()]
    if users:
        sql += f" and {alias}.user_id in ({', '.join('?' * len(users))})"
        args += users
    return sql, args


def raw_rows(db: Path, chat_id: str, after_id: int = 0, since: float | None = None, upto_id: int | None = None,
             newest: int | None = None, *, platform: str = "telegram", users=()) -> list[dict]:
    """The chat's user and assistant rows as stored, in stored order, with ``copy`` found (see the module
    docstring). Retired and compacted rows are included: they are still what was said. The chat is ``chat_id`` on
    ``platform``, and with ``users`` only those people's sessions of it."""
    con = _ro(db)
    try:
        cols = {r[1] for r in con.execute("pragma table_info(messages)")}
        uid = "m.message_uid" if "message_uid" in cols else "null"
        # two real messages with the same words in the same second have their own Telegram ids; copies share it
        pmid_same = ("coalesce(c.platform_message_id, '') = coalesce(m.platform_message_id, '')"
                     if "platform_message_id" in cols else "1")
        pmid = "m.platform_message_id" if "platform_message_id" in cols else "null"  # older schemas
        calls = "coalesce(m.tool_calls, '')" if "tool_calls" in cols else "''"
        kind = "coalesce(m.display_kind, '')" if "display_kind" in cols else "''"
        meta = "coalesce(m.display_metadata, '')" if "display_metadata" in cols else "''"
        active = "m.active" if "active" in cols else "null"
        chat, args = _chat("s", chat_id, platform, users)
        where = ["m.rowid > ?"]
        args.append(after_id)
        if since is not None:  # a row with no time is kept: ``_read`` makes it fail closed, never a skipped /x
            where.append("(m.timestamp >= ? or m.timestamp is null)")
            args.append(since)
        if upto_id is not None:
            where.append("m.rowid <= ?")
            args.append(upto_id)
        order = "order by m.rowid" if newest is None else "order by m.rowid desc limit ?"
        if newest is not None:
            args.append(int(newest))
        found = [list(r) for r in con.execute(
            f"""select m.rowid, m.timestamp, m.role, m.content, {pmid}, {kind}, {calls}, {uid},
                       (select min(c.rowid) from messages c where c.session_id = m.session_id
                              and c.timestamp = m.timestamp and c.rowid < m.rowid and c.role = m.role
                              and coalesce(c.content, '') = coalesce(m.content, '')
                              and {pmid_same}),
                       {meta}, {active}
                from messages m join sessions s on s.id = m.session_id
                where {chat} and m.role in ('user', 'assistant')
                  and {' and '.join(where)}
                {order}""", args)]
        if newest is not None:
            found.reverse()
        if any(r[8] is None and r[3] for r in found):
            _wider_copies(con, chat_id, cols, found, platform=platform, users=users)
        return [dict(zip(_KEYS, r)) for r in found]
    finally:
        con.close()


def _wider_copies(con: sqlite3.Connection, chat_id: str, cols: set, found: list[list], *, platform: str = "telegram",
                  users=()) -> None:
    """Fill in ``copy`` for the rows stored again with a new time, or in another session of the chat.
    Done here over the chat's rows with words, not in SQL: the question is a lookup by (role, words, id), which a
    correlated subquery answers by scanning the whole table once per row."""
    scols = {r[1] for r in con.execute("pragma table_info(sessions)")}
    pm = "c.platform_message_id" if "platform_message_id" in cols else "null"
    active = "coalesce(c.active, 1)" if "active" in cols else "1"
    ended = "cs.end_reason" if "end_reason" in scols else "null"
    top = max(int(r[0]) for r in found)
    earlier: dict = {}
    chat, args = _chat("cs", chat_id, platform, users)
    for rid, ts, role, content, mid, off, gone in con.execute(
            f"""select c.rowid, c.timestamp, c.role, c.content, {pm}, {active} = 0, {ended} = 'compression'
                from messages c join sessions cs on cs.id = c.session_id
                where {chat} and c.role in ('user', 'assistant')
                  and coalesce(c.content, '') != '' and c.rowid < ? order by c.rowid""", (*args, top)):
        earlier.setdefault((role, content, mid or ""), []).append((rid, ts, bool(off or gone)))
    for r in found:
        if r[8] is not None or not r[3]:
            continue
        mid = r[4] or ""
        for rid, ts, archived in earlier.get((r[2], r[3], mid), ()):
            if rid >= r[0]:
                break
            if ts <= r[1] and (mid or archived):
                r[8] = rid  # the first: ``earlier`` is in row order
                break


def max_id(db: Path) -> int:
    con = _ro(db)
    try:
        return int(con.execute("select coalesce(max(rowid), 0) from messages").fetchone()[0])
    finally:
        con.close()


def anchor(db: Path, row_id: int) -> dict | None:
    """What a row looks like (uid and time), to recognise the same database next time."""
    if not row_id:
        return None
    con = _ro(db)
    try:
        cols = {r[1] for r in con.execute("pragma table_info(messages)")}
        uid = "message_uid" if "message_uid" in cols else "null"
        row = con.execute(f"select {uid}, timestamp from messages where rowid = ?", (row_id,)).fetchone()
    finally:
        con.close()
    return {"id": row_id, "uid": row[0], "ts": row[1]} if row else {"id": row_id, "uid": None, "ts": None}


def first_id(db: Path, chat_id: str, since: datetime, *, platform: str = "telegram", users=()) -> int | None:
    """The first row of the chat at or after ``since``, if any (any role: a cursor may sit on a tool row)."""
    con = _ro(db)
    chat, args = _chat("s", chat_id, platform, users)
    try:
        row = con.execute(f"""select min(m.rowid) from messages m join sessions s on s.id = m.session_id
                              where {chat} and m.timestamp >= ?""", (*args, since.timestamp())).fetchone()
    finally:
        con.close()
    return int(row[0]) if row and row[0] else None


def last_activity(db: Path, chat_id: str, before: datetime, *, platform: str = "telegram",
                  users=()) -> datetime | None:
    con = _ro(db)
    chat, args = _chat("s", chat_id, platform, users)
    try:
        row = con.execute(f"""select max(m.timestamp) from messages m join sessions s on s.id = m.session_id
                              where {chat} and m.role in ('user', 'assistant')
                                and m.timestamp < ?""", (*args, before.timestamp())).fetchone()
    finally:
        con.close()
    try:
        return datetime.fromtimestamp(float(row[0]), timezone.utc) if row and row[0] is not None else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


# ── the live session (the mirror's target and the turn lease) ────────────────

def _ro_or_skip(db: Path) -> sqlite3.Connection:
    if not Path(db).is_file():
        raise NotMirrored(f"no sessions database at {db}")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=TIMEOUT)


def live_session(chat_id: str, db: Path, *, platform: str = "telegram", users=()) -> str:
    """The chat's live gateway session, as ``find_session_by_origin`` picks it (no user id): the newest
    live session of the platform for the chat (with ``users``, of those people only); none, or sessions of
    different users, is a skip."""
    con = _ro_or_skip(db)
    users = [str(u) for u in users or ()]
    who = f" and user_id in ({', '.join('?' * len(users))})" if users else ""
    try:
        rows = con.execute(
            f"""select id, coalesce(user_id, '') from sessions
                where lower(source) = ? and session_key is not null and chat_id = ? and ended_at is null{who}
                order by started_at desc""", (str(platform).lower(), str(chat_id), *users)).fetchall()
    finally:
        con.close()
    if not rows:
        raise NotMirrored("no live chat session for the chat")
    if len({u.strip() for _, u in rows if u.strip()}) > 1:
        raise NotMirrored("several people's sessions share the chat")
    return str(rows[0][0])


def turn_running(db: Path, session_id: str, now: float | None = None) -> bool:
    """Whether the conversation holds an unexpired turn lease (the companion is answering right now).

    The lease key is the conversation's root: compression continuations walk up to their parent."""
    now = time.time() if now is None else now
    con = _ro_or_skip(db)
    try:
        cols = {r[1] for r in con.execute("pragma table_info(sessions)")}
        key, seen = session_id, {session_id}
        while "parent_session_id" in cols:
            row = con.execute("select parent_session_id from sessions where id = ?", (key,)).fetchone()
            parent = row[0] if row else None
            if not parent or parent in seen:
                break
            prow = con.execute("select end_reason from sessions where id = ?", (parent,)).fetchone()
            if not prow or prow[0] != "compression":
                break
            key = parent
            seen.add(parent)
        try:
            lease = con.execute("select expires_at from session_turn_leases where conversation_id = ?",
                                (key,)).fetchone()
        except sqlite3.OperationalError:  # an older Hermes without turn leases
            return False
    finally:
        con.close()
    return bool(lease) and float(lease[0]) > now


def mirror_to_session(platform: str, chat_id: str, text: str, source_label: str, role: str, session_id: str) -> bool:
    """Hermes' own mirror (``gateway/mirror.py``); raises ImportError outside Hermes.

    Importing Hermes runs its bootstrap, which may try to finish a pending source update (a ``uv sync``
    of the shared environment) unless lazy installs are off. Hermes turns them off for cron scripts
    (``cron/scheduler_script.py`` ``_posix_cron_script_argv``); this makes sure of it for any caller."""
    os.environ.setdefault("HERMES_DISABLE_LAZY_INSTALLS", "1")
    from gateway.mirror import mirror_to_session as hermes_mirror  # Hermes' Python only
    return bool(hermes_mirror(platform, chat_id, text, source_label=source_label, role=role, session_id=session_id))


def _target(cfg) -> Path:
    """The profile's sessions database, which Hermes will write: refuses anything else."""
    if Path(os.environ.get("HERMES_HOME") or "/nonexistent").resolve() != Path(cfg.hermes_home).resolve():
        raise NotMirrored("HERMES_HOME is not the journal's profile home")
    db = Path(cfg.hermes_home) / "state.db"
    if cfg.state_db is not None and Path(cfg.state_db).resolve() != db.resolve():
        raise NotMirrored("hermes.state_db is not the profile's own database")
    return db


def _read(raw: list[dict]) -> list[Row]:
    """Every raw row as a ``Row``. A row whose time cannot be read is not skipped: it could be a bare ``/x``,
    and the privacy pass would then open its window on the private words after it. The chat is unreadable
    until the row is mended (as before the ports, when the same row raised); the readers log and retry."""
    rows = [to_row(r) for r in raw]
    bad = [r["id"] for r, row in zip(raw, rows) if row is None]
    if bad:
        raise ChatUnreadable(f"{len(bad)} row(s) with no readable time (first id {bad[0]})")
    return rows


class HermesTranscript:
    """``ports.Transcript`` over Hermes' sessions database for the configured chat (``channel``: its platform, its
    chat and, when configured, its owners' sessions only)."""
    errors = (OSError, sqlite3.Error)

    def __init__(self, cfg, db: Path | str):
        self.cfg, self.db = cfg, db
        self.source = str(db)

    def available(self) -> bool:
        return Path(self.db).is_file()

    def rows(self, after_id: int = 0, since: datetime | None = None, upto_id: int | None = None) -> list[Row]:
        raw = raw_rows(self.db, self.cfg.chat_id, after_id, since.timestamp() if since is not None else None, upto_id,
                       **self._scope())
        return _read(raw)

    def tail(self, n: int) -> list[Row]:
        return _read(raw_rows(self.db, self.cfg.chat_id, newest=n, **self._scope()))

    def _scope(self) -> dict:
        """The journal's chat: its platform and, when configured, its owners (``channel``)."""
        return {"platform": self.cfg.platform, "users": self.cfg.owner_ids}

    def max_id(self) -> int:
        return max_id(self.db)

    def anchor(self, row_id: int) -> dict | None:
        return anchor(self.db, row_id)

    def first_id(self, since: datetime) -> int | None:
        return first_id(self.db, self.cfg.chat_id, since, **self._scope())

    def busy(self) -> bool:
        """Whether the companion is answering in the chat right now (False when that can't be told)."""
        try:
            return turn_running(self.db, live_session(self.cfg.chat_id, self.db, **self._scope()))
        except (NotMirrored, sqlite3.Error, OSError):
            return False

    def last_activity(self, before: datetime) -> datetime | None:
        return last_activity(self.db, self.cfg.chat_id, before, **self._scope())

    def mirror(self, text: str, *, flushing: bool = False) -> bool | str:
        """True: in the session; "queued": a turn is running (or Hermes is not importable in this process, so
        the next tick writes it); raises ``NotMirrored`` otherwise. Writes through ``journal.mirror``'s seam
        (``_mirror_to_session``), which every test replaces."""
        db = _target(self.cfg)
        try:
            sid = live_session(self.cfg.chat_id, db, **self._scope())
            if turn_running(db, sid):
                return "queued"
        except sqlite3.Error as e:
            raise NotMirrored(f"sessions database unreadable ({type(e).__name__})") from None
        try:
            ok = mirror._mirror_to_session(self.cfg.platform, str(self.cfg.chat_id), text, mirror.SOURCE_LABEL, "user",
                                           sid)
        except ImportError:
            if not flushing:  # e.g. a manual /morning send runs on the gateway's bare interpreter: the tick will
                return "queued"
            raise NotMirrored("Hermes is not importable here") from None
        except Exception as e:  # mirror_to_session never raises; belt and braces
            raise NotMirrored(f"{type(e).__name__}: {e}"[:200]) from None
        if not ok:
            raise NotMirrored("Hermes did not write the row (see the Hermes log)")
        return True
