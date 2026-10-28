"""WP5, second review: each finding written as a failing test first. A pending voice answer beside a typed
confirmation, interrupts held back when the privacy pass is closed, a run that expires with interrupts to come, a
day at the end of the calendar, a phase answer typed after an interrupt, the quote verifier's corpus, and a typo in
the year."""
import dataclasses
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

from helpers import CHAT, make_cfg

from journal import audio, capture, context, entries, ledger, privacy, programs, schedule, telegram, tick, validate
import test_interrupts as ti
import test_programs as tp


class PendingVoiceTest(ti.Base):
    def add_pending_voice(self, rid):
        pid = programs._phase(self.the_run(), 10, 3)["prompt_id"]
        self.now += timedelta(minutes=1)
        with mock.patch.object(audio, "transcription_enabled", lambda cfg: True):
            capture.capture_to(self.cfg, pid, "web", audio.TRANSCRIBING, self.now, "audio", None,
                               "2026-10-20-fix-life-s10.ogg", None, transcript="pending")

    def test_a_typed_confirmation_beside_a_pending_voice_answer_is_read_on_next(self):
        rid = self.reach_schedule_phase()
        self.add_pending_voice(rid)
        self.answer("2026-11-02 07:00")
        blocks = programs._visit_blocks(self.cfg, self.the_run(), 10, 3)
        self.assertEqual([b for b, _ in blocks], ["2026-11-02 07:00"])
        with mock.patch.object(audio, "transcription_enabled", lambda cfg: True):
            self.assertTrue(programs.advance(self.cfg, rid, self.now, self.log, reason="next"))
        self.assertEqual(self.where(), (10, 4))
        self.assertEqual(len(self.items()), 8)
        self.assertFalse(any("Going on without interrupts" in s["text"] for s in self.bot.sends()))

    def test_a_confirmation_typed_before_the_voice_answer_is_read_too(self):
        rid = self.reach_schedule_phase()
        self.answer("2026-11-02 07:00")
        self.add_pending_voice(rid)
        blocks = programs._visit_blocks(self.cfg, self.the_run(), 10, 3)
        self.assertEqual([b for b, _ in blocks], ["2026-11-02 07:00"])

    def test_a_drifted_count_still_reads_nothing(self):
        rid = self.reach_schedule_phase()
        self.answer("2026-11-02 07:00")
        run = self.the_run()
        pid = programs._phase(run, 10, 3)["prompt_id"]
        with ledger.locked(self.cfg.ledger_path) as data:  # a capture the entry has no block for
            ledger.find(data, pid)["captures"].append({"at": self.now.isoformat(), "rule": "web", "source": "web"})
        self.assertEqual(programs._visit_blocks(self.cfg, self.the_run(), 10, 3), [])


