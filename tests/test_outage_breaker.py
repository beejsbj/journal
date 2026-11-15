"""OUTAGES (#42, findings 1 and 2): an outage, once found, is known across ticks (the breaker): model work is held
and probed for sparingly, prompts still go out plain on time; and when a model answers again, the held work comes
back in order, a few a tick, and what has gone stale is not sent to the chat (it stays in its file, recorded)."""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, add_prompt, make_cfg, real_ask

from journal import agent, capture, context, entries, ledger, outage, reflect, telegram, tick
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
        self.assertEqual((len(self.companion.runs), len(self.companion.probes)), (1, 0))

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
        self.assertEqual(self.companion.probes, [(outage.PROBE_BUDGET, "none")] * 5)  # cheap: no tools
        self.assertEqual(len(self.companion.runs), 1)  # nothing else ran in those 95 minutes
        self.companion.down = False
        self.new_process()
        self.at(timedelta(minutes=125))
        self.assertEqual(self.ask(), DRAFT)  # the probe answered: the ask goes on with its own run
        self.assertEqual((len(self.companion.runs), len(self.companion.probes)), (2, 6))
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
        self.assertEqual((seen, len(self.companion.probes)), (["held"], 1))

    def test_an_answer_elsewhere_closes_it(self):
        with self.assertRaises(agent.AgentUnavailable):
            self.ask()
        self.companion.down = False
        outage.answered(self.cfg, T0 + timedelta(minutes=1))  # (another process's run answered)
        self.assertIsNone(outage.known(self.cfg))
        self.assertEqual(self.ask(), DRAFT)
        self.assertEqual(self.companion.probes, [])

    def test_a_long_run_after_an_answer_is_not_an_outage(self):
        self.companion.down = False
        self.assertEqual(self.ask(), DRAFT)
        with mock.patch.object(self.companion, "ask", side_effect=agent.AgentUnavailable("timed out after 540s")):
            with self.assertRaises(agent.AgentUnavailable):
                self.ask()
        self.assertIsNone(outage.known(self.cfg))

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
        self.assertEqual(len(self.companion.probes), 1)

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
        # outage, then probes at +5, +15 and +35 minutes
        self.assertEqual(len(self.companion.runs), 1)
        self.assertEqual(len(self.companion.probes), 3)
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

    def test_back_again_held_work_resumes_at_once(self):
        tick.annotate(self.cfg, self.at(timedelta(0)), [])
        for minutes in (5, 10):
            self.new_process()
            tick.annotate(self.cfg, self.at(timedelta(minutes=minutes)), [])
        self.companion.down = False
        self.new_process()
        log = []
        tick.annotate(self.cfg, self.at(timedelta(minutes=15)), log)  # a probe is due: it answers
        # the entry the first run deferred waits out its 30 minutes; the two only held ask at once
        self.assertEqual([l for l in log if l.startswith("annotated")],
                         ["annotated 2026-10-14-evening", "annotated 2026-10-14-midweek"])


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
        self.assertEqual(len(self.companion.probes), 1)
        self.assertEqual(self.replied(log), self.ids[1:3])  # (the first entry waits out its 30 minutes)
        self.assertEqual(self.replied(self.run_tick(10)), self.ids[3:5])
        self.assertEqual(self.replied(self.run_tick(35)), [])  # the hour is full
        self.assertEqual(self.replied(self.run_tick(65)), self.ids[:1])

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


class ConfigTest(unittest.TestCase):
    def test_the_defaults(self):
        from journal import config as jconfig
        self.assertEqual(jconfig.DEFAULTS["recovery"],
                         {"enabled": True, "max_age_hours": 48, "breaker": True, "probe_after_minutes": 5,
                          "probe_max_minutes": 30, "catchup_per_tick": 2, "sends_per_tick": 2, "sends_per_hour": 4,
                          "stale_hours": 24})
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            self.assertEqual([outage.probe_wait(cfg, n) for n in range(5)],
                             [timedelta(minutes=m) for m in (5, 10, 20, 30, 30)])
            cfg.recovery.update(catchup_per_tick="x", stale_hours=-1, probe_after_minutes=0)
            self.assertEqual((outage.per_tick(cfg), outage.stale_after(cfg), outage.probe_wait(cfg, 0)),
                             (2, timedelta(hours=24), timedelta(minutes=5)))


if __name__ == "__main__":
    unittest.main()
