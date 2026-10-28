"""The journal's chat on any platform but Telegram, through Hermes' own senders: a ``ports.Channel``.

Each send runs ``hermes [-p <profile>] journal deliver`` (``deliver.py``) in the journal's profile, with the message
as JSON on stdin, and reads the new message's id back. The command runs in Hermes' interpreter, so it works from
the tick, the gateway and a spawned ``journal send`` alike, with the platform credentials Hermes already holds for
the profile; the journal handles no token of its own here.

Out of process Hermes can send and nothing more, so the capabilities are none (``caps``): no edit (the fold closes
the prompt without one), no reply (a reply target is dropped), no keyboard (dropped; a phase's text carries its
commands), no HTML (``compose.plain``). The text is sent as it is: the deliver command reads no ``MEDIA:`` tags and
mirrors nothing (the engine's own mirror does that, framed).
"""
from __future__ import annotations

import json
import subprocess

from .. import compose
from ..ports import Caps, ChannelError, Keyboard

TIMEOUT = 90  # seconds: Hermes' CLI starts, loads its plugins and sends (a long message goes in parts)


class HermesChannel:
    caps = Caps()
    blocking = True  # each send starts Hermes' CLI: seconds, not a call

    def __init__(self, cfg):
        self.cfg = cfg
        self.platform = cfg.platform

    def argv(self) -> list[str]:
        cfg = self.cfg
        return [cfg.hermes_command(), *cfg.profile_args(), "journal", "deliver"]

    def send(self, chat_id: str, html: str, *, reply_to: str | None = None, silent: bool = False,
             keyboard: Keyboard | None = None) -> str:
        """Send the message as plain text; ``reply_to``, ``silent`` and ``keyboard`` are what this channel cannot
        do, so they are left out (``caps``)."""
        text = compose.plain(html)
        if not text:
            raise ChannelError("nothing to send")
        request = json.dumps({"platform": self.platform, "chat_id": str(chat_id), "text": text})
        try:
            done = subprocess.run(self.argv(), input=request, capture_output=True, text=True, timeout=TIMEOUT,
                                  env=self.cfg.hermes_env())
        except subprocess.TimeoutExpired:
            raise ChannelError(f"hermes journal deliver timed out after {TIMEOUT} s") from None
        except OSError as e:
            raise ChannelError(f"hermes journal deliver could not run ({type(e).__name__})") from None
        answer = _answer(done.stdout)
        if answer is None:  # (only an exception's class from its output: a traceback's text may hold a secret)
            raise ChannelError(f"hermes journal deliver gave no answer (exit {done.returncode}"
                               f"{_error_class(done.stderr)})")
        if not answer.get("ok") or not answer.get("message_id"):
            raise ChannelError(str(answer.get("error") or "the message was not sent")[:300])
        return str(answer["message_id"])

    def edit(self, chat_id: str, message_id: str, html: str) -> None:
        raise ChannelError(f"{self.platform} messages cannot be edited from outside the gateway")


def _error_class(stderr: str) -> str:
    """``: SomeError`` for the last line of a traceback, else nothing."""
    import re
    lines = (stderr or "").strip().splitlines()
    m = re.match(r"^([A-Za-z_][\w.]*(?:Error|Exception|Exit))\b", lines[-1].strip()) if lines else None
    return f": {m.group(1)}" if m else ""


def _answer(stdout: str) -> dict | None:
    """The deliver command's JSON line: the last line of its output that is one (Hermes may print before it)."""
    for line in reversed((stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                found = json.loads(line)
            except ValueError:
                continue
            if isinstance(found, dict) and "ok" in found:
                return found
    return None
