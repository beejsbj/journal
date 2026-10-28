"""Deterministic message assembly: prompt header + opener + folded question bank (and, once finished, header +
opener + status line), and annotation replies.

Prompts are composed from a ``library.Practice`` (its title, emoji, bank and plain opener); an id string still
works and means the packaged built-in of that id, which is what callers written against the four ritual names
pass. ``tests/golden/`` holds the four rituals' messages byte for byte.
"""
from __future__ import annotations

import html
import re
from datetime import datetime

from . import library
from .config import Names
from .library import Practice
from .ports import Keyboard

DEFAULT_NAMES = Names()  # frozen, so a shared default is safe
HINT = "Tap to open the questions · reply to this message anytime"
PLAIN_HINT = "reply to this message anytime"  # in plain text the questions are shown in full: nothing to tap open
TELEGRAM_LIMIT = 4096


def plain_opener(practice: Practice | str, names: Names = DEFAULT_NAMES) -> str:
    """The opener used whenever the model's is missing or rejected: the practice's ``opener.plain``."""
    return library.resolve(practice).plain_opener(names)


def header(practice: Practice | str, local_dt: datetime, status: str = "", names: Names = DEFAULT_NAMES) -> str:
    """``🌿 Evening journal · Wed 14 Oct`` (`` · status`` when folded). The emoji is the practice's own when it has
    one, else the companion's."""
    p = library.resolve(practice)
    day = local_dt.strftime("%a %-d %b")
    suffix = f" · {status}" if status else ""
    emoji = p.emoji or names.emoji
    lead = f"{emoji} " if emoji else ""
    return f"{lead}{p.title} · {day}{suffix}"


def md_to_html(text: str) -> str:
    """Escape model text, keeping **bold** and _italic_ as the only markup."""
    out = html.escape(text, quote=False)
    out = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", out)
    out = re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"<i>\1</i>", out)
    return out


def bank_html(practice: Practice | str) -> str:
    """The question set with its original formatting: a numbered list (a flat bank), or bold numbered sections
    of dashed questions."""
    p = library.resolve(practice)
    esc = lambda t: html.escape(t, quote=False)  # noqa: E731
    if p.flat:
        return "\n".join(esc(f"{i}. {q.text}") for i, q in enumerate(p.questions, 1))
    return "\n\n".join(f"<b>{esc(str(i) + '. ' + (title or ''))}</b>\n" + "\n".join(esc(f"- {q.text}") for q in qs)
                       for i, (title, qs) in enumerate(p.sections, 1))


def telegram_html(practice: Practice | str, local_dt: datetime, opener: str | None, status: str = "",
                  names: Names = DEFAULT_NAMES) -> str:
    """Live prompt: header, opener, folded question set. Folded (status set): header, the opener as it was sent, and
    the status line in the question set's place (``folded_html``)."""
    if status:
        return folded_html(practice, local_dt, opener, status, names)
    head = f"<b>{html.escape(header(practice, local_dt, names=names), quote=False)}</b>"
    parts = [head]
    if opener:
        parts.append(md_to_html(opener.strip()))
    parts.append(f"<blockquote expandable>{bank_html(practice)}</blockquote>")
    parts.append(f"<i>{HINT}</i>")
    return "\n\n".join(parts)


def folded_html(practice: Practice | str, local_dt: datetime, opener: str | None, status: str,
                names: Names = DEFAULT_NAMES) -> str:
    """A finished prompt (``answered ✓``, ``missed — reply anytime``, ``sent again below``): the header line, the
    opener exactly as it went out, and the status as a short italic line where the question set was. Only the question
    set and the hint are dropped, so the chat still reads as what the companion said.

    The opener comes from the send record (``opener``, the plain one when the model's was missing or rejected), never
    from the chat or the library, so a fold can show nothing that was not already in the message. It goes through
    ``md_to_html`` as at send time. That markup can still be refused (crossed tags from the model's text: Telegram
    took the send as plain text, ``telegram.send_html``), so ``telegram.edit_html`` retries an edit it cannot parse as
    plain text, and the callers fold once more without the opener should that fail. The folded text is shorter
    than the one sent (no bank, no hint), so it fits the limit when that did; should it not (a retitled practice),
    the opener is dropped and the fold is the header and the status."""
    head = f"<b>{html.escape(header(practice, local_dt, names=names), quote=False)}</b>"
    line = f"<i>{html.escape(status, quote=False)}</i>"
    if opener and opener.strip():
        text = "\n\n".join([head, md_to_html(opener.strip()), line])
        if len(text) <= TELEGRAM_LIMIT:
            return text
    return "\n\n".join([head, line])


