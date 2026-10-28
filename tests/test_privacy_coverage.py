"""Coverage is proven by rows read, not by a stamp.

A saved privacy pass (``conversations.json`` ``privacy.at``) says when a pass ran, not which rows it read: with the
sessions database missing or pointed elsewhere, another chat id, or a clock that ran ahead, a pass is saved over rows
it never saw. A reader holding rows older than what the view recomputed (``View.start``) asks for a view that covers
them (``privacy.covering``). Each case below was seen leaking on the code before this rule (an adversarial probe
after earlier PR 15); each asserts the safe outcome: the off-the-record words stay out, the public words still go in.
"""
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import add_prompt, make_cfg
from test_sweep import FakeSessions, redirected

from journal import capture, context, conversations, privacy, recovery, sweep
from journal.validate import norm

T0 = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
SECRETS = ("secret", "won't tell", "window-private", "busy-private", "steered secret")


def m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class Base(unittest.TestCase):
    cursor_first = True  # the writer's first run (its cursor) before anything is said

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = make_cfg(t)
        self.db = FakeSessions(t / "state.db")
        self.cfg.hermes["state_db"] = str(self.db.path)
        privacy._CACHE.clear()
        if self.cursor_first:
            conversations.run(self.cfg, T0 - timedelta(hours=1), [])

    def tearDown(self):
        self.tmp.cleanup()

    def his(self, text, at, mid, **kw):
        self.db.add(text, at, mid, **kw)

    def hers(self, text, at):
        self.db.add(text, at, role="assistant")

    def steer(self, text, at, mid):
        self.db.add("[OUT-OF-BAND USER MESSAGE — a direct message]\n" + text + "\n[/OUT-OF-BAND USER MESSAGE]", at,
                    mid, display_kind="steer")

    def tick(self, at, steps=("privacy", "sweep", "conversations")):
        """What a tick does, in its order, each step caught on its own (a tick is a new process: no cache)."""
        privacy._CACHE.clear()
        log = []
        for s in steps:
            try:
                if s == "privacy":
                    privacy.update(self.cfg, at, log)
                elif s == "sweep":
                    sweep.run(self.cfg, at, log)
                else:
                    conversations.run(self.cfg, at, log)
            except Exception as e:  # noqa: BLE001 - the tick catches per step
                log.append(f"error: {s}: {type(e).__name__}: {e}")
        return log

    def files(self):
        return "".join(p.read_text() for p in sorted(self.cfg.root.glob("*.md")))

    def leaked(self):
        body = self.files()
        return [s for s in SECRETS if s in body]

    def exchange(self, b=0):
        self.his("start of the thread here", m(b), f"{b}1")
        self.hers("ok, go on", m(b + 1))
        conversations.note_off(self.cfg, f"{b}2", m(b + 3))
        self.his(capture.off_record_note(self.cfg) + "my secret is X", m(b + 3), f"{b}2")
        self.hers("About your secret X: I won't tell.", m(b + 5))
        self.his("thanks, new topic", m(b + 7), f"{b}4")
        self.hers("Sure, what is it?", m(b + 8))

    def assert_safe(self, public="new topic"):
        self.assertEqual(self.leaked(), [])
        if public:
            self.assertIn(public, self.files())


