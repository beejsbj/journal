"""Practice definitions as files: the built-in rituals and the user's own, parsed, resolved and rendered.

A practice is one Markdown file: flat ``key: value`` front matter (read by ``entries.front_matter``, so the
grammar is the one every entry already uses) and a body of ``## N. Title`` sections holding ``- `` questions,
or a flat list of questions. The four built-ins under ``library/practices/`` were generated once from the old
``questions.py`` constants (since removed) and are frozen by ``tests/test_golden.py``: what he receives did not
change when the definitions became files.

Resolution (``load``): built-ins first, then every ``*.md`` directly under ``cfg.practices_dir``. A user file with
a built-in's id replaces it whole (no merging of sections: a file is a complete definition, and every key it
leaves out takes the default below), ``enabled: false`` hides one, and a file that does not parse is reported
as a problem while the built-in of the same id keeps working, so an editing mistake never silences a prompt.
The library is re-read whenever a file under ``practices_dir`` changes (name, mtime or size), so an edit is
live on the next tick and the next message without a restart.

A built-in is edited in place (BUILTIN-EDIT): his version is that same-id file (a program: directory) in
``practices_dir``, never a change to the packaged files, so it keeps the built-in's id, ``/start`` word and slots.
Beside it, ``practices_dir/.library`` (the state file of the directory's lock) keeps the shipped text his edit began
from, which is how a later release's change to the default is noticed (``edit_status``) and shown (``default_diff``);
``reset`` removes his version and that record. A program edited under a run in progress keeps its structure
(``run_conflict``).

The engine is keyed by ``Practice`` (WP2): what is due (``scheduled``), what is sent, folded, validated and recorded,
and which practice closes the day or the week for a reflection (``closer``). When a practice is sent is its file's
``schedule.*`` lines and nothing else (the config's old ``schedule.<id>`` override is gone; ``migrate-config`` moves
one into forked files). ``resolve`` keeps callers written against the ritual names working, and ``for_prompt`` finds
the practice a ledger prompt was sent for, or stands in for one that is gone.

Question ids are positional (``3`` in a flat list, ``9.2`` as section.question) unless a line ends in a
``{#id}`` pin; entries record id and text together, so a reorder never corrupts history, and a pin keeps an
id with its question when the web app moves it. Positional numbering skips ids that are pinned elsewhere, so a
pin can never collide with a neighbour.

``parse`` is strict on structure (every key known and given once, every value of the right shape, every section
holding a question) and lenient on prose (a line that is neither a heading nor a question is ignored, unless it
starts like a list item: that is named, so a typo never shortens the bank). Numbers are ASCII digits only: a
regex digit class and ``str.isdigit`` also take "٦" and "²", which crash ``int`` or make a time no clock meets.
A fault in the parser itself costs the one file it was reading, never the library. ``render`` writes the
canonical form; ``parse(render(x)) == x`` for every definition, which is what lets the web app show "the file
the server will write" and lets ``fork`` copy a built-in without a second serialiser.
"""
from __future__ import annotations

import contextlib
import difflib
import hashlib
import math
import os
import re
import stat
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path

from . import entries, ledger, programdefs
from .config import Config, Names
from .programdefs import Phase, Program, Session, parse_program, parse_session, parse_situational  # noqa: F401

ID = re.compile(r"^[a-z][a-z0-9-]{1,30}$")
TIME = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
_PIN = re.compile(r"\s*\{#([A-Za-z0-9][A-Za-z0-9._-]*)\}$")
_HEADING = re.compile(r"^##\s+(?:[0-9]+\.\s+)?(.*?)\s*$")  # "## 2. Day Review"; "## 3.5 hours" keeps its number
_BULLET = re.compile(r"^[-*•]\s+(.*\S)\s*$")
# A line that starts like a list item but is not a question: indented, `+ `, `1. `/`1) `, or `-text`. (`*text`
# is left alone: a line may open with *emphasis*; `---` is a rule.)
_NEAR_BULLET = re.compile(r"^(?:\s+[-*•+]\s|\+\s|[0-9]+[.)]\s|[-•][^\s-])")
_SLOT_KEY = re.compile(r"^schedule\.([0-9]+)\.(time|weekday)$")
_SLOT_NUMBER = re.compile(r"0|[1-9][0-9]{0,2}")  # one spelling per slot: schedule.1 and schedule.01 would merge
_WHOLE = re.compile(r"[0-9]{1,9}")  # bounded: int() refuses a string of more than 4300 digits
_PLACEHOLDER = re.compile(r"\{([a-z_]+)(?::([^{}]*))?\}")
_KEY = re.compile(r"^[a-z][a-z0-9_.]*$")

BUILTIN_DIR = Path(__file__).resolve().parent / "library" / "practices"
BUILTIN_PROGRAMS = Path(__file__).resolve().parent / "library" / "programs"  # one directory per program
BUILTIN_SITUATIONAL = Path(__file__).resolve().parent / "library" / "situational"  # one file each
LOCK_NAME = ".library.lock"  # under practices_dir; every writer to that directory takes it
PRACTICE = "practice"
CLOSES = ("day", "week")
OPENER_MODES = ("companion", "plain")
OPENER_PLACEHOLDERS = ("greet", "title", "user", "emoji")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")

# Every key a practice file may carry, with the value used when it is absent. ``kind``, ``id`` and ``title``
# have no default: a file is a practice only when it states them. Schedule keys are matched by pattern.
DEFAULTS: dict[str, object] = {
    "emoji": "",                      # "" = the companion's emoji (today's behaviour)
    "collapse_hours": 6.0,
    "annotate": True,
    "closes": None,                   # "day" | "week": the practice whose answer ends the period a reflection covers
    "opener.mode": "companion",
    "opener.shape": "a short opener (max ~900 characters)",
    "opener.limit": 1100,
    "opener.plain": "{title}. A sentence or two is enough.",
    "companion": "",                  # one line of stance for the companion's skill; "" lists the title only
    "group": "",
    "enabled": True,
}
REQUIRED = ("kind", "id", "title")

# Ids a practice may not take: the journal's own commands and entry ids, the program commands WP4 adds, the
# companion's analyses, plus (per config) the opt-out command and Hermes' own command names, and (``_REPEATED``)
# the repeat names of the notes and analyses (``j-2``, ``reflection-2``).
_RESERVED = frozenset({"j", "journal", "note", "reflection", "week", "start", "next", "skip", "back", "pause",
                       "status", "break", "program", "insights", "morning-journal", "evening-journal"})


class ParseError(ValueError):
    """The text is not a usable practice; the message lists every problem found (``problems`` has them apart)."""

    def __init__(self, message: str, problems: list[str] | None = None):
        super().__init__(message)
        self.problems = list(problems) if problems is not None else [message]


class UnknownDefinition(KeyError):
    """No practice has this id."""


class AlreadyForked(FileExistsError):
    """``fork`` found a user file for this id already; any other filesystem error stays an ``OSError``."""


class ReadOnlyDict(dict):
    """A dict that refuses changes: a loaded Practice's opener, shared through the library cache by every caller.
    Reading, ``dict(...)``, ``==``, JSON, copy, pickle and ``dataclasses.asdict`` work as for any dict (a
    ``MappingProxyType`` would refuse the last four); like a dict it has no hash."""

    def _refuse(self, *args, **kwargs):
        raise TypeError("a practice's opener is read-only; copy it with dict(...)")

    __setitem__ = __delitem__ = __ior__ = clear = pop = popitem = setdefault = update = _refuse

    def __reduce__(self):  # copy and pickle rebuild it whole instead of filling it key by key
        return (type(self), (dict(self),))


@dataclass(frozen=True)
class Slot:
    time: str
    weekday: int | None = None  # 0 = Monday … 6 = Sunday; None = every day


@dataclass(frozen=True)
class Question:
    id: str
    text: str
    section: str | None
    pinned: bool = False


@dataclass(frozen=True)
class Practice:
    id: str
    kind: str
    title: str
    emoji: str
    schedule: tuple[Slot, ...]
    sections: tuple[tuple[str | None, tuple[Question, ...]], ...]
    collapse_hours: float
    annotate: bool
    closes: str | None
    opener: ReadOnlyDict = field(hash=False)  # read-only (see __post_init__); a dict has no hash, the rest does
    companion: str
    group: str
    enabled: bool
    builtin: bool
    path: Path | None = field(default=None, compare=False)
    raw: str = field(default="", compare=False, repr=False)

    def __post_init__(self):
        # A loaded Practice is shared by the library cache: one caller must not change the opener for every other.
        object.__setattr__(self, "opener", ReadOnlyDict(self.opener))

    @property
    def flat(self) -> bool:
        """A body with no ``## `` headings: ids are ``1..n`` and the bank is a plain numbered list."""
        return len(self.sections) == 1 and self.sections[0][0] is None

    @property
    def questions(self) -> list[Question]:
        return [q for _, qs in self.sections for q in qs]

    def items(self) -> dict[str, str]:
        """id → question, in order (what ``asked:`` ids are checked against)."""
        return {q.id: q.text for q in self.questions}

    def bank_lines(self) -> list[str]:
        """The bank as numbered plain-text lines, formatted as the original skills were."""
        if self.flat:
            return [f"{i}. {q.text}" for i, q in enumerate(self.questions, 1)]
        lines: list[str] = []
        for i, (title, qs) in enumerate(self.sections, 1):
            lines.append(f"{i}. {title or ''}")
            lines.extend(f"   • {q.text}" for q in qs)
        return lines

    def bank_text(self) -> str:
        return "\n".join(self.bank_lines())

    def bank_with_ids(self) -> str:
        """The bank with each question's id in brackets: what the opener request shows the companion."""
        if self.flat:
            return "\n".join(f"[{q.id}] {q.text}" for q in self.questions)
        out = []
        for i, (title, qs) in enumerate(self.sections, 1):
            out.append(f"{i}. {title or ''}")
            out.extend(f"   [{q.id}] {q.text}" for q in qs)
        return "\n".join(out)

    def plain_opener(self, names: Names) -> str:
        """``opener.plain`` filled in: the opener sent whenever the model's is missing or rejected.

        ``{greet:Word}`` is the greeting the rituals always had ("Word, Sam 📓", "Word 📓", "Word, Sam.",
        "Word."); ``{title}``, ``{user}`` and ``{emoji}`` are what they say. The emoji is the practice's own
        when it has one, else the companion's. Anything else in braces is left as written (``parse`` has
        already refused unknown placeholder names)."""
        emoji = self.emoji or names.emoji

        def fill(m: re.Match) -> str:
            name, word = m.group(1), (m.group(2) or "").strip()
            if name == "greet" and word:  # a bare {greet} is refused by parse; left as written if built by hand
                who = f", {names.user}" if names.user else ""
                return f"{word}{who} {emoji}" if emoji else f"{word}{who}."
            return {"title": self.title, "user": names.user, "emoji": emoji}.get(name, m.group(0))
        return _PLACEHOLDER.sub(fill, self.opener["plain"])


