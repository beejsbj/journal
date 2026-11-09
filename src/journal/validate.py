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
# A reflection is free Markdown, so grounding is checked on what it quotes, and it fails closed. Only
# well-formed double quotation counts as quotation: a straight "…" or curly “…” pair on one line whose
# inside is not empty and neither starts nor ends with a space, and a blockquote line. Every other
# quote-like mark (``_scan``: an unpaired or mixed mark, ”…“, „…“, a quote inside a quote, single
# quotes used as quotation, «…», backticks) is irregular: it cannot be checked, so it never passes.
# A day reflection or week review checks each quotation against his words, file by file and paragraph
# by paragraph (``unfound``); one problem makes the answer a bad one, retried, and only on its last
# attempt does ``strike`` drop the sentences that hold the problems (``reflect.write_one``). A program's
# synthesis (``insights``), the catch-up and a program's construction and bridge reject the whole text
# on one (``_misquote``).

REFLECTION_LIMITS = {"reflection": 3500, "week": 6000, "insights": 5000}  # insights: a program's synthesis
QUOTE_MIN = 4  # characters inside a pair before ``_misquote`` checks it (the program and catch-up checks, as before)
QUOTE_GAP = 300  # characters of his at most between two parts of one quote stitched with "…"
_DOUBLE = '"“”„‟«»‹›'  # every double-style quote mark (a pair is "…" or “…”; the rest never pair)
_ODD = "„‟«»‹›‘‚`"  # marks that never quote in a reflection: irregular wherever they stand
_TYPO = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": "-", "–": "-", " ": " "})
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*")
_SENT_END = re.compile(r"[.!?…]+[\"”’')\]*_]*(?=\s|$)")
_LEAD = re.compile(r"[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]+)?(?:(?:\*\*[^*\n]+\*\*|__[^_\n]+__)[ \t]*[—–:-]?[ \t]*)?")
_ONLY_LEAD = re.compile(r"[ \t]*(?:(?:[-*+]|\d+[.)])[ \t]*)?(?:(?:\*\*[^*\n]+\*\*|__[^_\n]+__)[ \t]*[—–:-]?[ \t]*)?")
_BLOCK = re.compile(r"[ \t]*(?:[-*+][ \t]|\d+[.)][ \t]|>|#|\*\*[^*\n]+\*\*[ \t]*[—–:-])")  # a line that starts a block
_ABBREVIATIONS = ("e.g.", "i.e.", "etc.", "vs.", "cf.", "mr.", "mrs.", "ms.", "dr.", "st.", "approx.", "a.m.", "p.m.")
_OPENS_SINGLE = re.compile(r"(?:^|(?<=[\s(\[{—–/-]))'(?=\S)")  # a straight single quote opening a quotation


def _plain(s: str) -> str:
    return _norm((s or "").translate(_TYPO))


def _scan(text: str) -> tuple[list[tuple[int, int, str]], list[int]]:
    """(the well-formed quotations of ``text`` as (start, end, inside), where every irregular quote mark is). A
    quotation is a straight "…" or curly “…” pair on one line, closed by the next double mark on that line, with an
    inside that is not empty and does not start or end with whitespace. Anything else is irregular: a double mark
    that opens no such pair (and the mark that closes it wrongly: a mixed pair, a quote inside a quote, a quote
    across a line break), ” „ ‟ « » ‹ › where they stand, a single quote used as quotation (‘ and ‚ anywhere; a
    straight ' that opens a word, such as 'like this'; ’ and a ' inside a word are apostrophes), and a backtick."""
    text = text or ""
    pairs: list[tuple[int, int, str]] = []
    odd: list[int] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        i = 0
        while i < len(body):
            c = body[i]
            if c in '"“':
                j = next((k for k in range(i + 1, len(body)) if body[k] in _DOUBLE), None)
                inner = body[i + 1:j] if j is not None else ""
                if j is not None and body[j] == ('"' if c == '"' else "”") and inner and inner == inner.strip():
                    pairs.append((pos + i, pos + j + 1, inner))
                    odd += [pos + i + 1 + k for k, ch in enumerate(inner) if ch in _ODD]
                    odd += [pos + i + 1 + m.start() for m in _OPENS_SINGLE.finditer(inner)]
                else:
                    odd += [pos + i] + ([pos + j] if j is not None else [])
                i = (j if j is not None else i) + 1
                continue
            if c in _DOUBLE or c in _ODD or (c == "'" and _OPENS_SINGLE.match(body, i)):
                odd.append(pos + i)
            i += 1
        pos += len(line)
    return pairs, sorted(set(odd))


