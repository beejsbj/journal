"""Journal entries: one Markdown file per entry, flat at the journal root.

Rituals land in ``<root>/YYYY-MM-DD-<kind>.md`` (morning, evening, midweek, weekly); each ``/j``
note is its own ``<root>/YYYY-MM-DD-j.md``, ``-j-2.md``, …. A file holds the prompt, the user's
verbatim words (later direct replies append), the conversation that followed (``## Conversation``: the
user's later messages, each companion reply folded under the message it answers), the companion's
annotations and the companion's own notes. Code, not the model, owns these files; the user's words are
never rewritten.

Headings carry names (``## Sam``, ``## Ada's annotations``) from the configuration, but parsing
accepts any name, so renaming the companion or user never orphans existing files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from .audio import TRANSCRIBING
from .audio import label as audio_label
from .config import Names

PENDING = "_Pending._"
DEFAULT_NAMES = Names()

_ANNOTATIONS = re.compile(r"^## [^\n]*['’]s [Aa]nnotations[ \t]*$", re.M)
_NOTES = re.compile(r"^## [^\n]*['’]s [Nn]otes[ \t]*$", re.M)
_H2 = re.compile(r"^## [^\n]*$", re.M)
_NO_WORDS = re.compile(r"^_Nothing (?:from .+ )?yet\._$")
CONVERSATION_HEADING = "## Conversation"
# The engine's heading carries a hidden Obsidian comment under it, so a "## Conversation" line in the user's own
# words is never taken for it. Each conversation block carries one too (``%% journal his:<id> %%``), its key.
CONVERSATION_MARK = "%% journal:conversation %%"
_CONVERSATION = re.compile(r"^## Conversation[ \t]*\n\n?" + re.escape(CONVERSATION_MARK) + r"[ \t]*$", re.M)
ENGINE_MARK = re.compile(r"^%% journal[ :][^%\n]* %%[ \t]*(?:\n|$)", re.M)
# A program session's ``## Summary`` (written by code when the session ends, before the annotations) carries its own
# hidden mark, like the conversation heading: a "## Summary" line in his words is never taken for it. It ends his
# words and the conversation, so a late reply still lands above it.
SUMMARY_HEADING = "## Summary"
SUMMARY_MARK = "%% journal:summary %%"
_SUMMARY = re.compile(r"^## Summary[ \t]*\n\n?" + re.escape(SUMMARY_MARK) + r"[ \t]*$", re.M)
_ANSWER_KEY = re.compile(r"^%% journal (phase|interrupt):([0-9]+) %%[ \t]*$", re.M)  # either kind of answer
# A file the engine starts without an annotations section (a note saved from conversation, a practice that is not
# annotated) carries this hidden line where that section would be: his words and the conversation end there, and the
# engine's own sections (annotations, notes) are looked for only after it, so a heading he typed in their shape ends
# nothing. A file with an annotations section needs no line: his words end at the last one (``_section_end``).
# The engine's line is the last one, with only blank lines and then the end of the file or her annotations or notes
# heading after it (``_words_end``): his words come before it and her text never carries it (``_hers``), so a copy he
# pastes moves nothing, unless it is the last line of his words in a file without the engine's line (then his next
# reply goes in above that hidden line: nothing visible changes); and a reply or a conversation older code appended
# after the line leaves the file read by the rule without it.
WORDS_END_MARK = "%% journal:end-of-words %%"
_WORDS_END = re.compile(r"^" + re.escape(WORDS_END_MARK) + r"[ \t]*$", re.M)
_ONLY_HERS_AFTER = re.compile(r"\s*(?:\Z|## [^\n]*['’]s (?:[Aa]nnotations|[Nn]otes)[ \t]*$)", re.M)


def _words_end(body: str, start: int = 0) -> re.Match | None:
    """The engine's end-of-words line after ``start`` (see ``WORDS_END_MARK``), or None."""
    found = None
    for m in _WORDS_END.finditer(body, start):
        if _ONLY_HERS_AFTER.match(body, m.end()):
            found = m
    return found


def _hers(text: str) -> str:
    """Her text (annotations, notes) as written: model headings demoted and no engine key line in it."""
    return ENGINE_MARK.sub("", demote_headings((text or "").strip())).strip()


def phase_mark(n: int) -> str:
    """The hidden key line above each block of his words that answers program phase ``n``."""
    return f"%% journal phase:{n} %%"


def interrupt_mark(n: int) -> str:
    """The hidden key line above each block of his words that answers session interrupt ``n`` (a one-off question
    the tick sent on its own, not a phase)."""
    return f"%% journal interrupt:{n} %%"


def block_mark(kind: str, key: str) -> str:
    """The hidden key line that opens a conversation block (``kind`` his or her)."""
    return f"%% journal {kind}:{key} %%"
# A companion reply (or a reply-context line) folded into the conversation: an Obsidian callout, every line "> ".
# The engine's callouts in a conversation: her reply (opened by its ``journal her:`` key line) and the reply
# context above one of his messages (right after his ``journal his:`` key line). A callout of his own is his text.
_CALLOUT = re.compile(r"^%% journal her:[^%\n]* %%\n> \[![a-z]+\][-+]? [^\n]*\n(?:>[^\n]*(?:\n|$))*", re.M)
_REPLY_CONTEXT = re.compile(r"^(%% journal his:[^%\n]* %%\n)> \[!info\][-+]? ↩[^\n]*\n(?:>[^\n]*(?:\n|$))*\n?", re.M)


def _practice(kind: str, practice=None):
    """The practice an entry answers: the one given (the caller's library has it), else the packaged built-in of
    that id, else None (a practice the engine no longer knows: its entry is titled by id, without a bank)."""
    if practice is not None:
        return practice
    from . import library  # library reads front matter from here
    try:
        return library.builtins()[kind]
    except KeyError:
        return None


def title_for(kind: str, names: Names = DEFAULT_NAMES, practice=None) -> str:
    if kind == "j":
        return "Journal note"
    if kind == "note":
        return f"{names.companion}'s note"
    p = _practice(kind, practice)
    return p.title if p is not None else kind


def stem_for(local_date: str, kind: str, n: int = 1) -> str:
    suffix = "" if n <= 1 else f"-{n}"
    return f"{local_date}-{kind}{suffix}"


def entry_path(root: Path, stem: str) -> Path:
    return root / f"{stem}.md"


