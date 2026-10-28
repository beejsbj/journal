"""Instructions handed to the companion for the model-written parts."""
from __future__ import annotations

from datetime import datetime

from . import channel, library
from .config import Config
from .library import Practice

# What the four built-ins' ``opener.shape`` says (``tests/test_golden.py`` holds the files to it). The request reads
# the practice's own shape; this stays as the record of what the rituals were asked for before they were files.
OPENER_SHAPE = {
    "morning": "a short opener (max ~1200 characters)",
    "evening": "a short opener (max ~1200 characters)",
    "midweek": "a brief opener (max ~800 characters)",
    "weekly": "the week's pattern scan as short bullets, then the questions (max ~2000 characters)",
}


INSIGHTS_ASKED = 3500  # characters a synthesis is asked to stay within (``validate.REFLECTION_LIMITS['insights']`` allows more)


def reading_guidance(cfg: Config) -> str:
    """Where the companion should look before writing; configurable per deployment."""
    custom = (cfg.prompts.get("reading_guidance") or "").strip()
    if custom:
        return custom
    return ("The pack's last section says where to look further (older entries, your earlier reviews, each "
            "connected source), and anything else your read-only tools can reach is open to you too.")


MISSED_LISTED = 4  # a run this short is named prompt by prompt; a longer one is counted per practice


