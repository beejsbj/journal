"""Hermes side: registration through the root wrapper, `hermes journal setup` (mocked cron calls),
uninstall, doctor checks, and settings read through the plugin context."""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import CHAT, CleanEnv, ROOT, add_prompt, write_config

from journal import capture, context, hookstate, status
from journal.config import load
from journal.hermes import cron, hooks, runtime


class FakeCron:
    """Plays `hermes cron …`: records calls and keeps jobs in a list rendered like `cron list --all`."""

    def __init__(self):
        self.calls = []
        self.jobs = []

    def __call__(self, args):
        self.calls.append(list(args))
        if args[:2] == ["cron", "create"]:
            job = {"id": f"{len(self.jobs) + 1:012x}", "name": args[args.index("--name") + 1],
                   "schedule": args[2], "deliver": args[args.index("--deliver") + 1],
                   "state": "paused" if "--paused" in args else "active"}
            self.jobs.append(job)
            return subprocess.CompletedProcess(args, 0, "created\n", "")
        if args[:2] == ["cron", "list"]:
            text = "".join(f"\n  {j['id']} [{j['state']}]\n    Name:      {j['name']}\n    Schedule:  {j['schedule']}\n"
                           f"    Repeat:    ∞\n    Deliver:   {j['deliver']}\n" for j in self.jobs)
            return subprocess.CompletedProcess(args, 0, text, "")
        if args[:2] == ["cron", "remove"]:
            self.jobs = [j for j in self.jobs if j["id"] != args[2]]
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["cron", "edit"]:
            job = next((j for j in self.jobs if j["id"] == args[2]), None)
            if job is not None and "--schedule" in args:
                job["schedule"] = args[args.index("--schedule") + 1]
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 1, "", "unexpected")

    def kinds(self):
        return [c[1] for c in self.calls if c[0] == "cron"]


