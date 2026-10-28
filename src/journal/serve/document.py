"""Entry files as the web app reads them: the two-voice document (API.md §1) and the one-line summaries.

Everything here is parsed from the entry file itself (the engine owns its layout: ``entries.py``), plus the
prompt's ledger record and the library for titles. The rules that matter:

* **Two voices, never merged.** His words are the words section's ``### HH:MM TZ · text`` blocks and his messages
  in ``## Conversation``; hers are the folded callouts the conversation writer keys with ``%% journal her:<id> %%``.
  A block is ``kind: "his"`` or ``kind: "hers"``; the client folds every ``hers``. What counts as hers is exactly
  what ``entries.strip_callouts`` removes from his words, so the search (``entries.his_words``) and the document
  agree on whose words are whose.
* **Text as written.** Each ``text`` is the file's text with the engine's hidden key lines removed and the
  surrounding blank lines trimmed; nothing is reflowed or rewritten.
* **Only engine entries.** A stem must look like an entry name and ``entries.entry_key`` must accept the file
  (a hand-written ``YYYY-MM-DD.md`` or a file without the engine's marker is never served).

Summaries are cached by (path, mtime, size): listing a few hundred entries re-reads only what changed.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass, replace
from pathlib import Path

from .. import audio, capture, entries, images, ledger, library, today
from ..config import Config

STEM = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z][a-z0-9-]*$")
_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md"
_WORDS_HEAD = re.compile(
    r"^### (\d{1,2}:\d{2})(?: ([^\s·]+))? · (text|photo|voice transcript[^\n]*|audio file[^\n]*)[ \t]*$", re.M)
_ANSWER_KEY = re.compile(r"^%% journal (phase|interrupt):(\d+) %%[ \t]*$")
_MARK = re.compile(r"^%% journal (his|her):([^%\n]*?) %%[ \t]*$", re.M)
# Any ``![[name]]`` line; ``_audio_embeds`` keeps the ones that name a recording (a picture he pasted is not one).
_EMBED = re.compile(r"^!\[\[([^\]\n]+)\]\][ \t]*$", re.M)
_HIS_HEAD = re.compile(r"^\*\*((?:[A-Za-z]{3} \d{1,2} [A-Za-z]{3} )?\d{1,2}:\d{2})\*\*(?:( —)[ \t]?(.*)| · (.*))?$")
_CLOCK = re.compile(r" · (\d+(?::\d{2}){1,2})(?= ·|$)")
_FILE_NAME = re.compile(r'^audio file "([^"\n]+)"')
_NOTE_HEAD = re.compile(r"^### (\d{1,2}:\d{2}(?: \S+)?)[ \t]*$", re.M)
_H1 = re.compile(r"^# (.+)$", re.M)
_FROM = re.compile(r"^_From (.*)$", re.M)
_LINK = re.compile(r"\[\[([^\]|\n]+)(?:\|[^\]\n]*)?\]\]")
_NO_WORDS = re.compile(r"^_Nothing (?:from .+ )?yet\._$")


# ── finding entries ──────────────────────────────────────────────────────────

def entry_file(cfg: Config, stem: str) -> Path | None:
    """The engine entry for ``stem``, or None (a bad name, a missing file, a file the engine did not write)."""
    if not isinstance(stem, str) or not STEM.match(stem):
        return None
    path = entries.entry_path(cfg.root, stem)
    if not path.is_file() or entries.is_analysis(path):
        return None
    return path if entries.entry_key(path) else None


def engine_file(cfg: Config, stem: str) -> Path | None:
    """``entry_file`` narrowed to a file the engine wrote: its front matter has the engine's ``type`` and claims an
    id the name can mean (``capture.entry_stem``'s own test). A hand-written ``2026-10-13-evening.md`` is readable
    but is his: the web app never writes to it."""
    path = entry_file(cfg, stem)
    if path is None:
        return None
    fm = entries.front_matter_of(path)
    claimed = {fm.get("practice"), fm.get("kind")}
    if fm.get("type") != entries.ENGINE_TYPE or not claimed & {c[0] for c in entries.name_candidates(path.name)}:
        return None
    return path


def all_entries(cfg: Config) -> list[tuple[Path, tuple]]:
    """Every engine entry in the journal root with its ``(id, session, n)`` key, in no particular order."""
    out = []
    for path in cfg.root.glob(_GLOB):
        info = parsed(path)
        if info and info["key"]:
            out.append((path, info["key"]))
    return out


# ── parsing pieces ───────────────────────────────────────────────────────────

def _clean(text: str) -> str:
    """The text as written, without the engine's hidden key lines and the blank lines around it."""
    return entries.ENGINE_MARK.sub("", text or "").strip("\n").rstrip()


def _seconds(clock: str) -> int:
    n = 0
    for part in clock.split(":"):
        n = n * 60 + int(part)
    return n


def _source_meta(label: str) -> tuple[str, dict]:
    """``text`` / ``voice`` / ``audio`` / ``photo`` and what the heading says about the recording
    (``entries.block_heading``)."""
    label = label.strip()
    source = ("voice" if label.startswith("voice transcript") else "audio" if label.startswith("audio file")
              else "photo" if label == "photo" else "text")
    meta = {}
    m = _CLOCK.search(label)
    if m and source != "text":
        meta["duration"] = _seconds(m.group(1))
    f = _FILE_NAME.match(label)
    if f:
        meta["file_name"] = f.group(1)
    return source, meta


def _audio_embeds(text: str):
    """The embeds in ``text`` that ``/media`` would serve as audio (``audio.media_type``): ``![[diagram.png]]`` is a
    picture, ``![[….oga]]`` or ``![[….aif]]`` (the sender's own suffix is kept) is a recording."""
    return (m for m in _EMBED.finditer(text) if audio.media_type(m.group(1).strip()))


def _embed(text: str) -> tuple[str | None, str]:
    """The first audio embed (``![[name]]``) and the text without it."""
    m = next(_audio_embeds(text), None)
    if not m:
        return None, text
    return m.group(1).strip(), text[:m.start()] + text[m.end():]


def _pictures(text: str, stem: str | None = None) -> tuple[list[str], str]:
    """The kept pictures a photo block opens with (``![[name]]`` lines of an image type, ``images.media_type``,
    straight after the heading where the engine puts them; callers ask only of ``photo`` blocks) and the text
    without them. They are the engine's lines, not his words; an embed further down, in what he wrote, stays in his
    text as written. With the entry's ``stem``, only the names the engine gives its pictures count
    (``<stem>.jpg``, ``<stem>-2.jpg`` …): a caption of his that itself opens with an embed of another file stays
    his caption."""
    own = re.compile(rf"{re.escape(stem)}(?:-\d+)?\.\w+", re.I) if stem else None
    names: list[str] = []
    rest = text.lstrip("\n")
    while True:
        line, _, after = rest.partition("\n")
        m = _EMBED.match(line)
        if not m or not images.media_type(m.group(1).strip()) or (own and not own.fullmatch(m.group(1).strip())):
            break
        names.append(m.group(1).strip())
        rest = after.lstrip("\n")
    return (names, rest) if names else ([], text)


def _pictures_end(region: str, stem: str | None) -> int:
    """Where the pictures a photo block opens with end in ``region`` (0 when there are none): the same lines
    ``_pictures`` takes out of the text, so an edit of the caption starts after them and never touches them."""
    own = re.compile(rf"{re.escape(stem)}(?:-\d+)?\.\w+", re.I) if stem else None
    pos = len(region) - len(region.lstrip("\n"))
    end = 0
    while pos < len(region):
        nl = region.find("\n", pos)
        stop = len(region) if nl < 0 else nl
        m = _EMBED.match(region[pos:stop])
        if not m or not images.media_type(m.group(1).strip()) or (own and not own.fullmatch(m.group(1).strip())):
            break
        end = stop
        pos = stop + (len(region[stop:]) - len(region[stop:].lstrip("\n")))
    return end


def _answer_key_before(section: str, at: int) -> tuple[str, int] | None:
    """The ``%% journal phase:N %%`` or ``%% journal interrupt:N %%`` line just above a block heading (program session
    entries): ``("phase", N)`` / ``("interrupt", N)``."""
    for line in reversed(section[:at].rstrip("\n").splitlines()[-3:]):
        if not line.strip():
            continue
        m = _ANSWER_KEY.match(line.strip())
        return (m.group(1), int(m.group(2))) if m else None
    return None


@dataclass(frozen=True)
class Cut:
    """Where one block of his sits in the file, so that it can be edited or removed without touching anything else
    (``serve/blocks.py``). Offsets are into the section the block was parsed from, then into the whole file.

    ``unit`` is what deleting the block removes: its hidden key line, its heading and its body. ``text`` is what an
    edit replaces: his words, nothing the engine wrote (not the heading, the key line, the audio embed or a
    ``↩ replying to`` callout). ``lead`` and ``tail`` go around the new words (an empty body has no blank lines to keep);
    ``dash`` is a conversation message written ``**23:44** — words``, whose words follow the dash on the same line unless
    they open like Markdown does (``blocks.dash_lead``). ``refuse`` says why this block cannot be changed in place."""
    unit: tuple[int, int]
    text: tuple[int, int]
    lead: str = ""
    tail: str = ""
    dash: bool = False
    refuse: str | None = None

    def at(self, base: int) -> "Cut":
        return replace(self, unit=(self.unit[0] + base, self.unit[1] + base), text=(self.text[0] + base, self.text[1] + base))


def _key_start(section: str, at: int) -> int | None:
    """Where the ``%% journal phase:N %%`` / ``interrupt:N`` line that keys the heading at ``at`` starts (the line
    ``_answer_key_before`` reads), else None."""
    head = section[:at].rstrip()
    start = head.rfind("\n") + 1
    return start if _ANSWER_KEY.match(head[start:].strip()) else None


def _plain_cut(text: str, lo: int, hi: int, unit: tuple[int, int], embed: bool = True,
               photo_of: str | None = None) -> Cut:
    """The cut of a block whose words are the body ``text[lo:hi]`` less its audio embed (``embed``) and, for a photo
    block (``photo_of`` is the entry's stem), the pictures it opens with: the words sit after them, with the blank lines
    around them kept as they are. A body with words above its embed, or with one of the engine's own marker lines
    inside it, is not edited in place (the document shows both as one text)."""
    region = text[lo:hi]
    refuse = "marks" if entries.ENGINE_MARK.search(region) else None
    pre = lo
    m = next(_audio_embeds(region), None) if embed else None
    if m:
        pre = lo + m.end()
        if region[:m.start()].strip():
            refuse = refuse or "complex"
    if photo_of is not None:
        pre = max(pre, lo + _pictures_end(text[lo:hi], photo_of))
    rest = text[pre:hi]
    a, b = len(rest) - len(rest.lstrip("\n")), len(rest.rstrip())
    if b <= a:   # no words yet: the blank lines are replaced by the ones the engine writes
        return Cut(unit, (pre, hi), "\n\n", "\n\n", refuse=refuse)
    return Cut(unit, (pre + a, pre + b), refuse=refuse)


def _words_parts(section: str, stem: str | None = None) -> list[tuple[dict, Cut]]:
    heads = list(_WORDS_HEAD.finditer(section))
    starts = [_key_start(section, m.start()) for m in heads]
    begins = [m.start() if k is None else k for m, k in zip(heads, starts)]   # a block's unit starts at its key line
    ends = begins[1:] + [len(section)]
    first = begins[0] if heads else len(section)
    out = []
    lead = _clean(section[:first])
    if lead and not _NO_WORDS.match(lead.strip()):
        keep = min(1, len(section) - len(section.lstrip("\n")))   # (the blank line under the heading stays)
        out.append(({"kind": "his", "key": "words:0", "at": None, "source": "text", "text": lead, "audio": None,
                     "phase": None}, _plain_cut(section, 0, first, (keep, first), embed=False)))
    for i, m in enumerate(heads):
        source, meta = _source_meta(m.group(3))
        name, text = _embed(section[m.end():ends[i]])
        pics, text = _pictures(text, stem) if source == "photo" else ([], text)
        which, n = _answer_key_before(section, m.start()) or (None, None)
        block = {"kind": "his", "key": f"words:{len(out)}", "at": " ".join(x for x in m.group(1, 2) if x),
                 "source": source, "text": _clean(text), "audio": name, "phase": n if which == "phase" else None}
        if pics:
            block["images"] = pics
        if which == "interrupt":   # a one-off question of session 10, not a phase: the client labels it (API.md §1)
            block["interrupt"] = n
        if meta:
            block["meta"] = meta
        out.append((block, _plain_cut(section, m.end(), ends[i], (begins[i], ends[i]),
                                      photo_of=(stem or "") if source == "photo" else None)))
    return out


def words_blocks(section: str, stem: str | None = None) -> list[dict]:
    """His words section: one block per ``### HH:MM TZ · <source>`` heading (text before the first heading, in an
    older file, is a block without a time)."""
    return [block for block, _cut in _words_parts(section, stem)]


def _callout(text: str) -> tuple[str, list[str], str]:
    """A leading ``> [!kind]- title`` callout: (title line, body lines without the ``> ``, the text after it)."""
    lines = text.lstrip("\n").split("\n")
    n = 0
    while n < len(lines) and lines[n].startswith(">"):
        n += 1
    if n == 0:
        return "", [], text
    body = [l[2:] if l.startswith("> ") else l[1:] for l in lines[1:n]]
    return lines[0], body, "\n".join(lines[n:])


def _after_callout(text: str) -> int:
    """Where the text after ``_callout``'s callout starts in ``text`` (0 when there is none)."""
    lines = text.lstrip("\n").split("\n")
    n = 0
    while n < len(lines) and lines[n].startswith(">"):
        n += 1
    if n == 0:
        return 0
    return min(len(text), len(text) - len(text.lstrip("\n")) + sum(len(l) + 1 for l in lines[:n]))


def _his_message_cut(section: str, lo: int, hi: int, key: str, reply_to: str | None,
                 unit: tuple[int, int], stem: str | None = None) -> tuple[dict, Cut]:
    """One of his conversation messages, ``section[lo:hi]``: ``**23:44** — words`` or ``**23:44** · voice
    transcript · 0:12`` + body; with the cut that edits or removes it."""
    text = section[lo:hi]
    stripped = text.strip("\n")
    first, _, rest = stripped.partition("\n")
    m = _HIS_HEAD.match(first.strip())
    at, source, meta = None, "text", {}
    marks = "marks" if entries.ENGINE_MARK.search(text) else None
    if m:
        at = m.group(1)
        pre = len(text) - len(text.lstrip("\n"))
        line_end = lo + pre + len(first)
        if m.group(4) is not None:
            source, meta = _source_meta(m.group(4))
            body = rest
            cut = _plain_cut(section, line_end, hi, unit, photo_of=(stem or "") if source == "photo" else None)
        else:
            body = (m.group(3) or "") + ("\n" + rest if rest else "")
            if m.group(2):   # words after the dash, or under it
                dash_end = lo + pre + (len(first) - len(first.lstrip())) + m.end(2)
                complex_ = "complex" if next(_audio_embeds(body), None) else None
                cut = Cut(unit, (dash_end, max(dash_end, lo + len(text.rstrip()))), dash=True, refuse=marks or complex_)
            else:   # a bare time marker: the words are the lines under it
                cut = _plain_cut(section, line_end, hi, unit)
    else:
        body = stripped
        cut = _plain_cut(section, lo, hi, unit)
    name, body = _embed(body)
    pics, body = _pictures(body, stem) if source == "photo" else ([], body)
    block = {"kind": "his", "key": key, "at": at, "source": source, "text": _clean(body), "audio": name,
             "reply_to": reply_to}
    if pics:
        block["images"] = pics
    if meta:
        block["meta"] = meta
    return block, cut


def _his_message(text: str, key: str, reply_to: str | None = None, stem: str | None = None) -> dict:
    """One of his conversation messages as a block, on its own (the parse without the cut)."""
    return _his_message_cut(text, 0, len(text), key, reply_to, (0, len(text)), stem)[0]


def _conversation_parts(section: str, stem: str | None = None) -> list[tuple[dict, Cut | None]]:
    """``## Conversation``: his messages and her folded replies, each opened by its hidden key line. Her reply is
    the callout right after a ``her:`` key line, nothing more (anything after it reads as his, as
    ``entries.strip_callouts`` reads it); the ``↩ replying to`` callout after a ``his:`` key line is his
    message's ``reply_to``. A reply of hers has no cut: it is not his to change."""
    marks = list(_MARK.finditer(section))
    segments = []   # (kind, key, text start, text end, where its unit starts)
    first = marks[0].start() if marks else len(section)
    if _clean(section[:first]):
        segments.append(("his", None, 0, first, min(1, len(section) - len(section.lstrip("\n")))))
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(section)
        segments.append((m.group(1), m.group(2).strip(), m.end(), end, m.start()))
    out: list[tuple[dict, Cut | None]] = []
    for kind, key, lo, hi, begin in segments:
        name = f"{kind}:{key}" if key else f"conv:{len(out)}"
        if kind == "her":
            title, body, rest = _callout(section[lo:hi])
            if title:
                at = title.rsplit(" · ", 1)[1].strip() if " · " in title else None
                out.append(({"kind": "hers", "key": name, "at": at, "text": "\n".join(body).strip("\n").rstrip()}, None))
            cut_at = lo + _after_callout(section[lo:hi])
            lo = cut_at
            begin = cut_at + (1 if section.startswith("\n", cut_at) else 0)   # (the blank line under her callout stays)
            name = f"conv:{len(out)}"
            if not _clean(section[lo:hi]):
                continue
        reply_to = None
        title, body, rest = _callout(section[lo:hi])
        if title.startswith("> [!info]") and "↩" in title:
            reply_to = " ".join(" ".join(body).split()) or None
            lo += _after_callout(section[lo:hi])
        if _clean(section[lo:hi]):
            out.append(_his_message_cut(section, lo, hi, name, reply_to, (begin, hi), stem))
    return out


def conversation_blocks(section: str, stem: str | None = None) -> list[dict]:
    return [block for block, _cut in _conversation_parts(section, stem)]


def block_parts(body: str, stem: str | None = None) -> list[tuple[dict, Cut | None]]:
    """Every block of the entry in the order the document shows them (his words, then the conversation), each with
    the ``Cut`` that finds it in ``body`` (None for a block of hers). Offsets are into ``body``."""
    out: list[tuple[dict, Cut | None]] = []
    span = entries.words_span(body)
    if span:
        out += [(b, c.at(span[0])) for b, c in _words_parts(body[span[0]:span[1]], stem)]
    conv = entries.conversation_span(body)
    if conv:
        out += [(b, c.at(conv[0]) if c else None) for b, c in _conversation_parts(body[conv[0]:conv[1]], stem)]
    return out


def blocks_of(body: str, stem: str | None = None) -> list[dict]:
    """The document's ``blocks`` for the entry text ``body`` (``stem``: the entry's, which says which pictures are the
    engine's own)."""
    return [block for block, _cut in block_parts(body, stem)]


def _notes(body: str) -> list[dict]:
    span = entries.notes_span(body)
    if not span:
        return []
    section = body[span[0]:span[1]]
    heads = list(_NOTE_HEAD.finditer(section))
    out = []
    for i, m in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(section)
        out.append({"at": m.group(1), "text": _clean(section[m.end():end])})
    if not heads and _clean(section):
        out.append({"at": None, "text": _clean(section)})
    return out


def _list(value: str | None) -> list[str]:
    return entries.parse_flow_list(value)


def _meta(fm: dict, lists: dict | None = None) -> dict:
    """The front-matter keys the engine owns for the web app (set by hand in v1): only those present. ``lists`` are
    the block lists of the same front matter (``entries.front_matter_lists``): Obsidian writes ``mood:`` then
    ``  - calm`` lines, which ``fm`` shows as an empty value, so the sheet would open empty and a save would wipe them."""
    out = {}
    for key in ("mood", "people"):
        items = _list(fm[key]) if fm.get(key) else (lists or {}).get(key)
        if items:
            out[key] = items
    if "starred" in fm:
        out["starred"] = fm["starred"].strip().lower() in ("true", "yes", "1")
    return out


def meta_of(path: Path) -> dict:
    """``mood`` / ``people`` / ``starred`` as the entry's front matter has them now (only those present)."""
    head = entries.read_head(path)
    return _meta(entries.front_matter(head), entries.front_matter_lists(head))


def _session(fm: dict, key: tuple | None) -> int | None:
    if key and key[1] is not None:
        return key[1]
    try:
        return int(fm["session"])
    except (KeyError, ValueError):
        return None


def _his_text(blocks: list[dict]) -> str:
    """What he wrote in these blocks, nothing the engine added: no block headings, clock markers, key lines or
    audio embeds (the blocks already left those out), and not the placeholder lines for audio with no transcript.
    Search and the week's word count read this, so ``IST`` in a heading is not a word of his."""
    texts = (b["text"].replace(audio.NOT_TRANSCRIBED, "").replace(audio.TRANSCRIBING, "").strip()
             for b in blocks if b["kind"] == "his")
    return "\n\n".join(t for t in texts if t)


# ── the cache of parsed files ────────────────────────────────────────────────

_CACHE: dict[str, tuple[int, int, dict]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 5000


def parsed(path: Path) -> dict | None:
    """What summaries and search need from one file, re-read only when its mtime or size changed."""
    try:
        st = path.stat()
    except OSError:
        return None
    with _CACHE_LOCK:
        hit = _CACHE.get(str(path))
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    key = entries.entry_key(path)
    if not key or entries.is_analysis(path):
        info = {"key": None}
    else:
        try:
            body = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        fm = entries.front_matter(body)
        words = entries.his_words(body)
        span = entries.words_span(body)
        blocks = words_blocks(body[span[0]:span[1]], path.stem) if span else []
        conv = entries.conversation_span(body)
        said = _his_text(blocks + (conversation_blocks(body[conv[0]:conv[1]], path.stem) if conv else []))
        h1 = _H1.search(body)
        info = {
            "key": key, "fm": fm, "title": h1.group(1).strip() if h1 else None,
            "first_line": today.first_line(words), "words": len(said.split()), "search": said.casefold(),
            "time": next((b["at"].split(" ")[0] for b in blocks if b.get("at")), None),
            "flags": {"annotated": bool(entries.annotations(body)), "has_conversation": bool(entries.conversation(body)),
                      "transcript_pending": audio.TRANSCRIBING in body or audio.NOT_TRANSCRIBED in words,
                      "audio": any(True for _ in _audio_embeds(body))},
            "meta": _meta(fm, entries.front_matter_lists(body)), "mtime": st.st_mtime,
        }
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()
        _CACHE[str(path)] = (st.st_mtime_ns, st.st_size, info)
    return info


def matches(path: Path, q: str) -> bool:
    """Case-insensitive substring over **his words only** (``entries.his_words``): her replies are never searched."""
    info = parsed(path)
    return bool(info and info.get("key")) and q.casefold() in info["search"]


def words_count(path: Path) -> int:
    info = parsed(path)
    return info.get("words", 0) if info and info.get("key") else 0


# ── practice details ─────────────────────────────────────────────────────────

def _practice(lib: library.Library | None, pid: str) -> library.Practice | None:
    if pid in entries.FREEFORM_IDS:
        return None
    if lib is not None and pid in lib.practices:
        return lib.practices[pid]
    return library.builtins().get(pid)


def practice_ref(cfg: Config, lib, pid: str) -> dict | None:
    """``{id, title, emoji}`` of the practice an entry answers; None for a /j note or a companion-started note."""
    if pid in entries.FREEFORM_IDS:
        return None
    p = _practice(lib, pid) or _program_of(lib, pid)
    return {"id": pid, "title": p.title if p else pid, "emoji": (p.emoji if p else "") or ""}


# ── the two shapes ───────────────────────────────────────────────────────────

def summary(cfg: Config, path: Path, lib=None) -> dict | None:
    """EntrySummary: ``{stem, date, kind, practice, program, session, time, title, emoji, first_line, flags, meta}``."""
    info = parsed(path)
    if not info or not info.get("key"):
        return None
    pid = info["key"][0]
    fm = info["fm"]
    p = _practice(lib, pid)
    emoji = (p.emoji if p else "") or ("" if pid != "note" else cfg.names.emoji)
    return {"stem": path.stem, "date": path.stem[:10], "kind": pid, "practice": pid if pid not in entries.FREEFORM_IDS
            else None, "program": fm.get("program") or None, "session": _session(fm, info["key"]),
            "time": info["time"], "title": info["title"] or entries.title_for(pid, cfg.names, p), "emoji": emoji,
            "first_line": info["first_line"], "flags": dict(info["flags"]), "meta": dict(info["meta"])}


def prompt_records(cfg: Config) -> dict[str, dict]:
    """Every ledger record by the stem of the entry it is for (its ``entry_path``, else its ``stem``); when two name
    one entry, the first sent. The ledger keeps recent weeks only, so an older entry has none."""
    found: dict[str, dict] = {}
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        stem = Path(p["entry_path"]).stem if p.get("entry_path") else p.get("stem")
        if not stem:
            continue
        if stem not in found or (p.get("sent_at") or "") < (found[stem].get("sent_at") or ""):
            found[stem] = p
    return found


def _prompt_record(cfg: Config, stem: str) -> dict | None:
    """The ledger record whose entry this is; None once pruned."""
    return prompt_records(cfg).get(stem)


def _reflection_for(cfg: Config, stem: str) -> str | None:
    """The day reflection that read this entry (its ``_From [[…]]`` line names it)."""
    for path in reversed(entries.analyses_on(cfg.root, stem[:10], "reflection")):
        try:
            m = _FROM.search(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if m and stem in _LINK.findall(m.group(1)):
            return path.stem
    return None


def sources_of(text: str) -> list[str]:
    """The stems an analysis file says it was written from (its ``_From [[a]], [[b]] · written …`` line)."""
    m = _FROM.search(text or "")
    return _LINK.findall(m.group(1)) if m else []


def _program_of(lib, name: str):
    """The program (or situational definition) ``name`` of the library, else of the packaged ones."""
    if lib is not None and name in lib.programs:
        return lib.programs[name]
    return library.builtin_programs().get(name)


def _phase_titles(lib, fm: dict, number: int | None) -> list[dict]:
    """``[{number, title}]`` of a program session entry's phases: the dividers name them."""
    session = _session_def(lib, fm, number)
    return [{"number": ph.number, "title": ph.title} for ph in session.phases] if session else []


def _session_def(lib, fm: dict, number: int | None):
    """The program's session ``number`` (the one the document reports: the name's, else the front matter's), or None
    when the program has no such session: a hand-edited ``session: 0`` or ``-1`` must not index from the end."""
    prog = _program_of(lib, fm.get("program") or "")
    return prog.session(number) if prog and number and 1 <= number <= len(prog.sessions) else None


def _phase_sections(lib, fm: dict, number: int | None) -> list[tuple[str, list]]:
    """A program session entry's questions, by phase (``Phase 2: Title``), from the program's own definition: such an
    entry answers a program, not a practice, so ``Practice.sections`` has nothing for it."""
    session = _session_def(lib, fm, number)
    return [(f"Phase {ph.number}: {ph.title}", list(ph.questions)) for ph in session.phases if ph.questions] if session else []


def _opener(cfg: Config, rec: dict | None) -> str | None:
    """The opener as sent, or None (``capture.stored_opener``: a narrow check, not a privacy pass; DECISIONS.md,
    WEB-DESIGN, the open point on the opener's sources)."""
    return capture.stored_opener(cfg, rec)


def prompt_of(cfg: Config, lib, pid: str, rec: dict | None, fm: dict | None = None,
              session_no: int | None = None) -> dict:
    """The prompt as he was sent it: when, the opener, and the question set, with the questions the opener wove in
    (``asked``). One shape for the entry's document and Today's rows. The questions are the snapshot the send record
    took (``library.question_snapshot``), which is what the prompt carried below the opener; a record from before
    snapshots has none, and then they are the practice's own as the library has them now (a program session's are its
    phases', read from the program: its phase records keep the text sent, not the question set)."""
    p = _practice(lib, pid)
    sent = library.snapshot_sections(rec)  # what the prompt carried; the library's today for a record that has none
    sections = list(sent) if sent else list(p.sections) if p else _phase_sections(lib, fm or {}, session_no)
    return {"sent_at_local": (rec or {}).get("sent_at_local"), "opener": _opener(cfg, rec),
            "opener_source": (rec or {}).get("opener_source"), "asked": list((rec or {}).get("asked") or []),
            "questions": [{"id": q.id, "text": q.text, "section": title} for title, qs in sections for q in qs],
            "phases": _phase_titles(lib, fm or {}, session_no) if not p and not sent else []}


def document(cfg: Config, path: Path, lib=None) -> dict:
    """The two-voice document for one engine entry (API.md §1)."""
    body = path.read_text(encoding="utf-8", errors="replace")
    stem = path.stem
    key = entries.entry_key(path) or (stem[11:], None, 1)
    pid = key[0]
    fm = entries.front_matter(body)
    summary_text = entries.summary_text(body) or None  # the engine's marked ``## Summary`` only, never his own line
    p = _practice(lib, pid)
    rec = _prompt_record(cfg, stem)
    session_no = _session(fm, key)
    prompt = None
    if pid not in entries.FREEFORM_IDS:
        prompt = prompt_of(cfg, lib, pid, rec, fm, session_no)
    blocks = blocks_of(body, stem)
    words = entries.his_words(body)
    h1 = _H1.search(body)
    st = path.stat()
    annotations = entries.annotations(body)
    return {
        "stem": stem, "date": stem[:10], "kind": pid, "practice": practice_ref(cfg, lib, pid),
        "program": fm.get("program") or None, "session": session_no, "run": fm.get("run") or None,
        "title": h1.group(1).strip() if h1 else entries.title_for(pid, cfg.names, p),
        "voices": {"his": cfg.names.user or "You", "hers": cfg.names.companion},
        "prompt": prompt, "blocks": blocks, "summary": summary_text,
        "analysis": {"annotations": annotations or None, "notes": _notes(body),
                     "reflection": _reflection_for(cfg, stem)},
        "flags": {"annotated": bool(annotations), "has_conversation": bool(entries.conversation(body)),
                  "transcript_pending": audio.TRANSCRIBING in body or audio.NOT_TRANSCRIBED in words,
                  "audio": any(True for _ in _audio_embeds(body))},
        "meta": _meta(fm, entries.front_matter_lists(body)), "path": str(path), "mtime": st.st_mtime,
        # whether the web app may add to it or set its meta (a file only named like an entry is his: writes are 409),
        # so the page does not offer a composer that would be refused after he has typed
        "writable": engine_file(cfg, stem) is not None,
    }


def references(cfg: Config, name: str) -> bool:
    """Whether an engine entry embeds the media file ``name`` (``![[name]]``). Kept audio is named after its entry
    (``<stem>.ogg``, ``<stem>-2.m4a``), so only that day's entries are read."""
    if not re.match(r"^\d{4}-\d{2}-\d{2}-", name):
        return False
    needle = f"![[{name}]]"
    for path in cfg.root.glob(f"{name[:10]}-*.md"):
        info = parsed(path)
        if not info or not info.get("key"):
            continue
        try:
            if needle in path.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False
