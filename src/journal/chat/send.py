"""The chat face's prompt delivery: ``send_prompt`` sends one prepared prompt record to the chat, keeps the ledger's
account of it (``count_send_failure``, re-sends, ``_attach`` onto a record his words on a face made), appends it to
``sent.jsonl`` (``_archive_sent``, pruned by ``prune_sent``) and mirrors it into the companion's chat session.

Moved out of ``tick.py`` unchanged (issue #43, CHAT-1); ``tick`` still exports every name here."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from .. import channel, ledger, mirror, today
from ..alert import Alert
from ..config import Config
from ..ports import ChannelError, Keyboard
from .deliver import Delivery, Mirror, deliver
from .fold import FOLD_STATE

SEND_ATTEMPTS = 3
EARLIER_OPENERS = 5  # the openers of earlier sends of one prompt a record keeps (so a quote of a folded one matches)


class SendFailed(Alert):
    """A prompt could not be delivered after SEND_ATTEMPTS ticks."""



SENT_FIELDS = ("id", "kind", "local_date", "scheduled", "sent_at", "sent_at_local", "chat_id", "message_id",
               "previous_message_ids", "opener", "opener_source", "asked")


def _archive_sent(cfg: Config, record: dict) -> None:
    """Append every delivered prompt to ``sent.jsonl`` (pruned with the ledger, to KEEP_DAYS): the engine's
    own memory of what it sent, not an archive. The journal never holds an unanswered prompt's opener."""
    try:
        path = cfg.state_dir / "sent.jsonl"
        line = json.dumps({k: record[k] for k in SENT_FIELDS if k in record}, ensure_ascii=False)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # the ledger still has it; never fail a delivered send over the archive


def count_send_failure(cfg: Config, record: dict, error: Exception, log: list[str]) -> int:
    """A send of ``record`` failed: count the attempt on its ledger entry (created with ``scheduled`` so prune keeps
    the count) and raise ``SendFailed`` on the ``SEND_ATTEMPTS``-th. Returns the attempts so far."""
    pid = record["id"]
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, pid)
        # answered on a face while the send went out (captures, never delivered): nothing is owed, so nothing is
        # counted. A delivered record (a re-send) counts as it always did.
        answered = bool(q and q.get("captures") and not q.get("message_id"))
        if answered:
            pass
        elif q is None:  # "scheduled" keeps prune() from dropping the attempt count
            q = channel.stamp(cfg, {"id": pid, "kind": record["kind"], "local_date": record["local_date"],
                                    "chat_id": cfg.chat_id, "scheduled": record["scheduled"]})
            data["prompts"].append(q)
        if not answered:
            q["send_attempts"] = q.get("send_attempts", 0) + 1
            q["send_error"] = str(error)[:300]
        attempts = (q or {}).get("send_attempts", 0)
    if answered:
        log.append(f"{pid}: send failed ({error}); not counted: it was answered meanwhile")
        return attempts
    log.append(f"{pid}: send attempt {attempts}/{SEND_ATTEMPTS} failed ({error})")
    if attempts >= SEND_ATTEMPTS:
        raise SendFailed(f"{pid} was not delivered after {attempts} attempts: {error}") from error
    return attempts


def send_prompt(cfg: Config, record: dict, log: list[str], *, retry=None, refold=None, refold_bare=None,
                label: str | None = None, keyboard: Keyboard | None = None) -> bool:
    """Deliver one prepared prompt record and keep the ledger's account of it. Returns True when it was delivered.

    ``record`` holds at least ``id``, ``kind``, ``local_date``, ``scheduled``, ``chat_id``, ``html`` and
    ``plain_text``; every field is stored as given (and the platform, off Telegram: ``channel.stamp``). A send error
    (``ports.ChannelError``) retries once at once with ``retry()``'s fields
    (the plain opener) when given; a send that still fails counts an attempt on the record (``send_attempts``,
    created with ``scheduled`` so prune keeps the count) and raises ``SendFailed`` on the ``SEND_ATTEMPTS``-th. A
    delivered record is stored (a second send of the same id makes the new message current: the old one still
    takes replies, is listed in ``previous_message_ids``, and is edited to ``refold(old)``'s text, ``old`` being the record it replaced, as it stood; if Telegram cannot parse
    that, ``refold_bare(old)``'s; a platform that cannot edit leaves it as sent), appended to
    ``sent.jsonl`` and mirrored into the companion's chat (``label``: what was sent, default "the <kind> prompt").
    Practices use it through ``send_due``; program phases and one-off interrupts will too."""
    pid = record["id"]
    channel.stamp(cfg, record)
    chat = cfg.channel()

    def plain(e: ChannelError) -> str:  # the send failed: once more at once, with the plain opener
        log.append(f"{pid}: send failed ({e}); retrying with the plain opener")
        record.update(retry())
        return record["html"]
    d = deliver(cfg, record["html"], log, chat=chat, catch=(Exception,), fallback=None if retry is None else plain,
                keyboard=keyboard, record=lambda d: _account(cfg, record, d, log, refold, refold_bare, label))
    return d.error is None