def next_stem(root: Path, local_date: str, kind: str) -> str:
    """First unused stem for a kind that can repeat in a day (/j notes)."""
    n = 1
    while entry_path(root, stem_for(local_date, kind, n)).exists():
        n += 1
    return stem_for(local_date, kind, n)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def _prompt_md(prompt: dict, names: Names, practice=None) -> str:
    """The ``## Prompt`` section: when it was sent and by whom, the woven-in questions, the opener, and the bank
    folded under it, as numbered lines (a flat bank) or bold section headings with their questions."""
    kind = prompt["kind"]
    if kind == "j":
        if prompt.get("capture") == "auto":
            return "_Freeform — saved from the conversation._"
        return "_Freeform — sent with /j._"
    if kind == "note":
        return f"_Started by {names.companion}'s note._"
    sent = prompt.get("sent_at_local") or ""
    meta = f"_Sent {sent} · opener: {prompt.get('opener_source', '')}_\n\n" if sent else ""
    if prompt.get("asked"):
        meta += "_Woven in: " + "; ".join(f"[{a['id']}] {a['question']}" for a in prompt["asked"]) + "_\n\n"

    p = _practice(kind, practice)
    flat = p is not None and p.flat

    def fmt(line: str) -> str:
        if line.startswith("   "):
            return f"> - {line.strip(' •')}"
        return f"> {line}" if flat else f"> **{line}**"
    lines = "\n".join(fmt(l) for l in (p.bank_lines() if p is not None else []))
    opener = demote_headings((prompt.get("opener") or "").strip())
    bank = f"\n\n> [!question]- Questions\n{lines}" if lines else ""
    return f"{meta}{opener}{bank}".strip()


def _phase_callout(prompt: dict) -> str:
    """One phase as it was sent, folded under ``## Prompt`` (``> [!question]- Phase N: Title``), with a note when
    a reflect phase went out with its template because no model text could be used."""
    head = f"> [!question]- Phase {prompt['phase']}: {prompt.get('phase_title') or ''}".rstrip()
    lines = [f"> {l}" if l.strip() else ">" for l in (prompt.get("phase_text") or "").strip().splitlines()]
    if prompt.get("reflect"):
        lines += [">", f"> _reflect: {prompt['reflect']}_"]
    return "\n".join([head, *lines])


def _interrupt_callout(prompt: dict) -> str:
    """One interrupt as it was sent, folded under ``## Prompt`` (``> [!question]- Interrupt 3/8 (+6h) · 11:00``)."""
    head = f"> [!question]- {interrupt_label(prompt)}".rstrip()
    lines = [f"> {l}" if l.strip() else ">" for l in (prompt.get("interrupt_text") or "").strip().splitlines()]
    return "\n".join([head, *lines])


def interrupt_label(prompt: dict) -> str:
    """``Interrupt 3/8 (+6h) · 11:00``: how the entry names an interrupt (its callout and the insights document)."""
    of = f"/{prompt['interrupt_count']}" if prompt.get("interrupt_count") else ""
    offset = f" ({prompt['interrupt_offset']})" if prompt.get("interrupt_offset") else ""
    at = f" · {prompt['interrupt_at']}" if prompt.get("interrupt_at") else ""
    return f"Interrupt {prompt['interrupt']}{of}{offset}{at}"


def _program_prompt_md(prompt: dict) -> str:
    """A session entry's ``## Prompt``: which session, then each phase as it is answered (``add_phase_prompt``)."""
    if prompt.get("session_count"):
        what = f"_Session {prompt['session']} of {prompt['session_count']} · {prompt.get('session_title') or ''}"
    else:
        what = f"_{prompt.get('session_title') or prompt.get('title') or prompt['kind']}"
    framework = f" · {prompt['framework']}" if prompt.get("framework") else ""
    return f"{what}{framework}_\n\n{_interrupt_callout(prompt) if prompt.get('interrupt') else _phase_callout(prompt)}"


def add_phase_prompt(path: Path, prompt: dict) -> bool:
    """Fold a phase (or an interrupt) into the session entry's ``## Prompt`` the first time it is answered (a
    re-sent phase is not folded twice). Returns True when the file changed."""
    body = path.read_text(encoding="utf-8")
    callout = _interrupt_callout(prompt) if prompt.get("interrupt") else _phase_callout(prompt)
    head = callout.split("\n", 1)[0]
    words = _words_heading(body)
    if words is None or any(l == head for l in body[:words.start()].splitlines()):
        return False
    at = words.start()
    body = body[:at].rstrip() + "\n\n" + callout + "\n\n" + body[at:]
    _atomic_write(path, body)
    return True


def _new_file(prompt: dict, names: Names, practice=None) -> str:
    if prompt.get("program"):
        return _new_session_file(prompt, names)
    date, kind = prompt["local_date"], prompt["kind"]
    day = datetime.strptime(date, "%Y-%m-%d").strftime("%a %-d %b %Y")
    how = f"capture: {prompt.get('capture') or '/j'}\n" if kind == "j" else ""
    # ``practice:`` names the definition a ritual entry answers (``kind:`` stays for every reader that has it);
    # a freeform note answers none.
    answers = f"practice: {kind}\n" if kind not in FREEFORM_IDS else ""
    annotations = (f"\n\n{names.annotations_heading}\n\n{PENDING}" if prompt.get("annotate", True) is not False
                   else f"\n\n{WORDS_END_MARK}")  # a note saved from conversation (or a practice) that won't be
    # annotated: none pending, and the hidden line that ends his words in its place
    return (f"---\ndate: {date}\nkind: {kind}\n{answers}type: journal\nsource: {names.frontmatter_source}\n{how}---\n\n"
            f"# {title_for(kind, names, practice)} — {day}\n\n## Prompt\n\n{_prompt_md(prompt, names, practice)}\n\n"
            f"{names.words_heading}\n\n{names.no_words}{annotations}\n")


def _new_session_file(prompt: dict, names: Names) -> str:
    """A program session's entry (``YYYY-MM-DD-<program>-sNN.md``; a situational run's ``YYYY-MM-DD-<id>.md``):
    front matter naming the program, session and run, so readers find it by front matter (``entry_key``)."""
    date, kind = prompt["local_date"], prompt["kind"]
    day = datetime.strptime(date, "%Y-%m-%d").strftime("%a %-d %b %Y")
    session = f"session: {prompt['session']}\n" if prompt.get("session_count") else ""
    title = prompt.get("title") or kind
    if prompt.get("session_count"):
        title = f"{title} · Session {prompt['session']}: {prompt.get('session_title') or ''}".rstrip(": ")
    annotations = (f"\n\n{names.annotations_heading}\n\n{PENDING}" if prompt.get("annotate", True) is not False
                   else "")
    return (f"---\ndate: {date}\nkind: {kind}\npractice: {kind}\nprogram: {prompt['program']}\n{session}"
            f"run: {prompt['run']}\ntype: journal\nsource: {names.frontmatter_source}\n---\n\n"
            f"# {title} — {day}\n\n## Prompt\n\n{_program_prompt_md(prompt)}\n\n"
            f"{names.words_heading}\n\n{names.no_words}{annotations}\n")


