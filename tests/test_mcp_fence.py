"""The import fence around ``journal mcp``, the same as ``journal serve``'s (API.md Decision 2): no module of
``journal/mcp`` imports what reads the chat transcript, its privacy results, packs, the mirror queue or the notebook,
and none reaches a model (``agent``). What the face reaches through the engine is fenced by behaviour instead
(``test_mcp.PrivacyTest`` runs every tool with ``sqlite3.connect`` made to fail)."""
import unittest

from helpers import ROOT
from test_serve_fence import FORBIDDEN, imported

FACE = ROOT / "src" / "journal" / "mcp"


class FenceTest(unittest.TestCase):
    def test_no_mcp_module_imports_the_chat_side_or_a_model(self):
        files = sorted(FACE.glob("*.py"))
        self.assertEqual([p.name for p in files], ["__init__.py", "handlers.py", "server.py"])
        for path in files:
            with self.subTest(path.name):
                self.assertEqual(imported(path) & (FORBIDDEN | {"agent", "tools", "__import__", "importlib"}), set())

    def test_the_fence_sees_an_import(self):
        self.assertIn("serve", imported(FACE / "handlers.py"))
        self.assertIn("sqlite3", imported(ROOT / "src" / "journal" / "hermes" / "transcript.py"))
