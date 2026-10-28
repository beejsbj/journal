"""The API's answers, pinned as JSON files the web app's tests read (``web/tests``): one shared description of what
the server says, so the Python and TypeScript suites cannot drift apart.

Each file is one GET against the temporary journal of ``serve_helpers`` (a fixed clock, neutral names). Paths and
mtimes, the only things that change from run to run, are normalised. When the API changes on purpose, regenerate
with ``JOURNAL_UPDATE_FIXTURES=1 python -m pytest tests/test_web_fixtures.py`` and commit the diff: the web app's
runtime shape checks (``web/src/api.ts``) and its tests then show what the client has to follow.
"""
import json
import os
import re
from pathlib import Path

from helpers import ROOT
from serve_helpers import EVENING_NOW, ServeCase, add_day_of_prompts, add_edited_builtin
from test_serve_programs import ProgramCase
from test_serve_writes import WriteCase

PACKAGE = str(ROOT / "src" / "journal")   # where the built-in practices live: the checkout, wherever it is
DIR = ROOT / "tests" / "fixtures" / "api"
ROUTES = {
    "today": "/api/v1/today",
    "entries": "/api/v1/entries?limit=50",
    "days": "/api/v1/days?from=2026-10-01&to=2026-10-15",
    "entry-evening": "/api/v1/entries/2026-10-14-evening",
    "entry-session": "/api/v1/entries/2026-10-15-fix-life-s01",
    "entry-note": "/api/v1/entries/2026-10-15-j",
    "reflections": "/api/v1/reflections",
    "insights-weeks": "/api/v1/insights/weeks?from=2026-10-01&to=2026-10-15",
    "library": "/api/v1/library",
    "library-gratitude": "/api/v1/library/gratitude",
    "settings": "/api/v1/settings",
    "session": "/api/v1/session",
}
MTIME = 1760000000.0


def normalise(value, tmp: str):
    if isinstance(value, dict):
        return {k: (MTIME if k == "mtime" and isinstance(v, float) else normalise(v, tmp)) for k, v in value.items()}
    if isinstance(value, list):
        return [normalise(v, tmp) for v in value]
    if isinstance(value, str):
        return value.replace(tmp, "/journal").replace(PACKAGE, "/package")
    return value