# Section parsing. The engine writes the sections in a fixed order: Prompt, the user's words,
# annotations, notes. Everything the model writes (opener, annotations, notes) has its level-one and
# level-two headings demoted on the way in (``demote_headings``), so the only free-form ``## `` lines
# in a file are the user's own, and they can only sit inside the words section. The words section
# therefore ends at the *last* annotations heading, and the annotations section at the notes heading
# after it, whatever the user wrote.

def demote_headings(text: str) -> str:
    """Model text only (never the user's words): ``#``/``##`` headings become ``###``."""
    return re.sub(r"(?m)^#{1,2}(?=[ \t])", "###", text or "")


def _start_of(body: str, m: re.Match) -> int:
    return m.end() + 1 if m.end() < len(body) and body[m.end()] == "\n" else m.end()


def _last(rx: re.Pattern, body: str, start: int = 0) -> re.Match | None:
    found = None
    for m in rx.finditer(body, start):
        found = m
    return found


def _words_heading(body: str) -> re.Match | None:
    """The user's section: the first ``## `` heading that isn't the prompt, annotations or notes."""
    for m in _H2.finditer(body):
        line = m.group(0)
        if line.strip() == "## Prompt" or _CONVERSATION.match(body, m.start()) or _ANNOTATIONS.fullmatch(line) \
                or _NOTES.fullmatch(line) or _SUMMARY.match(body, m.start()):
            continue  # (a "## Conversation" without the engine's mark is a user named Conversation)
        return m
    return None


def _section_end(body: str, start: int) -> int:
    """Where the user's words and the conversation stop: the engine's end-of-words line (``WORDS_END_MARK``), else
    the last annotations heading, else the last notes heading (an older file without annotations), else the end."""
    mark = _words_end(body, start)
    ann = None if mark else _annotations_heading(body, start)
    if mark:
        end = mark.start()
    elif ann:
        end = ann.start()
    else:
        notes = _last(_NOTES, body, start)
        end = notes.start() if notes else len(body)
    summary = _SUMMARY.search(body, start)  # a program session's summary sits after them
    return summary.start() if summary and summary.start() < end else end


def _conversation_heading(body: str, start: int, end: int) -> re.Match | None:
    """The engine's ``## Conversation`` heading (with its hidden mark), after the words, before the annotations."""
    m = _CONVERSATION.search(body, start)
    return m if m and m.start() < end else None


def _after_words(body: str, start: int = 0) -> int:
    """Where the engine's own sections may begin: after the end-of-words line when the file has one, else ``start``."""
    mark = _words_end(body, start)
    return mark.end() if mark else start


def _annotations_heading(body: str, after: int = 0) -> re.Match | None:
    return _last(_ANNOTATIONS, body, _after_words(body, after))


def words_span(body: str) -> tuple[int, int] | None:
    m = _words_heading(body)
    if not m:
        return None
    start = _start_of(body, m)
    end = _section_end(body, start)  # no annotations heading: a note saved from conversation, or an older file
    conv = _conversation_heading(body, start, end)
    return start, conv.start() if conv else end


def conversation_span(body: str) -> tuple[int, int] | None:
    """The ``## Conversation`` section (after the words, before the annotations), or None."""
    m = _words_heading(body)
    if not m:
        return None
    start = _start_of(body, m)
    end = _section_end(body, start)
    conv = _conversation_heading(body, start, end)
    return (_start_of(body, conv), end) if conv else None


def annotations_span(body: str) -> tuple[int, int] | None:
    w = _words_heading(body)
    m = _annotations_heading(body, _start_of(body, w) if w else 0)
    if not m:
        return None
    start = _start_of(body, m)
    nxt = _H2.search(body, start)
    return start, nxt.start() if nxt else len(body)


def notes_span(body: str) -> tuple[int, int] | None:
    ann = annotations_span(body)
    m = _NOTES.search(body, ann[0]) if ann else _last(_NOTES, body, _after_words(body))
    if not m:
        return None
    start = _start_of(body, m)
    return start, len(body)


def block_heading(at: datetime, source: str, meta: dict | None = None) -> str:
    return f"### {at.strftime('%H:%M %Z')} · {audio_label(source, meta, at.tzinfo)}"


def _raw_block(at: datetime, text: str, source: str, audio: str | None = None, meta: dict | None = None,
               images: Sequence[str] = ()) -> str:
    """One block of his words: heading, then the kept recording or pictures as Obsidian embeds, then his words."""
    embed = f"![[{audio}]]\n\n" if audio else "".join(f"![[{name}]]\n\n" for name in images)
    return f"{block_heading(at, source, meta)}\n\n{embed}{text.rstrip()}\n\n"


# The heading of a recording's block as the engine writes it: ``### HH:MM … · audio file …`` (or ``voice
# transcript``) among his words, ``**HH:MM** · audio file …`` in the conversation (``conversations.marker``).
_RECORDING_HEADING = re.compile(r"(?:### [0-9]{1,2}:[0-9]{2}\b[^\n]*?|\*\*[^*\n]+\*\*)"
                                r" · (?:audio file|voice transcript)\b[^\n]*")


def _recording_block(body: str, embed: str) -> tuple[int, int, int] | None:
    """Where the engine's block for this recording is: (its heading line's start and end, the embed's start). The
    embed counts only as a whole line with nothing but blank lines between it and an engine heading of a recording
    above it: his words can carry the same ``![[…]]`` (an edit from the web app, mid-line or on a line of
    its own under a text heading), and those are his, never the block to fill."""
    line = re.compile(r"^" + re.escape(embed) + r"[ \t]*$", re.M)
    for span in (words_span(body), conversation_span(body)):
        if not span:
            continue
        for m in line.finditer(body, span[0], span[1]):
            end = m.start()
            while end > span[0] and body[end - 1] == "\n":
                end -= 1
            head_start = max(body.rfind("\n", span[0], end) + 1, span[0])
            if _RECORDING_HEADING.fullmatch(body, head_start, end):
                return head_start, end, m.start()
    return None


