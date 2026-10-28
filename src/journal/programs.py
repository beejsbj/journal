"""Programs: ordered sessions of phases, run by code over the journal's chat (PRACTICES.md §4).

A run is a JSON file, ``state_dir/runs/<program>/<run>.json``, holding where he is (``current``), each session's
entry and status, and each phase's send and transitions. It never holds his words: those are in the journal, one
entry per session (``YYYY-MM-DD-<program>-sNN.md``, dated the day the session started; a situational run writes
``YYYY-MM-DD-<id>.md``), each block of his words under a hidden ``%% journal phase:N %%`` key.

**A phase send is a ledger prompt** (``<run>-sNN-pM``, ``collapse_hours: 3``), delivered by ``tick.send_prompt``, so
capture, the sweep, folding, threading, the mirror and annotation are the existing code paths. The phase that is
open (``phase_open`` on its record) takes every message while it is visible and the newest prompt in the chat, not
only the first (``capture.open_phase``, ``capture.superseded``); a break closes it.

**Transitions** (``start``, ``advance``, ``skip``, ``back``, ``pause``, ``resume``, ``abandon``, ``insights``) run
one at a time under the tick's lock: in the tick's ``step``, or in a spawned ``journal program <action>`` which
takes the lock first (``locked``). They are idempotent: an advance is keyed ``<run>:<session>:<phase>:<last capture
id>`` (``cmd<visit>`` for a command), and a key the phase has already used is a no-op, so a sweep re-capture, a
redelivered update, a lost-then-retried spawn or the tick racing a spawn all collapse to one send. A transition
writes the run file *before* it sends (``send_due``); if the process dies in between, the next ``step`` sends.

**Advance** (Decision 1): ``advance: settle`` (fix-life) moves on once the newest capture on the open phase is
``programs.settle_minutes`` (2) old; the hook spawns ``journal program settle <run>`` on each capture, and the
tick's ``step`` catches a lost spawn. ``advance: next`` waits for ``/next``. ``/next`` always advances at once.

**Session 10's interrupts** (PRACTICES.md §4.8) come only from a confirmation: when the open phase is a ``schedule``
phase, the settle looks for a reply that is ``YYYY-MM-DD HH:MM`` or ``HH:MM`` (``parse_confirmation``), times the
phase's interrupt lines from it (``confirm_schedule``) and moves on; anything else is kept as his words, asked about
once more, and schedules nothing. The interrupts live in ``run["scheduled"]`` as wall-clock times, the tick's ``step``
sends them (``send_interrupts``: ledger prompts a reply answers, under the interrupt's key in the session's entry) and
a synthesis, when the program asks for one, is appended to the insights document there too (``synthesize``).

**No model needed.** A ``reflect`` phase asks the model for its construction (validated: quotes only from his
answers in this session, ≤ 900 characters, not failure text); anything else sends the file's own text with the
template note, at once. Summaries and the insights document are filled from his verbatim words. Nothing here
waits on a model.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import html
import json
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path

from . import agent, audio, compose, entries, ledger, library, mirror, notebook, outage, programdefs, prompts, validate
from .config import Config, settle_minutes
from .ports import ChannelError
from .programdefs import Program

RUNS = "runs"
COLLAPSE_HOURS = 3  # a phase message folds (and stops taking the next message) after this long
ACTIVE = ("active", "paused")
MAX_DAYS_AHEAD = 60  # a confirmed protocol day further off than this is taken for a typo (2062 for 2026), not timed
CLOSED = ("abandoned", "expired")  # a run that ended without finishing: its unsent one-offs are cancelled
INTERRUPT_COLLAPSE_HOURS = 24  # an interrupt keeps its question visible this long (a reply to it works at any age)
SYNTHESIS_BUDGET = 240  # seconds of model time for an insights synthesis
SYNTHESIS_ATTEMPTS = 3  # bad answers from a reachable model before the compiled document stands alone
SYNTHESIS_INPUT_CAP = 60_000  # characters of the compiled document handed to the model
SUMMARY_ATTEMPTS = 3
REFLECT_BUDGET = 120  # seconds of model time for a reflect phase's construction
SLOT_WORDS = 140  # a prose slot takes his answer only when it is one short line
SKIPPED = "_(skipped)_"
ABOVE = "_(in the answer above)_"


class ProgramError(ValueError):
    """What to tell him instead (no such program, nothing running, the first phase has no /back …)."""


class Busy(ProgramError):
    """Another program is unfinished, and only one runs at a time (``one_at_a_time``). ``run`` is that run; nothing
    was changed."""

    def __init__(self, text: str, run: dict):
        super().__init__(text)
        self.run = run


# ── run files ────────────────────────────────────────────────────────────────

def runs_dir(cfg: Config) -> Path:
    return cfg.state_dir / RUNS


def run_path(cfg: Config, program: str, run_id: str) -> Path:
    return runs_dir(cfg) / program / f"{run_id}.json"


def all_runs(cfg: Config) -> list[dict]:
    """Every run on disk, oldest first."""
    out = []
    for f in sorted(runs_dir(cfg).glob("*/*.json")):
        run = ledger.read_state(f)
        if run.get("id") and run.get("program"):
            out.append(run)
    return sorted(out, key=lambda r: r.get("started_at") or "")


def active_runs(cfg: Config) -> list[dict]:
    return [r for r in all_runs(cfg) if r.get("status") in ACTIVE]


def find_run(cfg: Config, run_id: str) -> dict | None:
    return next((r for r in all_runs(cfg) if r["id"] == run_id), None)


def last_activity(cfg: Config, run: dict, prompts: list[dict] | None = None) -> datetime:
    """When he or the engine last did something in the run: its last transition or his newest answer to one of its
    phases (a capture changes only the ledger, so the run file alone would call an older run newer than the one he is
    answering now, and let a situational run expire under a fresh answer)."""
    if prompts is None:
        prompts = ledger.snapshot(cfg.ledger_path)["prompts"]
    stamps = [run.get("last_activity_at"), run.get("started_at")]
    stamps += [c.get("at") for p in prompts if p.get("run") == run["id"] for c in p.get("captures") or []]
    found = []
    for t in stamps:
        try:
            found.append(datetime.fromisoformat(t))
        except (TypeError, ValueError):
            continue
    return max(found, default=datetime.min.replace(tzinfo=timezone.utc))


def current_run(cfg: Config, program_id: str | None = None) -> dict | None:
    """The active (or paused) run he last did something in, of ``program_id`` when given. Without one, a run with a
    session in progress comes first (``session_in_progress``), so ``/next``, ``/skip``, ``/back`` and ``/break`` act
    on the session he is in, even when a reply to an older run's phase was the last thing he did."""
    if program_id is None:
        mid = session_in_progress(cfg, datetime.now(timezone.utc))
        if mid is not None:
            return mid
    runs = [r for r in active_runs(cfg) if program_id is None or r["program"] == program_id]
    if len(runs) < 2:
        return runs[0] if runs else None
    prompts = ledger.snapshot(cfg.ledger_path)["prompts"]
    return max(runs, key=lambda r: last_activity(cfg, r, prompts))


def in_session(run: dict) -> bool:
    """Whether the run is mid-session: active (not on a break, not over) with its current session begun and not yet
    completed. A run between sessions, or on ``/break``, is not; nor is a session whose last phase he has finished
    (``completion_due``: only the engine's summary is owed, which the tick's step retries, and a completion that
    keeps failing must not block every other program)."""
    if run.get("status") != "active":
        return False
    s = (run.get("current") or {}).get("session")
    sess = (run.get("sessions") or {}).get(str(s)) or {}
    return sess.get("status") == "in_progress" and not sess.get("completion_due")


def session_in_progress(cfg: Config, now: datetime, besides: str | None = None) -> dict | None:
    """The run of another program than ``besides`` that is mid-session (the one he last did something in, should
    there be two: a program and a situational session), else None. A run whose definition no longer loads does not
    count (it can only be abandoned, and the tick's step leaves it alone), nor does a situational run idle past its
    ``expires_hours`` (the tick's step closes it)."""
    found = []
    for run in active_runs(cfg):
        if run["program"] == besides or not in_session(run):
            continue
        try:
            prog = _program(cfg, run["program"])
        except ProgramError:
            continue
        if not _expired(cfg, prog, run, now):
            found.append(run)
    if len(found) < 2:
        return found[0] if found else None
    prompts = ledger.snapshot(cfg.ledger_path)["prompts"]
    return max(found, key=lambda r: last_activity(cfg, r, prompts))


def _start_word(prog: Program) -> str:
    return prog.aliases[0] if prog.aliases else prog.id


def _where(prog: Program, run: dict) -> str:
    """Where an unfinished run is, for the refusal: ``on a break in session 3 of 10: The Identity Construction
    Tracer``, ``in session 3 of 10: …``, or ``session 3 of 10 is next: …``."""
    s = run["current"]["session"]
    at, title = f"session {s} of {len(prog.sessions)}", prog.session(s).title
    if run.get("status") == "paused":
        return f"on a break in {at}: {title}"
    if (run.get("sessions") or {}).get(str(s), {}).get("status") == "in_progress":
        return f"in {at}: {title}"
    return f"{at} is next: {title}"


def busy_text(cfg: Config, other: dict, wanted: str | None = None, *, web: bool = False) -> str:
    """The refusal: which program is unfinished, where it is, and the ways out (carry on with it and finish it, or
    abandon it). ``wanted``: the program he asked for. ``web``: in the web app's words (its buttons and the "Carry
    on with it" link beside the line, not the chat's commands)."""
    try:
        prog = _program(cfg, other["program"])
    except ProgramError:
        prog = None
    title, word = (prog.title, _start_word(prog)) if prog else (other["program"], other["program"])
    enabled = prog is not None and prog.enabled
    try:  # (a run left past the end of a definition he trimmed: no "where", the rest as it is)
        where = f" ({_where(prog, other)})" if prog else ""
    except (KeyError, IndexError, TypeError):
        where = ""
    lead = f"{title} isn't finished{where}, and only one program runs at a time."
    if web:
        return f"{lead} Finish it or abandon it first."
    then = ""
    if wanted:
        try:
            then = f" Then /start {_start_word(_program(cfg, wanted))}."
        except ProgramError:
            pass
    end = f"end it with /abandon {word} (its entries stay)"
    if not enabled:  # (a program turned off can't be taken up again until it is turned back on)
        return f"{lead} It's turned off: {end}, or turn it back on to finish it.{then}"
    return f"{lead} Carry on with /start {word} and finish it, or {end}.{then}"


def blocking(cfg: Config, program_id: str, run: dict | None = None) -> dict | None:
    """The unfinished run that stops ``program_id`` from starting now, else None (DECISIONS.md, ONE-PROGRAM): a run,
    active or on a break, mid-session or between sessions, of another program that is not situational. A situational
    definition is never stopped and never stops another. ``run``: the program's own unfinished run, when the caller
    has it: a program with one is never stopped (it already holds the slot; going on with it is how the slot is
    freed). A run whose definition no longer loads does not count (it can only be abandoned)."""
    try:
        if _program(cfg, program_id).situational:
            return None
    except ProgramError:
        return None
    if run is not None and run.get("status") in ACTIVE and run.get("program") == program_id:
        return None
    found = []
    for other in active_runs(cfg):
        if other["program"] == program_id:
            continue
        try:
            if _program(cfg, other["program"]).situational:
                continue
        except ProgramError:
            continue
        found.append(other)
    if len(found) < 2:
        return found[0] if found else None
    prompts = ledger.snapshot(cfg.ledger_path)["prompts"]
    return max(found, key=lambda r: last_activity(cfg, r, prompts))


def one_at_a_time(cfg: Config, program_id: str, log: list[str], run: dict | None = None) -> None:
    """One program at a time, across every face (DECISIONS.md, ONE-PROGRAM): raise ``Busy`` when another program
    (not situational) has an unfinished run, before anything is changed. ``start`` asks this under the tick's lock
    whenever it would begin a new run, so two starts from two faces at once give one run. Finishing the run or
    abandoning it frees the slot; ``/break`` does not."""
    other = blocking(cfg, program_id, run)
    if other is not None:
        log.append(f"{program_id}: not started; {other['id']} ({other['program']}) is unfinished")
        raise Busy(busy_text(cfg, other, program_id), other)


def _save(cfg: Config, run: dict) -> None:
    with ledger.locked_state(run_path(cfg, run["program"], run["id"])) as data:
        data.clear()
        data.update(json.loads(json.dumps(run)))


