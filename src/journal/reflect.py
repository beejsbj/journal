"""The companion's day reflections and week reviews: written into the journal, sent to the chat.

Day reflection (``YYYY-MM-DD-reflection.md``): once per local day that has at least one captured
entry. Due when that day's entry of the practice that closes the day (``closes: day``; the evening
ritual) has been annotated, or at ``reflections.day.fallback_time`` the next morning (default 04:00)
if it never was; never before that practice's prompt has been out for an hour. Week review
(``YYYY-MM-DD-week.md``, dated the week's last day, the weekday of the practice that says ``closes:
week``; the weekly ritual's Sunday): due when that entry is annotated, or at
``reflections.week.fallback_time`` the day after. Both wait while an entry of theirs is still being
annotated or transcribed. Which practice closes a period is ``library.closer``: a fork that renames the
evening ritual keeps the reflection as long as it says ``closes: day``.

The model (one-shot, no tools, like annotations) sees only the period's entry files (the user's words,
annotations, the companion's notes), plus, for a week, that week's day reflections, and the latest
earlier analysis so it can say what moved. The private notebook is not an input; ``FOLLOW-UP:`` lines
go to it, as with annotations. Every quote must appear in the inputs (``validate.check_reflection``);
a bad answer writes nothing and is retried on later ticks, ``ATTEMPTS`` times, then alerted once. A
weak model therefore yields no reflection rather than a bad one. A model that cannot be reached at all
is different: that is an outage, which defers the reflection without using an attempt (``outage``:
retried at most every 30 minutes for up to 7 days, one alert once it has lasted 6 hours).

Unanswered prompts leave no trace in the journal: the files hold only what was answered (the opener is
in each entry's own file). Records live in the ledger under ``reflections``.
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from pathlib import Path

from . import agent, channel, compose, entries, ledger, library, mirror, notebook, outage, prompts, schedule, validate
from .ports import ChannelError
from .config import Config

ATTEMPTS = 3
SEND_ATTEMPTS = 6
INPUT_CAP = 12000  # characters per entry file handed to the model
BUDGET = 300


def enabled(cfg: Config, kind: str) -> bool:
    spec = cfg.reflections.get("day" if kind == "reflection" else "week") or {}
    return spec.get("enabled", True) is not False and cfg.agent.get("enabled") is not False


def _fallback_time(cfg: Config, kind: str) -> time:
    spec = cfg.reflections.get("day" if kind == "reflection" else "week") or {}
    hh, mm = (int(x) for x in str(spec.get("fallback_time") or "04:00").split(":"))
    return time(hh, mm)


def _at(cfg: Config, day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=cfg.tz)


def week_end_day(cfg: Config) -> int:
    """The weekday a week ends on: the ``closes: week`` practice's first weekly slot, else Sunday."""
    p = library.closer(cfg, "week")
    days = [s.weekday for s in p.schedule if s.weekday is not None] if p is not None else []
    return days[0] if days else 6


def _day_close(cfg: Config, d: date) -> tuple[str | None, datetime | None]:
    """(prompt id, local time) of the slot that closes day ``d``: the latest of the ``closes: day`` practice's
    slots that fall on that day. (None, None) when nothing closes it; (id, None) when its practice is not
    scheduled that day (an annotated entry still triggers; there is no send time to wait for)."""
    p = library.closer(cfg, "day")
    if p is None:
        return None, None
    on = [(s.time, n) for n, s in enumerate(p.schedule, 1) if s.weekday is None or s.weekday == d.weekday()]
    if not on:
        return schedule.prompt_id(d.isoformat(), p.id), None
    t, n = max(on)
    hh, mm = (int(x) for x in t.split(":"))
    return schedule.prompt_id(d.isoformat(), p.id, n), _at(cfg, d, time(hh, mm))


def _week_close(cfg: Config, end: date) -> str | None:
    """The prompt id of the ``closes: week`` practice on the week's last day (its first slot that day)."""
    p = library.closer(cfg, "week")
    if p is None:
        return None
    n = next((n for n, s in enumerate(p.schedule, 1) if s.weekday is None or s.weekday == end.weekday()), 1)
    return schedule.prompt_id(end.isoformat(), p.id, n)


def record_id(kind: str, local_date: str) -> str:
    return f"{local_date}-{kind}"


def find(data: dict, rid: str) -> dict | None:
    return next((r for r in data.get("reflections") or [] if r.get("id") == rid), None)


