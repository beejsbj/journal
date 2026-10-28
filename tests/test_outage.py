"""A model (or speech-to-text backend) that cannot be reached defers work instead of using up its attempts;
a reachable model's bad answer still counts. Hermes' own failure text is never an answer."""
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import add_prompt, make_cfg, real_ask

from journal import agent, capture, context, entries, ledger, outage, reflect, tick, validate
from test_reflect import Base as ReflectBase

IST = ZoneInfo("Asia/Kolkata")
GENERIC_FAILED = ("⚠️ Something went wrong and I couldn't finish this reply. Use /retry to try again, or /new "
                  "to start a fresh conversation. Technical details are in the gateway log (`hermes logs`).")
UNREACHABLE = ("⚠️ The AI model service isn't reachable right now — the configured model endpoint is not running "
               "or is unreachable. Wait a moment and use /retry; if it persists, run `hermes doctor` on the host.")
DRAFT = "- He slept badly but felt clear."


def done(rc=0, out="", err=""):
    return subprocess.CompletedProcess(["hermes"], rc, out, err)


class AskTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def ask(self, result=None, exc=None):
        with mock.patch("journal.agent.subprocess.run", side_effect=exc, return_value=result):
            return real_ask(self.cfg, "q", 60)

    def test_an_answer_is_returned(self):
        self.assertEqual(self.ask(done(out=DRAFT + "\n\nsession_id: 2026_abc\n")), DRAFT)

    def test_an_empty_answer_is_a_bad_answer_not_an_outage(self):
        self.assertIsNone(self.ask(done(out="\nsession_id: 2026_abc\n")))

    def test_a_failed_run_is_an_outage_and_keeps_why(self):
        # `hermes chat -Q` prints a failed turn's copy on stdout and exits 1 (cli_single_query._single_query_exit_code)
        with self.assertRaises(agent.AgentUnavailable) as cm:
            self.ask(done(1, UNREACHABLE, "session_id: x\n"))
        self.assertTrue(str(cm.exception).startswith("exit 1: ⚠️ The AI model service isn't reachable"))
        with self.assertRaises(agent.AgentUnavailable) as cm:
            self.ask(done(1, "", "Error: no credentials\nsession_id: x\n"))
        self.assertEqual(str(cm.exception), "exit 1: Error: no credentials")
        with self.assertRaises(agent.AgentUnavailable):
            self.ask(done(130))

    def test_timeout_and_a_missing_command_are_outages(self):
        with self.assertRaises(agent.AgentUnavailable) as cm:
            self.ask(exc=subprocess.TimeoutExpired("hermes", 240))
        self.assertIn("timed out after 240s", str(cm.exception))
        with self.assertRaises(agent.AgentUnavailable):
            self.ask(exc=FileNotFoundError("hermes"))

    def test_hermes_failure_text_is_never_an_answer_even_with_exit_zero(self):
        for text in (GENERIC_FAILED, UNREACHABLE, "Something odd.\n\nProvider said: 401 unauthorized"):
            with self.assertRaises(agent.AgentUnavailable):
                self.ask(done(0, text))

    def test_is_failure_text(self):
        for text in (GENERIC_FAILED, UNREACHABLE, "⏱️ The AI model service's usage limit is reached; it resets in 2h.",
                     "⚠️ Processing completed but no response was generated."):
            self.assertTrue(agent.is_failure_text(text), text)
        for text in ("", None, DRAFT, '{"opener": "Morning 🌻", "citations": []}',
                     "- He wondered whether to use /retry on the script " + "x" * 800):
            self.assertFalse(agent.is_failure_text(text), text)

    def test_off_is_not_an_outage(self):
        self.cfg.agent["enabled"] = False
        self.assertIsNone(real_ask(self.cfg, "q", 60))


