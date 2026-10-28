"""The routes of ``journal serve`` that change something: his words, entry meta, the library, settings, and the
sends and program steps that are spawned rather than run here.

What keeps these safe is what they call, not what they check:

* **The engine only appends his words; he can change them.** A reply or a block goes through
  ``capture.capture_web`` / ``capture.add_web_block`` (``entries.record_reply``, under the ledger lock like the hook's
  and the tick's writes). The routes that change a file he already has are his own acts, each carrying the state he was
  looking at and refused on drift (409, never an overwrite): ``PATCH …/meta`` replaces the front matter alone
  (``entries.set_meta``), ``PATCH``/``DELETE …/blocks/<key>`` replace or remove one block of his and nothing else
  (``blocks``; DECISIONS.md, 2026-10-20). Everything outside what he changed is carried over byte for byte. Her words
  are never editable here.
* **``/x`` holds.** Text that starts with the opt-out command is never written: the request is refused with
  ``422 off-the-record`` and nothing is saved, so what he typed stays in the browser. A line shaped like the
  engine's own marker (``%% journal … %%``) is refused too: it could pass for her voice or a program's phase.
* **Locks are the engine's, taken in the engine's order and bounded** (``ledger.deadline``: 5 s, then ``423``).
* **A send or a program step is a spawned ``journal …`` process**, never run in a request thread: it waits for the
  tick's lock the way ``/start`` does from the chat, and answers ``202`` at once.
* **A definition is never saved unless it parses**, is rendered canonically and replaces the file only if the file
  is the one the editor read (mtime).

This module imports no module that reads the chat (``tests/test_serve_fence.py``).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from typing import Callable

from .. import audio, capture, entries, ledger, library, settings
from .. import config as jconfig
from . import api, blocks, document, uploads
from .api import HTTPError, Reply, Request

TEXT_LIMIT = 50_000       # characters of any text field (API.md Decision 7)
LOCK_WAIT = 5.0           # seconds a request waits for a lock before 423
FUTURE_SLACK = timedelta(minutes=5)
PAST_LIMIT = timedelta(days=31)   # how far back ``at`` may stamp a block: a late note, not a rewritten calendar
CLIENT_ID = re.compile(r"^[\w.-]{1,64}$")
MOOD_ITEM = re.compile(r"^[^,\[\]\n\r\t]{1,60}$")


# ── the shape every write shares ─────────────────────────────────────────────

def bounded(fn: Callable) -> Callable:
    """Run a write under the lock deadline: a lock not had in ``LOCK_WAIT`` seconds is 423, a filesystem that
    refuses the write is 503 (the request failed; the journal is as it was)."""
    @wraps(fn)
    def run(req: Request):
        try:
            with ledger.deadline(LOCK_WAIT):
                return fn(req)
        except ledger.LockBusy as e:
            raise HTTPError(423, "busy", str(e)) from None
        except OSError as e:
            raise HTTPError(503, "engine-unavailable", f"cannot write: {type(e).__name__}") from None
    return run


def _text(body: dict, key: str = "text") -> str | None:
    value = body.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPError(400, "bad-request", f"{key} must be text")
    if len(value) > TEXT_LIMIT:
        raise HTTPError(413, "too-large", f"{key} is at most {TEXT_LIMIT:,} characters")
    # one line ending, before any check reads it and before it is written: the engine's marker pattern knows only
    # "\n", and a file is read back with universal newlines, so a "\r" the checks passed over would become a line
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _his_words(req: Request, text: str | None) -> str | None:
    """His text, if he sent any, after the refusals: off the record, the engine's own marker lines, and the engine's
    transcript-pending placeholder."""
    if text is None:
        return None
    if capture.opt_out(req.cfg, text) is not None:
        raise HTTPError(422, "off-the-record", "this starts with the off-the-record command, so it is not saved; "
                                               "what you typed is still in your browser")
    if capture.opt_out_inside(req.cfg, text):  # as the chat takes a merged burst: a /x on any line covers all of it
        raise HTTPError(422, "off-the-record", "a line of this starts with the off-the-record command, so none of it "
                                               "is saved; what you typed is still in your browser")
    if entries.ENGINE_MARK.search(text):
        raise HTTPError(422, "reserved-line", "a line shaped like `%% journal … %%` is the engine's own marker and "
                                              "cannot be part of your words")
    if audio.TRANSCRIBING in text:  # the tick would fill it as a recording's block, and the editor would lock it
        raise HTTPError(422, "placeholder", f"“{audio.TRANSCRIBING}” is the journal's line for a recording still "
                                            "being transcribed and cannot be part of your words")
    return text


def _at(req: Request, body: dict, stem: str | None = None) -> datetime:
    """The time a block is stamped with: ``at`` (an ISO time with its offset, up to a month back and not in the
    future), else the server's clock. On an existing entry (``stem``) it cannot predate the entry's own day."""
    raw = body.get("at")
    if raw is None:
        return req.now
    try:
        when = datetime.fromisoformat(raw) if isinstance(raw, str) else None
    except ValueError:
        when = None
    if when is None or when.tzinfo is None:
        raise HTTPError(400, "bad-request", "at must be an ISO time with its offset, like 2026-10-15T09:12:00+05:30")
    try:
        if when > req.now + FUTURE_SLACK:
            raise HTTPError(400, "bad-request", "at is in the future")
        if when < req.now - PAST_LIMIT:
            raise HTTPError(400, "bad-request", f"at is more than {PAST_LIMIT.days} days ago")
        if stem and when.astimezone(req.cfg.tz).date().isoformat() < stem[:10]:
            raise HTTPError(400, "bad-request", "at is before the day of this entry")
    except (OverflowError, OSError, ValueError):   # a year-1 time cannot be converted to another offset
        raise HTTPError(400, "bad-request", "at is out of range") from None
    return when


def _client_id(body: dict) -> str | None:
    value = body.get("client_id")
    if value is None:
        return None
    if not isinstance(value, str) or not CLIENT_ID.match(value):
        raise HTTPError(400, "bad-request", "client_id is up to 64 letters, digits, . _ -")
    return value


def _client_seen(cfg: jconfig.Config, client_id: str, blocks: bool) -> bool:
    """Whether a request with this ``client_id`` was already written and is still remembered. Under the ledger lock:
    a retry that overlaps the original (which has claimed the upload and is still writing) waits for it, instead of
    reading the ledger as it was before the write."""
    with ledger.locked(cfg.ledger_path) as data:
        return capture.client_seen(data, client_id, blocks)


class _Words:
    """What a POST brings: text, an upload, or both."""

    def __init__(self, req: Request, body: dict, client_id: str | None = None, blocks: bool = False):
        self.client_id, self.blocks = client_id, blocks   # blocks: the route also dedupes against ``web_blocks``
        self.text = _his_words(req, _text(body))
        self.upload_id = body.get("upload")
        if self.upload_id is not None and not isinstance(self.upload_id, str):
            raise HTTPError(400, "bad-request", "upload must be an upload id")
        if not (self.text or "").strip() and not self.upload_id:
            raise HTTPError(400, "bad-request", "send text, an upload, or both")
        self.cfg = req.cfg
        self.up: uploads.Upload | None = None

    def __enter__(self):
        if self.upload_id:
            try:
                self.up = uploads.claim(self.cfg, self.upload_id)
            except KeyError:
                # A retry (same client_id) of a request that went through: its upload was consumed by the first
                # one. The capture below answers it with the first result and takes nothing from the upload.
                if not (self.client_id and _client_seen(self.cfg, self.client_id, self.blocks)):
                    raise HTTPError(404, "not-found", "that upload is unknown, used or older than an hour") from None
        return self

    def __exit__(self, kind, exc, tb):
        if self.up is not None:
            (uploads.release if exc else uploads.finish)(self.cfg, self.up)
        return False

    @property
    def args(self) -> dict:
        up = self.up
        meta = {"file_name": up.name} if up and up.name else None
        return {"media": str(up.path) if up else None, "ext": up.ext if up else ".ogg", "meta": meta}


# ── notes, entries, blocks, meta ─────────────────────────────────────────────

def _engine_entry(req: Request) -> Path:
    """The file a write goes to: an entry the engine wrote. One that is only named like an entry (his own page
    called ``2026-10-13-evening.md``) is readable but never written to: 409."""
    stem = req.params["stem"]
    if document.entry_file(req.cfg, stem) is None:
        raise HTTPError(404, "not-found", "no journal entry by that name")
    path = document.engine_file(req.cfg, stem)
    if path is None:
        raise HTTPError(409, "not-engine-entry", "this file is yours, not one the journal wrote, so the web app does "
                                                 "not write to it")
    return path


@bounded
def entry_create(req: Request):
    """A reply to a practice's open prompt (``practice``), else a /j note of its own. ``at`` stamps the heading."""
    body = req.json()
    practice = body.get("practice")
    if practice is not None:
        if not isinstance(practice, str):
            raise HTTPError(400, "bad-request", "practice must be a practice id")
        if practice not in req.lib().practices and practice not in entries.FREEFORM_IDS:
            raise HTTPError(404, "not-found", "no practice by that id")
    cid = _client_id(body)
    words, when = _Words(req, body, cid), _at(req, body)
    with words:
        info = capture.capture_web(req.cfg, practice, words.text or "", req.now, at=when, client_id=cid, **words.args)
    path = Path(info["path"])
    done = {"stem": path.stem, "path": str(path), "rule": info["rule"]}
    return Reply(200 if info.get("duplicate") else 201, done)


