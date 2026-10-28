"""The web app's files: the committed build in ``serve/static`` (or ``serve.assets_dir``), else a placeholder.

The build (WP11) is a single-page app: any path without a file extension (``/``, ``/entries/2026-10-14-evening``,
``/pair``) answers ``index.html`` and the client routes it. Until a build is committed, the small page in
``serve/fallback`` stands in; it can pair a browser and nothing else. A path never leaves its directory (resolved,
then checked to be inside), and dot-files are never served.
"""
from __future__ import annotations

import hashlib
import json
import mimetypes
from pathlib import Path
from urllib.parse import unquote

from ..config import Config
from .api import FileReply

PACKAGED = Path(__file__).resolve().parent / "static"
FALLBACK = Path(__file__).resolve().parent / "fallback"
TYPES = {".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
         ".css": "text/css; charset=utf-8", ".html": "text/html; charset=utf-8", ".json": "application/json",
         ".svg": "image/svg+xml", ".webmanifest": "application/manifest+json", ".woff2": "font/woff2"}


# What the web build is made from, and how it is fingerprinted: ``web/scripts/source-hash.ts`` is the same algorithm
# in TypeScript, and the build writes the result to ``static/build.json``. Only inputs count (not ``web/tests`` or
# ``web/e2e``), so editing a test never asks for a rebuild.
WEB_INPUT_FILES = ("index.html", "package.json", "package-lock.json", "tsconfig.json", "vite.config.ts")
WEB_INPUT_DIRS = ("src", "scripts", "public")


def web_source_hash(web: Path) -> str:
    """sha256 over ``<relative posix path>\0<sha256 hex of its bytes>\n`` for each input file in path order
    (dot-files skipped): equal to what ``web/scripts/source-hash.ts`` wrote into ``build.json`` for the same files."""
    found = [f for f in WEB_INPUT_FILES if (web / f).is_file()]
    for d in WEB_INPUT_DIRS:
        base = web / d
        if base.is_dir():
            found += [p.relative_to(web).as_posix() for p in base.rglob("*")
                      if p.is_file() and not any(part.startswith(".") for part in p.relative_to(web).parts)]
    total = hashlib.sha256()
    for rel in sorted(found):
        digest = hashlib.sha256((web / rel).read_bytes()).hexdigest()
        total.update(f"{rel}\0{digest}\n".encode())
    return total.hexdigest()


def build_info(cfg: Config) -> dict | None:
    """``build.json`` of the web app build in use (``{"source": <hash of web/>}``), or None when there is none."""
    try:
        data = json.loads((assets_dir(cfg) / "build.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and isinstance(data.get("source"), str) else None


def checkout_web() -> Path | None:
    """``web/`` of the checkout this package runs from, when it does (an editable or in-tree run), else None."""
    web = Path(__file__).resolve().parents[3] / "web"
    return web if (web / "package.json").is_file() else None


def assets_dir(cfg: Config) -> Path:
    configured = cfg.serve.get("assets_dir")
    return Path(configured).expanduser() if isinstance(configured, str) and configured.strip() else PACKAGED


def built(cfg: Config) -> bool:
    """Whether a web app build is there (its ``index.html``)."""
    return (assets_dir(cfg) / "index.html").is_file()


def _inside(base: Path, rel: str) -> Path | None:
    base = base.resolve()
    try:
        path = (base / rel).resolve()
    except (OSError, ValueError):
        return None
    if path != base and base not in path.parents:
        return None
    return path if path.is_file() else None


def lookup(cfg: Config, url_path: str) -> FileReply | None:
    """The file for a GET of ``url_path``, or None (404)."""
    rel = unquote(url_path).lstrip("/")
    if "\x00" in rel or any(part.startswith(".") for part in rel.split("/") if part):
        return None
    base = assets_dir(cfg) if built(cfg) else FALLBACK
    path = _inside(base, rel) if rel else None
    if path is None and "." not in rel.rsplit("/", 1)[-1]:
        path = _inside(base, "index.html")  # an app route: the client renders it
    if path is None:
        return None
    ctype = TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileReply(path, ctype, cache="no-cache")
