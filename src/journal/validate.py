"""Grounding check for model-written openers.

The opener is the only model text in a prompt. It may reference the user's life
only through citations; citations from the journal or Telegram must quote
text that actually exists in the context pack. Anything else falls back to
the plain opener, so a weak or confused model degrades to "plain", never to
"invented".

What counts as evidence of what the user said is only his own words: his entries and his Telegram
messages, plus the companion's quoted analysis. The companion's own journal messages (openers,
annotation replies, reflections) appear in the pack's conversation section so it sees what it already
said, but those lines (``MINE``) are not citable: ``check`` ignores them, and ``context.make_verifier``
leaves openers and the journal's mirrored sends out of the records it checks quotes against.
"""
from __future__ import annotations

import json
import re

from . import library
from .agent import is_failure_text
from .library import Practice

CLAIM_MARKERS = re.compile(
    r"\b(you (mentioned|said|wrote|told|shared|had|were|did|have been|'ve been|landed|flew)"
    r"|yesterday|last night|earlier today|this morning you|you're (still|back)"
    r"|bus run|your shift)\b",
    re.I,
)
VERIFIABLE = ("journal:", "telegram:", "chat:", "reflection:", "week:")  # reflection/week: hers, in the pack
# his words: checked against his records only (``verify``), never the pack's text. ``chat:`` is his chat on a platform
# other than Telegram (``channel.cite``); both are the same check.
HIS = ("journal:", "telegram:", "chat:")
STANDING = "standing"  # the pack's "Where things stand" facts, cited as such: checked against that section only
STANDING_HEADING = "## Where things stand"
STANDING_GROUNDS = 12  # characters a standing quote needs before it can ground a claim about him
HIS_KINDS = ("journal", "telegram", "chat")
_TOKEN = re.compile(r"[\w.-]+")
CONVERSATION_HEADING = "## Recent conversation"  # his messages and hers, interleaved
MINE = "- [you] "  # a pack line holding the companion's own words: never evidence
# The four built-ins' ``opener.limit`` (``tests/test_golden.py`` holds the files to it); ``check`` reads the
# practice's own limit.
LIMITS = {"morning": 1400, "evening": 1400, "midweek": 900, "weekly": 2200}


def norm(s: str) -> str:
    return _norm(s)


def citable(pack_text: str) -> str:
    """The pack without the companion's own words (``MINE`` lines): what a citation may be checked against."""
    return "".join(l for l in (pack_text or "").splitlines(keepends=True) if not l.startswith(MINE))


def parse_source(source: str) -> tuple[str, str]:
    """(kind, the rest) of a citation's source: the kind lowercased and stripped (``Journal:`` and `` journal:``
    are ``journal``), the rest stripped. A source with no colon is all kind (``standing``)."""
    kind, sep, rest = str(source or "").strip().partition(":")
    return kind.strip().lower(), rest.strip() if sep else ""


def source_stem(rest: str) -> str:
    """The file a ``journal:`` source names: its leading ``[\\w.-]+`` token without ``.md``, so the heading the pack
    shows (``2026-10-21-evening (Wed 21 Oct, 5 days ago)``) works when copied whole; "" when there is none."""
    m = _TOKEN.match(rest or "")
    return m.group(0).removesuffix(".md") if m else ""


def fold(s: str) -> str:
    """A quote as compared: curly and straight quotes, dashes and spaces folded, case and whitespace normalised, and
    leading and trailing punctuation dropped (what the reflection checker does with a quoted passage)."""
    return _plain(s).strip(" .,;:!?'\"-")


