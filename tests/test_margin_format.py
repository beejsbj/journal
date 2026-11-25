"""MARGIN-FORMAT (issue #31, DECISIONS.md): the entry format margin notes stand on.

* Every block of his the engine writes carries a stable key (``%% journal block:<id> %%`` above its heading, above a
  program's answer key), which survives appends, a web edit of the words and lines under the heading; deleting the
  block removes it.
* A margin note is one record at the end of her annotations section: its key line (id, when, state, anchor), her words
  in a callout, the thread in the conversation's grammar. ``margin.parse`` reads back what ``margin.render`` writes.
* Her words in the margin are hers and never his: every reader of his words leaves them out (search, the quote
  check's corpus, packs, reflections, the annotation input, the document, MCP); his replies in a thread are his.
* An entry without a margin reads exactly as before, and nothing in the engine writes a note yet.
"""
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import PERSON, make_cfg

from journal import document, entries, margin, reflect
from journal.mcp import handlers

IST = timezone(timedelta(hours=5, minutes=30), "IST")
AT = datetime(2026, 11, 25, 9, 12, tzinfo=IST)
HER = "ZEPHYRHERS"        # a word only she says, in her note
HER_REPLY = "QUILLHERS"   # a word only she says, in her reply in the thread
HIS = "The sea was loud.\n\nThen it went quiet."
HIS_REPLY = "The gull, mostly."
PROMPT = {"stem": "2026-11-25-evening", "kind": "evening", "local_date": "2026-11-25"}