def fill_audio_block(path: Path, audio: str, transcript: str | None, heading: str | None = None,
                     placeholder: str = "", replace_only: bool = False) -> bool:
    """Complete an audio file's reply block once the tick has transcribed (or probed) it.

    The engine-owned parts change: the heading line (metadata) and the ``placeholder`` line. The user's
    caption, if any, stays as it is, after the transcript (``replace_only``: only ever replace the
    placeholder, never insert). The block is the recording's embed as a whole line directly under the engine's
    heading (``_recording_block``); returns False, having written nothing, if there is no such block (he removed
    it, or only his own words mention the recording).
    """
    body = path.read_text(encoding="utf-8")
    embed = f"![[{audio}]]"
    found = _recording_block(body, embed)
    if found is None:
        return False
    head_start, end, at = found
    if heading:
        body = body[:head_start] + heading + body[end:]
        at = body.find(embed, head_start)
    after = at + len(embed)
    rest = body[after:]
    lead = rest[:len(rest) - len(rest.lstrip("\n"))]
    tail = rest[len(lead):]
    if transcript is not None:
        if placeholder and tail.startswith(placeholder):
            tail = transcript.strip() + tail[len(placeholder):]
        elif not replace_only:
            tail = transcript.strip() + "\n\n" + tail
        body = body[:after] + "\n\n" + tail
    _atomic_write(path, body)
    return True


def record_reply(root: Path, prompt: dict, text: str, captured_at: datetime, source: str = "text",
                 audio: str | None = None, names: Names = DEFAULT_NAMES, meta: dict | None = None,
                 practice=None, images: Sequence[str] = ()) -> Path:
    """Write or append the user's verbatim words to this prompt's entry file. A new file is titled and carries
    the bank of ``practice`` (the one the prompt was sent for; by default the built-in of the prompt's kind).
    ``images``: pictures he sent with the words, kept beside the entry (``images.keep``) and embedded above them."""
    path = entry_path(root, prompt["stem"])
    body = path.read_text(encoding="utf-8") if path.exists() else _new_file(prompt, names, practice)
    span = words_span(body)
    if span is None:  # a file the companion started with a note: add the user's section before it
        notes = _NOTES.search(body)
        insert = (f"{names.words_heading}\n\n{names.no_words}\n\n{names.annotations_heading}\n\n{PENDING}\n\n")
        if notes:
            body = body[:notes.start()] + insert + body[notes.start():]
        else:
            body = body.rstrip() + "\n\n" + insert
        span = words_span(body)
    s, e = span
    words = body[s:e].strip()
    words = "" if _NO_WORDS.match(words) else words + "\n\n"
    keyed = bool(prompt.get("program") and (prompt.get("phase") or prompt.get("interrupt")))
    key = (interrupt_mark(prompt["interrupt"]) if prompt.get("interrupt") else phase_mark(prompt["phase"])) + "\n" \
        if keyed else ""
    body = body[:s] + "\n" + words + key + _raw_block(captured_at, text, source, audio, meta, images) + body[e:]
    _atomic_write(path, body)
    if keyed:
        add_phase_prompt(path, prompt)
    return path


def _answer_blocks(body: str, which: str, keep_empty: bool = False) -> dict[int, list[str]]:
    """His words in a session entry under the hidden key lines of one kind (``phase`` or ``interrupt``), block by
    block, with the engine's lines (block headings, audio embeds, the transcription placeholder) left out. A block
    ends at the next key line of either kind, so an interrupt's answer never reads as the end of a phase's."""
    span = words_span(body)
    if not span:
        return {}
    text = body[span[0]:span[1]]
    out: dict[int, list[str]] = {}
    keys = list(_ANSWER_KEY.finditer(text))
    for i, m in enumerate(keys):
        if m.group(1) != which:
            continue
        chunk = text[m.end():keys[i + 1].start() if i + 1 < len(keys) else len(text)]
        lines = [l for l in chunk.strip("\n").splitlines()]
        if lines and lines[0].startswith("### "):
            lines = lines[1:]
        words = "\n".join(l for l in lines if not re.match(r"^!\[\[[^\]]+\]\]\s*$", l)).strip()
        if words == TRANSCRIBING:
            words = ""
        if words or keep_empty:
            out.setdefault(int(m.group(2)), []).append(words)
    return out


def phase_blocks(body: str, keep_empty: bool = False) -> dict[int, list[str]]:
    """His words in a session entry by phase, one string per block he wrote (in the order he wrote them).
    ``keep_empty`` keeps a block that has no words yet (an audio file waiting for its transcript, a photo with no
    caption) as ``""``, so the blocks can be paired one for one with the captures that made them."""
    return _answer_blocks(body, "phase", keep_empty)


def phase_answers(body: str) -> dict[int, str]:
    """His words in a session entry by phase: every block under a ``%% journal phase:N %%`` key, joined. Read-only."""
    return {n: "\n\n".join(v) for n, v in phase_blocks(body).items()}


def interrupt_blocks(body: str) -> dict[int, list[str]]:
    """His words in a session entry by interrupt, one string per block he wrote (in the order he wrote them)."""
    return _answer_blocks(body, "interrupt")


def interrupt_answers(body: str) -> dict[int, str]:
    """His words in a session entry by interrupt: every block under a ``%% journal interrupt:N %%`` key, joined."""
    return {n: "\n\n".join(v) for n, v in interrupt_blocks(body).items()}


def summary_text(body: str) -> str:
    """The session's ``## Summary`` as written by code ("" when the session has none yet)."""
    m = _SUMMARY.search(body or "")
    if not m:
        return ""
    rest = body[m.end():]
    nxt = _H2.search(rest)
    return rest[:nxt.start() if nxt else len(rest)].strip()


# A block of his (or a companion note) as the engine writes it: an optional hidden key line, then a
# ``### HH:MM <zone> …`` heading. Code from before the programs appends a late reply below ``## Summary``; this finds
# those. A key line alone counts too (it is never summary text), and so does a conversation key line (``his:`` or
# ``her:``: old code appends conversation lines below the summary when a conversation sits above it). The summary's
# own copy is written so that nothing in it has any of these shapes (``_summary_copy``).
_KEY_LINE = r"%% journal (?:phase|interrupt):[0-9]+ %%[ \t]*"
_CONV_KEY_LINE = r"%% journal (?:his|her):[^%\n]* %%[ \t]*"
_TIME_HEADING = r"### [0-9]{1,2}:[0-9]{2}\b[^\n]*"
_STRAY_START = re.compile(rf"^(?:{_KEY_LINE}|{_CONV_KEY_LINE}|{_TIME_HEADING})$", re.M)
_STRAY_CUT = re.compile(rf"^(?:{_KEY_LINE}|{_CONV_KEY_LINE})$", re.M)
_CONV_BLOCK = re.compile(_CONV_KEY_LINE)


