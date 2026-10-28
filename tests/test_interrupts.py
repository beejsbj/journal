"""WP5: session 10's interrupts and the insights export. The eight interrupts are timed only from a day and wake time
he confirms (``HH:MM`` alone is tomorrow), kept as wall-clock times so a DST change or a move to another timezone
leaves them where he put them, sent by the tick inside the grace window, and answered by replying to them: the reply
lands in the session-10 entry under its label. The insights document compiles deterministically (his interrupt
answers included) and may gain a validated synthesis; nothing that goes wrong there touches the compiled file."""
import dataclasses
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CHAT, add_prompt, make_cfg

from journal import agent, audio, validate, capture, entries, ledger, library, outage, programdefs, programs, schedule, sweep, telegram
from journal import tick
from journal.serve import document
import test_programs as tp
import test_programs_review as rv

UTC = timezone.utc


class Base(tp.Base):
    """A run at session 10, phase 3 (the schedule phase), the way Telegram would have driven it."""

    TZ = "Asia/Kolkata"

    def setUp(self):
        super().setUp()
        self.cfg = make_cfg(Path(self.tmp.name), names=tp.NAMES, timezone=self.TZ, **self.cfg_extra())

    def cfg_extra(self):
        return {}

    def reach_schedule_phase(self):
        self.start(session=10)
        for p in (1, 2):
            self.answer(f"S10P{p}: words of mine for this phase")
            self.assertTrue(self.settle())
        self.assertEqual(self.where(), (10, 3))
        return self.the_run()["id"]

    def confirm(self, text="2026-11-02 07:00"):
        """Answer the schedule phase and let the settle run."""
        self.answer(text)
        return self.settle()

    def items(self):
        return self.the_run()["scheduled"]

    def tick_at(self, when):
        self.now = when
        return programs.send_interrupts(self.cfg, self.now, self.log)

    def sent_interrupts(self):
        return [p for p in self.bot.sends() if re.search(r"Session 10/10 · Interrupt [0-9]/8", p["text"])]

    def local(self, text):
        """A wall-clock time in the configured zone as an instant."""
        return datetime.fromisoformat(text).replace(tzinfo=self.cfg.tz).astimezone(UTC)


class LongInterruptTest(unittest.TestCase):
    def test_a_very_long_question_still_fits_a_telegram_message(self):
        from journal import compose
        prog = library.load(make_cfg(Path(tempfile.mkdtemp()), names=tp.NAMES)).program("fix-life")
        html = compose.interrupt_html(prog, 10, 1, 8, "Why? " * 3000 + "end", tp.NAMES)
        self.assertLessEqual(len(html), compose.TELEGRAM_LIMIT)
        self.assertIn("Interrupt 1/8", html)
        self.assertTrue(html.endswith(f"<i>{compose.INTERRUPT_HINT}</i>"))
        short = compose.interrupt_html(prog, 10, 1, 8, "What am I avoiding?", tp.NAMES)
        self.assertIn("What am I avoiding?", short)
        self.assertNotIn("…", short)