class StampWithoutRowsTest(Base):
    """Hole 1: a pass saved without reading the rows the writer later walks from its cursor."""

    def test_sessions_database_missing_for_three_days(self):
        self.tick(m(-30))
        self.exchange()
        away = self.db.path.with_suffix(".away")
        shutil.move(self.db.path, away)
        for k in range(0, 73, 6):
            log = self.tick(m(12) + timedelta(hours=k))
        self.assertTrue(any("privacy pass not saved" in line for line in log), log)
        shutil.move(away, self.db.path)
        self.tick(m(12) + timedelta(hours=73))
        self.assert_safe()

    def test_sessions_database_misconfigured_for_three_days(self):
        self.tick(m(-30))
        self.exchange()
        real = self.cfg.hermes["state_db"]
        self.cfg.hermes["state_db"] = str(Path(self.tmp.name) / "nope" / "state.db")
        for k in range(0, 73, 6):
            self.tick(m(12) + timedelta(hours=k))
        self.cfg.hermes["state_db"] = real
        self.tick(m(12) + timedelta(hours=73))
        self.assert_safe()

    def test_another_chat_id_for_three_days(self):
        """The database is there, so passes are saved, but over none of this chat's rows."""
        self.tick(m(-30))
        self.exchange()
        real = self.cfg.chat_id
        self.cfg.chat_id = "42"
        for k in range(0, 73, 6):
            self.tick(m(12) + timedelta(hours=k))
        self.cfg.chat_id = real
        self.tick(m(12) + timedelta(hours=73))
        self.assert_safe()

    def test_first_pass_saved_while_the_writer_was_off(self):
        self.exchange()
        self.cfg.conversations["enabled"] = False
        self.tick(m(12) + timedelta(hours=100))
        self.cfg.conversations["enabled"] = True
        self.tick(m(12) + timedelta(hours=101))
        self.assert_safe()

    def test_a_pass_stamped_by_a_clock_far_ahead_then_closed(self):
        self.tick(m(-30))
        privacy._CACHE.clear()
        privacy.update(self.cfg, m(-25) + timedelta(hours=100), [])  # only the pass ran with the bad clock
        self.exchange()
        with mock.patch.object(privacy, "compute", side_effect=RuntimeError("closed")):
            for k in range(0, 72, 6):
                self.tick(m(12) + timedelta(hours=k))
        self.tick(m(12) + timedelta(hours=73))
        self.assert_safe()

    def test_a_whole_tick_with_a_clock_far_ahead_then_an_outage(self):
        self.tick(m(-30))
        self.tick(m(-25) + timedelta(hours=100))
        self.exchange()
        self.tick(m(12) + timedelta(hours=73))
        self.assertEqual(self.leaked(), [])

    def test_the_writer_re_takes_the_view_from_its_oldest_row(self):
        """The writer's own check, whatever the stamp says."""
        self.tick(m(-30))
        self.exchange()
        with conversations.locked(self.cfg) as st:  # a pass that claims to have covered everything
            st["privacy"] = {"private": {}, "windows": [], "at": (m(12) + timedelta(hours=60)).isoformat()}
        privacy._CACHE.clear()
        pv = privacy.view(self.cfg, m(12) + timedelta(hours=61))
        self.assertGreater(pv.start, m(8))  # the default view did not recompute the exchange
        conversations.run(self.cfg, m(12) + timedelta(hours=61), [])
        self.assert_safe()

    def test_a_missing_database_is_not_stamped(self):
        Path(self.db.path).unlink()
        log = []
        privacy.update(self.cfg, m(10), log)
        self.assertNotIn("privacy", conversations.snapshot(self.cfg))
        self.assertIn("privacy pass not saved", " ".join(log))

    def test_the_writer_checks_the_rows_own_marks_too(self):
        """Defence in depth: a his row marked off the record by itself is never written, even if a view said
        it was public (the pass and the writer disagreeing must not mean his /x is saved)."""
        self.exchange()
        open_view = privacy.View()
        open_view.start = m(-60)
        with mock.patch.object(privacy, "view", return_value=open_view):
            conversations.run(self.cfg, m(12), [])
        self.assertNotIn("my secret is X", self.files())
        self.assertIn("new topic", self.files())