class HermesHome(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.home = t / ".hermes" / "profiles" / "iris"
        self.home.mkdir(parents=True)
        (t / "Journal").mkdir()
        self.conf = write_config(t / "iris.json", {"root": str(t / "Journal"), "telegram": {"chat_id": CHAT},
                                                  "companion": {"name": "Iris", "emoji": "🌻"},
                                                  "user": {"name": "Theo", "pronouns": "he/him/his"},
                                                  "guard": {"escalate_to": "Max"}})
        os.environ["HERMES_HOME"] = str(self.home)  # Hermes' own resolver honours it too
        os.environ["JOURNAL_CONFIG"] = str(self.conf)
        self.cfg = load()
        self.src = ROOT / "src"

    def tearDown(self):
        runtime.unbind()
        self.tmp.cleanup()
        super().tearDown()


class SetupTest(HermesHome):
    def test_setup_creates_the_job_once(self):
        fake = FakeCron()
        code, lines = cron.setup(self.cfg, self.src, run=fake)
        self.assertEqual(code, 0, lines)
        create = next(c for c in fake.calls if c[:2] == ["cron", "create"])
        self.assertEqual(create, ["cron", "create", "*/5 * * * *", "--no-agent", "--script", "journal-tick.py",
                                  "--deliver", "local", "--failure-deliver", f"telegram:{CHAT}", "--name", "journal tick"])
        stub = self.home / "scripts" / "journal-tick.py"
        text = stub.read_text()
        self.assertIn(repr(str(self.src)), text)
        self.assertIn(f"os.environ[\"HERMES_HOME\"] = {str(self.home)!r}", text)
        compile(text, str(stub), "exec")
        mtime = stub.stat().st_mtime_ns
        code, lines = cron.setup(self.cfg, self.src, run=fake)  # again: nothing new
        self.assertEqual(code, 0)
        self.assertEqual(fake.kinds().count("create"), 1)
        self.assertEqual(len(fake.jobs), 1)
        self.assertTrue(any("already set up" in l for l in lines), lines)
        self.assertTrue(any(l.startswith("kept ") for l in lines))
        self.assertEqual(stub.stat().st_mtime_ns, mtime)

    def test_setup_respects_failure_deliver_and_paused(self):
        self.cfg.hermes["failure_deliver"] = "bot-chat:max"
        fake = FakeCron()
        cron.setup(self.cfg, self.src, run=fake, paused=True)
        create = next(c for c in fake.calls if c[:2] == ["cron", "create"])
        self.assertIn("bot-chat:max", create)
        self.assertIn("--paused", create)
        self.assertEqual(fake.jobs[0]["state"], "paused")

    def test_setup_fixes_a_drifted_job_from_the_store(self):
        (self.home / "cron").mkdir()
        (self.home / "cron" / "jobs.json").write_text(json.dumps({"jobs": [
            {"id": "abc123abc123", "name": "journal tick", "script": "journal-tick.py", "no_agent": True,
             "enabled": True, "schedule": {"kind": "cron", "expr": "*/10 * * * *"}, "deliver": "local",
             "failure_deliver": "telegram:1"}]}))
        fake = FakeCron()
        code, lines = cron.setup(self.cfg, self.src, run=fake)
        self.assertEqual(code, 0)
        edit = next(c for c in fake.calls if c[:2] == ["cron", "edit"])
        self.assertEqual(edit, ["cron", "edit", "abc123abc123", "--schedule", "*/5 * * * *",
                                "--failure-deliver", f"telegram:{CHAT}"])
        self.assertNotIn("create", fake.kinds())

    def test_duplicates_reported_not_deleted(self):
        fake = FakeCron()
        fake.jobs = [{"id": "aaaaaaaaaaaa", "name": "journal tick", "schedule": "*/5 * * * *", "deliver": "local",
                      "state": "active"},
                     {"id": "bbbbbbbbbbbb", "name": "journal tick", "schedule": "*/5 * * * *", "deliver": "local",
                      "state": "active"}]
        code, lines = cron.setup(self.cfg, self.src, run=fake)
        self.assertEqual(code, 0)
        self.assertTrue(any("2 journal tick jobs" in l for l in lines))
        self.assertNotIn("remove", fake.kinds())

    def test_create_failure_is_reported(self):
        def broken(args):
            if args[:2] == ["cron", "create"]:
                return subprocess.CompletedProcess(args, 1, "", "scheduler unavailable")
            return subprocess.CompletedProcess(args, 0, "", "")
        code, lines = cron.setup(self.cfg, self.src, run=broken)
        self.assertEqual(code, 1)
        self.assertIn("scheduler unavailable", lines[-1])

    def test_uninstall_removes_job_and_stub_only(self):
        fake = FakeCron()
        cron.setup(self.cfg, self.src, run=fake)
        (self.cfg.state_dir).mkdir(parents=True, exist_ok=True)
        (self.cfg.state_dir / "ledger.json").write_text("{}")
        code, lines = cron.uninstall(self.cfg, run=fake)
        self.assertEqual(code, 0)
        self.assertEqual(fake.jobs, [])
        self.assertFalse((self.home / "scripts" / "journal-tick.py").exists())
        self.assertTrue((self.cfg.state_dir / "ledger.json").exists())

    def test_not_under_hermes(self):
        os.environ.pop("HERMES_HOME")
        cfg = load()
        self.assertEqual(cron.setup(cfg, self.src, run=FakeCron())[0], 2)

    def test_doctor_rows(self):
        (self.home / "config.yaml").write_text("plugins:\n  enabled: [journal, old-journal]\n")
        self.assertNotIn("legacy plugin", {n for n, s, d in cron.checks(self.cfg, self.src, run=FakeCron())})
        for bad in ("old-journal", False, [1]):   # not a list of names: a warning, never a silent pass
            self.cfg.hermes["legacy_plugins"] = bad
            self.assertEqual({n: s for n, s, d in cron.checks(self.cfg, self.src, run=FakeCron())}["legacy plugin"],
                             "warn", bad)
        self.cfg.hermes["legacy_plugins"] = ["old-journal"]
        fake = FakeCron()
        rows = {n: (s, d) for n, s, d in cron.checks(self.cfg, self.src, run=fake)}
        self.assertEqual(rows["cron stub"][0], "fail")
        self.assertEqual(rows["cron job"][0], "fail")
        self.assertEqual(rows["legacy plugin"][0], "fail")
        cron.setup(self.cfg, self.src, run=fake)
        rows = {n: (s, d) for n, s, d in cron.checks(self.cfg, self.src, run=fake)}
        self.assertEqual(rows["cron stub"][0], "ok")
        self.assertEqual(rows["cron job"][0], "ok")
        self.assertEqual(rows["plugin enabled"][0], "ok")

    def test_parse_cron_list(self):
        jobs = cron.parse_cron_list("  a1b2c3d4e5f6 [paused]\n    Name:      Journal engine tick (Iris)\n"
                                    "    Schedule:  */5 * * * *\n    Deliver:   local\n")
        self.assertEqual((jobs[0].id, jobs[0].enabled, jobs[0].name), ("a1b2c3d4e5f6", False, "Journal engine tick (Iris)"))

    def test_stub_runs_a_tick(self):
        path, _ = cron.write_stub(self.home, self.src)
        env = {k: v for k, v in os.environ.items() if k != "HERMES_HOME"}
        env["JOURNAL_TELEGRAM_TOKEN"] = ""
        state = Path(self.tmp.name) / "stub-state"
        env["JOURNAL_STATE_DIR"] = str(state)
        # Never let a due ritual reach a real agent or Telegram from a test: no model, nothing scheduled.
        off = {k: {"time": "00:00", "enabled": False} for k in ("morning", "evening", "midweek", "weekly")}
        env["JOURNAL_CONFIG"] = str(write_config(Path(self.tmp.name) / "stub.json", {
            **json.loads(self.conf.read_text()), "agent": {"enabled": False}, "hermes": {"command": "/bin/false"},
            "schedule": off}))
        out = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, env=env, timeout=60,
                             cwd=str(self.home))
        # Nothing is due (or a due send fails for want of a token, which is reported, not raised)
        self.assertIn(out.returncode, (0, 1), out.stderr)
        self.assertTrue((state / "last-tick.json").is_file(), out.stderr)