def _phase_target(req: Request, stem: str, body: dict) -> tuple[dict, str] | None:
    """The run and phase prompt a block on entry ``stem`` answers, or None for an entry that is not a program's.

    A program session's entry does not exist before its first answer, so the run (not the file) says what ``stem``
    is: some run's session has it as its entry. (If a file of his already sits at that name, the engine files the
    answer under the next free one, ``capture.entry_stem``, and the reply says which; his file is never written.) A block must name the phase it answers, and that phase must be the
    run's open one, delivered, with the run active: the rule a Telegram message follows, made explicit because a
    browser cannot rely on "the newest prompt in the chat". 409 ``phase-not-open`` otherwise."""
    from .. import programs
    found = None
    for run in programs.all_runs(req.cfg):
        for n in sorted(int(k) for k in run.get("sessions") or {} if k.isdigit()):
            if programs.entry_stem(req.cfg, run, n) == stem:
                found = (run, n)
    path = document.entry_file(req.cfg, stem)
    fm = entries.front_matter_of(path) if path else {}
    if found is None:
        if fm.get("program"):
            raise HTTPError(409, "phase-not-open", "this entry belongs to a program run that is no longer there, so "
                                                   "no phase of it is open")
        if body.get("phase") is not None:
            raise HTTPError(400, "bad-request", "phase is for the entries of a program session")
        return None
    run, s = found
    if req.lib().programs.get(run["program"]) is None:   # as ``api.wrong_state``: no definition, no open phase
        raise HTTPError(409, "phase-not-open", "the program's definition is missing, so no phase of this run is open",
                        run=run["id"], run_status=run.get("status"))
    phase = body.get("phase")
    if isinstance(phase, bool) or not isinstance(phase, int):
        raise HTTPError(400, "bad-request", "phase must be the number of the open phase of this session")
    if run.get("status") != "active":
        why = ("the program is on a break: resume it first" if run.get("status") == "paused"
               else f"the run is {run.get('status')}")
        raise HTTPError(409, "phase-not-open", why, run=run["id"], run_status=run.get("status"))
    cur = run.get("current") or {}
    ph = ((run.get("sessions") or {}).get(str(s)) or {}).get("phases", {}).get(str(phase)) or {}
    if (cur.get("session"), cur.get("phase")) != (s, phase) or not ph.get("prompt_id") or ph.get("send_due"):
        raise HTTPError(409, "phase-not-open", f"phase {phase} of this session is not the open phase",
                        run=run["id"], where=programs.where(run))
    return run, ph["prompt_id"]