class OpenerTest(unittest.TestCase):
    NOW = datetime(2026, 10, 14, 23, 0, tzinfo=IST)

    def opener(self, log=None, **ask):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            with mock.patch.object(context, "build", lambda c, n, *a: "pack"), mock.patch("journal.agent.ask", **ask):
                return tick._opener(cfg, "evening", self.NOW, "2026-10-14-evening", log)

    def test_an_outage_degrades_to_the_plain_opener(self):
        opener, source, cites, asked = self.opener(side_effect=agent.AgentUnavailable("exit 1"))
        self.assertEqual((source, cites, asked), ("plain:agent-failed", [], []))
        self.assertTrue(opener.startswith("Evening, Theo"))

    def test_the_log_says_why_the_agent_failed_and_nothing_he_wrote(self):
        stderr = ("exit 1: HTTP 429 Too Many Requests {'type': 'error', 'error': {'type': 'rate_limit_error', "
                  "'message': 'My dream about the sea'}}\nPrompt was: Evening, Theo " + "x" * 400)
        log: list[str] = []
        _, source, _, _ = self.opener(log, side_effect=agent.AgentUnavailable(stderr))
        self.assertEqual(source, "plain:agent-failed")  # the record is byte-equal to before
        self.assertEqual(len(log), 1, log)
        line = log[0]
        self.assertEqual(line.count("\n"), 0)
        self.assertLessEqual(len(line), 200)
        self.assertTrue(line.startswith("2026-10-14-evening: opener: plain:agent-failed: "), line)
        for kept in ("exit 1", "429", "rate_limit_error"):
            self.assertIn(kept, line)
        for never in ("dream", "mother", "Prompt", "xxx", "Theo"):
            self.assertNotIn(never, line)

    def test_a_failure_with_no_recognisable_class_logs_nothing_extra(self):
        log: list[str] = []
        self.opener(log, side_effect=agent.AgentUnavailable("something his words could be in"))
        self.assertEqual(log, [])
        self.opener(None, side_effect=agent.AgentUnavailable("exit 1"))  # no log given: still fine

    def test_prose_that_looks_like_a_status_or_class_gives_nothing_beyond_the_exit_label(self):
        # an agent that echoes what it was given: his words must not become a status or an error class
        r = agent.failure_reason
        self.assertEqual(r("exit 1: I owed 503 to the landlord and wrote family_error on the card"), "exit 1")
        self.assertEqual(r("exit 1: room 503, status of the family_error; Error codes 404 are rare"), "exit 1")
        self.assertEqual(r("exit 1: he typed the 'type' of family_error and 'type': 'x' 503"), "exit 1")

    def test_a_signal_death_is_named(self):
        self.assertEqual(agent.failure_reason("exit -9"), "killed by signal 9")
        self.assertEqual(agent.failure_reason("exit -15: terminated"), "killed by signal 15")
        log: list[str] = []
        self.opener(log, side_effect=agent.AgentUnavailable("exit -9"))
        self.assertEqual(log, ["2026-10-14-evening: opener: plain:agent-failed: killed by signal 9"])

    def test_the_reason_is_kept_in_errors_jsonl_and_shown_by_status(self):
        # the line says "failed", so the tick files it with the errors on purpose: "Recent errors" shows why
        from journal import status
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))

            def send(cfg_, now, log, only=None, dry_run=False):
                with mock.patch.object(context, "build", lambda c, n, *a: "pack"), \
                        mock.patch("journal.agent.ask", side_effect=agent.AgentUnavailable("exit 1: HTTP 429")):
                    tick._opener(cfg_, "evening", self.NOW, "2026-10-14-evening", log)
            with mock.patch.object(tick, "send_due", send):
                code, _ = tick.run(cfg)
            self.assertEqual(code, 0)
            rows = [json.loads(l) for l in (cfg.state_dir / "errors.jsonl").read_text().splitlines()]
            self.assertEqual([r["error"] for r in rows],
                             ["2026-10-14-evening: opener: plain:agent-failed: exit 1: 429"])
            self.assertIn("opener: plain:agent-failed: exit 1: 429",
                          status.format_status(status.status(cfg)))

    def test_failure_reason_whitelist(self):
        r = agent.failure_reason
        self.assertEqual(r("timed out after 300s"), "timed out")
        self.assertEqual(r("exit 1: Error code: 529 - {'type': 'error', 'error': {'type': 'overloaded_error', "
                           "'message': 'Overloaded'}}"), "exit 1: 529 overloaded_error")
        self.assertEqual(r('exit 1: {"type": "error", "error": {"type": "rate_limit_error"}}'),
                         "exit 1: rate_limit_error")
        self.assertEqual(r("exit 1: HTTP 502 Bad Gateway"), "exit 1: 502")
        self.assertEqual(r("exit 1: Error code: 401 - authentication_error"), "exit 1: 401 authentication_error")
        self.assertEqual(r("could not run the agent: [Errno 2] No such file: '/home/x/hermes'"),
                         "could not run the agent")
        self.assertEqual(r("agent reported a failure: ⚠ The AI model service isn't reachable right now"),
                         "agent reported a failure")
        self.assertEqual(r("exit 2"), "exit 2")
        self.assertEqual(r(""), "")

    def test_a_hermes_error_string_is_never_the_opener(self):
        _, source, _, _ = self.opener(return_value=GENERIC_FAILED)
        self.assertEqual(source, "plain:unparseable")
        _, source, _, _ = self.opener(return_value=json.dumps({"opener": GENERIC_FAILED, "citations": []}))
        self.assertEqual(source, "plain:agent error text")
        self.assertEqual(validate.check("evening", {"opener": UNREACHABLE, "citations": []}, "pack"),
                         (False, "agent error text"))


