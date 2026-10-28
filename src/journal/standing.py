"""Where things stand: the gap since he was last heard from, counted by code with no model.

The context pack (``context.build``) and the today block (``today``) both open with it, so the companion knows
whether he is writing every day or coming back after a fortnight, and says so warmly herself. Everything here is a
fact the journal holds:

* **Last heard from on the record**: the newest of his captured replies and notes (the ledger's ``captures``, which
  never hold an off-the-record message: capture skips them), a block he wrote in the web app (``web_blocks``), the
  date of his newest entry, and his newest chat message that the privacy pass says is public (``heard_in_chat``).
  Off the record leaves no trace: a ``/x`` message, a bare-``/x`` window and everything in it are not counted, and
  the hook's own ``last_his`` stamp, which every message moves, is never read. When the pass cannot run, nothing
  from the chat is counted (fail closed) and the block says so.
* **The last entry**: his newest entry (the pack's entries section starts with it).
* **Unanswered**: the run of practice prompts sent after the newest-sent one he answered (by send time),
  counted per practice, at most ``pack.lookback_days`` back. Program phases and notes are not practice prompts.
* **Programs in progress**: each active or paused run: which program, session k of n, the phase, the movement
  (``phases_of``), when he last answered in it (his captured answers only: a ``/start``, ``/next`` or ``/break``
  inside an off-the-record window would be timing, and a phase's send time moves with them) and its ``pace``.
* **The gap** in calendar days, and whether it reaches ``pack.returning_days`` (the opener's returning stance).

``collect`` returns a plain dict; ``pack_lines`` and ``today_lines`` render it. The today block runs in the gateway
hook, which never reads the transcript: it passes the chat part the tick cached (``today.refresh``) instead of
reading it.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from . import entries, ledger
from .config import Config, pack_lookback_days, pack_returning_days

MISSED_OPENERS = 2  # her last unanswered openers carried word for word (so she doesn't repeat them)
_FUTURE = timedelta(minutes=5)  # a record stamped later than this after now is a clock error, not news


# ── dates, said the same way everywhere ──────────────────────────────────────

def day_label(d: date, today: date) -> str:
    """``Thu 9 Oct``, with the year when it is not this year's."""
    return d.strftime("%a %-d %b" if d.year == today.year else "%a %-d %b %Y")


def age(d: date, today: date) -> str:
    days = (today - d).days
    if days <= 0:
        return "today"
    return "yesterday" if days == 1 else f"{days} days ago"


def dated(d: date, today: date) -> str:
    """``Thu 9 Oct, 12 days ago``."""
    return f"{day_label(d, today)}, {age(d, today)}"


def _parse(iso) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(iso))
    except (TypeError, ValueError):
        return None
    return at if at.tzinfo is not None else at.replace(tzinfo=timezone.utc)


# ── his chat, through the privacy pass ───────────────────────────────────────

def _his_message(row) -> bool:
    """A row he sent: his words (a reply the hook already saved included), or a picture with a caption. Not a
    platform notice, a delivery into the chat, the journal's mirror, or a compaction copy (read once)."""
    if row.role != "user" or row.copy or row.meta.get("notice") or row.meta.get("delivery"):
        return False
    return row.kind in ("his", "handled") or (row.kind == "system" and bool(row.meta.get("caption")))


def heard_in_chat(cfg: Config, now: datetime, days: int | None = None) -> tuple[datetime | None, str]:
    """(the time of his newest public chat message in the last ``days``, how the chat was read). The state is
    ``read``, ``none`` (no chat transcript: standalone, or no sessions database), ``unreadable`` or ``closed`` (the
    privacy pass could not run: nothing from the chat counts). Every row is asked ``conversations.row_is_off``
    against a pass that covers it (``privacy.covering``), so a ``/x`` message, a bare-``/x`` window and a captionless
    photo inside one never move the answer."""
    chat = cfg.transcript()
    if chat.source is None or not chat.available():
        return None, "none"
    since = now - timedelta(days=days if days is not None else pack_lookback_days(cfg))
    try:
        found = [r for r in chat.rows(since=since) if r.at > since and r.at <= now + _FUTURE and _his_message(r)]
    except chat.errors:
        return None, "unreadable"
    if not found:
        return None, "read"
    from . import conversations, privacy
    pv = privacy.covering(cfg, min(r.at for r in found), now=now)
    if pv.closed:
        return None, "closed"
    off = conversations.snapshot(cfg)
    for r in sorted(found, key=lambda r: (r.at, r.id), reverse=True):
        if not conversations.row_is_off(cfg, pv, r, off):
            return r.at, "read"
    return None, "read"


# ── the ledger and the entries ───────────────────────────────────────────────

