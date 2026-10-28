"""One program at a time (DECISIONS.md, ONE-PROGRAM): "One program means if I have not completed fix life in one day I
can't start another program. Of course practices and situational ones will continue to work." While a program (not
situational) has an unfinished run, active or on a break, mid-session or between sessions, starting another program
is refused with nothing changed, and the refusal names the unfinished one, where it is, and the ways out. Finishing or
abandoning it frees the slot; ``/break`` does not. Situational sessions are never refused and never refuse. A
program's own run is never refused; two starts at once, under the tick's lock, give one run. Telegram is a list of
calls; the model is unreachable; the clock is held."""
import dataclasses
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from serve_helpers import NOW
from test_programs import Base
from test_serve_programs import ProgramCase

from journal import cli, ledger, programs, telegram, tick
from journal import commands as jcommands

FIX = "Fix your entire life in 1 day"


def add_course(cfg, sessions=2, pid="course"):
    """A program of his own (not situational) beside fix-life: ``pid``, ``sessions`` sessions of one phase."""
    root = Path(cfg.practices_dir) / pid
    (root / "sessions").mkdir(parents=True, exist_ok=True)
    names = []
    for n in range(1, sessions + 1):
        names.append(f"0{n}-s{n}")
        (root / "sessions" / f"0{n}-s{n}.md").write_text(
            f"---\nkind: session\nid: s{n}\nnumber: {n}\ntitle: Part {n}\n---\n## Phase 1: A\n**1.1** Q{n}?\n",
            encoding="utf-8")
    (root / "program.md").write_text(
        f"---\nkind: program\nid: {pid}\ntitle: A {pid}\nadvance: next\nsessions: {', '.join(names)}\n---\nIntro\n",
        encoding="utf-8")


def snapshot(cfg):
    """Every run file and the ledger, byte for byte: a refusal must leave both as they were."""
    runs = {str(p): p.read_bytes() for p in sorted(Path(cfg.state_dir, "runs").glob("*/*.json"))}
    return runs, Path(cfg.ledger_path).read_bytes()


class OneAtATime(Base):
    def setUp(self):
        super().setUp()
        # the CLI and the chat commands read the wall clock, not the held one: hold it at the wall clock's time, so
        # a situational run started here is not already past its expires_hours when they look
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        add_course(self.cfg)

    def finish_session(self, run_id):
        """Skip through the open session to its end (the run is then between sessions, or complete)."""
        s = programs.find_run(self.cfg, run_id)["current"]["session"]
        while programs.find_run(self.cfg, run_id)["sessions"][str(s)]["status"] == "in_progress":
            self.now += timedelta(minutes=1)
            programs.skip(self.cfg, run_id, self.now, self.log)

    def cli(self, *argv):
        args = cli.build_parser().parse_args(["program", *argv])
        with mock.patch("sys.stdout"), mock.patch("sys.stderr") as err:
            code = cli.run(args, cfg_loader=lambda: self.cfg)
        return code, "".join(c.args[0] for c in err.write.call_args_list)

    def refused(self, pid="course"):
        """Starting ``pid`` is refused and changes nothing: the refusal's text."""
        before, sends = snapshot(self.cfg), len(self.bot.calls)
        with self.assertRaises(programs.Busy) as got:
            self.start(pid)
        self.assertEqual((snapshot(self.cfg), len(self.bot.calls)), (before, sends))
        return got.exception