class ConfirmationTest(Base):
    def test_parse(self):
        parse = programs.parse_confirmation
        from datetime import date
        self.assertEqual(parse("2026-11-02 07:00"), (date(2026, 11, 2), 7, 0))
        self.assertEqual(parse("  2026-11-02   23:59\n"), (date(2026, 11, 2), 23, 59))
        self.assertEqual(parse("07:00"), (None, 7, 0))
        self.assertEqual(parse("\n6:30"), None)  # one spelling: two digits
        for text in ("7am", "around seven", "2026-11-02", "07:00 sharp", "I wake at 07:00", "24:00", "07:60",
                     "2026-13-45 07:00", "", "2026-11-02 07:00 and 2026-11-03 07:00"):
            self.assertIsNone(parse(text), text)

    def test_the_schedule_phase_ends_with_what_to_send(self):
        self.reach_schedule_phase()
        text = self.bot.phase_sends()[-1]["text"]
        self.assertIn("YYYY-MM-DD HH:MM", text)
        self.assertIn("for example 2026-10-21 07:00", text)  # the day after the simulated one, local
        self.assertIn("I'll time the 8 interrupts from it", text)
        self.assertTrue(text.endswith(f"<i>{tp.compose.PHASE_HINT}</i>"))
        self.assertIn("**3.1**".replace("**3.1**", "<b>3.1</b>"), text)  # question 3.1 as authored, kept

    def test_a_day_and_time_time_the_eight_interrupts_at_the_stated_offsets(self):
        self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        self.assertEqual(self.where(), (10, 4))
        items = self.items()
        self.assertEqual([i["offset"] for i in items], ["+2h", "+4h", "+6h", "+8h", "+10h", "+12h", "+13h", "+14h"])
        self.assertEqual([i["local"] for i in items], [f"2026-11-02T{h}:00" for h in
                                                       ("09", "11", "13", "15", "17", "19", "20", "21")])
        self.assertEqual({i["status"] for i in items}, {"pending"})
        self.assertEqual(items[0]["at"], "2026-11-02T09:00:00+05:30")
        self.assertEqual(items[0]["id"], "s10-i1")
        self.assertEqual(items[0]["text"], "What am I avoiding right now by doing what I'm doing?")
        # the prose slot of the +13h question is filled from session 1 5.1 (here: not answered, so its name)
        self.assertIn("_protective identity_", items[6]["text"])
        note = self.bot.sends()[-2]["text"]  # the note, then phase 4
        self.assertIn("Interrupts set for Monday 2 November", note)
        self.assertIn("+2h · <b>09:00</b>", note)
        self.assertIn("+14h · <b>21:00</b>", note)
        self.assertIn("Phase 4/5", self.bot.sends()[-1]["text"])

    def test_a_time_alone_is_tomorrow(self):
        self.reach_schedule_phase()
        self.confirm("07:00")
        self.assertEqual(self.items()[0]["local"], "2026-10-21T09:00")  # T0 is the evening of the 20th, local

    def test_a_bare_time_is_the_day_after_he_wrote_it_not_after_the_settle(self):
        rid = self.reach_schedule_phase()
        self.now = self.local("2026-10-20T23:58")
        pid = programs.prompt_id(rid, 10, 3)  # (a reply to the phase: it is long past its window)
        self.answer("07:00", after=timedelta(minutes=1), reply_to=str(self.prompt(pid)["message_id"]))  # 23:59 local
        self.assertTrue(self.settle(after=timedelta(minutes=3)))  # the settle runs at 00:02 on the 21st
        self.assertEqual(self.items()[0]["local"], "2026-10-21T09:00")
        self.assertEqual(rid, self.the_run()["id"])

    def test_back_to_a_confirmed_phase_then_next_keeps_what_was_timed_and_says_so(self):
        rid = self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        programs.back(self.cfg, rid, self.now, self.log)
        self.assertEqual(self.where(), (10, 3))
        self.now += timedelta(minutes=1)
        before = len(self.bot.sends())
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        said = self.bot.sends()[before]["text"]
        self.assertIn("Keeping the 8 interrupts already set, from Monday 2 November 09:00", said)
        self.assertNotIn("without interrupts", said)
        self.assertEqual({i["status"] for i in self.items()}, {"pending"})

    def test_skip_after_back_to_a_confirmed_phase_cancels_what_was_timed(self):
        rid = self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        programs.back(self.cfg, rid, self.now, self.log)
        self.now += timedelta(minutes=1)
        before = len(self.bot.sends())
        programs.advance(self.cfg, rid, self.now, self.log, reason="skip")
        self.assertEqual(self.where(), (10, 4))
        self.assertEqual({i["status"] for i in self.items()}, {"cancelled"})
        self.assertIn("Going on without interrupts: the 8 already set are cancelled", self.bot.sends()[before]["text"])

    def test_the_cancellation_is_saved_before_the_note_says_so(self):
        rid = self.reach_schedule_phase()
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        programs.back(self.cfg, rid, self.now, self.log)
        seen = []

        def say(cfg, prog, markup, log, now):
            seen.append({i["status"] for i in programs.find_run(cfg, rid)["scheduled"]})  # what a death here leaves
            return True
        with mock.patch.object(programs, "_say", say):
            programs.advance(self.cfg, rid, self.now + timedelta(minutes=1), self.log, reason="skip")
        self.assertEqual(seen, [{"cancelled"}])

    def test_the_record_keeps_the_question_that_was_sent(self):
        from journal import compose
        rid = self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        run = self.the_run()
        item = run["scheduled"][0]
        item["text"] = "Why? " * 3000 + "the unseen tail"
        rec = programs._interrupt_record(self.cfg, self.prog(), run, item, self.local("2026-11-02T09:00"))
        self.assertLessEqual(len(rec["html"]), compose.TELEGRAM_LIMIT)
        self.assertTrue(rec["interrupt_text"].endswith("…"))
        self.assertNotIn("unseen tail", rec["interrupt_text"] + rec["plain_text"] + rec["html"])
        self.assertIn(rec["interrupt_text"], rec["plain_text"])

    def test_the_last_one_listed_need_not_be_the_latest_in_time(self):
        rid = self.reach_schedule_phase()
        real = programs.programdefs.interrupts
        with mock.patch.object(programs.programdefs, "interrupts", lambda ph: tuple(reversed(real(ph)))):
            self.answer("2026-10-20 07:00")  # +2h (09:00) has passed, but +14h (21:00) has not
            self.assertTrue(self.settle())
        self.assertEqual(self.where(), (10, 4))
        self.assertEqual(len(self.items()), 8)

    def test_the_latest_interrupt_is_the_latest_by_instant(self):
        # across the skipped hour, 02:30 (read as 07:30Z) is later than 03:00 (07:00Z): at 07:15Z it is still ahead
        self.cfg = make_cfg(Path(self.tmp.name), names=tp.NAMES, timezone="America/New_York")
        rid = self.reach_schedule_phase()
        self.answer("2027-03-14 00:00")
        plan = (programdefs.Interrupt(2.5, "Half past."), programdefs.Interrupt(3.0, "On the hour."))
        with mock.patch.object(programs.programdefs, "interrupts", lambda ph: plan):
            outcome = programs.confirm_schedule(self.cfg, self.prog(), self.the_run(), 10, 3,
                                                datetime(2027, 3, 14, 7, 15, tzinfo=UTC), self.log)
        self.assertEqual(outcome, ("scheduled", ""))
        self.assertEqual([i["local"] for i in self.the_run(rid)["scheduled"]], ["2027-03-14T02:30", "2027-03-14T03:00"])

    def test_an_offset_is_rounded_to_the_minute_it_is_stored_in(self):
        self.reach_schedule_phase()
        run = self.the_run()
        plan = (programdefs.Interrupt(0.999, "Almost an hour."),)
        with mock.patch.object(programs.programdefs, "interrupts", lambda ph: plan):
            items = programs.plan_interrupts(self.cfg, self.prog(), run, 10, 3, datetime(2026, 11, 2).date(), 7, 0)
        self.assertEqual(items[0]["local"], "2026-11-02T08:00")  # 59.94 minutes, not 59

    def test_a_late_old_block_is_not_taken_for_the_new_visits_reply(self):
        rid = self.reach_schedule_phase()
        pid = programs.prompt_id(rid, 10, 3)
        self.assertTrue(self.confirm("2026-11-02 07:00"))
        sent_before = self.now
        self.now += timedelta(minutes=10)
        programs.back(self.cfg, rid, self.now, self.log)
        self.assertEqual(self.where(), (10, 3))
        self.answer("hmm, not sure yet")  # free text in the new visit
        # the sweep finds a reply he sent before the /back and files it now, after the newer one
        capture.capture_to(self.cfg, pid, "direct-reply", "2026-11-09 07:00", sent_before, "text", "late-1")
        blocks = programs._visit_blocks(self.cfg, self.the_run(), 10, 3)
        self.assertEqual([b for b, _ in blocks], ["hmm, not sure yet"])
        self.assertFalse(self.settle())  # free text schedules nothing
        self.assertEqual(self.items()[0]["local"], "2026-11-02T09:00")  # and the old late line did not replace it

    def test_a_session_can_have_only_one_schedule_phase(self):
        raw = library.BUILTIN_PROGRAMS.joinpath("fix-life/sessions/10-one-day-reset.md").read_text(encoding="utf-8")
        head, _, tail = raw.partition("## Phase 4")
        twice = head + "## Phase 4" + tail.replace("type: task", 'type: schedule\n\n- +1h: "Another?"', 1)
        session, problems = programdefs.parse_session(twice)
        self.assertIsNone(session)
        self.assertTrue(any("only one schedule phase" in p for p in problems), problems)

    def test_a_day_at_the_end_of_the_calendar_is_not_a_confirmation(self):
        self.reach_schedule_phase()
        self.answer("9999-12-31 23:00")  # the interrupts would be past the last date there is
        self.assertFalse(self.settle())
        self.assertEqual(self.where(), (10, 3))
        self.assertEqual(self.the_run().get("scheduled"), [])

    def test_next_after_a_day_that_is_over_says_it_is_over(self):
        rid = self.reach_schedule_phase()
        self.answer("2026-10-01 07:00")
        self.now += timedelta(minutes=1)
        before = len(self.bot.sends())
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        said = self.bot.sends()[before]["text"]
        self.assertIn("Going on without interrupts: that day and time are already over", said)
        self.assertEqual(self.the_run()["scheduled"], [])

    def test_a_correction_inside_the_settle_replaces_the_first_try(self):
        self.reach_schedule_phase()
        self.answer("2026-11-02 07:00")
        self.answer("sorry, I mean 2026-11-03 08:30", after=timedelta(seconds=20))  # not a confirmation itself
        self.answer("2026-11-03 08:30", after=timedelta(seconds=20))
        self.assertTrue(self.settle())
        self.assertEqual(self.items()[0]["local"], "2026-11-03T10:30")
        self.assertEqual(len(self.items()), 8)

    def test_free_text_schedules_nothing_and_asks_once_more(self):
        self.reach_schedule_phase()
        before = len(self.bot.sends())
        self.answer("probably around seven, I'll see how I sleep")
        self.assertFalse(self.settle())  # the phase stays open
        self.assertEqual(self.where(), (10, 3))
        self.assertEqual(self.the_run().get("scheduled"), [])
        asked = self.bot.sends()[before:]
        self.assertEqual(len(asked), 1)
        self.assertIn("couldn't find a day and wake time", asked[0]["text"])
        self.assertIn("YYYY-MM-DD HH:MM", asked[0]["text"])
        # his words are kept as they are
        self.assertIn("probably around seven", self.entry().read_text(encoding="utf-8"))
        # a second unusable reply is not asked about again
        self.answer("hmm, 7ish")
        self.assertFalse(self.settle())
        self.assertEqual(len(self.bot.sends()), before + 1)
        # and the next real confirmation still works
        self.answer("2026-11-02 07:00")
        self.assertTrue(self.settle())
        self.assertEqual(len(self.items()), 8)

    def test_next_and_skip_go_on_without_interrupts(self):
        rid = self.reach_schedule_phase()
        self.answer("no idea yet")
        self.assertTrue(programs.advance(self.cfg, rid, self.now, self.log, reason="next"))
        self.assertEqual(self.where(), (10, 4))
        self.assertEqual(self.the_run().get("scheduled"), [])
        self.assertIn("Going on without interrupts", self.bot.sends()[-2]["text"])

    def test_skip_schedules_nothing(self):
        rid = self.reach_schedule_phase()
        self.assertTrue(programs.skip(self.cfg, rid, self.now, self.log))
        self.assertEqual(self.where(), (10, 4))
        self.assertEqual(self.the_run().get("scheduled"), [])

    def test_a_day_that_is_over_is_not_scheduled(self):
        self.reach_schedule_phase()
        before = len(self.bot.sends())
        self.answer("2026-10-01 07:00")
        self.assertFalse(self.settle())
        self.assertEqual(self.the_run().get("scheduled"), [])
        self.assertIn("already over", self.bot.sends()[before]["text"])

    def test_an_answer_before_a_back_is_not_this_visits_confirmation(self):
        rid = self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        self.assertTrue(programs.back(self.cfg, rid, self.now, self.log))
        self.assertEqual(self.where(), (10, 3))
        self.now += timedelta(minutes=1)
        self.answer("actually I haven't decided")
        self.assertFalse(self.settle())  # the old confirmation is not read again
        self.assertEqual(self.where(), (10, 3))
        self.answer("2026-11-03 07:00")
        self.assertTrue(self.settle())
        pending = [i for i in self.items() if i["status"] == "pending"]
        self.assertEqual(len(pending), 8)  # the first eight were replaced, not added to
        self.assertEqual(pending[0]["local"], "2026-11-03T09:00")

    def test_a_schedule_phase_must_list_its_interrupts(self):
        raw = library.BUILTIN_PROGRAMS.joinpath("fix-life/sessions/10-one-day-reset.md").read_text(encoding="utf-8")
        bare = re.sub(r"(?m)^- \+\d+h: .*\n", "", raw)
        session, problems = programdefs.parse_session(bare)
        self.assertIsNone(session)
        self.assertTrue(any("schedule phase but lists no interrupts" in p for p in problems), problems)
        ok, none = programdefs.parse_session(raw)
        self.assertEqual(none, [])
        self.assertEqual(len(programdefs.interrupts(ok.phase(3))), 8)
        self.assertEqual(programdefs.interrupts(ok.phase(2)), ())  # only a schedule phase has interrupts
        empty = re.sub(r"(?m)^- \+14h: .*$", '- +14h: ""', raw)  # an interrupt that asks nothing is not one
        blank, problems = programdefs.parse_session(empty)
        self.assertEqual(len(programdefs.interrupts(blank.phase(3))), 7)
        long_title, problems = programdefs.parse_session(re.sub(r"(?m)^title: .*$", "title: " + "T" * 500, raw, count=1))
        self.assertTrue(any("title is longer than" in p for p in problems), problems)
        wordy = re.sub(r"(?m)^- \+14h: .*$", "- +14h: " + "Why? " * 400, raw)
        too_long, problems = programdefs.parse_session(wordy)
        self.assertTrue(any("question is longer than" in p for p in problems), problems)
        huge = raw.replace("- +14h:", "- +" + "9" * 400 + "h:")  # not an interrupt of a day: nothing may overflow on it
        odd, problems = programdefs.parse_session(huge)
        self.assertEqual(problems, [])
        self.assertEqual(len(programdefs.interrupts(odd.phase(3))), 7)


