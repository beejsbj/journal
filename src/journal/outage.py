"""Outages are deferred, not counted as failed attempts.

When the model or the speech-to-text backend cannot be reached, the work (an annotation, a reflection,
a transcript) is not at fault, so it must not use up its attempts and be dropped for good. A record
that hit an outage carries ``deferred_since`` (first outage in the current run of them),
``last_deferred_at`` and ``defer_error``. It is retried no more often than every ``RETRY_EVERY``, for
up to ``GIVE_UP_AFTER`` after ``deferred_since``; a ``defer_alerted_at`` marks the single alert raised
once the outage has lasted ``ALERT_AFTER``. A successful attempt, or a bad answer from a reachable
model (which counts as an attempt as before), clears the deferral.

**The breaker** (``recovery.breaker``, on by default) is what the engine knows of an outage across processes: a tick
is a fresh process, so without it every step of every tick found the outage again with a model run of its own. The
first model run that finds no model opens it (``agent.ask``), in ``outage.json`` under ``state_dir`` (read and written
under ``ledger.locked_state``): a run that timed out, unless another run answered in the same process (a long run, not
an outage), and a run that failed otherwise once a probe run at once (``confirm``) fails too. When that probe answers,
the failure was the request's own (one picture, a prompt too long): nothing is held, and a fault of the request's
(``request_failed``) counts as an attempt of the record's. While it is open, ``agent.ask`` starts nothing: it raises ``ModelHeld`` (an
``AgentUnavailable``, so every caller defers as before and an opener goes out plain at once, never after a
timeout). Once ``recovery.probe_after_minutes`` (5) have passed, the next ask first runs one probe: a fixed question
with no tools, no words of his and a short budget (``PROBE_BUDGET``). A probe that fails waits twice as long for the
next (5, 10, 20, then every ``recovery.probe_max_minutes``, 30; a probe set further off than that, after the clock
was set back, is due); one that answers, or any model run that answers,
closes the breaker, and the ask that probed goes on with its own run. A probe is claimed under the lock before it
runs, so two processes never probe at once. A held ask is no try of the record's: ``defer`` keeps its
``last_deferred_at`` (so the record is free the moment a model answers) and its first ``defer_error``; and the record
whose own run found the outage is no different (``waiting`` reads the breaker: held at no cost while it is open, free
once it closed), so the work comes back oldest first.

**The catch-up is paced** (``catchup``): each model step (annotations, picture descriptions, reflections) runs at most
``recovery.catchup_per_tick`` (2) model calls a tick, in order, so work held through an outage comes back a few
items at a time and no tick holds the lock for an hour. What goes to the chat is paced and dated in ``tick`` and
``reflect`` (``chat_room``, ``stale``).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from . import ledger
from .config import Config
from .ports import CompanionUnavailable

RETRY_EVERY = timedelta(minutes=30)
ALERT_AFTER = timedelta(hours=6)
GIVE_UP_AFTER = timedelta(days=7)
KEYS = ("deferred_since", "last_deferred_at", "defer_error", "defer_alerted_at")


def _when(rec: dict, key: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(rec[key])
    except (KeyError, TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else None  # (one with no zone cannot be compared: as if not there)


def deferred(rec: dict) -> bool:
    return _when(rec, "deferred_since") is not None


def waiting(rec: dict, now: datetime, cfg: Config | None = None) -> bool:
    """Deferred and tried too recently to try again. With ``cfg`` and the breaker on, a try that found the outage the
    breaker holds for is no reason to wait (its ask is held at no cost, and the first ask after the outage probes),
    and neither is a try before the last outage closed: the record keeps its place, oldest first."""
    last = _when(rec, "last_deferred_at")
    if last is None or now - last >= RETRY_EVERY:
        return False
    if cfg is None or not breaker_on(cfg):
        return True
    st = ledger.read_state(_path(cfg))
    down, back = _when(st, "down_since"), _when(st.get("last") or {}, "answered_at")
    try:
        if down is not None:
            return last < down  # (a try before this outage began waits as before)
        return back is None or back <= last
    except TypeError:  # (a time without its zone, written by hand)
        return True


def expired(rec: dict, now: datetime) -> bool:
    """The outage has lasted longer than we keep trying."""
    since = _when(rec, "deferred_since")
    return since is not None and now - since >= GIVE_UP_AFTER


def defer(rec: dict, now: datetime, detail: str) -> bool:
    """Record an outage on ``rec`` (in place). True once when it has lasted ``ALERT_AFTER``: the caller alerts.

    A held ask (``ModelHeld``: the breaker started no run) is no try: ``last_deferred_at`` and an earlier
    ``defer_error`` stay as they were, so the record is tried again the first time a model answers."""
    rec.setdefault("deferred_since", now.isoformat())
    if held(detail):
        rec.setdefault("defer_error", (detail or "")[:200])
    else:
        rec["last_deferred_at"] = now.isoformat()
        rec["defer_error"] = (detail or "")[:200]
    if rec.get("defer_alerted_at") or now - _when(rec, "deferred_since") < ALERT_AFTER:
        return False
    rec["defer_alerted_at"] = now.isoformat()
    return True


def clear(rec: dict) -> None:
    for k in KEYS:
        rec.pop(k, None)


def describe(rec: dict, tz) -> str:
    """'since 14 Oct 22:10: <error>' for alerts and status."""
    since = _when(rec, "deferred_since")
    when = since.astimezone(tz).strftime("%-d %b %H:%M %Z") if since else "?"
    return f"since {when}: {rec.get('defer_error') or 'unknown error'}"


# ── the breaker: an outage known across processes ───────────────────────────

STATE = "outage.json"
PROBE_QUERY = "This is a connection check. Reply with the single word: ok"
PROBE_BUDGET = 120  # seconds (the run gets a minute more): a picture's description's budget, the smallest of the deferred steps (a bridge's 60 s falls back to its template),
# since Hermes caps a provider's stale timeout to what is left of the budget, and a probe must not fail where a real
# step would have been answered (an overloaded provider, a fallback)
HELD = "held"


class ModelHeld(CompanionUnavailable):
    """No model run was started: an outage is known (``outage.json``) and no probe is due yet. Its text is
    ``held: <reason>``, the reason being ``agent.failure_reason`` of the run that found the outage (a whitelist)."""


class RequestRefused(CompanionUnavailable):
    """The agent refused the request before any model ran (Hermes' ``invalid_params``: a launch parameter it does not
    accept, such as an unknown toolset name). The request is wrong, not the model: it never opens or holds the
    breaker. A caller treats it as it treats an unavailable model for that one record (an opener goes out plain, a
    record defers alone and is tried again on its own clock), so the work is still there once a fix is deployed.
    Its text is the failed run's own message, as before."""


