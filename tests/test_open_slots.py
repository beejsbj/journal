"""OPEN-CORE: slot states and slot resolution (docs/OPEN-PROMPTS.md, *Slot states*, *One slot resolution for every
face*). A slot's state comes from the schedule and the ledger together; ``slots.resolve`` says which slot words (or a
deliberate delivery) go to."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from helpers import CHAT, NEUTRAL, make_cfg

from journal import ledger, slots

IST = ZoneInfo("Asia/Kolkata")
WED = datetime(2026, 10, 14, 0, 0, tzinfo=IST)  # a Wednesday
THU = WED + timedelta(days=1)

WATER = """---
kind: practice
id: water
title: Water
schedule.1.time: 09:00
schedule.2.time: 15:00
collapse_hours: 2
opener.mode: plain
---
- Did you drink enough?
"""
DUSK = """---
kind: practice
id: dusk
title: Dusk
schedule.time: 21:00
collapse_hours: 2
---
- What is still with you?
"""
NIGHT = """---
kind: practice
id: night
title: Night
schedule.time: "00:30"
collapse_hours: 1
---
- What is keeping you up?
"""
WALK = """---
kind: practice
id: walk
title: A walk
---
- Where did you go?
"""


def at(h, m=0, day=WED):
    return day.replace(hour=h, minute=m)


class SlotCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name), names=NEUTRAL)
        for name, text in (("water.md", WATER), ("dusk.md", DUSK), ("walk.md", WALK)):
            d = Path(self.cfg.practices_dir)
            d.mkdir(parents=True, exist_ok=True)
            (d / name).write_text(text, encoding="utf-8")

    def add(self, **record):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append(record)
        return record

    def delivered(self, pid, kind, local_date, sent, **more):
        return self.add(id=pid, kind=kind, local_date=local_date, chat_id=CHAT, message_id="700",
                        sent_at=sent.astimezone(timezone.utc).isoformat(), opener="Hi", opener_source="plain:practice",
                        **more)

    def states(self, local_date, now, practice=None):
        return {s.id: s.state for s in slots.day(self.cfg, local_date, now, practice=practice)}


class SlotStateTest(SlotCase):
    def test_a_slot_with_no_record_is_upcoming_then_due_inside_its_window_then_missed(self):
        self.assertEqual(self.states("2026-10-14", at(8), "water"),
                         {"2026-10-14-water": "upcoming", "2026-10-14-water-2": "upcoming"})
        self.assertEqual(self.states("2026-10-14", at(10), "water"),
                         {"2026-10-14-water": "due", "2026-10-14-water-2": "upcoming"})
        self.assertEqual(self.states("2026-10-14", at(11, 1), "water"),
                         {"2026-10-14-water": "missed", "2026-10-14-water-2": "upcoming"})

    def test_a_delivered_record_is_open_inside_its_window_from_the_delivery_then_missed(self):
        self.delivered("2026-10-14-water", "water", "2026-10-14", at(10), collapse_hours=2)
        self.assertEqual(self.states("2026-10-14", at(11, 30), "water")["2026-10-14-water"], "open")
        self.assertEqual(self.states("2026-10-14", at(12, 1), "water")["2026-10-14-water"], "missed")

    def test_a_record_with_captures_is_answered_whichever_face_made_it(self):
        self.delivered("2026-10-14-water", "water", "2026-10-14", at(9), captures=[{"at": at(9, 5).isoformat()}])
        self.add(id="2026-10-14-water-2", kind="water", local_date="2026-10-14", message_id=None,
                 sent_at=at(14, 50).isoformat(), opened_via="web", captures=[{"at": at(14, 50).isoformat(), "via": "web"}])
        self.assertEqual(self.states("2026-10-14", at(16), "water"),
                         {"2026-10-14-water": "answered", "2026-10-14-water-2": "answered"})

    def test_the_record_a_failed_send_left_reads_as_due_then_missed(self):
        self.add(id="2026-10-14-water", kind="water", local_date="2026-10-14", chat_id=CHAT,
                 scheduled=at(9).isoformat(), send_attempts=2, send_error="Bad Gateway")
        self.assertEqual(self.states("2026-10-14", at(10), "water")["2026-10-14-water"], "due")
        self.assertEqual(self.states("2026-10-14", at(11, 30), "water")["2026-10-14-water"], "missed")

    def test_old_records_read_as_they_did(self):
        """Delivered, failed, backfilled, notes and phases: a fixture of each."""
        self.delivered("2026-10-13-evening", "evening", "2026-10-13", at(23, day=WED - timedelta(days=1)))
        self.add(id="2026-10-13-morning", kind="morning", local_date="2026-10-13", chat_id=CHAT, backfilled=True,
                 message_id=None, sent_at=at(6, day=WED - timedelta(days=1)).isoformat(),
                 captures=[{"at": at(6, 30, day=WED - timedelta(days=1)).isoformat(), "rule": "backfill"}])
        self.add(id="2026-10-13-j", kind="j", stem="2026-10-13-j", local_date="2026-10-13", message_id=None,
                 sent_at=at(12, day=WED - timedelta(days=1)).isoformat(), captures=[{"at": "x"}])
        self.add(id="run1-s1-p1", kind="fix-life", program="fix-life", run="run1", local_date="2026-10-13",
                 message_id="9", sent_at=at(10, day=WED - timedelta(days=1)).isoformat())
        got = self.states("2026-10-13", at(12))
        self.assertEqual(got["2026-10-13-evening"], "missed")      # delivered, never answered
        self.assertEqual(got["2026-10-13-morning"], "answered")    # backfilled, with a capture
        self.assertNotIn("2026-10-13-j", got)                      # a note is no slot
        self.assertNotIn("run1-s1-p1", got)                        # nor a program phase
        self.assertEqual(got["2026-10-13-water"], "missed")        # derived: never delivered

    def test_a_record_off_the_schedule_is_an_extra_slot_of_its_practice(self):
        self.delivered("2026-10-14-walk", "walk", "2026-10-14", at(10))
        day = slots.day(self.cfg, "2026-10-14", at(11), practice="walk")
        self.assertEqual([(s.id, s.n, s.state) for s in day], [("2026-10-14-walk", None, "open")])

    def test_weekday_slots_only_on_their_day(self):
        self.assertEqual(self.states("2026-10-14", at(13), "midweek"), {"2026-10-14-midweek": "due"})
        self.assertEqual(self.states("2026-10-15", at(13, day=WED + timedelta(days=1)), "midweek"), {})


class ResolveTest(SlotCase):
    def resolve(self, practice, words_at, now=None, **kw):
        return slots.resolve(self.cfg, practice, words_at, now=now or words_at, **kw)

    def test_step_1_a_due_slot_takes_the_words(self):
        got = self.resolve("water", at(10))
        self.assertEqual((got.id, got.step, got.state), ("2026-10-14-water", 1, "due"))

    def test_step_1_an_open_delivered_prompt_takes_them(self):
        self.delivered("2026-10-14-water-2", "water", "2026-10-14", at(15, 1), collapse_hours=2)
        got = self.resolve("water", at(15, 30))
        self.assertEqual((got.id, got.step, got.record["message_id"]), ("2026-10-14-water-2", 1, "700"))

    def test_the_record_a_failed_send_left_is_taken_as_due(self):
        self.add(id="2026-10-14-water", kind="water", local_date="2026-10-14", chat_id=CHAT,
                 scheduled=at(9).isoformat(), send_attempts=3, send_error="Bad Gateway")
        got = self.resolve("water", at(10))
        self.assertEqual((got.id, got.step, got.record["send_attempts"]), ("2026-10-14-water", 1, 3))

    def test_overnight_a_missed_evening_takes_the_answer_and_tonight_is_untouched(self):
        thu = WED + timedelta(days=1)
        got = self.resolve("dusk", at(0, 30, day=thu))
        self.assertEqual((got.id, got.step), ("2026-10-14-dusk", 2))
        # at 15:00 the same practice fills tonight's slot early
        got = self.resolve("dusk", at(15, day=thu))
        self.assertEqual((got.id, got.step), ("2026-10-15-dusk", 3))

    def test_day_ends_at_moves_the_boundary(self):
        self.cfg.day_ends_at = "00:15"
        got = self.resolve("dusk", at(0, 30, day=WED + timedelta(days=1)))
        self.assertEqual((got.id, got.step), ("2026-10-15-dusk", 3))

    def _night(self):
        (Path(self.cfg.practices_dir) / "night.md").write_text(NIGHT, encoding="utf-8")

    def test_a_slot_before_day_ends_at_belongs_to_the_journal_day_before_it(self):
        """Review round 1: a 00:30 slot on the 15th is the 14th's journal day. Words at 02:30 on the 15th (journal day
        the 14th) take that slot, missed two hours ago, not the 14th's own 00:30 slot, a day older."""
        self._night()
        got = self.resolve("night", at(2, 30, day=THU), now=at(2, 30, day=THU))
        self.assertEqual((got.id, got.step), ("2026-10-15-night", 2))

    def test_early_words_late_at_night_fill_the_slot_after_midnight(self):
        """Words at 23:00 on the 14th: the next slot of that journal day is 00:30 on the 15th (step 3), not the 14th's
        00:30 slot, which belongs to the journal day before."""
        self._night()
        got = self.resolve("night", at(23), now=at(23))
        self.assertEqual((got.id, got.step), ("2026-10-15-night", 3))

    def test_a_slot_belongs_to_its_journal_day_by_its_schedule_not_its_delivery(self):
        """Review round 2: the 15th's 00:30 slot is the 14th's night even when the tick delivered it late, after
        ``day_ends_at``: where words go must not depend on when a tick ran."""
        self._night()
        self.cfg.day_ends_at = "00:40"
        self.delivered("2026-10-15-night", "night", "2026-10-15", at(0, 45, day=THU))
        got = self.resolve("night", at(2, 30, day=THU), now=at(2, 30, day=THU))
        self.assertEqual((got.id, got.step), ("2026-10-16-night", 3))

    def test_the_night_practice_done_in_the_day_fills_tonights_slot(self):
        """Pinned as the doc reads (steps 2-4 on the journal day; DECISIONS.md, OPEN-CORE, review round 2): at 09:00
        the 15th's 00:30 slot was last night's, so words for the night practice fill tonight's, dated the 16th, as an
        evening chosen at 15:00 fills tonight's evening."""
        self._night()
        self.delivered("2026-10-15-night", "night", "2026-10-15", at(0, 30, day=THU))
        got = self.resolve("night", at(9, day=THU), now=at(9, day=THU))
        self.assertEqual((got.id, got.step), ("2026-10-16-night", 3))

    def test_step_1_an_open_evening_at_half_past_midnight_is_last_nights(self):
        self.delivered("2026-10-14-evening", "evening", "2026-10-14", at(23))
        got = self.resolve("evening", at(0, 30, day=WED + timedelta(days=1)))
        self.assertEqual((got.id, got.step), ("2026-10-14-evening", 1))

    def test_step_2_a_missed_morning_at_eight_in_the_evening(self):
        got = self.resolve("morning", at(20))
        self.assertEqual((got.id, got.step, got.state), ("2026-10-14-morning", 2, "missed"))
        self.delivered("2026-10-14-morning", "morning", "2026-10-14", at(6))
        got = self.resolve("morning", at(20))
        self.assertEqual((got.id, got.step, got.record["message_id"]), ("2026-10-14-morning", 2, "700"))

    def test_step_3_early_words_fill_the_next_slot_with_no_record(self):
        got = self.resolve("water", at(8))
        self.assertEqual((got.id, got.step), ("2026-10-14-water", 3))
        self.delivered("2026-10-14-water", "water", "2026-10-14", at(9), captures=[{"at": at(9, 5).isoformat()}])
        got = self.resolve("water", at(11, 30))
        self.assertEqual((got.id, got.step), ("2026-10-14-water-2", 3))

    def test_step_3_takes_a_later_slot_whose_only_record_is_a_failed_send(self):
        """Words dated before a slot whose send failed (no delivery, no captures) fill that slot early, as a slot
        with no record would be filled, rather than making an extra entry."""
        self.delivered("2026-10-14-water", "water", "2026-10-14", at(9), captures=[{"at": at(9, 5).isoformat()}])
        self.add(id="2026-10-14-water-2", kind="water", local_date="2026-10-14", chat_id=CHAT,
                 scheduled=at(15).isoformat(), send_attempts=1, send_error="Bad Gateway")
        got = self.resolve("water", at(14), now=at(15, 30))
        self.assertEqual((got.id, got.step, got.record["send_attempts"]), ("2026-10-14-water-2", 3, 1))

    def test_after_an_answer_choosing_the_practice_again_fills_the_next_slot_that_fits(self):
        self.delivered("2026-10-14-water", "water", "2026-10-14", at(9), captures=[{"at": at(9, 5).isoformat()}])
        got = self.resolve("water", at(10))
        self.assertEqual((got.id, got.step, got.record), ("2026-10-14-water-2", 3, None))
        got = self.resolve("water", at(10), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-14-water-2", 3))

    def test_step_3_never_takes_a_slot_already_delivered(self):
        self.delivered("2026-10-14-dusk", "dusk", "2026-10-14", at(21))
        got = self.resolve("dusk", at(20), now=at(21, 30))   # backdated before the delivery
        self.assertEqual((got.id, got.step), ("2026-10-14-dusk-2", 4))

    def test_backdated_words_never_answer_a_prompt_sent_after_them(self):
        self.delivered("2026-10-14-morning", "morning", "2026-10-14", at(6))
        got = self.resolve("morning", at(5, 30), now=at(7))
        self.assertNotEqual(got.id, "2026-10-14-morning")
        self.assertEqual(got.step, 4)

    def test_an_answered_practice_chosen_again_makes_an_extra_entry(self):
        self.delivered("2026-10-14-morning", "morning", "2026-10-14", at(6), captures=[{"at": at(6, 5).isoformat()}])
        got = self.resolve("morning", at(7))
        self.assertEqual((got.id, got.step, got.record, got.n), ("2026-10-14-morning-2", 4, None, None))
        self.add(id="2026-10-14-morning-2", kind="morning", local_date="2026-10-14", message_id=None,
                 sent_at=at(7).isoformat(), opened_via="cli", captures=[{"at": at(7).isoformat(), "via": "cli"}])
        self.assertEqual(self.resolve("morning", at(8)).id, "2026-10-14-morning-3")

    def test_a_second_extra_skips_a_file_already_there(self):
        self.delivered("2026-10-14-morning", "morning", "2026-10-14", at(6), captures=[{"at": at(6, 5).isoformat()}])
        (self.cfg.root / "2026-10-14-morning-2.md").write_text("an older entry\n", encoding="utf-8")
        self.assertEqual(self.resolve("morning", at(7)).id, "2026-10-14-morning-3")

    def test_an_extra_never_takes_an_id_another_practice_owns(self):
        """Review round 1: beside a practice named ``water-3`` (the library only warns about the pair), water's extra
        entry skips ``<date>-water-3``, which is that practice's slot."""
        (Path(self.cfg.practices_dir) / "water-3.md").write_text(WATER.replace("id: water", "id: water-3"),
                                                                 encoding="utf-8")
        self.delivered("2026-10-14-water", "water", "2026-10-14", at(9), captures=[{"at": at(9, 5).isoformat()}])
        self.delivered("2026-10-14-water-2", "water", "2026-10-14", at(15), captures=[{"at": at(15, 5).isoformat()}])
        got = self.resolve("water", at(16))
        self.assertEqual((got.id, got.step), ("2026-10-14-water-4", 4))

    def test_an_unscheduled_practice_takes_slot_ones_id_then_the_next(self):
        self.assertEqual(self.resolve("walk", at(10)).id, "2026-10-14-walk")
        self.add(id="2026-10-14-walk", kind="walk", local_date="2026-10-14", message_id=None,
                 sent_at=at(10).isoformat(), opened_via="web", captures=[{"at": at(10).isoformat(), "via": "web"}])
        self.assertEqual(self.resolve("walk", at(11)).id, "2026-10-14-walk-2")

    def test_a_practice_with_no_slot_that_day_takes_slot_ones_id(self):
        got = self.resolve("weekly", at(23, 1), delivery=True)   # weekly is on Sundays; this is a Wednesday
        self.assertEqual((got.id, got.step), ("2026-10-14-weekly", 4))

    def test_words_before_yesterday_make_an_extra_entry_on_that_day_and_fill_no_old_slot(self):
        mon = WED - timedelta(days=2)
        got = self.resolve("morning", at(7, day=mon), now=at(12))
        self.assertEqual((got.id, got.local_date, got.step), ("2026-10-12-morning-2", "2026-10-12", 4))
        # yesterday's journal day is still in reach
        got = self.resolve("morning", at(7, day=WED - timedelta(days=1)), now=at(12))
        self.assertEqual((got.id, got.step), ("2026-10-13-morning", 2))

    def test_a_delivery_reads_states_now_and_skips_the_sent_before_check(self):
        """A deliberate send is made now: a delivered prompt the test clock thinks is newer is still the one."""
        self.delivered("2026-10-14-evening", "evening", "2026-10-14", at(23, 30))
        got = self.resolve("evening", at(23, 10), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-14-evening", 1))

    def test_a_delivery_before_day_ends_at_takes_todays_calendar_date(self):
        """The owner, 2026-11-09 (decision 1): /start morning at 03:30 delivers today's morning (step 3, its 06:00
        slot), not yesterday's missed one; words written at 03:30 still belong to the journal day before."""
        got = self.resolve("morning", at(3, 30), delivery=True)
        self.assertEqual((got.id, got.local_date, got.step), ("2026-10-14-morning", "2026-10-14", 3))
        got = self.resolve("morning", at(3, 30))
        self.assertEqual((got.id, got.step), ("2026-10-13-morning", 2))

    def test_a_delivery_before_day_ends_at_takes_last_nights_slot_when_it_is_nearer(self):
        """Decision 1's evening case: at 00:30 last night's 21:00 dusk is 3.5 hours behind and tonight's 20.5 ahead,
        so /start dusk means last night's (still open here), as words at 00:30 do; tonight's slot is left alone."""
        self.delivered("2026-10-14-dusk", "dusk", "2026-10-14", at(23))
        got = self.resolve("dusk", at(0, 30, day=THU), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-14-dusk", 1))
        got = self.resolve("dusk", at(0, 30, day=THU))
        self.assertEqual((got.id, got.step), ("2026-10-14-dusk", 1))

    def test_a_delivery_before_day_ends_at_never_sent_last_night_takes_its_missed_slot(self):
        got = self.resolve("dusk", at(0, 30, day=THU), delivery=True)
        self.assertEqual((got.id, got.local_date, got.step), ("2026-10-14-dusk", "2026-10-14", 2))

    def test_a_delivery_before_day_ends_at_after_last_nights_answer_is_an_extra_of_last_night(self):
        """Last night's dusk is answered: /start dusk at 00:30 is a second one of that night, not tonight's early."""
        self.delivered("2026-10-14-dusk", "dusk", "2026-10-14", at(21), captures=[{"at": at(21, 5).isoformat()}])
        got = self.resolve("dusk", at(0, 30, day=THU), delivery=True)
        self.assertEqual((got.id, got.local_date, got.step), ("2026-10-14-dusk-2", "2026-10-14", 4))

    def test_a_delivery_before_day_ends_at_with_no_slot_today_takes_last_nights_within_twelve_hours(self):
        """A Wednesday-only practice on Thursday: at 00:30 its 21:00 is last night's; one at 09:00, 16 hours behind
        by 01:00, is not, and /start makes an extra dated today. An exact tie is today's."""
        d = Path(self.cfg.practices_dir)
        (d / "late.md").write_text(DUSK.replace("dusk", "late").replace("Dusk", "Late").replace(
            "schedule.time: 21:00", "schedule.time: 21:00\nschedule.weekday: 2"), encoding="utf-8")
        (d / "early.md").write_text(DUSK.replace("dusk", "early").replace("Dusk", "Early").replace(
            "schedule.time: 21:00", "schedule.time: 09:00\nschedule.weekday: 2"), encoding="utf-8")
        (d / "noon.md").write_text(DUSK.replace("dusk", "noon").replace("Dusk", "Noon").replace(
            "schedule.time: 21:00", "schedule.time: 13:00"), encoding="utf-8")
        got = self.resolve("late", at(0, 30, day=THU), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-14-late", 2))
        got = self.resolve("early", at(1, 0, day=THU), delivery=True)
        self.assertEqual((got.id, got.local_date, got.step), ("2026-10-15-early", "2026-10-15", 4))
        got = self.resolve("noon", at(1, 0, day=THU), delivery=True)   # 12 hours each way
        self.assertEqual((got.id, got.step), ("2026-10-15-noon", 3))

    def test_a_delivery_before_day_ends_at_takes_the_nearer_of_two_slots_a_day(self):
        """Water, 09:00 and 15:00: at 02:00 yesterday's 15:00 is 11 hours behind and today's 09:00 seven ahead."""
        got = self.resolve("water", at(2, 0, day=THU), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-15-water", 3))

    def test_a_delivery_before_day_ends_at_takes_todays_slot_due_or_missed_before_then(self):
        """Today's own slots before the hour still count for a delivery: the 00:30 night slot, due at 00:45, is the
        one /start night delivers; at 03:30, missed, it is the one re-sent; an unscheduled practice's extra is dated
        today."""
        self._night()
        got = self.resolve("night", at(0, 45, day=THU), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-15-night", 1))
        got = self.resolve("night", at(3, 30, day=THU), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-15-night", 2))
        got = self.resolve("walk", at(3, 30, day=THU), delivery=True)
        self.assertEqual((got.id, got.local_date, got.step), ("2026-10-15-walk", "2026-10-15", 4))

    def test_a_delivery_after_day_ends_at_resolves_as_before(self):
        got = self.resolve("morning", at(4, 0), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-14-morning", 3))
        self.delivered("2026-10-14-dusk", "dusk", "2026-10-14", at(21))
        got = self.resolve("dusk", at(22), delivery=True)
        self.assertEqual((got.id, got.step), ("2026-10-14-dusk", 1))

    def test_an_unknown_practice_raises(self):
        with self.assertRaises(Exception):
            self.resolve("nope", at(10))


class JournalDayTest(SlotCase):
    def test_words_before_day_ends_at_belong_to_the_day_before(self):
        self.assertEqual(slots.journal_day(self.cfg, at(3, 59)).isoformat(), "2026-10-13")
        self.assertEqual(slots.journal_day(self.cfg, at(4, 0)).isoformat(), "2026-10-14")
        self.cfg.day_ends_at = "01:00"
        self.assertEqual(slots.journal_day(self.cfg, at(3, 59)).isoformat(), "2026-10-14")
        self.assertEqual(slots.journal_day(self.cfg, at(0, 30)).isoformat(), "2026-10-13")

    def test_an_unusable_day_ends_at_is_the_default(self):
        self.cfg.day_ends_at = "25:00"
        self.assertEqual(slots.journal_day(self.cfg, at(3, 59)).isoformat(), "2026-10-13")


if __name__ == "__main__":
    unittest.main()
