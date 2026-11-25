"""Notes follow the journal day (issue #82): a note put down before ``windows.day_ends_at`` (default 04:00) is filed
under the day before, as a prompt answered then already is, whichever way it came (a /j in the chat, a chat thread that
became a note, the web app, the CLI, MCP). Today, the day reflection, ``GET /days`` and the Entries list read the
same day: they key on the entry's stem, and Today asks the same helper. Existing files are never moved."""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import test_conversations as tc
from helpers import make_cfg
from serve_helpers import ServeCase

from journal import actions, capture, entries, ledger, reflect, slots, today
from journal.config import journal_day

IST = ZoneInfo("Asia/Kolkata")


def ist(day, h, m=0):
    return datetime(2026, 10, day, h, m, tzinfo=IST)


class JournalDayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))   # (Asia/Kolkata, day_ends_at 04:00)

    def test_the_boundary(self):
        for when, day in ((ist(15, 23, 59), "2026-10-15"), (ist(16, 0, 0), "2026-10-15"),
                          (ist(16, 0, 30), "2026-10-15"), (ist(16, 3, 59), "2026-10-15"),
                          (ist(16, 4, 0), "2026-10-16"), (ist(16, 4, 1), "2026-10-16")):
            self.assertEqual(journal_day(self.cfg, when).isoformat(), day, when)
            self.assertEqual(slots.journal_day(self.cfg, when).isoformat(), day, when)  # (one helper)

    def test_the_moment_is_read_in_the_journals_timezone(self):
        self.assertEqual(journal_day(self.cfg, ist(16, 0, 30).astimezone(timezone.utc)), date(2026, 10, 15))

    def test_day_ends_at_midnight_is_the_calendar_date(self):
        self.cfg.day_ends_at = "00:00"
        self.assertEqual(journal_day(self.cfg, ist(16, 0, 0)), date(2026, 10, 16))
        self.assertEqual(journal_day(self.cfg, ist(15, 23, 59)), date(2026, 10, 15))


class NoteDayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))

    def stems(self):
        return sorted(p.stem for p in self.cfg.root.glob("*.md"))

    def test_a_chat_j_at_0030_is_last_nights_note(self):
        info = capture.capture_note(self.cfg, "can't sleep", ist(16, 0, 30), "text", "540")
        self.assertEqual(Path(info["path"]).name, "2026-10-15-j.md")
        body = (self.cfg.root / "2026-10-15-j.md").read_text()
        self.assertIn("date: 2026-10-15\n", body)
        self.assertIn("can't sleep", body)
        self.assertEqual(ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-j")["local_date"],
                         "2026-10-15")

    def test_a_note_at_0400_starts_the_new_day(self):
        capture.capture_note(self.cfg, "up early", ist(16, 4, 0), "text", "541")
        capture.capture_note(self.cfg, "still up", ist(16, 3, 59), "text", "542")
        self.assertEqual(self.stems(), ["2026-10-15-j", "2026-10-16-j"])

    def test_notes_either_side_of_midnight_share_the_day_and_number_on(self):
        capture.capture_note(self.cfg, "before midnight", ist(15, 23, 50), "text", "550")
        actions.note(self.cfg, "after midnight, on the web", ist(16, 0, 20), via="web", now=ist(16, 0, 20))
        actions.note(self.cfg, "from the CLI", ist(16, 1, 0), via="cli", now=ist(16, 1, 0))
        actions.note(self.cfg, "from MCP", ist(16, 2, 0), via="mcp", now=ist(16, 2, 0))
        self.assertEqual(self.stems(), ["2026-10-15-j", "2026-10-15-j-2", "2026-10-15-j-3", "2026-10-15-j-4"])

    def test_a_backdated_web_note_takes_the_journal_day_of_its_own_time(self):
        """``at`` (the words' time), not the server's clock, decides the day."""
        actions.note(self.cfg, "written last night", ist(16, 0, 30), via="web", now=ist(16, 9, 0))
        self.assertEqual(self.stems(), ["2026-10-15-j"])

    def test_an_existing_note_is_not_moved(self):
        """A note filed by the calendar date before this change stays where it is; the next one numbers past
        nothing of it (another day's file)."""
        old = self.cfg.root / "2026-10-16-j.md"
        self.cfg.root.mkdir(parents=True, exist_ok=True)
        old.write_text("---\ndate: 2026-10-16\nkind: j\ntype: journal\n---\n\nan old 00:10 note\n", encoding="utf-8")
        capture.capture_note(self.cfg, "a new 00:30 note", ist(16, 0, 30), "text", "560")
        self.assertEqual(self.stems(), ["2026-10-15-j", "2026-10-16-j"])
        self.assertIn("an old 00:10 note", old.read_text())

    def test_the_readers_agree_at_0030(self):
        """The note just put down is among Today's *Written today* and the today block's entries, under Today's date,
        and the day reflection of that date reads it."""
        capture.capture_note(self.cfg, "the lake at night", ist(16, 0, 30), "text", "570")
        data = today.collect(self.cfg, ist(16, 0, 35).astimezone(timezone.utc))
        self.assertEqual((data["date"], data["weekday"], data["time"]), ("2026-10-15", "Thu", "00:35"))
        self.assertEqual([e["stem"] for e in data["entries"]], ["2026-10-15-j"])
        self.assertEqual([p.stem for p in entries.entries_on(self.cfg.root, data["date"])], ["2026-10-15-j"])
        self.assertEqual([p.stem for p in reflect._his_entries(self.cfg, [date(2026, 10, 15)])], ["2026-10-15-j"])

    def test_today_turns_at_day_ends_at(self):
        capture.capture_note(self.cfg, "the lake at night", ist(16, 0, 30), "text", "571")
        data = today.collect(self.cfg, ist(16, 4, 0).astimezone(timezone.utc))
        self.assertEqual((data["date"], data["time"], data["entries"]), ("2026-10-16", "04:00", []))
        self.assertIn("Fri 16 Oct 2026, 04:00", today.render(self.cfg, ist(16, 4, 0)))


