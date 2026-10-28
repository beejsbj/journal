"""Which practices are due, evaluated in the configured local timezone.

The tick runs every few minutes on the host clock; due-ness is decided here in local wall time, so travel and
DST never require rewriting cron schedules. What is scheduled comes from the practice library
(``library.scheduled``: every enabled practice with a slot in its file).

Besides practices there are one-offs: the eight interrupts of fix-life session 10, which a program run holds in its
``scheduled`` list once he has confirmed a day and wake time (``programs.confirm_schedule``). Each is a wall-clock
time (``local``, no zone) that is read in the *current* timezone, so a DST change or ``journal_set_location`` between
the confirmation and the day moves nothing off the clock he stated; ``due`` returns them with the practices.

A practice with several slots is due once per slot: the first slot's prompt is ``<date>-<id>`` (so the rituals'
ids and entry stems are what they always were), a later one ``<date>-<id>-<n>``, the same rule as entry stems
(``entries.stem_for``).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from . import entries, library, programs
from .config import Config
from .library import Practice


@dataclass(frozen=True)
class Due:
    """One slot of one practice, due now."""
    practice: Practice
    slot: int  # 1-based position in the practice's schedule
    at: datetime  # the slot's local time today

    @property
    def local_date(self) -> str:
        return self.at.date().isoformat()

    @property
    def prompt_id(self) -> str:
        return prompt_id(self.local_date, self.practice.id, self.slot)


def prompt_id(local_date: str, pid: str, slot: int = 1) -> str:
    """The ledger id (and entry stem) of a practice's prompt: ``2026-10-14-evening``, ``2026-10-14-water-2``."""
    return entries.stem_for(local_date, pid, slot)


def slots_due(cfg: Config, now_local: datetime, sent_ids: set[str]) -> list[Due]:
    """Every slot due now and not yet sent (``sent_ids`` holds prompt ids), in library order."""
    out = []
    for p in library.scheduled(cfg):
        for n, slot in enumerate(p.schedule, 1):
            if slot.weekday is not None and now_local.weekday() != slot.weekday:
                continue
            hh, mm = (int(x) for x in slot.time.split(":"))
            at = now_local.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if not (at <= now_local < at + timedelta(hours=cfg.grace_hours)):
                continue
            due = Due(p, n, at)
            if due.prompt_id in sent_ids:
                continue
            out.append(due)
    return out


@dataclass(frozen=True)
class OneOff:
    """One scheduled one-off of a program run (an interrupt), due now."""
    run: str
    program: str
    item: dict  # the run's ``scheduled`` entry
    at: datetime  # its wall-clock time in the current timezone

    @property
    def prompt_id(self) -> str:
        return f"{self.run}-{self.item['id']}"


def wall_clock(cfg: Config, local: str) -> datetime:
    """``2026-11-02T11:00`` (a wall-clock time with no zone) as that time in the configured timezone. A time the
    clocks skip (spring forward) is read with the offset from before the change, so it is the hour after; one they
    repeat (fall back) is the first of the two."""
    return datetime.fromisoformat(local).replace(tzinfo=cfg.tz)


def oneoffs_due(cfg: Config, now_local: datetime, sent_ids: set[str]) -> list[OneOff]:
    """Every pending one-off of a live run whose time has come and is still inside the grace window, oldest first.
    Past the window it is not due any more (``programs.send_interrupts`` records it as missed)."""
    out = []
    for run in programs.all_runs(cfg):
        if run.get("status") in programs.CLOSED:
            continue
        for item in run.get("scheduled") or []:
            if item.get("status", "pending") != "pending":
                continue
            at = wall_clock(cfg, item["local"])
            try:
                start = at.astimezone(timezone.utc)  # (two datetimes of one zone compare by wall clock: use instants)
                if not (start <= now_local.astimezone(timezone.utc) < start + timedelta(hours=cfg.grace_hours)):
                    continue
            except OverflowError:  # a day at the end of the calendar never comes (``send_interrupts`` records it)
                continue
            one = OneOff(run["id"], run["program"], item, at)
            if one.prompt_id not in sent_ids:
                out.append(one)
    return sorted(out, key=lambda o: o.at.astimezone(timezone.utc))  # (by instant: a skipped hour reorders wall times)


def due(cfg: Config, now_local: datetime, sent_ids: set[str]) -> list[tuple[str, datetime]]:
    """(id, scheduled local time) for each practice slot and each one-off due now: ``slots_due`` and ``oneoffs_due``
    for callers that only need ids (a one-off's id is its prompt id)."""
    return [(d.practice.id, d.at) for d in slots_due(cfg, now_local, sent_ids)] + [
        (o.prompt_id, o.at) for o in oneoffs_due(cfg, now_local, sent_ids)]
