"""Edit or delete one block of his in an entry file, and nothing else in it.

His entries are plain Markdown he can already change in his notes app, so the web app is one more editor for him
(DECISIONS.md, 2026-10-20, amending Decision 4). The rule that binds the engine, the models and every automatic path
(none of them rewrites a block he wrote) is unchanged: this module runs only when he asks, from the web app, for a
block of his.

What it does is a splice. ``document.block_parts`` finds the block in the file text (the same parser the document
shows, so a key means the same block here and there) and gives the two spans that matter: the words, which an edit
replaces, and the whole unit (hidden key line, heading, body), which a delete removes. Everything outside the span is
carried over byte for byte: the other blocks, her callouts and notes, the annotations, the summary, the front matter.

Then it checks its own work. The new text is parsed again and must show exactly the blocks the old one did, the
edited one with the new words (a delete: the others, less that one), and the same annotations, notes, summary and
front matter. Words that would be read as something else once written (a heading shaped like the engine's, a line
that opens her section, a recording's embed) fail that check and are refused with nothing written, rather than
quietly splitting or swallowing blocks.

No lock is taken here and nothing is written: the caller holds the ledger lock and decides whether to write.
"""
from __future__ import annotations

import re

from .. import audio, entries
from . import document

# How the engine opens a conversation message whose words would read as Markdown at the start of a line
# (``conversations._BLOCK_START``; ``tests/test_serve_blocks.py`` pins that the two agree): its words then go under
# the time marker, not after it.
_BLOCK_START = re.compile(r"^(?:#|>|[-*+] |\d+[.)] |```|\||!\[\[)")


class Refused(Exception):
    """A change that is not made: an HTTP status, a slug and a line the person can read."""

    def __init__(self, status: int, slug: str, detail: str):
        super().__init__(detail)
        self.status, self.slug, self.detail = status, slug, detail


def clean(text: str) -> str:
    """His new words as they will be stored: no blank lines around them (the document trims the same)."""
    return text.strip("\n").rstrip()


def _find(body: str, key: str, was: str | None, at: str | None, stem: str | None):
    """(index, parts, block, cut) of the block of his called ``key``, if it is there as he last saw it."""
    parts = document.block_parts(body, stem)
    for i, (block, cut) in enumerate(parts):
        if block["key"] != key:
            continue
        if block["kind"] != "his" or cut is None:
            raise Refused(409, "not-his", "that is not one of your blocks: the companion's words are not edited here")
        if (was is not None and block["text"] != was) or (at is not None and block["at"] != at):
            raise Refused(409, "changed", "that block changed since you read it; reload the entry and try again")
        if audio.TRANSCRIBING in block["text"]:
            raise Refused(409, "transcribing", "this recording is still being transcribed; change it once the "
                                               "transcript is in")
        if cut.refuse:
            raise Refused(409, "not-editable", "this block has something in it the web app cannot place safely "
                                               "(a line of the engine's own, or words above its recording); change "
                                               "it in your notes app")
        return i, parts, block, cut
    raise Refused(404, "not-found", "no block of that name in this entry (it may have been removed; reload the entry)")


def _facts(body: str) -> tuple:
    """What an edit of one block must leave as it was around the blocks: the entry's front matter, the companion's
    annotations and notes, the program summary."""
    notes = entries.notes_span(body)
    return (entries.front_matter(body), entries.annotations(body), body[notes[0]:notes[1]] if notes else None,
            entries.summary_text(body))


def _same_entry(old: str, new: str, wanted: list[dict], stem: str | None) -> bool:
    return document.blocks_of(new, stem) == wanted and _facts(new) == _facts(old)


def dash_lead(text: str) -> str:
    """What goes between a conversation message's ``—`` and its words: a space, or a blank line when the words open
    like Markdown (the engine's own rule for a message it writes)."""
    return "\n\n" if _BLOCK_START.match(text) else " "


def edit(body: str, key: str, text: str, *, was: str | None = None, at: str | None = None,
         stem: str | None = None) -> str:
    """``body`` with the words of block ``key`` replaced by ``text`` (already normalised: ``clean``). ``was`` and
    ``at`` are the words and the time the person was looking at: a block that reads differently now is ``changed``.
    ``stem`` is the entry's (which pictures a photo block opens with are the engine's own)."""
    i, parts, block, cut = _find(body, key, was, at, stem)
    lead = dash_lead(text) if cut.dash else cut.lead
    new = body[:cut.text[0]] + lead + text + cut.tail + body[cut.text[1]:]
    wanted = [b for b, _c in parts]
    wanted[i] = {**block, "text": text}
    if not _same_entry(body, new, wanted, stem):
        raise Refused(422, "ambiguous-text", "written like that, these words would be read as something other than "
                                             "your words (a heading, a recording's embed or a line of the "
                                             "engine's), so nothing was saved; change it in your notes app")
    return new


def remove(body: str, key: str, *, was: str | None = None, at: str | None = None, stem: str | None = None) -> str:
    """``body`` without block ``key``: its hidden key line, heading and body. An answer to a program's phase or
    interrupt stays: the run counts the blocks it was sent, so it can be edited here but not removed."""
    i, parts, block, cut = _find(body, key, was, at, stem)
    if block.get("phase") is not None or "interrupt" in block:
        raise Refused(409, "program-answer", "an answer to a program's question can be edited here but not removed: "
                                             "the program counts it")
    new = body[:cut.unit[0]] + body[cut.unit[1]:]
    # (a block's key is its place among the blocks, so the ones after it are renumbered: compare without it)
    wanted = [{k: v for k, v in b.items() if k != "key"} for j, (b, _c) in enumerate(parts) if j != i]
    got = [{k: v for k, v in b.items() if k != "key"} for b in document.blocks_of(new, stem)]
    if got != wanted or _facts(new) != _facts(body):
        raise Refused(422, "ambiguous-text", "removing it would change more than this block, so nothing was removed; "
                                             "change it in your notes app")
    return new
