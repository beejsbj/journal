"""Persona and user naming: headings, wording, and tolerant parsing of files written under other names."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import PERSON, add_prompt, make_cfg

from journal import capture, compose, context, entries, prompts
from journal.config import Config, Names

OLD_IRIS_FILE = """---
date: 2026-10-13
kind: evening
type: journal
source: iris-journal
---

# Evening journal — Tue 13 Oct 2026

## Prompt

Evening 🌻

## Theo

### 23:10 IST · text

Long day at the lake.

## Iris's annotations

_Pending._

## Iris's notes

### 23:40 IST

Worth a follow-up (inference).
"""


class NeutralDefaultsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), names=Names())
        self.sent = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_unnamed_user_and_default_companion(self):
        add_prompt(self.cfg, sent=self.sent)
        info = capture.capture(self.cfg, self.cfg.chat_id, "100", "words", self.sent + timedelta(hours=1))
        body = Path(info["path"]).read_text()
        self.assertIn("## Entry\n", body)
        self.assertIn("## Companion's annotations", body)
        self.assertIn("source: journal", body)
        self.assertNotIn("Iris", body)
        self.assertNotIn("Theo", body)
        self.assertIn("as the user's answer", capture.context_note(self.cfg, info))
        self.assertIn("Respond as Companion", capture.context_note(self.cfg, info))

    def test_plain_openers_without_a_name(self):
        self.assertEqual(compose.plain_opener("evening", Names(emoji="")),
                         "Evening. Let the day land. Answer whatever pulls at you; you don't need to do them all.")
        self.assertTrue(compose.plain_opener("morning", Names(user="Sam", emoji="")).startswith("Morning, Sam. Start"))
        self.assertTrue(compose.plain_opener("morning", Names()).startswith("Morning 📓 Start"))
        self.assertTrue(compose.header("weekly", datetime(2026, 10, 18), names=Names(emoji="")).startswith("Weekly"))

    def test_prompts_use_pronouns(self):
        theirs = prompts.opener_request(self.cfg, "evening", datetime(2026, 10, 14, 23, 0), "PACK", "BANK")
        self.assertIn("You are Companion, writing the opening of the user's evening journal prompt.", theirs)
        self.assertIn("Their question set", theirs)
        self.assertIn("Just talk to them.", theirs)
        self.cfg.names = PERSON
        his = prompts.opener_request(self.cfg, "evening", datetime(2026, 10, 14, 23, 0), "PACK", "BANK")
        self.assertIn("You are Iris, writing the opening of Theo's evening journal prompt.", his)
        self.assertIn("His question set", his)
        self.assertIn("Just talk to him.", his)
        ann = prompts.annotation_request(self.cfg, "ENTRY", None)
        self.assertIn("for Theo's later review", ann)
        self.assertIn("in his raw entry", ann)

    def test_reading_guidance_configurable(self):
        self.assertIn("The pack's last section says where to look further", prompts.reading_guidance(self.cfg))
        self.cfg.prompts["reading_guidance"] = "Look in the vault."
        self.assertIn("Look in the vault.", prompts.opener_request(self.cfg, "morning", datetime(2026, 10, 14, 6, 0),
                                                                   "P", "B"))


class TolerantParsingTest(unittest.TestCase):
    """A file written as Iris/Theo still parses under any other names, and vice versa."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "2026-10-13-evening.md"
        self.path.write_text(OLD_IRIS_FILE)

    def tearDown(self):
        self.tmp.cleanup()

    def test_reads_old_headings(self):
        body = self.path.read_text()
        self.assertIn("Long day at the lake.", entries.raw_entry(body))
        self.assertTrue(entries.annotations_pending(body))

    def test_writes_into_old_headings_under_new_names(self):
        sam = Names(user="Sam", companion="Ada")
        entries.write_annotations(self.path, "- A long day (inference).", sam)
        prompt = {"stem": "2026-10-13-evening", "kind": "evening", "local_date": "2026-10-13"}
        entries.record_reply(self.path.parent, prompt, "And a late swim.", datetime(2026, 10, 14, 0, 5), names=sam)
        entries.add_companion_note(self.path, "Another note.", datetime(2026, 10, 14, 0, 6), names=sam)
        body = self.path.read_text()
        self.assertEqual(body.count("## Iris's annotations"), 1)
        self.assertNotIn("Ada's", body)  # existing headings are reused, not duplicated
        self.assertNotIn("## Sam", body)
        self.assertLess(body.index("And a late swim."), body.index("## Iris's annotations"))
        self.assertIn("- A long day (inference).", entries.annotations(body))
        self.assertTrue(body.rstrip().endswith("Another note."))
        self.assertIn("Worth a follow-up (inference).", body)

    def test_context_pack_reads_annotations_under_any_name(self):
        cfg = Config(root=self.path.parent, names=Names(companion="Ada"))
        entries.write_annotations(self.path, "- Lake.")
        out = context.journal_entries(cfg)
        self.assertEqual(len(out), 1)
        self.assertIn("_Ada's annotations:_ - Lake.", out[0])

    def test_curly_apostrophe_headings(self):
        self.path.write_text(OLD_IRIS_FILE.replace("Iris's annotations", "Iris’s annotations"))
        self.assertTrue(entries.annotations_pending(self.path.read_text()))


class AnnotationHtmlTest(unittest.TestCase):
    def test_header_and_bullets(self):
        html = compose.annotation_html("evening", "- He felt **tired** & flat.\n  - sub point\n- Second", PERSON)
        self.assertTrue(html.startswith("<b>🌻 Iris's notes on your evening entry</b>\n\n"))
        self.assertIn("• He felt <b>tired</b> &amp; flat.", html)
        self.assertIn("    ◦ sub point", html)
        self.assertIn("• Second", html)
        self.assertIn("on your note</b>", compose.annotation_html("j", "- x", PERSON))

    def test_long_annotations_cut_on_a_line(self):
        text = "\n".join(f"- point {i} " + "x" * 80 for i in range(200))
        html = compose.annotation_html("morning", text, PERSON)
        self.assertLessEqual(len(html), compose.TELEGRAM_LIMIT)
        self.assertTrue(html.endswith("<i>… the rest is in the entry.</i>"))


if __name__ == "__main__":
    unittest.main()
