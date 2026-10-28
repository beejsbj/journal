"""The engine pieces the web app's writes stand on (WP10), tested without HTTP: the timed lock, the front-matter
writer, definitions from JSON and their safe replacement, the settings allow-list, and the web captures."""
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from helpers import CleanEnv, make_cfg
from serve_helpers import NEUTRAL

from journal import capture, entries, ledger, library
from journal import config as jconfig

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 15, 9, 12, tzinfo=IST).astimezone(timezone.utc)


class DeadlineTest(unittest.TestCase):
    def test_a_held_lock_is_busy_after_the_deadline_and_free_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            held, release = threading.Event(), threading.Event()

            def holder():
                with ledger.locked(path):
                    held.set()
                    release.wait(5)
            t = threading.Thread(target=holder)
            t.start()
            held.wait(5)
            started = time.monotonic()
            with self.assertRaises(ledger.LockBusy):
                with ledger.deadline(0.2):
                    with ledger.locked(path):
                        self.fail("the lock was not held")
            with self.assertRaises(ledger.LockBusy):
                with ledger.deadline(0.2), ledger.locked_state(Path(tmp) / "ledger.json"):
                    self.fail("the state lock shares the file's lock")
            self.assertLess(time.monotonic() - started, 2)
            release.set()
            t.join(5)
            with ledger.deadline(0.2), ledger.locked(path) as data:  # free again
                data["prompts"].append({"id": "x"})
            self.assertEqual(ledger.snapshot(path)["prompts"][0]["id"], "x")

    def test_the_deadline_is_per_thread_and_ends_with_its_block(self):
        with ledger.deadline(0.05):
            seen = []
            t = threading.Thread(target=lambda: seen.append(getattr(ledger._local, "until", None)))
            t.start()
            t.join()
            self.assertEqual(seen, [None])
        self.assertIsNone(getattr(ledger._local, "until", None))


ENTRY = """---
date: 2026-10-14
kind: evening
practice: evening
mood:
  - calm
  - tired
type: journal
source: journal
---

# Evening journal

## Sam

### 23:12 IST · text

First words.
  with odd spacing

---

## Ada's annotations

_Pending._
"""


class SetMetaTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "2026-10-14-evening.md"
        self.path.write_text(ENTRY, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_only_the_front_matter_changes(self):
        entries.set_meta(self.path, {"mood": "[calm, glad]", "people": "[Ada]", "starred": "true"})
        text = self.path.read_text(encoding="utf-8")
        self.assertEqual(text[text.index("\n---\n\n# Evening"):], ENTRY[ENTRY.index("\n---\n\n# Evening"):])
        fm = entries.front_matter(text)
        self.assertEqual((fm["mood"], fm["people"], fm["starred"]), ("[calm, glad]", "[Ada]", "true"))
        self.assertNotIn("  - calm", text)  # the old list's lines went with the key
        self.assertEqual((fm["date"], fm["kind"], fm["practice"], fm["type"]),
                         ("2026-10-14", "evening", "evening", "journal"))
        self.assertEqual(text.count("mood:"), 1)

    def test_remove_and_untouched(self):
        entries.set_meta(self.path, {"starred": "true"})
        entries.set_meta(self.path, {"mood": None, "starred": None, "people": None})
        fm = entries.front_matter(self.path.read_text(encoding="utf-8"))
        self.assertEqual(sorted(fm), ["date", "kind", "practice", "source", "type"])
        before = self.path.stat().st_mtime_ns
        entries.set_meta(self.path, {"mood": None})  # nothing to change: the file is not rewritten
        self.assertEqual(self.path.stat().st_mtime_ns, before)

    def test_refusals(self):
        bare = Path(self.tmp.name) / "2026-10-14-bare.md"
        bare.write_text("no front matter\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            entries.set_meta(bare, {"mood": "[a]"})
        with self.assertRaises(ValueError):
            entries.set_meta(self.path, {"mood": "[a]\nkind: forged"})
        self.assertEqual(self.path.read_text(encoding="utf-8"), ENTRY)


class DefinitionTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), names=NEUTRAL)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_a_definition_from_json_renders_as_the_file_does(self):
        lib = library.load(self.cfg)
        for pid, p in library.builtins().items():
            with self.subTest(pid):
                made = library.from_dict(lib.to_dict(p))
                text, back = library.write_text(made)
                self.assertEqual(text, library.render(p))
                self.assertEqual(back, library.parse(library.render(p)))

    def test_a_pinned_question_keeps_its_id_and_a_partial_definition_takes_defaults(self):
        d = {"id": "gratitude", "title": "Daily gratitude", "schedule": [{"time": "21:30"}],
             "sections": [{"title": None, "questions": [{"text": "Three things?"},
                                                         {"text": "Who?", "pinned": True, "id": "who"}]}]}
        text, back = library.write_text(library.from_dict(d))
        self.assertIn("- Who? {#who}", text)
        self.assertEqual([q.id for q in back.questions], ["1", "who"])
        self.assertEqual((back.collapse_hours, back.annotate, back.enabled, back.kind), (6.0, True, True, "practice"))

    def test_problems_are_all_named_and_a_question_that_would_not_read_back_is_refused(self):
        with self.assertRaises(library.ParseError) as cm:
            library.from_dict({"id": ["x"], "title": 3, "schedule": "daily", "sections": [{"questions": [{"text": ""}]}],
                               "enabled": "yes"})
        self.assertGreaterEqual(len(cm.exception.problems), 4)
        with self.assertRaises(library.ParseError) as cm:
            library.write_text(library.from_dict({"id": "late", "title": "Late", "schedule": [{"time": "25:00"}],
                                                  "sections": [{"questions": [{"text": "Q?"}]}]}))
        self.assertTrue(any("HH:MM" in p for p in cm.exception.problems))
        with self.assertRaises(library.ParseError):
            library.write_text(library.from_dict({"id": "pins", "title": "Pins",
                                                  "sections": [{"questions": [{"text": "Why? {#sneaky}"}]}]}))
        with self.assertRaises(library.ParseError):
            library.write_text(library.from_dict({"id": "inject", "title": "A\nkind: x",
                                                  "sections": [{"questions": [{"text": "Q?"}]}]}))

    def test_write_creates_replaces_on_mtime_and_refuses_drift(self):
        text = library.render(library.from_dict({"id": "gratitude", "title": "Gratitude",
                                                 "sections": [{"questions": [{"text": "Q?"}]}]}))
        path = library.write(self.cfg, "gratitude", text, None)
        self.assertEqual(path, self.cfg.practices_dir / "gratitude.md")
        with self.assertRaises(library.Changed):   # a file is there now; creating again is not "replace"
            library.write(self.cfg, "gratitude", text, None)
        seen = path.stat().st_mtime
        library.write(self.cfg, "gratitude", text.replace("Q?", "Q2?"), seen)
        self.assertIn("Q2?", path.read_text(encoding="utf-8"))
        path.write_text(text.replace("Q?", "edited in Obsidian?"), encoding="utf-8")
        import os
        os.utime(path, (seen + 100, seen + 100))
        with self.assertRaises(library.Changed):
            library.write(self.cfg, "gratitude", text, seen)
        self.assertIn("Obsidian", path.read_text(encoding="utf-8"))   # his edit survived
        with self.assertRaises(library.Changed):   # the file is gone but the editor thinks it is there
            library.write(self.cfg, "other", text, 5.0)
        (self.cfg.practices_dir / "broken.md").write_text("not a practice", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            library.write(self.cfg, "broken", text, None)

    def test_remove_by_id_even_when_the_file_is_named_otherwise_and_for_a_broken_file(self):
        self.cfg.practices_dir.mkdir(parents=True)
        (self.cfg.practices_dir / "mine.md").write_text(
            "---\nkind: practice\nid: gratitude\ntitle: G\n---\n- Q?\n", encoding="utf-8")
        self.assertEqual(library.remove(self.cfg, "gratitude").name, "mine.md")
        self.assertIsNone(library.remove(self.cfg, "gratitude"))
        (self.cfg.practices_dir / "evening.md").write_text("broken", encoding="utf-8")
        self.assertEqual(library.remove(self.cfg, "evening").name, "evening.md")
        with self.assertRaises(ValueError):
            library.remove(self.cfg, "../x")


class SettingsPatchTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.file = self.dir / "config.json"
        self.file.write_text(json.dumps({"root": str(self.dir / "J"), "windows": {"grace_hours": 3},
                                         "timezone": "UTC"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_the_allow_list(self):
        patch, refused, problems = jconfig.web_patch({
            "windows": {"collapse_hours": 5, "bogus": 1}, "conversations.quiet_minutes": 30,
            "programs.settle_minutes": 2, "reflections": {"day": {"enabled": False, "fallback_time": "05:00"}},
            "telegram": {"chat_id": "1"}, "serve.allow_write": True, "state_dir": "/x", "timezone": "Europe/London",
            "today": {"max_chars": 900, "sources": ["entries"]}})
        self.assertEqual(patch, {"windows": {"collapse_hours": 5}, "conversations": {"quiet_minutes": 30},
                                 "programs": {"settle_minutes": 2},
                                 "reflections": {"day": {"enabled": False, "fallback_time": "05:00"}},
                                 "timezone": "Europe/London", "today": {"max_chars": 900, "sources": ["entries"]}})
        self.assertEqual(sorted(refused), ["serve", "state_dir", "telegram", "windows.bogus"])
        self.assertEqual(problems, [])
        for bad in ({"windows": {"collapse_hours": -1}}, {"windows": {"collapse_hours": True}},
                    {"timezone": "Not/AZone"}, {"coordinates": {"lat": 95, "lon": 0}}, {"windows": 5},
                    {"reflections": {"day": {"fallback_time": "25:00"}}}, {"today": {"sources": ["nope"]}},
                    {"today": {"max_chars": 10}}, {"conversations": {"quiet_minutes": float("inf")}}):
            with self.subTest(bad):
                self.assertTrue(jconfig.web_patch(bad)[2], bad)

    def test_a_nested_change_merges_into_the_file_and_keeps_the_rest(self):
        cfg = jconfig.load(self.file)
        layers = jconfig.patch_values(cfg, {"windows": {"collapse_hours": 5}, "timezone": "Europe/London"})
        self.assertEqual(layers, {"windows": "file", "timezone": "file"})
        data = json.loads(self.file.read_text(encoding="utf-8"))
        self.assertEqual(data["windows"], {"grace_hours": 3, "collapse_hours": 5})
        self.assertEqual((data["timezone"], data["root"]), ("Europe/London", str(self.dir / "J")))
        after = jconfig.load(self.file)
        self.assertEqual((after.collapse_hours, after.grace_hours, after.timezone), (5.0, 3.0, "Europe/London"))

    def test_a_key_nobody_set_goes_to_the_file(self):
        jconfig.patch_values(jconfig.load(self.file), {"conversations": {"quiet_minutes": 20}})
        self.assertEqual(json.loads(self.file.read_text(encoding="utf-8"))["conversations"], {"quiet_minutes": 20})
        self.assertEqual(jconfig.quiet_minutes(jconfig.load(self.file)), 20.0)

    def test_hermes_owned_keys_say_how_to_change_them_and_write_nothing(self):
        cfg = jconfig.load(self.file, settings={"windows": {"collapse_hours": 4}})
        before = self.file.read_text(encoding="utf-8")
        with self.assertRaises(jconfig.ConfigError) as cm:
            jconfig.patch_values(cfg, {"timezone": "Europe/London", "windows": {"collapse_hours": 5}})
        self.assertIn("hermes config set plugins.entries.journal.settings.windows.collapse_hours", str(cm.exception))
        self.assertEqual(self.file.read_text(encoding="utf-8"), before)

    def test_env_owned_and_a_missing_file_under_hermes(self):
        cfg = jconfig.load(self.file)
        cfg.origins["windows"] = "env"
        with self.assertRaises(jconfig.ConfigError) as cm:
            jconfig.patch_values(cfg, {"windows": {"collapse_hours": 5}})
        self.assertIn("environment", str(cm.exception))
        hermes = jconfig.from_dict({"root": str(self.dir / "J")}, hermes_home=self.dir / "home",
                                   origins={"root": "hermes"})
        with self.assertRaises(jconfig.ConfigError) as cm:   # a file made here would be one nothing reads
            jconfig.patch_values(hermes, {"windows": {"collapse_hours": 5}})
        self.assertIn("hermes config set", str(cm.exception))
        self.assertFalse((self.dir / "home").exists() and any((self.dir / "home").iterdir()))


class WebCaptureTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), names=NEUTRAL)
        self.cfg.audio["command"] = ["true"]   # a transcription backend exists
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({
                "id": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15", "chat_id": self.cfg.chat_id,
                "message_id": "10", "sent_at": (NOW - timedelta(hours=1)).isoformat(), "sent_at_local": "08:12 IST",
                "opener": "Morning", "opener_source": "ada"})

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def prompt(self, pid="2026-10-15-morning"):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)

    def test_a_reply_to_an_open_prompt_is_a_capture_that_folds_the_telegram_message(self):
        info = capture.capture_web(self.cfg, "morning", "Slept well.", NOW)
        self.assertEqual((info["rule"], Path(info["path"]).name), ("web", "2026-10-15-morning.md"))
        p = self.prompt()
        self.assertTrue(p["needs_edit"])
        cap = p["captures"][0]
        self.assertEqual((cap["rule"], cap["source"], cap["via"]), ("web", "web", "web"))
        text = Path(info["path"]).read_text(encoding="utf-8")
        self.assertIn("### 09:12 IST · text\n\nSlept well.\n", text)
        self.assertEqual(capture.open_prompts(ledger.snapshot(self.cfg.ledger_path), self.cfg, NOW), [])

    def test_without_an_open_prompt_it_is_a_note_and_nothing_is_folded(self):
        info = capture.capture_web(self.cfg, "evening", "A thought.", NOW)
        self.assertEqual((info["rule"], Path(info["path"]).name), ("/j", "2026-10-15-j.md"))
        self.assertIsNone(self.prompt() .get("captures"))
        note = self.prompt("2026-10-15-j")
        self.assertFalse(note["needs_edit"])
        again = capture.capture_web(self.cfg, None, "Another.", NOW)
        self.assertEqual(Path(again["path"]).name, "2026-10-15-j-2.md")

    def test_a_retry_with_the_same_client_id_writes_once(self):
        first = capture.capture_web(self.cfg, "morning", "Once.", NOW, client_id="abc")
        second = capture.capture_web(self.cfg, "morning", "Once.", NOW, client_id="abc")
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["path"], first["path"])
        self.assertEqual(Path(first["path"]).read_text(encoding="utf-8").count("Once."), 1)

    def test_the_client_id_is_its_own_field_and_never_a_telegram_message_id(self):
        capture.capture_web(self.cfg, "morning", "Once.", NOW, client_id="abc")
        cap = self.prompt()["captures"][0]
        self.assertEqual(cap["client_id"], "abc")
        self.assertIsNone(cap["platform_message_id"])

    def test_a_note_dated_before_the_prompt_was_sent_does_not_answer_it(self):
        sent = NOW - timedelta(hours=1)
        info = capture.capture_web(self.cfg, "morning", "Two days ago.", NOW, at=sent - timedelta(days=2))
        self.assertEqual((info["rule"], Path(info["path"]).name), ("/j", "2026-10-13-j.md"))
        self.assertIn("### ", Path(info["path"]).read_text(encoding="utf-8"))
        self.assertIsNone(self.prompt().get("captures"))
        ok = capture.capture_web(self.cfg, "morning", "Since it was sent.", NOW, at=sent + timedelta(minutes=1))
        self.assertEqual(ok["rule"], "web")

    def test_a_recording_is_kept_beside_the_entry_and_waits_for_its_transcript(self):
        media = Path(self.tmp.name) / "up.bin"
        media.write_bytes(b"OggS-recording")
        info = capture.capture_web(self.cfg, "morning", "", NOW, media=str(media), ext=".ogg")
        kept = self.cfg.root / "2026-10-15-morning.ogg"
        self.assertEqual(kept.read_bytes(), b"OggS-recording")
        text = Path(info["path"]).read_text(encoding="utf-8")
        self.assertIn("![[2026-10-15-morning.ogg]]", text)
        self.assertIn("transcript pending", text)
        cap = self.prompt()["captures"][0]
        self.assertEqual((cap["source"], cap["audio"], cap["transcript"], cap["via"]),
                         ("audio", "2026-10-15-morning.ogg", "pending", "web"))

    def test_a_block_goes_through_the_entrys_prompt_and_an_untracked_entry_gets_it_directly(self):
        first = capture.capture_web(self.cfg, "morning", "First.", NOW)
        stem = Path(first["path"]).stem
        blk = capture.add_web_block(self.cfg, stem, "Second.", NOW + timedelta(minutes=5))
        self.assertEqual(blk["rule"], "web")
        self.assertEqual(len(self.prompt()["captures"]), 2)
        text = Path(first["path"]).read_text(encoding="utf-8")
        self.assertLess(text.index("First."), text.index("Second."))
        # an entry the ledger no longer knows (pruned) or one the companion started: written straight in
        lone = self.cfg.root / "2026-10-10-evening.md"
        lone.write_text("---\nkind: evening\npractice: evening\ntype: journal\n---\n\n# E\n\n## Sam\n\n"
                        "_Nothing from Sam yet._\n\n## Ada's annotations\n\n_Pending._\n", encoding="utf-8")
        out = capture.add_web_block(self.cfg, "2026-10-10-evening", "Late words.", NOW)
        self.assertTrue(out["untracked"])
        body = lone.read_text(encoding="utf-8")
        self.assertIn("Late words.", body)
        self.assertNotIn("_Nothing from Sam yet._", body)
        self.assertTrue(body.index("Late words.") < body.index("Ada's annotations"))

    def test_a_retried_block_on_an_untracked_entry_is_written_once(self):
        lone = self.cfg.root / "2026-10-10-evening.md"
        lone.write_text("---\nkind: evening\npractice: evening\ntype: journal\n---\n\n# E\n\n## Sam\n\n"
                        "_Nothing from Sam yet._\n\n## Ada's annotations\n\n_Pending._\n", encoding="utf-8")
        first = capture.add_web_block(self.cfg, "2026-10-10-evening", "Retried words.", NOW, client_id="r1")
        again = capture.add_web_block(self.cfg, "2026-10-10-evening", "Retried words.", NOW, client_id="r1")
        self.assertNotIn("duplicate", first)
        self.assertTrue(again["duplicate"])
        self.assertEqual(lone.read_text(encoding="utf-8").count("Retried words."), 1)
        other = capture.add_web_block(self.cfg, "2026-10-10-evening", "A different block.", NOW, client_id="r2")
        self.assertNotIn("duplicate", other)
        # old memories are dropped, so the ledger does not grow without end
        with ledger.locked(self.cfg.ledger_path) as data:
            data["web_blocks"]["r1"]["at"] = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        capture.add_web_block(self.cfg, "2026-10-10-evening", "Later.", NOW, client_id="r3")
        self.assertEqual(sorted(ledger.snapshot(self.cfg.ledger_path)["web_blocks"]), ["r2", "r3"])


if __name__ == "__main__":
    unittest.main()