class ClockTest(Base):
    """Wall-clock times, read in the zone that applies when they come due."""

    def due(self, instant):
        return [o.item["id"] for o in schedule.oneoffs_due(self.cfg, instant.astimezone(self.cfg.tz), set())]

    def test_the_grace_window(self):
        self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")  # +2h = 09:00 IST = 03:30Z
        first = self.local("2026-11-02T09:00")
        self.assertEqual(self.due(first - timedelta(minutes=1)), [])
        self.assertEqual(self.due(first), ["s10-i1"])
        self.assertEqual(self.due(first + timedelta(hours=1, minutes=59)), ["s10-i1"])
        self.assertEqual(self.due(first + timedelta(hours=2)), ["s10-i2"])  # (windows.grace_hours is 2: +2h is over)
        self.assertEqual(self.due(self.local("2026-11-02T11:00")), ["s10-i2"])

    def test_wall_clock_arithmetic_across_a_dst_change(self):
        self.TZ = "America/New_York"
        self.cfg = make_cfg(Path(self.tmp.name), names=tp.NAMES, timezone="America/New_York")
        self.reach_schedule_phase()
        self.confirm("2026-11-01 00:00")  # the night the clocks go back (02:00 EDT -> 01:00 EST)
        locals_ = [i["local"] for i in self.items()]
        self.assertEqual(locals_, [f"2026-11-01T{h}:00" for h in ("02", "04", "06", "08", "10", "12", "13", "14")])
        # each is at its stated clock time, not 2, 4 … elapsed hours after midnight (which would be an hour early)
        ny = ZoneInfo("America/New_York")
        at = {i["offset"]: schedule.wall_clock(self.cfg, i["local"]) for i in self.items()}
        self.assertEqual(at["+2h"].astimezone(UTC), datetime(2026, 11, 1, 7, 0, tzinfo=UTC))   # 02:00 EST
        self.assertEqual(at["+14h"].astimezone(UTC), datetime(2026, 11, 1, 19, 0, tzinfo=UTC))  # 14:00 EST
        self.assertEqual(self.due(datetime(2026, 11, 1, 9, 0, tzinfo=UTC)), ["s10-i2"])  # 04:00 EST
        self.assertEqual(self.due(datetime(2026, 11, 1, 8, 0, tzinfo=UTC)), ["s10-i1"])  # 03:00 EST: +4h not yet
        self.assertEqual(at["+4h"].astimezone(ny).strftime("%H:%M %Z"), "04:00 EST")

    def test_one_offs_are_ordered_by_instant_not_by_wall_clock(self):
        # the hour that spring forward skips: 02:30 is read as 07:30Z, so it comes after 03:00 (07:00Z)
        self.cfg = make_cfg(Path(self.tmp.name), names=tp.NAMES, timezone="America/New_York")
        self.now = datetime(2027, 3, 12, 15, 0, tzinfo=UTC)  # (a protocol day is at most ``MAX_DAYS_AHEAD`` off)
        self.reach_schedule_phase()
        self.confirm("2027-03-14 00:00")
        run = self.the_run()
        for item, local in zip(run["scheduled"], ("2027-03-14T02:30", "2027-03-14T03:00")):
            item["local"] = local
        run["scheduled"] = run["scheduled"][:2]
        programs._save(self.cfg, run)
        now = datetime(2027, 3, 14, 7, 45, tzinfo=UTC)
        got = [o.item["local"] for o in schedule.oneoffs_due(self.cfg, now.astimezone(self.cfg.tz), set())]
        self.assertEqual(got, ["2027-03-14T03:00", "2027-03-14T02:30"])
        self.assertEqual([i["id"] for i in programs.pending_interrupts(self.cfg)], ["s10-i2", "s10-i1"])

    def test_a_time_the_clocks_skip_is_read_with_the_offset_from_before(self):
        cfg = make_cfg(Path(self.tmp.name), names=tp.NAMES, timezone="America/New_York")
        skipped = schedule.wall_clock(cfg, "2027-03-14T02:30")  # 02:00 -> 03:00 that night
        self.assertEqual(skipped.astimezone(UTC), datetime(2027, 3, 14, 7, 30, tzinfo=UTC))  # 03:30 EDT

    def test_a_move_to_another_timezone_leaves_the_times_on_the_stated_clock(self):
        self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        london = dataclasses.replace(self.cfg, timezone="Europe/London")  # journal_set_location rewrote the zone
        nine = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)  # 09:00 in London in November
        got = [o.item["id"] for o in schedule.oneoffs_due(london, nine.astimezone(london.tz), set())]
        self.assertEqual(got, ["s10-i1"])
        ist = datetime(2026, 11, 2, 3, 30, tzinfo=UTC)  # 09:00 in Kolkata: no longer his clock
        self.assertEqual([o.item["id"] for o in schedule.oneoffs_due(london, ist.astimezone(london.tz), set())], [])
        self.assertEqual(programs.pending_interrupts(london)[0]["at"], "2026-11-02T09:00:00+00:00")

    def test_due_lists_one_offs_with_the_practices(self):
        self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        got = schedule.due(self.cfg, self.local("2026-11-02T09:00").astimezone(self.cfg.tz), set())
        self.assertIn((f"{self.the_run()['id']}-s10-i1", self.local("2026-11-02T09:00").astimezone(self.cfg.tz)), got)