class RefusalTest(OneAtATime):
    def test_a_second_program_is_refused_while_one_is_mid_session_and_nothing_changes(self):
        fix = self.start()
        self.now += timedelta(minutes=5)
        got = self.refused()
        self.assertEqual(got.run["id"], fix["id"])
        self.assertEqual(str(got), f"{FIX} isn't finished (in session 1 of 10: The Anti-Vision Architect), and only "
                                   "one program runs at a time. Carry on with /start fix-life and finish it, or end it "
                                   "with /abandon fix-life (its entries stay). Then /start course.")
        self.assertEqual([r["program"] for r in programs.active_runs(self.cfg)], ["fix-life"])

    def test_a_run_on_a_break_still_holds_the_slot(self):
        """/break pauses; it does not free the slot."""
        fix = self.start()
        programs.pause(self.cfg, fix["id"], self.now, self.log)
        self.assertIn(f"{FIX} isn't finished (on a break in session 1 of 10: The Anti-Vision Architect)",
                      str(self.refused()))

    def test_a_run_between_sessions_still_holds_the_slot(self):
        fix = self.start()
        self.finish_session(fix["id"])
        self.assertFalse(programs.in_session(programs.find_run(self.cfg, fix["id"])))
        self.assertIn(f"{FIX} isn't finished (session 2 of 10 is next: The Hidden Goal Decoder)",
                      str(self.refused()))

    def test_abandoning_it_frees_the_slot(self):
        fix = self.start()
        self.finish_session(fix["id"])
        self.refused()
        programs.abandon(self.cfg, fix["id"], self.now, self.log)
        self.assertTrue(programs.in_session(self.start("course")))

    def test_finishing_it_frees_the_slot(self):
        course = self.start("course")
        self.refused("fix-life")
        self.finish_session(course["id"])
        programs.advance(self.cfg, course["id"], self.now, self.log, reason="next")  # session 2 begins
        self.refused("fix-life")
        self.finish_session(course["id"])
        self.assertEqual(programs.find_run(self.cfg, course["id"])["status"], "completed")
        self.assertTrue(programs.in_session(self.start("fix-life")))

    def test_a_situational_session_is_never_refused_and_never_refuses(self):
        fix = self.start()
        prep = self.start("conversation-prep")             # fix-life mid-session: prep goes ahead
        self.assertTrue(programs.in_session(prep) and programs.in_session(programs.find_run(self.cfg, fix["id"])))
        self.assertEqual(self.refused().run["id"], fix["id"])   # what holds the slot is fix-life, not prep
        programs.abandon(self.cfg, fix["id"], self.now, self.log)
        self.assertTrue(programs.in_session(self.start("course")))  # prep mid-session refuses nothing
        debrief = self.start("post-session-debrief")        # a second situational one, beside both
        self.assertTrue(programs.in_session(debrief))

    def test_a_program_never_refuses_its_own_run(self):
        """Taking its own run up again, after a break or between sessions, is going on with it: never refused."""
        fix = self.start()
        self.start("conversation-prep")
        self.now += timedelta(minutes=5)
        again = self.start()                                # mid-session: the open phase is sent again
        self.assertEqual((again["id"], self.where()), (fix["id"], (1, 1)))
        programs.pause(self.cfg, fix["id"], self.now, self.log)
        programs.resume(self.cfg, fix["id"], self.now, self.log)
        self.assertEqual(programs.find_run(self.cfg, fix["id"])["status"], "active")
        self.finish_session(fix["id"])
        self.assertTrue(programs.advance(self.cfg, fix["id"], self.now, self.log, reason="next"))
        self.assertEqual(programs.find_run(self.cfg, fix["id"])["sessions"]["2"]["status"], "in_progress")

    def test_a_situational_run_past_its_expiry_is_started_afresh_whatever_else_is_unfinished(self):
        prep = self.start("conversation-prep")
        self.start("fix-life")
        self.now += timedelta(hours=13)  # conversation-prep expires after 12 idle hours
        again = self.start("conversation-prep")
        self.assertNotEqual(again["id"], prep["id"])
        self.assertEqual(programs.find_run(self.cfg, prep["id"])["status"], "expired")

    def test_the_refusal_for_a_program_turned_off_offers_abandon_only(self):
        self.start()
        load = programs._program

        def off(cfg, pid):
            prog = load(cfg, pid)
            return dataclasses.replace(prog, enabled=False) if pid == "fix-life" else prog
        with mock.patch.object(programs, "_program", off):
            got = str(self.refused())
        self.assertNotIn("/start fix-life", got)
        self.assertTrue(got.endswith("It's turned off: end it with /abandon fix-life (its entries stay), or turn it "
                                     "back on to finish it. Then /start course."), got)

    def test_a_run_left_past_a_trimmed_definition_is_named_as_it_is(self):
        """Review on PR #75: a run whose session the definition no longer has is still enabled; the refusal names it
        by title and offers /start, and only "where" is left out."""
        course = self.start("course")
        self.finish_session(course["id"])
        programs.advance(self.cfg, course["id"], self.now, self.log, reason="next")   # in session 2
        add_course(self.cfg, sessions=1)                                               # he trims it to one
        (Path(self.cfg.practices_dir) / "course" / "sessions" / "02-s2.md").unlink()
        got = str(self.refused("fix-life"))
        self.assertTrue(got.startswith("A course isn't finished, and only one program runs at a time. Carry on with "
                                       "/start course and finish it, or end it with /abandon course"), got)

    def test_after_a_situational_session_its_summary_says_how_to_pick_the_program_up(self):
        """Review on PR #75: prep's phases supersede fix-life's open phase, and prep's summary removes its keyboard;
        so the summary says /start fix-life sends the open phase again, which then takes the next message."""
        fix = self.start()
        self.now += timedelta(minutes=1)
        prep = self.start("conversation-prep")
        self.finish_session(prep["id"])
        self.assertEqual(programs.find_run(self.cfg, prep["id"])["status"], "completed")
        self.assertIn("Fix your entire life in 1 day is still open at session 1, phase 1: /start fix-life sends it "
                      "again.", self.bot.summaries()[-1]["text"])
        self.now += timedelta(minutes=1)
        self.start()
        got = self.answer("back to the anti-vision")
        self.assertEqual(got["prompt"]["id"], programs.prompt_id(fix["id"], 1, 1))

    def test_a_situational_summary_with_nothing_else_open_says_nothing_more(self):
        prep = self.start("conversation-prep")
        self.finish_session(prep["id"])
        self.assertNotIn("still open", self.bot.summaries()[-1]["text"])

    def test_a_run_whose_definition_is_gone_does_not_block(self):
        fix = self.start()
        run = programs.find_run(self.cfg, fix["id"])
        programs.run_path(self.cfg, "fix-life", fix["id"]).unlink()
        run["program"] = "gone-program"
        programs._save(self.cfg, run)
        self.assertTrue(programs.in_session(self.start("course")))

    def test_two_unfinished_from_before_the_rule_both_carry_on(self):
        """Not migrated: two programs unfinished at deploy each go on with their own run (each holds a slot); a third
        program is refused until both are done."""
        fix = self.start()
        with mock.patch.object(programs, "blocking", lambda *a, **k: None):
            course = self.start("course")
        for rid in (fix["id"], course["id"]):
            programs.pause(self.cfg, rid, self.now, self.log)
        for pid in ("fix-life", "course"):
            self.assertEqual(self.start(pid)["status"], "active")
        add_course(self.cfg, pid="other")
        self.assertIn(programs.find_run(self.cfg, self.refused("other").run["id"])["program"], ("fix-life", "course"))


