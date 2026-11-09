"""Search his words in the journal: what the companion's ``journal_search`` tool runs, in chat and in the reflection
call (README *Tools*).

On the record by construction: it reads the journal folder's dated files only, and of each only what is his
(``entries.his_text``: an entry's words section and his conversation lines, a dated file of his own whole), never
her annotations, notes, folded replies, reflections, week reviews or the engine's summaries. It never opens the chat
transcript, a context pack, the notebook or the companion's memory, and it writes nothing. Standard library only.

A query is words (an entry matches any of them, as a word or the start of one: ``walk`` finds "walked"), and
"phrases in double quotes" that must appear as written. With three or more of them, an entry must match at least
half. Entries with more matches come first, then the newest.
"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from . import entries
from .config import Config

LIMIT = 8            # entries a search returns unless asked for fewer or more
MAX_LIMIT = 20
QUERY_LIMIT = 200    # characters of a query (as ``/entries?q`` and MCP's ``journal_entries``)
MAX_NEEDLES = 8      # words and phrases of a query that count
EXCERPTS = 2         # excerpts per entry
WINDOW = 110         # characters kept either side of a match in an excerpt
_DATED = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]*.md"
_STOP = frozenset("""a an and are as at be but by did do for from had has have he her his i if in is it its me my
no not of on or our she so that the their them then there they this to was we were what when where which who why
with you your""".split())
_EMBED = re.compile(r"!\[\[[^\]\n]*\]\]")
_TYPO = {"'": "['’‘]", '"': '["“”]', "-": "[-–—]"}


class SearchError(ValueError):
    """A query the search cannot run (empty, too long, a date that is not YYYY-MM-DD)."""


def files(cfg: Config) -> list[Path]:
    """The journal's dated Markdown files, oldest name first (entries, analyses and his own dated files alike:
    ``entries.his_text`` decides what of each is his)."""
    root = Path(cfg.root)
    return sorted(root.glob(_DATED)) if root.is_dir() else []


def his_words_everywhere(cfg: Config) -> str:
    """Every word of his in the journal, file after file: what a quote reached through search is checked against."""
    out = []
    for p in files(cfg):
        try:
            text = entries.his_text(p)
        except OSError:
            continue
        if text:
            out.append(text)
    return "\n\n".join(out)


def _needles(query: str) -> list[tuple[str, re.Pattern]]:
    """(the word or phrase as asked, its pattern): phrases in double quotes first, then single words."""
    out: list[tuple[str, re.Pattern]] = []
    for phrase in re.findall(r"[\"“]([^\"“”]+)[\"”]", query):
        words = phrase.split()
        if words:
            body = r"\s+".join("".join(_TYPO.get(c, re.escape(c)) for c in w) for w in words)
            out.append((" ".join(words), re.compile(r"(?<!\w)" + body + r"(?!\w)", re.I)))
    rest = re.sub(r"[\"“][^\"“”]*[\"”]", " ", query)
    seen = {label.lower() for label, _ in out}
    for w in re.findall(r"\w+", rest.lower()):
        if len(w) < 2 or w in _STOP or w in seen:
            continue
        seen.add(w)
        out.append((w, re.compile(r"(?<!\w)" + re.escape(w), re.I)))
    return out[:MAX_NEEDLES]


def _date(value, name: str) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value).strip()).isoformat()
    except ValueError:
        raise SearchError(f"{name} must be a date, YYYY-MM-DD") from None


def _excerpt(text: str, start: int, end: int) -> str:
    lo, hi = max(0, start - WINDOW), min(len(text), end + WINDOW)
    if lo > 0:
        cut = text.find(" ", lo, start)
        lo = cut + 1 if cut >= 0 else lo
    if hi < len(text):
        cut = text.rfind(" ", end, hi)
        hi = cut if cut >= 0 else hi
    return ("… " if lo > 0 else "") + text[lo:hi].strip() + (" …" if hi < len(text) else "")


def _clean(text: str) -> str:
    """His words as one line of prose for excerpts: no embedded audio or pictures, no bold markers, single spaces."""
    return " ".join(_EMBED.sub(" ", text).replace("**", "").split())


def search(cfg: Config, query: str, date_from=None, date_to=None, limit=None) -> dict:
    """``{"query", "results": [{"entry", "date", "kind", "matched", "excerpts"}], "more"}``: entries whose words
    match, best first. Raises ``SearchError`` for a query it cannot run."""
    q = " ".join(str(query or "").split())
    if not q:
        raise SearchError("query is empty: give words or a phrase in double quotes")
    if len(q) > QUERY_LIMIT:
        raise SearchError(f"query is at most {QUERY_LIMIT} characters")
    needles = _needles(q)
    if not needles:
        raise SearchError("query has no words to look for (only very common ones)")
    start, end = _date(date_from, "date_from"), _date(date_to, "date_to")
    try:
        want = LIMIT if limit in (None, "") else max(1, min(MAX_LIMIT, int(limit)))
    except (TypeError, ValueError):
        raise SearchError("limit is a whole number") from None
    need = 1 if len(needles) < 3 else (len(needles) + 1) // 2
    found = []
    for path in files(cfg):
        day = path.name[:10]
        if (start and day < start) or (end and day > end):
            continue
        try:
            text = _clean(entries.his_text(path))
        except OSError:
            continue
        if not text:
            continue
        hits = [(label, m) for label, rx in needles if (m := rx.search(text))]
        if len(hits) < need:
            continue
        excerpts, spans = [], []
        for _, m in hits:
            if len(excerpts) >= EXCERPTS:
                break
            if any(lo <= m.start() < hi for lo, hi in spans):
                continue
            spans.append((m.start() - WINDOW, m.end() + WINDOW))
            excerpts.append(_excerpt(text, m.start(), m.end()))
        found.append((len(hits), path.stem, {"entry": path.stem, "date": day, "kind": entries.kind_of(path) or "his own",
                                             "matched": [label for label, _ in hits], "excerpts": excerpts}))
    found.sort(key=lambda f: (f[0], f[1]), reverse=True)
    return {"query": q, "results": [f[2] for f in found[:want]], "more": max(0, len(found) - want)}
