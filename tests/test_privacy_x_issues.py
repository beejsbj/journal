"""Open /x findings on the privacy pass (issues #4 and #5).

Each case failed on main before its fix. Every one asserts the safe outcome: off-the-record words stay out of every
reader, and public words still reach the readers that show them.
"""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import FakeTranscript, make_cfg
from test_privacy_coverage import Base, m

from journal import context, conversations, privacy, recovery
from journal.config import Config
from journal.ports import Row


class OldOriginalCopyTest(Base):
    """#4: a compaction copy whose original is older than the view's recompute was private only by the original's
    saved id, and the saved pass ages out at ``KEEP_DAYS``. A copy of a private row more than that old, with no mark
    of its own (a message steered in inside a bare-/x window, her reply in that window), read as public to the
    readers of raw rows. The view now re-takes the pass from the original's time when it holds such a copy."""
    cursor_first = False

    def long_ago(self):
        """A bare /x the gateway answered, a steered message and her reply inside it, then a public exchange; the
        saved pass is taken, then 401 days later it ages out."""
        conversations.note_off(self.cfg, None, m(0), thread_start=True)  # no row: only the hook's record
        self.steer("busy-private words, steered in", m(1), "71")  # the busy path: no mark, no id recorded
        self.hers("busy-private reply", m(2))
        self.his("public after the window", m(120), "72")
        self.hers("Sure, what is it?", m(121))
        self.tick(m(130))
        later = m(130) + timedelta(days=KEEP + 1)
        self.tick(later)
        self.tick(later + timedelta(minutes=5))
        return later + timedelta(minutes=10)

    def rotate(self, at, public=True):
        """Compaction with ``in_place`` false: the carried tail goes into a child session, stamped with ``at``."""
        self.db.sql("insert into sessions (id, source, chat_id, session_key, user_id, started_at, parent_session_id) "
                    "values ('tg2', 'telegram', ?, 'k', ?, 5.0, 'tg')", self.cfg.chat_id, self.cfg.chat_id)
        self.db.sql("update sessions set ended_at = 6.0, end_reason = 'compression' where id = 'tg'")
        self.db.add("[OUT-OF-BAND USER MESSAGE — a direct message]\nbusy-private words, steered in\n"
                    "[/OUT-OF-BAND USER MESSAGE]", at, "71", display_kind="steer", session="tg2")
        self.db.add("busy-private reply", at, role="assistant", session="tg2")
        if public:
            self.db.add("public after the window", at, "72", session="tg2")
            self.db.add("Sure, what is it?", at, role="assistant", session="tg2")

    def test_the_saved_answers_have_aged_out(self):
        """The precondition: nothing saved names the old private rows any more."""
        self.long_ago()
        self.assertEqual(conversations.snapshot(self.cfg)["privacy"]["private"], {})

    def test_the_view_makes_the_copies_private(self):
        at = self.long_ago()
        self.rotate(at)
        privacy._CACHE.clear()
        rows = self.cfg.transcript().rows(since=at - timedelta(minutes=1))
        self.assertEqual(len(rows), 4)
        self.assertTrue(all(r.copy for r in rows))
        pv = privacy.view(self.cfg, at + timedelta(minutes=5))
        private = {r.text for r in rows if pv.row(r.id)}
        self.assertEqual(private, {r.text for r in rows if "busy-private" in r.text})

    def test_the_catch_up_skips_the_copies(self):
        at = self.long_ago()
        self.rotate(at)
        privacy._CACHE.clear()
        said = " ".join(t for _w, _a, t in recovery.scan(self.cfg, self.db.path, at + timedelta(minutes=5))["before"])
        self.assertNotIn("busy-private", said)
        self.assertIn("public after the window", said)

    def test_the_pack_leaves_out_his_steered_copy(self):
        """His own chat in the pack (capture mode ``everything``): the copy of his steered message is left out, his
        public message's copy is in."""
        self.cfg.capture = {"mode": "everything"}
        self.cfg.sources["telegram_history"] = {"enabled": True, "days": 7, "limit": 25}
        at = self.long_ago()
        self.rotate(at)
        privacy._CACHE.clear()
        said = " ".join(line for _at, line in context._his_messages(self.cfg, now=at + timedelta(minutes=5)))
        self.assertNotIn("busy-private", said)
        self.assertIn("public after the window", said)

    def test_the_saved_pass_keeps_the_copies(self):
        """The next tick saves the copies' own ids, with their own (recent) times."""
        at = self.long_ago()
        self.rotate(at)
        self.tick(at + timedelta(minutes=5))
        saved = {int(k) for k in conversations.snapshot(self.cfg)["privacy"]["private"]}
        rows = self.cfg.transcript().rows(since=at - timedelta(minutes=1))
        self.assertEqual(saved, {r.id for r in rows if "busy-private" in r.text})

    def test_a_recent_original_costs_no_re_take(self):
        """The view goes back only for a copy whose original it did not read."""
        self.tick(m(-30))
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        self.steer("busy-private words, steered in", m(1), "71")
        self.hers("busy-private reply", m(2))
        self.tick(m(30))
        self.rotate(m(40), public=False)
        privacy._CACHE.clear()
        pv = privacy.view(self.cfg, m(45))
        self.assertEqual(pv.start, m(30) - privacy.RECOMPUTE)
        rows = self.cfg.transcript().rows(since=m(39))
        self.assertTrue(rows and all(r.copy and pv.row(r.id) for r in rows))