def _summary_copy(text: str) -> str:
    """The summary's text as written: model headings demoted, and nothing in it shaped like one of his blocks (a
    ``### 10:30`` line, which verbatim answers carry and ``demote_headings`` makes from his ``# 7:00``, becomes
    ``#### 10:30``) or like an engine key line, so a later ``write_summary`` never takes the summary for his words."""
    return re.sub(r"(?m)^(?=### [0-9]{1,2}:[0-9]{2}\b)", "#", ENGINE_MARK.sub("", demote_headings((text or "").strip())))


def _stray_blocks(rest: str) -> tuple[str, list[str], str]:
    """Split what follows the summary mark into (the summary's own text, blocks of his, a note or a conversation
    line found in it, what follows them). The first block starts at the first key line or time heading; from there
    blocks are cut only at key lines, so a ``### 10:30`` or a ``## `` line he typed inside one stays in it. The
    blocks run to where his words section would end (the last annotations heading, else the last notes heading, else
    the end), so a heading he typed in the engine's shape ends nothing."""
    nxt = _H2.search(rest)
    end = nxt.start() if nxt else len(rest)
    first = _STRAY_START.search(rest, 0, end)
    if not first:
        return rest[:end], [], rest[end:]
    end = _section_end(rest, first.start())  # the rule that ends his words: a typed "## Today's notes" doesn't
    cuts = [first.start()] + [m.start() for m in _STRAY_CUT.finditer(rest, first.start() + 1, end)] + [end]
    return rest[:cuts[0]], [rest[a:b].strip("\n") for a, b in zip(cuts, cuts[1:])], rest[end:]


def _place_block(head: str, block: str) -> str:
    """Put one of his blocks back where it belongs in ``head`` (the entry above the summary): a conversation line
    at the end (the conversation's end); a block under a phase or interrupt key after the last block under the
    same key; any other at the end of his words."""
    span = words_span(head)
    at = len(head) if _CONV_BLOCK.match(block) or not span else span[1]
    key = _ANSWER_KEY.match(block)
    if key and span:
        mine = list(_ANSWER_KEY.finditer(head, span[0], span[1]))
        same = [i for i, m in enumerate(mine) if (m.group(1), m.group(2)) == (key.group(1), key.group(2))]
        if same:
            i = same[-1]
            at = mine[i + 1].start() if i + 1 < len(mine) else span[1]
    return head[:at].rstrip("\n") + "\n\n" + block + "\n\n" + head[at:].lstrip("\n")


def write_summary(path: Path, text: str, names: Names = DEFAULT_NAMES) -> str:
    """Write (or replace: it is code's, filled from his words) the session's ``## Summary``, after his words and
    the conversation, before the annotations. Model-style headings in the copy are demoted, so a ``## `` line he
    wrote cannot open a section there; his own blocks above are untouched. Only the summary's text is replaced: a
    block of his, a note or a conversation line found under the mark, where code from before the programs appends
    a late reply, is moved above the summary first (into its phase's group when it carries a phase key, a
    conversation line at the conversation's end, anything else at the end of his words). Returns the digest of the
    section as written (``summary_digest``)."""
    body = path.read_text(encoding="utf-8")
    section = f"{SUMMARY_HEADING}\n\n{SUMMARY_MARK}\n\n{_summary_copy(text)}\n"
    m = _SUMMARY.search(body)
    if m:
        _, strays, after = _stray_blocks(body[m.end():])
        head = body[:m.start()]
        for block in strays:
            head = _place_block(head, block)
        body = head + section + ("\n" + after if after else "")
    else:
        w = _words_heading(body)
        at = _section_end(body, _start_of(body, w) if w else 0)  # (no summary here: the end of his words)
        head, tail = body[:at].rstrip() + "\n\n", body[at:]
        body = head + section + ("\n" + tail if tail else "")
    _atomic_write(path, body)
    return summary_digest(body) or ""


def summary_section(body: str) -> str | None:
    """The summary exactly as it stands in ``body``: from its ``## Summary`` heading to the next ``## `` heading (or
    the end), trailing blank lines cut. None when the engine's mark is not there (no summary, or one with the mark
    deleted: that is not the engine's section any more)."""
    m = _SUMMARY.search(body or "")
    if not m:
        return None
    rest = body[m.end():]
    nxt = _H2.search(rest)
    return body[m.start():m.end() + (nxt.start() if nxt else len(rest))].rstrip()


def summary_digest(body: str) -> str | None:
    """A digest of ``summary_section``: what the engine records when it writes the section, so that a later writer
    can tell the section is still what it wrote (and not text he added or changed in his notes app), apart from the
    whitespace at the end of its lines."""
    section = summary_section(body)
    if section is None:
        return None
    # (trailing whitespace of each line is not counted: an editor that strips it on save would otherwise make
    # every later refresh refuse; the section is written with its exact bytes)
    return hashlib.sha256("\n".join(line.rstrip() for line in section.split("\n")).encode("utf-8")).hexdigest()


def summary_unsafe_to_replace(body: str, written: str | None) -> str | None:
    """Why ``write_summary`` must not be run on ``body`` by a writer that only refreshes, or None when it may: it
    only replaces a section that is there (the engine's mark), is byte for byte what the engine last wrote
    (``written``, the digest ``write_summary`` returned; none known is refused), and has nothing of his under it
    (``_stray_blocks``). Anything else is words of his, or an entry the engine did not lay out, and stays as it is."""
    m = _SUMMARY.search(body or "")
    if not m:
        return "it has no summary section of the engine's (no mark)"
    if _stray_blocks(body[m.end():])[1]:
        return "a block of his sits under the summary"
    if not written:
        return "no record of what the engine wrote there"
    if summary_digest(body) != written:
        return "the section is not what the engine wrote (it was changed in the file)"
    return None


def raw_entry(body: str) -> str:
    """The user's words section: the first message and later direct replies (not the conversation). The
    engine's hidden key lines (``%% journal phase:2 %%``) are left out: they are not his words, and this is what
    packs, annotations and quote checks read."""
    span = words_span(body)
    text = ENGINE_MARK.sub("", body[span[0]:span[1]]).strip() if span else ""
    return "" if _NO_WORDS.match(text) else text


def conversation(body: str) -> str:
    """The ``## Conversation`` section as written: the user's later messages and the companion's folded replies."""
    span = conversation_span(body)
    return body[span[0]:span[1]].strip() if span else ""