class ClosedPrivacyTest(ti.Base):
    def setUp(self):
        super().setUp()
        self.rid = self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))

    def break_state_db(self):
        bad = Path(self.tmp.name) / "state.db"
        bad.write_bytes(b"not a sqlite database at all" * 100)
        self.cfg = dataclasses.replace(self.cfg, hermes={**self.cfg.hermes, "state_db": str(bad)})
        self.assertTrue(privacy.view(self.cfg, self.now).closed)

    def test_a_pass_that_cannot_run_holds_the_interrupts_and_the_loss_is_alerted(self):
        # fail closed: a send is a boundary the recomputed windows would honour, whatever the hook's own fallback
        # says (``privacy.window_open`` cannot see a private thread that went on past quiet_minutes)
        self.break_state_db()
        self.assertFalse(privacy.window_open(self.cfg, self.local("2026-11-02T09:00")))  # (the hook would say no)
        self.assertEqual(self.tick_at(self.local("2026-11-02T09:00")), [])
        self.assertEqual(self.sent_interrupts(), [])
        self.assertEqual(self.items()[0]["status"], "pending")
        self.assertTrue(any("held back" in l for l in self.log))
        alerts = self.tick_at(self.local("2026-11-02T11:01"))  # the window passed: the day is going, and he is told
        self.assertEqual(self.sent_interrupts(), [])
        self.assertEqual(self.items()[0]["status"], "missed")
        self.assertEqual(len(alerts), 1)
        self.assertIn("privacy pass could not run", alerts[0])

    def test_the_interrupts_go_again_when_the_pass_can_run(self):
        self.break_state_db()
        self.tick_at(self.local("2026-11-02T09:00"))
        self.assertEqual(self.sent_interrupts(), [])
        self.cfg = dataclasses.replace(self.cfg, hermes={**self.cfg.hermes, "state_db": None})
        self.assertFalse(privacy.view(self.cfg, self.now).closed)
        self.tick_at(self.local("2026-11-02T10:00"))  # still inside the grace window
        self.assertEqual(len(self.sent_interrupts()), 1)

    def test_an_interrupt_lost_to_a_hold_back_is_alerted_once(self):
        with mock.patch.object(programs, "_off_the_record", return_value=True):
            self.assertEqual(self.tick_at(self.local("2026-11-02T09:00")), [])  # (held, still inside its window)
            self.assertEqual(self.items()[0]["held_back"][:10], "2026-11-02")
            alerts = self.tick_at(self.local("2026-11-02T11:01"))  # the window passed
        self.assertEqual(self.items()[0]["status"], "missed")
        self.assertEqual(self.items()[0]["note"], "held back")
        self.assertEqual(len(alerts), 1)
        self.assertIn("interrupt 1/8", alerts[0])
        self.assertIn("held back", alerts[0])
        with mock.patch.object(programs, "_off_the_record", return_value=True):
            self.assertEqual(self.tick_at(self.local("2026-11-02T11:06")), [])  # (said once)

    def test_an_interrupt_that_was_never_held_back_is_missed_without_an_alert(self):
        self.assertEqual(self.tick_at(self.local("2026-11-02T11:01")), [])  # (the tick was away)
        self.assertEqual(self.items()[0]["status"], "missed")
        self.assertNotIn("note", self.items()[0])

    def test_the_tick_raises_the_alert(self):
        with mock.patch.object(programs, "_off_the_record", return_value=True):
            self.tick_at(self.local("2026-11-02T09:00"))
        self.now = self.local("2026-11-02T11:01")
        with self.assertRaises(tick.Alert):
            programs.send_due_interrupts(self.cfg, self.now, self.log, set())


SITUATIONAL = """---
kind: situational
id: day-plan
title: Day plan
expires_hours: 4
---
## Phase 1: Times
type: schedule
### Interrupts
- +2h: "What now?"
- +6h: "And now?"
### Question
**1.1** When do you wake?

## Phase 2: Close
**2.1** Anything else?

## Summary
- {1.1}
"""


