"""The tick's margin notes (issue #31, MARGIN-NOTES): the annotation step grown up. When an entry's annotation is
due (``tick.annotate``: quiet for ``annotate_after_minutes`` after his newest words, paced, deferred while the
model is unreachable), the one request that asks for her analysis may also ask for a few margin notes, each on one
block of his words. The model only writes words; this module owns everything that must not fail:

* **What she is shown** (``annotation_input``, ``margin_digest``): the entry with each block that may take a note
  labelled ``[block <key>]`` (the engine's own key, ``entries.BLOCK_KEY``), and the margin she has written so far
  set apart from it, every note with its state, every reply of his labelled as his (DECISIONS.md, MARGIN-FORMAT,
  "his replies reach the annotation model unlabelled") and everything else there labelled as hers.
* **What she may answer** (``split``): one line per note, ``NOTE <key>: <her note>``, after the bullets. A line
  that starts ``NOTE`` and is not that shape is dropped, and never left in her analysis.
* **What is kept** (``choose``): a note on a block that exists in the entry, has its own key, is not very short
  (``MIN_WORDS``) and has no open or dismissed note already; at most one per block and ``MAX_NOTES`` per entry per
  pass; one line, within ``NOTE_LIMIT`` characters; not the agent's failure copy; every quote of his in it his,
  word for word (``validate.unfound`` against his words in this entry); and ``margin.render`` must write it so it
  reads back as hers. Anything else is dropped; silence is a fine answer.
* **How it is written** (``write``): her analysis and the notes in one write, under the caller's ledger lock, and
  only when the result reads back with his words, his blocks and the notes already there unchanged; otherwise her
  analysis alone (the floor: no note, never a broken file).

Notes go to the file only: nothing here sends (MARGIN-CHAT does, behind the annotation-reply switch). Log lines
and reasons say counts and reasons, never her words or his.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import document, entries, margin, validate
from .agent import is_failure_text
from .config import Config

MAX_NOTES = 3        # notes on one entry in one pass (the owner, 2026-11-25: sparing)
MIN_WORDS = 10       # a block with fewer words gets none
NOTE_LIMIT = 300     # characters in one note
NOTE_MIN = 8         # characters: shorter is no note
KEEPS = ("open", "dismissed")   # a block with a note in one of these states gets no new one

_NOTE_LINE = re.compile(r"NOTE(?=[\s:\[]|[A-Za-z0-9-]+:)")   # (a NOTE line, well formed or not)
_NOTE = re.compile(r"NOTE (?:\[block ([A-Za-z0-9][A-Za-z0-9-]*)\]|([A-Za-z0-9][A-Za-z0-9-]*)):[ \t]+(\S.*)")
_WORD = re.compile(r"\w+")


# ── what she is shown ────────────────────────────────────────────────────────

def eligible(body: str, stem: str | None = None) -> dict[str, dict]:
    """The blocks of his words section that may take a new note, by key: keyed (a block written before keys has no
    anchor, and a copied key names only the first block that has it), at least ``MIN_WORDS`` words, and no open or
    dismissed note on it already. A message of his in the conversation takes none here."""
    taken = {n.anchor for n in margin.notes(body) if n.state in KEEPS}
    out = {}
    for b in document.blocks_of(body, stem):
        anchor = b.get("anchor") or ""
        if b.get("kind") != "his" or not anchor.startswith("block:") or anchor in taken:
            continue
        if len(_WORD.findall(b.get("text") or "")) >= MIN_WORDS:
            out[anchor[len("block:"):]] = b
    return out


def annotation_input(body: str, keys) -> str:
    """The entry as the annotation request shows it: ``entries.without_companion_replies`` (her folded replies out,
    the engine's lines out), with the margin left out (``margin_digest`` shows it, labelled) and each block whose
    key is in ``keys`` labelled ``[block <key>]`` where its key line stands."""
    ann, analysis = entries.annotations_span(body), entries._analysis_span(body)
    if ann and analysis and analysis[1] < ann[1]:   # (the margin: from its heading to the end of her section)
        body = body[:analysis[1]] + body[ann[1]:]
    keys = set(keys)
    body = entries.BLOCK_KEY.sub(lambda m: f"[block {m.group(1)}]" if m.group(1) in keys else m.group(0), body)
    return entries.without_companion_replies(body)


def _indent(label: str, text: str) -> str:
    lines = (text or "").strip().splitlines() or [""]
    return f"  - {label}: {lines[0]}" + "".join(f"\n    {l}" for l in lines[1:])


def _callout_words(text: str) -> str:
    """Her words in a callout at the start of ``text`` (its title line left out), else ``text`` as it is."""
    title, lines, stop = margin._callout(text.lstrip("\n"))
    if title is None:
        return text.strip()
    rest = text.lstrip("\n")[stop:].strip()
    return "\n".join(lines).strip() + (f"\n{rest}" if rest else "")


def margin_digest(body: str, names) -> str:
    """Her margin so far, as the annotation request shows it: each note with what it is on and its state, her words
    as hers, and under it the thread, each reply of his labelled as his and everything else as hers (the margin
    fails closed, ``entries.his_margin``). "" when the entry has no notes."""
    out = []
    lead = entries.margin_parts(entries.margin(body))
    if lead and lead[0][0] is None:
        out.append(_indent(f"In the margin, not marked as {names.possessive}", lead[0][2]))
    for note in margin.notes(body):
        on = f"[block {note.anchor[len('block:'):]}]" if note.anchor.startswith("block:") else (
            "a message in the conversation" if note.anchor.startswith("his:") else "the entry as a whole")
        out.append(f"- Your note on {on} ({note.state}): " + " ".join(note.text.split()))
        for kind, line, text in entries.margin_parts(note.thread):
            if kind == "his":
                words = entries.strip_callouts(line + text)
                if words:
                    out.append(_indent(f"{names.possessive.capitalize()} reply ({names.possessive} own words)", words))
            elif text.strip():
                out.append(_indent("Your reply", _callout_words(text)))
    return "\n".join(out)


def request(cfg: Config, body: str, stem: str, reply: str | None, log: list[str], pid: str) -> tuple[str, dict]:
    """The annotation request for the entry ``body`` and the blocks it offers for notes. When the margin cannot be
    read (an entry hand-edited out of shape), the request is the analysis alone, as before notes: a note is never
    worth her analysis."""
    from . import prompts
    try:
        keys = eligible(body, stem)
        return prompts.annotation_request(cfg, annotation_input(body, keys), reply,
                                          margin=margin_digest(body, cfg.names), notes=bool(keys)), keys
    except Exception as e:  # noqa: BLE001
        log.append(f"{pid}: margin notes not offered ({type(e).__name__})")
        return prompts.annotation_request(cfg, entries.without_companion_replies(body), reply), {}


# ── what she may answer ──────────────────────────────────────────────────────

def split(text: str) -> tuple[str, list[tuple[str, str]], int]:
    """(her answer without its NOTE lines, the notes as (block key, her words) in order, how many NOTE lines were
    not the shape). Any line that starts ``NOTE`` (after a list marker) leaves her analysis, well formed or not."""
    keep, notes, bad = [], [], 0
    for line in (text or "").splitlines():
        stripped = line.strip().lstrip("-* ").strip()
        if not _NOTE_LINE.match(stripped):
            keep.append(line)
            continue
        m = _NOTE.fullmatch(stripped)
        if m:
            notes.append((m.group(1) or m.group(2), m.group(3).strip()))
        else:
            bad += 1
    return "\n".join(keep).strip(), notes, bad


def _clock(block: dict) -> str | None:
    """The block's time as its heading says it (``09:12``), for the note's title."""
    at = (block.get("at") or "").split(" ")[0]
    return at if re.fullmatch(r"\d{1,2}:\d{2}", at) else None


def choose(cfg: Config, body: str, proposed: list[tuple[str, str]], at: datetime,
           stem: str | None = None) -> tuple[list[str], Counter]:
    """The notes to write, as ``margin.render`` wrote them, and why the others were dropped (a reason → a count).
    ``at`` is when she wrote them (aware); ``body`` the entry as it is now."""
    dropped: Counter = Counter()
    blocks = eligible(body, stem)
    his = entries.his_words(body)
    keyed = {m.group(1) for m in entries.BLOCK_KEY.finditer(body)}
    seen: set[str] = set()
    out: list[str] = []
    text_so_far = body
    for key, words in proposed:
        words = " ".join(words.split())
        if key in seen:
            dropped["a second note on one block"] += 1
            continue
        seen.add(key)
        if key not in blocks:
            dropped["no such block" if key not in keyed else "a block that takes no note"] += 1
            continue
        if len(out) >= MAX_NOTES:
            dropped[f"over {MAX_NOTES} on the entry"] += 1
            continue
        if not (NOTE_MIN <= len(words) <= NOTE_LIMIT) or words.startswith(("{", "#", ">", "```", "%")) \
                or "%%" in words or is_failure_text(words):
            dropped["not a note"] += 1
            continue
        if validate.unfound(words, [his]):
            dropped["a quote not in his words"] += 1
            continue
        anchor = f"block:{key}"
        nid = margin.new_id(text_so_far, at, anchor, words)
        note = margin.Note(id=nid, anchor=anchor, at=at.isoformat(timespec="seconds"), text=words)
        try:
            md = margin.render(note, cfg.names.companion, at.strftime("%H:%M"), _clock(blocks[key]))
        except ValueError:
            dropped["would not read back"] += 1
            continue
        out.append(md)
        text_so_far += "\n" + md
    return out, dropped


# ── how it is written ────────────────────────────────────────────────────────

def _sound(before: str, after: str, added: list[str]) -> bool:
    """Whether ``after`` (``before`` with her analysis and ``added`` written) keeps everything else as it was: his
    words and his blocks unchanged, the notes already there unchanged and in order, and the new ones after them."""
    if entries.his_words(after) != entries.his_words(before) or entries.raw_entry(after) != entries.raw_entry(before):
        return False
    if document.blocks_of(after) != document.blocks_of(before):
        return False
    old, new = margin.notes(before), margin.notes(after)
    want = [margin.parse(md)[0] for md in added]
    return new[:len(old)] == old and [(n.id, n.anchor, n.text) for n in new[len(old):]] \
        == [(n.id, n.anchor, n.text) for n in want]


def write(cfg: Config, path: Path, analysis: str, proposed: list[tuple[str, str]], at: datetime) -> Counter:
    """Write her analysis and the notes she proposed that ``choose`` keeps, in one write, reading the entry as it is
    now (call under ``ledger.locked``: the hook and the conversation writer append under it). Returns what happened:
    ``written`` and each reason a note was dropped, as counts."""
    body = path.read_text(encoding="utf-8")
    try:
        notes, result = choose(cfg, body, proposed, at, path.stem) if proposed else ([], Counter())
        new = entries.with_annotations(body, analysis, cfg.names, notes)
        sound = not notes or _sound(body, new, notes)
    except Exception as e:  # noqa: BLE001  (a note is never worth her analysis: it is written alone)
        notes, result, sound = proposed, Counter({f"an error ({type(e).__name__})": len(proposed)}), False
    if not sound:
        if notes and not any(r.startswith("an error") for r in result):
            result["the entry would not read back"] += len(notes)
        notes, new = [], entries.with_annotations(body, analysis, cfg.names)
    entries.write_file(path, new)
    if notes:
        result["written"] = len(notes)
    return result


def describe(result: Counter) -> str:
    """A log line's part for what ``write`` did: counts and reasons, never words."""
    parts = [f"+{result['written']} margin note{'s' if result['written'] != 1 else ''}"] if result.get("written") else []
    dropped = sorted((r, n) for r, n in result.items() if r != "written")
    if dropped:
        parts.append("dropped " + ", ".join(f"{n} ({r})" for r, n in dropped))
    return "; ".join(parts)