class OutageStillSafeTest(Base):
    """Outages the rule must not break (these held before it, too)."""

    def test_outages_with_and_without_a_prior_pass(self):
        for prior in (False, True):
            for h in (49, 300):
                with self.subTest(prior=prior, hours=h):
                    self.tearDown()
                    self.setUp()
                    if prior:
                        self.tick(m(-30))
                    self.exchange()
                    self.tick(m(12) + timedelta(hours=h))
                    self.assert_safe()

    def test_an_outage_during_a_turn(self):
        self.tick(m(-30))
        self.his("start of the thread here", m(0), "1")
        self.hers("ok, go on", m(1))
        conversations.note_off(self.cfg, "2", m(3))
        self.his(capture.off_record_note(self.cfg) + "my secret is X", m(3), "2")
        self.tick(m(6))
        self.hers("About your secret X: I won't tell.", m(7))
        self.his("thanks, new topic", m(9), "4")
        self.hers("Sure, what is it?", m(10))
        self.tick(m(12) + timedelta(hours=300))
        self.assertEqual(self.leaked(), [])

    def test_closed_for_three_days_then_open(self):
        self.tick(m(-30))
        self.exchange()
        with mock.patch.object(privacy, "compute", side_effect=RuntimeError("db locked")):
            for k in range(13):
                self.tick(m(12) + timedelta(hours=6 * k))
        self.assertNotIn("new topic", self.files())  # nothing written while closed
        self.tick(m(12) + timedelta(hours=73))
        self.assert_safe()

    def test_windows_open_across_a_gap(self):
        self.tick(m(-30))
        conversations.note_off(self.cfg, None, m(0), thread_start=True)  # a bare /x the gateway answered
        conversations.note_off(self.cfg, "11", m(1))
        self.his(capture.off_record_note(self.cfg, thread=True) + "window-private one", m(1), "11")
        self.hers("window-private reply one", m(2))
        self.tick(m(5))  # saved with the window open
        conversations.note_off(self.cfg, "12", m(20))
        self.his(capture.off_record_note(self.cfg, thread=True) + "window-private two", m(20), "12")
        self.hers("window-private reply two", m(21))
        self.his("public after the window", m(120), "13")
        self.hers("public reply", m(121))
        self.tick(m(130) + timedelta(hours=100))
        self.assert_safe("public after the window")

    def test_a_busy_path_window_across_a_gap(self):
        self.tick(m(-30))
        self.his("a question", m(0), "20")
        self.steer("/x", m(1), "21")
        self.steer("busy-private words", m(2), "22")
        self.hers("busy-private reply", m(3))
        self.tick(m(2.5))
        self.steer("steered secret later", m(30), "23")
        self.hers("busy-private second reply", m(31))
        self.his("public after the window", m(120), "24")
        self.tick(m(130) + timedelta(hours=300))
        self.assertEqual(self.leaked(), [])


class BackfillLookbackTest(Base):
    """Hole 2: the backfill recomputed from --since itself, so a window or turn begun before it was lost."""
    cursor_first = False

    def test_a_busy_path_window_across_since(self):
        x = datetime(2026, 10, 14, 18, 25, tzinfo=timezone.utc)  # 23:55 local, the day before --since
        self.steer("/x", x, "50")
        self.steer("busy-private before midnight", x + timedelta(minutes=2), "51")
        self.steer("steered secret after midnight", x + timedelta(minutes=10), "52")
        self.hers("busy-private reply after midnight", x + timedelta(minutes=11))
        conversations.backfill(self.cfg, date(2026, 10, 15), write=True, now=T0)
        self.assertEqual(self.leaked(), [])

    def test_a_tainted_turn_across_since(self):
        x = datetime(2026, 10, 14, 18, 28, tzinfo=timezone.utc)
        conversations.note_off(self.cfg, "40", x)
        self.his(capture.off_record_note(self.cfg) + "my secret is Z", x, "40")
        self.hers("About your secret Z: I won't tell.", x + timedelta(minutes=4))
        self.his("thanks, new topic", x + timedelta(minutes=10), "41")
        lines = conversations.backfill(self.cfg, date(2026, 10, 15), write=True, now=T0)
        self.assert_safe()
        self.assertIn("not written: 1 off", lines[-1])  # her reply is private, not merely unattached

    def test_a_mixed_turn_across_since_then_the_live_tick(self):
        x = datetime(2026, 10, 14, 18, 28, tzinfo=timezone.utc)
        self.his("a question before midnight", x - timedelta(minutes=1), "44")
        conversations.note_off(self.cfg, "45", x)
        self.his(capture.off_record_note(self.cfg) + "my secret is W", x, "45")
        self.steer("and a public part after midnight", x + timedelta(minutes=3), "46")
        self.hers("About your secret W: I won't tell.", x + timedelta(minutes=4))
        conversations.backfill(self.cfg, date(2026, 10, 15), write=True, now=T0)
        self.tick(T0 + timedelta(minutes=5))
        self.assertEqual(self.leaked(), [])


