"""Settings from every face: ``journal config``, ``hermes journal config``, and the serve unit that lets the web app
save (``settings.apply`` is the one function; the web side of the same table is in ``test_serve_writes``)."""
import argparse
import contextlib
import errno
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import CleanEnv, write_config

from journal import cli, config as jconfig, settings
from journal.hermes import runtime
from journal.serve import unit
from test_hermes import FakeCtx, load_wrapper


def run_cli(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(list(argv))
        except SystemExit as e:  # argparse
            code = e.code
    return code, out.getvalue(), err.getvalue()


class FileCase(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        (t / "Journal").mkdir()
        self.file = write_config(t / "conf" / "c.json", {"root": str(t / "Journal"), "state_dir": str(t / "state"),
                                                         "timezone": "Asia/Kolkata", "windows": {"grace_hours": 3}})

    def journal(self, *argv):
        return run_cli("--config", str(self.file), *argv)

    def data(self):
        return json.loads(self.file.read_text(encoding="utf-8"))


class ConfigCommandTest(FileCase):
    def test_set_writes_the_config_file_and_get_reads_it_back(self):
        code, out, err = self.journal("config", "set", "windows.collapse_hours", "5")
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("windows.collapse_hours = 5  (written to the config file)", out)
        self.assertEqual(self.data()["windows"], {"grace_hours": 3, "collapse_hours": 5})   # the sibling is kept
        self.assertEqual(self.journal("config", "get", "windows.collapse_hours")[1], "5\n")
        self.assertEqual(self.journal("config", "get", "windows.grace_hours")[1], "3\n")
        self.assertEqual(self.journal("config", "get", "windows.opener_budget_seconds")[1], "240\n")   # the default
        group = json.loads(self.journal("config", "get", "windows")[1])
        self.assertEqual((group["collapse_hours"], group["grace_hours"]), (5, 3))

    def test_a_group_read_shows_only_what_can_be_set(self):
        """`config get conversations` is not a way to read the keys `config get conversations.enabled` refuses."""
        got = json.loads(self.journal("config", "get", "conversations")[1])
        self.assertEqual(list(got), ["quiet_minutes"])
        self.assertEqual(sorted(json.loads(self.journal("config", "get", "windows")[1])),
                         ["annotate_after_minutes", "collapse_hours", "grace_hours", "opener_budget_seconds"])
        self.assertEqual(sorted(json.loads(self.journal("config", "get", "reflections")[1])), ["day", "week"])
        self.assertNotIn("opt_out_prefix", self.journal("config", "get", "conversations")[1])
        self.assertEqual(self.journal("config", "get", "conversations.enabled")[0], 2)

    def test_values_are_json_or_plain_text(self):
        for key, text in (("timezone", "Europe/London"), ("today.enabled", "false"), ("today.max_chars", "900"),
                          ("reflections.week.fallback_time", "05:30"), ("conversations.quiet_minutes", "12.5"),
                          ("coordinates", '{"lat": 38.7, "lon": -9.1}')):
            with self.subTest(key):
                self.assertEqual(self.journal("config", "set", key, text)[0], 0)
        d = self.data()
        self.assertEqual((d["timezone"], d["today"], d["reflections"], d["conversations"], d["coordinates"]),
                         ("Europe/London", {"enabled": False, "max_chars": 900},
                          {"week": {"fallback_time": "05:30"}}, {"quiet_minutes": 12.5}, {"lat": 38.7, "lon": -9.1}))
        self.assertFalse(jconfig.load(self.file).today["enabled"])

    def test_the_file_is_replaced_whole_and_nothing_is_left_beside_it(self):
        before = sorted(p.name for p in self.file.parent.iterdir())
        self.journal("config", "set", "windows.collapse_hours", "5")
        self.assertEqual(sorted(p.name for p in self.file.parent.iterdir()), before)
        self.assertEqual(self.data()["root"], str(Path(self.tmp.name) / "Journal"))

    def test_a_key_off_the_allow_list_is_refused_and_nothing_is_written(self):
        before = self.file.read_bytes()
        for key in ("root", "state_dir", "practices_dir", "telegram.chat_id", "telegram.token_command",
                    "serve.allow_write", "guard.enabled", "hermes.command", "agent.command", "windows.nope",
                    "conversations.opt_out_prefix", "nonsense"):
            with self.subTest(key):
                code, out, err = self.journal("config", "set", key, "x")
                self.assertEqual((code, out), (2, ""))
                self.assertIn("not a setting", err)
                self.assertEqual(self.journal("config", "get", key)[0], 2)   # not readable either: no secret leaks
        self.assertEqual(self.file.read_bytes(), before)

    def test_a_bad_value_names_the_problem_and_nothing_is_written(self):
        before = self.file.read_bytes()
        for key, text in (("windows.collapse_hours", "-2"), ("windows.grace_hours", "0.1"), ("windows.collapse_hours", "73"),
                          ("windows.opener_budget_seconds", "1.5"), ("timezone", "Not/AZone"), ("today.enabled", "yes"),
                          ("reflections.day.fallback_time", "25:00"), ("today.max_chars", "10"),
                          ("coordinates", '{"lat": 91, "lon": 0}'), ("windows", "5")):
            with self.subTest(key, text=text):
                code, out, err = self.journal("config", "set", key, text)
                self.assertEqual((code, out), (2, ""))
                self.assertIn("nothing was saved", err)
        self.assertEqual(self.file.read_bytes(), before)

    def test_list_shows_value_layer_and_whether_it_can_be_changed(self):
        code, out, _ = self.journal("config", "list")
        self.assertEqual(code, 0)
        self.assertIn("windows.grace_hours", out)
        line = next(l for l in out.splitlines() if l.startswith("windows.grace_hours"))
        self.assertIn("3  (file)", line)
        self.assertIn("(default)", next(l for l in out.splitlines() if l.startswith("today.max_chars")))
        rows = json.loads(self.journal("config", "list", "--json")[1])
        self.assertEqual(rows["config_file"], str(self.file))
        by = {r["key"]: r for r in rows["settings"]}
        self.assertEqual([r["key"] for r in rows["settings"]], settings.leaf_keys())
        self.assertEqual((by["windows.grace_hours"]["value"], by["windows.grace_hours"]["layer"],
                          by["windows.grace_hours"]["writable"]), (3, "file", True))
        self.assertEqual(by["today.max_chars"]["layer"], "default")
        for key in ("root", "telegram.chat_id", "state_dir"):
            self.assertNotIn(key, by)

    def test_json_answers(self):
        got = json.loads(self.journal("config", "set", "windows.collapse_hours", "5", "--json")[1])
        self.assertEqual(got, {"written": {"windows.collapse_hours": "file"}})
        got = json.loads(self.journal("config", "get", "windows.grace_hours", "--json")[1])
        self.assertEqual(got, {"key": "windows.grace_hours", "value": 3, "layer": "file"})
        code, out, _ = self.journal("config", "set", "timezone", "Not/AZone", "--json")
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out)["error"], "invalid")

    def test_a_missing_action_is_a_usage_error(self):
        self.assertEqual(self.journal("config")[0], 2)