# ── what a period holds ──────────────────────────────────────────────────────

def _his_entries(cfg: Config, days: list[date]) -> list[Path]:
    out = []
    for d in days:
        for p in entries.entries_on(cfg.root, d.isoformat()):
            out.append(p)
    return out


def _has_words(paths: list[Path]) -> bool:
    return any(entries.raw_entry(p.read_text(encoding="utf-8", errors="replace")) for p in paths)


def _in_progress(cfg: Config, data: dict, days: set[str]) -> bool:
    """An entry of the period is still being transcribed or annotated (with attempts left)."""
    from .tick import ANNOTATE_ATTEMPTS
    for p in data["prompts"]:
        if p.get("local_date") not in days or not p.get("captures"):
            continue
        if any(c.get("transcript") == "pending" for c in p["captures"]):
            return True
        path = Path(p.get("entry_path") or "")
        if p.get("annotate") is False or p.get("capture") == "auto" and cfg.conversations.get("annotate_auto") is not True:
            continue  # never annotated: nothing to wait for
        if p.get("program") and not p.get("session_complete"):
            continue  # a program session is annotated when it completes, which may be days away
        if not p.get("annotated_at") and p.get("annotate_attempts", 0) < ANNOTATE_ATTEMPTS and path.is_file() \
                and entries.annotations_pending(path.read_text(encoding="utf-8")):
            return True
    return False


def _period(kind: str, end: date) -> list[date]:
    return [end] if kind == "reflection" else [end - timedelta(days=i) for i in range(6, -1, -1)]


def due(cfg: Config, data: dict, now_local: datetime) -> list[tuple[str, date, str]]:
    """(kind, period end date, trigger "annotated" | "fallback") for analyses due now.

    Today's and yesterday's day, and this week, are always candidates; so is any period whose attempt
    was deferred by an outage (until it is written or the outage outlasts the retry window)."""
    out = []
    today = now_local.date()
    waiting = [r for r in data.get("reflections") or [] if outage.deferred(r) and not r.get("written_at")]

    def pending(kind: str) -> set[date]:
        return {date.fromisoformat(r["local_date"]) for r in waiting if r.get("kind") == kind and r.get("local_date")}

    if enabled(cfg, "reflection"):
        for d in sorted({today - timedelta(days=1), today} | pending("reflection")):
            fallback = _at(cfg, d + timedelta(days=1), _fallback_time(cfg, "reflection"))
            closing_id, sent = _day_close(cfg, d)
            if sent is not None:
                fallback = max(fallback, sent + timedelta(hours=1))
            trigger = _trigger(cfg, data, "reflection", d, closing_id, fallback, now_local)
            if trigger:
                out.append(("reflection", d, trigger))
    if enabled(cfg, "week"):
        this_week = today - timedelta(days=(today.weekday() - week_end_day(cfg)) % 7)
        for end in sorted({this_week} | pending("week")):
            fallback = _at(cfg, end + timedelta(days=1), _fallback_time(cfg, "week"))
            trigger = _trigger(cfg, data, "week", end, _week_close(cfg, end), fallback, now_local)
            if trigger:
                out.append(("week", end, trigger))
    return out


def _trigger(cfg: Config, data: dict, kind: str, end: date, ritual_id: str | None, fallback: datetime,
             now_local: datetime) -> str | None:
    rec = find(data, record_id(kind, end.isoformat()))
    if rec and (rec.get("written_at") or rec.get("attempts", 0) >= ATTEMPTS or rec.get("skipped")):
        return None
    if rec and outage.waiting(rec, now_local):  # an outage was found moments ago: not again yet
        return None
    since = data.get("reflections_since")
    if since:  # yesterday's day may still be reflected on the first run; no older day, no older week
        first = date.fromisoformat(since) - timedelta(days=1 if kind == "reflection" else 0)
        if end < first:
            return None
    days = _period(kind, end)
    if not _has_words(_his_entries(cfg, days)):
        return None
    if _in_progress(cfg, data, {d.isoformat() for d in days}):
        return None
    ritual = ledger.find(data, ritual_id) if ritual_id else None
    if ritual and ritual.get("annotated_at"):
        return "annotated"
    return "fallback" if now_local >= fallback else None


# ── writing ──────────────────────────────────────────────────────────────────