class SendTest(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        # phases 4 and 5 are answered the same evening, the session completes, the protocol day is the 2nd
        for p in (4, 5):
            self.answer(f"S10P{p}: words of mine for this phase")
            self.assertTrue(self.settle())

    def test_the_tick_sends_each_interrupt_as_a_ledger_prompt(self):
        self.assertEqual(self.tick_at(self.local("2026-11-02T08:59")), [])
        self.assertEqual(self.sent_interrupts(), [])
        self.tick_at(self.local("2026-11-02T13:00"))
        sent = self.sent_interrupts()
        self.assertEqual(len(sent), 1)
        self.assertIn("Interrupt 3/8", sent[0]["text"])
        self.assertIn("Am I moving toward the life I hate or the life I want?", sent[0]["text"])
        self.assertNotIn("reply_markup", sent[0])  # no keyboard: it is not a phase
        rec = self.prompt(f"{self.rid}-s10-i3")
        self.assertEqual((rec["kind"], rec["program"], rec["run"], rec["session"], rec["interrupt"]),
                         ("fix-life", "fix-life", self.rid, 10, 3))
        self.assertEqual(rec["stem"], "2026-10-20-fix-life-s10")
        self.assertEqual(rec["interrupt_offset"], "+6h")
        self.assertNotIn("phase", rec)
        self.assertEqual(self.the_run()["scheduled"][2]["status"], "sent")
        self.assertEqual(self.the_run()["scheduled"][2]["prompt_id"], f"{self.rid}-s10-i3")
        # the same tick again, and the next one: nothing twice
        self.tick_at(self.local("2026-11-02T13:05"))
        self.assertEqual(len(self.sent_interrupts()), 1)

    def test_the_tick_step_sends_them(self):
        self.now = self.local("2026-11-02T09:00")
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.sent_interrupts()), 1)

    def test_an_interrupt_past_the_grace_window_is_missed_not_sent_late(self):
        self.tick_at(self.local("2026-11-02T11:30") + timedelta(minutes=45))  # 12:15: +2h is 3 h gone, +4h 1 h15
        self.assertEqual(len(self.sent_interrupts()), 1)
        self.assertIn("Interrupt 2/8", self.sent_interrupts()[0]["text"])
        status = {i["offset"]: i["status"] for i in self.the_run()["scheduled"]}
        self.assertEqual(status["+2h"], "missed")
        self.assertEqual(status["+4h"], "sent")
        self.assertEqual(status["+6h"], "pending")

    def test_only_the_newest_of_several_due_at_once_is_sent(self):
        self.tick_at(self.local("2026-11-02T20:30"))  # +12h (19:00) and +13h (20:00) are both inside two hours
        self.assertEqual(len(self.sent_interrupts()), 1)
        self.assertIn("Interrupt 7/8", self.sent_interrupts()[0]["text"])
        status = {i["offset"]: (i["status"], i.get("note")) for i in self.the_run()["scheduled"]}
        self.assertEqual(status["+12h"], ("missed", "superseded"))

    def test_a_failing_send_is_tried_again_then_alerts_once(self):
        with mock.patch.object(telegram, "_call", side_effect=telegram.TelegramError("chat not found")):
            self.assertEqual(self.tick_at(self.local("2026-11-02T09:00")), [])
            self.assertEqual(self.tick_at(self.local("2026-11-02T09:05")), [])
            alerts = self.tick_at(self.local("2026-11-02T09:10"))
            self.assertEqual(len(alerts), 1)
            self.assertIn("was not delivered after 3 attempts", alerts[0])
            self.assertEqual(self.tick_at(self.local("2026-11-02T09:15")), [])  # given up
        self.assertEqual(self.the_run()["scheduled"][0]["status"], "failed")
        self.assertEqual(self.sent_interrupts(), [])
        self.tick_at(self.local("2026-11-02T11:00"))  # the next interrupt is its own
        self.assertEqual(len(self.sent_interrupts()), 1)

    def test_a_send_that_was_not_written_down_is_not_repeated(self):
        self.tick_at(self.local("2026-11-02T09:00"))
        run = self.the_run()
        run["scheduled"][0].update(status="pending", prompt_id=None)  # the engine died before saving the run
        programs._save(self.cfg, run)
        self.tick_at(self.local("2026-11-02T09:05"))
        self.assertEqual(len(self.sent_interrupts()), 1)
        self.assertEqual(self.the_run()["scheduled"][0]["status"], "sent")

    def test_a_break_does_not_hold_them_and_abandoning_cancels_them(self):
        # (this run is still active: sessions 1-9 were skipped over; a finished run is in ``CompletedRunTest``)
        programs.pause(self.cfg, self.rid, self.now, self.log)
        self.tick_at(self.local("2026-11-02T09:00"))
        self.assertEqual(len(self.sent_interrupts()), 1)  # (an interrupt is not an advance)
        programs.abandon(self.cfg, self.rid, self.now, self.log)
        self.assertEqual({i["status"] for i in self.the_run()["scheduled"]}, {"sent", "cancelled"})
        self.tick_at(self.local("2026-11-02T11:00"))
        self.assertEqual(len(self.sent_interrupts()), 1)  # nothing more after the abandon

    def test_an_interrupt_waits_while_a_bare_x_holds_the_chat_off_the_record(self):
        # a send ends a bare /x window, so his next private message would be filed: it waits, then goes or is missed
        with mock.patch.object(programs, "_off_the_record", return_value=True):
            self.tick_at(self.local("2026-11-02T09:00"))
            self.tick_at(self.local("2026-11-02T09:30"))
        self.assertEqual(self.sent_interrupts(), [])
        self.assertEqual(self.the_run()["scheduled"][0]["status"], "pending")
        self.assertTrue(any("off the record" in l for l in self.log))
        with mock.patch.object(programs, "_off_the_record", return_value=False):
            self.tick_at(self.local("2026-11-02T10:00"))  # the window closed, inside the grace period
        self.assertEqual(len(self.sent_interrupts()), 1)
        self.assertIn("Interrupt 1/8", self.sent_interrupts()[0]["text"])

    def test_an_interrupt_held_back_past_the_grace_window_is_missed(self):
        with mock.patch.object(programs, "_off_the_record", return_value=True):
            self.tick_at(self.local("2026-11-02T09:00"))
            self.tick_at(self.local("2026-11-02T11:01"))  # (+2h is 2 h 1 min gone; +4h is due and also held)
        self.assertEqual(self.sent_interrupts(), [])
        self.assertEqual(self.the_run()["scheduled"][0]["status"], "missed")
        self.assertEqual(self.the_run()["scheduled"][1]["status"], "pending")

    def test_the_superseded_ones_are_saved_before_the_newest_goes_out(self):
        class Died(BaseException):
            pass
        real = tick.send_prompt

        def send_then_die(*a, **k):
            real(*a, **k)
            raise Died()
        with mock.patch.object(tick, "send_prompt", send_then_die), self.assertRaises(Died):
            programs.send_interrupts(self.cfg, self.local("2026-11-02T20:30"), self.log)
        # (it died after the send, before the run was saved again): the older one is already out of the way
        status = {i["offset"]: i["status"] for i in self.the_run()["scheduled"]}
        self.assertEqual(status["+12h"], "missed")
        self.tick_at(self.local("2026-11-02T20:35"))  # the next tick finds the delivered one and sends nothing more
        self.assertEqual(len(self.sent_interrupts()), 1)

    def test_the_status_lists_what_is_still_to_come(self):
        self.now = self.local("2026-11-02T12:00")
        self.tick_at(self.now)
        text = programs.status_text(self.cfg, self.now)
        self.assertIn("Interrupts to come (6): 13:00, 15:00, 17:00, 19:00, 20:00, 21:00.", text)
        self.assertEqual(len(programs.status(self.cfg, self.now)["scheduled"]), 6)

    def test_an_interrupt_folds_to_its_header(self):
        self.tick_at(self.local("2026-11-02T13:00"))
        later = self.local("2026-11-03T13:30")  # a day on: its window has passed
        self.now = later
        tick.fold(self.cfg, later, self.log)
        edits = [p for m, p in self.bot.calls if m == "editMessageText" and "Interrupt" in p["text"]]
        self.assertEqual(len(edits), 1)
        self.assertIn("Interrupt 3/8 · open — reply anytime", edits[0]["text"])
        texts = sweep.prompt_texts(self.cfg, self.prompt(f"{self.rid}-s10-i3"))
        self.assertTrue(any("interrupt 3 8 open reply anytime" in t for t in texts))


