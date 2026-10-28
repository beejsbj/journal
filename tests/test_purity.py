"""The core stays agent-neutral (ARCHITECTURE.md §1, WP7): Hermes is imported only by the Hermes adapter, and the
chat transcript is opened only by the transcript adapter.

Checked with ``ast`` over every module of ``src/journal``:

* no module outside ``journal/hermes/`` imports one of Hermes' own packages (``gateway``, ``hermes_cli``,
  ``hermes_constants``, ``agent``, ``tools``, ``cron`` …), except the ones listed in ``HERMES_ALLOWED`` with the
  reason they still do;
* ``sqlite3.connect`` is called only by the transcript adapter and by the context sources that open their own
  read-only databases (``sources.py``);
* the readers of the chat (conversations, privacy, sweep, recovery, context, mirror) do not import ``sqlite3`` at
  all, and nothing in the core imports ``journal.hermes`` at module level: the adapter is chosen at run time by
  ``Config.transcript()`` / ``Config.companion()`` (and reached lazily by the old entry points kept for callers).
"""
import ast
import unittest

from helpers import ROOT

SRC = ROOT / "src" / "journal"
HERMES_PACKAGES = {"gateway", "hermes_cli", "hermes_constants", "hermes_state", "hermes_time", "agent", "tools",
                   "cron", "run_agent", "model_tools", "toolsets", "plugins", "acp_adapter", "honcho_integration"}
# Core modules that still import a Hermes package, and why (each is a later package's to move).
HERMES_ALLOWED = {
    "audio.py": {"tools"},  # speech-to-text for audio files uses Hermes' own STT under Hermes (DECISIONS: WP7 raised)
}
SQLITE_CONNECT_ALLOWED = {"hermes/transcript.py", "sources.py"}
READERS = ("conversations.py", "privacy.py", "sweep.py", "recovery.py", "context.py", "mirror.py")


def modules():
    for path in sorted(SRC.rglob("*.py")):
        yield path.relative_to(SRC).as_posix(), ast.parse(path.read_text(encoding="utf-8"))


def absolute_imports(tree) -> set[str]:
    """Top-level package names of every absolute import, anywhere in the file (functions included)."""
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.add(node.module.split(".")[0])
    return out


def connect_calls(tree) -> int:
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Attribute) and f.attr == "connect" and getattr(f.value, "id", "") == "sqlite3":
                n += 1
        elif isinstance(node, ast.ImportFrom) and node.module == "sqlite3" and any(a.name == "connect" for a in node.names):
            n += 1
    return n


def module_level_journal_hermes(rel: str, tree) -> bool:
    """Whether a core module imports ``journal.hermes`` (or ``.hermes``) outside any function."""
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if (node.level and (mod == "hermes" or mod.startswith("hermes."))) or mod.startswith("journal.hermes"):
                return True
            if node.level and not mod and any(a.name == "hermes" for a in node.names):
                return True
        elif isinstance(node, ast.Import) and any(a.name.startswith("journal.hermes") for a in node.names):
            return True
    return False


class PurityTest(unittest.TestCase):
    def test_only_the_adapter_imports_hermes(self):
        seen = set()
        for rel, tree in modules():
            if rel.startswith("hermes/"):
                continue
            with self.subTest(rel):
                found = absolute_imports(tree) & HERMES_PACKAGES
                self.assertEqual(found - HERMES_ALLOWED.get(rel, set()), set())
                seen |= {rel} if found else set()
        self.assertEqual(seen, set(HERMES_ALLOWED))  # an allowance nobody needs any more is removed

    def test_only_the_transcript_and_the_sources_open_sqlite(self):
        callers = {rel for rel, tree in modules() if connect_calls(tree)}
        self.assertEqual(callers, SQLITE_CONNECT_ALLOWED)

    def test_the_readers_do_not_import_sqlite(self):
        trees = dict(modules())
        for rel in READERS:
            with self.subTest(rel):
                self.assertNotIn("sqlite3", absolute_imports(trees[rel]))

    def test_the_core_reaches_the_adapter_only_at_run_time(self):
        for rel, tree in modules():
            if rel.startswith("hermes/") or rel == "__init__.py":
                continue
            with self.subTest(rel):
                self.assertFalse(module_level_journal_hermes(rel, tree))

    def test_the_checks_see_what_they_look_for(self):
        trees = dict(modules())
        self.assertIn("gateway", absolute_imports(trees["hermes/transcript.py"]))
        self.assertGreater(connect_calls(trees["hermes/transcript.py"]), 0)
        self.assertTrue(module_level_journal_hermes("x", ast.parse("from .hermes import transcript")))
        self.assertTrue(module_level_journal_hermes("x", ast.parse("from . import hermes")))
        self.assertFalse(module_level_journal_hermes("x", ast.parse("def f():\n    from .hermes import transcript")))


class ThinAdapterTest(unittest.TestCase):
    """WP8: the Hermes hooks are event <-> dict translation; what happens to a message and a tool call is the engine's
    (``inbound``, ``turncontext``, ``tools``)."""
    ENGINE_STATE = {"capture", "ledger", "conversations", "hookstate", "privacy", "context", "programs", "notebook",
                    "location", "programview"}

    def test_the_hook_module_stays_small_and_does_not_touch_engine_state(self):
        path = SRC / "hermes" / "hooks.py"
        self.assertLess(len(path.read_text(encoding="utf-8").splitlines()), 150)
        imported = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 2:
                imported |= {a.name for a in node.names} | ({node.module} if node.module else set())
        self.assertEqual(imported & self.ENGINE_STATE, set())
        self.assertTrue({"inbound", "tools", "turncontext"} <= imported)

    def test_the_tool_module_keeps_off_engine_state(self):
        tree = ast.parse((SRC / "hermes" / "tools.py").read_text(encoding="utf-8"))
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 2 for a in n.names}
        self.assertEqual(imported & self.ENGINE_STATE, set())

    def test_every_registered_handler_lands_in_tools_call_with_its_name(self):
        from unittest import mock

        from journal import tools
        from journal.hermes import runtime
        from journal.hermes import tools as hermes_tools
        self.assertEqual(set(hermes_tools.HANDLERS), set(tools.HANDLERS))
        for name, handler in hermes_tools.HANDLERS.items():
            with self.subTest(name), mock.patch.object(runtime, "load_config", return_value="cfg"), \
                    mock.patch.object(runtime, "settings_writer", return_value=None), \
                    mock.patch.object(tools, "call", return_value="{}") as call:
                self.assertEqual(handler({"a": 1}, session_id="s", turn_id="t"), "{}")
                (cfg, called, params, turn), env = call.call_args
                self.assertEqual((cfg, called, params), ("cfg", name, {"a": 1}))
                self.assertEqual((turn.session_id, turn.turn_id), ("s", "t"))

    def test_the_engine_modules_that_took_the_logic_are_agent_neutral(self):
        for rel in ("inbound.py", "tools.py", "commands.py"):
            with self.subTest(rel):
                tree = dict(modules())[rel]
                self.assertEqual(absolute_imports(tree) & HERMES_PACKAGES, set())
                self.assertFalse(module_level_journal_hermes(rel, tree))


if __name__ == "__main__":
    unittest.main()
