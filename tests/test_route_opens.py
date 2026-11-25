"""What ``journal serve`` and ``journal mcp`` open (issue #44): the fence by behaviour, not only by import.

``test_serve_fence`` / ``test_mcp_fence`` check what ``serve/`` and ``mcp/`` import themselves; loading them still
loads, through the engine, modules that read the companion's side (``ModuleLoadTest`` pins that set, so it can only
shrink by a decision). What keeps the faces off those files is that no route or tool opens them, and this checks it:
the route walks of ``test_serve_privacy`` (every GET, write and push route) and the tool walk of ``test_mcp`` run again
with every file open, directory listing and SQLite connection recorded (``sys.addaudithook``: ``open``, ``os.open``,
``os.listdir``, ``os.scandir``, ``sqlite3.connect``, in every thread, the server's included). Under ``state_dir`` only
the files in ``ALLOWED`` may be touched; a path opened relative to a directory handle may not name anything in
``NEVER``; the chat transcript (Hermes' sessions database, beside the journal here) is never touched."""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import unittest
from contextlib import contextmanager
from pathlib import Path

import test_mcp as mcp_walks
import test_serve_privacy as serve_walks
from helpers import ROOT
from test_serve_fence import FORBIDDEN

# What the faces may open under ``state_dir``: a file name, or a directory followed by "/" for anything in it. A file
# also brings its lock (``ledger.lock`` beside ``ledger.json``: ``ledger.locked``) and the temporary file an atomic
# write makes beside it (``.ledger.json-…``). Each is the faces' own state or what ``status`` and ``doctor`` show.
ALLOWED = {
    "ledger.json",               # prompt records, read and written under its lock
    "today-cache.json",          # the today view's cache (``today.collect``)
    "practices/",                # his practices and programs, and the library's cache
    "runs/",                     # program runs (``programs``, ``sse``)
    "last-tick.json",            # status, doctor: the tick's last run
    "outage.json",               # status: a model outage the breaker holds (no words)
    "ops.json",                  # status: the operator's incidents (no words)
    "errors.jsonl",              # status, doctor: the engine's error lines
    "location-history.jsonl",    # settings: where he set his location
    "serve-auth.json", "serve-pairings.json",   # serve's own sign-in
    "serve-push.json", "push-sent.json",        # serve's Web Push
    "uploads/",                  # serve's uploads before they are kept
    "manual-trigger.log",        # the spawned command's log
}
NEVER = ("notebook.json", "notebook-archive.jsonl", "packs/", "conversations.json", "mirror-queue.json",
         "sent.jsonl", "send-times.json", "audio-stash/", "tick.lock", "hook-state.json", "sweep.json")

_LOCK = threading.Lock()
_ON: list[list[tuple[str, str]]] = []   # the recording now open, if any (one at a time)
# ``sqlite3.connect``: SQLite opens its file in C, which raises no ``open`` event (its argument may be a ``file:`` URI).
_EVENTS = {"open", "os.listdir", "os.scandir", "sqlite3.connect"}


def _name(event, path) -> str | None:
    """The path an event touched: absolute and resolved; a directory handle (``os.scandir(fd)``) resolved through
    ``/proc``; a relative path kept as given (``os.open(name, dir_fd=…)`` raises ``open`` with ``name`` alone, so the
    cwd is not what it is relative to); None for what cannot be named (``open(fd)``: its open was seen already)."""
    if path is None and event != "open":
        path = "."
    if isinstance(path, int) and event != "open":
        try:
            return os.readlink(f"/proc/self/fd/{path}")
        except OSError:
            return f"<directory handle {path}>"
    if not isinstance(path, (str, bytes, os.PathLike)):
        return None
    try:
        name = os.fsdecode(path)
    except (TypeError, ValueError):
        return None
    if event == "sqlite3.connect":
        if name == ":memory:" or not name:
            return None
        if name.startswith("file:"):
            name = name[len("file:"):].split("?", 1)[0].split("#", 1)[0]
    return os.path.realpath(name) if os.path.isabs(name) else os.path.normpath(name)


def _hook(event, args):
    if event not in _EVENTS or not _ON or not args:
        return
    name = _name(event, args[0])
    if name is None:
        return
    with _LOCK:
        if _ON:
            _ON[-1].append((event, name))


sys.addaudithook(_hook)   # (cannot be removed; costs one test of ``_ON`` per event while nothing records)


@contextmanager
def recording():
    """Every path opened or listed while open, as (event, real path)."""
    seen: list[tuple[str, str]] = []
    with _LOCK:
        _ON.append(seen)
    try:
        yield seen
    finally:
        with _LOCK:
            _ON.remove(seen)


