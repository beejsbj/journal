"""His words from a face (the web app, the CLI, MCP), written by the engine: ``answer`` and ``note`` (OPEN-PROMPTS).

Every face that saves his words calls here, so the rules hold in one place:

* **The refusals** (rule 7). Words that start with the off-the-record command, or have a line that does (the chat
  takes a merged burst with ``/x`` on a later line as off the record), a line shaped like the engine's own marker
  (``%% journal … %%``, which could pass for the other voice or a program's phase), and the transcript-pending
  placeholder are refused whole, before anything is written: ``Refused`` with a code a face maps to its own answer.
* **The slot** (``slots.resolve``). ``answer`` writes into the slot the practice's words belong to: a due or open
  slot, else the journal day's missed one, else the next one later that day (doing it early fills it, and the tick
  skips its send), else an extra entry of that practice. Never a /j note, never an answered record. A slot with no
  record gets one now, in the same locked call as the capture: ``message_id: None``, ``sent_at`` the time of his
  words, ``opened_via`` the face, the practice's plain opener and its question snapshot. The record a failed send
  left is filled in the same way.
* **Not a chat boundary** (rule 7). Nothing here writes ``sent.jsonl``, ``send-times.json`` or the thread stamps, and
  nothing is mirrored into the companion's session (rule 8): a face's words never end a bare ``/x`` window, start a
  thread or take his next chat message. She reads them where she reads the ledger and the entries.

Both run under the ledger lock (a face bounds the wait with ``ledger.deadline``). Both return what
``capture.record`` does, ``{prompt, path, rule}``, with ``slot`` and ``step`` for ``answer``; a repeated
``client_id`` returns the first write's result with ``duplicate``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import audio, capture, channel, compose, entries, ledger, library, slots
from .config import Config

FACES = tuple(entries.FACES)  # web, cli, mcp
FUTURE_SLACK = timedelta(minutes=5)  # a face's clock a little ahead of the server's (as ``serve/writes`` allows)


class Refused(ValueError):
    """Words the journal turns away; nothing was written. ``code``: ``off-the-record``, ``reserved-line``,
    ``placeholder``, ``empty``, ``not-a-practice`` or ``bad-time``; ``str(e)`` says why, in words a face can show."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def refuse(cfg: Config, text: str | None) -> None:
    """Raise ``Refused`` when ``text`` cannot be his words in the journal (the three refusals of rule 7)."""
    text = text or ""
    if capture.opt_out(cfg, text) is not None:
        raise Refused("off-the-record", "this starts with the off-the-record command, so it stays out of the journal: "
                                        "nothing was saved")
    if capture.opt_out_inside(cfg, text):
        raise Refused("off-the-record", "a line of this starts with the off-the-record command, so none of it goes "
                                        "into the journal: nothing was saved")
    if entries.ENGINE_MARK.search(text):
        raise Refused("reserved-line", "a line shaped like `%% journal … %%` is the engine's own marker and cannot be "
                                       "part of his words: nothing was saved")
    if audio.TRANSCRIBING in text:
        raise Refused("placeholder", f"“{audio.TRANSCRIBING}” is the journal's line for a recording still being "
                                     "transcribed and cannot be part of his words: nothing was saved")


def _words(cfg: Config, text: str | None, media: str | None) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    refuse(cfg, text)
    if not text.strip() and not media:
        raise Refused("empty", "there are no words to save: nothing was saved")
    return text


def _when(at: datetime | None, now: datetime) -> datetime:
    """The time on his words: ``at`` (with its offset, not in the future), else now."""
    if at is None:
        return now
    if at.tzinfo is None or at.utcoffset() is None:
        raise Refused("bad-time", "the time on these words has no offset, so it cannot be placed: nothing was saved")
    if at > now + FUTURE_SLACK:
        raise Refused("bad-time", "the time on these words is in the future: nothing was saved")
    return at


def _via(via: str) -> str:
    if via not in FACES:
        raise ValueError(f"via must be one of {', '.join(FACES)} (got {via!r})")
    return via


