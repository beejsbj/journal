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

* **Opening is a view** (``view``): what the web app's *Write now*, ``journal open`` and MCP's ``journal_start`` show
  is read here and writes nothing, so there is no "opened but not answered" record and an accidental tap changes
  nothing.
* **More words for an entry go through that entry** (``add``: ``journal write --entry``), not through the practice,
  which would fill the next slot (OPEN-PROMPTS, *Adding more words to an entry already written*).

The writes run under the ledger lock (a face bounds the wait with ``ledger.deadline``). They return what
``capture.record`` does, ``{prompt, path, rule}``, with ``slot`` and ``step`` for ``answer``; a repeated
``client_id`` returns the first write's result with ``duplicate``.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import audio, capture, channel, compose, entries, ledger, library, slots
from .config import Config

FACES = tuple(entries.FACES)  # web, cli, mcp
FUTURE_SLACK = timedelta(minutes=5)  # a face's clock a little ahead of the server's (as ``serve/writes`` allows)


class Refused(ValueError):
    """Words the journal turns away; nothing was written. ``code``: ``off-the-record``, ``reserved-line``,
    ``placeholder``, ``empty``, ``not-a-practice`` or ``bad-time``, and for ``add`` ``not-an-entry``,
    ``not-engine-entry`` or ``program-entry``; ``str(e)`` says why, in words a face can show."""

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


def _words(cfg: Config, text: str | None) -> str:
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    refuse(cfg, text)
    return text


def _not_empty(text: str, media: str | None) -> None:
    """Checked after a repeated ``client_id`` is answered: a retry whose upload the first request consumed carries
    no words and no file, and is the first write's result, not a refusal."""
    if not text.strip() and not media:
        raise Refused("empty", "there are no words to save: nothing was saved")


def when(cfg: Config, at: datetime | None, now: datetime) -> datetime:
    """The time on his words: ``at`` (with its offset, not in the future, and one the journal's day arithmetic can
    place: not at the edge of the calendar), else now. A face may check it before he writes (``journal write``)."""
    if at is None:
        return now
    if at.tzinfo is None or at.utcoffset() is None:
        raise Refused("bad-time", "the time on these words has no offset, so it cannot be placed: nothing was saved")
    if at > now + FUTURE_SLACK:
        raise Refused("bad-time", "the time on these words is in the future: nothing was saved")
    try:
        slots.journal_day(cfg, at) - timedelta(days=2)   # (what ``resolve`` reckons with)
    except (OverflowError, ValueError):
        raise Refused("bad-time", "the time on these words is out of range: nothing was saved") from None
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
    return p   # (``enabled: false`` is "not scheduled" only: every face still opens and writes it; the owner, 2026-11-09)


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
    words = _words(cfg, text)
    now = now or datetime.now(timezone.utc)
    at = when(cfg, at, now)
    p = _practice(cfg, practice)
    with ledger.locked(cfg.ledger_path) as data:
        done = ledger.capture_of_client(data, client_id)
        if done:
            return {**capture.planned_info(cfg, done[0]), "rule": done[1].get("rule", ""), "duplicate": True,
                    "slot": done[0].get("id")}
        _not_empty(words, media)
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
    words = _words(cfg, text)
    now = now or datetime.now(timezone.utc)
    at = when(cfg, at, now)
    with ledger.locked(cfg.ledger_path) as data:
        done = ledger.capture_of_client(data, client_id)
        if done:
            return {**capture.planned_info(cfg, done[0]), "rule": done[1].get("rule", ""), "duplicate": True}
        _not_empty(words, media)
        rec = capture.new_note_prompt(data, cfg, at)
        return capture._web_record(cfg, data, rec, "/j", words, at, client_id, media, ext, meta, via=via)


def add(cfg: Config, stem: str, text: str | None, at: datetime | None = None, *, via: str,
        now: datetime | None = None, client_id: str | None = None, media: str | None = None, ext: str = ".ogg",
        meta: dict | None = None, key_of: Callable[[Path], str | None] | None = None) -> dict:
    """More of his words for the entry ``stem``, appended as a block of their own (``capture.add_web_block``, the web
    app's *Add*), stamped ``at`` (default now; not before the entry's day). Only an entry the engine wrote, and not a
    program session's (its answers belong to a phase, which the chat and the web app keep). Raises ``Refused``
    (nothing written)."""
    via = _via(via)
    words = _words(cfg, text)
    _not_empty(words, media)   # (a retry of a block is answered by ``add_web_block``; only the CLI adds this way)
    now = now or datetime.now(timezone.utc)
    at = when(cfg, at, now)
    entry_for(cfg, stem, at)
    info = capture.add_web_block(cfg, stem, words, at, media=media, ext=ext, meta=meta, client_id=client_id,
                                 key_of=key_of, via=via)
    return {**info, "stem": stem}


