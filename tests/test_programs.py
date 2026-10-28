"""Programs (WP4): fix-life over Telegram with no model, the run state machine, settle-then-advance, idempotent
transitions, reflect phases with their template fallback, session entries and summaries, the insights export, the
situational definitions, and the commands. Telegram is a list of calls; the model is unreachable unless a test says
otherwise; the clock is held at the moment being simulated."""
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import CHAT, CleanEnv, make_cfg

from journal import (agent, capture, cli, compose, config, context, entries, ledger, library, programdefs, programs,
                     sweep, telegram, tick, today)
from journal import commands as jcommands
from journal.config import Names
from journal.hermes import commands as hcommands
from journal.hermes import hooks

NAMES = Names(user="Sam", companion="Ada", emoji="🌿", pronouns="he/him/his", frontmatter_source="journal")
T0 = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)  # 15:30 IST, a Tuesday
ORIGINALS = Path(__file__).resolve().parent / "fixtures" / "fix-life-en"
CONFIRM = "2026-10-27 07:00"  # session 10 phase 3: the day and wake time its interrupts are timed from (WP5)
FALLBACK = "Ada would normally build this from your answers; write it yourself if you like."


class Bot:
    """Telegram as a list of calls; every send gets the next message id."""

    def __init__(self):
        self.calls, self.next_id = [], 700

    def __call__(self, cfg, method, payload):
        self.calls.append((method, payload))
        self.next_id += 1
        return {"message_id": self.next_id}

    def sends(self):
        return [p for m, p in self.calls if m == "sendMessage"]

    def phase_sends(self):
        return [p for p in self.sends() if (p.get("reply_markup") or {}).get("keyboard")]

    def summaries(self):
        return [p for p in self.sends() if (p.get("reply_markup") or {}).get("remove_keyboard")]


class Base(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name), names=NAMES)
        self.bot = Bot()
        self.asked = []
        self.model = None  # a function query -> text; None: unreachable
        self.mid = 5000
        self.log: list[str] = []
        test = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return test.now.astimezone(tz) if tz else test.now

        def ask(cfg, query, *a, **k):
            self.asked.append(query)
            if self.model is None:
                raise agent.AgentUnavailable("no model in this test")
            return self.model(query)
        for p in (mock.patch.object(telegram, "_call", self.bot), mock.patch.object(telegram, "token", lambda c: "T"),
                  mock.patch.object(agent, "ask", ask), mock.patch.object(tick, "datetime", Clock),
                  mock.patch.object(programs, "datetime", Clock), mock.patch.object(hooks, "datetime", Clock)):
            p.start()
            self.addCleanup(p.stop)
        self.now = T0

    # driving it
    def start(self, pid="fix-life", **kw):
        return programs.start(self.cfg, pid, self.now, self.log, **kw)

    def the_run(self, run_id=None):
        if run_id is None:  # the run in progress, else the latest (a completed run is no longer current)
            run_id = (programs.current_run(self.cfg) or programs.all_runs(self.cfg)[-1])["id"]
        return programs.find_run(self.cfg, run_id)

    def answer(self, text, after=timedelta(minutes=1), reply_to=None, source="text", mid=None):
        self.now += after
        self.mid += 1
        return capture.capture(self.cfg, CHAT, reply_to, text, self.now, source, mid or str(self.mid))

    def settle(self, after=timedelta(minutes=3)):
        self.now += after
        return programs.settle(self.cfg, self.the_run()["id"], self.now, self.log)

    def prompt(self, pid):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)

    def where(self):
        r = self.the_run()
        return r["current"]["session"], r["current"]["phase"]

    def entry(self, s=None):
        r = self.the_run()
        s = s or r["current"]["session"]
        return programs.entry_path(self.cfg, r, s)

    def prog(self, pid="fix-life"):
        return library.load(self.cfg).program(pid)


