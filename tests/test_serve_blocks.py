"""Editing and deleting his own blocks from the web app (DECISIONS.md, 2026-10-20: the web app is one more editor for
him). Every test talks HTTP to a real server over a temporary journal, or calls ``serve.blocks`` on entry text.

What they pin: an edit changes the words of that block and not one other byte; a delete removes exactly the block (its
key line, heading and body); drift is 409 and writes nothing; hers is refused; the ledger lock is taken; a program's
phase answer can be edited (its summary stays code's, refreshed by the next ``write_summary``) but not removed.
"""
import threading
import time
import unittest
from datetime import timedelta
from unittest import mock

from serve_helpers import EVENING, NOW, NOTE, SESSION
from test_serve_writes import WriteCase, checksum

from journal import conversations, entries, ledger
from journal.serve import blocks, document, writes

EVENING_STEM = "2026-10-14-evening"
SESSION_STEM = "2026-10-15-fix-life-s01"
URL = "/api/v1/entries/{}/blocks/{}"


def block(doc, key):
    return next(b for b in doc["blocks"] if b["key"] == key)


class EditCase(WriteCase):
    def doc(self, stem=EVENING_STEM):
        return self.ok("GET", f"/api/v1/entries/{stem}")

    def patch(self, key, text, stem=EVENING_STEM, want=200, **extra):
        doc = self.doc(stem)
        body = {"text": text, "was": block(doc, key)["text"], "at": block(doc, key)["at"], **extra}
        return self.ok("PATCH", URL.format(stem, key), body, want=want) if want == 200 else \
            self.refused("PATCH", URL.format(stem, key), body, want=want)

    def delete(self, key, stem=EVENING_STEM, want=200, **extra):
        doc = self.doc(stem)
        body = {"was": block(doc, key)["text"], "at": block(doc, key)["at"], **extra}
        return self.ok("DELETE", URL.format(stem, key), body, want=want) if want == 200 else \
            self.refused("DELETE", URL.format(stem, key), body, want=want)


