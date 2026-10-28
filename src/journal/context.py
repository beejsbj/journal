"""Context pack: a dated, sourced snapshot of the user's recent life.

Gathered by code before the companion writes an opener, so even a weak model has real material to
work from, and so the validator can check its quotes. Every source is read-only, time-bounded,
optional and configurable: a disabled source is left out, a failing one is listed as unavailable
rather than guessed at. The companion may read further with its own tools.
"""
from __future__ import annotations

import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import channel, conversations, entries, ledger, notebook, sources, standing
from .config import Config, pack_entries, pack_max_chars, pack_pointers
from .validate import CONVERSATION_HEADING, MINE, STANDING_HEADING

REFLECTION_DAYS = 3
MINE_DAYS = 3  # how far back the companion's own journal messages are shown
MINE_EACH = 450  # characters kept of each of them
MINE_CAP = 2400  # characters for all of them
NOTE_PACK_HOURS = 12  # a /j note gets the latest pack this fresh
TURN_PACK_CAP = 4000  # characters of pack injected into one turn (Hermes keeps it in the transcript)
TURN_SECTION_CAP = 900  # characters per pack section
TURN_JOURNAL_CAP = 400  # the raw journal excerpts: the companion can read the entries themselves
ENTRY_CAP = 1500  # characters of his words kept per entry
ENTRY_NOTES_CAP = 600  # characters of her annotations kept per entry
ANALYSIS_CAP = 1500  # characters kept of each analysis of hers
POINTERS_HEADING = "## Where to look further"
_PROMPT_SECTION = re.compile(r"^## Prompt[ \t]*\n.*?(?=^## |\Z)", re.S | re.M)


def clean_user_text(text: str) -> str:
    """His words alone: no gateway origin note, no quoted message he replied to, no journal hook note (the last
    two hold the companion's own text). The transcript adapter reads every row this way (``Row.meta["loose"]``);
    this is the same reading for text that did not come from a row."""
    from .hermes.transcript import clean_user_text as clean
    return clean(text)


def _one_line(text: str, cap: int = MINE_EACH) -> str:
    """One line (a model-written heading can never open a pack section), shortened to ``cap``."""
    text = " ".join((text or "").split())
    return text if len(text) <= cap else text[:cap].rsplit(" ", 1)[0] + " …"


def prompt_state(cfg: Config, p: dict, now_local: datetime) -> str:
    if p.get("captures"):
        return "answered"
    from .capture import window
    sent = datetime.fromisoformat(p["sent_at"]).astimezone(cfg.tz)
    return "missed, never answered" if now_local - sent >= window(cfg, p) else "no reply yet"


def unanswered(cfg: Config, now_local: datetime, limit: int | None = None) -> list[dict]:
    """The run of most recent practice prompts that got no reply, newest first: back to the newest-sent one he
    answered, at most ``pack.lookback_days`` (``standing.unanswered``); [] when the ledger can't be read.

    Stops at the first answered one: "his last two prompts went unanswered" is a signal for the next opener."""
    try:
        run = standing.unanswered(cfg, now_local)[0]
    except Exception:  # noqa: BLE001 - a ledger that can't be read loses the count, never the prompt
        run = []
    return run if limit is None else run[:limit]


def her_messages(cfg: Config, now_local: datetime) -> list[tuple[datetime, str]]:
    """What the journal sent as the companion over the last ``MINE_DAYS`` days: prompts (with whether they
    were answered), annotation replies, reflections, catch-ups after an outage. Newest kept within
    ``MINE_CAP``. Lines start with ``validate.MINE``: her words, not his."""
    from . import ledger, library, reflect
    cutoff = now_local - timedelta(days=MINE_DAYS)
    data = ledger.snapshot(cfg.ledger_path)
    rows: list[tuple[datetime, str]] = []

    def when(iso: str | None) -> datetime | None:
        try:
            at = datetime.fromisoformat(iso).astimezone(cfg.tz) if iso else None
        except ValueError:
            return None
        return at if at is not None and cutoff <= at <= now_local + timedelta(minutes=5) else None

    def add(at: datetime, what: str, text: str) -> None:
        rows.append((at, f"{MINE}{at.strftime('%a %-d %b %H:%M')} · {what}: {_one_line(text)}"))

    for p in data["prompts"]:
        at = when(p.get("sent_at")) if p.get("message_id") else None
        if at and p.get("kind") not in entries.FREEFORM_IDS:
            plain = str(p.get("opener_source") or "").startswith("plain")
            opener = "(the stock opener)" if plain else (p.get("opener") or "").strip()
            title = library.for_prompt(cfg, p).title
            add(at, f"{title} prompt · {prompt_state(cfg, p, now_local)}", opener or "(no opener)")
        reply = p.get("annotation_reply") or {}
        at = when(reply.get("sent_at")) if reply.get("message_id") else None
        path = Path(p.get("entry_path") or "")
        if at and path.is_file():
            what = "your note" if p.get("kind") in ("j", "note") else f"{cfg.names.possessive} {p.get('kind')} entry"
            add(at, f"your annotations on {what} (a reply to it)",
                entries.annotations(path.read_text(encoding="utf-8", errors="replace")))
    for r in data.get("reflections") or []:
        tg = r.get("telegram") or {}
        at = when(tg.get("sent_at")) if tg.get("message_id") else None
        path = Path(r.get("path") or "")
        if at and path.is_file():
            label = "your day reflection" if r.get("kind") == "reflection" else "your week review"
            add(at, label, reflect.body_of(path.read_text(encoding="utf-8", errors="replace")))
    for r in data.get("recovery") or []:
        at = when(r.get("sent_at")) if r.get("message_id") else None
        if at:
            add(at, "your reply after being offline", r.get("text") or "")
    out, used = [], 0
    for at, line in sorted(rows, key=lambda r: r[0], reverse=True):
        if used + len(line) > MINE_CAP:
            break
        out.append((at, line))
        used += len(line) + 1
    return out