class FullRunTest(Base):
    """The ten sessions, driven through capture with Telegram stubbed and the model unreachable throughout."""

    def test_ten_sessions_with_no_model(self):
        prog = self.prog()
        self.start()
        run_id = self.the_run()["id"]
        for s in range(1, 11):
            if s > 1:
                self.now += timedelta(hours=2)
                self.start()  # the next session: /start fix-life
            for p in range(1, len(prog.session(s).phases) + 1):
                self.assertEqual(self.where(), (s, p))
                info = self.answer(CONFIRM if (s, p) == (10, 3) else f"S{s}P{p}: words of mine for this phase")
                self.assertEqual((info["prompt"]["session"], info["prompt"]["phase"]), (s, p))
                self.assertTrue(self.settle())
        run = self.the_run(run_id)
        self.assertEqual(run["status"], "completed")

        # every phase was sent as authored, once, with the reply keyboard
        phases = self.bot.phase_sends()
        self.assertEqual(len(phases), 51)
        i = 0
        for session in prog.sessions:
            for ph in session.phases:
                html = phases[i]["text"]
                i += 1
                self.assertIn(f"Session {session.number}/10 · Phase {ph.number}/{len(session.phases)}", html)
                for q in ph.questions:
                    self.assertIn(f"<b>{q.id}</b> {compose.md_to_html(q.text)}", html)
                if ph.type == "reflect":
                    self.assertIn(FALLBACK, html)  # the template, at once
                self.assertTrue(html.endswith(f"<i>{compose.PHASE_HINT}</i>"))
        self.assertIn("Ten structured sessions in four movements", phases[0]["text"])  # the intro, new run only
        self.assertNotIn("Ten structured sessions", phases[6]["text"])
        self.assertEqual(len(self.bot.summaries()), 10)

        # session entries: named, front-mattered, his words verbatim under phase keys, summaries filled from them
        for s in range(1, 11):
            path = self.cfg.root / f"2026-10-20-fix-life-s{s:02d}.md" if s < 5 else self.entry(s)
            body = path.read_text(encoding="utf-8")
            fm = entries.front_matter(body)
            self.assertEqual((fm["kind"], fm["practice"], fm["program"], fm["session"], fm["run"], fm["type"]),
                             ("fix-life", "fix-life", "fix-life", str(s), run_id, "journal"))
            self.assertEqual(entries.entry_key(path), ("fix-life", s, 1))
            self.assertIn(f"%% journal phase:1 %%\n### ", body)
            self.assertNotIn("%% journal phase", entries.his_words(body))
            summary = entries.summary_text(body)
            self.assertTrue(summary.startswith(f"✅ **Session {s} complete"))
            self.assertRegex(summary, rf"S{s}P[0-9]: words of mine for this phase")
            self.assertNotIn("{", summary)
        s1 = (self.cfg.root / "2026-10-20-fix-life-s01.md").read_text(encoding="utf-8")
        self.assertIn("- Current dissatisfaction: S1P1: words of mine for this phase", s1)
        self.assertIn("- Repeated complaints: _(in the answer above)_", s1)  # 1.2: the same phase, said once
        self.assertIn("**Anti-vision statement:** S1P6: words of mine for this phase", s1)
        self.assertIn("_reflect: skipped (no model)_", s1)
        self.assertLess(s1.index("## Summary"), s1.index("## Ada's annotations"))
        s10 = self.entry(10).read_text(encoding="utf-8")
        self.assertIn("### Generated Document", s10)
        self.assertIn("people to see me as S1P5: words of mine for this phase?", s10)  # a slot from session 1

        # the insights export: every session's summary and every answer, verbatim
        export = self.cfg.root / run["insights_entry"]
        export = export.with_suffix(".md")
        text = export.read_text(encoding="utf-8")
        self.assertEqual(entries.front_matter(text)["kind"], "insights")
        for s in range(1, 11):
            for p in range(1, len(prog.session(s).phases) + 1):
                self.assertIn(CONFIRM if (s, p) == (10, 3) else f"S{s}P{p}: words of mine for this phase", text)
        self.assertIsNone(entries.entry_key(export))  # not an entry anything appends to

        # nothing waited on a model: the only questions put to it were the four reflect constructions
        self.assertEqual(len(self.asked), 4)
        size = self.cfg.ledger_path.stat().st_size
        print(f"\nledger after the scripted ten-session run: {size} bytes, "
              f"{len(ledger.snapshot(self.cfg.ledger_path)['prompts'])} prompts")
        self.assertLess(size, 400_000)