class EditTest(EditCase):
    def test_a_words_block_changes_its_text_and_not_one_other_byte(self):
        before = self.entry(EVENING_STEM)
        got = self.patch("words:0", "First words,\nrewritten about the lighthouse.")
        self.assertEqual(got["block"]["text"], "First words,\nrewritten about the lighthouse.")
        self.assertEqual(got["block"]["at"], "23:12 IST")
        self.assertEqual(self.entry(EVENING_STEM),
                         before.replace("First words, about the lighthouse.", "First words,\nrewritten about the lighthouse."))
        doc = self.doc()
        self.assertEqual(block(doc, "words:0")["text"], "First words,\nrewritten about the lighthouse.")
        self.assertEqual(doc["mtime"], got["mtime"])
        self.assertEqual([b["text"] for b in doc["blocks"] if b["key"] != "words:0"],
                         [b["text"] for b in self.original_blocks() if b["key"] != "words:0"])

    def original_blocks(self):
        return document.blocks_of(EVENING)

    def test_a_voice_block_edits_the_transcript_and_keeps_its_recording(self):
        before = self.entry(EVENING_STEM)
        got = self.patch("words:1", "A spoken thought, corrected.")
        self.assertEqual(got["block"]["audio"], "2026-10-14-evening.ogg")
        self.assertEqual(self.entry(EVENING_STEM), before.replace("A spoken thought.", "A spoken thought, corrected."))
        self.assertIn("### 23:40 IST · voice transcript · 2:31\n\n![[2026-10-14-evening.ogg]]", self.entry(EVENING_STEM))

    def test_a_conversation_message_edits_after_its_time_marker_and_keeps_the_callout_and_her_reply(self):
        before = self.entry(EVENING_STEM)
        self.patch("his:813", "Later I thought of the sea.")
        self.assertEqual(self.entry(EVENING_STEM), before.replace("Later I thought of the harbour.",
                                                                  "Later I thought of the sea."))
        got = self.patch("his:816", "yes,\nand the gulls and the tide")
        after = self.entry(EVENING_STEM)
        self.assertIn("> [!info]- ↩ replying to\n> The harbour sounds like\n\n**23:50** — yes,\nand the gulls and the tide\n", after)
        self.assertEqual(got["block"]["reply_to"], "The harbour sounds like")
        self.assertEqual(after.count("%% journal her:4815 %%"), 1)
        self.assertEqual(after[after.index("## Ada's annotations"):], before[before.index("## Ada's annotations"):])

    def test_a_message_that_opens_like_markdown_goes_under_its_time_marker_as_the_engine_writes_it(self):
        self.patch("his:813", "- tea\n- the sea")
        self.assertIn("**23:44** —\n\n- tea\n- the sea\n", self.entry(EVENING_STEM))
        self.assertEqual(block(self.doc(), "his:813")["text"], "- tea\n- the sea")
        self.patch("his:813", "plain again")
        self.assertIn("**23:44** — plain again\n", self.entry(EVENING_STEM))

    def test_the_markdown_rule_is_the_conversation_writers(self):
        for text in ("# h", "> q", "- a", "* a", "+ a", "1. a", "2) a", "```x", "| a", "![[x]]", "plain", "a - b", "#nospace"):
            with self.subTest(text):
                self.assertEqual(bool(blocks._BLOCK_START.match(text)), bool(conversations._BLOCK_START.match(text)))
        self.assertEqual(blocks._BLOCK_START.pattern, conversations._BLOCK_START.pattern)

    def test_editing_back_gives_the_original_bytes_for_every_block_of_his(self):
        for name, body in (("evening", EVENING), ("session", SESSION), ("note", NOTE)):
            for b in document.blocks_of(body):
                if b["kind"] != "his":
                    continue
                with self.subTest(name, key=b["key"]):
                    other = blocks.edit(body, b["key"], "Z marks the spot")
                    self.assertNotEqual(other, body)
                    self.assertEqual(document.blocks_of(other)[[x["key"] for x in document.blocks_of(body)].index(b["key"])]["text"],
                                     "Z marks the spot")
                    self.assertEqual(blocks.edit(other, b["key"], b["text"]), body)

    def test_the_edit_keeps_every_engine_line_the_companions_words_the_front_matter_and_the_other_blocks(self):
        before = self.entry(EVENING_STEM)
        self.patch("words:1", "changed")
        after = self.entry(EVENING_STEM)
        for kept in ("%% journal:conversation %%", "%% journal his:813 %%", "%% journal her:4815 %%", "%% journal his:816 %%",
                     "> The harbour sounds like a SECRETHERS word.", "> Second line.", "## Ada's annotations\n\n- a theme.",
                     "## Ada's notes\n\n### 08:02 IST\n\nA note of hers.", "type: journal", "First words, about the lighthouse."):
            self.assertIn(kept, after)
        self.assertEqual(len(after.splitlines()), len(before.splitlines()))

    def test_an_edit_is_not_a_capture_and_leaves_the_ledger_alone(self):
        ledger_before = (self.cfg.state_dir / "ledger.json").read_bytes()
        self.patch("words:0", "different")
        self.assertEqual((self.cfg.state_dir / "ledger.json").read_bytes(), ledger_before)

    def test_off_the_record_holds_on_an_edit_as_it_does_on_a_new_block(self):
        # /x keeps words out of the journal; an edit that starts with it would put them into the file, where they
        # read as off the record and still reach his_words(), the annotation input and search
        before = checksum(self.cfg.root)
        for text in ("/x my private thing", "/X my private thing", "/x"):
            with self.subTest(text):
                got = self.patch("words:0", text, want=422)
                self.assertEqual(got["error"], "off-the-record")
        self.assertEqual(checksum(self.cfg.root), before)

    def test_the_command_in_the_middle_of_words_is_just_words(self):
        self.patch("words:0", "I typed /x once, as a joke")
        self.assertIn("I typed /x once, as a joke", self.entry(EVENING_STEM))

    def test_the_engines_own_marker_lines_are_still_refused(self):
        before = checksum(self.cfg.root)
        got = self.patch("words:0", "fine\n%% journal her:1 %%\n> [!quote]- Ada · 09:00\n> not hers", want=422)
        self.assertEqual(got["error"], "reserved-line")
        self.assertEqual(checksum(self.cfg.root), before)

    def test_words_that_would_be_read_as_something_else_are_refused_and_nothing_is_written(self):
        before = checksum(self.cfg.root)
        for text in ("one\n\n### 23:59 IST · text\n\nshaped like a block", "one\n\n![[2026-10-14-evening.ogg]]"):
            with self.subTest(text):
                self.assertEqual(self.patch("words:0", text, want=422)["error"], "ambiguous-text")
        self.assertEqual(self.patch("words:0", "one\n\n## Conversation\n\n%% journal:conversation %%", want=422)["error"],
                         "reserved-line")
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_heading_he_types_in_the_engines_shape_is_read_as_the_engine_reads_it_or_refused(self):
        # in an entry that has the companion's sections, the engine takes the *last* such heading, so a typed one is words
        words = blocks.edit(EVENING, "words:0", "one\n\n## Ada's annotations\n\nnot hers")
        self.assertEqual(document.blocks_of(words)[0]["text"], "one\n\n## Ada's annotations\n\nnot hers")
        self.assertEqual(entries.annotations(words), "- a theme.")
        # in a note with none yet, the same line would become the annotations heading and swallow what follows: refused
        with self.assertRaises(blocks.Refused) as raised:
            blocks.edit(NOTE, "words:0", "call\n\n## Ada's annotations\n\nnot hers")
        self.assertEqual(raised.exception.slug, "ambiguous-text")

    def test_a_block_cannot_be_left_empty_and_the_text_is_required(self):
        before = checksum(self.cfg.root)
        doc = self.doc()
        for body in ({"was": "x"}, {"text": "  \n", "was": block(doc, "words:0")["text"]}, {"text": 5, "was": "x"}):
            self.refused("PATCH", URL.format(EVENING_STEM, "words:0"), body)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_the_text_limit_and_line_endings(self):
        self.refused("PATCH", URL.format(EVENING_STEM, "words:0"),
                     {"text": "x" * (writes.TEXT_LIMIT + 1), "was": block(self.doc(), "words:0")["text"]}, want=413)
        self.patch("words:0", "one\r\ntwo\rthree")
        self.assertEqual(block(self.doc(), "words:0")["text"], "one\ntwo\nthree")

    def test_a_block_with_no_words_yet_can_be_given_some(self):
        body = EVENING.replace("A spoken thought.", "")
        new = blocks.edit(body, "words:1", "now with words")
        self.assertIn("![[2026-10-14-evening.ogg]]\n\nnow with words\n\n## Conversation", new)
        self.assertEqual(document.blocks_of(new)[1]["text"], "now with words")

    def test_an_older_entry_with_text_above_its_first_heading_edits_and_deletes(self):
        legacy = EVENING.replace("### 23:12 IST · text\n\nFirst words, about the lighthouse.\n\n",
                                 "First words, with no heading.\n\n")
        self.assertEqual(document.blocks_of(legacy)[0]["key"], "words:0")
        self.assertIsNone(document.blocks_of(legacy)[0]["at"])
        edited = blocks.edit(legacy, "words:0", "Rewritten, still no heading.")
        self.assertEqual(edited, legacy.replace("First words, with no heading.", "Rewritten, still no heading."))
        gone = blocks.remove(legacy, "words:0")
        self.assertEqual(gone, legacy.replace("First words, with no heading.\n\n", "", 1))
        self.assertEqual([b["text"] for b in document.blocks_of(gone)][0], "A spoken thought.")

    def test_a_block_with_one_of_the_engines_lines_inside_it_is_left_to_the_notes_app(self):
        odd = EVENING.replace("First words, about the lighthouse.", "First words\n%% journal phase:9 %%\nmore")
        with self.assertRaises(blocks.Refused) as raised:
            blocks.edit(odd, "words:0", "x")
        self.assertEqual((raised.exception.status, raised.exception.slug), (409, "not-editable"))
        with self.assertRaises(blocks.Refused):
            blocks.remove(odd, "words:0")

    def test_a_recording_still_being_transcribed_is_left_alone(self):
        waiting = EVENING.replace("A spoken thought.", "_(audio file — transcript pending)_")
        for fn in (lambda: blocks.edit(waiting, "words:1", "x"), lambda: blocks.remove(waiting, "words:1")):
            with self.assertRaises(blocks.Refused) as raised:
                fn()
            self.assertEqual(raised.exception.slug, "transcribing")