def plain_text(practice: Practice | str, local_dt: datetime, opener: str | None, names: Names = DEFAULT_NAMES) -> str:
    """The archived orientation prompt: exactly what was sent, minus markup."""
    parts = [header(practice, local_dt, names=names)]
    if opener:
        parts.append(opener.strip())
    parts.append(library.resolve(practice).bank_text())
    return "\n\n".join(parts)


_BULLET = re.compile(r"^\s*[-*•]\s+")
_LINK = re.compile(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', re.S | re.I)


def plain(markup: str) -> str:
    """A composed message (Telegram HTML) as plain text, for a platform that takes no HTML (``ports.Caps.html``):
    the same words and lines with the tags gone, a link as its text with the address after it. The question set is
    no longer folded there, so the prompt's hint keeps only its second half (``PLAIN_HINT``)."""
    def link(m: re.Match) -> str:
        url, text = html.unescape(m.group(1)), m.group(2)
        shown = html.unescape(re.sub(r"<[^>]+>", "", text))
        return text if not url or url == shown else f"{text} ({html.escape(url, quote=False)})"
    text = _LINK.sub(link, markup or "")
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I).replace(f"<i>{HINT}</i>", f"<i>{PLAIN_HINT}</i>")
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _md_lines(text: str, keep_blank: bool = False) -> list[str]:
    """Markdown bullets and paragraphs as Telegram HTML lines (• and ◦ for bullets)."""
    lines = []
    for line in text.strip().splitlines():
        if not line.strip():
            if keep_blank and lines and lines[-1]:
                lines.append("")
            continue
        body = _BULLET.sub("", line)
        indent = len(line) - len(line.lstrip())
        lines.append(("    ◦ " if indent >= 2 and _BULLET.match(line) else "• " if _BULLET.match(line) else "")
                     + md_to_html(body.strip()))
    return lines


def _fit(head: str, lines: list[str], tail: str, wrap: tuple[str, str] = ("", "")) -> str:
    """head + lines (inside ``wrap``) within Telegram's limit, cut on a whole line so no tag is left open."""
    open_, close = wrap
    text = head + "\n\n" + open_ + "\n".join(lines) + close
    if len(text) <= TELEGRAM_LIMIT:
        return text
    out = head + "\n\n" + open_
    for l in lines:
        if len(out) + len(l) + 1 + len(close) + len(tail) > TELEGRAM_LIMIT:
            break
        out += l + "\n"
    return out.rstrip("\n") + close + tail


def annotation_html(kind: str, annotations: str, names: Names = DEFAULT_NAMES) -> str:
    """The annotations as a Telegram reply: a short header, then the bullets."""
    what = "your note" if kind in ("j", "note") else f"your {kind} entry"
    lead = f"{names.emoji} " if names.emoji else ""
    title = f"{lead}{names.companion}'s notes on {what}"
    head = f"<b>{html.escape(title, quote=False)}</b>"
    return _fit(head, _md_lines(annotations), "\n\n<i>… the rest is in the entry.</i>")


def reflection_html(title: str, body: str, names: Names = DEFAULT_NAMES) -> str:
    """A reflection or week review as its own message: bold title, the text in an expandable quote."""
    lead = f"{names.emoji} " if names.emoji else ""
    head = f"<b>{html.escape(lead + title, quote=False)}</b>"
    return _fit(head, _md_lines(body, keep_blank=True), "\n\n<i>… the rest is in the journal.</i>",
                ("<blockquote expandable>", "</blockquote>"))


# ── program phases (PRACTICES.md §5) ─────────────────────────────────────────
# A phase message is header, the previous phase's transition as an italic lead-in, the phase's blocks as authored
# (questions keep their **N.M** ids), and the command hint. Built as Markdown parts first, so the Telegram HTML,
# the plain text archived in the ledger and the JSON for the web app are one rendering.

PHASE_HINT = "reply here · /next · /skip · /back · /break"
# Text buttons (a reply keyboard): a tap sends the command as if typed. Removed with the session summary. A platform
# without reply keyboards (``ports.Caps.keyboard``) goes without; the hint line above names the same commands.
KEYBOARD = Keyboard((("/next", "/skip"), ("/back", "/break")), "Answer in your own words")
REMOVE_KEYBOARD = Keyboard()
_PLAIN_HEADINGS = (None, "Introduction", "Question", "Questions", "Task", "Tasks", "Analysis")
_CONSTRUCTED = ("Task", "Tasks", "Analysis")  # what a reflect phase's construction stands in for


