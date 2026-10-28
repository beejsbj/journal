"""Gateway hooks: translation between Hermes' events and the engine, nothing more. What happens to a message is
``journal.inbound`` (capture, ``/x``, audio, program answers); what the companion is told is ``journal.turncontext``;
which tools refuse in an off-the-record turn is ``journal.tools``.

pre_gateway_dispatch  a message event becomes an ``inbound.Inbound``; the ``Outcome`` goes back as Hermes' dict
                      (``rewrite`` the text, ``skip`` a command the engine already ran, or None to pass).
pre_llm_call          ``inbound.begin_turn`` writes the voice transcripts that arrived and marks an off-record turn;
                      ``turncontext.build`` returns the notebook, pack / program block and today block to inject.
pre_tool_call         ``tools.refusal`` (a write in an off-record turn), then the guard.

Every callback fails open for capture (a bug must never drop a message) and the guard fails open too (a bug must
never block every tool); errors are logged. The privacy checks inside the engine fail closed.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from .. import audio, channel, guard, images, inbound, tools, turncontext
from . import commands, runtime

log = logging.getLogger("journal.hermes")


def _kind(event) -> str:
    mt = getattr(event, "message_type", "")
    return str(getattr(mt, "name", mt)).lower()


def audio_media(event) -> tuple[str | None, str]:
    """(local path, mime) of the event's audio attachment, if it has one.

    VOICE and AUDIO messages carry their audio first; a DOCUMENT counts when its attachment's MIME type is audio/*
    (a recording forwarded as a file)."""
    urls = [str(u) for u in (getattr(event, "media_urls", None) or [])]
    types = [str(t or "") for t in (getattr(event, "media_types", None) or [])]
    kind = _kind(event)
    for i, url in enumerate(urls):
        mime = types[i].lower() if i < len(types) else ""
        if not os.path.isfile(url):
            continue
        if mime.startswith("audio/") or (not mime and kind in ("voice", "audio")):
            return url, mime
    if kind in ("voice", "audio"):
        first = next((u for u in urls if os.path.isfile(u)), None)
        return first, (types[0].lower() if types else "")
    return None, ""


def _file_name(event) -> str | None:
    """The sender's name for the attachment, if it has a suffix (the Telegram message Hermes keeps as ``raw_message``)."""
    raw = getattr(event, "raw_message", None)
    for attr in ("audio", "document", "voice"):
        obj = getattr(raw, attr, None) if raw is not None else None
        name = getattr(obj, "file_name", None) if obj is not None else None
        if isinstance(name, str) and os.path.splitext(name)[1]:
            return name
    return None


def to_inbound(event, at: datetime) -> inbound.Inbound:
    """The event as an ``Inbound``. Reading the attachment can never fail it: ``receive``'s ``/x`` check runs on the
    text, and a bug in the media or its metadata must not turn an off-the-record message into one that passes
    through unmarked. An attachment that cannot be read is treated as absent (the message is still handled)."""
    src = getattr(event, "source", None)
    reply_to = getattr(event, "reply_to_message_id", None)
    kind = _kind(event)
    try:
        media, mime = audio_media(event)
        pictures = images.from_event(event) if not media else ()
    except Exception:
        log.exception("journal: could not read the message's attachment")
        media, mime, pictures = None, "", ()
    try:
        meta = audio.event_meta(event) if media or kind in ("voice", "audio") else {}
    except Exception:
        log.exception("journal: could not read the audio's metadata")
        meta = {}
    return inbound.Inbound(
        platform=channel.norm(getattr(src, "platform", "")), chat_id=str(getattr(src, "chat_id", "")), at=at,
        user_id=str(getattr(src, "user_id", "") or "") or None,
        message_id=str(getattr(event, "message_id", "") or ""), text=getattr(event, "text", "") or "",
        reply_to=str(reply_to) if reply_to else None, kind=kind if kind in ("text", "voice", "audio", "document",
                                                                           "photo", "video") else "other",
        media_path=media, mime=mime, file_name=_file_name(event), has_media=bool(getattr(event, "media_urls", None)),
        images=pictures, meta=meta)


def on_dispatch(event=None, **_kw):
    try:
        src = getattr(event, "source", None)
        cfg = runtime.config_for_event(log) if src is not None else None
        if cfg is None or not channel.is_platform(cfg, getattr(src, "platform", "")):
            return None  # (``receive`` checks the chat and the sender: one platform's events are read, no other's)
        out = inbound.receive(cfg, to_inbound(event, datetime.now(timezone.utc)), spawn=commands.spawn)
        if out.action == "rewrite":
            return {"action": "rewrite", "text": out.text}
        return {"action": "skip", "reason": out.reason} if out.action == "drop" else None
    except Exception:
        log.exception("journal dispatch hook failed; message passed through uncaptured")
        return None


def on_llm_call(user_message=None, platform=None, session_id=None, turn_id=None, **_kw):
    try:
        cfg = runtime.load_config()
    except Exception:  # nothing below can run without the state dir; the message itself is unaffected
        log.exception("journal: configuration failed in the LLM hook")
        return None
    turn = turncontext.Turn(platform=str(platform or ""), session_id=session_id, turn_id=turn_id,
                            user_message=user_message or "", at=datetime.now(timezone.utc),
                            chat_id=runtime.session_value("CHAT_ID"), user_id=runtime.session_value("USER_ID"))
    inbound.begin_turn(cfg, turn, spawn=commands.spawn)
    try:
        context_text = turncontext.build(cfg, turn)
    except Exception:
        log.exception("journal: could not build the turn's context")
        return None
    return {"context": context_text} if context_text else None


def on_tool_call(tool_name=None, args=None, session_id=None, turn_id=None, **_kw):
    name = str(tool_name or "")
    try:
        cfg = runtime.load_config()
    except Exception:
        log.exception("journal: configuration failed in the tool guard")
        if name in tools.WRITES:  # its handler would fail too; never write blind
            return {"action": "block", "message": "The journal isn't configured right now, so nothing was saved."}
        return None
    try:
        args = args if isinstance(args, dict) else {}
        msg = tools.refusal(cfg, name, args, tools.ToolTurn(session_id, turn_id))
        if msg:
            log.warning("journal: %s refused in an off-the-record turn", name)
        else:
            msg = guard.check(cfg, name, args)
            if msg:
                log.warning("journal guard blocked %s", name)
    except Exception:
        log.exception("journal guard failed open")
        return None
    return {"action": "block", "message": msg} if msg else None