def recent_openers(cfg: Config, now_local: datetime) -> list[str]:
    """Her recent prompt lines alone, newest first (status and tests)."""
    return [l for _, l in her_messages(cfg, now_local) if " prompt · " in l]


def conversation_rows(cfg: Config, now_local: datetime,
                      db: Path | None = None) -> tuple[list[tuple[datetime, str]], str | None]:
    """(his recent Telegram messages and her journal messages, dated, oldest first; a note when his could not be
    read). His lines are citable (``telegram:<time>``); hers start with ``validate.MINE`` and never are."""
    rows = list(her_messages(cfg, now_local))
    note = None
    if cfg.source_enabled("telegram_history") and cfg.transcript(db).source is not None:
        try:
            rows += _his_messages(cfg, db, now_local)  # the pack's own now (a test's fixed one too)
        except Exception as e:  # a failing source must never block the prompt
            note = (f"_{cfg.names.user_possessive} {channel.label(cfg)} messages unavailable: {type(e).__name__}: "
                    f"{str(e)[:120]}_")
    return sorted(rows, key=lambda r: r[0]), note


def conversation(cfg: Config, now_local: datetime, db: Path | None = None) -> list[str]:
    """His recent Telegram messages and her journal messages, interleaved, oldest first.

    His lines are citable (``telegram:<time>``); hers start with ``validate.MINE`` and never are."""
    rows, note = conversation_rows(cfg, now_local, db)
    return [line for _, line in rows] + ([note] if note else [])


def entry_words(body: str) -> str:
    """His words in an entry (and his conversation lines; never her folded replies), or the older formats' raw
    entry; "" when there are none."""
    text = entries.his_words(body)
    if not text:  # older formats
        m = re.search(r"## Raw Entry\n(.*?)(?=\n## |\Z)", body, re.S)
        text = m.group(1).strip() if m else ""
    return text


def _end_of(d, cfg: Config) -> datetime:
    """The last moment of local day ``d``: a dated item's place among timed ones (his entry of a day outlasts that
    day's chat lines when the far end is dropped)."""
    return datetime.combine(d, datetime.max.time().replace(microsecond=0), tzinfo=cfg.tz)


def entry_items(cfg: Config, now_local: datetime | None = None,
                limit: int | None = None) -> list[tuple[datetime | None, str]]:
    """His newest ``limit`` entries (``pack.entries``), newest first and whatever their age, each as (its day's end,
    the pack text). With ``now_local`` each heading carries its date and age (``journal:<stem> (Thu 9 Oct, 12 days
    ago)``), so a few entries spread over two months reach her with their spacing visible."""
    limit = pack_entries(cfg) if limit is None else limit
    found = []
    for p in cfg.root.glob("*.md"):
        kind = entries.kind_of(p)
        if not kind or kind in ("note", *entries.ANALYSIS_KINDS):
            continue
        body = p.read_text(encoding="utf-8", errors="replace")
        text = entry_words(body)  # his words and his conversation lines; never her folded replies
        if text:
            found.append((p.stem, text, entries.annotations(body)))
    out = []
    for stem, text, notes in sorted(found, reverse=True)[:limit]:
        day = standing._stem_date(stem)
        label = f" ({standing.dated(day, now_local.date())})" if day and now_local is not None else ""
        out.append((_end_of(day, cfg) if day else None,
                    f"### journal:{stem}{label}\n{text[:ENTRY_CAP]}\n\n_{cfg.names.companion}'s annotations:_ "
                    f"{notes[:ENTRY_NOTES_CAP]}"))
    return out


def journal_entries(cfg: Config, limit: int | None = None, now_local: datetime | None = None) -> list[str]:
    """Newest engine entries (the user's words + annotations), newest first (``entry_items``)."""
    return [text for _, text in entry_items(cfg, now_local, limit)]


def latest_week(cfg: Config) -> Path | None:
    """Her newest week review, whatever its age."""
    found = sorted((p for p in cfg.root.glob("*-week*.md") if entries.is_analysis(p)),
                   key=lambda p: (p.name[:10], p.name))
    return found[-1] if found else None