def standing_section(pack_text: str) -> str:
    """The pack's "Where things stand" section (code's facts, no model), or ""."""
    text = pack_text or ""
    at = text.find(STANDING_HEADING + "\n")
    if at < 0:
        return ""
    end = text.find("\n## ", at + len(STANDING_HEADING))
    return text[at:end if end >= 0 else len(text)]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def parse(raw: str) -> dict | None:
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def check(practice: Practice | str, data: dict | None, pack_text: str, verify=None) -> tuple[bool, str]:
    """Return (ok, reason) for a model-written opener of ``practice`` (its ``opener.limit`` caps the length).

    `verify(source, quote) -> bool | None` checks journal/Telegram quotes (and ``brain:`` paths inside the journal)
    against his words in the real records (``context.make_verifier``), so the companion can cite what she read
    beyond the pack and her own words never pass as his; with it, the pack's text is not consulted for those
    sources. Without it (tests): the pack only. Source names are read case- and space-insensitively; a
    journal-like or telegram-like name the check doesn't know is refused (fail closed). ``standing`` citations
    are checked against the pack's "Where things stand" section. A claim about him (``CLAIM_MARKERS``) needs one
    citation that grounds it: his words that verified, a standing fact of at least ``STANDING_GROUNDS``
    characters, or a named fact of another source (a book, the weather, a task); her analyses and a scrap of
    the standing section do not.
    """
    limit = library.resolve(practice).opener["limit"]
    if not data:
        return False, "unparseable"
    opener = data.get("opener")
    cites = data.get("citations") or []
    if not isinstance(opener, str) or not opener.strip():
        return False, "empty opener"
    if is_failure_text(opener):
        return False, "agent error text"
    if len(opener) > limit:
        return False, f"too long ({len(opener)})"
    if len(re.findall(r"(?m)^\s*\d+[.)]\s", opener)) >= 6:
        return False, "pastes the question set"
    if not isinstance(cites, list):
        return False, "citations not a list"
    pack = _norm(citable(pack_text))
    grounded = False  # a claim about him needs one citation that really grounds it (see below)
    for c in cites:
        if not isinstance(c, dict) or not str(c.get("quote", "")).strip():
            return False, "citation without quote"
        src = str(c.get("source", ""))
        kind, _rest = parse_source(src)
        q = _norm(str(c["quote"]))
        if kind in (*HIS_KINDS, "brain") and verify is not None:
            ok = verify(src, q)  # his words in that source: her review, her notes or the pack's own prose never pass
            if ok is None:  # a brain: path outside the journal: not his journal, not checked (as before)
                continue
            grounded = grounded or ok
        elif kind in HIS_KINDS:
            ok = fold(q) in _plain(pack)  # (no verifier: tests)
            grounded = grounded or ok
        elif kind in ("reflection", "week"):
            ok = q in pack  # hers: checked, but her analysis never grounds a claim about him
        elif kind == STANDING:
            ok = bool(fold(q)) and fold(q) in _plain(standing_section(pack_text))
            grounded = grounded or (ok and len(fold(q)) >= STANDING_GROUNDS)
        elif kind.startswith(HIS_KINDS) or kind.startswith(STANDING):
            return False, f"unrecognised source {src.strip()[:60]!r}"  # fail closed: Journals:, telegram-chat:, …
        else:
            grounded = True  # a named fact (a book, the weather, a task): it grounds a question, as the request says
            continue
        if not ok:
            return False, f"unverified quote from {src}"
    if CLAIM_MARKERS.search(opener) and not grounded:
        return False, "claim without citation"
    return True, "ok"


def asked(practice: Practice | str, data: dict) -> list[dict]:
    """The bank questions the companion wove in, keeping only ids that exist (bad ids are dropped, not fatal)."""
    items = library.resolve(practice).items()
    out = []
    for a in data.get("asked") or []:
        if isinstance(a, dict) and str(a.get("id")) in items:
            out.append({"id": str(a["id"]), "question": items[str(a["id"])], "as": str(a.get("as", "")).strip()})
    return out