def _after_phase_block(req: Request, info: dict) -> str:
    """Move the run on as the program's rule says, as a Telegram answer does: ``settle`` spawns ``journal program
    settle <run>`` (it waits ``programs.settle_minutes``, then advances if he has gone quiet); ``next`` waits for
    /next. A spawn that cannot start is not an error: the block is saved, and the tick's ``programs`` step settles
    the run a few minutes late. Returns what will happen: ``settle``, ``next`` or ``tick``."""
    prompt = info["prompt"]
    if prompt.get("advance", "settle") != "settle" or not prompt.get("run"):
        return "next"
    try:
        req.app.spawn(req.cfg, ["program", "settle", str(prompt["run"])])
    except OSError:
        return "tick"
    return "settle"


@bounded
def block_add(req: Request):
    """A block of his words appended to an entry. On a program session's entry it is the answer to the open phase
    (``phase``), and the run then moves on by the program's own rule."""
    body = req.json()
    stem, cid = req.params["stem"], _client_id(body)
    dup = ledger.capture_of_client(ledger.snapshot(req.cfg.ledger_path), cid) if cid else None
    # A retry of a block already written is answered from the ledger, even after the run moved on and wherever the
    # answer was filed (`stem` may be the planned name while a file of his holds it: the answer sits one name on).
    target = None if dup else _phase_target(req, stem, body)
    of_program = target is not None or bool(dup and dup[0].get("run"))
    if not of_program:
        _engine_entry(req)
    words, when = _Words(req, body, cid, blocks=True), _at(req, body, None if of_program else stem)
    if of_program:
        when = req.now   # an answer cannot predate its phase (nor the run's last /back): the server's clock, not `at`
    with words:
        try:
            info = capture.add_web_block(req.cfg, stem, words.text or "", when, client_id=cid,
                                         phase_prompt=target[1] if target else None,
                                         key_of=lambda p: _last_words_key(req, p), **words.args)
        except capture.PhaseClosed:
            raise HTTPError(409, "phase-not-open", "the run moved on from that phase; reload it") from None
    path = Path(info["path"])
    # the key the block got when it was written (kept with the capture); a record from before that was kept has
    # none, and the newest block is the best answer left
    done = {"key": info.get("block") or _last_words_key(req, path)}
    if of_program:   # (the stem too: the answer goes where the engine files the session, not always at `stem`)
        done["stem"] = path.stem
        if not info.get("duplicate"):
            done["advance"] = _after_phase_block(req, info)
    return Reply(200 if info.get("duplicate") else 201, done)


def _last_words_key(req: Request, path: Path) -> str | None:
    """The key of the last block of his words in the entry at ``path``: that of the block written last."""
    doc = document.document(req.cfg, path, req.lib())
    blocks = [b["key"] for b in doc["blocks"] if b["kind"] == "his" and b["key"].startswith("words:")]
    return blocks[-1] if blocks else None


# ── changing a block of his ──────────────────────────────────────────────────

BLOCK_KEY = re.compile(r"^(?:words|conv|his|her):[^\x00-\x1f/]{1,120}$")   # the keys the document gives its blocks


def _block_target(req: Request, body: dict) -> tuple[Path, str, str | None, str | None, float | None]:
    """(file, block key, the words he was reading, their time, the entry's mtime) of a change to one block. He must
    say what he was looking at: ``was`` (the block's words) or ``mtime`` (the entry's), or both. A block that reads
    differently, or an entry that moved, is ``409 changed`` and nothing is written."""
    key = req.params["key"]
    if not BLOCK_KEY.match(key):
        raise HTTPError(404, "not-found", "no block by that name")
    was, at, seen = body.get("was"), body.get("at"), body.get("mtime")
    if was is not None and not isinstance(was, str):
        raise HTTPError(400, "bad-request", "was must be the block's words as you read them")
    if at is not None and not isinstance(at, str):
        raise HTTPError(400, "bad-request", "at must be the block's time as you read it")
    if seen is not None and (isinstance(seen, bool) or not isinstance(seen, (int, float))):
        raise HTTPError(400, "bad-request", "mtime must be the number the entry was read with")
    if was is None and seen is None:
        raise HTTPError(400, "bad-request", "say what you were looking at: was (the block's words) or mtime (the "
                                            "entry's), so a change made meanwhile is not overwritten")
    if was is not None:
        was = was.replace("\r\n", "\n").replace("\r", "\n")
    return _engine_entry(req), key, was, at, None if seen is None else float(seen)


