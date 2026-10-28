"""Configuration layering, precedence, origins, Hermes settings and write-back."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from helpers import CleanEnv, real_ask, write_config

from journal import config as jconfig
from journal.config import ConfigError, load

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def hermes_home_with_settings(base: Path, settings: dict, name: str = "iris") -> Path:
    home = base / ".hermes" / "profiles" / name
    home.mkdir(parents=True)
    (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {"enabled": ["journal"],
                                                                  "entries": {"journal": {"settings": settings}}}}))
    return home


class DefaultsTest(CleanEnv):
    def test_root_is_required(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_config(Path(t) / "c.json", {"timezone": "UTC"})
            with self.assertRaises(ConfigError) as e:
                load(p)
            self.assertIn("root", str(e.exception))

    def test_bad_timezone_and_unreadable_file(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(ConfigError):
                load(write_config(Path(t) / "c.json", {"root": t, "timezone": "Mars/Olympus"}))
            bad = Path(t) / "bad.json"
            bad.write_text("{not json")
            with self.assertRaises(ConfigError):
                load(bad)
            with self.assertRaises(ConfigError):
                load(Path(t) / "missing.json")  # an explicit path must exist

    def test_generic_defaults(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = load(write_config(Path(t) / "c.json", {"root": t}))
            self.assertEqual(cfg.names.companion, "Companion")
            self.assertEqual(cfg.names.user, "")
            self.assertEqual(cfg.timezone, "UTC")
            self.assertEqual(cfg.chat_id, "")
            self.assertFalse(cfg.source_enabled("tududi"))
            self.assertTrue(cfg.source_enabled("weather"))
            self.assertEqual(cfg.telegram["token_env"], "JOURNAL_TELEGRAM_TOKEN")
            self.assertIsNone(cfg.hermes_home)
            self.assertEqual(cfg.origins["timezone"], "default")
            self.assertEqual(cfg.origins["root"], "file")

    def test_nested_objects_merge_lists_replace(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = load(write_config(Path(t) / "c.json", {
                "root": t, "windows": {"collapse_hours": 3}, "guard": {"protected_paths": ["/a"]},
                "sources": {"listenbrainz": {"enabled": True, "user": "sam"}}}))
            self.assertEqual(cfg.collapse_hours, 3.0)
            self.assertEqual(cfg.grace_hours, 2.0)
            self.assertEqual(cfg.guard["protected_paths"], ["/a"])
            self.assertTrue(cfg.guard["restrict_hermes_cli"])
            self.assertEqual(cfg.source("listenbrainz")["user"], "sam")
            self.assertEqual(cfg.source("scrobbler")["command"], "chat-scrobbler")

    def test_problems_reported(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = load(write_config(Path(t) / "c.json", {"root": t}))
            problems = "\n".join(jconfig.problems(cfg))
            self.assertIn("telegram.chat_id is not set", problems)
            self.assertNotIn("schedule", problems)

    def test_a_grace_shorter_than_one_tick_is_named_in_doctor(self):
        with tempfile.TemporaryDirectory() as t:
            for grace, flagged in ((0, True), (0.1, True), (0.25, False), (2, False)):
                cfg = load(write_config(Path(t) / "c.json", {"root": t, "windows": {"grace_hours": grace}}))
                with self.subTest(grace=grace):
                    self.assertEqual(any("windows.grace_hours" in p for p in jconfig.problems(cfg)), flagged)

    def test_unusable_today_settings_are_named_not_swallowed(self):
        with tempfile.TemporaryDirectory() as t:
            def built(today):
                cfg = load(write_config(Path(t) / "c.json", {"root": t, "today": today}))
                return cfg, "\n".join(jconfig.problems(cfg) + cfg.warnings)
            cfg, _ = built(False)                       # `false` is the natural "off"
            self.assertIs(cfg.today.get("enabled"), False)
            cfg, text = built("off")                    # not an object: warned, defaults, config survives
            self.assertIn("today must be an object", text)
            self.assertNotIn("enabled", cfg.today)
            _, text = built({"enabled": "false"})
            self.assertIn("today.enabled must be true or false", text)
            _, text = built({"enabled": 0})
            self.assertIn("today.enabled must be true or false", text)
            _, text = built({"sources": ["tasks", "wether"]})
            self.assertIn("unknown names ['wether']", text)


class FileResolutionTest(CleanEnv):
    def test_env_var_and_xdg_default(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_config(Path(t) / "env.json", {"root": t, "location": "env"})
            os.environ["JOURNAL_CONFIG"] = str(p)
            self.assertEqual(load().location, "env")
            del os.environ["JOURNAL_CONFIG"]
            os.environ["XDG_CONFIG_HOME"] = t
            write_config(Path(t) / "journal" / "config.json", {"root": t, "location": "xdg"})
            self.assertEqual(load().location, "xdg")
            self.assertEqual(load(p).location, "env")  # --config wins

    def test_standalone_state_dir_defaults(self):
        with tempfile.TemporaryDirectory() as t:
            os.environ["XDG_STATE_HOME"] = str(Path(t) / "state")
            p = write_config(Path(t) / "c.json", {"root": t})
            self.assertEqual(load(p).state_dir, Path(t) / "state" / "journal")
            os.environ["JOURNAL_STATE_DIR"] = str(Path(t) / "override")
            self.assertEqual(load(p).state_dir, Path(t) / "override")


@unittest.skipIf(yaml is None, "PyYAML not installed")
class HermesLayerTest(CleanEnv):
    def test_settings_override_file_and_record_origin(self):
        with tempfile.TemporaryDirectory() as t:
            f = write_config(Path(t) / "iris.json", {"root": t, "timezone": "Asia/Kolkata", "location": "Hillside",
                                                     "companion": {"name": "Iris", "emoji": "🌻"}})
            home = hermes_home_with_settings(Path(t), {"config_file": str(f), "location": "Chicago",
                                                       "companion": {"emoji": "🌷"}})
            cfg = load(hermes_home=home)
            self.assertEqual(cfg.config_file, f)
            self.assertEqual(cfg.location, "Chicago")
            self.assertEqual(cfg.timezone, "Asia/Kolkata")
            self.assertEqual((cfg.names.companion, cfg.names.emoji), ("Iris", "🌷"))
            self.assertEqual(cfg.origins["location"], "hermes")
            self.assertEqual(cfg.origins["timezone"], "file")
            self.assertEqual(cfg.state_dir, home / "plugin-data" / "journal")
            self.assertEqual(cfg.state_db, home / "state.db")
            self.assertEqual(cfg.profile_args(), [])  # the exact home, by HERMES_HOME, not a derived -p name
            self.assertEqual(cfg.hermes_env()["HERMES_HOME"], str(home))

    def test_named_profile_uses_p_and_drops_home(self):
        with tempfile.TemporaryDirectory() as t:
            os.environ["HERMES_HOME"] = "/somewhere/else"
            cfg = load(write_config(Path(t) / "c.json", {"root": t, "hermes": {"profile": "iris"}}))
            self.assertEqual(cfg.profile_args(), ["-p", "iris"])
            self.assertNotIn("HERMES_HOME", cfg.hermes_env())

    def test_hermes_home_from_env_and_settings_only(self):
        with tempfile.TemporaryDirectory() as t:
            home = hermes_home_with_settings(Path(t), {"root": t, "telegram": {"chat_id": 42}})
            os.environ["HERMES_HOME"] = str(home)
            cfg = load()
            self.assertEqual(cfg.chat_id, "42")
            self.assertIsNone(cfg.config_file)  # under Hermes there is no implicit config file

    def test_snapshot_env_beats_config_yaml(self):
        with tempfile.TemporaryDirectory() as t:
            home = hermes_home_with_settings(Path(t), {"root": t, "location": "yaml"})
            os.environ["JOURNAL_HERMES_SETTINGS"] = json.dumps({"root": t, "location": "snapshot"})
            self.assertEqual(load(hermes_home=home).location, "snapshot")

    def test_legacy_config_subtree_read(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t) / "h"
            home.mkdir()
            (home / "config.yaml").write_text(yaml.safe_dump(
                {"plugins": {"entries": {"journal": {"config": {"root": t, "location": "legacy"}}}}}))
            self.assertEqual(load(hermes_home=home).location, "legacy")

    def test_broken_config_yaml_is_a_warning_not_a_crash(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t) / "h"
            home.mkdir()
            (home / "config.yaml").write_text("plugins: [unclosed")
            f = write_config(Path(t) / "c.json", {"root": t})
            cfg = load(f, hermes_home=home)
            self.assertTrue(any("could not parse" in w for w in cfg.warnings))

    def test_default_home_has_no_profile_flag_but_sets_env(self):
        with tempfile.TemporaryDirectory() as t:
            home = Path(t) / ".hermes"
            home.mkdir()
            cfg = load(write_config(Path(t) / "c.json", {"root": t}), hermes_home=home)
            self.assertEqual(cfg.profile_args(), [])
            self.assertEqual(cfg.hermes_env()["HERMES_HOME"], str(home))
            self.assertEqual(jconfig.hermes_data_root(home), home)
            self.assertEqual(jconfig.hermes_data_root(home / "profiles" / "x"), home)


class WriteBackTest(CleanEnv):
    def test_file_owned_keys_written_to_file(self):
        with tempfile.TemporaryDirectory() as t:
            f = write_config(Path(t) / "c.json", {"root": t, "timezone": "UTC", "other": 1})
            cfg = load(f)
            calls = []
            written = jconfig.write_values(cfg, {"timezone": "Asia/Tokyo"}, hermes_writer=lambda k, v: calls.append(k))
            self.assertEqual(written, {"timezone": "file"})
            self.assertEqual(calls, [])
            data = json.loads(f.read_text())
            self.assertEqual((data["timezone"], data["other"]), ("Asia/Tokyo", 1))

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_hermes_owned_keys_go_through_the_writer(self):
        with tempfile.TemporaryDirectory() as t:
            f = write_config(Path(t) / "c.json", {"root": t})
            home = hermes_home_with_settings(Path(t), {"config_file": str(f), "timezone": "UTC"})
            cfg = load(hermes_home=home)
            calls = []
            written = jconfig.write_values(cfg, {"timezone": "Asia/Tokyo", "location": "Tokyo"},
                                           hermes_writer=lambda k, v: calls.append((k, v)))
            self.assertEqual(written, {"timezone": "hermes", "location": "file"})  # location unset: the file exists
            self.assertEqual(calls, [("timezone", "Asia/Tokyo")])
            with self.assertRaises(ConfigError):
                jconfig.write_values(cfg, {"timezone": "UTC"})  # no writer for a Hermes-owned key

    def test_unset_key_with_only_hermes_settings_goes_to_hermes(self):
        cfg = jconfig.from_dict({"root": "/tmp/j"}, hermes_home=Path("/tmp/h"),
                                origins={"root": "hermes"})
        calls = []
        self.assertEqual(jconfig.write_values(cfg, {"location": "x"}, lambda k, v: calls.append(k)),
                         {"location": "hermes"})


class AdapterTest(CleanEnv):
    """``adapter`` picks the transcript and the companion: auto (Hermes when there is one), none, and the
    values that are not built or not valid, which warn and fall back to auto."""

    def cfg(self, **kw):
        return jconfig.from_dict({"root": "/tmp/j", **kw}, hermes_home=Path("/tmp/h"), environ={})

    def adapter_warnings(self, cfg):
        return [w for w in cfg.warnings if "adapter" in w]

    def test_values_and_their_warnings(self):
        for given in ("auto", "none"):
            c = self.cfg(adapter=given)
            self.assertEqual((c.adapter, self.adapter_warnings(c)), (given, []))
        c = self.cfg()
        self.assertEqual((c.adapter, self.adapter_warnings(c)), ("auto", []))
        c = self.cfg(adapter="plainchat")
        self.assertEqual(c.adapter, "auto")
        self.assertIn("plainchat is not built yet", self.adapter_warnings(c)[0])
        for given in ("Hermes", 5):
            c = self.cfg(adapter=given)
            self.assertEqual(c.adapter, "auto")
            self.assertEqual(len(self.adapter_warnings(c)), 1)
            self.assertNotIn("not built yet", self.adapter_warnings(c)[0])

    def test_the_companion_follows_the_adapter_and_a_command_wins(self):
        from journal import agent
        from journal.hermes.companion import HermesCompanion
        self.assertIsInstance(self.cfg().companion(), HermesCompanion)
        self.assertIsInstance(self.cfg(adapter="none").companion(), agent.NoCompanion)
        for adapter in ("auto", "none"):
            c = self.cfg(adapter=adapter, agent={"command": ["run-it", "{query_file}"]})
            self.assertIsInstance(c.companion(), agent.CommandCompanion)
            self.assertNotIsInstance(c.companion(), (agent.NoCompanion, HermesCompanion))

    def test_with_no_companion_ask_is_an_outage_and_runs_nothing(self):
        from unittest import mock
        from journal import agent
        c = self.cfg(adapter="none")
        with mock.patch("subprocess.run", side_effect=AssertionError("ran a command")):
            with self.assertRaises(agent.AgentUnavailable) as e:
                real_ask(c, "q", 10)
        self.assertIn("adapter is none", str(e.exception))
        self.assertEqual(agent.failure_reason(str(e.exception)), "could not run the agent")
        c = self.cfg(adapter="none", agent={"enabled": False})
        self.assertIsNone(real_ask(c, "q", 10))  # a switched-off agent stays off, not an outage

    def test_doctor_says_when_no_companion_will_answer(self):
        from journal import status
        with tempfile.TemporaryDirectory() as t:
            c = jconfig.from_dict({"root": t, "state_dir": f"{t}/state", "adapter": "none"}, hermes_home=Path(t),
                                  environ={})
            (row,) = [r for r in status.doctor(c, token=False) if r[0] == "agent"]
            self.assertEqual(row[1], "warn")
            self.assertIn("agent.command", row[2])
            c = jconfig.from_dict({"root": t, "state_dir": f"{t}/state", "adapter": "none",
                                   "agent": {"command": ["/bin/sh"]}}, hermes_home=Path(t), environ={})
            (row,) = [r for r in status.doctor(c, token=False) if r[0] == "agent"]
            self.assertEqual(row[1], "ok")


if __name__ == "__main__":
    unittest.main()


class EndStateTest(CleanEnv):
    """WP13: the shims are gone, and the plugin's settings form is the configuration."""

    def test_retired_names_are_gone(self):
        import importlib
        self.assertFalse(hasattr(jconfig, "KINDS"))
        self.assertFalse(hasattr(jconfig, "DEFAULT_SCHEDULE"))
        self.assertNotIn("schedule", jconfig.DEFAULTS)
        with self.assertRaises(ImportError):
            importlib.import_module("journal.questions")

    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_plugin_yaml_schema_is_exactly_the_configurable_keys(self):
        from helpers import ROOT
        schema = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))["config_schema"]
        self.assertEqual(set(schema), set(jconfig.TOP_LEVEL_KEYS))
        for key, spec in schema.items():
            with self.subTest(key):
                self.assertTrue(spec.get("description"), "every setting says what it is")
                if "default" in spec and key in jconfig.DEFAULTS and isinstance(spec["default"], dict):
                    default = jconfig.DEFAULTS[key]
                    self.assertTrue(set(spec["default"]) <= set(default), (key, spec["default"]))

    def test_the_ritual_ids_come_from_the_packaged_files(self):
        from journal import entries, library
        self.assertEqual(library.builtin_ids(), ("morning", "evening", "midweek", "weekly"))
        self.assertEqual(entries.known_ids(), (*library.builtin_ids(), "j", "note"))
        for pid in library.builtin_ids():  # each keeps its command, so none can be the off-the-record prefix
            self.assertTrue(jconfig.opt_out_problem(f"/{pid}"), pid)
        self.assertEqual(jconfig.opt_out_problem("/x"), "")