@contextlib.contextmanager
def locked(cfg: Config):
    """The tick's lock, waited for: every transition outside the tick runs under it (a spawned ``journal program
    …``), so it never races the tick's ``step`` or another spawn."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "tick.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).isoformat()


def _session(run: dict, s: int) -> dict:
    return run.setdefault("sessions", {}).setdefault(str(s), {})


def _phase(run: dict, s: int, p: int) -> dict:
    return _session(run, s).setdefault("phases", {}).setdefault(str(p), {})


def prompt_id(run_id: str, s: int, p: int) -> str:
    return f"{run_id}-s{s:02d}-p{p}"


def _program(cfg: Config, program_id: str) -> Program:
    try:
        return library.load(cfg).program(program_id)
    except library.UnknownDefinition:
        raise ProgramError(f"There's no program called “{program_id}”.") from None


# ── reading his answers ──────────────────────────────────────────────────────

def entry_stem(cfg: Config, run: dict, s: int) -> str | None:
    """The session's entry: where its first answer went (``capture.entry_stem`` moves it off a file of his that
    appeared at the planned name before then; every phase of the session follows), else the planned stem."""
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        if p.get("run") == run["id"] and p.get("session") == s and p.get("entry_path"):
            return Path(p["entry_path"]).stem
    return _session(run, s).get("entry")


def entry_path(cfg: Config, run: dict, s: int) -> Path | None:
    stem = entry_stem(cfg, run, s)
    return entries.entry_path(cfg.root, stem) if stem else None


def answers(cfg: Config, run: dict, s: int) -> dict[int, str]:
    """His words in session ``s`` of the run, by phase (from the entry file; {} before the first answer)."""
    path = entry_path(cfg, run, s)
    if path is None or not path.is_file():
        return {}
    return entries.phase_answers(path.read_text(encoding="utf-8"))


def _short(text: str) -> str | None:
    """His answer as a prose slot: only when it is a single short line (a whole multi-question answer dropped into
    "in the area of …" would read as nonsense)."""
    t = (text or "").strip()
    if t in (audio.NOT_TRANSCRIBED, audio.TRANSCRIBING, audio.NO_TRANSCRIPT):
        return None  # the engine's note about a recording, not words of his to put in a sentence
    return t if t and "\n" not in t and len(t) <= SLOT_WORDS else None


def filler(cfg: Config, prog: Program, run: dict, s: int):
    """``text → text`` with ``{user}`` and the session's prose slots resolved from his earlier answers: his own
    words when the answer is one short line, else the slot's name in italics (what it stands for)."""
    session = prog.session(s)
    cache: dict[int, dict[int, str]] = {}

    def answer_of(k: int, qid: str) -> str | None:
        if k < 1 or k > len(prog.sessions):
            return None
        cache.setdefault(k, answers(cfg, run, k))
        n = prog.session(k).phase_of(qid)
        return cache[k].get(n) if n else None

    slots = dict(session.slots)

    def fill(text: str) -> str:
        def one(m: re.Match) -> str:
            name = m.group(1)
            if name == "user":
                return cfg.names.user or "you"
            if name in slots:
                ref = programdefs.ref_of(slots[name])
                got = _short(answer_of(ref[0] or s, ref[1])) if ref else None
                return got or f"_{name}_"
            return m.group(0)
        return programdefs.PLACEHOLDER.sub(one, text or "")
    return fill


def fill_template(cfg: Config, prog: Program, run: dict, s: int, template: str) -> str:
    """A ``## Summary`` / document template filled from his words: ``{N.M}`` is his whole answer to that question's
    phase (he answers a phase in one message, or several), once per phase (a later slot of the same phase says
    "in the answer above"); a skipped or unanswered phase is ``_(skipped)_``. ``{user}`` and prose slots as
    ``filler``."""
    session = prog.session(s)
    got = answers(cfg, run, s)
    used: set[int] = set()
    prose = filler(cfg, prog, run, s)

    def one(m: re.Match) -> str:
        name = m.group(1)
        n = session.phase_of(name)
        if n is None:
            return prose(m.group(0))
        if n in used:
            return ABOVE if got.get(n) else SKIPPED
        used.add(n)
        return got.get(n) or SKIPPED
    return programdefs.PLACEHOLDER.sub(one, template or "").strip()


# ── sending a phase ──────────────────────────────────────────────────────────

def _entry_stem(cfg: Config, prog: Program, run: dict, s: int, local_date: str) -> str:
    """The new session's entry: ``<date>-<program>-sNN`` (situational: ``<date>-<id>``), or the next free ``-N``
    when another run already has that name (a file of another run, or a prompt of another run)."""
    base = f"{local_date}-{prog.id}" + ("" if prog.situational else f"-s{s:02d}")
    taken = {p.get("stem") for p in ledger.snapshot(cfg.ledger_path)["prompts"] if p.get("run") != run["id"]}
    n, stem = 1, base
    while True:
        path = entries.entry_path(cfg.root, stem)
        theirs = path.exists() and entries.front_matter_of(path).get("run") != run["id"]
        if stem not in taken and not theirs:
            return stem
        n += 1
        stem = f"{base}-{n}"


def _enter(cfg: Config, prog: Program, run: dict, s: int, now: datetime) -> None:
    """Session ``s`` begins (or is taken up again): it keeps its entry, if it has one."""
    sess = _session(run, s)
    if not sess.get("entry"):
        local = now.astimezone(cfg.tz).date().isoformat()
        sess["entry"] = _entry_stem(cfg, prog, run, s, local)
        sess["local_date"] = local
    sess["status"] = "in_progress"
    sess.setdefault("started_at", _iso(now))
    sess.pop("completed_at", None)
    _arrive(run, s, 1, now)


def _arrive(run: dict, s: int, p: int, renewed: datetime | None = None) -> None:
    """Phase ``p`` of session ``s`` becomes the open phase and is owed a send. ``renewed`` (the moment) is given when
    the run comes to it by moving on or jumping, not by re-sending the open phase: on a second visit the answers
    from before ``renewed`` belong to the first, so the settle waits for a new one (a ``/back`` and then forward
    again would otherwise settle the phase at once, on an answer he gave before going back)."""
    run["current"] = {"session": s, "phase": p}
    ph = _phase(run, s, p)
    ph["send_due"] = True
    ph["visit"] = ph.get("visit", 0) + 1
    if renewed is not None and ph["visit"] > 1:
        ph["reopened_at"] = _iso(renewed)
    ph.pop("skipped", None)
    for k in ("confirm_asked", "schedule_told"):  # a schedule phase asked again, or reopened: a new visit asks afresh
        ph.pop(k, None)


def _construction(cfg: Config, prog: Program, run: dict, s: int, p: int, log: list[str]) -> tuple[str | None, str]:
    """(model text, note for the entry) for a reflect phase; (None, why) when the template goes out instead."""
    if cfg.programs.get("reflect", True) is False:
        return None, "skipped (off)"
    got = answers(cfg, run, s)
    his = "\n\n".join(f"Phase {n}:\n{t}" for n, t in sorted(got.items()) if n < p)
    if not his.strip():
        return None, "skipped (no answers yet)"
    session = prog.session(s)
    query = prompts.reflect_phase_request(cfg, prog, session, session.phase(p), his)
    try:
        raw = agent.ask(cfg, query, REFLECT_BUDGET, toolsets="none")
    except agent.AgentUnavailable as e:
        log.append(f"{run['id']}: session {s} phase {p}: reflect text unavailable ({e}); template sent")
        return None, "skipped (no model)"
    ok, why = validate.check_reflect_phase(raw, his)
    if not ok:
        log.append(f"{run['id']}: session {s} phase {p}: reflect text rejected ({why}); template sent")
        return None, f"skipped ({why})"
    return raw.strip(), ""


def _bridge(cfg: Config, prog: Program, run: dict, s: int, p: int, log: list[str]) -> str:
    """The optional one-paragraph bridge before an ask phase (``programs.bridges``), quoting only his last
    answer; "" when off, unavailable or rejected."""
    if cfg.programs.get("bridges") is not True or p < 2:
        return ""
    last = answers(cfg, run, s).get(p - 1, "")
    if not last.strip():
        return ""
    try:
        raw = agent.ask(cfg, prompts.bridge_request(cfg, prog, prog.session(s), last), 60, toolsets="none")
    except agent.AgentUnavailable:
        return ""
    ok, why = validate.check_bridge(raw, last)
    if not ok:
        log.append(f"{run['id']}: session {s} phase {p}: bridge rejected ({why})")
        return ""
    return raw.strip()


def _send_phase(cfg: Config, prog: Program, run: dict, s: int, p: int, now: datetime, log: list[str],
                intro: str = "", auto: bool = False) -> bool:
    """Deliver the open phase (``tick.send_prompt``: retries, ``sent.jsonl``, re-send semantics, the mirror) and
    record it on the run. A phase is never deferred for want of a model: a reflect phase without usable model text
    goes out with the template at once.

    ``auto``: the engine is sending on its own (a settle, or the tick delivering a phase it owed), not because he
    asked. Such a send never closes a bare ``/x`` window (``_held_off``): it is held, ``send_due`` stays set, and the
    tick's step tries again once the window has closed. The window is asked before the model calls (a tick inside a
    long window does not spend one on a phase it will not send) and again just before the first message goes, since
    a reflect construction can take minutes and a ``/x`` sent meanwhile is his."""
    from . import tick
    if auto and _held_off(cfg, run, s, p, now, log):
        return False
    session = prog.session(s)
    phase = session.phase(p)
    sess = _session(run, s)
    lead = session.phase(p - 1).transition if p > 1 else ""
    note = ""
    construction = None
    if phase.type == "reflect":
        construction, note = _construction(cfg, prog, run, s, p, log)
    fallback = (f"_({cfg.names.companion} would normally build this from your answers; write it yourself if you "
                "like.)_")
    bridge = _bridge(cfg, prog, run, s, p, log) if phase.type == "ask" else ""
    parts = compose.phase_parts(prog, s, p, lead=lead, construction=construction, fallback=fallback, intro=intro,
                                fill=filler(cfg, prog, run, s),
                                ask=confirm_request(cfg, phase, now) if phase.type == "schedule" else "")
    if bridge:
        parts.insert(0, (None, bridge))
    messages = compose.phase_messages(prog, s, p, parts, cfg.names)
    pid = prompt_id(run["id"], s, p)
    record = {
        "id": pid, "stem": sess["entry"], "kind": prog.id, "practice": prog.id, "program": prog.id, "run": run["id"],
        "session": s, "phase": p, "local_date": sess.get("local_date") or now.astimezone(cfg.tz).date().isoformat(),
        "scheduled": _iso(now), "chat_id": cfg.chat_id, "title": prog.title, "session_title": session.title,
        "phase_title": phase.title, "phase_count": len(session.phases), "advance": prog.advance,
        "html": messages[-1], "plain_text": compose.phase_text(prog, s, p, parts, cfg.names),
        "phase_text": compose.phase_body_text(parts[1:] if intro else parts), "collapse_hours": COLLAPSE_HOURS, "opener": "",
        "opener_source": "program", "phase_open": True,
        **({"session_count": len(prog.sessions), "framework": session.framework} if not prog.situational else {}),
        **({"emoji": prog.emoji} if prog.emoji else {}),
        **({"annotate": False} if not prog.annotate else {}),
        **({"reflect": note} if phase.type == "reflect" and note else {}),
    }
    if auto and _held_off(cfg, run, s, p, now, log):
        return False
    ph0 = _phase(run, s, p)
    # the earlier parts a failed attempt already sent count only in the chat they went to (a record of them that names
    # no chat was made before CHANNELS, so on Telegram); after the channel moved they are sent again, in the new chat
    here = f"{cfg.platform}:{cfg.chat_id}"
    went_to = ph0.get("chunk_chat") or (f"telegram:{cfg.chat_id}" if cfg.platform == "telegram" else None)
    earlier = (list(ph0.get("chunk_ids") or [])
               if went_to == here and len(ph0.get("chunk_ids") or []) == len(messages) - 1 else [])
    try:
        for part in messages[len(earlier):-1]:  # over Telegram's limit: the phase in two messages, the last one answered
            earlier.append(cfg.channel().send(cfg.chat_id, part))
            ph0["chunk_ids"], ph0["chunk_chat"] = list(earlier), here  # a retry of the last one must not send these again
            _save(cfg, run)
    except ChannelError as e:
        tick.count_send_failure(cfg, record, e, log)  # counted like the last message's failures: alert, then wait
        return False
    if earlier:
        record["previous_message_ids"] = earlier
    label = f"{prog.id} session {s} phase {p}" if not prog.situational else f"{prog.id} phase {p}"
    keyboard = compose.KEYBOARD if cfg.programs.get("keyboard", True) is not False else None  # off: typed commands
    delivered = tick.send_prompt(cfg, record, log, label=label, keyboard=keyboard,
                                 refold=lambda _old: compose.folded_phase_html(record, "sent again below", cfg.names))
    if not delivered:
        return False
    with ledger.locked(cfg.ledger_path) as data:  # one open phase per run
        for q in data["prompts"]:
            if q.get("run") != run["id"]:
                continue
            if q["id"] == pid:
                q["phase_open"] = True
                _span(q, True, q.get("sent_at") or _iso(datetime.now(timezone.utc)))
                for k in ("advanced_at", "send_attempts", "send_error"):  # a later failure counts from zero again
                    q.pop(k, None)
                q["needs_edit"] = False
            elif q.get("phase_open"):
                q["phase_open"] = False
                _span(q, False, _iso(datetime.now(timezone.utc)))
    ph = _phase(run, s, p)
    ph.update(prompt_id=pid, sent_at=_iso(datetime.now(timezone.utc)), send_due=False)
    ph.pop("chunk_ids", None)
    ph.pop("chunk_chat", None)
    run["last_activity_at"] = _iso(now)
    _save(cfg, run)
    return True


