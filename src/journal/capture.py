"""Decide whether an incoming message is a journal reply, and save it.

Rules:
1. A direct reply to a journal prompt (where the platform says what a message replies to) is always a journal
   reply, at any age. Every direct reply is appended to that entry.
2. Otherwise, the first message after a prompt while it is still visible (before it collapses)
   answers the most recent visible prompt sent in that chat. A program phase that is still the run's open phase takes every
   message while it is visible, not only the first: a correction or a second voice note inside the settle joins
   the phase (and under ``advance: next`` every reply does, until /next). That holds only while the phase is the
   newest prompt sent in the chat: a ritual sent after it is answered as it always was, and the phase goes back to
   taking direct replies only. A phase the run has moved on from (answered, skipped, paused) takes only direct
   replies.
   An interrupt sent after a prompt takes that prompt out of this rule (it answers only to a direct reply).
3. Anything else is ordinary conversation.
"""
from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import audio as jaudio
from . import channel, entries, ledger, library
from .config import Config


def _aware(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def window(cfg: Config, prompt: dict) -> timedelta:
    """How long a sent prompt stays visible (then it folds and stops taking the next message): its practice's
    ``collapse_hours``, which the tick records on a user practice's prompt, else ``windows.collapse_hours``. The
    built-ins record none, so the configured window still governs the four rituals as it always did."""
    hours = prompt.get("collapse_hours")
    if isinstance(hours, (int, float)) and not isinstance(hours, bool) and hours > 0:
        # held to a year: a window ``timedelta`` cannot hold would raise in the hook for every ritual's reply, and
        # in the fold for every prompt, not only for the practice whose file says it
        return timedelta(hours=min(hours, library.MAX_COLLAPSE_HOURS))
    return timedelta(hours=min(cfg.collapse_hours, library.MAX_COLLAPSE_HOURS))


def open_phase(prompt: dict, at: datetime | None = None) -> bool:
    """A program phase that is (``at``: was, at that moment) the open phase of its run: it takes more than one
    message. ``programs`` sets ``phase_open`` when the phase is sent and clears it when the run moves on or goes on a
    break, keeping the spans in between (``open_spans``) for the sweep, which asks about a message's own time."""
    if not prompt.get("program"):
        return False
    if at is not None and prompt.get("open_spans") is not None:  # when it was open, send to close (programs._span)
        return any(_aware(a) <= at and (b is None or at < _aware(b)) for a, b in prompt["open_spans"])
    if prompt.get("phase_open"):
        return True
    return at is not None and bool(prompt.get("advanced_at")) and at < _aware(prompt["advanced_at"])


def sent_by(prompt: dict, at: datetime | None = None) -> datetime:
    """When the prompt was last sent by ``at`` (now, without it): a prompt sent again has its earlier sends in
    ``earlier_sent_at`` (``tick.send_prompt``), and each kept taking replies until the next."""
    sends = [_aware(t) for t in [*(prompt.get("earlier_sent_at") or []), prompt["sent_at"]]]
    return max((t for t in sends if at is None or t <= at), default=min(sends))


def superseded(data: dict, prompt: dict, at: datetime | None = None) -> bool:
    """Whether another prompt was sent in the chat after ``prompt`` (and, with ``at``, by then): the newest prompt
    is the one his next message answers, so an open phase beneath a later prompt stops taking every message. A
    session's interrupt is not such a prompt: it takes no typed message itself (only a direct reply), so it leaves
    an open phase taking his messages as before (``interrupted`` is what keeps a ritual from taking them)."""
    sent = sent_by(prompt, at)
    return any(q is not prompt and not q.get("interrupt") and q.get("message_id") and q.get("sent_at")
               and channel.same_chats(q, prompt) and sent < _aware(q["sent_at"])
               and (at is None or _aware(q["sent_at"]) <= at) for q in data["prompts"])


def interrupted(data: dict, prompt: dict, at: datetime | None = None) -> bool:
    """Whether a session's interrupt was sent in the chat after ``prompt`` (and, with ``at``, by then). An interrupt
    is answered only by replying to it, so it takes no message itself; but it is the newest thing in the chat, and a
    typed answer to it must not be filed as the answer to an older ritual that is still visible."""
    sent = sent_by(prompt, at)
    return any(q.get("interrupt") and q.get("message_id") and q.get("sent_at") and channel.same_chats(q, prompt)
               and sent < _aware(q["sent_at"]) and (at is None or _aware(q["sent_at"]) <= at) for q in data["prompts"])


def open_prompts(data: dict, cfg: Config, now: datetime) -> list[dict]:
    """The prompts still taking a reply: sent, inside their window, and either unanswered and not folded (a practice's
    prompt) or a program phase that is the open phase and the newest prompt in the chat."""
    return [
        p for p in data["prompts"]
        if p.get("message_id") and now - _aware(p["sent_at"]) < window(cfg, p)
        and ((open_phase(p) and not superseded(data, p))
             or (not p.get("program") and not p.get("collapsed_at") and not p.get("captures")))
    ]


def match(data: dict, cfg: Config, chat_id: str, reply_to: str | None, now: datetime) -> tuple[dict | None, str]:
    if not cfg.chat_id or str(chat_id) != str(cfg.chat_id):
        return None, ""
    if reply_to:
        p = ledger.by_message_id(data, chat_id, reply_to, cfg.platform)
        if p:
            return p, "direct-reply"
    # (an open program phase keeps taking his typed messages beneath an interrupt: it is open on purpose and takes
    # every message, which an interrupt, answered only by replying to it, does not change; a prompt sent in another
    # chat, before the channel moved, takes nothing typed here)
    visible = [p for p in open_prompts(data, cfg, now)
               if channel.same_chat(cfg, p) and (p.get("program") or not interrupted(data, p))]
    if visible:
        return max(visible, key=lambda p: p["sent_at"]), "next-message"
    return None, ""


def _engine_entry_of(path, kind: str) -> bool:
    """The file is an entry the engine wrote for practice ``kind`` (its front matter says so), not his own."""
    fm = entries.front_matter_of(path)
    return fm.get("type") == entries.ENGINE_TYPE and kind in (fm.get("practice"), fm.get("kind"))


def _same_session(a: dict, b: dict) -> bool:
    return bool(a.get("run")) and a.get("run") == b.get("run") and a.get("session") == b.get("session")


def entry_stem(cfg: Config, prompt: dict, data: dict | None = None) -> str:
    """The stem of the entry a prompt's replies go into (``data``: the ledger, to see other prompts' entries).

    Once the prompt has an entry, that one. Before its first reply, its own stem (``<date>-<id>``, ``-<n>`` for a
    later slot) unless a file is already there that is not this practice's engine entry (a hand-written
    ``2026-10-14-thoughts.md`` beside a practice ``thoughts``, another practice's entry) or that another prompt
    writes to: then the first free ``<date>-<id>-<n>`` past the practice's slot numbers, so it never takes a later
    slot's name, as ``new_note_prompt`` finds the next free note. His file is never written to. A /j note's stem was
    chosen free when it was made."""
    if prompt.get("entry_path"):  # the file its replies are in, whatever a later re-send wrote into ``stem``
        return Path(prompt["entry_path"]).stem
    for q in (data or {}).get("prompts", []):  # a session's phases share the entry its first answer went into
        if q is not prompt and q.get("entry_path") and _same_session(q, prompt):
            return Path(q["entry_path"]).stem
    stem = prompt.get("stem") or entries.stem_for(prompt["local_date"], prompt["kind"])
    if prompt.get("captures") or prompt["kind"] in entries.FREEFORM_IDS:
        return stem
    others = [p for p in (data or {}).get("prompts", []) if p is not prompt and p.get("id") != prompt.get("id")
              and not _same_session(p, prompt)]  # a session's phases share its entry
    taken = {p.get("stem") for p in others} | {Path(p["entry_path"]).stem for p in others if p.get("entry_path")}
    path = entries.entry_path(cfg.root, stem)
    if stem not in taken and (not path.exists() or _engine_entry_of(path, prompt["kind"])):
        return stem
    n = max(len(library.for_prompt(cfg, prompt).schedule), 1) + 1
    while True:
        fresh = entries.stem_for(prompt["local_date"], prompt["kind"], n)
        if fresh not in taken and not entries.entry_path(cfg.root, fresh).exists():
            return fresh
        n += 1


def record(cfg: Config, prompt: dict, rule: str, text: str, now: datetime,
           source: str = "text", platform_message_id: str | None = None, audio: str | None = None,
           meta: dict | None = None, via: str | None = None, transcript: str | None = None,
           data: dict | None = None, client_id: str | None = None, images: list[str] | None = None,
           describe: bool = False) -> dict:
    """Write the reply into the journal and note it on the ledger prompt (caller holds the lock; ``data`` is
    the ledger it holds, so a first reply's entry never takes another prompt's file: ``entry_stem``).

    ``now`` is when the message was sent (the heading's time); ``via`` marks captures made by the
    sweep; ``transcript: "pending"`` asks the tick to transcribe the kept audio file.

    ``images``: the pictures kept with the message (``images.keep``), embedded in the block above his caption (the
    ``text``); ``describe`` asks the tick for the companion's one-line description of each (``images.describe``).
    """
    local_now = now.astimezone(cfg.tz)
    prompt["stem"] = entry_stem(cfg, prompt, data)
    # the practice titles a new entry and gives it its bank; for_prompt never raises, so a broken definition can
    # cost the bank in the file, never the capture
    practice = None if prompt["kind"] in entries.FREEFORM_IDS else library.for_prompt(cfg, prompt)
    path = entries.record_reply(cfg.root, prompt, text, local_now, source, audio, cfg.names, meta, practice,
                                images or ())
    prompt.setdefault("captures", []).append({
        "at": now.isoformat(),
        "rule": rule,
        "source": source,
        "platform_message_id": platform_message_id,
        **({"audio": audio} if audio else {}),
        **({"images": list(images)} if images else {}),
        **({"describe": "pending"} if images and describe else {}),
        **({"meta": meta} if meta else {}),
        **({"via": via} if via else {}),
        **({"client_id": client_id} if client_id else {}),
        **({"transcript": transcript} if transcript else {}),
    })
    prompt["entry_path"] = str(path)
    prompt["needs_edit"] = True
    return {"prompt": dict(prompt), "path": str(path), "rule": rule}


def capture(cfg: Config, chat_id: str, reply_to: str | None, text: str, now: datetime,
            source: str = "text", platform_message_id: str | None = None) -> dict | None:
    """Save the message if it is a journal reply. Returns capture info or None."""
    if not text or not text.strip() or text.lstrip().startswith("/"):
        return None
    with ledger.locked(cfg.ledger_path) as data:
        done = ledger.capture_of(data, platform_message_id)
        if done:  # already captured (by the sweep, or a redelivered update): never twice
            return {**planned_info(cfg, done[0]), "rule": done[1].get("rule", ""), "duplicate": True}
        prompt, rule = match(data, cfg, chat_id, reply_to, now)
        if not prompt:
            return None
        return record(cfg, prompt, rule, text, now, source, platform_message_id, data=data)


def capture_to(cfg: Config, pid: str, rule: str, text: str, now: datetime,
               source: str, platform_message_id: str | None = None, audio: str | None = None,
               meta: dict | None = None, transcript: str | None = None, images: list[str] | None = None,
               describe: bool = False) -> dict | None:
    """Save into a known prompt (voice notes: matched at arrival, written after transcription; photos: matched
    and written at arrival, with the pictures kept beside the entry)."""
    with ledger.locked(cfg.ledger_path) as data:
        prompt = ledger.find(data, pid)
        if not prompt or ledger.capture_of(data, platform_message_id):
            return None
        info = record(cfg, prompt, rule, text, now, source, platform_message_id, audio, meta,
                      transcript=transcript, data=data, images=images, describe=describe)
        if prompt.get("kind") == "j":
            prompt["needs_edit"] = False  # nothing to fold in Telegram
        return info


# ── the web app's writes (``journal serve``): the same entries, the same ledger lock ─────────────────────────────

WEB_RULE = "web"   # the capture rule of a reply written from the web app to a practice's open prompt
WEB_VIA = "web"    # ``captures[].via``: where the web app's captures came from, whatever their source
MCP_VIA = "mcp"    # the same for ``journal mcp`` (an MCP client): rule, source and via all say ``mcp``


def block_key(key_of: Callable[[Path], str | None] | None, path: Path) -> str | None:
    """The key ``key_of`` gives the block just written to ``path`` (None without one, or when it cannot say: the
    words are already in the file, and a key only labels them)."""
    if key_of is None:
        return None
    try:
        return key_of(path)
    except Exception:  # noqa: BLE001
        return None


def _web_record(cfg: Config, data: dict, prompt: dict, rule: str, text: str, now: datetime, client_id: str | None,
                media: str | None, ext: str, meta: dict | None,
                key_of: Callable[[Path], str | None] | None = None, via: str = WEB_VIA) -> dict:
    """Write one web reply into ``prompt``'s entry (caller holds the ledger lock). An audio file is kept beside
    the entry exactly as the Telegram path keeps one, and waits for the tick's transcript when a backend exists; the
    caption, if any, stays as he wrote it. ``via`` names the face that wrote it (``web``, or ``mcp``): it is the
    capture's ``via`` and, for typed words, its ``source`` (the entry's heading says ``text`` for both)."""
    kept = None
    if media:
        kept = jaudio.keep(cfg, media, str(entries.entry_path(cfg.root, entry_stem(cfg, prompt, data))), ext)
    pending = bool(kept) and jaudio.transcription_enabled(cfg)
    words = text.strip() or ((jaudio.TRANSCRIBING if pending else jaudio.NOT_TRANSCRIBED) if kept else "")
    try:
        # the client id is its own field, never ``platform_message_id``: that is a Telegram message id, and the
        # tick's annotation reply threads under it
        info = record(cfg, prompt, rule, words, now, "audio" if kept else via, None, kept, meta, via=via,
                      transcript="pending" if pending else None, data=data, client_id=client_id)
    except BaseException:
        if kept:  # nothing references the copy
            Path(cfg.root, kept).unlink(missing_ok=True)
        raise
    if prompt.get("kind") in entries.FREEFORM_IDS or not prompt.get("message_id"):
        prompt["needs_edit"] = False  # no Telegram message to fold
    if key_of is not None:   # still under the lock: the key of the block written now, kept for a retry to return
        key = block_key(key_of, Path(info["path"]))
        if key:
            prompt["captures"][-1]["block"] = info["block"] = key
    return info


def capture_web(cfg: Config, practice: str | None, text: str, now: datetime, *, media: str | None = None,
                ext: str = ".ogg", meta: dict | None = None, client_id: str | None = None,
                at: datetime | None = None, via: str = WEB_VIA) -> dict:
    """A reply written in the web app: into the open prompt of ``practice`` when it has one (rule ``web``; the
    Telegram prompt then folds to "answered"), else a /j note of its own (rule ``/j``). ``client_id`` makes a retry
    harmless: a second request with the same id returns the first one's result (``duplicate``). Returns what
    ``record`` does: ``{prompt, path, rule}``.

    ``now`` is the server's clock: it decides which prompt is open. ``at`` (default ``now``) is only the time the
    heading carries, so a note he backdates cannot answer a prompt that was sent after the moment he gives: that
    is a /j note of its own, stamped ``at``.

    ``via``: the face writing (``web``; ``mcp`` for ``journal mcp``). A reply to an open prompt then has that rule
    (``web`` / ``mcp``), and the capture that source and via; nothing else differs."""
    at = at or now
    with ledger.locked(cfg.ledger_path) as data:
        done = ledger.capture_of_client(data, client_id)
        if done:
            return {**planned_info(cfg, done[0]), "rule": done[1].get("rule", ""), "duplicate": True}
        found = [p for p in open_prompts(data, cfg, now) if p.get("kind") == practice and at >= _aware(p["sent_at"])
                 ] if practice else []
        if found:
            prompt, rule = max(found, key=lambda p: p["sent_at"]), via   # WEB_RULE for the web app
        else:
            prompt, rule = new_note_prompt(data, cfg, at), "/j"
        return _web_record(cfg, data, prompt, rule, text, at, client_id, media, ext, meta, via=via)


def prompt_of_entry(data: dict, stem: str) -> dict | None:
    """The ledger prompt whose replies are in entry ``stem`` (None once the ledger has pruned it, or when the
    companion started the entry)."""
    for p in data["prompts"]:
        if p.get("captures") and (Path(p["entry_path"]).stem if p.get("entry_path") else p.get("stem")) == stem:
            return p
    return None


WEB_BLOCK_KEEP = timedelta(days=3)   # how long a retried block on an entry with no ledger prompt is remembered


class PhaseClosed(Exception):
    """The program phase a web block was meant for is not the run's open phase (any more)."""


def add_web_block(cfg: Config, stem: str, text: str, now: datetime, *, media: str | None = None, ext: str = ".ogg",
                  meta: dict | None = None, client_id: str | None = None, phase_prompt: str | None = None,
                  key_of: Callable[[Path], str | None] | None = None) -> dict:
    """Append a block of his words to the engine entry ``stem`` (the caller has checked it is the engine's own
    file). Through the entry's ledger prompt when it still has one, so the tick sees the reply (annotations wait on
    the first one); otherwise straight into the file. Nothing already in the file is touched. A repeated
    ``client_id`` returns the first result on either path (an entry with no prompt remembers it for a few days in
    the ledger's ``web_blocks``). Returns ``{path, rule, key?}``.

    ``phase_prompt``: the ledger id of a program phase prompt. The block is that phase's answer, keyed like a
    Telegram answer (``%% journal phase:N %%``), and is written only while the phase is still the run's open phase
    (checked here under the ledger lock, where ``programs`` closes it; the caller has checked ``stem`` is the run's
    session entry, and the answer goes where ``entry_stem`` says); otherwise
    ``PhaseClosed`` and nothing is written. Moving the run on is the caller's: the settle or ``/next``.

    ``key_of(path)`` names the block just written (the web document's key for it); it is called under the lock and
    its answer kept with the capture, so a retry returns the first block's key even after others were added."""
    with ledger.locked(cfg.ledger_path) as data:
        done = ledger.capture_of_client(data, client_id)
        if done:
            return {**planned_info(cfg, done[0]), "rule": done[1].get("rule", ""), "duplicate": True,
                    "block": done[1].get("block")}
        seen = data.setdefault("web_blocks", {})
        for cid in [k for k, v in seen.items() if _stale(v)]:
            del seen[cid]
        if client_id and client_id in seen:
            return {"path": str(entries.entry_path(cfg.root, seen[client_id]["stem"])), "rule": WEB_RULE,
                    "untracked": True, "duplicate": True, "block": seen[client_id].get("block")}
        if phase_prompt is not None:
            prompt = ledger.find(data, phase_prompt)
            if prompt is None or not open_phase(prompt):
                raise PhaseClosed(phase_prompt)
            return _web_record(cfg, data, prompt, WEB_RULE, text, now, client_id, media, ext, meta, key_of)
        prompt = prompt_of_entry(data, stem)
        if prompt is not None:
            return _web_record(cfg, data, prompt, WEB_RULE, text, now, client_id, media, ext, meta, key_of)
        path = entries.entry_path(cfg.root, stem)
        kept = jaudio.keep(cfg, media, str(path), ext) if media else None
        # no ledger prompt, so no capture for the tick to find: an audio file here is kept but not transcribed
        words = text.strip() or (jaudio.NOT_TRANSCRIBED if kept else "")
        try:
            entries.record_reply(cfg.root, {"stem": stem}, words, now.astimezone(cfg.tz), "audio" if kept else "web",
                                 kept, cfg.names, meta)
        except BaseException:
            if kept:
                Path(cfg.root, kept).unlink(missing_ok=True)
            raise
        key = block_key(key_of, path)
        if client_id:
            seen[client_id] = {"stem": stem, "at": datetime.now(timezone.utc).isoformat(), **({"block": key} if key else {})}
        return {"path": str(path), "rule": WEB_RULE, "untracked": True, **({"block": key} if key else {})}


def client_seen(data: dict, client_id: str, blocks: bool = True) -> bool:
    """Whether a web request with this ``client_id`` is already written and still remembered: a capture, or (with
    ``blocks``, for the route that dedupes against it) a block on an entry with no ledger prompt that
    ``web_blocks`` has not yet let go of (``WEB_BLOCK_KEEP``)."""
    if ledger.capture_of_client(data, client_id):
        return True
    if not blocks:
        return False
    rec = (data.get("web_blocks") or {}).get(client_id)
    return rec is not None and not _stale(rec)


def _stale(entry: dict) -> bool:
    try:
        return datetime.now(timezone.utc) - _aware(entry["at"]) > WEB_BLOCK_KEEP
    except (KeyError, TypeError, ValueError):
        return True


JOURNAL_COMMAND = re.compile(r"^\s*/(?:j|journal)(?:@\w+)?(?:\s+|$)", re.I)


def strip_command(text: str) -> str | None:
    """Return the text after a /j or /journal command, or None if it isn't one."""
    m = JOURNAL_COMMAND.match(text or "")
    return text[m.end():] if m else None


def new_note_prompt(data: dict, cfg: Config, now: datetime) -> dict:
    """A fresh record for one /j note: each note is its own entry file (…-j.md, …-j-2.md)."""
    local_date = now.astimezone(cfg.tz).date().isoformat()
    taken = {p.get("stem") for p in data["prompts"]}
    n, stem = 1, entries.stem_for(local_date, "j")
    while stem in taken or entries.entry_path(cfg.root, stem).exists():
        n += 1
        stem = entries.stem_for(local_date, "j", n)
    p = channel.stamp(cfg, {"id": stem, "stem": stem, "kind": "j", "local_date": local_date, "chat_id": cfg.chat_id,
                            "message_id": None, "sent_at": now.isoformat(), "opener": "", "opener_source": "/j"})
    data["prompts"].append(p)
    return p


def capture_note(cfg: Config, text: str, now: datetime, source: str = "text",
                 platform_message_id: str | None = None, audio: str | None = None) -> dict:
    with ledger.locked(cfg.ledger_path) as data:
        done = ledger.capture_of(data, platform_message_id)
        if done:
            return {**planned_info(cfg, done[0]), "rule": "/j", "duplicate": True}
        p = new_note_prompt(data, cfg, now)
        info = record(cfg, p, "/j", text, now, source, platform_message_id, audio)
        p["needs_edit"] = False  # nothing to fold in Telegram
        return info


def reserve_note(cfg: Config, now: datetime) -> dict:
    """For a /j voice note: create its record now; the transcript is written after STT."""
    with ledger.locked(cfg.ledger_path) as data:
        return dict(new_note_prompt(data, cfg, now))


def release_note(cfg: Config, pid: str) -> None:
    """Take back a note ``reserve_note`` made that nothing was written into (a captureless prompt only)."""
    with ledger.locked(cfg.ledger_path) as data:
        p = ledger.find(data, pid)
        if p is not None and not p.get("captures"):
            data["prompts"].remove(p)


def note_context(cfg: Config, info: dict) -> str:
    n = cfg.names
    who = n.user or "The user"
    # Keep the fixed ending ("Don't edit the file.]"): readers strip this note by it (context.clean_user_text).
    return (
        f"[Journal note — {who} used /j; the journal system saved this verbatim to {info['path']}. "
        f"{n.subject.capitalize()} chose to keep it, so it matters to {n.object}: read around it before you answer "
        f"(older entries, your notebook, whatever it touches), following the journal:journal-system skill, then "
        f"respond to what {n.subject} actually wrote; a short answer is fine when that's what fits. Your chat replies "
        f"are saved into the entry too. Don't edit the file.]\n\n"
    )


def opt_out(cfg: Config, text: str) -> str | None:
    """The text after the opt-out command (``conversations.opt_out_prefix``, default ``/x``), "" for the bare
    command, or None when the message doesn't start with it."""
    from .config import opt_out_prefix
    prefix = opt_out_prefix(cfg)
    m = re.match(rf"^\s*{re.escape(prefix)}(?:@\w+)?(?:\s+|$)", text or "", re.I)
    return (text or "")[m.end():] if m else None


def opt_out_inside(cfg: Config, text: str) -> bool:
    """Whether a later line of the message starts with the opt-out command while the message itself does not.

    Hermes merges an album's captions and a burst of messages sent within a moment into one event, joined by
    line breaks, so a ``/x`` he put on the second picture or message sits mid-text. The command is only ever read at
    the start, so the whole event is taken as off the record instead (fail closed: nothing is kept or written)."""
    if opt_out(cfg, text) is not None:
        return False
    return any(opt_out(cfg, line) is not None for line in (text or "").splitlines()[1:])


OFF_RECORD_END = "it stays out of the journal.]"
OFF_RECORD = re.compile(r"\[Off the record — .*?" + re.escape(OFF_RECORD_END) + r"\s*", re.S)


def stored_opener(cfg: Config, record: dict | None) -> str | None:
    """The opener a send record holds, for a reader that shows it again (the web app, a Telegram fold), or None.
    It is her text, written by the tick's model call. The check is narrow, and is not a privacy pass: it catches a
    literal echo, an opener with a line that starts with the opt-out command or that holds the engine's
    off-the-record notice, and then withholds the whole opener. It cannot tell whether the model drew on something
    said off the record and put it in other words; what keeps that out is what the tick gives the model."""
    text = (record or {}).get("opener")
    if not isinstance(text, str) or not text.strip():
        return None
    if opt_out(cfg, text) is not None or opt_out_inside(cfg, text) or OFF_RECORD.search(text):
        return None
    return text


def off_record_note(cfg: Config, thread: bool = False) -> str:
    """Prefix the agent sees on an off-the-record message (``/x``); the stored row keeps it, so every reader
    can tell the message apart even without the plugin's state."""
    n = cfg.names
    who = n.user or "The user"
    what = ("this conversation is off the record until it goes quiet" if thread else
            f"{who} marked this message /x")
    return (f"[Off the record — {what}: the journal will not save it or your reply. Answer as you normally would, "
            f"but don't add any of it to the journal (no journal_add_note); {OFF_RECORD_END}\n\n")


ENTRY_ID = re.compile(r"^[a-z][a-z0-9-]*$")


def add_companion_note(cfg: Config, text: str, now: datetime, local_date: str | None = None,
                       kind: str | None = None, known_ids: Callable[[], Iterable[str]] | None = None,
                       check: Callable[[Path], None] | None = None) -> str:
    """The companion's own addition, appended under its notes heading; never touches the user's words.

    ``kind`` is any id the engine knows (``known_ids``, by default ``entries.known_ids``: the rituals plus ``j``
    and ``note``) or the id of an engine entry that already exists that day (a user practice the library has
    since lost still has its history); anything else is refused, so a misspelling never lands a note on the wrong
    day. The same ids decide which files of the day are the engine's, so an id the caller's library knows finds
    its entry even without front matter, and a hand-written file never receives her note (``entries.entry_key``).

    ``check(path)``, when given, runs under the ledger lock on the file the note is about to land in, and may
    raise ``ValueError`` to turn the note away (a face that refuses files it does not write to uses it).
    """
    if not text or not text.strip():
        raise ValueError("note text is empty")
    local_now = now.astimezone(cfg.tz)
    local_date = local_date or local_now.date().isoformat()
    datetime.strptime(local_date, "%Y-%m-%d")  # validate
    kind = kind.strip().lower() if kind else None
    ids = tuple(dict.fromkeys((*(known_ids() if known_ids else entries.known_ids()), *entries.FREEFORM_IDS)))
    day = entries.entries_on(cfg.root, local_date, ids)
    if kind:
        if not ENTRY_ID.match(kind) or (kind not in ids and not any(entries.kind_of(p, ids) == kind for p in day)):
            raise ValueError(f"unknown kind {kind!r}")
        # "note" (the tool's word for a freeform entry) means a /j note or a companion-started one
        wanted = set(entries.FREEFORM_IDS) if kind in entries.FREEFORM_IDS else {kind}
        day = [p for p in day if entries.kind_of(p, ids) in wanted]
    # A ritual's name alone makes a file an entry, so his own 2026-10-14-evening.md can sit beside the engine's
    # -evening-2.md (where entry_stem put his reply): her note goes to the one the engine wrote.
    day = [p for p in day if entries.front_matter_of(p).get("type") == entries.ENGINE_TYPE] or day
    if day:
        path = day[-1]
    else:  # nothing that day: a companion-started note entry
        stem = entries.next_stem(cfg.root, local_date, "note")
        path = entries.entry_path(cfg.root, stem)
    with ledger.locked(cfg.ledger_path):  # every writer of entry files takes this lock (hook, tick, tools)
        if check:
            check(path)
        entries.add_companion_note(path, text, local_now, {"kind": "note", "local_date": local_date}, cfg.names)
    return str(path)


def planned_info(cfg: Config, prompt: dict, data: dict | None = None) -> dict:
    """Where a reply to ``prompt`` is (or will be) written, before it is: what a voice note is told and kept beside
    while it waits for its transcript (``data``: the ledger the prompt was matched in; see ``entry_stem``)."""
    return {"prompt": prompt, "path": str(entries.entry_path(cfg.root, entry_stem(cfg, prompt, data)))}


def context_note(cfg: Config, info: dict) -> str:
    """Prefix the agent sees, so the companion knows which prompt this answers."""
    p = info["prompt"]
    n = cfg.names
    opener = (p.get("opener") or "").strip()
    asked = f" {n.companion}'s opener for that prompt was: “{opener}”" if opener else ""
    return (
        f"[Journal reply — saved verbatim by the journal system as {n.user_possessive} answer to the "
        f"{p['kind']} journal of {p['local_date']} ({info['path']}).{asked} "
        f"Respond as {n.companion} following the journal:journal-system skill. Do not edit the journal file; "
        f"the rest of this conversation (your replies too) is saved into the entry, and "
        f"annotations are written automatically.]\n\n"
    )
