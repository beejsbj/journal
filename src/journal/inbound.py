"""What the engine does with one message that arrives for the companion, before any model sees it.

An adapter turns its platform's event into an ``Inbound``, calls ``receive`` and applies the ``Outcome``: pass the
message on untouched, send the companion different text (``rewrite``: a note in front of his words, or his words
stripped of a command), or ``drop`` it (a command the engine has already run). Nothing here knows Hermes; what only
the adapter can do is handed in (``spawn``, how to run ``python -m journal …`` detached).

What happens, in order, for a message in the journal's chat (``channel.ours``: its platform, its chat, and from him
when ``channel.user_ids`` names him; a message anywhere else, or from anyone else, passes through untouched):

* ``/start <id>`` becomes ``/journal-start <id>`` (the platform owns ``/start`` itself); ``/next`` ``/skip`` ``/back``
  ``/break`` ``/program`` are run here and dropped, because the platform would run them only while the companion is
  idle and otherwise hand them to her as text.
* ``/x`` (the opt-out prefix) takes a message, or with nothing after it the rest of the thread, off the record: the
  command is stripped, the companion is told, nothing is captured. Inside a bare-``/x`` window every message is
  marked the same way, except an explicit ``/j``. This check comes before any capture and **fails closed**: if
  recording it fails, the message still goes through marked off the record.
* Audio (a voice note, an audio file, a recording sent as a document) is kept beside its entry with the entry's name
  and the original extension. A voice note is written when its transcript reaches the turn (``begin_turn``); an audio
  file is recorded at once and transcribed by the tick; chat audio that answers no prompt is stashed for the
  conversation writer.
* Images (a photo, or a jpg/png/webp/heic/gif sent as a file) are kept the same way (``images.py``): a photo that
  answers a prompt, a phase or a ``/j`` is copied beside the entry and embedded in his block above his caption,
  written at once; one in ordinary chat is stashed for the conversation writer. Off the record, nothing is kept.
* ``/j <text>`` is written as a note; a reply to a prompt is written to its entry. Both are saved verbatim before
  the companion sees anything, and the companion gets a note in front saying where (and, for a reply, the context
  pack of the prompt it answers, or for a program phase answer the program block, queued for ``turncontext``).
  A program phase answer spawns ``journal program settle <run>`` (the run moves on once he has been quiet for
  ``programs.settle_minutes``).

``begin_turn`` is the other half: when a model turn starts it writes the voice transcripts that have arrived and
remembers a turn marked off the record, so the journal's writing tools refuse to run in it (``tools.call``).

Capture fails open (a bug must never drop a message: it passes through uncaptured, and the busy-path sweep finds it);
privacy fails closed. What one call learns for a later one (a voice note awaiting its transcript, the pack queued for
a turn, a turn marked off the record) is kept in ``hook-state.json`` (``hookstate``), so a restart between the two
loses nothing.
"""
from __future__ import annotations

import contextlib
import html
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping

from . import audio, capture, channel, commands, context, conversations, hookstate, images, ledger, privacy
from .commands import Spawn
from .config import Config
from .turncontext import Turn

log = logging.getLogger("journal.inbound")
NO_TRANSCRIPT = audio.NO_TRANSCRIPT
NOT_TRANSCRIBED = audio.NOT_TRANSCRIBED
ATTACHMENTS = ("voice", "audio", "document", "photo", "video")  # (kinds that carry a file)
_REPLY_PREFIX = re.compile(r'^\[Replying to[^\n]*\]\s*', re.S)


@dataclass(frozen=True)
class Inbound:
    """One incoming message, as the adapter reads it from its platform's event."""
    platform: str
    chat_id: str
    message_id: str
    text: str
    at: datetime                      # timezone-aware
    reply_to: str | None = None       # the message id it answers
    kind: str = "text"                # text | voice | audio | document | photo | video | other
    media_path: str | None = None     # the audio file on disk, when the message carries audio
    mime: str = ""
    file_name: str | None = None      # the sender's name for the audio file, if any
    has_media: bool = False           # any attachment at all (a bare /x with one is a message, not a window)
    images: tuple = ()                # (path, mime) of each picture on disk the message carries
    meta: Mapping = field(default_factory=dict, hash=False, compare=False)  # ``audio.event_meta``
    user_id: str | None = None        # the sender, as the platform names him (``channel.user_ids``)


