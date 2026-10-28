"""Old review findings on the privacy pass, its views and its windows (issues #52, #53, #57, #58, #60).

Each case failed on main before its fix. Every one asserts the safe outcome: off-the-record words stay out of
every reader, and public words still reach the readers that show them.
"""
import json
from datetime import datetime, timedelta

from helpers import add_prompt
from test_privacy_coverage import Base, m
from test_sweep import FakeSessions

from journal import capture, context, conversations, privacy, recovery
from journal.validate import norm


class HistoryLimitTest(Base):
    """#60: the pack's ``telegram_history.limit`` counts the messages it can show, not the rows it read: a burst of
    off-the-record messages must not use up the limit and push his public ones out of the pack."""

    def test_a_burst_of_off_record_messages_does_not_use_up_the_limit(self):
        self.cfg.sources["telegram_history"] = {"enabled": True, "days": 7, "limit": 3}
        self.his("the lake was cold this morning", m(0), "1")
        self.his("and the bread came out well", m(1), "2")
        for k in range(4):
            conversations.note_off(self.cfg, f"1{k}", m(3 + k))
            self.his(capture.off_record_note(self.cfg) + f"my secret is number {k}", m(3 + k), f"1{k}")
        lines = [line for _at, line in context._his_messages(self.cfg, now=m(30))]
        self.assertEqual([line.split(" — ", 1)[1] for line in lines],
                         ["the lake was cold this morning", "and the bread came out well"])

    def test_the_limit_still_holds_over_public_messages(self):
        self.cfg.sources["telegram_history"] = {"enabled": True, "days": 7, "limit": 2}
        for k, words in enumerate(("first words", "second words", "third words")):
            self.his(words, m(k), f"2{k}")
        conversations.note_off(self.cfg, "29", m(5))
        self.his(capture.off_record_note(self.cfg) + "my secret is here", m(5), "29")
        lines = [line for _at, line in context._his_messages(self.cfg, now=m(30))]
        self.assertEqual([line.split(" — ", 1)[1] for line in lines], ["second words", "third words"])