class HookRestartTest(HermesHome):
    """What one hook callback learns for a later one is on disk, so a gateway restart between them loses
    nothing. The restart is simulated by reloading the hooks module (which emptied its dicts before)."""

    def setUp(self):
        super().setUp()
        self.cfg.root.mkdir(exist_ok=True)
        self.now = datetime.now(timezone.utc)
        self.prompt = add_prompt(self.cfg, kind="evening", sent=self.now - timedelta(minutes=20), message_id="700")
        self.cache = Path(self.tmp.name) / "cache"
        self.cache.mkdir()

    def event(self, text="", kind="TEXT", reply_to=None, media=None, types=None, mid="9"):
        return SimpleNamespace(text=text, message_type=SimpleNamespace(name=kind), message_id=mid,
                               reply_to_message_id=reply_to, media_urls=media or [], media_types=types or [],
                               raw_message=None,
                               source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id=CHAT))

    def restart(self):
        importlib.reload(hooks)

    def test_pending_voice_note_survives_a_restart(self):
        media = self.cache / "v.ogg"
        media.write_bytes(b"\x00ogg")
        out = hooks.on_dispatch(event=self.event(kind="VOICE", media=[str(media)], types=["audio/ogg"], reply_to="700"))
        self.assertEqual(hookstate.summary(self.cfg)["pending_voice"], 1)
        self.restart()
        hooks.on_llm_call(user_message='"slept badly, long walk"\n\n' + out["text"], platform="telegram")
        body = (self.cfg.root / "2026-10-14-evening.md").read_text()
        self.assertIn("![[2026-10-14-evening.ogg]]\n\nslept badly, long walk", body)
        self.assertEqual(hookstate.summary(self.cfg)["pending_voice"], 0)

    def test_a_voice_note_the_state_file_cannot_hold_is_left_to_the_sweep(self):
        # The sweep skips every row carrying the hook's note as already saved, so a note the hook could not
        # remember must go through unmarked (and its audio copy, which nothing would reference, must go too).
        media = self.cache / "v.ogg"
        media.write_bytes(b"\x00ogg")
        with mock.patch.object(hookstate, "remember_voice", side_effect=OSError("read-only state dir")):
            out = hooks.on_dispatch(event=self.event(kind="VOICE", media=[str(media)], types=["audio/ogg"],
                                                     reply_to="700"))
        self.assertIsNone(out)
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), [])
        self.assertEqual(hookstate.summary(self.cfg)["pending_voice"], 0)

    def test_queued_pack_survives_a_restart(self):
        context.save(self.cfg, self.prompt["id"], "# Context pack\n\n## Weather\nRain, PACKFACT\n")
        out = hooks.on_dispatch(event=self.event("rainy, tired", reply_to="700", mid="701"))
        self.assertEqual(hookstate.summary(self.cfg)["pending_packs"], 1)
        self.restart()
        ctx = hooks.on_llm_call(user_message=out["text"], platform="telegram")["context"]
        self.assertIn("PACKFACT", ctx)
        self.assertIsNone(hooks.on_llm_call(user_message=out["text"], platform="telegram"))  # once only

    def test_off_record_turn_survives_a_restart(self):
        note = capture.off_record_note(self.cfg)
        hooks.on_llm_call(user_message=note + "a secret", platform="telegram", session_id="s1", turn_id="t1")
        self.restart()
        out = hooks.on_tool_call(tool_name="journal_add_note", args={"note": "the secret"}, session_id="s1",
                                 turn_id="t1")
        self.assertEqual(out["action"], "block")
        self.assertIn("Off the record", out["message"])
        self.assertEqual(list(self.cfg.root.glob("*.md")), [])
        hooks.on_llm_call(user_message="public again", platform="telegram", session_id="s1", turn_id="t2")
        self.assertEqual(hookstate.summary(self.cfg)["off_turns"], 0)

    def test_only_chat_turns_touch_the_state_file(self):
        # The dispatch hook marks only the chat platform's messages, so a CLI or cron turn has nothing to record
        # or clear; and a chat turn that finds none of its records changes nothing but the thread stamps (WP3:
        # ``last_his`` moves on every message of his, which is what makes "45 quiet minutes" decidable).
        path = hookstate.path(self.cfg)
        self.assertIsNone(hooks.on_llm_call(user_message="hello", platform="cli", session_id="s", turn_id="t"))
        self.assertFalse(path.exists())
        hooks.on_llm_call(user_message="hello", platform="telegram", session_id="s", turn_id="t")
        self.assertTrue(path.exists())  # the first chat turn shapes the file
        sections = {k: v for k, v in json.loads(path.read_text()).items() if k != "stamps"}
        hooks.on_llm_call(user_message="again", platform="telegram", session_id="s", turn_id="t2")
        self.assertEqual({k: v for k, v in json.loads(path.read_text()).items() if k != "stamps"}, sections)
        before = (path.stat().st_ino, path.stat().st_mtime_ns)
        hooks.on_llm_call(user_message="again", platform="cli", session_id="s", turn_id="t3")
        self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns), before)

    def test_doctor_shows_the_hook_state_row(self):
        rows = {n: (st, d) for n, st, d in status.doctor(self.cfg, token=False)}
        self.assertEqual(rows["hook state"][0], "ok")
        self.assertIn("not written yet", rows["hook state"][1])
        hooks.on_llm_call(user_message=capture.off_record_note(self.cfg) + "x", platform="telegram", session_id="s")
        rows = {n: (st, d) for n, st, d in status.doctor(self.cfg, token=False)}
        self.assertIn("1 off-record turn(s)", rows["hook state"][1])
        hookstate.path(self.cfg).write_text("{not json")
        rows = {n: (st, d) for n, st, d in status.doctor(self.cfg, token=False)}
        self.assertEqual(rows["hook state"][0], "warn")
        self.assertIn("not readable", rows["hook state"][1])