def phase_header(prog, session_n: int, phase_n: int, names: Names = DEFAULT_NAMES) -> str:
    """``🧠 Fix your entire life in 1 day · Session 3/10 · Phase 2/5`` (a situational run: no session)."""
    emoji = prog.emoji or names.emoji
    lead = f"{emoji} " if emoji else ""
    s = prog.session(session_n)
    where = f" · Session {session_n}/{len(prog.sessions)}" if not prog.situational else ""
    return f"{lead}{prog.title}{where} · Phase {phase_n}/{len(s.phases)}"


def folded_phase_html(record: dict, status: str, names: Names = DEFAULT_NAMES) -> str:
    """A phase message folded to its header (``answered ✓``, ``open — reply anytime``, ``sent again below``), from
    what its ledger record says, so it folds even after the definition changed or went away."""
    emoji = record.get("emoji") or names.emoji
    lead = f"{emoji} " if emoji else ""
    where = (f" · Session {record['session']}/{record['session_count']}" if record.get("session_count") else "")
    if record.get("interrupt"):  # a one-off question of the session (``interrupt_html``), not a phase
        head = f"{lead}{record.get('title') or record.get('kind')}{where} · Interrupt {record['interrupt']}/" \
               f"{record.get('interrupt_count', '?')}"
    else:
        head = f"{lead}{record.get('title') or record.get('kind')}{where} · Phase {record.get('phase')}/" \
               f"{record.get('phase_count', '?')}"
    return f"<b>{html.escape(f'{head} · {status}', quote=False)}</b>"


def phase_parts(prog, session_n: int, phase_n: int, *, lead: str = "", construction: str | None = None,
                fallback: str = "", intro: str = "", fill=lambda text: text,
                ask: str = "") -> list[tuple[str | None, str]]:
    """The phase body as ``(heading, markdown)`` parts in order. ``construction``: a reflect phase's model text,
    which stands in for its Task/Analysis blocks; when it is None those blocks are sent with ``fallback`` (the
    template note) after them. ``intro``: the program's intro, at the top of a new run's first message. ``fill``
    resolves prose slots from his earlier answers (``programs.fill``). ``ask``: the engine's own closing line (a
    schedule phase asks for the day and wake time it times its interrupts from)."""
    s = prog.session(session_n)
    ph = s.phase(phase_n)
    parts: list[tuple[str | None, str]] = []
    if intro:
        parts.append((None, intro))
    if phase_n == 1 and not prog.situational:
        if s.quote:
            parts.append((None, f"> {s.quote}"))
        if s.preamble:
            parts.append((None, fill(s.preamble)))
    if lead:
        parts.append((None, f"_{lead.strip()}_"))
    placed = False
    for heading, text in ph.blocks:
        shown = None if heading in _PLAIN_HEADINGS else heading
        if ph.type == "reflect" and heading in _CONSTRUCTED:
            if construction is not None:
                if not placed:
                    parts.append((None, construction.strip()))
                placed = True
                continue
            parts.append((shown, fill(text)))
            if fallback and not placed:
                parts.append((None, fallback))
            placed = True
            continue
        if ph.type == "reflect" and not placed and heading not in (None, "Introduction"):
            # a reflect phase without a Task/Analysis block: the construction (or the note) follows the intro
            parts.append((None, construction.strip() if construction is not None else fallback))
            placed = True
        parts.append((shown, fill(text)))
    if ph.type == "reflect" and not placed:
        parts.append((None, construction.strip() if construction is not None else fallback))
    if ask:
        parts.append((None, ask))
    return [(h, t) for h, t in parts if t and t.strip()]


# ── session interrupts (PRACTICES.md §4.8) ───────────────────────────────────
# A timed question the tick sends on its own, from the times he confirmed. It is not a phase: no keyboard, no
# /next, and a reply to this message is its answer.

INTERRUPT_HINT = "reply to this message when you can · a line is enough"


def interrupt_header(prog, session_n: int, n: int, of: int, names: Names = DEFAULT_NAMES) -> str:
    """``🧠 Fix your entire life in 1 day · Session 10/10 · Interrupt 3/8``."""
    emoji = prog.emoji or names.emoji
    lead = f"{emoji} " if emoji else ""
    where = f" · Session {session_n}/{len(prog.sessions)}" if not prog.situational else ""
    return f"{lead}{prog.title}{where} · Interrupt {n}/{of}"