class DisagreeingReadsTest(unittest.TestCase):
    """#4, its failure path: a transcript that returns an original by its id with a time the view's own read should
    have held, but did not return it by time. The view fails closed at once, with no read stepping back."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        self.now = now
        rows = [Row(1, now - timedelta(hours=1), "user", "his", "public words", "11"),
                Row(2, now - timedelta(minutes=30), "user", "his", "public words", "11", copy=1)]
        self.reads = 0

        class Odd(FakeTranscript):
            def rows(chat, after_id=0, since=None, upto_id=None):
                self.reads += 1
                found = super().rows(after_id, since, upto_id)
                return [r for r in found if since is None or r.id != 1]  # the original, never by time

        chat = Odd(rows, source="memory")
        patch = mock.patch.object(Config, "transcript", lambda cfg, db=None: chat)
        patch.start()
        self.addCleanup(patch.stop)
        privacy._CACHE.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_view_fails_closed(self):
        v = privacy.view(self.cfg, self.now)
        self.assertTrue(v.closed)
        self.assertIn("newer than the view's start", v.why)
        self.assertLessEqual(self.reads, 2)


class BusyBareXOwnTimeTest(Base):
    """#5: a bare /x that came in on the busy path (a stored row) opened its window at the stored-order clock, not at
    its own time. A message he sent after the /x but stored before it was read before any window existed, and was
    public. The window now opens at the /x's own time too (only ever more private)."""

    def private(self, at):
        privacy._CACHE.clear()
        rows = self.cfg.transcript().rows(since=m(-10))
        pv = privacy.view(self.cfg, at)
        return {r.text for r in rows if r.id in pv.private}

    def test_a_message_sent_after_a_queued_x_but_stored_before_it(self):
        self.tick(m(-30))
        self.his("a question", m(0), "20")
        self.steer("busy-private words sent after the x", m(2), "22")  # stored first
        self.his("/x", m(1), "21")  # queued: stored after it, with the time he sent it
        self.hers("busy-private reply", m(3))
        self.his("public after the window", m(120), "24")
        self.hers("public reply", m(121))
        private = self.private(m(122))
        self.assertIn("busy-private words sent after the x", private)
        self.assertIn("busy-private reply", private)
        self.assertNotIn("a question", private)
        self.assertNotIn("public after the window", private)
        self.tick(m(130))
        self.assert_safe("public after the window")

    def test_a_message_sent_before_the_x_stays_public(self):
        self.tick(m(-30))
        self.steer("public words sent before the x", m(0), "20")
        self.his("/x", m(1), "21")
        self.hers("a reply", m(2))
        self.assertNotIn("public words sent before the x", self.private(m(3)))

    def test_her_answer_stored_before_a_queued_x_sent_before_it_is_private(self):
        """The window rule, now by the /x's own time: a /x he sent at 10:01, queued during her turn and stored after
        her 10:03 answer to his public 10:00 question, makes that answer private too (more private: the window
        is open from 10:01). His question stays public."""
        self.tick(m(-30))
        self.his("a public question", m(0), "20")
        self.hers("her answer, inside the window", m(3))
        self.his("/x", m(1), "21")  # sent at 10:01, stored after her answer
        self.hers("busy-private reply", m(4))
        private = self.private(m(5))
        self.assertIn("her answer, inside the window", private)
        self.assertIn("busy-private reply", private)
        self.assertNotIn("a public question", private)

    def test_the_stored_order_start_still_holds(self):
        """The old start is kept: a window from the /x's own time that lapsed before the row was stored still leaves
        the rows after the stored /x inside a window, as before."""
        self.tick(m(-30))
        self.his("a question", m(0), "20")
        self.hers("an answer", m(100))
        self.his("/x", m(1), "21")  # queued behind a long turn, stored at minute 100's place
        self.steer("busy-private words after the stored x", m(101), "22")
        self.hers("busy-private reply", m(102))
        private = self.private(m(103))
        self.assertIn("busy-private words after the stored x", private)
        self.assertIn("busy-private reply", private)


