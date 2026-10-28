"""Every route of ``journal serve`` with ``sqlite3.connect`` made to fail (API.md §3): none may open the chat
transcript, so each answers as it normally does, and the patched function is never even called. The journal has a
conversation-bearing entry, a reflection, a program session entry and a user practice (``serve_helpers``). A route
added to ``api.ROUTES`` without a sample here fails the test, so the walk stays complete."""
import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from serve_helpers import HERS, IST, NOW, ServeCase

from journal import conversations, ledger, privacy
from journal.serve import api, auth, writes

SAMPLES = {
    r"/healthz": [("/healthz", 200)],
    r"/api/v1/healthz": [("/api/v1/healthz", 200)],
    r"/api/v1/session": [("/api/v1/session", 200)],
    r"/api/v1/today": [("/api/v1/today", 200)],
    r"/api/v1/entries": [("/api/v1/entries", 200), ("/api/v1/entries?q=harbour", 200),
                         (f"/api/v1/entries?q={HERS}", 200), ("/api/v1/entries?program=fix-life&kind=fix-life", 200)],
    r"/api/v1/entries/(?P<stem>[^/]+)": [("/api/v1/entries/2026-10-14-evening", 200),
                                         ("/api/v1/entries/2026-10-15-fix-life-s01", 200),
                                         ("/api/v1/entries/2026-10-14", 404)],
    r"/api/v1/days": [("/api/v1/days?from=2026-10-01&to=2026-10-15", 200)],
    r"/api/v1/media/(?P<name>[^/]+)": [("/api/v1/media/2026-10-14-evening.ogg", 200),
                                       ("/api/v1/media/2026-10-14-stray.ogg", 404)],
    r"/api/v1/reflections": [("/api/v1/reflections", 200)],
    r"/api/v1/insights/weeks": [("/api/v1/insights/weeks", 200)],
    r"/api/v1/library": [("/api/v1/library", 200)],
    r"/api/v1/library/(?P<id>[^/]+)": [("/api/v1/library/gratitude", 200), ("/api/v1/library/evening", 200)],
    r"/api/v1/library/(?P<id>[^/]+)/default": [("/api/v1/library/evening/default", 200),
                                               ("/api/v1/library/fix-life/default", 200),
                                               ("/api/v1/library/gratitude/default", 404)],
    r"/api/v1/runs": [("/api/v1/runs", 200)],
    r"/api/v1/runs/(?P<id>[^/]+)": [("/api/v1/runs/2026-10-15-a1f3", 404)],
    r"/api/v1/runs/(?P<id>[^/]+)/insights": [("/api/v1/runs/2026-10-15-a1f3/insights", 404)],
    r"/api/v1/status": [("/api/v1/status", 200)],
    r"/api/v1/doctor": [("/api/v1/doctor", 200)],
    r"/api/v1/settings": [("/api/v1/settings", 200)],
}


# The routes that change something (``writes.ROUTES``): a sample each, with the status it answers. The sessions
# database must stay closed for these too: a web write is his words from a form, never read from or checked against
# the chat, and the off-the-record command is honoured on the text itself.
WRITE_SAMPLES = {
    ("POST", r"/api/v1/entries"): [({"practice": "morning", "text": "a note"}, "/api/v1/entries", 201),
                                   ({"text": "/x not saved"}, "/api/v1/entries", 422)],
    ("POST", r"/api/v1/entries/(?P<stem>[^/]+)/blocks"): [({"text": "more"}, "/api/v1/entries/2026-10-14-evening/blocks", 201)],
    ("PATCH", r"/api/v1/entries/(?P<stem>[^/]+)/blocks/(?P<key>[^/]+)"):
        [({"text": "edited", "was": "First words, about the lighthouse."}, "/api/v1/entries/2026-10-14-evening/blocks/words:0", 200)],
    ("DELETE", r"/api/v1/entries/(?P<stem>[^/]+)/blocks/(?P<key>[^/]+)"):
        [({"was": "call the harbour master"}, "/api/v1/entries/2026-10-15-j/blocks/words:0", 200)],
    ("PATCH", r"/api/v1/entries/(?P<stem>[^/]+)/meta"): [({"starred": True}, "/api/v1/entries/2026-10-14-evening/meta", 200)],
    ("POST", r"/api/v1/uploads"): [(None, "/api/v1/uploads", 400)],
    ("PUT", r"/api/v1/library/(?P<id>[^/]+)"): [({"raw": "x", "mtime": 1}, "/api/v1/library/gratitude", 422),
                                                ({"raw": "x"}, "/api/v1/library/fix-life", 422)],
    ("POST", r"/api/v1/library/(?P<id>[^/]+)/fork"): [(None, "/api/v1/library/evening/fork", 201)],
    ("DELETE", r"/api/v1/library/(?P<id>[^/]+)"): [(None, "/api/v1/library/gratitude", 204)],
    ("POST", r"/api/v1/library/(?P<id>[^/]+)/send"): [(None, "/api/v1/library/morning/send", 202)],
    ("POST", r"/api/v1/runs"): [({"program": "fix-life"}, "/api/v1/runs", 202)],
    ("POST", r"/api/v1/runs/(?P<id>[^/]+)/(?P<action>next|skip|back|pause|resume|abandon|insights)"):
        [(None, "/api/v1/runs/2026-10-15-a1f3/next", 404)],
    ("PATCH", r"/api/v1/settings"): [({"windows": {"collapse_hours": 5}}, "/api/v1/settings", 409),
                                     ({"conversations": {"quiet_minutes": 30}}, "/api/v1/settings", 200),
                                     ({"location": "Lisbon"}, "/api/v1/settings", 200)],
    ("POST", r"/api/v1/settings/location"): [({"lat": 38.7, "lon": -9.1}, "/api/v1/settings/location", 200)],
}