def _changed(fn: Callable[[str], str], req: Request, path: Path, seen: float | None) -> tuple[str, float]:
    """Run ``fn`` on the entry's text under the ledger lock (the one capture, the tick and the conversation writer take),
    write what it returns atomically, and give back the new text and mtime. Nothing is written when it refuses."""
    with ledger.locked(req.cfg.ledger_path):
        if seen is not None and abs(path.stat().st_mtime - seen) > 1e-6:
            raise HTTPError(409, "changed", "the entry changed since you read it; reload it and try again")
        old = path.read_text(encoding="utf-8")
        try:
            new = fn(old)
        except blocks.Refused as e:
            raise HTTPError(e.status, e.slug, e.detail) from None
        if new != old:
            entries.write_file(path, new)
        return new, path.stat().st_mtime


@bounded
def block_edit(req: Request):
    """Replace the words of one block of his (``PATCH …/blocks/<key>``): ``{text, was?, at?, mtime?}``. The block's
    heading, key line, time, audio and everything else in the file stay as they are. The new text goes through the
    same two refusals as a new block (``_his_words``): off the record (``/x`` holds on every surface, so words that
    begin with it, or with a line that begins with it, are not written, and the browser keeps them), and the engine's own marker lines. Plain edit, no
    history. Editing a phase answer of a completed program session also has its ``## Summary`` written again."""
    body = req.json()
    unknown = sorted(set(body) - {"text", "was", "at", "mtime"})
    if unknown:
        raise HTTPError(400, "bad-request", f"{', '.join(unknown)} cannot be sent here", keys=unknown)
    text = _text(body)
    path, key, was, at, seen = _block_target(req, body)
    if text is None:
        raise HTTPError(400, "bad-request", "text is the block's new words")
    text = blocks.clean(text)
    if not text.strip():
        raise HTTPError(400, "bad-request", "a block cannot be left empty: remove it instead")
    text = _his_words(req, text)
    new, mtime = _changed(lambda old: blocks.edit(old, key, text, was=was, at=at, stem=path.stem), req, path, seen)
    block = next((b for b in document.blocks_of(new, path.stem) if b["key"] == key), None)
    out = {"block": block, "mtime": mtime}
    refresh, why = _summary_refresh(req, new, path.stem, block)
    if refresh:
        out["summary"] = refresh
    if why:
        out["summary_reason"] = why
    return out


def _summary_refresh(req: Request, new: str, stem: str, block: dict | None) -> tuple[str | None, str | None]:
    """After an edit of a phase answer of a program session, have the session's ``## Summary`` written again from his
    words if it has one to write (``journal program refresh-summary``, spawned: it takes the tick's lock, which this
    server never does, and writes the summary under the ledger lock in the engine's order). ``"refreshing"`` when a
    session that has completed was sent for, ``"not-started"`` when that could not be (the edit is saved all the same;
    the summary keeps the old words), None when there is nothing to promise: not a phase answer, not a program's
    entry, or a session with no summary yet or one that waits for a transcript.

    The command is spawned for any session that is open or completed, not only for the ones due now: the tick may be
    in the middle of writing that summary (``complete_session`` reads the answers and only then marks the session
    completed; ``_refill_summary`` writes from the answers and only then clears its flag), and an edit saved in that
    gap is seen here as an open or waiting session while the summary written a moment earlier lacks it. The command
    waits for the tick's lock and decides then, so it does nothing unless the session is due by that time. It also
    keeps the entry as it is when the summary section is not what the engine wrote (DECISIONS.md, SUMMARY-REFRESH)."""
    if block is None or block.get("phase") is None:
        return None, None
    from .. import programs
    run_id = entries.front_matter(new).get("run")
    run = programs.find_run(req.cfg, run_id) if run_id else None
    s = programs.session_of_entry(req.cfg, run, stem) if run else None
    sess = ((run or {}).get("sessions") or {}).get(str(s), {})
    status = sess.get("status")
    if s is None or status not in ("completed", "in_progress"):
        return None, None
    promised = status == "completed" and programs.summary_refresh_due(run, s)
    if promised:
        why = entries.summary_unsafe_to_replace(new, sess.get("summary_sha"))
        if why:
            return "kept", why
    try:
        req.app.spawn(req.cfg, ["program", "refresh-summary", run["id"], "--session", str(s)])
    except OSError:
        return ("not-started" if promised else None), None
    return ("refreshing" if promised else None), None


