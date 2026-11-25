"""The day log (issue #82): one journal day's lines from every entry filed under it, in time order, read-only.
``daylog.day`` and ``GET /api/v1/days/<date>``."""
import hashlib
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import test_conversations as tc
from helpers import CHAT, add_prompt, make_cfg
from serve_helpers import ServeCase

from journal import actions, capture, daylog

IST = ZoneInfo("Asia/Kolkata")
m = tc.m   # minutes after 19:30 IST, Thu 15 Oct 2026


def checksums(root: Path) -> dict:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*"))}


class DayLogTest(tc.Base):
    """A note, a web note, and an evening prompt answered with a conversation that runs past midnight."""

    def setUp(self):
        super().setUp()
        info = capture.capture_note(self.cfg, "a line at dinner", m(0), "text", "540")
        self.his(capture.note_context(self.cfg, info) + "a line at dinner", m(0), "540")
        actions.note(self.cfg, "a web line before bed", m(150), via="web", now=m(150))   # 22:00
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(200), message_id="600")
        capture.capture(self.cfg, CHAT, "600", "the day was long", m(220), "text", "541")   # 23:10
        self.his('[Replying to: "Evening journal"]\n\n[Journal reply — saved …]\n\nthe day was long', m(220), "541")
        self.hers("Long how?", m(221))
        self.his("the bank, mostly", m(250), "542")    # 23:40
        self.hers("Ah, the bank.", m(251))
        self.his("and the rain", m(275), "543")        # 00:05 on Fri 16
        self.hers("Rain helps.", m(276))
        self.tick(m(330))

    def test_every_line_of_the_day_in_time_order(self):
        before = checksums(self.cfg.root)
        log = daylog.day(self.cfg, "2026-10-15")
        self.assertEqual(checksums(self.cfg.root), before)   # (read-only)
        got = [(l["time"], l["kind"], l["stem"], l["text"].strip()) for l in log["lines"]]
        self.assertEqual(got, [
            ("19:30", "his", "2026-10-15-j", "a line at dinner"),
            ("22:00", "his", "2026-10-15-j-2", "a web line before bed"),
            ("23:10", "his", "2026-10-15-evening", "the day was long"),
            ("23:11", "hers", "2026-10-15-evening", "Long how?"),
            ("23:40", "his", "2026-10-15-evening", "the bank, mostly"),
            ("23:41", "hers", "2026-10-15-evening", "Ah, the bank."),
            ("00:05", "his", "2026-10-15-evening", "and the rain"),
            ("00:06", "hers", "2026-10-15-evening", "Rain helps."),
        ])
        self.assertEqual([l["date"] for l in log["lines"]][-2:], ["2026-10-16", "2026-10-16"])
        self.assertEqual(log["lines"][0]["date"], "2026-10-15")
        self.assertEqual((log["date"], log["weekday"]), ("2026-10-15", "Thu"))
        self.assertEqual([e["stem"] for e in log["entries"]], ["2026-10-15-j", "2026-10-15-j-2", "2026-10-15-evening"])
        self.assertTrue(all(l["key"] for l in log["lines"]))   # (each line links to its block)

    def test_her_lines_are_hers_and_carry_no_fields_of_his(self):
        hers = [l for l in daylog.day(self.cfg, "2026-10-15")["lines"] if l["kind"] == "hers"]
        self.assertEqual(len(hers), 3)
        self.assertTrue(all("source" not in l and "audio" not in l for l in hers))

    def test_an_empty_day_and_its_neighbours(self):
        log = daylog.day(self.cfg, "2026-10-14")
        self.assertEqual((log["lines"], log["entries"], log["prev"], log["next"]), ([], [], None, "2026-10-15"))
        self.assertEqual(daylog.day(self.cfg, "2026-10-15")["prev"], None)


class AddAfterTalkTest(tc.Base):
    """An *Add* goes to the words section, below which a conversation may already have begun: the two sections are
    placed apart, and a block the ledger has a capture for is placed on that capture's day."""

    def setUp(self):
        super().setUp()
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(80), message_id="600")   # 20:50
        capture.capture(self.cfg, CHAT, "600", "the words", m(90), "text", "541")                     # 21:00
        self.his('[Replying to: "Evening journal"]\n\n[Journal reply — saved …]\n\nthe words', m(90), "541")
        self.hers("And then?", m(91))                                                                  # 21:01
        self.his("and then the talk", m(95), "542")                                                    # 21:05
        self.tick(m(150))
        self.stem = "2026-10-15-evening"

    def got(self, local_date="2026-10-15"):
        return [(l["date"], l["time"], l["kind"], l["text"].strip()) for l in daylog.day(self.cfg, local_date)["lines"]]

    def test_an_add_after_the_conversation_moves_none_of_it(self):
        actions.add(self.cfg, self.stem, "one more thing", via="web", now=m(120))                      # 21:30
        self.assertEqual(self.got(), [
            ("2026-10-15", "21:00", "his", "the words"),
            ("2026-10-15", "21:01", "hers", "And then?"),
            ("2026-10-15", "21:05", "his", "and then the talk"),
            ("2026-10-15", "21:30", "his", "one more thing"),
        ])

    def test_an_add_days_later_is_placed_on_its_own_day(self):
        sunday = datetime(2026, 10, 18, 21, 30, tzinfo=IST)
        actions.add(self.cfg, self.stem, "on Sunday", via="web", now=sunday)
        self.assertEqual(self.got()[-1], ("2026-10-18", "21:30", "his", "on Sunday"))
        self.assertEqual([d for d, *_ in self.got()[:-1]], ["2026-10-15"] * 3)

    def test_a_backdated_add_earlier_than_the_block_before_it_stays_on_its_day(self):
        actions.add(self.cfg, self.stem, "before it all", at=datetime(2026, 10, 15, 18, 0, tzinfo=IST), via="web",
                    now=m(200))
        self.assertEqual(self.got()[0], ("2026-10-15", "18:00", "his", "before it all"))


class PlacingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))   # day_ends_at 04:00
        self.day = date(2026, 10, 15)

    def place(self, ats, start=None):
        return [(d.isoformat() if d else None, t) for d, t in
                daylog._placed(self.cfg, [{"at": a} for a in ats], self.day, start)]

    def test_an_hour_earlier_than_the_last_is_the_next_day(self):
        self.assertEqual(self.place(["23:12 IST", "23:40", "00:10", "08:00 IST"]),
                         [("2026-10-15", "23:12"), ("2026-10-15", "23:40"), ("2026-10-16", "00:10"),
                          ("2026-10-16", "08:00")])

    def test_a_dated_marker_names_its_day(self):
        self.assertEqual(self.place(["21:00 IST", "Fri 16 Oct 00:30", "00:45"]),
                         [("2026-10-15", "21:00"), ("2026-10-16", "00:30"), ("2026-10-16", "00:45")])

    def test_an_entry_that_starts_before_day_ends_at_is_the_night_after_its_date(self):
        self.assertEqual(self.place(["00:30 IST"]), [("2026-10-16", "00:30")])
        self.assertEqual(self.place(["04:00 IST"]), [("2026-10-15", "04:00")])

    def test_the_ledger_says_when_it_knows(self):
        """A note filed under its calendar date at 00:30 (before notes followed the journal day) is that morning's."""
        self.assertEqual(self.place(["00:30 IST"], start=self.day), [("2026-10-15", "00:30")])

    def test_a_block_with_no_time_keeps_no_time(self):
        self.assertEqual(self.place([None, "09:00 IST"]), [(None, None), ("2026-10-15", "09:00")])

    def test_the_words_and_the_conversation_are_placed_apart(self):
        """Words 21:00, her 21:01, his 21:05, an *Add* 21:30: the Add sits in the words, above the conversation."""
        blocks = [{"key": "words:0", "at": "21:00 IST"}, {"key": "words:1", "at": "21:30 IST"},
                  {"key": "her:a", "at": "21:01"}, {"key": "his:b", "at": "21:05"}]
        self.assertEqual([(d.isoformat(), t) for d, t in daylog._placed(self.cfg, blocks, self.day, None)],
                         [("2026-10-15", "21:00"), ("2026-10-15", "21:30"), ("2026-10-15", "21:01"),
                          ("2026-10-15", "21:05")])

    def test_a_capture_the_ledger_has_names_the_day(self):
        """With the ledger: a block added on Sunday is Sunday's; without, file order alone (the shortcut)."""
        blocks = [{"key": "words:0", "at": "19:30 IST"}, {"key": "words:1", "at": "21:30 IST"},
                  {"key": "words:2", "at": "18:00 IST"}]
        caps = ([datetime(2026, 10, 15, 19, 30, tzinfo=IST), datetime(2026, 10, 18, 21, 30, tzinfo=IST),
                 datetime(2026, 10, 15, 18, 0, tzinfo=IST)], [])
        self.assertEqual([(d.isoformat(), t) for d, t in daylog._placed(self.cfg, blocks, self.day, None, caps)],
                         [("2026-10-15", "19:30"), ("2026-10-18", "21:30"), ("2026-10-15", "18:00")])
        self.assertEqual([(d.isoformat(), t) for d, t in daylog._placed(self.cfg, blocks, self.day, None)],
                         [("2026-10-15", "19:30"), ("2026-10-15", "21:30"), ("2026-10-16", "18:00")])

    def test_a_dated_marker_across_the_new_year(self):
        self.day = date(2026, 12, 31)
        self.assertEqual(self.place(["23:00 IST", "Fri 1 Jan 00:20"]), [("2026-12-31", "23:00"), ("2027-01-01", "00:20")])


class DayRouteTest(ServeCase):
    def test_the_route(self):
        log = self.get_json("/api/v1/days/2026-10-14")
        self.assertEqual(log["date"], "2026-10-14")
        self.assertIn("voices", log)
        self.assertTrue(log["lines"])
        self.assertTrue(all(l["stem"].startswith("2026-10-14-") for l in log["lines"]))
        keys = [(l["date"] or "", l["time"] or "") for l in log["lines"] if l["time"]]
        self.assertEqual(keys, sorted(keys))

    def test_a_bad_date(self):
        self.get_json("/api/v1/days/2026-13-40", status=400)
        self.assertEqual(self.request("GET", "/api/v1/days/yesterday")[0], 404)


if __name__ == "__main__":
    unittest.main()
