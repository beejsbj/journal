"""migrate-config and migrate-state: correct output, and the source is never changed."""
import fcntl
import hashlib
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from helpers import CleanEnv, write_config

from journal import library, migrate
from journal.config import load

# The shape of the predecessor's journal.json (version 2).
OLD = {
    "version": 2,
    "root": "/srv/notes/Journal",
    "timezone": "Asia/Kolkata",
    "location": "Hillside",
    "telegram_chat_id": "100200300",
    "profile": "iris",
    "schedule": {"morning": {"time": "06:00"}, "evening": {"time": "23:00"},
                 "midweek": {"time": "12:00", "weekday": 2}, "weekly": {"time": "20:00", "weekday": 6}},
    "collapse_hours": 6,
    "grace_hours": 2,
    "opener_budget_seconds": 240,
    "annotate_after_minutes": 20,
    "coordinates": {"lat": 12.3456, "lon": 65.4321, "place": "Hillside (assumed; update when travelling)"},
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class MigrateConfigTest(CleanEnv):
    def test_old_file_converted(self):
        new, notes = migrate.convert_config(OLD)
        self.assertEqual(new["telegram"], {"chat_id": "100200300"})
        self.assertEqual(new["hermes"], {"profile": "iris"})
        self.assertEqual(new["windows"], {"collapse_hours": 6, "grace_hours": 2, "opener_budget_seconds": 240,
                                          "annotate_after_minutes": 20})
        for k in ("root", "timezone", "location", "coordinates"):
            self.assertEqual(new[k], OLD[k])
        self.assertNotIn("schedule", new)  # nothing reads it any more: migrate_config moves it into practice files
        self.assertEqual(notes, [])

    def test_unknown_and_state_dir_reported(self):
        _new, notes = migrate.convert_config({**OLD, "state_dir": "/old", "mystery": 1})
        self.assertTrue(any("state_dir" in n for n in notes))
        self.assertTrue(any("mystery" in n for n in notes))

    def test_written_file_loads_with_overlay_and_source_untouched(self):
        with tempfile.TemporaryDirectory() as t:
            src = write_config(Path(t) / "old.json", OLD)
            before = digest(src)
            overlay = write_config(Path(t) / "deploy.json", {"user": {"name": "Theo"},
                                                             "companion": {"name": "Iris", "emoji": "🌻"},
                                                             "telegram": {"token_command": ["secret-env", "companion"]}})
            dest = Path(t) / "new" / "iris.json"
            new, notes = migrate.migrate_config(src, dest, overlay, practices_dir=Path(t) / "practices")
            self.assertEqual(digest(src), before)
            self.assertEqual(new["telegram"], {"chat_id": "100200300", "token_command": ["secret-env", "companion"]})
            cfg = load(dest)
            self.assertEqual((cfg.chat_id, cfg.names.companion, cfg.profile), ("100200300", "Iris", "iris"))
            self.assertEqual(cfg.coordinates, (12.3456, 65.4321))
            with self.assertRaises(FileExistsError):
                migrate.migrate_config(src, dest, practices_dir=Path(t) / "practices")
            migrate.migrate_config(src, dest, force=True, practices_dir=Path(t) / "practices")
            with self.assertRaises(ValueError):
                migrate.migrate_config(src, src, force=True, practices_dir=Path(t) / "practices")

    def test_new_format_passes_through(self):
        new, notes = migrate.convert_config({"root": "/j", "telegram": {"chat_id": "1"}})
        self.assertEqual(new, {"root": "/j", "telegram": {"chat_id": "1"}})
        self.assertIn("already in the new format", notes[0])


class MigrateScheduleTest(CleanEnv):
    """A ``schedule`` block (WP13: no longer read) is moved into forked practice files, with a note."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)
        self.practices = self.t / "practices"
        self.base = {"root": str(self.t / "journal"), "timezone": "Asia/Kolkata", "state_dir": str(self.t / "state")}

    def migrate(self, schedule, write=True, overlay=None, practices_dir="default", hermes_home=None, **extra):
        src = write_config(self.t / "old.json", {**self.base, "schedule": schedule, **extra})
        merge = write_config(self.t / "overlay.json", overlay) if overlay else None
        dest = self.t / "new.json" if write else None
        new, notes = migrate.migrate_config(src, dest, merge, hermes_home=hermes_home,
                                            practices_dir=self.practices if practices_dir == "default" else practices_dir)
        return new, notes, dest

    def sent(self, pid, hermes_home=None):
        """When the new setup sends ``pid``: the library of the written config, loaded the way the tick loads it
        (nothing set by hand: where the forks went must be where the engine reads)."""
        return library.get(load(self.t / "new.json", hermes_home=hermes_home), pid)

    def profile(self, settings):
        """A Hermes profile home whose config.yaml carries the plugin ``settings``."""
        import yaml
        home = self.t / "profile"
        home.mkdir(exist_ok=True)
        (home / "config.yaml").write_text(yaml.safe_dump({"plugins": {"entries": {"journal": {"settings": settings}}}}),
                                          encoding="utf-8")
        return home

    def test_the_live_shape_all_four_the_same_as_the_files_moves_nothing(self):
        new, notes, dest = self.migrate(OLD["schedule"])
        self.assertEqual([n for n in notes if n.startswith("schedule.")],
                         [f"schedule.{pid}: the built-in already says {said}; nothing to move" for pid, said in (
                             ("morning", "daily 06:00"), ("evening", "daily 23:00"), ("midweek", "Wed 12:00"),
                             ("weekly", "Sun 20:00"))])
        self.assertTrue(any("`schedule` is not carried over" in n for n in notes))
        self.assertNotIn("schedule", new)
        self.assertNotIn("schedule", json.loads(dest.read_text()))
        self.assertFalse(self.practices.exists())  # no fork was needed
        self.assertEqual(load(dest).warnings, [])  # and the written config has nothing to warn about

    def test_a_changed_time_is_forked_with_the_rest_of_the_built_in_unchanged(self):
        new, notes, dest = self.migrate({"evening": {"time": "22:00"}, "weekly": {"time": "19:00"},
                                         "midweek": {"weekday": 3}, "morning": {"time": "06:00"}})
        for pid, said in (("evening", "daily 22:00"), ("weekly", "Sun 19:00"), ("midweek", "Thu 12:00")):
            with self.subTest(pid):
                self.assertTrue(any(n.startswith(f"schedule.{pid}: forked {pid} to {self.practices / (pid + '.md')}"
                                                 f" with {said}") for n in notes), notes)
                got = self.sent(pid)
                self.assertEqual((library.describe_schedule(got.schedule), got.builtin), (said, False))
                self.assertEqual(got.sections, library.builtins()[pid].sections)
        self.assertFalse((self.practices / "morning.md").exists())  # said what the file says
        self.assertNotIn("schedule", json.loads(dest.read_text()))

    def test_practices_dir_is_recorded_in_the_config_when_it_is_not_where_the_config_points(self):
        new, notes, dest = self.migrate({"evening": {"time": "22:00"}})
        self.assertEqual(new["practices_dir"], str(self.practices))
        self.assertEqual(json.loads(dest.read_text())["practices_dir"], str(self.practices))
        self.assertTrue(any("practices_dir" in n and str(self.practices) in n for n in notes), notes)
        self.assertEqual(library.describe_schedule(self.sent("evening").schedule), "daily 22:00")

    def test_practices_dir_that_is_already_the_default_is_not_written(self):
        home = self.t / "profile"
        new, _notes, dest = self.migrate({"evening": {"time": "22:00"}}, hermes_home=home,
                                         practices_dir=home / "plugin-data" / "journal" / "practices")
        self.assertNotIn("practices_dir", new)
        self.assertEqual(library.describe_schedule(self.sent("evening", home).schedule), "daily 22:00")

    def test_the_forks_go_where_the_hermes_settings_put_practices_dir(self):
        mine = self.t / "custom-practices"
        home = self.profile({"practices_dir": str(mine)})
        _new, notes, dest = self.migrate({"evening": {"time": "21:00"}}, practices_dir=None, hermes_home=home)
        self.assertTrue((mine / "evening.md").is_file())
        self.assertFalse((home / "plugin-data").exists())
        self.assertTrue(any(f"forked evening to {mine / 'evening.md'}" in n for n in notes), notes)
        self.assertEqual(library.describe_schedule(self.sent("evening", home).schedule), "daily 21:00")

    def test_a_practices_dir_the_hermes_settings_would_override_is_refused_before_anything_is_written(self):
        home = self.profile({"practices_dir": str(self.t / "elsewhere")})
        with self.assertRaises(ValueError) as cm:
            self.migrate({"evening": {"time": "22:00"}}, hermes_home=home)
        self.assertIn("practices_dir", str(cm.exception))
        self.assertFalse(self.practices.exists())
        self.assertFalse((self.t / "elsewhere").exists())
        self.assertFalse((self.t / "new.json").exists())

    def test_a_bad_timezone_does_not_stop_locating_the_directory(self):
        _new, notes, _dest = self.migrate({"evening": {"time": "22:00"}}, timezone="Mars/Base")
        self.assertTrue((self.practices / "evening.md").is_file())

    def test_a_config_error_is_worded_after_itself(self):
        src = write_config(self.t / "cfg.json", {"root": str(self.t / "journal"), "state_dir": 5,
                                                 "telegram": {"chat_id": "1"}, "schedule": {"evening": {"time": "22:00"}}})
        with self.assertRaises(ValueError) as cm:
            migrate.migrate_config(src, self.t / "new.json", hermes_home=self.t / "profile")
        self.assertIn("state_dir", str(cm.exception))
        self.assertNotIn("--practices-dir", str(cm.exception))

    def test_a_schedule_in_the_hermes_settings_is_moved_too_and_wins(self):
        home = self.profile({"schedule": {"evening": {"time": "21:00"}, "weekly": {"time": "19:00"}}})
        _new, notes, _dest = self.migrate({"evening": {"time": "22:00"}}, hermes_home=home)
        self.assertEqual(library.describe_schedule(self.sent("evening", home).schedule), "daily 21:00")
        self.assertEqual(library.describe_schedule(self.sent("weekly", home).schedule), "Sun 19:00")
        self.assertTrue(any("Hermes settings" in n for n in notes), notes)
        self.assertTrue(any("hermes config unset plugins.entries.journal.settings.schedule" in n for n in notes), notes)

    def test_a_schedule_only_in_the_hermes_settings_is_moved(self):
        home = self.profile({"schedule": {"evening": {"time": "21:00"}}})
        src = write_config(self.t / "cfg.json", {"root": str(self.t / "journal"), "state_dir": str(self.t / "state"),
                                                 "telegram": {"chat_id": "1"}})
        dest = self.t / "new.json"
        _new, notes = migrate.migrate_config(src, dest, hermes_home=home)
        self.assertTrue((self.t / "state" / "practices" / "evening.md").is_file(), notes)
        self.assertTrue(any("forked evening" in n for n in notes), notes)

    def test_the_doctor_warning_for_a_hermes_schedule_names_the_steps_that_work(self):
        home = self.profile({"schedule": {"evening": {"time": "21:00"}}, "root": str(self.t / "journal")})
        warning = next(w for w in load(hermes_home=home).warnings if "`schedule`" in w)
        self.assertIn("migrate-config", warning)
        self.assertIn("--hermes-home", warning)
        self.assertIn("hermes config unset plugins.entries.journal.settings.schedule", warning)

    def test_without_to_nothing_is_written(self):
        new, notes, dest = self.migrate({"evening": {"time": "22:00"}}, write=False)
        self.assertTrue(any(n.startswith("schedule.evening: would fork evening into") for n in notes), notes)
        self.assertIn("nothing was forked: pass --to to write the config and the forks", notes)
        self.assertFalse(self.practices.exists())
        self.assertNotIn("schedule", new)

    def test_enabled_false_forks_an_on_demand_practice(self):
        _new, notes, _dest = self.migrate({"morning": {"enabled": False}})
        self.assertTrue(any("forked morning" in n and "with on demand" in n for n in notes), notes)
        self.assertEqual(self.sent("morning").schedule, ())

    def test_a_fork_keeps_the_window_the_built_in_folded_on(self):
        _new, notes, _dest = self.migrate({"evening": {"time": "22:00"}}, windows={"collapse_hours": 3}, telegram={"chat_id": "1"})
        self.assertEqual(self.sent("evening").collapse_hours, 3.0)
        self.assertTrue(any("collapse_hours: 3 (windows.collapse_hours" in n for n in notes), notes)

    def test_his_own_file_is_never_rewritten(self):
        self.practices.mkdir(parents=True)
        text = ("---\nkind: practice\nid: gratitude\ntitle: Daily gratitude\nschedule.time: 21:30\n---\n"
                "Some prose of his.\n- Three things today.\n")
        mine = self.practices / "gratitude.md"
        mine.write_text(text, encoding="utf-8")
        _new, notes, _dest = self.migrate({"gratitude": {"time": "20:00"}})
        self.assertEqual(mine.read_text(encoding="utf-8"), text)
        self.assertTrue(any("is your own file, so it is not rewritten; give it schedule.time: 20:00" in n
                            for n in notes), notes)

    def test_entries_that_cannot_be_used_are_named_and_skipped(self):
        _new, notes, _dest = self.migrate({"morning": {"time": "6am"}, "weekly": {"weekday": 9}, "lunch": {"time": "13:00"},
                                           "evening": "23:30"})
        text = "\n".join(notes)
        self.assertIn("schedule.morning: not a usable schedule ({'time': '6am'}); nothing to move", text)
        self.assertIn("schedule.weekly: not a usable schedule ({'weekday': 9})", text)
        self.assertIn("schedule.lunch: no practice has this id; nothing to move", text)
        self.assertIn("schedule.evening: not a usable schedule ('23:30')", text)
        self.assertFalse(self.practices.exists())

    def test_a_schedule_that_is_not_an_object_is_named(self):
        _new, notes, _dest = self.migrate(["x"])
        self.assertIn("schedule is not an object (['x']); nothing to move", notes)

    def test_the_overlay_may_carry_schedule_too_and_wins_per_entry(self):
        _new, notes, _dest = self.migrate({"evening": {"time": "22:00"}}, overlay={"schedule": {"evening": {"time": "21:00"}}})
        self.assertEqual(library.describe_schedule(self.sent("evening").schedule), "daily 21:00")

    def test_a_config_already_in_the_new_shape_is_handled_the_same(self):
        new, notes, dest = self.migrate({"evening": {"time": "22:00"}}, telegram={"chat_id": "7"})
        self.assertEqual(library.describe_schedule(self.sent("evening").schedule), "daily 22:00")
        self.assertEqual(json.loads(dest.read_text())["telegram"], {"chat_id": "7"})
        self.assertIn("input is already in the new format; copied as is", notes)

    def test_a_second_run_finds_the_fork_his_and_leaves_it(self):
        self.migrate({"evening": {"time": "22:00"}})
        before = (self.practices / "evening.md").read_text(encoding="utf-8")
        src = write_config(self.t / "old2.json", {**self.base, "schedule": {"evening": {"time": "22:00"}}})
        _new, notes = migrate.migrate_config(src, None, practices_dir=self.practices)
        self.assertTrue(any("already says daily 22:00; nothing to move" in n for n in notes), notes)
        self.assertEqual((self.practices / "evening.md").read_text(encoding="utf-8"), before)

    def test_an_unwritable_practices_directory_stops_with_what_was_done(self):
        blocker = self.t / "practices"
        blocker.write_text("a file where the directory should be", encoding="utf-8")
        with self.assertRaises(OSError):
            self.migrate({"evening": {"time": "22:00"}})
        self.assertFalse((self.t / "new.json").exists())  # the config is written last

    def test_no_schedule_no_notes_about_it(self):
        _new, notes, _dest = self.migrate({})
        self.assertEqual([n for n in notes if "schedule" in n], [])


class MigrateStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.old = t / "iris-journal"
        (self.old / "packs").mkdir(parents=True)
        (self.old / "ledger.json").write_text(json.dumps({"version": 1, "prompts": [
            {"id": "2026-10-14-morning", "captures": [{"at": "x"}, {"at": "y"}]}]}))
        (self.old / "notebook.json").write_text(json.dumps({"version": 1, "items": [{"id": "a1"}]}))
        (self.old / "notebook-archive.jsonl").write_text('{"id": "z"}\n')
        (self.old / "packs" / "2026-10-14-morning.md").write_text("# Context pack")
        for name in ("ledger.lock", "tick.lock", "notebook.lock"):
            (self.old / name).write_text("")
        (self.old / ".ledger-abc123").write_text("temp")
        self.new = t / "plugin-data" / "journal"

    def tearDown(self):
        self.tmp.cleanup()

    def snapshot(self, d: Path) -> dict:
        return {str(p.relative_to(d)): (digest(p), p.stat().st_mtime_ns) for p in d.rglob("*") if p.is_file()}

    def test_copies_everything_but_locks_and_leaves_source_alone(self):
        before = self.snapshot(self.old)
        lines = migrate.migrate_state(self.old, self.new)
        self.assertEqual(self.snapshot(self.old), before)
        got = sorted(str(p.relative_to(self.new)) for p in self.new.rglob("*") if p.is_file())
        self.assertEqual(got, ["ledger.json", "notebook-archive.jsonl", "notebook.json", "packs/2026-10-14-morning.md"])
        self.assertIn("ledger: 1 prompt(s), 2 capture(s)", lines[-1])

    def test_refuses_to_clobber_then_backs_up_with_force(self):
        migrate.migrate_state(self.old, self.new)
        (self.new / "ledger.json").write_text('{"prompts": []}')
        with self.assertRaises(FileExistsError):
            migrate.migrate_state(self.old, self.new)
        lines = migrate.migrate_state(self.old, self.new, force=True)
        self.assertTrue(any(".bak-" in l for l in lines))
        self.assertEqual(json.loads((self.new / "ledger.json").read_text())["prompts"][0]["id"], "2026-10-14-morning")
        self.assertEqual(len(list(self.new.glob("ledger.json.bak-*"))), 1)

    def test_any_differing_file_needs_force(self):
        migrate.migrate_state(self.old, self.new)
        (self.new / "ledger.json").unlink()
        (self.new / "notebook.json").unlink()
        (self.new / "notebook-archive.jsonl").write_text('{"id": "newer"}\n')
        with self.assertRaises(FileExistsError) as e:
            migrate.migrate_state(self.old, self.new)
        self.assertIn("notebook-archive.jsonl", str(e.exception))
        self.assertEqual((self.new / "notebook-archive.jsonl").read_text(), '{"id": "newer"}\n')
        migrate.migrate_state(self.old, self.new, force=True)
        self.assertEqual(len(list(self.new.glob("notebook-archive.jsonl.bak-*"))), 1)

    def test_directory_collision_refused_even_with_force(self):
        (self.new / "notebook-archive.jsonl").mkdir(parents=True)
        for force in (False, True):
            with self.assertRaises(FileExistsError) as e:
                migrate.migrate_state(self.old, self.new, force=force)
            self.assertIn("non-file", str(e.exception))
        self.assertFalse((self.new / "ledger.json").exists())  # nothing copied before refusing

    def test_file_where_a_directory_is_needed_refused_before_copying(self):
        self.new.mkdir(parents=True)
        (self.new / "packs").write_text("not a directory")
        with self.assertRaises(FileExistsError) as e:
            migrate.migrate_state(self.old, self.new, force=True)
        self.assertIn("packs/2026-10-14-morning.md", str(e.exception))
        self.assertFalse((self.new / "ledger.json").exists())

    def test_waits_for_a_running_tick(self):
        held = threading.Event()
        released = []

        def hold():
            with open(self.old / "tick.lock", "a+") as fh:
                fcntl.flock(fh, fcntl.LOCK_EX)
                held.set()
                time.sleep(0.3)
                released.append(time.monotonic())
        th = threading.Thread(target=hold)
        th.start()
        held.wait()
        notes = []
        migrate.migrate_state(self.old, self.new, note=notes.append)
        done = time.monotonic()
        th.join()
        self.assertTrue(notes and "waiting" in notes[0])
        self.assertGreaterEqual(done, released[0])

    def test_same_dir_and_missing_source_refused(self):
        with self.assertRaises(ValueError):
            migrate.migrate_state(self.old, self.old)
        with self.assertRaises(FileNotFoundError):
            migrate.migrate_state(self.old / "nope", self.new)


if __name__ == "__main__":
    unittest.main()
