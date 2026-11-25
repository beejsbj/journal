"""The index at the top of docs/DECISIONS.md is current, and the generator reads the document the way it says (#49)."""
import importlib.util
import unittest

from helpers import ROOT

spec = importlib.util.spec_from_file_location("gen_decisions_index", ROOT / "tools" / "gen_decisions_index.py")
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)

SAMPLE = """# Decisions

Intro.

| # | Decision | Reason |
| --- | --- | --- |
| 1 | **First.** More. | Why. |
| 2 | ~~**Second.**~~ **Superseded (row 1).** | Why. |

## Raised by packages (open, not decided)

- **ALPHA — an open point.** Words that go
  on over lines.

## Taken by packages (recorded here)

- **BETA (2026-11-01) — a decision with a
  wrapped lead.** Words.
- **BETA (2026-11-01) — a decision with a wrapped lead.** Same lead again.
"""


class DecisionsIndexTest(unittest.TestCase):
    def test_the_index_in_the_document_is_current(self):
        text = gen.DOC.read_text(encoding="utf-8")
        new, problems = gen.build(text)
        self.assertEqual(problems, [], "run `python3 tools/gen_decisions_index.py`")
        self.assertEqual(new, text)

    def test_every_entry_is_in_the_index_once_with_a_status(self):
        text = gen.DOC.read_text(encoding="utf-8")
        a, b = text.index(gen.START), text.index(gen.END)
        listed = [l for l in text[a:b].split("\n") if l.startswith("- [")]
        bullets = [l for l in text[b:].split("\n") if gen.BULLET.match(l)]
        rows = [l for l in text.split("\n") if gen.ROW.match(l)]
        self.assertEqual(len(listed), len(bullets) + len(rows))
        self.assertTrue(all(l.rsplit(" · ", 1)[1] in ("open", "decided", "superseded") for l in listed))

    def test_a_sample_gets_anchors_an_index_and_its_statuses(self):
        new, problems = gen.build(SAMPLE)
        self.assertTrue(problems)
        again, none = gen.build(new)
        self.assertEqual((again, none), (new, []))            # idempotent, and nothing left to say
        self.assertIn('| <a id="decision-1"></a>1 |', new)
        index = new[new.index(gen.START):new.index(gen.END)]
        self.assertIn("· superseded", index)                    # row 2 is struck through
        self.assertIn("(#decision-2) · superseded", index)
        self.assertIn("[ALPHA — an open point](#alpha-an-open-point) · open", index)
        self.assertIn("a decision with a wrapped lead](#beta-2026-11-01-a-decision-with-a-wrapped-lead) · decided", index)
        ids = [m.group(1) for m in gen.ANCHOR.finditer(new)]
        self.assertEqual(len(ids), len(set(ids)))              # the repeated lead got its own anchor
        self.assertIn("-2", "".join(ids))

    def test_an_existing_anchor_survives_an_edit_of_the_title(self):
        new, _ = gen.build(SAMPLE)
        edited = new.replace("an open point.", "a re-titled open point.")
        again, problems = gen.build(edited)
        self.assertIn('<a id="alpha-an-open-point"></a>', again)
        self.assertEqual(len(problems), 1)                     # only the index is stale, no anchor was made anew
        self.assertEqual(gen.build(again)[1], [])


if __name__ == "__main__":
    unittest.main()
