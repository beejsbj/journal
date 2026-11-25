"""OUTAGES (#42, findings 1 and 2): an outage, once found, is known across ticks (the breaker): model work is held
and probed for sparingly, prompts still go out plain on time; and when a model answers again, the held work comes
back in order, a few a tick, and what has gone stale is not sent to the chat (it stays in its file, recorded)."""
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, add_prompt, make_cfg, real_ask

from journal import agent, capture, context, entries, ledger, outage, reflect, standing, telegram, tick
from journal.config import Config
from test_annotation_replies import FakeBot
from test_reflect import Base as ReflectBase

DRAFT = "- He slept badly but felt clear."
T0 = datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc)


class Companion:
    """A ``ports.Companion`` that is down or answers, and counts its runs (probes apart)."""

    def __init__(self):
        self.down, self.runs, self.probes, self.during_probe = True, [], [], None

    def ask(self, query, budget, toolsets=None, image=None, env=None):
        if query == outage.PROBE_QUERY:
            self.probes.append((budget, toolsets))
            if self.during_probe:
                self.during_probe()
        else:
            self.runs.append((query, budget, toolsets))
        if self.down:
            raise agent.AgentUnavailable("exit 1: Error code: 503 - {'type': 'error', 'error': "
                                         "{'type': 'overloaded_error', 'message': 'his words: the sea'}}")
        return "ok" if query == outage.PROBE_QUERY else DRAFT


class BreakerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.annotate_after_minutes = 0
        self.companion = Companion()
        self.clock = T0
        agent._answered = False
        self.patches = [mock.patch.object(agent, "ask", real_ask),
                        mock.patch.object(Config, "companion", lambda cfg: self.companion),
                        mock.patch.object(outage, "_now", lambda: self.clock),
                        mock.patch("journal.context.companion_reply_after", lambda *a, **k: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        agent._answered = False
        self.tmp.cleanup()

    def new_process(self):
        """A tick is a fresh process: nothing in memory carries over."""
        agent._answered = False

    def at(self, delta):
        self.clock = T0 + delta
        return self.clock

    def ask(self):
        return agent.ask(self.cfg, "q", 60, toolsets="none")


class BreakerTest(BreakerBase):
    def test_a_failed_run_opens_it_and_later_asks_start_nothing(self):
        with self.assertRaises(agent.AgentUnavailable) as cm:
            self.ask()
        self.assertNotIsInstance(cm.exception, outage.ModelHeld)  # the run that found it
        st = outage.known(self.cfg)
        self.assertEqual((st["down_since"], st["probes"]), (T0.isoformat(), 0))
        self.assertEqual(st["last_error"], "exit 1: 503 overloaded_error")  # the whitelist: never his words
        self.assertEqual(st["next_probe_at"], (T0 + timedelta(minutes=5)).isoformat())
        self.new_process()  # (another tick: the outage is still known)
        self.at(timedelta(minutes=4))
        with self.assertRaises(outage.ModelHeld) as cm:
            self.ask()
        self.assertEqual(str(cm.exception), "held: exit 1: 503 overloaded_error")
        self.assertEqual((len(self.companion.runs), len(self.companion.probes)), (1, 1))  # (the probe that confirmed it)

    def test_probes_back_off_and_one_that_answers_closes_it(self):
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()
        waits = []
        for minutes in (5, 15, 35, 65, 95):  # each probe is due once the last one's wait is over
            self.new_process()
            self.at(timedelta(minutes=minutes))
            with self.assertRaises(outage.ModelHeld):
                self.ask()
            st = outage.known(self.cfg)
            waits.append(datetime.fromisoformat(st["next_probe_at"]) - self.clock)
        self.assertEqual(waits, [timedelta(minutes=m) for m in (10, 20, 30, 30, 30)])
        self.assertEqual(self.companion.probes, [(outage.PROBE_BUDGET, "none")] * 6)  # cheap: no tools (the first
        # confirmed the outage at once)
        self.assertEqual(len(self.companion.runs), 1)  # nothing else ran in those 95 minutes
        self.companion.down = False
        self.new_process()
        self.at(timedelta(minutes=125))
        self.assertEqual(self.ask(), DRAFT)  # the probe answered: the ask goes on with its own run
        self.assertEqual((len(self.companion.runs), len(self.companion.probes)), (2, 7))
        self.assertIsNone(outage.known(self.cfg))
        last = ledger.read_state(self.cfg.state_dir / outage.STATE)["last"]
        self.assertEqual((last["down_since"], last["answered_at"], last["probes"]),
                         (T0.isoformat(), (T0 + timedelta(minutes=125)).isoformat(), 5))
        self.assertTrue(agent.answered_this_run())

    def test_a_probe_is_claimed_so_another_run_meanwhile_is_held(self):
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()
        self.at(timedelta(minutes=5))
        seen = []

        def another_process():
            try:
                agent.ask(self.cfg, "q2", 60)
            except outage.ModelHeld:
                seen.append("held")
        self.companion.during_probe = another_process
        with self.assertRaises(outage.ModelHeld):
            self.ask()
        self.assertEqual((seen, len(self.companion.probes)), (["held"], 2))  # (the confirming one, then this one)

    def test_an_answer_elsewhere_closes_it(self):
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()
        self.companion.down = False
        outage.answered(self.cfg, T0 + timedelta(minutes=1))  # (another process's run answered)
        self.assertIsNone(outage.known(self.cfg))
        self.assertEqual(self.ask(), DRAFT)
        self.assertEqual(len(self.companion.probes), 1)  # only the one that confirmed the outage

    def test_a_long_run_after_an_answer_is_not_an_outage(self):
        self.companion.down = False
        self.assertEqual(self.ask(), DRAFT)
        with mock.patch.object(self.companion, "ask", side_effect=agent.AgentUnavailable("timed out after 540s")):
            with self.assertRaises(agent.AgentUnavailable):
                self.ask()
        self.assertIsNone(outage.known(self.cfg))

    def test_a_timeout_with_no_answer_yet_is_confirmed_by_a_probe(self):
        # (OUTAGES re-review, medium 1) one request that always runs too long trips nothing while a probe answers
        self.companion.down = False
        real = self.companion.ask

        def slow(query, budget, toolsets=None, image=None, env=None):
            if query == "slow":
                raise agent.AgentUnavailable("timed out after 240s")
            return real(query, budget, toolsets, image, env)
        with mock.patch.object(self.companion, "ask", slow):
            with self.assertRaises(agent.AgentUnavailable) as cm:
                agent.ask(self.cfg, "slow", 180, toolsets="none")
        self.assertNotIsInstance(cm.exception, outage.ModelHeld)
        self.assertIsNone(outage.known(self.cfg))
        self.assertEqual(len(self.companion.probes), 1)
        self.new_process()
        self.assertEqual(self.ask(), DRAFT)  # nothing is held

    def test_trip_at_once_opens_it_with_no_probe(self):
        # (OUTAGES re-review, medium 2) the opener's failure goes out plain at once: no probe waited out first
        with self.assertRaises(agent.AgentUnavailable):
            agent.ask(self.cfg, "q", 60, trip_at_once=True)
        self.assertEqual(self.companion.probes, [])
        self.assertIsNotNone(outage.known(self.cfg))

    def test_a_confirming_answer_closes_a_breaker_another_process_opened(self):
        self.companion.down = False
        real = self.companion.ask

        def other_opens(query, budget, toolsets=None, image=None, env=None):
            if query == "bad":
                outage.trip(self.cfg, "exit 1")  # (another process found the outage meanwhile)
                raise agent.AgentUnavailable("exit 1: Error code: 503")
            return real(query, budget, toolsets, image, env)
        with mock.patch.object(self.companion, "ask", other_opens):
            with self.assertRaises(agent.AgentUnavailable):
                agent.ask(self.cfg, "bad", 60, toolsets="none")
        self.assertIsNone(outage.known(self.cfg))  # the probe answered: closed, not held till the next probe

    def test_a_time_with_no_zone_in_the_file_is_as_if_not_there(self):
        path = self.cfg.state_dir / outage.STATE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"down_since": T0.isoformat(), "next_probe_at": "2026-10-14T12:00:00"}))
        self.companion.down = False
        self.assertEqual(self.ask(), DRAFT)  # (a probe, not a TypeError)

    def test_switched_off_every_ask_runs(self):
        self.cfg.recovery["breaker"] = False
        for _ in range(3):
            with self.assertRaises(agent.AgentUnavailable) as cm:
                self.ask()
            self.assertNotIsInstance(cm.exception, outage.ModelHeld)
        self.assertEqual((len(self.companion.runs), self.companion.probes), (3, []))
        self.assertIsNone(outage.known(self.cfg))

    def test_the_probe_sends_nothing_and_reads_no_chat(self):
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()
        self.companion.down = False
        self.at(timedelta(minutes=5))
        nothing = mock.Mock(side_effect=AssertionError("the probe touched the chat"))
        with mock.patch.object(Config, "channel", nothing), mock.patch.object(Config, "transcript", nothing):
            self.assertEqual(self.ask(), DRAFT)
        self.assertEqual(len(self.companion.probes), 2)

    def test_a_held_ask_is_no_try_of_the_record(self):
        rec = {}
        outage.defer(rec, T0, "exit 1: 503")
        outage.defer(rec, T0 + timedelta(minutes=5), "held: exit 1: 503")
        self.assertEqual((rec["deferred_since"], rec["last_deferred_at"], rec["defer_error"]),
                         (T0.isoformat(), T0.isoformat(), "exit 1: 503"))
        fresh = {}
        outage.defer(fresh, T0, "held: exit 1: 503")
        self.assertFalse(outage.waiting(fresh, T0))  # free the moment a model answers
        self.assertTrue(outage.deferred(fresh))

    def test_status_and_the_tick_say_so(self):
        from journal import status
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()
        self.assertEqual(tick._outage_news(self.cfg, None),
                         ["model outage: no model answered (exit 1: 503 overloaded_error); model work is held until a "
                          "probe answers, and prompts go out with the plain opener"])
        self.assertIn("Model outage known since 14 Oct 11:30 IST (exit 1: 503 overloaded_error): model work is held; "
                      "0 probe(s) so far, the next at 14 Oct 11:35 IST", status.format_status(status.status(self.cfg, T0)))
        down = outage.known(self.cfg)
        self.cfg.recovery["breaker"] = False  # switched off while open: nothing is held, or said to be
        self.assertIsNone(outage.known(self.cfg))
        self.cfg.recovery["breaker"] = True
        self.companion.down = False
        self.at(timedelta(minutes=5))
        self.ask()
        self.assertEqual(tick._outage_news(self.cfg, down),
                         ["model outage over: a model answered again (down since 14 Oct 11:30 IST); held work catches "
                          "up, paced"])

    def test_failure_reason_of_a_held_ask(self):
        r = agent.failure_reason
        self.assertEqual(r("held: exit 1: 503 overloaded_error"), "held: exit 1: 503 overloaded_error")
        self.assertEqual(r("held: model unreachable"), "held: model unreachable")
        self.assertEqual(r("held: my dream about the sea"), "held")  # anything else in it is dropped
        self.assertEqual(r("held: exit 1: and then she said"), "held")