def analysis_items(cfg: Config, now_local: datetime) -> list[tuple[datetime | None, str]]:
    """The latest day reflection (if within ``REFLECTION_DAYS``) and her latest week review whatever its age,
    dated and labelled as hers."""
    from . import reflect
    out = []
    today = now_local.date()
    for kind in ("reflection", "week"):
        path = reflect.latest(cfg, kind, today, REFLECTION_DAYS) if kind == "reflection" else latest_week(cfg)
        if path is None:
            continue
        body = reflect.body_of(path.read_text(encoding="utf-8", errors="replace"))
        # a heading of hers (the model wrote it) must not open a pack section that isn't labelled as hers
        body = re.sub(r"(?m)^[ \t]{0,3}#{1,6}[ \t]+", "", body)
        if len(body) > ANALYSIS_CAP:
            body = body[:ANALYSIS_CAP].rsplit(" ", 1)[0] + " …"
        day = standing._stem_date(path.stem)
        if kind == "reflection":
            label = "your day reflection"
        else:
            ending = f", for the week ending {standing.dated(day, today)}" if day else ""
            label = f"your week review{ending}"
        out.append((_end_of(day, cfg) if day else None, f"### {kind}:{path.stem} ({label})\n{body}"))
    return out


def earlier_analysis(cfg: Config, now_local: datetime) -> list[str]:
    """The latest day reflection (if within REFLECTION_DAYS) and the latest week review, clearly the companion's."""
    return [text for _, text in analysis_items(cfg, now_local)]


def _his_messages(cfg: Config, db: Path | None = None, now: datetime | None = None) -> list[tuple[datetime, str]]:
    """His newest chat messages (``telegram_history``: the ``days`` before ``now``, the wall clock when not given; at
    most ``limit`` of the ones it shows), oldest first, as pack lines. Deliveries into the chat and the journal's
    mirrors are not his messages; off-the-record ones are left out by the privacy pass over the whole horizon."""
    src = cfg.source("telegram_history")
    days, limit = int(src.get("days", 7)), int(src.get("limit", 25))
    chat = cfg.transcript(db)
    if chat.source is None:
        raise FileNotFoundError("no Hermes sessions database configured (hermes.state_db)")
    since = ((now.astimezone(timezone.utc) if now is not None else datetime.now(timezone.utc))
             - timedelta(days=days))
    from . import privacy
    pv = privacy.covering(cfg, since)  # its whole horizon, not the stamp's
    if pv.closed:
        raise RuntimeError(f"the privacy pass could not run ({getattr(pv, 'why', '')})")
    found = [r for r in chat.rows(since=since) if r.role == "user" and r.at > since and not r.meta.get("delivery")]
    off = conversations.snapshot(cfg)
    if not conversations.journals_all_chat(cfg):
        # (arm) ordinary chat is not journal material, and a pack is what an opener or an annotation is written from
        # and then saved into the entry (``## Prompt``), where the quote checker would let it be quoted verbatim:
        # only what the journal itself kept (a reply, a note, a message in a saved thread) is read back
        data = ledger.snapshot(cfg.ledger_path)
        kept = off.get("written") or {}
        found = [r for r in found if r.kind == "handled"  # (the hook's own note: it saved this one)
                 or (r.platform_message_id and ledger.capture_of(data, r.platform_message_id))
                 or (r.platform_message_id or f"row:{r.meta.get('uid') or r.id}") in kept]
    out = []
    # the newest ``limit`` of what can be shown: the limit is counted after the mirrors, the off-the-record rows and
    # the rows with no words are left out, so a burst of /x messages does not push his public ones out of the pack
    for r in sorted(found, key=lambda r: r.at, reverse=True):
        if len(out) >= limit:
            break
        skip = r.kind == "mirror" or conversations.row_is_off(cfg, pv, r, off)
        text = "" if skip else r.meta.get("loose") or ""
        if text:
            at = r.at.astimezone(cfg.tz)
            out.append((at, f"- {channel.cite(cfg)}:{at.isoformat(timespec='minutes')} — {text[:700]}"))
    return out[::-1]


def telegram_messages(cfg: Config, db: Path | None = None) -> list[str]:
    """His recent Telegram messages to the companion, oldest first (the journal's own mirrored sends excluded)."""
    return [line for _, line in _his_messages(cfg, db)]


def his_text(path: Path) -> str:
    """What in a journal file is his, for the quote check: an engine entry's words section and his conversation
    lines (``entry_words``), never her annotations, her notes, her folded replies, the opener under ``## Prompt`` or
    the engine's summary; a file of his own (no engine entry, not an analysis) whole, without its front matter;
    nothing from a file the companion or the engine wrote (a reflection, a week review, an insights document)."""
    if entries.authored_by_engine(path):
        return ""
    body = path.read_text(encoding="utf-8", errors="replace")
    if entries.kind_of(path) is not None:
        return entry_words(body)
    return re.sub(r"\A---\n.*?\n---\n", "", body, count=1, flags=re.S)


def _his_chat_text(row) -> str | None:
    """What of a chat row is his words for the quote check: the rows ``standing._his_message`` counts as his (not a
    cron delivery, a compaction summary, a platform notice or the journal's mirror), and of a picture only his
    caption (the vision description beside it is the model's); None for any other row."""
    if not standing._his_message(row):
        return None
    if row.kind == "system":
        return str(row.meta.get("caption") or "")
    return str(row.meta.get("loose") or "")