class OutageRecordTest(unittest.TestCase):
    def test_state_machine(self):
        t0 = datetime(2026, 10, 14, 22, 0, tzinfo=timezone.utc)
        rec = {}
        self.assertFalse(outage.deferred(rec))
        self.assertFalse(outage.defer(rec, t0, "exit 1"))
        self.assertTrue(outage.deferred(rec))
        self.assertTrue(outage.waiting(rec, t0 + timedelta(minutes=29)))
        self.assertFalse(outage.waiting(rec, t0 + timedelta(minutes=30)))
        self.assertFalse(outage.defer(rec, t0 + timedelta(hours=5, minutes=59), "exit 1"))
        self.assertTrue(outage.defer(rec, t0 + timedelta(hours=6), "exit 1"))  # once
        self.assertFalse(outage.defer(rec, t0 + timedelta(hours=7), "exit 1"))
        self.assertEqual(rec["deferred_since"], t0.isoformat())
        self.assertFalse(outage.expired(rec, t0 + timedelta(days=7) - timedelta(minutes=1)))
        self.assertTrue(outage.expired(rec, t0 + timedelta(days=7)))
        outage.clear(rec)
        self.assertEqual(rec, {})


class AnnotateOutageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.annotate_after_minutes = 0
        self.sent = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)
        add_prompt(self.cfg, sent=self.sent)
        self.info = capture.capture(self.cfg, self.cfg.chat_id, "100", "slept badly, clear head",
                                    self.sent + timedelta(minutes=5), "text", "4242")
        self.t0 = self.sent + timedelta(hours=1)
        self.calls, self.down, self.answer = 0, True, DRAFT
        self.patches = [mock.patch.object(agent, "ask", self.ask),
                        mock.patch("journal.context.companion_reply_after", lambda *a, **k: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def ask(self, *a, **k):
        self.calls += 1
        if self.down:
            raise agent.AgentUnavailable("exit 1: " + UNREACHABLE)
        return self.answer

    def step(self, delta):
        log = []
        tick.annotate(self.cfg, self.t0 + delta, log)
        return log

    def prompt(self):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-morning")

    def test_an_outage_defers_without_using_attempts_and_backs_off(self):
        log = self.step(timedelta(0))
        self.assertTrue(any("annotation deferred, model unreachable" in l for l in log))
        p = self.prompt()
        self.assertEqual((p.get("annotate_attempts", 0), p["deferred_since"]), (0, self.t0.isoformat()))
        self.assertIn("unreachable", p["defer_error"])
        self.assertEqual(self.step(timedelta(minutes=29)), [])
        self.assertEqual(self.calls, 1)  # not tried again within 30 minutes
        self.step(timedelta(minutes=31))
        self.assertEqual(self.calls, 2)
        p = self.prompt()
        self.assertEqual((p.get("annotate_attempts", 0), p["deferred_since"], p["last_deferred_at"]),
                         (0, self.t0.isoformat(), (self.t0 + timedelta(minutes=31)).isoformat()))
        for _ in range(tick.ANNOTATE_ATTEMPTS + 2):  # many more outages never exhaust the attempts
            self.step(timedelta(minutes=31 * (_ + 2)))
        self.assertEqual(self.prompt().get("annotate_attempts", 0), 0)
        self.assertEqual(entries.annotations(Path(self.info["path"]).read_text()), "")

    def test_status_shows_what_is_waiting(self):
        from journal import status
        self.step(timedelta(0))
        st = status.status(self.cfg, self.t0 + timedelta(minutes=5))
        self.assertEqual([(d["id"], d["what"]) for d in st["deferred"]], [("2026-10-14-morning", "annotation")])
        self.assertIn("Deferred by an outage (model or speech-to-text): 2026-10-14-morning annotation (since",
                      status.format_status(st))
        self.down = False
        self.step(timedelta(minutes=31))
        self.assertEqual(status.status(self.cfg, self.t0 + timedelta(hours=1))["deferred"], [])

    def test_one_alert_after_six_hours_not_every_tick(self):
        self.step(timedelta(0))
        self.step(timedelta(hours=5, minutes=59))  # tried, still under six hours
        with self.assertRaises(tick.Alert) as cm:
            self.step(timedelta(hours=6, minutes=40))
        self.assertIn("2026-10-14-morning is still not annotated", str(cm.exception))
        self.assertIn("unreachable since", str(cm.exception))
        self.step(timedelta(hours=7, minutes=20))  # tried again, but the alert was already raised
        self.step(timedelta(hours=9))

    def test_the_alert_makes_the_tick_exit_non_zero(self):
        self.step(timedelta(0))
        with mock.patch.object(tick, "send_due"), mock.patch.object(tick.sweep, "run"), \
                mock.patch.object(tick, "fold"), mock.patch.object(tick, "process_audio"), \
                mock.patch.object(tick, "_reflect"), mock.patch.object(tick.notebook, "sweep"), \
                mock.patch.object(tick, "reply_annotations"), mock.patch.object(tick, "prune"), \
                mock.patch.object(tick, "_record_run") as rec, \
                mock.patch("journal.tick.datetime") as dt:
            dt.now.return_value = self.t0 + timedelta(hours=6, minutes=40)
            dt.fromisoformat = datetime.fromisoformat
            code, log = tick.run(self.cfg)
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("ALERT: 2026-10-14-morning is still not annotated") for l in log))

    def test_recovery_annotates_and_clears_the_deferral(self):
        self.step(timedelta(0))
        self.down = False
        log = self.step(timedelta(minutes=31))
        self.assertIn("annotated 2026-10-14-morning", log[0])
        p = self.prompt()
        self.assertEqual(p["annotate_attempts"], 1)
        self.assertNotIn("deferred_since", p)
        self.assertNotIn("defer_error", p)
        self.assertIn("He slept badly", entries.annotations(Path(self.info["path"]).read_text()))

    def test_a_bad_answer_from_a_reachable_model_still_uses_an_attempt(self):
        self.step(timedelta(0))
        self.down, self.answer = False, ""
        log = self.step(timedelta(minutes=31))
        self.assertIn("2026-10-14-morning: annotation attempt failed", log)
        p = self.prompt()
        self.assertEqual(p["annotate_attempts"], 1)
        self.assertNotIn("deferred_since", p)
        self.answer = "{\"json\": true}"
        for n in range(tick.ANNOTATE_ATTEMPTS):
            self.step(timedelta(hours=1 + n))
        self.assertEqual(self.prompt()["annotate_attempts"], tick.ANNOTATE_ATTEMPTS)  # as before: then dropped

    def test_given_up_after_seven_days_with_one_alert(self):
        self.step(timedelta(0))
        with self.assertRaises(tick.Alert) as cm:
            self.step(timedelta(days=7, minutes=1))
        self.assertIn("was not annotated: the model was unreachable for 7 days", str(cm.exception))
        self.assertEqual(self.prompt()["annotate_attempts"], tick.ANNOTATE_ATTEMPTS)
        calls = self.calls
        self.step(timedelta(days=8))
        self.assertEqual(self.calls, calls)

    def test_a_tick_stops_at_the_first_outage(self):
        add_prompt(self.cfg, kind="evening", sent=self.sent)
        capture.capture(self.cfg, self.cfg.chat_id, "100", "second entry", self.sent + timedelta(minutes=6),
                        "text", "4243")
        self.step(timedelta(0))
        self.assertEqual(self.calls, 1)


