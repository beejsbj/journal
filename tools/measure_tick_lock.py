"""Measure how long a program step (``/next``, ``journal program …``) or ``journal send`` waits on ``tick.lock`` while a
tick is inside a model call (issue #45).

It runs a real ``tick.run`` over a temporary root, with one annotation due and a fake companion that takes ``--slow``
seconds, and starts a second party that takes the lock the way a spawned ``journal program next`` does
(``programs.locked``). The wait it prints is how long that party sat behind the tick; the table under it is the
longest a tick can sit in one model call (the budgets the engine hands the companion), which is how long a ``/next``
that arrives just after the call started can wait *for that call alone*. No live state is read or written: the root, the
state dir and the ledger are made in a temporary directory, and no model or chat is reached.

    python3 tools/measure_tick_lock.py [--slow SECONDS]
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


def measure(slow: float) -> tuple[float, float]:
    """(seconds the tick took, seconds ``programs.locked`` waited), the wait starting 0.2 s into the model call."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = make_cfg(Path(tmp))
        cfg.annotate_after_minutes = 0
        sent = datetime.now(timezone.utc) - timedelta(hours=1)
        add_prompt(cfg, sent=sent)
        capture.capture(cfg, cfg.chat_id, "100", "slept badly", sent + timedelta(minutes=5), "text", "4242")
        started, waited = threading.Event(), []

        def slow_ask(*_a, **_k):
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
                mock.patch("journal.context.companion_reply_after", lambda *a, **k: None), \
                mock.patch.object(telegram, "_call", lambda *a, **k: {"message_id": 1}), \
                mock.patch.object(telegram, "token", lambda c: "T"):
            thread.start()
            t0 = time.monotonic()
            tick.run(cfg)
            took = time.monotonic() - t0
            thread.join()
        return took, waited[0]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--slow", type=float, default=3.0, help="seconds the fake model takes (default 3)")
    args = ap.parse_args(argv)
    took, waited = measure(args.slow)
    print(f"fake model call {args.slow:.1f} s; the tick took {took:.2f} s; a second party asking {0.2:.1f} s into the call "
          f"waited {waited:.2f} s for tick.lock (the call's remaining {args.slow - 0.2:.1f} s, plus every step after it)")
    print()
    print("Longest one model call in a tick (seconds the companion is given; a run is killed 60 s after it):")
    for name, secs in (("opener (windows.opener_budget_seconds)", jconfig.DEFAULTS["windows"]["opener_budget_seconds"]),
                       ("annotation, per prompt (literal in tick.annotate)", 180),
                       ("picture description, per picture (literal in tick.process_images)", 120),
                       ("day reflection / week review (reflect.BUDGET)", reflect.BUDGET),
                       ("outage catch-up (recovery.BUDGET)", recovery.BUDGET),
                       ("program: reflect phase", programs.REFLECT_BUDGET), ("program: bridge (literal in programs._bridge)", 60),
                       ("program: insights synthesis", programs.SYNTHESIS_BUDGET)):
        print(f"  {name:<52}{secs:>4} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
