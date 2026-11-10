"""The busy-gap sweep: capture journal replies the gateway hook never saw.

Hermes runs ``pre_gateway_dispatch`` only when a message is admitted as a new turn. A message that
arrives while the companion is still replying takes the busy path instead: in ``interrupt`` mode
(the default) plain text is folded into the running turn (``redirect``) and media is queued and run
as an in-band follow-up; neither is ever admitted, so the hook never captures it. Hermes does persist
those messages to the sessions database, so every tick reads the chat transcript (``cfg.transcript()``,
read-only) and applies the same capture rules the hook would have, at the message's own time:

1. a direct reply to a journal prompt, recognised by the quoted text Hermes stores in front of it
   (``[Replying to: "…"]``), matched against the prompt's header and sent text, whatever its age;
2. otherwise the first message while a prompt was visible and unanswered;
3. ``/j <text>`` as its own note.

Never twice: captures are keyed by the platform's message id (ledger), rows the hook already handled carry
its ``[Journal reply — …]`` / ``[Journal note — …]`` note, and a watermark (the last row id seen)
keeps each sweep to new rows. The first sweep looks back ``sweep.lookback_hours`` (48 h). A missing
or locked database is logged and skipped; the next tick tries again.

What the database cannot tell: a text message folded into a running turn is stored without its
reply pointer, so for those only rule 2 can apply. The audio of a voice note is not linked to its
row, so a swept voice note is recorded as its transcript without the embedded recording (unless the
row names the cached file). A picture Hermes attached natively is named in its row: it is kept beside the entry
while Hermes' picture cache still holds it (a path anywhere else is not copied; a picture that is not kept is said
in the log, and his caption is still saved). Such a row sent with no caption is stored at the time its turn ran,
which for a queued message is later than he sent it; the cached file's time is when it arrived (``arrival``).

A row the transcript withheld from his words (``Row.meta["withheld"]``: it still carried something added to his
message) is never captured; the sweep logs it under ``WITHHELD``, which the tick files under its errors (not a row
the hook already saved: his words are in the entry as he sent them).
"""
from __future__ import annotations

import contextlib
import html
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import audio, capture, channel, compose, entries, images, ledger, library
from .config import Config
from .ports import Row

STATE = "sweep.json"
WITHHELD = "sweep: withheld"  # opens the log line of a withheld row: the tick files it in errors.jsonl
STATUSES = ("", "answered ✓", "missed — reply anytime", "sent again below")
PHASE_STATUSES = ("answered ✓", "open — reply anytime", "sent again below")  # what a folded phase header can end in


# ── reading rows ─────────────────────────────────────────────────────────────

def words(text: str) -> str:
    """Comparison form: markup, punctuation, emoji and spacing gone, lower case."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return " ".join(re.findall(r"\w+", text.lower()))


def message(row: Row) -> dict | None:
    """One of his rows as a capture candidate: {message_id, quote, text, source, audio_path} (+ images), or None
    when it is not his words (handled already, a mirror, a frame or placeholder, withheld, nothing) or the
    platform's internal notification. Another notice is read by its words, as the hook would have seen them. A
    picture attached natively is a candidate with or without a caption (``images``: the files its row names)."""
    if row.role != "user" or row.meta.get("internal"):
        return None
    pictures = list(row.meta.get("images") or ())
    if row.kind != "his" and not (row.kind == "empty" and pictures):
        return None
    return {"message_id": row.platform_message_id, "quote": row.quote, "text": row.text, "source": row.source,
            "audio_path": row.audio_path, **({"images": pictures} if pictures else {})}


def picture_caches(cfg: Config, db=None) -> list[Path]:
    """Hermes' picture cache of the profile, resolved: ``cache/images`` or the older ``image_cache`` in its home
    (gateway/platforms/base.py ``get_image_cache_dir``). The home is the profile's, or the directory of the sessions
    database the chat is read from (``db``, else ``hermes.state_db``)."""
    db = db if db is not None else cfg.state_db
    homes = [h for h in (cfg.hermes_home, Path(db).parent if db else None) if h is not None]
    return [(Path(h) / sub).resolve() for h in homes for sub in ("cache/images", "image_cache")]


def in_cache(path: str, caches) -> Path | None:
    """The picture's file, resolved, when it lies in one of Hermes' picture caches; None for a path anywhere else
    (a row can name any path: a picture is only ever copied from the cache Hermes wrote it to)."""
    try:
        found = Path(path).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    return found if any(found.is_relative_to(c) for c in caches) else None


def arrival(row: Row, pictures, since: datetime) -> datetime:
    """When a message with pictures came in. Hermes stores a picture sent with no caption at the time its turn ran,
    for a message queued behind a stuck turn long after he sent it; the gateway wrote each picture to its cache as
    it arrived (a new file every time), so the earliest file time after ``since`` and before the row's own time
    is when he sent it. ``pictures``: files in Hermes' cache only (``in_cache``)."""
    at = row.at
    for path in pictures or ():
        try:
            t = datetime.fromtimestamp(os.stat(path).st_mtime, timezone.utc)
        except (OSError, ValueError, OverflowError):
            continue
        if since <= t < at:
            at = t
    return at


