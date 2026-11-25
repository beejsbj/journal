"""Prompt ledger: what was sent, where, and what has been captured.

Lives in the state directory, never in the journal (the journal only ever receives
answered entries). The gateway plugin and the cron tick both write it, so every
mutation happens under an exclusive flock.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import threading
import time
from pathlib import Path

_local = threading.local()


class LockBusy(TimeoutError):
    """A lock was not obtained before the calling thread's ``deadline``."""


@contextlib.contextmanager
def deadline(seconds: float):
    """Within this block, taking ``locked`` or ``locked_state`` waits at most ``seconds`` in all (an absolute time,
    shared by every lock the block takes) and then raises ``LockBusy``. Per thread: the hook and the tick, which
    never set one, keep waiting as they always did. ``journal serve`` bounds a request this way (HTTP 423)."""
    before = getattr(_local, "until", None)
    _local.until = time.monotonic() + seconds
    try:
        yield
    finally:
        _local.until = before


def _flock(lock) -> None:
    until = getattr(_local, "until", None)
    if until is None:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= until:
                raise LockBusy("the journal is busy; try again in a moment") from None
            time.sleep(0.02)


@contextlib.contextmanager
def locked(path: Path):
    """Yield the ledger dict; persist it atomically on clean exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "a+") as lock:
        _flock(lock)
        try:
            data = _read(path)
            yield data
            _write(path, data)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def snapshot(path: Path) -> dict:
    return _read(path)


@contextlib.contextmanager
def locked_state(path: Path):
    """Like ``locked``, for another JSON state file shared by the gateway and the tick (a plain dict).

    Written back only when the dict changed: the hook takes this lock on every turn to look for its own records,
    and a look that finds nothing must not rewrite the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_suffix(".lock"), "a+") as lock:
        _flock(lock)
        try:
            data = read_state(path)
            before = _dumps(data)
            yield data
            if _dumps(data) != before:
                _write(path, data)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_state(path: Path) -> dict:
    """The state file as a dict; {} when it is missing, unreadable, not JSON or not UTF-8 (``ValueError``
    covers both decode errors), so a damaged file starts over instead of failing every access."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _read(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    data.setdefault("version", 1)
    data.setdefault("prompts", [])
    return data


def _dumps(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=1)


def _write(path: Path, data: dict) -> None:
    """Atomic 0600 write next to ``path`` (named after it, so a leftover says which file it was for). A dump
    that fails half-way leaves the file as it was and no temp file behind."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(_dumps(data))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def capture_of_client(data: dict, client_id: str | None) -> tuple[dict, dict] | None:
    """(prompt, capture) that the web app already wrote for this ``client_id`` (a retrying browser sends the same
    one). Kept apart from ``platform_message_id``: that field is a Telegram message id, and the tick replies to it."""
    if not client_id:
        return None
    for p in data["prompts"]:
        for c in p.get("captures") or []:
            if c.get("client_id") == client_id:
                return p, c
    return None


# The send record of a reflection or week review (``message_id``, ``sent_at``, ``attempts``, ``error``, ``skipped``,
# ``mirrored``) is kept under a name that says Telegram whatever the platform. PLATFORM-FIELDS renames it over two
# releases: this one (release 1) reads ``chat`` and ``telegram`` and still writes ``telegram``, so a gateway that
# runs older code reads every record this tick writes; release 2 writes ``chat``, once every reader is release 1.
SEND_KEY = "telegram"      # the name written today, and by every release before this one
SEND_KEY_NEW = "chat"      # the name read first, written from release 2 on
SEND_KEYS = (SEND_KEY_NEW, SEND_KEY)  # a record's name for its send, in the order read


def send_of(rec) -> dict:
    """The record's send, read: the one of ``chat`` / ``telegram`` that says it was sent or skipped, else the first that
    holds anything (``chat`` before ``telegram``), else ``{}`` (review of #83: a rolled-back tick can leave
    ``chat: {mirrored: True}`` beside ``telegram: {message_id: …}``, and the flag alone must not read as unsent). Not the
    record's own dict when it has none, so a reader never makes a key by looking."""
    if isinstance(rec, dict):
        held = [rec.get(key) for key in SEND_KEYS]
        held = [d for d in held if isinstance(d, dict) and d]
        for d in held:  # the one that says what happened (sent or skipped) wins over one holding only a flag
            if d.get("message_id") or d.get("skipped"):
                return d
        if held:
            return held[0]
    return {}


def send_slot(rec: dict) -> dict:
    """The record's send, to change (inside ``locked``): the dict ``send_of`` reads when there is one, so a record
    never ends up with the send half under each name; else a new one under ``SEND_KEY``."""
    for key in SEND_KEYS:
        found = rec.get(key)
        if isinstance(found, dict) and found:
            return found
    slot = rec.get(SEND_KEY)
    if not isinstance(slot, dict):
        slot = rec[SEND_KEY] = {}
    return slot


def prompt_id(kind: str, local_date: str) -> str:
    return f"{local_date}-{kind}"


def find(data: dict, pid: str) -> dict | None:
    return next((p for p in data["prompts"] if p["id"] == pid), None)


def by_message_id(data: dict, chat_id: str, message_id: str, platform: str | None = None) -> dict | None:
    """The prompt sent as ``message_id`` in the chat (on ``platform``, when given: a record naming none is
    Telegram's, ``channel.platform_of``)."""
    return next(
        (p for p in data["prompts"]
         if str(p.get("chat_id")) == str(chat_id)
         and (platform is None or (str(p.get("platform") or "").strip().lower() or "telegram") == platform)
         and (str(p.get("message_id")) == str(message_id)
              or str(message_id) in [str(x) for x in p.get("previous_message_ids", [])])),
        None,
    )


def answers(prompt: dict | None) -> list[dict]:
    """His answers to a prompt: its captures without the conversation's. A recording or a picture he sent in the chat
    after it is filed on the prompt of the entry it landed in (``rule: conversation``) only so the tick transcribes
    or describes it; it wrote a conversation line, not an answer block, so a reader pairing answers with blocks (a
    program phase) or asking whether a phase was answered leaves it out."""
    return [c for c in (prompt or {}).get("captures") or [] if c.get("rule") != "conversation"]


def capture_of(data: dict, message_id: str | None) -> tuple[dict, dict] | None:
    """(prompt, capture) that already holds this platform message, so nothing is captured twice."""
    if not message_id:
        return None
    for p in data["prompts"]:
        for c in p.get("captures") or []:
            if str(c.get("platform_message_id") or "") == str(message_id):
                return p, c
    return None
