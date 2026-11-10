"""Slot states and slot resolution: where a practice stands, whatever face it was done on (OPEN-PROMPTS).

A record is written when something happens: a delivery to the chat (``tick.send_prompt``) or his first words on a face
(``actions.answer``). A slot that has neither is still a state, derived from the schedule. Every scheduled slot of a
day has exactly one:

* ``upcoming``: its time has not come, and it has no record;
* ``due``: its time has come, it is inside its window, and it has no record, or only the record a failed send leaves
  (``send_attempts``, no ``message_id``, no captures). A chat install shows this until the tick sends it, or while a
  send keeps failing; a no-chat install shows it as the open prompt;
* ``open``: a delivered record, unanswered, inside its window measured from the delivery (as the chat has always
  read it, ``capture.window``);
* ``answered``: a record with captures, made by any face;
* ``missed``: its window is over, unanswered, with or without a record.

A slot's window, before anything was delivered, is the one a delivery at the slot's time would have had
(``capture.window``): the practice's ``collapse_hours``, else ``windows.collapse_hours``.

This module only reads. What reads the chat (capture matching, the sweep, folding, thread boundaries, the ``/x``
windows) does not come here: it keeps reading delivered records (``message_id``), so nothing a face writes can reach
it. ``resolve`` is the one answer to "which slot do these words go to" (and "which slot does a deliberate delivery
send"), for every face.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from . import capture, entries, ledger, library
from .config import Config, day_ends_at
from .library import Practice

UPCOMING, DUE, OPEN, ANSWERED, MISSED = "upcoming", "due", "open", "answered", "missed"
FACES = entries.FACES  # ``opened_via`` → where he wrote, as said aloud


@dataclass(frozen=True)
class Slot:
    """One slot of one practice on one day, and its state. ``n`` is its place in the practice's schedule (None for an
    extra entry: a deliberate send or words off the schedule); ``at`` its scheduled local time (for an extra, when its
    record says it was asked for, or None); ``record`` the ledger record it has, if any. ``step``: which step of
    ``resolve`` chose it (0 when it was not resolved)."""
    id: str
    practice: str
    local_date: str
    n: int | None
    at: datetime | None
    record: dict | None
    state: str
    step: int = 0

    @property
    def delivered(self) -> bool:
        return delivered(self.record)

    @property
    def time(self) -> datetime | None:
        """When the slot happened: its delivery, else its scheduled time, else when he wrote (a face-made extra)."""
        if self.delivered:
            return _parse(self.record.get("sent_at")) or self.at
        return self.at or _parse((self.record or {}).get("sent_at"))


def _parse(iso) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if isinstance(iso, str) and iso else None
    except ValueError:
        return None


def delivered(record: dict | None) -> bool:
    """A record the chat has: sent with a message id (or, from before message ids were kept, ``backfilled``)."""
    return bool(record) and bool(record.get("sent_at")) and bool(record.get("message_id") or record.get("backfilled"))


def failed_send(record: dict | None) -> bool:
    """The record a failed send leaves: attempts counted, nothing delivered, nothing written."""
    return bool(record) and not record.get("message_id") and not record.get("captures") \
        and not record.get("backfilled") and not record.get("opened_via")


def face_made(record: dict | None) -> bool:
    """A record his first words on a face made (``opened_via``), whether or not a delivery attached to it since."""
    return bool(record) and bool(record.get("opened_via"))


def window(cfg: Config, practice: Practice | None, record: dict | None = None) -> timedelta:
    """How long a slot stays open: a delivered record's own window (``capture.window``), else the window a delivery
    at the slot's time would have had."""
    if record is not None and record.get("collapse_hours") is not None:
        return capture.window(cfg, record)
    if practice is None or getattr(practice, "builtin", True):
        return capture.window(cfg, {})
    return capture.window(cfg, {"collapse_hours": practice.collapse_hours})


def state_of(cfg: Config, practice: Practice | None, at: datetime | None, record: dict | None,
             now: datetime) -> str:
    """The state of one slot scheduled at ``at`` (None: off the schedule) with ``record`` (None: no record), read at
    ``now``."""
    if record and record.get("captures"):
        return ANSWERED
    if delivered(record):
        try:
            sent = datetime.fromisoformat(record["sent_at"])
            return OPEN if now - sent < window(cfg, practice, record) else MISSED
        except (TypeError, ValueError):
            return MISSED
    if at is None:
        at = _parse((record or {}).get("scheduled"))
    if at is None:
        return MISSED
    if now < at:
        return UPCOMING
    return DUE if now < at + window(cfg, practice, record) else MISSED