def strip_callouts(text: str) -> str:
    """Conversation text without the folded callouts (the companion's replies and reply-context lines)."""
    out = ENGINE_MARK.sub("", _REPLY_CONTEXT.sub(r"\1", _CALLOUT.sub("", text or "")))
    return re.sub(r"\n{3,}", "\n\n", out).strip()


def his_conversation(body: str) -> str:
    """The user's own lines in the conversation section (time markers kept, the companion's replies removed)."""
    return strip_callouts(conversation(body))


def his_words(body: str) -> str:
    """Everything the user said in this entry: the words section, then their conversation lines."""
    return "\n\n".join(t for t in (raw_entry(body), his_conversation(body)) if t)


def without_summary(body: str) -> str:
    """The entry with the engine's marked ``## Summary`` section removed: it is code's prose (a template filled
    from his words, plus its own wording), so a quote check must not accept it as something he wrote. A heading he
    typed himself has no mark and stays."""
    m = _SUMMARY.search(body)
    if not m:
        return body
    rest = body[m.end():]
    nxt = _H2.search(rest)
    return body[:m.start()] + (rest[nxt.start():] if nxt else "")


def without_companion_replies(body: str) -> str:
    """The whole entry with the companion's folded conversation replies removed (annotation input, quote
    checks): her chat replies must never read as the user's words."""
    span = conversation_span(body)
    if span:
        s, e = span
        inner = strip_callouts(body[s:e])
        body = body[:s] + "\n" + (inner + "\n\n" if inner else "") + body[e:]
    # the engine's hidden key lines (``%% journal phase:2 %%`` in his words, the conversation mark) are not
    # his words either: the annotation model must not see them and the quote checker must not accept them
    return ENGINE_MARK.sub("", body)


def append_conversation(path: Path, blocks: str, names: Names = DEFAULT_NAMES) -> None:
    """Append conversation blocks to the entry's ``## Conversation`` section, creating it (after the words,
    before the annotations and notes) on first use. Nothing else in the file changes."""
    blocks = (blocks or "").strip()
    if not blocks:
        return
    body = path.read_text(encoding="utf-8")
    span = conversation_span(body)
    if span is None:
        words = words_span(body)
        if words is None:  # a file the companion started with a note: the user's section first
            record_shell = f"{names.words_heading}\n\n{names.no_words}\n\n"
            notes = _NOTES.search(body)
            at = notes.start() if notes else len(body)
            body = body[:at].rstrip() + "\n\n" + record_shell + body[at:].lstrip("\n")
            words = words_span(body)
        end = words[1]
        head, rest = body[:end].rstrip() + "\n\n", body[end:].lstrip("\n")
        body = head + f"{CONVERSATION_HEADING}\n\n{CONVERSATION_MARK}\n\n" + ("\n" + rest if rest else "")
        span = conversation_span(body)
    s, e = span
    current = body[s:e].strip()
    middle = (current + "\n\n" if current else "") + blocks + "\n"
    rest = body[e:].lstrip("\n")
    _atomic_write(path, body[:s] + "\n" + middle + ("\n" + rest if rest else ""))


def annotations(body: str) -> str:
    span = annotations_span(body)
    text = body[span[0]:span[1]].strip() if span else ""
    return "" if text == PENDING else text


def annotations_pending(body: str) -> bool:
    span = annotations_span(body)
    return bool(span) and body[span[0]:span[1]].strip() in ("", PENDING) and bool(raw_entry(body))


def write_annotations(path: Path, text: str, names: Names = DEFAULT_NAMES) -> None:
    """Replace only the annotations; the user's words and the companion's notes are untouched."""
    body = path.read_text(encoding="utf-8")
    span = annotations_span(body)
    if span is None:
        body = body.rstrip() + f"\n\n{names.annotations_heading}\n\n"
        span = (len(body), len(body))
    s, e = span
    rest = body[e:].lstrip("\n")
    body = body[:s] + "\n" + _hers(text) + "\n" + ("\n" + rest if rest else "")
    _atomic_write(path, body)


def add_companion_note(path: Path, text: str, at: datetime, prompt: dict | None = None,
                       names: Names = DEFAULT_NAMES) -> Path:
    """Append the companion's own timestamped note (creating a companion-started entry if needed)."""
    if path.exists():
        body = path.read_text(encoding="utf-8")
    else:
        fresh = _new_file(prompt, names)
        body = fresh[:fresh.index(names.words_heading)].rstrip() + "\n"
    note = f"### {at.strftime('%H:%M %Z')}\n\n{_hers(text)}\n"
    if notes_span(body):
        body = body.rstrip() + "\n\n" + note
    else:
        body = body.rstrip() + f"\n\n{names.notes_heading}\n\n" + note
    _atomic_write(path, body)
    return path


# An engine entry is ``YYYY-MM-DD-<id>[-sNN][-N].md``: the practice id (``evening``, ``j``, ``note``, a user's
# ``gratitude``), an optional program session number and an optional repeat number. The id is checked against
# ``known_ids`` (the four rituals plus ``j`` and ``note``; a user practice's id deliberately is not one). A file
# with an unknown id still counts when its own front matter claims the id (``kind: <id>`` or ``practice: <id>``)
# *and* says the engine wrote it (``type: journal``, which every engine entry carries), so a user practice's
# entries, and a renamed or deleted practice's history, are recognised. The authorship marker is what keeps the
# engine's writers (the companion's notes above all) out of files it did not create: the user's own
# ``YYYY-MM-DD.md`` and ``YYYY-MM-DD-2.md`` never match (an id starts with a letter), and a hand-written
# ``YYYY-MM-DD-thoughts.md`` is ignored even when it says ``kind: thoughts`` (DECISIONS.md, "Raised by packages").
#
# An id may itself end in ``-10`` or ``-s01`` (``level-10``, ``reset-s01`` are valid ids), so the name alone cannot
# say where the id stops: ``name_candidates`` lists every reading, longest id first, and ``entry_key`` takes the
# first one the ids it is given (then the file's front matter) confirm. ``name_parts`` is the shortest reading
# (the most suffixes), what the old regex gave.
_ENGINE_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-([a-z][a-z0-9-]*?)(?:-s(\d{2}))?(-\d+)?\.md$")
_ENTRY_STEM = re.compile(r"^\d{4}-\d{2}-\d{2}-([a-z][a-z0-9-]*)\.md$")
_REPEAT = re.compile(r"^(.+)-(\d+)$")
_SESSION = re.compile(r"^(.+)-s(\d{2})$")
FREEFORM_IDS = ("j", "note")  # a /j note; an entry the companion started with a note
ENGINE_TYPE = "journal"  # the ``type:`` value every engine-written entry carries