class TodayOffTest(FileCase):
    def test_today_false_in_the_file_reads_as_off_and_the_rest_as_defaults(self):
        cfg = jconfig.from_dict({**json.loads(self.file.read_text()), "today": False}, config_file=self.file)
        self.assertIs(settings.current(cfg, "today.enabled"), False)
        self.assertEqual(settings.current(cfg, "today.max_chars"), 1200)
        self.assertIs(settings.current(cfg, "today")["enabled"], False)
        d = json.loads(self.file.read_text())
        d["today"] = False
        self.file.write_text(json.dumps(d))
        code, out, _ = self.journal("config", "get", "today.enabled")
        self.assertEqual((code, out), (0, "false\n"))
        rows = {r["key"]: r["value"] for r in settings.rows(jconfig.load(self.file))}
        self.assertEqual((rows["today.enabled"], rows["today.max_chars"]), (False, 1200))


class NoFileUnderHermesTest(unittest.TestCase):
    """Hermes with no config file: a save has nowhere to go but `hermes config set`, and every face says so."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = jconfig.load(hermes_home=t / "home", settings={"root": str(t / "J"), "state_dir": str(t / "s")})

    def test_the_keys_are_hermes_owned_in_the_listing_and_in_the_web_origins(self):
        self.assertIsNone(self.cfg.config_file)
        rows = {r["key"]: r for r in settings.rows(self.cfg)}
        self.assertEqual((rows["windows.collapse_hours"]["layer"], rows["windows.collapse_hours"]["writable"]),
                         ("hermes", False))
        origins = settings.origins(self.cfg)
        self.assertEqual({origins[k] for k in jconfig.WEB_WRITABLE}, {"hermes"})

    def test_a_config_file_keeps_the_origins_as_they_are(self):
        cfg = jconfig.from_dict({"root": self.tmp.name}, config_file=Path(self.tmp.name) / "c.json")
        self.assertEqual(settings.origins(cfg), cfg.origins)
        self.assertEqual(settings.layer(cfg, "today.enabled"), "default")


class HermesOwnedTest(FileCase):
    """A key in Hermes settings is not written by any face: the command to run is printed instead."""

    def setUp(self):
        super().setUp()
        self.conf_cfg = jconfig.load(self.file, settings={"windows": {"collapse_hours": 4}, "location": "Chicago"})

    def set(self, key, text, *extra):
        out, err = io.StringIO(), io.StringIO()
        args = cli.build_parser().parse_args(["config", "set", key, text, *extra])
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.run(args, cfg_loader=lambda: self.conf_cfg)
        return code, out.getvalue(), err.getvalue()

    def test_it_says_so_and_prints_the_hermes_command(self):
        before = self.file.read_bytes()
        code, out, err = self.set("windows.collapse_hours", "5")
        self.assertEqual((code, out), (1, ""))
        self.assertIn("is set in Hermes settings", err)
        self.assertIn("run: hermes config set plugins.entries.journal.settings.windows.collapse_hours 5\n", err)
        self.assertEqual(self.file.read_bytes(), before)
        code, out, _err = self.set("windows.collapse_hours", "6", "--json")
        self.assertEqual(json.loads(out)["command"],
                         "hermes config set plugins.entries.journal.settings.windows.collapse_hours 6")
        self.assertEqual(self.set("timezone", "Europe/London")[0], 0)   # the timezone is the file's: written
        self.assertEqual(self.data()["timezone"], "Europe/London")

    def test_the_value_is_quoted_for_a_shell(self):
        code, _out, err = self.set("location", "New York, NY")
        self.assertEqual(code, 1)
        self.assertIn("settings.location 'New York, NY'\n", err)

    def test_the_listing_marks_it_and_nothing_else_is_touched(self):
        rows = {r["key"]: r for r in settings.rows(self.conf_cfg)}
        self.assertEqual((rows["windows.collapse_hours"]["layer"], rows["windows.collapse_hours"]["writable"]),
                         ("hermes", False))
        self.assertIn("hermes config set plugins.entries.journal.settings.windows.collapse_hours",
                      rows["windows.collapse_hours"]["command"])
        self.assertEqual(rows["windows.grace_hours"]["layer"], "hermes")   # the whole `windows` group is Hermes'
        self.assertTrue(rows["conversations.quiet_minutes"]["writable"])

    def test_a_place_is_changed_in_all_three_keys_not_the_label_alone(self):
        """Hermes owns `location`; the lookup would set it with the timezone and coordinates, so the advice names all."""
        code, _out, err = self.set("location", "Lisbon")
        self.assertEqual(code, 1)
        self.assertIn("run: hermes config set plugins.entries.journal.settings.location Lisbon\n", err)
        self.assertIn("settings.timezone <timezone of the place>\n", err)
        self.assertIn("settings.coordinates ", err)
        code, out, _err = self.set("location", "Lisbon", "--json")
        got = json.loads(out)
        self.assertEqual(len(got["commands"]), 3)
        self.assertEqual(got["command"], "\n".join(got["commands"]))

    def test_a_place_is_advised_in_full_when_only_the_timezone_is_hermes_owned(self):
        cfg = jconfig.load(self.file, settings={"timezone": "Asia/Kolkata"})   # location is the file's, timezone Hermes'
        self.assertEqual(cfg.origins.get("timezone"), "hermes")
        with self.assertRaises(settings.SettingsError) as raised:
            settings.apply(cfg, {"location": "Lisbon"})
        self.assertEqual([c.split("settings.")[1].split(" ")[0] for c in raised.exception.commands],
                         ["location", "timezone", "coordinates"])

    def test_a_named_profile_is_in_every_line(self):
        """Pasted without -p the line would change the shell's default profile."""
        home = Path(self.tmp.name) / ".hermes" / "profiles" / "iris"
        cfg = jconfig.load(self.file, hermes_home=home, settings={"windows": {"collapse_hours": 4}})
        with self.assertRaises(settings.SettingsError) as raised:
            settings.apply(cfg, {"windows": {"collapse_hours": 5, "grace_hours": 1}})
        self.assertEqual(len(raised.exception.commands), 2)
        for line in raised.exception.commands:
            self.assertTrue(line.startswith("hermes -p iris config set plugins.entries.journal.settings.windows."), line)
        self.assertIn("hermes -p iris config set", str(raised.exception.message))
        rows = {r["key"]: r for r in settings.rows(cfg)}
        self.assertTrue(rows["windows.grace_hours"]["command"].startswith("hermes -p iris config set"))

    def test_a_group_is_advised_one_line_per_key(self):
        code, _out, err = self.set("windows", '{"collapse_hours": 5, "grace_hours": 1}')
        self.assertEqual(code, 1)
        self.assertIn("run: hermes config set plugins.entries.journal.settings.windows.collapse_hours 5\n", err)
        self.assertIn("run: hermes config set plugins.entries.journal.settings.windows.grace_hours 1\n", err)

    def test_every_hermes_owned_part_of_one_change_is_named(self):
        cfg = jconfig.load(self.file, settings={"windows": {"collapse_hours": 4}, "today": {"max_chars": 900}})
        with self.assertRaises(settings.SettingsError) as raised:
            settings.apply(cfg, {"windows.collapse_hours": 5, "today.max_chars": 800, "conversations.quiet_minutes": 9})
        self.assertEqual([c.split("plugins.entries.journal.")[1].split(" ") for c in raised.exception.commands],
                         [["settings.windows.collapse_hours", "5"], ["settings.today.max_chars", "800"]])

    def test_an_env_owned_key_is_refused(self):
        cfg = jconfig.load(self.file)
        cfg.origins["today"] = "env"
        with self.assertRaises(settings.SettingsError) as raised:
            settings.apply(cfg, {"today.enabled": False})
        self.assertEqual(raised.exception.code, "not-writable")
        self.assertIn("environment", raised.exception.message)
        self.assertIsNone(raised.exception.command)

    def test_a_standalone_install_with_no_file_makes_one_under_xdg(self):
        t = Path(self.tmp.name)
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(t / "xdg"), "JOURNAL_STATE_DIR": str(t / "s")}):
            cfg = jconfig.load(settings={"root": str(t / "Journal")})   # no home, no file: layer "hermes" is the dict
            cfg.origins = {}
            settings.apply(cfg, {"windows.collapse_hours": 5})
            self.assertEqual(json.loads((t / "xdg" / "journal" / "config.json").read_text())["windows"],
                             {"collapse_hours": 5})

    def test_under_hermes_with_no_config_file_the_hermes_command_is_the_way(self):
        cfg = jconfig.load(hermes_home=Path(self.tmp.name) / "home", settings={"root": str(Path(self.tmp.name) / "J")})
        self.assertIsNone(cfg.config_file)
        with self.assertRaises(settings.SettingsError) as raised:
            settings.apply(cfg, {"windows.collapse_hours": 5})
        self.assertEqual(raised.exception.command,
                         "hermes config set plugins.entries.journal.settings.windows.collapse_hours 5")