REFUSED = "invalid_params"  # Hermes' wire code for a refused launch parameter (``gateway.session_policy._refuse``)


def refused(reason) -> bool:
    """Whether a failed run's reason (``agent.failure_reason``) is a refusal of the request (``REFUSED``)."""
    return REFUSED in str(reason or "").replace(":", " ").split()


def note_refused(cfg: Config, what: str, reason: str, field: str = "") -> None:
    """One line in ``errors.jsonl`` for a refused request, apart from outages: ``what`` (a step, or the probe), the
    whitelisted ``reason`` and the refused parameter's name (Hermes' field name, e.g. ``toolsets``). Any process may
    write it (a tick, ``journal send``, ``journal program``); a plain append, as the tick's own lines."""
    import json
    line = (f"model request refused ({reason}{f', {field}' if field else ''}): {what}; the agent did not accept "
            "the request, so no model ran; not an outage: nothing is held, and this call falls back")
    try:
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        with open(cfg.state_dir / "errors.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"at": _now().isoformat(), "mode": "model", "error": line}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def held(detail) -> bool:
    return str(detail or "").startswith(HELD + ":")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _path(cfg: Config):
    return cfg.state_dir / STATE


def breaker_on(cfg: Config) -> bool:
    return cfg.recovery.get("breaker", True) is not False


def _minutes(cfg: Config, key: str, default: float) -> float:
    try:
        value = float(cfg.recovery.get(key, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def probe_wait(cfg: Config, failed_probes: int) -> timedelta:
    """How long after the outage was found (0 probes) or after the n-th failed probe the next probe waits:
    ``probe_after_minutes``, doubling, at most ``probe_max_minutes``."""
    first = _minutes(cfg, "probe_after_minutes", 5)
    most = max(first, _minutes(cfg, "probe_max_minutes", 30))
    return timedelta(minutes=min(first * 2 ** min(failed_probes, 16), most))


def probes(st: dict) -> int:
    """Probes failed in the outage ``st`` holds (0 when the file says something else: it never stops an ask)."""
    try:
        return max(0, int(st.get("probes") or 0))
    except (TypeError, ValueError):
        return 0


def known(cfg: Config) -> dict | None:
    """The outage the breaker holds for (``down_since``, ``last_error``, ``probes``, ``next_probe_at``), or None (also
    whenever the breaker is switched off: then nothing is held, whatever the file says)."""
    st = ledger.read_state(_path(cfg)) if breaker_on(cfg) else {}
    return st if st.get("down_since") else None


def _held(cfg: Config, st: dict) -> ModelHeld:
    return ModelHeld(f"{HELD}: {st.get('last_error') or 'model unreachable'}"[:200])


def gate(cfg: Config, companion, now: datetime | None = None) -> bool:
    """Before a model run (``agent.ask``). Returns at once when no outage is known; raises ``ModelHeld`` while one is
    and no probe is due; when one is due, claims it, runs it, and returns True once it answers (the breaker is
    closed: the caller's own run goes ahead) or raises ``ModelHeld`` when it does not (the next probe waits longer).
    (``locked_state`` writes on a clean exit only, so nothing is raised inside it.)"""
    if not breaker_on(cfg) or not ledger.read_state(_path(cfg)).get("down_since"):
        return False
    now = now or _now()
    with ledger.locked_state(_path(cfg)) as st:
        if not st.get("down_since"):
            return False  # closed meanwhile by another process
        due = _when(st, "next_probe_at")
        wait = due is not None and now < due <= now + probe_wait(cfg, 16)  # (one further off: the clock jumped back)
        if not wait:  # claimed: a run elsewhere sees the next probe already scheduled and is held meanwhile
            st["next_probe_at"] = (now + probe_wait(cfg, probes(st) + 1)).isoformat()
            st["probing_at"] = now.isoformat()
        failed = _held(cfg, st)
    if wait:
        raise failed
    try:
        companion.ask(PROBE_QUERY, PROBE_BUDGET, "none")
    except CompanionUnavailable as e:
        from .agent import failure_reason, refused_field
        if refused(failure_reason(str(e))):  # the probe's own request was refused: no evidence of an outage at all
            note_refused(cfg, "the outage probe", failure_reason(str(e)), refused_field(str(e)))
            release(cfg, now)  # (holding on would hold every model step for as long as the refusal lasts)
            return False
        with ledger.locked_state(_path(cfg)) as st:
            st.pop("probing_at", None)
            if st.get("down_since"):
                st["probes"] = probes(st) + 1
                st["last_probe_at"] = now.isoformat()
                st["last_error"] = failure_reason(str(e)) or st.get("last_error") or "model unreachable"
                st["next_probe_at"] = (_now_or(now) + probe_wait(cfg, st["probes"])).isoformat()
            failed = _held(cfg, st)
        raise failed from e
    answered(cfg, now)
    return True


def confirm(cfg: Config, companion) -> bool | None:
    """A run just failed with the breaker shut (``agent.ask``): whether a model answers the probe now. True: the
    request failed on its own and nothing is held; False: the probe failed too, and the caller opens the breaker; None:
    the probe's own request was refused (``RequestRefused``), which says nothing of the model either way, so nothing
    is opened and the run's record defers alone. With the breaker off, no probe and False (as before it: the run's
    failure is the outage)."""
    if not breaker_on(cfg):
        return False
    try:
        companion.ask(PROBE_QUERY, PROBE_BUDGET, "none")
    except CompanionUnavailable as e:
        from .agent import failure_reason, refused_field
        if refused(failure_reason(str(e))):
            note_refused(cfg, "the outage probe", failure_reason(str(e)), refused_field(str(e)))
            return None
        return False
    return True


def request_failed(reason: str) -> bool:
    """Whether a failed run's reason (``agent.failure_reason``) puts the fault in the request itself, not the provider:
    an HTTP 4xx other than 408 (timeout) and 429 (rate limit), or ``invalid_request_error``. Asked only after a probe
    answered; such a request would fail the same way next time, so it counts as an attempt."""
    codes = re.findall(r"\b([45]\d\d)\b", reason or "")
    return any(c.startswith("4") and c not in ("408", "429") for c in codes) or "invalid_request_error" in (reason or "")


def _now_or(now: datetime) -> datetime:
    """The time after a probe ran (it may have taken its whole budget), never before the time it started."""
    return max(now, _now())


def trip(cfg: Config, reason: str, now: datetime | None = None) -> None:
    """A model run found no model: open the breaker (or keep it open, with this reason)."""
    if not breaker_on(cfg):
        return
    now = now or _now()
    with ledger.locked_state(_path(cfg)) as st:
        if not st.get("down_since"):  # (``last``, the outage before, stays)
            st.update(down_since=now.isoformat(), probes=0, next_probe_at=(now + probe_wait(cfg, 0)).isoformat())
        st["last_error"] = (reason or "model unreachable")[:200]


def answered(cfg: Config, now: datetime | None = None) -> None:
    """A model answered: close the breaker if it is open, keeping when the outage ran (``last``). Reads only, unless
    it was open."""
    _close(cfg, now, "answered_at")


def release(cfg: Config, now: datetime | None = None) -> None:
    """The probe's own request was refused: close the breaker without saying a model answered (``last`` has
    ``released_at``, not ``answered_at``). Work is then tried on its own again, record by record, as before the
    breaker: a real outage is found again by the next run that fails, and its record defers alone."""
    _close(cfg, now, "released_at")


def released(cfg: Config) -> bool:
    """Whether the last outage the breaker held for ended by ``release`` (a refused probe), not by a model's answer."""
    last = ledger.read_state(_path(cfg)).get("last") or {}
    return isinstance(last, dict) and bool(last.get("released_at")) and not last.get("answered_at")


def _close(cfg: Config, now: datetime | None, key: str) -> None:
    if not ledger.read_state(_path(cfg)).get("down_since"):
        return
    now = now or _now()
    with ledger.locked_state(_path(cfg)) as st:
        if not st.get("down_since"):
            return
        last = {"down_since": st["down_since"], key: now.isoformat(), "probes": probes(st),
                "last_error": st.get("last_error") or ""}
        st.clear()
        st["last"] = last


# ── the paced catch-up ───────────────────────────────────────────────────────

def per_tick(cfg: Config) -> int:
    """Model calls one step makes in one tick (``recovery.catchup_per_tick``; at least 1)."""
    return _count(cfg, "catchup_per_tick", 2)


def _count(cfg: Config, key: str, default: int) -> int:
    try:
        return max(1, int(cfg.recovery.get(key, default)))
    except (TypeError, ValueError):
        return default


def stale_after(cfg: Config) -> timedelta:
    """How late a message to the chat may be (``recovery.stale_hours``): an annotation reply after his newest words,
    a reflection or week review after its fallback time. Later, it is not sent (it stays in its file)."""
    return timedelta(hours=_minutes(cfg, "stale_hours", 24))


def _sent_times(data: dict) -> list[str]:
    """When each message the engine sent of its own went out: annotation replies, analyses, outage catch-ups."""
    out = [(p.get("annotation_reply") or {}).get("sent_at") for p in data.get("prompts") or [] if isinstance(p, dict)]
    out += [(r.get("telegram") or {}).get("sent_at") for r in data.get("reflections") or [] if isinstance(r, dict)]
    out += [r.get("sent_at") for r in data.get("recovery") or [] if isinstance(r, dict)]
    return [t for t in out if isinstance(t, str) and t]


def chat_room(cfg: Config, now: datetime) -> int:
    """How many more of those messages may go to the chat in this tick: ``recovery.sends_per_tick`` (2) less those
    sent in it, and ``recovery.sends_per_hour`` (4) less those sent in the hour up to ``now``. A tick's steps share
    one ``now`` and stamp every send with it, so "sent in this tick" is ``sent_at == now``. Prompts, interrupts and
    program phases are not counted and never held: they are on the schedule he chose."""
    times = _sent_times(ledger.snapshot(cfg.ledger_path))
    stamp, hour_ago = now.isoformat(), now - timedelta(hours=1)
    this_tick = sum(1 for t in times if t == stamp)
    this_hour = sum(1 for t in times if (at := _when({"t": t}, "t")) is not None and at.tzinfo is not None
                    and hour_ago < at <= now)
    return max(0, min(_count(cfg, "sends_per_tick", 2) - this_tick, _count(cfg, "sends_per_hour", 4) - this_hour))


def pace_note(cfg: Config, n: int, what: str) -> str:
    return (f"chat pacing: {n} {what} wait for a later tick ({_count(cfg, 'sends_per_tick', 2)} a tick, "
            f"{_count(cfg, 'sends_per_hour', 4)} an hour)")