class StateMachineTest(Base):
    def test_start_sends_phase_one_and_a_second_start_resends_it(self):
        self.start()
        first = self.prompt(f"{self.the_run()['id']}-s01-p1")
        self.assertTrue(first["phase_open"])
        self.assertEqual(first["collapse_hours"], 3)
        self.assertEqual(self.bot.phase_sends()[0]["reply_markup"]["keyboard"][0][0]["text"], "/next")
        self.now += timedelta(minutes=10)
        self.start()
        again = self.prompt(first["id"])
        self.assertEqual(again["previous_message_ids"], [first["message_id"]])  # the old message still takes replies
        self.assertEqual(len(programs.active_runs(self.cfg)), 1)
        self.assertIn("sent again below", self.bot.calls[-1][1]["text"] if self.bot.calls[-1][0] == "editMessageText"
                      else [p["text"] for m, p in self.bot.calls if m == "editMessageText"][-1])

    def test_skip_marks_the_phase_and_its_summary_slot(self):
        self.start("prep")
        programs.skip(self.cfg, None, self.now, self.log)
        self.assertEqual(self.where(), (1, 2))
        self.assertTrue(self.the_run()["sessions"]["1"]["phases"]["1"]["skipped"])
        self.answer("That I'll freeze. My line: I need this to change.")
        programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next")
        body = self.entry(1).read_text(encoding="utf-8")
        self.assertIn("- The conversation, and what I want from it: _(skipped)_", body)
        self.assertIn("- What I'm bracing for, and my line: That I'll freeze.", body)

    def test_back_resends_the_previous_phase_and_keeps_earlier_answers(self):
        self.start()
        self.answer("first answer to phase one")
        self.assertTrue(self.settle())
        self.assertEqual(self.where(), (1, 2))
        programs.back(self.cfg, None, self.now, self.log)
        self.assertEqual(self.where(), (1, 1))
        rid = self.the_run()["id"]
        self.assertTrue(self.prompt(f"{rid}-s01-p1")["phase_open"])
        self.assertFalse(self.prompt(f"{rid}-s01-p2")["phase_open"])
        self.answer("and something I forgot")
        self.assertTrue(self.settle())
        self.assertEqual(self.where(), (1, 2))
        answers = programs.answers(self.cfg, self.the_run(), 1)
        self.assertEqual(answers[1], "first answer to phase one\n\nand something I forgot")
        with self.assertRaises(programs.ProgramError):
            programs.back(self.cfg, None, self.now, self.log)  # (1, 1) after one back
            programs.back(self.cfg, None, self.now, self.log)

    def test_pause_holds_the_settle_and_start_picks_up_an_answer_given_on_the_break(self):
        self.start()
        mid = self.prompt(f"{self.the_run()['id']}-s01-p1")["message_id"]
        programs.pause(self.cfg, None, self.now, self.log)
        self.answer("answered while on a break", reply_to=mid)  # a direct reply: the paused phase takes only those
        self.assertFalse(self.settle(timedelta(minutes=10)))
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.where(), (1, 1))
        self.start()  # /start fix-life
        self.assertEqual(self.the_run()["status"], "active")
        self.assertEqual(self.where(), (1, 2))  # it carried on from the answer, not a re-send of phase 1
        self.assertEqual(len(self.bot.phase_sends()), 2)

    def test_abandon_then_start_begins_a_new_run(self):
        first = self.start()["id"]
        programs.abandon(self.cfg, None, self.now, self.log)
        self.assertEqual(programs.find_run(self.cfg, first)["status"], "abandoned")
        self.assertFalse(self.prompt(f"{first}-s01-p1")["phase_open"])
        self.now += timedelta(minutes=1)
        second = self.start()["id"]
        self.assertNotEqual(first, second)

    def test_jump_to_a_session(self):
        self.start(session=4)
        self.assertEqual(self.where(), (4, 1))
        self.answer("an outcome")
        self.assertEqual(self.entry().name, "2026-10-20-fix-life-s04.md")
        self.now += timedelta(hours=1)
        self.start(session=2)
        self.assertEqual(self.where(), (2, 1))
        self.assertEqual(self.the_run()["sessions"]["4"]["entry"], "2026-10-20-fix-life-s04")  # kept for a return

    def test_the_keyboard_can_be_switched_off(self):
        self.cfg.programs["keyboard"] = False
        self.start()
        self.assertEqual(self.bot.phase_sends(), [])
        self.assertIn("reply here · /next", self.bot.sends()[-1]["text"])  # the commands are still named

    def test_nothing_running_is_said(self):
        with self.assertRaises(programs.ProgramError):
            programs.skip(self.cfg, None, self.now, self.log)
        with self.assertRaises(programs.ProgramError):
            self.start("nope")