class PromptBoundaryCacheTest(Base):
    """#58: a prompt sent by another process (the tick, ``journal send``) ends a bare-/x window, and the gateway's
    briefly cached view must not hide that send: his reply right after it would be taken off the record."""

    def sent(self, how, at):
        if how == "ledger":
            add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=at, message_id="700")
        elif how == "sent.jsonl":
            with open(self.cfg.state_dir / "sent.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"sent_at": at.isoformat()}) + "\n")
        else:
            privacy.remember_sends(self.cfg, [at.isoformat()])

    def test_a_send_from_another_process_closes_the_window_at_once(self):
        for how in ("ledger", "sent.jsonl", "send-times.json"):
            with self.subTest(how=how):
                self.tearDown()
                self.setUp()
                self.tick(m(-30))
                conversations.note_off(self.cfg, None, m(0), thread_start=True)  # a bare /x the gateway answered
                privacy._CACHE.clear()
                self.assertTrue(privacy.window_open(self.cfg, m(1)))  # the gateway's view, now cached
                self.sent(how, m(1) + timedelta(seconds=10))
                self.assertFalse(privacy.window_open(self.cfg, m(1) + timedelta(seconds=20)))


class ReplacedDatabaseTest(Base):
    """#57: the saved pass names rows by id, and ids mean nothing in another sessions database. A replacement (a
    rebuilt or new Hermes database) that grows into the old ids must not inherit their private answers, which would
    drop his public messages; what the old database said privately stays out all the same."""

    def replace_database(self):
        self.db.path.unlink()
        self.db = FakeSessions(self.db.path)

    def test_old_private_ids_do_not_hide_new_rows(self):
        self.tick(m(-30))
        self.exchange()  # rows 1-7, the /x message among them
        self.tick(m(12))
        self.assertTrue(conversations.snapshot(self.cfg)["privacy"]["private"])  # saved by id
        self.replace_database()
        self.tick(m(20))  # the writer sees the new database and starts again from here
        for k in range(8):
            self.his(f"public words number {k}", m(21 + k), f"9{k}")
        self.tick(m(40))
        body = self.files()
        self.assertEqual([k for k in range(8) if f"public words number {k}" not in body], [])
        self.assertEqual(self.leaked(), [])

    def test_the_view_drops_them_before_the_next_tick(self):
        """The gateway asks between ticks: the view itself knows the store changed."""
        self.tick(m(-30))
        self.exchange()
        self.tick(m(12))
        self.replace_database()
        for k in range(8):
            self.his(f"public words number {k}", m(21 + k), f"9{k}")
        privacy._CACHE.clear()
        self.assertEqual(privacy.view(self.cfg, m(30)).private, {})

    def test_the_same_database_keeps_them(self):
        self.tick(m(-30))
        self.exchange()
        self.tick(m(12))
        saved = {int(k) for k in conversations.snapshot(self.cfg)["privacy"]["private"]}
        self.tick(m(12) + timedelta(hours=100))  # a pass saved long after, over the same database
        privacy._CACHE.clear()
        # the rows are now older than what the view recomputes, so only the saved answers name them
        later = privacy.view(self.cfg, m(12) + timedelta(hours=101))
        self.assertGreater(later.start, m(12))
        self.assertTrue(saved and saved <= set(later.private))


    def test_a_cached_view_does_not_outlive_the_database(self):
        """The gateway's cached view is not reused for a database replaced at the same path, even when the new
        one's newest row id is the same as the old one's."""
        self.tick(m(-30))
        self.exchange()
        self.tick(m(12))
        top = self.cfg.transcript().max_id()
        privacy._CACHE.clear()
        self.assertTrue(privacy.view(self.cfg, m(30)).private)  # cached
        self.replace_database()
        for k in range(top):
            self.his(f"public words number {k}", m(21 + k), f"9{k}")
        self.assertEqual(self.cfg.transcript().max_id(), top)
        self.assertEqual(privacy.view(self.cfg, m(30)).private, {})

    def test_a_deleted_row_on_the_same_database_keeps_the_saved_ids(self):
        """Review of PR #77: Hermes deletes rows in normal use (a deleted session, a transcript rewritten after a
        model switch). That must not drop the saved private ids: a compaction copy whose original is older than a
        reader's recompute is private only by its original's saved id."""
        self.tick(m(-30))
        conversations.note_off(self.cfg, None, m(0), thread_start=True)  # a bare /x the gateway answered
        self.steer("busy-private words, queued", m(1), "71")  # no mark of its own, no id recorded
        self.hers("busy-private reply", m(2))
        self.his("public after the window", m(120), "72")
        self.hers("Sure, what is it?", m(121))
        self.tick(m(130))
        saved = set(conversations.snapshot(self.cfg)["privacy"]["private"])
        self.assertEqual(len(saved), 2)
        for k in range(1, 6):
            self.tick(m(130) + timedelta(days=k))
        later = m(130) + timedelta(days=5, hours=1)
        self.db.add("a row in another session", later, session="tool", role="assistant")  # the newest row
        self.tick(later)
        self.db.sql("delete from messages where session_id = 'tool'")
        self.tick(later + timedelta(minutes=5))
        self.assertEqual(set(conversations.snapshot(self.cfg)["privacy"]["private"]), saved)
        # a compaction rotation carries the old turn into a child session, with new times
        self.db.sql("insert into sessions (id, source, chat_id, session_key, user_id, started_at, parent_session_id) "
                    "values ('tg2', 'telegram', ?, 'k', ?, 5.0, 'tg')", self.cfg.chat_id, self.cfg.chat_id)
        self.db.sql("update sessions set ended_at = 6.0, end_reason = 'compression' where id = 'tg'")
        copied = later + timedelta(minutes=10)
        self.db.add("[OUT-OF-BAND USER MESSAGE — a direct message]\nbusy-private words, queued\n"
                    "[/OUT-OF-BAND USER MESSAGE]", copied, "71", display_kind="steer", session="tg2")
        self.db.add("busy-private reply", copied, role="assistant", session="tg2")
        privacy._CACHE.clear()
        said = " ".join(t for _w, _a, t in recovery.scan(self.cfg, self.db.path, copied + timedelta(minutes=5))
                        ["before"])
        self.assertNotIn("busy-private", said)


class OutOfOrderWindowTest(Base):
    """#53: a queued message is stored after rows with later times (it carries the time he sent it). The pass walks
    rows in stored order, so the window could lapse before the queued row was reached, and the row, private by its
    own time, was then tested only against the open window: it was read as public. It is tested against the closed
    windows too, and its own time is activity of the window, as if it had been stored in order."""

    def opened(self):
        self.tick(m(-30))
        conversations.note_off(self.cfg, None, m(0), thread_start=True)  # a bare /x the gateway answered
        conversations.note_off(self.cfg, "11", m(1))
        self.his(capture.off_record_note(self.cfg, thread=True) + "window-private one", m(1), "11")
        self.hers("window-private reply one", m(2))

    def private(self, at):
        privacy._CACHE.clear()
        rows = self.cfg.transcript().rows(since=m(-10))
        pv = privacy.view(self.cfg, at)
        return {r.text for r in rows if r.id in pv.private}

    def test_a_queued_row_inside_a_window_that_lapsed_before_it_was_stored(self):
        self.opened()
        self.his("public after the window", m(100), "13")
        self.steer("busy-private words sent at minute thirty", m(30), "12")  # stored now, sent at minute 30
        self.hers("busy-private reply to both", m(101))
        self.assertIn("busy-private words sent at minute thirty", self.private(m(102)))
        self.assertIn("busy-private reply to both", self.private(m(102)))  # her answer took in a private row
        self.tick(m(110))
        self.assert_safe("public after the window")

    def test_its_time_keeps_the_window_going_for_rows_stored_before_it(self):
        """By its own time the queued message is his activity at minute 30, so the window still held at minute 50,
        when a message stored before it was sent (quiet_minutes is 45)."""
        self.opened()
        self.steer("steered secret sent at minute fifty", m(50), "13")
        self.steer("busy-private words sent at minute thirty", m(30), "12")
        self.hers("busy-private reply", m(51))
        self.assertTrue(privacy.window_open(self.cfg, m(60)))  # 45 minutes from the last activity, not before
        self.his("public after the window", m(200), "14")
        self.hers("Sure, what is it?", m(201))
        private = self.private(m(202))
        for words in ("steered secret sent at minute fifty", "busy-private words sent at minute thirty",
                      "busy-private reply"):
            self.assertIn(words, private)
        self.assertNotIn("public after the window", private)
        self.tick(m(210))
        self.assert_safe("public after the window")

    def test_a_queued_photo_inside_a_lapsed_window_and_her_reply_to_it(self):
        self.opened()
        self.his("public after the window", m(100), "13")
        self.his("", m(30), "12")  # a captionless photo sent at minute 30, stored now
        self.hers("I see the busy-private picture", m(101))
        private = self.private(m(102))
        self.assertIn("I see the busy-private picture", private)
        self.tick(m(110))
        self.assert_safe("public after the window")

    def test_a_queued_message_sent_after_the_window_stays_public(self):
        self.opened()
        self.his("a later message", m(100), "13")
        self.steer("public words sent at minute sixty", m(60), "12")  # after the window ended at minute 47
        self.hers("Sure, tell me.", m(101))
        self.assertNotIn("public words sent at minute sixty", self.private(m(102)))

    def test_a_queued_message_after_a_prompt_send_stays_public(self):
        """A prompt ends the window; a queued message sent after it is his answer, whatever was stored before."""
        self.opened()
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(10), message_id="700")
        self.his("a later message", m(100), "13")
        self.steer("public words sent at minute twenty", m(20), "12")
        self.assertNotIn("public words sent at minute twenty", self.private(m(102)))


