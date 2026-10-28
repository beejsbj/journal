"""The read routes of ``journal serve`` (and the primitives every route shares): one function per route, each a read of
the engine's own structures. The routes that change something are in ``writes``.

Every shape is something the engine already has, serialised (``today.collect``, ``Library.to_dict``,
``status.status``, the entry files through ``document``), never a second model of the journal (API.md). What no
route reads, by decision (API.md Decision 2): the chat transcript (``state.db``), ``conversations.json``, the
privacy pass, ``packs/``, ``audio-stash/``, ``mirror-queue.json`` and the notebook. This module and its siblings
import none of the modules that do (``tests/test_serve_fence.py``), and every route runs with
``sqlite3.connect`` made to fail (``tests/test_serve_privacy.py``).

Reads take no lock: every engine write is an atomic replace, so a read sees a whole file. The only writes here
are pairing's (a short ``locked_state`` on ``serve-pairings.json``); the journal's own writes are ``writes``.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .. import audio, capture, entries, images, ledger, library, location, reflect, status, today, version
from .. import settings as settings_
from ..config import Config, effective_today, profile_from_home
from . import auth, document

log = logging.getLogger(__name__)

JSON_LIMIT = 1024 * 1024  # bytes of a JSON body
LIST_LIMIT = 200          # most items one page of /entries may carry
DAYS_LIMIT = 400          # widest /days or /insights range


# ── request and response primitives ──────────────────────────────────────────

class HTTPError(Exception):
    """An error reply: ``{"error": slug, "detail": line}`` with ``status``."""

    def __init__(self, status: int, slug: str, detail: str = "", **extra):
        super().__init__(detail or slug)
        self.status, self.slug, self.detail, self.extra = status, slug, detail, extra

    def payload(self) -> dict:
        return {"error": self.slug, "detail": self.detail, **self.extra}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, str]
    params: dict[str, str]
    headers: Any
    body: bytes
    cfg: Config
    now: datetime
    app: Any
    scheme: str = "http"
    credential: str = ""        # how it was signed in: "tailscale", "token", "cookie"; "" on a public route
    login: str | None = None    # the Tailscale login, when credential is "tailscale"
    _lib: Any = None

    def lib(self) -> library.Library:
        """The practice library, loaded once per request."""
        if self._lib is None:
            try:
                self._lib = library.load(self.cfg)
            except RuntimeError as e:  # the packaged built-ins are unreadable: an install fault
                raise HTTPError(503, "engine-unavailable", str(e)) from None
        return self._lib

    def json(self) -> dict:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise HTTPError(400, "bad-request", "the body is not JSON") from None
        if not isinstance(data, dict):
            raise HTTPError(400, "bad-request", "the body must be a JSON object")
        return data


@dataclass
class Reply:
    status: int = 200
    payload: Any = None             # JSON-serialisable, or None for no body
    headers: list = field(default_factory=list)


@dataclass
class FileReply:
    path: Path
    content_type: str
    ranges: bool = False            # media: honour ``Range: bytes=…``
    cache: str = "no-store"


@dataclass
class StreamReply:
    """A long-lived ``text/event-stream``: ``run(write, stop)`` writes events until the client goes or the server
    stops; ``release`` runs afterwards (the stream slot)."""
    run: Callable
    release: Callable = lambda: None


# ── query helpers ────────────────────────────────────────────────────────────

def _date(req: Request, name: str, default: date | None = None) -> date | None:
    raw = req.query.get(name)
    if not raw:
        return default
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPError(400, "bad-request", f"{name} must be a date like 2026-10-14 (got {raw!r})") from None


def _int(req: Request, name: str, default: int, lo: int, hi: int) -> int:
    raw = req.query.get(name)
    if raw is None or raw == "":
        return default
    if not re.fullmatch(r"\d{1,9}", raw):
        raise HTTPError(400, "bad-request", f"{name} must be a whole number (got {raw!r})")
    return max(lo, min(hi, int(raw)))


def _range(req: Request, default_days: int) -> tuple[date, date]:
    today_local = req.now.astimezone(req.cfg.tz).date()
    to = _date(req, "to", today_local)
    start = _date(req, "from", to - timedelta(days=default_days - 1))
    if start > to:
        raise HTTPError(400, "bad-request", "from is after to")
    if (to - start).days >= DAYS_LIMIT:
        raise HTTPError(400, "bad-request", f"at most {DAYS_LIMIT} days at a time")
    return start, to


def _mtime(path: Path | None) -> float | None:
    try:
        return path.stat().st_mtime if path else None
    except OSError:
        return None


# ── health and pairing ───────────────────────────────────────────────────────

def healthz(req: Request):
    last = ledger.read_state(req.cfg.state_dir / "last-tick.json")
    return {"ok": True, **version.stamp(), "tick_last_at": last.get("at")}


def pair(req: Request):
    """Redeem a one-time pairing code for the session cookie."""
    code = req.json().get("token")
    if not isinstance(code, str) or not auth.redeem(req.cfg, code):
        raise HTTPError(401, "pairing", "this pairing link is unknown, used or expired: run `journal serve pair` again")
    value = auth.session_value(req.app.cookie_key(req.cfg), req.app.token(req.cfg))
    return Reply(204, None, [("Set-Cookie", auth.cookie_header(value, secure=req.app.secure_cookie(req)))])


def session(req: Request):
    """How this request is signed in, for the web app: ``{"via": "tailscale", "login": …}`` (pairing has nothing
    to do, so the app hides it and Unpair), else ``{"via": "cookie" | "token", "login": null}``."""
    return {"via": req.credential, "login": req.login}


def logout(req: Request):
    """Forget the cookie in this browser (``journal serve token --rotate`` signs out every browser)."""
    return Reply(204, None, [("Set-Cookie", auth.cookie_header("", secure=req.app.secure_cookie(req), clear=True))])


# ── today, entries, days ─────────────────────────────────────────────────────

def _sent_prompts(cfg: Config) -> list[dict]:
    """The ledger's sent prompts of the structured practices (a /j note or a freeform capture is no prompt, and
    neither is a program phase: ``/skip`` records on the run, not the ledger, so a skipped or passed-over phase
    would read as missed; Today's rows leave program prompts out too, ``today.collect``)."""
    return [p for p in ledger.snapshot(cfg.ledger_path)["prompts"]
            if p.get("message_id") and p.get("sent_at") and p.get("kind") not in entries.FREEFORM_IDS
            and not p.get("program")]


