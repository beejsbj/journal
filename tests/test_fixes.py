"""Small fixes: his words may hold any Markdown, kept audio is world-readable, a crashed tick step
exits 1, and an empty `root` setting never hides the config file's."""
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, CleanEnv, add_prompt, make_cfg, write_config

from journal import capture, config, entries, tick

WORDS = "## Not a section\nmy own heading above\n# and a title\n## Iris's annotations\nthat line was mine too"


class HeadingsInHisWordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.sent = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)
        add_prompt(self.cfg, sent=self.sent)

    def tearDown(self):
        self.tmp.cleanup()

    def test_any_markdown_survives_annotations_notes_and_appends(self):
        info = capture.capture(self.cfg, CHAT, "100", WORDS, self.sent + timedelta(hours=1))
        path = Path(info["path"])
        self.assertIn(WORDS, entries.raw_entry(path.read_text()))
        self.assertTrue(entries.annotations_pending(path.read_text()))
        entries.write_annotations(path, "## A model heading\n- bullet", self.cfg.names)
        capture.add_companion_note(self.cfg, "## note heading\nnote", self.sent + timedelta(hours=2),
                                   "2026-10-14", "morning")
        capture.capture(self.cfg, CHAT, "100", "second reply", self.sent + timedelta(hours=3))
        body = path.read_text()
        raw = entries.raw_entry(body)
        self.assertIn(WORDS, raw)
        self.assertTrue(raw.rstrip().endswith("second reply"))
        self.assertEqual(entries.annotations(body), "### A model heading\n- bullet")
        self.assertIn("### note heading", body[entries.notes_span(body)[0]:])
        self.assertEqual(body.count("## Iris's annotations\n"), 2)  # his line, and the real heading
        entries.write_annotations(path, "- rewritten", self.cfg.names)
        body2 = path.read_text()
        self.assertIn(WORDS, entries.raw_entry(body2))
        self.assertEqual(entries.annotations(body2), "- rewritten")
        self.assertIn("note heading", body2)


class TickExitTest(unittest.TestCase):
    def test_a_crashed_step_exits_1_soft_failures_exit_0(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            quiet = mock.patch.object(tick, "send_due", lambda *a, **k: None)
            with quiet, mock.patch.object(tick, "fold", lambda cfg, now, log: log.append("x: fold failed (t)")):
                code, log = tick.run(cfg)
            self.assertEqual(code, 0, log)

            def boom(*a, **k):
                raise ValueError("bad entry")
            with quiet, mock.patch.object(tick, "annotate", boom):
                code, log = tick.run(cfg)
            self.assertEqual(code, 1)
            self.assertIn("error: annotate: ValueError: bad entry", log)


class KeptAudioModeTest(CleanEnv):
    def test_kept_audio_is_0644(self):
        from journal import inbound
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            src = Path(t) / "cache.ogg"
            src.write_bytes(b"x")
            os.chmod(src, 0o600)
            name = inbound.keep_audio(cfg, str(src), str(cfg.root / "2026-10-14-evening.md"), ".ogg")
            self.assertEqual(stat.S_IMODE((cfg.root / name).stat().st_mode), 0o644)


class EmptyRootSettingTest(CleanEnv):
    def test_empty_root_in_settings_does_not_override_the_file(self):
        with tempfile.TemporaryDirectory() as t:
            conf = write_config(Path(t) / "c.json", {"root": str(Path(t) / "J")})
            cfg = config.load(settings={"config_file": str(conf), "root": ""}, hermes_home=Path(t) / "home")
            self.assertEqual(cfg.root, Path(t) / "J")
            with self.assertRaises(config.ConfigError):
                config.load(settings={"root": ""}, hermes_home=Path(t) / "home")

    def test_schema_root_not_required(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not installed")
        from helpers import ROOT
        spec = yaml.safe_load((ROOT / "plugin.yaml").read_text())["config_schema"]["root"]
        self.assertFalse(spec.get("required", False))
        self.assertEqual(spec.get("default"), "")


if __name__ == "__main__":
    unittest.main()


class SentArchiveTest(unittest.TestCase):
    def test_every_delivered_prompt_is_archived_and_survives_prune(self):
        import json
        from zoneinfo import ZoneInfo
        from journal import ledger, telegram
        ist = ZoneInfo("Asia/Kolkata")
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            ids = iter(["555", "556"])
            asked = [{"id": "1", "question": "How did you sleep?", "as": "Sleep okay?"}]
            with mock.patch.object(telegram, "send_html", lambda *a, **k: next(ids)), \
                    mock.patch.object(telegram, "edit_html", lambda *a, **k: None), \
                    mock.patch.object(tick, "_opener", lambda *a: ("Morning 🌻 how was the night?", "iris", [], asked)):
                now = datetime(2026, 10, 14, 6, 5, tzinfo=ist)
                tick.send_due(cfg, now, [])
                tick.send_due(cfg, now, [], only="morning")  # a manual resend
            rows = [json.loads(l) for l in (cfg.state_dir / "sent.jsonl").read_text().splitlines()]
            self.assertEqual([r["message_id"] for r in rows], ["555", "556"])
            self.assertEqual(rows[0]["opener"], "Morning 🌻 how was the night?")
            self.assertEqual(rows[0]["asked"], asked)
            self.assertEqual((rows[0]["id"], rows[0]["kind"], rows[0]["local_date"]),
                             ("2026-10-14-morning", "morning", "2026-10-14"))
            self.assertEqual(rows[1]["previous_message_ids"], ["555"])
            self.assertNotIn("html", rows[0])
            tick.prune(cfg, datetime(2027, 6, 1, tzinfo=timezone.utc))
            self.assertEqual(ledger.snapshot(cfg.ledger_path)["prompts"], [])
            self.assertFalse((cfg.state_dir / "sent.jsonl").read_text().strip())  # same retention as the ledger

    def test_sent_archive_keeps_what_the_ledger_keeps(self):
        import json
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            now = datetime(2026, 10, 14, 12, 0, tzinfo=timezone.utc)
            old = (now - timedelta(days=tick.KEEP_DAYS + 1)).isoformat()
            new = (now - timedelta(days=tick.KEEP_DAYS - 1)).isoformat()
            rows = [{"id": "old", "sent_at": old}, {"id": "new", "sent_at": new}, {"id": "undated"}]
            path = cfg.state_dir / "sent.jsonl"
            cfg.state_dir.mkdir(parents=True)
            path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
            tick.prune(cfg, now)
            kept = [l for l in path.read_text().splitlines()]
            self.assertEqual([json.loads(l)["id"] for l in kept[:2]], ["new", "undated"])
            self.assertEqual(kept[2], "not json")  # unreadable lines are left alone, not lost
            self.assertEqual(oct(path.stat().st_mode & 0o777), "0o600")
            before = path.stat().st_ino
            tick.prune(cfg, now)  # nothing to drop: the file is not rewritten
            self.assertEqual(path.stat().st_ino, before)