def keep_pictures(cfg: Config, paths, entry_path: str, caches) -> tuple[list[str], list[str]]:
    """(kept, why not): each picture a row names, copied beside the entry (``images.keep``) when it is a picture
    file in Hermes' picture cache; one outside it, gone from it, too large or that cannot be copied is left out, with
    the reason (fixed words, never a path; his caption is still saved)."""
    kept, missed = [], []
    for path in paths or ():
        found = in_cache(path, caches)
        if found is None:
            missed.append("not in Hermes' picture cache")
        elif not images.media_type(found.name):
            missed.append("not a picture kind that is kept")
        elif not found.is_file():
            missed.append("gone from Hermes' picture cache")
        else:
            try:
                kept.append(images.keep(cfg, str(found), entry_path, found.suffix.lower()))
            except images.ImageTooLarge:
                missed.append("over images.max_mb")
            except (OSError, ValueError) as e:
                missed.append(f"the copy failed: {type(e).__name__}")
    return kept, missed


def _record(cfg: Config, pictures: list[str], *args, **kw) -> dict | None:
    """``capture.record``; the pictures already copied for it are removed again when nothing was recorded."""
    try:
        info = capture.record(cfg, *args, images=pictures, **kw)
    except BaseException:
        _drop(cfg, pictures)
        raise
    if not info:
        _drop(cfg, pictures)
    return info


def _drop(cfg: Config, names: list[str]) -> None:
    for name in names:
        with contextlib.suppress(OSError):
            os.unlink(os.path.join(str(cfg.root), name))


def parse(content: str, platform_message_id: str | None) -> dict | None:
    """One stored user message → {message_id, quote, text, source, audio_path} (None: not a candidate). The old
    entry point, over the Hermes adapter's reading (``journal.hermes.transcript.read``)."""
    from .hermes.transcript import read
    msg = read(content, platform_message_id)
    if msg["kind"] != "his":
        return None
    return {k: msg[k] for k in ("message_id", "quote", "text", "source", "audio_path")}


def rows(cfg: Config, db: Path, after_id: int, since: float) -> list[tuple]:
    """(id, ts, platform id, content, display kind) of his rows after ``after_id`` and newer than ``since``, as
    stored (the old shape, kept for callers of it; the sweep reads ``cfg.transcript()``)."""
    from .hermes.transcript import raw_rows
    return [(r["id"], r["ts"], r["pmid"], r["content"], r["display_kind"])
            for r in raw_rows(db, cfg.chat_id, after_id, since, platform=cfg.platform, users=cfg.owner_ids)
            if r["role"] == "user" and (r["ts"] is None or float(r["ts"]) > since)]  # (no time: kept, as stored)


def __getattr__(name: str):
    if name == "read":  # the row reading moved to the transcript adapter with the rest of Hermes' row semantics
        from .hermes.transcript import read
        return read
    raise AttributeError(name)