def _prompt_outcome(cfg: Config, p: dict, now: datetime) -> str | None:
    """``answered`` (it has a capture), ``missed`` (its window has closed with none), ``open`` (still waiting) or None
    (a record the engine would not have written)."""
    if p.get("captures"):
        return "answered"
    try:
        return "missed" if now - datetime.fromisoformat(p["sent_at"]) >= capture.window(cfg, p) else "open"
    except (TypeError, ValueError):
        return None


def week_strip(cfg: Config, now: datetime, days_: int = 7) -> list[dict]:
    """The Today view's week strip: the last ``days_`` local days, oldest first, each with how many prompts were
    answered and missed (from the ledger, which keeps recent weeks only) and one ``state``: ``answered`` (all of the
    day's prompts, none waiting), ``partial`` (some answered, some missed or still waiting), ``missed`` (none answered)
    or ``none`` (nothing sent, or only open prompts)."""
    end = now.astimezone(cfg.tz).date()
    prompts = _sent_prompts(cfg)
    out = []
    for back in range(days_ - 1, -1, -1):
        day = end - timedelta(days=back)
        outcomes = [_prompt_outcome(cfg, p, now) for p in prompts if p.get("local_date") == day.isoformat()]
        answered, missed, waiting = outcomes.count("answered"), outcomes.count("missed"), outcomes.count("open")
        state = ("none" if not answered and not missed else "missed" if not answered
                 else "answered" if not missed and not waiting else "partial")
        out.append({"date": day.isoformat(), "weekday": day.strftime("%a"), "answered": answered, "missed": missed,
                    "state": state})
    return out


def today_(req: Request):
    """``today.collect`` (the dict the thread-start block renders; DECISIONS.md, WP9) plus ``week``: the
    strip the Today view draws, and with ``stems`` the entry stem on each row and the reflection. ``week`` is added here, not in ``collect``, so the block and its goldens stay as
    they are (DECISIONS.md, WP11). Each row that was sent also carries its ``prompt`` (``document.prompt_of``: the
    opener and the question set, as the entry's document shows them), so Today shows every prompt in full whatever
    its state; only Telegram folds a finished one. ``voices`` names the two voices, as the document does, so the
    prompt is labelled with the companion's name."""
    data = today.collect(req.cfg, req.now, stems=True)
    rows = (data.get("practices") or {}).get("rows") or []
    if any(r.get("stem") for r in rows):
        try:
            records = document.prompt_records(req.cfg)
        except Exception:  # noqa: BLE001 - the ledger unreadable: the rows stand, without their prompts
            log.exception("journal serve: today: the prompts could not be read")
            records = None
        for r in rows if records is not None else ():
            if not r.get("stem"):
                continue
            try:  # one bad record costs its own card's prompt, never the route
                r["prompt"] = document.prompt_of(req.cfg, req.lib(), r["id"], records.get(r["stem"]))
            except Exception:  # noqa: BLE001
                log.exception("journal serve: today: the prompt of %s could not be built", r.get("id"))
    return {**data, "week": week_strip(req.cfg, req.now),
            "voices": {"his": req.cfg.names.user or "You", "hers": req.cfg.names.companion},
            # the header shows the place: when OpenStreetMap named it, the page credits it there (Nominatim's policy)
            "location_source": location.place_source(req.cfg)}


