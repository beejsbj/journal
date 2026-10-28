"""``hermes journal deliver``: one message into a chat on any platform Hermes sends to, run inside Hermes.

``HermesChannel`` (the ``ports.Channel`` for every platform but Telegram) runs this command of the plugin's own CLI,
so the send happens in Hermes' interpreter with the profile's credentials loaded the way ``hermes send`` loads them
(``hermes_cli.send_cmd._load_hermes_env``), whether the caller is the tick, the gateway or a spawned ``journal send``.

It reads ``{"platform", "chat_id", "text"}`` as JSON on stdin and prints one JSON line: ``{"ok": true,
"message_id": "…"}``, or ``{"ok": false, "error": "…"}`` with exit status 1.

Why not ``hermes send`` (``tools/send_message_tool.send_message_tool``): after every send it mirrors the raw text
into the chat's session as an assistant row (``_mirror_sent_message``; ``gateway.mirror.mirror_to_session``'s role
default), which the journal's transcript would read as the companion's reply, beside the journal's own framed,
user-role mirror; and it reads ``MEDIA:<path>`` in the text as a file to upload, which no text the journal sends
(an opener the model wrote, his quoted words) may do. So this resolves the target and the platform's config the way
``send_message_tool`` does and calls its platform router itself: no mirror, no media. Every other guard
``_handle_send`` puts around that router is kept, in its order: the text's lone surrogates scrubbed, a Slack user
target opened as its DM, the relay egress authorization (which refuses when it cannot answer), and every error
redacted of secrets before it is printed (it lands in the ledger and the tick's log). Those are Hermes internals
(read at Hermes ``e06df9c``); one that is missing is a failed send with that reason, never a fallback to the
mirroring path or a send without the guard.
"""
from __future__ import annotations

import json
import os
import sys


class DeliverError(RuntimeError):
    pass


def deliver(platform: str, chat_id: str, text: str) -> str:
    """Send ``text`` to ``chat_id`` on ``platform``; the new message's id (the last part's, when the platform's
    sender split it). Raises ``DeliverError`` (its text redacted of secrets once Hermes' redaction is loaded)."""
    os.environ.setdefault("HERMES_DISABLE_LAZY_INSTALLS", "1")
    platform, chat_id = str(platform or "").strip().lower(), str(chat_id or "").strip()
    if not platform or not chat_id or not text.strip():
        raise DeliverError("a platform, a chat id and some text are needed")
    try:
        from agent.message_sanitization import _sanitize_surrogates
        from hermes_cli.send_cmd import _load_hermes_env
        from model_tools import _run_async
        from tools import send_message_tool as smt
        from gateway.config import load_gateway_config
        resolve_target, resolve_config, route = (smt._resolve_tool_target, smt._resolve_platform_config,
                                                 smt._send_to_platform)
        authorize, slack_dm, redact = smt._authorize_relay_target, smt._slack_dm_chat_id, smt._sanitize_error_text
    except (ImportError, AttributeError) as e:
        raise DeliverError(f"this Hermes has no send path the journal can use without its mirror ({e})") from None

    def refuse(why) -> DeliverError:
        return DeliverError(redact(str(why))[:300] or "the platform refused the message")

    text = _sanitize_surrogates(text)
    _load_hermes_env()  # the gateway config reads the platform's credentials from the profile's environment
    name, chat, thread, err = resolve_target(f"{platform}:{chat_id}")
    if err:
        raise refuse(err)
    if not chat:
        raise refuse(f"{platform}:{chat_id} names no chat")
    try:
        config = load_gateway_config()
    except Exception as e:  # noqa: BLE001 - Hermes' own loader: say what it said
        raise refuse(f"the gateway configuration could not be read ({type(e).__name__}: {e})") from None
    enum, pconfig, _entry, err = resolve_config(name, config)
    if err:
        raise refuse(err)
    if name == "slack":  # a user id or handle is opened as its DM first (send_message_tool's order: before the guard)
        chat, failed = slack_dm(pconfig, chat)
        if failed:
            raise refuse(failed.get("error") if isinstance(failed, dict) else failed)
    denial = authorize(name, chat, thread, native_token=getattr(pconfig, "token", None))
    if denial:
        raise refuse(denial)
    try:
        result = _run_async(route(enum, pconfig, chat, text, thread_id=thread))
    except Exception as e:  # noqa: BLE001 - a platform's SDK may raise anything
        raise refuse(f"{type(e).__name__}: {e}") from None
    if not isinstance(result, dict) or result.get("error") or not result.get("success", True):
        raise refuse((result or {}).get("error") if isinstance(result, dict) else result)
    mid = result.get("message_id")
    if mid in (None, ""):
        raise refuse(f"{platform} sent the message but gave no id for it")
    return str(mid)


def main(stdin=None, stdout=None) -> int:
    """The command: JSON in, one JSON line out (see the module docstring)."""
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    try:
        req = json.loads(stdin.read() or "{}")
        if not isinstance(req, dict):
            raise DeliverError("the request must be a JSON object")
        mid = deliver(req.get("platform", ""), req.get("chat_id", ""), str(req.get("text") or ""))
    except (DeliverError, ValueError) as e:
        stdout.write(json.dumps({"ok": False, "error": str(e)[:500]}) + "\n")
        return 1
    stdout.write(json.dumps({"ok": True, "message_id": mid}) + "\n")
    return 0
