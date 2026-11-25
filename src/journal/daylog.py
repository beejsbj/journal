"""The day log (issue #82): one journal day's lines from every entry filed under it, in time order.

Read-only over the entry files, as they are: a /j note, a chat thread that became a note, a web note, a prompt
entry's words and the conversation under it. Nothing is merged or rewritten; each line names the entry and the block
it came from, so a face links back to it. Her replies are lines of their own (``kind: "hers"``), never folded into his.

Only entry files are read (the ones the Entries list shows, ``document.filter_entries``), plus the ledger for the
moment an entry's first words were captured. Entries never hold off-the-record words (``/x`` is refused before
anything is written), so nothing here can show them; the chat transcript is never opened.

**Times.** An entry's words headings carry a time only (``### 23:12 IST · text``); a conversation marker or her reply
carries a weekday and date when it falls on another day than the entry's (``**Fri 16 Oct 00:30**``). The two sections
are placed apart, since an *Add* appends to the words after a conversation may have begun under them. A block whose
time matches a capture the ledger still has for the entry (the same local hour and minute, each capture used once, in
order: the words' captures for the words section, the ``conversation`` ones for the conversation) takes that
capture's day: so a line added days later, or backdated, is placed when it was written. Otherwise a section is walked
in file order (which is the order things were written): it starts on the entry's day (the calendar day of its first
capture when the ledger has it, else the entry's date); a dated marker sets the day; a time earlier than the one
before it on the same day means the next day. With no ledger record, an entry whose first time is before
``windows.day_ends_at`` starts on the day after its date (the journal day: an evening answered at 00:30, a note put
down then). ``shortcut: once the ledger has pruned an entry (weeks), its blocks are placed by file order alone: a note
filed by the calendar date before the journal-day change is placed a day late, and a block added days later reads as
the entry's day (a backdated one earlier than the block before it, as the next day); upgrade by stamping the
capture's date into the words heading if it matters.``
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path

from . import document, entries
from .config import Config, day_ends_at

_TIME = re.compile(r"(\d{1,2}):(\d{2})")
_DATED = re.compile(r"^(?:[A-Za-z]{3} )?(\d{1,2}) ([A-Za-z]{3})\b")
_MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                       "dec"), 1)}
MAX_LINE = 4000   # characters of one line sent to a face; the entry has it whole


def _clock(at: str | None) -> tuple[int, int] | None:
    m = _TIME.search(at or "")
    if not m:
        return None
    hh, mm = int(m.group(1)), int(m.group(2))
    return (hh, mm) if hh < 24 and mm < 60 else None


def _dated(at: str | None, near: date) -> date | None:
    """The date a marker names (``Fri 16 Oct 00:30``), in the year that puts it nearest after ``near``."""
    m = _DATED.match((at or "").strip())
    if not m:
        return None
    month = _MONTHS.get(m.group(2).lower())
    if not month:
        return None
    for year in (near.year, near.year + 1, near.year - 1):
        try:
            d = date(year, month, int(m.group(1)))
        except ValueError:
            continue
        if timedelta(days=-1) <= d - near <= timedelta(days=200):
            return d
    return None


def _local(cfg: Config, at: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(at or "").astimezone(cfg.tz)
    except (TypeError, ValueError):
        return None


def _first_capture_day(cfg: Config, rec: dict | None) -> date | None:
    for c in (rec or {}).get("captures") or []:
        local = _local(cfg, c.get("at") if isinstance(c, dict) else None)
        if local is not None:
            return local.date()
    return None


def _capture_times(cfg: Config, rec: dict | None) -> tuple[list[datetime], list[datetime]]:
    """The local times of the entry's captures in the ledger, in order: (the words', the conversation's)."""
    words: list[datetime] = []
    conv: list[datetime] = []
    for c in (rec or {}).get("captures") or []:
        local = _local(cfg, c.get("at") if isinstance(c, dict) else None)
        if local is not None:
            (conv if c.get("rule") == "conversation" else words).append(local)
    return words, conv


def _walk(blocks: list[dict], entry_day: date, start: date, times: list[datetime]) -> list[tuple[date | None, str | None]]:
    """One section of an entry, in file order (see the module's *Times*)."""
    day, last = start, None
    pool = list(times)
    out: list[tuple[date | None, str | None]] = []
    for b in blocks:
        clock = _clock(b.get("at"))
        if clock is None:
            out.append((None, None))
            continue
        named = _dated(b.get("at"), entry_day)
        hit = next((i for i, t in enumerate(pool) if (t.hour, t.minute) == clock), None)
        if hit is not None:
            day = pool.pop(hit).date()   # (the ledger knows when it was written)
        elif named is not None:
            day = named
        elif last is not None and clock < last:
            day += timedelta(days=1)   # (written in order: an earlier hour is the next day)
        last = clock
        out.append((day, f"{clock[0]:02d}:{clock[1]:02d}"))
    return out


