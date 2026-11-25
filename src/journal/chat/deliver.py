"""``deliver``: the chat face's one operation for "send this to the journal's chat, record it, mirror it" (issue #43,
CHAT-2).

Every message the engine sends on its own goes through it: a prompt (``send.send_prompt``: practices, program phases,
interrupts), an annotation reply (``replies.reply_annotations``), a reflection or week review (``reflect.send``), the
catch-up letter after an outage (``recovery._send``) and a program session's summary (``programs._send_summary``).
The three steps, always in this order:

1. **Send** ``html`` to ``to`` (default the configured chat) through ``chat`` (default ``cfg.channel()``, made inside
   the send's ``try``), with the platform keywords as given (``reply_to``, ``keyboard``, ``silent``: passed only when
   the caller passes them). An error of a class in ``catch`` (default ``ChannelError``) is caught and handed on;
   anything else propagates. ``fallback(error)``, when given, is asked once after a ``ChannelError`` for a second text
   to send at once (a prompt's plain opener); its own failure is the one handed on.
2. **Record**: ``record(delivery)`` keeps the caller's account (each kind keeps its own: a ledger prompt record, an
   ``annotation_reply``, a reflection's send slot, a ``recovery`` record, a program run's session), writes its own log
   lines, and returns what to tell the companion (``Mirror``), or ``None`` to tell her nothing (a failure, or a record
   that has gone). It may raise (``SendFailed``): that propagates.
3. **Mirror**: when ``record`` returned a ``Mirror``, ``mirror.to_chat`` frames it into the companion's chat session,
   after the caller's log lines.

What stays with the callers: whether anything is owed at all (``channel.platform: none``, ``/x``, pacing and the outage
breaker's chat room, staleness, the catch-up letter's hold), and the texts. ``deliver`` decides none of those, so moving a
kind of send onto it changes no rule; with no chat a caller never reaches it, and a send that did would be refused by
``ports.NoChannel`` like any other.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, NamedTuple

from .. import mirror
from ..config import Config
from ..ports import ChannelError


class Mirror(NamedTuple):
    """What the companion is told of a delivery (``mirror.to_chat``): ``what`` was sent, its ``body``, and the ledger
    ``ref`` the mirror's outcome is recorded on (``None``: not recorded)."""
    what: str
    body: str
    ref: tuple | None = None


@dataclass
class Delivery:
    """One send's outcome, as ``record`` sees it. ``message_id``: what the chat gave back (``None``: no send went
    through). ``error``: what stopped it (an exception from the send, or the caller's own reason when it had nothing to
    send). ``chat``: the channel it went through, for a caller that edits after (a re-send's refold) or sends again.
    ``told``: what ``record`` returned, so the caller knows whether the companion was told."""
    message_id: Any = None
    error: Exception | str | None = None
    chat: Any = None
    told: Mirror | None = None


def deliver(cfg: Config, html: str, log: list[str], *, record: Callable[[Delivery], Mirror | None], to=None,
            chat=None, catch: tuple[type[BaseException], ...] = (ChannelError,),
            fallback: Callable[[ChannelError], str] | None = None, **send) -> Delivery:
    """Send ``html`` to the chat, let ``record`` keep the account, mirror what it returns (the module's docstring).
    Returns the ``Delivery``."""
    d = Delivery(chat=chat)
    try:
        if d.chat is None:
            d.chat = cfg.channel()
        try:
            d.message_id = d.chat.send(cfg.chat_id if to is None else to, html, **send)
        except ChannelError as e:
            if fallback is None:
                raise
            html = fallback(e)
            d.message_id = d.chat.send(cfg.chat_id if to is None else to, html, **send)
    except catch as e:
        d.error = e
    d.told = record(d)
    if d.told is not None:
        what, body, ref = d.told
        mirror.to_chat(cfg, what, body, log, **({"ref": ref} if ref is not None else {}))
    return d