def _send_current(cfg: Config, prog: Program, run: dict, now: datetime, log: list[str], intro: str = "",
                  auto: bool = False) -> bool:
    cur = run["current"]
    return _send_phase(cfg, prog, run, cur["session"], cur["phase"], now, log, intro, auto)


def _held_off(cfg: Config, run: dict, s: int, p: int, now: datetime, log: list[str]) -> bool:
    """Whether an automatic phase send must wait for a bare ``/x`` window (or a privacy pass that cannot run). Asked
    at the later of ``now`` and the clock, and held if either says open. The clock sees a ``/x`` that arrived after
    ``now`` (while a model call ran), which ``now`` cannot. ``now`` sees what the clock has already lost: the tick
    sends its rituals before the programs step, and a ritual sent in this tick ended the window after ``now``, so
    only ``now`` still calls it open. Holding for that one tick leaves the ritual the newest message, so his reply
    to it is the ritual's and not the phase's; the phase goes out at the next tick, after it."""
    clock = datetime.now(timezone.utc)
    if not (_off_the_record(cfg, max(now, clock)) or _off_the_record(cfg, now)):
        return False
    log.append(f"{run['id']}: session {s} phase {p} held back: off the record (or the privacy pass cannot say); "
               f"the tick sends it once the window closes")
    return True


# ── transitions ──────────────────────────────────────────────────────────────

def _expired(cfg: Config, prog: Program, run: dict, now: datetime) -> bool:
    if not prog.expires_hours:
        return False
    return now - last_activity(cfg, run) > timedelta(hours=prog.expires_hours)


def _expire(cfg: Config, prog: Program, run: dict, now: datetime, log: list[str]) -> None:
    """End a situational run that went idle past ``expires_hours``. Like ``abandon``, it cancels the interrupts not
    yet sent (a pass over ``CLOSED`` runs would never send them, and left ``pending`` they would show in the web app
    as still to come), and says so: he confirmed them, and nobody else will tell him they were dropped."""
    run.update(status="expired", completed_at=_iso(now))
    _reconcile_sent(cfg, run)  # (one delivered but not yet written down is not one that was cancelled)
    cancelled = _cancel_pending(run)
    if cancelled:
        # (owed until it is delivered: see below; the words it needs are kept, so a program file that is edited or
        # deleted before the next tick cannot make the note, or its alert, impossible)
        run["expiry_notice"] = {"cancelled": cancelled, "attempts": 0, "title": prog.title,
                                "hours": prog.expires_hours}
    _save(cfg, run)  # (before the note: a death after it must not leave them to be shown as pending)
    _close_phases(cfg, run)
    log.append(f"{run['id']}: expired ({prog.expires_hours:g} hours without activity)"
               + (f"; {cancelled} scheduled interrupt(s) cancelled" if cancelled else ""))
    _owed_expiry_notice(cfg, run, now, log, [])


def _owed_expiry_notice(cfg: Config, run: dict, now: datetime, log: list[str], alerts: list[str]) -> None:
    """Tell him the interrupts he had set were cancelled when the run expired. The send is tried on the tick that
    expires the run and, if Telegram is down, on the next ones (``tick.SEND_ATTEMPTS`` in all), then alerted: he is
    otherwise still expecting reminders that were quietly removed."""
    from .tick import SEND_ATTEMPTS
    owed = run.get("expiry_notice")
    if not owed:
        return
    if _say_about(cfg, run["program"], f"<i>{html.escape(owed.get('title') or run['program'], quote=False)} ended after "
                  f"{owed.get('hours') or 0:g} hours without activity: the {owed['cancelled']} interrupts already set "
                  f"are cancelled.</i>", log, now):
        run.pop("expiry_notice")
    else:
        owed["attempts"] = owed.get("attempts", 0) + 1
        if owed["attempts"] >= SEND_ATTEMPTS:
            run.pop("expiry_notice")
            alerts.append(f"{run['id']}: could not tell him that {owed['cancelled']} interrupts were cancelled "
                          f"when the run expired")
    _save(cfg, run)


def is_expired(cfg: Config, run: dict, now: datetime) -> bool:
    """Whether a situational run has been idle past its program's ``expires_hours``: ``start`` closes such a run and
    begins a new one, so a caller deciding whether "a run is active" (the web app's ``POST /runs``) asks this too."""
    try:
        return _expired(cfg, _program(cfg, run["program"]), run, now)
    except ProgramError:
        return False


def _span(q: dict, opened: bool, at: str) -> None:
    """Keep ``open_spans`` on a phase prompt: when it was the open phase (``capture.open_phase`` reads it at a
    message's own time, which one boolean and one stamp cannot say once a break closes and ``/start`` reopens it)."""
    spans = q.setdefault("open_spans", [])
    if opened:
        if not spans or spans[-1][1] is not None:
            spans.append([at, None])
    elif spans and spans[-1][1] is None:
        spans[-1][1] = at


def _close_phases(cfg: Config, run: dict, **extra) -> None:
    """No phase of the run takes the next message any more (the run ended, expired or a session completed)."""
    with ledger.locked(cfg.ledger_path) as data:
        for q in data["prompts"]:
            if q.get("run") == run["id"] and (q.get("phase_open") or extra):
                if q.get("phase_open"):
                    q["phase_open"] = False
                    _span(q, False, _iso(datetime.now(timezone.utc)))
                    q["needs_edit"] = True  # fold it
                if extra and (extra.get("session") is None or q.get("session") == extra["session"]):
                    q.update({k: v for k, v in extra.items() if k != "session"})


def start(cfg: Config, program_id: str, now: datetime, log: list[str], session: int | None = None,
          only_new: bool = False) -> dict:
    """``/start <program>``: one active run per program. A new run starts at session 1 (or ``session``) with the
    program's intro on its first message; an existing one resumes: a paused run is taken up again, a run between
    sessions begins the next, a run mid-phase re-sends the open phase (the earlier message still takes replies).
    ``session`` jumps there. A situational run past ``expires_hours`` is closed and a new one started.

    ``only_new``: begin a run only if the program has none in progress (after the expiry above); otherwise do nothing.
    The web app checks that before it spawns this, but two requests can both pass the check before either child has
    the lock; the second child must not re-send the first one's phase or jump it to its own ``session``.

    One program at a time (``one_at_a_time``): a new run is refused with ``Busy`` while another program (not
    situational) has an unfinished run, before anything changes. The program's own run, whatever state it is in,
    is never refused."""
    prog = _program(cfg, program_id)
    if not prog.enabled:
        raise ProgramError(f"{prog.title} is turned off (enabled: false in its file).")
    if session is not None and not 1 <= session <= len(prog.sessions):
        raise ProgramError(f"{prog.title} has sessions 1 to {len(prog.sessions)}.")
    run = current_run(cfg, prog.id)
    expired = run is not None and _expired(cfg, prog, run, now)
    if run is not None and not expired and only_new:
        log.append(f"{run['id']}: already in progress; not started again")
        return run
    one_at_a_time(cfg, prog.id, log, None if expired else run)
    if expired:
        _expire(cfg, prog, run, now, log)
        run = None
    if run is None:
        local = now.astimezone(cfg.tz).date().isoformat()
        rid = f"{local}-{secrets.token_hex(3)}"
        while any(r["id"] == rid for r in all_runs(cfg)):  # (runs are keyed by it: files, prompt ids, entry names)
            rid = f"{local}-{secrets.token_hex(3)}"
        run = {"program": prog.id, "id": rid, "status": "active",
               "started_at": _iso(now), "completed_at": None, "paused_since": None, "last_activity_at": _iso(now),
               "sessions": {}, "scheduled": [], "insights_entry": None}
        _enter(cfg, prog, run, session or 1, now)
        _save(cfg, run)
        log.append(f"{run['id']}: started {prog.id}" + (f" at session {session}" if session else ""))
        _send_current(cfg, prog, run, now, log, intro=prog.intro)
        return run
    was_paused = run.get("status") == "paused"
    run.update(status="active", paused_since=None, last_activity_at=_iso(now))
    cur = run["current"]
    if session is not None and session != cur["session"]:
        left = _session(run, cur["session"])
        if left.get("status") == "in_progress":  # set aside: /start picks it up again from its first phase
            left["status"] = "left"
        _close_phases(cfg, run)  # now, not when the new phase is delivered: a failed send must not leave it taking chat
        _enter(cfg, prog, run, session, now)
        log.append(f"{run['id']}: jumped to session {session}")
    elif _session(run, cur["session"]).get("status") != "in_progress":
        _enter(cfg, prog, run, cur["session"], now)
        log.append(f"{run['id']}: session {cur['session']} begins")
    else:
        ph = _phase(run, cur["session"], cur["phase"])
        caps = _captures(cfg, ph.get("prompt_id"))
        newest = _capture_key(max(caps, key=lambda c: c["at"])) if caps else None
        used = f"{run['id']}:{cur['session']}:{cur['phase']}:{newest}" in (ph.get("advance_keys") or [])
        schedule_phase = prog.session(cur["session"]).phase(cur["phase"]).type == "schedule"  # (needs its confirmation)
        if was_paused and prog.advance == "settle" and newest and not ph.get("send_due") and not used \
                and not schedule_phase:
            # answered while on a break: carry on from there (an active run just gets the phase again, and the
            # settle or /next moves it on as usual)
            _save(cfg, run)
            log.append(f"{run['id']}: resumed; session {cur['session']} phase {cur['phase']} was answered")
            advance(cfg, run["id"], now, log, reason="resume", capture_id=newest)
            return run
        _arrive(run, cur["session"], cur["phase"])
        log.append(f"{run['id']}: resumed at session {cur['session']} phase {cur['phase']}")
    _save(cfg, run)
    _send_current(cfg, prog, run, now, log)
    return run


def _require_run(cfg: Config, run_id: str | None) -> dict:
    run = find_run(cfg, run_id) if run_id else current_run(cfg)
    if run is None or run.get("status") not in ACTIVE:
        raise ProgramError("No program is running. /program lists them; /start <id> begins one.")
    return run


def require(cfg: Config, run_id: str | None) -> tuple[dict, Program]:
    run = _require_run(cfg, run_id)
    return run, _program(cfg, run["program"])


def _captures(cfg: Config, pid: str | None) -> list[dict]:
    """The phase's answers (``ledger.answers``: not a conversation's recording or picture filed on its prompt)."""
    p = ledger.find(ledger.snapshot(cfg.ledger_path), pid) if pid else None
    return ledger.answers(p)


def _capture_key(c: dict) -> str:
    return str(c.get("platform_message_id") or c.get("at"))


def where(run: dict) -> str:
    """``session:phase:visit`` of the run's open phase: what a command is bound to (``--expect``). The visit counts
    the times the phase was arrived at, so a ``/back`` to it makes an earlier command stale too."""
    s, p = run["current"]["session"], run["current"]["phase"]
    return f"{s}:{p}:{_phase(run, s, p).get('visit', 0)}"


def parse_where(text: str) -> tuple[int, int, int]:
    try:
        s, p, v = (int(x) for x in str(text).split(":"))
    except ValueError:
        raise ProgramError(f"“{text}” is not a session:phase:visit.") from None
    return s, p, v


def _moved_on(run: dict, expect: tuple[int, ...] | None) -> bool:
    """Whether the run is no longer where a command was given (``expect``: session, phase[, visit])."""
    if expect is None:
        return False
    s, p = run["current"]["session"], run["current"]["phase"]
    return tuple(expect[:2]) != (s, p) or (len(expect) > 2 and expect[2] != _phase(run, s, p).get("visit", 0))


def advance(cfg: Config, run_id: str, now: datetime, log: list[str], *, reason: str,
            capture_id: str | None = None, expect: tuple[int, ...] | None = None) -> bool:
    """Move the run on from its open phase. Idempotent: False when the key (``capture_id``, else the command and
    the phase's visit) was already used on this phase, when ``expect`` (session, phase[, visit]) is no longer the
    open one (a command that was given for a phase the run has since left, or a settle that lost to a ``/next``),
    or when the run is not active. Between sessions, starts the next one; with the open phase not yet delivered,
    delivers it (that is what was owed)."""
    run = find_run(cfg, run_id)
    if run is None or run.get("status") != "active":
        return False
    prog = _program(cfg, run["program"])
    s, p = run["current"]["session"], run["current"]["phase"]
    if _moved_on(run, expect):
        log.append(f"{run['id']}: already moved on from {':'.join(str(x) for x in expect)} ({reason} ignored)")
        return False
    sess = _session(run, s)
    if sess.get("status") != "in_progress":  # between sessions: /next begins the next one
        if reason == "settle":
            return False
        _enter(cfg, prog, run, s, now)
        _save(cfg, run)
        return _send_current(cfg, prog, run, now, log)
    ph = _phase(run, s, p)
    if ph.get("send_due"):
        return _send_current(cfg, prog, run, now, log) if reason != "settle" else False
    key = f"{run['id']}:{s}:{p}:{capture_id or 'cmd' + str(ph.get('visit', 1))}"
    if key in (ph.get("advance_keys") or []):
        return False
    if prog.session(s).phase(p).type == "schedule" and reason != "skip" \
            and not _confirmed(cfg, prog, run, s, p, now, log, explicit=reason != "settle"):
        return False
    elif reason == "skip" and prog.session(s).phase(p).type == "schedule":
        _call_off(cfg, prog, run, s, now, log)  # (a /back to a confirmed phase, then /skip: "without interrupts")
    caps = _captures(cfg, ph.get("prompt_id"))
    ph.setdefault("advance_keys", []).append(key)
    ph.update(advanced_at=_iso(now), captures=[_capture_key(c) for c in caps], reason=reason)
    if caps:
        ph.setdefault("answered_at", caps[0]["at"])
    run["last_activity_at"] = _iso(now)
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, ph.get("prompt_id") or "")
        if q is not None:
            q.update(phase_open=False, advanced_at=_iso(now), needs_edit=True)
            _span(q, False, _iso(now))
    log.append(f"{run['id']}: session {s} phase {p} → " + ("next phase" if p < len(prog.session(s).phases)
                                                             else "session complete") + f" ({reason})")
    if p < len(prog.session(s).phases):
        _arrive(run, s, p + 1, now)
        _save(cfg, run)
        _send_current(cfg, prog, run, now, log, auto=reason == "settle")
    else:
        sess["completion_due"] = True  # until complete_session finishes: the tick's step finishes it if this dies
        _save(cfg, run)
        complete_session(cfg, run, s, now, log)
    return True