def _placed(cfg: Config, blocks: list[dict], entry_day: date, start: date | None,
            captures: tuple[list[datetime], list[datetime]] = ([], [])) -> list[tuple[date | None, str | None]]:
    """(calendar day, "HH:MM") for each block of one entry, in its order (None, None for a block with no time). The
    words section and the conversation are placed apart, each from the entry's start day; ``captures``: the local
    times of the entry's ledger captures (``_capture_times``)."""
    ends = day_ends_at(cfg)
    first = next((c for c in (_clock(b.get("at")) for b in blocks) if c), None)
    begin = start or (entry_day + timedelta(days=1) if first is not None and first < ends else entry_day)
    words = [b for b in blocks if str(b.get("key") or "").startswith("words:")]
    conv = [b for b in blocks if not str(b.get("key") or "").startswith("words:")]
    placed = iter(_walk(words, entry_day, begin, captures[0]))
    talk = iter(_walk(conv, entry_day, begin, captures[1]))
    return [next(placed) if str(b.get("key") or "").startswith("words:") else next(talk) for b in blocks]


def _line(stem: str, b: dict, day: date | None, hhmm: str | None) -> dict:
    text = b.get("text") or ""
    out = {"stem": stem, "key": b["key"], "kind": b["kind"], "date": day.isoformat() if day else None, "time": hhmm,
           "text": text if len(text) <= MAX_LINE else text[:MAX_LINE].rsplit(" ", 1)[0] + " …"}
    if b["kind"] == "his":
        out.update(source=b.get("source") or "text", audio=b.get("audio"), reply_to=b.get("reply_to"))
        if b.get("images"):
            out["images"] = list(b["images"])
        if b.get("meta"):
            out["meta"] = dict(b["meta"])
    return out


def day(cfg: Config, local_date: str, lib=None) -> dict:
    """The day log of journal day ``local_date`` (``YYYY-MM-DD``): ``{date, weekday, entries, lines, reflection, prev,
    next}``. ``entries``: the day's entries in time order (``{stem, kind, practice, title, emoji, time}``); ``lines``:
    every block of theirs, his and hers, ``{stem, key, kind, date, time, text, …}`` in time order (``date`` the calendar
    day the line was written, which after midnight is the day after; a block with no time keeps its place in its
    entry); ``prev`` / ``next``: the nearest days before and after with an entry, or None."""
    want = date.fromisoformat(local_date)
    found = document.all_entries(cfg)
    days = sorted({p.stem[:10] for p, _k in found})
    mine = sorted((p for p, _k in found if p.stem[:10] == local_date), key=lambda p: p.stem)
    records = document.prompt_records(cfg) if mine else {}
    rows: list[tuple[tuple, dict]] = []
    listed = []
    for order, path in enumerate(mine):
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        s = document.summary(cfg, path, lib)
        if s is None:
            continue
        blocks = document.blocks_of(body, path.stem)
        rec = records.get(path.stem)
        placed = _placed(cfg, blocks, want, _first_capture_day(cfg, rec), _capture_times(cfg, rec))
        prev_key = (want.isoformat(), "00:00")
        first_key = next(((d.isoformat(), t) for d, t in placed if d is not None), None)
        if first_key is not None:
            prev_key = first_key
        for i, (b, (d, t)) in enumerate(zip(blocks, placed)):
            if d is not None:
                prev_key = (d.isoformat(), t)
            if not (b.get("text") or "").strip() and not b.get("audio") and not b.get("images"):
                continue
            rows.append(((*prev_key, order, i), _line(path.stem, b, d, t)))
        listed.append(((*(first_key or (want.isoformat(), "")), order),
                       {k: s[k] for k in ("stem", "kind", "practice", "title", "emoji")} | {"time": first_key[1] if first_key else None}))
    rows.sort(key=lambda r: r[0])
    listed.sort(key=lambda r: r[0])
    refl = entries.analyses_on(cfg.root, local_date, "reflection")
    earlier = [d for d in days if d < local_date]
    later = [d for d in days if d > local_date]
    return {"date": local_date, "weekday": want.strftime("%a"),
            "entries": [e for _k, e in listed], "lines": [line for _k, line in rows],
            "reflection": refl[-1].stem if refl else None,
            "prev": earlier[-1] if earlier else None, "next": later[0] if later else None}