class RequestFailureTest(BreakerBase):
    """OUTAGES review, findings 1-3: a failure of one request is no outage, a probe can be answered where a real step
    would be, and a probe set far off by a clock jump is due."""

    BAD = ("exit 1: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
           "'message': 'prompt is too long: his words'}}")

    def failing(self, error, which="bad"):
        """The companion answers, except for the query ``which``, whose run fails with ``error``."""
        real = self.companion.ask

        def ask(query, budget, toolsets=None, image=None, env=None):
            if query == which:
                self.companion.runs.append((query, budget, toolsets))
                raise agent.AgentUnavailable(error)
            return real(query, budget, toolsets, image, env)
        return mock.patch.object(self.companion, "ask", ask)

    def test_a_bad_request_opens_nothing_and_is_a_bad_answer(self):
        self.companion.down = False
        with self.failing(self.BAD):
            self.assertIsNone(agent.ask(self.cfg, "bad", 60, toolsets="none"))  # (the caller counts an attempt)
            self.assertIsNone(outage.known(self.cfg))
            self.assertEqual(self.ask(), DRAFT)  # nothing is held
        self.assertEqual(len(self.companion.probes), 1)  # the probe that found a model answering

    def test_a_provider_error_on_one_run_while_a_probe_answers_defers_that_work_alone(self):
        self.companion.down = False
        with self.failing("exit 1: Error code: 503 - {'type': 'error', 'error': {'type': 'overloaded_error'}}"):
            with self.assertRaises(agent.AgentUnavailable) as cm:
                agent.ask(self.cfg, "bad", 60, toolsets="none")
            self.assertNotIsInstance(cm.exception, outage.ModelHeld)
            self.assertIsNone(outage.known(self.cfg))
            self.assertEqual(self.ask(), DRAFT)

    def test_request_failed(self):
        f = outage.request_failed
        self.assertTrue(f("exit 1: 400 invalid_request_error") and f("exit 1: 413") and f("invalid_request_error"))
        self.assertFalse(f("exit 1: 429 rate_limit_error") or f("exit 1: 408") or f("exit 1: 503 overloaded_error")
                         or f("exit 1") or f("timed out") or f("model unreachable"))

    def test_an_annotation_whose_request_fails_ends_at_its_attempts_and_holds_nothing(self):
        for n, kind in enumerate(("morning", "evening")):
            add_prompt(self.cfg, kind=kind, sent=T0 - timedelta(hours=3 - n), message_id=str(100 + n))
            capture.capture(self.cfg, CHAT, str(100 + n), f"words {n}", T0 - timedelta(hours=3 - n, minutes=-5),
                            "text", str(4242 + n))
        self.companion.down = False
        real = self.companion.ask

        def ask(query, budget, toolsets=None, image=None, env=None):  # the morning entry's request always fails
            if query != outage.PROBE_QUERY and "words 0" in query:
                self.companion.runs.append((query, budget, toolsets))
                raise agent.AgentUnavailable(self.BAD)
            return real(query, budget, toolsets, image, env)
        with mock.patch.object(self.companion, "ask", ask):
            for minutes in range(0, 30, 5):
                self.new_process()
                tick.annotate(self.cfg, self.at(timedelta(minutes=minutes)), [])
        data = ledger.snapshot(self.cfg.ledger_path)
        morning, evening = (ledger.find(data, f"2026-10-14-{k}") for k in ("morning", "evening"))
        self.assertEqual(morning["annotate_attempts"], tick.ANNOTATE_ATTEMPTS)  # ended, not retried for 7 days
        self.assertFalse(morning.get("annotated_at"))
        self.assertTrue(evening["annotated_at"])  # the other entry was never held
        self.assertIsNone(outage.known(self.cfg))

    def test_a_probe_gets_the_budget_of_the_smallest_real_step(self):
        # Hermes caps a provider's stale timeout to the budget left: a 45 s probe died where a picture's description
        # (120 s, the smallest real budget) would have been answered, so only a probe kept the breaker open
        self.assertGreaterEqual(outage.PROBE_BUDGET, 120)

    def test_a_probe_set_far_off_by_a_clock_jump_is_due(self):
        self.at(timedelta(days=7))  # the clock jumped a week ahead, and a run found no model
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()
        self.companion.down = False
        self.new_process()
        self.at(timedelta(minutes=1))  # set right again: the probe "due in a week" is due now
        self.assertEqual(self.ask(), DRAFT)
        self.assertIsNone(outage.known(self.cfg))

    def test_a_damaged_outage_file_stops_no_ask_and_no_tick(self):
        path = self.cfg.state_dir / outage.STATE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"down_since": "not a time", "probes": "x", "next_probe_at": 5}))
        with self.assertRaises(outage.ModelHeld):  # (not a ValueError: the opener still goes out plain)
            self.ask()
        self.companion.down = False
        self.at(timedelta(minutes=40))
        self.assertEqual(self.ask(), DRAFT)
        self.assertEqual(tick._outage_news(self.cfg, {"down_since": "not a time"}),
                         ["model outage over: a model answered again (down since ?); held work catches up, paced"])