@bounded
def block_delete(req: Request):
    """Remove one block of his (``DELETE …/blocks/<key>``): its key line, heading and body, and nothing else. The
    recording a voice block points at stays on disk. Takes ``{was?, at?, mtime?}`` as the edit does."""
    body = req.json()
    unknown = sorted(set(body) - {"was", "at", "mtime"})
    if unknown:
        raise HTTPError(400, "bad-request", f"{', '.join(unknown)} cannot be sent here", keys=unknown)
    path, key, was, at, seen = _block_target(req, body)
    _new, mtime = _changed(lambda old: blocks.remove(old, key, was=was, at=at, stem=path.stem), req, path, seen)
    return {"mtime": mtime}


def _meta_values(body: dict) -> dict[str, str | None]:
    unknown = sorted(set(body) - {*entries.META_KEYS, "mtime"})
    if unknown:
        raise HTTPError(400, "bad-request", f"{', '.join(unknown)} cannot be set here: only "
                                            f"{', '.join(entries.META_KEYS)} (date, kind, practice, program, session "
                                            "and run belong to the engine)", keys=unknown)
    out: dict[str, str | None] = {}
    for key in ("mood", "people"):
        if key not in body:
            continue
        items = body[key]
        if not isinstance(items, list) or len(items) > 40 or not all(isinstance(i, str) for i in items):
            raise HTTPError(400, "bad-request", f"{key} must be a list of short texts")
        clean = [i.strip() for i in items if i.strip()]
        if not all(MOOD_ITEM.match(i) and i.splitlines() == [i] for i in clean):   # no line-separator character
            raise HTTPError(400, "bad-request", f"each of {key} is up to 60 characters, without commas or brackets")
        out[key] = entries.flow_list(clean) if clean else None
    if "starred" in body:
        if not isinstance(body["starred"], bool):
            raise HTTPError(400, "bad-request", "starred must be true or false")
        out["starred"] = "true" if body["starred"] else None
    if not out:
        raise HTTPError(400, "bad-request", "nothing to change")
    return out


@bounded
def meta_patch(req: Request):
    body = req.json()
    changes = _meta_values(body)
    seen = body.get("mtime")
    if seen is not None and (isinstance(seen, bool) or not isinstance(seen, (int, float))):
        raise HTTPError(400, "bad-request", "mtime must be the number the entry was read with")
    path = _engine_entry(req)
    with ledger.locked(req.cfg.ledger_path):  # the same lock every other writer of entry files takes
        if seen is not None and abs(path.stat().st_mtime - float(seen)) > 1e-6:
            raise HTTPError(409, "mtime", "the entry changed since you read it; reload it")
        try:
            entries.set_meta(path, changes)
        except ValueError as e:
            raise HTTPError(409, "not-writable", str(e)) from None
        mtime = path.stat().st_mtime
    return {"meta": document.meta_of(path), "mtime": mtime}


@bounded
def upload_create(req: Request):
    try:
        return Reply(201, uploads.store(req.cfg, req.headers.get("Content-Type") or "", req.body))
    except uploads.BadUpload as e:
        raise HTTPError(e.status, "unsupported-media" if e.status == 415 else "bad-request", str(e)) from None


upload_create.body_limit = uploads.LIMIT


# ── library ──────────────────────────────────────────────────────────────────

ID = library.ID


def _library_view(req: Request, pid: str) -> dict:
    req._lib = None   # the write changed the files: read the library again
    return api.library_one(req)


def _practice_or_404(req: Request) -> tuple[str, library.Practice | None]:
    pid = req.params["id"]
    if not ID.fullmatch(pid):
        raise HTTPError(404, "not-found", "no practice by that id")
    return pid, req.lib().practices.get(pid)


def _active_runs(req: Request, pid: str) -> list[str]:
    """Ids of the runs of program ``pid`` that are active or paused: read from ``state_dir/runs/<pid>/`` (PRACTICES.md
    §4.1)."""
    return [r["id"] for r in library.runs_in_progress(req.cfg, pid)]


def _mtime_of(body: dict):
    seen = body.get("mtime")
    if seen is not None and (isinstance(seen, bool) or not isinstance(seen, (int, float))):
        raise HTTPError(400, "bad-request", "mtime must be the number the definition was read with")
    return seen


@bounded
def library_put(req: Request):
    """Save a definition: ``{raw}`` or ``{definition}`` with the ``mtime`` it was read at. ``?validate=1`` writes
    nothing and answers ``{valid, problems, raw}`` with the canonical text that would be saved. A built-in is edited
    in place: the first save makes his file of the same id (no ``mtime``: there is no file of his to have read). A
    program or situational definition takes one file at a time (``_program_put``)."""
    pid, current = _practice_or_404(req)
    body = req.json()
    dry = req.query.get("validate") not in (None, "", "0", "false")
    if current is None and library.program_kind(req.cfg, pid) is not None:
        return _program_put(req, pid, body, dry)
    if ("raw" in body) == ("definition" in body):
        raise HTTPError(400, "bad-request", "send either raw (the file's text) or definition")
    seen = _mtime_of(body)
    if not dry and pid in library.program_words(req.cfg)[0]:   # (the dry run says so as a problem, below)
        raise HTTPError(409, "is-program", f"{pid!r} is a program or situational definition, not a practice: a "
                                           "practice cannot take its id")
    problems: list[str] = []
    canonical = None
    try:
        if "raw" in body:
            if not isinstance(body["raw"], str):
                raise HTTPError(400, "bad-request", "raw must be text")
            if len(body["raw"]) > TEXT_LIMIT:
                raise HTTPError(413, "too-large", f"raw is at most {TEXT_LIMIT:,} characters")
            practice = library.parse(body["raw"])
        else:
            practice = library.from_dict(body["definition"])
        canonical, practice = library.write_text(practice)
        problems += library.check(practice, req.cfg)
        if practice.id != pid:
            problems.append(f"the definition's id is {practice.id!r} but the URL's is {pid!r}; an id does not change")
    except library.ParseError as e:
        problems += e.problems
        canonical = None
    if dry:
        return {"valid": not problems, "problems": problems, "raw": canonical}
    if problems:
        raise HTTPError(422, "invalid", "the definition is not a usable practice; nothing was saved", problems=problems)
    if current is not None and current.builtin:
        seen = None   # his edit of a built-in begins: no file of his was read, so there is none to have drifted
    elif current is not None and seen is None:
        raise HTTPError(400, "bad-request", "mtime is required to replace a definition (GET it first)")
    try:
        library.write(req.cfg, pid, canonical, seen)
    except library.Changed as e:
        raise HTTPError(409, "mtime", f"{e}; reload it, then save again") from None
    except FileExistsError as e:
        raise HTTPError(409, "exists", str(e)) from None
    return _library_view(req, pid)