class UnwritableFileTest(FileCase):
    def test_the_listing_does_not_promise_a_write_the_directory_would_refuse(self):
        with mock.patch("journal.settings.os.access", return_value=False):
            rows = {r["key"]: r for r in settings.rows(jconfig.load(self.file))}
        for key in ("location", "timezone", "coordinates", "windows.collapse_hours", "today.enabled"):
            self.assertFalse(rows[key]["writable"], key)
            self.assertIn("cannot write in", rows[key]["why"])
        self.assertTrue(all(r["writable"] for r in settings.rows(jconfig.load(self.file))))


    def test_a_directory_the_user_cannot_write_is_reported_with_the_fix(self):
        before = self.file.read_bytes()
        with mock.patch("journal.settings.os.access", return_value=False):
            code, out, err = self.journal("config", "set", "windows.collapse_hours", "5")
        self.assertEqual((code, out), (1, ""))
        self.assertIn(f"cannot write in {self.file.parent}", err)
        self.assertIn("writable for the user the journal runs as", err)
        self.assertNotIn("ReadWritePaths", err)   # the unit has no sandbox to widen (SERVE-UNSANDBOXED)
        self.assertEqual(self.file.read_bytes(), before)

    def test_a_write_that_fails_anyway_is_said_and_the_file_is_as_it_was(self):
        before = self.file.read_bytes()
        with mock.patch("journal.config.write_json_file", side_effect=OSError(errno.EROFS, "Read-only file system")):
            code, _out, err = self.journal("config", "set", "windows.collapse_hours", "5")
        self.assertEqual(code, 1)
        self.assertIn("Read-only file system", err)
        self.assertEqual(self.file.read_bytes(), before)

    def test_a_place_is_not_looked_up_when_the_file_cannot_take_it(self):
        with mock.patch("journal.settings.os.access", return_value=False), \
                mock.patch("journal.location.geocode") as geocode:
            code, _out, _err = self.journal("config", "set", "location", "Lisbon")
        self.assertEqual(code, 1)
        geocode.assert_not_called()