class LongHorizonReadersTest(Base):
    """Hole 3: readers that look back further than the view recomputed."""
    cursor_first = False

    def test_pack_and_verifier_before_the_first_pass(self):
        now = datetime.now(timezone.utc)
        b = now - timedelta(days=4)
        self.his("a question", b, "60")
        self.steer("/x", b + timedelta(minutes=1), "61")
        self.steer("steered secret four days ago", b + timedelta(minutes=2), "62")
        self.hers("busy-private reply", b + timedelta(minutes=3))
        privacy._CACHE.clear()
        privacy.update(self.cfg, now, [])  # the first pass ever (a fresh install)
        conversations.run(self.cfg, now, [])  # the writer's first run: its cursor is now
        privacy._CACHE.clear()
        msgs = context.telegram_messages(self.cfg)
        self.assertTrue(any("a question" in line for line in msgs))
        self.assertFalse(any("steered secret" in line for line in msgs))
        verify = context.make_verifier(self.cfg)
        self.assertFalse(verify("telegram:x", norm("steered secret four days ago")))
        self.assertTrue(verify("telegram:x", norm("a question")))

    def test_annotation_alignment_for_an_old_prompt(self):
        """``companion_reply_after`` aligns an annotation with a reply that can be days old."""
        self.exchange()
        now = datetime.now(timezone.utc)
        real = self.cfg.chat_id
        self.cfg.chat_id = "42"
        privacy._CACHE.clear()
        privacy.update(self.cfg, now, [])  # a pass saved over none of this chat's rows
        self.cfg.chat_id = real
        privacy._CACHE.clear()
        got = context.companion_reply_after(self.cfg, m(2))
        self.assertNotIn("won't tell", got or "")

    def test_the_outage_catch_up_reads_back_further_than_the_view(self):
        """``recovery.scan`` reads ``max_age`` plus a day back: 72 hours by default, past the 48 recomputed."""
        self.cfg.recovery = {"enabled": True, "max_age_hours": 48}
        self.exchange()
        real = self.cfg.chat_id
        at = m(12) + timedelta(hours=60)
        self.cfg.chat_id = "42"
        privacy._CACHE.clear()
        privacy.update(self.cfg, at, [])
        self.cfg.chat_id = real
        privacy._CACHE.clear()
        said = " ".join(t for _w, _a, t in recovery.scan(self.cfg, self.db.path, at)["before"])
        self.assertIn("new topic", said)
        self.assertNotIn("secret", said)

    def test_the_outage_catch_up_after_the_database_was_missing(self):
        self.tick(m(-30))
        self.exchange()
        away = self.db.path.with_suffix(".away")
        shutil.move(self.db.path, away)
        for k in range(0, 61, 6):
            self.tick(m(12) + timedelta(hours=k))
        shutil.move(away, self.db.path)
        at = m(12) + timedelta(hours=61)
        privacy._CACHE.clear()
        privacy.update(self.cfg, at, [])
        privacy._CACHE.clear()
        said = " ".join(t for _w, _a, t in recovery.scan(self.cfg, self.db.path, at)["before"])
        self.assertNotIn("secret", said)
        self.assertNotIn("won't tell", said)

    def test_a_sweep_looking_back_further_than_the_view(self):
        """``sweep.lookback_hours`` above 48 reads rows the default view did not recompute."""
        self.cfg.sweep["lookback_hours"] = 96
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-5), message_id="670")
        conversations.note_off(self.cfg, None, m(0), thread_start=True)  # a bare /x the gateway answered
        self.db.add(redirected("window-private thought while she typed", "822"), m(1))
        self.db.add(redirected("a public thought much later", "823"), m(120))
        at = m(130) + timedelta(hours=60)
        real = self.cfg.chat_id
        self.cfg.chat_id = "42"
        privacy._CACHE.clear()
        privacy.update(self.cfg, at, [])  # a pass saved over none of this chat's rows
        self.cfg.chat_id = real
        privacy._CACHE.clear()
        sweep.run(self.cfg, at, [])
        self.assert_safe("a public thought much later")

    def test_sweep_and_catch_up_after_an_outage(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-5), message_id="670")
        self.tick(m(-30))
        self.steer("/x", m(1), "320")
        self.steer("busy-private while she typed", m(2), "321")
        self.hers("busy-private answer", m(3))
        conversations.note_off(self.cfg, "322", m(10))
        self.his(capture.off_record_note(self.cfg) + "my secret is Y", m(10), "322")
        self.hers("About your secret Y: I won't tell.", m(11))
        self.his("unanswered public question", m(12), "323")
        for h in (30, 60, 100):
            at = m(15) + timedelta(hours=h)
            privacy._CACHE.clear()
            privacy.update(self.cfg, at, [])
            privacy._CACHE.clear()
            res = recovery.scan(self.cfg, self.db.path, at)
            said = " ".join(t for _, _, t in res["before"]) + " " + " ".join(x["text"] for x in res["his"])
            for s in ("busy-private", "secret", "won't tell"):
                self.assertNotIn(s, said)
        self.tick(m(15) + timedelta(hours=100))
        self.assertEqual(self.leaked(), [])