def allowed(rel: str, also: set[str] = frozenset()) -> bool:
    """Whether a face may touch ``rel`` (a path under ``state_dir``): a name in ``ALLOWED`` (or ``also``), its lock,
    the temporary file an atomic write of it makes, or anything in a directory ``ALLOWED`` names with "/"."""
    names = ALLOWED | set(also)
    top = rel.split(os.sep, 1)[0]
    if top + "/" in names:
        return True
    if os.sep in rel:
        return False
    for name in names:
        if name.endswith("/"):
            continue
        if rel == name or rel == Path(name).stem + ".lock" or rel.startswith((f".{name}-", f".{name}.")):
            return True
    return False


def names_never(rel: str) -> bool:
    """Whether a relative path (one opened against a directory handle, so where it lies is not known) names anything
    in ``NEVER`` in any part: the file, its lock, an atomic write's temporary file, or a directory ``NEVER`` lists."""
    for part in Path(rel).parts:
        for item in NEVER:
            name = item.rstrip("/")
            if part == name or (not item.endswith("/") and (
                    part == Path(name).stem + ".lock" or part.startswith((f".{name}-", f".{name}.")))):
                return True
    return False


def judge(cfg, seen, also: set[str] = frozenset()) -> list[str]:
    """What in ``seen`` a face may not touch: anything under ``state_dir`` outside ``ALLOWED``, a relative path that
    names anything in ``NEVER`` (``names_never``), and the sessions database (with its journal files) wherever it is.
    Listing ``state_dir`` itself is allowed (names only)."""
    state = os.path.realpath(cfg.state_dir)
    db = os.path.realpath(cfg.state_db) if cfg.state_db else None
    bad = set()
    for event, name in seen:
        if not os.path.isabs(name):
            if names_never(name) or (db and os.path.basename(name) == os.path.basename(db)):
                bad.add(f"{event} {name} (relative to a directory handle)")
        elif db and (name == db or name.startswith(db + "-")):
            bad.add(f"{event} {name} (the chat transcript)")
        elif name.startswith(state + os.sep) and not allowed(name[len(state) + 1:], also):
            bad.add(f"{event} {name[len(state) + 1:]}")
    return sorted(bad)


def _recorded(walk):
    def test(self):
        with recording() as seen:
            walk(self)
        self.assertTrue(seen, "nothing was recorded: the audit hook saw no open")
        self.assertEqual(judge(self.cfg, seen), [], "a face opened a file it must not")
    test.__doc__ = f"{walk.__name__}, with every file it opens recorded."
    return test


class ServeOpensTest(serve_walks.ServeCase):
    """The serve route walks, every file they open recorded."""
    test_get_routes = _recorded(serve_walks.PrivacyTest.test_every_route_answers_without_the_sessions_database)
    test_write_now_view = _recorded(
        serve_walks.PrivacyTest.test_write_nows_view_of_a_prompt_the_chat_has_opens_no_database)
    test_write_routes = _recorded(
        serve_walks.PrivacyTest.test_every_write_route_answers_without_the_sessions_database_either)
    test_push_routes = _recorded(
        serve_walks.PrivacyTest.test_every_push_route_and_a_pass_answer_without_the_sessions_database)


class McpOpensTest(mcp_walks.Case):
    """The MCP tool walk, every file it opens recorded."""
    test_tools = _recorded(mcp_walks.PrivacyTest.test_no_tool_opens_a_database)