KEEP = privacy.KEEP_DAYS


class SliceBoundsTest(Base):
    """#4, the slice's bounds (review of PR #70): the slice reaches far enough each way, and a window begun before
    it, which it cannot see, makes the copies private rather than public."""
    cursor_first = False

    def rotate(self, at, carried):
        """Compaction with ``in_place`` false: ``carried`` (kind, text, Telegram id) go into a child session."""
        self.db.sql("insert into sessions (id, source, chat_id, session_key, user_id, started_at, parent_session_id) "
                    "values ('tg2', 'telegram', ?, 'k', ?, 5.0, 'tg')", self.cfg.chat_id, self.cfg.chat_id)
        self.db.sql("update sessions set ended_at = 6.0, end_reason = 'compression' where id = 'tg'")
        for kind, text, mid in carried:
            if kind == "steer":
                self.db.add("[OUT-OF-BAND USER MESSAGE — a direct message]\n" + text + "\n[/OUT-OF-BAND USER MESSAGE]",
                            at, mid, display_kind="steer", session="tg2")
            elif kind == "his":
                self.db.add(text, at, mid, session="tg2")
            else:
                self.db.add(text, at, role="assistant", session="tg2")

    def copies(self, end, carried, days=KEEP + 1):
        """Ticks every six hours to ``end`` (as the live install runs), the saved pass aged out ``days`` later, then a
        compaction carrying ``carried``: the copies the view reads private, and the view."""
        k = 0
        while m(k * 360) <= end:
            self.tick(m(k * 360), steps=("privacy",))
            k += 1
        later = end + timedelta(days=days)
        self.tick(later, steps=("privacy",))
        at = later + timedelta(minutes=10)
        self.rotate(at, carried)
        privacy._CACHE.clear()
        rows = self.cfg.transcript().rows(since=at - timedelta(minutes=1))
        self.assertEqual(len(rows), len(carried))
        pv = privacy.view(self.cfg, at + timedelta(minutes=5))
        self.assertFalse(pv.closed, pv.why if pv.closed else "")
        return {r.text for r in rows if pv.row(r.id)}, pv

    def long_window(self, quiet, every, hours):
        """A bare /x the hook answered, kept open by steered messages ``every`` minutes apart for ``hours`` (less than
        ``quiet_minutes`` apart, no prompt send): its last message lies more than ``RECOMPUTE`` after the /x."""
        self.cfg.conversations["quiet_minutes"] = quiet
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        t, n = 1, 0
        while t < hours * 60:
            self.steer(f"filler words {n}", m(t), str(100 + n))
            t += every
            n += 1
        self.steer("busy-private words late in the long window", m(t), "71")
        self.hers("busy-private reply late in the long window", m(t + 1))
        self.his("public after the window", m(t + quiet + 600), "72")
        self.hers("Sure, what is it?", m(t + quiet + 601))
        private, pv = self.copies(m(t + quiet + 700), [("steer", "busy-private words late in the long window", "71"),
                                                      ("hers", "busy-private reply late in the long window", None),
                                                      ("his", "public after the window", "72")])
        self.assertEqual(private, {"busy-private words late in the long window",
                                   "busy-private reply late in the long window"})
        self.assertEqual(pv.retake["uncertain"], 2)  # (answered by the window at the slice's start)

    def test_a_window_begun_more_than_the_slice_before_a_quiet_day(self):
        """``quiet_minutes`` a day, a message every 20 hours: the /x is 60 hours before the original."""
        self.long_window(quiet=1440, every=20 * 60, hours=60)

    def test_a_window_begun_more_than_the_slice_before_a_chatty_one(self):
        """The default quiet, a message every 30 minutes for 50 hours."""
        self.long_window(quiet=45, every=30, hours=50)

    def test_the_copies_of_one_long_window_carried_early_and_late(self):
        """One long window; a compaction carries an early row (within ``RECOMPUTE`` of the /x) and a late one (more
        than twice that after it): two slices, the late one blind to the /x."""
        self.cfg.conversations["quiet_minutes"] = 1440
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        self.steer("busy-private early words", m(1), "70")
        self.hers("busy-private early reply", m(2))
        for n, h in enumerate(range(20, 160, 20)):
            self.steer(f"busy-private filler {n}", m(h * 60), str(100 + n))
        self.steer("busy-private late words", m(150 * 60), "71")
        self.hers("busy-private late reply", m(150 * 60 + 1))
        end = m(150 * 60 + 1440 + 600)
        self.his("public after the window", end, "72")
        private, pv = self.copies(end + timedelta(minutes=30),
                                  [("steer", "busy-private early words", "70"), ("hers", "busy-private early reply", None),
                                   ("steer", "busy-private late words", "71"), ("hers", "busy-private late reply", None)])
        self.assertEqual(len(private), 4)
        self.assertEqual(pv.retake["slices"], 2)

    def test_a_queued_x_stored_after_the_old_original(self):
        """The slice reaches ``RECOMPUTE`` past the original: a bare /x he sent before it, queued and stored after it
        (#5), makes it private."""
        self.his("a question", m(0), "20")
        self.steer("busy-private words sent after the x", m(2), "22")  # stored first
        self.his("/x", m(1), "21")  # queued: stored after it, with the time he sent it
        self.hers("busy-private reply", m(3))
        self.his("public after the window", m(120), "24")
        # (only that copy: another original's slice would reach past it and hide a slice that stops at its own)
        private, pv = self.copies(m(130), [("steer", "busy-private words sent after the x", "22")])
        self.assertEqual(private, {"busy-private words sent after the x"})
        self.assertEqual(pv.retake["uncertain"], 0)

    def test_a_public_original_after_a_quiet_gap_stays_public(self):
        """The cost side: a slice that reaches back past the chat's last quiet gap proves the original public, and its
        copy reads public. (A slice reaching back less far would only guess: a window opened at its start runs on
        through his messages ten minutes apart, so the copy would read private.)"""
        self.his("an old public message", m(-300), "10")
        for n in range(18):
            self.his(f"public words {n}", m(n * 10), str(20 + n))
            self.hers(f"public reply {n}", m(n * 10 + 1))
        self.his("public words late in the chat", m(180), "40")
        private, pv = self.copies(m(200), [("his", "public words late in the chat", "40")], days=3)
        self.assertEqual(private, set())
        self.assertEqual(pv.retake["uncertain"], 0)