PHOTO_STEM = "2026-10-14-evening"
PHOTO = (EVENING.split("## Conversation")[0].rstrip("\n") + "\n\n### 14:30 IST · photo\n\n"
         "![[2026-10-14-evening.jpg]]\n![[2026-10-14-evening-2.jpg]]\n\nthe lake at six\n")


class PhotoTest(unittest.TestCase):
    """A photo block is the engine's pictures and then his caption: an edit changes the caption and never a picture."""

    def setUp(self):
        self.parts = [b for b in document.blocks_of(PHOTO, PHOTO_STEM) if b["source"] == "photo"]
        self.key = self.parts[0]["key"]

    def test_the_caption_is_the_block_and_the_pictures_are_not(self):
        self.assertEqual(len(self.parts), 1)
        self.assertEqual(self.parts[0]["text"], "the lake at six")
        self.assertEqual(self.parts[0]["images"], ["2026-10-14-evening.jpg", "2026-10-14-evening-2.jpg"])

    def test_an_edit_changes_the_caption_and_keeps_every_picture_line(self):
        new = blocks.edit(PHOTO, self.key, "the lake, at dawn", was="the lake at six", at=self.parts[0]["at"],
                          stem=PHOTO_STEM)
        self.assertEqual(new, PHOTO.replace("the lake at six", "the lake, at dawn"))
        again = [b for b in document.blocks_of(new, PHOTO_STEM) if b["source"] == "photo"][0]
        self.assertEqual((again["text"], again["images"]), ("the lake, at dawn", self.parts[0]["images"]))

    def test_a_photo_with_no_caption_takes_one_without_losing_its_picture(self):
        bare = PHOTO.replace("\nthe lake at six\n", "")
        key = [b for b in document.blocks_of(bare, PHOTO_STEM) if b["source"] == "photo"][0]["key"]
        new = blocks.edit(bare, key, "a caption at last", stem=PHOTO_STEM)
        self.assertIn("![[2026-10-14-evening.jpg]]\n![[2026-10-14-evening-2.jpg]]\n\na caption at last\n", new)

    def test_a_caption_that_would_read_as_a_picture_is_refused_and_nothing_changes(self):
        with self.assertRaises(blocks.Refused) as got:
            blocks.edit(PHOTO, self.key, "![[2026-10-14-evening-3.jpg]]\nwords", stem=PHOTO_STEM)
        self.assertEqual(got.exception.slug, "ambiguous-text")

    def test_delete_removes_the_heading_pictures_and_caption_together(self):
        new = blocks.remove(PHOTO, self.key, stem=PHOTO_STEM)
        self.assertNotIn("photo", new)
        self.assertNotIn("the lake at six", new)
        self.assertNotIn("2026-10-14-evening.jpg", new)
        self.assertEqual(blocks.edit(new, "words:1", "x", stem=PHOTO_STEM).count("23:40 IST"), 1)