# ── parsing ──────────────────────────────────────────────────────────────────

def _split(text: str) -> tuple[str | None, str]:
    """(front-matter block including fences, body) or (None, text) when the file has no front matter.

    A leading byte-order mark is dropped (some editors write one) and a closing fence on the last line gets
    the newline ``entries.front_matter`` needs, so neither turns a correct header into "kind is required"."""
    text = text.replace("\r\n", "\n").removeprefix("﻿")
    if not text.startswith("---\n"):
        return None, text
    end = text.find("\n---\n", 3)
    if end < 0:
        return (text + "\n", "") if text.endswith("\n---") else (None, text)
    return text[:end + 5], text[end + 5:]


MAX_COLLAPSE_HOURS = 8760.0   # a year: a window a ``timedelta`` cannot hold stops every capture and fold, not one practice


def _coerce(key: str, value: str, problems: list[str]):
    """The typed value for ``key`` (None when the value is unusable, with the reason recorded)."""
    if key in ("annotate", "enabled"):
        low = value.lower()
        if low in ("true", "false"):
            return low == "true"
        problems.append(f"{key} must be true or false (got {value!r})")
    elif key == "collapse_hours":
        try:
            n = float(value) if value.isascii() else -1.0
        except ValueError:
            n = -1.0
        if math.isfinite(n) and 0 < n <= MAX_COLLAPSE_HOURS:  # inf would reach the web app as `Infinity`, which JSON.parse refuses
            return n
        problems.append(f"{key} must be a positive number of at most {MAX_COLLAPSE_HOURS:g} hours (got {value!r})")
    elif key == "opener.limit":
        if _WHOLE.fullmatch(value) and int(value) > 0:
            return int(value)
        problems.append(f"{key} must be a positive whole number (got {value!r})")
    elif key == "closes":
        if value in CLOSES:
            return value
        problems.append(f"closes must be one of {', '.join(CLOSES)} (got {value!r})")
    elif key == "opener.mode":
        if value in OPENER_MODES:
            return value
        problems.append(f"opener.mode must be one of {', '.join(OPENER_MODES)} (got {value!r})")
    elif key == "opener.plain":
        found = _PLACEHOLDER.findall(value)
        bad = [name for name, _ in found if name not in OPENER_PLACEHOLDERS]
        if bad:
            problems.append(f"opener.plain uses unknown placeholder(s) {{{'}, {'.join(bad)}}}; "
                            f"known: {', '.join('{' + p + '}' for p in OPENER_PLACEHOLDERS)}")
        if any(name == "greet" and not word.strip() for name, word in found):
            problems.append("opener.plain: {greet} needs a word, like {greet:Evening}")
        return value
    else:
        return value
    return None


def _slots(fm: dict[str, str], problems: list[str]) -> tuple[Slot, ...]:
    """``schedule.time``/``schedule.weekday`` as one slot, or ``schedule.N.time``/``.weekday`` as several."""
    single = {k.split(".")[1]: v for k, v in fm.items() if k in ("schedule.time", "schedule.weekday")}
    numbered: dict[int, dict[str, str]] = {}
    for k, v in fm.items():
        m = _SLOT_KEY.match(k)
        if not m:
            continue
        num = m.group(1)
        if not _SLOT_NUMBER.fullmatch(num):
            msg = (f"schedule.{num if len(num) <= 8 else num[:8] + '…'}: a slot number has no leading zero and at "
                   "most 3 digits (like schedule.2.time)")
            if msg not in problems:
                problems.append(msg)
            continue
        numbered.setdefault(int(num), {})[m.group(2)] = v
    if single and numbered:
        problems.append("use either schedule.time (one slot) or schedule.N.time (several), not both")
        return ()
    specs = [(("schedule",), single)] if single else [((f"schedule.{n}",), numbered[n]) for n in sorted(numbered)]
    out = []
    for (prefix,), spec in specs:
        if not spec.get("time"):
            problems.append(f"{prefix}.time is required for a slot")
            continue
        if not TIME.match(spec["time"]):
            problems.append(f"{prefix}.time must be HH:MM (got {spec['time']!r})")
            continue
        wd: int | None = None
        if spec.get("weekday", "") != "":
            raw = spec["weekday"]
            if re.fullmatch(r"[0-6]", raw):
                wd = int(raw)
            else:
                problems.append(f"{prefix}.weekday must be 0 (Mon) to 6 (Sun) (got {raw!r})")
                continue
        out.append(Slot(spec["time"], wd))
    return tuple(out)


def _body(body: str, problems: list[str], first_line: int = 1) -> tuple[tuple[str | None, tuple[Question, ...]], ...]:
    """Sections and questions with their ids assigned: positional, skipping ids pinned anywhere in the file.
    ``first_line`` is the body's line number in the file, so a problem names the line he would open."""
    raw: list[tuple[str | None, list[tuple[str, str | None]]]] = []
    for n, line in enumerate(body.splitlines(), first_line):
        line = line.rstrip()
        if not line.strip():
            continue
        h = _HEADING.match(line)
        if h:
            raw.append((h.group(1), []))
            continue
        b = _BULLET.match(line)
        if not b:
            if _NEAR_BULLET.match(line):  # a question with a typo would be dropped from the bank without a word
                problems.append(f"line {n}: {line!r} looks like a question but is not one: "
                                "write it as `- question` at the line's start")
            continue  # other prose between questions is allowed and ignored
        text, pin = b.group(1), None
        m = _PIN.search(text)
        if m:
            text, pin = text[:m.start()].rstrip(), m.group(1)
            if not text:
                problems.append(f"line {n}: question {{#{pin}}} has a pin but no text")
                continue
        if not raw:
            raw.append((None, []))
        raw[-1][1].append((text, pin))
    flat = len(raw) == 1 and raw[0][0] is None
    if not flat and raw and raw[0][0] is None:
        # A sectioned bank has no untitled section: it would be sent as "1. " and read back titled "1.".
        n = len(raw[0][1])
        problems.append(f"{n} question{'s' if n != 1 else ''} above the first `## ` heading: move "
                        f"{'them' if n != 1 else 'it'} under a heading, or remove the headings for a flat list")
    for title, qs in raw:
        if title is not None and not qs:  # it would be sent as a numbered heading with nothing under it
            problems.append(f"section {title!r} has no questions: add `- ` lines under it or remove the heading")
    pins = [pin for _, qs in raw for _, pin in qs if pin]
    for dup in sorted({p for p in pins if pins.count(p) > 1}):
        problems.append(f"question id {{#{dup}}} is pinned more than once")
    taken = set(pins)
    sections = []
    for i, (title, qs) in enumerate(raw, 1):
        n = 1
        out = []
        for text, pin in qs:
            if pin:
                out.append(Question(pin, text, title, pinned=True))
                continue
            while (qid := str(n) if flat else f"{i}.{n}") in taken:
                n += 1
            out.append(Question(qid, text, title))
            n += 1
        sections.append((title, tuple(out)))
    if not any(qs for _, qs in sections):
        problems.append("no questions: the body needs at least one `- ` line")
    return tuple(sections)


def _parse(text: str, path: Path | None, builtin: bool) -> tuple[Practice | None, list[str]]:
    problems: list[str] = []
    block, body = _split(text)
    if block is None:
        return None, ["no front matter: the file must start with a `---` block holding kind, id and title"]
    for n, line in enumerate(block.splitlines()[1:-1], 2):
        if line.strip() and ":" not in line:  # the header reader skips it: `enabled false` would be no setting at all
            problems.append(f"line {n}: {line.strip()!r} is not a `key: value` line")
    pairs = entries.front_matter_pairs(block)
    fm = dict(pairs)  # what entries.front_matter returns: the last value of a repeated key
    keys = [k for k, _ in pairs]
    for key in dict.fromkeys(k for k in keys if keys.count(k) > 1):
        problems.append(f"key {key!r} is given more than once; keep one")
    for key in REQUIRED:
        if not fm.get(key):
            problems.append(f"{key} is required")
    kind = fm.get("kind", "")
    if kind and kind != PRACTICE:
        problems.append(f"kind must be {PRACTICE} (got {kind!r}; a program is a directory with a program.md, a "
                        "situational definition says kind: situational)")
    pid = fm.get("id", "")
    if pid and not ID.match(pid):
        problems.append(f"id must be 2 to 31 lowercase letters, digits or hyphens, starting with a letter (got {pid!r})")
    for key in fm:
        if not _KEY.match(key):
            problems.append(f"key {key!r} is not a valid name (lowercase, digits, _ and .)")
        elif key not in REQUIRED and key not in DEFAULTS and not _SLOT_KEY.match(key) \
                and key not in ("schedule.time", "schedule.weekday"):
            problems.append(f"unknown key {key!r}")
    values: dict[str, object] = {}
    for key, default in DEFAULTS.items():
        value = fm.get(key, "")
        if value == "":
            values[key] = default  # absent or empty: the default (an empty emoji/companion/group is "" anyway)
            continue
        got = _coerce(key, value, problems)
        values[key] = default if got is None else got
    schedule = _slots(fm, problems)
    sections = _body(body, problems, first_line=block.count("\n") + 1)
    if problems:
        return None, problems
    practice = Practice(
        id=pid, kind=kind, title=fm["title"], emoji=str(values["emoji"]), schedule=schedule, sections=sections,
        collapse_hours=float(values["collapse_hours"]), annotate=bool(values["annotate"]),
        closes=values["closes"] if values["closes"] else None,
        opener={"mode": values["opener.mode"], "shape": values["opener.shape"], "limit": values["opener.limit"],
                "plain": values["opener.plain"]},
        companion=str(values["companion"]), group=str(values["group"]), enabled=bool(values["enabled"]),
        builtin=builtin, path=path, raw=text,
    )
    return practice, []


def _parse_guarded(text: str, path: Path | None, builtin: bool) -> tuple[Practice | None, list[str]]:
    """``_parse``, with anything it raises made the file's problem: a fault in the parser costs one file, never
    the library (and with it, once the tick reads the library, every prompt)."""
    try:
        return _parse(text, path, builtin)
    except Exception as e:  # noqa: BLE001 - reported as the file's problem; the built-in of its id is still used
        return None, [f"cannot parse ({type(e).__name__}: {e})"]