def _order(item: tuple[Path, tuple]) -> tuple:
    path, _key = item
    info = document.parsed(path) or {}
    return (path.stem[:10], info.get("time") or "", path.stem)


def _filtered(req: Request, start: date | None, end: date | None) -> list[tuple[Path, tuple]]:
    q = (req.query.get("q") or "").strip()
    if len(q) > 200:
        raise HTTPError(400, "bad-request", "q is at most 200 characters")
    return filter_entries(req.cfg, start, end, kind=req.query.get("kind") or None,
                          program=req.query.get("program") or None, q=q, audio_only=req.query.get("audio") == "1")


def filter_entries(cfg: Config, start: date | None = None, end: date | None = None, *, kind: str | None = None,
                   program: str | None = None, q: str = "", audio_only: bool = False) -> list[tuple[Path, tuple]]:
    """The engine entries that pass the filters, newest first: what ``/entries`` and ``/days`` page through, and
    what ``journal mcp``'s ``journal_entries`` lists. ``q`` is a substring of **his words only**
    (``document.matches``)."""
    out = []
    for path, key in document.all_entries(cfg):
        day = path.stem[:10]
        if (start and day < start.isoformat()) or (end and day > end.isoformat()):
            continue
        if kind and key[0] != kind:
            continue
        if program:
            fm = (document.parsed(path) or {}).get("fm") or {}
            if fm.get("program") != program and not (key[0] == program and key[1] is not None):
                continue
        if q and not document.matches(path, q):
            continue
        if audio_only and not (document.parsed(path) or {}).get("flags", {}).get("audio"):
            continue
        out.append((path, key))
    return sorted(out, key=_order, reverse=True)


def entries_list(req: Request):
    """EntrySummary pages, newest first. ``q`` searches his words only; ``cursor`` is the offset ``next`` gave."""
    start, end = _date(req, "from"), _date(req, "to")
    if start and end and start > end:
        raise HTTPError(400, "bad-request", "from is after to")
    limit = _int(req, "limit", 50, 1, LIST_LIMIT)
    offset = _int(req, "cursor", 0, 0, 10 ** 9)
    found = _filtered(req, start, end)
    page = found[offset:offset + limit]
    lib = req.lib()
    items = [s for s in (document.summary(req.cfg, path, lib) for path, _ in page) if s]
    nxt = str(offset + limit) if offset + limit < len(found) else None
    return {"items": items, "next": nxt}


def _day_reflection(cfg: Config, day: str) -> str | None:
    found = entries.analyses_on(cfg.root, day, "reflection")
    return found[-1].stem if found else None


def days(req: Request):
    """Entries grouped by day for History, newest day first (days with an entry or a reflection)."""
    start, end = _range(req, 14)
    lib = req.lib()
    by_day: dict[str, list[dict]] = {}
    for path, _key in _filtered(req, start, end):
        s = document.summary(req.cfg, path, lib)
        if s:
            by_day.setdefault(s["date"], []).append(s)
    out = []
    d = end
    while d >= start:
        day = d.isoformat()
        ref = _day_reflection(req.cfg, day)
        if day in by_day or ref:
            out.append({"date": day, "entries": by_day.get(day, []), "reflection": ref})
        d -= timedelta(days=1)
    return out


def entry_doc(req: Request):
    path = document.entry_file(req.cfg, req.params["stem"])
    if path is None:
        raise HTTPError(404, "not-found", "no journal entry by that name")
    return document.document(req.cfg, path, req.lib())


# ── media ────────────────────────────────────────────────────────────────────

_MEDIA_NAME = re.compile(r"^[\w][\w.-]*$")
def media_type(name: str) -> str | None:
    """The type of a kept audio or image file name, or None (nothing else is ever served from here)."""
    return audio.media_type(name) or images.media_type(name)


def media(req: Request):
    """An audio file or image kept beside its entry: a plain name, inside the journal root, of an audio or image type
    (``audio.media_type``, ``images.media_type``), and embedded by an engine entry (so ``packs/``, ``audio-stash/``
    and anything else on disk are unservable)."""
    name = req.params["name"]
    mime = media_type(name) if _MEDIA_NAME.match(name) and ".." not in name else None
    root = Path(req.cfg.root).resolve()
    path = (root / name).resolve() if mime else None
    if path is None or path.parent != root or not path.is_file() or not document.references(req.cfg, name):
        raise HTTPError(404, "not-found", "no such media file")
    return FileReply(path, mime, ranges=mime.startswith("audio/"))