def known_ids() -> tuple[str, ...]:
    """The ids recognised by name alone: the built-in rituals, ``j`` and ``note``. A practice of his own is not
    added (WP2): an entry the engine writes for it carries ``practice: <id>`` and ``type: journal``, which is
    how it is recognised, so a hand-written ``YYYY-MM-DD-<id>.md`` that happens to share a practice's id is never
    taken for the engine's (nor given the companion's notes)."""
    from . import library  # (library imports this module: at call time only)
    return (*library.builtin_ids(), *FREEFORM_IDS)


# The companion's analyses (reflect.py): YYYY-MM-DD-reflection.md (a day), YYYY-MM-DD-week.md (the week
# ending that day). A file is only treated as one when its front matter says the engine wrote it, so
# a hand-written note that happens to share the name is never mistaken for (or overwritten as) one.
ANALYSIS_KINDS = ("reflection", "week")
_INSIGHTS_NAME = re.compile(r"-insights(-\d+)?\.md$")  # (``programs.insights``: ``<date>-<program>-insights[-n]``)
_ANALYSIS_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-(reflection|week)(-\d+)?\.md$")
_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)
_FRONT_LIMIT = 64 * 1024  # how much of a file's head is read for its front matter: more than any real block


def name_parts(name: str) -> tuple[str, int | None, int] | None:
    """``(id, session, n)`` from an entry-shaped file name, or None. By name alone: no disk, no id check."""
    m = _ENGINE_NAME.match(name)
    if not m:
        return None
    return m.group(1), int(m.group(2)) if m.group(2) else None, int(m.group(3)[1:]) if m.group(3) else 1


def name_candidates(name: str) -> list[tuple[str, int | None, int]]:
    """Every ``(id, session, n)`` an entry-shaped name could mean, longest id first: ``level-10`` is the practice
    ``level-10`` or the tenth ``level``; ``reset-s01`` the practice ``reset-s01`` or session 1 of ``reset``;
    ``fix-life-s01-2`` the practice ``fix-life-s01-2``, the second ``fix-life-s01``, or session 1 of ``fix-life``
    for the second time. [] for a name that is not entry-shaped."""
    m = _ENTRY_STEM.match(name)
    if not m:
        return []
    tail = m.group(1)
    out = [(tail, None, 1)]
    rest, n = tail, 1
    rep = _REPEAT.match(tail)
    if rep:
        rest, n = rep.group(1), int(rep.group(2))
        out.append((rest, None, n))
    ses = _SESSION.match(rest)
    if ses:
        out.append((ses.group(1), int(ses.group(2)), n))
    return out


def entry_key(path: Path, ids: tuple[str, ...] | None = None) -> tuple[str, int | None, int] | None:
    """``(id, session, n)`` of an engine entry, or None for anything else (the user's own files, analyses).

    ``ids`` are the ids known without reading (``known_ids()`` by default; a caller with a library passes its own,
    and must pass the same set to ``entries_on``/``kind_of`` so one view of the day is used throughout). Of the
    name's readings (``name_candidates``) the first whose id is known wins, so a practice ``level-10`` beats the
    tenth ``level`` when both exist; a name no known id explains is accepted only when the file's own front
    matter claims one of its readings and carries the engine's ``type``."""
    cands = name_candidates(path.name)
    if not cands or any(c[0] in ANALYSIS_KINDS for c in cands):
        return None
    ids = known_ids() if ids is None else ids
    for c in cands:
        if c[0] in ids:
            return c
    fm = front_matter_of(path)
    if fm.get("type") != ENGINE_TYPE:
        return None
    claimed = (fm.get("practice"), fm.get("kind"))
    return next((c for c in cands if c[0] in claimed), None)


def entries_on(root: Path, local_date: str, ids: tuple[str, ...] | None = None) -> list[Path]:
    """Engine entries for a day (not hand-written files, not the companion's analyses), oldest first."""
    ids = known_ids() if ids is None else ids
    return sorted((p for p in root.glob(f"{local_date}-*.md") if entry_key(p, ids)),
                  key=lambda p: p.stat().st_mtime)


def front_matter(body: str) -> dict[str, str]:
    """``{key: value}`` of the leading ``---`` block, values as strings with one layer of matching quotes
    removed (``kind: "thanks"`` is the id ``thanks``); {} when the body has no front matter. A key given twice
    keeps its last value (``front_matter_pairs`` shows both)."""
    return dict(front_matter_pairs(body))


def front_matter_pairs(body: str) -> list[tuple[str, str]]:
    """Every ``key: value`` line of the leading ``---`` block in order, repeats included, read exactly as
    ``front_matter`` reads them: what a strict reader (a practice file) needs to refuse a key given twice."""
    m = _FRONT.match(body or "")
    out = []
    for line in (m.group(1).splitlines() if m else []):
        k, sep, v = line.partition(":")
        if sep:
            v = v.strip()
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                v = v[1:-1]
            out.append((k.strip(), v))
    return out


_BLOCK_ITEM = re.compile(r"[ \t]*-(?:[ \t]+(.*))?$")


def front_matter_lists(body: str) -> dict[str, list[str]]:
    """The keys of the leading ``---`` block written as a YAML block list (``mood:`` then ``  - calm`` lines: what
    Obsidian's Properties editor writes), as ``{key: [items]}``. ``front_matter`` sees such a key with an empty
    value; this is where its items are. A double-quoted item is decoded, a single-quoted one unquoted. Keys with a
    value on their own line, or with no items, are not here."""
    m = _FRONT.match(body or "")
    out: dict[str, list[str]] = {}
    key = None
    for line in (m.group(1).splitlines() if m else []):
        item = _BLOCK_ITEM.match(line) if key else None
        if item:
            text = (item.group(1) or "").strip()
            if len(text) >= 2 and text[0] == text[-1] == '"':
                try:
                    text = json.loads(text)
                except ValueError:
                    text = text[1:-1]
            elif len(text) >= 2 and text[0] == text[-1] == "'":
                text = text[1:-1].replace("''", "'")
            if isinstance(text, str) and text.strip():
                out[key].append(text.strip())
            continue
        k, sep, v = line.partition(":")
        key = k.strip() if sep and line[:1] not in (" ", "\t") and not v.strip() else None
        if key:
            out[key] = []
    return {k: v for k, v in out.items() if v}


def read_head(path: Path) -> str:
    """The first ``_FRONT_LIMIT`` characters of a file ("" when it cannot be read): enough for its front matter."""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(_FRONT_LIMIT)
    except OSError:
        return ""