class FacesTest(OneAtATime):
    def test_cli_start_refuses_with_the_reason_and_notifies_the_chat(self):
        self.start()
        before = snapshot(self.cfg)
        sends = len(self.bot.sends())
        code, err = self.cli("start", "course")
        self.assertEqual(code, 2)
        self.assertIn(f"{FIX} isn't finished", err)
        self.assertEqual((snapshot(self.cfg), len(self.bot.sends())), (before, sends))
        code, _ = self.cli("start", "course", "--notify")
        self.assertEqual(code, 2)
        self.assertIn("/abandon fix-life", self.bot.sends()[-1]["text"])
        self.assertEqual(snapshot(self.cfg), before)

    def test_cli_new_from_the_web_spawn_refuses_too(self):
        self.start()
        before = snapshot(self.cfg)
        code, err = self.cli("start", "course", "--new")
        self.assertEqual((code, snapshot(self.cfg)), (2, before))
        self.assertIn("only one program runs at a time", err)

    def test_chat_start_answers_the_refusal_and_spawns_nothing(self):
        """Telegram, Discord and Slack share ``commands.start_reply`` (the Hermes plugin adapts it per platform)."""
        fix = self.start()
        programs.pause(self.cfg, fix["id"], self.now, self.log)
        spawned = []
        spawn = lambda cfg, *a: spawned.append(a)  # noqa: E731
        reply = jcommands.start_reply(self.cfg, "course", spawn)
        self.assertTrue(reply.startswith(f"{FIX} isn't finished (on a break in session 1 of 10"), reply)
        self.assertIn("Carry on with /start fix-life and finish it, or end it with /abandon fix-life", reply)
        self.assertEqual(spawned, [])
        # its own program goes ahead, and so does a situational one
        self.assertIn("is on its way", jcommands.start_reply(self.cfg, "fix-life", spawn))
        self.assertIn("is on its way", jcommands.start_reply(self.cfg, "prep", spawn))
        self.assertEqual(spawned, [("program", "start", "fix-life", "--notify"),
                                   ("program", "start", "conversation-prep", "--notify")])

    def test_chat_next_between_sessions_begins_the_next_one_whatever_else_runs(self):
        fix = self.start()
        self.finish_session(fix["id"])
        prep = self.start("conversation-prep")
        programs.pause(self.cfg, prep["id"], self.now, self.log)   # (so /next names fix-life: nothing mid-session)
        spawned = []
        self.assertIsNone(jcommands.program_action_reply(self.cfg, "next", "next", lambda cfg, *a: spawned.append(a)))
        self.assertEqual(spawned[-1][:3], ("program", "next", fix["id"]))
        self.assertEqual(self.cli("next", fix["id"])[0], 0)
        self.assertTrue(programs.in_session(programs.find_run(self.cfg, fix["id"])))

    def test_break_says_it_stays_unfinished(self):
        desc = dict((n, d) for n, _a, d in jcommands.PROGRAM_COMMANDS)["break"]
        self.assertIn("it stays unfinished", desc)

    def the_reviewers_case(self):
        """fix-life between sessions, prep mid-session, and the last thing he did a reply to an old fix-life phase."""
        fix = self.start()
        old = self.prompt(programs.prompt_id(fix["id"], 1, 1))["message_id"]
        self.finish_session(fix["id"])
        prep = self.start("conversation-prep")
        self.answer("one more thought on that first question", after=timedelta(minutes=5), reply_to=old)
        self.assertGreater(programs.last_activity(self.cfg, programs.find_run(self.cfg, fix["id"])),
                           programs.last_activity(self.cfg, programs.find_run(self.cfg, prep["id"])))
        return fix, prep

    def test_run_commands_act_on_the_run_with_a_session_in_progress(self):
        """/break, /next, /skip and /back reach the session he is in, not the run touched last (DECISIONS.md,
        ONE-PROGRAM)."""
        fix, prep = self.the_reviewers_case()
        self.assertEqual(programs.current_run(self.cfg)["id"], prep["id"])
        spawned = []
        spawn = lambda cfg, *a: spawned.append(a)  # noqa: E731
        self.assertIsNone(jcommands.program_action_reply(self.cfg, "next", "next", spawn))
        self.assertEqual(spawned[-1][:3], ("program", "next", prep["id"]))
        self.assertIn("On a break. /start conversation-prep", jcommands.program_action_reply(self.cfg, "break", "pause", spawn))
        self.assertEqual(spawned[-1], ("program", "pause", prep["id"], "--notify"))
        # the CLI with no run named resolves the same way (the web app names the run in its URL)
        self.assertEqual(self.cli("pause"), (0, ""))
        self.assertEqual(programs.find_run(self.cfg, prep["id"])["status"], "paused")
        self.assertEqual(programs.find_run(self.cfg, fix["id"])["status"], "active")
        # with nothing mid-session, the run touched last again
        self.assertEqual(programs.current_run(self.cfg)["id"], fix["id"])


