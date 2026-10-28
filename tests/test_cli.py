"""The `journal` command line: argument handling, exit codes, and that secrets never reach output."""
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, CleanEnv, add_prompt, write_config

from journal import cli, tick
from journal.config import load


def run_cli(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:  # argparse
            code = e.code
    return code, out.getvalue(), err.getvalue()


class ArgsTest(CleanEnv):
    def test_help_version_and_missing_command(self):
        code, out, _ = run_cli("--help")
        self.assertEqual(code, 0)
        for sub in ("tick", "send", "status", "doctor", "practices", "migrate-config", "migrate-state"):
            self.assertIn(sub, out)
        code, out, _ = run_cli("--version")
        self.assertEqual((code, out.strip()), (0, "journal 2.0.0"))
        code, out, _ = run_cli()
        self.assertEqual(code, 2)
        self.assertIn("usage:", out)

    def test_bad_arguments_exit_2(self):
        self.assertEqual(run_cli("send", "lunch")[0], 2)
        self.assertEqual(run_cli("migrate-config")[0], 2)  # --from is required
        self.assertEqual(run_cli("nonsense")[0], 2)

    def test_configuration_problem_exit_2(self):
        code, _out, err = run_cli("--config", "/nonexistent/x.json", "status")
        self.assertEqual(code, 2)
        self.assertIn("configuration problem", err)


class CommandsTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "Journal").mkdir()
        self.conf = write_config(t / "c.json", {"root": str(t / "Journal"), "state_dir": str(t / "state"),
                                               "timezone": "Asia/Kolkata", "telegram": {"chat_id": CHAT},
                                               "companion": {"name": "Iris", "emoji": "🌻"}})
        self.cfg = load(self.conf)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_send_dry_run_previews_without_sending(self):
        with mock.patch.object(tick, "_opener", lambda *a: ("Evening 🌻 hi", "iris", [], [])):
            code, out, _ = run_cli("--config", str(self.conf), "send", "evening", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[dry-run] ", out)
        self.assertIn("Evening journal", out)
        self.assertFalse(self.cfg.ledger_path.exists() and json.loads(self.cfg.ledger_path.read_text())["prompts"])

    def test_tick_records_its_run_and_status_reports_it(self):
        add_prompt(self.cfg, sent=datetime.now(timezone.utc) - timedelta(hours=1), message_id="5")
        with mock.patch.object(tick, "fold", lambda cfg, now, log: log.append("p: fold failed (timeout)")), \
                mock.patch.object(tick, "send_due", lambda *a, **k: None):
            code, out, _ = run_cli("--config", str(self.conf), "tick")
        self.assertEqual(code, 0)
        self.assertIn("fold failed", out)
        code, out, _ = run_cli("--config", str(self.conf), "status")
        self.assertEqual(code, 0)
        self.assertIn("Last run:", out)
        self.assertIn("Open prompts: 2026-10-14-morning", out)
        self.assertIn("Recent errors:", out)
        self.assertIn("fold failed (timeout)", out)
        code, out, _ = run_cli("--config", str(self.conf), "status", "--json")
        s = json.loads(out)
        self.assertEqual(s["open_prompts"][0]["id"], "2026-10-14-morning")
        self.assertEqual(s["last_tick"]["mode"], "tick")

    def test_status_pending_annotations_read_the_entry(self):
        from journal import capture, entries
        add_prompt(self.cfg, sent=datetime.now(timezone.utc) - timedelta(hours=1), message_id="5")
        info = capture.capture(self.cfg, self.cfg.chat_id, "5", "words", datetime.now(timezone.utc))
        code, out, _ = run_cli("--config", str(self.conf), "status", "--json")
        self.assertEqual(json.loads(out)["pending_annotations"], ["2026-10-14-morning"])
        entries.write_annotations(Path(info["path"]), "- annotated by an older engine")
        code, out, _ = run_cli("--config", str(self.conf), "status", "--json")
        self.assertEqual(json.loads(out)["pending_annotations"], [])

    def test_send_failure_alert_exits_1(self):
        def fail(*a, **k):
            raise tick.SendFailed("2026-10-14-evening was not delivered after 3 attempts: x")
        with mock.patch.object(tick, "send_due", fail):
            code, out, _ = run_cli("--config", str(self.conf), "tick")
        self.assertEqual(code, 1)
        self.assertIn("ALERT:", out)

    def test_doctor_never_prints_the_token(self):
        os.environ["JOURNAL_TELEGRAM_TOKEN"] = "123456:SECRET-TOKEN-VALUE"
        code, out, err = run_cli("--config", str(self.conf), "doctor")
        self.assertNotIn("SECRET-TOKEN-VALUE", out + err)
        self.assertIn("✓ telegram token: obtained from environment variable JOURNAL_TELEGRAM_TOKEN", out)
        self.assertIn("✓ journal root", out)
        del os.environ["JOURNAL_TELEGRAM_TOKEN"]
        code, out, _ = run_cli("--config", str(self.conf), "doctor", "--json")
        rows = {r["check"]: r for r in json.loads(out)}
        self.assertEqual(rows["telegram token"]["status"], "fail")
        self.assertEqual(code, 1)

    def test_doctor_token_command(self):
        data = json.loads(self.conf.read_text())
        data["telegram"]["token_command"] = ["/bin/sh", "-c", "echo OTHER=1; echo TELEGRAM_BOT_TOKEN=abc:XYZSECRET"]
        self.conf.write_text(json.dumps(data))
        code, out, _ = run_cli("--config", str(self.conf), "doctor")
        self.assertIn("obtained from token_command", out)
        self.assertNotIn("XYZSECRET", out)

    def test_migrate_config_to_stdout_and_file(self):
        old = write_config(Path(self.tmp.name) / "old.json", {"root": "/j", "telegram_chat_id": 5, "profile": "iris"})
        code, out, err = run_cli("migrate-config", "--from", str(old))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["telegram"], {"chat_id": "5"})
        dest = Path(self.tmp.name) / "new.json"
        self.assertEqual(run_cli("migrate-config", "--from", str(old), "--to", str(dest))[0], 0)
        self.assertEqual(run_cli("migrate-config", "--from", str(old), "--to", str(dest))[0], 2)  # exists

    def test_migrate_config_moves_a_schedule_block_into_forks(self):
        old = write_config(Path(self.tmp.name) / "old.json", {"root": "/j", "telegram_chat_id": 5,
                                                              "schedule": {"evening": {"time": "22:15"}}})
        practices = Path(self.tmp.name) / "my-practices"
        code, out, err = run_cli("migrate-config", "--from", str(old), "--practices-dir", str(practices))
        self.assertEqual(code, 0)
        self.assertNotIn("schedule", json.loads(out))  # printed, not carried
        self.assertIn("nothing was forked: pass --to", err)
        self.assertFalse(practices.exists())
        dest = Path(self.tmp.name) / "new.json"
        code, _out, err = run_cli("migrate-config", "--from", str(old), "--to", str(dest),
                                  "--practices-dir", str(practices))
        self.assertEqual(code, 0)
        self.assertIn(f"schedule.evening: forked evening to {practices / 'evening.md'} with daily 22:15", err)
        self.assertIn("schedule.time: 22:15", (practices / "evening.md").read_text(encoding="utf-8"))
        # the same, under a profile home: the forks land where that config's practices_dir is
        home = Path(self.tmp.name) / "profile"
        dest2 = Path(self.tmp.name) / "new2.json"
        code, _out, err = run_cli("--hermes-home", str(home), "migrate-config", "--from", str(old), "--to", str(dest2))
        self.assertEqual(code, 0)
        self.assertTrue((home / "plugin-data" / "journal" / "practices" / "evening.md").is_file())
        # a place that cannot hold the files is an error, not a half-migration
        blocker = Path(self.tmp.name) / "blocked"
        blocker.write_text("x")
        code, _out, err = run_cli("migrate-config", "--from", str(old), "--to", str(Path(self.tmp.name) / "n3.json"),
                                  "--practices-dir", str(blocker))
        self.assertEqual(code, 2)
        self.assertFalse((Path(self.tmp.name) / "n3.json").exists())

    def test_migrate_state_defaults_to_the_configured_state_dir(self):
        old = Path(self.tmp.name) / "old-state"
        old.mkdir()
        (old / "ledger.json").write_text('{"version": 1, "prompts": []}')
        code, out, _ = run_cli("--config", str(self.conf), "migrate-state", "--from", str(old))
        self.assertEqual(code, 0, out)
        self.assertTrue((self.cfg.state_dir / "ledger.json").is_file())
        self.assertEqual(run_cli("--config", str(self.conf), "migrate-state", "--from", str(old))[0], 2)

    def test_module_entry_point(self):
        import subprocess
        import sys
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
        out = subprocess.run([sys.executable, "-m", "journal", "--version"], capture_output=True, text=True, env=env)
        self.assertEqual(out.stdout.strip(), "journal 2.0.0")