def _prompts(data) -> list[dict]:
    """The ledger's prompt records that are records (a corrupt one is skipped, never a reason not to send)."""
    found = data.get("prompts") if isinstance(data, dict) else None
    return [p for p in found if isinstance(p, dict)] if isinstance(found, list) else []


def _captures(p: dict) -> list[dict]:
    """A prompt's capture records that are records."""
    found = p.get("captures")
    return [c for c in found if isinstance(c, dict)] if isinstance(found, list) else []


def _title(cfg: Config, p: dict) -> str:
    from . import library
    try:
        return library.for_prompt(cfg, p).title
    except Exception:  # noqa: BLE001
        return str(p.get("kind") or "practice")


def _via(cfg: Config, p: dict, c: dict) -> str:
    if c.get("rule") == "conversation":
        return "a chat message"
    if p.get("program") or p.get("run"):
        return f"an answer in {p.get('title') or p.get('kind') or 'a program'}"
    if p.get("kind") in entries.FREEFORM_IDS:
        return "a note"
    return f"a reply to the {_title(cfg, p)} prompt"


def _his_entries(cfg: Config) -> list[str]:
    """Stems of his entries with words in them, newest first: the set the pack's entries section reads
    (``context.journal_entries``: not her notes, not the analyses)."""
    found = []
    for p in cfg.root.glob("*.md"):
        kind = entries.kind_of(p)
        if kind and kind not in ("note", *entries.ANALYSIS_KINDS):
            found.append(p.stem)
    return sorted(found, reverse=True)


def last_entry(cfg: Config) -> str | None:
    """His newest entry that holds words of his (the stem), or None."""
    from .context import entry_words
    for stem in _his_entries(cfg):
        try:
            if entry_words((cfg.root / f"{stem}.md").read_text(encoding="utf-8", errors="replace")):
                return stem
        except OSError:
            continue
    return None


def _stem_date(stem: str) -> date | None:
    try:
        return date.fromisoformat(stem[:10])
    except ValueError:
        return None


def unanswered(cfg: Config, now_local: datetime, data: dict | None = None) -> tuple[list[dict], dict | None, bool]:
    """(the practice prompts sent after the newest-sent practice prompt he answered, newest first; that answered
    prompt, or None; whether the count stopped at ``pack.lookback_days`` before reaching one). Ordered by send
    time: a late reply to an older prompt does not reach past newer ones. A program phase and a note are not
    practice prompts. A record that can't be read is skipped (a corrupt one must never stop a prompt going out).
    The ``context.unanswered`` run."""
    data = data if data is not None else ledger.snapshot(cfg.ledger_path)
    cutoff = now_local - timedelta(days=pack_lookback_days(cfg))
    sent = []
    for p in _prompts(data):
        try:
            if (p.get("kind") not in entries.FREEFORM_IDS and p.get("message_id") and not p.get("program")
                    and _parse(p.get("sent_at")) is not None):
                sent.append((_parse(p["sent_at"]), p))
        except Exception:  # noqa: BLE001 - one odd record costs itself
            continue
    run: list[dict] = []
    for at, p in sorted(sent, key=lambda x: x[0], reverse=True):
        if _captures(p):
            return run, p, False
        if at < cutoff:
            return run, None, True
        run.append(p)
    return run, None, False


def _heard(cfg: Config, data: dict, now: datetime, chat_at: datetime | None, entry: str | None) -> dict | None:
    """The newest on-record sign of him: ``{"date", "at" (None for an entry: its day only), "via"}``."""
    best: tuple[datetime, str] | None = None

    def offer(at: datetime | None, via: str) -> None:
        nonlocal best
        if at is not None and at <= now + _FUTURE and (best is None or at > best[0]):
            best = (at, via)

    for p in _prompts(data):
        for c in _captures(p):
            try:
                offer(_parse(c.get("at")), _via(cfg, p, c))
            except Exception:  # noqa: BLE001 - one odd record costs itself
                continue
    blocks = data.get("web_blocks")
    for rec in (blocks.values() if isinstance(blocks, dict) else []):
        if isinstance(rec, dict):
            offer(_parse(rec.get("at")), "a block written in the web app")
    offer(chat_at, "a chat message")
    out = None
    if best is not None:
        local = best[0].astimezone(cfg.tz)
        out = {"date": local.date().isoformat(), "at": local.isoformat(timespec="minutes"), "via": best[1]}
    d = _stem_date(entry) if entry else None
    today = now.astimezone(cfg.tz).date()
    if d is not None and d <= today and (out is None or d > date.fromisoformat(out["date"])):
        out = {"date": d.isoformat(), "at": None, "via": f"an entry (journal:{entry})"}
    return out


# ── programs ─────────────────────────────────────────────────────────────────