# ── matching (the hook's rules, at the message's own time) ───────────────────

def prompt_texts(cfg: Config, p: dict) -> list[str]:
    """What the prompt's Telegram message said at some point: each header state and the sent text."""
    out = []
    if p.get("sent_at") and p.get("kind") not in entries.FREEFORM_IDS:
        local = datetime.fromisoformat(p["sent_at"]).astimezone(cfg.tz)
        practice = library.for_prompt(cfg, p)
        out += [words(compose.header(practice, local, s, cfg.names)) for s in STATUSES]  # (folds before the opener stayed)
        # a fold keeps the opener: header, opener, status line; also of an earlier send of the prompt (a re-send)
        earlier = p.get("earlier_openers")
        sent = [p.get("opener"), p.get("sent_opener"), *(earlier if isinstance(earlier, list) else [])]
        for opener in dict.fromkeys(o for o in sent if isinstance(o, str) and o):  # (a damaged record never raises)
            out += [words(compose.folded_html(practice, local, opener, s, cfg.names)) for s in STATUSES[1:]]
    for key in ("plain_text", "html", "sent_plain_text", "sent_html"):  # (sent_*: a delivery onto a face's record)
        if p.get(key):
            out.append(words(p[key]))
    if p.get("program"):  # a phase folds to its own header line (tick.fold, programs): quoting that is a reply to it
        out += [words(compose.folded_phase_html(p, s, cfg.names)) for s in PHASE_STATUSES]
    return [t for t in out if t]


def by_quote(cfg: Config, data: dict, quote: str, at: datetime) -> dict | None:
    """The prompt a reply quotes: the quote (possibly truncated, or a selected part) is in what the
    prompt said, or it opens with the prompt's header. Latest prompt sent before the message wins."""
    q = words(quote)
    if len(q) < 8:
        return None
    found = []
    for p in data["prompts"]:
        if not channel.same_chat(cfg, p) or not p.get("message_id") or not p.get("sent_at"):
            continue
        if min(datetime.fromisoformat(t) for t in [*(p.get("earlier_sent_at") or []), p["sent_at"]]) > at:
            continue
        texts = prompt_texts(cfg, p)
        if any(q in t for t in texts) or (texts and q.startswith(texts[0])):
            found.append(p)
    return max(found, key=lambda p: p["sent_at"]) if found else None


def visible_at(cfg: Config, data: dict, at: datetime) -> dict | None:
    """The most recent prompt visible and still unanswered when the message was sent (or a program phase that was
    still open then: it takes every message)."""
    out = []
    for p in data["prompts"]:
        if not channel.same_chat(cfg, p) or not p.get("message_id") or not p.get("sent_at"):
            continue
        win = capture.window(cfg, p)
        # every send of the prompt: a message sent again (/start on an open phase) keeps taking replies sent
        # between the sends, which the sweep sees later
        if not any(datetime.fromisoformat(t) <= at < datetime.fromisoformat(t) + win
                   for t in [*(p.get("earlier_sent_at") or []), p["sent_at"]]):
            continue
        if capture.open_phase(p, at):  # a program phase still open then took every message (capture.match) …
            if not capture.superseded(data, p, at):  # … unless a later prompt had been sent by then
                out.append(p)
            continue
        if p.get("program"):  # a phase the run had moved on from takes only direct replies
            continue
        if p.get("collapsed_at") and datetime.fromisoformat(p["collapsed_at"]) <= at:
            continue
        if capture.interrupted(data, p, at):  # an interrupt had been sent since: a typed message answers neither
            continue
        if any(datetime.fromisoformat(c["at"]) <= at for c in p.get("captures") or []):
            continue
        if journaled_between(cfg, data, capture.sent_by(p, at), at):  # the writer has made an auto note of a message of
            continue  # his since the prompt went out (a sweep that failed a tick): that one answered the prompt
        out.append(p)
    return max(out, key=lambda p: capture.sent_by(p, at)) if out else None


