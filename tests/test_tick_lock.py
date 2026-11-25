"""The tick yields ``tick.lock`` to a waiting send or program step (issue #45, ``ticklock``).

A scheduled tick used to hold the lock across every step, model calls included, so a ``/next`` or ``journal send``
waited for the whole tick. Now the tick lets a waiter go at its safe points (between steps, and between the model
calls of a step that makes several) and takes the lock back after it; sends stay ordered under the lock. Everything
runs in threads with deadlines, so a deadlock fails the test instead of hanging it."""
import fcntl
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from helpers import add_prompt, make_cfg

from journal import agent, capture, ledger, programs, telegram, tick, ticklock

DEADLINE = 10  # seconds: far above anything here, so only a deadlock reaches it
# A Tuesday, 14:00 Asia/Kolkata (the helpers' zone): no ritual due, so a tick sends nothing of its own
QUIET = datetime(2026, 10, 13, 8, 30, tzinfo=timezone.utc)
# 06:05 Asia/Kolkata on a Wednesday: the morning ritual is due
MORNING = datetime(2026, 10, 14, 0, 35, tzinfo=timezone.utc)


def _clock(start: datetime, step: timedelta = timedelta(0)):
    """``tick``'s ``datetime`` with ``now()`` from ``start``, moving ``step`` on each call (a send's ``sent_at``)."""
    lock, at = threading.Lock(), [start]

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            with lock:
                t = at[0]
                at[0] = t + step
            return t.astimezone(tz) if tz else t.replace(tzinfo=None)
    return Clock


def _until(cond, what: str):
    """Wait for ``cond`` (a test's own wait: the engine never polls)."""
    end = time.monotonic() + DEADLINE
    while not cond():
        if time.monotonic() > end:
            raise AssertionError(f"timed out waiting for {what}")
        time.sleep(0.01)