def _slot_time(cfg: Config, local_date: str, hhmm: str) -> datetime:
    hh, mm = (int(x) for x in hhmm.split(":"))
    return datetime.combine(date.fromisoformat(local_date), time(hh, mm), tzinfo=cfg.tz)


def _practice_records(rec: dict) -> bool:
    """A record that can be a practice's slot: not a note, not a program phase or interrupt."""
    return bool(rec.get("id")) and rec.get("kind") not in entries.FREEFORM_IDS and not rec.get("program") \
        and not rec.get("interrupt") and not rec.get("run")


def scheduled_on(practice: Practice, local_date: str) -> list[tuple[int, library.Slot]]:
    """(n, slot) of each slot of an enabled practice that falls on ``local_date``."""
    if not getattr(practice, "enabled", True):
        return []
    weekday = date.fromisoformat(local_date).weekday()
    return [(n, s) for n, s in enumerate(practice.schedule, 1) if s.weekday is None or s.weekday == weekday]


def day(cfg: Config, local_date: str, now: datetime, *, data: dict | None = None,
        practice: Practice | str | None = None) -> list[Slot]:
    """Every slot of ``local_date``: each scheduled slot of each scheduled practice (in library order, slot order),
    then each record of that day that is no scheduled slot (a deliberate send, an extra entry), by its time. With
    ``practice``, that practice's only. Notes and program phases are no slots."""
    data = data if data is not None else ledger.snapshot(cfg.ledger_path)
    records = {p["id"]: p for p in data["prompts"] if p.get("local_date") == local_date and _practice_records(p)}
    only = None if practice is None else (practice.id if isinstance(practice, Practice) else practice)
    if only is None:
        practices = library.scheduled(cfg)
    else:
        try:
            practices = [library.resolve(practice, cfg)]
        except Exception:  # noqa: BLE001 - a practice the library lost still has its records
            practices = []
    out: list[Slot] = []
    taken: set[str] = set()
    for p in practices:
        for n, slot in scheduled_on(p, local_date):
            pid = entries.stem_for(local_date, p.id, n)
            at = _slot_time(cfg, local_date, slot.time)
            rec = records.get(pid)
            taken.add(pid)
            out.append(Slot(pid, p.id, local_date, n, at, rec, state_of(cfg, p, at, rec, now)))
    extras = []
    for pid, rec in records.items():
        if pid in taken or (only is not None and rec.get("kind") != only):
            continue
        p = library.for_prompt(cfg, rec)
        at = _parse(rec.get("scheduled"))
        at = at.astimezone(cfg.tz) if at else None
        extras.append(Slot(pid, str(rec.get("kind")), local_date, None, at, rec, state_of(cfg, p, at, rec, now)))
    extras.sort(key=lambda s: (s.time or datetime.max.replace(tzinfo=timezone.utc)).astimezone(timezone.utc))
    return out + extras


def history(cfg: Config, start: date, end: date, now: datetime, data: dict | None = None, *,
            derived: bool | None = None) -> list[Slot]:
    """The slots from ``start`` to ``end`` (local dates, both included) that count in his history: a slot delivered
    to the chat or answered on any face, and, with ``derived`` (by default: on a journal with no chat), a scheduled
    slot that is due or went by with neither. On a chat install a slot the chat never got (the tick was down) was
    never before him, so it is no miss of his, as the history always read it; on a no-chat install the due slot *is*
    the prompt, and its passing is the miss. A derived slot counts only from its practice's first record in the ledger
    on: the days before he had the practice, or before the ledger kept anything, read as nothing, not as misses."""
    derived = (not cfg.has_channel) if derived is None else derived
    data = data if data is not None else ledger.snapshot(cfg.ledger_path)
    first: dict[str, str] = {}
    for rec in data["prompts"]:
        if _practice_records(rec) and rec.get("local_date"):
            kind = str(rec.get("kind"))
            first[kind] = min(first.get(kind, rec["local_date"]), rec["local_date"])
    out = []
    d = start
    while d <= end:
        for s in day(cfg, d.isoformat(), now, data=data):
            if not (delivered(s.record) or (s.record or {}).get("captures")):
                if not derived or s.n is None or s.practice not in first or d.isoformat() < first[s.practice]:
                    continue
            out.append(s)
        d += timedelta(days=1)
    return out