class ExpiryTest(ti.Base):
    def test_an_interrupt_delivered_but_not_written_down_is_not_called_cancelled(self):
        rid = self.expired_run()
        run = self.the_run()
        first = run["scheduled"][0]
        with ledger.locked(self.cfg.ledger_path) as data:  # the send reached Telegram; the engine died before the run was saved
            data["prompts"].append({"id": f"{rid}-{first['id']}", "kind": "day-plan", "message_id": "901",
                                    "sent_at": self.now.isoformat(), "chat_id": CHAT})
        self.now += timedelta(hours=5)
        programs.step(self.cfg, self.now, self.log)
        run = programs.find_run(self.cfg, rid)
        self.assertEqual([i["status"] for i in run["scheduled"]], ["sent", "cancelled"])
        self.assertIn("the 1 interrupts already set are cancelled", self.bot.sends()[-1]["text"])


    def expired_run(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "day-plan.md").write_text(SITUATIONAL, encoding="utf-8")
        programs.start(self.cfg, "day-plan", self.now, self.log)
        self.answer("2026-11-02 07:00")
        self.now += timedelta(minutes=3)
        programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next")
        return self.the_run()["id"]

    def test_a_note_that_could_not_be_sent_is_tried_again_then_alerted(self):
        rid = self.expired_run()
        self.now += timedelta(hours=5)
        down = telegram.TelegramError("down")
        with mock.patch.object(telegram, "send_html", side_effect=down):
            programs.step(self.cfg, self.now, self.log)  # the run expires; the note fails
            self.assertEqual(programs.find_run(self.cfg, rid)["expiry_notice"]["attempts"], 1)
            self.now += timedelta(minutes=5)
            programs.step(self.cfg, self.now, self.log)
            self.assertEqual(programs.find_run(self.cfg, rid)["expiry_notice"]["attempts"], 2)
            self.now += timedelta(minutes=5)
            with self.assertRaises(tick.Alert) as ctx:
                programs.step(self.cfg, self.now, self.log)
        self.assertIn("could not tell him", str(ctx.exception))
        self.assertNotIn("expiry_notice", programs.find_run(self.cfg, rid))

    def test_a_note_does_not_need_the_program_file_to_load_again(self):
        rid = self.expired_run()
        self.now += timedelta(hours=5)
        with mock.patch.object(telegram, "send_html", side_effect=telegram.TelegramError("down")):
            programs.step(self.cfg, self.now, self.log)
            (Path(self.cfg.practices_dir) / "day-plan.md").unlink()  # the definition is gone before the next tick
            self.now += timedelta(minutes=5)
            programs.step(self.cfg, self.now, self.log)
            self.now += timedelta(minutes=5)
            with self.assertRaises(tick.Alert):  # (the attempts still run out and he is not left unsaid)
                programs.step(self.cfg, self.now, self.log)

    def test_a_note_sent_after_the_program_file_is_gone_still_names_it(self):
        rid = self.expired_run()
        self.now += timedelta(hours=5)
        with mock.patch.object(telegram, "send_html", side_effect=telegram.TelegramError("down")):
            programs.step(self.cfg, self.now, self.log)
        (Path(self.cfg.practices_dir) / "day-plan.md").unlink()
        self.now += timedelta(minutes=5)
        programs.step(self.cfg, self.now, self.log)
        self.assertIn("Day plan ended after 4 hours without activity", self.bot.sends()[-1]["text"])
        self.assertNotIn("expiry_notice", programs.find_run(self.cfg, rid))

    def test_a_note_that_failed_once_goes_out_on_the_next_tick(self):
        rid = self.expired_run()
        self.now += timedelta(hours=5)
        with mock.patch.object(telegram, "send_html", side_effect=telegram.TelegramError("down")):
            programs.step(self.cfg, self.now, self.log)
        before = len(self.bot.sends())
        self.now += timedelta(minutes=5)
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.sends()), before + 1)
        self.assertIn("the 2 interrupts already set are cancelled", self.bot.sends()[-1]["text"])
        self.assertNotIn("expiry_notice", programs.find_run(self.cfg, rid))
        self.now += timedelta(minutes=5)
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.sends()), before + 1)  # (said once)

    def test_a_run_that_expires_cancels_its_interrupts_and_says_so(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "day-plan.md").write_text(SITUATIONAL, encoding="utf-8")
        programs.start(self.cfg, "day-plan", self.now, self.log)
        self.answer("2026-11-02 07:00")
        self.now += timedelta(minutes=3)
        self.assertTrue(programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next"))
        run = self.the_run()
        self.assertEqual([i["status"] for i in run["scheduled"]], ["pending", "pending"])
        self.now += timedelta(hours=5)
        programs.step(self.cfg, self.now, self.log)
        run = programs.find_run(self.cfg, run["id"])
        self.assertEqual(run["status"], "expired")
        self.assertEqual([i["status"] for i in run["scheduled"]], ["cancelled", "cancelled"])
        self.assertTrue(any("2 scheduled interrupt(s) cancelled" in l for l in self.log))
        self.assertIn("the 2 interrupts already set are cancelled", self.bot.sends()[-1]["text"])
        self.assertEqual(programs.pending_interrupts(self.cfg), [])

    def test_a_new_start_that_closes_an_expired_run_cancels_its_interrupts_too(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "day-plan.md").write_text(SITUATIONAL, encoding="utf-8")
        programs.start(self.cfg, "day-plan", self.now, self.log)
        self.answer("2026-11-02 07:00")
        self.now += timedelta(minutes=3)
        programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next")
        old = self.the_run()["id"]
        self.now += timedelta(hours=5)
        programs.start(self.cfg, "day-plan", self.now, self.log)
        self.assertEqual({i["status"] for i in programs.find_run(self.cfg, old)["scheduled"]}, {"cancelled"})


class CalendarEdgeTest(ti.Base):
    TZ = "Atlantic/Cape_Verde"  # UTC-1: the last interrupt of 9999-12-31 is past the end of the calendar in UTC

    def test_a_day_at_the_end_of_the_calendar_is_refused_not_stored(self):
        self.reach_schedule_phase()
        self.assertFalse(self.confirm("9999-12-31 07:00"))
        self.assertEqual(self.items(), [])
        self.assertIn("more than 60 days away", self.bot.sends()[-1]["text"])
        self.tick_at(self.now)  # nothing to trip over

    def test_an_item_already_stored_beyond_the_calendar_does_not_block_other_runs(self):
        rid = self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        run = self.the_run()
        run["scheduled"].append({**run["scheduled"][0], "id": "s10-i99", "local": "9999-12-31T21:00"})
        programs._save(self.cfg, run)
        self.tick_at(self.local("2026-11-02T09:00"))
        self.assertEqual(len(self.sent_interrupts()), 1)
        status = {i["id"]: i["status"] for i in self.the_run()["scheduled"]}
        self.assertEqual(status["s10-i99"], "missed")
        self.assertEqual(schedule.oneoffs_due(self.cfg, self.now.astimezone(self.cfg.tz), set()), [])

    def test_a_day_just_inside_the_limit_is_accepted(self):
        self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-12-19 07:00"))  # the 20 Oct plus 60 days
        self.assertEqual(len(self.items()), 8)


class FarDayTest(ti.Base):
    def test_a_mistyped_year_is_asked_again_and_next_goes_on_without(self):
        self.reach_schedule_phase()
        self.assertFalse(self.confirm("2062-11-02 07:00"))
        self.assertEqual(self.items(), [])
        text = self.bot.sends()[-1]["text"]
        self.assertIn("That day is more than 60 days away", text)
        self.assertEqual(self.where(), (10, 3))
        rid = self.the_run()["id"]
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.assertTrue(any("Going on without interrupts: that day is more than 60 days away" in s["text"]
                            for s in self.bot.sends()))


class YearShownTest(ti.Base):
    def test_a_day_in_another_year_shows_its_year(self):
        self.now = self.local("2026-12-30T10:00")
        self.reach_schedule_phase()
        self.assertTrue(self.confirm("2027-01-11 07:00"))
        note = next(s["text"] for s in self.bot.sends() if "Interrupts set" in s["text"])
        self.assertIn("Monday 11 January 2027", note)
        self.assertIn("Mon 11 Jan 2027 09:00", programs.status_text(self.cfg, self.now))

    def test_a_day_in_this_year_has_none(self):
        self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        note = next(s["text"] for s in self.bot.sends() if "Interrupts set" in s["text"])
        self.assertIn("Monday 2 November", note)
        self.assertNotIn("2026", note.splitlines()[0])

    def test_the_status_shows_the_date_for_a_day_more_than_a_week_off(self):
        self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))  # 13 days after the 20th
        self.assertIn("Mon 2 Nov 09:00", programs.status_text(self.cfg, self.now))
        self.now = self.local("2026-10-30T10:00")  # within the week: the weekday alone
        self.assertIn("Mon 09:00", programs.status_text(self.cfg, self.now))