def _label(cfg: Config, kind: str, end: date) -> tuple[str, str]:
    """(title for the file and message, period wording for the prompt)."""
    who = cfg.names.companion
    if kind == "reflection":
        return f"{who}'s reflection — {end.strftime('%a %-d %b %Y')}", end.strftime("%A %-d %B %Y")
    start = end - timedelta(days=6)
    span = (f"{start.strftime('%-d')}–{end.strftime('%-d %b %Y')}" if start.month == end.month
            else f"{start.strftime('%-d %b')} – {end.strftime('%-d %b %Y')}")
    return f"{who}'s week review — {span}", f"the week of {span}"


# Only the engine's callouts, by the key line before them; a callout he wrote himself stays his words.
_HER_REPLY = re.compile(r"^%% journal her:[^%\n]* %%\n> \[!quote\][-+]? (.+?) · ([^\n]+)\n((?:>[^\n]*(?:\n|$))*)", re.M)
_REPLY_CONTEXT = re.compile(r"^(%% journal his:[^%\n]* %%\n)> \[!info\][-+]? ↩[^\n]*\n(?:>[^\n]*(?:\n|$))*\n?", re.M)
HER_REPLY_SHORT = 240  # characters kept of each of her chat replies when an entry is over INPUT_CAP


def _her_replies_labelled(cfg: Config, text: str, keep: int | None = None) -> str:
    """Her folded chat replies in an entry, relabelled so they read as hers (never as his words), optionally
    shortened to ``keep`` characters (0: only the label). Reply-context lines (the message he answered) go."""
    who = cfg.names.companion
    them = cfg.names.user or "the user"

    def one(m: re.Match) -> str:
        body = "\n".join(l[1:].strip() for l in m.group(3).splitlines())
        body = " ".join(body.split())
        if keep is not None:
            body = "" if keep == 0 else (body if len(body) <= keep else body[:keep].rsplit(" ", 1)[0] + " …")
        label = f"[{who}'s chat reply, {m.group(2).strip()}: her words, not {them}'s"
        return (f"{label}]\n" if not body else f"{label}] {body}\n") + "\n"
    return entries.ENGINE_MARK.sub("", _REPLY_CONTEXT.sub(r"\1", _HER_REPLY.sub(one, text)))


def _entry_text(cfg: Config, p: Path) -> str:
    """An entry as reflection input: his words in full where possible; her chat replies labelled as hers and
    trimmed first (shortened, then reduced to their labels) before the entry itself is cut at INPUT_CAP."""
    raw = entries.without_question_bank(p.read_text(encoding="utf-8", errors="replace"))
    text = _her_replies_labelled(cfg, raw)
    for keep in (HER_REPLY_SHORT, 0):
        if len(text) <= INPUT_CAP:
            break
        text = _her_replies_labelled(cfg, raw, keep)
    if len(text) > INPUT_CAP:
        text = text[:INPUT_CAP] + "\n[… the rest of this entry is omitted here]"
    return text


def _input_block(cfg: Config, paths: list[Path], label: str = "journal") -> str:
    out = []
    for p in paths:
        if label == "journal":
            text = _entry_text(cfg, p)
        else:
            text = _without_legacy_callout(entries.without_question_bank(p.read_text(encoding="utf-8", errors="replace")))
            if len(text) > INPUT_CAP:
                text = text[:INPUT_CAP] + "\n[… the rest of this entry is omitted here]"
        out.append(f"### {label}:{p.stem}\n{text}")
    return "\n\n".join(out)


_HER_LINE = re.compile(r"^\[[^\]\n]*'s chat reply, [^\]\n]*: her words, not [^\]\n]*\][^\n]*\n?", re.M)


def _his_corpus(inputs: str) -> str:
    """What a reflection may quote as his: the inputs without her labelled chat replies."""
    return _HER_LINE.sub("", inputs)


def _earlier(cfg: Config, kind: str, end: date) -> list[Path]:
    """The latest earlier analysis of the same kind (within two periods), to say what moved."""
    step = 1 if kind == "reflection" else 7
    for back in range(1, 3):
        d = end - timedelta(days=step * back)
        found = entries.analyses_on(cfg.root, d.isoformat(), kind)
        if found:
            return found[-1:]
    return []


_LEGACY_CALLOUT = ("> [!note]- Today's prompts", "> [!note]- This week's prompts")