class DeleteTest(EditCase):
    def test_a_words_block_goes_with_its_heading_and_body_and_nothing_else(self):
        before = self.entry(EVENING_STEM)
        self.delete("words:0")
        self.assertEqual(self.entry(EVENING_STEM), before.replace("### 23:12 IST · text\n\nFirst words, about the lighthouse.\n\n", ""))
        doc = self.doc()
        self.assertEqual([b["key"] for b in doc["blocks"]][:2], ["words:0", "his:813"])
        self.assertEqual(block(doc, "words:0")["text"], "A spoken thought.")
        self.assertNotIn("lighthouse", self.entry(EVENING_STEM))

    def test_the_last_words_block_and_the_recording_file(self):
        before = self.entry(EVENING_STEM)
        self.delete("words:1")
        self.assertEqual(self.entry(EVENING_STEM),
                         before.replace("### 23:40 IST · voice transcript · 2:31\n\n![[2026-10-14-evening.ogg]]\n\nA spoken thought.\n\n", ""))
        self.assertTrue((self.cfg.root / "2026-10-14-evening.ogg").is_file())   # the file stays: delete removes the block only

    def test_a_conversation_message_goes_with_its_key_line_and_reply_callout_and_hers_stays(self):
        before = self.entry(EVENING_STEM)
        self.delete("his:816")
        self.assertEqual(self.entry(EVENING_STEM),
                         before.replace("%% journal his:816 %%\n> [!info]- ↩ replying to\n> The harbour sounds like\n\n**23:50** — yes, and the gulls.\n\n", ""))
        self.delete("his:813")
        after = self.entry(EVENING_STEM)
        self.assertNotIn("%% journal his:813 %%", after)
        self.assertNotIn("Later I thought of the harbour.", after)
        self.assertNotIn("the gulls", after)
        self.assertIn("%% journal her:4815 %%\n> [!quote]- Ada · 23:45\n> The harbour sounds like a SECRETHERS word.", after)
        self.assertEqual([b["kind"] for b in self.doc()["blocks"]], ["his", "his", "hers"])

    def test_the_only_block_leaves_an_empty_section_that_still_takes_a_new_block(self):
        stem = "2026-10-15-j"
        self.delete("words:0", stem=stem)
        self.assertEqual(self.doc(stem)["blocks"], [])
        got = self.ok("POST", f"/api/v1/entries/{stem}/blocks", {"text": "a new thought"}, want=201)
        self.assertEqual(got["key"], "words:0")
        self.assertIn("a new thought", self.entry(stem))

    def test_her_replies_are_not_removed_by_removing_his(self):
        body = blocks.remove(EVENING, "his:813")
        self.assertEqual([b["key"] for b in document.blocks_of(body)], ["words:0", "words:1", "her:4815", "his:816"])

    def test_a_phase_answer_is_edited_but_never_removed(self):
        before = self.entry(SESSION_STEM)
        got = self.delete("words:0", stem=SESSION_STEM, want=409)
        self.assertEqual(got["error"], "program-answer")
        self.assertEqual(self.entry(SESSION_STEM), before)