class OpenPhaseBeneathAnInterruptTest(ti.Base):
    def test_a_typed_phase_answer_after_an_interrupt_still_goes_to_the_open_phase(self):
        rid = self.reach_schedule_phase()
        self.assertTrue(self.confirm("07:00"))  # tomorrow: the 21st
        self.assertEqual(self.where(), (10, 4))
        self.now = self.local("2026-10-21T08:50")
        programs.start(self.cfg, "fix-life", self.now, self.log)  # phase 4 again
        self.tick_at(self.local("2026-10-21T09:00"))
        self.assertEqual(len(self.sent_interrupts()), 1)
        info = self.answer("my phase 4 answer, typed", after=timedelta(minutes=5))
        self.assertIsNotNone(info)
        self.assertEqual((info["prompt"]["session"], info["prompt"]["phase"]), (10, 4))
        self.assertTrue(self.settle())
        self.assertEqual(self.where(), (10, 5))

    def test_the_sweep_agrees(self):
        rid = self.reach_schedule_phase()
        self.assertTrue(self.confirm("07:00"))
        self.now = self.local("2026-10-21T08:50")
        programs.start(self.cfg, "fix-life", self.now, self.log)
        self.tick_at(self.local("2026-10-21T09:00"))
        data = ledger.snapshot(self.cfg.ledger_path)
        from journal import sweep
        got = sweep.visible_at(self.cfg, data, self.local("2026-10-21T09:05"))
        self.assertEqual((got["session"], got["phase"]), (10, 4))

    def test_an_interrupt_still_takes_a_ritual_out_of_the_first_message_after_it(self):
        from helpers import add_prompt
        self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        add_prompt(self.cfg, "evening", "2026-11-02", self.local("2026-11-02T08:00"), "300")
        self.tick_at(self.local("2026-11-02T09:00"))
        data = ledger.snapshot(self.cfg.ledger_path)
        prompt, _ = capture.match(data, self.cfg, CHAT, None, self.local("2026-11-02T09:05"))
        self.assertIsNone(prompt)  # (not the ritual, and not the phase beneath the ritual either)