class CompactionCopiesTest(Base):
    """Hole 4: a compaction copy (same session, role, time and words, a new id) of a private row was public: the
    pass skips copies, so a copy's id never became private, and readers that read raw rows saw it."""

    def compacted(self):
        self.tick(m(-30))
        self.exchange()
        self.db.sql("update messages set active = 0")  # compaction archives the originals …
        self.his(capture.off_record_note(self.cfg) + "my secret is X", m(3), "02")  # … and stores the tail again
        self.hers("About your secret X: I won't tell.", m(5))
        self.his("thanks, new topic", m(7), "04")
        self.hers("Sure, what is it?", m(8))
        privacy._CACHE.clear()
        privacy.update(self.cfg, m(10), [])
        privacy._CACHE.clear()

    def test_a_copy_is_private_when_its_original_is(self):
        self.compacted()
        pv = privacy.view(self.cfg, m(10))
        self.assertEqual(sorted(pv.private), [3, 4, 7, 8])
        self.assertEqual(sorted(int(k) for k in conversations.snapshot(self.cfg)["privacy"]["private"]),
                         [3, 4, 7, 8])  # saved too

    def test_annotation_alignment_skips_the_copy(self):
        self.compacted()
        with mock.patch.object(privacy, "datetime", _At(m(10))):
            privacy._CACHE.clear()
            got = context.companion_reply_after(self.cfg, m(2))
        self.assertNotIn("won't tell", got or "")

    def test_the_catch_up_skips_the_copy(self):
        self.compacted()
        said = " ".join(t for _w, _a, t in recovery.scan(self.cfg, self.db.path, m(10))["before"])
        self.assertIn("new topic", said)
        self.assertNotIn("won't tell", said)

    def test_the_writer_writes_the_conversation_once_and_privately(self):
        self.compacted()
        self.tick(m(12))
        self.assert_safe()
        self.assertEqual(self.files().count("thanks, new topic"), 1)


