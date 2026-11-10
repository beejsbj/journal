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
import unicodedata
from functools import lru_cache

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
# A reflection is free Markdown, so grounding is checked on what it quotes. Only well-formed double
# quotation counts as quotation: a straight "…" or curly “…” pair on one line whose inside is not empty
# and neither starts nor ends with a space, and a blockquote (``_BLOCKQUOTE``, as Obsidian renders one).
# A double quote mark of any other kind, or outside such a pair, is irregular (``quote_like``): it cannot
# be checked, so it never passes. One case is read again: a quotation whose inside holds marks of its
# own is one quotation when the whole of it is his verbatim (``_scan``: a sentence of his that quotes
# someone). Single quotes, HTML and paraphrase are not judged here (open).
# A day reflection or week review checks each quotation against his words, file by file and paragraph
# by paragraph (``unfound``); one problem makes the answer a bad one, retried, and one that never
# passes is never written (``reflect.write_one``). A program's synthesis (``insights``), the catch-up
# and a program's construction and bridge reject the whole text on one (``_misquote``: the same check,
# against their one source). What is said of a problem outside the ledger is numbers (``place``,
# ``problem``): a rejected text's words, which may be his, go to no log line and no alert.

REFLECTION_LIMITS = {"reflection": 3500, "week": 6000, "insights": 5000}  # insights: a program's synthesis
QUOTE_GAP = 300  # characters of his at most between two parts of one quote stitched with "…"
_DOUBLE_NAMES = ("DOUBLE PRIME", "TRIPLE PRIME", "QUADRUPLE PRIME", "DOUBLE APOSTROPHE", "CORNER BRACKET")
_BLOCKQUOTE = re.compile(r"[ ]{0,3}(?:[-*+]\s+|\d+[.)]\s+)?>")  # a blockquote line, in a list item or not
_BLOCKQUOTE_MARKS = re.compile(r"[ ]{0,3}(?:[-*+]\s+|\d+[.)]\s+)?(?:>[ \t]?)+")
_TYPO = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "—": "-", "–": "-", " ": " "})
_ELLIPSIS = re.compile(r"\s*(?:\.\.\.|…|\[\.\.\.\]|\[…\])\s*")


def _plain(s: str) -> str:
    return _norm((s or "").translate(_TYPO))


@lru_cache(maxsize=4096)
def quote_like(c: str) -> bool:
    """Whether character ``c`` is a double quote mark of any kind: the straight " and fullwidth ＂, anything Unicode
    names a double quotation mark (“ ” „ ‟ « » ❝ ❞ 〝 〞 〟 🙶 ⹂ …), the double, triple and quadruple primes (″ ‴ ⁗ ‶ ‷
    ʺ), the double apostrophe ˮ, and the corner brackets CJK writing quotes with (「 」 『 』 ﹁ ﹂ ｢ ｣). A class, not a
    list. Single quote marks, apostrophes, backticks and HTML are not judged here (an open point: DECISIONS.md,
    CONTINUITY)."""
    if c in '"＂':
        return True
    name = unicodedata.name(c, "")
    return ("QUOTATION" in name and "DOUBLE" in name) or any(n in name for n in _DOUBLE_NAMES)


def _scan(text: str, his=None) -> tuple[list[tuple[int, int, str]], list[int]]:
    """(the well-formed quotations of ``text`` as (start, end, inside), where every irregular mark is). A quotation is
    a straight "…" or curly “…” pair on one line, closed by the next double quote mark on that line (``quote_like``),
    with an inside that is not empty and does not start or end with whitespace. Every other double quote mark is
    irregular: one that opens no such pair, the one that closes it wrongly (a mixed pair, a quote inside a quote, a
    quote across a line break), and every other kind wherever it stands (« », „, ″, ˮ, 「 」 …).

    ``his`` (a test: is this passage, exactly as it stands, in his words?) gives a line that has irregular marks one
    more reading (``_pairs``): an opening mark may close at a later mark of the line when everything between the
    two, the marks inside included, is his verbatim. A sentence of his that itself holds quotation marks can then
    be quoted whole; nothing else changes, since a line without irregular marks is never read again."""
    text = text or ""
    pairs: list[tuple[int, int, str]] = []
    odd: list[int] = []
    pos = 0
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        marks = [i for i, c in enumerate(body) if quote_like(c)]
        found, bad = _pairs(body, marks)
        if bad and his is not None:
            found, bad = _pairs(body, marks, his)
        pairs += [(pos + i, pos + j, inner) for i, j, inner in found]
        odd += [pos + i for i in bad]
        pos += len(line)
    return pairs, odd