def _not_paused(run: dict) -> None:
    """A command that was queued behind the tick's lock must not move a run that went on a break meanwhile (only
    ``/start`` takes it up again)."""
    if run.get("status") == "paused":
        raise ProgramError(f"It's on a break: /start {run['program']} picks it up where you left off.")


def skip(cfg: Config, run_id: str | None, now: datetime, log: list[str], expect: tuple[int, ...] | None = None) -> bool:
    """``/skip``: the open phase is marked skipped (its summary slots say so) and the run moves on. ``expect``: the
    phase the command was given for (``where``); a tap that arrives after the run moved on does nothing."""
    run, _ = require(cfg, run_id)
    _not_paused(run)
    s, p = run["current"]["session"], run["current"]["phase"]
    if _moved_on(run, expect):
        log.append(f"{run['id']}: already moved on from {':'.join(str(x) for x in expect)} (skip ignored)")
        return False
    if _session(run, s).get("status") == "in_progress" and not _phase(run, s, p).get("send_due"):
        _phase(run, s, p)["skipped"] = True
        _save(cfg, run)
    return advance(cfg, run["id"], now, log, reason="skip", expect=expect)


def back(cfg: Config, run_id: str | None, now: datetime, log: list[str], expect: tuple[int, ...] | None = None) -> bool:
    """``/back``: the previous phase is sent again and becomes the open one; what he wrote for it stays in the
    entry, and a new answer is added under it. ``expect`` as for ``skip``."""
    run, prog = require(cfg, run_id)
    _not_paused(run)
    s, p = run["current"]["session"], run["current"]["phase"]
    if _moved_on(run, expect):
        log.append(f"{run['id']}: already moved on from {':'.join(str(x) for x in expect)} (back ignored)")
        return False
    if _session(run, s).get("status") != "in_progress":
        raise ProgramError(f"Session {s} hasn't started yet: /next begins it.")
    if p == 1:
        raise ProgramError("This is the first phase of the session; there's nothing before it. /skip moves on.")
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, _phase(run, s, p).get("prompt_id") or "")
        if q is not None:
            q["phase_open"] = False
            _span(q, False, _iso(now))
    _arrive(run, s, p - 1)
    _phase(run, s, p - 1)["reopened_at"] = _iso(now)
    run.update(last_activity_at=_iso(now))
    _save(cfg, run)
    log.append(f"{run['id']}: back to session {s} phase {p - 1}")
    return _send_current(cfg, prog, run, now, log)


def pause(cfg: Config, run_id: str | None, now: datetime, log: list[str]) -> dict:
    """``/break``: nothing advances until he starts it again. The open phase stops taking every message (it would
    otherwise swallow whatever he says for the next three hours, and ``/start`` would make that his answer); it
    takes a direct reply as any answered phase does, and ``/start`` carries on from one or sends the phase again."""
    run, _ = require(cfg, run_id)
    if run["status"] != "paused":
        run.update(status="paused", paused_since=_iso(now))
        _save(cfg, run)
        _close_phases(cfg, run)  # (folds it too: the message shows it is no longer waiting for the next reply)
        log.append(f"{run['id']}: paused")
    return run


def resume(cfg: Config, run_id: str | None, now: datetime, log: list[str], only_paused: bool = False) -> dict:
    """Take a paused run up again: what ``/start <program>`` does for one. ``only_paused``: do nothing for a run that
    is not on a break (a second queued resume would send the open phase again)."""
    run, prog = require(cfg, run_id)
    if only_paused and run.get("status") != "paused":
        log.append(f"{run['id']}: not on a break; nothing to resume")
        return run
    return start(cfg, prog.id, now, log)


def _holding_interrupts(cfg: Config, run_id: str | None) -> dict | None:
    """A run that finished but still has interrupts to come: session 10 is the last session, so by the protocol day
    the run is ``completed`` and ``require`` finds nothing to end. ``run_id`` names it; without one, the newest."""
    runs = [find_run(cfg, run_id)] if run_id else all_runs(cfg)
    held = [r for r in runs if r and r.get("status") == "completed"
            and any(i.get("status", "pending") == "pending" for i in r.get("scheduled") or [])]
    return max(held, key=lambda r: r.get("last_activity_at") or "", default=None)


def abandon(cfg: Config, run_id: str | None, now: datetime, log: list[str]) -> dict:
    """End the run where it is; its entries stay. The next ``/start`` begins a new run. A run that has already
    completed but still has interrupts to come (session 10's, which go out the day after the session) is not
    ended again: its pending interrupts are cancelled and it stays ``completed``. With no ``run_id`` and both an
    active run and such a completed one, either could be meant and the wrong guess is not undone by a revert (an
    abandoned run is over, cancelled interrupts do not come back): it names both and ends neither."""
    try:
        run = _require_run(cfg, run_id)   # (not `require`: a run whose program file is gone must still be endable)
    except ProgramError:
        run = _holding_interrupts(cfg, run_id)
        if run is None:
            raise
        n = _cancel_pending(run)
        _save(cfg, run)
        log.append(f"{run['id']}: {n} scheduled interrupt(s) cancelled")
        return run
    held = None if run_id else _holding_interrupts(cfg, None)
    if held is not None:
        n = sum(1 for i in held.get("scheduled") or [] if i.get("status", "pending") == "pending")
        raise ProgramError(
            f"Two runs could be meant: {run['id']} ({run['program']}, active) and {held['id']} ({held['program']}, "
            f"completed, {n} interrupt(s) still to come). Name one: journal program abandon <run>.")
    run.update(status="abandoned", completed_at=_iso(now))
    _reconcile_sent(cfg, run)
    cancelled = _cancel_pending(run)  # an interrupt of a run that was ended is not sent
    _save(cfg, run)
    _close_phases(cfg, run)
    log.append(f"{run['id']}: abandoned" + (f"; {cancelled} scheduled interrupt(s) cancelled" if cancelled else ""))
    return run


def _reconcile_sent(cfg: Config, run: dict) -> None:
    """Note what the ledger already says about the run's pending interrupts: one whose prompt has a ``message_id`` was
    delivered (the engine died before it wrote that down), one that used its last attempt failed. Done before a run
    cancels its pending interrupts, so he is never told that one he already received was cancelled."""
    from .tick import SEND_ATTEMPTS
    snap = {p["id"]: p for p in ledger.snapshot(cfg.ledger_path)["prompts"]}
    for item in run.get("scheduled") or []:
        if item.get("status", "pending") != "pending":
            continue
        pid = f"{run['id']}-{item['id']}"
        rec = snap.get(pid) or {}
        if rec.get("message_id"):
            item.update(status="sent", prompt_id=pid)
        elif rec.get("send_attempts", 0) >= SEND_ATTEMPTS:
            item.update(status="failed", prompt_id=None)


def _cancel_pending(run: dict, s: int | None = None) -> int:
    pending = [i for i in run.get("scheduled") or [] if i.get("status", "pending") == "pending"
               and (s is None or i.get("session") == s)]
    for item in pending:
        item["status"] = "cancelled"
    return len(pending)


def settle(cfg: Config, run_id: str, now: datetime, log: list[str]) -> bool:
    """The settle check (``journal program settle``, the tick's ``step``): advance the open phase when its newest
    capture is at least ``programs.settle_minutes`` old and the program advances by settling. The key is that
    capture, so a correction inside the window simply makes a later check the one that advances."""
    run = find_run(cfg, run_id)
    if run is None or run.get("status") != "active":
        return False
    try:
        prog = _program(cfg, run["program"])
    except ProgramError:
        return False
    if prog.advance != "settle":
        return False
    s, p = run["current"]["session"], run["current"]["phase"]
    ph = _phase(run, s, p)
    if _session(run, s).get("status") != "in_progress" or ph.get("send_due") or not ph.get("prompt_id"):
        return False
    caps = _captures(cfg, ph["prompt_id"])
    if ph.get("reopened_at"):  # a /back: the answers from before it are not an answer to this visit
        caps = [c for c in caps if datetime.fromisoformat(c["at"]) >= datetime.fromisoformat(ph["reopened_at"])]
    if not caps:
        return False
    newest = max(caps, key=lambda c: c["at"])
    if now - datetime.fromisoformat(newest["at"]) < timedelta(minutes=settle_minutes(cfg)):
        return False
    if audio.transcription_enabled(cfg) and any(c.get("transcript") == "pending" for c in caps):
        return False  # an audio answer not yet transcribed: moving on now would build the next phase as if skipped
    if _off_the_record(cfg, now):
        return False
    return advance(cfg, run_id, now, log, reason="settle", capture_id=_capture_key(newest), expect=(s, p))


def _off_the_record(cfg: Config, now: datetime) -> bool:
    """Whether a bare ``/x`` is holding the chat off the record (or the privacy pass cannot say). A phase send ends
    such a window (``privacy``: a prompt send does), so an automatic send while he is talking privately would make
    his next private message a phase answer: the settle waits until the window closes (at the first silence of
    ``quiet_minutes``) and the tick's ``step`` tries again, and every automatic phase send asks again just before
    it goes (``_held_off``: an owed send, a send after a model call). An explicit ``/next`` is his own act and goes
    ahead.

    The interrupts ask it too, and a pass that cannot run holds them, whatever the hook's own fallback says
    (``privacy.window_open``): a send is a boundary the recomputed windows will honour once the database is readable
    again, so a private thread that went on past ``quiet_minutes`` while it was not would be cut at the send. Fail
    closed; what it costs is a protocol day, and that is made loud (``send_interrupts``) and not silent."""
    from . import privacy
    pv = privacy.view(cfg, now)
    return pv.closed or pv.open_at(now)


# ── session 10's interrupts (PRACTICES.md §4.8) ──────────────────────────────

_CONFIRM = re.compile(r"^\s*(?:([0-9]{4}-[0-9]{2}-[0-9]{2})\s+)?([01][0-9]|2[0-3]):([0-5][0-9])\s*$")


def parse_confirmation(text: str) -> tuple[date | None, int, int] | None:
    """``(day, hour, minute)`` when ``text`` is, whole, a day and wake time (``2026-11-02 07:00``) or a time alone
    (``07:00``: ``day`` is None, which means tomorrow), else None. Nothing else is read as a time: ``7am`` or ``around
    seven`` schedules nothing, and he is asked once more."""
    m = _CONFIRM.match(text or "")
    if not m:
        return None
    try:
        day = date.fromisoformat(m.group(1)) if m.group(1) else None
    except ValueError:  # 2026-13-45
        return None
    return day, int(m.group(2)), int(m.group(3))


def confirm_request(cfg: Config, phase, now: datetime) -> str:
    """The closing line the engine adds to a schedule phase: what to send, and what it will do with it."""
    example = (now.astimezone(cfg.tz).date() + timedelta(days=1)).isoformat() + " 07:00"
    return (f"Confirm the protocol day and your wake time as YYYY-MM-DD HH:MM, for example {example} (a bare 07:00 "
            f"means tomorrow), and I'll time the {len(programdefs.interrupts(phase))} interrupts from it. /skip goes "
            "on without them.")


