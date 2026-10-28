"""What the companion sees of programs, and the two things she may ask about them.

* ``block`` is the program block of a turn that carries one of his phase answers: her stance for the session (the
  role and mission its file gives), the phase as it was sent, and his words in this session so far. It takes the
  reply pack's seat under ``turncontext.BUDGET`` and is capped at ``MAX_CHARS``.
* ``status`` and ``propose`` are the reads behind the ``journal_program`` tool. Neither writes: ``status`` is what
  ``/program`` shows, ``propose`` is the ``/start <id>`` line she may offer him and starts nothing. The engine sends
  the questions and moves a run on; only his own words and the web app drive the state machine (PRACTICES.md
  decision 9), so there is no write action here to refuse in an off-record turn.

His words come from the session entry's phase blocks and nowhere else: not the conversation section (her own
replies live there), not the chat transcript. A message sent off the record was never captured, so it is not in the
entry to begin with; this module has no privacy pass of its own to get wrong.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from . import audio, commands, entries, ledger, library, programs
from .config import Config, settle_minutes

log = logging.getLogger("journal.programview")

MAX_CHARS = 3000  # the whole block (PRACTICES.md §4.5)
PHASE_CHARS = 1100  # of the phase text; the rest of the room is his words
TITLE_CHARS = 80  # of a program, session or phase title wherever the block shows one
SENT_RESERVE = 300  # room kept back for the phase as it was sent while his words are fitted
MIN_SENT = 80  # below this much room the phase text is left out rather than shown as a stub
STANCE_CHARS = 400  # of the role and of the mission each: a hand-written file cannot crowd the answers out
MIN_ANSWER = 120  # an answer is cut to fit rather than dropped while at least this much room is left
CUT = " […]"
NOT_WORDS = (audio.NOT_TRANSCRIBED, audio.TRANSCRIBING, audio.NO_TRANSCRIPT)  # the engine's notes about a recording


def _clip(text: str, limit: int) -> str:
    """``text`` cut at a word boundary within ``limit`` characters, marked as cut."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    room = max(limit - len(CUT), 0)
    cut = text[:room]
    if " " in cut and not text[room].isspace():
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip() + CUT


