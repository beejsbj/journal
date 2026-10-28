"""``write_summary`` never drops a block of his. Code before the programs (ff9d304) does not stop at the summary: a
reply to an old phase message lands below ``## Summary``. Re-completing that session after rolling forward must
move the block above the summary, not replace it."""
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from journal import entries

UTC = timezone.utc
STRAY = "### 09:15 UTC · text\n\nA late answer, in my own words.\nWith a second line and a `## not a heading` in it.\n"
HEADING = re.compile(r"(?m)^### \d{2}:\d{2}\b")


def prompt(n: int) -> dict:
    return {"stem": "2026-10-20-demo-s01", "local_date": "2026-10-20", "kind": "demo", "program": "demo", "run": "r1",
            "session": 1, "session_count": 2, "session_title": "First", "title": "Demo", "phase": n,
            "phase_title": f"P{n}", "phase_text": f"Question {n}"}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for n in (1, 2):
            entries.record_reply(self.root, prompt(n), f"Answer to phase {n}.", datetime(2026, 10, 20, 8, n, tzinfo=UTC))
        self.path = self.root / "2026-10-20-demo-s01.md"

    def stray(self, block: str = STRAY, key: str = "") -> None:
        """What the old code did: append below the summary, before the annotations."""
        body = self.path.read_text(encoding="utf-8")
        ann = entries._ANNOTATIONS.search(body)
        at = ann.start() if ann else len(body)
        body = body[:at].rstrip() + "\n\n" + key + block.rstrip("\n") + "\n\n" + body[at:]
        self.path.write_text(body, encoding="utf-8")

    def heads(self, body: str) -> int:
        return len(HEADING.findall(body))



class SummarySafety(Base):
    def test_a_stray_block_under_the_summary_survives_a_recompletion(self):
        entries.write_summary(self.path, "First summary.")
        self.stray(key=entries.phase_mark(2) + "\n")
        before = self.path.read_text(encoding="utf-8")
        self.assertLess(before.index("%% journal:summary %%"), before.index(STRAY))
        entries.write_summary(self.path, "Second summary.")
        after = self.path.read_text(encoding="utf-8")
        self.assertIn(STRAY, after)                       # byte-identical
        self.assertEqual(after.count(STRAY), 1)
        self.assertLess(after.index(STRAY), after.index("## Summary"))   # above the summary
        self.assertEqual(entries.summary_text(after), "Second summary.")
        self.assertNotIn("First summary.", after)
        # it joined phase 2's group, with its key, and is read as his phase 2 words
        self.assertEqual(entries.phase_blocks(after)[2][-1], STRAY.split("\n\n", 1)[1].strip())
        self.assertEqual(len(entries.phase_blocks(after)[2]), 2)
        self.assertEqual(entries.phase_blocks(after)[1], ["Answer to phase 1."])

    def test_an_unkeyed_stray_goes_just_above_the_summary_unchanged(self):
        entries.write_summary(self.path, "First summary.")
        self.stray()
        entries.write_summary(self.path, "Second summary.")
        after = self.path.read_text(encoding="utf-8")
        self.assertEqual(after.count(STRAY), 1)
        self.assertEqual(after[after.index(STRAY) + len(STRAY):].lstrip("\n").split("\n", 1)[0], "## Summary")

    def test_a_note_and_several_blocks_survive_in_order(self):
        entries.write_summary(self.path, "S.\n\n### Document title\n\nBody.")
        note = "### 09:20 UTC\n\nA companion note."
        second = "### 09:30 UTC · voice\n\n![[a.m4a]]\n\nSpoken."
        self.stray(STRAY, key=entries.phase_mark(1) + "\n")
        self.stray(note)
        self.stray(second, key=entries.phase_mark(2) + "\n")
        entries.write_summary(self.path, "Again.\n\n### Document title\n\nBody 2.")
        after = self.path.read_text(encoding="utf-8")
        for block in (STRAY, note, second):
            self.assertEqual(after.count(block.rstrip("\n")), 1, block)
            self.assertLess(after.index(block.rstrip("\n")), after.index("## Summary"))
        self.assertEqual(entries.summary_text(after), "Again.\n\n### Document title\n\nBody 2.")
        self.assertLess(after.index(STRAY), after.index(note))

    def test_a_stray_without_annotations_at_the_end_of_the_file(self):
        entries.write_summary(self.path, "First.")
        body = self.path.read_text(encoding="utf-8")
        ann = entries._ANNOTATIONS.search(body)
        self.path.write_text(body[:ann.start()].rstrip() + "\n\n" + STRAY, encoding="utf-8")
        entries.write_summary(self.path, "Second.")
        after = self.path.read_text(encoding="utf-8")
        self.assertEqual(after.count(STRAY.rstrip("\n")), 1)

    def test_write_summary_never_reduces_his_blocks(self):
        cases = [
            lambda: None,
            lambda: self.stray(key=entries.phase_mark(1) + "\n"),
            lambda: self.stray(),
            lambda: (self.stray(), self.stray("### 10:00 UTC · voice\n\nmore")),
        ]
        for i, setup in enumerate(cases):
            with self.subTest(case=i):
                entries.write_summary(self.path, "One.")
                setup()
                for text in ("Two.", "Three.\n\n## Heading in the copy\n\n### A document", "Four."):
                    n = self.heads(self.path.read_text(encoding="utf-8"))
                    entries.write_summary(self.path, text)
                    self.assertGreaterEqual(self.heads(self.path.read_text(encoding="utf-8")), n)

    def test_a_key_line_with_an_unusual_heading_still_survives(self):
        entries.write_summary(self.path, "First.")
        odd = "### an odd heading\n\nwords of his"
        self.stray(odd, key=entries.phase_mark(1) + "\n")
        entries.write_summary(self.path, "Second.")
        after = self.path.read_text(encoding="utf-8")
        self.assertEqual(after.count("words of his"), 1)
        self.assertLess(after.index("words of his"), after.index("## Summary"))
        self.assertEqual(entries.summary_text(after), "Second.")



