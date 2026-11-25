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
