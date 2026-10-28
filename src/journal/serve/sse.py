"""``GET /api/v1/events``: a ``text/event-stream`` telling the web app what changed on disk.

The engine has no event bus: the tick, the gateway hook and the CLI write files. So this is a poller, every
``poll_seconds`` (2 s), over modification times only, never contents:

* ``entries-changed {"stem"}`` — an engine entry in the journal root was written, created or removed;
* ``ledger-changed {}`` — ``ledger.json`` (prompts sent, answered, folded);
* ``runs-changed {"id"}`` — a run file under ``state_dir/runs/`` (programs, WP4);
* ``library-changed {"id"}`` — a practice file in ``practices_dir``;
* ``tick {"at"}`` — ``last-tick.json`` (a tick finished).

A ``: ping`` comment every 25 s keeps an idle proxy connection open (a reverse proxy's idle timeout is typically a
few minutes). Each stream holds one server thread, so the app caps how many run at once.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .. import entries, ledger
from ..config import Config
from . import document


def _mtimes(directory: Path, pattern: str) -> dict[str, int]:
    out = {}
    try:
        it = os.scandir(directory)
    except OSError:
        return out
    with it:
        for e in it:
            if e.is_file() and Path(e.name).match(pattern) and not e.name.startswith("."):
                try:
                    out[e.name] = e.stat().st_mtime_ns
                except OSError:
                    continue
    return out


def _mtime(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _library(practices: Path) -> dict[str, int]:
    """Top-level ``<id>.md`` practices, plus each user program's files (``<program>/program.md``, its sessions),
    reported as one ``<program>.md`` (the directory's name) so the event's id is the program's."""
    out = _mtimes(practices, "*.md")
    if practices.is_dir():
        for sub in sorted(practices.iterdir()):
            if sub.is_dir() and not sub.name.startswith("."):
                files = {**_mtimes(sub, "*.md"),
                         **{f"sessions/{k}": v for k, v in _mtimes(sub / "sessions", "*.md").items()}}
                if files:  # one stamp for the lot, so adding, editing or removing any of its files changes it
                    out[f"{sub.name}.md"] = hash(tuple(sorted(files.items())))
    return out


def snapshot(cfg: Config) -> dict:
    runs: dict[str, int] = {}
    base = cfg.state_dir / "runs"
    if base.is_dir():
        for path in base.rglob("*.json"):
            m = _mtime(path)
            if m is not None:
                runs[path.stem] = m
    return {
        "entries": {k: v for k, v in _mtimes(cfg.root, "*.md").items() if document.STEM.match(k[:-3])},
        "ledger": _mtime(cfg.ledger_path),
        "runs": runs,
        "library": _library(Path(cfg.practices_dir)),
        "tick": _mtime(cfg.state_dir / "last-tick.json"),
    }


def changes(cfg: Config, before: dict, after: dict, known: set[str]) -> list[tuple[str, dict]]:
    """The events between two snapshots. ``known`` holds the stems seen to be engine entries, so a removed one
    (whose file can no longer say whose it is) is still reported, and his own files never are."""
    out = []
    old, new = before["entries"], after["entries"]
    for name in sorted(set(old) | set(new)):
        if old.get(name) == new.get(name):
            continue
        stem = name[:-3]
        if name in new:
            if entries.entry_key(cfg.root / name) and not entries.is_analysis(cfg.root / name):
                known.add(stem)
            elif stem not in known:
                continue
        elif stem not in known:
            continue
        else:
            known.discard(stem)
        out.append(("entries-changed", {"stem": stem}))
    if before["ledger"] != after["ledger"]:
        out.append(("ledger-changed", {}))
    for rid in sorted(set(before["runs"]) | set(after["runs"])):
        if before["runs"].get(rid) != after["runs"].get(rid):
            out.append(("runs-changed", {"id": rid}))
    for name in sorted(set(before["library"]) | set(after["library"])):
        if before["library"].get(name) != after["library"].get(name):
            out.append(("library-changed", {"id": name[:-3]}))
    if before["tick"] != after["tick"] and after["tick"] is not None:
        at = ledger.read_state(cfg.state_dir / "last-tick.json").get("at")
        out.append(("tick", {"at": at}))
    return out


def known_stems(cfg: Config, snap: dict) -> set[str]:
    """The stems of the engine's entries in a snapshot: what ``changes`` treats as known. Reflections and week
    reviews are not entries (the document route refuses them), so they are left out here exactly as ``changes``
    leaves them out when a file appears."""
    return {name[:-3] for name in snap["entries"]
            if entries.entry_key(cfg.root / name) and not entries.is_analysis(cfg.root / name)}


def frame(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


def stream(cfg_of, write, stop, *, poll_seconds: float = 2.0, ping_seconds: float = 25.0) -> None:
    """Write events until ``write`` fails (the client went away) or ``stop`` is set. ``cfg_of()`` gives the current
    configuration (a travel update can move nothing here, but the app reloads it anyway)."""
    cfg = cfg_of()
    prev = snapshot(cfg)
    known = known_stems(cfg, prev)
    write(b"retry: 5000\n: connected\n\n")
    last_ping = time.monotonic()
    while not stop.wait(poll_seconds):
        try:
            cfg = cfg_of()
        except Exception:  # noqa: BLE001 - a config that broke mid-stream: keep watching what was configured
            pass
        cur = snapshot(cfg)
        for event, data in changes(cfg, prev, cur, known):
            write(frame(event, data))
        prev = cur
        if time.monotonic() - last_ping >= ping_seconds:
            write(b": ping\n\n")
            last_ping = time.monotonic()