class RefusedRequestTest(BreakerBase):
    """HERMES-TOOLSETS: a request the agent refuses before any model runs (Hermes' ``invalid_params``) says nothing of
    the model. It never opens or holds the breaker, runs no probe, falls back for its own record alone and has a line
    of its own in ``errors.jsonl``; a refused probe holds nothing either."""

    REFUSED = ("exit 1: Hermes couldn't start the model connection: invalid_params (toolsets: unknown or invalid "
               "toolset name: 'his words'). Your message was not sent. Run `hermes doctor` to check the setup.")

    def refusing(self, probe=False, runs=True):
        real = self.companion.ask

        def ask(query, budget, toolsets=None, image=None, env=None):
            is_probe = query == outage.PROBE_QUERY
            if (probe and is_probe) or (runs and not is_probe):
                (self.companion.probes.append((budget, toolsets)) if is_probe
                 else self.companion.runs.append((query, budget, toolsets)))
                raise agent.AgentUnavailable(self.REFUSED)
            return real(query, budget, toolsets, image, env)
        return mock.patch.object(self.companion, "ask", ask)

    def errors(self):
        path = self.cfg.state_dir / "errors.jsonl"
        return [json.loads(l) for l in path.read_text().splitlines()] if path.is_file() else []

    def test_the_reason_is_whitelisted(self):
        self.assertEqual(agent.failure_reason(self.REFUSED), "exit 1: invalid_params")
        self.assertEqual(agent.refused_field(self.REFUSED), "toolsets")
        self.assertTrue(outage.refused("exit 1: invalid_params") and outage.refused("held: exit 1: invalid_params"))
        self.assertFalse(outage.refused("exit 1") or outage.refused("exit 1: 400 invalid_request_error"))
        self.assertEqual(agent.failure_reason("exit 1: he wrote invalid_params in prose"), "exit 1")  # (only after
        # Hermes' own copy)

    def test_a_refused_run_holds_nothing_and_says_so(self):
        self.companion.down = False
        with self.refusing():
            for once in (False, True):  # (an opener's ``trip_at_once`` too)
                with self.assertRaises(outage.RequestRefused) as cm:
                    agent.ask(self.cfg, "q", 60, toolsets="none", trip_at_once=once)
                self.assertIsInstance(cm.exception, agent.AgentUnavailable)  # (callers fall back as before)
                self.assertNotIsInstance(cm.exception, outage.ModelHeld)
        self.assertIsNone(outage.known(self.cfg))
        self.assertEqual(self.companion.probes, [])  # no probe: the refusal is the request's, whatever the model
        self.assertEqual(self.ask(), DRAFT)  # nothing is held
        errors = self.errors()
        self.assertEqual([e["mode"] for e in errors], ["model", "model"])
        self.assertTrue(errors[0]["error"].startswith(
            "model request refused (exit 1: invalid_params, toolsets): a model step; "), errors[0])
        self.assertNotIn("his words", json.dumps(errors))  # (the detail Hermes echoes is never read)

    def test_a_refused_probe_releases_an_open_breaker(self):
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()  # a real outage opened it
        self.assertTrue(outage.known(self.cfg))
        self.companion.down = False
        self.new_process()
        self.at(timedelta(minutes=5))
        with self.refusing(probe=True, runs=False):
            self.assertEqual(self.ask(), DRAFT)  # the probe was refused: no evidence, so the ask runs on its own
        self.assertIsNone(outage.known(self.cfg))
        last = ledger.read_state(self.cfg.state_dir / outage.STATE)["last"]
        self.assertEqual(last["released_at"], (T0 + timedelta(minutes=5)).isoformat())
        self.assertNotIn("answered_at", last)
        self.assertIn("the outage probe", self.errors()[-1]["error"])
        # (L1, review of #62: the tick's line says it was released, not that a model answered)
        self.assertEqual(tick._outage_news(self.cfg, {"down_since": T0.isoformat()}),
                         ["model outage released (down since 14 Oct 11:30 IST): the probe's own request was refused, "
                          "so no model was heard from; held work is tried again record by record, and a run that "
                          "fails is found again"])

    def test_a_refused_probe_after_a_failed_run_opens_nothing(self):
        self.companion.down = False
        with self.refusing(probe=True, runs=False), \
                mock.patch.object(self.companion, "down", True):  # the run fails; the probe is refused
            with self.assertRaises(agent.AgentUnavailable) as cm:
                self.ask()
        self.assertNotIsInstance(cm.exception, (outage.ModelHeld, outage.RequestRefused))  # (this record defers)
        self.assertIsNone(outage.known(self.cfg))

    def test_an_annotation_whose_request_is_refused_waits_and_is_written_after_the_fix(self):
        add_prompt(self.cfg, kind="morning", sent=T0 - timedelta(hours=3), message_id="100")
        capture.capture(self.cfg, CHAT, "100", "words", T0 - timedelta(hours=2, minutes=55), "text", "4242")
        self.companion.down = False
        with self.refusing():
            tick.annotate(self.cfg, self.at(timedelta(0)), [])
        rec = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-morning")
        self.assertFalse(rec.get("annotate_attempts"))  # no attempt spent: the fix deploys, the work is still there
        self.assertTrue(rec.get("deferred_since"))
        self.assertIsNone(outage.known(self.cfg))
        self.new_process()
        tick.annotate(self.cfg, self.at(outage.RETRY_EVERY), [])  # the fix is in: its own clock, then written
        rec = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-morning")
        self.assertTrue(rec.get("annotated_at"))


