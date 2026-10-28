"""What the gateway hook must remember between its callbacks, kept on disk.

The dispatch hook sees a message before any model runs; the LLM hook sees the turn it starts; the tool
guard sees the tools the companion calls in that turn. What the first knows and the later ones need (a voice
note waiting for its transcript, a context pack queued for one turn, a turn that took in an off-the-record
message) used to live in module-level dicts, so a gateway restart between two callbacks dropped it: the voice
note was never written, the pack never shown, and the turn's privacy rested on the database check alone.

It lives here now, in ``state_dir/hook-state.json`` under ``ledger.locked_state``: the file lock serialises the
gateway's threads and any other process, every record carries a deadline, and expired records are dropped on
every access. Sections: ``voice`` (key → pending voice record), ``packs`` (hook note → block), ``programs``
(hook note → which phase answer the turn carries), ``off_turns`` (session id → turn), ``stamps`` (plain values other
parts of the engine keep here: thread stamps). Failing
open is the caller's job; like every other state writer this module raises on an unwritable state directory.
"""
from __future__ import annotations

import contextlib
import json
import time
from pathlib import Path

from . import ledger
from .config import Config

FILE = "hook-state.json"
VOICE_TTL = 900  # a transcript that hasn't arrived in 15 minutes isn't coming
PACK_TTL = 900  # the pack is for the turn the capture started; a later turn mustn't get it
OFF_TURN_TTL = 3600  # a turn marked off the record stays so for an hour at most
SECTIONS = ("voice", "packs", "programs", "off_turns")


def path(cfg: Config) -> Path:
    return cfg.state_dir / FILE


def _alive(record, now: float) -> bool:
    """A record with a numeric deadline still ahead. A deadline that isn't a number (a hand edit, another
    writer's slip) counts as expired, so one bad record is dropped on the next access instead of making every
    access raise, which the privacy guard would read as "refuse every journal write" until someone edits the file."""
    if not isinstance(record, dict):
        return False
    try:
        return float(record.get("deadline") or 0) >= now
    except (TypeError, ValueError):
        return False


def _tidy(state: dict, now: float) -> dict:
    """Every section a dict; every expired record gone. Mutates and returns ``state``."""
    for name in SECTIONS:
        section = state.get(name)
        if not isinstance(section, dict):
            section = {}
        state[name] = {k: v for k, v in section.items() if _alive(v, now)}
    if not isinstance(state.get("stamps"), dict):
        state["stamps"] = {}
    return state


@contextlib.contextmanager
def locked(cfg: Config, now: float | None = None):
    """The whole state, tidied, under the file lock; written back atomically on clean exit if it changed."""
    with ledger.locked_state(path(cfg)) as state:
        yield _tidy(state, time.time() if now is None else now)


def snapshot(cfg: Config, now: float | None = None) -> dict:
    """A read-only view (expired records left out; nothing written)."""
    return _tidy(ledger.read_state(path(cfg)), time.time() if now is None else now)


# ── pending voice notes ──────────────────────────────────────────────────────

def remember_voice(cfg: Config, key: str, record: dict, now: float | None = None) -> None:
    """A voice note matched at dispatch, to be written once Hermes has transcribed it (``take_voice``)."""
    now = time.time() if now is None else now
    with locked(cfg, now) as state:
        state["voice"][key] = {**record, "deadline": now + VOICE_TTL}


def take_voice(cfg: Config, message: str, now: float | None = None) -> dict | None:
    """The pending voice note whose hook note is in this turn's message, removed; the oldest first when two
    notes share a note text (two replies to the same prompt), so each finds its own transcript."""
    with locked(cfg, now) as state:
        key = next((k for k, v in sorted(state["voice"].items(), key=lambda kv: kv[1]["deadline"])
                    if v.get("note") and v["note"] in (message or "")), None)
        return state["voice"].pop(key, None) if key is not None else None


# ── pending context packs ────────────────────────────────────────────────────

def remember_pack(cfg: Config, note: str, block: str, now: float | None = None) -> None:
    """The context block for the one turn whose message carries ``note``."""
    now = time.time() if now is None else now
    with locked(cfg, now) as state:
        state["packs"][note] = {"block": block, "deadline": now + PACK_TTL}


def take_pack(cfg: Config, message: str, now: float | None = None) -> str | None:
    with locked(cfg, now) as state:
        key = next((k for k in state["packs"] if k in (message or "")), None)
        pend = state["packs"].pop(key, None) if key is not None else None
        return pend["block"] if pend else None


# ── pending program blocks ───────────────────────────────────────────────────

def remember_program(cfg: Config, note: str, ref: dict, now: float | None = None) -> None:
    """The phase answer a turn carries, for the one turn whose message holds ``note``. Only the pointer is kept
    (run, session, phase, prompt): the block is read from the entry when the turn starts, so a voice note that was
    transcribed in between is in it."""
    now = time.time() if now is None else now
    with locked(cfg, now) as state:
        state["programs"][note] = {**ref, "deadline": now + PACK_TTL}


def take_program(cfg: Config, message: str, now: float | None = None) -> dict | None:
    """The pointer queued for this turn (its hook note is in the message), removed: the block comes once."""
    with locked(cfg, now) as state:
        key = next((k for k in state["programs"] if k in (message or "")), None)
        pend = state["programs"].pop(key, None) if key is not None else None
        return {k: v for k, v in pend.items() if k != "deadline"} if pend else None


# ── off-the-record turns ─────────────────────────────────────────────────────

def mark_off_turn(cfg: Config, session_id: str, turn_id: str | None, now: float | None = None) -> None:
    """This session's current turn took in an off-the-record message: the journal's writing tools refuse it."""
    now = time.time() if now is None else now
    with locked(cfg, now) as state:
        state["off_turns"][str(session_id)] = {"turn": str(turn_id or ""), "deadline": now + OFF_TURN_TTL}


def clear_off_turn(cfg: Config, session_id: str, now: float | None = None) -> None:
    """A public turn of the session clears its mark."""
    with locked(cfg, now) as state:
        state["off_turns"].pop(str(session_id), None)


def off_turn(cfg: Config, session_id: str, now: float | None = None) -> dict | None:
    """The live off-record mark for a session (``{"turn", "deadline"}``), or None."""
    return snapshot(cfg, now)["off_turns"].get(str(session_id or ""))


# ── stamps ───────────────────────────────────────────────────────────────────

def stamp(cfg: Config, **values) -> dict:
    """Set plain values (thread stamps such as ``last_his``); returns all stamps."""
    with locked(cfg) as state:
        state["stamps"].update(values)
        return dict(state["stamps"])


def stamps(cfg: Config) -> dict:
    return snapshot(cfg)["stamps"]


# ── for status and doctor ────────────────────────────────────────────────────

def summary(cfg: Config, now: float | None = None) -> dict:
    """Counts per section, and whether the file (if any) could be read: a file that exists but isn't JSON
    means records were lost, which ``read_state`` otherwise hides by starting over."""
    p = path(cfg)
    readable = True
    if p.exists():
        try:
            readable = isinstance(json.loads(p.read_text(encoding="utf-8")), dict)
        except (OSError, ValueError):  # not JSON, or not UTF-8
            readable = False
    state = snapshot(cfg, now)
    return {"path": str(p), "exists": p.exists(), "readable": readable,
            "pending_voice": len(state["voice"]), "pending_packs": len(state["packs"]),
            "pending_programs": len(state["programs"]),
            "off_turns": len(state["off_turns"])}