def parse(text: str, path: Path | None = None, builtin: bool = False) -> Practice:
    """The practice a file's text defines; ``ParseError`` names every problem when it cannot be one."""
    practice, problems = _parse_guarded(text, path, builtin)
    if practice is None:
        raise ParseError("; ".join(problems), problems)
    return practice


def _num(n: float) -> str:
    return str(int(n)) if float(n).is_integer() else str(n)


def _quote(value: str) -> str:
    """A value as front matter must hold it: ``entries.front_matter`` strips the spaces around a value and then one
    layer of matching quotes, so a value with spaces at either end, or one that starts and ends with the same
    quote, gets a layer of quotes and reads back unchanged."""
    if value != value.strip() or (len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'"):
        return f'"{value}"'
    return value


def render(p: Practice) -> str:
    """The canonical file for a practice: the keys a fork is likely to change written out even at their
    defaults (emoji, collapse, annotate, the opener, companion), ``closes``/``group``/``enabled`` only when
    set, slots numbered only when there are several, sections numbered by position, pins kept."""
    q = _quote
    lines = ["---", f"kind: {p.kind}", f"id: {p.id}", f"title: {q(p.title)}", f"emoji: {q(p.emoji)}"]
    if len(p.schedule) == 1:
        s = p.schedule[0]
        lines.append(f"schedule.time: {s.time}")
        if s.weekday is not None:
            lines.append(f"schedule.weekday: {s.weekday}")
    else:
        for n, s in enumerate(p.schedule, 1):
            lines.append(f"schedule.{n}.time: {s.time}")
            if s.weekday is not None:
                lines.append(f"schedule.{n}.weekday: {s.weekday}")
    lines.append(f"collapse_hours: {_num(p.collapse_hours)}")
    lines.append(f"annotate: {'true' if p.annotate else 'false'}")
    if p.closes:
        lines.append(f"closes: {p.closes}")
    lines += [f"opener.mode: {p.opener['mode']}", f"opener.shape: {q(p.opener['shape'])}",
              f"opener.limit: {p.opener['limit']}", f"opener.plain: {q(p.opener['plain'])}",
              f"companion: {q(p.companion)}"]
    if p.group:
        lines.append(f"group: {q(p.group)}")
    if not p.enabled:
        lines.append("enabled: false")
    lines = [line.rstrip() for line in lines] + ["---"]  # `emoji:` with nothing after it, not a trailing space

    def q_line(question: Question) -> str:
        pin = f" {{#{question.id}}}" if question.pinned else ""
        return f"- {question.text}{pin}"

    if p.flat:
        lines += [q_line(x) for x in p.questions]
    else:
        for i, (title, qs) in enumerate(p.sections, 1):
            if i > 1:
                lines.append("")
            lines.append(f"## {i}. {title or ''}".rstrip())
            lines += [q_line(x) for x in qs]
    return "\n".join(lines) + "\n"


def _text_field(d: Mapping, key: str, problems: list[str], where: str = "", default: str = "") -> str:
    value = d.get(key, default)
    name = f"{where}{key}"
    if value is None:
        return default
    if not isinstance(value, str):
        problems.append(f"{name} must be text")
        return default
    if "\n" in value or "\r" in value:
        problems.append(f"{name} must be one line")
    return value


def from_dict(d: Mapping, path: Path | None = None) -> Practice:
    """A practice from the JSON ``Library.to_dict`` gives (the web editor's ``definition``): the same fields, keys
    left out take the defaults. Only shapes are checked here (text where text goes, one line where a line goes);
    ``ParseError`` names every problem. The meaning of the values is ``parse``'s: ``write_text`` renders this and
    parses it back, so what is saved is exactly what a file would have to say."""
    problems: list[str] = []
    if not isinstance(d, Mapping):
        raise ParseError("the definition must be an object")
    slots: list[Slot] = []
    raw_slots = d.get("schedule", [])
    if not isinstance(raw_slots, list):
        problems.append("schedule must be a list of {time, weekday}")
        raw_slots = []
    for i, s in enumerate(raw_slots, 1):
        if not isinstance(s, Mapping) or not isinstance(s.get("time"), str):
            problems.append(f"schedule {i}: needs a time like \"21:30\"")
            continue
        wd = s.get("weekday")
        if wd is not None and (isinstance(wd, bool) or not isinstance(wd, int)):
            problems.append(f"schedule {i}: weekday is a whole number 0 (Mon) to 6 (Sun), or null")
            continue
        slots.append(Slot(s["time"], wd))
    opener_in = d.get("opener") if isinstance(d.get("opener"), Mapping) else {}
    if d.get("opener") is not None and not isinstance(d.get("opener"), Mapping):
        problems.append("opener must be an object")
    opener = {"mode": DEFAULTS["opener.mode"], "shape": DEFAULTS["opener.shape"], "limit": DEFAULTS["opener.limit"],
              "plain": DEFAULTS["opener.plain"]}
    for key in opener:
        if key in opener_in and opener_in[key] is not None:
            opener[key] = opener_in[key]
    for key in ("mode", "shape", "plain"):
        if not isinstance(opener[key], str) or "\n" in opener[key]:
            problems.append(f"opener.{key} must be one line of text")
            opener[key] = DEFAULTS[f"opener.{key}"]
    if isinstance(opener["limit"], bool) or not isinstance(opener["limit"], int):
        problems.append("opener.limit must be a whole number")
        opener["limit"] = DEFAULTS["opener.limit"]
    hours = d.get("collapse_hours", DEFAULTS["collapse_hours"])
    if isinstance(hours, bool) or not isinstance(hours, (int, float)) or not math.isfinite(hours) \
            or not 0 < hours <= MAX_COLLAPSE_HOURS:
        problems.append(f"collapse_hours must be a positive number of at most {MAX_COLLAPSE_HOURS:g} hours")
        hours = DEFAULTS["collapse_hours"]
    for key in ("annotate", "enabled"):
        if not isinstance(d.get(key, DEFAULTS[key]), bool):
            problems.append(f"{key} must be true or false")
    closes = d.get("closes")
    if closes is not None and not isinstance(closes, str):
        problems.append("closes must be \"day\", \"week\" or null")
        closes = None
    sections: list[tuple[str | None, tuple[Question, ...]]] = []
    raw_sections = d.get("sections", [])
    if not isinstance(raw_sections, list):
        problems.append("sections must be a list")
        raw_sections = []
    for i, sec in enumerate(raw_sections, 1):
        if not isinstance(sec, Mapping) or not isinstance(sec.get("questions", []), list):
            problems.append(f"section {i}: needs a title and a list of questions")
            continue
        title = sec.get("title")
        if title is not None and (not isinstance(title, str) or "\n" in title):
            problems.append(f"section {i}: the title is one line of text")
            continue
        qs = []
        for j, q in enumerate(sec.get("questions", []), 1):
            if not isinstance(q, Mapping) or not isinstance(q.get("text"), str) or not q["text"].strip() \
                    or "\n" in q["text"]:
                problems.append(f"section {i}, question {j}: needs one line of text")
                continue
            pinned = q.get("pinned") is True
            qid = str(q.get("id") or "")
            if pinned and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", qid):
                problems.append(f"section {i}, question {j}: a pinned question needs an id of letters, digits, . _ -")
                continue
            qs.append(Question(qid, q["text"].strip(), title, pinned=pinned))
        sections.append((title, tuple(qs)))
    pid = _text_field(d, "id", problems)
    practice = Practice(
        id=pid, kind=_text_field(d, "kind", problems, default=PRACTICE) or PRACTICE,
        title=_text_field(d, "title", problems), emoji=_text_field(d, "emoji", problems, default=""),
        schedule=tuple(slots), sections=tuple(sections), collapse_hours=float(hours),
        annotate=d.get("annotate", True) is not False, closes=closes or None, opener=opener,
        companion=_text_field(d, "companion", problems), group=_text_field(d, "group", problems),
        enabled=d.get("enabled", True) is not False, builtin=False, path=path,
    )
    if problems:
        raise ParseError("; ".join(problems), problems)
    return practice


def write_text(p: Practice) -> tuple[str, Practice]:
    """(the canonical file, the practice it parses back to) for a practice built by ``from_dict``. ``ParseError``
    when the file would not be a valid practice, or when a question or title would not read back as written (a
    question ending in ``{#…}`` is a pin, not text)."""
    text = render(p)
    back = parse(text, p.path)
    if (back.title, [(t, [q.text for q in qs]) for t, qs in back.sections]) != \
            (p.title, [(t, [q.text for q in qs]) for t, qs in p.sections]):
        raise ParseError("a title, section or question would not read back as written; "
                         "avoid a trailing {#...} in a question and leading or trailing spaces")
    return text, back


# ── loading ──────────────────────────────────────────────────────────────────

@dataclass
class Library:
    """Every definition the engine knows, user overrides applied; ``problems`` is what doctor reports."""
    practices: dict[str, Practice]
    builtins: dict[str, Practice]
    practices_dir: Path
    problems: list[str] = field(default_factory=list)
    programs: dict[str, Program] = field(default_factory=dict)  # programs and situational definitions, by id

    def get(self, pid: str) -> Practice:
        try:
            return self.practices[pid]
        except KeyError:
            raise UnknownDefinition(pid) from None

    def enabled(self) -> list[Practice]:
        return [p for p in self.practices.values() if p.enabled]

    def program(self, word: str) -> Program:
        """A program or situational definition by id or alias; ``UnknownDefinition`` when none has it."""
        word = (word or "").lower()
        if word in self.programs:
            return self.programs[word]
        for prog in self.programs.values():
            if word in prog.aliases:
                return prog
        raise UnknownDefinition(word)

    def enabled_programs(self) -> list[Program]:
        return [p for p in self.programs.values() if p.enabled]

    def scheduled(self) -> list[Practice]:
        """Enabled practices that have a slot: what the tick sends (``library.scheduled(cfg)`` loads and asks)."""
        return [p for p in self.enabled() if p.schedule]

    def user_files(self) -> list[Path]:
        return [p.path for p in self.practices.values() if not p.builtin and p.path]

    def source(self, p: Practice) -> str:
        """``builtin``, ``fork`` (a user file in place of a built-in) or ``user`` (a practice of his own)."""
        if p.builtin:
            return "builtin"
        return "fork" if p.id in self.builtins else "user"

    def to_dict(self, p: Practice) -> dict:
        """A practice as JSON (``journal practices … --json``, and the shape ``GET /library`` serves)."""
        return {
            "id": p.id, "kind": p.kind, "title": p.title, "emoji": p.emoji, "source": self.source(p),
            "enabled": p.enabled, "schedule": [{"time": s.time, "weekday": s.weekday} for s in p.schedule],
            "closes": p.closes, "annotate": p.annotate, "collapse_hours": p.collapse_hours, "opener": dict(p.opener),
            "companion": p.companion, "group": p.group, "path": str(p.path) if p.path else None,
            "sections": [{"title": title, "questions": [{"id": q.id, "text": q.text, "pinned": q.pinned} for q in qs]}
                         for title, qs in p.sections],
        }


def describe_schedule(slots) -> str:
    """``daily 23:00``, ``Wed 12:00``, ``daily 08:00, Sun 20:30``; ``on demand`` when there is no slot."""
    if not slots:
        return "on demand"
    return ", ".join(f"{'daily' if s.weekday is None else WEEKDAYS[s.weekday]} {s.time}" for s in slots)


def _order(p: Practice):
    """Daily practices by time, then weekly ones by day, then the unscheduled by id: the shape of a week."""
    if not p.schedule:
        return (2, 0, "", p.id)
    first = p.schedule[0]
    return (0 if first.weekday is None else 1, first.weekday or 0, first.time, p.id)


def _read_dir(directory: Path, builtin: bool, problems: list[str]) -> dict[str, Practice]:
    """Every ``*.md`` in a directory as practices, by id; unparsable or duplicate files become problems."""
    out: dict[str, Practice] = {}
    try:
        files = sorted(f for f in directory.iterdir() if f.suffix == ".md" and not f.name.startswith("."))
    except OSError as e:
        if not isinstance(e, FileNotFoundError):
            problems.append(f"{directory}: cannot list: {type(e).__name__}")
        return out
    for f in files:
        try:  # stat itself, not Path.is_file(): that re-raises EACCES on 3.11-3.13 and swallows it on 3.14
            regular = stat.S_ISREG(f.stat().st_mode)
        except FileNotFoundError:
            if f.is_symlink():  # a definition linked in from elsewhere whose target moved: say so, never skip it unseen
                problems.append(f"{f}: a link to a file that does not exist")
            continue
        except OSError as e:
            problems.append(f"{f}: cannot read: {type(e).__name__}")
            continue
        if not regular:  # a directory that happens to end in .md
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            problems.append(f"{f}: cannot read: {type(e).__name__}")
            continue
        if entries.front_matter(text).get("kind") == programdefs.SITUATIONAL:
            continue  # a situational definition: ``_read_programs`` reads it
        practice, errs = _parse_guarded(text, f, builtin)
        if practice is None:
            problems.append(f"{f}: " + "; ".join(errs))
            continue
        if practice.id in out:
            problems.append(f"{f}: id {practice.id!r} is already defined by {out[practice.id].path}; that one is used")
            continue
        out[practice.id] = practice
    return out


def _read_programs(programs_dir: Path | None, situational_dir: Path, builtin: bool,
                   problems: list[str]) -> dict[str, Program]:
    """Programs (``<dir>/<name>/program.md`` with its sessions) and situational files (``kind: situational``) by
    id. A definition that does not parse is a problem and is left out; nothing here can stop the practices. In his
    directory a file or folder that cannot be listed, entered or read is skipped quietly: ``_read_dir`` (the
    practices' reader, which looks at the same directory) has already named it."""
    out: dict[str, Program] = {}
    found: list[tuple[Path, object]] = []

    def entries_of(directory: Path | None) -> list[Path]:
        if directory is None:
            return []
        try:
            return sorted(f for f in directory.iterdir() if not f.name.startswith("."))
        except OSError as e:
            if builtin and not isinstance(e, FileNotFoundError):
                problems.append(f"{directory}: cannot list: {type(e).__name__}")
            return []
    for d in entries_of(programs_dir):
        try:
            if (d / "program.md").is_file():
                found.append((d / "program.md", d))
        except OSError:
            continue
    for f in entries_of(situational_dir):
        try:
            if f.suffix == ".md" and f.is_file() and \
                    (builtin or programdefs.kind_of_file(f) == programdefs.SITUATIONAL):
                found.append((f, None))
        except OSError:
            continue
    for path, directory in found:
        try:
            if directory is not None:
                prog, errs = programdefs.parse_program(directory, builtin)
            else:
                prog, errs = programdefs.parse_situational(path.read_text(encoding="utf-8"), path, builtin)
        except Exception as e:  # noqa: BLE001 - a fault in the parser costs this definition only
            prog, errs = None, [f"cannot parse ({type(e).__name__}: {e})"]
        if prog is None:
            problems.append(f"{path}: " + "; ".join(errs))
            continue
        if prog.id in out:
            problems.append(f"{path}: id {prog.id!r} is already defined by {out[prog.id].path}; that one is used")
            continue
        out[prog.id] = prog
    return out


def _program_stamp(directory: Path) -> tuple:
    """What a program reload is keyed on: every ``program.md`` and session file one directory down."""
    out = []
    for f in sorted([*directory.glob("*/program.md"), *directory.glob("*/sessions/*.md")]):
        try:
            st = f.stat()
            out.append((str(f.relative_to(directory)), st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((str(f), None, None))
    return tuple(out)


def builtin_programs(problems: list[str] | None = None) -> dict[str, Program]:
    """The packaged programs and situational definitions, by id. Unlike the built-in practices a broken one is a
    problem, not an exception: a program that cannot load must never stop the rituals."""
    return _read_programs(BUILTIN_PROGRAMS, BUILTIN_SITUATIONAL, True, problems if problems is not None else [])


_builtins_cache: tuple[tuple, dict[str, Practice]] | None = None
_cache: dict[Path, tuple[tuple, Library]] = {}


def _stamp(directory: Path) -> tuple:
    """What a reload is keyed on: the name, mtime and size of every ``*.md`` in the directory. A file that cannot
    be stat'ed (a dangling link) is keyed by its name alone, so it never blanks the stamp and hides later edits."""
    try:
        files = sorted(f for f in directory.iterdir() if f.suffix == ".md" and not f.name.startswith("."))
    except OSError:
        return ()
    out = []
    for f in files:
        try:
            st = f.stat()
            out.append((f.name, st.st_mtime_ns, st.st_size))
        except OSError:
            out.append((f.name, None, None))
    return tuple(out)


def builtin_ids() -> tuple[str, ...]:
    """The ids of the packaged rituals, in library order (morning, evening, midweek, weekly): read from the files,
    once per process (the package does not change under a running one), so no second list can drift from them."""
    global _builtin_ids
    if _builtin_ids is None:
        _builtin_ids = tuple(builtins())
    return _builtin_ids


_builtin_ids: tuple[str, ...] | None = None


def builtins() -> dict[str, Practice]:
    """The packaged practices, by id. Loud when the package data is broken: that is a build fault, not a
    user's editing mistake."""
    global _builtins_cache
    stamp = _stamp(BUILTIN_DIR)
    if _builtins_cache and _builtins_cache[0] == stamp:
        return _builtins_cache[1]
    problems: list[str] = []
    out = _read_dir(BUILTIN_DIR, True, problems)
    if problems or not out:
        raise RuntimeError("built-in practices are unreadable: " + ("; ".join(problems) or f"nothing in {BUILTIN_DIR}"))
    ordered = {p.id: p for p in sorted(out.values(), key=_order)}
    _builtins_cache = (stamp, ordered)
    return ordered


def reserved_ids(cfg: Config | None = None) -> set[str]:
    """Ids a user practice may not take (see ``_RESERVED``); with a config, the configured opt-out command too.

    The same in every process: Hermes' names come from the dated table ``config.HERMES_COMMANDS`` alone, never
    its live registry (which only the gateway has loaded), and the opt-out name is the configured one whether or
    not it is valid. Otherwise the gateway and the cron tick could disagree on which practices exist."""
    from . import config as jconfig
    out = set(_RESERVED) | {c.replace("_", "-") for c in jconfig.HERMES_COMMANDS}
    if cfg is not None:
        prefix = cfg.conversations.get("opt_out_prefix") or "/x"
        if isinstance(prefix, str):
            out.add(prefix.lstrip("/").lower().replace("_", "-"))  # spelled as an id would be, like the table
    return out


# The ids the engine's own repeated entries take: the second /j note of a day is ``<date>-j-2``, a second companion
# note ``-note-2``, a hand-written reflection's neighbour ``-reflection-2``. A practice named like one would share its
# prompt ids or its file names (and an entry of ``week-2`` would read as a week review), so those are reserved too.
_REPEATED = re.compile(r"(?:j|note|reflection|week)(?:-s[0-9]{2}(?:-[0-9]+)?|-[0-9]+)")
_SLOT_ID = re.compile(r"(.+)-([1-9][0-9]*)")  # ``<id>-<n>``: what a later slot's prompt id ends in (schedule.prompt_id)


def _is_reserved(pid: str, reserved: set[str]) -> bool:
    return pid in reserved or bool(_REPEATED.fullmatch(pid))


def _slot_clash(pid: str, practices: Mapping[str, Practice]) -> str | None:
    """Why ``pid`` cannot be a practice beside ``practices``: it is ``<id>-<n>`` while ``<id>`` has n or more slots,
    so it is also the prompt id (and entry stem) of that practice's n-th slot, and whichever is sent first would
    suppress or overwrite the other. None when it is free."""
    m = _SLOT_ID.fullmatch(pid)
    owner = practices.get(m.group(1)) if m else None
    if owner is None or int(m.group(2)) > len(owner.schedule):
        return None
    return (f"id {pid!r} is the prompt id of slot {m.group(2)} of {owner.id!r}, which has {len(owner.schedule)} "
            "slots; rename it")


def _suffix_clash(ids: list[str]) -> list[str]:
    """Pairs like ``level`` and ``level-10``: by name alone an entry of the second reads as the tenth of the
    first (see DECISIONS.md, WP0), so a library should not hold both."""
    out = []
    for a in ids:
        for b in ids:
            if b != a and re.fullmatch(rf"{re.escape(a)}-(\d+|s\d{{2}})", b):
                out.append(f"ids {a!r} and {b!r} are both defined: an entry of {b!r} also reads as one of {a!r}; rename one")
    return out


def load(cfg: Config) -> Library:
    """Built-ins, then the user's files, resolved; cached until a file under ``practices_dir`` changes."""
    directory = Path(cfg.practices_dir)
    stamp = (_stamp(BUILTIN_DIR), _stamp(directory), tuple(sorted(reserved_ids(cfg))),
             _program_stamp(BUILTIN_PROGRAMS), _stamp(BUILTIN_SITUATIONAL), _program_stamp(directory))
    hit = _cache.get(directory)
    if hit and hit[0] == stamp:
        return hit[1]
    problems: list[str] = []
    base = builtins()
    user = _read_dir(directory, False, problems)
    reserved = reserved_ids(cfg)
    merged = dict(base)
    for pid, practice in user.items():
        if _is_reserved(pid, reserved):
            problems.append(f"{practice.path}: id {pid!r} is reserved (a command or an entry kind); the file is ignored")
            continue
        merged[pid] = practice
    for pid in [p for p in merged if p in user]:
        clash = _slot_clash(pid, merged)
        if clash:  # the suffixed one gives way; the practice it shadows keeps every slot
            problems.append(f"{merged[pid].path}: {clash} (the file is ignored)")
            del merged[pid]
    for i, msg in enumerate(problems):
        # A broken user file named after a built-in shadows nothing: say which definition will be sent.
        for pid in base:
            if pid not in user and msg.startswith(f"{directory / (pid + '.md')}:"):
                problems[i] = f"{msg} (built-in {pid!r} is used)"
    problems += _suffix_clash(sorted(merged))
    programs = _merge_programs(directory, merged, reserved, problems)
    lib = Library(practices={p.id: p for p in sorted(merged.values(), key=_order)}, builtins=base,
                  practices_dir=directory, problems=problems, programs=programs)
    _cache[directory] = (stamp, lib)
    return lib


def _merge_programs(directory: Path, practices: Mapping[str, Practice], reserved: set[str],
                    problems: list[str]) -> dict[str, Program]:
    """Built-in programs, then his (``practices_dir/<name>/program.md``, ``practices_dir/*.md`` with ``kind:
    situational``): a user definition with a built-in's id replaces it whole, as for practices. One id space for
    all three kinds (``/start <id>`` resolves them together): a program named like a practice, or an alias that is
    another definition's id, is a problem and is left out."""
    progs = builtin_programs(problems)
    user = _read_programs(directory, directory, False, problems) if directory.is_dir() else {}
    for pid, prog in user.items():
        if _is_reserved(pid, reserved):
            problems.append(f"{prog.path}: id {pid!r} is reserved (a command or an entry kind); the file is ignored")
            continue
        progs[pid] = prog
    out: dict[str, Program] = {}
    for pid, prog in progs.items():
        if pid in practices:
            problems.append(f"{prog.path}: id {pid!r} is also a practice; the program is ignored")
            continue
        m = _SLOT_ID.fullmatch(pid)
        if m and m.group(1) in practices:  # its entry ``<date>-<id>.md`` would read as that practice's n-th entry
            problems.append(f"{prog.path}: id {pid!r} reads as entry {m.group(2)} of the practice {m.group(1)!r}; "
                            "the program is ignored")
            continue
        taken = set(practices) | set(progs)
        bad = [a for a in prog.aliases if a in taken or _is_reserved(a, reserved)]
        if bad:
            problems.append(f"{prog.path}: alias {', '.join(bad)} is already an id or reserved; ignored")
            prog = replace(prog, aliases=tuple(a for a in prog.aliases if a not in bad))
        out[pid] = prog
    seen: dict[str, str] = {}
    for pid, prog in list(out.items()):
        for a in prog.aliases:
            if a in seen:
                problems.append(f"{prog.path}: alias {a!r} is also an alias of {seen[a]!r}; {seen[a]!r} keeps it")
                out[pid] = replace(out[pid], aliases=tuple(x for x in out[pid].aliases if x != a))
            else:
                seen[a] = pid
    return out


def get(cfg: Config, pid: str) -> Practice:
    return load(cfg).get(pid)


def problems(cfg: Config) -> list[str]:
    return load(cfg).problems


def resolve(practice: Practice | str, cfg: Config | None = None) -> Practice:
    """A ``Practice`` for a practice or an id, so callers written against the ritual names (``compose.header(
    "evening", …)``) keep working. With a config the id is looked up in that library (his files, his forks);
    without one, in the packaged built-ins. ``UnknownDefinition`` when nothing has the id."""
    if isinstance(practice, Practice):
        return practice
    if cfg is not None:
        return load(cfg).get(practice)
    try:
        return builtins()[practice]
    except KeyError:
        raise UnknownDefinition(practice) from None


def placeholder(pid: str, title: str | None = None, emoji: str = "") -> Practice:
    """A stand-in for a practice the library no longer has (renamed, deleted or broken since a prompt was sent):
    enough to fold that prompt's message and to title an entry for a late reply. No questions, no schedule;
    never sent (it is disabled)."""
    return Practice(id=pid, kind=PRACTICE, title=title or pid, emoji=emoji, schedule=(), sections=(),
                    collapse_hours=float(DEFAULTS["collapse_hours"]), annotate=True, closes=None,
                    opener={"mode": "plain", "shape": DEFAULTS["opener.shape"], "limit": DEFAULTS["opener.limit"],
                            "plain": DEFAULTS["opener.plain"]},
                    companion="", group="", enabled=False, builtin=False)


def for_prompt(cfg: Config, prompt: Mapping) -> Practice:
    """The practice a ledger prompt was sent for (by its ``kind``), as the library has it now; else the packaged
    built-in of that id; else a ``placeholder`` titled as the prompt recorded it. Never raises: the hook captures
    replies through this, and a capture must not fail because a definition did."""
    pid = str(prompt.get("kind") or "")
    try:
        return load(cfg).get(pid)
    except Exception:  # noqa: BLE001 - unknown id, or a library that cannot load at all
        pass
    try:
        return builtins()[pid]
    except Exception:  # noqa: BLE001
        return placeholder(pid, prompt.get("title"), str(prompt.get("emoji") or ""))


# ── the question set as it was sent ──────────────────────────────────────────

SNAPSHOT_MAX_CHARS = 20000  # a bank larger than this is not snapshotted (the record stays small; readers fall back)


def question_snapshot(practice: Practice) -> list[dict]:
    """The practice's question set as a send record keeps it: ``[{id, text, section}]`` in order (``section`` is the
    heading's title, None in a flat list). A practice's file can change after its prompt went out; this is what the
    prompt carried. Empty when there is nothing to keep or the set is over ``SNAPSHOT_MAX_CHARS``."""
    out = [{"id": q.id, "text": q.text, "section": title} for title, qs in practice.sections for q in qs]
    size = sum(len(q["id"]) + len(q["text"]) + len(q["section"] or "") for q in out)
    return out if size <= SNAPSHOT_MAX_CHARS else []


def snapshot_sections(record: Mapping | None) -> tuple[tuple[str | None, tuple[Question, ...]], ...] | None:
    """A send record's snapshot as ``Practice.sections`` shapes it, or None when the record has none (it went out
    before snapshots, or the set was too big) or what it holds is not a well-formed list: the reader then uses the
    library, as before. Never raises."""
    raw = (record or {}).get("questions")
    if not isinstance(raw, list) or not raw:
        return None
    sections: list[tuple[str | None, list[Question]]] = []
    for item in raw:
        if not isinstance(item, dict):
            return None
        qid, text, title = item.get("id"), item.get("text"), item.get("section")
        if not isinstance(qid, str) or not isinstance(text, str) or not (title is None or isinstance(title, str)):
            return None
        if not sections or sections[-1][0] != title:
            sections.append((title, []))
        sections[-1][1].append(Question(id=qid, text=text, section=title))
    return tuple((title, tuple(qs)) for title, qs in sections)


def scheduled(cfg: Config) -> list[Practice]:
    """Enabled practices with at least one slot, in library order: what the tick sends."""
    return load(cfg).scheduled()


# ── what closes a period ─────────────────────────────────────────────────────

def _latest(p: Practice) -> str:
    """The latest daily slot of a practice ("" when it has none): when its day ends."""
    return max((s.time for s in p.schedule if s.weekday is None), default="")


def closer(cfg: Config, period: str) -> Practice | None:
    """The enabled practice whose answer ends ``period`` (``closes: day`` / ``closes: week``) for a reflection,
    None when no practice closes it (the reflection then waits for its fallback time).
    Several closing the day: the one scheduled latest (the day is not over before it); several closing the week:
    the first in library order."""
    found = [p for p in load(cfg).enabled() if p.closes == period]
    if not found:
        return None
    if period == "day":
        return max(found, key=_latest)  # max keeps the first of equals: library order breaks a tie
    return found[0]


def program_words(cfg: Config) -> tuple[set[str], set[str]]:
    """(ids, aliases) of every program and situational definition, built-in or his, read from the definitions
    themselves rather than from ``load(cfg).programs``: a program that a practice of the same id shadows drops out
    of that map, which is exactly the case this exists to prevent. A definition of his that does not parse (a
    session file missing for a moment, a typo, even a ``kind`` misspelled) still holds its words, taken from its front
    matter and its folder or file name: mending it later must not find a practice in its place, with its run stranded."""
    directory = Path(cfg.practices_dir)
    found = [builtin_programs()]
    ids: set[str] = set()
    aliases: set[str] = set()
    with contextlib.suppress(OSError):
        if directory.is_dir():
            found.append(_read_programs(directory, directory, False, []))
            for f in sorted(directory.iterdir()):
                if f.name.startswith("."):
                    continue
                try:
                    if f.is_dir() and (f / "program.md").is_file():
                        head, name = entries.front_matter_of(f / "program.md"), f.name
                    elif f.suffix == ".md" and f.is_file():
                        head = entries.front_matter_of(f)
                        kind = head.get("kind", "")
                        if kind == PRACTICE or not (head.get("id") or kind in (programdefs.SITUATIONAL, "program")):
                            continue   # a practice, or a page of his own; what names an id, or says it is a definition, holds its words
                        name = f.stem if head.get("kind") == programdefs.SITUATIONAL else ""
                    else:
                        continue
                except OSError:
                    continue
                ids |= {w.lower() for w in (head.get("id"), name) if w}
                aliases |= {a.lower() for a in programdefs._list(head.get("aliases", ""))}
    ids |= {pid for progs in found for pid in progs}
    aliases |= {a for progs in found for prog in progs.values() for a in prog.aliases}
    return ids, aliases


def _program_clash(pid: str, cfg: Config) -> str | None:
    ids, aliases = program_words(cfg)
    if pid in ids:
        return f"id {pid!r} is a program or situational definition; a practice cannot share an id with one"
    try:
        known = pid in load(cfg).practices
    except Exception:  # noqa: BLE001 - the library row says why it cannot load
        known = False
    if pid in aliases and not known:   # a practice already holding the word keeps it (the alias is the one dropped)
        return f"id {pid!r} is the alias of a program or situational definition; choose another"
    return None


def check(practice: Practice, cfg: Config | None = None) -> list[str]:
    """Why a parsed practice cannot join the library: its id is reserved, it is a program's id (or a new practice's
    id is a program's alias: the program would drop out of the library and strand its runs), or it is another
    practice's later slot (cfg: the library it would join). [] when it can."""
    problems: list[str] = []
    if _is_reserved(practice.id, reserved_ids(cfg)):
        problems.append(f"id {practice.id!r} is reserved (a command or an entry kind)")
    elif cfg is not None and (taken := _program_clash(practice.id, cfg)):
        problems.append(taken)
    elif cfg is not None:
        try:  # the library it would join: a file named like another practice's later slot is ignored there
            others = {k: v for k, v in load(cfg).practices.items() if k != practice.id}
        except Exception:  # noqa: BLE001 - the library row says why it cannot load
            others = {}
        clash = _slot_clash(practice.id, others)
        if clash:
            problems.append(f"{clash}; the library ignores it")
    return problems


def validate_file(path: Path, cfg: Config | None = None) -> list[str]:
    """Why a file is not a usable practice; [] when it is. For ``journal practices validate FILE``."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        return [f"cannot read: {type(e).__name__}: {e}"]
    practice, problems = _parse_guarded(text, Path(path), False)
    if practice:
        problems += check(practice, cfg)
    return problems


# ── writing (fork) ───────────────────────────────────────────────────────────

def held(directory: Path):
    """The directory's write lock, ``<practices_dir>/.library.lock``: the ledger's own ``locked_state`` lock, so
    every writer to the directory (``fork`` here, serve's writes later) serialises on it the way the engine's state
    writers do. The state dict it yields is ``practices_dir/.library``: ``defaults`` holds, for each built-in he has
    edited, the shipped files his edit began from (``_remember``); it is written back only when a writer changed it.
    The leading dot keeps it out of the library, the event stream and a notes app's file list."""
    return ledger.locked_state(directory / LOCK_NAME.removesuffix(".lock"))


def _atomic_write(path: Path, text: str) -> None:
    """As ``ledger._write`` but for text he edits: 0644 (Obsidian and the web app read it), the temp file hidden (a
    leading dot, so ``load`` never reads a half-written definition)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def fork_window(cfg: Config, p: Practice) -> float:
    """The ``collapse_hours`` a fork of ``p`` is written with: ``windows.collapse_hours`` when it is usable (a file
    could hold it), else the file's own."""
    hours = cfg.collapse_hours
    ok = (isinstance(hours, (int, float)) and not isinstance(hours, bool) and math.isfinite(hours)
          and 0 < hours <= MAX_COLLAPSE_HOURS)
    return float(hours) if ok else p.collapse_hours


def window_note(cfg: Config, pid: str) -> str:
    """What to tell him when a fork's ``collapse_hours`` is not the built-in file's (see ``fork_window``), else ''."""
    base = builtins()[pid]
    hours = fork_window(cfg, base)
    if hours == base.collapse_hours:
        return ""
    return (f"; collapse_hours: {hours:g} (windows.collapse_hours, which governed the built-in), so it folds as "
            "before; the file's value decides from now on")


def fork(cfg: Config, pid: str, schedule: tuple[Slot, ...] | None = None) -> Path:
    """Copy a built-in into ``practices_dir`` so it can be edited by hand: a practice or a situational definition as
    ``<id>.md``, a program as the directory ``<id>/`` (``program.md`` and ``sessions/``). The copy replaces the
    built-in whole from the next load, and the shipped text it began from is remembered (``_remember``).
    ``schedule`` writes other slots into a practice's copy (``migrate-config``; ``()`` = on demand only).
    ``AlreadyForked`` when he already has a definition of that id (``reset`` to start again); any other filesystem
    trouble (the directory unwritable, or a file in its place) is the plain ``OSError``.

    A practice's copy states the window the built-in was folding on, ``windows.collapse_hours``: a built-in's prompt
    records none, a fork's records its file's, so copying the file's 6 would quietly change when an unedited fork
    folds and stops taking the next message."""
    ship = shipped(pid)
    if ship is None:
        raise UnknownDefinition(pid)
    if schedule is not None and ship.kind != PRACTICE:
        raise ValueError(f"{pid!r} is a {ship.kind}; only a practice has a schedule")
    directory = Path(cfg.practices_dir)
    dest = _edit_path(directory, pid, ship.kind)
    with held(directory) as state:
        if _his(directory, pid, ship.kind) is not None or os.path.lexists(dest):
            raise AlreadyForked(f"{pid!r} is already forked under {directory}")
        _prune(state, directory)
        if ship.kind == PRACTICE:
            base = builtins()[pid]
            source = replace(base, collapse_hours=fork_window(cfg, base),
                             **({} if schedule is None else {"schedule": tuple(schedule)}))
            _atomic_write(dest, render(source))
        elif ship.kind == programdefs.PROGRAM:
            _new_program_dir(directory, pid, ship.files)
        else:
            _atomic_write(dest, next(iter(ship.files.values())))
        _remember(state, ship)
    return dest


class Changed(RuntimeError):
    """The file under a definition is not the one the caller read (its mtime moved), or it vanished."""


def _own_path(cfg: Config, pid: str) -> Path:
    if not ID.fullmatch(pid):
        raise ValueError(f"{pid!r} is not a practice id")
    return Path(cfg.practices_dir) / f"{pid}.md"


def _check_mtime(path: Path, expect_mtime: float | None) -> None:
    try:
        seen = path.stat().st_mtime
    except OSError:
        raise Changed(f"{path.name} is gone") from None
    if expect_mtime is None or abs(seen - float(expect_mtime)) > 1e-6:
        raise Changed(f"{path.name} changed since it was read")


def write(cfg: Config, pid: str, text: str, expect_mtime: float | None) -> Path:
    """Save the user's file for ``pid`` (``text`` is canonical, from ``write_text``). Under the directory's lock:
    an existing user file (found by id, whatever its file name) is replaced only when its mtime is
    ``expect_mtime``, so an edit made in Obsidian since the caller read it is never overwritten (``Changed``); a
    new id is created as ``<id>.md`` (``Changed`` if ``expect_mtime`` says a file was there, ``FileExistsError``
    if a file of that name is, one that does not parse included). A new file under a built-in's id is his edit of
    it: the shipped text it began from is remembered. Returns the path."""
    own = _own_path(cfg, pid)
    directory = own.parent
    with held(directory) as state:
        _prune(state, directory)
        current = _read_dir(directory, False, []).get(pid)
        if current is not None and current.path is not None:
            path = current.path
            _check_mtime(path, expect_mtime)
            _atomic_write(path, text)
            return path
        path = own
        if os.path.lexists(path):
            raise FileExistsError(_occupied(path, pid, "practice"))
        if expect_mtime is not None:
            raise Changed(f"{path.name} is gone")
        _atomic_write(path, text)
        ship = shipped(pid)
        if ship is not None and ship.kind == PRACTICE:
            _remember(state, ship)
        else:
            _forget(state, pid)
    return path


def remove(cfg: Config, pid: str) -> Path | None:
    """Delete the user's file for ``pid`` (a built-in then applies again; a practice of his own is gone), found by
    id or, for a file that does not parse, by name. The path removed, or None when there was none."""
    own = _own_path(cfg, pid)
    with held(own.parent) as state:
        current = _read_dir(own.parent, False, []).get(pid)
        path = current.path if current is not None and current.path is not None else own
        _forget(state, pid)
        _prune(state, own.parent)
        if not os.path.lexists(path):
            return None
        path.unlink()
    return path


# ── his edits of the built-ins ───────────────────────────────────────────────
#
# A built-in is edited in place: his version is a definition of the same id in ``practices_dir`` (a practice's or a
# situational definition's ``<id>.md``, a program's ``<id>/``), which ``load`` puts in the built-in's place whole. The
# packaged files are never written. ``.library``'s ``defaults.<id>`` keeps the shipped files his edit began from, so
# a later release that changes the default is noticed and shown, and his version is kept until he resets it.

DEFAULTS_KEY = "defaults"


@dataclass(frozen=True)
class Shipped:
    """A packaged definition: its kind (``practice``, ``program``, ``situational``), where it is (a program's
    directory), and the text of its files by name (``evening.md``; ``program.md``, ``sessions/<name>.md``)."""
    id: str
    kind: str
    path: Path
    files: Mapping[str, str]

    @property
    def sha(self) -> str:
        return _fingerprint(self.files)


def _fingerprint(files: Mapping[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(files):
        h.update(name.encode("utf-8") + b"\0" + files[name].encode("utf-8") + b"\0")
    return h.hexdigest()


def _program_files(directory: Path) -> dict[str, str]:
    """A program directory's definition files by name: ``program.md`` and every ``sessions/*.md`` (not only those
    ``sessions:`` lists: one taken out of the list stays beside it, to put back)."""
    out = {"program.md": (directory / "program.md").read_text(encoding="utf-8")}
    with contextlib.suppress(FileNotFoundError, NotADirectoryError):
        for f in sorted((directory / "sessions").iterdir()):
            if f.suffix == ".md" and not f.name.startswith(".") and f.is_file():
                out[f"sessions/{f.name}"] = f.read_text(encoding="utf-8")
    return out


def shipped(pid: str) -> Shipped | None:
    """The packaged definition of ``pid``, whatever its kind; None when nothing ships under that id."""
    p = builtins().get(pid)
    if p is not None and p.path is not None:
        return Shipped(pid, PRACTICE, p.path, ReadOnlyDict({p.path.name: p.raw}))
    prog = builtin_programs().get(pid)
    if prog is None or prog.path is None:
        return None
    if prog.situational:
        return Shipped(pid, prog.kind, prog.path, ReadOnlyDict({prog.path.name: prog.path.read_text(encoding="utf-8")}))
    return Shipped(pid, prog.kind, prog.path.parent, ReadOnlyDict(_program_files(prog.path.parent)))


def _edit_path(directory: Path, pid: str, kind: str) -> Path:
    """Where an edit of a definition of ``kind`` is created: ``<id>/`` for a program, else ``<id>.md``."""
    if not ID.fullmatch(pid):
        raise ValueError(f"{pid!r} is not a definition id")
    return directory / pid if kind == programdefs.PROGRAM else directory / f"{pid}.md"


def _named_id(path: Path) -> str:
    """The id a definition file's front matter names ("" when none, or it cannot be read), a byte-order mark
    skipped as the parser skips it."""
    return (entries.front_matter(entries.read_head(path).lstrip("\ufeff")).get("id") or "").strip().lower()


def _claims(path: Path, pid: str, loaded: set[Path]) -> bool:
    """Whether a definition file that does not load as ``pid`` is still his broken edit of it: it loaded under no id
    (``loaded``: the files the library read as some definition) and its front matter names that id, or none. A file
    at ``<id>.md`` (or ``<id>/program.md``) that is another id's definition (a fork renamed by hand, say) is never a
    broken edit of this built-in: resetting this one must not delete it."""
    return path not in loaded and _named_id(path) in ("", pid)


def _occupied(path: Path, pid: str, what: str) -> str:
    """Why an edit of ``pid`` cannot be made at ``path``: another id's definition is there (rename it to free the
    name), or a file that does not load (mend or delete it)."""
    named = _named_id(path / "program.md" if path.is_dir() else path)
    if named and named != pid:
        return f"{path} holds the definition {named!r}; rename it to free the name {pid!r}"
    return f"{path} exists but is not a usable {what}; mend it or delete it first"


def _his(directory: Path, pid: str, kind: str) -> Path | None:
    """His definition of ``pid`` (a ``kind``) in ``directory``: the file, or a program's directory. Found by id, else
    by the name an edit gives it (``<id>.md``, ``<id>/program.md``) when it does not parse and names no other id;
    None when he has none."""
    found: Path | None = None
    practices = _read_dir(directory, False, [])
    progs = _read_programs(directory, directory, False, []) if directory.is_dir() else {}
    loaded = {d.path for d in (*practices.values(), *progs.values()) if d.path is not None}
    if kind == PRACTICE:
        mine = practices.get(pid)
        found = mine.path if mine is not None else None
    else:
        prog = progs.get(pid)
        if prog is not None and prog.path is not None and prog.kind == kind:
            found = prog.path.parent if kind == programdefs.PROGRAM else prog.path
    if found is not None:
        return found
    fallback = _edit_path(directory, pid, kind)
    if kind == programdefs.PROGRAM:
        head = fallback / "program.md"
        return fallback if os.path.lexists(head) and _claims(head, pid, loaded) else None
    if not os.path.lexists(fallback) or not _claims(fallback, pid, loaded):
        return None
    # ``<id>.md`` is a practice's or a situational definition's: the file says which
    situational = programdefs.kind_of_file(fallback) == programdefs.SITUATIONAL
    return fallback if situational == (kind == programdefs.SITUATIONAL) else None


def his_files(cfg: Config, pid: str, kind: str) -> dict[str, str] | None:
    """The text of his definition of ``pid`` by file name (as ``Shipped.files`` names them), None when he has none or
    it cannot be read."""
    path = _his(Path(cfg.practices_dir), pid, kind)
    if path is None:
        return None
    try:
        if kind == programdefs.PROGRAM:
            return _program_files(path)
        return {path.name: path.read_text(encoding="utf-8")}
    except (OSError, UnicodeDecodeError):
        return None


def his_path(cfg: Config, pid: str) -> Path | None:
    """Where his edit of the built-in ``pid`` is (a file, or a program's directory), None when he has not edited it
    (or nothing ships under ``pid``). A file of his that does not parse counts: the built-in is in force, but the
    file is there to mend or reset."""
    ship = shipped(pid)
    return _his(Path(cfg.practices_dir), pid, ship.kind) if ship is not None else None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _remember(state: dict, ship: Shipped) -> None:
    """Keep the shipped files an edit of ``ship`` begins from (the directory's lock is held)."""
    kept = state.get(DEFAULTS_KEY)
    if not isinstance(kept, dict):
        kept = state[DEFAULTS_KEY] = {}
    kept[ship.id] = {"kind": ship.kind, "sha": ship.sha, "files": dict(ship.files), "at": _now()}


def _prune(state: dict, directory: Path) -> None:
    """Drop the record of any edit that is gone (his file deleted by hand, say), so a later edit made by hand reads as
    "not known" instead of against the old one. Every writer runs this under the lock."""
    kept = state.get(DEFAULTS_KEY)
    if not isinstance(kept, dict):
        return
    for pid in list(kept):
        ship = shipped(pid) if isinstance(pid, str) and ID.fullmatch(pid) else None
        if ship is None or _his(directory, pid, ship.kind) is None:
            _forget(state, pid)


def _forget(state: dict, pid: str) -> None:
    kept = state.get(DEFAULTS_KEY)
    if isinstance(kept, dict) and pid in kept:
        del kept[pid]
        if not kept:
            del state[DEFAULTS_KEY]


def _base(cfg: Config, pid: str) -> dict | None:
    """The record of the default his edit of ``pid`` began from, when it is whole; read without the lock (the file is
    replaced atomically, so a reader sees one version or the other)."""
    kept = ledger.read_state(Path(cfg.practices_dir) / LOCK_NAME.removesuffix(".lock")).get(DEFAULTS_KEY)
    rec = kept.get(pid) if isinstance(kept, dict) else None
    if not isinstance(rec, dict) or not isinstance(rec.get("files"), dict) or not isinstance(rec.get("sha"), str):
        return None
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in rec["files"].items()):
        return None
    return rec


def edit_status(cfg: Config, pid: str, his_known: bool = False) -> dict | None:
    """For a built-in he has edited: ``{"default_changed", "edited_at"}``. ``default_changed`` is whether the shipped
    default differs from the one his edit began from (None when that is not known: an edit made before it was kept,
    or by hand, or a record that is damaged); ``edited_at`` is when the edit began (None likewise). None when nothing
    ships under ``pid`` or he has no version of it (``his_known``: the caller has seen his version in force, so the
    directory is not read again)."""
    ship = shipped(pid)
    if ship is None or (not his_known and _his(Path(cfg.practices_dir), pid, ship.kind) is None):
        return None
    rec = _base(cfg, pid)
    if rec is None:
        return {"default_changed": None, "edited_at": None}
    at = rec.get("at")
    return {"default_changed": rec["sha"] != ship.sha, "edited_at": at if isinstance(at, str) else None}


def _diff(a: Mapping[str, str], b: Mapping[str, str], before: str, after: str) -> str:
    out: list[str] = []
    for name in sorted(set(a) | set(b)):
        out += difflib.unified_diff(a.get(name, "").splitlines(), b.get(name, "").splitlines(),
                                    f"{before}/{name}", f"{after}/{name}", lineterm="")
    return "\n".join(out) + ("\n" if out else "")


def default_diff(cfg: Config, pid: str) -> dict:
    """What the shipped ``pid`` is now, against his edit: ``{"since", "yours"}``, unified diffs. ``since``: what changed
    in the default since his edit began (None when nothing did, or that is not known); ``yours``: the shipped default
    against his version ("" when they are the same, None when he has no version). ``UnknownDefinition`` when nothing
    ships under ``pid``."""
    ship = shipped(pid)
    if ship is None:
        raise UnknownDefinition(pid)
    rec = _base(cfg, pid)
    since = None
    if rec is not None and rec["sha"] != ship.sha:
        since = _diff(rec["files"], ship.files, "default when you edited", "default now")
    mine = his_files(cfg, pid, ship.kind)
    yours = None
    if mine is not None:
        if ship.kind != programdefs.PROGRAM:  # one file each: compare them whatever his is called
            mine = {next(iter(ship.files)): next(iter(mine.values()))}
        yours = _diff(ship.files, mine, "default", "yours")
    return {"since": since, "yours": yours}


def reset(cfg: Config, pid: str) -> Path | None:
    """Put the shipped ``pid`` back: remove his version of it (a practice's or situational definition's file; a
    program's ``program.md`` and its shipped or listed session files, then its folders once nothing else of his is in
    them) and the record of the default it began from. Returns what was removed, None when he had no version.
    ``UnknownDefinition`` when nothing ships under ``pid``; ``RunInProgress`` (nothing removed) when a run of a
    program would see its structure change (``run_conflict``)."""
    ship = shipped(pid)
    if ship is None:
        raise UnknownDefinition(pid)
    directory = Path(cfg.practices_dir)
    with held(directory) as state:
        path = _his(directory, pid, ship.kind)
        if path is not None and ship.kind != PRACTICE:
            _refuse_under_run(cfg, pid, builtin_programs()[pid])
        _forget(state, pid)
        _prune(state, directory)
        if path is None:
            return None
        if ship.kind == programdefs.PROGRAM:
            _remove_program(path, ship)
        else:
            path.unlink()
    return path


def _remove_program(directory: Path, ship: Shipped) -> None:
    """His edit of a shipped program: ``program.md`` and the session files that are the shipped program's or that his
    ``sessions:`` lists, then its folders when they are empty. Anything else he keeps there stays (notes, a session
    he is drafting and has not listed yet)."""
    sessions = directory / "sessions"
    names = {n.removeprefix("sessions/") for n in ship.files if n.startswith("sessions/")}
    names |= {f"{n}.md" for n in programdefs._list(entries.front_matter_of(directory / "program.md").get("sessions", ""))
              if programdefs.SESSION_FILE.match(n)}
    for name in sorted(names):
        f = sessions / name
        with contextlib.suppress(FileNotFoundError, NotADirectoryError, IsADirectoryError):
            if not f.is_dir():
                f.unlink()
    with contextlib.suppress(FileNotFoundError):
        (directory / "program.md").unlink()
    for d in (sessions, directory):
        with contextlib.suppress(OSError):
            d.rmdir()


def _new_program_dir(directory: Path, pid: str, files: Mapping[str, str]) -> Path:
    """A program's directory ``<id>/`` made whole in one step: written under a hidden name, then renamed, so the
    library never reads a half-copied program. ``FileExistsError`` when something is already there."""
    dest = directory / pid
    if os.path.lexists(dest):
        raise FileExistsError(_occupied(dest, pid, "program"))
    tmp = Path(tempfile.mkdtemp(dir=directory, prefix=f".{pid}."))
    try:
        (tmp / "sessions").mkdir()
        for name, text in files.items():
            f = tmp / _file_name(name)
            f.write_text(text, encoding="utf-8")
            os.chmod(f, 0o644)
        os.chmod(tmp / "sessions", 0o755)
        os.chmod(tmp, 0o755)
        os.rename(tmp, dest)
    except BaseException:
        for f in sorted(tmp.rglob("*"), reverse=True):
            with contextlib.suppress(OSError):
                f.rmdir() if f.is_dir() else f.unlink()
        with contextlib.suppress(OSError):
            tmp.rmdir()
        raise
    return dest


_SESSION_NAME = re.compile(r"sessions/([0-9A-Za-z][0-9A-Za-z._-]*)\.md")


def _file_name(name: str) -> str:
    """A program file's name as the API and ``Shipped.files`` give it: ``program.md`` or ``sessions/<name>.md``,
    never a path out of the program's directory."""
    if name == "program.md" or _SESSION_NAME.fullmatch(name):
        return name
    raise ValueError(f"{name!r} is not a file of a program (program.md, or sessions/<name>.md)")


# ── programs and situational definitions: one file at a time ─────────────────

@dataclass(frozen=True)
class Draft:
    """A program or situational definition with one file changed, as it would be saved: ``program`` is what it
    parses to (None when it does not), ``problems`` why it cannot be saved."""
    id: str
    kind: str
    name: str
    text: str
    files: Mapping[str, str]
    program: Program | None
    problems: tuple[str, ...]
    builtin: bool   # he has no version of it: saving makes his edit of the shipped one


def program_kind(cfg: Config, pid: str) -> str | None:
    """``program`` or ``situational`` when ``pid`` names one (his, or shipped), else None."""
    lib = load(cfg)
    if pid in lib.programs:
        return lib.programs[pid].kind
    ship = shipped(pid)
    if ship is not None and ship.kind != PRACTICE:
        return ship.kind
    directory = Path(cfg.practices_dir)
    for kind in (programdefs.PROGRAM, programdefs.SITUATIONAL):
        if _his(directory, pid, kind) is not None:
            return kind
    return None


def draft(cfg: Config, pid: str, name: str | None, text: str) -> Draft:
    """Program or situational ``pid`` with the file ``name`` (a program's ``program.md`` / ``sessions/<x>.md``; a
    situational definition's one file, ``None`` for it) replaced by ``text``: parsed as the whole definition it makes,
    from his version when he has one, else from the shipped one. ``UnknownDefinition`` when ``pid`` is neither;
    ``ValueError`` when ``name`` is not one of its files."""
    kind = program_kind(cfg, pid)
    if kind is None:
        raise UnknownDefinition(pid)
    lib = load(cfg)
    mine = his_files(cfg, pid, kind)
    ship = shipped(pid)
    if mine is None and (ship is None or ship.kind != kind):
        raise UnknownDefinition(pid)
    files = dict(mine if mine is not None else ship.files)
    if kind == programdefs.PROGRAM:
        name = _file_name(name or "program.md")
        if name not in files:
            raise ValueError(f"{pid} has no file {name}")
    else:
        only = next(iter(files))
        if name not in (None, "", only):
            raise ValueError(f"{pid} is one file, {only}")
        name = only
    files[name] = text
    problems: list[str] = []
    prog: Program | None = None
    try:
        if kind == programdefs.PROGRAM:
            with tempfile.TemporaryDirectory(prefix="journal-draft-") as tmp:
                for n, t in files.items():
                    f = Path(tmp) / n
                    f.parent.mkdir(exist_ok=True)
                    f.write_text(t, encoding="utf-8")
                prog, errs = programdefs.parse_program(Path(tmp), False)
                errs = [e.replace(f"{tmp}/", "") for e in errs]   # name his files, not the scratch copy
        else:
            prog, errs = programdefs.parse_situational(text, None, False)
    except Exception as e:  # noqa: BLE001 - a fault in the parser is a problem with this draft, not a crash
        prog, errs = None, [f"cannot parse ({type(e).__name__}: {e})"]
    problems += errs
    if prog is not None:
        if prog.id != pid:
            problems.append(f"the definition's id is {prog.id!r} but this is {pid!r}; an id does not change")
        if prog.kind != kind:
            problems.append(f"this is a {kind}; it cannot become a {prog.kind}")
        problems += _alias_problems(cfg, lib, prog)
    return Draft(pid, kind, name, text, ReadOnlyDict(files), prog if not problems else None, tuple(problems),
                 mine is None)


def _alias_problems(cfg: Config, lib: Library, prog: Program) -> list[str]:
    """An alias the draft adds that is another definition's id or alias, or a reserved word: the library would drop
    it with a problem, so the save names it instead."""
    current = lib.programs.get(prog.id)
    had = set(current.aliases) if current is not None else set()
    taken = set(lib.practices) | {p for p in lib.programs if p != prog.id}
    taken |= {a for p in lib.programs.values() if p.id != prog.id for a in p.aliases}
    reserved = reserved_ids(cfg)
    return [f"alias {a!r} is already an id or alias, or reserved; choose another"
            for a in prog.aliases if a not in had and (a in taken or _is_reserved(a, reserved))]


def file_mtime(cfg: Config, pid: str, kind: str, name: str | None) -> float | None:
    """The mtime of his file ``name`` of program or situational ``pid`` (what a save of it is checked against), None
    when he has no such file."""
    path = _his(Path(cfg.practices_dir), pid, kind)
    if path is None:
        return None
    target = path / _file_name(name or "program.md") if kind == programdefs.PROGRAM else path
    try:
        return target.stat().st_mtime
    except OSError:
        return None


def save_draft(cfg: Config, d: Draft, expect_mtime: float | None) -> Path:
    """Write ``d`` (it has no problems). Under the directory's lock: his file of that name is replaced only when its
    mtime is ``expect_mtime`` (``Changed`` otherwise: an edit in Obsidian is never overwritten); with no version of
    his, the shipped definition is copied with the change in it (a program's whole directory, made in one step) and
    the shipped text it began from is remembered (``expect_mtime`` must then be None, ``Changed`` otherwise;
    ``FileExistsError`` when something unusable is already where it would go). Under the same lock: ``Changed`` when
    another file of the program is not what the draft was checked against, ``RunInProgress`` when a run of it would
    see its structure change. Returns the file written."""
    if d.problems:
        raise ValueError("a draft with problems is not saved")
    directory = Path(cfg.practices_dir)
    with held(directory) as state:
        _prune(state, directory)
        path = _his(directory, d.id, d.kind)
        ship = shipped(d.id)
        now = his_files(cfg, d.id, d.kind) if path is not None else (dict(ship.files) if ship is not None else None)
        if now is not None and d.kind == programdefs.PROGRAM and \
                {k: v for k, v in now.items() if k != d.name} != {k: v for k, v in d.files.items() if k != d.name}:
            raise Changed("another file of the program changed since it was read")
        if d.program is not None:
            _refuse_under_run(cfg, d.id, d.program)
        if path is not None:
            target = path / d.name if d.kind == programdefs.PROGRAM else path
            if not os.path.lexists(target):
                raise Changed(f"{d.name} is gone")
            _check_mtime(target, expect_mtime)
            _atomic_write(target, d.text)
            return target
        if ship is None or ship.kind != d.kind:
            raise Changed(f"{d.id} is gone")
        if expect_mtime is not None:
            raise Changed(f"{d.name} is gone")
        if d.kind == programdefs.PROGRAM:
            target = _new_program_dir(directory, d.id, d.files) / d.name
        else:
            target = _edit_path(directory, d.id, d.kind)
            if os.path.lexists(target):
                raise FileExistsError(_occupied(target, d.id, "definition"))
            _atomic_write(target, d.text)
        _remember(state, ship)
    return target


# ── a program edited under a run in progress ─────────────────────────────────

RUN_STATUSES_IN_PROGRESS = ("active", "paused")


class RunInProgress(RuntimeError):
    """A save or a reset would change the structure of a program a run is in the middle of (``run_conflict``)."""

    def __init__(self, message: str, runs: list[str]):
        super().__init__(message)
        self.runs = runs


def runs_in_progress(cfg: Config, pid: str) -> list[dict]:
    """The runs of ``pid`` that are active or paused, read from ``state_dir/runs/<pid>/`` (PRACTICES.md §4.1)."""
    out = []
    for f in sorted((Path(cfg.state_dir) / "runs" / pid).glob("*.json")):
        run = ledger.read_state(f)
        if run.get("status") in RUN_STATUSES_IN_PROGRESS:
            out.append({**run, "id": run.get("id") or f.stem})
    return out


def _guarded_runs(cfg: Config, pid: str) -> list[dict]:
    """The runs of ``pid`` whose structure must hold: active or paused, and a completed one with session 10's
    interrupts still to come (they are keyed by session and phase, and sent from the definition)."""
    out = []
    for f in sorted((Path(cfg.state_dir) / "runs" / pid).glob("*.json")):
        run = ledger.read_state(f)
        scheduled = run.get("scheduled")
        pending = isinstance(scheduled, list) and any(
            isinstance(i, dict) and i.get("status", "pending") == "pending" for i in scheduled)
        if run.get("status") in RUN_STATUSES_IN_PROGRESS or (run.get("status") == "completed" and pending):
            out.append({**run, "id": run.get("id") or f.stem})
    return out


def run_conflict(cfg: Config, pid: str, new: Program) -> str | None:
    """Why ``new`` cannot take the place of program ``pid`` now, else None. A run in progress (active or paused, or
    completed with interrupts still to come) keeps its structure: its state and his entries are keyed by session and
    phase numbers, and his answers are matched to questions by their ids, so a save or a reset that adds, removes,
    reorders or retypes a session or a phase, or moves a question id, waits for the run to end. Words (questions,
    intros, transitions, summaries) may change: they apply from the next send, and what was sent is already in his
    entries. When no definition of ``pid`` loads (his own program, broken), the structure the run was keyed by is not
    known, so no save from here can be checked against it: it is refused until the run is abandoned, and he mends the
    files by hand meanwhile (the guard is at the writers)."""
    runs = _guarded_runs(cfg, pid)
    if not runs:
        return None
    current = load(cfg).programs.get(pid)
    if current is None:
        return (f"{pid} does not load, so the sessions and phases its run in progress was keyed by are not known: "
                "mend its files by hand, or abandon the run first.")
    if programdefs.shape(current) == programdefs.shape(new):
        return None
    run = runs[0]
    at = run.get("current") if isinstance(run.get("current"), dict) else {}
    if run.get("status") == "completed":
        return (f"{current.title} has a run whose interrupts are still to come: its sessions, phases and question "
                "numbers stay as they are until they are sent. Change only the wording, or wait.")
    where = f"session {at.get('session')}, phase {at.get('phase')}" if at.get("session") else "started"
    paused = " (on a break)" if run.get("status") == "paused" else ""
    return (f"{current.title} has a run in progress ({where}{paused}): its sessions, phases and question numbers "
            "stay as they are until it ends. Change only the wording, or finish or abandon the run first.")


def _refuse_under_run(cfg: Config, pid: str, new: Program) -> None:
    why = run_conflict(cfg, pid, new)
    if why:
        raise RunInProgress(why, [r["id"] for r in _guarded_runs(cfg, pid)])