class SliceReadsTest(unittest.TestCase):
    """#4, over a transcript held in memory: a saved window takes the slice back to its start, and an original that
    one of the slice's reads does not return closes the view."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))
        now = self.now = datetime.now(timezone.utc).replace(microsecond=0)
        x = now - timedelta(days=13)  # a bare /x the hook answered, three days before the original
        rows, t, i = [], x + timedelta(minutes=1), 1
        while t < now - timedelta(days=10):
            rows.append(Row(i, t, "user", "his", f"busy-private filler {i}", str(1000 + i)))
            t, i = t + timedelta(minutes=30), i + 1
        self.original = i
        rows.append(Row(i, t, "user", "his", "busy-private words", str(1000 + i)))
        rows.append(Row(i + 1, t + timedelta(hours=2), "user", "his", "public words", str(1001 + i)))
        self.copy = i + 2
        rows.append(Row(i + 2, now - timedelta(minutes=30), "user", "his", "busy-private words", str(1000 + i),
                        copy=i))
        self.rows, self.drop = rows, None
        conversations.note_off(self.cfg, None, x, thread_start=True)
        with conversations.locked(self.cfg) as st:  # the saved pass: its window, its private rows aged out
            st["privacy"] = {"private": {}, "windows": [[x.isoformat(), (t + timedelta(minutes=45)).isoformat()]],
                             "at": (now - timedelta(hours=1)).isoformat()}
        test = self

        class Dropping(FakeTranscript):
            def rows(chat, after_id=0, since=None, upto_id=None):
                found = super().rows(after_id, since, upto_id)
                return [r for r in found if not (test.drop and test.drop(since) and r.id == test.original)]

        patch = mock.patch.object(Config, "transcript", lambda cfg, db=None: Dropping(self.rows, source="memory"))
        patch.start()
        self.addCleanup(patch.stop)
        privacy._CACHE.clear()

    def test_a_saved_window_takes_the_slice_back_to_its_start(self):
        """The /x lies more than ``RECOMPUTE`` before the original, but the saved window still open there takes the
        slice back to it: the slice answers the original private, with no guess."""
        v = privacy.view(self.cfg, self.now)
        self.assertFalse(v.closed, v.why if v.closed else "")
        self.assertTrue(v.row(self.copy))
        self.assertTrue(v.row(self.original))
        self.assertEqual(v.retake["uncertain"], 0)

    def test_a_slice_shared_with_an_earlier_original_keeps_the_saved_windows_start(self):
        """An earlier original (half a day before), in the same saved window: the shared slice begins at that
        window's start, and both are answered for sure."""
        early = next(r for r in self.rows if r.at >= self.now - timedelta(days=10, hours=12))
        self.rows.append(Row(self.copy + 1, self.now - timedelta(minutes=20), "user", "his", early.text,
                             early.platform_message_id, copy=early.id))
        v = privacy.view(self.cfg, self.now)
        self.assertFalse(v.closed, v.why if v.closed else "")
        self.assertEqual((v.retake["slices"], v.retake["uncertain"]), (1, 0))
        self.assertTrue(v.row(self.copy) and v.row(self.copy + 1))

    def test_an_original_its_id_read_does_not_return_closes_the_view(self):
        self.drop = lambda since: since is None
        v = privacy.view(self.cfg, self.now)
        self.assertTrue(v.closed)
        self.assertIn("could not be read", v.why)

    def test_an_original_its_slice_does_not_return_closes_the_view(self):
        self.drop = lambda since: since is not None
        v = privacy.view(self.cfg, self.now)
        self.assertTrue(v.closed)
        self.assertIn("not in its slice", v.why)