def _plain_of(markup: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", markup))


def _say(cfg: Config, prog: Program, markup: str, log: list[str], now: datetime) -> bool:
    """A short note from the engine (not a prompt: nothing answers it). Best effort; True when it was delivered."""
    return _say_about(cfg, prog.id, markup, log, now)


def _say_about(cfg: Config, program_id: str, markup: str, log: list[str], now: datetime) -> bool:
    """``_say`` for a note that must not depend on the program's definition still loading."""
    try:
        cfg.channel().send(cfg.chat_id, markup)
    except ChannelError as e:
        log.append(f"note not sent ({e})")
        return False
    mirror.to_chat(cfg, f"{cfg.names.companion} sent a note about {program_id} at {mirror.stamp(cfg, now)}",
                   _plain_of(markup), log)
    return True


def _visit_blocks(cfg: Config, run: dict, s: int, p: int) -> list[tuple[str, datetime]]:
    """His blocks of words for phase ``p`` of this visit to it, oldest first, each with the time he sent it (a
    ``/back`` reopened the phase: what he wrote before is not an answer to the new visit). A block with no words
    yet is left out and does not hide the others. Empty when the blocks and the captures cannot be told apart one
    for one."""
    path = entry_path(cfg, run, s)
    body = _read(path)
    blocks = entries.phase_blocks(body, keep_empty=True).get(p, []) if body else []
    ph = _phase(run, s, p)
    caps = _captures(cfg, ph.get("prompt_id"))
    if len(blocks) != len(caps):
        # Every capture wrote one block (a block with no words yet, an audio file waiting for its transcript or a
        # photo with no caption, is counted and left empty), so a count that differs means the entry and the ledger
        # have drifted apart: with no way to tell which block is which capture's, nothing is read, rather than
        # pairing a block with another one's time (an answer from before a /back re-read, a bare time given the
        # wrong day).
        return []
    pairs = [(b, datetime.fromisoformat(c["at"])) for b, c in zip(blocks, caps) if b]  # (the ones with words)
    if ph.get("reopened_at"):  # then drop what was written before the /back, whenever it reached the entry
        reopened = datetime.fromisoformat(ph["reopened_at"])
        pairs = [(b, at) for b, at in pairs if at >= reopened]
    return pairs


def _batch(run: dict, s: int) -> int:
    """Which confirmation of session ``s`` this is: the first is 1; a later one (the session run again, or a phase
    reopened after interrupts went out) is the next, so its prompt ids are its own."""
    return 1 + max((i.get("batch", 1) for i in run.get("scheduled") or [] if i.get("session") == s
                    and i.get("status", "pending") != "pending"), default=0)


def plan_interrupts(cfg: Config, prog: Program, run: dict, s: int, p: int, day: date, hour: int,
                    minute: int) -> list[dict]:
    """The phase's interrupts timed from the wake time ``day hour:minute``: wall-clock arithmetic (each offset is
    added to the clock time he stated), kept as ``local`` so it is read in whatever timezone applies on the day;
    ``at`` is that time in today's zone, for display. The question has the session's prose slots filled in now."""
    fill = filler(cfg, prog, run, s)
    plan = programdefs.interrupts(prog.session(s).phase(p))
    wake = datetime.combine(day, dtime(hour, minute))
    batch = _batch(run, s)
    items = []
    for n, itr in enumerate(plan, 1):
        local = wake + timedelta(minutes=round(itr.hours * 60))
        items.append({"id": f"s{s:02d}-i{n}" if batch == 1 else f"s{s:02d}-b{batch}-i{n}", "session": s, "phase": p,
                      "batch": batch, "n": n, "of": len(plan), "offset": itr.label,
                      "local": local.strftime("%Y-%m-%dT%H:%M"), "at": local.replace(tzinfo=cfg.tz).isoformat(),
                      "text": fill(itr.text), "status": "pending", "prompt_id": None})
    return items


def confirm_schedule(cfg: Config, prog: Program, run: dict, s: int, p: int, now: datetime,
                     log: list[str]) -> tuple[str, str]:
    """Look for his confirmation in the open schedule phase and, when there is one, schedule the interrupts from it
    (replacing the ones of this session that have not been sent yet). Returns ``("scheduled", "")``, ``("none", "")``
    (no reply of this visit is a day and time), ``("past", why)`` (it is one, but every interrupt would be over) or
    ``("far", why)`` (it is one, but more than ``MAX_DAYS_AHEAD`` days away: a mistyped year is refused like a day
    that is over, not kept for years).

    His newest reply that parses wins, so a correction inside the settle replaces the first try."""
    found = next(((c, at) for c, at in ((parse_confirmation(b), at) for b, at in reversed(_visit_blocks(cfg, run, s, p)))
                  if c), None)
    if found is None:
        return "none", ""
    (day, hour, minute), sent = found
    day = day or sent.astimezone(cfg.tz).date() + timedelta(days=1)  # (the day he wrote it, not the day we settle)
    from . import schedule
    if day - now.astimezone(cfg.tz).date() > timedelta(days=MAX_DAYS_AHEAD):
        return "far", f"{day.isoformat()} {hour:02d}:{minute:02d}"
    try:
        items = plan_interrupts(cfg, prog, run, s, p, day, hour, minute)
        ends = max((schedule.wall_clock(cfg, i["local"]).astimezone(timezone.utc) for i in items), default=None)
        # (the latest by instant, in whatever order the phase lists them; with the grace window the tick adds, so a
        # day the calendar cannot hold is refused here and not on every tick after)
        if ends is not None:
            _ = ends + timedelta(hours=cfg.grace_hours)
    except OverflowError:  # a day at the end of the calendar plus hours: not a day with interrupts in it
        return "far", f"{day.isoformat()} {hour:02d}:{minute:02d}"
    if ends is None:
        return "none", ""
    if ends <= now:
        return "past", f"{day.isoformat()} {hour:02d}:{minute:02d}"
    keep = [i for i in run.get("scheduled") or [] if not (i.get("session") == s and i.get("status", "pending") == "pending")]
    run["scheduled"] = keep + items
    ph = _phase(run, s, p)
    run["last_activity_at"] = _iso(now)
    _save(cfg, run)
    gone = sum(1 for i in items if schedule.wall_clock(cfg, i["local"]).astimezone(timezone.utc) <= now)
    log.append(f"{run['id']}: {len(items)} interrupts timed from {day.isoformat()} {hour:02d}:{minute:02d}"
               + (f" ({gone} already past)" if gone else ""))
    if not ph.get("schedule_told"):
        first = schedule.wall_clock(cfg, items[0]["local"])
        rows = []
        for i in items:
            at = schedule.wall_clock(cfg, i["local"])
            rows.append((i["offset"], at.strftime("%H:%M" if at.date() == first.date() else "%a %H:%M"), i["text"]))
        if _say(cfg, prog, compose.schedule_confirmed_html(rows, _day_name(first, now.astimezone(cfg.tz)), gone),
                log, now):
            ph["schedule_told"] = True
            _save(cfg, run)
    return "scheduled", ""


def _confirmed(cfg: Config, prog: Program, run: dict, s: int, p: int, now: datetime, log: list[str], *,
               explicit: bool) -> bool:
    """The schedule phase is being left. True: go on (the interrupts are timed, or he said ``/next`` and gets none).
    False: stay on the phase; the settle found no usable confirmation and has asked once."""
    outcome, why = confirm_schedule(cfg, prog, run, s, p, now, log)
    if outcome == "scheduled":
        return True
    if explicit:  # /next or the web app's button: his own act, with nothing new to schedule
        from . import schedule
        standing = [i for i in run.get("scheduled") or []
                    if i.get("session") == s and i.get("status", "pending") == "pending"]
        if standing:  # (a /back to a phase already confirmed: what was timed stands)
            first = schedule.wall_clock(cfg, min(i["local"] for i in standing))
            _say(cfg, prog, f"<i>Keeping the {len(standing)} interrupts already set, from "
                            f"{_day_name(first, now.astimezone(cfg.tz))} {first.strftime('%H:%M')}.</i>", log, now)
        else:
            why = {"past": "that day and time are already over",
                   "far": f"that day is more than {MAX_DAYS_AHEAD} days away"}.get(
                       outcome, "no day and wake time was confirmed")
            _say(cfg, prog, f"<i>Going on without interrupts: {why}.</i>", log, now)
        return True
    ph = _phase(run, s, p)
    if not ph.get("confirm_asked"):  # (asked once per visit; the settle looks again every tick without saying so)
        lead = {"past": "That day and time are already over. ",
                "far": f"That day is more than {MAX_DAYS_AHEAD} days away. "}.get(
                    outcome, "I couldn't find a day and wake time in that. ")
        phase = prog.session(s).phase(p)
        if _say(cfg, prog, f"<i>{html.escape(lead, quote=False)}{html.escape(confirm_request(cfg, phase, now), quote=False)}</i>",
                log, now):
            ph["confirm_asked"] = _iso(now)
            _save(cfg, run)
            log.append(f"{run['id']}: session {s} phase {p} waits for a day and wake time ({outcome})")
    return False


def _call_off(cfg: Config, prog: Program, run: dict, s: int, now: datetime, log: list[str]) -> None:
    """``/skip`` on a schedule phase goes on without interrupts: the ones of session ``s`` still pending (timed by
    an earlier visit to the phase) are cancelled, and he is told. Nothing pending: nothing to say."""
    n = _cancel_pending(run, s)
    if n:
        _save(cfg, run)  # (before the note: a death after it must not leave them to be sent)
        log.append(f"{run['id']}: {n} scheduled interrupt(s) of session {s} cancelled (skipped)")
        _say(cfg, prog, f"<i>Going on without interrupts: the {n} already set are cancelled.</i>", log, now)


def _interrupt_record(cfg: Config, prog: Program, run: dict, item: dict, at: datetime) -> dict:
    """The ledger record of one interrupt (``<run>-<item id>``): a program prompt that is not a phase, so a reply to
    it is written into its session's entry under ``%% journal interrupt:N %%``."""
    s = item["session"]
    session = prog.session(s)
    n, of = item["n"], item["of"]
    text = compose.interrupt_question(prog, s, n, of, item["text"], cfg.names)  # (what the message will carry)
    stem = entry_stem(cfg, run, s) or _session(run, s).get("entry")
    return {
        "id": f"{run['id']}-{item['id']}", "stem": stem, "kind": prog.id, "practice": prog.id, "program": prog.id,
        "run": run["id"], "session": s, "interrupt": n, "interrupt_count": of, "interrupt_offset": item["offset"],
        "interrupt_at": at.strftime("%a %-d %b %H:%M"), "interrupt_text": text,
        "local_date": at.date().isoformat(), "scheduled": at.isoformat(), "chat_id": cfg.chat_id, "title": prog.title,
        "session_title": session.title, "collapse_hours": INTERRUPT_COLLAPSE_HOURS,
        "html": compose.interrupt_html(prog, s, n, of, text, cfg.names),
        "plain_text": compose.interrupt_text(prog, s, n, of, text, cfg.names), "opener": "",
        "opener_source": "program",
        **({"session_count": len(prog.sessions), "framework": session.framework} if not prog.situational else {}),
        **({"emoji": prog.emoji} if prog.emoji else {}),
        **({"annotate": False} if not prog.annotate else {}),
    }


def send_interrupts(cfg: Config, now: datetime, log: list[str], tried: set[str] | None = None) -> list[str]:
    """The tick's one-offs: send each interrupt that is due (inside the grace window), record the others as missed.

    Only the newest of several due at once goes (a tick that was down for an hour would otherwise send two or three
    questions back to back); the older ones are recorded as missed. A send that fails is tried again on the next tick
    while the window lasts, and alerts, like any prompt, once ``tick.SEND_ATTEMPTS`` ticks have failed. Returns the
    alerts. ``tried`` (the tick's, shared by its two passes) holds the ids already attempted this tick: they are left
    alone, and each attempt made here is added."""
    from . import schedule, tick
    tried = set() if tried is None else tried
    now_local = now.astimezone(cfg.tz)
    grace = timedelta(hours=cfg.grace_hours)
    snap = {p["id"]: p for p in ledger.snapshot(cfg.ledger_path)["prompts"]}
    alerts: list[str] = []
    for run in all_runs(cfg):  # what the ledger and the clock already say about the items
        if run.get("status") in CLOSED:
            continue
        changed = False
        for item in run.get("scheduled") or []:
            if item.get("status", "pending") != "pending":
                continue
            pid = f"{run['id']}-{item['id']}"
            rec = snap.get(pid) or {}
            if rec.get("message_id"):  # sent, and the engine died before it wrote that down
                item.update(status="sent", prompt_id=pid)
            elif rec.get("send_attempts", 0) >= tick.SEND_ATTEMPTS:
                item.update(status="failed", prompt_id=None)
            else:
                try:
                    over = schedule.wall_clock(cfg, item["local"]).astimezone(timezone.utc) + grace <= now
                except OverflowError:  # a day at the end of the calendar: it never comes, and must not stop the others
                    item.update(status="missed", note="date out of range")
                    changed = True
                    continue
                if not over:
                    continue
                item["status"] = "missed"
                if item.get("held_back"):  # it was due, and the chat was off the record for the whole window
                    item["note"] = "held back"
                    alerts.append(f"{run['id']}: interrupt {item['n']}/{item['of']} ({item['offset']}) was held back "
                                f"(off the record, or the privacy pass could not run) until its grace window passed "
                                f"and is missed")
            changed = True
        if changed:
            _save(cfg, run)
    sent_ids = {pid for pid, p in snap.items() if p.get("message_id") or p.get("send_attempts", 0) >= tick.SEND_ATTEMPTS}
    due: dict[str, list] = {}
    for one in schedule.oneoffs_due(cfg, now_local, sent_ids | tried):
        due.setdefault(one.run, []).append(one)
    if due and _off_the_record(cfg, now):
        # a send ends a bare /x window (privacy: a prompt send does), so his next private message would be filed;
        # they stay pending inside the grace window and are missed after it (``held_back`` is what makes that an
        # alert: a protocol day lost to a long /x, or to a privacy pass that cannot run, is not lost silently)
        log.append("interrupt held back: off the record (or the privacy pass cannot say)")
        for run_id, ones in due.items():
            run = find_run(cfg, run_id)
            ids = {o.item["id"] for o in ones}
            fresh = [i for i in (run or {}).get("scheduled") or [] if i["id"] in ids and not i.get("held_back")]
            for i in fresh:
                i["held_back"] = _iso(now)
            if fresh:
                _save(cfg, run)
        return alerts
    for run_id, ones in due.items():
        run = find_run(cfg, run_id)
        if run is None:
            continue
        newest = ones[-1]
        for one in ones[:-1]:
            next(i for i in run["scheduled"] if i["id"] == one.item["id"]).update(status="missed", note="superseded")
        if len(ones) > 1:
            _save(cfg, run)  # (before the send: a death after it must not leave the older ones to go out next tick)
        tried.add(newest.prompt_id)
        try:
            prog = _program(cfg, run["program"])
            item = next(i for i in run["scheduled"] if i["id"] == newest.item["id"])
            record = _interrupt_record(cfg, prog, run, item, newest.at)
            delivered = tick.send_prompt(
                cfg, record, log, label=f"{prog.id} interrupt {item['n']}",
                refold=lambda _old, r=record: compose.folded_phase_html(r, "sent again below", cfg.names))
            if delivered:
                item.update(status="sent", prompt_id=record["id"], sent_at=_iso(now))
                run["last_activity_at"] = _iso(now)
        except tick.SendFailed as e:  # (the ledger has the attempts; the item is not tried again)
            next(i for i in run["scheduled"] if i["id"] == newest.item["id"]).update(status="failed")
            alerts.append(str(e))
        except ProgramError as e:
            log.append(f"{run['id']}: interrupt not sent ({e})")
        except Exception as e:  # noqa: BLE001 - one run's trouble must not hold back the others
            log.append(f"{run['id']}: interrupt {newest.item['id']} not sent ({type(e).__name__}: {e})")
        _save(cfg, run)
    return alerts


def send_due_interrupts(cfg: Config, now: datetime, log: list[str], tried: set[str]) -> None:
    """The tick's step for the one-offs, run before the practices' ``send_due``: a ritual due in the same tick then
    goes out after the interrupt and stays the newest message in the chat, so what he types next is still filed as
    its answer (an interrupt sent after an unanswered ritual takes it out of "the first message after a prompt",
    ``capture.interrupted``). ``programs.step`` runs the pass again later in the tick for the interrupts a settle has
    just timed, with the same ``tried``. Raises ``tick.Alert`` for the sends that used their last attempt."""
    from .tick import Alert
    alerts = send_interrupts(cfg, now, log, tried)
    if alerts:
        raise Alert("; ".join(alerts))


def pending_interrupts(cfg: Config, run: dict | None = None) -> list[dict]:
    """The interrupts still to come (all live runs, or ``run``), soonest first: ``{run, id, offset, at, text}`` with
    ``at`` the wall-clock time in the current timezone as an ISO string."""
    from . import schedule
    out = []
    for r in ([run] if run else all_runs(cfg)):
        if r.get("status") in CLOSED:
            continue
        for item in r.get("scheduled") or []:
            if item.get("status", "pending") == "pending":
                out.append({"run": r["id"], "id": item["id"], "n": item["n"], "of": item["of"],
                            "offset": item["offset"], "text": item["text"],
                            "at": schedule.wall_clock(cfg, item["local"]).isoformat()})
    return sorted(out, key=lambda i: datetime.fromisoformat(i["at"]).astimezone(timezone.utc))


class SummaryKept(Exception):
    """A refresh that does not replace the summary section (``why``): words of his might be in it."""


def _write_summary(cfg: Config, prog: Program, run: dict, s: int, *, refresh: bool = False) -> tuple[str, str]:
    """(summary, document) of session ``s`` filled from his words, written into the entry as its ``## Summary``. The
    digest of the section as written is kept on the session (``summary_sha``; the caller saves the run). With
    ``refresh`` it only replaces a section that is still exactly what the engine last wrote (``SummaryKept`` when it
    is not, or has nothing to compare with, or has a block of his under it, or is not there): see
    ``refresh_summary``. Without it (the session completing, or its transcript arriving) it writes the section as
    the entry's layout says, as it always did."""
    session = prog.session(s)
    summary = fill_template(cfg, prog, run, s, session.summary)
    document = fill_template(cfg, prog, run, s, session.document) if session.document else ""
    path = entry_path(cfg, run, s)
    if path is not None and path.is_file() and (summary or document):
        text = summary + (f"\n\n### {session.document_title}\n\n{document}" if document else "")
        with ledger.locked(cfg.ledger_path):  # every writer of entry files takes this lock
            if refresh:  # (read under the lock: nothing of his can be added between this check and the write)
                why = entries.summary_unsafe_to_replace(path.read_text(encoding="utf-8"),
                                                        _session(run, s).get("summary_sha"))
                if why:
                    raise SummaryKept(why)
            digest = entries.write_summary(path, text, cfg.names)
        if digest:
            _session(run, s)["summary_sha"] = digest
    elif refresh:
        raise SummaryKept("the entry or its summary is not there")
    return summary, document


def _transcripts_pending(cfg: Config, run: dict, s: int) -> bool:
    """Whether an audio answer of session ``s`` is still waiting for its transcript."""
    if not audio.transcription_enabled(cfg):
        return False
    return any(c.get("transcript") == "pending" for q in ledger.snapshot(cfg.ledger_path)["prompts"]
               if q.get("run") == run["id"] and q.get("session") == s for c in q.get("captures") or [])


def _refill_summary(cfg: Config, prog: Program, run: dict, k: str, sess: dict, log: list[str]) -> None:
    """A session that completed with an audio answer still being transcribed has ``_(skipped)_`` in that slot: once
    no transcript is pending the entry's ``## Summary`` is written again from his words (it is the engine's section;
    the message that was sent stays as sent)."""
    if not sess.get("summary_audio_pending") or _transcripts_pending(cfg, run, int(k)):
        return
    _write_summary(cfg, prog, run, int(k))
    sess.pop("summary_audio_pending", None)
    _save(cfg, run)
    log.append(f"{run['id']}: session {k} summary filled in from the transcript")


def session_of_entry(cfg: Config, run: dict, stem: str) -> int | None:
    """The number of the session of ``run`` whose entry is ``stem`` (None: the run has no session there)."""
    return next((int(k) for k in sorted(run.get("sessions") or {}, key=int) if entry_stem(cfg, run, int(k)) == stem),
                None)


def summary_refresh_due(run: dict, s: int) -> bool:
    """Whether an edit of one of his phase answers in session ``s`` should have its ``## Summary`` written again:
    only a session that has completed (an open one writes the summary when it completes, from the words as they
    are then), and not one still waiting for an audio answer's transcript (``_refill_summary`` writes it when the
    transcript is in, from the words as they are then)."""
    sess = (run.get("sessions") or {}).get(str(s)) or {}
    return sess.get("status") == "completed" and not sess.get("summary_audio_pending")


def claim_refresh(cfg: Config, run_id: str, s: int):
    """The "a refresh of this session is already waiting" lock, taken without waiting: the open file (give it to
    ``release_refresh``) or None when another spawned ``refresh-summary`` for the session holds it. That one reads
    the entry only once it has the tick's lock, so it will see this edit as well: a second one has nothing to add
    and exits."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    f = open(cfg.state_dir / f"refresh-summary-{run_id}-s{s}.lock", "a+")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def release_refresh(f) -> None:
    """Let the next refresh of the session queue behind the tick's lock (call it once that lock is held, before the
    entry is read: an edit saved after the read is not in it and needs a refresh of its own)."""
    with contextlib.suppress(OSError):
        fcntl.flock(f, fcntl.LOCK_UN)
    f.close()


def refresh_summary(cfg: Config, run_id: str, s: int, log: list[str]) -> bool:
    """Write the entry's ``## Summary`` of a completed session again from his words as they stand (he edited a phase
    answer after the session ended). Run under the tick's lock (``journal program refresh-summary``, spawned by the
    web app): ``complete_session`` and ``_refill_summary`` write it under that lock too, so this cannot interleave
    with them, and it takes the ledger lock only inside ``_write_summary``, in the engine's order. The state is read
    again here, so a request that raced a ``/back``, a re-run or a refill does nothing (silently: it is not news).

    It fails closed: the section is replaced only if it is byte for byte what the engine last wrote (the digest kept
    on the session by ``_write_summary``), is found by the engine's mark, and has no block of his under it. A section
    he added to or moved, a mark he deleted, an entry completed before the digest was kept: left as they are, and the
    log says so (his words come before a fresher summary). It changes the summary's own text and the digest on the
    session, nothing else: the message already sent and a compiled insights document stay as they are
    (``/start <program> insights`` compiles the document again from the entries). Whether it wrote is returned."""
    run = find_run(cfg, run_id)
    if run is None:
        log.append(f"{run_id}: no such run; summary of session {s} left as it is")
        return False
    if not summary_refresh_due(run, s):
        return False   # (an open session writes it when it completes, a waiting transcript when it arrives)
    try:
        _write_summary(cfg, _program(cfg, run["program"]), run, s, refresh=True)
    except SummaryKept as e:
        log.append(f"{run_id}: session {s} summary left as it is, not written from his edited words: {e}")
        return False
    _save(cfg, run)
    log.append(f"{run_id}: session {s} summary written again from his edited words")
    return True


def complete_session(cfg: Config, run: dict, s: int, now: datetime, log: list[str]) -> None:
    """The session's last phase is done: fill ``## Summary`` (and the session's document) from his words into the
    entry, send it as the session-complete message with the keyboard removed, say what comes next, file the
    debrief follow-up, and, after the last session, compile the insights document."""
    prog = _program(cfg, run["program"])
    session = prog.session(s)
    sess = _session(run, s)
    last = session.phases[-1]
    summary, document = _write_summary(cfg, prog, run, s)
    if _transcripts_pending(cfg, run, s):  # (an audio answer given just before /next: the tick fills the slot in)
        sess["summary_audio_pending"] = True
    sess.update(status="completed", completed_at=_iso(now))
    sess.pop("completion_due", None)
    _close_phases(cfg, run, session=s, session_complete=True)
    done = {int(k) for k, v in (run.get("sessions") or {}).items() if v.get("status") == "completed"}
    left = [] if prog.situational else [k for k in range(1, len(prog.sessions) + 1) if k not in done]
    if left:  # the next session not done: after this one, else (he jumped ahead) the first one before it
        k = next((k for k in left if k > s), left[0])
        nxt = prog.session(k)
        run["current"] = {"session": k, "phase": 1}
        _session(run, k).setdefault("status", "pending")
        next_line = (f"Next: /start {prog.id} continues with session {k} ({nxt.title}) whenever you're ready.")
    else:  # every session is done (finishing the last one after a jump is not the end of the run)
        run.update(status="completed", completed_at=_iso(now))
        next_line = still_open_line(cfg, prog, now) if prog.situational else ""
    sess["next_line"] = next_line  # (no words of his): a summary send that is retried says the same
    run["last_activity_at"] = _iso(now)
    _save(cfg, run)
    log.append(f"{run['id']}: session {s} complete" + ("; run complete" if run["status"] == "completed" else ""))
    _send_summary(cfg, prog, run, s, now, log, last.completion, summary, document, next_line)
    debrief = None
    if prog.debrief:
        with contextlib.suppress(library.UnknownDefinition):
            debrief = library.load(cfg).program(prog.debrief)
    if debrief is not None and debrief.enabled:
        try:
            notebook.add(cfg, f"{prog.title}, session {s} ({session.title}) is complete: offer the "
                              f"{debrief.title.lower()} when it fits (/start {debrief.aliases[0] if debrief.aliases else debrief.id}).",
                         source=entry_stem(cfg, run, s) or run["id"], now=now)
        except Exception as e:  # noqa: BLE001 - the session is complete; the follow-up is a courtesy
            log.append(f"{run['id']}: debrief follow-up not saved ({type(e).__name__}: {e})")
    if run["status"] == "completed" and not prog.situational and prog.insights:
        try:
            insights(cfg, run["id"], now, log)
        except Exception as e:  # noqa: BLE001 - the export can be made again with `journal program insights`
            log.append(f"{run['id']}: insights not written ({type(e).__name__}: {e})")


def still_open_line(cfg: Config, prog: Program, now: datetime) -> str:
    """After a situational session that ran while a program was mid-session (situational ones are never held back,
    DECISIONS.md, ONE-PROGRAM-RUN): the program's open phase is no longer the newest prompt, so it no longer takes
    the next message, and this summary's send removes its keyboard. Say how to pick it up: ``/start`` sends the open
    phase again. (Also said by ``/abandon`` of a situational run.)"""
    other = session_in_progress(cfg, now, besides=prog.id)
    if other is None:
        return ""
    try:
        held = _program(cfg, other["program"])
        s, p = other["current"]["session"], other["current"]["phase"]
        at = "" if held.situational else f"session {s}, "
        return f"{held.title} is still open at {at}phase {p}: /start {_start_word(held)} sends it again."
    except (ProgramError, KeyError, IndexError, TypeError):
        return ""


def _send_summary(cfg: Config, prog: Program, run: dict, s: int, now: datetime, log: list[str], completion: str,
                  summary: str, document: str, next_line: str) -> bool:
    session = prog.session(s)
    html = compose.summary_html(prog, s, completion, summary, session.document_title, document, next_line, cfg.names)
    sess = _session(run, s)
    try:
        mid = cfg.channel().send(cfg.chat_id, html, keyboard=compose.REMOVE_KEYBOARD)
    except ChannelError as e:
        sess["summary_attempts"] = sess.get("summary_attempts", 0) + 1
        sess["summary_error"] = str(e)[:300]
        _save(cfg, run)
        log.append(f"{run['id']}: session {s} summary not sent ({e}); attempt {sess['summary_attempts']}/"
                   f"{SUMMARY_ATTEMPTS}")
        return False
    sess.update(summary_sent_at=_iso(now), summary_message_id=mid)
    sess.pop("summary_error", None)
    _save(cfg, run)
    plain = "\n\n".join(t for t in (completion, summary, document, next_line) if t)
    what = (f"{cfg.names.companion} sent the summary of {prog.id} " +
            (f"session {s} " if not prog.situational else "") + f"at {mirror.stamp(cfg, now)}")
    mirror.to_chat(cfg, what, plain, log)
    return True


def step(cfg: Config, now: datetime, log: list[str], *, tried: set[str] | None = None) -> None:
    """The tick's step (the tick holds its lock): deliver a phase a transition owed but never sent, settle-advance
    a phase whose spawn was lost, retry an unsent summary, close a situational run past ``expires_hours``. A run
    whose send fails does not stop the others: its alert is raised, once, after the loop. Nor does a run that
    crashes (an edited definition, a damaged file): it is logged and alerted by id, and session 10's interrupts and
    the syntheses, which are time-bound and belong to other runs, still go. ``tried``: the prompt ids the tick's
    earlier pass at the interrupts (``send_due_interrupts``, ahead of its practices) already attempted: this pass
    sends the ones a settle above has just timed, and does not try those again (a failed send counts once a tick)."""
    from .tick import Alert
    alerts: list[str] = []
    failed: list[Alert] = []
    for run in all_runs(cfg):
        try:
            _step_run(cfg, run, now, log, alerts)
        except Alert as e:  # (a send that used its last attempt: the alert is kept, the other runs go on)
            failed.append(e)
            alerts.append(str(e))
        except Exception as e:  # noqa: BLE001 - this run waits; the others (and the interrupts) must not
            log.append(f"{run.get('id')}: step failed ({type(e).__name__}: {e})")
            alerts.append(f"{run.get('id')}: programs step crashed ({type(e).__name__}: {e})")
    try:
        alerts += send_interrupts(cfg, now, log, tried)
    except Exception as e:  # noqa: BLE001 - the syntheses below still run; the interrupts are tried again next tick
        log.append(f"interrupts: step failed ({type(e).__name__}: {e})")
        alerts.append(f"interrupts: {type(e).__name__}: {e}")
    for run in all_runs(cfg):  # a synthesis that waited for a model (or for another try)
        if (run.get("synthesis") or {}).get("status") == "pending":
            try:
                alerts += synthesize(cfg, run["id"], now, log)
            except Exception as e:  # noqa: BLE001 - the compiled document stands; tried again next tick
                log.append(f"{run['id']}: synthesis not written ({type(e).__name__}: {e})")
    if len(failed) == 1 and len(alerts) == 1:
        raise failed[0]  # (its own type: SendFailed)
    if alerts:
        raise Alert("; ".join(alerts))


def _step_run(cfg: Config, run: dict, now: datetime, log: list[str], alerts: list[str]) -> None:
    """One run's share of the tick's ``step``."""
    from .tick import SEND_ATTEMPTS
    if run.get("status") == "completed":  # (a completed run still owes a summary its send failed)
        try:
            prog = _program(cfg, run["program"])
        except ProgramError:
            return
        for k, sess in sorted((run.get("sessions") or {}).items()):
            _refill_summary(cfg, prog, run, k, sess, log)
            _retry_summary(cfg, prog, run, k, sess, now, log, alerts)
        return
    if run.get("status") == "expired" and run.get("expiry_notice"):  # (a note about cancelled interrupts not yet sent)
        _owed_expiry_notice(cfg, run, now, log, alerts)
        return
    if run.get("status") not in ACTIVE:
        return
    try:
        prog = _program(cfg, run["program"])
    except ProgramError:
        return  # the definition is gone or broken: doctor says so; the run waits
    if _expired(cfg, prog, run, now):
        _expire(cfg, prog, run, now, log)
        return
    for k, sess in sorted((run.get("sessions") or {}).items()):
        if sess.get("completion_due") and sess.get("status") == "in_progress":
            log.append(f"{run['id']}: session {k} was left completing; finishing it")
            try:
                complete_session(cfg, run, int(k), now, log)
            except Exception as e:  # noqa: BLE001 - tried again next tick; the log has why
                log.append(f"{run['id']}: session {k} still cannot complete ({type(e).__name__}: {e})")
            run = find_run(cfg, run["id"]) or run
            continue
        _refill_summary(cfg, prog, run, k, sess, log)
        _retry_summary(cfg, prog, run, k, sess, now, log, alerts)
    if run["status"] != "active":
        return
    s, p = run["current"]["session"], run["current"]["phase"]
    if _session(run, s).get("status") == "in_progress" and _phase(run, s, p).get("send_due"):
        rec = ledger.find(ledger.snapshot(cfg.ledger_path), prompt_id(run["id"], s, p)) or {}
        if rec.get("send_attempts", 0) >= SEND_ATTEMPTS:
            return  # given up (alerted once); his next /start, /next or /skip tries again
        log.append(f"{run['id']}: session {s} phase {p} was owed a send; sending it")
        _send_current(cfg, prog, run, now, log, auto=True)  # (not his act: it must not close a /x window)
        return
    settle(cfg, run["id"], now, log)


def _retry_summary(cfg: Config, prog: Program, run: dict, k: str, sess: dict, now: datetime, log: list[str],
                   alerts: list[str]) -> None:
    """Send a session's completion message again after a failed send (the same text: summary and document are
    recomputed from his words, the next-session line was kept). The failure that uses the last attempt is alerted."""
    if sess.get("status") != "completed" or sess.get("summary_sent_at") \
            or sess.get("summary_attempts", 0) >= SUMMARY_ATTEMPTS:
        return  # (zero attempts is a completion that was saved but died before its message)
    session = prog.session(int(k))
    doc = fill_template(cfg, prog, run, int(k), session.document) if session.document else ""
    if not _send_summary(cfg, prog, run, int(k), now, log, session.phases[-1].completion,
                         fill_template(cfg, prog, run, int(k), session.summary), doc, sess.get("next_line") or "") \
            and sess.get("summary_attempts", 0) >= SUMMARY_ATTEMPTS:
        alerts.append(f"{run['id']} session {k}: the completion message was not delivered after "
                      f"{SUMMARY_ATTEMPTS} attempts ({sess.get('summary_error', '')}); the summary is in the entry")


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path | None) -> str:
    try:
        return path.read_text(encoding="utf-8") if path else ""
    except OSError:
        return ""


# ── the insights document ────────────────────────────────────────────────────

def _sections(cfg: Config, prog: Program, run: dict) -> list[str]:
    """The deterministic body of the insights document: per session its title, dates, filled summary, his answers in
    full under their phase labels and, for a session with interrupts, each one's question and his answer to it."""
    out = []
    for session in prog.sessions:
        sess = (run.get("sessions") or {}).get(str(session.number)) or {}
        if not sess.get("entry"):
            continue
        body = _read(entry_path(cfg, run, session.number))
        days = [datetime.fromisoformat(sess[k]).astimezone(cfg.tz).strftime("%-d %b %Y")
                for k in ("started_at", "completed_at") if sess.get(k)]
        when = " – ".join(dict.fromkeys(days))
        out.append(f"## Session {session.number}: {session.title}\n")
        stem_s = entry_stem(cfg, run, session.number)
        out.append(f"_{when} · [[{stem_s}]]_\n" if when else f"_[[{stem_s}]]_\n")
        summary = entries.summary_text(body)
        if not summary and sess.get("status") == "completed":  # (all skipped: no entry file was ever made)
            summary = fill_template(cfg, prog, run, session.number, session.summary)
        out.append(entries.demote_headings(summary) + "\n" if summary else "_(not completed)_\n")
        got = entries.phase_answers(body) if body else {}
        out.append(f"### {cfg.names.user_possessive_title} answers\n")
        for ph in session.phases:
            text = got.get(ph.number)
            out.append(f"**Phase {ph.number}: {ph.title}**\n\n"
                       + (entries.demote_headings(text) if text else SKIPPED) + "\n")
        items = sorted((i for i in run.get("scheduled") or [] if i.get("session") == session.number),
                       key=lambda i: (i.get("local", ""), i.get("n", 0)))
        if items:
            said = entries.interrupt_answers(body) if body else {}
            out.append(f"### {cfg.names.user_possessive_title} answers to the interrupts\n")
            for i in items:
                at = datetime.fromisoformat(i["local"]).strftime("%a %-d %b %H:%M")
                state = i.get("status", "pending")
                words = said.get(i["n"]) if state == "sent" else None
                out.append(f"**Interrupt {i['n']}/{i['of']} ({i['offset']}) · {at}** — {i['text']}\n\n"
                           + (entries.demote_headings(words) if words else
                              "_(no answer)_" if state == "sent" else
                              "_(not yet sent)_" if state == "pending" else f"_(not sent: {state})_") + "\n")
    return out