class TickLockCase(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.audio["transcribe"] = False
        self.events: list[str] = []
        self.errors: list[str] = []
        self.patches = [mock.patch("journal.context.companion_reply_after", lambda *a, **k: None),
                        mock.patch.object(telegram, "token", lambda c: "T")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def thread(self, fn, *args):
        def body():
            try:
                fn(*args)
            except BaseException as e:  # noqa: BLE001 - reported by the test, not lost in the thread
                self.errors.append(repr(e))
        t = threading.Thread(target=body, daemon=True)
        t.start()
        return t

    def join(self, *threads):
        for t in threads:
            t.join(DEADLINE)
            self.assertFalse(t.is_alive(), "a thread is still waiting: deadlock")
        self.assertEqual(self.errors, [])

    def waiter(self, ready: threading.Event, then=None):
        """A spawned ``journal program …``: waits for tick.lock (``programs.locked``) once ``ready`` is set."""
        self.assertTrue(ready.wait(DEADLINE))
        with programs.locked(self.cfg):
            self.events.append("waiter")
            if then:
                then()

    def blocks_until_waited(self, name: str, ready: threading.Event):
        """A step that announces itself and returns only once a waiter is waiting for the lock it holds."""
        def step(*_a, **_k):
            self.events.append(name)
            ready.set()
            _until(lambda: ticklock.waiting(self.cfg), "the waiter")
        return step


class YieldTest(TickLockCase):
    def test_a_waiter_gets_the_lock_between_steps(self):
        """A ``/next`` that arrives during a step runs when that step ends, before the next one: not after the tick."""
        ready = threading.Event()
        with mock.patch.object(tick, "datetime", _clock(QUIET)), \
                mock.patch.object(tick, "fold", self.blocks_until_waited("fold", ready)), \
                mock.patch.object(tick, "process_audio", lambda *a: self.events.append("audio")), \
                mock.patch.object(tick, "annotate", lambda *a: self.events.append("annotate")):
            ticker = self.thread(lambda: self.events.append(("tick", tick.run(self.cfg)[0])))
            other = self.thread(self.waiter, ready)
            self.join(other, ticker)
        self.assertEqual(self.events, ["fold", "waiter", "audio", "annotate", ("tick", 0)])

    def test_a_waiter_gets_the_lock_between_two_annotations(self):
        """Inside a step that calls a model once per entry, the waiter goes between two calls (issue #45's measure:
        the rest of one call, not of every call after it)."""
        self.cfg.annotate_after_minutes = 0
        for spec in ("day", "week"):
            self.cfg.reflections.setdefault(spec, {})["enabled"] = False
        for k, kind in enumerate(("morning", "evening")):
            sent = QUIET - timedelta(hours=1, minutes=k)
            add_prompt(self.cfg, kind=kind, local_date="2026-10-13", sent=sent, message_id=str(100 + k))
            capture.capture(self.cfg, self.cfg.chat_id, str(100 + k), "slept badly", sent + timedelta(minutes=5),
                            "text", str(4242 + k))
        ready = threading.Event()
        first = self.blocks_until_waited("ask", ready)

        def ask(*_a, **_k):
            if "ask" in self.events:
                self.events.append("ask")
            else:
                first()
            return "- a note\n"
        with mock.patch.object(tick, "datetime", _clock(QUIET)), mock.patch.object(agent, "ask", ask), \
                mock.patch.object(telegram, "_call", lambda *a, **k: {"message_id": 1}):
            ticker = self.thread(tick.run, self.cfg)
            other = self.thread(self.waiter, ready)
            self.join(other, ticker)
        self.assertEqual(self.events, ["ask", "waiter", "ask"])
        snap = ledger.snapshot(self.cfg.ledger_path)
        self.assertTrue(all(p.get("annotated_at") for p in snap["prompts"]))  # both done, once each
        self.assertTrue(all(p.get("annotate_attempts") == 1 for p in snap["prompts"]))

    def test_a_tick_that_starts_while_another_is_yielded_skips_its_round(self):
        """``tick-run.lock``: the gap is the moment the first tick has let go of ``tick.lock`` and the waiter has not
        yet taken it. Here the waiting flag is held (shared, as a waiter does) with no one taking ``tick.lock``, so
        the first tick stays yielded and ``tick.lock`` is free. A second tick then finds nothing but ``tick-run.lock``
        in its way: it must skip, not run in the gap.

        The test never probes the lock files (a probe's own ``flock`` can make the first tick's non-blocking take of
        ``tick.lock`` fail, so it skips its round, and a probe can pass before the tick has taken anything). It
        watches the tick itself: a spy on ``ticklock``'s ``flock`` sets ``yielded`` right after the tick's own
        ``LOCK_UN`` of ``tick.lock``. From then on the state is fixed until the test lets go of the flag: the first
        tick holds ``tick-run.lock`` and waits on the flag; ``tick.lock`` is free."""
        yielded, real = threading.Event(), fcntl.flock

        class Spy:
            """``ticklock``'s ``fcntl``, telling the test when the tick lets go of ``tick.lock``."""
            def __getattr__(self, name):
                return getattr(fcntl, name)

            @staticmethod
            def flock(f, op):
                real(f, op)
                if op == fcntl.LOCK_UN and str(getattr(f, "name", "")).endswith(ticklock.LOCK):
                    yielded.set()

        ran: list = []
        with mock.patch.object(tick, "datetime", _clock(QUIET)), \
                mock.patch.object(tick, "fold", lambda *a: self.events.append("fold")), \
                mock.patch.object(tick, "process_audio", lambda *a: self.events.append("audio")), \
                mock.patch.object(ticklock, "fcntl", Spy()):
            self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
            first = second = None
            with open(self.cfg.state_dir / ticklock.WAIT, "a+") as flag:
                fcntl.flock(flag, fcntl.LOCK_SH)  # a waiter's flag, and it never takes tick.lock
                try:
                    first = self.thread(tick.run, self.cfg)
                    self.assertTrue(yielded.wait(DEADLINE), "the first tick never yielded")
                    self.assertTrue(self._lock_free(ticklock.LOCK))  # the gap: yielded, and no one has it
                    second = self.thread(lambda: ran.append(tick.run(self.cfg)))  # a second tick, in the gap
                    second.join(DEADLINE / 2)  # it skips at once; a tick that ran would block on the flag
                    skipped = not second.is_alive()
                finally:
                    fcntl.flock(flag, fcntl.LOCK_UN)  # the waiter is gone: the first tick takes its lock back
            self.join(*(t for t in (first, second) if t))
        self.assertTrue(skipped, "the second tick did not skip: it ran in the gap")
        self.assertEqual(ran, [(0, [])])  # skipped, nothing run
        self.assertEqual(self.events, ["fold", "audio"])  # (one of each: the second tick ran no step)

    def _lock_free(self, name: str) -> bool:
        """A non-blocking probe of one of the lock files: free when nobody holds it. Only where nothing else is
        about to take that lock (a probe holds it for an instant and a non-blocking taker would lose)."""
        with open(self.cfg.state_dir / name, "a+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            fcntl.flock(probe, fcntl.LOCK_UN)
            return True

    def test_a_tick_still_skips_while_a_send_holds_the_lock(self):
        with ticklock.waited(self.cfg):
            done = []
            t = self.thread(lambda: done.append(tick.run(self.cfg)))
            self.join(t)
        self.assertEqual(done, [(0, [])])

    def test_no_waiter_no_yield(self):
        self.assertFalse(ticklock.pause())  # (outside a tick: nothing to yield)
        with ticklock.scheduled(self.cfg) as ours:
            self.assertTrue(ours)
            self.assertFalse(ticklock.waiting(self.cfg))
            self.assertFalse(ticklock.pause())
            self.assertEqual(ticklock.yields(), 0)


class OrderTest(TickLockCase):
    def test_a_send_during_a_yielded_tick_and_the_ticks_send_do_not_race(self):
        """A ``journal send`` that arrives while the tick's opener is with the model waits for the tick's own send
        (its interrupts and practices go out together), then goes at the tick's next safe point, before the tick's
        later steps. Every message is sent with tick.lock held, one at a time, and the ledger's order of sends is the
        chat's: the newest prompt (what a reply is matched to) is the one sent last."""
        ready, sends, inside = threading.Event(), [], [0]
        mids = iter(range(500, 600))

        def opener(cfg, practice, now_local, pid, log=None):
            if pid.endswith("-morning"):  # the tick's, slow: the deliberate send arrives meanwhile
                self.blocks_until_waited("opener", ready)()
            return "Hello", "plain:test", [], []

        def send_html(cfg, chat_id, text, **_k):
            inside[0] += 1
            try:
                self.assertEqual(inside[0], 1, "two sends at once")
                with open(self.cfg.state_dir / ticklock.LOCK, "a+") as probe:  # the sender holds the lock
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
                time.sleep(0.05)
                mid = str(next(mids))
                sends.append(mid)
                self.events.append("sent")
                return mid
            finally:
                inside[0] -= 1

        def deliberate():
            self.assertTrue(ready.wait(DEADLINE))
            code, _log = tick.run(self.cfg, only="evening")
            self.assertEqual(code, 0)

        with mock.patch.object(tick, "datetime", _clock(MORNING, timedelta(seconds=1))), \
                mock.patch.object(tick, "_opener", opener), mock.patch.object(telegram, "send_html", send_html), \
                mock.patch("journal.privacy.update", lambda *a, **k: self.events.append("privacy")):
            ticker = self.thread(lambda: self.events.append(("tick", tick.run(self.cfg)[0])))
            other = self.thread(deliberate)
            self.join(other, ticker)
        # the tick's send first, then the waiter's at the safe point before "privacy", then the rest of the tick
        self.assertEqual(self.events[:4], ["opener", "sent", "sent", "privacy"])
        self.assertEqual(self.events[-1], ("tick", 0))
        data = ledger.snapshot(self.cfg.ledger_path)
        sent = sorted((p for p in data["prompts"] if p.get("message_id")), key=lambda p: p["sent_at"])
        self.assertEqual([p["kind"] for p in sent], ["morning", "evening"])
        self.assertEqual([p["message_id"] for p in sent], sends)  # ledger order == chat order
        morning, evening = sent
        self.assertTrue(capture.superseded(data, morning))  # his next message answers the evening prompt
        self.assertFalse(capture.superseded(data, evening))


if __name__ == "__main__":
    unittest.main()