class PlaceTest(FileCase):
    def test_a_place_is_looked_up_and_written_with_its_timezone_and_coordinates(self):
        hit = {"name": "Lisbon", "admin1": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1,
               "timezone": "Europe/Lisbon"}
        with mock.patch("journal.location.geocode", return_value=hit):
            code, out, err = self.journal("config", "set", "location", "Lisbon")
        self.assertEqual((code, err), (0, ""), out)
        d = self.data()
        self.assertEqual((d["location"], d["timezone"], d["coordinates"]["lat"]),
                         ("Lisbon, Lisbon, Portugal", "Europe/Lisbon", 38.7))
        self.assertIn("location = Lisbon, Lisbon, Portugal", out)
        with mock.patch("journal.location.geocode", side_effect=ValueError("couldn't find a place called 'Zzz'")):
            self.assertEqual(self.journal("config", "set", "location", "Zzz")[0], 2)
        with mock.patch("journal.location.geocode", side_effect=OSError("curl")):
            self.assertEqual(self.journal("config", "set", "location", "Faro")[0], 1)
        self.assertEqual(self.data()["timezone"], "Europe/Lisbon")



NOMINATIM = {"name": "Ernakulam", "lat": "9.9674277", "lon": "76.2454436",
             "address": {"city": "Kochi", "state_district": "Ernakulam", "state": "Kerala", "country": "India"}}