def journal_day(cfg: Config, at: datetime) -> date:
    """The journal day of a moment: its local date, except that before ``windows.day_ends_at`` (default 04:00) it
    is still the day before. An evening answered at 00:30 is that evening's."""
    local = at.astimezone(cfg.tz)
    hh, mm = day_ends_at(cfg)
    return local.date() - timedelta(days=1) if (local.hour, local.minute) < (hh, mm) else local.date()


def resolve(cfg: Config, practice: Practice | str, at: datetime, *, now: datetime | None = None,
            data: dict | None = None, delivery: bool = False) -> Slot:
    """The slot words written at ``at`` go to (or, with ``delivery``, the slot a deliberate send delivers: ``at`` is
    then now). States are read at ``now`` (the server's clock), and a delivered prompt takes words only when it was
    sent at or before ``at`` (WP10: backdated words never answer a prompt sent after them). In order:

    1. a slot of the practice that is due or open now, today's or yesterday's, newest first, whose time is at or
       before ``at`` (an evening answered at 00:30 takes last night's);
    2. else the journal day's newest missed slot whose time is at or before ``at`` (a slot between midnight and
       ``day_ends_at`` belongs to the journal day before its date, as words written then do);
    3. else the next slot later the same journal day that has no record (doing it early fills it; the tick then
       skips its send); one already delivered is not taken;
    4. else an extra entry of the practice for the journal day: slot 1's id when no slot of the practice falls on
       that day and the id is free, otherwise the next free number after the scheduled slots (free: no record, no
       entry file, and not another practice's id).

    Words dated before the previous journal day go straight to step 4: an old miss stays a miss. A deliberate
    delivery before ``day_ends_at`` takes today's calendar date instead of the journal day before
    (``_by_calendar``), unless the practice's nearest scheduled slot is last night's (``_last_night_is_nearer``):
    then it resolves as words do. The slot returned is never an answered one, so a delivery never lands on words he wrote on a
    face, and choosing a practice always starts something. Raises ``library.UnknownDefinition`` for an id the
    library does not have."""
    p = library.resolve(practice, cfg)
    now = now or datetime.now(timezone.utc)
    data = data if data is not None else ledger.snapshot(cfg.ledger_path)
    jday = journal_day(cfg, at)
    today = at.astimezone(cfg.tz).date()
    if delivery and jday != today and not _last_night_is_nearer(cfg, p, at, now, data, jday, today):
        return _by_calendar(cfg, p, at, now, data, today)
    if jday >= journal_day(cfg, now) - timedelta(days=1):
        today = now.astimezone(cfg.tz).date()
        found = _step1(cfg, p, at, now, data, today, delivery) or _step2(cfg, p, at, now, data, jday, delivery) \
            or _step3(cfg, p, at, now, data, jday)
        if found is not None:
            return found
    return _extra(cfg, p, jday, data, now)


def _before(slot: Slot, at: datetime, delivery: bool) -> bool:
    if delivery:
        return True  # a delivery is made now: every slot it can take is behind it
    when = slot.time
    return when is not None and when <= at


def _newest(found: list[Slot]) -> list[Slot]:
    return sorted(found, key=lambda s: s.time.astimezone(timezone.utc) if s.time else datetime.min.replace(
        tzinfo=timezone.utc), reverse=True)


def _with_step(slot: Slot, step: int) -> Slot:
    return Slot(slot.id, slot.practice, slot.local_date, slot.n, slot.at, slot.record, slot.state, step)


def _step1(cfg, p, at, now, data, today: date, delivery: bool) -> Slot | None:
    found = [s for d in (today, today - timedelta(days=1)) for s in day(cfg, d.isoformat(), now, data=data, practice=p)
             if s.state in (DUE, OPEN) and _before(s, at, delivery)]
    return _with_step(_newest(found)[0], 1) if found else None


def _of_journal_day(cfg, p, now, data, jday: date) -> list[Slot]:
    """The practice's slots that belong to journal day ``jday``: its date's slots from ``day_ends_at`` on, and the next
    date's before it (a 00:30 slot is the night of the day before). A slot belongs by its scheduled time, so a late
    delivery does not move it; an extra by when it happened; one with no time stays with its date."""
    out = []
    for d in (jday, jday + timedelta(days=1)):
        for s in day(cfg, d.isoformat(), now, data=data, practice=p):
            when = s.at if s.at is not None else s.time
            if (journal_day(cfg, when) if when is not None else d) == jday:
                out.append(s)
    return out