class AbandonCommandTest(OneAtATime):
    """``/abandon <id>``: the chat's way to free the slot. An ended run is not undone, so it must be named."""

    def setUp(self):
        super().setUp()
        self.spawned = []

    def reply(self, raw=""):
        return jcommands.abandon_reply(self.cfg, raw, lambda cfg, *a: self.spawned.append(a))

    def test_a_finished_run_with_interrupts_to_come_can_still_be_abandoned(self):
        """Review on PR #75: session 10's interrupts outlive the run; /abandon fix-life cancels them, as the CLI and
        the web's Abandon do."""
        fix = self.start()
        run = programs.find_run(self.cfg, fix["id"])
        run["status"] = "completed"
        run["scheduled"] = [{"id": f"i{n}", "n": n, "of": 2, "offset": f"+{2 * n}h", "text": "Q", "status": "pending",
                             "local": "2030-01-01T09:00"} for n in (1, 2)]
        programs._save(self.cfg, run)
        self.assertEqual(self.reply("fix-life"), f"Cancelling the 2 interrupt(s) still to come from {FIX}. Its "
                                                 "entries stay in the journal.")
        self.assertEqual(self.spawned, [("program", "abandon", fix["id"], "--notify")])
        self.assertEqual(self.reply(), "Which one? /abandon fix-life (cancels its interrupts still to come). Its "
                                       "entries stay.")
        self.assertEqual(self.reply("course"), "There's no course run to abandon. You can name: /abandon fix-life "
                                               "(cancels its interrupts still to come).")

    def test_a_run_whose_definition_is_gone_is_ended_without_offering_start(self):
        fix = self.start()
        run = programs.find_run(self.cfg, fix["id"])
        programs.run_path(self.cfg, "fix-life", fix["id"]).unlink()
        run["program"] = "gone-program"
        programs._save(self.cfg, run)
        self.assertEqual(self.reply("gone-program"), "Ending gone-program where it is. Its entries stay in the "
                                                     "journal.")
        self.assertEqual(self.spawned, [("program", "abandon", fix["id"], "--notify")])

    def test_with_nothing_running_it_says_so(self):
        self.assertEqual(self.reply("fix-life"), "No program is running, so there is nothing to abandon.")
        self.assertEqual(self.spawned, [])

    def test_alone_it_ends_nothing_and_says_what_could_be_ended(self):
        self.start()
        self.now += timedelta(minutes=1)
        self.start("conversation-prep")
        self.assertEqual(self.reply(), "Which one? /abandon fix-life, /abandon prep. Its entries stay.")
        self.assertEqual(self.spawned, [])

    def test_named_by_id_or_alias_it_spawns_the_abandon_of_that_run(self):
        fix = self.start()
        prep = self.start("conversation-prep")
        self.assertEqual(self.reply("Fix-Life"), f"Ending {FIX} where it is. Its entries stay in the journal; "
                                                 "/start fix-life begins it afresh.")
        self.assertIn("Conversation prep", self.reply("prep"))
        self.assertEqual(self.spawned, [("program", "abandon", fix["id"], "--notify"),
                                        ("program", "abandon", prep["id"], "--notify")])
        # what the spawn runs: the run is over and the slot is free
        self.assertEqual(self.cli("abandon", fix["id"]), (0, ""))
        self.assertTrue(programs.in_session(self.start("course")))

    def test_abandoning_a_situational_run_says_how_to_pick_the_program_up(self):
        self.start()
        self.now += timedelta(minutes=1)
        self.start("conversation-prep")
        self.assertTrue(self.reply("prep").endswith("Fix your entire life in 1 day is still open at session 1, phase "
                                                    "1: /start fix-life sends it again."))

    def test_a_program_with_no_run_is_not_abandoned(self):
        self.start()
        self.assertEqual(self.reply("course"), "There's no course run to abandon. You can name: /abandon fix-life.")
        self.assertEqual(self.spawned, [])

    def test_it_is_registered_beside_the_other_program_commands(self):
        spec = next(s for s in jcommands.specs() if s.name == "abandon")
        self.assertIn("/abandon <id>", spec.description)