def interrupt_question(prog, session_n: int, n: int, of: int, text: str, names: Names = DEFAULT_NAMES) -> str:
    """The question as the message carries it: whole, or (a very long authored one) shortened on a word until the
    message fits Telegram's limit. The ledger keeps this one, so what a reply folds under ``## Prompt`` and what the
    companion is told is what was sent."""
    words, question = text.split(), text
    while words and len(_interrupt_message(prog, session_n, n, of, question, names)) > TELEGRAM_LIMIT:
        words = words[:int(len(words) * 0.9)]
        question = " ".join(words) + " …"
    return question


def _interrupt_message(prog, session_n: int, n: int, of: int, text: str, names: Names) -> str:
    head = html.escape(interrupt_header(prog, session_n, n, of, names), quote=False)
    return f"<b>{head}</b>\n\n{md_block_html(text)}\n\n<i>{INTERRUPT_HINT}</i>"


def interrupt_html(prog, session_n: int, n: int, of: int, text: str, names: Names = DEFAULT_NAMES) -> str:
    """The interrupt message: header, the question (fitted), the hint."""
    return _interrupt_message(prog, session_n, n, of, interrupt_question(prog, session_n, n, of, text, names), names)


def interrupt_text(prog, session_n: int, n: int, of: int, text: str, names: Names = DEFAULT_NAMES) -> str:
    """What the interrupt said, minus markup: the ledger's ``plain_text`` and the mirror's copy."""
    return "\n\n".join((interrupt_header(prog, session_n, n, of, names), _md_plain(text), INTERRUPT_HINT))


def schedule_confirmed_html(rows: list[tuple[str, str, str]], day: str, past: int = 0) -> str:
    """The note after a confirmation: one line per interrupt (``rows``: offset, local time, question), the day, and
    how many have already gone by."""
    lines = [f"{html.escape(offset, quote=False)} · <b>{html.escape(at, quote=False)}</b>" for offset, at, _ in rows]
    gone = f"\n\n<i>{past} of them {'has' if past == 1 else 'have'} already passed.</i>" if past else ""
    return (f"<b>Interrupts set for {html.escape(day, quote=False)}</b>\n" + "\n".join(lines)
            + "\n\n<i>Each arrives as its own message: reply to it with what's true right now.</i>" + gone)


def md_block_html(text: str) -> str:
    """Authored Markdown as Telegram HTML: ``**bold**``, ``_italic_``, ``- `` bullets as •, and ``> `` lines as one
    blockquote; everything else escaped and kept line for line."""
    out, quote = [], []

    def flush():
        if quote:
            out.append("<blockquote>" + "\n".join(quote) + "</blockquote>")
            quote.clear()
    for line in (text or "").strip("\n").splitlines():
        if line.startswith(">"):
            quote.append(md_to_html(line.lstrip("> ").rstrip()))
            continue
        flush()
        body = _BULLET.sub("• ", line, count=1) if _BULLET.match(line) else line
        out.append(md_to_html(body.rstrip()))
    flush()
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def _md_plain(text: str) -> str:
    plain = re.sub(r"\*\*(.+?)\*\*", r"\1", text or "")
    return re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"\1", plain).strip()


def phase_html(prog, session_n: int, phase_n: int, parts, status: str = "", names: Names = DEFAULT_NAMES) -> str:
    """The phase message (folded with ``status``: the header line alone, ``answered ✓`` / ``open — reply
    anytime``)."""
    title = prog.session(session_n).phase(phase_n).title
    head = phase_header(prog, session_n, phase_n, names)
    if status:
        return f"<b>{html.escape(f'{head} · {status}', quote=False)}</b>"
    out = [f"<b>{html.escape(head, quote=False)}</b>\n<b>{html.escape(title, quote=False)}</b>"]
    for heading, text in parts:
        block = md_block_html(text)
        out.append(f"<b>{html.escape(heading, quote=False)}</b>\n{block}" if heading else block)
    out.append(f"<i>{PHASE_HINT}</i>")
    return "\n\n".join(out)


