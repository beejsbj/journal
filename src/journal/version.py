"""Which code is running: the release number and a fingerprint of the package's own source.

Two installs run the journal on one host: the Hermes plugin's ``src/`` (gateway hooks and the cron tick) and the
``uv tool`` install (``journal serve``). Both should be pinned to the same commit, but neither carries the git sha
(a plugin install copies files; a wheel built from git keeps only the version). So the fingerprint is computed
from what is actually on disk: a sha256 over the package's ``.py`` files, path and content, in path order. Two
installs of one commit give the same value whatever their location; any code difference gives another. The tick
records it in ``last-tick.json`` and ``journal doctor`` compares it with its own (the "same version as tick" row).
"""
from __future__ import annotations

import functools
import hashlib
from pathlib import Path

from . import __version__

PACKAGE = Path(__file__).resolve().parent


@functools.lru_cache(maxsize=1)
def code_id() -> str:
    """Twelve hex characters naming this package's Python source (stable across install locations)."""
    h = hashlib.sha256()
    for path in sorted(PACKAGE.rglob("*.py")):
        rel = path.relative_to(PACKAGE).as_posix()
        if "__pycache__" in rel:
            continue
        h.update(rel.encode() + b"\0")
        try:
            h.update(path.read_bytes())
        except OSError:
            h.update(b"<unreadable>")
        h.update(b"\0")
    return h.hexdigest()[:12]


def stamp() -> dict:
    """``{"version", "code"}``: what the tick writes into ``last-tick.json`` and ``/healthz`` serves."""
    return {"version": __version__, "code": code_id()}