class VerifierCorpusTest(ti.InsightsTest):
    test_the_compiled_document_quotes_his_interrupt_answers_and_says_what_did_not_go = None
    test_a_synthesis_is_appended_under_her_heading_and_the_compiled_part_is_untouched = None
    test_a_synthesis_that_fails_validation_leaves_the_compiled_file_intact = None

    def test_neither_her_synthesis_nor_the_engines_questions_pass_as_his_words(self):
        self.finish()
        self.synth_model(self.synthesis())
        programs.insights(self.cfg, self.rid, self.now, self.log)
        self.assertEqual(self.the_run()["synthesis"]["status"], "written")
        verify = context.make_verifier(self.cfg, db=None)
        hers = "that sentence is more exact than any of the big statements around it"
        question = "Am I moving toward the life I hate or the life I want?"
        # (a quote verifies against the file it cites (CONTEXT-PACK, review M1): every file is asked, so neither of
        # hers passes under any name, and his line passes under its own entry's)
        stems = [p.stem for p in self.cfg.root.glob("*.md")]
        for stem in stems:
            self.assertFalse(verify(f"journal:{stem}", validate.norm(hers)), stem)
            self.assertFalse(verify(f"journal:{stem}", validate.norm(question)), stem)
        line = validate.norm("Dodging the email I owe my sister.")
        own = []
        for stem in stems:
            before = len(verify.notes)
            if verify(f"journal:{stem}", line) and len(verify.notes) == before:
                own.append(stem)
        self.assertEqual(len(own), 1)  # his, in the session entry, with no note
        self.assertFalse(entries.authored_by_engine(self.cfg.root / f"{own[0]}.md"))
        other = next(stem for stem in stems if stem != own[0])
        self.assertTrue(verify(f"journal:{other}", line))  # under another name still his (cited-elsewhere) ...
        self.assertEqual(verify.notes[-1], f"cited-elsewhere: journal:{other} is in journal:{own[0]}")  # ... and said

    def test_a_marked_summary_is_not_his_words_but_a_heading_he_typed_is(self):
        body = ("---\ntype: journal\n---\n# t\n\n## Words\n\nmine alone.\n\n## Summary\n\n%% journal:summary %%\n\n"
                "engine prose here\n\n## Notes\n\nafter.\n")
        self.assertNotIn("engine prose", entries.without_summary(body))
        self.assertIn("after.", entries.without_summary(body))
        self.assertIn("mine alone.", entries.without_summary(body))
        typed = "## Summary\n\nhis own heading and words\n"
        self.assertEqual(entries.without_summary(typed), typed)

    def test_a_hand_written_file_with_an_analysis_kind_and_an_author_still_counts(self):
        # (not named as the engine names its files, or not authored by the engine: his)
        for name, fm in (("2026-10-02-thoughts.md", "kind: reflection\nauthor: me"),
                         ("2026-10-03-thoughts.md", "kind: week\nauthor: me"),
                         ("2026-10-04-mine-insights.md", "kind: insights\nauthor: me\nrun: x"),
                         ("2026-10-05-notes.md", "kind: insights\nauthor: engine\nrun: x"),
                         ("2026-10-06-mine-insights.md", "kind: insights\nauthor: engine")):
            (self.cfg.root / name).write_text(f"---\n{fm}\n---\nmy line in {name}\n", encoding="utf-8")
            self.assertFalse(entries.authored_by_engine(self.cfg.root / name), name)
        verify = context.make_verifier(self.cfg, db=None)
        self.assertTrue(verify("journal:2026-10-04-mine-insights", validate.norm("my line in 2026-10-04-mine-insights.md")))

    def test_the_engines_own_analysis_and_insights_files_are_hers(self):
        (self.cfg.root / "2026-10-02-reflection.md").write_text(
            "---\nkind: reflection\ntype: journal\nauthor: ada\n---\nher reading\n", encoding="utf-8")
        (self.cfg.root / "2026-10-02-fix-life-insights-2.md").write_text(
            "---\nkind: insights\nauthor: engine\nrun: r1\n---\nengine text\n", encoding="utf-8")
        self.assertTrue(entries.authored_by_engine(self.cfg.root / "2026-10-02-reflection.md"))
        self.assertTrue(entries.authored_by_engine(self.cfg.root / "2026-10-02-fix-life-insights-2.md"))

    def test_a_file_of_his_with_an_author_in_its_front_matter_still_counts(self):
        (self.cfg.root / "2026-10-01-mine.md").write_text(
            "---\nauthor: me\n---\nmy own line about the garden\n", encoding="utf-8")
        verify = context.make_verifier(self.cfg, db=None)
        self.assertTrue(verify("journal:2026-10-01-mine", validate.norm("my own line about the garden")))


if __name__ == "__main__":
    unittest.main()
