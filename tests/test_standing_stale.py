"""STALE-COUNT (the owner, 2026-11-15): the companion is told how many annotation replies and reflection sends were
skipped as too late after an outage (``skipped: "stale"``), as she is of the "no chat" skips: counts only, in the
pack and the today block, for a day after the skip."""
import tempfile
import unittest
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import add_prompt, make_cfg

from journal import ledger, standing

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)


class StaleCountTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def skip(self, kind, local_date, why, at):
        add_prompt(self.cfg, kind=kind, local_date=local_date, sent=NOW - timedelta(days=2))
        with ledger.locked(self.cfg.ledger_path) as data:
            p = data["prompts"][-1]
            p.update(captures=[{"at": (NOW - timedelta(days=2)).isoformat(), "rule": "reply", "source": "text"}],
                     annotated_at=at.isoformat(),
                     annotation_reply={"skipped": why, "skipped_at": at.isoformat()})

    def reflection(self, day, why, at):
        with ledger.locked(self.cfg.ledger_path) as data:
            data.setdefault("reflections", []).append(
                {"id": f"{day}-reflection", "kind": "reflection", "local_date": day, "written_at": at.isoformat(),
                 "telegram": {"skipped": why, "skipped_at": at.isoformat()}})

    def test_counted_for_a_day_and_never_with_the_no_chat_skips(self):
        self.skip("morning", "2026-10-12", "stale", NOW - timedelta(hours=2))
        self.skip("evening", "2026-10-12", "stale", NOW - timedelta(hours=2))
        self.skip("morning", "2026-10-13", "no chat", NOW - timedelta(hours=2))  # the no-chat line's, not this one
        self.reflection("2026-10-12", "stale", NOW - timedelta(hours=1))
        self.reflection("2026-10-11", "stale", NOW - timedelta(days=3))  # an older outage's: said already
        stand = standing.collect(self.cfg, NOW)
        self.assertEqual(stand["stale"], {"annotations": 2, "reflections": 1})
        self.assertIn("- After an outage: 2 replies to his entries and 1 reflection weren't sent, too late; they are "
                      "in the entries.", standing.pack_lines(self.cfg, stand))
        self.assertIn("- after an outage: 2 replies to his entries and 1 reflection weren't sent, too late; they are "
                      "in the entries", standing.today_lines(self.cfg, stand))
        later = standing.collect(self.cfg, NOW + timedelta(days=1))
        self.assertEqual(later["stale"], {"annotations": 0, "reflections": 0})
        self.assertFalse([l for l in standing.pack_lines(self.cfg, later) if "outage" in l])

    def test_one_reply_and_the_pronoun_from_config(self):
        self.cfg.names = dataclasses.replace(self.cfg.names, pronouns="she/her/her")
        self.skip("morning", "2026-10-12", "stale", NOW - timedelta(minutes=5))
        self.assertIn("- after an outage: 1 reply to her entry wasn't sent, too late; it is in the entries",
                      standing.today_lines(self.cfg, standing.collect(self.cfg, NOW)))

    def test_nothing_skipped_says_nothing_and_a_damaged_mark_costs_only_itself(self):
        self.assertEqual(standing.collect(self.cfg, NOW)["stale"], {"annotations": 0, "reflections": 0})
        self.skip("morning", "2026-10-12", "stale", NOW)
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"][-1]["annotation_reply"]["skipped_at"] = "not a time"
            data.setdefault("reflections", []).append("not a record")
        self.skip("evening", "2026-10-12", "stale", NOW + timedelta(hours=1))  # a clock ahead: not counted
        stand = standing.collect(self.cfg, NOW)
        self.assertEqual(stand["stale"], {"annotations": 0, "reflections": 0})
        self.assertEqual(stand["unavailable"], [])


if __name__ == "__main__":
    unittest.main()