class HerNoteDayTest(unittest.TestCase):
    """Her own note with no date given (``capture.add_companion_note``) follows the journal day as his do."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))

    def test_her_note_at_0030_joins_his_note_of_last_night(self):
        capture.capture_note(self.cfg, "before midnight", ist(15, 23, 50), "text", "580")
        path = capture.add_companion_note(self.cfg, "a thought for him", ist(16, 0, 30))
        self.assertEqual(Path(path).name, "2026-10-15-j.md")
        self.assertEqual(sorted(p.name for p in self.cfg.root.glob("*.md")), ["2026-10-15-j.md"])
        self.assertIn("a thought for him", Path(path).read_text())

    def test_her_note_at_0400_is_the_new_days(self):
        capture.capture_note(self.cfg, "before midnight", ist(15, 23, 50), "text", "581")
        path = capture.add_companion_note(self.cfg, "a thought for him", ist(16, 4, 0))
        self.assertEqual(Path(path).name, "2026-10-16-note.md")

    def test_a_date_given_is_kept(self):
        capture.capture_note(self.cfg, "on the 16th", ist(16, 9, 0), "text", "582")
        path = capture.add_companion_note(self.cfg, "for the 16th", ist(16, 0, 30), local_date="2026-10-16")
        self.assertEqual(Path(path).name, "2026-10-16-j.md")


class TodayHeaderTest(unittest.TestCase):
    """Between midnight and ``day_ends_at`` the today block names the journal day and the clock's day apart, and
    says which day the tasks (the calendar's) are due."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))

    def tasks(self, day, at):
        today._write_cache(self.cfg, {"at": at.isoformat(), "tasks": {
            "count": 1, "names": ["call the bank"], "date": day, "at": at.isoformat()}})

    def test_after_midnight_both_days_are_named(self):
        self.tasks("2026-10-16", ist(16, 0, 30))
        data = today.collect(self.cfg, ist(16, 0, 35).astimezone(timezone.utc))
        self.assertEqual((data["date"], data["calendar_date"]), ("2026-10-15", "2026-10-16"))
        self.assertEqual(today.header_of(data), "## Journal: today — journal day Thu 15 Oct 2026 · now Fri 00:35 IST")
        self.assertIn("- tasks due Fri: 1 — call the bank", today.lines_of(data))

    def test_in_the_day_the_header_is_as_before(self):
        self.tasks("2026-10-16", ist(16, 9, 0))
        data = today.collect(self.cfg, ist(16, 9, 12).astimezone(timezone.utc))
        self.assertEqual(today.header_of(data), "## Journal: today — Fri 16 Oct 2026, 09:12 IST")
        self.assertIn("- tasks due today: 1 — call the bank", today.lines_of(data))

    def test_yesterdays_task_list_is_not_shown_after_midnight(self):
        """The tasks stay keyed to the calendar date (a task is due by the calendar): at 00:35 Friday, Thursday's
        cached list is not Friday's."""
        self.tasks("2026-10-15", ist(15, 23, 0))
        data = today.collect(self.cfg, ist(16, 0, 35).astimezone(timezone.utc))
        self.assertIsNone(data["tasks"])

    def test_a_block_without_the_calendar_date_reads_as_before(self):
        """(an older dict, e.g. a pinned fixture: one day, as it always was)"""
        data = {"date": "2026-10-15", "time": "09:12", "timezone": "IST"}
        self.assertEqual(today.header_of(data), "## Journal: today — Thu 15 Oct 2026, 09:12 IST")


class RangeDefaultTest(unittest.TestCase):
    """serve's ``from``/``to`` default ends on the journal day, as Today does."""

    def test_to_defaults_to_the_journal_day(self):
        from types import SimpleNamespace
        from journal.serve import api
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(Path(d))
            req = SimpleNamespace(cfg=cfg, now=ist(16, 0, 30).astimezone(timezone.utc), query={})
            self.assertEqual(api._range(req, 14), (date(2026, 10, 2), date(2026, 10, 15)))
            req.now = ist(16, 4, 0).astimezone(timezone.utc)
            self.assertEqual(api._range(req, 14)[1], date(2026, 10, 16))


class AutoNoteDayTest(tc.Base):
    """A chat thread that answers no prompt becomes a note: one that starts at 00:30 IST is last night's."""

    def test_a_thread_at_0030_is_last_nights_note(self):
        t = ist(16, 0, 30).astimezone(timezone.utc)
        self.his("still thinking about the bank call", t, "800")
        self.hers("What about it?", t + timedelta(minutes=1))
        log = self.tick(t + timedelta(minutes=10))
        self.assertEqual(log, ["conversation → new note 2026-10-15-j.md: +1 from Theo, +1 from Iris"])
        body = (self.cfg.root / "2026-10-15-j.md").read_text()
        self.assertIn("date: 2026-10-15\n", body)
        self.assertIn("still thinking about the bank call", body)

    def test_a_thread_at_0400_is_the_new_days_note(self):
        t = ist(16, 4, 0).astimezone(timezone.utc)
        self.his("up before the birds", t, "801")
        self.tick(t + timedelta(minutes=10))
        self.assertTrue((self.cfg.root / "2026-10-16-j.md").exists())

    def test_a_dry_run_plans_the_same_day(self):
        from journal import conversations
        (self.cfg.state_dir / "conversations.json").unlink()  # (a backfill reads what the live writer has not)
        t = ist(16, 0, 30).astimezone(timezone.utc)
        self.his("a line at half past midnight", t, "802")
        lines = conversations.backfill(self.cfg, date(2026, 10, 15), write=False, now=t + timedelta(minutes=10))
        self.assertTrue(any("2026-10-15-j.md" in l for l in lines), lines)
        self.assertFalse(any("2026-10-16-j" in l for l in lines), lines)


if __name__ == "__main__":
    unittest.main()


class ServeDaysTest(ServeCase):
    """``GET /days`` and the Entries list put a 00:30 note under last night, as Today does."""

    def test_a_0030_note_is_under_last_night(self):
        at = datetime(2026, 10, 15, 0, 30, tzinfo=IST)   # (the server's clock is 09:12 that morning)
        info = actions.note(self.cfg, "half past midnight", at, via="web", now=at)
        stem = Path(info["path"]).stem
        self.assertEqual(stem[:10], "2026-10-14")
        days = self.get_json("/api/v1/days?from=2026-10-14&to=2026-10-15")
        by_day = {d["date"]: [e["stem"] for e in d["entries"]] for d in days}
        self.assertIn(stem, by_day["2026-10-14"])
        self.assertNotIn(stem, by_day.get("2026-10-15", []))
        listed = self.get_json("/api/v1/entries?from=2026-10-14&to=2026-10-14")
        self.assertIn(stem, [e["stem"] for e in listed["items"]])