class WiderCopiesTest(Base):
    """Review of PR "PRIV": Hermes stores a carried tail again in other shapes than same session and same time. The
    copy can sit in the child session of a rotation (``compression.in_place`` false), or carry the compaction's
    time (older Hermes, or its best-effort timestamp carry failing); on Iris's own database 17 of 26 copies had a
    new time. Each was read as a new row, so the writer wrote the steered /x words and her private reply."""

    def busy_window(self):
        self.tick(m(-30))
        self.his("a question", m(0), "20")
        self.steer("/x", m(1), "21")
        self.steer("busy-private words", m(2), "22")
        self.hers("busy-private reply", m(3))
        self.his("public after the window", m(120), "24")
        self.hers("public reply", m(121))
        self.tick(m(130))

    def test_a_rotation_child_session_copy(self):
        self.busy_window()
        self.db.sql("insert into sessions (id, source, chat_id, session_key, user_id, started_at, parent_session_id) "
                    "values ('tg2', 'telegram', ?, 'k', ?, 5.0, 'tg')", self.cfg.chat_id, self.cfg.chat_id)
        self.db.sql("update sessions set ended_at = 6.0, end_reason = 'compression' where id = 'tg'")
        self.steer_in("busy-private words", m(2), "22", "tg2")
        self.db.add("busy-private reply", m(3), role="assistant", session="tg2")
        self.his("public after the window", m(120), "24", session="tg2")
        self.hers_in("public reply", m(121), "tg2")
        log = self.tick(m(140))
        self.assertEqual(self.leaked(), [], log)
        self.assertEqual(self.files().count("public after the window"), 1)
        said = " ".join(t for _w, _a, t in recovery.scan(self.cfg, self.db.path, m(140))["before"])
        self.assertNotIn("busy-private", said)

    def steer_in(self, text, at, mid, session):
        self.db.add("[OUT-OF-BAND USER MESSAGE — a direct message]\n" + text + "\n[/OUT-OF-BAND USER MESSAGE]", at,
                    mid, display_kind="steer", session=session)

    def hers_in(self, text, at, session):
        self.db.add(text, at, role="assistant", session=session)

    def test_an_in_place_copy_stamped_at_compaction_time(self):
        self.tick(m(-30))
        self.his("a question", m(0), "20")
        self.steer("/x", m(1), "21")
        self.steer("busy-private words", m(2), "22")
        self.hers("busy-private reply", m(3))
        self.tick(m(100))
        self.db.sql("update messages set active = 0")
        self.hers("[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted", m(120))
        self.steer("busy-private words", m(120), "22")
        self.hers("busy-private reply", m(120))
        log = self.tick(m(140))
        self.assertEqual(self.leaked(), [], log)

    def test_a_tainted_reply_carried_with_a_new_time(self):
        self.tick(m(-30))
        self.exchange()
        self.tick(m(20))
        self.db.sql("update messages set active = 0")
        self.hers("[CONTEXT COMPACTION] summary of the earlier turns", m(30))
        self.hers("About your secret X: I won't tell.", m(30))
        self.hers("Sure, what is it?", m(30))
        self.tick(m(45))
        self.assertEqual(self.leaked(), [])
        said = " ".join(t for _w, _a, t in recovery.scan(self.cfg, self.db.path, m(45))["before"])
        self.assertNotIn("won't tell", said)

    def test_a_reply_said_again_while_the_first_is_live_is_not_a_copy(self):
        """No archive, no Telegram id, same session: two replies with the same words are two replies."""
        self.tick(m(-30))
        self.hers("Sure, what is it?", m(1))
        self.his("a thought", m(2), "30")
        self.hers("Sure, what is it?", m(3))
        rows = conversations.rows(self.cfg, self.db.path, 0)
        self.assertEqual([r[8] for r in rows if r[2] == "assistant"], [None, None])

    def test_a_message_resent_with_another_telegram_id_is_not_a_copy(self):
        self.tick(m(-30))
        self.his("ok", m(1), "30")
        self.db.sql("update messages set active = 0")
        self.his("ok", m(2), "31")
        rows = conversations.rows(self.cfg, self.db.path, 0)
        self.assertEqual([r[8] for r in rows], [None, None])


