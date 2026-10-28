"""WP5 review findings that were still open after the merge, each pinned by a test that failed before its fix: one
run's crash in the programs step does not hold back session 10's interrupts, an old confirmation is not paired with a
new visit's time, a ritual due in the same tick as an interrupt stays the newest message, and a bare ``abandon`` does
not guess between an active run and a completed one with interrupts to come."""
from datetime import timedelta
from pathlib import Path
from unittest import mock

from helpers import CHAT

from journal import audio, capture, ledger, programs, telegram, tick
import test_interrupts as ti
import test_programs as tp

TWO = """---
kind: situational
id: myprep
title: My prep
advance: settle
---
## Phase 1: One
**1.1** First?

## Phase 2: Two
**2.1** Second?

## Summary
- {1.1} {2.1}
"""
ONE = """---
kind: situational
id: myprep
title: My prep
advance: settle
---
## Phase 1: One
**1.1** First?

## Summary
- {1.1}
"""


class Protocol(ti.Base):
    """Session 10 answered through phase 5 with the interrupts timed from ``wake`` on 2 November."""

    WAKE = "07:00"

    def setUp(self):
        super().setUp()
        self.rid = self.reach_schedule_phase()
        self.confirm(f"2026-11-02 {self.WAKE}")
        for p in (4, 5):
            self.answer(f"S10P{p}: words of mine for this phase")
            self.assertTrue(self.settle())


class StepIsolationTest(Protocol):
    def test_another_runs_crash_does_not_hold_back_the_interrupts(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "myprep.md").write_text(TWO, encoding="utf-8")
        self.now = self.local("2026-11-02T07:30")  # the protocol day: he runs a program of his own in the morning
        mine = programs.start(self.cfg, "myprep", self.now, self.log)["id"]
        self.answer("first answer")
        self.assertTrue(programs.settle(self.cfg, mine, self.now + timedelta(minutes=3), self.log))
        self.now += timedelta(minutes=3)
        self.answer("second answer, then he edits the file before it settles")
        (d / "myprep.md").write_text(ONE, encoding="utf-8")  # (phase 2 is gone: settling it raises IndexError)
        self.now = self.local("2026-11-02T09:00")
        with self.assertRaises(tick.Alert) as caught:  # still loud: the tick exits 1 and the scheduler alerts
            programs.step(self.cfg, self.now, self.log)
        self.assertIn(mine, str(caught.exception))  # (it names the run)
        self.assertEqual(len(self.sent_interrupts()), 1)
        self.assertIn("Interrupt 1/8", self.sent_interrupts()[0]["text"])
        self.assertTrue(any("step failed" in line for line in self.log))

    def test_a_crash_in_the_interrupts_does_not_hold_back_a_pending_synthesis(self):
        with mock.patch.object(programs, "send_interrupts", side_effect=OSError("disk")), \
                mock.patch.object(programs, "synthesize", return_value=[]) as synth:
            run = programs.find_run(self.cfg, self.rid)
            run["synthesis"] = {"status": "pending"}
            programs._save(self.cfg, run)
            with self.assertRaises(tick.Alert):
                programs.step(self.cfg, self.local("2026-11-02T09:00"), self.log)
        synth.assert_called_once()

    def sent_interrupts(self):
        return ti.Base.sent_interrupts(self)


class PairingTest(ti.Base):
    def test_an_old_confirmation_is_not_re_read_when_a_new_audio_answer_has_no_words_yet(self):
        rid = self.reach_schedule_phase()
        self.assertTrue(self.confirm("07:00"))  # sent on the 20th, so the day is the 21st
        self.assertEqual(self.items()[0]["local"], "2026-10-21T09:00")
        self.now += timedelta(hours=10)  # the next morning he goes back to the schedule phase
        programs.back(self.cfg, rid, self.now, self.log)
        pid = programs._phase(self.the_run(), 10, 3)["prompt_id"]
        self.now += timedelta(minutes=1)
        with mock.patch.object(audio, "transcription_enabled", lambda cfg: True):
            # an audio file: the capture is recorded, its block is still the placeholder (no words to read yet)
            capture.capture_to(self.cfg, pid, "next-message", audio.TRANSCRIBING, self.now, "audio", "777",
                               "2026-10-20-fix-life-s10.ogg", None, transcript="pending")
            self.assertEqual(programs._visit_blocks(self.cfg, self.the_run(), 10, 3), [])  # (can't tell: none read)
            programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        pending = [i for i in self.items() if i["status"] == "pending"]
        self.assertEqual(pending[0]["local"], "2026-10-21T09:00")  # not re-timed from the old "07:00" on the 22nd
        self.assertFalse(any("timed from 2026-10-22" in line for line in self.log))

    def test_one_block_per_capture_still_pairs_exactly(self):
        self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        blocks = programs._visit_blocks(self.cfg, self.the_run(), 10, 3)
        self.assertEqual([b for b, _ in blocks], ["2026-11-02 07:00"])