def phase_messages(prog, session_n: int, phase_n: int, parts, names: Names = DEFAULT_NAMES) -> list[str]:
    """The phase as Telegram messages: one, or (over Telegram's limit) two, split on the phase's own boundary:
    everything up to the questions first, the questions and the hint second. The last is the reply target."""
    whole = phase_html(prog, session_n, phase_n, parts, names=names)
    if len(whole) <= TELEGRAM_LIMIT:
        return [whole]
    qblocks = [i for i, (h, t) in enumerate(parts) if re.search(r"^\*\*[0-9]+\.[0-9]+\*\*", t, re.M)]
    cut = qblocks[0] if qblocks and qblocks[0] > 0 else max(1, len(parts) // 2)
    first = phase_html(prog, session_n, phase_n, parts[:cut], names=names).rsplit("\n\n", 1)[0]  # no hint yet
    second = phase_html(prog, session_n, phase_n, parts[cut:], names=names)
    tail = "\n\n<i>… the rest is in the definition file.</i>"
    return [m if len(m) <= TELEGRAM_LIMIT else _closed(m[:TELEGRAM_LIMIT - len(tail) - 40].rsplit("\n", 1)[0]) + tail
            for m in (first, second)]


_TAG = re.compile(r"<(/?)(b|i|u|s|code|pre|blockquote|a|tg-spoiler)\b[^>]*>")


def _closed(markup: str) -> str:
    """``markup`` cut anywhere between lines, with the tags it left open closed (Telegram refuses a message whose
    tags do not balance)."""
    stack: list[str] = []
    for m in _TAG.finditer(markup):
        if m.group(1):
            if m.group(2) in stack:
                del stack[len(stack) - 1 - stack[::-1].index(m.group(2)):]
        else:
            stack.append(m.group(2))
    return markup + "".join(f"</{t}>" for t in reversed(stack))


def phase_text(prog, session_n: int, phase_n: int, parts, names: Names = DEFAULT_NAMES) -> str:
    """What the phase message said, minus markup: the ledger's ``plain_text`` and the mirror's copy."""
    title = prog.session(session_n).phase(phase_n).title
    out = [phase_header(prog, session_n, phase_n, names), title]
    for heading, text in parts:
        out.append((f"{heading}\n" if heading else "") + _md_plain(text))
    out.append(PHASE_HINT)
    return "\n\n".join(out)


def phase_body_text(parts) -> str:
    """The phase without header and hint, as Markdown: what the session entry folds under ``## Prompt``."""
    return "\n\n".join((f"**{h}**\n" if h else "") + t for h, t in parts)


def phase_json(prog, session_n: int, phase_n: int, parts, names: Names = DEFAULT_NAMES) -> dict:
    """The phase as data (what the web app shows)."""
    ph = prog.session(session_n).phase(phase_n)
    return {"program": prog.id, "session": session_n, "phase": phase_n, "type": ph.type, "title": ph.title,
            "header": phase_header(prog, session_n, phase_n, names),
            "blocks": [{"heading": h, "text": t} for h, t in parts],
            "questions": [{"id": q.id, "text": q.text} for q in ph.questions], "hint": PHASE_HINT}


def summary_html(prog, session_n: int, completion: str, summary: str, document_title: str = "",
                 document: str = "", next_line: str = "", names: Names = DEFAULT_NAMES) -> str:
    """The session-complete message: the last phase's completion text, the filled summary, the compiled
    document (expandable) and what comes next; within Telegram's limit, cut on a whole line."""
    emoji = prog.emoji or names.emoji
    where = f" · Session {session_n}/{len(prog.sessions)}" if not prog.situational else ""
    head = f"<b>{html.escape(f'{emoji} ' if emoji else '', quote=False)}{html.escape(prog.title + where, quote=False)}</b>"
    body = [md_block_html(t) for t in (completion, summary) if t and t.strip()]
    if document and document.strip():
        body.append(f"<b>{html.escape(document_title, quote=False)}</b>\n<blockquote expandable>"
                    f"{md_block_html(document)}</blockquote>")
    tail = f"\n\n<i>{md_to_html(next_line)}</i>" if next_line else ""
    text = head + "\n\n" + "\n\n".join(body) + tail
    if len(text) <= TELEGRAM_LIMIT:
        return text
    cut = "\n\n<i>… the rest is in the entry.</i>"
    lines = (head + "\n\n" + "\n\n".join(md_block_html(t) for t in (completion, summary) if t)).split("\n")
    out = ""
    for line in lines:
        if len(out) + len(line) + 1 + len(cut) + len(tail) > TELEGRAM_LIMIT:
            break
        out += line + "\n"
    out = re.sub(r"<blockquote>(?![\s\S]*</blockquote>)", "", out)  # never leave a quote open
    return out.rstrip("\n") + cut + tail