def movement(phases_of: str, session: int) -> str:
    """The movement a session belongs to, from a program's ``phases_of: diagnosis=1-4, understanding=5-7``:
    ``diagnosis (sessions 1–4)``; "" when the line doesn't name one."""
    for part in (phases_of or "").split(","):
        name, sep, span = part.partition("=")
        m = re.fullmatch(r"\s*(\d+)\s*(?:[-–]\s*(\d+))?\s*", span) if sep else None
        if not m:
            continue
        lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        if lo <= session <= hi and name.strip():
            what = f"session {lo}" if lo == hi else f"sessions {lo}–{hi}"
            return f"{name.strip()} ({what})"
    return ""


def _programs(cfg: Config, data: dict) -> list[dict]:
    from . import programs
    out = []
    for run in programs.active_runs(cfg):
        try:
            prog = programs._program(cfg, run["program"])
        except programs.ProgramError:
            continue
        cur = run.get("current") or {}
        s, p = int(cur.get("session") or 1), int(cur.get("phase") or 1)
        if not 1 <= s <= len(prog.sessions):
            continue
        session = prog.session(s)
        answers = [t for q in _prompts(data) if q.get("run") == run["id"]
                   for t in (_parse(c.get("at")) for c in _captures(q) if c.get("rule") != "conversation")
                   if t is not None]
        sess = (run.get("sessions") or {}).get(str(s)) or {}
        out.append({
            "program": run["program"], "title": prog.title, "situational": prog.situational,
            "session": s, "sessions": len(prog.sessions), "session_title": session.title,
            "phase": p, "phases": len(session.phases), "started": sess.get("status") == "in_progress",
            "paused": run.get("status") == "paused", "movement": movement(prog.phases_of, s),
            "last_answer": max(answers).astimezone(cfg.tz).isoformat(timespec="minutes") if answers else None,
            "pace": " ".join((prog.pace or "").split()),
        })
    return out


# ── the whole picture ────────────────────────────────────────────────────────

def collect(cfg: Config, now: datetime, chat: tuple[datetime | None, str] | None = None) -> dict:
    """Where things stand at ``now``, as a plain dict. ``chat`` is ``heard_in_chat``'s answer when the caller
    already has it (the today block passes the tick's cached one; the hook never reads the transcript); None
    reads the chat here (the tick, building a pack). Each part fails alone: a part that cannot be read is left out
    and named in ``unavailable``."""
    now_local = now.astimezone(cfg.tz)
    today = now_local.date()
    out: dict = {"date": today.isoformat(), "heard": None, "chat": "none", "entry": None,
                 "missed": {"total": 0, "counts": {}, "since": None, "since_title": None, "capped": False,
                            "days": pack_lookback_days(cfg), "run": []},
                 "programs": [], "gap_days": None, "returning": False,
                 "returning_days": pack_returning_days(cfg), "unavailable": []}
    try:
        data = ledger.snapshot(cfg.ledger_path)
        if not isinstance(data, dict):
            raise TypeError("the ledger is not an object")
    except Exception:  # noqa: BLE001 - an unreadable ledger costs its parts, not the block
        data, out["unavailable"] = {"prompts": []}, ["the ledger"]
    if chat is None:
        try:
            chat = heard_in_chat(cfg, now)
        except Exception:  # noqa: BLE001 - fail closed: nothing from the chat
            chat = (None, "unreadable")
    out["chat"] = chat[1]
    try:
        out["entry"] = last_entry(cfg)
    except Exception:  # noqa: BLE001
        out["unavailable"].append("the entries")
    out["heard"] = _heard(cfg, data, now, chat[0], out["entry"])
    if out["heard"]:
        out["gap_days"] = max(0, (today - date.fromisoformat(out["heard"]["date"])).days)
        out["returning"] = out["gap_days"] >= out["returning_days"]
    try:
        run, answered, capped = unanswered(cfg, now_local, data)
    except Exception:  # noqa: BLE001 - the count is lost, never the prompt
        run, answered, capped = [], None, False
        out["unavailable"].append("the unanswered prompts")
    counts: dict[str, int] = {}
    for p in run:
        counts[str(p.get("kind") or "?")] = counts.get(str(p.get("kind") or "?"), 0) + 1
    since = _parse(answered.get("sent_at")) if answered else None
    out["missed"].update(total=len(run), counts=dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))),
                         since=since.astimezone(cfg.tz).isoformat(timespec="minutes") if since else None,
                         since_title=_title(cfg, answered) if answered else None, capped=capped, run=run)
    try:
        out["programs"] = _programs(cfg, data)
    except Exception:  # noqa: BLE001
        out["unavailable"].append("the program runs")
    return out


# ── rendering ────────────────────────────────────────────────────────────────

def _when(iso: str | None, day: str, today: date, clock: bool = True) -> str:
    d = date.fromisoformat(day)
    at = _parse(iso) if iso else None
    hm = f" {at.strftime('%H:%M')}" if at is not None and clock else ""
    return f"{day_label(d, today)}{hm}, {age(d, today)}"


