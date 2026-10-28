import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import add_prompt, make_cfg

from journal import capture, entries, ledger, notebook


class PerEntryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.base = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_ritual_and_each_j_is_its_own_flat_file(self):
        add_prompt(self.cfg, "morning", sent=self.base, message_id="1")
        add_prompt(self.cfg, "evening", sent=self.base + timedelta(hours=17), message_id="2")
        capture.capture(self.cfg, self.cfg.chat_id, "1", "morning words", self.base + timedelta(hours=1))
        capture.capture_note(self.cfg, "first thought", self.base + timedelta(hours=8))
        capture.capture_note(self.cfg, "second thought", self.base + timedelta(hours=9))
        capture.capture(self.cfg, self.cfg.chat_id, "2", "evening words", self.base + timedelta(hours=18))
        names = sorted(p.name for p in self.cfg.root.glob("*.md"))
        self.assertEqual(names, ["2026-10-14-evening.md", "2026-10-14-j-2.md", "2026-10-14-j.md",
                                 "2026-10-14-morning.md"])
        self.assertEqual(entries.raw_entry((self.cfg.root / "2026-10-14-j-2.md").read_text()).splitlines()[-1],
                         "second thought")
        body = (self.cfg.root / "2026-10-14-evening.md").read_text()
        self.assertIn("kind: evening", body)
        self.assertIn("source: iris-journal", body)

    def test_companion_note_never_touches_his_words_or_annotations(self):
        add_prompt(self.cfg, sent=self.base)
        info = capture.capture(self.cfg, self.cfg.chat_id, "100", "my words", self.base + timedelta(hours=1))
        path = Path(info["path"])
        capture.add_companion_note(self.cfg, "Links to an August entry (inference).", self.base + timedelta(hours=2),
                                   "2026-10-14", "morning")
        entries.write_annotations(path, "- annotated")
        capture.capture(self.cfg, self.cfg.chat_id, "100", "more words", self.base + timedelta(hours=3))
        body = path.read_text()
        self.assertIn("my words", entries.raw_entry(body))
        self.assertLess(body.index("more words"), body.index("## Iris's annotations"))
        self.assertLess(body.index("## Iris's annotations"), body.index("## Iris's notes"))
        self.assertIn("- annotated", body)

    def test_companion_note_on_empty_day_starts_a_note_entry(self):
        p = capture.add_companion_note(self.cfg, "Worth keeping.", self.base)
        self.assertTrue(p.endswith("2026-10-14-note.md"))
        body = Path(p).read_text()
        self.assertIn("## Iris's notes", body)
        self.assertIn("# Iris's note — Wed 14 Oct 2026", body)
        self.assertNotIn("## Theo", body)

    def test_reply_into_companion_started_entry_inserts_his_section_first(self):
        p = Path(capture.add_companion_note(self.cfg, "Worth keeping.", self.base))
        entries.record_reply(self.cfg.root, {"stem": p.stem, "kind": "note", "local_date": "2026-10-14"},
                             "his words", self.base.astimezone(self.cfg.tz), names=self.cfg.names)
        body = p.read_text()
        self.assertLess(body.index("## Theo"), body.index("## Iris's annotations"))
        self.assertLess(body.index("## Iris's annotations"), body.index("## Iris's notes"))
        self.assertEqual(entries.raw_entry(body).splitlines()[-1], "his words")

    def test_voice_audio_embedded(self):
        add_prompt(self.cfg, sent=self.base)
        with ledger.locked(self.cfg.ledger_path) as data:
            prompt = ledger.find(data, "2026-10-14-morning")
            info = capture.record(self.cfg, prompt, "direct-reply", "slept okay", self.base + timedelta(hours=1),
                                  "voice", "9", "2026-10-14-morning-063000.ogg")
        body = Path(info["path"]).read_text()
        self.assertIn("· voice transcript\n\n![[2026-10-14-morning-063000.ogg]]\n\nslept okay", body)