class FakeCtx:
    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        self.tools, self.hooks, self.commands, self.skills, self.cli = [], [], [], {}, {}
        self.skill_notes = {}
        self.profile_name = "iris"

    def register_tool(self, name, toolset, schema, handler, description="", emoji=""):
        self.tools.append((name, toolset, schema))

    def register_hook(self, name, cb):
        self.hooks.append(name)

    def register_command(self, name, handler, description=""):
        self.commands.append(name)

    def register_skill(self, name, path, description=""):
        self.skills[name] = Path(path)
        self.skill_notes[name] = description

    def register_cli_command(self, name, help, setup_fn, handler_fn=None, description=""):
        self.cli[name] = (setup_fn, handler_fn)

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def set_config(self, key, value):
        self.settings[key] = value


def load_wrapper():
    """Import the repository root the way Hermes imports a directory plugin."""
    spec = importlib.util.spec_from_file_location("hermes_plugins.journal", ROOT / "__init__.py",
                                                  submodule_search_locations=[str(ROOT)])
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RegisterTest(HermesHome):
    def test_wrapper_registers_everything(self):
        ctx = FakeCtx({"config_file": str(self.conf)})
        load_wrapper().register(ctx)
        self.assertEqual([t[0] for t in ctx.tools], ["journal_notebook", "journal_set_location", "journal_add_note",
                                                  "journal_program"])  # WP6
        self.assertEqual({t[1] for t in ctx.tools}, {"journal"})
        self.assertEqual(ctx.hooks, ["pre_gateway_dispatch", "pre_llm_call", "pre_tool_call"])
        self.assertEqual(ctx.commands, ["j", "x", "morning", "evening", "midweek", "weekly", "morning-journal",
                                        "evening-journal", "journal-start",  # WP2: /start <id>, routed by the hook
                                        "next", "skip", "back", "break", "abandon",   # WP4: the program commands
                                        "program",                                     # (abandon: ONE-PROGRAM)
                                        "web"])                                        # WEB-COMMAND
        self.assertIn("journal", ctx.cli)
        note = next(s for n, _ts, s in ctx.tools if n == "journal_add_note")
        self.assertIn("Theo's journal", note["description"])
        self.assertIn("\"Iris's notes\"", note["description"])

    def test_skill_rendered_with_names(self):
        ctx = FakeCtx({"config_file": str(self.conf)})
        load_wrapper().register(ctx)
        path = ctx.skills["journal-system"]
        self.assertTrue(str(path).startswith(str(self.home / "plugin-data" / "journal")))
        text = path.read_text()
        self.assertNotIn("{{", text)
        self.assertIn("How Iris converses when Theo answers", text)
        self.assertIn("offer to ask Max", text)
        self.assertIn(str(self.conf), text)
        self.assertTrue((path.parent / "references" / "voice-and-consent.md").is_file())

    def test_the_registered_skill_description_follows_the_capture_mode(self):
        ctx = FakeCtx({"config_file": str(self.conf)})
        load_wrapper().register(ctx)
        everything = ctx.skill_notes["journal-system"]
        self.assertIn("(the conversation too, unless /x)", everything)
        ctx = FakeCtx({"config_file": str(self.conf), "capture": {"mode": "arm"}})
        load_wrapper().register(ctx)
        arm = ctx.skill_notes["journal-system"]
        self.assertIn("not ordinary chat", arm)
        self.assertNotIn("unless /x", arm)
        self.assertIn("Ordinary chat that answers no journal prompt is not saved", ctx.skills["journal-system"].read_text())

    def test_a_skill_that_cannot_be_rendered_in_place_is_rendered_elsewhere_not_registered_raw(self):
        import journal.hermes as plugin
        real = plugin.render_skill
        calls = []

        def flaky(dest, tokens):
            calls.append(dest)
            if len(calls) == 1:
                raise OSError("state directory not writable")
            return real(dest, tokens)
        ctx = FakeCtx({"config_file": str(self.conf), "capture": {"mode": "arm"}})
        with mock.patch.object(plugin, "render_skill", flaky):
            load_wrapper().register(ctx)
        text = ctx.skills["journal-system"].read_text()
        self.assertEqual(len(calls), 2)
        self.assertNotIn("{{", text)
        self.assertIn("Ordinary chat that answers no journal prompt is not saved", text)
        self.assertNotIn("Any other thread becomes a note entry of its own", text)

    def test_unconfigured_profile_still_registers_neutrally(self):
        os.environ.pop("JOURNAL_CONFIG")
        ctx = FakeCtx({})
        load_wrapper().register(ctx)
        self.assertEqual(len(ctx.tools), 4)
        text = ctx.skills["journal-system"].read_text()
        self.assertIn("How Companion converses when the user answers", text)
        self.assertIn("**Fix your entire life in 1 day** (`/start fix-life`)", text)  # the built-in programs

    def test_settings_come_from_the_plugin_context(self):
        ctx = FakeCtx({"config_file": str(self.conf), "location": "Chicago"})
        runtime.bind(ctx)
        cfg = runtime.load_config()
        self.assertEqual((cfg.location, cfg.origins["location"]), ("Chicago", "hermes"))
        env = runtime.child_env(cfg)
        self.assertEqual(json.loads(env["JOURNAL_HERMES_SETTINGS"])["location"], "Chicago")
        self.assertEqual(env["HERMES_HOME"], str(self.home))

    def test_hermes_journal_cli_setup_and_status(self):
        ctx = FakeCtx({})
        load_wrapper().register(ctx)
        setup_fn, handler = ctx.cli["journal"]
        parser = argparse.ArgumentParser(prog="hermes journal")
        setup_fn(parser)
        fake = FakeCron()
        out = io.StringIO()
        with mock.patch.object(cron, "hermes_runner", lambda cfg: fake), contextlib.redirect_stdout(out):
            code = handler(parser.parse_args(["setup", "--config-file", str(self.conf)]))
        self.assertEqual(code, 0, out.getvalue())
        self.assertEqual(ctx.settings["config_file"], str(self.conf.resolve()))
        self.assertEqual(fake.kinds().count("create"), 1)
        with mock.patch.object(cron, "hermes_runner", lambda cfg: fake), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handler(parser.parse_args(["setup"])), 0)
            self.assertEqual(handler(parser.parse_args(["status"])), 0)
        self.assertEqual(fake.kinds().count("create"), 1)  # idempotent through the CLI too
        with mock.patch.object(cron, "hermes_runner", lambda cfg: fake), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(handler(parser.parse_args(["uninstall"])), 0)
        self.assertEqual(fake.jobs, [])

    def test_hermes_journal_practices(self):
        ctx = FakeCtx({"config_file": str(self.conf)})
        load_wrapper().register(ctx)
        setup_fn, handler = ctx.cli["journal"]
        parser = argparse.ArgumentParser(prog="hermes journal")
        setup_fn(parser)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(handler(parser.parse_args(["practices", "list"])), 0)
            self.assertEqual(handler(parser.parse_args(["practices", "show", "midweek", "--json"])), 0)
            self.assertEqual(handler(parser.parse_args(["practices", "validate"])), 0)
        lines = out.getvalue().splitlines()
        self.assertEqual([l.split()[0] for l in lines[:4]], ["morning", "evening", "midweek", "weekly"])
        self.assertIn('"id": "midweek"', out.getvalue())
        self.assertTrue(lines[-1].startswith("ok: 4 practices (4 built-in, 0 user) in "))

    def test_hermes_journal_without_config_explains(self):
        os.environ.pop("JOURNAL_CONFIG")
        ctx = FakeCtx({})
        load_wrapper().register(ctx)
        setup_fn, handler = ctx.cli["journal"]
        parser = argparse.ArgumentParser()
        setup_fn(parser)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(handler(parser.parse_args(["setup"])), 2)
        self.assertIn("plugins.entries.journal.settings.root", err.getvalue())


if __name__ == "__main__":
    unittest.main()