class PrivacyTest(ServeCase):
    def test_the_guard_is_not_vacuous(self):
        """The fixture has a real sessions database and a persisted pass, so a transcript reader would reach
        ``sqlite3.connect`` (an earlier fixture had neither, and every reader returned before connecting)."""
        self.assertTrue(self.cfg.state_db.is_file())
        self.assertTrue(conversations.enabled(self.cfg))
        calls = []

        def refuse(*a, **k):
            calls.append(a)
            raise sqlite3.OperationalError("refused")
        with mock.patch.object(sqlite3, "connect", refuse):
            privacy._CACHE.clear()
            self.assertTrue(privacy.view(self.cfg, NOW).closed)  # fails closed, having tried to open it
        self.assertTrue(calls)
        privacy._CACHE.clear()

    def test_every_route_answers_without_the_sessions_database(self):
        missing = {p for m, p, _fn, _a in api.ROUTES if m == "GET"} - set(SAMPLES)
        self.assertEqual(missing, set(), "add a sample for each new GET route")
        calls = []

        def refuse(*a, **k):
            calls.append(a)
            raise AssertionError("journal serve opened a SQLite database")

        def refuse_view(*a, **k):
            calls.append(a)
            raise AssertionError("journal serve asked the privacy pass")
        with mock.patch.object(sqlite3, "connect", refuse), mock.patch.object(privacy, "view", refuse_view):
            for pattern, samples in SAMPLES.items():
                for path, want in samples:
                    with self.subTest(path):
                        code, _h, body = self.request("GET", path)
                        self.assertEqual(code, want, body[:300])
                        if path.startswith("/api/v1/entries") and code == 200 and "q=" in path:
                            self.assertNotIn(HERS, json.dumps(json.loads(body)["items"]))
            code, _h, _b = self.request("POST", "/api/v1/pair", {"token": auth.new_pairing(self.cfg)}, token=False,
                                        headers={"Origin": self.origin})
            self.assertEqual(code, 204)
            self.assertEqual(self.request("POST", "/api/v1/logout", headers={"Origin": self.origin})[0], 204)
            self.assertEqual(self.request("GET", "/", token=False)[0], 200)
        self.assertEqual(calls, [])

    def test_every_write_route_answers_without_the_sessions_database_either(self):
        walked = {(m, p) for m, p, _fn, _a in writes.ROUTES}
        self.assertEqual(walked - set(WRITE_SAMPLES), set(), "add a sample for each new write route")
        self.app.spawn = lambda cfg, args: str(cfg.state_dir / "manual-trigger.log")
        sent = datetime(2026, 10, 15, 6, 0, tzinfo=IST)
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15",
                                    "chat_id": self.cfg.chat_id, "message_id": "600", "sent_at": sent.isoformat(),
                                    "sent_at_local": "06:00 IST", "opener": "Morning", "opener_source": "ada"})
        self.cfg.hermes_home = None   # settings: the file layer is the config's own file
        self.cfg.config_file = Path(self.tmp.name) / "config.json"
        self.cfg.config_file.write_text(json.dumps({"root": str(self.cfg.root)}), encoding="utf-8")
        self.cfg.origins = {"windows": "hermes"}
        hit = {"name": "Lisbon", "admin1": "", "country": "Portugal", "latitude": 38.7, "longitude": -9.1,
               "timezone": "Europe/Lisbon"}
        calls = []

        def refuse(*a, **k):
            calls.append(a)
            raise AssertionError("journal serve reached for the chat transcript on a write")
        with mock.patch.object(sqlite3, "connect", refuse), mock.patch.object(privacy, "view", refuse), \
                mock.patch("journal.location.geocode", return_value=hit), \
                mock.patch("journal.location.reverse_geocode", return_value=hit):
            for (method, _pattern), samples in WRITE_SAMPLES.items():
                for body, path, want in samples:
                    with self.subTest(method=method, path=path, body=body):
                        code, _h, raw = self.request(method, path, body, headers={"Origin": self.origin})
                        self.assertEqual(code, want, raw[:300])
        self.assertEqual(calls, [])

    def test_the_status_route_leaves_out_what_the_privacy_pass_knows(self):
        with mock.patch("journal.privacy.window_open") as window, mock.patch("journal.notebook.open_items") as items, \
                mock.patch("journal.hookstate.summary") as summary:
            self.assertEqual(self.request("GET", "/api/v1/status")[0], 200)
            self.assertEqual(self.request("GET", "/api/v1/doctor")[0], 200)
        self.assertEqual((window.call_count, items.call_count, summary.call_count), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