class TickUnderOutageTest(BreakerBase):
    """Finding 1 on main: each tick started model runs of its own while every model was down."""

    def setUp(self):
        super().setUp()
        for n, kind in enumerate(("morning", "evening", "midweek")):
            add_prompt(self.cfg, kind=kind, sent=T0 - timedelta(hours=3 - n), message_id=str(100 + n))
            capture.capture(self.cfg, CHAT, str(100 + n), f"words {n}", T0 - timedelta(hours=3 - n, minutes=-5),
                            "text", str(4242 + n))

    def test_an_hour_of_outage_runs_the_model_once_and_probes_three_times(self):
        for minutes in range(0, 61, 5):  # a tick every five minutes
            self.new_process()
            tick.annotate(self.cfg, self.at(timedelta(minutes=minutes)), [])
        # main: a run on nearly every tick (each entry has its own 30-minute clock); now: the run that found the
        # outage and the probe that confirmed it, then probes at +5, +15 and +35 minutes
        self.assertEqual(len(self.companion.runs), 1)
        self.assertEqual(len(self.companion.probes), 4)
        data = ledger.snapshot(self.cfg.ledger_path)
        self.assertTrue(all(p.get("annotate_attempts", 0) == 0 for p in data["prompts"]))  # no attempt used

    def test_an_opener_goes_out_plain_at_once(self):
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()
        self.new_process()
        log = []
        with mock.patch.object(context, "build", lambda c, n, *a: "pack"):
            opener, source, _, _ = tick._opener(self.cfg, "evening", self.at(timedelta(minutes=2)),
                                                "2026-10-14-evening", log)
        self.assertEqual(source, "plain:agent-failed")
        self.assertTrue(opener.startswith("Evening, Theo"))
        self.assertEqual(len(self.companion.runs), 1)  # no run started, so no wait for a timeout
        self.assertEqual(log, ["2026-10-14-evening: opener: plain:agent-failed: held: exit 1: 503 overloaded_error"])

    def test_the_breaker_opens_mid_tick_and_holds_the_later_steps(self):
        later = []

        def reflect_step(cfg, now, log):  # a later step of the same tick asks too
            try:
                agent.ask(cfg, "a reflection", 300, toolsets="none")
            except outage.ModelHeld as e:
                later.append(str(e))
        quiet = ("send_due", "fold", "process_audio", "process_images", "reply_annotations", "prune")
        with mock.patch.multiple(tick, **{n: mock.DEFAULT for n in quiet}), \
                mock.patch.multiple("journal.tick.programs", send_due_interrupts=mock.DEFAULT, step=mock.DEFAULT), \
                mock.patch("journal.tick.privacy.update"), mock.patch("journal.tick.sweep.run"), \
                mock.patch("journal.tick.conversations.run"), mock.patch("journal.tick.recovery.run"), \
                mock.patch("journal.tick.mirror.flush"), mock.patch("journal.tick.today.refresh"), \
                mock.patch("journal.tick.notebook.sweep"), mock.patch.object(tick, "_reflect", reflect_step), \
                mock.patch("journal.tick.datetime") as dt:
            dt.now.return_value, dt.fromisoformat = self.at(timedelta(0)), datetime.fromisoformat
            code, log = tick.run(self.cfg)
            self.assertEqual(later, ["held: exit 1: 503 overloaded_error"])  # no run, and no probe: none is due
            self.assertEqual((len(self.companion.runs), len(self.companion.probes)), (1, 1))
            self.assertIn("model outage: no model answered (exit 1: 503 overloaded_error); model work is held until "
                          "a probe answers, and prompts go out with the plain opener", log)
            self.assertEqual(code, 0)  # an outage is no crash: the alert comes after 6 hours
            self.companion.down, later[:] = False, []
            self.new_process()
            dt.now.return_value = self.at(timedelta(minutes=5))  # the next tick: a probe is due, and answers
            code, log = tick.run(self.cfg)
        self.assertEqual(later, [])
        self.assertIn("model outage over: a model answered again (down since 14 Oct 11:30 IST); held work catches "
                      "up, paced", log)
        self.assertEqual(len(self.companion.probes), 2)

    def test_back_again_held_work_resumes_at_once(self):
        tick.annotate(self.cfg, self.at(timedelta(0)), [])
        for minutes in (5, 10):
            self.new_process()
            tick.annotate(self.cfg, self.at(timedelta(minutes=minutes)), [])
        self.companion.down = False
        self.new_process()
        log = []
        tick.annotate(self.cfg, self.at(timedelta(minutes=15)), log)  # a probe is due: it answers
        # oldest first: the entry whose own run found the outage does not wait out its 30 minutes (the outage it
        # found is over), and the two only held come after it (two a tick)
        self.assertEqual([l for l in log if l.startswith("annotated")],
                         ["annotated 2026-10-14-morning", "annotated 2026-10-14-evening"])