class RetentionTest(Base):
    """#52: the hook's records of a bare /x it answered (``off_threads``) and of the messages it took off the record
    (``off``) are the only sign of privacy for rows that carry no mark of their own (messages steered in on the busy
    path inside a bare-/x window). They were pruned after 400 days, and the history they cover can be read for
    longer: a backfill with an older ``--since``, a quote verifier that reads the whole chat. They are the pass's
    inputs, kept for good like the prompt sends."""
    cursor_first = False

    def long_ago(self):
        conversations.note_off(self.cfg, None, m(0), thread_start=True)  # a bare /x the gateway answered (no row)
        self.steer("busy-private words, steered in", m(1), "71")  # the busy path: no mark, no id recorded
        self.hers("busy-private reply", m(2))
        self.his("public after the window", m(120), "72")
        self.hers("Sure, what is it?", m(121))
        later = m(200) + timedelta(days=401)
        self.tick(later)  # the writer starts here; the pass is saved
        self.tick(later + timedelta(minutes=10))  # a commit, which prunes
        return later + timedelta(minutes=20)

    def test_a_backfill_older_than_a_year_keeps_a_bare_x_window(self):
        now = self.long_ago()
        conversations.backfill(self.cfg, m(0).date(), write=True, now=now)
        self.assert_safe("public after the window")

    def test_the_quote_verifier_still_refuses_the_window(self):
        self.long_ago()
        privacy._CACHE.clear()
        verify = context.make_verifier(self.cfg)
        self.assertFalse(verify("telegram:x", norm("busy-private words, steered in")))
        self.assertTrue(verify("telegram:x", norm("public after the window")))

    def test_the_records_are_kept(self):
        self.long_ago()
        st = conversations.snapshot(self.cfg)
        self.assertTrue(st.get("off_threads"))

