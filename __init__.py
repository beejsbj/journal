"""Hermes plugin entry point. The code lives in ``src/journal``; this wrapper makes the repository root
an installable directory plugin (``hermes plugins install beejsbj/journal``)."""
import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.realpath(__file__)), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from journal.hermes import register  # noqa: E402,F401
