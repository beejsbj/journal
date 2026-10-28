"""After a model outage left his chat messages answered only by Hermes' failure line, the journal sends one
catch-up reply once a model answers again: never while the outage lasts, never twice, never for old messages,
never when he has had a real answer since."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, CleanEnv, make_cfg
from test_mirror import Recorder, under_hermes
from test_outage import GENERIC_FAILED, UNREACHABLE
from test_reflect import FakeBot
from test_sweep import FakeSessions

from journal import agent, ledger, recovery, telegram, tick, validate

T0 = datetime(2026, 10, 15, 16, 0, tzinfo=timezone.utc)  # 21:30 IST
NOTICE = agent.FAILED_TURN_NOTICES[0]  # what Hermes stores for a failed turn (the ⚠️ copy only goes to Telegram)
PROVIDER_DOWN = json.dumps({"error": "Connection error: provider unreachable",
                            "error_surface": {"layer": "provider", "code": "connection", "retryable": True}})
REPLY = "I was away for a little while, sorry. A rough day and no sleep sounds heavy. Want to tell me about it?"


class Base(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = make_cfg(t)
        self.db = FakeSessions(t / "hermes-home" / "state.db")
        self.mirror = Recorder()
        under_hermes(self, self.cfg, self.mirror, self.db)
        self.bot = FakeBot()
        self.asked = []
        self.answers = [REPLY]
        for p in (mock.patch.object(telegram, "_call", self.bot), mock.patch.object(telegram, "token", lambda c: "T"),
                  mock.patch.object(agent, "ask", self.ask), mock.patch.object(agent, "_answered", False)):
            p.start()
            self.addCleanup(p.stop)

    def ask(self, cfg, query, budget, toolsets=None):
        self.asked.append((query, toolsets))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def failed(self, at, meta=PROVIDER_DOWN):
        """The row Hermes stores when a turn fails (agent/conversation_loop.py _close_durable_failed_turn)."""
        self.db.add(NOTICE, at, role="assistant", display_kind="failed_turn", meta=meta)

    def outage(self, start=T0, meta=PROVIDER_DOWN):
        """A real exchange, then two of his messages whose turns failed."""
        self.db.add("hi, back home now", start - timedelta(hours=2), mid="1")
        self.db.add("Welcome back — how was the drive?", start - timedelta(hours=2) + timedelta(seconds=20),
                    role="assistant")
        self.db.add("rough day, can't sleep", start, mid="2")
        self.failed(start + timedelta(seconds=30), meta)
        self.db.add("you there?", start + timedelta(minutes=20), mid="3")
        self.failed(start + timedelta(minutes=20, seconds=30), meta)

    def run_at(self, now):
        log = []
        recovery.run(self.cfg, now, log)
        return log

    def recs(self):
        return ledger.snapshot(self.cfg.ledger_path).get("recovery") or []

    def sends(self):
        return [p for m, p in self.bot.calls if m == "sendMessage"]


class CatchUpTest(Base):
    def test_waits_out_the_outage_then_sends_exactly_one(self):
        self.outage()
        self.answers = [agent.AgentUnavailable("exit 1: ⚠️ unreachable"), REPLY]
        log = self.run_at(T0 + timedelta(hours=1))
        self.assertIn("catch-up deferred, model still unreachable", log[0])
        self.assertEqual(self.sends(), [])  # nothing is sent while the outage lasts
        (rec,) = self.recs()
        self.assertEqual((rec["status"], rec["offline_since"]), ("waiting", T0.isoformat()))
        self.assertEqual([m["message_id"] for m in rec["messages"]], ["2", "3"])

        self.assertEqual(self.run_at(T0 + timedelta(hours=1, minutes=10)), [])  # backs off for 30 minutes
        self.assertEqual(len(self.asked), 1)

        log = self.run_at(T0 + timedelta(hours=1, minutes=40))
        self.assertEqual(log, ["sent the catch-up for outage-3 (2 unanswered message(s))"])
        (send,) = self.sends()
        self.assertEqual(send["reply_parameters"]["message_id"], 3)  # a reply to his last unanswered message
        self.assertEqual(send["text"], REPLY)
        query, toolsets = self.asked[-1]
        self.assertEqual(toolsets, "none")
        self.assertIn("You were offline from 15 Oct 21:30 to 15 Oct 23:10 IST", query)
        self.assertIn("- 15 Oct 21:30 IST: rough day, can't sleep\n- 15 Oct 21:50 IST: you there?", query)
        self.assertIn("- Iris (15 Oct 19:30): Welcome back — how was the drive?", query)
        self.assertNotIn("not processed", query)  # failed-turn rows are not conversation
        (call,) = self.mirror.calls
        self.assertEqual(call["role"], "user")
        self.assertEqual(call["text"], "[Journal: Iris was offline from 15 Oct 21:30 to 15 Oct 23:10 IST; this reply "
                                       "was sent after reconnecting, to his messages from that time]\n" + REPLY)
        (rec,) = self.recs()
        self.assertEqual((rec["status"], rec["message_id"], rec["mirrored"]), ("sent", "701", True))

        for later in (2, 5, 30):  # never again for this outage
            self.assertEqual(self.run_at(T0 + timedelta(hours=later)), [])
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(len(self.asked), 2)
        from journal.status import format_status, status
        self.assertIn("Outage catch-up: outage-3 sent 2026-10-15T17:40", format_status(status(self.cfg)))

    def test_a_model_that_answered_earlier_in_the_tick_skips_the_backoff(self):
        self.outage()
        self.answers = [agent.AgentUnavailable("exit 1"), REPLY]
        self.run_at(T0 + timedelta(hours=1))
        agent._answered = True  # e.g. an annotation just worked
        self.assertEqual(self.run_at(T0 + timedelta(hours=1, minutes=5)),
                         ["sent the catch-up for outage-3 (2 unanswered message(s))"])

    def test_answered_live_since_means_nothing_to_catch_up(self):
        self.outage()
        self.answers = [agent.AgentUnavailable("exit 1")]
        self.run_at(T0 + timedelta(hours=1))
        self.db.add("ok it works again?", T0 + timedelta(hours=1, minutes=20), mid="4")
        self.db.add("I'm here. Rough day, you said?", T0 + timedelta(hours=1, minutes=21), role="assistant")
        log = self.run_at(T0 + timedelta(hours=2))
        self.assertEqual(log, ["outage-3: no catch-up (answered live since)"])
        self.assertEqual(self.recs()[0]["status"], "skipped")
        self.assertEqual(self.run_at(T0 + timedelta(hours=3)), [])
        self.assertEqual(self.sends(), [])

    def test_a_newest_message_still_being_answered_waits(self):
        self.outage()
        self.db.add("hello??", T0 + timedelta(hours=1), mid="4")  # no reply row yet: a live turn may be running
        self.assertEqual(self.run_at(T0 + timedelta(hours=1, minutes=1)), [])
        self.assertEqual((self.asked, self.sends(), self.recs()), ([], [], []))

    def test_older_than_48_hours_only_notes_it(self):
        self.outage()
        log = self.run_at(T0 + timedelta(hours=49))
        self.assertEqual(self.asked, [])
        self.assertEqual(self.sends(), [])
        (call,) = self.mirror.calls
        head, body = call["text"].split("\n", 1)
        self.assertEqual(head, "[Journal: Iris was offline from 15 Oct 21:30 to 17 Oct 22:30 IST; Theo wrote "
                               "meanwhile and got only an error line back. No catch-up reply was sent (older than 48 h)]")
        self.assertEqual(body, "- 15 Oct 21:30 IST: rough day, can't sleep\n- 15 Oct 21:50 IST: you there?")
        self.assertEqual(self.recs()[0]["status"], "noted")
        self.assertIn("noted in the chat session", log[0])
        self.assertEqual(self.run_at(T0 + timedelta(hours=50)), [])

    def test_bad_answers_fall_back_to_a_note(self):
        self.outage()
        self.answers = ['You said "I never slept a wink" — rough.']  # not something he wrote
        for i in range(recovery.ATTEMPTS):
            self.run_at(T0 + timedelta(hours=1, minutes=i))
        self.assertEqual(self.sends(), [])
        self.assertEqual(self.recs()[0]["status"], "noted")
        self.assertIn("no usable reply after 3 attempts", self.mirror.calls[-1]["text"])

    def test_a_failed_send_retries_the_same_text(self):
        self.outage()
        self.bot.fail = 1
        log = self.run_at(T0 + timedelta(hours=1))
        self.assertIn("catch-up send attempt 1/6 failed", log[0])
        log = self.run_at(T0 + timedelta(hours=1, minutes=5))
        self.assertEqual(log, ["sent the catch-up for outage-3 (2 unanswered message(s))"])
        self.assertEqual(len(self.asked), 1)  # written once

    def test_a_second_outage_gets_its_own_catch_up(self):
        self.outage()
        self.run_at(T0 + timedelta(hours=1))
        later = T0 + timedelta(hours=5)
        self.db.add("again?", later, mid="5")
        self.failed(later + timedelta(seconds=30))
        self.run_at(later + timedelta(minutes=10))
        self.assertEqual([r["status"] for r in self.recs()], ["sent", "sent"])
        self.assertEqual([m["message_id"] for m in self.recs()[1]["messages"]], ["5"])
        self.assertEqual(self.sends()[1]["reply_parameters"]["message_id"], 5)

    def test_mirrors_cron_rows_and_commands_are_not_his_messages(self):
        self.outage()
        self.db.add("[Journal: Iris sent the evening prompt at 23:01 IST]\nEvening", T0 + timedelta(minutes=25))
        self.db.add("[Cron delivery: x]\nhello", T0 + timedelta(minutes=26))
        self.db.add("/status", T0 + timedelta(minutes=27), mid="9")
        self.run_at(T0 + timedelta(hours=1))
        self.assertEqual([m["message_id"] for m in self.recs()[0]["messages"]], ["2", "3"])

    def test_nothing_without_an_outage_off_or_standalone(self):
        self.db.add("hi", T0, mid="1")
        self.db.add("Hello!", T0 + timedelta(seconds=10), role="assistant")
        self.assertEqual(self.run_at(T0 + timedelta(hours=1)), [])
        self.outage(T0 + timedelta(hours=2))
        self.cfg.recovery["enabled"] = False
        self.assertEqual(self.run_at(T0 + timedelta(hours=3)), [])
        self.cfg.recovery["enabled"] = True
        self.cfg.hermes_home = None  # standalone: no sessions database
        self.assertEqual(self.run_at(T0 + timedelta(hours=3)), [])
        self.assertEqual(self.asked, [])

    def test_the_tick_runs_it(self):
        self.outage()
        steps = []
        with mock.patch.object(recovery, "run", lambda *a: steps.append("recover")):
            with mock.patch.multiple(tick, send_due=lambda *a, **k: None, fold=lambda *a, **k: None,
                                     process_audio=lambda *a, **k: None, annotate=lambda *a, **k: None,
                                     reply_annotations=lambda *a, **k: None, _reflect=lambda *a, **k: None), \
                    mock.patch("journal.sweep.run", lambda *a, **k: 0):
                tick.run(self.cfg)
        self.assertEqual(steps, ["recover"])

    def test_hermes_failure_copy_stored_by_older_versions_counts(self):
        self.db.add("rough day, can't sleep", T0, mid="2")
        self.db.add(GENERIC_FAILED, T0 + timedelta(seconds=30), role="assistant")
        self.db.add("you there?", T0 + timedelta(minutes=20), mid="3")
        self.db.add(UNREACHABLE, T0 + timedelta(minutes=20, seconds=30), role="assistant")
        self.assertEqual(self.run_at(T0 + timedelta(hours=1)),
                         ["sent the catch-up for outage-1 (2 unanswered message(s))"])

    def test_a_failed_turn_without_an_outage_sign_needs_the_model_to_have_been_down(self):
        self.outage(meta=None)  # an interrupt or a refusal closes a turn the same way
        log = self.run_at(T0 + timedelta(hours=1))  # the model answers at once: it was not an outage
        self.assertEqual(log, ["outage-3: no catch-up (no sign of an outage: the turn failed for another reason)"])
        self.assertEqual((self.sends(), self.recs()[0]["status"]), ([], "skipped"))

    def test_without_an_outage_sign_a_deferral_is_the_evidence(self):
        self.outage(meta=None)
        self.answers = [agent.AgentUnavailable("exit 1"), REPLY]
        self.run_at(T0 + timedelta(hours=1))
        self.assertTrue(self.recs()[0]["outage_seen"])
        self.assertEqual(self.run_at(T0 + timedelta(hours=2)),
                         ["sent the catch-up for outage-3 (2 unanswered message(s))"])

    def test_no_sign_and_a_model_already_working_skips_without_a_model_run(self):
        self.outage(meta=None)
        agent._answered = True
        self.run_at(T0 + timedelta(hours=1))
        self.assertEqual(self.asked, [])
        self.assertEqual(self.recs()[0]["status"], "skipped")

    def test_never_while_the_companion_is_mid_turn(self):
        self.outage()
        self.db.sql("insert into session_turn_leases values ('tg', 'pid=1:turn=x', 0, ?)",
                    datetime.now().timestamp() + 300)
        self.assertEqual(self.run_at(T0 + timedelta(hours=1)), [])
        self.assertEqual((self.asked, self.sends()), ([], []))
        self.db.sql("update session_turn_leases set expires_at = 1")
        self.assertEqual(self.run_at(T0 + timedelta(hours=1, minutes=5)),
                         ["sent the catch-up for outage-3 (2 unanswered message(s))"])

class CheckCatchupTest(unittest.TestCase):
    CORPUS = "rough day, can't sleep\nyou there?"

    def test_rules(self):
        ok = validate.check_catchup
        self.assertEqual(ok(REPLY, self.CORPUS), (True, "ok"))
        self.assertEqual(ok('Back now. "rough day, can\'t sleep" — I\'m sorry.', self.CORPUS), (True, "ok"))
        self.assertEqual(ok("", self.CORPUS), (False, "empty or too short"))
        self.assertEqual(ok(GENERIC_FAILED, self.CORPUS), (False, "agent error text"))
        self.assertEqual(ok("x" * 1600, self.CORPUS), (False, "too long (1600 > 1500)"))
        self.assertEqual(ok('{"text": "hi"}', self.CORPUS), (False, "not a chat message"))
        self.assertEqual(ok("Sorry, the model provider was down. Rough day?", self.CORPUS),
                         (False, "technical detail ('model')"))
        self.assertEqual(ok("I'm back. The error you saw is gone.", "the error thing again"), (True, "ok"))
        self.assertFalse(ok('Back. You said "slept like a log".', self.CORPUS)[0])


if __name__ == "__main__":
    unittest.main()