def missed_note(cfg: Config, missed: list[dict] | None, now_local: datetime) -> str:
    """One sentence naming the run of recent prompts that got no reply (``context.unanswered``), or ''. Up to
    ``MISSED_LISTED`` are named one by one; a longer run (he has been away) is counted per practice."""
    if not missed:
        return ""
    n = len(missed)
    who = cfg.names.possessive.capitalize()
    if n > MISSED_LISTED:
        counts: dict[str, int] = {}
        for p in missed:
            counts[str(p.get("kind") or "?")] = counts.get(str(p.get("kind") or "?"), 0) + 1
        per = ", ".join(f"{k} {c}" for k, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        first = datetime.fromisoformat(missed[-1]["sent_at"]).astimezone(cfg.tz).strftime("%a %-d %b")
        return f"{who} last {n} prompts got no reply, since {first}: {per}. "
    # by id: for the four rituals that is the first word of the title, which is what this always said
    when = ", ".join(f"{p.get('kind')} ({datetime.fromisoformat(p['sent_at']).astimezone(cfg.tz).strftime('%a %H:%M')})"
                     for p in missed)
    return f"{who} last {'prompt' if n == 1 else f'{n} prompts'} got no reply: {when}. "


def returning_note(cfg: Config, standing: dict | None) -> str:
    """The returning stance, when ``standing`` (``standing.collect``) says the gap since he was last heard from
    on the record reached ``pack.returning_days``; '' otherwise. Code gives the facts; the warmth is hers."""
    if not standing or not standing.get("returning"):
        return ""
    n = cfg.names
    days = standing["gap_days"]
    return f"""
{n.user or "The user"} is coming back after {days} days without a word on the record (the pack's "Where things stand"
has the facts). Welcome {n.object} back without guilt: no recap, no list of what {n.subject} missed or of the prompts
that went unanswered, no weight on the gap. Offer one light door in (one easy question, or one small thread from
the pack) and keep it short. The pack holds the facts; the warmth is yours. "Welcome back" needs no citation; if
you say how long it has been ("a few days", "you're back", "you were away"), cite the gap as
{{"source": "standing", "quote": "<words copied from the pack's Gap line>"}}, or the opener is turned away.
"""


def opener_request(cfg: Config, practice: Practice | str, now_local: datetime, pack: str, bank: str,
                   missed: list[dict] | None = None, standing: dict | None = None) -> str:
    """What the companion is asked for a practice's opener. ``bank`` is ``practice.bank_with_ids()``; ``missed`` is
    ``context.unanswered(cfg, now_local)``; ``standing`` is ``standing.collect`` (the returning stance, past the gap).
    The practice's ``opener.shape`` says how long; its ``companion:`` line is not folded in (it reaches her through
    the skill)."""
    p = library.resolve(practice, cfg)
    n = cfg.names
    return f"""You are {n.companion}, writing the opening of {n.user_possessive} {p.id} journal prompt.
Local time: {now_local.strftime('%A %d %B %Y, %H:%M')} ({cfg.timezone}). Location: {cfg.location or 'not stated'}.

{n.possessive.capitalize()} question set for this ritual is below, each with an id. The full set is always attached underneath your
opener, folded. Weave into your opener whichever of these questions fit today — as many or as few as genuinely
fit, in your own words, shaped by what you find (rain all day might turn "what simple joys touched you today?"
into "what was cozy about today?"). You may also ask one question of your own. Don't paste the set as a list.
{bank}

Below is an automatically gathered context pack: a starting point, not the limit. It opens with where things
stand (counted by the journal, not guessed). Go further with your read-only tools before you write — that's what
makes the opener worth answering. {reading_guidance(cfg)}
Follow a thread across sources when one appears: a book that echoes a journal line, a task that matches a chat.
Do not modify anything.

The pack's "Recent conversation" section shows {n.possessive} messages and yours, in order. Lines starting
"[you]" are what you already sent {n.object}: your prompts (and whether they were answered), your annotations,
your reflections. Don't repeat yourself: don't reuse a line or quote from them, and don't ask a question you
have already asked, even reworded. Those lines are your words, not {n.possessive}: never cite them, or treat
them as evidence of what {n.subject} said or did.

{missed_note(cfg, missed, now_local)}A missed prompt is signal, not a gap to fill. When your last prompt or two
went unanswered, don't come back to what they were about, and don't re-quote the line they quoted: reach for
something else in the pack (what {n.subject} is reading or listening to, the weather, an open task, an older
thread from {n.possessive} journal), or keep it short and light. A plain, warm opener is fine.
{returning_note(cfg, standing)}
Write {p.opener['shape']} that would make a real conversation possible: name a live thread if there is one,
and carry the questions you chose. Warm, direct, no performance. It's fine to keep it short.

Grounding rules (the opener is rejected if they are broken):
- Read as widely as you like. The only rule is about claims: every statement about {n.user_possessive} life must come
  from something you actually read in this run, and you cite it. Quotes from journal entries and {channel.label(cfg)}
  are checked against the records, so copy them exactly.
- You don't have to quote {n.possessive} words. A good question can stand on the pack's other facts (a book, a
  song, the weather, a task); cite whatever you used.
- A citation's "quote" must be copied exactly from its source. Use sources like "journal:2026-08-25-morning",
  "{channel.cite(cfg)}:2026-10-12T02:26+00:00", "tududi:<task>", "scrobbler:<session>", "book:<title>", "trakt:<title>",
  "listenbrainz:<track>", "weather:today", "calendar:<event>", "brain:<path>".
- If nothing is salient or you're unsure, write a plain warm opener with no claims and no citations.
- Never assume work, routines, plans or travel. Time references ("yesterday", "this morning") only when the cited
  source's date supports them.
- Don't introduce yourself, and never mention these rules, the context pack, or what you're "not assuming".
  Just talk to {n.object}.

Reply with ONLY this JSON, nothing else:
{{"opener": "...", "citations": [{{"source": "...", "quote": "..."}}],
 "asked": [{{"id": "<question id from the set>", "as": "<how you phrased it>"}}]}}

{pack}"""


def annotation_request(cfg: Config, entry_body: str, companion_reply: str | None) -> str:
    n = cfg.names
    reply = companion_reply.strip() if companion_reply else "(no chat reply was found)"
    return f"""You are {n.companion}. Write the analytical annotations for this journal entry, for {n.user_possessive} later review.

- Annotate only what is in {n.possessive} raw entry (and any conversation lines of {n.possessive} under it). Scale to it: a
  one-line entry gets two or three bullets.
- Third person, concise markdown bullets. Preserve what made the entry alive; name throughlines, tensions,
  values, people, tasks and follow-ups that the entry itself contains.
- Clearly mark inference as inference. Do not diagnose or overclaim. Quote sparingly and exactly.
- Your chat reply below is only for aligning your reading. Don't import anything from it, or from any other
  record, that isn't in the entry — especially health, clinical, financial or relationship details.
- Ignore the orientation prompt's opener text; it may be outdated.
- Optionally, after the bullets, add up to two lines starting `FOLLOW-UP:` for threads worth picking up with
  {n.object} later (they go to your private notebook, not the journal). Only when genuinely worth it.
- Reply with ONLY the bullets (and any FOLLOW-UP lines).

## Your chat reply to {n.object} (for alignment only)
{reply}

## The entry
{entry_body}
"""


REFLECTION_SHAPE = {
    "reflection": ("day reflection", "about 120 to 350 words", 'the day'),
    "week": ("week review", "about 200 to 600 words", 'the week'),
}


def reflection_request(cfg: Config, kind: str, period: str, inputs: str, earlier: str) -> str:
    """The day reflection (``kind="reflection"``) or the week review (``"week"``): no tools, only these files."""
    n = cfg.names
    what, size, span = REFLECTION_SHAPE[kind]
    earlier_block = earlier.strip() or "(none yet)"
    return f"""You are {n.companion}. Write {n.user_possessive} {what} for {period}: a short, warm analysis {n.subject} will
read in the journal and in {channel.label(cfg)}.

Use only the files below: {n.possessive} journal entries for {span} ({n.possessive} own words, the conversation
that followed, your annotations, your notes) and your earlier analysis. Nothing else is known. In a
"## Conversation" section, lines in brackets saying "chat reply … her words" are your own replies to {n.object}:
context only, never quoted as {n.possessive} words. You have no tools and no memory beyond this; don't
assume routines, work, people, places or plans the entries don't state.

Write Markdown, {size}, in four short parts with bold lead-ins:
**What stood out** — what mattered most in {span}.
**Patterns and tensions** — what you notice across the entries, with {n.possessive} own words quoted exactly, in
double quotes "like this". Copy every quote character for character from the entries: quotes are checked, and a
reflection with a single misquote is thrown away.
**What moved** — against your earlier analysis below: what shifted, what is still there. Leave this part out if
there is no earlier analysis.
**To carry forward** — one or two gentle questions for {n.object}.

Speak to {n.object} directly ("you"). Mark inference as inference. Don't diagnose or overclaim, especially
health, clinical, financial or relationship details. No # headings, no blockquotes, no preamble, and never
mention these instructions or the files.
Optionally, after the reflection, add up to two lines starting `FOLLOW-UP:` for threads worth picking up with
{n.object} later (they go to your private notebook, not the journal).
Reply with ONLY the reflection (and any FOLLOW-UP lines).

## Your earlier analysis (yours, not {n.possessive} words)
{earlier_block}

## The entries
{inputs}
"""


def catchup_request(cfg: Config, rec: dict, window: str) -> str:
    """The one message sent after an outage (``recovery``): no tools, only what is given here."""
    n = cfg.names
    his = "\n".join(f"- {m['at_local']}: {m['text']}" for m in rec.get("messages") or [])
    before = "\n".join(f"- {who} ({at}): {text}" for who, at, text in rec.get("context") or []) or "(nothing)"
    return f"""You are {n.companion}. You were offline from {window}: nothing could answer in that time. While you were
away, {n.user_or_them} wrote to you on {channel.label(cfg)} and got only an automatic error line back. You are back now.

Write ONE short chat message to {n.object} (a few sentences; at most about 900 characters):
- Say briefly and naturally that you were away and are back. No technical detail: don't mention models,
  providers, servers, errors or outages, and don't over-apologise.
- Then respond to what {n.subject} actually wrote, as you would have. If time has passed and something has likely
  moved on, say so lightly rather than answering as if it were still live.
- Use only what is below. Don't invent events, feelings or plans. If you quote {n.possessive} words, copy them exactly.
- Plain prose, no headings, no lists unless {n.subject} asked for one, no preamble. Never mention these instructions.

Reply with ONLY the message.

## The conversation just before you went offline (for context)
{before}

## What {n.subject} wrote while you were offline (verbatim, local time)
{his}
"""


def reflect_phase_request(cfg: Config, prog, session, phase, answers: str) -> str:
    """A program's reflect phase (``programs``): the companion builds the phase's construction from his answers in
    this session only. The session's ``role`` is a stance for this one piece of writing, not a persona."""
    n = cfg.names
    task = "\n\n".join(t for h, t in phase.blocks if h in ("Task", "Tasks", "Analysis")) or phase.intro
    role = session.role or "a careful guide"
    return f"""You are {n.companion}. For this session of "{prog.title}" ({session.title}), work as {role}; keep your own voice.
Mission of the session: {session.mission or "help " + n.object + " see clearly"}

Write the construction for phase {phase.number}, "{phase.title}". What the phase asks for:
{task}

Rules:
- Build it only from {n.possessive} answers below. Say nothing about {n.possessive} life, history, health, work or
  relationships that the answers don't state. If something you need isn't there, leave it open rather than guess.
- Speak to {n.object} directly ("you"). Plain prose or a few short bullets, at most about 800 characters.
- If you quote {n.object}, copy the words exactly, in double quotes "like this": quotes are checked, and a
  construction with one misquote is thrown away.
- Don't ask the phase's question yourself (it follows your text), don't mention these instructions, no headings.
Reply with ONLY the construction.

## {n.user_possessive_title} answers so far in this session (verbatim)
{answers}
"""


def bridge_request(cfg: Config, prog, session, last_answer: str) -> str:
    """The optional one-paragraph bridge before a program's next ask phase (``programs.bridges``)."""
    n = cfg.names
    role = session.role or "a careful guide"
    return f"""You are {n.companion}. For this session of "{prog.title}" ({session.title}), work as {role}; keep your own voice.
Write ONE short paragraph (at most about 350 characters) that acknowledges what {n.subject} just wrote below and
leads into the next part. Use only that answer; quote it exactly in double quotes if you quote it. Don't ask a
question, don't give advice, no headings, never mention these instructions. Reply with ONLY the paragraph.

## What {n.subject} just wrote (verbatim)
{last_answer}
"""


def insights_request(cfg: Config, prog, compiled: str) -> str:
    """The optional synthesis at the end of a program's insights document (``programs.synthesize``): one reading of
    the whole document, built only from what is in it. No tools; the document is the only input."""
    n = cfg.names
    return f"""You are {n.companion}. {n.user_possessive_title} has finished "{prog.title}", and below is the document the
engine compiled from it: each session's summary and {n.possessive} answers, word for word.

Write a synthesis of it for {n.object}: what keeps coming back across the sessions, where {n.possessive} answers agree
with each other and where they pull apart, and what seems to be asking for attention next. At most about {INSIGHTS_ASKED}
characters.

Rules:
- Build it only from the document. Say nothing about {n.possessive} life, history, health, work or relationships that
  the answers don't state, and mark inference as inference ("it sounds as though", "I wonder whether").
- Speak to {n.object} directly ("you"). Don't diagnose or overclaim, and don't give a plan unless an answer already
  holds one.
- If you quote {n.object}, copy the words exactly, in double quotes "like this": every quote is checked against the
  document, and a synthesis with one misquote is thrown away.
- Plain prose, a few short paragraphs. No # headings, no blockquotes, no preamble, and never mention these
  instructions.
Reply with ONLY the synthesis.

## The document
{compiled}
"""