class ReplyTest(Base):
    def setUp(self):
        super().setUp()
        self.rid = self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        for p in (4, 5):
            self.answer(f"S10P{p}: words of mine for this phase")
            self.assertTrue(self.settle())
        self.tick_at(self.local("2026-11-02T13:00"))
        self.mid_of_interrupt = str(self.prompt(f"{self.rid}-s10-i3")["message_id"])

    def reply(self, text, to=None, after=timedelta(minutes=4)):
        self.now += after
        self.mid += 1
        return capture.capture(self.cfg, CHAT, to, text, self.now, "text", str(self.mid))

    def test_a_reply_lands_in_the_session_entry_under_its_label(self):
        info = self.reply("Dodging the email I owe my sister.", to=self.mid_of_interrupt)
        self.assertEqual(info["rule"], "direct-reply")
        self.assertEqual(info["prompt"]["interrupt"], 3)
        path = Path(info["path"])
        self.assertEqual(path, self.entry(10))
        body = path.read_text(encoding="utf-8")
        self.assertIn("%% journal interrupt:3 %%\n### ", body)
        self.assertIn("Dodging the email I owe my sister.", body)
        self.assertIn("> [!question]- Interrupt 3/8 (+6h) · Mon 2 Nov 13:00", body)  # the question, folded under Prompt
        self.assertIn("> Am I moving toward the life I hate or the life I want?", body)
        self.assertEqual(entries.interrupt_answers(body), {3: "Dodging the email I owe my sister."})
        # it is not one of his phase answers, and the phase it follows does not swallow it
        self.assertNotIn("Dodging", entries.phase_answers(body)[5])
        self.assertEqual(entries.phase_answers(body)[5], "S10P5: words of mine for this phase")
        self.assertNotIn("Dodging", entries.his_words(body).split("S10P5")[0])
        # and the engine's key line is not his words
        self.assertNotIn("journal interrupt", entries.his_words(body))
        self.assertTrue(self.prompt(f"{self.rid}-s10-i3")["captures"])

    def test_a_typed_answer_is_not_filed_as_an_older_rituals_answer(self):
        # a morning prompt, sent at 12:30 and unanswered, is still visible when interrupt 3 goes out at 13:00
        add_prompt(self.cfg, kind="morning", local_date="2026-11-02", sent=self.local("2026-11-02T12:30"),
                   message_id="9001")
        self.tick_at(self.local("2026-11-02T13:00"))
        info = self.reply("Avoiding the call to my sister, honestly.", after=timedelta(minutes=3))
        self.assertIsNone(info)  # ordinary chat: an interrupt is answered by replying to it
        self.assertFalse((self.cfg.root / "2026-11-02-morning.md").exists())
        self.assertIsNone(self.prompt("2026-11-02-morning").get("captures"))
        # a ritual sent after the interrupt is the newest again
        add_prompt(self.cfg, kind="evening", local_date="2026-11-02", sent=self.local("2026-11-02T13:20"),
                   message_id="9002")
        self.assertEqual(self.reply("long day", after=timedelta(minutes=30))["prompt"]["kind"], "evening")

    def test_the_sweep_files_it_the_same_way(self):
        add_prompt(self.cfg, kind="morning", local_date="2026-11-02", sent=self.local("2026-11-02T12:30"),
                   message_id="9001")
        self.tick_at(self.local("2026-11-02T13:00"))
        with ledger.locked(self.cfg.ledger_path) as data:
            seen = sweep.visible_at(self.cfg, data, self.local("2026-11-02T13:03"))
            before = sweep.visible_at(self.cfg, data, self.local("2026-11-02T12:45"))
        self.assertIsNone(seen)
        self.assertEqual(before["kind"], "morning")

    def test_two_answers_to_the_same_interrupt_join_under_its_key(self):
        self.reply("First thought.", to=self.mid_of_interrupt)
        self.reply("And a second one.", to=self.mid_of_interrupt)
        got = entries.interrupt_answers(self.entry(10).read_text(encoding="utf-8"))
        self.assertEqual(got, {3: "First thought.\n\nAnd a second one."})

    def test_a_message_that_is_not_a_reply_is_not_an_answer(self):
        self.assertIsNone(self.reply("unrelated, I am making tea"))
        self.assertNotIn("making tea", self.entry(10).read_text(encoding="utf-8"))

    def test_a_reply_to_an_older_interrupt_goes_to_its_own_key(self):
        self.tick_at(self.local("2026-11-02T15:00"))
        later = str(self.prompt(f"{self.rid}-s10-i4")["message_id"])
        self.reply("about the fourth", to=later)
        self.reply("about the third, late", to=self.mid_of_interrupt)
        got = entries.interrupt_answers(self.entry(10).read_text(encoding="utf-8"))
        self.assertEqual(got, {4: "about the fourth", 3: "about the third, late"})

    def test_the_web_apps_document_reads_it_as_a_block_of_his_with_no_phase(self):
        self.reply("a line for the web app", to=self.mid_of_interrupt)
        doc = document.document(self.cfg, self.entry(10))
        mine = [b for b in doc["blocks"] if b["kind"] == "his" and "a line for the web app" in b["text"]]
        self.assertEqual(len(mine), 1)
        self.assertIsNone(mine[0]["phase"])
        self.assertNotIn("journal interrupt", mine[0]["text"])
        phase5 = [b for b in doc["blocks"] if b["phase"] == 5]
        self.assertEqual([b["text"] for b in phase5], ["S10P5: words of mine for this phase"])

    def test_the_companion_is_told_what_this_is(self):
        info = capture.planned_info(self.cfg, self.prompt(f"{self.rid}-s10-i3"))
        note = programs.reply_note(self.cfg, info)
        self.assertTrue(note.startswith("[Journal reply — interrupt reply:"))
        self.assertIn("interrupt 3/8 (+6h)", note)
        self.assertIn("Am I moving toward the life I hate or the life I want?", note)
        self.assertTrue(note.rstrip().endswith("annotations are written automatically.]"))