class ReflectOutageTest(ReflectBase):
    def setUp(self):
        super().setUp()
        self.down = True

    def ask(self, cfg, query, budget, toolsets=None):
        self.asked.append((query, toolsets))
        if self.down:
            raise agent.AgentUnavailable("exit 1: " + UNREACHABLE)
        return self.answer

    def evening(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway. the soup was good. "
                           "But idk. the bus can wait", sent + timedelta(minutes=10), "11")

    def test_an_outage_defers_and_backs_off(self):
        self.evening()
        alerts, log = self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.assertEqual(alerts, [])
        self.assertTrue(any("reflection deferred, model unreachable" in l for l in log))
        rec = self.rec("2026-10-14-reflection")
        self.assertEqual(rec.get("attempts", 0), 0)
        self.assertEqual(rec["deferred_since"], datetime(2026, 10, 14, 18, 15, tzinfo=timezone.utc).isoformat())
        self.reflect(datetime(2026, 10, 14, 23, 55, tzinfo=IST))
        self.assertEqual(len(self.asked), 1)  # within 30 minutes: not again
        for n in range(1, 6):  # an outage never uses up the attempts
            self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST) + timedelta(minutes=35 * n))
        self.assertEqual(self.rec("2026-10-14-reflection").get("attempts", 0), 0)
        self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())
        self.assertEqual(self.bot.calls, [])

    def test_one_alert_after_six_hours(self):
        self.evening()
        self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        alerts, _ = self.reflect(datetime(2026, 10, 15, 5, 40, tzinfo=IST))  # tried, five hours fifty-five in
        self.assertEqual(alerts, [])
        alerts, _ = self.reflect(datetime(2026, 10, 15, 6, 20, tzinfo=IST))
        self.assertEqual(len(alerts), 1)
        self.assertIn("2026-10-14-reflection is still not written: the model has been unreachable since 14 Oct 23:45",
                      alerts[0])
        self.assertEqual(self.reflect(datetime(2026, 10, 15, 7, 0, tzinfo=IST))[0], [])

    def test_retried_for_days_then_written_on_recovery(self):
        self.evening()
        self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.down = False
        # three days on: not yesterday's or today's day any more, but still owed
        alerts, log = self.reflect(datetime(2026, 10, 17, 10, 0, tzinfo=IST))
        self.assertEqual(alerts, [])
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)
        rec = self.rec("2026-10-14-reflection")
        self.assertEqual(rec["attempts"], 1)
        self.assertNotIn("deferred_since", rec)
        self.assertEqual(len(self.bot.calls), 1)

    def test_a_bad_answer_still_uses_an_attempt(self):
        self.evening()
        self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.down, self.answer = False, 'You said "I never wrote this at all" and that is a long enough text. ' * 2
        alerts, log = self.reflect(datetime(2026, 10, 15, 0, 30, tzinfo=IST))
        self.assertTrue(any("reflection attempt 1/3 failed" in l for l in log))
        rec = self.rec("2026-10-14-reflection")
        self.assertEqual(rec["attempts"], 1)
        self.assertNotIn("deferred_since", rec)
        for n in range(2):
            alerts, log = self.reflect(datetime(2026, 10, 15, 1, 0, tzinfo=IST) + timedelta(hours=n))
        self.assertEqual(len(alerts), 1)
        self.assertIn("was not written after 3 attempts", alerts[0])

    def test_given_up_after_seven_days(self):
        self.evening()
        self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        alerts, _ = self.reflect(datetime(2026, 10, 21, 23, 50, tzinfo=IST))
        self.assertEqual(len(alerts), 1)
        self.assertIn("was not written: the model was unreachable for 7 days", alerts[0])
        _, log = self.reflect(datetime(2026, 10, 22, 6, 0, tzinfo=IST))
        self.assertFalse([l for l in log if "2026-10-14-reflection" in l])  # dropped: never asked for again
        self.assertEqual(self.rec("2026-10-14-reflection")["attempts"], reflect.ATTEMPTS)

    def test_the_run_stops_at_the_first_outage(self):
        self.evening()
        sent = self.prompt("evening", "2026-10-15", "23", "12", "Evening")
        self.answer_prompt("evening", "2026-10-15", "another rainy day, walked anyway", sent + timedelta(minutes=10), "12")
        self.reflect(datetime(2026, 10, 15, 23, 50, tzinfo=IST))  # yesterday's and today's are both due
        self.assertEqual(len(self.asked), 1)  # the second would only wait out another timeout