class ProgramTest(EditCase):
    def test_a_phase_answer_edit_changes_that_answer_and_the_summary_is_codes_to_refresh(self):
        before = self.entry(SESSION_STEM)
        got = self.patch("words:1", "The life I truly want.", stem=SESSION_STEM)
        self.assertEqual(got["block"]["phase"], 2)
        after = self.entry(SESSION_STEM)
        self.assertEqual(after, before.replace("The life I want.", "The life I truly want."))
        self.assertIn("%% journal phase:2 %%\n### 06:20 IST · text\n\nThe life I truly want.", after)
        self.assertEqual(entries.summary_text(after), "Two phases done.")   # code's text, untouched by the edit
        self.assertEqual(entries.phase_answers(after)[2], "The life I truly want.")
        # the next write_summary fills the summary from his words as they are now; his blocks are not touched by it
        path = self.cfg.root / f"{SESSION_STEM}.md"
        entries.write_summary(path, f"Phase 2: {entries.phase_answers(after)[2]}", self.cfg.names)
        refreshed = path.read_text(encoding="utf-8")
        self.assertEqual(entries.summary_text(refreshed), "Phase 2: The life I truly want.")
        self.assertEqual(refreshed[:refreshed.index("## Summary")], after[:after.index("## Summary")])
        self.assertEqual(document.blocks_of(refreshed), document.blocks_of(after))