class PacedCatchUpTest(BreakerBase):
    """Finding 2 on main: once a model answered, every held annotation was written and sent in one tick."""

    def setUp(self):
        super().setUp()
        self.bot = FakeBot()
        self.patches += [mock.patch.object(telegram, "_call", self.bot),
                         mock.patch.object(telegram, "token", lambda cfg: "T")]
        for p in self.patches[-2:]:
            p.start()
        days = ("2026-10-10", "2026-10-11", "2026-10-12", "2026-10-13", "2026-10-14")
        for n, day in enumerate(days):  # five entries written while the model was down, an hour apart
            sent = T0 - timedelta(hours=6 - n)
            add_prompt(self.cfg, kind="morning", local_date=day, sent=sent, message_id=str(100 + n))
            capture.capture(self.cfg, CHAT, str(100 + n), f"words {n}", sent + timedelta(minutes=5), "text",
                            str(4242 + n))
        self.ids = [f"{d}-morning" for d in days]

    def run_tick(self, minutes):
        self.new_process()
        now, log = self.at(timedelta(minutes=minutes)), []
        tick.annotate(self.cfg, now, log)
        tick.reply_annotations(self.cfg, now, log)
        return log

    def replied(self, log):
        return [l.split()[2] for l in log if l.startswith("replied to")]

    def test_the_backlog_comes_back_in_order_a_few_at_a_time(self):
        self.companion.down = False
        self.cfg.recovery["breaker"] = False  # (pacing holds without it too)
        first = self.run_tick(0)
        self.assertEqual(self.replied(first), self.ids[:2])  # oldest first, two a tick
        self.assertIn("annotations: 2 asked for this tick; the rest wait for the next (paced catch-up)", first)
        self.assertEqual(self.replied(self.run_tick(5)), self.ids[2:4])
        third = self.run_tick(10)
        self.assertEqual(self.replied(third), [])  # four in the hour already
        self.assertIn("chat pacing: 1 annotation reply wait for a later tick (2 a tick, 4 an hour)", third)
        self.assertEqual(self.replied(self.run_tick(55)), [])
        self.assertEqual(self.replied(self.run_tick(60)), self.ids[4:])  # the first two left the hour
        self.assertEqual(len([c for c in self.bot.calls if c[0] == "sendMessage"]), 5)
        self.assertEqual(len(self.companion.runs), 5)

    def test_after_an_outage(self):
        self.run_tick(0)  # down: one run finds it
        self.companion.down = False
        log = self.run_tick(5)  # the probe answers
        self.assertEqual(len(self.companion.probes), 2)  # (the one that confirmed the outage, and this one)
        self.assertEqual(self.replied(log), self.ids[:2])  # oldest first: the first entry's outage is over too
        self.assertEqual(self.replied(self.run_tick(10)), self.ids[2:4])
        self.assertEqual(self.replied(self.run_tick(35)), [])  # the hour is full
        self.assertEqual(self.replied(self.run_tick(65)), self.ids[4:])

    def test_annotation_replies_and_analyses_share_the_chat_limit(self):
        self.companion.down = False
        self.cfg.recovery["breaker"] = False
        path = self.cfg.root / "2026-10-13-reflection.md"
        path.write_text("---\nkind: reflection\n---\n\n# x\n\n**What stood out** — rain.\n")
        with ledger.locked(self.cfg.ledger_path) as data:
            data.setdefault("reflections", []).append(
                {"id": "2026-10-13-reflection", "kind": "reflection", "local_date": "2026-10-13", "path": str(path),
                 "written_at": T0.isoformat(), "trigger": "annotated"})

        def tick_at(minutes):
            log = self.run_tick(minutes)
            reflect.send(self.cfg, self.clock, log)  # the reflect step, later in the same tick
            return log
        first = tick_at(0)
        self.assertEqual(len(self.replied(first)), 2)
        self.assertIn("chat pacing: 1 analysis wait for a later tick (2 a tick, 4 an hour)", first)  # the tick's two
        second = tick_at(5)
        self.assertEqual(len(self.replied(second)), 2)
        self.assertIn("chat pacing: 1 analysis wait for a later tick (2 a tick, 4 an hour)", second)  # the hour's four
        self.assertIn("sent 2026-10-13-reflection to Telegram", tick_at(60))  # the first two left the hour
        self.assertEqual(len([c for c in self.bot.calls if c[0] == "sendMessage"]), 6)  # five replies, one analysis

    def test_a_reply_gone_stale_is_not_sent_and_says_so(self):
        self.companion.down = False
        self.cfg.recovery.update(breaker=False, catchup_per_tick=5)
        log = self.run_tick(20 * 60 + 30)  # later that day: the three oldest entries' words are over 24 hours old
        self.assertEqual(self.replied(log), self.ids[3:5])
        for pid in self.ids[:3]:
            p = ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)
            self.assertEqual(p["annotation_reply"]["skipped"], "stale")
            self.assertTrue(p["annotated_at"])  # written into the entry all the same
            self.assertIn(DRAFT, entries.annotations(Path(p["entry_path"]).read_text()))
        self.assertIn(f"{self.ids[0]}: annotation reply not sent: stale (26 h after his words; the annotations are "
                      "in the entry)", log)
        self.assertFalse([l for l in log if "failed" in l])  # a drop is no error
        # STALE-COUNT: the companion is told how many, never which words (the owner, 2026-11-15)
        stand = standing.collect(self.cfg, self.clock)
        self.assertEqual(stand["stale"], {"annotations": 3, "reflections": 0})
        line = "- After an outage: 3 replies to his entries weren't sent, too late; they are in the entries."
        self.assertIn(line, standing.pack_lines(self.cfg, stand))
        self.assertIn(line, context.build(self.cfg, self.clock, stand=stand))
        self.assertNotIn("words 0", "\n".join(standing.pack_lines(self.cfg, stand)
                                               + standing.today_lines(self.cfg, stand)))


