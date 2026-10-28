"""Mirror what the journal sends into the companion's own chat session, so her live chat knows it.

The engine sends prompts, annotation replies, reflections and catch-up messages through the Telegram
Bot API directly (it needs their message ids to fold and reply). Hermes never sees those sends, so
without a mirror the companion's chat transcript would not contain them: when the user answers, she
would see only Telegram's truncated ``[Replying to: "…"]`` quote. Hermes cron deliveries had the same
problem and solve it with ``gateway.mirror.mirror_to_session``, which appends a row to the chat's
live session transcript (state.db). The journal uses the same call, the same way cron does: as a
**user**-role row with a framing prefix, because the text is not the agent speaking in that turn.
An assistant-role mirror would replay as assistant→assistant and break strict-alternation providers;
a user-role row merges with the next real user message (``mirror_to_session``'s docstring). The
gateway reloads the transcript from state.db every turn, so the row is in her context next time.

Every mirrored row starts with ``[Journal: `` (``PREFIX``). Hermes keeps no mirror metadata in the
database (``_append_to_sqlite`` stores only role and content), so that prefix is the only way to
tell these rows from the user's own words, and every reader of user rows skips them (``is_mirror``):
the context pack, the sweep, the quote verifier and the outage catch-up.

Not in the middle of a turn. ``mirror_to_session`` itself does not check, and a row appended while the
companion is answering lands between his message and her reply: on replay the two user rows merge and
her answer reads as if it ignored the note. So the transcript (``cfg.transcript().mirror``, the Hermes adapter)
resolves the live session, read-only, the way Hermes would (``find_session_by_origin``: the newest live Telegram
session of the chat), and while that conversation holds a live turn lease (``session_turn_leases``) the mirror
waits in ``QUEUE`` here and goes in on a later tick (``flush``), its framing still saying when it was sent. The
session id is passed to Hermes explicitly, so the row lands in the session that was checked.

Best-effort and Hermes-only: standalone (no ``HERMES_HOME``, or Hermes not importable) it is a no-op,
``hermes.mirror_to_chat: false`` turns it off, and a failed mirror is logged and never fails a send.
Each send's ledger record gets ``mirrored``: true, false, or "queued" until it goes in.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from .config import Config
from .ports import NotMirrored

log = logging.getLogger("journal.mirror")
PREFIX = "[Journal: "
SOURCE_LABEL = "journal"
CAP = 6000  # characters of body kept in a mirrored row (a week review is the longest thing sent)
QUEUE = "mirror-queue.json"
QUEUE_MAX_AGE = timedelta(hours=12)  # a mirror that could not go in by then is dropped (stuck lease)


_Skip = NotMirrored  # not mirrored, for a reason worth one log line


def is_mirror(text: str | None) -> bool:
    """A row the journal mirrored into the chat: the companion's words, never the user's."""
    return (text or "").lstrip().startswith(PREFIX)


def enabled(cfg: Config) -> bool:
    return (cfg.hermes.get("mirror_to_chat", True) is not False and cfg.hermes_home is not None
            and bool(cfg.chat_id))


def frame(what: str, body: str) -> str:
    """``[Journal: <what>]`` and the full text underneath."""
    body = (body or "").strip()
    if len(body) > CAP:
        body = body[:CAP].rsplit("\n", 1)[0] + "\n[…]"
    return f"{PREFIX}{what.strip()}]\n{body}"


def stamp(cfg: Config, at: datetime) -> str:
    """'23:01 IST' in the user's timezone."""
    return at.astimezone(cfg.tz).strftime("%H:%M %Z")


# ── the live session (read by the transcript adapter) ────────────────────────

def turn_running(db: Path, session_id: str, now: float | None = None) -> bool:
    """Whether the conversation holds an unexpired turn lease (``journal.hermes.transcript.turn_running``)."""
    from .hermes.transcript import turn_running as running
    return running(db, session_id, now)


# ── writing ──────────────────────────────────────────────────────────────────

def _mirror_to_session(platform: str, chat_id: str, text: str, source_label: str, role: str,
                       session_id: str) -> bool:
    """The write into her session: Hermes' own ``gateway.mirror.mirror_to_session`` through the adapter
    (``journal.hermes.transcript.mirror_to_session``); raises ImportError outside Hermes. The adapter calls it
    through this name, which every test replaces."""
    from .hermes.transcript import mirror_to_session
    return mirror_to_session(platform, chat_id, text, source_label, role, session_id)


