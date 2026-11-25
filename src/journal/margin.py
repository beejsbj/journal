"""Margin notes (issue #31): the companion's note on one block of his, with its thread and its state, kept as one
record in the entry file (DECISIONS.md, MARGIN-FORMAT). This module is the record's grammar: ``render`` writes the
note itself, ``notes`` reads every note of an entry back. ``entries`` owns where the margin is (the end of her
annotations section, under ``### Margin notes`` and its hidden line); ``document`` turns a note's thread into blocks.

A note as the file holds it::

    %% journal note:n4c1e9a07 at:2026-11-25T09:14:00+05:30 state:open on:block:b3f9a2c10 %%
    > [!note] Ada · 09:14 · on 09:12
    > Which part of the quiet stayed with you?

    %% journal his:web-9b21 %%
    **09:20** — The gull, mostly.

    %% journal her:n4c1e9a07-2 %%
    > [!note]- Ada · 09:21
    > The late ones always sound surprised.

* **The key line** is the engine's: the note's id, when it was written, its state (``open``, ``resolved``,
  ``dismissed``; ``closed:`` says when it stopped being open) and, last, what it is on: a block of his words
  (``block:<id>``, the key above the block's heading), a message of his in the conversation (``his:<id>``), or the
  entry as a whole (``entry``). A field this module does not know is kept and ignored, so a later field reads here.
* **Her note** is the callout under it, every line ``> ``, as her replies in the conversation are: open notes stand
  open (``[!note]``), a resolved or dismissed one is folded (``[!note]-``) and says so at the end of its title.
* **The thread** follows, in the conversation's own grammar: a message of his under ``%% journal his:<id> %%`` (and
  anything else not in one of her keyed callouts: what he types under a note in his notes app is his), her reply in
  a callout under ``%% journal her:<id> %%``. It runs to the next note's key line or the end of the margin.

Her words in a note or a thread are hers and never his: every reader of his words leaves out what follows a
``note:`` or ``her:`` key line in a callout (``entries.strip_callouts``). This module reads no chat, no model and no
state: the faces read it through ``document``.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime

from . import entries

STATES = ("open", "resolved", "dismissed")
_KEY = re.compile(r"^%% journal note:([A-Za-z0-9][A-Za-z0-9-]*)((?: [^%\n]*)?) %%[ \t]*$", re.M)
_TITLE = re.compile(r"^> \[!note\]([-+]?) (.*)$")


@dataclass(frozen=True)
class Note:
    """One margin note as its record says: ``thread`` is the text after her note, up to the next note (``document``
    reads it as blocks)."""
    id: str
    anchor: str              # "block:<id>", "his:<id>" or "entry"
    at: str                  # ISO time with its offset, when she wrote it
    state: str = "open"      # one of STATES
    closed: str | None = None
    text: str = ""           # her words, as written
    title: str = ""          # the callout's title, as written (who, when, what it is on)
    thread: str = ""
    extra: dict = field(default_factory=dict)   # fields of the key line this version does not know


def key_line(note: Note) -> str:
    """The note's hidden key line (``on:`` last: an anchor is the rest of the line)."""
    fields = [f"note:{note.id}", f"at:{note.at}", f"state:{note.state}"]
    if note.closed and note.state != "open":
        fields.append(f"closed:{note.closed}")
    fields += [f"{k}:{v}" for k, v in note.extra.items()]
    fields.append(f"on:{note.anchor}")
    return f"%% journal {' '.join(fields)} %%"


def render(note: Note, who: str, at_label: str, on_label: str | None = None) -> str:
    """The note as the file holds it: its key line, then her words in a callout titled ``<who> · <at_label>``, with
    `` · on <on_label>`` (the time of the block it answers) and, once it is not open, its state. Her words go in as
    ``entries`` writes all of hers: model headings demoted, no engine line."""
    if note.state not in STATES:
        raise ValueError(f"not a state of a margin note: {note.state!r}")
    if not re.fullmatch(r"(?:block|his):\S.*|entry", note.anchor):
        raise ValueError(f"not an anchor: {note.anchor!r}")
    title = " · ".join(x for x in (who, at_label, f"on {on_label}" if on_label else "",
                                  note.state if note.state != "open" else "") if x)
    fold = "" if note.state == "open" else "-"
    lines = [key_line(note), f"> [!note]{fold} {title}"]
    lines += [f"> {l}" if l.strip() else ">" for l in entries._hers(note.text).splitlines()]
    return "\n".join(lines) + "\n"


def new_id(body: str, at: datetime, anchor: str, text: str) -> str:
    """An id for a new note: ``n`` and eight hex digits of a hash of when, on what and what she said; ``-2`` … when
    the entry already has it."""
    base = "n" + hashlib.sha256(f"{at.isoformat()}\n{anchor}\n{text}".encode("utf-8")).hexdigest()[:8]
    taken = {m.group(1) for m in _KEY.finditer(body)}
    key, n = base, 2
    while key in taken:
        key, n = f"{base}-{n}", n + 1
    return key


def _fields(rest: str) -> tuple[dict, str]:
    """The key line's fields after the id, and its anchor (``on:``, the rest of the line)."""
    out: dict = {}
    rest = rest.strip()
    on = re.search(r"(?:^| )on:(.*)$", rest)
    anchor = on.group(1).strip() if on else ""
    if on:
        rest = rest[:on.start()]
    for token in rest.split():
        name, sep, value = token.partition(":")
        if sep and name and name not in out:
            out[name] = value
    return out, anchor


def _callout(text: str) -> tuple[str | None, list[str], int]:
    """Her note's callout at the start of ``text``: its title line, its lines without ``> ``, where it ends."""
    lines = text.split("\n")
    if not lines or not lines[0].startswith("> [!note]"):
        return None, [], 0
    n = 1
    while n < len(lines) and lines[n].startswith(">"):
        n += 1
    body = [l[2:] if l.startswith("> ") else l[1:] for l in lines[1:n]]
    return lines[0], body, min(len(text), sum(len(l) + 1 for l in lines[:n]))


def parse(text: str) -> list[Note]:
    """Every note in the margin's text (``entries.margin``), in the file's order. A key line with no callout of hers
    under it is no note (a hand edit took it apart); an unknown state reads as ``open``, so the note still shows."""
    keys = list(_KEY.finditer(text or ""))
    out = []
    for i, m in enumerate(keys):
        end = keys[i + 1].start() if i + 1 < len(keys) else len(text)
        seg = text[m.end():end].lstrip("\n")
        title, body, stop = _callout(seg)
        if title is None:
            continue
        fields, anchor = _fields(m.group(2))
        known = {"at", "state", "closed"}
        state = fields.get("state") if fields.get("state") in STATES else "open"
        t = _TITLE.match(title)
        out.append(Note(id=m.group(1), anchor=anchor or "entry", at=fields.get("at", ""), state=state,
                        closed=fields.get("closed") or None, text="\n".join(body).strip("\n").rstrip(),
                        title=t.group(2).strip() if t else "", thread=seg[stop:].strip("\n").rstrip(),
                        extra={k: v for k, v in fields.items() if k not in known}))
    return out


def notes(body: str) -> list[Note]:
    """The margin notes of an entry file's text, in order ([] without a margin)."""
    return parse(entries.margin(body))


def section(notes_md: list[str]) -> str:
    """The margin as a writer appends it to her annotations section: the heading, its hidden line, the notes."""
    return f"{entries.MARGIN_HEADING}\n\n{entries.MARGIN_MARK}\n\n" + "\n".join(n.rstrip() + "\n" for n in notes_md)