@dataclass(frozen=True)
class Outcome:
    action: str                       # "pass" | "rewrite" | "drop"
    text: str = ""                    # the rewritten text the model should see
    captured: dict | None = None      # capture info, for logging
    reason: str = ""                  # why a message was dropped

    @classmethod
    def rewrite(cls, text: str, captured: dict | None = None) -> "Outcome":
        return cls("rewrite", text, captured)


PASS = Outcome("pass")


def original_extension(msg: Inbound, media: str, mime: str) -> str:
    """Keep the format that arrived: the sender's file name, else the cached file, else the MIME type."""
    if msg.file_name and os.path.splitext(msg.file_name)[1]:
        return os.path.splitext(msg.file_name)[1].lower()
    ext = os.path.splitext(media)[1]
    return ext.lower() if ext else audio.extension_for(mime)


def image_files(msg: Inbound) -> list[tuple[str, str]]:
    """The pictures of ``msg`` worth keeping, as (path on disk, suffix): files that exist and are one of the kept
    kinds. The sender's file name names only a lone attachment."""
    out = []
    for path, mime in msg.images:
        if not path or not os.path.isfile(path):
            continue
        ext = images.extension(path, msg.file_name if len(msg.images) == 1 else None, mime, photo=msg.kind == "photo")
        if ext:
            out.append((path, ext))
    return out


def keep_images(cfg: Config, files: list[tuple[str, str]], entry_path: str) -> list[str]:
    """Copy each picture next to its entry (``images.keep``); one that is too large or cannot be copied is left out
    and said in the log (its caption is still saved)."""
    kept = []
    for path, ext in files:
        try:
            kept.append(images.keep(cfg, path, entry_path, ext))
        except images.ImageTooLarge as e:
            log.warning("journal did not keep an image (%s)", e)
        except Exception:
            log.exception("journal could not keep an image")
    return kept


def keep_audio(cfg: Config, media: str, entry_path: str, ext: str) -> str | None:
    """Copy the audio next to its entry, same name: 2026-10-13-evening.ogg (then -2, -3 …)."""
    try:
        return audio.keep(cfg, media, entry_path, ext)
    except Exception:
        log.exception("journal could not keep the audio")
        return None


def receive(cfg: Config, msg: Inbound, *, spawn: Spawn | None = None) -> Outcome:
    """Handle one incoming message (see the module docstring). Never raises: a bug passes the message through."""
    try:
        if not channel.ours(cfg, msg.platform, msg.chat_id, msg.user_id):
            return PASS  # another platform, another chat or another person: never the journal's (fails closed)
        after_j = capture.strip_command(msg.text)
        routed = commands.routed_start(msg.text)
        if routed is not None:  # a command, never his words: nothing to capture or mark off the record
            return Outcome.rewrite(routed)
        program_cmd = commands.routed_program(msg.text)
        if program_cmd is not None:
            return _program_command(cfg, program_cmd, spawn)

        try:
            off = _off_record(cfg, msg, after_j)
        except Exception:  # fail closed for privacy: the message still goes through marked off the record
            log.exception("journal: could not record an off-the-record message")
            rest = capture.opt_out(cfg, msg.text)
            # (a bare /x with a file attached is one off-the-record message too: its audio must not be kept)
            marked = rest is not None and (rest.strip() or msg.has_media or msg.kind in ATTACHMENTS)
            off = Outcome.rewrite(capture.off_record_note(cfg) + rest.lstrip()) if marked else None
            if off is None and capture.opt_out_inside(cfg, msg.text):
                off = Outcome.rewrite(capture.off_record_note(cfg) + msg.text)
        if off is not None:
            return off

        if msg.media_path or msg.kind in ("voice", "audio"):
            return _audio(cfg, msg, after_j, spawn)

        if msg.images:
            out = _image(cfg, msg, after_j, spawn)
            if out is not None:
                return out

        if after_j is not None:
            if not after_j.strip():
                return PASS
            info = capture.capture_note(cfg, after_j, msg.at, "text", msg.message_id)
            log.info("journal note captured: %s", info["path"])
            note = capture.note_context(cfg, info)
            _remember_pack(cfg, note, None, msg.at)
            return Outcome.rewrite(note + after_j, info)

        info = capture.capture(cfg, msg.chat_id, msg.reply_to, msg.text, msg.at, "text", msg.message_id)
        if not info:
            return PASS
        log.info("journal reply captured: %s (%s)", info["path"], info["rule"])
        if info["prompt"].get("program"):
            if not info.get("duplicate"):
                _settle_later(cfg, info["prompt"], spawn)
            note = _program_note(cfg, info)
            _remember_program(cfg, note, info["prompt"])
            return Outcome.rewrite(note + msg.text, info)
        note = capture.context_note(cfg, info)
        _remember_pack(cfg, note, info["prompt"], msg.at)
        return Outcome.rewrite(note + msg.text, info)
    except Exception:
        log.exception("journal inbound failed; message passed through uncaptured")
        return PASS