def _write(cfg: Config, text: str, flushing: bool = False) -> bool | str:
    """True: in the session; "queued": a turn is running (or Hermes is not importable in this process, so
    the next tick writes it); False: there is no chat session to write to; raises _Skip otherwise."""
    return cfg.transcript().mirror(text, flushing=flushing)


def _mark(cfg: Config, ref, value) -> None:
    """Record ``mirrored`` on the ledger record ``ref`` = (list, id, sub-record key or None)."""
    if not ref:
        return
    from . import ledger
    kind, rid, key = ref
    with ledger.locked(cfg.ledger_path) as data:
        rec = next((r for r in data.get(kind) or [] if r.get("id") == rid), None)
        if rec is not None:
            (rec.setdefault(key, {}) if key else rec)["mirrored"] = value


def to_chat(cfg: Config, what: str, body: str, log_lines: list[str] | None = None, ref=None) -> bool | str:
    """Append ``frame(what, body)`` to the companion's live chat session as a user-role row, or queue it
    while she is mid-turn. Records the outcome on ``ref`` in the ledger. Never raises."""
    if not enabled(cfg):
        if ref:
            _mark(cfg, ref, False)
        return False
    text = frame(what, body)
    item = {"text": text, "what": what, "ref": list(ref) if ref else None,
            "queued_at": datetime.now().astimezone().isoformat()}
    if _read_queue(cfg):  # earlier mirrors are still waiting: keep the order
        _queue(cfg, item)
        _mark(cfg, ref, "queued")
        flush(cfg, datetime.now().astimezone(), log_lines if log_lines is not None else [])
        return "queued" if any(i.get("text") == text for i in _read_queue(cfg)) else True
    try:
        result = _write(cfg, text)
    except _Skip as e:
        result = False
        log.warning("journal: not mirrored into the chat session (%s): %s", e, what)
        if log_lines is not None:
            log_lines.append(f"not mirrored into the chat session ({e}): {what}")
    if result == "queued":
        _queue(cfg, item)
        if log_lines is not None:
            log_lines.append(f"mirror queued for a later tick (the companion is mid-turn, or Hermes is not "
                             f"importable here): {what}")
    _mark(cfg, ref, result)
    return result


# ── the queue (mirrors waiting for a turn to end) ────────────────────────────

def _queue_path(cfg: Config) -> Path:
    return cfg.state_dir / QUEUE


def _read_queue(cfg: Config) -> list[dict]:
    try:
        data = json.loads(_queue_path(cfg).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _write_queue(cfg: Config, items: list[dict]) -> None:
    path = _queue_path(cfg)
    if not items:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{QUEUE}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _queue(cfg: Config, item: dict) -> None:
    _write_queue(cfg, _read_queue(cfg) + [item])  # only the tick writes it, under the tick lock


def flush(cfg: Config, now: datetime, log_lines: list[str]) -> None:
    """Write queued mirrors in order once no turn is running; drop any older than ``QUEUE_MAX_AGE``."""
    items = _read_queue(cfg)
    if not items:
        return
    left = []
    for i, item in enumerate(items):
        ref = tuple(item["ref"]) if item.get("ref") else None
        try:
            queued = datetime.fromisoformat(item.get("queued_at") or "")
        except ValueError:
            queued = now
        if not enabled(cfg) or now - queued > QUEUE_MAX_AGE:
            log_lines.append(f"mirror dropped, never got a turn boundary: {item.get('what')}")
            _mark(cfg, ref, False)
            continue
        try:
            result = _write(cfg, item["text"], flushing=True)
        except _Skip as e:
            log_lines.append(f"not mirrored into the chat session ({e}): {item.get('what')}")
            _mark(cfg, ref, False)
            continue
        if result is False:  # no chat session to write to (no transcript): nothing was mirrored
            log_lines.append(f"not mirrored into the chat session (no chat session): {item.get('what')}")
            _mark(cfg, ref, False)
            continue
        if result == "queued":  # still mid-turn: keep this one and everything after it, in order
            left = items[i:]
            break
        _mark(cfg, ref, True)
        log_lines.append(f"mirrored (after a turn ended): {item.get('what')}")
    _write_queue(cfg, left)