# ── reflections ──────────────────────────────────────────────────────────────
# A reflection is free Markdown, so grounding is checked on what it quotes: every passage in double
# quotes (straight or curly) and every blockquote line must appear in the supplied inputs. A program's
# synthesis (``insights``), the catch-up and a program's construction and bridge reject the whole text
# on one that doesn't. A day reflection or week review is checked against his words alone, file by
# file (``unfound``): one that fails is a bad answer and is retried, and only on its last attempt does
# ``strike`` drop the sentences holding such quotes so the reflection is not lost (``reflect.write_one``).

REFLECTION_LIMITS = {"reflection": 3500, "week": 6000, "insights": 5000}  # insights: a program's synthesis
QUOTE_MIN = 4  # characters inside a pair of quotes (stripped) before it is checked: "No" or "ok" is no passage
_CURLY = re.compile(r"“([^“”]*)”")
_TYPO = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": "-", "–": "-", " ": " "})
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*")
_SENT_END = re.compile(r"[.!?…]+[\"”’')\]*_]*(?=\s|$)")
_LEAD = re.compile(r"[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?(?:(?:\*\*[^*\n]+\*\*|__[^_\n]+__)[ \t]*[—–:-]?[ \t]*)?")


def _plain(s: str) -> str:
    return _norm((s or "").translate(_TYPO))


def _quotations(text: str) -> tuple[list[tuple[int, int, str]], list[int]]:
    """Every pair of double quotes in ``text`` as (start, end, inside), and where the marks that pair with nothing
    are. Curly pairs first (they may span lines); then the straight marks outside them, paired left to right on each
    line at any length, so a short quote ("No") never shifts the pairs after it. A line's odd straight mark, and a
    curly mark with no partner, pair with nothing."""
    text = text or ""
    pairs = [(m.start(), m.end(), m.group(1)) for m in _CURLY.finditer(text)]

    def inside(i: int) -> bool:
        return any(a <= i < b for a, b, _ in pairs)
    stray = [i for i, c in enumerate(text) if c in "“”" and not inside(i)]
    straight = []
    pos = 0
    for line in text.splitlines(keepends=True):
        marks = [pos + i for i, c in enumerate(line) if c == '"' and not inside(pos + i)]
        straight += [(a, b + 1, text[a + 1:b]) for a, b in zip(marks[::2], marks[1::2])]
        if len(marks) % 2:
            stray.append(marks[-1])
        pos += len(line)
    return sorted(pairs + straight), sorted(stray)


def quotes(text: str) -> list[str]:
    """Passages a reflection presents as quoted: what is inside each pair of double quotes (``_quotations``; at
    least ``QUOTE_MIN`` characters) and each blockquote line."""
    pairs, _ = _quotations(text)
    out = [inner.strip() for _, _, inner in pairs if len(inner.strip()) >= QUOTE_MIN]
    out += [l.lstrip("> ").strip() for l in (text or "").splitlines() if l.startswith(">") and l.lstrip("> ").strip()]
    return out


def _missing(q: str, haystack: str) -> str | None:
    """The first fragment of quoted passage ``q`` not in ``haystack`` (already ``_plain``), or None."""
    for part in _ELLIPSIS.split(q):  # "I … think": each fragment must be there
        frag = _plain(part).strip(" .,;:!?'\"-")
        if len(frag) >= 3 and frag not in haystack:
            return part.strip()[:60]
    return None


def _misquote(text: str, corpus: str) -> str | None:
    """The first quoted passage of ``text`` that is not in ``corpus`` (None: every quote is real)."""
    haystack = _plain(corpus)
    for q in quotes(text):
        bad = _missing(q, haystack)
        if bad is not None:
            return bad
    return None


def drop_stray_marks(text: str) -> tuple[str, int]:
    """``text`` without the quote marks that pair with nothing (``_quotations``), and how many there were: a mark
    with no partner cannot be checked, so it never stands."""
    _, stray = _quotations(text)
    for i in reversed(stray):
        text = text[:i] + text[i + 1:]
    return text, len(stray)