class ApiFixturesTest(ServeCase):
    def test_every_fixture_is_what_the_server_says(self):
        tmp = str(Path(self.tmp.name).resolve())
        # What a configured install says: the settings the Settings view draws, with an origin per top-level key.
        self.cfg.raw.update({
            "location": "Hillside", "windows": {"collapse_hours": 6, "grace_hours": 2},
            "reflections": {"day": {"enabled": True}, "week": {"enabled": True}},
            "conversations": {"quiet_minutes": 45}, "programs": {"settle_minutes": 2},
            "today": {"enabled": True, "max_chars": 1200, "cache_minutes": 30}, "serve": {"port": 8790,
                                                                                         "token_command": "set"}})
        self.cfg.origins.update({"location": "file", "windows": "file", "reflections": "file", "conversations": "hermes",
                                 "programs": "default", "today": "file", "serve": "file"})
        update = os.environ.get("JOURNAL_UPDATE_FIXTURES") == "1"
        DIR.mkdir(parents=True, exist_ok=True)
        for name, path in ROUTES.items():
            got = normalise(self.get_json(path), tmp)
            file = DIR / f"{name}.json"
            text = json.dumps(got, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
            if update:
                file.write_text(text, encoding="utf-8")
            self.assertTrue(file.is_file(), f"{file.name}: regenerate with JOURNAL_UPDATE_FIXTURES=1")
            self.assertEqual(json.loads(file.read_text(encoding="utf-8")), got, f"{file.name} is out of date")

    def test_the_fixtures_hold_no_temporary_path(self):
        for file in DIR.glob("*.json"):
            self.assertNotIn("/tmp/", file.read_text(encoding="utf-8"), file.name)



class TodayPromptsFixtureTest(ServeCase):
    """Today in the evening, with a prompt in every state (``serve_helpers.add_day_of_prompts``): the cards that show
    each prompt in full."""

    def test_the_today_prompts_fixture_is_what_the_server_says(self):
        tmp = str(Path(self.tmp.name).resolve())
        add_day_of_prompts(self.cfg)
        self.cfg.location = "Kochi, Kerala, India"
        self.app.clock = lambda: EVENING_NOW
        got = normalise(self.get_json("/api/v1/today"), tmp)
        file = DIR / "today-prompts.json"
        if os.environ.get("JOURNAL_UPDATE_FIXTURES") == "1":
            file.write_text(json.dumps(got, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self.assertTrue(file.is_file(), f"{file.name}: regenerate with JOURNAL_UPDATE_FIXTURES=1")
        self.assertEqual(json.loads(file.read_text(encoding="utf-8")), got, f"{file.name} is out of date")

class RunFixturesTest(ProgramCase):
    """The run screen's answers: a fix-life run at its first phase (``GET /runs``, ``GET /runs/{id}``)."""

    def test_the_run_fixtures_are_what_the_server_says(self):
        tmp = str(Path(self.tmp.name).resolve())
        update = os.environ.get("JOURNAL_UPDATE_FIXTURES") == "1"
        for name, path in {"runs": "/api/v1/runs", "run-one": f"/api/v1/runs/{self.rid}"}.items():
            text = json.dumps(normalise(self.get_json(path), tmp)).replace(self.rid, "2026-10-15-run1")
            # the phase was sent by the real clock (programs.py stamps `sent_at` with `datetime.now`): its time is not pinned
            got = json.loads(re.sub(r"open since [^\"]*", "open since 09:12", text))
            file = DIR / f"{name}.json"
            text = json.dumps(got, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
            if update:
                file.write_text(text, encoding="utf-8")
            self.assertTrue(file.is_file(), f"{file.name}: regenerate with JOURNAL_UPDATE_FIXTURES=1")
            self.assertEqual(json.loads(file.read_text(encoding="utf-8")), got, f"{file.name} is out of date")


class ValidateFixtureTest(WriteCase):
    """What the editor's dry run answers (``PUT /library/{id}?validate=1``) for the neutral practice, sent as the form
    sends it: the canonical file beside the problems. The web tests read it to check the form against the server."""

    def test_the_validation_fixture_is_what_the_server_says(self):
        tmp = str(Path(self.tmp.name).resolve())
        one = self.get_json("/api/v1/library/gratitude")
        got = normalise(self.ok("PUT", "/api/v1/library/gratitude?validate=1",
                                {"definition": one["definition"], "mtime": one["mtime"]}), tmp)
        update = os.environ.get("JOURNAL_UPDATE_FIXTURES") == "1"
        file = DIR / "library-validate.json"
        text = json.dumps(got, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
        if update:
            file.write_text(text, encoding="utf-8")
        self.assertTrue(file.is_file(), f"{file.name}: regenerate with JOURNAL_UPDATE_FIXTURES=1")
        self.assertEqual(json.loads(file.read_text(encoding="utf-8")), got, f"{file.name} is out of date")
        self.assertTrue(got["valid"])


class EditedBuiltinFixturesTest(WriteCase):
    """A built-in he edited in place, whose default has changed since (``serve_helpers.add_edited_builtin``): the
    Library page (``GET /library/midweek``), the default beside his version (``GET /library/midweek/default``), and a
    built-in program as its editor reads it (``GET /library/fix-life``)."""

    def test_the_edited_builtin_fixtures_are_what_the_server_says(self):
        tmp = str(Path(self.tmp.name).resolve())
        add_edited_builtin(self.cfg)
        update = os.environ.get("JOURNAL_UPDATE_FIXTURES") == "1"
        for name, path in {"library-midweek-edited": "/api/v1/library/midweek",
                           "library-midweek-default": "/api/v1/library/midweek/default",
                           "library-fix-life": "/api/v1/library/fix-life"}.items():
            got = normalise(self.get_json(path), tmp)
            file = DIR / f"{name}.json"
            text = json.dumps(got, indent=2, ensure_ascii=False, sort_keys=False) + "\n"
            if update:
                file.write_text(text, encoding="utf-8")
            self.assertTrue(file.is_file(), f"{file.name}: regenerate with JOURNAL_UPDATE_FIXTURES=1")
            self.assertEqual(json.loads(file.read_text(encoding="utf-8")), got, f"{file.name} is out of date")
        edited = json.loads((DIR / "library-midweek-edited.json").read_text(encoding="utf-8"))
        self.assertEqual((edited["definition"]["source"], edited["override"]["default_changed"]), ("fork", True))