class DriftTest(EditCase):
    def test_a_stale_mtime_is_409_changed_and_nothing_is_written(self):
        doc = self.doc()
        before = checksum(self.cfg.root)
        self.ok("POST", f"/api/v1/entries/{EVENING_STEM}/blocks", {"text": "added meanwhile"}, want=201)
        after = checksum(self.cfg.root)
        for method, body in (("PATCH", {"text": "mine", "mtime": doc["mtime"]}), ("DELETE", {"mtime": doc["mtime"]})):
            got = self.refused(method, URL.format(EVENING_STEM, "words:0"), body, want=409)
            self.assertEqual(got["error"], "changed")
            self.assertIn("reload", got["detail"])
        self.assertEqual(checksum(self.cfg.root), after)
        self.assertNotEqual(before, after)
        self.ok("PATCH", URL.format(EVENING_STEM, "words:0"), {"text": "mine", "mtime": self.doc()["mtime"]})

    def test_a_block_that_reads_differently_is_409_even_when_the_entry_moved_for_other_reasons(self):
        self.ok("POST", f"/api/v1/entries/{EVENING_STEM}/blocks", {"text": "added meanwhile"}, want=201)   # the entry moved
        got = self.ok("PATCH", URL.format(EVENING_STEM, "words:0"), {"text": "mine", "was": "First words, about the lighthouse."})
        self.assertEqual(got["block"]["text"], "mine")   # his block is as he read it: the entry moving elsewhere is no conflict
        before = checksum(self.cfg.root)
        for method, body in (("PATCH", {"text": "again", "was": "First words, about the lighthouse."}),
                             ("DELETE", {"was": "First words, about the lighthouse."}),
                             ("PATCH", {"text": "again", "was": "mine", "at": "10:00 IST"})):
            self.assertEqual(self.refused(method, URL.format(EVENING_STEM, "words:0"), body, want=409)["error"], "changed")
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_key_that_moved_under_him_does_not_edit_the_wrong_block(self):
        # another tab removed words:0, so what he calls words:1 is no longer the block he read
        self.delete("words:0")
        before = checksum(self.cfg.root)
        got = self.refused("PATCH", URL.format(EVENING_STEM, "words:1"), {"text": "x", "was": "A spoken thought."}, want=404)
        self.assertEqual(got["error"], "not-found")
        self.refused("DELETE", URL.format(EVENING_STEM, "words:0"), {"was": "First words, about the lighthouse."}, want=409)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_he_must_say_what_he_was_looking_at(self):
        before = checksum(self.cfg.root)
        self.refused("PATCH", URL.format(EVENING_STEM, "words:0"), {"text": "x"})
        self.refused("DELETE", URL.format(EVENING_STEM, "words:0"), {})
        self.refused("PATCH", URL.format(EVENING_STEM, "words:0"), {"text": "x", "mtime": "soon"})
        self.refused("PATCH", URL.format(EVENING_STEM, "words:0"), {"text": "x", "was": 4})
        self.refused("PATCH", URL.format(EVENING_STEM, "words:0"), {"text": "x", "was": "", "style": "bold"})
        self.assertEqual(checksum(self.cfg.root), before)


class RefusalTest(EditCase):
    def test_hers_is_never_editable_here(self):
        before = checksum(self.cfg.root)
        for method, body in (("PATCH", {"text": "rewritten", "mtime": self.doc()["mtime"]}), ("DELETE", {"mtime": self.doc()["mtime"]})):
            got = self.refused(method, URL.format(EVENING_STEM, "her:4815"), body, want=409)
            self.assertEqual(got["error"], "not-his")
        self.assertEqual(checksum(self.cfg.root), before)

    def test_no_such_block_entry_or_key_shape(self):
        mtime = self.doc()["mtime"]
        before = checksum(self.cfg.root)
        for stem, key in ((EVENING_STEM, "words:9"), (EVENING_STEM, "his:nope"), (EVENING_STEM, "analysis"),
                          (EVENING_STEM, "words:"), (EVENING_STEM, "x%00y"), ("2026-10-14", "words:0"),
                          ("2026-10-14-thoughts", "words:0"), ("2026-10-14-reflection", "words:0"), ("nope", "words:0")):
            with self.subTest(stem=stem, key=key):
                self.refused("PATCH", URL.format(stem, key), {"text": "x", "mtime": mtime}, want=404)
                self.refused("DELETE", URL.format(stem, key), {"mtime": mtime}, want=404)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_file_named_like_an_entry_but_not_written_by_the_engine_is_not_written_to(self):
        name = "2026-10-13-evening"
        (self.cfg.root / f"{name}.md").write_text("---\nkind: evening\n---\n\n## Sam\n\n### 09:00 IST · text\n\nmine\n", encoding="utf-8")
        before = checksum(self.cfg.root)
        got = self.refused("PATCH", URL.format(name, "words:0"), {"text": "x", "was": "mine"}, want=409)
        self.assertEqual(got["error"], "not-engine-entry")
        self.refused("DELETE", URL.format(name, "words:0"), {"was": "mine"}, want=409)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_the_collection_and_the_entry_itself_are_still_not_editable(self):
        before = checksum(self.cfg.root)
        for method in ("PUT", "PATCH", "DELETE"):
            for path in (f"/api/v1/entries/{EVENING_STEM}/blocks", f"/api/v1/entries/{EVENING_STEM}", "/api/v1/entries"):
                with self.subTest(method=method, path=path):
                    self.assertIn(self.write(method, path, {"text": "rewritten"})[0], (404, 405))
        self.assertEqual(self.write("PUT", URL.format(EVENING_STEM, "words:0"), {"text": "x"})[0], 405)
        self.assertEqual(checksum(self.cfg.root), before)