class HookTest(rv.HookBase):
    def test_an_interrupt_reply_is_noted_and_moves_nothing(self):
        self.start(session=10)
        for p in (1, 2):
            self.answer(f"S10P{p}: words")
            self.settle()
        self.answer("2026-11-02 07:00")
        self.settle()
        for p in (4, 5):
            self.answer(f"S10P{p}: words")
            self.settle()
        programs.send_interrupts(self.cfg, datetime(2026, 11, 2, 3, 30, tzinfo=UTC), self.log)
        rid = self.the_run()["id"]
        mid = str(self.prompt(f"{rid}-s10-i1")["message_id"])
        self.now = datetime(2026, 11, 2, 3, 40, tzinfo=UTC)
        self.spawned.clear()
        out = self.msg("The honest answer: my phone.", reply_to=mid)
        self.assertEqual(out["action"], "rewrite")
        self.assertIn("[Journal reply — interrupt reply:", out["text"])
        self.assertEqual(self.spawned, [])  # no settle: it is not the run's open phase


class CompletedRunTest(tp.FullRunTest):
    """Session 10 is the last session: by the protocol day the run is ``completed``, not active. The interrupts still
    go out, and ending them must work then (the real case; ``SendTest``'s run is still active)."""

    def test_ten_sessions_with_no_model(self):
        super().test_ten_sessions_with_no_model()
        rid = self.the_run()["id"]
        self.assertEqual(self.the_run()["status"], "completed")
        self.assertEqual(len([i for i in self.the_run()["scheduled"] if i["status"] == "pending"]), 8)
        self.assertIn("Interrupts to come (8)", programs.status_text(self.cfg, self.now))

        programs.abandon(self.cfg, rid, self.now, self.log)  # by id
        run = self.the_run()
        self.assertEqual(run["status"], "completed")  # it did finish; only its interrupts are called off
        self.assertEqual({i["status"] for i in run["scheduled"]}, {"cancelled"})
        self.assertNotIn("Interrupts to come", programs.status_text(self.cfg, self.now))
        with self.assertRaises(programs.ProgramError):  # nothing left to end
            programs.abandon(self.cfg, rid, self.now, self.log)
        with self.assertRaises(programs.ProgramError):
            programs.abandon(self.cfg, None, self.now, self.log)

        for i in run["scheduled"]:  # again, without naming the run
            i["status"] = "pending"
        programs._save(self.cfg, run)
        programs.abandon(self.cfg, None, self.now, self.log)
        self.assertEqual({i["status"] for i in self.the_run()["scheduled"]}, {"cancelled"})
        before = len(self.bot.sends())
        programs.send_interrupts(self.cfg, schedule.wall_clock(self.cfg, "2026-10-27T09:00"), self.log)
        self.assertEqual(len(self.bot.sends()), before)  # none goes out