def _between(at, start: datetime, end: datetime) -> bool:
    try:
        when = datetime.fromisoformat(at or "")
    except (TypeError, ValueError):
        return False
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return start <= when < end


def journaled_between(cfg: Config, data: dict, start: datetime, end: datetime) -> bool:
    """Whether the conversation writer has kept a message of his, sent in [start, end), in an auto note of its own
    making: a sweep that failed for a tick lets the writer get there first (an auto note, and the conversation after
    it). The next sweep must not then file his *next* message as the answer to a prompt that his earlier one, already
    journaled, was the answer to (it would be in two entries, and the wrong one would be the answer). A /j the writer
    recovered, and a conversation in an entry a reply went into, do not answer a prompt: the hook and the sweep
    handle those on their own."""
    from . import conversations
    autos = [p for p in data["prompts"] if p.get("capture") == "auto" and not p.get("asked") and not p.get("kept")]
    for p in autos:  # the ledger is durable: the note's own first message is in it even if the writer's state is not
        for c in p.get("captures") or []:
            if _between(c.get("at"), start, end):
                return True
    stems = {Path(p.get("entry_path") or "").stem for p in autos}
    for rec in (conversations.snapshot(cfg).get("written") or {}).values():
        if rec.get("entry") in stems and _between(rec.get("at"), start, end):
            return True
    return False


def reconcile(cfg: Config, msg: dict, at: datetime, log: list[str] | None = None, caches=None) -> dict | None:
    """Capture one stored message if the hook would have. Returns capture info or None. Its pictures, if any, are
    kept beside the entry and embedded above his caption, as the hook does; one that is not kept is said in ``log``.
    ``caches``: Hermes' picture caches (``picture_caches``)."""
    text = msg["text"]
    pictures = msg.get("images") or ()
    describe = images.describing(cfg)
    caches = picture_caches(cfg) if caches is None else caches

    def keep(entry_path: str) -> list[str]:
        kept, missed = keep_pictures(cfg, pictures, entry_path, caches)
        if log is not None:
            log.extend(f"sweep: a picture of message {msg['message_id']} was not kept ({why})" for why in missed)
        return kept

    after_j = capture.strip_command(text)
    if text.lstrip().startswith("/") and after_j is None:
        return None  # another slash command
    with ledger.locked(cfg.ledger_path) as data:
        if ledger.capture_of(data, msg["message_id"]):
            return None
        if after_j is not None:
            if not after_j.strip() and not pictures:
                return None
            p = capture.new_note_prompt(data, cfg, at)
            pics = keep(p["stem"] + ".md")
            if not after_j.strip() and not pics:
                data["prompts"].remove(p)  # nothing to save after all
                return None
            info = _record(cfg, pics, p, "/j", after_j, at, "photo" if pics else msg["source"], msg["message_id"],
                           via="sweep", describe=describe)
            p["needs_edit"] = False
            return info
        prompt = by_quote(cfg, data, msg["quote"], at) if msg["quote"] else None
        rule = "direct-reply"
        if prompt is None:
            prompt, rule = visible_at(cfg, data, at), "next-message"
        if prompt is None:
            return None
        kept = None
        stem = capture.entry_stem(cfg, prompt, data)  # the file record() will write; what it holds is kept beside it
        if msg.get("audio_path") and os.path.isfile(msg["audio_path"]):
            try:
                kept = audio.keep(cfg, msg["audio_path"], stem + ".md",
                                  os.path.splitext(msg["audio_path"])[1].lower() or ".ogg")
            except OSError:
                kept = None
        pics = keep(stem + ".md")
        words_ = text or (audio.NOT_TRANSCRIBED if msg["source"] == "audio" else "")
        if not words_ and not pics:
            return None
        info = _record(cfg, pics, prompt, rule, words_, at, "photo" if pics else msg["source"], msg["message_id"],
                       kept, via="sweep", transcript="pending" if kept and msg["source"] == "audio" else None,
                       data=data, describe=describe)
        if prompt.get("kind") == "j":
            prompt["needs_edit"] = False
        return info


# ── the sweep ────────────────────────────────────────────────────────────────

