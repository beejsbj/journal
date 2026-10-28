"""What the companion is told on one turn, and the budget that keeps it short.

The blocks, in the order she reads them: her private notebook (every turn, unchanged), then at most one of the
context pack of a journal reply (``hookstate`` queued it when the reply was captured), the program block of a
turn that carries one of his phase answers (a phase has no opener, so no pack: the block takes its seat), and, at
the start of a thread, the today block. ``BUDGET`` is the rule in code: two blocks a turn, and the today block
never on a turn that already carries a pack or a program block (it waits for the next thread start, not for the
next turn).

Nothing here reads the chat transcript or calls a source; every piece comes from state the tick and the
hooks already wrote. Each piece fails on its own: a notebook that cannot be read costs the notebook, not the pack.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from . import channel, hookstate, notebook, programview, today
from .config import Config

log = logging.getLogger("journal.turncontext")

BUDGET = {"blocks": 2, "today_with_pack": False}


@dataclass(frozen=True)
class Turn:
    """One model turn as the hook sees it."""
    platform: str
    session_id: str | None
    turn_id: str | None
    user_message: str
    at: datetime  # timezone-aware
    chat_id: str | None = None   # the turn's chat and sender, when the adapter knows them (``channel.turn_ours``)
    user_id: str | None = None


def build(cfg: Config, turn: Turn) -> str | None:
    """The context for ``turn``: the blocks joined by blank lines, or None when there are none."""
    blocks: list[str] = []
    try:
        block = notebook.render(cfg, turn.at.astimezone(cfg.tz).date().isoformat())
        if block:
            blocks.append(block)
    except Exception:
        log.exception("journal notebook injection failed")
    # the journal's chat; a CLI or cron turn, or one in another chat, is not a thread of his
    chat = channel.turn_ours(cfg, turn.platform, turn.chat_id, turn.user_id)
    primary = (_program(cfg, turn) or _pack(cfg, turn)) if chat else None
    if primary:
        blocks.append(primary)
    if chat:
        block = _today(cfg, turn, with_pack=bool(primary))
        if block and (not primary or BUDGET["today_with_pack"]) and len(blocks) < BUDGET["blocks"]:
            blocks.append(block)
    return "\n\n".join(blocks[:BUDGET["blocks"]]) if blocks else None


def _program(cfg: Config, turn: Turn) -> str | None:
    """The program block for a turn carrying a phase answer (the hook queued which one), once. A pointer that
    cannot be read costs the block: the turn goes on without it (and may then take the today block)."""
    try:
        ref = hookstate.take_program(cfg, turn.user_message or "")
        return programview.block(cfg, ref, turn.at) if ref else None
    except Exception:
        log.exception("journal: could not build the program block")
        return None


def _pack(cfg: Config, turn: Turn) -> str | None:
    """The pack queued for this turn (its hook note is in the message), once."""
    try:
        return hookstate.take_pack(cfg, turn.user_message or "")
    except Exception:
        log.exception("journal: could not read the queued context pack")
        return None


def _today(cfg: Config, turn: Turn, with_pack: bool) -> str | None:
    try:
        if not today.begin(cfg, turn.at, allow=BUDGET["today_with_pack"] or not with_pack):
            return None
        block = today.render(cfg, turn.at)
        if block:
            today.note_injected(cfg, turn.at)
        else:
            today.note_empty(cfg)
        return block or None
    except Exception:  # fail open: no block
        log.exception("journal: could not build the today block")
        return None
