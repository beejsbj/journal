"""WP4 review findings that were still open after the merge, each pinned by a test that failed before its fix: a phase
re-entered going forward does not settle on its old answer, a session summary whose audio answer was still being
transcribed is filled in once the transcript lands, one run's failed send does not stop the other runs' step, a
session file name cannot leave the program's directory, and the sweep's redelivery of a captured message advances
the phase once."""
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from journal import capture, ledger, library, programdefs, programs, sweep, telegram, tick
from journal import audio as jaudio
from test_programs import Base


class ReenteredPhaseTest(Base):
    def test_a_phase_entered_again_going_forward_does_not_settle_on_its_old_answer(self):
        self.start()
        self.answer("phase one")
        self.assertTrue(self.settle())  # (1, 2)
        self.answer("phase two, first answer")
        self.now += timedelta(seconds=20)
        programs.back(self.cfg, None, self.now, self.log)  # /back within the settle window: phase 1 again
        self.assertEqual(self.where(), (1, 1))
        self.answer("phase one, again")
        self.assertTrue(self.settle())  # phase 2 is sent again
        self.assertEqual(self.where(), (1, 2))
        self.now += timedelta(seconds=20)
        programs.step(self.cfg, self.now, self.log)  # the tick: the old phase-2 answer is not an answer to this visit
        self.assertEqual(self.where(), (1, 2))
        self.answer("phase two, revised")
        self.assertTrue(self.settle())
        self.assertEqual(self.where(), (1, 3))

    def test_a_first_visit_is_unchanged(self):
        self.start()
        self.answer("phase one")
        self.assertTrue(self.settle())
        self.assertNotIn("reopened_at", self.the_run()["sessions"]["1"]["phases"]["2"])


class PendingAudioSummaryTest(Base):
    def test_a_summary_written_before_the_transcript_lands_is_filled_in_by_the_tick(self):
        self.cfg.audio["command"] = ["transcribe"]
        self.start("prep")
        rid = self.the_run()["id"]
        self.answer("who and when")
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.now += timedelta(minutes=1)
        (self.cfg.root).mkdir(parents=True, exist_ok=True)
        (self.cfg.root / "x.ogg").write_bytes(b"ogg")
        capture.capture_to(self.cfg, f"{rid}-s01-p2", "next-message", jaudio.TRANSCRIBING, self.now, "audio", "55",
                           "x.ogg", None, transcript="pending")
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")  # /next: the session completes at once
        self.assertEqual(self.the_run(rid)["status"], "completed")
        body = self.entry(1).read_text(encoding="utf-8")
        self.assertIn("_(skipped)_", body)
        programs.step(self.cfg, self.now, self.log)  # still pending: nothing to fill yet
        self.assertIn("_(skipped)_", self.entry(1).read_text(encoding="utf-8"))
        with mock.patch.object(jaudio, "transcribe", lambda cfg, path: ("my line, whatever happens", "")), \
                mock.patch.object(jaudio, "probe", lambda cfg, path: {}):
            tick.process_audio(self.cfg, self.now, self.log)
        programs.step(self.cfg, self.now + timedelta(minutes=1), self.log)
        body = self.entry(1).read_text(encoding="utf-8")
        summary = body.split("## Summary", 1)[1]
        self.assertIn("my line, whatever happens", summary)
        self.assertNotIn("skipped", summary.split("##", 1)[0])
        self.assertNotIn("summary_audio_pending", self.the_run(rid)["sessions"]["1"])

    def test_a_complete_session_with_nothing_pending_sets_no_flag(self):
        self.start("prep")
        rid = self.the_run()["id"]
        self.answer("who and when")
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.answer("fear")
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.assertNotIn("summary_audio_pending", self.the_run(rid)["sessions"]["1"])


class StepIsolationTest(Base):
    def test_one_runs_failed_send_does_not_stop_the_step_for_the_others(self):
        # fix-life first and prep after it, as before ONE-PROGRAM (the step walks the runs oldest first, so fix-life's
        # failing send comes before prep's expiry); each goes on a break so the next may start, and fix-life is then
        # taken up again, keeping its earlier start
        self.start("fix-life")
        fl = programs.current_run(self.cfg, "fix-life")["id"]
        programs.pause(self.cfg, fl, self.now, self.log)
        self.now += timedelta(minutes=1)
        self.start("prep")
        prep = programs.current_run(self.cfg, "conversation-prep")["id"]
        programs.pause(self.cfg, prep, self.now, self.log)  # (a situational run on a break still expires)
        self.start("fix-life")
        run = programs.find_run(self.cfg, fl)
        run["sessions"]["1"]["phases"]["1"]["send_due"] = True
        programs._save(self.cfg, run)
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, f"{fl}-s01-p1")["send_attempts"] = 2  # the next failure is the third: an alert
        later = self.now + timedelta(hours=200)  # (the situational run has expired by then)
        with mock.patch.object(telegram, "_call", side_effect=telegram.TelegramError("boom")):
            with self.assertRaises(tick.Alert) as caught:
                programs.step(self.cfg, later, self.log)
        self.assertIn(fl, str(caught.exception))
        self.assertEqual(programs.find_run(self.cfg, prep)["status"], "expired")


class SessionNameTest(Base):
    def test_a_session_name_cannot_leave_the_programs_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            prog = root / "practices" / "my-program"
            (prog / "sessions").mkdir(parents=True)
            outside = root / "elsewhere"
            outside.mkdir()
            (prog / "sessions" / "01-ok.md").write_text("---\nkind: session\nid: one\nnumber: 1\ntitle: One\n---\n## Phase 1: A\n**1.1** Q?\n",
                                                        encoding="utf-8")
            (outside / "secret.md").write_text("---\nkind: session\nid: secret\nnumber: 1\ntitle: Secret\n---\n## Phase 1: S\n**1.1** Q?\n",
                                               encoding="utf-8")
            (prog / "program.md").write_text("---\nkind: program\nid: my-program\ntitle: Mine\n"
                                             "sessions: ../../elsewhere/secret\n---\nIntro\n", encoding="utf-8")
            parsed, problems = programdefs.parse_program(prog)
            self.assertIsNone(parsed)
            self.assertTrue(any("plain file name" in p for p in problems), problems)
            (prog / "program.md").write_text("---\nkind: program\nid: my-program\ntitle: Mine\n"
                                             "sessions: 01-ok\n---\nIntro\n", encoding="utf-8")
            parsed, problems = programdefs.parse_program(prog)
            self.assertEqual(problems, [])
            self.assertEqual(parsed.sessions[0].title, "One")


class SweepRedeliveryTest(Base):
    def test_the_sweep_redelivering_a_captured_message_advances_the_phase_once(self):
        self.start()
        first = self.answer("my answer", mid="9001")
        msg = {"message_id": "9001", "quote": None, "text": "my answer", "source": "text", "audio_path": None}
        self.assertIsNone(sweep.reconcile(self.cfg, msg, self.now))  # already in the ledger: not captured twice
        self.assertEqual(len(self.prompt(first["prompt"]["id"])["captures"]), 1)
        self.assertTrue(self.settle())
        self.assertIsNone(sweep.reconcile(self.cfg, msg, self.now))  # redelivered after the advance
        self.assertFalse(programs.settle(self.cfg, self.the_run()["id"], self.now + timedelta(minutes=5), self.log))
        programs.step(self.cfg, self.now + timedelta(minutes=5), self.log)
        self.assertEqual(self.where(), (1, 2))
        self.assertEqual(len(self.bot.phase_sends()), 2)