class OtherChatAlignmentTest(Base):
    """Review of PR "PRIV": ``companion_reply_after`` for a prompt recorded in another chat read rows the pass never
    computes (it computes the configured chat), so her reply in a tainted turn there read as public."""
    cursor_first = False

    def test_another_chat_reads_nothing(self):
        now = datetime.now(timezone.utc)
        b = now - timedelta(hours=3)
        conversations.note_off(self.cfg, "2", b)
        self.his(capture.off_record_note(self.cfg) + "my secret is X", b, "2", session="other")
        self.db.add("About your secret X: I won't tell.", b + timedelta(minutes=1), session="other",
                    role="assistant")
        privacy._CACHE.clear()
        privacy.update(self.cfg, now, [])
        privacy._CACHE.clear()
        self.assertIsNone(context.companion_reply_after(self.cfg, b - timedelta(minutes=1), chat_id="42"))

    def test_the_configured_chat_is_still_read(self):
        now = datetime.now(timezone.utc)
        b = now - timedelta(hours=3)
        self.his("a question", b, "2")
        self.hers("a public answer", b + timedelta(minutes=1))
        privacy._CACHE.clear()
        for chat in (None, str(self.cfg.chat_id)):
            self.assertEqual(context.companion_reply_after(self.cfg, b - timedelta(minutes=1), chat_id=chat),
                             "a public answer")


class SweepEdgeTest(Base):
    """Review of PR "PRIV": ``covering`` added no lookback when the reader's oldest row was at or after where the view
    began, but a window begun just before that was never computed if the passes that should have seen it ran on a
    clock that was ahead (the future stamp saved no window). The sweep's default 48 h lookback sits exactly there."""
    cursor_first = False

    def test_a_window_begun_just_before_the_sweeps_lookback(self):
        now = datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc)
        r0 = now - timedelta(hours=48)
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=r0 - timedelta(minutes=10),
                   message_id="670")
        conversations.note_off(self.cfg, None, r0 - timedelta(minutes=5), thread_start=True)  # a bare /x, answered
        self.db.add(redirected("window-private thought while she typed", "822"), r0 + timedelta(minutes=5))
        for h in range(0, 49, 6):  # a clock 100 h ahead: passes saved, none reading these rows
            self.tick(r0 + timedelta(hours=h + 100), steps=("privacy", "sweep"))
        self.tick(now, steps=("privacy", "sweep"))  # the clock is right again
        self.assertEqual(self.leaked(), [])

    def test_covering_takes_a_naive_time_as_local(self):
        """(Cross-review) a reader handing a naive datetime must not raise on comparing it with the view's start."""
        now = datetime.now(timezone.utc)
        v = privacy.covering(self.cfg, (now - timedelta(days=3)).astimezone().replace(tzinfo=None), now)
        self.assertLessEqual(v.start, now - timedelta(days=3) - privacy.RECOMPUTE + timedelta(minutes=1))

    def test_covering_goes_back_a_lookback_before_the_oldest_row(self):
        now = datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc)
        v = privacy.covering(self.cfg, now - timedelta(hours=48), now)
        self.assertLessEqual(v.start, now - timedelta(hours=48) - privacy.RECOMPUTE)


def _At(at: datetime):
    """A ``datetime`` whose ``now`` is ``at`` (for readers that ask the view without a ``now``)."""
    class Pinned(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)
    return Pinned


if __name__ == "__main__":
    unittest.main()