def _step2(cfg, p, at, now, data, jday: date, delivery: bool) -> Slot | None:
    found = [s for s in _of_journal_day(cfg, p, now, data, jday)
             if s.state == MISSED and _before(s, at, delivery)]
    return _with_step(_newest(found)[0], 2) if found else None


def _step3(cfg, p, at, now, data, jday: date) -> Slot | None:
    later = [s for s in _of_journal_day(cfg, p, now, data, jday)
             if s.n is not None and s.at is not None and s.at > at and (s.record is None or failed_send(s.record))]
    later.sort(key=lambda s: s.at.astimezone(timezone.utc))
    return _with_step(later[0], 3) if later else None


def _last_night_is_nearer(cfg, p, at, now, data, jday: date, today: date) -> bool:
    """Before ``day_ends_at``, whether the practice's scheduled slot nearest to ``at`` belongs to the journal day
    before and not to today's date: ``/start evening`` at 00:30 means last night's 23:00, 1.5 hours ago, not
    tonight's, 22.5 hours ahead; ``/start morning`` at 03:30 means today's 06:00. Nearness only picks the day; the
    steps of ``resolve`` then pick the slot. A tie, a slot that is both (00:30 on today's date) and a practice with no
    schedule are today's; with no slot on today's date (a weekday practice on Saturday), last night's is nearer
    when it is under twelve hours behind."""
    def gaps(found):  # in UTC: elapsed time, not wall-clock, on a night the clocks change
        return [abs(s.at.astimezone(timezone.utc) - at.astimezone(timezone.utc)) for s in found]
    todays = [s for s in day(cfg, today.isoformat(), now, data=data, practice=p) if s.n is not None and s.at is not None]
    mine = {s.id for s in todays}
    before = [s for s in _of_journal_day(cfg, p, now, data, jday)
              if s.n is not None and s.at is not None and s.id not in mine]
    if not before:
        return False
    return min(gaps(before)) < (min(gaps(todays)) if todays else timedelta(hours=12))


def _by_calendar(cfg, p, at, now, data, today: date) -> Slot:
    """A deliberate delivery (``/start``, ``journal send``, *Send to chat*) made before ``day_ends_at`` takes today's
    calendar date for its slot, not the journal day before (the owner, 2026-11-09), when today's scheduled slot is
    the nearer one (``_last_night_is_nearer``): the same steps over today's
    slots only. A due or open one; else the newest missed one; else the next later one with no record, or only a
    failed send's; else an extra entry dated today. So ``/start morning`` at 03:30 delivers today's morning, and the
    tick's 06:00 send is then done. Words written at 03:30 still belong to the journal day before (``resolve``)."""
    mine = day(cfg, today.isoformat(), now, data=data, practice=p)
    for step, states in ((1, (DUE, OPEN)), (2, (MISSED,))):
        found = [s for s in mine if s.state in states]
        if found:
            return _with_step(_newest(found)[0], step)
    later = sorted((s for s in mine if s.n is not None and s.at is not None and s.at > at
                    and (s.record is None or failed_send(s.record))), key=lambda s: s.at.astimezone(timezone.utc))
    return _with_step(later[0], 3) if later else _extra(cfg, p, today, data, now)


def _extra(cfg: Config, p: Practice, jday: date, data: dict, now: datetime) -> Slot:
    local_date = jday.isoformat()
    ids = {q.get("id") for q in data["prompts"]}
    try:  # (``<id>-<n>`` may be another practice's id: its slot's stem, which an extra must never take)
        owned = set(library.load(cfg).practices)
    except Exception:  # noqa: BLE001 - a library that cannot load still has its records and files
        owned = set()

    def free(n: int) -> bool:
        stem = entries.stem_for(local_date, p.id, n)
        return f"{p.id}-{n}" not in owned and stem not in ids and not entries.entry_path(cfg.root, stem).exists()
    if not scheduled_on(p, local_date) and free(1):
        n = 1
    else:
        n = max(len(p.schedule), 1) + 1
        while not free(n):
            n += 1
    return Slot(entries.stem_for(local_date, p.id, n), p.id, local_date, None, None, None, DUE, 4)