def _missed_text(m: dict) -> str:
    per = ", ".join(f"{k} {n}" for k, n in m["counts"].items())
    return f"{m['total']} — {per}" if per else str(m["total"])


def program_text(prog: dict, today: date) -> str:
    """One run, in words: which program, session k of n, the phase, the movement, his last answer, the pace."""
    if prog["situational"]:
        where = f"{prog['title']} (a situational session), phase {prog['phase']} of {prog['phases']}"
    elif prog["started"]:
        where = (f"{prog['title']}: session {prog['session']} of {prog['sessions']} ({prog['session_title']}), "
                 f"phase {prog['phase']} of {prog['phases']}")
    else:
        done = f"session {prog['session'] - 1} done, " if prog["session"] > 1 else ""
        where = (f"{prog['title']}: {done}session {prog['session']} of {prog['sessions']} "
                 f"({prog['session_title']}) not begun yet")
    parts = [where + (", on a break" if prog["paused"] else "")]
    if prog["movement"]:
        parts.append(f"movement: {prog['movement']}")
    parts.append("last answered" + (" " if prog["last_answer"] else "") + (_when(prog["last_answer"], prog["last_answer"][:10], today, clock=False)
                                     if prog["last_answer"] else ": nothing yet"))
    if prog["pace"]:
        parts.append(f"its stated pace: {prog['pace']}")
    return "; ".join(parts)


def pack_lines(cfg: Config, data: dict) -> list[str]:
    """The pack's "Where things stand" section body."""
    n = cfg.names
    today = date.fromisoformat(data["date"])
    lines = [f"_Counted by the journal from its own records, with no model. Off-the-record messages are not "
             f"counted and leave no trace here._"]
    h = data["heard"]
    if h:
        lines.append(f"- Last heard from on the record: {_when(h['at'], h['date'], today)} ({h['via']}).")
    else:
        lines.append(f"- Last heard from on the record: nothing in the journal's records yet.")
    if data["chat"] == "closed":
        lines.append(f"- {n.possessive.capitalize()} chat messages are not counted: the privacy pass could not run, "
                     "so the journal reads none of them (fail closed).")
    elif data["chat"] == "unreadable":
        lines.append(f"- {n.possessive.capitalize()} chat could not be read just now: only the journal's own "
                     "records are counted.")
    if data["entry"]:
        d = _stem_date(data["entry"])
        lines.append(f"- Last entry: journal:{data['entry']}" + (f" ({dated(d, today)})." if d else "."))
    m = data["missed"]
    if m["total"]:
        if m["since"]:
            head = (f"since the newest practice prompt {n.subject} answered ({m['since_title']}, sent "
                    f"{_when(m['since'], m['since'][:10], today)})")
        else:
            head = f"in the last {m['days']} days, none of them answered" if m["capped"] else "so far"
        lines.append(f"- Prompts unanswered {head}: {_missed_text(m)}.")
    elif m["since"]:
        lines.append(f"- No prompt is waiting: the newest one was answered ({m['since_title']}, sent "
                     f"{_when(m['since'], m['since'][:10], today)}).")
    for prog in data["programs"]:
        lines.append(f"- Program in progress: {program_text(prog, today)}.")
    if data["gap_days"] is not None:
        days = data["gap_days"]
        span = "less than a day" if days == 0 else ("1 day" if days == 1 else f"{days} days")
        tail = (f"past the {data['returning_days']}-day mark: a return after time away"
                if data["returning"] else f"under the {data['returning_days']}-day mark")
        lines.append(f"- Gap: {span} since the last word from {n.object} on the record; {tail}.")
    for what in data["unavailable"]:
        lines.append(f"- _{what} could not be read just now_")
    return lines


def today_lines(cfg: Config, data: dict) -> list[str]:
    """The same facts, short, for the today block (its lines read ``- name: …``). The gap line only when he was
    last heard from before today; the unanswered line only when some are."""
    today = date.fromisoformat(data["date"])
    out = []
    h = data["heard"]
    if h and data["gap_days"]:
        entry = f"; last entry {data['entry']}" if data["entry"] else ""
        back = " (coming back after time away)" if data["returning"] else ""
        out.append(f"- last heard on the record: {_when(h['at'], h['date'], today, clock=False)}, "
                   f"{h['via']}{entry}{back}")
    m = data["missed"]
    if m["total"]:
        since = (f" since the newest one answered ({m['since_title']}, "
                 f"{day_label(date.fromisoformat(m['since'][:10]), today)})" if m["since"] else "")
        out.append(f"- prompts unanswered{since}: {_missed_text(m)}")
    for prog in data["programs"]:
        out.append(f"- program: {program_text(prog, today)}")
    return out