def _program_put(req: Request, pid: str, body: dict, dry: bool):
    """``PUT /library/{id}`` for a program or situational definition: ``{raw, file?, mtime}``, one file as he typed it
    (``file``: a program's ``program.md`` or ``sessions/<name>.md``; a situational definition is one file). The whole
    definition it makes must parse, keep its id, and, while a run of it is in progress, keep its structure
    (``library.run_conflict``: 409 ``active-run``). His version of a built-in is made by the first save."""
    raw, name = body.get("raw"), body.get("file")
    if "definition" in body or (isinstance(raw, str) and entries.front_matter(raw).get("kind") == library.PRACTICE):
        why = f"{pid!r} is a program or situational definition, not a practice: a practice cannot take its id"
        if dry:
            return {"valid": False, "problems": [why], "raw": None}
        raise HTTPError(409, "is-program", why)
    if not isinstance(raw, str):
        raise HTTPError(400, "bad-request", "send raw: the file's text (a program is edited one file at a time)")
    if name is not None and not isinstance(name, str):
        raise HTTPError(400, "bad-request", "file must be a file name of the definition")
    if len(raw) > TEXT_LIMIT:
        raise HTTPError(413, "too-large", f"raw is at most {TEXT_LIMIT:,} characters")
    seen = _mtime_of(body)
    raw = raw.replace("\r\n", "\n").replace("\r", "\n")
    try:
        d = library.draft(req.cfg, pid, name, raw)
    except library.UnknownDefinition:
        raise HTTPError(404, "not-found", "no practice or program by that id") from None
    except ValueError as e:
        raise HTTPError(404, "not-found", str(e)) from None
    problems = list(d.problems)
    conflict = library.run_conflict(req.cfg, pid, d.program) if d.program is not None else None
    if dry:
        return {"valid": not problems and conflict is None, "problems": problems + ([conflict] if conflict else []),
                "raw": raw if not problems else None}
    if problems:
        raise HTTPError(422, "invalid", "the definition does not load; nothing was saved", problems=problems)
    if d.builtin:
        seen = None   # his edit of a built-in begins with this save
    elif seen is None:
        raise HTTPError(400, "bad-request", "mtime is required to replace a file (GET it first)")
    try:   # the run guard and the other files are checked again under the directory's lock
        library.save_draft(req.cfg, d, seen)
    except library.RunInProgress as e:
        raise HTTPError(409, "active-run", str(e), runs=e.runs) from None
    except library.Changed as e:
        raise HTTPError(409, "mtime", f"{e}; reload it, then save again") from None
    except FileExistsError as e:
        raise HTTPError(409, "exists", str(e)) from None
    return _library_view(req, pid)


@bounded
def library_fork(req: Request):
    """Copy a built-in into ``practices_dir`` to edit by hand (a program: its whole directory). The web app no longer
    needs it (a save edits a built-in in place); the route stays for the CLI's parity and older clients."""
    pid = req.params["id"]
    if not ID.fullmatch(pid):
        raise HTTPError(404, "not-found", "no practice by that id")
    lib = req.lib()
    if pid not in lib.practices and pid not in lib.programs:
        raise HTTPError(404, "not-found", "no practice or program by that id")
    if library.shipped(pid) is None:
        raise HTTPError(409, "not-builtin", "only a built-in can be forked")
    try:
        library.fork(req.cfg, pid)
    except library.AlreadyForked as e:
        raise HTTPError(409, "already-forked", str(e)) from None
    return Reply(201, _library_view(req, pid))