def _practice(cfg: Config, practice) -> library.Practice:
    try:
        p = library.resolve(practice, cfg)
    except Exception:  # noqa: BLE001 - an unknown id, or a library that cannot load
        raise Refused("not-a-practice", f"no practice {practice!r}: nothing was saved") from None
    if not isinstance(p, library.Practice) or p.kind != library.PRACTICE:
        raise Refused("not-a-practice", f"{practice!r} is not a practice: nothing was saved")
    return p


def _face_fields(cfg: Config, at: datetime, via: str) -> dict:
    """When and where his first words on a face were written: ``sent_at`` (the many readers that need a time keep
    working; nothing that reads the chat reads a record without ``message_id``), and ``opened_*``, which a later
    delivery (``tick.send_prompt``'s attach) leaves as they are."""
    return {"sent_at": at.isoformat(), "opened_via": via, "opened_at": at.isoformat(),
            "opened_at_local": at.astimezone(cfg.tz).strftime("%H:%M %Z")}


def _record_for(cfg: Config, data: dict, slot: slots.Slot, p: library.Practice, at: datetime, via: str) -> dict:
    """The slot's record, made or filled in now (caller holds the lock)."""
    rec = slot.record
    if rec is not None and not slots.failed_send(rec):
        return rec  # a delivered prompt (due or open, or missed): his words answer it as a chat reply would
    base = {"stem": slot.id, "opener": compose.plain_opener(p, cfg.names), "opener_source": f"plain:{via}",
            **library.record_fields(p), "questions": library.question_snapshot(p) or None}
    if rec is None:
        rec = channel.stamp(cfg, {"id": slot.id, "kind": p.id, "local_date": slot.local_date, "chat_id": cfg.chat_id,
                                  "message_id": None,
                                  **({"scheduled": slot.at.isoformat()} if slot.at is not None else {})})
        data["prompts"].append(rec)
    for k, v in base.items():  # (a failed send's record has none of these; one it has is kept)
        rec.setdefault(k, v)
    rec.update(_face_fields(cfg, at, via))
    return rec


def answer(cfg: Config, practice, text: str | None, at: datetime | None = None, *, via: str,
           now: datetime | None = None, client_id: str | None = None, media: str | None = None, ext: str = ".ogg",
           meta: dict | None = None, key_of: Callable[[Path], str | None] | None = None) -> dict:
    """His words for ``practice``, written at ``at`` (default now; a face may let him backdate them), into the slot
    ``slots.resolve`` gives, read at ``now`` (the server's clock). ``via``: the face (``web``, ``cli``, ``mcp``),
    which is the capture's rule, source and via. ``media``: an audio file kept beside the entry and transcribed by
    the tick, as the web app's upload is. Raises ``Refused`` (nothing written)."""
    via = _via(via)
    words = _words(cfg, text, media)
    now = now or datetime.now(timezone.utc)
    at = _when(at, now)
    p = _practice(cfg, practice)
    with ledger.locked(cfg.ledger_path) as data:
        done = ledger.capture_of_client(data, client_id)
        if done:
            return {**capture.planned_info(cfg, done[0]), "rule": done[1].get("rule", ""), "duplicate": True}
        slot = slots.resolve(cfg, p, at, now=now, data=data)
        rec = _record_for(cfg, data, slot, p, at, via)
        info = capture._web_record(cfg, data, rec, via, words, at, client_id, media, ext, meta, key_of, via=via)
        return {**info, "slot": slot.id, "step": slot.step}


def note(cfg: Config, text: str | None, at: datetime | None = None, *, via: str, now: datetime | None = None,
         client_id: str | None = None, media: str | None = None, ext: str = ".ogg",
         meta: dict | None = None) -> dict:
    """His words as a note of their own (a /j note: ``<date>-j``, ``-j-2`` …), stamped ``at`` (default now). Raises
    ``Refused`` (nothing written)."""
    via = _via(via)
    words = _words(cfg, text, media)
    now = now or datetime.now(timezone.utc)
    at = _when(at, now)
    with ledger.locked(cfg.ledger_path) as data:
        done = ledger.capture_of_client(data, client_id)
        if done:
            return {**capture.planned_info(cfg, done[0]), "rule": done[1].get("rule", ""), "duplicate": True}
        rec = capture.new_note_prompt(data, cfg, at)
        return capture._web_record(cfg, data, rec, "/j", words, at, client_id, media, ext, meta, via=via)