def _fragments(q: str) -> list[str]:
    return [f for f in (_plain(part).strip(" .,;:!?'\"-") for part in _ELLIPSIS.split(q)) if len(f) >= 3]


def _in_one(frags: list[str], docs: list[str]) -> bool:
    """Whether one of ``docs`` (already ``_plain``) holds every fragment, in order."""
    for doc in docs:
        at = 0
        for f in frags:
            j = doc.find(f, at)
            if j < 0:
                break
            at = j + len(f)
        else:
            return True
    return False


def unfound(text: str, docs: list[str], wider=None) -> list[tuple[int, int, str]]:
    """The quoted passages of ``text`` that are not his words, as (start, end, passage): each pair of quotes with at
    least ``QUOTE_MIN`` characters inside, and each blockquote line (its span the whole line), that no single one of
    ``docs`` holds with its fragments around "…" in order. ``wider`` (a callable returning more documents, called at
    most once and only when a passage is missing from ``docs``) is his words in every file of the journal, for what a
    reflection reached through ``journal_search``: a quote stitched from several files is not his."""
    plain = [_plain(d) for d in docs if d]
    more: list[str] | None = None

    def found(q: str) -> bool:
        nonlocal more
        frags = _fragments(q)
        if not frags or _in_one(frags, plain):
            return True
        if wider is None:
            return False
        if more is None:
            more = [_plain(d) for d in (wider() or []) if d]
        return _in_one(frags, more)

    text = text or ""
    pairs, _ = _quotations(text)
    out = [(a, b, inner.strip()) for a, b, inner in pairs if len(inner.strip()) >= QUOTE_MIN and not found(inner)]
    pos = 0
    for line in text.splitlines(keepends=True):
        q = line.lstrip("> ").strip() if line.startswith(">") else ""
        if q and not found(q):
            out.append((pos, pos + len(line), q))
        pos += len(line)
    return sorted(out)


def _ends(text: str, lo: int, hi: int, pairs: list[tuple[int, int, str]]) -> list[int]:
    """Where sentences end in ``text[lo:hi]``: after a full stop (with its closing marks) that is not inside a
    quotation and is followed by the line's end or a new sentence (a capital, a quote mark, emphasis)."""
    out = []
    for m in _SENT_END.finditer(text, lo, hi):
        if any(a <= m.start() < b for a, b, _ in pairs):
            continue
        rest = text[m.end():].lstrip(" \t")
        if not rest or rest[0] in "\n\"“*_(" or rest[0].isupper():
            out.append(m.end())
    return out


def _sentence(text: str, a: int, b: int, inner: str, pairs: list[tuple[int, int, str]]) -> tuple[int, int]:
    """The span of the sentence holding the quote at ``text[a:b]``: from the last sentence end before it (or the line's
    start, after a list marker and a bold lead-in such as ``**What I notice** —``) to the end of the sentence it is
    in (the quote's own end when it closes on a full stop and a new sentence, or the line's end, follows). Full stops
    inside other quotations of the sentence are not its end (``_ends``)."""
    ls = text.rfind("\n", 0, a) + 1
    le = text.find("\n", b)
    le = len(text) if le < 0 else le
    start = ls
    lead = _LEAD.match(text, ls)
    if lead and lead.end() <= a:
        start = lead.end()
    start = max([start, *_ends(text, start, a, pairs)])
    rest = text[b:le].lstrip(" \t*_")
    if inner.rstrip()[-1:] in (".", "!", "?", "…") and (not rest or rest[0].isupper()):
        return start, b
    after = _ends(text, b, le, pairs)
    return start, after[0] if after else le


