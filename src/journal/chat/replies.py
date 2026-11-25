"""The chat face's annotation replies: each new annotation goes to the chat once, as a reply to the message it
annotates (``reply_annotations``), paced, skipped once stale or with no chat, and held for the catch-up letter after an
outage (``recovery.held``).

Moved out of ``tick.py`` unchanged (issue #43, CHAT-1); ``tick`` still exports every name here."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .. import channel, compose, entries, ledger, mirror, outage, recovery
from ..config import Config
from ..ports import ChannelError
from .deliver import Delivery, Mirror, deliver

REPLY_ATTEMPTS = 6


def _reply_target(p: dict) -> str | None:
    """The captured message the annotations answer: the first capture that has a Telegram message id (digits; a
    capture from anywhere else has none, and the annotations then go to the chat unthreaded)."""
    for c in p.get("captures") or []:
        mid = str(c.get("platform_message_id") or "")
        if mid.isdigit():
            return mid
    return None


def _reply_owed(p: dict) -> bool:
    """An annotation reply still to send: annotated, not sent, not skipped, attempts left."""
    reply = p.get("annotation_reply") or {}
    return bool(p.get("annotated_at")) and not reply.get("message_id") and not reply.get("skipped") \
        and reply.get("attempts", 0) < REPLY_ATTEMPTS


def _skip_annotation_replies(cfg: Config, now: datetime, log: list[str]) -> None:
    """No chat (``channel.platform: none``): each reply not sent is marked skipped, ``no chat``, and never sent,
    also once a chat is configured later and replies are on (the owner, 2026-11-09). The annotations stay in the entry;
    ``standing`` tells the companion how many there were, never their words. Nothing is attempted (rule 6)."""
    skipped = 0
    with ledger.locked(cfg.ledger_path) as data:
        for p in data["prompts"]:
            if _reply_owed(p):
                p.setdefault("annotation_reply", {}).update(skipped="no chat", skipped_at=now.isoformat())
                skipped += 1
    if skipped:
        log.append(f"no chat: {skipped} annotation repl{'y' if skipped == 1 else 'ies'} skipped (in the entries)")


def _newest_words(p: dict) -> datetime | None:
    """When his newest words in a prompt's captures were written (what an annotation reply answers)."""
    times = []
    for c in p.get("captures") or []:
        try:
            times.append(datetime.fromisoformat(c["at"]))
        except (KeyError, TypeError, ValueError):
            continue
    return max(times, default=None)


def _skip_stale_reply(cfg: Config, p: dict, now: datetime, newest: datetime, log: list[str]) -> None:
    """An annotation reply that would come more than ``recovery.stale_hours`` after his words is not sent: it is
    marked skipped, ``stale``, and stays in the entry (after an outage, or a tick that did not run)."""
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, p["id"])
        if q is None:
            return
        q.setdefault("annotation_reply", {}).update(skipped="stale", skipped_at=now.isoformat())
    hours = (now - newest).total_seconds() / 3600
    log.append(f"{p['id']}: annotation reply not sent: stale ({hours:.0f} h after {cfg.names.possessive} words; "
               "the annotations are in the entry)")


def _reply_account(cfg: Config, p: dict, now: datetime, text: str, d: Delivery, log: list[str]) -> Mirror | None:
    """One annotation reply's account (``deliver``'s ``record``): sent, its ``annotation_reply`` gets the message;
    not, an attempt and the error. What the companion is told when it was sent; nothing when the prompt is gone."""
    mid, error = d.message_id, None if d.error is None else str(d.error)
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, p["id"])
        if q is None:
            return None
        r = q.setdefault("annotation_reply", {})
        if mid:
            r.update(message_id=mid, sent_at=now.isoformat(), reply_to=_reply_target(q))
            r.pop("error", None)
        else:
            r["attempts"] = r.get("attempts", 0) + 1
            r["error"] = (error or "")[:300]
    if not mid:
        log.append(f"{p['id']}: annotation reply failed ({error})")
        return None
    log.append(f"replied to {p['id']} with its annotations")
    what = (f"{cfg.names.companion}'s annotations on {Path(p.get('entry_path') or p['id']).stem}, sent at "
            f"{mirror.stamp(cfg, now)} as a reply to {cfg.names.possessive} entry")
    return Mirror(what, text, ("prompts", p["id"], "annotation_reply"))


def reply_annotations(cfg: Config, now: datetime, log: list[str]) -> None:
    """Send each new annotation to the chat as a reply to the message it annotates (as a message of its own where the
    platform has no replies, or the prompt was made on another platform); exactly once.

    Oldest first, within the chat's pace (``outage.chat_room``: a few a tick, a few an hour; the rest wait for a later
    tick), and never later than ``recovery.stale_hours`` after his words (``_skip_stale_reply``). After an outage, the
    replies to what he wrote while she was away are not sent one by one: one catch-up message goes in their place
    (``recovery.held``; they are sent as before when it cannot be written)."""
    if not cfg.has_channel:   # (also with replies switched off: turned on later, they must not send the backlog)
        _skip_annotation_replies(cfg, now, log)
        return
    if not cfg.telegram.get("annotation_replies", True):
        return
    chat = None
    owed = [p for p in ledger.snapshot(cfg.ledger_path)["prompts"] if _reply_owed(p)]
    held = recovery.held(cfg, now) if owed else set()  # (after an outage: one letter in place of these, ``recovery``)
    if held & {p["id"] for p in owed}:
        n = len(held & {p["id"] for p in owed})
        log.append(f"{n} annotation repl{'y waits' if n == 1 else 'ies wait'} for the catch-up message after the outage")
        owed = [p for p in owed if p["id"] not in held]
    owed.sort(key=lambda p: _newest_words(p) or datetime.fromisoformat(p.get("annotated_at") or now.isoformat()))
    room, waiting = outage.chat_room(cfg, now), 0
    for p in owed:
        newest = _newest_words(p)
        if newest is not None and now - newest > outage.stale_after(cfg):
            _skip_stale_reply(cfg, p, now, newest, log)
            continue
        if room <= 0:
            waiting += 1
            continue
        path = Path(p.get("entry_path") or "")
        text = entries.annotations(path.read_text(encoding="utf-8")) if path.is_file() else ""
        if not text:  # nothing to send: the attempt is counted all the same
            told = _reply_account(cfg, p, now, text, Delivery(error="no annotations in the entry"), log)
        else:
            here = channel.same_chat(cfg, p)  # (a prompt made before the channel moved: no reply into the old chat)
            d = deliver(cfg, compose.annotation_html(p["kind"], text, cfg.names), log, chat=chat,
                        to=(p.get("chat_id") or cfg.chat_id) if here else cfg.chat_id,
                        reply_to=_reply_target(p) if here else None,
                        catch=(ChannelError, ValueError),  # one prompt's bad reply never stops the others'
                        record=lambda d, p=p, text=text: _reply_account(cfg, p, now, text, d, log))
            chat, told = (d.chat if d.chat is not None else chat), d.told
        if told is not None:
            room -= 1
    if waiting:
        log.append(outage.pace_note(cfg, waiting, "annotation repl" + ("y" if waiting == 1 else "ies")))