GRATITUDE = """---
kind: practice
id: gratitude
title: Daily gratitude
schedule.time: 21:30
---
- Three things today, and why each one mattered.
- Who made one of them possible?
"""


class PracticesCliTest(CleanEnv):
    """``journal practices list | show | validate | render | fork | reset | diff`` over a temporary practices
    directory."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        (t / "Journal").mkdir()
        self.conf = write_config(t / "c.json", {"root": str(t / "Journal"), "state_dir": str(t / "state"),
                                               "timezone": "Europe/Lisbon", "telegram": {"chat_id": "1"},
                                               "companion": {"name": "Ada", "emoji": "🌿"}, "user": {"name": "Sam"}})
        self.dir = t / "state" / "practices"

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def cli(self, *argv):
        return run_cli("--config", str(self.conf), "practices", *argv)

    def write(self, name, text):
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(text, encoding="utf-8")
        return self.dir / name

    def test_list_shows_the_four_built_ins(self):
        code, out, err = self.cli("list")
        self.assertEqual((code, err), (0, ""))
        lines = out.splitlines()
        self.assertEqual([l.split()[0] for l in lines], ["morning", "evening", "midweek", "weekly"])
        self.assertRegex(lines[1], r"^evening +Evening journal +daily 23:00 +built-in$")
        self.assertRegex(lines[2], r"^midweek +Midweek check-in +Wed 12:00 +built-in$")
        self.assertRegex(lines[3], r"^weekly +Weekly pattern scan +Sun 20:00 +built-in$")

    def test_list_with_user_files_and_a_problem(self):
        self.write("gratitude.md", GRATITUDE)
        self.write("evening.md", GRATITUDE.replace("id: gratitude", "id: evening").replace("schedule.time: 21:30\n", ""))
        self.write("broken.md", "not a practice")
        code, out, _ = self.cli("list")
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual([l.split()[0] for l in lines[:5]], ["morning", "gratitude", "midweek", "weekly", "evening"])
        self.assertRegex(lines[1], rf"^gratitude +Daily gratitude +daily 21:30 +user +{self.dir / 'gratitude.md'}$")
        self.assertRegex(lines[4], rf"^evening +Daily gratitude +on demand +edited +{self.dir / 'evening.md'}$")
        self.assertTrue(lines[-1].startswith(f"! {self.dir / 'broken.md'}: no front matter"))
        code, out, _ = self.cli("list", "--json")
        data = json.loads(out)
        self.assertEqual([p["id"] for p in data["practices"]], ["morning", "gratitude", "midweek", "weekly", "evening"])
        self.assertEqual([p["source"] for p in data["practices"]], ["builtin", "user", "builtin", "builtin", "fork"])
        self.assertEqual(data["practices"][1]["schedule"], [{"time": "21:30", "weekday": None}])
        self.assertEqual(data["practices_dir"], str(self.dir))
        self.assertEqual(len(data["problems"]), 1)

    def test_list_survives_a_file_the_parser_once_crashed_on(self):
        bad = self.write("gratitude.md", GRATITUDE.replace("21:30", "21:30\nopener.limit: ²"))
        code, out, err = self.cli("list")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(out.splitlines()[-1], f"! {bad}: opener.limit must be a positive whole number (got '²')")
        code, out, _ = self.cli("validate", str(bad))
        self.assertEqual((code, out), (1, f"{bad}: opener.limit must be a positive whole number (got '²')\n"))

    def test_show(self):
        code, out, _ = self.cli("show", "evening")
        self.assertEqual(code, 0)
        self.assertIn("evening · Evening journal (built-in)\n", out)
        self.assertIn("schedule: daily 23:00\n", out)
        self.assertIn("plain opener: Evening, Sam 🌿 Let the day land.", out)
        self.assertIn("1. Brain Dump & Letting Go\n   [1.1] What's spinning in your mind?\n", out)
        self.assertIn("   [9.2] What simple joys touched you today?\n", out)
        code, out, _ = self.cli("show", "evening", "--json")
        data = json.loads(out)
        self.assertEqual((data["id"], data["source"], data["closes"]), ("evening", "builtin", "day"))
        self.assertEqual(data["sections"][8]["questions"][1], {"id": "9.2", "text": "What simple joys touched you today?",
                                                               "pinned": False})
        self.assertTrue(data["raw"].startswith("---\nkind: practice\nid: evening\n"))
        code, _, err = self.cli("show", "lunch")
        self.assertEqual(code, 2)
        self.assertIn("no practice 'lunch' (known: morning, evening, midweek, weekly)", err)

    def test_validate_the_library_and_files(self):
        code, out, _ = self.cli("validate")
        self.assertEqual(code, 0)
        self.assertEqual(out, f"ok: 4 practices (4 built-in, 0 user) in {self.dir}\n")
        good = self.write("gratitude.md", GRATITUDE)
        fork = self.write("evening.md", GRATITUDE.replace("id: gratitude", "id: evening"))
        self.assertEqual(self.cli("validate")[1], f"ok: 5 practices (3 built-in, 2 user) in {self.dir}\n")
        bad = self.write("bad.md", GRATITUDE.replace("21:30", "9pm"))
        code, out, _ = self.cli("validate")
        self.assertEqual(code, 1)
        self.assertEqual(out, f"{bad}: schedule.time must be HH:MM (got '9pm')\n")
        code, out, _ = self.cli("validate", str(good), str(fork), str(bad))
        self.assertEqual(code, 1)
        self.assertEqual(out.splitlines(), [f"{good}: ok", f"{fork}: ok (replaces the built-in 'evening')",
                                            f"{bad}: schedule.time must be HH:MM (got '9pm')"])
        self.assertEqual(self.cli("validate", str(good))[0], 0)

    def test_render(self):
        from journal import library
        code, out, _ = self.cli("render", "morning")
        self.assertEqual((code, out), (0, library.builtins()["morning"].raw))
        loose = self.write("loose.md", GRATITUDE.replace("- Three", "Prose first.\n\n* Three"))
        code, out, _ = self.cli("render", str(loose))
        self.assertEqual(code, 0)
        self.assertIn("\n---\n- Three things today, and why each one mattered.\n- Who made", out)
        self.assertIn("opener.limit: 1100\n", out)  # every default written out
        bad = self.write("bad.md", "---\nid: x\n---\n")
        code, out, err = self.cli("render", str(bad))
        self.assertEqual((code, out), (1, ""))
        self.assertIn(f"{bad}: kind is required", err)
        self.assertEqual(self.cli("render", "lunch")[0], 2)

    def test_fork_reports_filesystem_errors_plainly(self):
        # A practices_dir that is a regular file raised FileExistsError from mkdir, read as "already forked".
        self.dir.parent.mkdir(parents=True, exist_ok=True)
        self.dir.write_text("not a directory", encoding="utf-8")
        code, out, err = self.cli("fork", "evening")
        self.assertEqual((code, out), (1, ""))
        self.assertTrue(err.startswith(f"journal: cannot fork into {self.dir}: "), err)
        self.assertNotIn("delete that file", err)
        self.dir.unlink()
        if os.geteuid() != 0:  # root ignores the mode
            self.dir.mkdir()
            self.dir.chmod(0o500)
            try:
                code, out, err = self.cli("fork", "evening")
            finally:
                self.dir.chmod(0o700)
            self.assertEqual((code, out), (1, ""))
            self.assertTrue(err.startswith(f"journal: cannot fork into {self.dir}: "), err)

    def test_fork(self):
        code, out, _ = self.cli("fork", "evening")
        self.assertEqual(code, 0)
        dest = self.dir / "evening.md"
        self.assertIn(str(dest), out)
        self.assertTrue(dest.is_file())
        self.assertIn("edited", [l for l in self.cli("list")[1].splitlines() if l.startswith("evening")][0].split())
        code, _, err = self.cli("fork", "evening")
        self.assertEqual(code, 1)
        self.assertIn("already forked", err)
        self.assertIn("journal practices reset evening", err)
        code, _, err = self.cli("fork", "gratitude")
        self.assertEqual(code, 2)
        self.assertIn("only a built-in can be forked (morning, evening, midweek, weekly, fix-life, ", err)

    def test_fork_a_program_and_a_situational_definition(self):
        code, out, _ = self.cli("fork", "fix-life")
        self.assertEqual(code, 0, out)
        self.assertTrue((self.dir / "fix-life" / "program.md").is_file())
        self.assertIn("journal practices reset fix-life", out)
        self.assertEqual(self.cli("fork", "conversation-prep")[0], 0)
        self.assertTrue((self.dir / "conversation-prep.md").is_file())

    def test_reset_and_diff(self):
        code, out, _ = self.cli("diff", "evening")
        self.assertEqual((code, out), (0, "evening: not edited; the shipped default is in force\n"))
        code, out, _ = self.cli("reset", "evening")
        self.assertEqual((code, out), (0, "evening: not edited; the shipped built-in is already in force\n"))
        self.cli("fork", "evening")
        path = self.dir / "evening.md"
        path.write_text(path.read_text(encoding="utf-8").replace("annotate: true", "annotate: false"), encoding="utf-8")
        code, out, _ = self.cli("diff", "evening")
        self.assertEqual(code, 0)
        self.assertIn("# the default has not changed since you edited it\n", out)
        self.assertIn("-annotate: true\n+annotate: false\n", out)
        self.assertIn("(your edit of the built-in)", self.cli("show", "evening")[1])
        code, out, _ = self.cli("reset", "evening")
        self.assertEqual((code, out), (0, f"reset evening: removed {path}; the shipped built-in is in force from the "
                                          "next tick\n"))
        self.assertFalse(path.exists())
        self.assertIn("(built-in)", self.cli("show", "evening")[1])
        code, _, err = self.cli("reset", "gratitude")
        self.assertEqual(code, 2)
        self.assertIn("only a built-in can be reset", err)
        self.assertEqual(self.cli("diff", "nope")[0], 2)

    def test_a_default_that_changed_since_the_edit_is_said(self):
        from journal import library
        self.cli("fork", "midweek")
        with library.held(self.dir) as state:
            rec = state["defaults"]["midweek"]
            rec["files"]["midweek.md"] = rec["files"]["midweek.md"].replace("Midweek check-in", "Midweek")
            rec["sha"] = library._fingerprint(rec["files"])
        line = [l for l in self.cli("list")[1].splitlines() if l.startswith("midweek")][0]
        self.assertIn("edited, default changed", line)
        self.assertIn("the default has changed since you edited it: journal practices diff midweek",
                      self.cli("show", "midweek")[1])
        out = self.cli("diff", "midweek")[1]
        self.assertIn("# what changed in the default since you edited it (", out)
        self.assertIn("+title: Midweek check-in", out)

    def test_reset_of_a_program_waits_for_a_run_that_its_structure_would_change(self):
        import json
        self.cli("fork", "fix-life")
        program = self.dir / "fix-life" / "program.md"
        program.write_text(program.read_text(encoding="utf-8").replace(", 10-one-day-reset", ""), encoding="utf-8")
        runs = self.dir.parent / "runs" / "fix-life"
        runs.mkdir(parents=True)
        (runs / "r1.json").write_text(json.dumps({"id": "r1", "program": "fix-life", "status": "active",
                                                  "current": {"session": 2, "phase": 1}}), encoding="utf-8")
        code, _, err = self.cli("reset", "fix-life")
        self.assertEqual(code, 1)
        self.assertIn("has a run in progress (session 2, phase 1)", err)
        self.assertTrue(program.is_file())
        (runs / "r1.json").write_text(json.dumps({"id": "r1", "program": "fix-life", "status": "abandoned"}),
                                      encoding="utf-8")
        self.assertEqual(self.cli("reset", "fix-life")[0], 0)
        self.assertFalse(program.exists())

    def test_no_subcommand_is_a_usage_error(self):
        code, out, err = self.cli()
        self.assertEqual(code, 2)
        self.assertIn("list", out + err)


if __name__ == "__main__":
    unittest.main()