def _program_command(cfg: Config, name: str, spawn: Spawn | None) -> Outcome:
    """A program command in the journal's chat: run it now and drop the message, so a tap that lands while she is
    mid-reply is not steered into her turn as text. If running it fails the message goes on as before (the platform
    runs the registered command when she is idle); once it has run, only the answer can be lost, never the action
    twice. An adapter with no ``spawn`` cannot run the ones that start a process (``/next`` ``/skip`` ``/back``
    ``/break``): they go on to the platform, as if the engine had not seen them; ``/program`` only reads."""
    if spawn is None and name != "program":
        return PASS
    try:
        reply = commands.run_program_command(cfg, name, spawn or _no_spawn)
    except Exception:
        log.exception("journal: could not run /%s on arrival", name)
        return PASS
    if reply:
        _answer(cfg, html.escape(reply, quote=False), f"/{name}")
    return Outcome("drop", reason=f"journal /{name}")


def _answer(cfg: Config, markup: str, what: str) -> None:
    """Send an answer nothing waits for. The gateway runs this hook on its event loop, so a channel whose send
    starts a process (``ports.Channel.blocking``) sends from a thread of its own; best effort either way."""
    def send(chat) -> None:
        try:
            chat.send(cfg.chat_id, markup)
        except Exception:
            log.exception("journal: could not answer %s", what)
    try:
        chat = cfg.channel()
    except Exception:
        log.exception("journal: could not answer %s", what)
        return
    if getattr(chat, "blocking", False):
        threading.Thread(target=send, args=(chat,), name="journal-answer", daemon=True).start()
    else:
        send(chat)


def _no_spawn(cfg, *args):
    raise RuntimeError("this adapter cannot start a background process")


def _program_note(cfg: Config, info) -> str:
    """The note in front of a program phase answer (``programs.reply_note``); the plain reply note if it fails."""
    try:
        from . import programs
        return programs.reply_note(cfg, info)
    except Exception:
        log.exception("journal: could not write the program reply note")
        return capture.context_note(cfg, info)


def _settle_later(cfg: Config, prompt: dict, spawn: Spawn | None) -> None:
    """A phase answer was saved: have the run move on once he has been quiet for the settle (``journal program
    settle <run>``, detached; it waits for the tick's lock). A lost spawn is caught by the tick's ``programs``
    step, a few minutes late. ``advance: next`` programs wait for /next instead."""
    if prompt.get("advance", "settle") != "settle" or not prompt.get("run") or prompt.get("interrupt"):
        return  # (an interrupt's reply moves nothing: it is not the run's open phase)
    try:
        (spawn or _no_spawn)(cfg, "program", "settle", str(prompt["run"]))
    except Exception:  # the tick's step advances it instead
        log.exception("journal: could not spawn the program settle; the tick will move the run on")