class PacedAnalysesTest(ReflectBase):
    def setUp(self):
        super().setUp()
        self.cfg.recovery["catchup_per_tick"] = 1

    def two_days(self):
        for day, mid in (("2026-10-14", "11"), ("2026-10-15", "12")):
            sent = self.prompt("evening", day, "23", mid, "Evening")
            self.answer_prompt("evening", day, "rain all day, walked anyway. the soup was good. But idk. the bus can "
                               "wait", sent + timedelta(minutes=10), mid)

    def test_written_a_few_a_tick_oldest_first(self):
        self.two_days()
        IST = self.cfg.tz
        _, log = self.reflect(datetime(2026, 10, 15, 23, 50, tzinfo=IST))
        wrote = [l for l in log if l.startswith("wrote")]
        self.assertEqual(len(wrote), 1)
        self.assertTrue(wrote[0].startswith("wrote 2026-10-14-reflection.md (annotated)"), wrote)
        self.assertIn("analyses: 1 asked for this tick; the rest wait for the next (paced catch-up)", log)
        _, log = self.reflect(datetime(2026, 10, 15, 23, 55, tzinfo=IST))
        self.assertTrue([l for l in log if l.startswith("wrote 2026-10-15-reflection.md")])

    def test_sends_are_paced_and_a_stale_one_is_not_sent(self):
        self.cfg.recovery["sends_per_tick"] = 1
        IST = self.cfg.tz
        with ledger.locked(self.cfg.ledger_path) as data:
            for day in ("2026-10-12", "2026-10-14", "2026-10-15"):
                path = self.cfg.root / f"{day}-reflection.md"
                path.write_text("---\nkind: reflection\n---\n\n# x\n\n**What stood out** — rain.\n")
                data.setdefault("reflections", []).append(
                    {"id": f"{day}-reflection", "kind": "reflection", "local_date": day, "path": str(path),
                     "written_at": "2026-10-15T18:00:00+00:00", "trigger": "annotated"})
        log = []
        reflect.send(self.cfg, datetime(2026, 10, 16, 0, 10, tzinfo=IST).astimezone(timezone.utc), log)
        self.assertIn("2026-10-12-reflection: not sent: stale (68 h past its time; in its file)", log)
        self.assertIn("sent 2026-10-14-reflection to Telegram", log)
        self.assertIn("chat pacing: 1 analysis wait for a later tick (1 a tick, 4 an hour)", log)
        self.assertEqual(len(self.bot.calls), 1)
        rec = reflect.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-12-reflection")
        self.assertEqual(rec["telegram"]["skipped"], "stale")
        stand = standing.collect(self.cfg, datetime(2026, 10, 16, 0, 15, tzinfo=IST))
        self.assertEqual(stand["stale"], {"annotations": 0, "reflections": 1})
        self.assertIn("- after an outage: 1 reflection wasn't sent, too late; it is in the entries",
                      standing.today_lines(self.cfg, stand))