def _state_path(cfg: Config) -> Path:
    return cfg.state_dir / STATE


def _read_state(cfg: Config) -> dict:
    try:
        data = json.loads(_state_path(cfg).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(cfg: Config, state: dict) -> None:
    path = _state_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{STATE}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def lookback(cfg: Config) -> timedelta:
    """How far back the sweep looks (``sweep.lookback_hours``; an unusable value means 48 hours). One reading for the
    sweep and for the conversation writer that waits on it, so they never disagree about what the sweep still sees."""
    try:
        hours = float(cfg.sweep.get("lookback_hours", 48))
        return timedelta(hours=hours if 0 < hours < 24 * 3650 else 48)
    except (TypeError, ValueError, OverflowError):
        return timedelta(hours=48)


def active(cfg: Config) -> bool:
    """Whether the tick sweeps at all (it is on and there is a chat to read)."""
    return cfg.sweep.get("enabled") is not False and bool(cfg.chat_id)


def swept_upto(cfg: Config, source) -> int:
    """The last row id a sweep has passed in the sessions database ``source`` (0: none yet, or another database)."""
    state = _read_state(cfg)
    return int(state.get("last_id") or 0) if state.get("db") == source else 0


def run(cfg: Config, now: datetime, log: list[str], db: Path | None = None) -> int:
    """One sweep. Returns how many messages were captured. Never raises for database trouble."""
    if cfg.sweep.get("enabled") is False or not cfg.chat_id:
        return 0
    chat = cfg.transcript(db)
    if chat.source is None:
        return 0  # standalone: no sessions database to reconcile against
    state = _read_state(cfg)
    last = int(state.get("last_id") or 0) if state.get("db") == chat.source else 0
    since = (now - lookback(cfg)).timestamp()
    after = datetime.fromtimestamp(since, timezone.utc)
    try:
        if last and last > chat.max_id():
            last = 0  # the database was replaced; the time bound and message ids keep this safe
        found = [r for r in chat.rows(after_id=last, since=after) if r.role == "user" and r.at > after]
    except chat.errors as e:
        log.append(f"sweep skipped: sessions database unavailable ({type(e).__name__}: {str(e)[:120]})")
        return 0
    captured = 0
    from . import conversations  # (it reads rows through this module)
    from . import privacy
    pv = privacy.covering(cfg, after, now, db=db)  # (lookback_hours > 48)
    if pv.closed:
        log.append(f"sweep skipped: the privacy pass could not run ({getattr(pv, 'why', '')})")
        return 0  # (the watermark stays: these rows are swept once it can)
    off = conversations.snapshot(cfg)
    caches = picture_caches(cfg, chat.source)
    try:
        for row in found:
            # (the reason is the adapter's fixed label, never his words; a row the hook saved is in its entry as he
            # sent it, so there is nothing for anyone to look at)
            if row.meta.get("withheld") and row.kind != "handled":
                log.append(f"{WITHHELD} row {row.id} (message {row.platform_message_id or '-'}): what is left of it "
                           f"still carries {row.meta['withheld']}, so it is not read as his words")
            msg = message(row)
            if msg and msg["message_id"]:
                at = arrival(row, [p for p in msg.get("images") or () if in_cache(p, caches)], after)
                # (a /j inside a bare-/x window is public by the pass's own rule, so this asks every row: a /j
                # merged with a /x message on a later line is off the record, as the hook and the writer take it)
                if conversations.row_is_off(cfg, pv, row, off) or (at != row.at and pv.at(at)):
                    last = max(last, row.id)
                    continue  # off the record (/x), also by when it arrived: never captured
                info = reconcile(cfg, msg, at, log, caches)
                if info:
                    captured += 1
                    log.append(f"swept message {msg['message_id']} into {Path(info['path']).name} ({info['rule']})")
            last = max(last, row.id)
    finally:  # rows handled so far are never looked at again, even if a later one failed
        _write_state(cfg, {"db": chat.source, "last_id": last, "at": now.isoformat()})
    return captured