class ConcurrentStartTest(OneAtATime):
    def test_two_starts_at_once_under_the_lock_give_one_run(self):
        """Two faces, two programs, the same moment: each takes the tick's lock (as ``journal program start`` does)
        and the second sees the first's run. ``_enter`` is slowed so a check made outside the lock would let both
        through."""
        enter = programs._enter

        def slow(*a, **k):
            time.sleep(0.3)
            return enter(*a, **k)
        results, go = {}, threading.Barrier(2)

        def face(pid):
            go.wait()
            try:
                with programs.locked(self.cfg):
                    programs.start(self.cfg, pid, self.now, [])
                results[pid] = "started"
            except programs.Busy:
                results[pid] = "busy"
        with mock.patch.object(programs, "_enter", slow):
            threads = [threading.Thread(target=face, args=(pid,)) for pid in ("fix-life", "course")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        self.assertEqual(sorted(results.values()), ["busy", "started"])
        self.assertEqual(len(programs.all_runs(self.cfg)), 1)


class WebTest(ProgramCase):
    """The web app checks before it spawns (409 ``program-in-progress``, the message shown as it is, with the "Carry
    on with it" link to ``run``); the spawned child checks again under the lock. fix-life is running (setUp)."""

    def setUp(self):
        super().setUp()
        add_course(self.cfg)

    def test_start_is_refused_naming_the_unfinished_program(self):
        got = self.refused("POST", "/api/v1/runs", {"program": "course"}, want=409)
        self.assertEqual((got["error"], got["run"], got["program"]), ("program-in-progress", self.rid, "fix-life"))
        self.assertEqual(got["detail"], f"{FIX} isn't finished (in session 1 of 10: The Anti-Vision Architect), and "
                                        "only one program runs at a time. Finish it or abandon it first.")
        programs.pause(self.cfg, self.rid, NOW, self.log)   # a break does not free it
        self.assertIn("on a break", self.refused("POST", "/api/v1/runs", {"program": "course"}, want=409)["detail"])
        self.assertEqual(self.spawned, [])
        programs.abandon(self.cfg, self.rid, NOW, self.log)
        self.ok("POST", "/api/v1/runs", {"program": "course"}, want=202)
        self.assertEqual(self.spawned, [["program", "start", "course", "--new"]])

    def test_a_situational_start_and_the_runs_own_actions_go_ahead(self):
        self.ok("POST", "/api/v1/runs", {"program": "conversation-prep"}, want=202)
        programs.pause(self.cfg, self.rid, NOW, self.log)
        self.ok("POST", f"/api/v1/runs/{self.rid}/resume", want=202)
        self.assertEqual(self.spawned, [["program", "start", "conversation-prep", "--new"],
                                        ["program", "resume", self.rid, "--paused"]])

    def test_the_spawned_child_refuses_a_start_that_lost_the_race(self):
        """Both requests passed the check; the second child, under the lock, finds the first one's run."""
        programs.abandon(self.cfg, self.rid, NOW, self.log)
        self.ok("POST", "/api/v1/runs", {"program": "course"}, want=202)
        programs.start(self.cfg, "fix-life", NOW + timedelta(minutes=1), self.log)   # the other face won
        with self.assertRaises(programs.Busy):
            programs.start(self.cfg, "course", NOW + timedelta(minutes=1), self.log, only_new=True)
        self.assertEqual([r["program"] for r in programs.active_runs(self.cfg)], ["fix-life"])


class SendStaysWithTickTest(OneAtATime):
    def test_an_owed_send_of_the_unfinished_run_is_still_delivered(self):
        """The tick's owed send continues the run that holds the slot (its first send failed): it is that run's
        own, so nothing defers it, and the run holds the slot meanwhile."""
        def down(cfg, method, payload):
            raise telegram.TelegramError("down")
        with mock.patch.object(telegram, "_call", down):
            fix = self.start()
        quiet = mock.patch.object(programs, "_off_the_record", lambda cfg, now: False)  # (no /x window)
        quiet.start()
        self.addCleanup(quiet.stop)
        self.assertTrue(programs.find_run(self.cfg, fix["id"])["sessions"]["1"]["phases"]["1"]["send_due"])
        self.refused()
        self.now += timedelta(minutes=5)
        programs.step(self.cfg, self.now, self.log)
        self.assertFalse(programs.find_run(self.cfg, fix["id"])["sessions"]["1"]["phases"]["1"]["send_due"])
        self.assertTrue(ledger.find(ledger.snapshot(self.cfg.ledger_path), programs.prompt_id(fix["id"], 1, 1))
                        .get("message_id"))