class LegacyCalloutTest(ReflectBase):
    def test_older_analyses_are_not_fed_back_with_their_prompts_callout(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections_since"] = "2026-10-12"
        sent = self.prompt("evening", "2026-10-14", "23", "11", "Evening")
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway. the soup was good; "
                           "the bus can wait", sent + timedelta(minutes=10), "11")
        old = self.cfg.root / "2026-10-14-reflection.md"
        old.write_text("---\ndate: 2026-10-14\nkind: reflection\ntype: journal\nsource: iris-journal\nauthor: iris\n---\n\n"
                       "# Iris's reflection — Wed 14 Oct 2026\n\n_From [[2026-10-14-evening]] · written 14 Oct 23:45 IST_\n\n"
                       "**What stood out** — rain.\n\n> [!note]- Today's prompts\n> **Evening journal** · 23:01 IST · answered\n"
                       "> Evening 🌻 the long tiring day opener.\n")
        wk = self.prompt("weekly", "2026-10-18", "20", "20", "End of the week.")
        self.answer_prompt("weekly", "2026-10-18", "a soft week", wk + timedelta(minutes=10), "20")
        self.reflect(datetime(2026, 10, 18, 20, 40, tzinfo=IST))
        query = self.asked[-1][0]
        self.assertIn("**What stood out** — rain.", query)
        self.assertNotIn("long tiring day opener", query)
        self.assertEqual(reflect.body_of(old.read_text()), "**What stood out** — rain.")


if __name__ == "__main__":
    unittest.main()