def _his_answers(cfg: Config, prog: Program, run: dict, compiled: str) -> str:
    """Only what he wrote in the run, and only what the compiled document shows: his answers to the phases and the
    interrupts. A quote in the synthesis is checked against this, not against the compiled document, whose question
    lines, titles and summary prose are the engine's and must not be quoted to him as his own; an answer added after
    the document was compiled is not among the answers above the synthesis, so it is not quotable either."""
    out = []
    for session in prog.sessions:
        body = _read(entry_path(cfg, run, session.number))
        if body:
            for text in (*(b for v in entries.phase_blocks(body).values() for b in v),
                         *(b for v in entries.interrupt_blocks(body).values() for b in v)):
                if entries.demote_headings(text) not in compiled:  # (a block as _sections wrote it into the answer)
                    continue
                for note in (audio.NOT_TRANSCRIBED, audio.TRANSCRIBING, audio.NO_TRANSCRIPT):
                    text = text.replace(note, "")  # the engine's note about a recording is not his words
                out.append(text)
    return "\n\u00b6\n".join(out)  # (a quote cannot run from the end of one answer into the next)


def _wants_synthesis(cfg: Config, prog: Program) -> bool:
    """The program's file asks for it (``insights: compile+synthesis``) or ``programs.synthesis`` is on."""
    return prog.insights == "compile+synthesis" or cfg.programs.get("synthesis") is True