def front_matter_of(path: Path) -> dict[str, str]:
    """A file's front matter from its head alone (up to ``_FRONT_LIMIT``, so a long ``tags:`` line is no
    problem and a huge file is not read whole); {} for a file that has none or cannot be read."""
    return front_matter(read_head(path))


def authored_by_engine(path: Path) -> bool:
    """A file the companion or the engine wrote whole, none of it his words: a reflection or week review
    (``is_analysis``: its name, ``kind`` and an ``author``) or a program's insights document (named
    ``…-insights.md``, ``kind: insights``, ``author: engine`` and the ``run`` it was compiled for). A file of his own
    is not one, whatever its front matter says: a hand-written ``author: me`` stays his."""
    if is_analysis(path):
        return True
    fm = front_matter_of(path)
    return (bool(_INSIGHTS_NAME.search(path.name)) and fm.get("kind") == "insights" and fm.get("author") == "engine"
            and bool(fm.get("run")))


def is_analysis(path: Path) -> bool:
    """An engine-written reflection or week review (by name and front matter)."""
    m = _ANALYSIS_NAME.match(path.name)
    if not m or not path.is_file():
        return False
    fm = front_matter_of(path)
    return fm.get("kind") == m.group(1) and bool(fm.get("author"))


def analyses_on(root: Path, local_date: str, kind: str) -> list[Path]:
    return sorted(p for p in root.glob(f"{local_date}-{kind}*.md") if is_analysis(p))


def kind_of(path: Path, ids: tuple[str, ...] | None = None) -> str | None:
    """The id of an engine entry (``evening``, ``j``, ``note``, ``fix-life`` …) or of an engine-written analysis
    (``reflection``, ``week``); None for the user's own files. Callers compare it with ids, so it stays a string;
    ``entry_key`` has the session and repeat numbers (and the meaning of ``ids``)."""
    key = entry_key(path, ids)
    if key:
        return key[0]
    m = _ANALYSIS_NAME.match(path.name)
    return m.group(1) if m and is_analysis(path) else None


def without_question_bank(body: str) -> str:
    """An entry as analysis input: no front matter, no folded question bank (the same every day)."""
    body = _FRONT.sub("", body or "", count=1)
    out, skipping = [], False
    for line in body.splitlines():
        if line.startswith("> [!question]"):
            skipping = True
            continue
        if skipping and line.startswith(">"):
            continue
        skipping = False
        out.append(line)
    return "\n".join(out).strip()


META_KEYS = ("mood", "people", "starred")  # the front-matter keys the web app may set on an entry (``set_meta``)


_PLAIN_ITEM = re.compile(r"[^\W_][\w '’.()/+-]*")   # what YAML reads as the same text, as a plain scalar in a flow list
_YAML_WORDS = frozenset({"true", "false", "yes", "no", "on", "off", "y", "n", "null", "~"})
_YAML_DATE = re.compile(r"\d{4}-\d\d?-\d\d?")   # a timestamp to YAML, a date object to Dataview
_YAML_NUMBER = re.compile(r"[-+]?(\d[\d_]*\.?\d*|\.\d+)([eE][-+]?\d+)?|0[xob][\da-fA-F_]+|[-+]?\.(inf|nan)", re.IGNORECASE)


def flow_list(items: list[str]) -> str:
    """``[a, b]`` for a front-matter list. An item that YAML would not read back as the same text (a leading ``#``,
    ``*``, ``!``, ``@``, ``%`` or quote, a ``: ``, ``true``, ``12`` …) is written as a double-quoted scalar
    (``json.dumps`` is valid YAML), so Obsidian's properties, Dataview and any YAML reader still parse the whole
    block; the plain ones stay plain, as hand-written files have them."""
    def one(item: str) -> str:
        if (_PLAIN_ITEM.fullmatch(item) and item.lower() not in _YAML_WORDS
                and not _YAML_NUMBER.fullmatch(item.replace(" ", "x")) and not _YAML_DATE.match(item)):
            return item
        return json.dumps(item, ensure_ascii=False)
    return "[" + ", ".join(one(i) for i in items) + "]"


_FLOW_ITEM = re.compile(r'\s*("(?:[^"\\]|\\.)*"|[^,]*?)\s*(?:,|$)')


def parse_flow_list(value: str | None) -> list[str]:
    """The items of a front-matter ``[a, b]`` (or bare ``a, b``) as ``flow_list`` writes them: a double-quoted item is
    decoded, any other is its text. Blank items are dropped."""
    text = (value or "").strip()
    if text.startswith("[") and text.endswith("]"):
        text = text[1:-1]
    out = []
    for m in _FLOW_ITEM.finditer(text):
        item = m.group(1)
        if item.startswith('"') and item.endswith('"') and len(item) >= 2:
            try:
                item = json.loads(item)
            except ValueError:
                item = item[1:-1]
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
    return out


def set_meta(path: Path, changes: dict[str, str | None]) -> None:
    """Set (a string) or remove (``None``) front-matter keys of an entry file. Only the front-matter block is
    rewritten, atomically; everything after it, his words included, is carried over byte for byte. A key is
    replaced where its first line is (a repeat, and the indented or ``- `` lines that follow it, go), a new one is
    added at the end of the block. ``ValueError`` when the file has no front matter or a value spans lines."""
    text = path.read_text(encoding="utf-8")
    m = _FRONT.match(text)
    if not m:
        raise ValueError("the entry has no front matter")
    if any(v and v.splitlines() != [v] for v in changes.values() if v is not None):  # \n, \r, \x0b, \x85, \u2028 …
        raise ValueError("a front-matter value is one line")
    pending = dict(changes)
    out: list[str] = []
    skipping = False  # inside the continuation lines of a key being replaced or removed
    for line in m.group(1).split("\n"):
        if skipping and (line[:1] in (" ", "\t") or line.startswith("- ")):
            continue
        skipping = False
        key = line.partition(":")[0].strip() if ":" in line and line[:1] not in (" ", "\t") else None
        if key in changes:
            skipping = True
            value = pending.pop(key, None)
            if value is not None:
                out.append(f"{key}: {value}")
            continue
        out.append(line)
    out += [f"{k}: {v}" for k, v in pending.items() if v is not None]
    block = "\n".join(out)
    new = text[:m.start(1)] + block + text[m.end(1):]
    if new != text:
        _atomic_write(path, new)


def write_file(path: Path, text: str) -> None:
    """Atomic 0644 write of an engine-owned file."""
    _atomic_write(path, text)