def strike(text: str, problems: list[tuple[int, int, str]]) -> str:
    """``text`` without what ``unfound`` found: a blockquote line goes whole, any other quote goes with the sentence
    that holds it, its lead-in ("you wrote", "as you put it") included, so no words that are not his stay attributed
    to him, quoted or reported. A bold lead-in at the head of a line stays."""
    pairs, _ = _quotations(text)
    spans = []
    for a, b, inner in problems:
        line_start = a == 0 or text[a - 1] == "\n"
        spans.append((a, b) if text[a:a + 1] == ">" and line_start else _sentence(text, a, b, inner, pairs))
    merged: list[list[int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in reversed(merged):
        text = text[:a] + text[b:]
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    text = "\n".join(l.rstrip() for l in text.splitlines())
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def check_reflection(kind: str, text: str | None, corpus: str, misquotes: bool = True) -> tuple[bool, str]:
    """(ok, reason) for a model-written day reflection or week review against its inputs (``misquotes=False``: the
    quotes were checked already, by ``unfound``)."""
    text = (text or "").strip()
    if len(text) < 80:
        return False, "empty or too short"
    if text.startswith("{") or text.startswith("```"):
        return False, "not prose"
    limit = REFLECTION_LIMITS[kind]
    if len(text) > limit:
        return False, f"too long ({len(text)} > {limit})"
    bad = _misquote(text, corpus) if misquotes else None
    if bad is not None:
        return False, f"quote not in the entries: {bad!r}"
    return True, "ok"


CATCHUP_LIMIT = 1500
_TECHNICAL = re.compile(r"\b(model|provider|server|api|endpoint|error|outage|gateway|hermes)\b", re.I)


def check_catchup(text: str | None, corpus: str) -> tuple[bool, str]:
    """(ok, reason) for the message sent after an outage: short plain prose, no failure text, no technical
    account of the outage, and every quote taken from his messages or the conversation it was shown."""
    text = (text or "").strip()
    if len(text) < 10:
        return False, "empty or too short"
    if text.startswith(("{", "```", "#")):
        return False, "not a chat message"
    if is_failure_text(text):
        return False, "agent error text"
    if len(text) > CATCHUP_LIMIT:
        return False, f"too long ({len(text)} > {CATCHUP_LIMIT})"
    m = _TECHNICAL.search(text)
    if m and m.group(0).lower() not in _plain(corpus).split():
        return False, f"technical detail ({m.group(0)!r})"
    bad = _misquote(text, corpus)
    if bad is not None:
        return False, f"quote not in his messages: {bad!r}"
    return True, "ok"


# ── program phases ───────────────────────────────────────────────────────────

REFLECT_PHASE_LIMIT = 900
BRIDGE_LIMIT = 400


def check_reflect_phase(text: str | None, answers: str) -> tuple[bool, str]:
    """(ok, reason) for a reflect phase's construction: prose, within 900 characters, not Hermes' failure copy, and
    every quote taken from his answers in this session (``answers``)."""
    text = (text or "").strip()
    if len(text) < 20:
        return False, "empty or too short"
    if text.startswith(("{", "```", "#")):
        return False, "not prose"
    if is_failure_text(text):
        return False, "agent error text"
    if len(text) > REFLECT_PHASE_LIMIT:
        return False, f"too long ({len(text)} > {REFLECT_PHASE_LIMIT})"
    bad = _misquote(text, answers)
    if bad is not None:
        return False, f"quote not in his answers: {bad!r}"
    return True, "ok"


def check_bridge(text: str | None, last_answer: str) -> tuple[bool, str]:
    """(ok, reason) for a bridge: one short paragraph, no question, quotes only from his last answer."""
    text = (text or "").strip()
    if len(text) < 10:
        return False, "empty or too short"
    if "\n\n" in text or text.startswith(("{", "```", "#", "-", "*")):
        return False, "not one paragraph"
    if is_failure_text(text):
        return False, "agent error text"
    if len(text) > BRIDGE_LIMIT:
        return False, f"too long ({len(text)} > {BRIDGE_LIMIT})"
    if "?" in text:
        return False, "asks a question"
    bad = _misquote(text, last_answer)
    if bad is not None:
        return False, f"quote not in his answer: {bad!r}"
    return True, "ok"