def _without_legacy_callout(text: str) -> str:
    """Analyses written before 2026-10-15 end with a folded callout of that period's openers; they are
    the companion's own words, never an input or part of the body."""
    lines = text.splitlines()
    cut = next((i for i, l in enumerate(lines) if l.startswith(_LEGACY_CALLOUT)), len(lines))
    return "\n".join(lines[:cut]).rstrip() + ("\n" if text.endswith("\n") and cut == len(lines) else "")


def body_of(text: str) -> str:
    """The model-written part of an analysis file (after the sources line; older files also end with the
    prompts callout, which is left out)."""
    rest = entries.without_question_bank(text)
    lines = rest.splitlines()
    start = next((i + 1 for i, l in enumerate(lines) if l.startswith("_From ")), 1)
    end = next((i for i, l in enumerate(lines) if l.startswith(_LEGACY_CALLOUT)), len(lines))
    return "\n".join(lines[start:end]).strip()


def render(cfg: Config, kind: str, end: date, body: str, sources: list[Path], written_local: datetime) -> str:
    n = cfg.names
    title, _ = _label(cfg, kind, end)
    links = ", ".join(f"[[{p.stem}]]" for p in sources)
    author = (n.companion or "companion").lower()
    parts = [f"---\ndate: {end.isoformat()}\nkind: {kind}\ntype: journal\nsource: {n.frontmatter_source}\n"
             f"author: {author}\n---", f"# {title}",
             f"_From {links} · written {written_local.strftime('%-d %b %H:%M %Z')}_",
             entries.demote_headings(body.strip())]
    return "\n\n".join(parts) + "\n"


def _target(cfg: Config, kind: str, end: date) -> Path:
    """The file to write; a hand-written file of the same name is never overwritten (-2, -3 … instead)."""
    n = 1
    while True:
        p = entries.entry_path(cfg.root, entries.stem_for(end.isoformat(), kind, n))
        if not p.exists():
            return p
        n += 1


def _outage(cfg: Config, rid: str, kind: str, end: date, now: datetime, detail: str, log: list[str]) -> str | None:
    """Park an analysis the model could not be reached for. Returns an alert message when this outage has
    lasted ``outage.ALERT_AFTER`` (once) or has outlasted ``outage.GIVE_UP_AFTER`` (the analysis is dropped)."""
    with ledger.locked(cfg.ledger_path) as data:
        recs = data.setdefault("reflections", [])
        rec = find(data, rid)
        if rec is None:
            rec = {"id": rid, "kind": kind, "local_date": end.isoformat()}
            recs.append(rec)
        alert, since = outage.defer(rec, now, detail), outage.describe(rec, cfg.tz)
        gave_up = outage.expired(rec, now)
        if gave_up:
            rec["attempts"] = ATTEMPTS
            rec["error"] = f"model unreachable for {outage.GIVE_UP_AFTER.days} days: {detail}"[:300]
    log.append(f"{rid}: reflection deferred, model unreachable ({detail})")
    if gave_up:
        return f"{rid} was not written: the model was unreachable for {outage.GIVE_UP_AFTER.days} days ({detail})"
    if alert:
        return f"{rid} is still not written: the model has been unreachable {since}; retrying every 30 minutes for up to 7 days"
    return None


def write_one(cfg: Config, kind: str, end: date, trigger: str, now: datetime,
              log: list[str]) -> tuple[str | None, bool]:
    """Try once to write one analysis. Returns (alert message or None, whether the model was unreachable).

    An unreachable model defers the analysis without using an attempt; a bad answer uses one."""
    rid = record_id(kind, end.isoformat())
    days = _period(kind, end)
    mine = [p for p in _his_entries(cfg, days)]
    dailies = [p for d in days for p in entries.analyses_on(cfg.root, d.isoformat(), "reflection")] \
        if kind == "week" else []
    earlier = _earlier(cfg, kind, end)
    inputs = _input_block(cfg, mine) + ("\n\n" + _input_block(cfg, dailies, "reflection") if dailies else "")
    earlier_text = _input_block(cfg, earlier, kind)
    title, period = _label(cfg, kind, end)
    try:
        raw = agent.ask(cfg, prompts.reflection_request(cfg, kind, period, inputs, earlier_text), BUDGET,
                        toolsets="none")
    except agent.AgentUnavailable as e:
        return _outage(cfg, rid, kind, end, now, str(e), log), True
    text, followups = notebook.split_followups(raw or "")
    ok, reason = (validate.check_reflection(kind, text, _his_corpus(inputs) + "\n" + earlier_text) if raw
                  else (False, "agent failed"))
    path = None
    if ok:
        path = _target(cfg, kind, end)
        entries.write_file(path, render(cfg, kind, end, text, mine + dailies, now.astimezone(cfg.tz)))
        for f in followups:
            notebook.add(cfg, f, source=path.stem)
    with ledger.locked(cfg.ledger_path) as data:
        recs = data.setdefault("reflections", [])
        rec = find(data, rid)
        if rec is None:
            rec = {"id": rid, "kind": kind, "local_date": end.isoformat()}
            recs.append(rec)
        rec["attempts"] = rec.get("attempts", 0) + 1
        outage.clear(rec)  # the model answered, well or badly
        if ok:
            rec.update(written_at=now.isoformat(), path=str(path), trigger=trigger)
            rec.pop("error", None)
        else:
            rec["error"] = reason[:300]
        attempts = rec["attempts"]
    if ok:
        log.append(f"wrote {path.name} ({trigger})" + (f" (+{len(followups)} notebook)" if followups else ""))
        return None, False
    log.append(f"{rid}: reflection attempt {attempts}/{ATTEMPTS} failed ({reason})")
    if attempts >= ATTEMPTS:
        return f"{rid} was not written after {attempts} attempts: {reason}", False
    return None, False