class LockTest(EditCase):
    def test_a_busy_ledger_is_423_and_the_entry_is_as_it_was(self):
        before = checksum(self.cfg.root)
        doc = self.doc()
        held, done = threading.Event(), threading.Event()

        def holder():
            with ledger.locked(self.cfg.ledger_path):
                held.set()
                done.wait(10)
        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        try:
            with mock.patch.object(writes, "LOCK_WAIT", 0.2):
                started = time.monotonic()
                self.refused("PATCH", URL.format(EVENING_STEM, "words:0"), {"text": "x", "mtime": doc["mtime"]}, want=423)
                self.refused("DELETE", URL.format(EVENING_STEM, "words:0"), {"mtime": doc["mtime"]}, want=423)
                self.assertLess(time.monotonic() - started, 5)
        finally:
            done.set()
            t.join(5)
        self.assertEqual(checksum(self.cfg.root), before)
        self.ok("PATCH", URL.format(EVENING_STEM, "words:0"), {"text": "x", "mtime": doc["mtime"]})

    def test_edits_and_deletes_beside_captures_lose_nothing(self):
        """The edit is a read-modify-write of the whole file, as every engine writer's is; only the lock keeps a
        capture that lands between its read and its write from being lost."""
        rounds, errors = 10, []

        def captures():
            try:
                for i in range(rounds):
                    code, _h, raw = self.write("POST", f"/api/v1/entries/{EVENING_STEM}/blocks", {"text": f"web block {i}"})
                    if code != 201:
                        raise AssertionError(f"block {i}: {code} {raw[:200]!r}")
                    from journal import capture
                    capture.capture_to(self.cfg, EVENING_STEM, "direct-reply", f"hook reply {i}",
                                       NOW + timedelta(seconds=i), "text", f"tg-{i}")
            except BaseException as e:   # noqa: BLE001
                errors.append(repr(e))

        def editor():
            try:
                for i in range(rounds):
                    code, _h, raw = self.write("PATCH", URL.format(EVENING_STEM, "words:0"),
                                               {"text": f"edit {i}", "was": "First words, about the lighthouse." if i == 0 else f"edit {i - 1}"})
                    if code != 200:
                        raise AssertionError(f"edit {i}: {code} {raw[:200]!r}")
            except BaseException as e:   # noqa: BLE001
                errors.append(repr(e))

        def remover():
            try:
                for i in range(rounds):
                    code, _h, raw = self.write("POST", f"/api/v1/entries/{EVENING_STEM}/blocks", {"text": f"doomed {i}"})
                    if code != 201:
                        raise AssertionError(f"doomed {i}: {code} {raw[:200]!r}")
                    doc = self.doc()
                    key = next(b["key"] for b in doc["blocks"] if b["text"] == f"doomed {i}")
                    code, _h, raw = self.write("DELETE", URL.format(EVENING_STEM, key), {"was": f"doomed {i}"})
                    if code != 200:
                        raise AssertionError(f"delete {i}: {code} {raw[:200]!r}")
            except BaseException as e:   # noqa: BLE001
                errors.append(repr(e))

        threads = [threading.Thread(target=f, daemon=True) for f in (captures, editor, remover)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        self.assertFalse([t for t in threads if t.is_alive()], "a writer is stuck")
        self.assertEqual(errors, [])
        text = self.entry(EVENING_STEM)
        for i in range(rounds):
            self.assertEqual(text.count(f"web block {i}\n"), 1, i)
            self.assertEqual(text.count(f"hook reply {i}\n"), 1, i)
            self.assertNotIn(f"doomed {i}", text)
        self.assertIn(f"edit {rounds - 1}\n", text)
        self.assertNotIn("lighthouse", text)
        self.assertEqual(text.count("A spoken thought."), 1)


if __name__ == "__main__":
    unittest.main()