class InsightsTest(Base):
    """The export: his interrupt answers in it, and the synthesis."""

    def cfg_extra(self):
        return {"programs": {"synthesis": True}}

    def finish(self):
        self.rid = self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        for p in (4, 5):
            self.answer(f"S10P{p}: words of mine for this phase")
            self.assertTrue(self.settle())
        self.tick_at(self.local("2026-11-02T13:00"))
        self.now = self.local("2026-11-02T13:10")
        self.mid += 1
        capture.capture(self.cfg, CHAT, str(self.prompt(f"{self.rid}-s10-i3")["message_id"]),
                        "Dodging the email I owe my sister.", self.now, "text", str(self.mid))
        self.now = self.local("2026-11-02T22:00")

    def doc(self):
        return (self.cfg.root / f"{self.the_run()['insights_entry']}.md").read_text(encoding="utf-8")

    def test_the_compiled_document_quotes_his_interrupt_answers_and_says_what_did_not_go(self):
        self.finish()
        self.tick_at(self.local("2026-11-02T22:00"))
        path = programs.insights(self.cfg, self.rid, self.now, self.log)
        text = path.read_text(encoding="utf-8")
        self.assertEqual(entries.front_matter(text)["kind"], "insights")
        self.assertIn("### Sam's answers to the interrupts", text)
        self.assertIn("**Interrupt 3/8 (+6h) · Mon 2 Nov 13:00** — Am I moving toward the life I hate or the life I "
                      "want?\n\nDodging the email I owe my sister.", text)
        self.assertIn("**Interrupt 1/8 (+2h) · Mon 2 Nov 09:00** — What am I avoiding right now by doing what I'm "
                      "doing?\n\n_(not sent: missed)_", text)
        self.assertIn("**Interrupt 4/8 (+8h) · Mon 2 Nov 15:00** — What's the most important thing I'm pretending "
                      "isn't important?\n\n_(not sent: missed)_", text)  # (the tick at 22:00 found it long gone)
        self.assertIn("**Interrupt 8/8 (+14h) · Mon 2 Nov 21:00** — Where in my life am I trading aliveness for "
                      "safety?\n\n_(no answer)_", text)  # (sent at 22:00, inside its window, not answered)
        self.assertIn("S10P5: words of mine for this phase", text)
        self.assertNotIn("Dodging", text.split("**Interrupt 3/8")[0])  # (not inside phase 5's answer)

    def synth_model(self, reply):
        self.model = lambda query: reply

    def synthesis(self, quote='"Dodging the email I owe my sister."'):
        return (f"Across these answers one thing keeps returning: the small, specific avoidance. You wrote {quote} and "
                "that sentence is more exact than any of the big statements around it. It sounds as though the "
                "protocol day showed you the shape more than it explained it.")

    def test_a_synthesis_is_appended_under_her_heading_and_the_compiled_part_is_untouched(self):
        self.finish()
        self.synth_model(self.synthesis())
        path = programs.insights(self.cfg, self.rid, self.now, self.log)
        text = path.read_text(encoding="utf-8")
        compiled, _, tail = text.partition("\n## Ada's synthesis\n")
        self.assertIn("one thing keeps returning", tail)
        self.assertIn("Ada's reading of the answers above: hers, not his.", tail)
        self.assertIn("Dodging the email I owe my sister.", compiled)
        self.assertEqual(self.the_run()["synthesis"]["status"], "written")
        self.assertEqual(len(self.asked), 1)
        self.assertIn("Dodging the email I owe my sister.", self.asked[0])  # the document is the input
        self.assertIn("every quote is checked", self.asked[0])
        # made again with the same answers: the document stays, no second model call
        again = programs.insights(self.cfg, self.rid, self.now + timedelta(hours=1), self.log)
        self.assertEqual(again, path)
        self.assertEqual(again.read_text(encoding="utf-8"), text)
        self.assertEqual(len(self.asked), 1)

    def test_a_synthesis_that_fails_validation_leaves_the_compiled_file_intact(self):
        self.finish()
        self.synth_model(self.synthesis('"I will fix everything on Monday"'))  # not in his words
        path = programs.insights(self.cfg, self.rid, self.now, self.log)
        compiled = path.read_text(encoding="utf-8")
        self.assertNotIn("## Ada's synthesis", compiled)
        self.assertEqual(self.the_run()["synthesis"]["status"], "pending")
        self.assertEqual(self.the_run()["synthesis"]["attempts"], 1)
        self.assertTrue(any("synthesis rejected (quote not in the entries" in l for l in self.log))
        for _ in range(2):  # the tick tries again, twice more, then the document stands alone
            self.now += timedelta(minutes=5)
            programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.the_run()["synthesis"]["status"], "failed")
        self.assertEqual(path.read_text(encoding="utf-8"), compiled)
        self.now += timedelta(minutes=5)
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.asked), 3)  # no fourth

    def test_too_short_or_too_long_is_rejected_too(self):
        self.finish()
        for bad in ("Nice work.", "x" * 5100):
            self.synth_model(bad)
            programs.insights(self.cfg, self.rid, self.now + timedelta(hours=1), self.log)
            self.assertNotIn("synthesis\n", self.doc().replace("## Ada's synthesis\n", "SYN\n"))
            self.assertNotIn("SYN", self.doc().replace("## Ada's synthesis\n", "SYN\n"))
            self.now += timedelta(hours=1)

    def test_no_model_defers_the_synthesis_without_using_an_attempt(self):
        self.finish()
        self.model = None
        path = programs.insights(self.cfg, self.rid, self.now, self.log)
        compiled = path.read_text(encoding="utf-8")
        syn = self.the_run()["synthesis"]
        self.assertEqual((syn["status"], syn.get("attempts", 0)), ("pending", 0))
        self.assertTrue(outage.deferred(syn))
        asked = len(self.asked)
        self.now += timedelta(minutes=10)
        programs.step(self.cfg, self.now, self.log)  # too soon (outage.RETRY_EVERY)
        self.assertEqual(len(self.asked), asked)
        self.synth_model(self.synthesis())
        self.now += timedelta(minutes=30)
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.the_run()["synthesis"]["status"], "written")
        self.assertTrue(self.doc().startswith(compiled.rstrip()))

    def test_a_long_outage_alerts_once_and_then_gives_up(self):
        self.finish()
        self.model = None
        programs.insights(self.cfg, self.rid, self.now, self.log)
        alerts = []
        t = self.now
        for hours in (7, 8):
            t = self.now + timedelta(hours=hours)
            try:
                programs.step(self.cfg, t, self.log)
            except tick.Alert as e:
                alerts.append(str(e))
        self.assertEqual(len(alerts), 1)
        self.assertIn("unreachable", alerts[0])
        try:
            programs.step(self.cfg, self.now + timedelta(days=8), self.log)
        except tick.Alert as e:
            self.assertIn("was not written", str(e))
        self.assertEqual(self.the_run()["synthesis"]["status"], "failed")

    def test_a_document_he_wrote_in_gets_no_synthesis(self):
        self.finish()
        self.model = None
        path = programs.insights(self.cfg, self.rid, self.now, self.log)
        path.write_text(path.read_text(encoding="utf-8") + "\nMy own note on this.\n", encoding="utf-8")
        self.synth_model(self.synthesis())
        asked = len(self.asked)
        self.now += timedelta(hours=1)
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.the_run()["synthesis"]["status"], "skipped")
        self.assertNotIn("## Ada's synthesis", path.read_text(encoding="utf-8"))
        self.assertEqual(len(self.asked), asked)  # (the model is not asked about a document that is his now)

    def test_his_edit_during_the_model_call_is_not_overwritten(self):
        self.finish()

        def model(query):  # he writes in the document (Obsidian) while the model thinks
            path = self.cfg.root / f"{self.the_run()['insights_entry']}.md"
            path.write_text(path.read_text(encoding="utf-8") + "\nMY OWN NOTE WRITTEN DURING THE CALL\n",
                            encoding="utf-8")
            return self.synthesis()
        self.model = model
        path = programs.insights(self.cfg, self.rid, self.now, self.log)
        text = path.read_text(encoding="utf-8")
        self.assertIn("MY OWN NOTE WRITTEN DURING THE CALL", text)
        self.assertNotIn("## Ada's synthesis", text)
        self.assertEqual(self.the_run()["synthesis"]["status"], "skipped")
        self.assertEqual(sorted(p.name for p in self.cfg.root.glob("*insights*")), [path.name])
        self.assertTrue(any("written in during the model call" in l for l in self.log))
        asked = len(self.asked)
        programs.step(self.cfg, self.now + timedelta(hours=1), self.log)  # and it is not tried again
        self.assertEqual(len(self.asked), asked)

    def test_the_engines_own_lines_cannot_be_quoted_to_him_as_his_words(self):
        self.finish()
        for engine_text in ("What am I avoiding right now by doing what I'm doing?", "not sent: missed"):
            self.synth_model(self.synthesis(f'"{engine_text}"'))  # in the document, but he did not write it
            programs.insights(self.cfg, self.rid, self.now + timedelta(hours=1), self.log)
            self.assertNotIn("## Ada's synthesis", self.doc())
            self.assertEqual(self.the_run()["synthesis"]["status"], "pending")
            self.assertTrue(any("quote not in the entries" in l for l in self.log))
        self.synth_model(self.synthesis('"words of mine for this phase"'))  # a phase answer of his: fine
        programs.insights(self.cfg, self.rid, self.now + timedelta(hours=2), self.log)
        self.assertEqual(self.the_run()["synthesis"]["status"], "written")

    def test_a_quote_cannot_run_from_one_answer_into_the_next(self):
        self.finish()
        self.synth_model(self.synthesis('"for this phase S10P2: words of mine"'))  # P1 ends, P2 begins: never written so
        programs.insights(self.cfg, self.rid, self.now, self.log)
        self.assertNotIn("## Ada's synthesis", self.doc())
        self.assertTrue(any("synthesis rejected (quote not in the entries" in l for l in self.log))

    def test_the_engines_note_about_a_recording_is_not_his_words(self):
        self.finish()
        body = self.entry(10).read_text(encoding="utf-8")
        note = audio.NOT_TRANSCRIBED
        self.entry(10).write_text(body.replace("S10P2: words of mine for this phase", note), encoding="utf-8")
        self.model = None
        programs.insights(self.cfg, self.rid, self.now, self.log)  # (the document shows the note in P2's place)
        self.assertIn(note, self.doc())
        words = programs._his_answers(self.cfg, self.prog(), self.the_run(), self.doc())
        self.assertNotIn("not transcribed", words)
        self.assertIn("S10P4: words of mine for this phase", words)
        ok, _ = validate.check_reflection("insights", self.synthesis('"audio file — not transcribed"'), words)
        self.assertFalse(ok)

    def test_an_answer_added_after_compiling_is_not_quotable(self):
        self.finish()
        self.model = None
        programs.insights(self.cfg, self.rid, self.now, self.log)  # compiled; the synthesis waits for a model
        self.now += timedelta(hours=1)
        self.mid += 1
        capture.capture(self.cfg, CHAT, str(self.prompt(f"{self.rid}-s10-i3")["message_id"]),
                        "A second thought, written long after the document was compiled.", self.now, "text",
                        str(self.mid))
        self.synth_model(self.synthesis('"A second thought, written long after the document was compiled."'))
        self.now += timedelta(minutes=40)
        programs.step(self.cfg, self.now, self.log)
        self.assertNotIn("## Ada's synthesis", self.doc())
        self.assertTrue(any("quote not in the entries" in l for l in self.log))

    def test_a_later_block_does_not_unseat_the_blocks_that_were_compiled(self):
        self.finish()
        self.model = None
        programs.insights(self.cfg, self.rid, self.now, self.log)
        self.now += timedelta(hours=1)
        self.mid += 1
        capture.capture(self.cfg, CHAT, str(self.prompt(f"{self.rid}-s10-i3")["message_id"]),
                        "A second block under the same interrupt.", self.now, "text", str(self.mid))
        self.synth_model(self.synthesis())  # quotes the first block, which the document shows
        self.now += timedelta(minutes=40)
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.the_run()["synthesis"]["status"], "written")

    def test_a_manual_compile_does_not_use_up_the_outage_alert(self):
        self.finish()
        self.model = None
        programs.insights(self.cfg, self.rid, self.now, self.log)
        later = self.now + timedelta(hours=7)  # past the 6 hours after which the outage is alerted once
        programs.insights(self.cfg, self.rid, later, self.log)  # /start fix-life insights: nowhere to alert
        self.assertNotIn("defer_alerted_at", self.the_run()["synthesis"])
        with self.assertRaises(tick.Alert) as caught:
            programs.step(self.cfg, later + timedelta(minutes=31), self.log)  # the tick's try says it
        self.assertIn("unreachable", str(caught.exception))

    def test_switching_the_synthesis_off_stops_a_deferred_one(self):
        self.finish()
        self.model = None
        programs.insights(self.cfg, self.rid, self.now, self.log)
        self.assertEqual(self.the_run()["synthesis"]["status"], "pending")
        self.cfg = make_cfg(Path(self.tmp.name), names=tp.NAMES, timezone=self.TZ)  # programs.synthesis unset
        self.synth_model(self.synthesis())
        asked = len(self.asked)
        programs.step(self.cfg, self.now + timedelta(hours=1), self.log)
        self.assertEqual(self.the_run()["synthesis"]["status"], "skipped")
        self.assertEqual(len(self.asked), asked)
        self.assertNotIn("## Ada's synthesis", self.doc())

    def test_compiling_again_keeps_the_synthesis_waits_and_attempts(self):
        self.finish()
        self.synth_model(self.synthesis('"I will fix everything on Monday"'))  # rejected: an attempt used
        programs.insights(self.cfg, self.rid, self.now, self.log)
        self.assertEqual(self.the_run()["synthesis"]["attempts"], 1)
        programs.insights(self.cfg, self.rid, self.now + timedelta(minutes=1), self.log)  # /start fix-life insights
        programs.insights(self.cfg, self.rid, self.now + timedelta(minutes=2), self.log)
        self.assertEqual(self.the_run()["synthesis"]["attempts"], 3)  # not reset to 0 each time
        self.assertEqual(self.the_run()["synthesis"]["status"], "failed")

    def test_compiling_again_in_an_outage_does_not_restart_its_clock(self):
        self.finish()
        self.model = None
        programs.insights(self.cfg, self.rid, self.now, self.log)
        first = dict(self.the_run()["synthesis"])
        self.assertTrue(outage.deferred(first))
        asked = len(self.asked)
        programs.insights(self.cfg, self.rid, self.now + timedelta(minutes=5), self.log)  # inside the 30-minute wait
        self.assertEqual(self.the_run()["synthesis"], first)
        self.assertEqual(len(self.asked), asked)  # no model call

    def test_off_by_default_and_on_by_the_program_file(self):
        self.cfg = make_cfg(Path(self.tmp.name), names=tp.NAMES, timezone=self.TZ)  # programs.synthesis unset
        self.finish()
        self.synth_model(self.synthesis())
        programs.insights(self.cfg, self.rid, self.now, self.log)
        self.assertEqual(self.asked, [])
        self.assertNotIn("synthesis", self.the_run())
        prog = dataclasses.replace(self.prog(), insights="compile+synthesis")
        self.assertTrue(programs._wants_synthesis(self.cfg, prog))
        self.assertFalse(programs._wants_synthesis(self.cfg, self.prog()))


if __name__ == "__main__":
    unittest.main()