def _pairs(body: str, marks: list[int], his=None) -> tuple[list[tuple[int, int, str]], list[int]]:
    """One line's quotations and irregular marks (``_scan``), ``marks`` being where its double quote marks are. With
    ``his``, an opening mark first tries the later marks of the line, the farthest first, and closes at one when the
    whole span between them is his verbatim (his own marks lie inside it); otherwise at the next mark, as always."""
    pairs: list[tuple[int, int, str]] = []
    odd: list[int] = []
    k = 0
    while k < len(marks):
        i = marks[k]
        if body[i] in '"“':
            close = '"' if body[i] == '"' else "”"
            ends = [k + 1]
            if his is not None:
                ends = [m for m in range(len(marks) - 1, k + 1, -1) if _closes(body, i, marks[m], close)
                        and his(body[i + 1:marks[m]])][:1] + ends
            m = ends[0]
            if m < len(marks) and _closes(body, i, marks[m], close):
                pairs.append((i, marks[m] + 1, body[i + 1:marks[m]]))
                k = m + 1
                continue
        odd.append(i)
        k += 1
    return pairs, odd


def _closes(body: str, i: int, j: int, close: str) -> bool:
    """Whether the mark at ``j`` closes a quotation opened at ``i``: the matching mark, around an inside that is not
    empty and neither starts nor ends with whitespace."""
    inner = body[i + 1:j]
    return body[j] == close and bool(inner) and inner == inner.strip()


def _blockquotes(text: str) -> list[tuple[int, int, str]]:
    """Each blockquote of ``text`` as (start, end, what it says): a run of lines that starts with a blockquote line
    (``_BLOCKQUOTE``: up to three spaces, perhaps a list item's marker, then ``>``) and goes on through every
    non-blank line straight after it (more ``>`` lines, or lazy continuation, which Obsidian renders inside it). Its
    span takes whole lines; what it says is its lines without their markers, joined."""
    out, block, pos = [], None, 0
    for line in text.splitlines(keepends=True):
        content = line.strip()
        if block is not None and content:
            block[1] = pos + len(line)
            block[2].append(_BLOCKQUOTE_MARKS.sub("", line, count=1).strip() if _BLOCKQUOTE.match(line) else content)
        else:
            if block is not None:
                out.append((block[0], block[1], " ".join(t for t in block[2] if t)))
                block = None
            if _BLOCKQUOTE.match(line):
                block = [pos, pos + len(line), [_BLOCKQUOTE_MARKS.sub("", line, count=1).strip()]]
        pos += len(line)
    if block is not None:
        out.append((block[0], block[1], " ".join(t for t in block[2] if t)))
    return out


def _misquote(text: str, corpus: str, source: str) -> str | None:
    """The first problem with what ``text`` quotes, checked as a reflection's quotes are (``unfound``) against its one
    source ``corpus`` (his messages, his answers, the compiled document), as a reason: an irregular double quote mark
    (in numbers only: the mark and where it stands, ``place``), or ``quote not in <source>:`` with the start of the
    quoted passage that is not there, as before. None: there is none."""
    bad = unfound(text, [corpus])
    if not bad:
        return None
    what = bad[0][2]
    return what if is_mark(what) else f"quote not in {source}: {what[:60]!r}"