def make_verifier(cfg: Config, db: Path | None = None):
    """Check a quote against *his* words in the source it cites, not just the pack. ``journal:<file>`` (the name read
    as its leading token, so a copied heading works) and a ``brain:`` path inside the journal folder are checked
    against his words in that file (``his_text``); ``telegram:`` against his public chat messages (``_his_chat_text``;
    the privacy pass decides public). Quotes and records are folded alike (``validate.fold``: typography, case,
    spaces, end punctuation). Her words never verify as his.

    When a quote is not in the file it cites but is in his words in exactly one other file, it is his and it
    passes; ``verify.notes`` gets a ``cited-elsewhere`` line naming both files (never the quote), for the tick's log.
    Returns None for a ``brain:`` path outside the journal (not checked, as any other named source)."""
    from .validate import fold, parse_source, source_stem
    from .validate import _plain as plain
    cache: dict = {}
    chat = cfg.transcript(db)
    root = cfg.root.resolve()
    notes: list[str] = []

    def his_of(path: Path) -> str:
        key = ("file", str(path))
        if key not in cache:
            cache[key] = plain(his_text(path)) if path.is_file() else ""
        return cache[key]

    def everything() -> dict:
        if "all" not in cache:
            cache["all"] = {p: his_of(p) for p in sorted(cfg.root.rglob("*.md"))}
        return cache["all"]

    def named(stem: str) -> list[Path]:
        if not stem:
            return []
        first = cfg.root / f"{stem}.md"
        return [first] if first.is_file() else sorted(cfg.root.rglob(f"{stem}.md"))

    def in_journal(rest: str) -> Path | None:
        """A brain: path that lands inside the journal folder (absolute, or relative to the vault above the journal or
        to the journal itself), else None."""
        raw = rest.strip()
        if not raw:
            return None
        for base in ((Path(raw),) if Path(raw).is_absolute() else (root.parent / raw, root / raw)):
            try:
                path = base.resolve()
            except OSError:
                continue
            if path.suffix != ".md" and path.with_suffix(".md").is_file():
                path = path.with_suffix(".md")
            if path != root and path.is_relative_to(root) and (base != root / raw or path.is_file()):
                return path  # (read against the journal itself only when that file is there)
        return None

    def telegram() -> str:
        if "telegram" not in cache:
            texts = []
            if chat.source is not None and chat.available():
                from . import privacy
                found = [r for r in chat.rows() if r.role == "user"]
                # every row it reads, however old (closed: every row is private, nothing is verifiable)
                oldest = min((r.at for r in found), default=None)
                pv = privacy.covering(cfg, oldest)
                off = conversations.snapshot(cfg)
                for r in found:
                    said = _his_chat_text(r)
                    if said and not conversations.row_is_off(cfg, pv, r, off):  # /x
                        texts.append(said)
            cache["telegram"] = plain("\n".join(texts))
        return cache["telegram"]

    def verify(source: str, quote_norm: str) -> bool | None:
        q = fold(quote_norm)
        if not q:
            return False
        kind, rest = parse_source(source)
        try:
            if kind in ("telegram", "chat"):  # his chat, whatever the platform (``channel.cite``)
                return q in telegram()
            if kind == "brain":
                path = in_journal(rest)
                if path is None:
                    return None
                cited = [path]
            elif kind == "journal":
                cited = named(source_stem(rest))
                if len(cited) > 1:  # two nested files of one name: neither vouches for the other
                    cited = []
            else:
                return False
            if len(cited) == 1 and q in his_of(cited[0]):
                return True
            # his words, filed under another name: still his (decided 2026-10-26), if in exactly one file
            others = [p for p, text in everything().items() if p not in cited and q in text]
            if len(others) == 1:
                notes.append(f"cited-elsewhere: {source.strip()[:80]} is in journal:{others[0].stem}")
                return True
            return False
        except (OSError, *chat.errors):
            return False

    verify.notes = notes
    return verify


def companion_reply_after(cfg: Config, ts: datetime, db: Path | None = None, chat_id: str | None = None,
                          platform: str | None = None) -> str | None:
    """The companion's first plain chat reply within an hour of ``ts`` in ``chat_id`` (the prompt's recorded
    chat; default the configured one), to align annotations. A prompt recorded in another chat reads nothing: the
    privacy pass does not compute that chat."""
    chat = cfg.transcript(db)
    if chat.source is None:
        return None
    if (chat_id is not None and str(chat_id) != str(cfg.chat_id)) or (platform is not None and platform != cfg.platform):
        # the privacy pass computes the configured chat only, and her rows carry no mark of their own: in another
        # chat a reply in a turn that took in an /x message would read as public. No alignment text, then.
        return None
    if ts.tzinfo is None:
        ts = ts.astimezone(timezone.utc)  # a naive time is local time (as ``timestamp()`` read it)
    end = ts + timedelta(hours=1)
    try:
        found = sorted((r for r in chat.rows(since=ts)
                        if r.role == "assistant" and ts < r.at < end and r.meta.get("reply")),
                       key=lambda r: (r.at, r.id))
    except chat.errors:
        return None
    from . import privacy
    pv = privacy.covering(cfg, ts)  # the prompt can be days old: the rows it reads, not the stamp, set the horizon
    if pv.closed:
        return None  # can't tell what was private: no alignment text rather than a private one
    for r in found:
        # the pass marks her rows private through the whole turn that took in a private message of his (a public
        # message steered into the same turn included), and inside a bare-/x window
        if not conversations.row_is_off(cfg, pv, r):
            return r.stored
    return None