def send(cfg: Config, now: datetime, log: list[str]) -> list[str]:
    """Send each written analysis to the chat once (silently when the overnight fallback wrote it)."""
    alerts, where = [], channel.label(cfg)
    for rec in ledger.snapshot(cfg.ledger_path).get("reflections") or []:
        tg = rec.get("telegram") or {}  # (the record's name for its send, whatever the platform)
        if not rec.get("written_at") or tg.get("message_id") or tg.get("attempts", 0) >= SEND_ATTEMPTS:
            continue
        path = Path(rec.get("path") or "")
        error, mid, title, body = None, None, "", ""
        if not path.is_file():
            error = "the file is gone"
        else:
            title, _ = _label(cfg, rec["kind"], date.fromisoformat(rec["local_date"]))
            body = body_of(path.read_text(encoding="utf-8"))
            try:
                mid = cfg.channel().send(cfg.chat_id, compose.reflection_html(title, body, cfg.names),
                                         silent=rec.get("trigger") == "fallback")
            except ChannelError as e:
                error = str(e)
        with ledger.locked(cfg.ledger_path) as data:
            q = find(data, rec["id"])
            if q is None:
                continue
            t = q.setdefault("telegram", {})
            if mid:
                t.update(message_id=mid, sent_at=now.isoformat(), silent=rec.get("trigger") == "fallback")
                t.pop("error", None)
            else:
                t["attempts"] = t.get("attempts", 0) + 1
                t["error"] = (error or "")[:300]
                if t["attempts"] >= SEND_ATTEMPTS:
                    alerts.append(f"{rec['id']} could not be sent to {where} after {t['attempts']} attempts: {error}")
        log.append(f"sent {rec['id']} to {where}" if mid else f"{rec['id']}: {where} send failed ({error})")
        if mid:
            mirror.to_chat(cfg, f"{title}, sent at {mirror.stamp(cfg, now)}", body, log,
                           ref=("reflections", rec["id"], "telegram"))
    return alerts


def run(cfg: Config, now: datetime, log: list[str]) -> list[str]:
    """One pass: write what is due (one attempt each; none once the model is found unreachable), then send.
    Returns alert messages."""
    alerts = []
    if not ledger.snapshot(cfg.ledger_path).get("reflections_since"):
        with ledger.locked(cfg.ledger_path) as data:  # the first run starts the clock: no backlog of old periods
            data.setdefault("reflections_since", now.astimezone(cfg.tz).date().isoformat())
    for kind, end, trigger in due(cfg, ledger.snapshot(cfg.ledger_path), now.astimezone(cfg.tz)):
        msg, down = write_one(cfg, kind, end, trigger, now, log)
        if msg:
            alerts.append(msg)
        if down:  # the model is unreachable for the rest of this tick too; don't wait out another timeout
            break
    return alerts + send(cfg, now, log)


def latest(cfg: Config, kind: str, today: date, within_days: int) -> Path | None:
    """The newest analysis of ``kind`` dated within ``within_days`` of ``today`` (for context packs)."""
    for back in range(0, within_days + 1):
        found = entries.analyses_on(cfg.root, (today - timedelta(days=back)).isoformat(), kind)
        if found:
            return found[-1]
    return None
