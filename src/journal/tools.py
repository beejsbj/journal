"""The companion's tools, independent of any agent: the specs an adapter registers and ``call``, which runs one.

``call(cfg, name, params, turn)`` is the only way a tool reaches the journal. It refuses the writes (the companion's
own notes, the notebook's add and resolve, a move by ``journal_set_location``) in an off-the-record turn before the handler runs, and reads (the
notebook's list, ``journal_program``) are allowed there. The refusal fails closed: when it cannot tell whether the
turn is off the record, nothing is written. An adapter that also has a hook ahead of the call (Hermes'
``pre_tool_call``) uses ``refusal`` there to turn a tool away early; ``call`` checks again, so a hook that fails
open does not let a write through.

Handlers return JSON strings and never raise (Hermes' tool contract, kept as the neutral one).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from . import capture, hookstate, location, notebook, privacy, programview
from .config import Config, Names

log = logging.getLogger("journal.tools")
WRITES: dict[str, tuple[str, ...] | None] = {"journal_add_note": None, "journal_notebook": ("add", "resolve"),
                                             "journal_set_location": None}
# (tool → the actions that write; None: every call writes. journal_program has no write action: status and propose
# are reads, allowed in an off-record turn. Off the record means nothing in the journal changes, a move included)


@dataclass(frozen=True)
class ToolTurn:
    """The turn a tool call runs in, as far as the adapter knows it (``turncontext.Turn`` also fits)."""
    session_id: str | None = None
    turn_id: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    name: str
    schema: dict
    description: str
    emoji: str = ""


def note_schema(n: Names) -> dict:
    return {
        "name": "journal_add_note",
        "description": (
            f"Add your own note to {n.user_possessive} journal: an observation, a connection you found while "
            "reading their records, or something from conversation worth keeping. It is appended, timestamped, "
            f"under \"{n.companion}'s notes\" in that day's entry and never changes {n.possessive} words or the "
            "annotations. Use it when something is genuinely worth keeping, not for every turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "note": {"type": "string", "description": "The note, in your voice. Mark inference as inference."},
                "date": {"type": "string", "description": "Local date YYYY-MM-DD of the entry; defaults to today."},
                "kind": {"type": "string",
                         "description": "Which entry of that day: a practice id such as morning, evening, midweek "
                                        "or weekly, or note (a freeform /j note); defaults to the most recent "
                                        "existing one. An id the journal doesn't know is refused."},
            },
            "required": ["note"],
        },
    }


def location_schema(n: Names) -> dict:
    who = n.user or "the user"
    return {
        "name": "journal_set_location",
        "description": (
            f"Update where {who} is, when you're told about a move or a trip. Looks the "
            "place up, then sets the journal's location, timezone and weather coordinates; prompts follow the new "
            "local time automatically. Confirm the place first if it's ambiguous. Returns the new local schedule."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "place": {"type": "string", "description": "City or town, e.g. 'Lisbon' or 'Chicago, United States'."},
                "timezone": {"type": "string", "description": "Optional IANA timezone override, e.g. 'Asia/Kolkata'."},
            },
            "required": ["place"],
        },
    }


def notebook_schema(n: Names) -> dict:
    who = n.user or "them"
    return {
        "name": "journal_notebook",
        "description": (
            f"Your private notebook (only you see it; it is not in the journal). Use it for threads you mean to "
            f"pick up with {who} later: 'ask how the conversation with X went', 'they wanted to try Y'. Open items "
            "are shown to you each turn. action=add with text (optional bring_up_after YYYY-MM-DD, optional source); "
            "action=resolve with id once it has come up or stopped mattering; action=list to see everything open."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["add", "resolve", "list"]},
                "text": {"type": "string"},
                "id": {"type": "string"},
                "outcome": {"type": "string", "description": "For resolve: what happened, briefly."},
                "bring_up_after": {"type": "string"},
                "source": {"type": "string", "description": "Where it came from, e.g. an entry name."},
            },
            "required": ["action"],
        },
    }


def program_schema(n: Names) -> dict:
    who = n.user or "them"
    return {
        "name": "journal_program",
        "description": (
            f"Read the journal's programs: guided multi-session runs and situational sessions that the engine sends "
            f"to {who} phase by phase. action=status shows the programs available and any run in progress (what "
            f"/program shows). action=propose with program (an id) returns the /start line you may offer {who}; it "
            f"starts nothing. You cannot start, advance, skip or edit a program: the engine sends the questions as "
            f"authored, and {n.subject} moves it on with /next, /skip, /back and /break."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["status", "propose"]},
                "program": {"type": "string", "description": "For propose: the program's id or alias."},
            },
            "required": ["action"],
        },
    }


def specs(names: Names) -> tuple[ToolSpec, ...]:
    """The tools to register, in registration order, worded with this journal's names."""
    return (
        ToolSpec("journal_notebook", notebook_schema(names), "Private notebook of threads to pick up later.", "📓"),
        ToolSpec("journal_set_location", location_schema(names),
                 "Move the journal to where the user is now (location, timezone, weather).", "🧭"),
        ToolSpec("journal_add_note", note_schema(names), "Append the companion's own note to a journal entry.",
                 names.emoji or "📝"),
        ToolSpec("journal_program", program_schema(names),
                 "Read the journal's programs: what is available and running; propose a /start line.", "📚"),
    )