def _off_record(cfg: Config, msg: Inbound, after_j) -> Outcome | None:
    """``/x`` and the thread it holds off the record. Returns the outcome, or None to carry on.

    ``/x <text>``: the message reaches the companion without the command, marked off the record, and nothing is
    captured. A bare ``/x`` starts off-the-record mode for the rest of the thread and is left to the slash command
    (which answers it). Inside that mode every message is marked the same way, except an explicit ``/j``."""
    rest = capture.opt_out(cfg, msg.text)
    if rest is not None:
        attached = msg.has_media or msg.kind in ATTACHMENTS
        if not rest.strip() and not attached:  # a bare /x with nothing attached: the rest of the thread
            conversations.note_off(cfg, msg.message_id, msg.at, thread_start=True)
            log.info("journal: the rest of this thread is off the record")
            return PASS  # the registered command replies
        conversations.note_off(cfg, msg.message_id, msg.at)
        return Outcome.rewrite(capture.off_record_note(cfg) + rest.lstrip())
    if capture.opt_out_inside(cfg, msg.text):  # a /x on a later item of a merged album or burst: all of it is private
        conversations.note_off(cfg, msg.message_id, msg.at)
        return Outcome.rewrite(capture.off_record_note(cfg) + msg.text)
    if after_j is None and not msg.text.lstrip().startswith("/") and privacy.window_open(cfg, msg.at):
        try:
            conversations.note_off(cfg, msg.message_id, msg.at)
        except Exception:  # fail closed: the window holds it, and the marker in the stored text still says so
            log.exception("journal: could not record a message inside an off-the-record window")
        return Outcome.rewrite(capture.off_record_note(cfg, thread=True) + msg.text)
    return None


def _remember_pack(cfg: Config, note: str, prompt: dict | None, now) -> None:
    """Queue the context pack for the turn this capture starts: the answered prompt's own pack, or for a
    /j note the most recent pack if it is less than ``context.NOTE_PACK_HOURS`` old."""
    try:
        block = context.pack_for_turn(cfg, prompt, now)
        if block:
            hookstate.remember_pack(cfg, note, block)
    except Exception:  # the capture stands; her turn just goes without the pack
        log.exception("journal: could not queue the context pack")


def _remember_program(cfg: Config, note: str, prompt: dict) -> None:
    """Queue the program block for the turn this phase answer starts (``turncontext`` reads it from the entry when
    the turn begins, so a voice note's transcript is in it). An interrupt's reply is not a phase answer: it gets
    no block, as before."""
    if prompt.get("interrupt") or not prompt.get("run"):
        return
    try:
        hookstate.remember_program(cfg, note, {"run": prompt["run"], "session": prompt.get("session"),
                                               "phase": prompt.get("phase"), "prompt": prompt.get("id")})
    except Exception:  # the capture stands; her turn just goes without the block
        log.exception("journal: could not queue the program block")