# ── reflections and insights ─────────────────────────────────────────────────

def _analyses(cfg: Config, kinds: tuple[str, ...], start: date | None, end: date | None) -> list[Path]:
    out = []
    for kind in kinds:
        for path in cfg.root.glob(f"[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-{kind}*.md"):
            day = path.stem[:10]
            if (start and day < start.isoformat()) or (end and day > end.isoformat()):
                continue
            if entries.is_analysis(path):
                out.append(path)
    return sorted(out, key=lambda p: (p.stem[:10], p.stem), reverse=True)


def reflections(req: Request):
    """The companion's day reflections and week reviews (hers, labelled by ``kind``), newest first."""
    kind = req.query.get("kind") or ""
    if kind not in ("", "reflection", "week"):
        raise HTTPError(400, "bad-request", "kind is reflection or week")
    kinds = (kind,) if kind else entries.ANALYSIS_KINDS
    limit = _int(req, "limit", 50, 1, LIST_LIMIT)
    out = []
    for path in _analyses(req.cfg, kinds, _date(req, "from"), _date(req, "to"))[:limit]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), path.stem)
        out.append({"stem": path.stem, "date": path.stem[:10], "kind": entries.kind_of(path), "title": title,
                    "body": reflect.body_of(text), "sources": document.sources_of(text)})
    return out


def _local_day(cfg: Config, stamp) -> str | None:
    """The local date (``YYYY-MM-DD``) of an ISO timestamp in a run file, None when it is missing or unreadable."""
    try:
        return datetime.fromisoformat(stamp).astimezone(cfg.tz).date().isoformat()
    except (TypeError, ValueError):
        return None


def _program_progress(req: Request, runs: list[dict], lo: str, hi: str) -> dict[str, str]:
    """Program id -> a line for the Insights week ``lo``..``hi``: how many of the program's sessions were finished by
    the week's end, and how many in the week. A program shows in a week where one of its runs began or finished a
    session, and in the current week while a run is still going. Of a program's runs the newest begun by the week's
    end speaks. Read from the run files (the engine's), so nothing here is his words."""
    from .. import programs
    today = req.now.astimezone(req.cfg.tz).date().isoformat()
    out: dict[str, str] = {}
    for run in runs:   # oldest first: a later run of the same program replaces an earlier one
        started = _local_day(req.cfg, run.get("started_at"))
        if not started or started > hi:
            continue
        sessions = [s for s in (run.get("sessions") or {}).values() if isinstance(s, dict)]
        days = [_local_day(req.cfg, s.get("completed_at")) for s in sessions if s.get("status") == "completed"]
        done = [d for d in days if d and d <= hi]
        this_week = [d for d in done if d >= lo]
        live = run.get("status") in programs.ACTIVE and hi >= today
        if not (lo <= started <= hi or this_week or live):
            continue
        prog = _program_of(req, run)
        total = len(prog.sessions) if prog else max(len(sessions), len(done))
        line = f"{len(done)} of {total} sessions done"
        out[run["program"]] = line + (f", {len(this_week)} this week" if this_week else "")
    return out


def insights_weeks(req: Request):
    """Per week (ending on the ``closes: week`` practice's day): entries, his words, prompts answered and missed
    (from the ledger, which keeps recent weeks only), and the week's review and day reflections."""
    start, end = _range(req, 56)
    cfg = req.cfg
    end_day = reflect.week_end_day(cfg)
    last = end + timedelta(days=(end_day - end.weekday()) % 7)
    prompts = _sent_prompts(cfg)
    found = document.all_entries(cfg)
    out = []
    try:
        from .. import programs
        runs = programs.all_runs(cfg)
    except Exception:   # a run file the engine cannot read must not take the Insights down
        log.exception("journal serve: runs unavailable for the insights")
        runs = []
    week_end = last
    while week_end - timedelta(days=6) <= end and week_end >= start:
        first = week_end - timedelta(days=6)
        lo, hi = first.isoformat(), week_end.isoformat()
        mine = [path for path, _ in found if lo <= path.stem[:10] <= hi]
        outcomes = [_prompt_outcome(cfg, p, req.now) for p in prompts if lo <= (p.get("local_date") or "") <= hi]
        answered, missed = outcomes.count("answered"), outcomes.count("missed")
        review = entries.analyses_on(cfg.root, hi, "week")
        out.append({"week_end": hi, "entries": len(mine), "words": sum(document.words_count(p) for p in mine),
                    "answered": answered, "missed": missed, "program_progress": _program_progress(req, runs, lo, hi),
                    "review": review[-1].stem if review else None,
                    "reflections": [p.stem for p in _analyses(cfg, ("reflection",), first, week_end)][::-1]})
        week_end -= timedelta(days=7)
    return out