def insights(cfg: Config, run_id: str | None, now: datetime, log: list[str]) -> Path:
    """Compile ``YYYY-MM-DD-<program>-insights.md`` (``kind: insights``, ``author: engine``): per session its title,
    dates, filled summary and his answers in full under their phase labels, and his answers to its interrupts.
    Deterministic; no model. Made again (same file) for the same run. ``run_id`` None: his latest run of a program
    that has started. When the program asks for a synthesis (``_wants_synthesis``) the model's reading is appended
    afterwards (``synthesize``); nothing that fails there touches the compiled document."""
    run = find_run(cfg, run_id) if run_id else None
    if run is None:
        runs = [r for r in all_runs(cfg) if not run_id or r["program"] == run_id]
        run = max(runs, key=lambda r: r.get("last_activity_at") or "", default=None)
    if run is None:
        raise ProgramError("No run to compile yet: /start <program> begins one.")
    prog = _program(cfg, run["program"])
    local = now.astimezone(cfg.tz)
    stem = run.get("insights_entry")
    if not stem:
        base, n = f"{local.date().isoformat()}-{prog.id}-insights", 1
        stem = base
        while entries.entry_path(cfg.root, stem).exists():
            fm = entries.front_matter_of(entries.entry_path(cfg.root, stem))
            if fm.get("author") == "engine" and fm.get("run") == run["id"]:
                break
            n += 1
            stem = f"{base}-{n}"
    body = "\n".join(_sections(cfg, prog, run))
    corpus = hashlib.sha256(body.encode("utf-8")).hexdigest()
    syn = run.get("synthesis") or {}
    path = entries.entry_path(cfg.root, stem)
    if syn.get("status") == "written" and syn.get("corpus") == corpus and path.exists() \
            and _sha(path) == run.get("insights_sha"):
        log.append(f"{run['id']}: insights unchanged since the synthesis was written; {path.name} stays")
        return path  # (nothing new to say: no second model call for the same answers)
    out = [f"---\ndate: {local.date().isoformat()}\nkind: insights\nauthor: engine\nprogram: {prog.id}\n"
           f"run: {run['id']}\nsource: {cfg.names.frontmatter_source}\n---\n",
           f"# {prog.title} — insights\n",
           f"_Compiled {local.strftime('%a %-d %b %Y, %H:%M %Z')} from run {run['id']}: each session's summary and "
           f"{cfg.names.possessive} answers, verbatim._\n", body]
    if path.exists() and _sha(path) != run.get("insights_sha"):
        # he may have written in the document since the engine did (or the engine died before it noted what it
        # wrote): it stays as it is, and this one is a new file
        base, n = re.sub(r"-\d+$", "", stem), 2
        while entries.entry_path(cfg.root, f"{base}-{n}").exists():
            n += 1
        stem = f"{base}-{n}"
        path = entries.entry_path(cfg.root, stem)
        log.append(f"{run['id']}: the earlier insights document was edited; compiling a new one")
    with ledger.locked(cfg.ledger_path):
        entries.write_file(path, "\n".join(out).rstrip() + "\n")
    run["insights_entry"] = stem
    run["insights_sha"] = _sha(path)
    if _wants_synthesis(cfg, prog):
        if not (syn.get("status") == "pending" and syn.get("corpus") == corpus):  # (else its attempts and waits stand)
            run["synthesis"] = {"status": "pending", "corpus": corpus}
    else:
        run.pop("synthesis", None)
    _save(cfg, run)
    log.append(f"{run['id']}: insights written to {path.name}")
    if run.get("synthesis"):
        synthesize(cfg, run["id"], now, log, quiet=True)
    return path