class SameTickTest(Protocol):
    WAKE = "09:00"  # interrupt 8 (+14h) falls at 23:00, the evening ritual's time

    def test_a_ritual_due_with_an_interrupt_stays_the_newest_message(self):
        self.now = self.local("2026-11-02T22:58")
        programs.send_interrupts(self.cfg, self.now, self.log)  # interrupt 7 (22:00) goes out
        self.now = self.local("2026-11-02T23:00")
        real = telegram._call

        def later(cfg, method, payload):  # (the real clock moves between two sends of one tick)
            out = real(cfg, method, payload)
            self.now += timedelta(seconds=2)
            return out
        with mock.patch.object(telegram, "_call", later):
            code, log = tick.run(self.cfg)
        snap = {p["id"]: p for p in ledger.snapshot(self.cfg.ledger_path)["prompts"]}
        evening, i8 = snap["2026-11-02-evening"], snap[f"{self.rid}-s10-i8"]
        self.assertTrue(evening.get("message_id") and i8.get("message_id"), log)
        self.assertGreater(evening["sent_at"], i8["sent_at"])  # the ritual went out last
        self.now += timedelta(minutes=5)
        self.mid += 1
        info = capture.capture(self.cfg, CHAT, None, "Today was the reset day. Here is my evening journal.",
                               self.now, "text", str(self.mid))
        self.assertIsNotNone(info)
        self.assertEqual((info["prompt"]["kind"], info["rule"]), ("evening", "next-message"))

    def test_the_tick_does_not_send_the_interrupts_twice_nor_burn_two_attempts(self):
        self.now = self.local("2026-11-02T11:00")  # interrupt 1 (+2h from the 09:00 wake time)
        with mock.patch.object(telegram, "_call", side_effect=telegram.TelegramError("down")):
            tick.run(self.cfg)
        rec = ledger.find(ledger.snapshot(self.cfg.ledger_path), f"{self.rid}-s10-i1") or {}
        self.assertEqual(rec.get("send_attempts"), 1)


class SettleInTheTickTest(ti.Base):
    def test_an_interrupt_timed_by_this_ticks_settle_goes_out_in_the_same_tick(self):
        rid = self.reach_schedule_phase()
        self.now = self.local("2026-10-20T16:30")
        self.answer("2026-10-20 14:20")  # interrupt 1 is 16:20; nothing has settled it yet
        self.now = self.local("2026-10-20T16:35")  # the tick: the settle is due, then the interrupt
        code, log = tick.run(self.cfg)
        self.assertEqual(len([p for p in self.bot.sends() if "Interrupt 1/8" in p["text"]]), 1, log)
        self.assertEqual(self.items()[0]["status"], "sent")
        self.assertEqual(self.prompt(f"{rid}-s10-i1")["kind"], "fix-life")

    def test_a_failed_send_is_counted_once_in_a_tick_whichever_pass_tries_it(self):
        rid = self.reach_schedule_phase()
        self.now = self.local("2026-10-20T16:30")
        self.answer("2026-10-20 14:20")
        self.now = self.local("2026-10-20T16:35")
        real = telegram._call

        def down(cfg, method, payload):
            if "Interrupt 1/8" in str(payload.get("text", "")):
                raise telegram.TelegramError("down")
            return real(cfg, method, payload)
        with mock.patch.object(telegram, "_call", down):
            tick.run(self.cfg)
        self.assertEqual(self.prompt(f"{rid}-s10-i1").get("send_attempts"), 1)


class AbandonTest(Protocol):
    def test_a_bare_abandon_with_two_candidates_names_both_and_ends_neither(self):
        self.finish_session_10()
        self.now = self.local("2026-11-01T20:00")
        programs.start(self.cfg, "conversation-prep", self.now, self.log)
        self.now += timedelta(minutes=5)
        with self.assertRaises(programs.ProgramError) as caught:
            programs.abandon(self.cfg, None, self.now, self.log)
        said = str(caught.exception)
        self.assertIn("conversation-prep", said)
        self.assertIn("fix-life", said)
        self.assertIn("journal program abandon <run>", said)
        self.assertEqual(programs.current_run(self.cfg)["program"], "conversation-prep")  # still active
        self.assertEqual({i["status"] for i in programs.find_run(self.cfg, self.rid)["scheduled"]}, {"pending"})
        # naming one works, either way
        programs.abandon(self.cfg, self.rid, self.now, self.log)
        self.assertEqual({i["status"] for i in programs.find_run(self.cfg, self.rid)["scheduled"]}, {"cancelled"})
        self.assertEqual(programs.current_run(self.cfg)["program"], "conversation-prep")

    def test_a_bare_abandon_with_one_candidate_still_ends_it(self):
        self.finish_session_10()
        programs.abandon(self.cfg, self.rid, self.now, self.log)  # (the interrupts are called off)
        self.now = self.local("2026-11-01T20:00")
        programs.start(self.cfg, "conversation-prep", self.now, self.log)
        programs.abandon(self.cfg, None, self.now, self.log)
        self.assertIsNone(programs.current_run(self.cfg))

    def finish_session_10(self):
        run = programs.find_run(self.cfg, self.rid)
        if run["status"] == "active":
            run.update(status="completed")
            programs._save(self.cfg, run)
