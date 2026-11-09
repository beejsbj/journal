"""``journal_search`` (DECISIONS.md, CONTINUITY): his words in the journal's files and nothing else, read-only and
allowed in any turn, registered apart in the ``journal_read`` toolset so the reflection can be given it alone."""
import json
import re
import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CHAT, ROOT, add_prompt, make_cfg

from journal import capture, conversations, entries, reflect, search, tools

IST = ZoneInfo("Asia/Kolkata")


class SearchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.entry("2026-09-20", "evening", "The harbour at dusk again. I walked the long way round.", "3")
        self.entry("2026-10-02", "morning", "Slept badly. The bus can wait; the harbour can't.", "4")
        self.entry("2026-10-14", "evening", "Rain all day, walked anyway. Mother called about the trip.", "5")
        ev = self.cfg.root / "2026-10-14-evening.md"
        entries.write_annotations(ev, "- He mentions the lighthouse keeper (her words).", self.cfg.names)
        entries.append_conversation(ev, conversations.her_block(self.cfg, datetime(2026, 10, 14, 18, 0, tzinfo=IST),
                                                                "The lighthouse sounds lovely.", key="r1"),
                                    self.cfg.names)
        reflect_path = self.cfg.root / "2026-10-14-reflection.md"
        entries.write_file(reflect_path, reflect.render(self.cfg, "reflection", date(2026, 10, 14),
                                                        "**What I notice** — the lighthouse, and the harbour.", [],
                                                        datetime(2026, 10, 14, 23, 0, tzinfo=IST)))
        (self.cfg.root / "2026-10-05-thoughts.md").write_text("my own file: the harbour was grey\n")
        (self.cfg.root / "harbour-notes.md").write_text("undated: harbour harbour harbour\n")

    def tearDown(self):
        self.tmp.cleanup()

    def entry(self, day, kind, words, mid):
        add_prompt(self.cfg, kind=kind, local_date=day,
                   sent=datetime.fromisoformat(f"{day}T06:00:00+00:00"), message_id=mid)
        capture.capture(self.cfg, CHAT, mid, words, datetime.fromisoformat(f"{day}T06:30:00+00:00"), "text",
                        str(int(mid) + 1000))

    def stems(self, query, **kw):
        return [r["entry"] for r in search.search(self.cfg, query, **kw)["results"]]

    def test_his_words_only(self):
        self.assertEqual(self.stems("harbour"), ["2026-10-05-thoughts", "2026-10-02-morning", "2026-09-20-evening"])
        self.assertEqual(self.stems("lighthouse"), [])  # her annotation, her chat reply, her reflection: never
        self.assertEqual(self.stems("Mother"), ["2026-10-14-evening"])

    def test_words_phrases_and_order(self):
        self.assertEqual(self.stems("walk"), ["2026-10-14-evening", "2026-09-20-evening"])  # walked: a word's start
        self.assertEqual(self.stems('"the bus can wait"'), ["2026-10-02-morning"])
        self.assertEqual(self.stems('"bus wait"'), [])
        self.assertEqual(self.stems("walked harbour")[0], "2026-09-20-evening")  # both words first
        self.assertEqual(self.stems("harbour dusk long"), ["2026-09-20-evening"])  # three words: at least two
        found = search.search(self.cfg, "dusk")["results"][0]
        self.assertEqual((found["date"], found["kind"], found["matched"]), ("2026-09-20", "evening", ["dusk"]))
        self.assertIn("The harbour at dusk again.", found["excerpts"][0])
        with self.assertRaises(search.SearchError):
            search.search(self.cfg, "the and of")  # only very common words

    def test_dates_limit_and_refusals(self):
        self.assertEqual(self.stems("harbour", date_from="2026-10-01"), ["2026-10-05-thoughts", "2026-10-02-morning"])
        self.assertEqual(self.stems("harbour", date_to="2026-10-01"), ["2026-09-20-evening"])
        out = search.search(self.cfg, "harbour", limit=1)
        self.assertEqual((len(out["results"]), out["more"]), (1, 2))
        for bad in ({"query": ""}, {"query": "x" * 201}, {"query": "harbour", "date_from": "last week"},
                    {"query": "harbour", "limit": "many"}):
            with self.subTest(bad), self.assertRaises(search.SearchError):
                search.search(self.cfg, bad.pop("query"), **bad)

    def test_the_tool_is_a_read_allowed_off_the_record(self):
        self.assertIsNone(tools.refusal(self.cfg, "journal_search", {"query": "harbour"}))
        with mock.patch.object(tools, "_off_record_turn", return_value=True):
            out = json.loads(tools.call(self.cfg, "journal_search", {"query": "harbour", "limit": 2}))
        self.assertTrue(out["success"])
        self.assertEqual([r["entry"] for r in out["results"]], ["2026-10-05-thoughts", "2026-10-02-morning"])
        bad = json.loads(tools.call(self.cfg, "journal_search", {"query": ""}))
        self.assertEqual(bad["success"], False)
        self.assertIn("query is empty", bad["error"])

    def test_it_never_opens_the_chat_or_writes(self):
        before = {p.name: p.read_bytes() for p in self.cfg.root.iterdir()}
        state = sorted(p.name for p in self.cfg.state_dir.rglob("*"))
        with mock.patch.object(sqlite3, "connect", side_effect=AssertionError("search read a database")), \
                mock.patch("journal.config.Config.transcript", side_effect=AssertionError("search read the chat")):
            self.assertTrue(search.search(self.cfg, "harbour")["results"])
            self.assertTrue(any("The harbour at dusk" in t for t in search.his_texts(self.cfg)))
        self.assertEqual({p.name: p.read_bytes() for p in self.cfg.root.iterdir()}, before)
        self.assertEqual(sorted(p.name for p in self.cfg.state_dir.rglob("*")), state)
        self.assertFalse(any("lighthouse" in t for t in search.his_texts(self.cfg)))

    def test_a_sync_conflict_copy_is_read_by_its_front_matter_not_whole(self):
        ev = self.cfg.root / "2026-10-14-evening.md"
        (self.cfg.root / "2026-10-14-evening (1).md").write_text(ev.read_text())
        refl = self.cfg.root / "2026-10-14-reflection.md"
        (self.cfg.root / "2026-10-14-reflection (1).md").write_text(refl.read_text())
        self.assertEqual(entries.his_text(self.cfg.root / "2026-10-14-evening (1).md"), entries.his_text(ev))
        self.assertEqual(entries.his_text(self.cfg.root / "2026-10-14-reflection (1).md"), "")
        self.assertEqual(self.stems("lighthouse"), [])  # her annotation and reflection, copied or not, are not his
        self.assertEqual(self.stems("Mother"), ["2026-10-14-evening (1)", "2026-10-14-evening"])
        mine = self.cfg.root / "2026-10-20-plans.md"
        mine.write_text("---\ntype: journal\n---\nmy own plans: the harbour first\n")  # his file, not entry-shaped
        self.assertIn("my own plans: the harbour first", entries.his_text(mine))

    def test_registered_apart_as_the_one_read_only_tool(self):
        from journal.config import Names
        specs = tools.specs(Names(user="Sam", companion="Ada"))
        self.assertEqual(tools.READ_ONLY, ("journal_search",))
        self.assertEqual(specs[-1].name, "journal_search")
        self.assertNotIn("journal_search", tools.WRITES)
        manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        listed = re.search(r"^provides_tools:\n((?:  - \S+\n)+)", manifest, re.M).group(1)
        self.assertEqual(sorted(l.strip("- \n") for l in listed.splitlines()), sorted(s.name for s in specs))


if __name__ == "__main__":
    unittest.main()
