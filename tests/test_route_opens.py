"""What ``journal serve`` and ``journal mcp`` open (issue #44): the fence by behaviour, not only by import.

``test_serve_fence`` / ``test_mcp_fence`` check what ``serve/`` and ``mcp/`` import themselves; loading them still
loads, through the engine, modules that read the companion's side (``ModuleLoadTest`` pins that set, so it can only
shrink by a decision). What keeps the faces off those files is that no route or tool opens them, and this checks it:
the route walks of ``test_serve_privacy`` (every GET, write and push route) and the tool walk of ``test_mcp`` run again
with every file open and directory listing recorded (``sys.addaudithook``: ``open``, ``os.open``, ``os.listdir``,
``os.scandir``, in every thread, the server's included). Under ``state_dir`` only the files in ``ALLOWED`` may be
touched; the chat transcript (Hermes' sessions database, beside the journal here) never is."""
import json
import os
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
# Read today by one route only, ``GET /api/v1/status`` (``status.status(web=True)``): API.md documents its today-block
# counts, which live in the hook's state, and it returns the sweep's watermark. Both are pinned to that route
# (``test_only_status_reads_these``) and raised in DECISIONS.md (ROUTE-OPENS) for the owner to settle.
STATUS_ONLY = {"hook-state.json": "today.injections (the today block's count and time; API.md, GET /status)",
               "sweep.json": "the sweep's watermark, returned as status's ``sweep``"}
NEVER = ("notebook.json", "notebook-archive.jsonl", "packs/", "conversations.json", "mirror-queue.json",
         "sent.jsonl", "send-times.json", "audio-stash/", "tick.lock", "hook-state.json", "sweep.json")

_LOCK = threading.Lock()
_ON: list[list[tuple[str, str]]] = []   # the recording now open, if any (one at a time)
_EVENTS = {"open", "os.listdir", "os.scandir"}


def _hook(event, args):
    if event not in _EVENTS or not _ON or not args:
        return
    path = args[0]
    if path is None and event != "open":
        path = "."
    if not isinstance(path, (str, bytes, os.PathLike)):
        return   # (a file descriptor: its open was seen already)
    try:
        name = os.path.realpath(os.fsdecode(path))
    except (TypeError, ValueError):
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


def judge(cfg, seen, also: set[str] = frozenset()) -> list[str]:
    """What in ``seen`` a face may not touch: anything under ``state_dir`` outside ``ALLOWED``, and the sessions
    database (with its journal files) wherever it is. Listing ``state_dir`` itself is allowed (names only)."""
    state = os.path.realpath(cfg.state_dir)
    db = os.path.realpath(cfg.state_db) if cfg.state_db else None
    bad = set()
    for event, name in seen:
        if db and (name == db or name.startswith(db + "-")):
            bad.add(f"{event} {name} (the chat transcript)")
        elif name.startswith(state + os.sep) and not allowed(name[len(state) + 1:], also):
            bad.add(f"{event} {name[len(state) + 1:]}")
    return sorted(bad)


def _recorded(walk):
    def test(self):
        with recording() as seen:
            walk(self)
        self.assertTrue(seen, "nothing was recorded: the audit hook saw no open")
        self.assertEqual(judge(self.cfg, seen, set(STATUS_ONLY)), [], "a face opened a file it must not")
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


class StatusOnlyTest(serve_walks.ServeCase):
    def test_only_status_reads_these(self):
        """Every GET route but ``/status`` opens neither file in ``STATUS_ONLY``; ``/status`` opens both. When the
        owner's choice (DECISIONS.md, ROUTE-OPENS) takes them out of the web status, the second half fails and this
        test, with ``STATUS_ONLY``, goes."""
        (self.cfg.state_dir / "hook-state.json").write_text("{}", encoding="utf-8")
        (self.cfg.state_dir / "sweep.json").write_text("{}", encoding="utf-8")
        with recording() as seen:
            for pattern, samples in serve_walks.SAMPLES.items():
                for path, want in samples:
                    if pattern != r"/api/v1/status":
                        with self.subTest(path):
                            self.assertEqual(self.request("GET", path)[0], want)
        self.assertEqual(judge(self.cfg, seen), [])
        with recording() as seen:
            self.assertEqual(self.request("GET", "/api/v1/status")[0], 200)
        self.assertEqual(judge(self.cfg, seen), sorted(f"open {n}" for n in STATUS_ONLY))


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
