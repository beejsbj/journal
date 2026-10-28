"""Outages are deferred, not counted as failed attempts.

When the model or the speech-to-text backend cannot be reached, the work (an annotation, a reflection,
a transcript) is not at fault, so it must not use up its attempts and be dropped for good. A record
that hit an outage carries ``deferred_since`` (first outage in the current run of them),
``last_deferred_at`` and ``defer_error``. It is retried no more often than every ``RETRY_EVERY``, for
up to ``GIVE_UP_AFTER`` after ``deferred_since``; a ``defer_alerted_at`` marks the single alert raised
once the outage has lasted ``ALERT_AFTER``. A successful attempt, or a bad answer from a reachable
model (which counts as an attempt as before), clears the deferral.
"""
from __future__ import annotations

from datetime import datetime, timedelta

RETRY_EVERY = timedelta(minutes=30)
ALERT_AFTER = timedelta(hours=6)
GIVE_UP_AFTER = timedelta(days=7)
KEYS = ("deferred_since", "last_deferred_at", "defer_error", "defer_alerted_at")


def _when(rec: dict, key: str) -> datetime | None:
    try:
        return datetime.fromisoformat(rec[key])
    except (KeyError, TypeError, ValueError):
        return None


def deferred(rec: dict) -> bool:
    return _when(rec, "deferred_since") is not None


def waiting(rec: dict, now: datetime) -> bool:
    """Deferred and tried too recently to try again."""
    last = _when(rec, "last_deferred_at")
    return last is not None and now - last < RETRY_EVERY


def expired(rec: dict, now: datetime) -> bool:
    """The outage has lasted longer than we keep trying."""
    since = _when(rec, "deferred_since")
    return since is not None and now - since >= GIVE_UP_AFTER


def defer(rec: dict, now: datetime, detail: str) -> bool:
    """Record an outage on ``rec`` (in place). True once when it has lasted ``ALERT_AFTER``: the caller alerts."""
    rec.setdefault("deferred_since", now.isoformat())
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