def quotes(text: str) -> list[str]:
    """Passages a text presents as quoted: the inside of each well-formed quotation (``_scan``) and each blockquote
    line."""
    pairs, _ = _scan(text)
    out = [inner for _, _, inner in pairs]
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
    """The first problem with what ``text`` quotes (None: there is none): an irregular quote mark (``_scan``), which
    cannot be checked, or a quoted passage of at least ``QUOTE_MIN`` characters not in ``corpus``."""
    pairs, odd = _scan(text)
    if odd:
        return f"irregular quote mark {text[odd[0]]!r}"
    haystack = _plain(corpus)
    for q in [inner for _, _, inner in pairs if len(inner) >= QUOTE_MIN] + quotes(text)[len(pairs):]:
        bad = _missing(q, haystack)
        if bad is not None:
            return bad
    return None


def _patterns(q: str) -> list[re.Pattern] | None:
    """A quoted passage as the patterns its parts around "…" must match, folded as the check compares them: a part of
    three characters or more anywhere, a shorter one only as a whole word. None when no part has three characters:
    there is nothing in it to check ("Go … be … it", "No")."""
    parts = [f for f in (_plain(p).strip(" .,;:!?'\"-") for p in _ELLIPSIS.split(q)) if f]
    if not any(len(f) >= 3 for f in parts):
        return None
    return [re.compile(re.escape(f) if len(f) >= 3 else r"(?<!\w)" + re.escape(f) + r"(?!\w)") for f in parts]


def _in_order(rxs: list[re.Pattern], para: str) -> bool:
    """Whether ``para`` holds every pattern in order, each within ``QUOTE_GAP`` characters of the one before."""
    first = rxs[0]
    for m in first.finditer(para):
        at, ok = m.end(), True
        for rx in rxs[1:]:
            n = rx.search(para, at)
            if n is None or n.start() - at > QUOTE_GAP:
                ok = False
                break
            at = n.end()
        if ok:
            return True
    return False


def _paragraphs(docs) -> list[str]:
    return [_plain(p) for d in docs if d for p in re.split(r"\n[ \t]*\n", d) if p.strip()]


def unfound(text: str, docs: list[str], wider=None) -> list[tuple[int, int, str]]:
    """What in ``text`` cannot stand as his words, as (start, end, what), in order: every irregular quote mark
    (``_scan``; its span the mark), every quotation and blockquote line (its span the whole line) that no single
    paragraph of ``docs`` holds with its parts around "…" in order and close together (``_in_order``), and every one
    with nothing in it to check (``_patterns``). ``wider`` (a callable returning more documents, called at most once
    and only when a passage is missing from ``docs``) is his words in every file of the journal, for what a
    reflection reached through ``journal_search``: a quote stitched from several files, or from far apart, is not
    his."""
    text = text or ""
    paras = _paragraphs(docs)
    more: list[str] | None = None

    def found(q: str) -> bool:
        nonlocal more
        rxs = _patterns(q)
        if rxs is None:
            return False
        if any(_in_order(rxs, p) for p in paras):
            return True
        if wider is None:
            return False
        if more is None:
            more = _paragraphs(wider() or [])
        return any(_in_order(rxs, p) for p in more)

    pairs, odd = _scan(text)
    out = [(i, i + 1, f"irregular quote mark {text[i]!r}: {_around(text, i)}") for i in odd]
    out += [(a, b, inner) for a, b, inner in pairs if not found(inner)]
    pos = 0
    for line in text.splitlines(keepends=True):
        q = line.lstrip("> ").strip() if line.startswith(">") else ""
        if q and not found(q):
            out.append((pos, pos + len(line), q))
        pos += len(line)
    return sorted(out)


def _around(text: str, i: int) -> str:
    """A few words either side of ``text[i]``, on one line (for a log line)."""
    return " ".join(text[max(0, i - 24):i + 24].split())