OPEN_METEO = {"latitude": 9.98, "longitude": 76.28, "timezone": "Asia/Kolkata"}


def answers(*replies):
    """Stand-ins for the two lookups ``reverse_geocode`` makes (Nominatim, then Open-Meteo), recording the URLs."""
    asked = []

    def fetch(url, headers=()):
        asked.append((url, headers))
        reply = replies[len(asked) - 1]
        if isinstance(reply, BaseException):
            raise reply
        return reply
    return fetch, asked


class NearTest(FileCase):
    """"Use my location": a device position becomes a place and a time zone, saved the way a typed place is."""

    def apply(self, lat, lon, fetch):
        cfg = jconfig.load(self.file)
        with mock.patch("journal.location._fetch_quietly", side_effect=fetch):
            return cfg, settings.apply_near(cfg, lat, lon, reload=lambda: jconfig.load(self.file))

    def test_a_position_is_named_and_the_place_is_saved_never_the_position(self):
        fetch, asked = answers(NOMINATIM, OPEN_METEO)
        cfg, done = self.apply(9.981234, 76.283456, fetch)
        self.assertEqual(done["written"], {"location": "file", "timezone": "file", "coordinates": "file"})
        self.assertEqual((done["location"]["location"], done["location"]["timezone"]), ("Kochi, Kerala, India", "Asia/Kolkata"))
        d = self.data()
        self.assertEqual((d["location"], d["timezone"]), ("Kochi, Kerala, India", "Asia/Kolkata"))
        # what is kept is the matched place's own centre, as for a typed place: not the device, not even cut
        self.assertEqual(d["coordinates"], {"lat": 9.9674, "lon": 76.2454, "place": "Kochi, Kerala, India",
                                            "source": "openstreetmap"})   # the name is OSM's: the app credits it
        # the position left the host once, cut, to name the place; the time zone was asked for the place
        self.assertEqual(len(asked), 2)
        self.assertTrue(asked[0][0].startswith("https://nominatim.openstreetmap.org/reverse?"))
        self.assertIn("lat=9.98&lon=76.28", asked[0][0])
        self.assertTrue(any(h.startswith("User-Agent: journal-engine") for h in asked[0][1]))
        self.assertTrue(asked[1][0].startswith("https://api.open-meteo.com/v1/forecast?latitude=9.9674&longitude=76.2454"))
        self.assertIn("timezone=auto", asked[1][0])
        for url, _h in asked:
            self.assertNotIn("9.981", url)
            self.assertNotIn("76.283", url)
        # nothing the host keeps holds the position: not the config, not the history
        line = (cfg.state_dir / "location-history.jsonl").read_text(encoding="utf-8").splitlines()[-1]
        self.assertEqual(json.loads(line)["query"], "device position")
        for text in (line, self.file.read_text(encoding="utf-8")):
            self.assertNotIn("9.98", text)
            self.assertNotIn("76.28", text)

    def test_the_credit_goes_with_the_place_osm_named_and_a_typed_place_clears_it(self):
        from journal import location
        fetch, _ = answers(NOMINATIM, OPEN_METEO)
        self.apply(9.9, 76.2, fetch)
        cfg = jconfig.load(self.file)
        self.assertEqual(location.place_source(cfg), "openstreetmap")
        hit = {"name": "Lisbon", "admin1": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1,
               "timezone": "Europe/Lisbon"}
        with mock.patch("journal.location.geocode", return_value=hit):
            settings.apply(cfg, {"location": "Lisbon"})
        cfg = jconfig.load(self.file)
        self.assertNotIn("source", self.data()["coordinates"])
        self.assertIsNone(location.place_source(cfg))
        # a hand edit of the place alone does not inherit the credit
        data = self.data()
        data["coordinates"]["source"] = "openstreetmap"
        data["location"] = "Somewhere else"
        self.file.write_text(json.dumps(data), encoding="utf-8")
        self.assertIsNone(location.place_source(jconfig.load(self.file)))

    def test_the_services_can_be_configured_and_a_bad_address_is_not_used(self):
        data = self.data()
        data["lookup"] = {"reverse_url": "https://nominatim.example.org/reverse", "timezone_url": "http://tz.local/v1/forecast"}
        self.file.write_text(json.dumps(data), encoding="utf-8")
        fetch, asked = answers(NOMINATIM, OPEN_METEO)
        self.apply(9.9, 76.2, fetch)
        self.assertTrue(asked[0][0].startswith("https://nominatim.example.org/reverse?format=jsonv2"))
        self.assertTrue(asked[1][0].startswith("http://tz.local/v1/forecast?latitude="))
        data["lookup"] = {"reverse_url": 'https://x.org/r"\noutput = /tmp/x', "timezone_url": 5}
        self.file.write_text(json.dumps(data), encoding="utf-8")
        fetch, asked = answers(NOMINATIM, OPEN_METEO)
        self.apply(9.9, 76.2, fetch)
        self.assertTrue(asked[0][0].startswith("https://nominatim.openstreetmap.org/reverse?"))
        self.assertTrue(asked[1][0].startswith("https://api.open-meteo.com/v1/forecast?"))

    def test_an_unreachable_service_is_unavailable_not_no_such_place(self):
        # what curl does when the network is down or the service does not answer in time: exit 28, nothing on stdout
        failed = subprocess.CompletedProcess(["curl"], 28, stdout="", stderr="")
        cfg = jconfig.load(self.file)
        with mock.patch("journal.location.subprocess.run", return_value=failed):
            with self.assertRaises(settings.SettingsError) as raised:
                settings.apply_near(cfg, 9.9, 76.2)
            self.assertEqual(raised.exception.code, settings.UNAVAILABLE)
            with self.assertRaises(settings.SettingsError) as raised:   # a typed place: the same, on main's path too
                settings.apply(cfg, {"location": "Lisbon"})
            self.assertEqual(raised.exception.code, settings.UNAVAILABLE)
        self.assertNotIn("Lisbon", self.file.read_text(encoding="utf-8"))

    def test_the_place_is_the_most_specific_name_the_address_has(self):
        fetch, _ = answers({"lat": "9.87", "lon": "76.27", "address": {"village": "Kumbalangi", "county": "Kochi",
                                                                          "state": "Kerala", "country": "India"}},
                           OPEN_METEO)
        _cfg, done = self.apply(9.9, 76.2, fetch)
        self.assertEqual(done["location"]["location"], "Kumbalangi, Kerala, India")

    def test_a_bad_position_is_refused_before_anything_is_asked(self):
        before = self.file.read_bytes()
        for lat, lon in ((91, 0), (0, 181), ("9.9", 76.2), (None, None), (True, 1), (float("nan"), 1)):
            with self.subTest(lat=lat, lon=lon):
                fetch, asked = answers()
                with self.assertRaises(settings.SettingsError) as raised:
                    self.apply(lat, lon, fetch)
                self.assertEqual(raised.exception.code, settings.INVALID)
                self.assertEqual(asked, [])
        self.assertEqual(self.file.read_bytes(), before)

    def test_nowhere_named_or_no_time_zone_is_invalid_and_nothing_is_written(self):
        before = self.file.read_bytes()
        no_centre = {k: v for k, v in NOMINATIM.items() if k not in ("lat", "lon")}
        for replies in (({"error": "Unable to geocode"},), (no_centre,), (NOMINATIM, {"error": True, "reason": "x"}),
                        (NOMINATIM, {"timezone": "Not/AZone"})):
            with self.subTest(replies):
                fetch, _ = answers(*replies)
                with self.assertRaises(settings.SettingsError) as raised:
                    self.apply(9.9, 76.2, fetch)
                self.assertEqual(raised.exception.code, settings.INVALID)
                self.assertNotIn("9.9", raised.exception.message)
        self.assertEqual(self.file.read_bytes(), before)

    def test_an_unreachable_lookup_is_unavailable(self):
        fetch, _ = answers(OSError("curl"))
        with self.assertRaises(settings.SettingsError) as raised:
            self.apply(9.9, 76.2, fetch)
        self.assertEqual(raised.exception.code, settings.UNAVAILABLE)

    def test_a_place_hermes_owns_is_refused_before_the_position_leaves_the_host(self):
        cfg = jconfig.load(self.file)
        cfg.origins["timezone"] = "hermes"
        cfg.hermes_home = Path(self.tmp.name) / "hermes"
        with mock.patch("journal.location._fetch_quietly") as fetch, self.assertRaises(settings.SettingsError) as raised:
            settings.apply_near(cfg, 9.9, 76.2)
        self.assertEqual(raised.exception.code, settings.NOT_WRITABLE)
        fetch.assert_not_called()

    def test_a_service_answering_an_http_error_is_unavailable_not_no_such_place(self):
        # a rate limit or an outage answered in JSON: curl exits 0 and prints the body, then the status (-w)
        cfg = jconfig.load(self.file)
        for code, body in ((429, '{"error": "Too many requests"}'), (503, '{"error": true, "reason": "busy"}'),
                           (404, '{"error": "Unable to geocode"}')):
            answered = subprocess.CompletedProcess(["curl"], 0, stdout=f"{body}\n{code}", stderr="")
            with self.subTest(code), mock.patch("journal.location.subprocess.run", return_value=answered):
                with self.assertRaises(settings.SettingsError) as raised:
                    settings.apply_near(cfg, 9.9, 76.2)
                self.assertEqual(raised.exception.code, settings.UNAVAILABLE)
                with self.assertRaises(settings.SettingsError) as raised:   # a typed place's lookup too
                    settings.apply(cfg, {"location": "Lisbon"})
                self.assertEqual(raised.exception.code, settings.UNAVAILABLE)
        self.assertNotIn("Lisbon", self.file.read_text(encoding="utf-8"))

    def test_curl_is_given_the_address_on_stdin_not_on_its_command_line(self):
        from journal import location
        done = subprocess.CompletedProcess(["curl"], 0, stdout='{"timezone": "Asia/Kolkata"}\n200', stderr="")
        with mock.patch("journal.location.subprocess.run", return_value=done) as run:
            got = location._fetch_quietly("https://example.org/x?lat=9.98&lon=76.28", ("User-Agent: t",))
        self.assertEqual(got, {"timezone": "Asia/Kolkata"})
        argv, kw = run.call_args.args[0], run.call_args.kwargs
        self.assertNotIn("9.98", " ".join(argv))
        self.assertEqual(argv[-2:], ["-K", "-"])
        self.assertIn('url = "https://example.org/x?lat=9.98&lon=76.28"', kw["input"])
        self.assertIn('header = "User-Agent: t"', kw["input"])