OPENER_CAP = 1200  # characters of each unanswered opener quoted (most are whole; a weekly one may be cut)
POINTER_CAP = 320  # characters of each ``pack.pointers`` line
NOT_CONNECTED_CAP = 600  # characters of ``sources.not_connected``


def _opener_key(cfg: Config, p: dict) -> str:
    """How her line for prompt ``p`` begins, in the openers section and in the conversation section alike."""
    at = datetime.fromisoformat(p["sent_at"]).astimezone(cfg.tz)
    return f"{MINE}{at.strftime('%a %-d %b %H:%M')} · {standing._title(cfg, p)} prompt"


def missed_openers(cfg: Config, now_local: datetime, run: list[dict] | None = None) -> list[str]:
    """Her openers of the last ``standing.MISSED_OPENERS`` unanswered prompts (``unanswered``), newest first, word
    for word on one line each, up to ``OPENER_CAP`` characters (so two long weekly openers cannot crowd his words out
    of a small budget). Lines start with ``validate.MINE``: her words, never evidence. A record that can't be read is
    skipped."""
    run = unanswered(cfg, now_local) if run is None else run
    out = []
    for p in run[:standing.MISSED_OPENERS]:
        try:
            plain = str(p.get("opener_source") or "").startswith("plain")
            opener = "(the stock opener)" if plain else str(p.get("opener") or "").strip() or "(no opener)"
            out.append(f"{_opener_key(cfg, p)}: {_one_line(opener, OPENER_CAP)}")
        except Exception:  # noqa: BLE001 - one odd record costs its line
            continue
    return out


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _source_pointer(cfg: Config, name: str, n) -> str | None:
    """One line for a connected source: what it holds, how far back the pack reads it, how to reach the rest."""
    src = cfg.source(name)
    if name == "weather":
        coords = cfg.raw.get("coordinates") if isinstance(cfg.raw.get("coordinates"), dict) else {}
        where = coords.get("place") or cfg.location or (
            f"{cfg.coordinates[0]}, {cfg.coordinates[1]}" if cfg.coordinates else f"{n.possessive} location")
        return (f"- Weather: the Open-Meteo forecast for {where}; the pack has now, today and tomorrow (a forecast: "
                "there is no history behind it). Cite as weather:<now|today|tomorrow>.")
    if name == "tududi":
        cmd = " ".join(sources._argv(src.get("command")))
        return (f"- Tasks and habits (tududi): the pack lists open and due tasks, habits and what was done in the "
                f"last 7 days; the rest of tududi (older done tasks, projects, notes) through your tududi tools (the "
                f"journal reads it with `{cmd}`). Cite as tududi:<task>.")
    if name == "scrobbler":
        cmd = " ".join(sources._argv(src.get("command") or "chat-scrobbler"))
        return (f"- {n.possessive.capitalize()} AI chats (chat-scrobbler): the pack has sessions updated in the last "
                f"3 days; older ones through `{cmd} list` and its search. Cite as scrobbler:<session>.")
    if name == "listenbrainz":
        user = str(src.get("user") or "").strip()
        return (f"- Listening (ListenBrainz, user {user}): the pack has the last 2 days; older listens through "
                f"https://api.listenbrainz.org/1/user/{user}/listens (max_ts pages back). Cite as listenbrainz:<time>.")
    return None


def _his_entry_days(cfg: Config) -> list:
    """The dates of his entries with words in them: the set the entries section reads (``entry_items``), so the
    pointer's count is his alone (not her notes, not the analyses, not an entry with no words of his)."""
    out = []
    for p in cfg.root.glob("*.md"):
        kind = entries.kind_of(p)
        day = standing._stem_date(p.stem)
        if not kind or kind in ("note", *entries.ANALYSIS_KINDS) or day is None:
            continue
        try:
            if entry_words(p.read_text(encoding="utf-8", errors="replace")):
                out.append(day)
        except OSError:
            continue
    return out


def _shown(n: int) -> str:
    return f"the pack has the newest {n}"