def _patterns(q: str) -> list[re.Pattern]:
    """A quoted passage as the patterns its parts around "…" must match, folded as the check compares them: a part of
    three characters or more anywhere, a shorter one only as a whole word ("No" must be a word of his, not the end of
    "know"). Empty when nothing is left to look for (a quote of punctuation alone)."""
    parts = [f for f in (_plain(p).strip(" .,;:!?'\"-") for p in _ELLIPSIS.split(q)) if f]
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
    """What in ``text`` cannot stand as his words, as (start, end, what), in order: every irregular mark (``_scan``;
    its span the mark; a quotation that holds marks of its own is one quotation when the whole of it is his
    verbatim), every quotation and blockquote (its span its whole lines) that no single paragraph of ``docs``
    holds with its parts around "…" in order and close together (``_in_order``, ``_patterns``). ``wider`` (a
    callable returning more documents, called at most once and only when a passage is missing from ``docs``) is his
    words in every file of the journal, for what a reflection reached through ``journal_search``: a quote stitched
    from several files, or from far apart, is not his."""
    text = text or ""
    paras = _paragraphs(docs)
    more: list[str] | None = None

    def found(q: str) -> bool:
        nonlocal more
        rxs = _patterns(q)
        if not rxs or any(_in_order(rxs, p) for p in paras):
            return True
        if wider is None:
            return False
        if more is None:
            more = _paragraphs(wider() or [])
        return any(_in_order(rxs, p) for p in more)

    def whole(q: str) -> bool:
        """Whether ``q`` exactly as it stands (its own quotation marks included, no "…" stitching) is in one paragraph
        of his: what lets a quotation close at a later mark of its line (``_scan``)."""
        nonlocal more
        f = _plain(q).strip(" .,;:!?-")
        if not f or any(f in p for p in paras):
            return bool(f)
        if wider is None:
            return False
        if more is None:
            more = _paragraphs(wider() or [])
        return any(f in p for p in more)

    pairs, odd = _scan(text, whole)
    blocks = _blockquotes(text)
    out = [(i, i + 1, f"irregular quote mark U+{ord(text[i]):04X} at {place(text, i)}") for i in odd]
    out += [(a, b, inner) for a, b, inner in pairs if not found(inner)]
    out += [(a, b, said or "(an empty blockquote)") for a, b, said in blocks if not said or not found(said)]
    return sorted(out)


def place(text: str, start: int, end: int | None = None) -> str:
    """Where ``text[start:end]`` stands, in numbers only: ``character 412 (line 3)``, and its length when ``end`` is
    given. What a log line, a ledger record or an alert may say of a rejected text: never its words, which may be
    his."""
    at = f"character {start + 1} (line {text.count(chr(10), 0, start) + 1})"
    return at if end is None else f"{at}, {end - start} characters long"


_MARK = re.compile(r"irregular quote mark U\+[0-9A-F]{4,6} at character \d+ \(line \d+\)")


def is_mark(what: str) -> bool:
    """Whether a finding of ``unfound`` is an irregular mark (said in numbers alone, so it may go anywhere), not a
    quoted passage (words: a passage that merely begins with the word "irregular" is still one)."""
    return _MARK.fullmatch(what or "") is not None


def problem(text: str, bad: tuple[int, int, str]) -> str:
    """One finding of ``unfound`` in numbers only, for a log line, a ledger record's ``error`` or an alert: an
    irregular mark is said that way already; a quotation that is not his is named by where it stands and how long
    it is."""
    start, end, what = bad
    return what if is_mark(what) else f"a quote not in his words at {place(text, start, end)}"


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
    bad = _misquote(text, corpus, "the entries") if misquotes else None
    if bad is not None:
        return False, bad
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
    bad = _misquote(text, corpus, "his messages")
    if bad is not None:
        return False, bad
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
    bad = _misquote(text, answers, "his answers")
    if bad is not None:
        return False, bad
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
    bad = _misquote(text, last_answer, "his answer")
    if bad is not None:
        return False, bad
    return True, "ok"
