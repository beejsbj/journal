"""The import fence around ``journal mcp``, the same as ``journal serve``'s (API.md Decision 2): no module of
``journal/mcp`` imports what reads the chat transcript, its privacy results, packs, the mirror queue or the notebook,
and none reaches a model (``agent``). Nor does it import ``serve/``: its readers are the core's (``document``, issue
#47), shared with the web app and fenced in ``test_serve_fence``. What the face reaches through the engine is fenced
by behaviour instead (``test_mcp.PrivacyTest`` runs every tool with ``sqlite3.connect`` made to fail)."""
import json
import subprocess
import sys
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
                self.assertEqual(imported(path) & (FORBIDDEN | {"agent", "tools", "serve", "__import__", "importlib"}),
                                 set())

    def test_loading_the_tools_loads_nothing_of_serve(self):
        """The fence above reads ``mcp/``'s own imports; this loads ``journal.mcp.handlers`` in a fresh interpreter
        and checks that nothing it brings in, through the core included, is a module of ``journal.serve``."""
        probe = ("import json, sys; sys.path.insert(0, sys.argv[1]); import journal.mcp.handlers; "
                 "print(json.dumps(sorted(n for n in sys.modules if n.startswith('journal.'))))")
        out = subprocess.run([sys.executable, "-I", "-c", probe, str(ROOT / "src")], capture_output=True, text=True,
                             timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr[-2000:])
        loaded = json.loads(out.stdout)
        self.assertIn("journal.mcp.handlers", loaded)
        self.assertIn("journal.document", loaded)   # (the shared reader, from the core)
        self.assertEqual([n for n in loaded if n == "journal.serve" or n.startswith("journal.serve.")], [])

    def test_the_fence_sees_an_import(self):
        self.assertIn("document", imported(FACE / "handlers.py"))   # (the core's shared reader, not serve's)
        self.assertIn("sqlite3", imported(ROOT / "src" / "journal" / "hermes" / "transcript.py"))
