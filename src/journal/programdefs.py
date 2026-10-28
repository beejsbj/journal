"""Program and situational definitions as files: parsed, checked, and filled from his answers.

A *program* is a directory: ``program.md`` (front matter, and a body that is the intro shown when a run starts)
and ``sessions/NN-<id>.md``, one file per session, listed in order by ``sessions:``. A *situational* definition is
a one-session program in a single file: its phases are in the file itself. Both are read by ``library.load`` beside
the practices, so ``/start <id>`` resolves all three kinds against one library.

A session body keeps the shape of the files it was ported from, so they stay nearly verbatim (PRACTICES.md §1.4):

- text before the first phase is the session's preamble (shown with phase 1);
- ``## Phase N: Title`` opens a phase; an optional ``type: ask|reflect|task|schedule`` line directly under it
  (default ``ask``);
- ``### Heading`` opens a block inside the phase. ``Transition`` is the lead-in shown at the top of the *next*
  phase, ``Completion`` closes the session (shown before the summary); every other heading's text is sent with the
  phase as written, so nothing authored is lost;
- a line ``**N.M** …`` is a question with the id ``N.M`` (outside ``Transition``/``Completion``). An id with a
  letter (``**4A.1**``) is reference text: it is sent, but it is not a question of its own;
- ``## Summary`` is the template the engine fills when the session ends; ``## Final Document: …`` /
  ``## Generated Document`` is a second template compiled the same way.

Templates and phase text take ``{N.M}`` (his answer to that question's phase, in the summary and document),
``{user}`` (his name) and prose slots named in the front matter: ``slot.what you want to change: 1.1`` (a question of
this session) or ``slot.protective identity: s1:5.1`` (a question of session 1 of the same run). How a slot is
filled from his words is ``fill``'s business; this module only checks that every placeholder resolves.

Parsing is strict on structure (every key known, phases numbered 1..n, ids unique, placeholders resolvable) and
lenient on prose. A fault costs the one definition, never the library.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import entries

SESSION, PROGRAM, SITUATIONAL = "session", "program", "situational"
PHASE_TYPES = ("ask", "reflect", "task", "schedule")
ADVANCE = ("settle", "next")
INSIGHTS = ("compile", "compile+synthesis")
LEAD, CLOSE = "Transition", "Completion"  # the two headings that are not sent with their own phase
ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
SESSION_FILE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")  # (a session file name; no "/" and no leading dot)
_PHASE = re.compile(r"^## Phase ([0-9]+):\s*(.*?)\s*$")
_H2 = re.compile(r"^## (.*?)\s*$")
_H3 = re.compile(r"^### (.*?)\s*$")
_QLINE = re.compile(r"^\*\*([0-9]+[A-Z]?\.[0-9]+)\*\*\s*(.*?)\s*$")
_QUESTION_ID = re.compile(r"^[0-9]+\.[0-9]+$")
_TYPE = re.compile(r"^type:\s*(\S*)\s*$")
_PLACEHOLDER = re.compile(r"\{([^{}\n]+)\}")
_REF = re.compile(r"^(?:s([0-9]{1,2}):)?([0-9]+\.[0-9]+)$")
_DOCUMENT = re.compile(r"^(?:Final Document|Generated Document)\b")
_KEY = re.compile(r"^[a-z][a-z0-9_.]*$")
# one interrupt of a ``schedule`` phase: ``- +2h: "What am I avoiding right now …"`` (quotes optional)
# (up to 999.999 hours: a longer number is no interrupt of a day, and would overflow a date)
_INTERRUPT = re.compile(r"^\s*[-*•]\s*\+([0-9]{1,3}(?:\.[0-9]{1,3})?)\s*h\s*:\s*(.+?)\s*$")

QUESTION_LIMIT = 1500  # an interrupt question (it is one Telegram message with its header; longer is shortened)
TITLE_LIMIT = 120  # (the header of every message carries it; Telegram refuses one over 4096 characters)
SESSION_KEYS = ("kind", "id", "number", "title", "minutes", "framework", "role", "mission", "quote")
PROGRAM_KEYS = ("kind", "id", "title", "emoji", "group", "advance", "pace", "phases_of", "sessions", "debrief",
                "insights", "sources", "enabled", "aliases")
SITUATIONAL_KEYS = ("kind", "id", "title", "emoji", "group", "aliases", "expires_hours", "annotate", "advance",
                    "after", "enabled", "role", "mission", "quote", "minutes", "framework")


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    section: str | None
    pinned: bool = False


@dataclass(frozen=True)
class Phase:
    number: int
    title: str
    type: str
    blocks: tuple[tuple[str | None, str], ...]  # (heading, text) in file order, Transition/Completion left out
    questions: tuple[Question, ...]
    transition: str = ""
    completion: str = ""

    @property
    def intro(self) -> str:
        return next((t for h, t in self.blocks if h == "Introduction"), "")

    def text(self) -> str:
        """Everything the file says for this phase (headings included): what the content test reads."""
        out = [f"### {h}\n{t}" if h else t for h, t in self.blocks]
        out += [f"### {LEAD}\n{self.transition}"] if self.transition else []
        out += [f"### {CLOSE}\n{self.completion}"] if self.completion else []
        return "\n".join(out)


@dataclass(frozen=True)
class Interrupt:
    """One timed question of a ``schedule`` phase: asked ``hours`` after the wake time he confirms."""
    hours: float
    text: str

    @property
    def label(self) -> str:
        """``+2h``, ``+1.5h``: how the file and the messages name the offset."""
        return f"+{self.hours:g}h"


def interrupts(phase: Phase) -> tuple[Interrupt, ...]:
    """The interrupts a ``schedule`` phase lists, in file order: its ``- +Nh: "question"`` lines, wherever in the
    phase they are. Other phases have none (a bullet like that in an ``ask`` phase is only text)."""
    if phase.type != "schedule":
        return ()
    out = []
    for _, text in phase.blocks:
        for line in text.splitlines():
            m = _INTERRUPT.match(line)
            question = m.group(2).strip().strip("\"“”").strip() if m else ""
            if question:  # (``- +2h: ""`` asks nothing)
                out.append(Interrupt(float(m.group(1)), question))
    return tuple(out)


@dataclass(frozen=True)
class Session:
    id: str
    number: int
    title: str
    phases: tuple[Phase, ...]
    summary: str = ""
    document_title: str = ""
    document: str = ""
    preamble: str = ""
    minutes: str = ""
    framework: str = ""
    role: str = ""
    mission: str = ""
    quote: str = ""
    slots: tuple[tuple[str, str], ...] = ()  # (name, ref) from ``slot.<name>: <ref>``
    path: Path | None = field(default=None, compare=False)

    def phase(self, n: int) -> Phase:
        return self.phases[n - 1]

    def phase_of(self, qid: str) -> int | None:
        """The phase number holding question ``qid``."""
        return next((p.number for p in self.phases for q in p.questions if q.id == qid), None)


@dataclass(frozen=True)
class Program:
    id: str
    kind: str  # "program" | "situational"
    title: str
    sessions: tuple[Session, ...]
    emoji: str = ""
    group: str = ""
    advance: str = "settle"
    pace: str = ""
    phases_of: str = ""
    debrief: str | None = None
    insights: str = "compile"
    intro: str = ""
    aliases: tuple[str, ...] = ()
    expires_hours: float | None = None
    annotate: bool = True
    after: str | None = None
    sources: str = ""
    enabled: bool = True
    builtin: bool = False
    path: Path | None = field(default=None, compare=False)

    @property
    def situational(self) -> bool:
        return self.kind == SITUATIONAL

    def session(self, n: int) -> Session:
        return self.sessions[n - 1]


# ── parsing ──────────────────────────────────────────────────────────────────

def _split(text: str) -> tuple[dict[str, str], list[tuple[str, str]], str, list[str]]:
    """(front matter, its pairs, body, problems) — the same reader and the same strictness as a practice file."""
    problems: list[str] = []
    text = text.replace("\r\n", "\n").removeprefix("﻿")
    if not text.startswith("---\n"):
        return {}, [], text, ["no front matter: the file must start with a `---` block"]
    end = text.find("\n---\n", 3)
    if end < 0:
        if text.endswith("\n---"):
            block, body = text + "\n", ""
        else:
            return {}, [], text, ["the front matter has no closing `---`"]
    else:
        block, body = text[:end + 5], text[end + 5:]
    for n, line in enumerate(block.splitlines()[1:-1], 2):
        if line.strip() and ":" not in line:
            problems.append(f"line {n}: {line.strip()!r} is not a `key: value` line")
    pairs = entries.front_matter_pairs(block)
    keys = [k for k, _ in pairs]
    for key in dict.fromkeys(k for k in keys if keys.count(k) > 1):
        problems.append(f"key {key!r} is given more than once; keep one")
    return dict(pairs), pairs, body, problems


def _known_keys(fm: dict[str, str], allowed: tuple[str, ...], problems: list[str], slots: bool) -> None:
    for key in fm:
        if slots and key.startswith("slot.") and len(key) > 5:
            continue
        if not _KEY.match(key) or key not in allowed:
            problems.append(f"unknown key {key!r}")


def _bool(fm: dict[str, str], key: str, default: bool, problems: list[str]) -> bool:
    value = fm.get(key, "")
    if value == "":
        return default
    if value.lower() in ("true", "false"):
        return value.lower() == "true"
    problems.append(f"{key} must be true or false (got {value!r})")
    return default


def _list(value: str) -> tuple[str, ...]:
    return tuple(x.strip() for x in value.split(",") if x.strip())


def _session_body(body: str, problems: list[str]) -> tuple[str, list[Phase], str, str, str]:
    """(preamble, phases, summary, document title, document) of a session body."""
    preamble: list[str] = []
    phases: list[dict] = []
    summary: list[str] | None = None
    doc_title, document = "", None
    target: list[str] = preamble
    expect_type = False
    for line in body.splitlines():
        m = _PHASE.match(line)
        if m:
            phases.append({"number": int(m.group(1)) if m.group(1).isascii() else -1, "title": m.group(2),
                           "type": "ask", "blocks": [[None, []]]})
            target, expect_type = phases[-1]["blocks"][0][1], True
            continue
        h2 = _H2.match(line)
        if h2:
            expect_type = False
            name = h2.group(1)
            if name == "Summary":
                summary = []
                target = summary
            elif _DOCUMENT.match(name):
                doc_title, document = name, []
                target = document
            else:
                problems.append(f"unknown section `## {name}` (a session has `## Phase N: Title`, `## Summary` and "
                                "an optional `## Final Document`/`## Generated Document`)")
                target = []
            continue
        if expect_type and line.strip():
            expect_type = False
            t = _TYPE.match(line)
            if t:
                if t.group(1) not in PHASE_TYPES:
                    problems.append(f"phase {phases[-1]['number']}: type must be one of {', '.join(PHASE_TYPES)} "
                                    f"(got {t.group(1)!r})")
                else:
                    phases[-1]["type"] = t.group(1)
                continue
        h3 = _H3.match(line)
        if h3 and phases and target is not summary and target is not document:
            phases[-1]["blocks"].append([h3.group(1), []])
            target = phases[-1]["blocks"][-1][1]
            continue
        target.append(line)
    out: list[Phase] = []
    for n, raw in enumerate(phases, 1):
        if raw["number"] != n:
            problems.append(f"phase {raw['number']} is out of order: phases are numbered 1, 2, 3 … (expected {n})")
        blocks, questions, lead, close = [], [], "", ""
        for heading, lines in raw["blocks"]:
            text = "\n".join(lines).strip("\n")
            if heading is None and not text.strip():
                continue
            if heading == LEAD:
                lead = text.strip()
                continue
            if heading == CLOSE:
                close = text.strip()
                continue
            blocks.append((heading, text))
            for line in lines:
                q = _QLINE.match(line.strip())
                if q and _QUESTION_ID.match(q.group(1)):
                    questions.append(Question(q.group(1), q.group(2), raw["title"]))
        if not raw["title"]:
            problems.append(f"phase {n} has no title (`## Phase {n}: Title`)")
        out.append(Phase(n, raw["title"], raw["type"], tuple(blocks), tuple(questions), lead, close))
        if raw["type"] == "schedule" and any(len(i.text) > QUESTION_LIMIT for i in interrupts(out[-1])):
            problems.append(f"phase {n}: an interrupt question is longer than {QUESTION_LIMIT} characters")
        if raw["type"] == "schedule" and not interrupts(out[-1]):
            problems.append(f"phase {n} is a schedule phase but lists no interrupts "
                            "(lines like `- +2h: \"the question\"`)")
    if sum(1 for p in out if p.type == "schedule") > 1:  # (interrupt ids and a confirmation are the session's)
        problems.append("a session can have only one schedule phase")
    if not out:
        problems.append("no phases: the body needs at least one `## Phase 1: Title`")
    ids = [q.id for p in out for q in p.questions]
    for dup in sorted({i for i in ids if ids.count(i) > 1}):
        problems.append(f"question **{dup}** appears more than once")
    return ("\n".join(preamble).strip(), out, "\n".join(summary or []).strip(), doc_title,
            "\n".join(document or []).strip())


def _check_placeholders(session: Session, problems: list[str]) -> None:
    """Every ``{…}`` in the phases, summary and document names a question, ``{user}`` or a declared slot."""
    ids = {q.id for p in session.phases for q in p.questions}
    names = {n for n, _ in session.slots}
    for name, ref in session.slots:
        m = _REF.match(ref)
        if not m:
            problems.append(f"slot.{name}: {ref!r} must be a question id like 1.1, or s1:5.1 for another session")
        elif not m.group(1) and m.group(2) not in ids:
            problems.append(f"slot.{name}: no question {m.group(2)} in this session")
    for where, text, answers in ([(f"phase {p.number}", p.text(), False) for p in session.phases]
                                 + [("summary", session.summary, True), ("document", session.document, True)]):
        for ph in _PLACEHOLDER.findall(text):
            if ph in names or ph == "user" or (answers and ph in ids):
                continue
            hint = "a question id" if answers else "a declared slot (`slot.<name>: 1.1` in the front matter)"
            problems.append(f"{where}: {{{ph}}} is not {hint} or {{user}}")


def _session_from(fm: dict[str, str], body: str, path: Path | None, problems: list[str], *, number: int,
                  title: str) -> Session | None:
    preamble, phases, summary, doc_title, document = _session_body(body, problems)
    slots = tuple((k[5:].strip(), v.strip()) for k, v in fm.items() if k.startswith("slot."))
    s = Session(id=fm.get("id", ""), number=number, title=title, phases=tuple(phases), summary=summary,
                document_title=doc_title, document=document, preamble=preamble, minutes=fm.get("minutes", ""),
                framework=fm.get("framework", ""), role=fm.get("role", ""), mission=fm.get("mission", ""),
                quote=fm.get("quote", ""), slots=slots, path=path)
    _check_placeholders(s, problems)
    return s


def parse_session(text: str, path: Path | None = None) -> tuple[Session | None, list[str]]:
    """One session file: (session, []) or (None, problems)."""
    fm, _, body, problems = _split(text)
    if fm.get("kind") != SESSION:
        problems.append(f"kind must be {SESSION} (got {fm.get('kind', '')!r})")
    for key in ("id", "number", "title"):
        if not fm.get(key):
            problems.append(f"{key} is required")
    if len(fm.get("title", "")) > TITLE_LIMIT:
        problems.append(f"title is longer than {TITLE_LIMIT} characters")
    _known_keys(fm, SESSION_KEYS, problems, slots=True)
    number = fm.get("number", "")
    if number and not (number.isascii() and number.isdigit() and 0 < int(number) < 100):
        problems.append(f"number must be a whole number from 1 to 99 (got {number!r})")
        number = "0"
    s = _session_from(fm, body, path, problems, number=int(number or 0), title=fm.get("title", ""))
    return (None, problems) if problems else (s, [])


def _common(fm: dict[str, str], kind: str, problems: list[str]) -> dict:
    """The keys a program and a situational definition share, typed."""
    for key in ("id", "title"):
        if not fm.get(key):
            problems.append(f"{key} is required")
    if len(fm.get("title", "")) > TITLE_LIMIT:
        problems.append(f"title is longer than {TITLE_LIMIT} characters")
    pid = fm.get("id", "")
    if pid and not ID.match(pid):
        problems.append(f"id must be 2 to 31 lowercase letters, digits or hyphens, starting with a letter (got {pid!r})")
    advance = fm.get("advance") or ("settle" if kind == PROGRAM else "next")
    if advance not in ADVANCE:
        problems.append(f"advance must be one of {', '.join(ADVANCE)} (got {advance!r})")
        advance = "next"
    aliases = _list(fm.get("aliases", ""))
    for a in aliases:
        if not ID.match(a):
            problems.append(f"alias {a!r} is not a valid id")
    return {"id": pid, "kind": kind, "title": fm.get("title", ""), "emoji": fm.get("emoji", ""),
            "group": fm.get("group", ""), "advance": advance, "aliases": aliases,
            "enabled": _bool(fm, "enabled", True, problems)}


def parse_situational(text: str, path: Path | None = None, builtin: bool = False) -> tuple[Program | None, list[str]]:
    """A situational file: a one-session program whose phases are in the file."""
    fm, _, body, problems = _split(text)
    if fm.get("kind") != SITUATIONAL:
        problems.append(f"kind must be {SITUATIONAL} (got {fm.get('kind', '')!r})")
    _known_keys(fm, SITUATIONAL_KEYS, problems, slots=True)
    common = _common(fm, SITUATIONAL, problems)
    expires = None
    if fm.get("expires_hours"):
        try:
            expires = float(fm["expires_hours"]) if fm["expires_hours"].isascii() else -1.0
        except ValueError:
            expires = -1.0
        if not (math.isfinite(expires) and expires > 0):
            problems.append(f"expires_hours must be a positive number (got {fm['expires_hours']!r})")
            expires = None
    session = _session_from(fm, body, path, problems, number=1, title=common["title"])
    for name, ref in (session.slots if session else ()):
        if ref.startswith("s"):
            problems.append(f"slot.{name}: a situational definition has one session; use a question id like 1.1")
    annotate = _bool(fm, "annotate", True, problems)  # before the check: a bad value rejects the file
    if problems:
        return None, problems
    after = fm.get("after") or None
    return Program(sessions=(session,), expires_hours=expires, annotate=annotate,
                   after=after, builtin=builtin, path=path, **common), []


def parse_program(directory: Path, builtin: bool = False) -> tuple[Program | None, list[str]]:
    """``directory/program.md`` and the session files it lists under ``directory/sessions``."""
    path = Path(directory) / "program.md"
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return None, [f"cannot read {path}: {type(e).__name__}"]
    fm, _, body, problems = _split(text)
    if fm.get("kind") != PROGRAM:
        problems.append(f"kind must be {PROGRAM} (got {fm.get('kind', '')!r})")
    _known_keys(fm, PROGRAM_KEYS, problems, slots=False)
    common = _common(fm, PROGRAM, problems)
    insights = fm.get("insights") or "compile"
    if insights not in INSIGHTS:
        problems.append(f"insights must be one of {', '.join(INSIGHTS)} (got {insights!r})")
    names = _list(fm.get("sessions", ""))
    if not names:
        problems.append("sessions is required: the session files in order, like `01-anti-vision, 02-hidden-goals`")
    sessions: list[Session] = []
    for n, name in enumerate(names, 1):
        if not SESSION_FILE.match(name):  # (never a path: the file is read from the program's own sessions/ and sent)
            problems.append(f"session {n} ({name}): a session is a plain file name in sessions/ "
                            "(letters, digits, '.', '_' and '-'), not a path")
            continue
        spath = Path(directory) / "sessions" / f"{name}.md"
        try:
            stext = spath.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            problems.append(f"session {n} ({name}): cannot read {spath} ({type(e).__name__})")
            continue
        s, errs = parse_session(stext, spath)
        if s is None:
            problems += [f"session {n} ({name}): {e}" for e in errs]
            continue
        if s.number != n:
            problems.append(f"session {n} ({name}) says number: {s.number}; sessions are numbered by their place "
                            "in `sessions:`")
        sessions.append(s)
    for s in sessions:
        for name, ref in s.slots:
            m = _REF.match(ref)
            k = int(m.group(1)) if m and m.group(1) else None
            if k is not None and (k < 1 or k > len(sessions) or sessions[k - 1].phase_of(m.group(2)) is None):
                problems.append(f"session {s.number}: slot.{name}: no question {m.group(2)} in session {k}")
    if problems:
        return None, problems
    return Program(sessions=tuple(sessions), pace=fm.get("pace", ""), phases_of=fm.get("phases_of", ""),
                   debrief=fm.get("debrief") or None, insights=insights, intro=body.strip(),
                   sources=fm.get("sources", ""), builtin=builtin, path=path, **common), []


def shape(prog: Program) -> tuple:
    """What a run of ``prog`` is keyed by: its kind, and in order each session's id with its phases' numbers, types
    and question ids. Two definitions of one shape differ only in words (``library.run_conflict``)."""
    return (prog.kind, tuple((s.id, tuple((ph.number, ph.type, tuple(q.id for q in ph.questions)) for ph in s.phases))
                             for s in prog.sessions))


def kind_of_file(path: Path) -> str:
    """The ``kind:`` a definition file claims (practice files say ``practice``)."""
    return entries.front_matter_of(path).get("kind", "")


def ref_of(ref: str) -> tuple[int | None, str] | None:
    """``1.1`` → (None, "1.1"); ``s1:5.1`` → (1, "5.1"); None for anything else."""
    m = _REF.match(ref or "")
    return (int(m.group(1)) if m.group(1) else None, m.group(2)) if m else None


def placeholders(text: str) -> list[str]:
    return _PLACEHOLDER.findall(text or "")


PLACEHOLDER = _PLACEHOLDER