@bounded
def library_delete(req: Request):
    """Remove the user's file: a built-in he edited goes back to the default (``library.reset``; a program's run in
    progress keeps its structure, so a reset that would change it is 409 ``active-run``), a practice of his own is
    gone."""
    pid, current = _practice_or_404(req)
    ship = library.shipped(pid)
    if ship is not None and library.his_path(req.cfg, pid) is not None:
        try:   # (a program's run guard is checked under the directory's lock)
            library.reset(req.cfg, pid)
        except library.RunInProgress as e:
            raise HTTPError(409, "active-run", str(e), runs=e.runs) from None
        return Reply(204)
    own = Path(req.cfg.practices_dir) / f"{pid}.md"
    if current is None and not os.path.lexists(own):
        if ship is not None:
            raise HTTPError(409, "builtin", "this is the built-in itself; there is no edit of yours to reset")
        raise HTTPError(404, "not-found", "no practice by that id")
    if current is not None and current.builtin and not os.path.lexists(own):
        raise HTTPError(409, "builtin", "this is the built-in itself; there is no file of yours to remove")
    # A plain practice has no runs. A run under its id belongs to a program it hides, and deleting the practice is
    # what gives that program (and the run) back, so the guard is for a definition that is not a practice.
    active = _active_runs(req, pid) if current is None else []
    if active:
        raise HTTPError(409, "active-run", "a run of this is active: finish or abandon it first", runs=active)
    library.remove(req.cfg, pid)
    return Reply(204)


# ── spawned: sends and program steps ─────────────────────────────────────────

def spawn_journal(cfg: jconfig.Config, args: list[str]) -> str:
    """Run ``journal <args>`` detached, with the same ``--config`` / ``--hermes-home`` as this server, its output
    appended to ``manual-trigger.log``. It outlives the request, and takes the tick's lock itself. It outlives a
    restart of this server only if the unit says ``KillMode=process`` (``serve/unit.py``): systemd's default stops
    every process of the unit's cgroup, and ``start_new_session`` does not leave the cgroup, so a child that is
    waiting for the tick's lock would be killed by the restart. Returns the log path. ``OSError`` when it cannot
    start."""
    argv = [sys.executable, "-m", "journal"]
    if cfg.config_file:
        argv += ["--config", str(cfg.config_file)]
    if cfg.hermes_home:
        argv += ["--hermes-home", str(cfg.hermes_home)]
    env = dict(os.environ)
    if cfg.hermes_home:   # as the plugin's own spawn (hermes/runtime.child_env): without it the child's mirror into
        env["HERMES_HOME"] = str(cfg.hermes_home)   # the chat session refuses ("not the journal's profile home")
    src = str(Path(__file__).resolve().parents[2])  # the directory holding the ``journal`` package
    env["PYTHONPATH"] = os.pathsep.join([src, *[p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]])
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    log = cfg.state_dir / "manual-trigger.log"
    with open(log, "a") as out:
        subprocess.Popen(argv + args, cwd=str(cfg.state_dir), stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                         start_new_session=True, env=env)
    return str(log)


def _spawned(req: Request, args: list[str]) -> Reply:
    try:
        log = req.app.spawn(req.cfg, args)
    except OSError as e:
        raise HTTPError(503, "engine-unavailable", f"cannot start it: {type(e).__name__}") from None
    return Reply(202, {"spawned": True, "log": log})


def library_send(req: Request):
    pid, current = _practice_or_404(req)
    if current is None:
        raise HTTPError(404, "not-found", "no practice by that id")
    if not current.enabled:
        raise HTTPError(409, "disabled", "this practice is turned off (enabled: false)")
    return _spawned(req, ["send", pid])


# Programs: every transition is `journal program <action> …` spawned (it takes the tick's lock and, for a send,
# talks to Telegram), after the request has checked the run is in a state the action means something in: a wrong
# state is 409 here instead of an error line in manual-trigger.log nobody reads.

EXPECT = re.compile(r"^\d{1,3}:\d{1,3}:\d{1,6}$")


def _one_at_a_time(req: Request, program_id: str) -> None:
    """409 ``program-in-progress`` (with ``run`` and ``program``: the unfinished one) when another program (not
    situational) has an unfinished run, so this one cannot start (``programs.one_at_a_time``). The spawned child asks
    again under the tick's lock and refuses too (in ``manual-trigger.log``) should a start from another face have won
    the race in between."""
    from .. import programs
    other = programs.blocking(req.cfg, program_id)
    if other is not None:
        raise HTTPError(409, "program-in-progress", programs.busy_text(req.cfg, other, program_id, web=True),
                        run=other["id"], program=other["program"])


def run_start(req: Request):
    """``{program, session?}``: begin a program (``journal program start``). A run in progress, paused or not, is
    resumed through its own routes (409 ``active-run`` names it); a situational run idle past ``expires_hours`` is
    closed by ``start`` itself, so it does not count."""
    from .. import programs
    body = req.json()
    pid = body.get("program")
    if not isinstance(pid, str):
        raise HTTPError(400, "bad-request", "program must be a program id")
    prog = req.lib().programs.get(pid)
    if prog is None:
        raise HTTPError(404, "not-found", "no program by that id")
    if not prog.enabled:
        raise HTTPError(409, "disabled", "this program is turned off (enabled: false)")
    session = body.get("session")
    if session is not None and (isinstance(session, bool) or not isinstance(session, int)
                                or not 1 <= session <= len(prog.sessions)):
        raise HTTPError(400, "bad-request", f"session is a number from 1 to {len(prog.sessions)}")
    run = programs.current_run(req.cfg, prog.id)
    if run is not None and not programs.is_expired(req.cfg, run, req.now):
        raise HTTPError(409, "active-run", "this program has a run in progress: carry on with it, or abandon it first",
                        run=run["id"], run_status=run["status"])
    _one_at_a_time(req, prog.id)
    args = ["program", "start", prog.id, "--new"] + (["--session", str(session)] if session else [])
    return _spawned(req, args)