class SettleTest(Base):
    def test_voice_note_then_correction_within_the_settle_is_one_advance_with_both_blocks(self):
        self.start()
        rid = self.the_run()["id"]
        self.now += timedelta(minutes=1)
        t = self.now
        self.mid += 1
        voice = capture.capture_to(self.cfg, f"{rid}-s01-p1", "next-message", "the transcript of my voice note", t,
                                   "voice", str(self.mid))
        self.assertIsNotNone(voice)
        fix = self.answer("correction: I meant forty-one", after=timedelta(seconds=40))
        self.assertEqual(fix["prompt"]["phase"], 1)  # the open phase took the second message too
        self.now = t + timedelta(minutes=2)  # the first spawn's check: the newest capture is 80 s old
        self.assertFalse(programs.settle(self.cfg, rid, self.now, self.log))
        self.now = t + timedelta(minutes=2, seconds=40)  # the second spawn's check
        self.assertTrue(programs.settle(self.cfg, rid, self.now, self.log))
        self.assertFalse(programs.settle(self.cfg, rid, self.now + timedelta(minutes=1), self.log))
        self.assertEqual(self.where(), (1, 2))
        self.assertEqual(len(self.bot.phase_sends()), 2)
        body = self.entry(1).read_text(encoding="utf-8")
        self.assertEqual(body.count("%% journal phase:1 %%"), 2)
        self.assertEqual(programs.answers(self.cfg, self.the_run(), 1)[1],
                         "the transcript of my voice note\n\ncorrection: I meant forty-one")

    def test_a_duplicated_capture_advances_once(self):
        self.start()
        self.answer("my answer", mid="9001")
        dup = self.answer("my answer", mid="9001", after=timedelta(seconds=5))  # the sweep, or a redelivery
        self.assertTrue(dup["duplicate"])
        self.assertTrue(self.settle())
        self.assertFalse(programs.settle(self.cfg, self.the_run()["id"], self.now, self.log))
        programs.step(self.cfg, self.now + timedelta(minutes=5), self.log)
        self.assertEqual(self.where(), (1, 2))
        self.assertEqual(len(self.bot.phase_sends()), 2)

    def test_a_lost_spawn_is_caught_by_the_tick(self):
        self.start()
        self.answer("my answer")
        self.now += timedelta(minutes=5)
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.where(), (1, 2))

    def test_the_tick_runs_the_programs_step(self):
        with mock.patch.object(programs, "step") as step, mock.patch.object(tick, "send_due"):
            tick.run(self.cfg)
        step.assert_called_once()

    def test_restart_mid_transition_sends_once_on_the_next_step(self):
        self.start()
        self.answer("my answer")
        with mock.patch.object(programs, "_send_phase", return_value=False):  # the process died before sending
            self.assertTrue(self.settle())
        self.assertTrue(self.the_run()["sessions"]["1"]["phases"]["2"]["send_due"])
        self.assertEqual(len(self.bot.phase_sends()), 1)
        programs.step(self.cfg, self.now, self.log)
        programs.step(self.cfg, self.now + timedelta(minutes=5), self.log)
        self.assertEqual(len(self.bot.phase_sends()), 2)
        self.assertEqual(self.where(), (1, 2))

    def test_an_owed_send_that_keeps_failing_alerts_once_and_waits_for_him(self):
        self.start()
        self.answer("my answer")
        with mock.patch.object(telegram, "_call", side_effect=telegram.TelegramError("chat not found")):
            self.assertTrue(self.settle())  # attempt 1
            for _ in range(2):
                self.now += timedelta(minutes=5)
                try:
                    programs.step(self.cfg, self.now, self.log)
                except tick.SendFailed:
                    pass  # the third attempt: one alert
            self.now += timedelta(minutes=5)
            programs.step(self.cfg, self.now, self.log)  # given up: no fourth attempt, no second alert
        rid = self.the_run()["id"]
        self.assertEqual(self.prompt(f"{rid}-s01-p2")["send_attempts"], 3)
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")  # his /next tries again
        self.assertEqual(len(self.bot.phase_sends()), 2)
        self.assertNotIn("send_attempts", self.prompt(f"{rid}-s01-p2"))

    def test_next_advances_at_once_and_a_second_next_is_its_own(self):
        self.start()
        self.answer("done already")
        rid = self.the_run()["id"]
        self.assertTrue(programs.advance(self.cfg, rid, self.now, self.log, reason="next"))
        self.assertEqual(self.where(), (1, 2))
        self.assertFalse(programs.settle(self.cfg, rid, self.now + timedelta(minutes=5), self.log))  # its key is used

    def test_off_the_record_answer_leaves_the_phase_open(self):
        self.start()
        event = SimpleNamespace(source=SimpleNamespace(platform="telegram", chat_id=CHAT), text="/x my private answer",
                                message_id="77", reply_to_message_id=None, message_type="TEXT", media_urls=[])
        with mock.patch.object(hooks.runtime, "load_config", lambda: self.cfg):
            out = hooks.on_dispatch(event)
        self.assertIn("Off the record", out["text"])
        self.assertEqual(self.prompt(f"{self.the_run()['id']}-s01-p1").get("captures"), None)
        self.assertFalse(self.settle(timedelta(minutes=10)))
        self.assertIn("phase 1/6 open since", programs.describe(self.cfg, self.the_run(), self.now))

    def test_advance_next_waits_for_next_and_takes_every_reply(self):
        self.start("prep")
        self.answer("With my manager, Thursday, about the project.")
        self.answer("That it gets fixed without blame.")
        self.assertFalse(self.settle(timedelta(minutes=30)))
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.where(), (1, 1))
        self.assertTrue(programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next"))
        self.assertEqual(self.where(), (1, 2))
        self.assertEqual(self.entry(1).name, "2026-10-20-conversation-prep.md")
        self.assertEqual(self.entry(1).read_text(encoding="utf-8").count("%% journal phase:1 %%"), 2)

    def test_the_sweep_puts_a_missed_second_message_into_the_open_phase(self):
        self.start()
        rid = self.the_run()["id"]
        self.answer("first part")
        later = self.now + timedelta(seconds=30)
        with ledger.locked(self.cfg.ledger_path) as data:
            self.assertEqual(sweep.visible_at(self.cfg, data, later)["id"], f"{rid}-s01-p1")
        self.assertTrue(self.settle())
        with ledger.locked(self.cfg.ledger_path) as data:  # after the advance, an earlier moment still matches
            self.assertEqual(sweep.visible_at(self.cfg, data, later)["id"], f"{rid}-s01-p1")

    def test_a_situational_run_expires(self):
        self.start("prep")
        rid = self.the_run()["id"]
        programs.step(self.cfg, self.now + timedelta(hours=13), self.log)
        self.assertEqual(programs.find_run(self.cfg, rid)["status"], "expired")
        self.now += timedelta(hours=14)
        self.assertNotEqual(self.start("prep")["id"], rid)


class ReflectTest(Base):
    def _to_reflect(self):
        self.start()
        self.answer("I tolerate a job that bores me. I'm 41.")
        self.settle()

    def test_a_model_answer_quoting_words_not_in_his_answers_is_rejected(self):
        self.model = lambda q: 'You said "I hate my Tuesdays", so here is your Tuesday: grey, tired, alone.'
        self._to_reflect()
        html = self.bot.phase_sends()[-1]["text"]
        self.assertIn(FALLBACK, html)
        self.assertIn("Where you wake up and what your body feels like", html)
        self.assertIn("2.1", html)
        self.assertEqual(len(self.asked), 1)
        self.assertIn("I tolerate a job that bores me", self.asked[0])
        self.assertIn("work as a future-trajectory analyst", self.asked[0])

    def test_a_valid_construction_stands_in_for_the_task(self):
        self.model = lambda q: ('It is a Tuesday like the others. "a job that bores me" is still the job; the alarm '
                                'goes off and you are tired before you stand up.')
        self._to_reflect()
        html = self.bot.phase_sends()[-1]["text"]
        self.assertIn("the alarm goes off", html)
        self.assertNotIn(FALLBACK, html)
        self.assertNotIn("Where you wake up", html)
        self.assertIn("<b>2.1</b>", html)
        rid = self.the_run()["id"]
        self.assertNotIn("reflect", self.prompt(f"{rid}-s01-p2"))

    def test_validators(self):
        from journal import validate
        self.assertFalse(validate.check_reflect_phase("x" * 901, "")[0])
        self.assertFalse(validate.check_reflect_phase("⚠ couldn't finish this reply, use /retry", "")[0])
        self.assertTrue(validate.check_bridge('You named "the job" plainly.', "the job bores me")[0])
        self.assertFalse(validate.check_bridge("What else is there?", "x")[0])


class ContentTest(unittest.TestCase):
    """The port of the original English sessions (tests/fixtures/fix-life-en, copied read-only)."""

    TYPES = {1: "ask reflect ask ask ask task", 2: "ask reflect ask task ask", 3: "ask ask ask ask ask",
             4: "ask reflect ask ask ask", 5: "ask ask ask task ask", 6: "ask ask reflect ask ask",
             7: "ask ask ask ask ask", 8: "ask ask ask ask ask", 9: "ask ask ask ask ask",
             10: "ask task schedule task task"}
    SLOTS = {1: ["1.1", "1.2", "4.1", "5.1", "6.1"], 2: ["1.1", "3.1", "4.1", "5.1", "5.3"],
             3: ["1.1", "2.2", "3.3", "5.3"], 4: ["1.1", "4.1", "4.2", "5.1", "5.3"],
             5: ["1.1", "2.1", "3.2", "4.1", "5.3"], 6: ["1.1", "3.1", "4.1", "5.1"], 7: ["2.1", "4.1", "5.1", "5.2"],
             9: ["1.1", "3.1", "5.4"]}

    def setUp(self):
        prog, problems = programdefs.parse_program(library.BUILTIN_PROGRAMS / "fix-life", True)
        self.assertEqual(problems, [])
        self.prog = prog

    def test_every_original_question_line_is_in_exactly_one_ported_phase(self):
        line = re.compile(r"^\*\*([0-9]+[A-Z]?\.[0-9]+)\*\*\s*(.*\S)", re.M)
        for f in sorted(ORIGINALS.glob("*.md")):
            n = int(f.name[:2])
            session = self.prog.session(n)
            original = f.read_text(encoding="utf-8")
            for m in re.finditer(r"^## Phase ([0-9]+):.*?(?=^## |\Z)", original, re.M | re.S):
                for q in line.finditer(m.group(0)):
                    qid, text = q.group(1), q.group(2)
                    if (n, qid) == (10, "5.2"):  # "create cron reminders?": dropped (Decision 5)
                        self.assertFalse(any(f"**{qid}**" in ph.text() for ph in session.phases))
                        continue
                    found = [ph.number for ph in session.phases if f"**{qid}** {text}" in ph.text()]
                    self.assertEqual(found, [int(m.group(1))], f"session {n} {qid}")

    def test_shape_types_and_summary_slots(self):
        self.assertEqual(sum(len(s.phases) for s in self.prog.sessions), 51)
        for s in self.prog.sessions:
            self.assertEqual(" ".join(ph.type for ph in s.phases), self.TYPES[s.number], s.id)
            if s.number in self.SLOTS:
                self.assertEqual(programdefs.placeholders(s.summary), self.SLOTS[s.number], s.id)
            self.assertTrue(s.role and s.mission, s.id)
        self.assertTrue(self.prog.session(8).document_title.startswith("Final Document"))
        self.assertEqual(self.prog.session(10).document_title, "Generated Document")
        self.assertIn("/start fix-life insights", self.prog.session(10).summary)

    def test_no_packaging_is_ported(self):
        for s in self.prog.sessions:
            raw = s.path.read_text(encoding="utf-8")
            for gone in ("## Metadata", "## AI Role", "## Mission", "Summary Template", "[▶️ Continue]", "```",
                         "Adopt the role", "insights.md"):
                self.assertNotIn(gone, raw, s.id)
        self.assertEqual(self.prog.intro.count("rabbit"), 0)

    def test_situational_definitions_and_aliases(self):
        lib = library.load(make_cfg(Path(tempfile.mkdtemp())))
        self.assertEqual(lib.program("prep").id, "conversation-prep")
        self.assertEqual(lib.program("debrief").after, "fix-life")
        self.assertEqual(lib.problems, [])


class EntriesTest(Base):
    def test_session_entries_are_recognised_and_his_own_file_is_not(self):
        self.start(session=3)
        self.answer("I'm not a morning person")
        path = self.entry()
        self.assertEqual(path.name, "2026-10-20-fix-life-s03.md")
        self.assertEqual(entries.kind_of(path), "fix-life")
        self.assertEqual(entries.entry_key(path), ("fix-life", 3, 1))
        self.assertIn(path, entries.entries_on(self.cfg.root, "2026-10-20"))
        mine = self.cfg.root / "2026-10-20-fix-life-s04.md"
        mine.write_text("# my own notes on session 4\n", encoding="utf-8")
        self.assertIsNone(entries.kind_of(mine))

    def test_a_late_reply_to_an_earlier_phase_lands_above_the_summary(self):
        self.start("debrief")
        rid = self.the_run()["id"]
        p1 = self.prompt(f"{rid}-s01-p1")
        self.answer("that it landed at all")
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.answer("call my sister")
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.assertEqual(programs.find_run(self.cfg, rid)["status"], "completed")
        self.answer("one more thing about phase one", reply_to=p1["message_id"])
        body = self.entry(1).read_text(encoding="utf-8")
        self.assertLess(body.index("one more thing about phase one"), body.index("## Summary"))
        self.assertIn("one more thing", entries.raw_entry(body))
        self.assertNotIn("Still with me", entries.raw_entry(body))  # the summary is not his words section

    def test_annotations_wait_for_the_session(self):
        self.start("debrief")
        rid = self.the_run()["id"]
        self.answer("that it landed at all")
        self.now += timedelta(hours=1)
        with mock.patch.object(agent, "ask", side_effect=AssertionError("annotated mid-session")):
            tick.annotate(self.cfg, self.now, self.log)
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.answer("call my sister")
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.now += timedelta(hours=1)
        notes = []
        with mock.patch.object(agent, "ask", lambda *a, **k: notes.append(a[1]) or "- He named one thing to do."):
            tick.annotate(self.cfg, self.now, self.log)
        self.assertEqual(len(notes), 1)  # once for the session, not per phase
        self.assertIn("He named one thing", self.entry(1).read_text(encoding="utf-8"))

    def test_fold_waits_for_the_advance_and_uses_the_phase_header(self):
        self.start()
        rid = self.the_run()["id"]
        self.answer("an answer")
        tick.fold(self.cfg, self.now, self.log)
        self.assertEqual([m for m, _ in self.bot.calls].count("editMessageText"), 0)
        self.settle()
        tick.fold(self.cfg, self.now, self.log)
        edits = [p["text"] for m, p in self.bot.calls if m == "editMessageText"]
        self.assertEqual(edits, ["<b>🧠 Fix your entire life in 1 day · Session 1/10 · Phase 1/6 · answered ✓</b>"])
        self.assertTrue(self.prompt(f"{rid}-s01-p1")["collapsed_at"])

    def test_prune_keeps_an_active_runs_prompts(self):
        self.start()
        rid = self.the_run()["id"]
        tick.prune(self.cfg, self.now + timedelta(days=tick.KEEP_DAYS + 10))
        self.assertIsNotNone(self.prompt(f"{rid}-s01-p1"))
        programs.abandon(self.cfg, None, self.now, self.log)
        tick.prune(self.cfg, self.now + timedelta(days=tick.KEEP_DAYS + 10))
        self.assertIsNone(self.prompt(f"{rid}-s01-p1"))

    def test_a_phase_is_not_a_missed_practice_prompt_and_not_a_practice_row(self):
        self.start()
        self.now += timedelta(hours=4)
        self.assertEqual(context.unanswered(self.cfg, self.now.astimezone(self.cfg.tz)), [])
        data = today.collect(self.cfg, self.now)
        self.assertEqual([r for r in data["practices"]["rows"] if r["id"] == "fix-life"], [])
        self.assertTrue(data["program"]["text"].startswith(
            "Fix your entire life in 1 day — session 1/10 (The Anti-Vision Architect), phase 1/6 open since"))


class SurfaceTest(Base):
    def test_the_hook_notes_a_phase_answer_and_spawns_the_settle(self):
        self.start()
        rid = self.the_run()["id"]
        spawned = []
        event = SimpleNamespace(source=SimpleNamespace(platform="telegram", chat_id=CHAT), text="my answer",
                                message_id="88", reply_to_message_id=None, message_type="TEXT", media_urls=[])
        with mock.patch.object(hooks.runtime, "load_config", lambda: self.cfg), \
                mock.patch.object(hcommands, "spawn", lambda cfg, *a: spawned.append(a)):
            self.now += timedelta(minutes=1)  # the hook stamps its message with the held clock, as tick and programs do
            out = hooks.on_dispatch(event)
        note = out["text"][:-len("my answer")]
        self.assertTrue(note.startswith("[Journal reply — program reply: Sam's answer to Fix your entire life"))
        self.assertIn("phase 1/6 “Current Reality Excavation”", note)
        self.assertTrue(note.rstrip().endswith("annotations are written automatically.]"))
        self.assertEqual(context.clean_user_text(out["text"]), "my answer")
        self.assertEqual(sweep.read(out["text"], "88")["kind"], "handled")
        self.assertEqual(spawned, [("program", "settle", rid)])

    def test_start_resolves_programs_aliases_sessions_and_insights(self):
        self.assertEqual(jcommands.start(self.cfg, "fix-life").program.id, "fix-life")
        self.assertEqual(jcommands.start(self.cfg, "Fix-Life 3").session, 3)
        self.assertEqual(jcommands.start(self.cfg, "fix-life session 4").session, 4)
        self.assertTrue(jcommands.start(self.cfg, "fix-life insights").insights)
        self.assertEqual(jcommands.start(self.cfg, "prep").program.id, "conversation-prep")
        self.assertIn("sessions 1 to 10", jcommands.start(self.cfg, "fix-life 11").reply)
        self.assertIsNone(jcommands.start(self.cfg, "fix-life 11").program)

    def test_program_commands_do_not_collide_with_hermes(self):
        names = [n for n, _, _ in hcommands.PROGRAM_COMMANDS] + ["abandon", "program"]
        self.assertEqual(names, ["next", "skip", "back", "break", "abandon", "program"])
        self.assertTrue({"abandon", "program"} <= {s.name for s in jcommands.specs()})
        for n in names:
            self.assertNotIn(n, config.HERMES_COMMANDS)
            self.assertIn(n, config.RESERVED_COMMANDS)
            self.assertTrue(config.opt_out_problem(f"/{n}"))
        for button in [b for row in compose.KEYBOARD.rows for b in row]:  # (a ports.Keyboard since CHANNELS)
            self.assertNotIn(button.lstrip("/"), config.HERMES_COMMANDS)

    def test_program_command_handlers(self):
        spawned = []
        with mock.patch.object(hcommands.runtime, "load_config", lambda: self.cfg), \
                mock.patch.object(hcommands, "spawn", lambda cfg, *a: spawned.append(a)):
            next_ = hcommands.program_action("next", "next")
            self.assertIn("No program is running", next_(""))
            self.start()
            rid = self.the_run()["id"]
            self.assertIsNone(next_(""))
            self.assertEqual(spawned[-1], ("program", "next", rid, "--notify", "--expect", "1:1:1"))
            self.assertIn("On a break", hcommands.program_action("break", "pause")(""))
            self.assertIn("Fix your entire life in 1 day — session 1/10", hcommands.program_status(""))
            start = hcommands.start("fix-life 2")
            self.assertIn("session 2 is on its way", start)
            self.assertEqual(spawned[-1], ("program", "start", "fix-life", "--notify", "--session", "2"))

    def test_cli_status_lists_fix_life(self):
        args = cli.build_parser().parse_args(["program", "status"])
        with mock.patch("sys.stdout") as out:
            self.assertEqual(cli.run(args, cfg_loader=lambda: self.cfg), 0)
        printed = "".join(c.args[0] for c in out.write.call_args_list)
        self.assertIn("/start fix-life (Fix your entire life in 1 day)", printed)

    def test_cli_start_and_next(self):
        for argv in (["program", "start", "prep"], ["program", "next"]):
            args = cli.build_parser().parse_args(argv)
            with mock.patch("sys.stdout"):
                self.assertEqual(cli.run(args, cfg_loader=lambda: self.cfg), 0)
        self.assertEqual(self.where(), (1, 2))
        args = cli.build_parser().parse_args(["program", "back"])
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            self.assertEqual(cli.run(args, cfg_loader=lambda: self.cfg), 0)
        self.assertEqual(self.where(), (1, 1))

    def test_debrief_follow_up_after_a_session(self):
        from journal import notebook
        self.start(session=9)
        for _ in range(5):
            self.answer("an answer")
            programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next")
        items = notebook.open_items(self.cfg)
        self.assertEqual(len(items), 1)
        self.assertIn("offer the post-session debrief when it fits (/start debrief)", items[0]["text"])
        self.assertEqual(self.where(), (10, 1))
        self.assertIn("Next: /start fix-life continues with session 10", self.bot.summaries()[-1]["text"])

    def test_a_long_phase_is_split_on_its_boundary(self):
        prog = self.prog()
        parts = [(None, "intro " * 500), (None, "**2.1** " + "question " * 100), (None, "more " * 300)]
        msgs = compose.phase_messages(prog, 1, 2, parts)
        self.assertEqual(len(msgs), 2)
        self.assertTrue(all(len(m) <= compose.TELEGRAM_LIMIT for m in msgs))
        self.assertIn("<b>2.1</b>", msgs[1])
        self.assertTrue(msgs[1].endswith(f"<i>{compose.PHASE_HINT}</i>"))


class LibraryTest(Base):
    def test_a_user_situational_file_and_a_clashing_program(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True)
        (d / "gratitude-walk.md").write_text("""---
kind: situational
id: gratitude-walk
title: Gratitude walk
---
## Phase 1: Out the door
**1.1** What did you notice first?

## Summary
- {1.1}
""", encoding="utf-8")
        (d / "morning-run").mkdir()
        (d / "bad.md").write_text("---\nkind: situational\nid: morning\ntitle: X\n---\n## Phase 1: A\n**1.1** Q\n",
                                  encoding="utf-8")
        lib = library.load(self.cfg)
        self.assertEqual(lib.program("gratitude-walk").title, "Gratitude walk")
        self.assertTrue(any("also a practice" in p for p in lib.problems))
        self.assertIn("morning", lib.practices)  # the practice is untouched

    def test_parse_problems(self):
        _, problems = programdefs.parse_situational("""---
kind: situational
id: x-y
title: X
---
## Phase 2: Wrong number
type: wander
**1.1** Q

## Summary
{9.9} {nothing}
""")
        text = "; ".join(problems)
        self.assertIn("out of order", text)
        self.assertIn("type must be one of", text)
        self.assertIn("{9.9}", text)
        self.assertIn("{nothing}", text)


if __name__ == "__main__":
    unittest.main()