def entry_for(cfg: Config, stem: str, at: datetime) -> Path:
    """The entry ``add`` writes ``stem``'s words into, or ``Refused``: only an entry the engine wrote, not a program
    session's, and not for words dated before the entry's day. A face may check it before he writes."""
    if entries.entry_file(cfg.root, stem) is None:
        raise Refused("not-an-entry", f"no journal entry {stem!r}: nothing was saved")
    path = entries.engine_file(cfg.root, stem)
    if path is None:
        raise Refused("not-engine-entry", "this file is his own, not one the journal wrote, so no face writes to it: "
                                          "nothing was saved")
    fm = entries.front_matter_of(path)
    if fm.get("program") or fm.get("run"):
        raise Refused("program-entry", "this is a program session's entry: its answers go to the open phase, in the "
                                       "chat or the web app: nothing was saved")
    if at.astimezone(cfg.tz).date().isoformat() < stem[:10]:
        raise Refused("bad-time", "the time on these words is before the day of this entry: nothing was saved")
    return path


# ── opening a practice: a view ───────────────────────────────────────────────

def view(cfg: Config, practice, *, now: datetime | None = None, at: datetime | None = None) -> dict:
    """The practice as a face shows it when he opens it (the web app's *Write now*, ``journal open``, MCP's
    ``journal_start``), and the slot words written now would fill (``slots.resolve`` on a snapshot). Writes nothing.

    ``prompt`` has the entry document's shape (``sent_at_local``, ``opener``, ``opener_source``, ``asked``,
    ``questions``): the practice's plain opener and its questions, or, when the slot is a prompt the chat already
    has, that prompt as it was sent. ``slot``: ``{id, date, state, step, at, delivered}`` (``at`` its scheduled
    local time, None for an extra entry); ``fills``: the same in words. ``at``: the time the words will carry, when he
    backdates them (``journal write --at``), so the view names the slot they will fill. Raises ``Refused``
    (``not-a-practice``, ``bad-time``)."""
    p = _practice(cfg, practice)
    now = now or datetime.now(timezone.utc)
    slot = slots.resolve(cfg, p, when(cfg, at, now), now=now)
    rec = slot.record if slot.delivered else None
    if rec is not None:
        sent = library.snapshot_sections(rec)
        sections = list(sent) if sent else list(p.sections)
        prompt = {"sent_at_local": rec.get("sent_at_local"), "opener": capture.stored_opener(cfg, rec),
                  "opener_source": rec.get("opener_source"), "asked": list(rec.get("asked") or [])}
    else:
        sections = list(p.sections)
        prompt = {"sent_at_local": None, "opener": compose.plain_opener(p, cfg.names), "opener_source": "plain",
                  "asked": []}
    prompt["questions"] = [{"id": q.id, "text": q.text, "section": title} for title, qs in sections for q in qs]
    at = slot.at.astimezone(cfg.tz).strftime("%H:%M") if slot.at is not None and slot.n is not None else None
    return {"practice": p.id, "title": p.title, "emoji": p.emoji, "prompt": prompt,
            "slot": {"id": slot.id, "date": slot.local_date, "state": slot.state, "step": slot.step, "at": at,
                     "delivered": slot.delivered},
            "fills": fills(cfg, slot, now)}


def fills(cfg: Config, slot: slots.Slot, now: datetime) -> str:
    """Where words written at ``now`` go (``slot``, from ``resolve``), in words a face can show under the practice."""
    when = slot.at.astimezone(cfg.tz).strftime("%H:%M") if slot.at is not None else None
    sent = slot.time.astimezone(cfg.tz).strftime("%H:%M") if slot.delivered and slot.time is not None else None
    # (by journal days: a 00:30 slot on Thursday's date is Wednesday night's, as ``resolve`` reckons it)
    of = slots.journal_day(cfg, slot.at) if slot.at is not None else date.fromisoformat(slot.local_date)
    days = (slots.journal_day(cfg, now) - of).days
    whose = "the" if days == 0 else "yesterday's" if days == 1 else "tomorrow's" if days == -1 else \
        f"{of.isoformat()}'s"
    if slot.step == 1:
        return (f"Answers {whose} prompt in the chat, sent at {sent}." if sent
                else f"Fills {whose} {when} slot, due now.")
    if slot.step == 2:
        return (f"Answers {whose} prompt in the chat, sent at {sent}, which went by unanswered." if sent
                else f"Fills {whose} {when} slot, which went by unanswered.")
    if slot.step == 3:
        return f"Fills {whose} {when} slot early" + ("; it will not be sent." if cfg.has_channel else ".")
    return f"Starts an extra entry of this practice ({slot.id})."