def old_append_conversation(path: Path, blocks: str) -> None:
    """``append_conversation`` of ff9d304 for an entry that already has its ``## Conversation``: that code's
    sections do not stop at the summary, so the conversation ends at the annotations heading and the blocks land
    below the summary text (ff9d304:src/journal/entries.py, lines 367-371)."""
    body = path.read_text(encoding="utf-8")
    conv = entries._CONVERSATION.search(body)
    s, e = entries._start_of(body, conv), entries._ANNOTATIONS.search(body).start()
    middle = body[s:e].strip() + "\n\n" + blocks.strip() + "\n"
    rest = body[e:].lstrip("\n")
    path.write_text(body[:s] + "\n" + middle + ("\n" + rest if rest else ""), encoding="utf-8")


class ReviewCases(Base):
    """The review of the first version: the summary's own copy must not look like one of his blocks, old
    conversation lines below the summary are kept, a block is cut only at key lines, and an unkeyed block goes to
    the end of his words."""

    def read(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def test_a_time_heading_in_the_summary_text_is_not_taken_for_his_block(self):
        text = "Intro.\n\n### 10:30 standup\n\nSUMMARY-TAIL\n\nTEMPLATE-END"
        entries.write_summary(self.path, text)
        self.assertNotRegex(entries.summary_text(self.read()), r"(?m)^### [0-9]{1,2}:[0-9]{2}")
        entries.write_summary(self.path, text)
        entries.write_summary(self.path, "Rewritten.")
        after = self.read()
        self.assertNotIn("SUMMARY-TAIL", after)
        self.assertNotIn("TEMPLATE-END", entries.raw_entry(after))
        self.assertEqual(entries.phase_answers(after), {1: "Answer to phase 1.", 2: "Answer to phase 2."})

    def test_a_key_line_in_the_summary_text_is_not_kept(self):
        entries.write_summary(self.path, "Intro.\n%% journal phase:1 %%\nSUMMARY-TAIL")
        entries.write_summary(self.path, "Rewritten.")
        after = self.read()
        self.assertNotIn("SUMMARY-TAIL", after)
        self.assertEqual(entries.phase_blocks(after), {1: ["Answer to phase 1."], 2: ["Answer to phase 2."]})

    def test_his_heading_shaped_answer_copied_into_the_summary_does_not_grow(self):
        d = self.root / "grow"
        d.mkdir()
        entries.record_reply(d, prompt(1), "Today:\n\n# 7:00 woke up\n\nslept badly",
                             datetime(2026, 10, 20, 8, 1, tzinfo=UTC))
        path = d / "2026-10-20-demo-s01.md"

        def fill() -> str:
            return "Phase 1:\n\n" + entries.phase_answers(path.read_text(encoding="utf-8"))[1] + "\n\nTEMPLATE-END"

        for _ in range(3):
            entries.write_summary(path, fill())
        after = path.read_text(encoding="utf-8")
        self.assertEqual(after.count("slept badly"), 2)               # his block, and its one copy in the summary
        self.assertNotIn("TEMPLATE-END", entries.raw_entry(after))

    def test_an_unkeyed_stray_does_not_set_off_the_summary_chain(self):
        entries.write_summary(self.path, "First.")
        self.stray()                                                    # joins the last phase when moved

        def fill() -> str:
            a = entries.phase_answers(self.read())
            return f"P1: {a.get(1)}\n\nP2: {a.get(2)}\n\nTEMPLATE-END"

        for _ in range(3):
            entries.write_summary(self.path, fill())
        after = self.read()
        self.assertNotIn("TEMPLATE-END", entries.raw_entry(after))
        self.assertEqual(after.count("A late answer, in my own words."), 2)   # in his words once, in the summary once
        self.assertEqual(entries.phase_answers(after)[1], "Answer to phase 1.")

    def test_old_conversation_lines_below_the_summary_survive(self):
        entries.append_conversation(self.path, "%% journal his:c0 %%\n**08:30** — earlier chat")
        entries.write_summary(self.path, "First.")
        old_append_conversation(self.path, "%% journal his:c9 %%\n**09:40** — HIS-CONV after rollback\n\n"
                                           "%% journal her:c9 %%\n> [!quote]- Ada · 09:41\n> HER-REPLY")
        before = self.read()
        self.assertGreater(before.index("HIS-CONV"), before.index("%% journal:summary %%"))
        entries.write_summary(self.path, "Second.")
        after = self.read()
        for frag in ("HIS-CONV after rollback", "HER-REPLY", "earlier chat"):
            self.assertEqual(after.count(frag), 1, frag)
        self.assertLess(after.index("HER-REPLY"), after.index("## Summary"))
        self.assertLess(after.index("earlier chat"), after.index("HIS-CONV"))   # at the conversation's end
        self.assertIn("HIS-CONV", entries.his_conversation(after))
        self.assertNotIn("HER-REPLY", entries.his_conversation(after))          # still her callout
        self.assertEqual(entries.summary_text(after), "Second.")
        entries.write_summary(self.path, "Second.")
        self.assertEqual(self.read(), after)                                    # settled

    def test_a_time_heading_he_typed_inside_a_keyed_block_does_not_split_it(self):
        entries.write_summary(self.path, "First.")
        block = "My day:\n\n### 10:30 standup\n\nSPLIT-TAIL"
        self.stray("### 09:15 UTC · text\n\n" + block, key=entries.phase_mark(1) + "\n")
        entries.write_summary(self.path, "Second.")
        after = self.read()
        self.assertIn(block, after)
        self.assertNotIn("Answer to phase 2.", after[after.index("My day:"):after.index("SPLIT-TAIL")])
        self.assertEqual(entries.phase_blocks(after)[1][-1].endswith("SPLIT-TAIL"), True)

    def test_an_unkeyed_stray_goes_to_the_end_of_his_words_not_into_the_conversation(self):
        entries.append_conversation(self.path, "%% journal his:c0 %%\n**08:30** — chat above the summary")
        entries.write_summary(self.path, "First.")
        self.stray()
        entries.write_summary(self.path, "Second.")
        after = self.read()
        self.assertIn("A late answer, in my own words.", entries.raw_entry(after))
        self.assertLess(after.index(STRAY), after.index("## Conversation"))
        self.assertIn("chat above the summary", entries.his_conversation(after))

    def test_a_heading_he_typed_with_the_engines_shape_does_not_end_the_stray_region(self):
        for typed in ("## Today's notes", "## Someone's annotations"):
            with self.subTest(typed=typed):
                self.setUp()
                entries.write_summary(self.path, "First.")
                self.stray(f"### 09:15 UTC · text\n\nBEFORE-TYPED\n\n{typed}\n\nAFTER-TYPED")
                entries.add_companion_note(self.path, "REAL-NOTE", datetime(2026, 10, 20, 11, 0, tzinfo=UTC))
                for text in ("Second.", "Third."):
                    entries.write_summary(self.path, text)
                    after = self.read()
                    mark = after.index("%% journal:summary %%")
                    for frag in ("BEFORE-TYPED", "AFTER-TYPED"):
                        self.assertEqual(after.count(frag), 1, frag)
                        self.assertLess(after.index(frag), mark, frag)
                        self.assertIn(frag, entries.raw_entry(after), frag)
                    self.assertEqual(after.count("REAL-NOTE"), 1)
                    self.assertGreater(after.index("REAL-NOTE"), mark)           # the real notes stay after
                    self.assertEqual(entries.summary_text(after), text)
