"""The day reflection waits for the journal day to end (the owner, 2026-11-25; NOTES-JOURNAL-DAY on review).

A note put down at 00:30 is filed under the night before (``config.journal_day``). So the day reflection of that day
is written only once the day is over (``windows.day_ends_at``, 04:00 by default), even when the evening entry was
annotated at 22:40, and it reads the after-midnight note. It is sent silently (it comes in the night). The week
review waits for its last day the same way. A reply to the reflection after the morning prompt went out is never
filed as the answer to that prompt, on any path (the hook, the sweep, the conversation writer)."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import test_reflect as tr
from helpers import CHAT, add_prompt

from journal import capture, entries, ledger, reflect, sweep

IST = ZoneInfo("Asia/Kolkata")


def ist(day, h, m=0):
    return datetime(2026, 10, day, h, m, tzinfo=IST)


class DayEndTest(tr.Base):
    def evening(self):
        """Wed 14 Oct: the evening prompt at 23:01, answered and annotated at 23:11."""
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway. the soup was good. "
                           "But idk. the bus can wait", sent + timedelta(minutes=10), "11")

    def late_note(self, words="the lake at night, still awake", at=None):
        """A /j at 00:30 on the 15th, filed under the 14th, and annotated (so nothing of the day is in progress)."""
        info = capture.capture_note(self.cfg, words, at or ist(15, 0, 30), "text", "900")
        path = Path(info["path"])
        entries.write_annotations(path, "- A late note.", self.cfg.names)
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, path.stem)["annotated_at"] = ist(15, 1, 0).isoformat()
        return path

    def test_an_annotated_evening_waits_for_the_day_to_end_and_reads_the_late_note(self):
        self.evening()
        self.assertEqual(self.reflect(ist(14, 23, 45)), ([], []))   # annotated, but the day is not over
        path = self.late_note()
        self.assertEqual(path.name, "2026-10-14-j.md")
        self.assertEqual(self.reflect(ist(15, 3, 59)), ([], []))
        alerts, log = self.reflect(ist(15, 4, 0))
        self.assertEqual(alerts, [])
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)
        query, _ = self.asked[0]
        self.assertIn("the lake at night, still awake", query)   # the after-midnight note is the day's
        self.assertIn("2026-10-14-j", (self.cfg.root / "2026-10-14-reflection.md").read_text())

    def test_sent_silently_whichever_way_it_was_triggered(self):
        self.evening()
        self.reflect(ist(15, 4, 5))
        method, payload = self.bot.calls[0]
        self.assertEqual(method, "sendMessage")
        self.assertIs(payload.get("disable_notification"), True)
        rec = self.rec("2026-10-14-reflection")
        self.assertEqual(rec["trigger"], "annotated")
        self.assertTrue(rec["telegram"]["silent"])

    def test_a_later_day_ends_at_moves_the_reflection_with_it(self):
        self.cfg.day_ends_at = "05:00"
        self.evening()
        self.assertEqual(self.reflect(ist(15, 4, 30)), ([], []))   # past the 04:00 fallback, but the day runs to 05:00
        _, log = self.reflect(ist(15, 5, 0))
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)

    def test_the_fallback_is_never_before_the_day_has_ended(self):
        self.cfg.reflections = {"day": {"fallback_time": "02:00"}}
        self.day()
        self.answer_prompt("morning", "2026-10-14", "rain all day, walked anyway. the soup was good. "
                           "But idk. the bus can wait", ist(14, 7, 0), "10")   # the evening never answered
        self.assertEqual(self.reflect(ist(15, 2, 30)), ([], []))
        _, log = self.reflect(ist(15, 4, 0))
        self.assertIn("wrote 2026-10-14-reflection.md (fallback) (+1 notebook)", log)

    def test_the_stale_rule_counts_from_the_days_end(self):
        """A reflection is not sent more than ``recovery.stale_hours`` (24) after its time; with the day running to
        06:00, that time is 06:00, not the 04:00 fallback."""
        self.cfg.day_ends_at = "06:00"
        self.evening()
        with ledger.locked(self.cfg.ledger_path) as data:   # written, not yet sent (the chat was down)
            data["reflections"] = [{"id": "2026-10-14-reflection", "kind": "reflection", "local_date": "2026-10-14",
                                    "written_at": ist(15, 6, 0).isoformat(), "trigger": "annotated",
                                    "path": str(self.cfg.root / "2026-10-14-reflection.md"), "attempts": 1}]
        (self.cfg.root / "2026-10-14-reflection.md").write_text("---\nkind: reflection\n---\n\nbody\n")
        rec = self.rec("2026-10-14-reflection")
        self.assertEqual(reflect._lateness(self.cfg, rec, ist(16, 5, 0).astimezone(timezone.utc)), timedelta(hours=23))
        self.assertEqual(reflect._lateness(self.cfg, rec, ist(16, 6, 30).astimezone(timezone.utc)),
                         timedelta(hours=24, minutes=30))

    def test_a_day_still_due_the_next_evening(self):
        """The day stays a candidate through the next journal day: a reflection held by a busy night is written at
        22:00 on the 15th all the same."""
        self.evening()
        _, log = self.reflect(ist(15, 22, 0))
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)


class WeekEndTest(tr.Base):
    def test_the_week_review_waits_for_its_last_day_to_end(self):
        add_prompt(self.cfg, kind="weekly", local_date="2026-10-18",
                   sent=datetime(2026, 10, 18, 20, 1, tzinfo=IST).astimezone(timezone.utc), message_id="30")
        self.answer_prompt("weekly", "2026-10-18", "rain all day, walked anyway. the soup was good. the bus can wait",
                           ist(18, 20, 30), "30")
        _, log = self.reflect(ist(18, 20, 40))
        self.assertFalse(any("week.md" in line for line in log), log)
        _, log = self.reflect(ist(19, 4, 0))
        self.assertTrue(any("wrote 2026-10-18-week.md (annotated)" in line for line in log), log)


class ReplyToReflectionTest(tr.Base):
    """The reflection lands at 04:00 and the morning prompt at 06:01: his reply to the reflection after that is a
    reply to the reflection, not his morning answer."""

    def setUp(self):
        super().setUp()
        DayEndTest.evening(self)
        self.reflect(ist(15, 4, 0))
        self.ref_mid = self.rec("2026-10-14-reflection")["telegram"]["message_id"]
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15",
                   sent=ist(15, 6, 1).astimezone(timezone.utc), message_id="40")

    def test_the_hook_files_no_reply_to_the_reflection_on_the_open_morning_prompt(self):
        data = ledger.snapshot(self.cfg.ledger_path)
        at = ist(15, 7, 0).astimezone(timezone.utc)
        self.assertEqual(capture.match(data, self.cfg, CHAT, self.ref_mid, at), (None, ""))
        prompt, rule = capture.match(data, self.cfg, CHAT, None, at)   # a typed message still answers the morning
        self.assertEqual((prompt["id"], rule), ("2026-10-15-morning", "next-message"))
        prompt, rule = capture.match(data, self.cfg, CHAT, "40", at)   # and so does a reply to the morning prompt
        self.assertEqual((prompt["id"], rule), ("2026-10-15-morning", "direct-reply"))

    def test_the_sweep_leaves_a_quoted_reply_to_the_reflection_to_the_writer(self):
        at = ist(15, 7, 0).astimezone(timezone.utc)
        quote = "What would being there look like, on your terms?"
        self.assertTrue(sweep.quotes_analysis(self.cfg, ledger.snapshot(self.cfg.ledger_path), quote, at))
        msg = {"message_id": "950", "quote": quote, "text": "being there means staying for dinner",
               "source": "text", "audio_path": None}
        self.assertIsNone(sweep.reconcile(self.cfg, msg, at))
        self.assertFalse(ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-morning").get("captures"))
        other = {**msg, "message_id": "951", "quote": "something that is not hers at all, nor the prompt's words"}
        self.assertIsNotNone(sweep.reconcile(self.cfg, other, at))   # (an unknown quote: the open prompt, as before)

    def test_a_quote_sent_after_the_message_or_too_short_counts_for_nothing(self):
        data = ledger.snapshot(self.cfg.ledger_path)
        quote = "What would being there look like, on your terms?"
        self.assertFalse(sweep.quotes_analysis(self.cfg, data, quote, ist(15, 3, 0).astimezone(timezone.utc)))
        self.assertFalse(sweep.quotes_analysis(self.cfg, data, "on your terms", ist(15, 7, 0).astimezone(timezone.utc)))


if __name__ == "__main__":
    unittest.main()