def _account(cfg: Config, record: dict, d: Delivery, log: list[str], refold, refold_bare,
             label: str | None) -> Mirror | None:
    """``send_prompt``'s account of one delivery (``deliver``'s ``record``): a failure counted on the record
    (``count_send_failure``); a delivery stored in the ledger (a re-send, an attach), stamped for the today block,
    appended to ``sent.jsonl``, the replaced message refolded; and what the companion is told."""
    pid, kind, names, chat = record["id"], record["kind"], cfg.names, d.chat
    if d.error is not None:
        count_send_failure(cfg, record, d.error, log)
        return None
    record["message_id"] = d.message_id
    record["sent_at"] = datetime.now(timezone.utc).isoformat()
    record["sent_at_local"] = datetime.now(timezone.utc).astimezone(cfg.tz).strftime("%H:%M %Z")
    replaced = None
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, pid)
        if q is None:
            data["prompts"].append(record)
        elif not q.get("message_id") and (q.get("captures") or q.get("opened_via")):
            _attach(q, record)  # words he wrote on a face meanwhile: the record keeps them, and gains the delivery
        elif not q.get("message_id"):
            q.update(record)
        else:  # manual re-trigger: the new message becomes current; the old one still accepts replies
            replaced = q["message_id"]
            old = dict(q)
            before = [*(q.get("previous_message_ids") or []), replaced]
            if q.get("opener") and not q.get("program"):  # the replaced message keeps that opener when it folds
                q["earlier_openers"] = [*(q.get("earlier_openers") or []), q["opener"]][-EARLIER_OPENERS:]
            if q.get("program") and q.get("sent_at"):  # (practices' records stay as they were)
                # the replaced message was current from then on: the sweep matches a message by its send time
                q["earlier_sent_at"] = [*(q.get("earlier_sent_at") or []), q["sent_at"]]
            # the entry stays where its replies already are (``capture.entry_stem`` may have moved it off a file
            # of his): the new record's default stem must not send the next reply elsewhere
            q.update({k: v for k, v in record.items() if k != "id" and not (k == "stem" and q.get("stem"))})
            # every earlier message still takes replies: the ones before this re-send, the message it replaced, and
            # the earlier halves of a phase that went out in two or more messages (``record`` carries the new ones)
            q["previous_message_ids"] = list(dict.fromkeys([*before, *(record.get("previous_message_ids") or [])]))
            q.pop("collapsed_at", None)
            q["needs_edit"] = False
            for k in FOLD_STATE:  # a new message: its fold starts afresh
                q.pop(k, None)
        if q is not None and "platform" not in record:  # sent on Telegram now (its records name none): a platform
            q.pop("platform", None)  # an earlier send on another one wrote would make it that chat's (``same_chat``)
    today.note_send(cfg)  # his next message starts a thread (and gets the today block)
    _archive_sent(cfg, {**record, "id": pid, **({"previous_message_ids": [replaced]} if replaced else {})})
    if replaced and refold is not None and chat.caps.edit and channel.same_chat(cfg, old):
        try:
            try:
                chat.edit(cfg.chat_id, replaced, refold(old))
            except ChannelError as e:
                if refold_bare is None or "parse entities" not in str(e):
                    raise
                chat.edit(cfg.chat_id, replaced, refold_bare(old))  # without the opener
        except ChannelError:
            pass
    log.append(f"sent {pid} (opener: {record.get('opener_source')}, wove {len(record.get('asked') or [])} questions)")
    what = (f"{names.companion} sent {label or f'the {kind} prompt'}{' again' if replaced else ''} at "
            f"{mirror.stamp(cfg, datetime.fromisoformat(record['sent_at']))}")
    return Mirror(what, record["plain_text"], ("prompts", pid, None))


ATTACHED = ("message_id", "sent_at", "sent_at_local", "chat_id", "platform")  # what a delivery gives a face's record


def _attach(q: dict, record: dict) -> None:
    """A delivery onto a record his words on a face made (the race of OPEN-PROMPTS rule 3: the scheduled send went out
    with the tick's opener after he answered). The record keeps its own opener, questions, stem and captures, and
    gains the delivery's fields, plus what the chat message says (``sent_opener``, ``sent_html``,
    ``sent_plain_text``), which the fold and the sweep read. ``needs_edit``: the next tick folds the message to
    "answered" instead of leaving its questions showing."""
    for k in ATTACHED:
        if k in record:
            q[k] = record[k]
    q["sent_opener"] = record.get("opener")
    q["sent_opener_source"] = record.get("opener_source")
    q["sent_html"] = record.get("html")
    q["sent_plain_text"] = record.get("plain_text")
    q.pop("collapsed_at", None)
    q["needs_edit"] = True


def prune_sent(cfg: Config, cutoff: str) -> None:
    """Drop ``sent.jsonl`` lines sent before ``cutoff`` (ISO); the file is rewritten only when something goes."""
    path = cfg.state_dir / "sent.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        keep = []
        for line in lines:
            try:
                when = json.loads(line).get("sent_at") or ""
            except (json.JSONDecodeError, AttributeError):
                when = ""
            if not when or when >= cutoff:  # no readable date: keep rather than lose it by accident
                keep.append(line)
        if len(keep) == len(lines):
            return
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sent-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(l + "\n" for l in keep))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass  # engine-private memory; the ledger is what matters