def pointers(cfg: Config, now_local: datetime) -> list[str]:
    """The pack's last section: what it leaves out and how to reach it, one line each, made from the config and
    from what is on disk (never a fixed list): his older entries and how far back they go, her earlier analyses,
    the program documents, her notebook's archive (what it holds, never where: it sits in the engine's state
    directory), and each source that is connected (enabled, and its static check passes: ``sources.check``), then
    ``pack.pointers`` (each cut to ``POINTER_CAP``). No pointer into his chat history (DECISIONS.md, CONTEXT-PACK)."""
    n = cfg.names
    today = now_local.date()
    out = []
    his = _his_entry_days(cfg)
    analyses: dict[str, list] = {"week": [], "reflection": []}
    for p in cfg.root.glob("*.md"):
        kind = entries.kind_of(p)
        day = standing._stem_date(p.stem)
        if kind in analyses and day is not None:
            analyses[kind].append(day)
    if his:
        out.append(f"- Older journal entries: one Markdown file per entry in {cfg.root} (YYYY-MM-DD-<practice>.md); "
                   f"{_count(len(his), 'entry', 'entries')} of {n.user_possessive} back to "
                   f"{standing.day_label(min(his), today)}; {_shown(min(len(his), pack_entries(cfg)))}. Read them "
                   f"with your file tools; cite one as journal:<file name without .md>.")
    if analyses["week"] or analyses["reflection"]:
        kinds = [f"{_count(len(analyses[k]), label)} back to {standing.day_label(min(analyses[k]), today)}"
                 for k, label in (("week", "week review"), ("reflection", "day reflection")) if analyses[k]]
        out.append(f"- Your earlier analysis ({'; '.join(kinds)}): in {cfg.root} as YYYY-MM-DD-week.md and "
                   f"YYYY-MM-DD-reflection.md. Yours, not {n.user_possessive} words; cite as week:<file name> or "
                   f"reflection:<file name>.")
    try:
        from . import programs
        if programs.all_runs(cfg):
            out.append(f"- Programs: each session {n.subject} {n.verb('work')} through is an entry in {cfg.root} "
                       f"(YYYY-MM-DD-<program>-sNN.md); a finished program has an insights document "
                       f"(…-insights.md, compiled by the journal). journal_program (action status) says what is running.")
    except Exception:  # noqa: BLE001 - a pointer is never worth failing the pack over
        pass
    try:
        items = [line for line in (cfg.state_dir / "notebook-archive.jsonl").read_text(encoding="utf-8").splitlines()
                 if line.strip()]
    except OSError:
        items = []
    if items:
        import json
        days = []
        for line in items:
            try:
                days.append(datetime.fromisoformat(json.loads(line)["created_at"]).astimezone(cfg.tz).date())
            except (ValueError, KeyError, TypeError):
                continue
        back = f", back to {standing.day_label(min(days), today)}" if days else ""
        out.append(f"- Your notebook's archive: {_count(len(items), 'thread')} resolved or gone stale{back}, not in "
                   f"this pack. Your open threads are above and through journal_notebook (action list).")
    connected = {name for name, ok, _ in sources.check(cfg) if ok}
    for name in ("weather", "tududi", "scrobbler", "listenbrainz"):
        line = _source_pointer(cfg, name, n) if name in connected else None
        if line:
            out.append(line)
    readers = [r for r in sources.READERS if r in connected]
    if readers:
        where = []
        for r in readers:
            src = cfg.source(r)
            where.append(f"{r.capitalize()} ({src.get('container') or src.get('db')})")
        out.append(f"- Reading: the pack has what moved in the last 30 days; the full progress history is in "
                   f"{', '.join(where)}, read-only. Cite as book:<title>.")
    out += [f"- {_one_line(line, POINTER_CAP)}" for line in pack_pointers(cfg)]
    return out


_ONE = {"entries": "entry", "lines": "line", "analyses": "analysis", "items": "item", "sections": "section"}
NEVER = None  # an item's tier when the budget may not drop it


class _Part:
    """One pack section: a heading, an optional intro line, and items as ``[at, text, tier]``. ``tier`` orders what
    the budget drops (``_fit``): 0 her conversation lines that repeat an opener quoted above, 1 her other
    conversation lines, 2 dated items (his chat lines, his entries but the newest, her analyses) oldest first,
    3 current sections from the pack's end, 4 pointer lines from the end; ``NEVER`` stays."""

    def __init__(self, title: str, items: list | None = None, intro: str = "", empty: str = "_nothing recent_",
                 sep: str = "\n", what: str = "items", tier: int | None = 3):
        self.title, self.intro, self.empty, self.sep, self.what = title, intro, empty, sep, what
        self.items: list[list] = []
        for x in items or []:
            at, text = x if isinstance(x, tuple) else (None, x)
            self.items.append([at, text, 3 if tier == 2 and at is None else tier])  # (undated: current)
        self.dated = any(at is not None for at, _t, _k in self.items)
        self.dropped = 0

    def counted(self) -> str:
        return f"{self.dropped} {self.what if self.dropped != 1 else _ONE.get(self.what, self.what)}"

    def render(self) -> str:
        body = self.sep.join(t for _a, t, _k in self.items)
        if self.dropped:
            older = "older " if self.dated else ""
            body += (self.sep if body else "") + (f"_{older}{self.counted()} left out for length; the last section "
                                                   f"says where to find more._")
        body = body.strip() or self.empty
        return f"## {self.title}\n" + (f"{self.intro}\n" if self.intro else "") + body


def _join(head: list[str], parts: list[_Part]) -> str:
    return "\n\n".join(head + [p.render() for p in parts]) + "\n"


