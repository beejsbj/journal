"""Registers the journal's tools with Hermes. The specs and what each tool does are the engine's (``journal.tools``);
this adapter only loads the configuration, names the turn and hands the call to ``tools.call``, which refuses the
writes in an off-the-record turn. (``hooks.on_tool_call`` turns them away earlier with the same ``tools.refusal``;
``call`` checks again, so a hook that failed open does not let a write through.)

Hermes passes the handler the session (and, when it has one, the turn) as keyword arguments; a handler never raises.
"""
from __future__ import annotations

import json

from .. import tools
from . import runtime

TOOLSET = "journal"


def _handler(name: str):
    def handler(params, **kw) -> str:
        try:
            cfg = runtime.load_config()
        except Exception as e:
            return json.dumps({"success": False, "error": f"{type(e).__name__}: {e}"})
        turn = tools.ToolTurn(kw.get("session_id"), kw.get("turn_id"))
        return tools.call(cfg, name, params, turn, reload=runtime.load_config,
                          settings_writer=runtime.settings_writer())
    handler.__name__ = name
    return handler


journal_add_note = _handler("journal_add_note")
journal_set_location = _handler("journal_set_location")
journal_notebook = _handler("journal_notebook")
journal_program = _handler("journal_program")
HANDLERS = {h.__name__: h for h in (journal_notebook, journal_set_location, journal_add_note, journal_program)}


def register(ctx, names) -> None:
    for spec in tools.specs(names):
        ctx.register_tool(name=spec.name, toolset=TOOLSET, schema=spec.schema, handler=HANDLERS[spec.name],
                          description=spec.description, emoji=spec.emoji)