# ── library ──────────────────────────────────────────────────────────────────

def _program_source(prog) -> str:
    """``builtin``, ``fork`` (his files in place of a packaged program) or ``user``: as ``Library.source`` for practices."""
    if prog.builtin:
        return "builtin"
    return "fork" if prog.id in library.builtin_programs() else "user"


def _active_runs(cfg: Config) -> dict[str, str]:
    """Program id -> the id of its active run (the newest), for the Library's "running" badge."""
    try:
        from .. import programs
        return {r["program"]: r["id"] for r in programs.active_runs(cfg)}
    except Exception:  # a run file the engine cannot read must not take the Library down
        log.exception("journal serve: runs unavailable for the library")
        return {}


def _default_changed(cfg: Config, pid: str, source: str) -> bool | None:
    """For an edited built-in (``source`` ``fork``): whether the shipped default has changed since he edited it (None:
    not known). None for anything else."""
    if source != "fork":
        return None
    try:
        return (library.edit_status(cfg, pid, his_known=True) or {}).get("default_changed")
    except Exception:  # a record the engine cannot read must not take the Library down
        log.exception("journal serve: the edit record of %s is unreadable", pid)
        return None


def _program_item(prog, active: dict[str, str], cfg: Config | None = None) -> dict:
    """A program or situational definition in the shape of a Library list row (API.md: ``sessions``, ``phases_total``,
    ``active_run``); it has no schedule or ``closes`` of its own."""
    source = _program_source(prog)
    return {"id": prog.id, "kind": prog.kind, "title": prog.title, "emoji": prog.emoji, "group": prog.group,
            "source": source, "enabled": prog.enabled, "schedule": [], "closes": None,
            "sessions": len(prog.sessions), "phases_total": sum(len(s.phases) for s in prog.sessions),
            "active_run": active.get(prog.id), "mtime": _mtime(prog.path),
            "default_changed": _default_changed(cfg, prog.id, source) if cfg is not None else None}


def _read(path: Path | None) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace") if path else ""
    except OSError:
        return ""


def _session_file(prog, s) -> str | None:
    """The name a save of session ``s`` takes (``PUT /library/{id}`` ``file``): ``sessions/<name>.md`` in a program;
    None in a situational definition (its one file is the definition's own)."""
    if prog.situational or s.path is None:
        return None
    return f"sessions/{s.path.name}"


def _program_one(prog, active: dict[str, str], cfg: Config | None = None) -> dict:
    """``GET /library/{id}`` for a program: its definition with the sessions' questions as the sections the view lists,
    the program file as ``raw`` and each session's file and phases under ``sessions`` (``file``: the name to save it
    under, with its ``mtime``)."""
    sections = [{"title": f"Session {s.number} · {s.title} — phase {ph.number}: {ph.title}",
                 "questions": [{"id": q.id, "text": q.text, "pinned": q.pinned} for q in ph.questions]}
                for s in prog.sessions for ph in s.phases if ph.questions]
    return {
        "definition": {**_program_item(prog, active, cfg), "companion": "", "opener": {"plain": prog.intro or None},
                       "annotate": prog.annotate, "advance": prog.advance, "sections": sections,
                       "path": str(prog.path) if prog.path else None},
        "raw": _read(prog.path), "problems": [], "mtime": _mtime(prog.path),
        "sessions": [{"number": s.number, "id": s.id, "title": s.title, "raw": _read(s.path),
                      "file": _session_file(prog, s), "mtime": _mtime(s.path),
                      "phases": [{"number": ph.number, "title": ph.title, "type": ph.type,
                                  "questions": [{"id": q.id, "text": q.text, "pinned": q.pinned} for q in ph.questions]}
                                 for ph in s.phases]} for s in prog.sessions]}


def _edited(cfg: Config, pid: str, lib: library.Library) -> tuple[dict | None, list[str]]:
    """(``override``, problems) for ``GET /library/{id}``: his edit of a built-in, ``{"default_changed",
    "edited_at", "in_force"}`` (``in_force`` false: his file does not load, so the built-in is used; the library's
    problems with it are returned), else None."""
    try:
        status = library.edit_status(cfg, pid)
        if status is None:
            return None, []
        path = library.his_path(cfg, pid)
    except Exception:  # an edit record the engine cannot read must not take the page down
        log.exception("journal serve: the edit record of %s is unreadable", pid)
        return None, []
    p, prog = lib.practices.get(pid), lib.programs.get(pid)
    in_force = (p is not None and not p.builtin) or (p is None and prog is not None and not prog.builtin)
    mine = (f"{path}:", f"{path}{os.sep}") if path is not None else ()
    problems = [] if in_force or not mine else [m for m in lib.problems if m.startswith(mine)]
    return {**status, "in_force": in_force}, problems