def _fit(head: list[str], parts: list[_Part], budget: int) -> None:
    """Drop items until the pack fits ``budget``, by tier (``_Part``): her repeated lines, her other conversation
    lines, then the oldest dated item anywhere (the far end), then current sections and pointer lines from the end.
    Where things stand, her quoted openers, *not connected* and his newest entry are never dropped."""
    while len(_join(head, parts)) > budget:
        found, order = [], 0
        for i, part in enumerate(parts):
            for j, (at, _t, tier) in enumerate(part.items):
                order += 1
                if tier is NEVER:
                    continue
                if tier <= 2:
                    key = (tier, at.timestamp() if at is not None else float("-inf"), 0)
                else:
                    key = (tier, 0.0, -order)
                found.append((key, i, j))
        if not found:
            return
        _key, i, j = min(found, key=lambda x: x[0])
        parts[i].items.pop(j)
        parts[i].dropped += 1


def _cut(text: str, budget: int) -> str:
    """The last resort: whole lines off the end until the pack fits, then the one line left over cut short."""
    lines = text.rstrip("\n").split("\n")
    while lines and len("\n".join(lines)) + 5 > budget:
        if len(lines) == 1:
            lines[0] = lines[0][:budget - 6]
            break
        lines.pop()
    return "\n".join(lines) + "\n[…]\n"


def build(cfg: Config, now_local: datetime, stand: dict | None = None) -> str:
    """The pack an opener is written from. ``stand`` is ``standing.collect``'s answer when the caller has it (the
    tick asks once, for the pack and the request); None collects it here.

    In order: where things stand (code, no model); her openers that got no reply, word for word; his newest entries
    by count; the recent conversation; her earlier analysis; her notebook; each enabled source; what is not
    connected; and where to look further. Its whole is held to ``pack.max_chars`` (``_fit``): her conversation lines
    go first, then the oldest dated material, then current sections and pointer lines from the end; where things
    stand, the openers, *not connected* and his newest entry always stay, and a last cut at the budget means the
    pack can never grow without bound."""
    n = cfg.names
    head = [
        "# Context pack",
        f"Generated {now_local.isoformat(timespec='minutes')} ({cfg.timezone}). "
        f"{n.user_possessive_title} current location per journal config: "
        f"{cfg.location or 'not set'}.",
        "Only the facts below (and anything you read yourself in this run) are known. "
        "Do not assume routines, work, or travel that aren't stated here.",
    ]
    parts: list[_Part] = []

    def part(title: str, fn, **kw) -> _Part:
        """A part from ``fn``: a list of (at, text) items, a list of lines (undated), or a string."""
        try:
            got = fn()
            if isinstance(got, list):
                items = list(got)
            else:
                items = [str(got)] if str(got).strip() else []
            made = _Part(title, items, **kw)
        except Exception as e:  # a source failing must never block the prompt
            made = _Part(title, [f"_unavailable: {type(e).__name__}: {str(e)[:120]}_"], **kw)
        parts.append(made)
        return made

    if stand is None:
        try:
            stand = standing.collect(cfg, now_local)
        except Exception as e:  # noqa: BLE001 - the section says so; the rest of the pack goes on
            parts.append(_Part(STANDING_HEADING[3:], [f"_unavailable: {type(e).__name__}: {str(e)[:120]}_"],
                               tier=NEVER))
    if stand is not None:
        part(STANDING_HEADING[3:], lambda: standing.pack_lines(cfg, stand), tier=NEVER)
    run = (stand or {}).get("missed", {}).get("run") or []
    repeated = set()
    if run:
        shown = min(len(run), standing.MISSED_OPENERS)
        quoted = part("Your openers that got no reply (your own words, word for word, newest first)",
                      lambda: missed_openers(cfg, now_local, run), tier=NEVER,
                      intro=f"_The last {shown} of the {len(run)} prompts unanswered (see above). Don't repeat "
                            f"them, their lines or their questions. Your words, not {n.user_possessive}: never "
                            f"evidence._")
        repeated = {t.split(": ", 1)[0] for _a, t, _k in quoted.items}
    found = part("Recent journal entries (newest first, by count whatever their age)",
                 lambda: entry_items(cfg, now_local), sep="\n\n", what="entries", tier=2)
    if found.items and found.items[0][0] is not None:
        found.items[0][2] = NEVER  # his newest entry always stays
    his = cfg.source_enabled("telegram_history") and cfg.transcript().source is not None
    days = int(cfg.source("telegram_history").get("days", 7))

    def chat():
        rows, note = conversation_rows(cfg, now_local)
        return rows + ([note] if note else [])

    talk = part(f"{CONVERSATION_HEADING[3:]} with {n.object}, oldest first: "
                + (f"{n.possessive} {channel.label(cfg)} messages (last {days} days) and " if his else "")
                + f"what you sent {n.object} through the journal (last {MINE_DAYS} days). Lines starting "
                  f"\"{MINE.strip()[2:]}\" are your own words, not {n.user_possessive}: never evidence of anything "
                  f"{n.subject} said", chat, what="lines", tier=2)
    for item in talk.items:
        if item[1].startswith(MINE):
            item[2] = 0 if any(item[1].startswith(k + " · ") for k in repeated) else 1
    part(f"Your earlier analysis (your own reflections, not {n.user_possessive} words)",
         lambda: analysis_items(cfg, now_local), sep="\n\n", what="analyses", tier=2)
    part("Your private notebook (open threads)", lambda: notebook.render(cfg, now_local.date().isoformat()) or "_empty_",
         what="sections")
    for name, title, fn in sources.ALL:
        if name == "reading":
            if not any(cfg.source_enabled(s) for s in sources.READERS):
                continue
        elif not cfg.source_enabled(name):
            continue
        part(title, lambda fn=fn: "\n".join(fn(cfg, now_local)), what="sections")
    gaps = " ".join(str(cfg.sources.get("not_connected") or "").split())
    if gaps:
        parts.append(_Part("Not connected", [_one_line(gaps, NOT_CONNECTED_CAP)], tier=NEVER))
    budget = pack_max_chars(cfg)
    look = _Part(POINTERS_HEADING[3:], intro=(
        "_What this pack leaves out, and how to reach it with your own read-only tools. Whatever you read there is "
        f"cited like the rest, and quotes from journal entries and {channel.label(cfg)} are checked against the real "
        "records._"),
        empty="_nothing beyond the pack_", what="lines", tier=4)
    look_ok = True
    try:
        look.items = [[None, line, 4] for line in pointers(cfg, now_local)]
    except Exception as e:  # noqa: BLE001
        look_ok = False
        look.items = [[None, f"_unavailable: {type(e).__name__}: {str(e)[:120]}_", NEVER]]
    parts.append(look)
    note_room = 300  # the line saying what the budget left out, added after the fit
    _fit(head, parts, budget - note_room)
    if look_ok and found.dropped:  # the entries pointer says how many the pack really carries
        for item in look.items:
            if item[1].startswith("- Older journal entries:"):
                kept = len(found.items)
                item[1] = re.sub(r"the pack has the newest \d+",
                                 f"{_shown(kept)} (the length budget left out {found.dropped} more)", item[1])
    gone = [f"{p.counted()} ({p.title.split(' (')[0].split(',')[0]})" for p in parts if p.dropped and p is not look]
    if gone and look_ok:
        note = (f"_This pack is held to {budget} characters: {'; '.join(gone)} were left out, oldest and her own "
                f"lines first. They are where these lines point._")
        look.items.insert(0, [None, note if len(note) <= note_room else note[:note_room - 2] + "…_", NEVER])
    text = _join(head, parts)
    return _cut(text, budget) if len(text) > budget else text