class HeldAnalysesTest(ReflectBase):
    """OUTAGES review, finding 4: while the breaker holds, every due analysis is deferred (a held ask runs nothing),
    so one that leaves the always-due window (yesterday, this week) during a long outage is not dropped silently."""

    def setUp(self):
        super().setUp()
        self.cfg.recovery["catchup_per_tick"] = 1
        PacedAnalysesTest.two_days(self)

    def ask(self, cfg, query, budget, toolsets=None, env=None):
        self.asked.append((query, toolsets))
        raise outage.ModelHeld("held: exit 1: 503")

    def test_every_due_analysis_is_deferred(self):
        IST = self.cfg.tz
        _, log = self.reflect(datetime(2026, 10, 15, 23, 50, tzinfo=IST))
        self.assertEqual(len([l for l in log if "reflection deferred" in l]), 2)  # not the first alone
        self.assertFalse([l for l in log if "paced catch-up" in l])  # held asks are free
        for day in ("2026-10-14", "2026-10-15"):
            rec = self.rec(f"{day}-reflection")
            self.assertTrue(rec["deferred_since"])
            self.assertNotIn("last_deferred_at", rec)  # no try: free the moment a model answers
        later = datetime(2026, 10, 18, 12, 0, tzinfo=IST)  # three days on, the 15th is no longer "yesterday"
        due = reflect.due(self.cfg, ledger.snapshot(self.cfg.ledger_path), later)
        self.assertIn(("reflection", date(2026, 10, 15), "annotated"), due)


class ConfigTest(unittest.TestCase):
    def test_the_defaults(self):
        from journal import config as jconfig
        self.assertEqual(jconfig.DEFAULTS["recovery"],
                         {"enabled": True, "max_age_hours": 48, "breaker": True, "probe_after_minutes": 5,
                          "probe_max_minutes": 30, "catchup_per_tick": 2, "sends_per_tick": 2, "sends_per_hour": 4,
                          "stale_hours": 24, "letter": True, "letter_min_minutes": 60,
                          "letter_deadline_minutes": 120})  # (CATCH-UP-LETTER)
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            self.assertEqual([outage.probe_wait(cfg, n) for n in range(5)],
                             [timedelta(minutes=m) for m in (5, 10, 20, 30, 30)])
            cfg.recovery.update(catchup_per_tick="x", stale_hours=-1, probe_after_minutes=0)
            self.assertEqual((outage.per_tick(cfg), outage.stale_after(cfg), outage.probe_wait(cfg, 0)),
                             (2, timedelta(hours=24), timedelta(minutes=5)))


if __name__ == "__main__":
    unittest.main()