class EntryKindsTest(unittest.TestCase):
    """Entries are recognised by name shape plus a known id, or by their own front matter; his own files never."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.root = self.cfg.root
        self.base = datetime(2026, 10, 20, 0, 30, tzinfo=timezone.utc)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name: str, front: str = "", body: str = "words\n") -> Path:
        p = self.root / name
        p.write_text((f"---\n{front}---\n\n" if front else "") + body)
        return p

    def test_program_session_file_is_recognised_with_its_parts(self):
        p = self.write("2026-10-20-fix-life-s01.md", "date: 2026-10-20\nkind: fix-life\npractice: fix-life\ntype: journal\n")
        self.assertEqual(entries.entry_key(p), ("fix-life", 1, 1))
        self.assertEqual(entries.kind_of(p), "fix-life")
        self.assertEqual(entries.entries_on(self.root, "2026-10-20"), [p])
        self.assertEqual(entries.name_parts("2026-10-20-j-3.md"), ("j", None, 3))
        self.assertEqual(entries.entry_key(self.write("2026-10-20-evening.md")), ("evening", None, 1))

    def test_his_own_files_are_ignored(self):
        self.write("2026-10-20.md", body="# My day\n")
        self.write("2026-10-20-2.md", body="more\n")
        self.write("2026-10-20-thoughts.md", body="no front matter, not ours\n")
        self.write("2026-10-20-reflection.md", "date: 2026-10-20\nkind: reflection\n", "by hand, no author\n")
        self.assertEqual(entries.entries_on(self.root, "2026-10-20"), [])
        for n in ("2026-10-20.md", "2026-10-20-2.md", "2026-10-20-thoughts.md", "2026-10-20-reflection.md"):
            self.assertIsNone(entries.kind_of(self.root / n), n)

    def test_unknown_id_with_matching_front_matter_is_recognised(self):
        kind = self.write("2026-10-20-gratitude.md", "date: 2026-10-20\nkind: gratitude\ntype: journal\n")
        practice = self.write("2026-10-20-gratitude-2.md", "date: 2026-10-20\npractice: gratitude\ntype: journal\n")
        other = self.write("2026-10-20-gratitude-3.md", "date: 2026-10-20\nkind: evening\ntype: journal\n")  # not its name
        self.assertEqual(entries.kind_of(kind), "gratitude")
        self.assertEqual(entries.entry_key(practice), ("gratitude", None, 2))
        self.assertIsNone(entries.kind_of(other))
        self.assertEqual(set(entries.entries_on(self.root, "2026-10-20")), {kind, practice})

    def test_his_own_file_claiming_a_kind_is_not_an_entry_and_never_receives_her_note(self):
        # Hand-written, front matter and all: ``kind:`` alone is his word for his file, not the engine's mark on
        # one it wrote. Before the fallback the five-name regex kept her notes out of such files; the ``type:``
        # marker keeps that promise.
        his = self.write("2026-10-20-thoughts.md", "date: 2026-10-20\nkind: thoughts\n", "## Me\n\nprivate musings\n")
        plan = self.write("2026-10-20-plan.md", "date: 2026-10-20\npractice: plan\nsource: me\n", "## Plan\n\nmine\n")
        before = {p: p.read_text() for p in (his, plan)}
        self.assertEqual(entries.entries_on(self.root, "2026-10-20"), [])
        self.assertIsNone(entries.kind_of(his))
        self.assertIsNone(entries.entry_key(plan))
        at = self.base + timedelta(hours=3)
        p = capture.add_companion_note(self.cfg, "Her note.", at)  # the day's most recent entry: none of ours
        self.assertTrue(p.endswith("2026-10-20-note.md"))
        with self.assertRaises(ValueError):
            capture.add_companion_note(self.cfg, "Her note.", at, "2026-10-20", "thoughts")
        self.assertEqual({p: p.read_text() for p in (his, plan)}, before)

    def test_front_matter_longer_than_a_block_or_quoted_is_still_read(self):
        tags = "tags: " + ", ".join(f"tag{i}" for i in range(150))  # well past the head the engine used to read
        self.assertGreater(len(tags), 600)
        long = self.write("2026-10-20-gratitude.md", f"date: 2026-10-20\n{tags}\nkind: gratitude\ntype: journal\n")
        quoted = self.write("2026-10-20-thanks.md", "date: 2026-10-20\nkind: \"thanks\"\ntype: 'journal'\n")
        self.assertEqual(entries.kind_of(long), "gratitude")
        self.assertEqual(entries.kind_of(quoted), "thanks")
        self.assertEqual(entries.front_matter("---\na: \"x\"\nb: 'y'\nc: \"half\nd: \"\"\n---\n"),
                         {"a": "x", "b": "y", "c": '"half', "d": ""})
        made = self.write("2026-10-20-reflection.md", f"date: 2026-10-20\n{tags}\nkind: reflection\nauthor: 'ada'\n")
        self.assertTrue(entries.is_analysis(made))
        at = self.base + timedelta(hours=3)
        self.assertEqual(capture.add_companion_note(self.cfg, "n", at, "2026-10-20", "gratitude"), str(long))

    def test_companion_note_looks_up_the_day_with_the_callers_ids(self):
        # An id the caller's library knows needs no front matter (as the rituals never did); the lookup must use
        # the same ids as the validation, or the note lands in a stray -note.md beside the entry it was meant for.
        custom = self.write("2026-10-20-custom.md", body="## Me\n\nwords\n")
        at = self.base + timedelta(hours=3)
        ids = lambda: ["custom"]  # noqa: E731
        p = capture.add_companion_note(self.cfg, "note", at, "2026-10-20", "custom", known_ids=ids)
        self.assertEqual(p, str(custom))
        self.assertIn(self.cfg.names.notes_heading, custom.read_text())
        self.assertEqual(entries.entries_on(self.root, "2026-10-20", ("custom",)), [custom])
        self.assertEqual(entries.entries_on(self.root, "2026-10-20"), [])  # unknown to the module's own ids

    def test_his_words_drop_the_engine_key_lines(self):
        add_prompt(self.cfg, "evening", "2026-10-20", sent=self.base, message_id="5")
        info = capture.capture(self.cfg, self.cfg.chat_id, "5", "first answer", self.base + timedelta(hours=1))
        path = Path(info["path"])
        body = path.read_text()
        s, e = entries.words_span(body)
        body = body[:e].rstrip() + "\n\n%% journal phase:2 %%\n\nsecond phase answer\n\n" + body[e:]
        entries.write_file(path, body)
        entries.append_conversation(path, "%% journal his:9 %%\n**20:00** · later in chat", self.cfg.names)
        text = path.read_text()
        self.assertIn("%% journal phase:2 %%", text)  # the file keeps its key
        for words in (entries.raw_entry(text), entries.his_words(text), entries.his_conversation(text)):
            self.assertNotIn("%% journal", words)
        self.assertIn("second phase answer", entries.raw_entry(text))
        self.assertIn("later in chat", entries.his_words(text))
        # the full-entry readers (annotation input, the quote checker) see no key lines either, with or without
        # a conversation section
        whole = entries.without_companion_replies(text)
        self.assertNotIn("%% journal", whole)
        self.assertIn("second phase answer", whole)
        self.assertIn("later in chat", whole)
        self.assertIn("%% journal phase:2 %%", body)  # the file before its conversation section
        self.assertNotIn("%% journal", entries.without_companion_replies(body))

    def test_an_id_that_ends_like_a_suffix_is_resolved_by_the_known_ids_or_front_matter(self):
        # ``level-10`` and ``reset-s01`` are valid ids; by name alone they read as the tenth ``level`` or session
        # 1 of ``reset``. The known ids settle it (longest id first), else the file's own front matter.
        self.assertEqual(entries.name_candidates("2026-10-20-level-10.md"), [("level-10", None, 1), ("level", None, 10)])
        self.assertEqual(entries.name_candidates("2026-10-20-fix-life-s01-2.md"),
                         [("fix-life-s01-2", None, 1), ("fix-life-s01", None, 2), ("fix-life", 1, 2)])
        self.assertEqual(entries.name_candidates("2026-10-20.md"), [])
        level = self.write("2026-10-20-level-10.md")
        reset = self.write("2026-10-20-reset-s01.md")
        ids = ("level-10", "reset-s01")
        self.assertEqual(entries.entry_key(level, ids), ("level-10", None, 1))
        self.assertEqual(entries.entry_key(reset, ids), ("reset-s01", None, 1))
        self.assertEqual(entries.entry_key(level, ("level",)), ("level", None, 10))
        self.assertEqual(entries.entry_key(reset, ("reset",)), ("reset", 1, 1))
        self.assertEqual(entries.entry_key(level, ("level", "level-10")), ("level-10", None, 1))
        self.assertEqual(set(entries.entries_on(self.root, "2026-10-20", ids)), {level, reset})
        self.assertEqual(entries.entries_on(self.root, "2026-10-20"), [])  # neither id known, no front matter
        claimed = self.write("2026-10-20-level-10-2.md", "date: 2026-10-20\npractice: level-10\ntype: journal\n")
        later = level.stat().st_mtime_ns + 1_000_000_000  # "newest" is by mtime; two writes a moment apart can share one
        os.utime(claimed, ns=(later, later))
        self.assertEqual(entries.entry_key(claimed), ("level-10", None, 2))
        self.assertEqual(entries.kind_of(claimed), "level-10")
        at = self.base + timedelta(hours=3)
        p = capture.add_companion_note(self.cfg, "n", at, "2026-10-20", "level-10", known_ids=lambda: ids)
        self.assertEqual(p, str(claimed))  # the newest level-10 entry of the day, not a stray note
        self.assertIsNone(entries.entry_key(self.write("2026-10-20-week-2.md"), ("week-2",)))  # analyses never

    def test_new_entries_name_their_practice(self):
        add_prompt(self.cfg, "evening", "2026-10-20", sent=self.base, message_id="5")
        capture.capture(self.cfg, self.cfg.chat_id, "5", "words", self.base + timedelta(hours=1))
        fm = entries.front_matter((self.root / "2026-10-20-evening.md").read_text())
        self.assertEqual((fm["kind"], fm["practice"]), ("evening", "evening"))
        capture.capture_note(self.cfg, "a thought", self.base + timedelta(hours=2))
        self.assertNotIn("practice", entries.front_matter((self.root / "2026-10-20-j.md").read_text()))

    def test_companion_note_accepts_known_and_existing_ids_only(self):
        n = self.cfg.names
        self.write("2026-10-20-gratitude.md", "date: 2026-10-20\nkind: gratitude\ntype: journal\n",
                   f"# Gratitude\n\n## Prompt\n\nq\n\n{n.words_heading}\n\nthankful\n\n{n.annotations_heading}\n\n_Pending._\n")
        at = self.base + timedelta(hours=3)
        p = capture.add_companion_note(self.cfg, "Links back.", at, "2026-10-20", "gratitude")
        self.assertTrue(p.endswith("2026-10-20-gratitude.md"))
        self.assertIn(n.notes_heading, (self.root / "2026-10-20-gratitude.md").read_text())
        self.assertTrue(capture.add_companion_note(self.cfg, "x", at, "2026-10-20", "Evening").endswith("-note.md"))
        p = capture.add_companion_note(self.cfg, "x", at, "2026-10-20", "custom", known_ids=lambda: ["custom"])
        self.assertTrue(p.endswith("2026-10-20-note-2.md"))  # known to the caller's library, none that day: a note
        for bad in ("gratitud", "Evening!", "../etc", "reflection"):
            with self.assertRaises(ValueError, msg=bad):
                capture.add_companion_note(self.cfg, "x", at, "2026-10-20", bad)


class NotebookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.state_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def test_add_render_resolve_archive(self):
        now = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
        a = notebook.add(self.cfg, "Ask how the Alex talk went", "2026-10-13-evening", now=now)
        notebook.add(self.cfg, "Check in on the garden decision", bring_up_after="2026-10-14", now=now)
        block = notebook.render(self.cfg, "2026-10-14")
        self.assertIn("Ask how the Alex talk went", block)
        self.assertIn("resolve it with journal_notebook", block)
        self.assertLess(block.index("garden"), block.index("Alex"))  # due item first
        notebook.resolve(self.cfg, a["id"], "came up; went well", now=now)
        self.assertEqual(len(notebook.open_items(self.cfg)), 1)
        arch = (self.cfg.state_dir / "notebook-archive.jsonl").read_text()
        self.assertIn("went well", arch)
        self.assertNotIn("prompts", json.loads((self.cfg.state_dir / "notebook.json").read_text()))

    def test_stale_items_archived_not_deleted(self):
        old = datetime(2026, 9, 1, tzinfo=timezone.utc)
        notebook.add(self.cfg, "old thread", now=old)
        n = notebook.sweep(self.cfg, datetime(2026, 10, 14, tzinfo=timezone.utc))
        self.assertEqual(n, 1)
        self.assertEqual(notebook.open_items(self.cfg), [])
        self.assertIn('"status": "stale"', (self.cfg.state_dir / "notebook-archive.jsonl").read_text())

    def test_followups_split_from_annotations(self):
        text, follow = notebook.split_followups("- He felt tired.\n- FOLLOW-UP: ask what 'lazy' meant\nFOLLOW-UP: sleep?")
        self.assertEqual(text, "- He felt tired.")
        self.assertEqual(follow, ["ask what 'lazy' meant", "sleep?"])


if __name__ == "__main__":
    unittest.main()