def _practice_view(cfg: Config, lib: library.Library, p: library.Practice) -> dict:
    """A practice as ``GET /library/{id}`` shows it. A built-in's ``collapse_hours`` is the window it folds on
    (``windows.collapse_hours``: a built-in's prompt records none), so an edit of it starts from what is in force."""
    d = lib.to_dict(p)
    if p.builtin:
        d["collapse_hours"] = library.fork_window(cfg, p)
    return d


def library_list(req: Request):
    lib = req.lib()
    active = _active_runs(req.cfg)
    return ([{**_practice_view(req.cfg, lib, p), "mtime": _mtime(p.path),
              "default_changed": _default_changed(req.cfg, p.id, lib.source(p))} for p in lib.practices.values()]
            + [_program_item(prog, active, req.cfg) for prog in lib.programs.values()])


def library_one(req: Request):
    lib = req.lib()
    pid = req.params["id"]
    prog = lib.programs.get(pid)
    if prog is not None and pid not in lib.practices:
        out = _program_one(prog, _active_runs(req.cfg), req.cfg)
    else:
        p = lib.practices.get(pid)
        if p is None:
            raise HTTPError(404, "not-found", "no practice or program by that id")
        problems = [] if p.builtin or not p.path else library.validate_file(Path(p.path), req.cfg)
        out = {"definition": _practice_view(req.cfg, lib, p), "raw": p.raw, "problems": problems,
               "mtime": _mtime(p.path)}
    override, problems = _edited(req.cfg, pid, lib)
    return {**out, "override": override, "problems": out["problems"] + problems}


def library_default(req: Request):
    """``GET /library/{id}/default``: the shipped definition of a built-in, in ``GET /library/{id}``'s shape (whatever
    is in force), with ``diff``: ``since`` (what changed in the default since his edit began; null when nothing did or
    that is not known) and ``yours`` (the default against his version; null when he has none)."""
    pid = req.params["id"]
    ship = library.shipped(pid) if library.ID.fullmatch(pid) else None
    if ship is None:
        raise HTTPError(404, "not-found", "nothing ships under that id")
    lib = req.lib()
    if ship.kind == library.PRACTICE:
        p = lib.builtins[pid]
        out = {"definition": _practice_view(req.cfg, lib, p), "raw": p.raw, "problems": [], "mtime": None}
    else:
        out = {**_program_one(library.builtin_programs()[pid], _active_runs(req.cfg)), "mtime": None}
        out["definition"]["mtime"] = None
        for s in out["sessions"]:
            s["mtime"] = None
    return {**out, "override": None, "diff": library.default_diff(req.cfg, pid)}


# ── programs (WP4) ───────────────────────────────────────────────────────────

RUN_STATUSES = ("active", "paused", "completed", "abandoned", "expired")


def _program_of(req: Request, run: dict):
    """The run's definition, or None when it has been deleted or no longer parses (the run is still shown)."""
    return req.lib().programs.get(run["program"])


def run_json(req: Request, run: dict) -> dict:
    """A run as the web app reads it (API.md ``Run``). ``where`` is the ``S:P:V`` token a transition is bound to
    (``expect``); ``sessions`` lists every session of the program with its entry's stem (None until the session
    begins; the planned stem before its first answer, the filed one after) so the screen can link them; ``scheduled`` is session 10's interrupts as the run file holds them."""
    from .. import programs
    prog = _program_of(req, run)
    cur = run.get("current") or None
    sessions = []
    for n in range(1, (len(prog.sessions) if prog else 0) + 1):
        sess = (run.get("sessions") or {}).get(str(n)) or {}
        sessions.append({"number": n, "title": prog.session(n).title, "status": sess.get("status") or "not-started",
                         "entry": programs.entry_stem(req.cfg, run, n),
                         "phases_total": len(prog.session(n).phases),
                         "started_at": sess.get("started_at"), "completed_at": sess.get("completed_at")})
    return {
        "id": run["id"], "program": run["program"], "title": prog.title if prog else run["program"],
        "emoji": prog.emoji if prog else "", "status": run.get("status"), "started_at": run.get("started_at"),
        "completed_at": run.get("completed_at"), "paused_since": run.get("paused_since"),
        "last_activity_at": run.get("last_activity_at"), "current": cur,
        "where": programs.where(run) if cur and prog else None,
        "text": programs.describe(req.cfg, run, req.now), "sessions": sessions,
        "insights_entry": run.get("insights_entry"),
        "scheduled": [{k: i.get(k) for k in ("id", "session", "n", "of", "offset", "local", "at", "status", "text")}
                      for i in run.get("scheduled") or []],
    }