def synthesize(cfg: Config, run_id: str, now: datetime, log: list[str], *, quiet: bool = False) -> list[str]:
    """Append the companion's synthesis to the run's compiled insights document: a one-shot without tools, validated
    like a reflection (every quote must be in the compiled text), under ``## <Companion>'s synthesis``. The compiled
    document is the only thing it reads and it is never rewritten, only added to:

    - the model cannot be reached: deferred (``outage``: tried again at most every 30 minutes for up to 7 days, an
      alert once it has been 6 hours), no attempt used;
    - the model answers badly (a misquote, too long, not prose): an attempt, ``SYNTHESIS_ATTEMPTS`` of them, then the
      document stands without a synthesis;
    - he has written in the document since it was compiled: left alone.

    ``quiet`` (a manual compile, which has nowhere to raise an alert): the one-time outage alert and the give-up are
    left for the tick's next try instead of being used up here.

    Returns the alerts to raise (empty when none)."""
    run = find_run(cfg, run_id)
    syn = (run or {}).get("synthesis") or {}
    if run is None or syn.get("status") != "pending" or outage.waiting(syn, now):
        return []
    prog = _program(cfg, run["program"])
    if not _wants_synthesis(cfg, prog):  # asked for when it was compiled, switched off since
        syn.update(status="skipped", reason="no longer asked for")
        _save(cfg, run)
        log.append(f"{run['id']}: no synthesis: it is no longer asked for")
        return []
    path = entries.entry_path(cfg.root, run.get("insights_entry") or "")
    if not run.get("insights_entry") or not path.is_file() or _sha(path) != run.get("insights_sha"):
        syn.update(status="skipped", reason="the document is not as it was compiled")
        _save(cfg, run)
        log.append(f"{run['id']}: no synthesis: the insights document was written in or is gone")
        return []
    compiled = _read(path)
    shown = compiled if len(compiled) <= SYNTHESIS_INPUT_CAP else compiled[:SYNTHESIS_INPUT_CAP] + \
        "\n[… the rest of the document is omitted here]"
    try:
        raw = agent.ask(cfg, prompts.insights_request(cfg, prog, shown), SYNTHESIS_BUDGET, toolsets="none")
    except agent.AgentUnavailable as e:
        alert = outage.defer(syn, now, str(e))
        gave_up = outage.expired(syn, now)
        if quiet and (alert or gave_up):  # not here: the tick's next try raises it
            if alert:
                syn.pop("defer_alerted_at", None)
            alert = gave_up = False
        if gave_up:
            syn.update(status="failed", reason=f"the model was unreachable for {outage.GIVE_UP_AFTER.days} days")
        _save(cfg, run)
        log.append(f"{run['id']}: synthesis deferred, model unreachable ({e})")
        if gave_up:
            return [f"{run['id']}: the insights synthesis was not written: the model was unreachable for "
                    f"{outage.GIVE_UP_AFTER.days} days ({e})"]
        return ([f"{run['id']}: the insights synthesis is still not written: the model has been unreachable "
                 f"{outage.describe(syn, cfg.tz)}; retrying every 30 minutes for up to 7 days"] if alert else [])
    outage.clear(syn)
    ok, why = validate.check_reflection("insights", raw, _his_answers(cfg, prog, run, compiled))
    if not ok:
        syn["attempts"] = syn.get("attempts", 0) + 1
        syn["error"] = why[:200]
        if syn["attempts"] >= SYNTHESIS_ATTEMPTS:
            syn["status"] = "failed"
        _save(cfg, run)
        log.append(f"{run['id']}: synthesis rejected ({why}); attempt {syn['attempts']}/{SYNTHESIS_ATTEMPTS}")
        return []
    n = cfg.names
    text = (compiled.rstrip() + f"\n\n## {n.companion}'s synthesis\n\n_{n.companion}'s reading of the answers above: "
            f"hers, not {n.possessive}._\n\n{entries.demote_headings(raw.strip())}\n")
    with ledger.locked(cfg.ledger_path):
        if not path.is_file() or _sha(path) != run.get("insights_sha"):  # he wrote in it during the model call
            syn.update(status="skipped", reason="written in since compiled")
            _save(cfg, run)
            log.append(f"{run['id']}: no synthesis: the insights document was written in during the model call")
            return []
        entries.write_file(path, text)
    syn.update(status="written", written_at=_iso(now))
    syn.pop("error", None)
    run["insights_sha"] = _sha(path)
    _save(cfg, run)
    log.append(f"{run['id']}: synthesis added to {path.name}")
    return []


# ── what he and the companion are told ───────────────────────────────────────

def _since(cfg: Config, iso: str | None, now: datetime) -> str:
    if not iso:
        return ""
    at = datetime.fromisoformat(iso).astimezone(cfg.tz)
    local = now.astimezone(cfg.tz)
    if at.date() == local.date():
        return at.strftime("%H:%M")
    if (local.date() - at.date()).days < 7:
        return at.strftime("%a %H:%M")
    return at.strftime("%-d %b")


def describe(cfg: Config, run: dict, now: datetime) -> str:
    """Where a run is, in one line: ``Fix your entire life in 1 day — session 3/10 (The Identity Construction
    Tracer), phase 2/5 open since Sun 21:14``."""
    try:
        prog = _program(cfg, run["program"])
    except ProgramError:
        return f"{run['program']} (its definition is missing)"
    if run.get("status") == "completed":
        return f"{prog.title}: complete"
    s, p = run["current"]["session"], run["current"]["phase"]
    if not 1 <= s <= len(prog.sessions) or not 1 <= p <= len(prog.session(s).phases):
        # he edited the program under a run in progress: say so rather than index past its end (the run list and
        # the chat's status read every run, ended ones too, so one stale run must not break them)
        return f"{prog.title} (its definition changed: session {s}, phase {p} is no longer there)"
    session = prog.session(s)
    where = "" if prog.situational else f" — session {s}/{len(prog.sessions)} ({session.title})"
    sess = _session(run, s)
    if sess.get("status") != "in_progress":
        done = f"session {s - 1} done; " if s > 1 else ""
        return f"{prog.title} — {done}session {s}/{len(prog.sessions)} ({session.title}) whenever ready"
    ph = _phase(run, s, p)
    sep = ", " if where else " — "
    state = f"{sep}phase {p}/{len(session.phases)}"
    caps = _captures(cfg, ph.get("prompt_id"))
    if ph.get("send_due"):
        state += " about to be sent"
    elif caps:
        state += f" answered {_since(cfg, max(c['at'] for c in caps), now)}"
    else:
        state += f" open since {_since(cfg, ph.get('sent_at'), now)}"
    if ph.get("confirm_asked") and session.phase(p).type == "schedule":
        state += " · waiting for a day and wake time (YYYY-MM-DD HH:MM)"
    if run.get("status") == "paused":
        state += f" · on a break since {_since(cfg, run.get('paused_since'), now)}"
    return f"{prog.title}{where}{state}"


def status(cfg: Config, now: datetime | None = None) -> dict:
    """The library's programs and the runs in progress (what ``/program`` and ``journal program status`` show)."""
    now = now or datetime.now(timezone.utc)
    lib = library.load(cfg)
    runs = active_runs(cfg)
    return {
        "programs": [{"id": p.id, "kind": p.kind, "title": p.title, "emoji": p.emoji, "aliases": list(p.aliases),
                      "sessions": len(p.sessions), "advance": p.advance, "enabled": p.enabled,
                      "source": "builtin" if p.builtin else "user"} for p in lib.programs.values()],
        "runs": [{"program": r["program"], "id": r["id"], "status": r["status"], "current": r.get("current"),
                  "started_at": r.get("started_at"), "text": describe(cfg, r, now)} for r in runs],
        "scheduled": pending_interrupts(cfg),  # the interrupts still to come, soonest first
    }


def _day_name(at: datetime, local: datetime) -> str:
    """``Monday 2 November``, with the year when it is not this one."""
    return at.strftime("%A %-d %B" if at.year == local.year else "%A %-d %B %Y")


def _when(at: datetime, local: datetime) -> str:
    """An interrupt's time as the status shows it: the clock time today, the weekday within the week, else the date
    (and the year when it is not this one), so a mistyped day is seen and not just sat on."""
    days = (at.date() - local.date()).days
    if days == 0:
        return at.strftime("%H:%M")
    if 0 < days <= 6:
        return at.strftime("%a %H:%M")
    return at.strftime("%a %-d %b %H:%M" if at.year == local.year else "%a %-d %b %Y %H:%M")


def status_text(cfg: Config, now: datetime | None = None) -> str:
    s = status(cfg, now)
    lines = [f"• {r['text']}" for r in s["runs"]] or ["No program is running."]
    startable = [p for p in s["programs"] if p["enabled"]]
    if startable:
        lines.append("")
        lines.append("Programs: " + ", ".join(
            f"/start {p['aliases'][0] if p['aliases'] else p['id']} ({p['title']})" for p in startable) + ".")
    if s["runs"]:
        lines.append("While one runs: /next · /skip · /back · /break; /start <id> picks it up again; /abandon <id> "
                     "ends it. One program at a time (situational sessions aside): another starts once it is "
                     "finished or abandoned.")
    if s["scheduled"]:
        local = now.astimezone(cfg.tz) if now else datetime.now(cfg.tz)
        when = [_when(datetime.fromisoformat(i["at"]), local) for i in s["scheduled"]]
        lines.append(f"Interrupts to come ({len(when)}): " + ", ".join(when) + ".")
    return "\n".join(lines)


def today_line(cfg: Config, now: datetime) -> dict | None:
    """The today block's ``program`` line: the run he last did something in."""
    run = current_run(cfg)
    return {"text": describe(cfg, run, now), "program": run["program"]} if run else None


def reply_note(cfg: Config, info: dict) -> str:
    """What the companion sees in front of a captured phase answer. It opens and ends like the other journal
    reply notes (``[Journal reply — … annotations are written automatically.]``): the sweep, the conversation
    writer and the pack readers recognise and strip it by those."""
    p, n = info["prompt"], cfg.names
    if p.get("interrupt"):  # a timed question the tick sent (``send_interrupts``), not a phase of the run
        return (f"[Journal reply — interrupt reply: {n.user_possessive} answer to interrupt {p['interrupt']}/"
                f"{p.get('interrupt_count', '?')} ({p.get('interrupt_offset', '')}) of {p.get('title') or p['kind']}, "
                f"session {p['session']} “{p.get('session_title', '')}” (the question was: “{p.get('interrupt_text', '')}”); "
                f"saved verbatim to {info['path']}. Respond to what {n.subject} wrote, as {n.companion}, and don't ask "
                f"the next question or move the program on yourself. Your chat replies are saved into the entry too, "
                f"and annotations are written automatically.]\n\n")
    where = (f"session {p['session']} “{p.get('session_title', '')}”, " if p.get("session_count") else "")
    run = find_run(cfg, p["run"]) if p.get("run") else None
    if run is not None and run.get("status") == "paused":
        sends = f"Nothing moves on while the program is on a break; {n.subject} picks it up again with /start {p['kind']}"
    else:
        how = ("a couple of minutes after " + n.subject + " goes quiet, or at once on /next"
               if p.get("advance", "settle") == "settle" else f"when {n.subject} {n.verb('send')} /next")
        sends = f"The engine sends the next phase itself ({how})"
    return (f"[Journal reply — program reply: {n.user_possessive} answer to {p.get('title') or p['kind']}, {where}"
            f"phase {p['phase']}/{p.get('phase_count', '?')} “{p.get('phase_title', '')}”; saved verbatim to "
            f"{info['path']}. {sends}: respond to what {n.subject} wrote, as "
            f"{n.companion}, and don't ask the next phase's questions or move the program on yourself. Your chat "
            f"replies are saved into the entry too, and annotations are written automatically.]\n\n")


def active_ids(cfg: Config) -> set[str]:
    """Run ids still in progress: ``tick.prune`` keeps their prompts whatever their age."""
    return {r["id"] for r in active_runs(cfg)}