def run_action(req: Request):
    """``next|skip|back|pause|resume|abandon|insights`` on a run. ``expect`` (``S:P:V``, the ``where`` the run was
    read at) binds next, skip and back to the phase the person was looking at: if the run has moved on (a settle, a
    tap in the chat) it is 409 ``moved-on`` and nothing is spawned, as a stale Telegram button does nothing."""
    from .. import programs
    action = req.params["action"]
    run = programs.find_run(req.cfg, req.params["id"])
    if run is None:
        raise HTTPError(404, "not-found", "no run by that id")
    body = req.json()
    why = api.wrong_state(run, action, req.lib().programs.get(run["program"]) is not None)
    if why:
        raise HTTPError(409, "wrong-state", why, run=run["id"], run_status=run.get("status"))
    args = ["program", action, run["id"]] + (["--paused"] if action == "resume" else [])
    expect = body.get("expect")
    if expect is not None:
        if action not in ("next", "skip", "back") or not isinstance(expect, str) or not EXPECT.match(expect):
            raise HTTPError(400, "bad-request", "expect is S:P:V, for next, skip or back")
        if run.get("current") and programs.where(run) != expect:
            raise HTTPError(409, "moved-on", "the run is not at that phase any more; reload it",
                            run=run["id"], where=programs.where(run))
        args += ["--expect", expect]
    # (a run's own transitions are never refused for another program: an unfinished run already holds its slot)
    return _spawned(req, args)


# ── settings ─────────────────────────────────────────────────────────────────

STATUS = {settings.KEY_NOT_ALLOWED: 400, settings.BAD_REQUEST: 400, settings.INVALID: 422,
          settings.NOT_WRITABLE: 409, settings.UNAVAILABLE: 503}


@bounded
def settings_patch(req: Request):
    """Change settings the web may change: ``settings.apply``, the one function every face uses (the allow-list, the
    ranges, which layer owns a key, the place lookup), with its refusals given their status. A key Hermes owns is
    409 with the ``hermes config set`` line, an env-owned one is 409, a key not on the allow-list is 400, a value
    out of range 422. Nothing is written unless every key can be."""
    body = req.json()
    if not body:
        raise HTTPError(400, "bad-request", "nothing to change")
    try:
        done = settings.apply(req.cfg, body, reload=req.app.config)
    except settings.SettingsError as e:
        extra = {k: v for k, v in (("keys", e.keys), ("problems", e.problems), ("command", e.command),
                                                ("commands", e.commands)) if v}
        raise HTTPError(STATUS[e.code], e.code, e.message, **extra) from None
    return {"written": done["written"]}


@bounded
def settings_near(req: Request):
    """"Use my location": ``{"lat", "lon"}`` from the browser, turned into a place and a time zone by
    ``settings.apply_near`` and saved like a typed place (``location.set_location``: the config's three values and a
    line in ``location-history.jsonl``). The answer names the place and the time zone, not the position; nothing here
    logs the body."""
    body = req.json()
    try:
        done = settings.apply_near(req.cfg, body.get("lat"), body.get("lon"), reload=req.app.config)
    except settings.SettingsError as e:
        extra = {k: v for k, v in (("problems", e.problems), ("command", e.command), ("commands", e.commands)) if v}
        raise HTTPError(STATUS[e.code], e.code, e.message, **extra) from None
    found = done["location"] or {}
    return {"written": done["written"], "location": found.get("location"), "timezone": found.get("timezone"),
            "local_time_now": found.get("local_time_now")}


# ── the table ────────────────────────────────────────────────────────────────

ACTIONS = "next|skip|back|pause|resume|abandon|insights"
WRITE = api.WRITE

ROUTES: list[tuple[str, str, Callable, str]] = [
    ("POST", r"/api/v1/entries", entry_create, WRITE),
    ("POST", r"/api/v1/entries/(?P<stem>[^/]+)/blocks", block_add, WRITE),
    ("PATCH", r"/api/v1/entries/(?P<stem>[^/]+)/blocks/(?P<key>[^/]+)", block_edit, WRITE),
    ("DELETE", r"/api/v1/entries/(?P<stem>[^/]+)/blocks/(?P<key>[^/]+)", block_delete, WRITE),
    ("PATCH", r"/api/v1/entries/(?P<stem>[^/]+)/meta", meta_patch, WRITE),
    ("POST", r"/api/v1/uploads", upload_create, WRITE),
    ("PUT", r"/api/v1/library/(?P<id>[^/]+)", library_put, WRITE),
    ("POST", r"/api/v1/library/(?P<id>[^/]+)/fork", library_fork, WRITE),
    ("DELETE", r"/api/v1/library/(?P<id>[^/]+)", library_delete, WRITE),
    ("POST", r"/api/v1/library/(?P<id>[^/]+)/send", library_send, WRITE),
    ("POST", r"/api/v1/runs", run_start, WRITE),
    ("POST", rf"/api/v1/runs/(?P<id>[^/]+)/(?P<action>{ACTIONS})", run_action, WRITE),
    ("PATCH", r"/api/v1/settings", settings_patch, WRITE),
    ("POST", r"/api/v1/settings/location", settings_near, WRITE),
]