class HermesFaceTest(CleanEnv):
    """``hermes journal config …`` is the same parser and the same function."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.home = t / "home"
        (t / "Journal").mkdir()
        self.conf = write_config(t / "c.json", {"root": str(t / "Journal"), "windows": {"grace_hours": 3}})
        os.environ["JOURNAL_CONFIG"] = str(self.conf)
        os.environ["HERMES_HOME"] = str(self.home)
        self.addCleanup(runtime.unbind)   # register() binds the plugin context for the whole process

    def handler(self, ctx):
        load_wrapper().register(ctx)
        setup_fn, handle = ctx.cli["journal"]
        parser = argparse.ArgumentParser(prog="hermes journal")
        setup_fn(parser)
        return lambda *argv: self.run_handle(handle, parser, argv)

    def run_handle(self, handle, parser, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = handle(parser.parse_args(list(argv)))
        return code, out.getvalue(), err.getvalue()

    def test_set_get_list_and_the_hermes_owned_key(self):
        run = self.handler(FakeCtx({"config_file": str(self.conf), "today": {"enabled": False}}))
        code, out, err = run("config", "set", "windows.collapse_hours", "5")
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("windows.collapse_hours = 5  (written to the config file)", out)
        self.assertEqual(json.loads(self.conf.read_text())["windows"], {"grace_hours": 3, "collapse_hours": 5})
        self.assertEqual(run("config", "get", "windows.collapse_hours")[1], "5\n")
        code, out, _ = run("config", "list", "--json")
        by = {r["key"]: r for r in json.loads(out)["settings"]}
        self.assertEqual((by["today.enabled"]["layer"], by["today.enabled"]["writable"]), ("hermes", False))
        self.assertEqual((by["windows.collapse_hours"]["layer"], by["windows.collapse_hours"]["writable"]), ("file", True))
        before = self.conf.read_bytes()
        code, _out, err = run("config", "set", "today.enabled", "true")
        self.assertEqual(code, 1)
        self.assertIn("run: hermes config set plugins.entries.journal.settings.today.enabled true", err)
        code, _out, err = run("config", "set", "root", "/elsewhere")
        self.assertEqual(code, 2)
        self.assertEqual(self.conf.read_bytes(), before)

    def test_it_never_writes_hermes_settings(self):
        ctx = FakeCtx({"config_file": str(self.conf), "location": "Chicago"})
        run = self.handler(ctx)
        run("config", "set", "location", "Lisbon")
        self.assertEqual(ctx.settings["location"], "Chicago")


class UnitTest(unittest.TestCase):
    """The unit has no sandbox (DECISIONS.md, SERVE-UNSANDBOXED): a save needs only the config file's directory to be
    writable by the service user, so the config file shapes the unit only through ``--config`` and its mount."""

    def cfg(self, tmp: Path, config_file=None, **over):
        base = {"root": str(tmp / "Journal"), "state_dir": str(tmp / "state")}
        return jconfig.from_dict({**base, **over}, config_file=config_file)

    def test_the_config_file_is_passed_and_nothing_is_widened_for_a_save(self):
        cfg = self.cfg(Path("/srv/x"), config_file=Path("/home/u/.config/journal/c.json"))
        text = unit.render(cfg, exe="/bin/journal", mount_of=lambda p: Path("/"))
        self.assertIn("--config /home/u/.config/journal/c.json", text)
        for absent in ("ReadWritePaths", "ProtectHome", "ProtectSystem", "PrivateTmp", "# The config file"):
            self.assertNotIn(absent, text)

    def test_a_config_file_on_its_own_mount_is_required_to_be_there(self):
        tmp = Path("/srv/x")
        cfg = self.cfg(tmp, config_file=Path("/etc/.config/journal/c.json"))
        mounts = {"/etc/.config/journal": Path("/etc/.config/journal")}
        text = unit.render(cfg, exe="/bin/journal", mount_of=lambda p: mounts.get(str(p), Path("/")))
        self.assertIn("RequiresMountsFor=/etc/.config/journal\n", text)
        self.assertIn("ExecStartPre=/usr/bin/mountpoint -q /etc/.config/journal\n", text)

    def test_a_dotdot_in_the_config_path_is_normalised_before_its_mount_is_found(self):
        cfg = self.cfg(Path("/srv/x"), config_file=Path("/etc/.config/cfg/../journal/c.json"))
        mounts = {"/etc/.config/journal": Path("/etc/.config/journal")}
        text = unit.render(cfg, exe="/bin/journal", mount_of=lambda p: mounts.get(str(p), Path("/")))
        self.assertIn("RequiresMountsFor=/etc/.config/journal\n", text)

    def test_no_config_file_means_no_extra_mount(self):
        text = unit.render(self.cfg(Path("/srv/x")), exe="/bin/journal", mount_of=lambda p: Path("/"))
        self.assertNotIn("RequiresMountsFor", text)
        self.assertNotIn("--config", text)

    def test_print_unit_from_the_command_line(self):
        with tempfile.TemporaryDirectory() as t:
            conf = write_config(Path(t) / "conf" / "c.json", {"root": str(Path(t) / "J"), "state_dir": str(Path(t) / "s")})
            code, out, _ = run_cli("--config", str(conf), "serve", "--print-unit")
        self.assertEqual(code, 0)
        self.assertIn(f"--config {conf} serve\n", out)
        self.assertNotIn("ReadWritePaths", out)


if __name__ == "__main__":
    unittest.main()