def note_md(body: str, anchor: str, state: str = "open", text: str = f"Which part of the quiet stayed? {HER}",
            thread: bool = True) -> str:
    n = margin.Note(id=margin.new_id(body, AT + timedelta(minutes=2), anchor, text), anchor=anchor,
                    at=(AT + timedelta(minutes=2)).isoformat(), state=state,
                    closed=(AT + timedelta(minutes=20)).isoformat() if state != "open" else None, text=text)
    out = margin.render(n, PERSON.companion, "09:14", "09:12")
    if thread:   # (written by hand here: nothing in the engine writes a thread yet)
        out += (f"\n%% journal his:web-1 %%\n**09:20** — {HIS_REPLY}\n\n"
                f"%% journal her:{n.id}-2 %%\n> [!note]- {PERSON.companion} · 09:21\n> Late ones sound surprised. "
                f"{HER_REPLY}\n")
    return out


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.path = entries.record_reply(self.cfg.root, PROMPT, HIS, AT, names=PERSON)

    def tearDown(self):
        self.tmp.cleanup()

    def body(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def key(self) -> str:
        return entries.BLOCK_KEY.search(self.body()).group(1)

    def with_margin(self, analysis: str = "- He comes back to the sea.", **kw) -> str:
        """The entry with her analysis and one note on his block, as a writer of MARGIN-NOTES would leave it."""
        entries.write_annotations(self.path, analysis, PERSON)
        body = self.body()
        span = entries.annotations_span(body)
        notes = margin.section([note_md(body, f"block:{self.key()}", **kw)])
        body = body[:span[1]].rstrip("\n") + "\n\n" + notes + ("\n" + body[span[1]:] if body[span[1]:] else "")
        self.path.write_text(body, encoding="utf-8")
        return body


class BlockKeyTest(Case):
    def test_a_block_opens_with_its_key_line(self):
        body = self.body()
        self.assertRegex(body, r"\n%% journal block:b[0-9a-f]{8} %%\n### 09:12 IST · text\n\nThe sea was loud.")
        self.assertNotIn("%% journal", entries.his_words(body))    # the key is the engine's, never his words

    def test_one_capture_one_key_and_a_second_block_another(self):
        first = self.key()
        entries.record_reply(self.cfg.root, PROMPT, "A gull, late.", AT + timedelta(minutes=5), names=PERSON)
        keys = entries.BLOCK_KEY.findall(self.body())
        self.assertEqual(keys[0], first)                  # an append leaves the first key as it was
        self.assertEqual(len(set(keys)), 2)
        self.assertEqual(entries.new_block_key("", AT, "text", HIS), first)   # the same capture, the same key

    def test_the_same_words_in_the_same_second_get_a_key_of_their_own(self):
        entries.record_reply(self.cfg.root, PROMPT, HIS, AT, names=PERSON)
        a, b = entries.BLOCK_KEY.findall(self.body())
        self.assertEqual(b, f"{a}-2")

    def test_the_document_gives_each_block_its_anchor(self):
        blocks = document.blocks_of(self.body(), self.path.stem)
        self.assertEqual([(b["key"], b["anchor"], b["text"]) for b in blocks],
                         [("words:0", f"block:{self.key()}", HIS)])

    def test_his_edit_keeps_the_key(self):
        from journal.serve import blocks as web
        new = web.edit(self.body(), "words:0", "The sea was loud, then not.", cfg=self.cfg, stem=self.path.stem)
        self.assertIn(f"%% journal block:{self.key()} %%\n### 09:12 IST · text\n\nThe sea was loud, then not.", new)
        self.assertEqual(document.blocks_of(new, self.path.stem)[0]["anchor"], f"block:{self.key()}")

    def test_a_line_under_the_heading_leaves_the_anchor(self):
        # (PARAGRAPH-TIMES, #74, puts its hidden line under the heading; the key above it is untouched)
        body = self.body().replace("### 09:12 IST · text\n", "### 09:12 IST · text\n%% journal times:08:32 08:42 %%\n")
        (block,) = document.blocks_of(body, self.path.stem)
        self.assertEqual((block["anchor"], block["text"]), (f"block:{self.key()}", HIS))

    def test_deleting_the_block_takes_its_key(self):
        entries.record_reply(self.cfg.root, PROMPT, "A gull, late.", AT + timedelta(minutes=5), names=PERSON)
        first = self.key()
        from journal.serve import blocks as web
        new = web.remove(self.body(), "words:0", stem=self.path.stem)
        self.assertNotIn(first, new)
        self.assertEqual([b["text"] for b in document.blocks_of(new, self.path.stem)], ["A gull, late."])

    def test_a_block_written_before_keys_has_no_anchor(self):
        old = re.sub(r"%% journal block:\S+ %%\n", "", self.body())
        self.assertEqual(document.blocks_of(old, self.path.stem)[0]["anchor"], None)

    def test_a_conversation_message_is_anchored_by_its_own_key(self):
        entries.append_conversation(self.path, "%% journal his:813 %%\n**23:44** — Later, the harbour.\n", PERSON)
        blocks = document.blocks_of(self.body(), self.path.stem)
        self.assertEqual(blocks[-1]["anchor"], "his:813")


class ProgramBlockTest(unittest.TestCase):
    """A program's answer: the block's key above its ``phase:N`` line, which stays right above the heading."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.prompt = {"stem": "2026-11-25-fix-life-s01", "kind": "fix-life", "local_date": "2026-11-25",
                       "program": "fix-life", "session": 1, "session_count": 3, "run": "r1", "phase": 1,
                       "title": "Fix your life", "session_title": "Start", "phase_text": "What hurts?"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_phase_answers_and_the_document_read_as_before(self):
        path = entries.record_reply(self.cfg.root, self.prompt, "First answer.", AT, names=PERSON)
        entries.record_reply(self.cfg.root, self.prompt, "Second answer.", AT + timedelta(minutes=3), names=PERSON)
        body = path.read_text(encoding="utf-8")
        self.assertRegex(body, r"%% journal block:b[0-9a-f]{8} %%\n%% journal phase:1 %%\n### 09:12 IST · text")
        self.assertEqual(entries.phase_blocks(body), {1: ["First answer.", "Second answer."]})
        blocks = document.blocks_of(body, path.stem)
        self.assertEqual([(b["phase"], b["text"], bool(b["anchor"])) for b in blocks],
                         [(1, "First answer.", True), (1, "Second answer.", True)])


class MarginNoteTest(Case):
    def test_render_then_parse_is_the_same_note(self):
        n = margin.Note(id="n1a2b3c4d", anchor="block:b3f9a2c10", at=AT.isoformat(), state="resolved",
                        closed=(AT + timedelta(hours=1)).isoformat(), text="One line.\n\nAnd a second, with **bold**.",
                        extra={"mode": "live"})
        md = margin.render(n, PERSON.companion, "09:12", "09:10")
        self.assertEqual(md.splitlines()[0], f"%% journal note:n1a2b3c4d at:{AT.isoformat()} state:resolved "
                                             f"closed:{(AT + timedelta(hours=1)).isoformat()} mode:live "
                                             f"on:block:b3f9a2c10 %%")
        self.assertEqual(md.splitlines()[1], f"> [!note]- {PERSON.companion} · 09:12 · on 09:10 · resolved")
        (got,) = margin.parse(md)
        self.assertEqual((got.id, got.anchor, got.at, got.state, got.closed, got.text, got.extra),
                         (n.id, n.anchor, n.at, n.state, n.closed, n.text, n.extra))
        self.assertEqual(margin.render(got, PERSON.companion, "09:12", "09:10"), md)

    def test_an_open_note_stands_open_and_her_engine_lines_never_reach_the_file(self):
        n = margin.Note(id="n1", anchor="entry", at=AT.isoformat(), text="## Big\n%% journal block:bad %%\nWords.")
        md = margin.render(n, PERSON.companion, "09:12")
        self.assertEqual(md, f"%% journal note:n1 at:{AT.isoformat()} state:open on:entry %%\n"
                             f"> [!note] {PERSON.companion} · 09:12\n> ### Big\n> Words.\n")

    def test_render_refuses_what_no_reader_could_read(self):
        with self.assertRaises(ValueError):
            margin.render(margin.Note(id="n1", anchor="entry", at="x", state="gone"), "Iris", "09:12")
        with self.assertRaises(ValueError):
            margin.render(margin.Note(id="n1", anchor="words:0", at="x"), "Iris", "09:12")

    def test_a_note_and_its_thread_read_back_from_the_entry(self):
        body = self.with_margin()
        (n,) = margin.notes(body)
        self.assertEqual((n.anchor, n.state, n.text), (f"block:{self.key()}", "open",
                                                       f"Which part of the quiet stayed? {HER}"))
        (doc_note,) = document.margin_of(body, self.path.stem)
        self.assertEqual([(b["kind"], b["key"], b["at"], b["text"]) for b in doc_note["thread"]],
                         [("his", "his:web-1", "09:20", HIS_REPLY),
                          ("hers", f"her:{n.id}-2", "09:21", f"Late ones sound surprised. {HER_REPLY}")])
        self.assertIsNone(doc_note["thread"][0]["anchor"])   # a reply in a thread is no block of the entry
        # the note's anchor is one of the document's blocks
        self.assertIn(doc_note["anchor"], [b["anchor"] for b in document.blocks_of(body, self.path.stem)])

    def test_unknown_state_reads_open_and_a_key_line_without_her_callout_is_no_note(self):
        md = (f"%% journal note:n1 at:{AT.isoformat()} state:later on:entry %%\n> [!note] Iris · 09:12\n> Hi.\n\n"
              f"%% journal note:n2 at:{AT.isoformat()} state:open on:entry %%\nnothing of hers here\n")
        self.assertEqual([(n.id, n.state) for n in margin.parse(md)], [("n1", "open")])

    def test_an_anchor_may_hold_a_space(self):
        md = f"%% journal note:n1 at:{AT.isoformat()} state:open on:his:row:12 34 %%\n> [!note] Iris · 09:12\n> Hi.\n"
        self.assertEqual(margin.parse(md)[0].anchor, "his:row:12 34")


class OldEntriesTest(Case):
    def test_an_annotations_section_without_a_margin_reads_exactly_as_before(self):
        entries.write_annotations(self.path, "- a theme.\n\n### Margin notes\n- a heading she typed", PERSON)
        body = self.body()
        span = entries.annotations_span(body)
        self.assertEqual(entries.annotations(body), body[span[0]:span[1]].strip())   # all of it is her analysis
        self.assertIsNone(entries.margin_span(body))
        self.assertEqual(document.margin_of(body), [])

    def test_her_analysis_is_replaced_and_the_margin_carried_over_byte_for_byte(self):
        body = self.with_margin(analysis=entries.PENDING)
        self.assertTrue(entries.annotations_pending(body))   # the margin is not her analysis
        self.assertEqual(entries.annotations(body), "")
        before = entries.margin(body)
        entries.write_annotations(self.path, "- the analysis, now.", PERSON)
        after = self.body()
        self.assertEqual(entries.margin(after), before)
        self.assertEqual(entries.annotations(after), "- the analysis, now.")
        self.assertFalse(entries.annotations_pending(after))


class HersNeverHisTest(Case):
    def test_her_margin_words_are_in_no_reader_of_his_words(self):
        body = self.with_margin()
        for name, text in (("his_words", entries.his_words(body)), ("entry_words", entries.entry_words(body)),
                           ("his_text", entries.his_text(self.path)), ("raw_entry", entries.raw_entry(body)),
                           ("annotation input", entries.without_companion_replies(body)),
                           ("annotations", entries.annotations(body))):
            with self.subTest(name):
                self.assertNotIn(HER, text)
                self.assertNotIn(HER_REPLY, text)
                self.assertNotIn("%% journal", text)
        self.assertFalse(document.matches(self.path, HER))
        self.assertFalse(document.matches(self.path, HER_REPLY))

    def test_his_reply_in_a_thread_is_his_words(self):
        body = self.with_margin()
        self.assertIn(HIS_REPLY, entries.his_words(body))
        self.assertIn(HIS_REPLY, entries.his_text(self.path))
        self.assertTrue(document.matches(self.path, "gull, mostly"))
        self.assertNotIn(HIS_REPLY, entries.raw_entry(body))   # not one of the blocks of his words section

    def test_the_document_keeps_the_voices_apart(self):
        self.with_margin()
        doc = document.document(self.cfg, self.path)
        self.assertEqual(doc["analysis"]["annotations"], "- He comes back to the sea.")
        self.assertTrue(all(HER not in b["text"] for b in doc["blocks"]))
        (n,) = doc["analysis"]["margin"]
        self.assertEqual((n["state"], n["anchor"]), ("open", f"block:{self.key()}"))
        self.assertIn(HER, n["text"])

    def test_a_reflection_reads_her_notes_as_hers_and_his_replies_as_his(self):
        self.with_margin()
        text = reflect._entry_text(self.cfg, self.path)
        self.assertRegex(text, rf"\[{PERSON.companion}'s margin note, 09:14 · on 09:12: her words, not "
                               rf"{PERSON.user}'s\] Which part of the quiet stayed\? {HER}")
        self.assertRegex(text, rf"\[{PERSON.companion}'s reply in the margin, 09:21: her words, not "
                               rf"{PERSON.user}'s\] Late ones sound surprised. {HER_REPLY}")
        self.assertIn(f"[{PERSON.user}'s reply in the margin: his words]\n**09:20** — {HIS_REPLY}", text)
        self.assertNotIn("%% journal", text)

    def test_mcp_labels_each_voice(self):
        self.with_margin(state="dismissed")
        doc = document.document(self.cfg, self.path)
        text = handlers.render_document(doc)
        she = f"**{PERSON.companion} (companion, not his words)**"
        he = f"**{PERSON.user} (his words)**"
        self.assertIn(f"## Margin note — {she} · on his block block:{self.key()} · dismissed\n\n> Which part", text)
        self.assertIn(f"{he} · 09:20\n> {HIS_REPLY}", text)
        self.assertIn(f"{she} · 09:21\n> Late ones sound surprised. {HER_REPLY}", text)

    def test_a_web_edit_of_his_block_leaves_the_margin_as_it_was(self):
        body = self.with_margin()
        from journal.serve import blocks as web
        new = web.edit(body, "words:0", "Only the sea.", cfg=self.cfg, stem=self.path.stem)
        self.assertEqual(entries.margin(new), entries.margin(body))


if __name__ == "__main__":
    unittest.main()