def _open_phase(req: Request, run: dict) -> dict | None:
    """The run's open phase as data: ``compose.phase_json`` of the definition (his earlier answers filled into its
    prose slots), plus ``sent_text`` (what Telegram was sent: a reflect phase's construction is only in that),
    ``open`` (it takes an answer), ``answered`` and ``skipped``. None when the run is over or its session has not begun; ``delivered`` is False while a send is owed."""
    from .. import compose, programs
    prog = _program_of(req, run)
    cur = run.get("current")
    if prog is None or not cur or run.get("status") not in programs.ACTIVE:
        return None
    s, p = cur["session"], cur["phase"]
    sess = (run.get("sessions") or {}).get(str(s)) or {}
    ph = (sess.get("phases") or {}).get(str(p)) or {}
    if sess.get("status") != "in_progress":
        return None
    try:
        session = prog.session(s)
        lead = session.phase(p - 1).transition if p > 1 else ""
        parts = compose.phase_parts(prog, s, p, lead=lead, fill=programs.filler(req.cfg, prog, run, s))
        out = compose.phase_json(prog, s, p, parts, req.cfg.names)
    except (IndexError, KeyError, ValueError):   # the definition was edited under a run in progress
        return None
    prompt = ledger.find(ledger.snapshot(req.cfg.ledger_path), ph["prompt_id"]) if ph.get("prompt_id") else None
    delivered = bool(prompt) and not ph.get("send_due")   # a re-send owed (a /back, a jump) leaves the old prompt there
    out.update(sent_text=(prompt or {}).get("phase_text"), delivered=delivered,
               open=delivered and capture.open_phase(prompt) and run.get("status") == "active",
               answered=bool(ledger.answers(prompt)), skipped=bool(ph.get("skipped")),
               phases_total=len(session.phases))
    return out


def wrong_state(run: dict, action: str, defined: bool = True) -> str | None:
    """Why ``action`` means nothing for ``run`` as it is, else None. (The engine would raise or do nothing; the
    request says so up front.) ``defined``: the run's program can still be read; without it only ``abandon`` works
    (every other action loads the definition, and a spawn that fails there says nothing to the browser)."""
    from .. import programs
    status = run.get("status")
    if not defined and action != "abandon":
        return "the program's definition is missing or no longer parses, so the run can only be abandoned"
    if action == "insights":
        return None
    if action == "resume":
        return None if status == "paused" else f"the run is {status}, not on a break"
    if action == "abandon":
        held = status == "completed" and any(i.get("status", "pending") == "pending" for i in run.get("scheduled") or [])
        return None if status in programs.ACTIVE or held else f"the run is {status}"
    if status == "paused":
        return "the program is on a break: resume it first"
    if status != "active":
        return f"the run is {status}"
    if action == "back":
        cur = run.get("current") or {}
        sess = (run.get("sessions") or {}).get(str(cur.get("session"))) or {}
        if sess.get("status") != "in_progress":
            return "this session has not begun: next begins it"
        if cur.get("phase") == 1:
            return "this is the first phase of the session; there is nothing before it"
    return None


def _actions(run: dict, defined: bool = True) -> list[str]:
    """The controls that mean something for the run now (``wrong_state`` is the rule)."""
    return [a for a in ("next", "skip", "back", "pause", "resume", "abandon") if wrong_state(run, a, defined) is None]


def _seen(req: Request, run: dict) -> dict:
    """The run as it stands now: a situational run idle past its ``expires_hours`` is ``expired`` already, though the
    file says ``active`` until the tick's step (or a ``/start``) closes it. The same rule ``POST /runs`` applies."""
    from .. import programs
    if run.get("status") in programs.ACTIVE and programs.is_expired(req.cfg, run, req.now):
        return {**run, "status": "expired"}
    return run


def runs(req: Request):
    from .. import programs
    status, program = req.query.get("status") or None, req.query.get("program") or None
    if status and status not in RUN_STATUSES:
        raise HTTPError(400, "bad-request", f"status is one of {', '.join(RUN_STATUSES)}")
    found = [r for r in map(lambda r: _seen(req, r), programs.all_runs(req.cfg))
             if (not status or r.get("status") == status) and (not program or r["program"] == program)]
    return [run_json(req, r) for r in sorted(found, key=lambda r: r.get("started_at") or "", reverse=True)]


def _run_or_404(req: Request) -> dict:
    from .. import programs
    run = programs.find_run(req.cfg, req.params["id"])
    if run is None:
        raise HTTPError(404, "not-found", "no run by that id")
    return run