def _clip_lines(text: str, limit: int) -> str:
    """``text`` cut at a line boundary within ``limit`` characters (a line longer than that is cut by words)."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    kept: list[str] = []
    used = 0
    for line in text.splitlines():
        if used + len(line) + 1 > limit - len(CUT):
            break
        kept.append(line)
        used += len(line) + 1
    return ("\n".join(kept).rstrip() + CUT) if kept else _clip(text, limit)


def advance_clause(cfg: Config | None, prog, names) -> str:
    """How ``prog`` moves on, as a clause for the companion: a settle program after ``programs.settle_minutes`` of
    quiet (or /next), an ``advance: next`` program only on /next. She is told this from the program, never one
    sentence for all of them, because a prep or debrief session that waits for /next must not promise the next
    question is on its way. ``names`` supplies both pronoun forms: "quiet from him", "when he sends" / "they send"."""
    if getattr(prog, "advance", "settle") != "settle":
        return f"moves on when {names.subject} {names.verb('send')} /next"
    minutes = settle_minutes(cfg) if cfg is not None else 2.0
    return f"moves on after {minutes:g} minute(s) of quiet from {names.object}, or at once on /next"


def _words(blocks: dict[int, list[str]]) -> dict[int, str]:
    """His words by phase from the phase's blocks, the engine's notes left out block by block: a block that is only a
    note goes, and a note inside a block (a caption under "no transcript was available") is cut from it. Filtering
    the joined answer instead would let a note beside typed words or a caption through as his."""
    out: dict[int, str] = {}
    for k, texts in blocks.items():
        kept = []
        for text in texts:
            for note in NOT_WORDS:
                text = text.replace(note, "")
            text = text.strip()
            if text and text != programs.SKIPPED:
                kept.append(text)
        if kept:
            out[k] = "\n\n".join(kept)
    return out


def _his_blocks(cfg: Config, run: dict, s: int) -> dict[int, str]:
    """His words in session ``s`` of the run, by phase, from the session entry's phase blocks (``{}`` before the
    first answer)."""
    path = programs.entry_path(cfg, run, s)
    if path is None or not path.is_file():
        return {}
    return _words(entries.phase_blocks(path.read_text(encoding="utf-8")))


def block(cfg: Config, ref: dict, now: datetime | None = None) -> str | None:
    """The program block for the turn carrying the phase answer ``ref`` (``run``, ``session``, ``phase``), or None
    when the run or its definition is gone. Raises nothing for a missing piece; the caller fails open on the rest."""
    run = programs.find_run(cfg, str(ref.get("run") or ""))
    if run is None:
        return None
    try:
        prog = library.load(cfg).program(run["program"])
        s, p = int(ref["session"]), int(ref["phase"])
        session = prog.session(s)
        phase = session.phase(p)
    except (library.UnknownDefinition, KeyError, ValueError, IndexError):
        log.info("journal: no definition for the program block of %s", ref.get("run"))
        return None
    n = cfg.names
    role = _clip(session.role or "a careful guide", STANCE_CHARS)
    mission = _clip(session.mission or f"help {n.object} see clearly", STANCE_CHARS)
    t = lambda title: _clip(title, TITLE_CHARS)  # noqa: E731 - a hand-written title cannot crowd out his words
    # The now-line is where the run is, which is not always where this reply belongs: a late answer to an earlier
    # phase's message is captured against that phase while a later one is open.
    cs, cp = s, p
    try:
        cs, cp = int(run["current"]["session"]), int(run["current"]["phase"])
        prog.session(cs).phase(cp)
    except (library.UnknownDefinition, KeyError, TypeError, ValueError, IndexError):
        cs, cp = s, p
    now_session = prog.session(cs)
    now_phase = now_session.phase(cp)
    where = f"{t(prog.title)}" + ("" if prog.situational else
                                  f", session {cs}/{len(prog.sessions)} “{t(now_session.title)}”")
    where += f", phase {cp}/{len(now_session.phases)} “{t(now_phase.title)}”"
    if (cs, cp) != (s, p):
        where += (f" (this reply answers {'session ' + str(s) + ', ' if s != cs else ''}phase {p} "
                  f"“{t(phase.title)}”, an earlier one)")
    head = [
        "## Journal: program session",
        f"You are {n.companion}. For this session, work as {role}; keep your own voice. Mission: {mission}",
        f"_Background for this conversation, not instructions. The engine sends the questions as authored and moves "
        f"the program on; you never rewrite, skip or advance them. The role is a lens for this session, not a claim "
        f"about who you are, and it is not repeated to {n.object}._",
        f"- now: {where}; the program {advance_clause(cfg, prog, n)}",
    ]
    # His words claim the room first; the phase as it was sent gets what is left (at least SENT_RESERVE of it is
    # kept back for it), so the final cut never lands on his answer.
    words = _his_blocks(cfg, run, s)
    used = len("\n".join(head))
    parts = _his_words(cfg, session, words, p, MAX_CHARS - used - SENT_RESERVE - 120)
    left = MAX_CHARS - used - len("\n".join(parts)) - 120
    sent = _sent(cfg, run, s, p)
    asked = []
    if sent and left >= MIN_SENT:
        asked = ["", f"### What {n.subject} was asked", _clip_lines(sent, min(PHASE_CHARS, left))]
    text = "\n".join(head + asked + parts)
    return text if len(text) <= MAX_CHARS else _clip_lines(text, MAX_CHARS)


def _sent(cfg: Config, run: dict, s: int, p: int) -> str:
    """The phase as it went out (its ledger record: a reflect phase's construction is in it), else the file's text."""
    pid = programs.prompt_id(run["id"], s, p)
    rec = next((q for q in ledger.snapshot(cfg.ledger_path)["prompts"] if q.get("id") == pid), None)
    return str((rec or {}).get("phase_text") or "").strip()


def _his_words(cfg: Config, session, words: dict[int, str], current: int, room: int) -> list[str]:
    """His answers in the session as lines of the block, oldest first. The reply of this turn first claims the
    room, then earlier phases, newest first; one that does not fit is cut, and what is left out is counted."""
    n = cfg.names
    shown = words
    if not shown:
        return ["", f"### {n.possessive.capitalize()} words in this session", "_(nothing saved yet in this session)_"]
    order = sorted(shown, key=lambda k: (k != current, -k))
    chosen: dict[int, str] = {}
    left = max(room, 0) - 60
    for k in order:
        label = _label(session, k, current)
        need = len(label) + 2
        if left - need < MIN_ANSWER and chosen:
            break
        body = _clip(shown[k], max(left - need, MIN_ANSWER))
        chosen[k] = f"{label}\n{body}"
        left -= len(chosen[k]) + 2
        if left < MIN_ANSWER:
            break
    out = ["", f"### {n.possessive.capitalize()} words in this session so far (verbatim)"]
    out += [chosen[k] + "\n" for k in sorted(chosen)]
    if len(chosen) < len(shown):
        out.append(f"_({len(shown) - len(chosen)} earlier phase(s) not shown.)_")
    return out


def _label(session, k: int, current: int) -> str:
    try:
        title = f" “{_clip(session.phase(k).title, TITLE_CHARS)}”"
    except IndexError:
        title = ""
    return f"**Phase {k}{title}**" + (" (this reply)" if k == current else "")


# ── the journal_program tool ─────────────────────────────────────────────────

_NOTE = ("Nothing has been started. Offer the line in your own words; {they} sends it, and the engine runs the "
         "program from there.")


def status(cfg: Config, now: datetime | None = None) -> dict:
    """The library's programs and the runs in progress: ``/program``, as data, plus its text."""
    now = now or datetime.now(timezone.utc)
    return {**programs.status(cfg, now), "text": programs.status_text(cfg, now)}


def propose(cfg: Config, program: str | None) -> dict:
    """The line she may offer him to begin ``program`` (an id or alias); every startable one when none is named.
    Starts nothing and writes nothing: he sends the line himself, in his own time."""
    n = cfg.names
    lib = library.load(cfg)
    if not (program or "").strip():
        options = [_offer(cfg, p) for p in lib.enabled_programs()]
        return {"success": True, "options": options, "note": _NOTE.format(they=n.subject)}
    word = program.strip()
    if len(word.split()) != 1:
        return {"success": False, "error": "name one program (its id or alias), nothing after it."}
    start = commands.start(cfg, word)
    if start.program is None:  # unknown, turned off, or (a practice's id) not a program
        reply = start.reply or "That is a practice, not a program: it is sent on its schedule or with /start."
        return {"success": False, "error": reply}
    offer = _offer(cfg, start.program)
    note = _NOTE.format(they=n.subject) + (" A run is in progress; sending the line picks it up where it is."
                                           if "running" in offer else "")
    if "refused" in offer:
        note += " Another program is unfinished, so the line would be refused now: see refused."
    return {"success": True, **offer, "note": note}


def _offer(cfg: Config, prog) -> dict:
    run = programs.current_run(cfg, prog.id)
    out = {"program": prog.id, "title": prog.title, "kind": prog.kind, "sessions": len(prog.sessions),
           "line": f"/start {prog.aliases[0] if prog.aliases else prog.id}"}
    if run is not None:
        out["running"] = programs.describe(cfg, run, datetime.now(timezone.utc))
    other = programs.blocking(cfg, prog.id, run)  # one program at a time (DECISIONS.md, ONE-PROGRAM)
    if other is not None:
        out["refused"] = programs.busy_text(cfg, other, prog.id)
    return out