def _audio(cfg: Config, msg: Inbound, after_j, spawn: Spawn | None) -> Outcome:
    media, mime, now = msg.media_path, msg.mime, msg.at
    if after_j is not None:
        prompt, rule = capture.reserve_note(cfg, now), "/j"
        caption = after_j
        info = capture.planned_info(cfg, prompt)
        note = capture.note_context(cfg, info)
    else:
        snap = ledger.snapshot(cfg.ledger_path)
        prompt, rule = capture.match(snap, cfg, msg.chat_id, msg.reply_to, now)
        if not prompt:
            if (media and conversations.enabled(cfg)
                    and cfg.transcript().source is not None):  # (with no transcript nothing writes the chat)
                try:  # chat audio: kept for the conversation writer, which puts it beside the entry
                    conversations.stash_audio(cfg, msg.message_id, media, original_extension(msg, media, mime),
                                              dict(msg.meta), now)
                except Exception:
                    log.exception("journal could not stash the chat audio")
            return PASS
        caption = msg.text
        # planned from a snapshot, recorded later under the lock (capture_to): the two agree unless another prompt
        # of the same practice takes a fresh name in between, and then only the kept audio's name differs
        info = capture.planned_info(cfg, prompt, snap)
        note = _program_note(cfg, info) if prompt.get("program") else capture.context_note(cfg, info)
    kept = keep_audio(cfg, media, info["path"], original_extension(msg, media, mime)) if media else None
    meta = dict(msg.meta)
    if prompt.get("program"):  # a program phase has no opener pack: its turn gets the program block instead
        _remember_program(cfg, note, prompt)
    else:
        _remember_pack(cfg, note, None if rule == "/j" else prompt, now)
    if msg.kind == "voice":
        try:
            hookstate.remember_voice(cfg, msg.message_id or str(time.time()), {
                "pid": prompt["id"], "rule": rule, "msg_id": msg.message_id, "note": note,
                "caption": caption, "audio": kept, "meta": meta,
            })
        except Exception:
            # Without the record no later call writes the transcript, and the sweep skips every row that
            # carries the hook's note as already saved: marking this one would lose the reply for good. So the
            # message goes through untouched (the sweep captures its transcript like any reply the hook missed,
            # without the recording) and the copy of the audio, which nothing would reference, goes.
            log.exception("journal: could not remember the voice note; leaving its row to the sweep")
            if kept:
                with contextlib.suppress(OSError):
                    os.unlink(os.path.join(str(cfg.root), kept))
            return PASS
    else:  # an audio file: the gateway won't transcribe it; record it now, the tick transcribes it
        pending = bool(kept) and audio.transcription_enabled(cfg)
        words = caption.strip() or (audio.TRANSCRIBING if pending else NOT_TRANSCRIBED)
        saved = capture.capture_to(cfg, prompt["id"], rule, words, now, "audio", msg.message_id, kept, meta,
                                   transcript="pending" if kept else None)
        if saved:
            log.info("journal audio captured: %s", saved["path"])
            if saved["prompt"].get("program"):
                _settle_later(cfg, saved["prompt"], spawn)
    return Outcome.rewrite(note + caption, info)


def _image(cfg: Config, msg: Inbound, after_j, spawn: Spawn | None) -> Outcome | None:
    """A message with pictures (the off-the-record check has passed). Answering a prompt, a phase or a ``/j``, the
    pictures are kept beside the entry and written into his block now, caption verbatim; chatting, they are
    stashed for the conversation writer. None when there is no picture to keep (the message is then handled as
    the text it also is)."""
    files = image_files(msg)
    if not files:
        return None
    now = msg.at
    if after_j is not None:
        done = ledger.capture_of(ledger.snapshot(cfg.ledger_path), msg.message_id) if msg.message_id else None
        if done:  # a redelivered update: its note exists; reserving another would leave an empty one behind
            info = {**capture.planned_info(cfg, done[0]), "rule": "/j", "duplicate": True}
            return Outcome.rewrite(capture.note_context(cfg, info) + after_j, info)
        prompt, rule, caption = capture.reserve_note(cfg, now), "/j", after_j
        info = capture.planned_info(cfg, prompt)
        note = capture.note_context(cfg, info)
    else:
        if msg.text.lstrip().startswith("/"):
            return PASS  # a command with a picture attached is no reply
        snap = ledger.snapshot(cfg.ledger_path)
        prompt, rule = capture.match(snap, cfg, msg.chat_id, msg.reply_to, now)
        if not prompt:
            _stash_chat_images(cfg, msg, files, now)
            return PASS
        caption = msg.text
        info = capture.planned_info(cfg, prompt, snap)
        note = _program_note(cfg, info) if prompt.get("program") else capture.context_note(cfg, info)
        if msg.message_id and ledger.capture_of(snap, msg.message_id):  # a redelivered update: saved already
            return Outcome.rewrite(note + caption, info)
    kept = keep_images(cfg, files, info["path"])
    if not kept and not caption.strip():
        log.warning("journal: a picture with no caption could not be kept; nothing to save")
        if rule == "/j":
            capture.release_note(cfg, prompt["id"])  # the note reserved for it will never get words
        return PASS
    saved = capture.capture_to(cfg, prompt["id"], rule, caption, now, "photo" if kept else "text", msg.message_id,
                               images=kept,
                               describe=images.describing(cfg))
    if not saved:  # the prompt is gone, or the message was captured meanwhile: nothing references the copies
        for name in kept:
            with contextlib.suppress(OSError):
                os.unlink(os.path.join(str(cfg.root), name))
        return PASS
    log.info("journal image captured: %s", saved["path"])
    if prompt.get("program"):
        _remember_program(cfg, note, prompt)
        _settle_later(cfg, saved["prompt"], spawn)
    else:
        _remember_pack(cfg, note, None if rule == "/j" else prompt, now)
    return Outcome.rewrite(note + caption, saved)


