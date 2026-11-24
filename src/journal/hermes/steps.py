"""The engine's own one-shots as the plugin sees them: whether a turn is one, and the search bound it carries.

A model step (an opener, an annotation, a reflection, a picture's description, the outage probe) is the engine's own
run of ``hermes chat --oneshot``. It must see only the query the engine wrote: ``hooks.on_llm_call`` adds no
notebook, pack or today block to it, and a reflection's ``journal_search`` finds nothing dated after its period.

Every run carries both facts in two places (``agent.CommandCompanion.ask``): ``JOURNAL_MODEL_STEP=1`` and
``JOURNAL_SEARCH_UPTO`` in its environment, and the same in its query's first line (``agent.step_mark``). The
environment arrives only where the one-shot is its own process (Hermes before its gateway ran every one-shot). A
Hermes whose gateway runs the one-shot keeps the client's environment per session, filtered to an allowlist of its own
(``agent/session_client_env.py``), and never sets it in the gateway's ``os.environ``: there only the query arrives.
So a turn is the engine's step when any of these holds (``is_step``):

* its user message carries the mark (any Hermes, any process: the engine wrote it);
* ``JOURNAL_MODEL_STEP=1`` is in this process's environment (a one-shot that is its own process);
* Hermes says the turn is a finite one (``gateway.session_finite.finite_turn_required()``: ``hermes chat -q``,
  ``--oneshot``, ``-z``), whoever ran it: no one-shot is a conversation of hers;
* the user message cannot be read at all (fails closed: no context is added to a turn the hook cannot place).

Her chat is none of these: a message from a person on a gateway platform, or an interactive ``hermes`` session, is
never finite and carries no mark (a line of his that copies the mark exactly is treated as a step: no context that
turn, the safe side).

The search bound goes from the hook to the tool by the turn's ids (``note`` keeps it, ``search_upto`` reads it), in
this process's memory: Hermes runs a turn's ``pre_llm_call`` hook and its tool calls in the same process; the hook gets
the session id and the turn id, a plugin tool only the session id (so the bound is found by session there). A bound is
kept only where Hermes does not say the turn is a chat turn (``finite_turn() is not False``), and any turn without a
mark drops what was kept for its ids: a line of his that copies the mark never leaves a bound on her later searches. A one-shot's mark with no bound is kept as "no bound". In a finite turn whose
mark the hook never saw (a hook that failed, ids that do not match), ``journal_search`` refuses rather than search
with no bound: a reflection written late must not read what came after its period (``hermes/tools.py``).
"""
from __future__ import annotations

import os
import threading
from collections import OrderedDict

from .. import agent

KEEP = 64  # ids remembered (a one-shot is one turn; a few run at once at most)
_bounds: OrderedDict[str, str | None] = OrderedDict()
_lock = threading.Lock()


def finite_turn() -> bool | None:
    """Hermes' own word on whether the turn running here is a finite one-shot: True, False, or None when this Hermes
    has no such fact (it predates its gateway running one-shots, or the plugin is not under Hermes)."""
    try:
        from gateway.session_finite import finite_turn_required
        value = finite_turn_required()
    except Exception:
        return None
    return value if isinstance(value, bool) else None


def readable(user_message) -> bool:
    return isinstance(user_message, (str, list, tuple))


def note(user_message, session_id=None, turn_id=None) -> bool:
    """Whether this turn is the engine's own model step (see the module's docstring), and, when it is, keep the
    search bound its mark names for ``search_upto``. Never raises."""
    try:
        marked, upto = agent.read_step_mark(user_message)
    except Exception:
        marked, upto = False, None
    finite = finite_turn()
    if marked and finite is not False:
        _keep(session_id, turn_id, upto)
    else:  # her chat, or a turn with no mark: nothing kept for its ids, so no bound carries into a later turn
        _drop(session_id, turn_id)
    if marked or os.environ.get(agent.MODEL_STEP_ENV) == "1" or not readable(user_message):
        return True
    return finite is True


def search_upto(session_id=None, turn_id=None) -> tuple[bool, str | None]:
    """``(known, bound)``: whether the hook saw this turn's mark (by its turn id, else its session id), and the
    search bound it named (None: no bound)."""
    with _lock:
        for key in _keys(session_id, turn_id):
            if key in _bounds:
                return True, _bounds[key]
    return False, None


def _keys(session_id, turn_id) -> list[str]:
    return [k for k in (f"turn:{turn_id}" if turn_id else "", f"session:{session_id}" if session_id else "") if k]


def _drop(session_id, turn_id) -> None:
    with _lock:
        for key in _keys(session_id, turn_id):
            _bounds.pop(key, None)


def _keep(session_id, turn_id, upto: str | None) -> None:
    with _lock:
        for key in _keys(session_id, turn_id):
            _bounds[key] = upto
            _bounds.move_to_end(key)
        while len(_bounds) > KEEP:
            _bounds.popitem(last=False)