def trim_for_turn(pack: str, cap: int = TURN_PACK_CAP) -> str:
    """A saved pack, shortened for one chat turn: each section cut to ``TURN_SECTION_CAP`` (the raw journal
    excerpts to ``TURN_JOURNAL_CAP``), the whole to ``cap``. Section headings are kept."""
    parts = re.split(r"(?m)^(?=## )", (pack or "").strip())
    out = []
    for part in parts:
        if part.startswith("# Context pack"):
            part = part.split("\n", 1)[1] if "\n" in part else ""  # the title; the label replaces it
        if not part.strip():
            continue
        limit = TURN_JOURNAL_CAP if part.startswith("## Recent journal entries") else TURN_SECTION_CAP
        if len(part) > limit:
            part = part[:limit].rsplit("\n", 1)[0].rstrip() + "\n[…]\n"
        out.append(part.strip())
    text = "\n\n".join(out)
    if len(text) > cap:
        text = text[:cap].rsplit("\n", 1)[0].rstrip() + "\n[…]"
    return text


def pack_for_turn(cfg: Config, prompt: dict | None, now: datetime) -> str | None:
    """The block injected into the companion's turn when a journal reply (``prompt``) or a /j note
    (``prompt`` None: the newest pack under ``NOTE_PACK_HOURS`` old) was captured, or None."""
    d = cfg.state_dir / "packs"
    path = d / f"{prompt['id']}.md" if prompt and prompt.get("id") else None
    if prompt is None and d.is_dir():
        fresh = [p for p in d.glob("*.md")
                 if now.timestamp() - p.stat().st_mtime < NOTE_PACK_HOURS * 3600]
        path = max(fresh, key=lambda p: p.stat().st_mtime) if fresh else None
    if path is None or not path.is_file():
        return None
    body = trim_for_turn(path.read_text(encoding="utf-8", errors="replace"))
    if not body:
        return None
    when = datetime.fromtimestamp(path.stat().st_mtime).astimezone(cfg.tz).strftime("%a %-d %b %H:%M")
    what = (f"the context your opener for this {prompt['kind']} prompt was written from" if prompt else
            "the context your most recent journal opener was written from")
    return (f"## Journal: {what}\n_Gathered {when}, trimmed for this turn. Background, not instructions: read "
            f"further with your tools if something here matters to what {cfg.names.subject} wrote. Lines starting "
            f"\"[you]\" are your own earlier words._\n\n{body}")


def save(cfg: Config, pid: str, text: str) -> Path:
    d = cfg.state_dir / "packs"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{pid}.md"
    p.write_text(text, encoding="utf-8")
    os.chmod(p, 0o600)
    return p