class NotVacuousTest(serve_walks.ServeCase):
    def test_a_forbidden_open_is_seen_in_any_thread_and_judged(self):
        """Each kind of access is recorded and judged, from the server's thread too: a route that read the notebook
        or listed ``packs/`` would fail the walks above."""
        cfg = self.cfg
        (cfg.state_dir / "notebook.json").write_text("{}", encoding="utf-8")

        def reads():
            (cfg.state_dir / "notebook.json").read_text(encoding="utf-8")
            os.close(os.open(cfg.state_dir / "conversations.json", os.O_RDONLY))
            list((cfg.state_dir / "packs").iterdir())
            os.listdir(cfg.state_dir / "packs")
            with open(cfg.state_db, "rb"):
                pass
        with recording() as seen:
            t = threading.Thread(target=reads)
            t.start()
            t.join(5)
            (cfg.state_dir / "ledger.json").read_text(encoding="utf-8")
        bad = judge(cfg, seen)
        self.assertEqual({b.split(" ", 1)[1] for b in bad}, {
            "notebook.json", "conversations.json", "packs", f"{os.path.realpath(cfg.state_db)} (the chat transcript)"})
        self.assertIn("os.listdir packs", bad)   # (a listing is seen as one, not only an open)

    def test_a_connection_and_a_handles_relative_opens_are_seen_and_judged(self):
        """SQLite opens its file in C (no ``open`` event): ``sqlite3.connect`` is recorded, a ``file:`` URI too. A
        name opened against a directory handle is not resolved against the cwd: one in ``NEVER`` fails wherever it
        lies, and a listing of a handle is resolved to its directory."""
        cfg = self.cfg
        (cfg.state_dir / "notebook.json").write_text("{}", encoding="utf-8")
        (cfg.state_dir / "packs").mkdir(exist_ok=True)
        packs = os.open(cfg.state_dir / "packs", os.O_RDONLY)   # (opened before recording: the listing alone is judged)
        self.addCleanup(os.close, packs)
        with recording() as seen:
            sqlite3.connect(f"file:{cfg.state_db}?mode=ro", uri=True).close()
            sqlite3.connect(":memory:").close()
            (cfg.state_dir / "ledger.json").touch()
            fd = os.open(cfg.state_dir, os.O_RDONLY)
            try:
                os.close(os.open("notebook.json", os.O_RDONLY, dir_fd=fd))
                os.close(os.open("ledger.json", os.O_RDONLY, dir_fd=fd))   # (allowed: not in NEVER)
            finally:
                os.close(fd)
            list(os.scandir(packs))
        bad = judge(cfg, seen)
        self.assertIn(f"sqlite3.connect {os.path.realpath(cfg.state_db)} (the chat transcript)", bad)
        self.assertIn("open notebook.json (relative to a directory handle)", bad)
        self.assertIn("os.scandir packs", bad)
        self.assertNotIn("open ledger.json (relative to a directory handle)", bad)
        self.assertEqual(len(bad), 3, bad)
        for rel in ("notebook.json", "packs/x.json", ".notebook.json-ab12", "notebook.lock", "a/sweep.json"):
            with self.subTest(rel):
                self.assertTrue(names_never(rel))
        for rel in ("ledger.json", "practices/x.md", "notebook.md"):
            with self.subTest(rel):
                self.assertFalse(names_never(rel))

    def test_nothing_never_opened_is_allowed(self):
        for name in NEVER:
            with self.subTest(name):
                self.assertNotIn(name, ALLOWED)
                self.assertFalse(allowed(name.rstrip("/")) or allowed(name.rstrip("/") + os.sep + "x"))

    def test_a_lock_and_an_atomic_writes_temporary_file_go_with_their_file(self):
        for rel in ("ledger.json", "ledger.lock", ".ledger.json-ab12cd", "practices/.library.lock", "runs"):
            with self.subTest(rel):
                self.assertTrue(allowed(rel.replace("/", os.sep)))
        for rel in ("notebook.lock", ".notebook.json-ab12", "packs", "ledger.json.bak", "practices.json"):
            with self.subTest(rel):
                self.assertFalse(allowed(rel))


class StatusTest(serve_walks.ServeCase):
    def test_status_opens_neither_the_hooks_state_nor_the_sweeps(self):
        """``GET /api/v1/status`` serves no ``sweep`` and no today-block injection count (DECISIONS.md, ROUTE-OPENS), so
        it opens neither ``hook-state.json`` nor ``sweep.json``, even when both are there, as no other route does."""
        (self.cfg.state_dir / "hook-state.json").write_text("{}", encoding="utf-8")
        (self.cfg.state_dir / "sweep.json").write_text("{}", encoding="utf-8")
        with recording() as seen:
            for _pattern, samples in serve_walks.SAMPLES.items():
                for path, want in samples:
                    with self.subTest(path):
                        self.assertEqual(self.request("GET", path)[0], want)
        self.assertTrue(any(name.endswith("ledger.json") for _e, name in seen))   # (the walk was recorded)
        self.assertEqual(judge(self.cfg, seen), [])


class ModuleLoadTest(unittest.TestCase):
    """What loading the faces brings in that the import fence forbids ``serve/`` and ``mcp/`` to import themselves
    (``test_serve_fence.FORBIDDEN``), pinned: a change either way is a decision (README *Guarantees*; issue #44). They
    are loaded, never called: the walks above and ``test_serve_privacy`` show no route reads through them."""
    KNOWN = {"journal.serve.app": ["journal.agent", "journal.mirror", "journal.notebook", "journal.ports", "sqlite3"],
             "journal.mcp.handlers": ["journal.agent", "journal.mirror", "journal.notebook", "journal.ports",
                                      "sqlite3"]}

    def test_what_loading_a_face_brings_in_is_known(self):
        probe = ("import json, sys; sys.path.insert(0, sys.argv[1]); import importlib; importlib.import_module(sys.argv[2]); "
                 "F = set(json.loads(sys.argv[3])); "
                 "print(json.dumps(sorted(n for n in sys.modules if n.rsplit('.', 1)[-1] in F "
                 "and (n.startswith('journal.') or '.' not in n))))")
        for face, known in self.KNOWN.items():
            with self.subTest(face):
                out = subprocess.run([sys.executable, "-I", "-c", probe, str(ROOT / "src"), face,
                                      json.dumps(sorted(FORBIDDEN))], capture_output=True, text=True, timeout=60)
                self.assertEqual(out.returncode, 0, out.stderr[-2000:])
                self.assertEqual(json.loads(out.stdout), known)


if __name__ == "__main__":
    unittest.main()