def run_one(req: Request):
    run = _seen(req, _run_or_404(req))
    return {**run_json(req, run), "phase": _open_phase(req, run), "keyboard": _actions(run, _program_of(req, run) is not None)}


def run_insights(req: Request):
    run = _run_or_404(req)
    stem = run.get("insights_entry")
    # the compiled document is not an "entry" in the journal's sense (no practice id in its name): it is the file the
    # run names, and only if it is the one the engine wrote for this run
    path = entries.entry_path(req.cfg.root, stem) if isinstance(stem, str) and document.STEM.match(stem) else None
    if path is None or not path.is_file() or entries.front_matter_of(path).get("run") != run["id"]:
        raise HTTPError(404, "not-found", "no insights entry for that run")
    text = path.read_text(encoding="utf-8")
    return {"stem": path.stem, "body": re.sub(r"\A---\n.*?\n---\n", "", text, count=1, flags=re.S).lstrip("\n")}


# ── status, doctor, settings ─────────────────────────────────────────────────

def status_(req: Request):
    """``journal status`` without what is read from the chat side (``status.status(web=True)``)."""
    return status.status(req.cfg, req.now, web=True)


def doctor_(req: Request):
    """``journal doctor`` rows; no secret is probed from the web (``token=False``)."""
    return [{"check": n, "status": s, "detail": d} for n, s, d in status.doctor(req.cfg, token=False, web=True)]


SECRET = "<set>"


def redacted(cfg: Config) -> dict:
    """The effective configuration with every secret's source hidden: ``telegram.token_*``, ``serve.token_*``
    and ``sources.*.command`` read ``"<set>"`` when set."""
    data = copy.deepcopy(cfg.raw)
    for section in ("telegram", "serve"):
        part = data.get(section)
        if isinstance(part, dict):
            for key in part:
                if key.startswith("token_") and part[key] not in (None, "", []):
                    part[key] = SECRET
    for part in (data.get("sources") or {}).values():
        if isinstance(part, dict) and part.get("command"):
            part["command"] = SECRET
    data.update({"root": str(cfg.root), "state_dir": str(cfg.state_dir), "practices_dir": str(cfg.practices_dir),
                 "timezone": cfg.timezone})
    if not isinstance(data.get("today"), dict):   # `today: false` in a file: off, the rest at its defaults
        data["today"] = effective_today(cfg)
    return data


def settings(req: Request):
    """The effective configuration, where each key comes from, and (under a named Hermes profile) its name, which the
    page needs to print a ``hermes -p <profile> config set`` line that changes this profile and not the default one."""
    profile = profile_from_home(req.cfg.hermes_home)
    return {"settings": json.loads(json.dumps(redacted(req.cfg), default=str)), "origins": settings_.origins(req.cfg),
            **({"profile": profile} if profile else {})}


# ── the table ────────────────────────────────────────────────────────────────

PUBLIC, TOKEN, WRITE = "public", "token", "write"   # WRITE: a token, and `serve.allow_write` must not be false

ROUTES: list[tuple[str, str, Callable, str]] = [
    ("GET", r"/healthz", healthz, PUBLIC),
    ("GET", r"/api/v1/healthz", healthz, PUBLIC),
    ("POST", r"/api/v1/pair", pair, PUBLIC),
    ("POST", r"/api/v1/logout", logout, TOKEN),
    ("GET", r"/api/v1/session", session, TOKEN),
    ("GET", r"/api/v1/today", today_, TOKEN),
    ("GET", r"/api/v1/entries", entries_list, TOKEN),
    ("GET", r"/api/v1/entries/(?P<stem>[^/]+)", entry_doc, TOKEN),
    ("GET", r"/api/v1/days", days, TOKEN),
    ("GET", r"/api/v1/media/(?P<name>[^/]+)", media, TOKEN),
    ("GET", r"/api/v1/reflections", reflections, TOKEN),
    ("GET", r"/api/v1/insights/weeks", insights_weeks, TOKEN),
    ("GET", r"/api/v1/library", library_list, TOKEN),
    ("GET", r"/api/v1/library/(?P<id>[^/]+)", library_one, TOKEN),
    ("GET", r"/api/v1/library/(?P<id>[^/]+)/default", library_default, TOKEN),
    ("GET", r"/api/v1/runs", runs, TOKEN),
    ("GET", r"/api/v1/runs/(?P<id>[^/]+)", run_one, TOKEN),
    ("GET", r"/api/v1/runs/(?P<id>[^/]+)/insights", run_insights, TOKEN),
    ("GET", r"/api/v1/status", status_, TOKEN),
    ("GET", r"/api/v1/doctor", doctor_, TOKEN),
    ("GET", r"/api/v1/settings", settings, TOKEN),
]