def _stash_chat_images(cfg: Config, msg: Inbound, files: list[tuple[str, str]], now) -> None:
    """Chat that answers no prompt: keep a copy of each picture for the conversation writer, which puts it beside
    the entry the thread lands in (Hermes' cache is not ours). Only when something writes the chat."""
    if not (msg.message_id and conversations.enabled(cfg)
            and cfg.transcript().source is not None):  # (with no transcript nothing writes the chat)
        return
    try:
        conversations.stash_images(cfg, msg.message_id, files, now, max_bytes=images.max_bytes(cfg))
    except Exception:
        log.exception("journal could not stash the chat image")


def transcript(user_message: str, pend: dict) -> str:
    """The voice note's words out of the turn's message: what is left once the journal's note, the reply quote and
    the caption are taken away. If the platform merged an off-the-record message into the turn, everything from its
    marker on is cut first: the marker and his private words are not the transcript (and a transcript that came
    after them is lost rather than risk writing them)."""
    off = capture.OFF_RECORD.search(user_message)
    if off is not None:
        user_message = user_message[:off.start()]
    text = user_message.replace(pend["note"], "")
    text = _REPLY_PREFIX.sub("", text.strip())
    if pend.get("caption"):
        text = text.replace(pend["caption"], "").strip()
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] == '"':
        text = text[1:-1].strip()
    return text or NO_TRANSCRIPT


def begin_turn(cfg: Config, turn: Turn, *, spawn: Spawn | None = None) -> None:
    """A model turn is starting: write the voice transcripts that have arrived, and remember a turn marked off the
    record. Each part fails on its own (a bug here never stops the turn)."""
    _capture_voice(cfg, turn, spawn)
    try:
        _note_off_turn(cfg, turn)
    except Exception:  # the tool guard's check over the chat still applies
        log.exception("journal: could not record the off-the-record turn")


def _capture_voice(cfg: Config, turn: Turn, spawn: Spawn | None) -> None:
    try:
        if not channel.turn_ours(cfg, turn.platform, turn.chat_id, turn.user_id):
            return None
        user_message, now = turn.user_message, turn.at
        message = user_message or ""
        pend = hookstate.take_voice(cfg, message)
        if not pend:
            return None
        text = transcript(message, pend)
        if pend.get("caption"):
            text = f"{text}\n\n{pend['caption']}"
        info = capture.capture_to(cfg, pend["pid"], pend["rule"], text, now, "voice",
                                  pend["msg_id"], pend.get("audio"), pend.get("meta"))
        if info:
            log.info("journal voice reply captured: %s", info["path"])
            if info["prompt"].get("program"):
                _settle_later(cfg, info["prompt"], spawn)
    except Exception:
        log.exception("journal voice capture failed")


def _note_off_turn(cfg: Config, turn: Turn) -> None:
    """Remember a turn whose message was marked off the record (/x, or a bare-/x thread), so the journal's own
    writing tools refuse to run in it. Any other turn of the session clears it. Only the journal chat's turns
    (``channel.turn_ours``): arrival marks only those, so a CLI or cron turn, or one in another chat, has nothing to
    record or clear and must not touch the state file."""
    if not turn.session_id or not channel.turn_ours(cfg, turn.platform, turn.chat_id, turn.user_id):
        return
    if capture.OFF_RECORD.search(turn.user_message or ""):
        hookstate.mark_off_turn(cfg, str(turn.session_id), turn.turn_id)
    else:
        hookstate.clear_off_turn(cfg, str(turn.session_id))
