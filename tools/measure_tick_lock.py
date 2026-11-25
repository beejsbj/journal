"""Measure how long a program step (``/next``, ``journal program …``) or ``journal send`` waits on ``tick.lock`` while a
tick is inside a model call (issue #45).

It runs a real ``tick.run`` over a temporary root with the clock pinned to a time when no ritual is due (a ritual due
at the real time would add an opener call and change the number), ``--calls`` annotations due (each one model call)
and a fake companion that takes ``--slow`` seconds, and starts a second party that takes the lock the way a spawned
``journal program next`` does (``programs.locked``), 0.2 s into the first call. The wait it prints is how long that
party sat behind the tick. Before the tick yielded (``ticklock``) it was the rest of that call plus every later call
(about ``calls × slow``); now it is the rest of the one call (about ``slow``): the tick lets the waiter go between two
annotations. The table under it is the longest a tick can sit in one model call (the budgets the engine hands the
companion), which is now how long a ``/next`` can wait at most. No live state is read or written: the root, the state
dir and the ledger are made in a temporary directory, and no model or chat is reached.

    python3 tools/measure_tick_lock.py [--slow SECONDS] [--calls N]
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))   # (helpers: a neutral config and a no-network guard)

from helpers import add_prompt, make_cfg  # noqa: E402

from journal import agent, capture, programs, recovery, reflect, telegram, tick  # noqa: E402
from journal import config as jconfig  # noqa: E402

# A Tuesday, 14:00 in the harness's zone (Asia/Kolkata): clear of the built-ins' times (06:00, Wed 12:00, Sun 20:00, 23:00)
# and their two-hour grace, so the only model call is the annotation.
PINNED = datetime(2026, 10, 13, 8, 30, tzinfo=timezone.utc)


KINDS = ("morning", "evening", "midweek", "weekly")  # (the built-ins: one entry each on the pinned day)


class _PinnedDatetime(datetime):
    """``tick``'s ``datetime`` with ``now()`` fixed (``tick.run`` takes no clock of its own)."""

    @classmethod
    def now(cls, tz=None):
        return PINNED.astimezone(tz) if tz else PINNED.replace(tzinfo=None)


def _caller() -> str:
    """The ``module.function`` that asked the model: the nearest frame outside ``agent`` in the ``journal`` package."""
    frame = sys._getframe(2)
    while frame:
        mod = frame.f_globals.get("__name__", "")
        if mod.startswith("journal.") and mod != "journal.agent":
            return f"{mod.removeprefix('journal.')}.{frame.f_code.co_name}"
        frame = frame.f_back
    return "?"


def measure(slow: float, calls: int = 2) -> tuple[float, float, list[str]]:
    """(seconds the tick took, seconds ``programs.locked`` waited, who asked the model), the wait starting 0.2 s into
    the first model call, with ``calls`` annotations due."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(Path(tmp))
        cfg.annotate_after_minutes = 0
        cfg.recovery["catchup_per_tick"] = calls  # (all of them in this tick)
        for spec in ("day", "week"):  # (the evening's annotation would make the day reflection due: a call of its own)
            cfg.reflections.setdefault(spec, {})["enabled"] = False
        for k, kind in enumerate(KINDS[:calls]):  # entries of the day, each answered, each owed its annotations
            sent = PINNED - timedelta(hours=1, minutes=k)  # (all today: an earlier day would make a reflection due)
            add_prompt(cfg, kind=kind, local_date="2026-10-13", sent=sent, message_id=str(100 + k))
            capture.capture(cfg, cfg.chat_id, str(100 + k), "slept badly", sent + timedelta(minutes=5), "text",
                            str(4242 + k))
        started, waited, asked = threading.Event(), [], []

        def slow_ask(*_a, **_k):
            asked.append(_caller())
            started.set()
            time.sleep(slow)
            return "- a note\n"

        def second_party():
            started.wait()
            time.sleep(0.2)
            t0 = time.monotonic()
            with programs.locked(cfg):
                pass
            waited.append(time.monotonic() - t0)

        thread = threading.Thread(target=second_party)
        with mock.patch.object(agent, "ask", slow_ask), \
                mock.patch.object(tick, "datetime", _PinnedDatetime), \
                mock.patch("journal.context.companion_reply_after", lambda *a, **k: None), \
                mock.patch.object(telegram, "_call", lambda *a, **k: {"message_id": 1}), \
                mock.patch.object(telegram, "token", lambda c: "T"):
            thread.start()
            t0 = time.monotonic()
            tick.run(cfg)
            took = time.monotonic() - t0
            thread.join()
        return took, waited[0], asked


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slow", type=float, default=3.0, help="seconds the fake model takes (default 3)")
    ap.add_argument("--calls", type=int, default=2, choices=range(1, len(KINDS) + 1), metavar="N",
                    help=f"annotations due, one model call each (1 to {len(KINDS)}; default 2)")
    args = ap.parse_args(argv)
    took, waited, asked = measure(args.slow, args.calls)
    print(f"fake model call {args.slow:.1f} s, {args.calls} due; the tick took {took:.2f} s; a second party asking "
          f"0.2 s into the first call waited {waited:.2f} s for tick.lock (the call's remaining {args.slow - 0.2:.1f} s; "
          f"{args.calls * args.slow - 0.2:.1f} s if the tick held the lock to its end)")
    print(f"model calls in that tick: {len(asked)}" + (f" ({', '.join(asked)})" if asked else ""))
    if len(asked) != args.calls:
        print(f"  (expected {args.calls}, the annotations: something else was due, or not all of them were asked)")
    print()
    print("Longest one model call in a tick (seconds the companion is given; a run is killed 60 s after it):")
    for name, secs in (("opener (windows.opener_budget_seconds)", jconfig.DEFAULTS["windows"]["opener_budget_seconds"]),
                       ("annotation, per prompt (literal in tick.annotate)", 180),
                       ("picture description, per picture (literal in tick.process_images)", 120),
                       ("day reflection / week review (reflect.BUDGET)", reflect.BUDGET),
                       ("outage catch-up (recovery.BUDGET)", recovery.BUDGET),
                       ("program: reflect phase", programs.REFLECT_BUDGET), ("program: bridge (literal in programs._bridge)", 60),
                       ("program: insights synthesis", programs.SYNTHESIS_BUDGET)):
        print(f"  {name:<66}{secs:>4} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