def _unit(text: str, a: int) -> tuple[int, int]:
    """The block of ``text`` around ``a``: from the first line of its paragraph, or of the list item, lead-in,
    blockquote or heading it is in, to the last line before a blank line or the next such line. A hard-wrapped
    sentence lies within one block."""
    start = text.rfind("\n", 0, a) + 1
    while start > 0 and not _BLOCK.match(text, start):
        prev = text.rfind("\n", 0, start - 1) + 1
        if not text[prev:start - 1].strip():
            break
        start = prev
    end = text.find("\n", a)
    end = len(text) if end < 0 else end
    while end < len(text):
        nxt = text.find("\n", end + 1)
        nxt = len(text) if nxt < 0 else nxt
        line = text[end + 1:nxt]
        if not line.strip() or _BLOCK.match(line):
            break
        end = nxt
    return start, end


def _ends(text: str, lo: int, hi: int, pairs: list[tuple[int, int, str]]) -> list[int]:
    """Where sentences end in ``text[lo:hi]``: after a full stop (with its closing marks) that is not inside a
    quotation, not the end of a common abbreviation (e.g., i.e., Dr.), and followed by the end of the block or a new
    sentence (a capital, a quote mark, emphasis)."""
    out = []
    for m in _SENT_END.finditer(text, lo, hi):
        if any(a <= m.start() < b for a, b, _ in pairs):
            continue
        word = text[max(lo, text.rfind(" ", lo, m.start()) + 1):m.end()].lower().strip("\"”’')]*_\n\t")
        if word.endswith(_ABBREVIATIONS):
            continue
        rest = text[m.end():hi].lstrip(" \t\n")
        if not rest or rest[0] in "\"“*_(" or rest[0].isupper():
            out.append(m.end())
    return out


def _sentence(text: str, a: int, b: int, pairs: list[tuple[int, int, str]]) -> tuple[int, int]:
    """The span of the sentence holding ``text[a:b]`` (a quotation or an irregular mark): from the last sentence end
    before it in its block (``_unit``), or the block's start after a list marker and a bold lead-in such as
    ``**What I notice** —``, to the first sentence end after it in the block, or the block's end. Full stops inside
    quotations are not sentence ends (``_ends``)."""
    lo, hi = _unit(text, a)
    hi = max(hi, b)
    start = lo
    lead = _LEAD.match(text, lo)
    if lead and lead.end() <= a:
        start = lead.end()
    start = max([start, *_ends(text, start, a, pairs)])
    inner = text[a:b]
    rest = text[b:hi].lstrip(" \t*_\n")
    if b - a > 1 and inner[-2:-1] in (".", "!", "?", "…") and (not rest or rest[0].isupper()):
        return start, b  # the quotation closes its sentence on a full stop
    after = _ends(text, b, hi, pairs)
    return start, after[0] if after else hi


def strike(text: str, problems: list[tuple[int, int, str]]) -> str:
    """``text`` without what ``unfound`` found: a blockquote line goes whole, anything else goes with the sentence that
    holds it, its lead-in ("you wrote", "as you put it") included, so no words that are not his stay attributed to
    him, quoted or reported. Only the seams are tidied: the spaces where a sentence went, and a line left empty or
    holding nothing but a bold lead-in or a list marker, which goes too (an emptied "To carry forward" leaves no line
    for the today block to misread). An attribution in the sentence before a quotation ("You wrote this on Monday.")
    stays."""
    pairs, _ = _scan(text)
    spans = []
    for a, b, _ in problems:
        line_start = a == 0 or text[a - 1] == "\n"
        spans.append((a, text.find("\n", a) + 1 or len(text)) if text[a:a + 1] == ">" and line_start
                     else _sentence(text, a, b, pairs))
    merged: list[list[int]] = []
    for a, b in sorted(spans):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    for a, b in reversed(merged):
        left, right = text[:a].rstrip(" \t"), text[b:].lstrip(" \t")
        glue = "" if (not left or left.endswith("\n") or not right or right[0] in "\n.,;:!?") else " "
        text, seam = left + glue + right, len(left)
        ls = text.rfind("\n", 0, seam) + 1
        le = text.find("\n", seam)
        le = len(text) if le < 0 else le
        if _ONLY_LEAD.fullmatch(text[ls:le]):
            text = text[:ls] + text[le + 1:]
            if text[max(0, ls - 2):ls] == "\n\n" and text[ls:ls + 1] == "\n":
                text = text[:ls] + text[ls + 1:]  # (the blank line before it and the one after: one is enough)
    return text.strip()


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
