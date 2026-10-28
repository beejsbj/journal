"""The committed web app build (``src/journal/serve/static``) against its sources (``web/``).

Node never runs on the deploy host, so the suite cannot rebuild; it can check that the build on disk was made from the
sources on disk: ``static/build.json`` carries the fingerprint ``web/scripts/source-hash.ts`` computed at build time and
``serve.static.web_source_hash`` recomputes it in Python. A change under ``web/`` without a rebuilt ``static/`` fails
here (and the web workflow fails it again by rebuilding and diffing). The rest pins what the build must not contain:
the front end is a PWA (DECISIONS.md, row 11: the 2026-10-19 "no PWA" reading was withdrawn the next day), so the build
must carry a service worker and a manifest that install from any origin, and no inline script.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import ROOT, make_cfg
from serve_helpers import NEUTRAL

from journal import status
from journal.serve import static

WEB = ROOT / "web"
STATIC = ROOT / "src" / "journal" / "serve" / "static"


class SourceHashTest(unittest.TestCase):
    def test_it_covers_the_inputs_and_only_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            (web / "src" / "routes").mkdir(parents=True)
            (web / "tests").mkdir()
            (web / "index.html").write_text("<html>", encoding="utf-8")
            (web / "src" / "a.ts").write_text("a", encoding="utf-8")
            (web / "src" / "routes" / "b.tsx").write_text("b", encoding="utf-8")
            base = static.web_source_hash(web)
            (web / "tests" / "x.test.ts").write_text("a test", encoding="utf-8")    # not an input
            (web / "src" / ".DS_Store").write_text("litter", encoding="utf-8")      # dot-files are skipped
            (web / "node_modules").mkdir()
            (web / "node_modules" / "m.js").write_text("m", encoding="utf-8")       # never an input
            self.assertEqual(static.web_source_hash(web), base)
            (web / "src" / "a.ts").write_text("changed", encoding="utf-8")
            self.assertNotEqual(static.web_source_hash(web), base)

    def test_the_algorithm_is_the_documented_one(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            web = Path(tmp)
            (web / "src").mkdir()
            (web / "package.json").write_bytes(b"{}")
            (web / "src" / "m.ts").write_bytes(b"x")
            h = hashlib.sha256()
            for rel, data in (("package.json", b"{}"), ("src/m.ts", b"x")):
                h.update(f"{rel}\0{hashlib.sha256(data).hexdigest()}\n".encode())
            self.assertEqual(static.web_source_hash(web), h.hexdigest())


@unittest.skipUnless((STATIC / "index.html").is_file(), "no web app build is committed yet")
class CommittedBuildTest(unittest.TestCase):
    def test_the_build_is_of_the_sources(self):
        info = json.loads((STATIC / "build.json").read_text(encoding="utf-8"))
        self.assertEqual(info["source"], static.web_source_hash(WEB),
                         "web/ changed since src/journal/serve/static was built: run the web workflow "
                         "(workflow_dispatch or any push touching web/), download its `static` artifact and commit it")

    def test_the_page_loads_only_its_own_files_and_runs_no_inline_code(self):
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('<div id="app">', html)
        refs = re.findall(r'(?:src|href)="(/[^"]+)"', html)
        self.assertTrue([r for r in refs if r.startswith("/assets/")])
        for ref in refs:
            self.assertTrue((STATIC / ref.lstrip("/")).is_file(), ref)
        for tag in re.findall(r"<script\b[^>]*>", html):
            self.assertIn("src=", tag)                       # CSP is default-src 'self': no inline script runs
        self.assertNotIn("<style", html)
        self.assertNotRegex(html, r"\sstyle=")
        self.assertNotRegex(html, r"https?://(?!www\.w3\.org)")   # nothing is fetched from elsewhere

    def test_it_is_a_pwa_that_installs_from_any_origin(self):
        names = {p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*") if p.is_file()}
        self.assertTrue({"sw.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-512.png",
                         "icons/icon-maskable-512.png", "icons/apple-touch-icon.png", "icons/mark.svg"} <= names)
        html = (STATIC / "index.html").read_text(encoding="utf-8")
        self.assertIn('<link rel="manifest" href="/manifest.webmanifest">', html)
        manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
        # nothing names a host or a port: the same build installs from a tailnet address with a port, a published
        # host or loopback
        self.assertEqual((manifest["start_url"], manifest["scope"], manifest["id"], manifest["display"]),
                         ("/", "/", "/", "standalone"))
        self.assertNotRegex(json.dumps(manifest), r"https?:|:\d{2,5}\b")
        sizes = {(i["sizes"], i["purpose"]) for i in manifest["icons"]}
        self.assertTrue({("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes)
        for icon in manifest["icons"]:
            self.assertFalse(icon["src"].startswith(("/", "http")), icon["src"])    # relative to the manifest
            self.assertTrue((STATIC / icon["src"]).is_file(), icon["src"])
        # the installed app's colours are the page's (tokens.css, the light sheet)
        tokens = (WEB / "src" / "tokens.css").read_text(encoding="utf-8")
        bg = re.search(r"--bg:\s*(#[0-9a-fA-F]{6});", tokens).group(1).lower()
        self.assertEqual((manifest["theme_color"], manifest["background_color"]), (bg, bg))
        self.assertIn(f'content="{bg}" media="(prefers-color-scheme: light)"', html)

    def test_the_service_worker_is_a_classic_script_that_precaches_the_shell_and_holds_the_rules(self):
        sw = (STATIC / "sw.js").read_text(encoding="utf-8")
        self.assertNotRegex(sw, r"^\s*(import|export)\s", "sw.js must run as a classic script (one bundle)")
        for needle in ('"url":"index.html"', '"url":"manifest.webmanifest"', "x-journal-cache", "SKIP_WAITING",
                       "CLEAR_DATA", "journal-data-v1"):
            self.assertIn(needle, sw)
        for asset in re.findall(r'(?:src|href)="(/assets/[^"]+)"', (STATIC / "index.html").read_text(encoding="utf-8")):
            self.assertIn(f'"url":"{asset.lstrip("/")}"', sw, asset)     # the shell is versioned by the build's file names
        self.assertNotIn("build.json", sw)
        # nothing of this host: the worker serves whatever origin it was fetched from
        self.assertEqual(re.findall(r"https?://[^\"'`\s)]+", sw), ["https://bit.ly/wb-precache"])   # a comment's link in Workbox
        # the page registers it, and the api never goes through a write rule
        self.assertTrue(any("serviceWorker" in js.read_text(encoding="utf-8") for js in (STATIC / "assets").glob("*.js")))

    def test_the_build_never_names_a_person_or_a_path_of_this_install(self):
        for path in STATIC.rglob("*"):
            if path.is_file() and path.suffix in {".js", ".css", ".html", ".json", ".webmanifest", ".svg"}:
                text = path.read_text(encoding="utf-8")
                for word in ("/home/", "/mnt/", ".ts.net"):   # a deployment's names: tools/check_private.py
                    self.assertNotIn(word, text, f"{path.name}: {word}")


class RollbackKillSwitchTest(unittest.TestCase):
    """A 404 on ``/sw.js`` does not remove an installed worker (the browser keeps it when the update check fails), so a
    rollback to a build without a worker must serve one that unregisters itself; ``web/tests/killswitch.test.ts`` runs it."""

    def test_the_kill_switch_ships_and_the_rollback_in_deploy_serves_it(self):
        script = (ROOT / "deploy" / "killswitch-sw.js").read_text(encoding="utf-8")
        for needle in ("skipWaiting", "caches.delete", "registration.unregister()", "navigate("):
            self.assertIn(needle, script)
        self.assertNotIn("fetch", script)       # it answers no request: everything goes to the server
        deploy = (ROOT / "DEPLOY.md").read_text(encoding="utf-8")
        rollback = deploy[deploy.index("Rollback: the installed worker"):]
        rollback = rollback[:rollback.index("## SERVEPORT")]
        self.assertIn("deploy/killswitch-sw.js", rollback)
        self.assertIn("assets_dir", rollback)
        self.assertNotIn("removes it (the standard behaviour", deploy)

    def test_the_orientation_files_say_that_serve_ships(self):
        for name in ("AGENTS.md", "CLAUDE.md"):
            head = (ROOT / name).read_text(encoding="utf-8")[:300]
            self.assertIn("and `journal serve` with a PWA", head, name)
            self.assertNotIn("later `journal serve`", head, name)


@unittest.skipUnless((STATIC / "index.html").is_file(), "no web app build is committed yet")
class DoctorRowTest(unittest.TestCase):
    def row(self, cfg):
        return {n: (s, d) for n, s, d in status.doctor(cfg)}["serve assets"]

    def test_the_row_names_the_build_and_warns_when_web_moved_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), names=NEUTRAL)
            info = json.loads((STATIC / "build.json").read_text(encoding="utf-8"))
            state, detail = self.row(cfg)
            self.assertEqual(state, "ok")
            self.assertIn(info["source"][:12], detail)
            moved = Path(tmp) / "web"
            moved.mkdir()
            (moved / "package.json").write_text("{}", encoding="utf-8")
            with mock.patch.object(static, "checkout_web", return_value=moved):
                state, detail = self.row(cfg)
            self.assertEqual(state, "warn")
            self.assertIn("rebuild", detail)


if __name__ == "__main__":
    unittest.main()