def _off_record_turn(cfg: Config, turn: ToolTurn | None) -> bool:
    """Whether this call runs in an off-the-record turn: the turn the adapter marked (first signal), or the privacy
    pass's end state over the chat (second signal: also messages steered into a running turn without the hook): her
    turn took in a private row, or a bare-/x window is open. A pass that can't run says yes."""
    session_id = getattr(turn, "session_id", None)
    turn_id = getattr(turn, "turn_id", None)
    rec = hookstate.off_turn(cfg, str(session_id or ""))
    if rec and (not rec["turn"] or not turn_id or rec["turn"] == str(turn_id)):
        return True
    now = datetime.now(timezone.utc)
    pv = privacy.view(cfg, now)
    return pv.tainted() or pv.open_at(now)


def refusal(cfg: Config, name: str, params: dict, turn: ToolTurn | None = None) -> str | None:
    """The refusal for a journal-writing tool in an off-the-record turn, or None. If the check itself fails, the
    write is refused (privacy fails closed)."""
    if name not in WRITES:
        return None
    actions = WRITES[name]
    if actions is not None and str((params or {}).get("action") or "list") not in actions:
        return None  # reading the notebook is fine
    try:
        off = _off_record_turn(cfg, turn)
    except Exception:
        log.exception("journal: could not tell whether this turn is off the record; refusing the write")
        return ("The journal couldn't check whether this conversation is off the record, so nothing was saved. "
                "Try again later.")
    if not off:
        return None
    who = cfg.names.user or "the user"
    if name == "journal_set_location":
        return (f"Off the record: {who} marked this conversation /x, so nothing in the journal changes, a move "
                f"included. The location and timezone were not changed; suggest {cfg.names.object} says it on the "
                "record when it should count.")
    return (f"Off the record: {who} marked this conversation /x, so nothing from it can go into the journal or "
            f"your notebook. Nothing was saved; tell {cfg.names.object} you've kept it out if it matters.")


def _fail(e: Exception) -> str:
    return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})


def _add_note(cfg: Config, params: dict, **_) -> str:
    path = capture.add_companion_note(cfg, str(params.get("note", "")), datetime.now(timezone.utc),
                                      params.get("date") or None, params.get("kind") or None)
    return json.dumps({"success": True, "path": path})


def _set_location(cfg: Config, params: dict, *, reload: Callable | None = None,
                  settings_writer: Callable | None = None, **_) -> str:
    result = location.set_location(cfg, str(params.get("place", "")).strip(), params.get("timezone") or None,
                                   reload=reload, hermes_writer=settings_writer)
    return json.dumps({"success": True, **result})


def _notebook(cfg: Config, params: dict, **_) -> str:
    action = str(params.get("action", "list"))
    if action == "add":
        return json.dumps({"success": True, "item": notebook.add(cfg, str(params.get("text", "")),
                           str(params.get("source", "")), params.get("bring_up_after") or None)})
    if action == "resolve":
        return json.dumps({"success": True, "item": notebook.resolve(cfg, str(params.get("id", "")),
                                                                     str(params.get("outcome", "")))})
    return json.dumps({"success": True, "open": notebook.open_items(cfg)})


def _program(cfg: Config, params: dict, **_) -> str:
    """Both actions are reads (no run state, ledger or entry is touched), so they work in an off-record turn."""
    action = str(params.get("action") or "status")
    if action == "status":
        return json.dumps({"success": True, **programview.status(cfg)}, ensure_ascii=False)
    if action == "propose":
        return json.dumps(programview.propose(cfg, params.get("program") or None), ensure_ascii=False)
    return json.dumps({"success": False, "error": f"unknown action {action!r}: use status or propose"})


HANDLERS: dict[str, Callable[..., str]] = {"journal_add_note": _add_note, "journal_set_location": _set_location,
                                           "journal_notebook": _notebook, "journal_program": _program}


def call(cfg: Config, name: str, params: dict | None, turn: ToolTurn | None = None, **env: Any) -> str:
    """Run tool ``name`` for the companion and return its JSON result; never raises. ``env`` is what only the
    adapter can supply (``reload``, ``settings_writer`` for ``journal_set_location``)."""
    try:
        params = params if isinstance(params, dict) else {}
        handler = HANDLERS.get(name)
        if handler is None:
            return json.dumps({"success": False, "error": f"unknown tool {name!r}"})
        why = refusal(cfg, name, params, turn)
        if why:
            log.warning("journal: %s refused in an off-the-record turn", name)
            return json.dumps({"success": False, "off_record": True, "error": why})
        return handler(cfg, params, **env)
    except Exception as e:
        return _fail(e)
