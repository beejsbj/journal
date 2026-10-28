"""Minimal Telegram Bot API client (send, reply, edit; HTML mode), and ``TelegramChannel``: the ``ports.Channel``
the engine sends through when the journal's chat is on Telegram (the default). It can do everything the engine asks
of a channel: edit (the fold), reply to a message, a reply keyboard, HTML.

The bot token comes from configuration, never from a file this module reads itself:

1. the environment variable named by ``telegram.token_env`` (default ``JOURNAL_TELEGRAM_TOKEN``);
2. ``telegram.token_command``: a command whose output holds the token, either as a
   ``<token_key>=<value>`` line or as its first line. Secret helpers occasionally stall, so it
   is retried once with a longer timeout.

The token is fetched once per process, kept in memory only, and never logged or printed.
"""
from __future__ import annotations

import html
import json
import os
import re
import shlex
import subprocess
import threading
import urllib.error
import urllib.request

from .config import Config
from .ports import Caps, ChannelError, Keyboard


class TelegramError(ChannelError):
    pass


_TOKEN: str | None = None
_TOKEN_LOCK = threading.Lock()


def _parse_token(output: str, key: str) -> str | None:
    lines = [l.strip() for l in (output or "").splitlines() if l.strip()]
    for line in lines:
        if line.startswith(f"{key}=") or line.startswith(f"export {key}="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    if len(lines) == 1 and "=" not in lines[0]:
        return lines[0]
    return None


def _command_argv(cmd) -> list[str]:
    if isinstance(cmd, str):
        return ["/bin/sh", "-c", cmd]
    return [str(x) for x in cmd]


def resolve_token(cfg: Config, environ: dict | None = None) -> tuple[str | None, str]:
    """(token, where it came from). Never raises; never returns the token in the description."""
    return resolve_secret(cfg.telegram, "TELEGRAM_BOT_TOKEN", "telegram", environ)


def resolve_secret(spec: dict, default_key: str, section: str, environ: dict | None = None) -> tuple[str | None, str]:
    """A secret from a ``{token_env, token_command, token_key}`` section (``telegram``, ``serve``): the environment
    variable first, then the command's output. (secret, where it came from); never raises, never describes the
    secret itself."""
    env = os.environ if environ is None else environ
    name = spec.get("token_env") or ""
    if name and env.get(name):
        return env[name].strip(), f"environment variable {name}"
    cmd = spec.get("token_command")
    if cmd:
        key = spec.get("token_key") or default_key
        why = "timed out"
        for timeout in (30, 60):  # the helper occasionally stalls or comes back empty: try twice
            try:
                out = subprocess.run(_command_argv(cmd), capture_output=True, text=True, timeout=timeout)
            except subprocess.TimeoutExpired:
                why = "timed out"
                continue
            except OSError as e:
                return None, f"token_command could not run ({type(e).__name__})"
            tok = _parse_token(out.stdout, key)
            if tok:
                return tok, "token_command"
            why = f"printed no {key}"
        return None, f"token_command {why} (two attempts)"
    return None, f"no token: set {name or section + '.token_env'} or {section}.token_command"


def token(cfg: Config) -> str:
    """The bot token, fetched once per process."""
    global _TOKEN
    with _TOKEN_LOCK:
        if _TOKEN:
            return _TOKEN
        tok, where = resolve_token(cfg)
        if not tok:
            raise TelegramError(f"bot token unavailable: {where}")
        _TOKEN = tok
        return tok


def _reset_token_cache() -> None:  # tests
    global _TOKEN
    _TOKEN = None


def _call(cfg: Config, method: str, payload: dict) -> dict:
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token(cfg)}/{method}",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read() or b"{}")
        except json.JSONDecodeError:
            body = {"description": f"HTTP {e.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise TelegramError(f"{method}: {type(e).__name__}: {e}") from None
    if not body.get("ok"):
        raise TelegramError(f"{method}: {body.get('description', 'unknown error')}")
    return body["result"]


def send_html(cfg: Config, chat_id: str, text: str, reply_to: str | None = None, silent: bool = False,
              reply_markup: dict | None = None) -> str:
    """Send an HTML message (optionally as a reply; ``silent``: no notification; ``reply_markup``: a reply keyboard
    or its removal, as the Bot API takes it); returns the new message id."""
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML",
               "link_preview_options": {"is_disabled": True}}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    if silent:
        payload["disable_notification"] = True
    if reply_to:
        payload["reply_parameters"] = {"message_id": int(reply_to), "allow_sending_without_reply": True}
    try:
        result = _call(cfg, "sendMessage", payload)
    except TelegramError as e:
        if "parse entities" not in str(e):
            raise
        payload.pop("parse_mode")  # never lose a message to a markup error
        payload["text"] = html.unescape(re.sub(r"<[^>]+>", "", text))
        result = _call(cfg, "sendMessage", payload)
    return str(result["message_id"])


def edit_html(cfg: Config, chat_id: str, message_id: str, text: str) -> None:
    """Edit a message to ``text`` (HTML). As ``send_html`` does, a markup Telegram cannot parse (crossed tags from
    a model's text) is retried once as the plain text, so an edit is never lost to it."""
    payload = {"chat_id": chat_id, "message_id": int(message_id), "text": text, "parse_mode": "HTML",
               "link_preview_options": {"is_disabled": True}}
    try:
        try:
            _call(cfg, "editMessageText", payload)
        except TelegramError as e:
            if "parse entities" not in str(e):
                raise
            payload.pop("parse_mode")
            payload["text"] = html.unescape(re.sub(r"<[^>]+>", "", text))
            _call(cfg, "editMessageText", payload)
    except TelegramError as e:
        if "message is not modified" not in str(e):
            raise


def shell_quote(cmd) -> str:
    """Display form of a token command (for doctor output); contains no secret."""
    return cmd if isinstance(cmd, str) else " ".join(shlex.quote(str(x)) for x in cmd)


def markup(keyboard: Keyboard) -> dict:
    """A ``ports.Keyboard`` as the Bot API's ``reply_markup``: rows of text buttons, offered once, or its removal."""
    if not keyboard.rows:
        return {"remove_keyboard": True}
    out = {"keyboard": [[{"text": t} for t in row] for row in keyboard.rows],
           "one_time_keyboard": True, "resize_keyboard": True, "is_persistent": False}
    if keyboard.placeholder:
        out["input_field_placeholder"] = keyboard.placeholder
    return out


class TelegramChannel:
    """The journal's chat on Telegram, through the Bot API (``send_html``, ``edit_html``, looked up when called)."""
    platform = "telegram"
    caps = Caps(edit=True, reply=True, keyboard=True, html=True)
    blocking = False  # one HTTPS call (bounded at 30 s), as the engine always made it

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def send(self, chat_id: str, html: str, *, reply_to: str | None = None, silent: bool = False,
             keyboard: Keyboard | None = None) -> str:
        kw = {}
        if reply_to:
            kw["reply_to"] = reply_to
        if silent:
            kw["silent"] = True
        if keyboard is not None:
            kw["reply_markup"] = markup(keyboard)
        return send_html(self.cfg, chat_id, html, **kw)

    def edit(self, chat_id: str, message_id: str, html: str) -> None:
        edit_html(self.cfg, chat_id, message_id, html)
