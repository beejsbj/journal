"""WP10 review points on the transitions the web spawns, each pinned by a test that failed before its fix: a second
``start`` or ``resume`` that was already queued when the first one ran does nothing (it would send the open phase
again, or jump the run to its own session), and a run whose program file is gone can still be ended."""
from datetime import timedelta
from unittest import mock

from journal import cli, programs
from test_programs import Base


class QueuedTwiceTest(Base):
    def test_a_second_start_that_was_queued_with_the_first_does_not_resend_or_jump(self):
        first = self.start(only_new=True)
        sent = len(self.bot.phase_sends())
        again = self.start(only_new=True, session=3)
        self.assertEqual((again["id"], self.where()), (first["id"], (1, 1)))
        self.assertEqual(len(self.bot.phase_sends()), sent)
        self.assertIn("already in progress", self.log[-1])
        self.assertEqual(len(programs.all_runs(self.cfg)), 1)

    def test_without_only_new_a_start_still_resumes_as_the_chat_does(self):
        self.start()
        sent = len(self.bot.phase_sends())
        self.start()
        self.assertEqual(len(self.bot.phase_sends()), sent + 1)

    def test_a_resume_queued_twice_resumes_once(self):
        self.start()
        rid = self.the_run()["id"]
        programs.pause(self.cfg, rid, self.now, self.log)
        programs.resume(self.cfg, rid, self.now, self.log, only_paused=True)
        sent = len(self.bot.phase_sends())
        programs.resume(self.cfg, rid, self.now, self.log, only_paused=True)
        self.assertEqual(len(self.bot.phase_sends()), sent)
        self.assertEqual(self.the_run()["status"], "active")

    def test_the_cli_takes_both_flags(self):
        p = cli.build_parser()
        self.assertTrue(p.parse_args(["program", "start", "fix-life", "--new"]).new)
        self.assertTrue(p.parse_args(["program", "resume", "RUN", "--paused"]).paused)
        self.start()
        sent = len(self.bot.phase_sends())
        args = p.parse_args(["program", "start", "fix-life", "--new", "--session", "4"])
        with mock.patch("sys.stdout"):
            self.assertEqual(cli.run(args, cfg_loader=lambda: self.cfg), 0)
        self.assertEqual((self.where(), len(self.bot.phase_sends())), ((1, 1), sent))


class OrphanedRunTest(Base):
    def test_a_run_whose_program_is_gone_can_be_abandoned(self):
        self.start()
        run = self.the_run()
        programs.run_path(self.cfg, run["program"], run["id"]).unlink()
        run["program"] = "gone-program"      # its program file was deleted by hand, or no longer parses
        programs._save(self.cfg, run)
        with self.assertRaises(programs.ProgramError):
            programs.require(self.cfg, run["id"])
        programs.abandon(self.cfg, run["id"], self.now + timedelta(minutes=1), self.log)
        self.assertEqual(programs.find_run(self.cfg, run["id"])["status"], "abandoned")
