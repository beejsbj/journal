"""The import fence around ``journal serve`` (API.md Decision 2): no module of ``journal/serve`` imports what reads
the chat transcript, its privacy results, packs, the mirror queue or the notebook, and none reaches a model
(``agent``: README *Guarantees*, the web app calls no model). What the server reaches through
the engine (``today.collect``, ``status.status``) is fenced by ``test_serve_privacy`` instead, which runs every
route with ``sqlite3.connect`` made to fail and every way to a model fenced (``helpers.no_model``).

The readers the web app shares with ``journal mcp`` live in the core since issue #47 (``SHARED``: the entry document,
the summaries and the filtered list); they are fenced here like a module of ``serve/``, and import nothing of
``serve/`` either."""
import ast
import unittest

from helpers import ROOT

SERVE = ROOT / "src" / "journal" / "serve"
SHARED = [ROOT / "src" / "journal" / "document.py"]   # the faces' shared readers, in the core (issue #47)
FORBIDDEN = {"conversations", "sweep", "recovery", "privacy", "mirror", "context", "ports", "notebook", "sqlite3",
             "transcript", "agent"}


def imported(path) -> set[str]:
    """Every module name and imported name in a file, split at the dots (``from ..x import y`` gives x and y)."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            for a in node.names:
                names |= set(a.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            names |= set((node.module or "").split("."))
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.Call) and getattr(node.func, "id", "") == "__import__":
            names.add("__import__")
    return names - {""}


class FenceTest(unittest.TestCase):
    def test_no_serve_module_imports_the_chat_side(self):
        files = sorted(SERVE.glob("*.py"))
        self.assertGreaterEqual(len(files), 7)  # app, api, auth, document, sse, static, unit (+ __init__)
        for path in files:
            with self.subTest(path.name):
                self.assertEqual(imported(path) & (FORBIDDEN | {"__import__", "importlib"}), set())

    def test_the_shared_readers_import_neither_the_chat_side_nor_a_face(self):
        for path in SHARED:
            with self.subTest(path.name):
                self.assertTrue(path.is_file())
                self.assertEqual(imported(path) & (FORBIDDEN | {"serve", "mcp", "tools", "__import__", "importlib"}),
                                 set())

    def test_the_old_name_of_the_document_is_the_same_module(self):
        from journal import document
        from journal.serve import document as old
        self.assertIs(old, document)

    def test_the_fence_sees_an_import(self):
        # the module that does read the transcript (WP7 moved every read of Hermes' database there)
        sample = SERVE.parent / "hermes" / "transcript.py"
        self.assertIn("sqlite3", imported(sample))
        self.assertIn("conversations", imported(SERVE.parent / "context.py"))
        self.assertIn("agent", imported(SERVE.parent / "validate.py"))  # and a model import is seen the same way
        self.assertIn("agent", FORBIDDEN)
