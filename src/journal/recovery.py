"""Talk back after an outage: one catch-up reply for the messages a model outage left unanswered.

When no model is reachable, Hermes still answers each chat message, with its failure line (``⚠️ Something
went wrong and I couldn't finish this reply…``, ``agent.is_failure_text``). Nothing answers those
messages once the model is back: the gateway does not retry, and the companion's next turn starts from
whatever he writes next. So every tick reads the chat transcript (read-only, cheap) for the run of his
messages since the companion's last real reply whose only answers were failure lines, and, once a model
answers again, sends **one** catch-up message:

* Detection (``scan``): Hermes does not store the failure copy it sends; it closes a failed turn with an
  assistant row ``display_kind = 'failed_turn'`` ("Your request was not processed…", ``hermes.transcript.is_failed_turn``).
  Candidates are his chat messages after the companion's last real reply, where failed turns came
  after them and the newest of them was itself closed (a newest message with no answer yet may be a live
  turn in progress: wait). The journal's own mirrored rows, cron mirrors and gateway notes are never his
  messages. A failed turn is also closed this way for an interrupt or a refusal, so a catch-up needs a
  sign of an outage: a failed-turn row carrying the provider error (``display_metadata``), or the
  catch-up's own model run finding the model unreachable. Without one, nothing is sent.
* When: right away if a model step already answered in this tick (``agent.answered_this_run``), else at
  most every ``outage.RETRY_EVERY`` (30 min): the catch-up's own model run is the probe. An unreachable
  model defers it (``outage``) without using an attempt.
* What: a one-shot without tools, given his unanswered messages verbatim with their times, the outage
  window (first failure → recovery) and the conversation just before. It must be short, plain prose, not
  failure text, and quote only what is in those inputs (``validate.check_catchup``); three bad answers,
  and a note is mirrored instead.
* Sent as a reply to his last unanswered message (a message of its own where the platform has no replies; never
  while the companion is mid-turn in the chat), then mirrored into the chat session (``mirror``), so the live companion knows what she said.
* Never more than one per outage (records in the ledger under ``recovery``; each covers the rows it
  answered, and later scans start after them). Messages older than ``recovery.max_age_hours`` (48) get no
  reply, only a mirrored note so she knows he wrote while she was away. If he has had a real answer since,
  nothing is sent.

While the outage lasts nothing is sent: Hermes already answers each message with its failure line.

**The letter** (CATCH-UP-LETTER, the owner's decision 1 on #42): the same one message also covers the journal entries
he wrote while she was away, in place of their annotation replies, which would otherwise come late one by one. Her
notes are still written into the entries (the paced annotation step); only the chat side changes.

* Which outage: the breaker's (``outage.span``: from ``down_since`` to the ``answered_at`` of the model that came
  back), when it lasted ``recovery.letter_min_minutes`` (60) or more; a shorter one keeps the replies, barely late.
  Which entries: prompts (not program phases) with his words from ``annotate_after_minutes`` before it began (their
  notes were due in it) to its end, whose reply is still owed and would be sent (``_covered``). With the breaker off,
  no outage is known across ticks and nothing changes.
* When: at once, in the first tick after a model answered (``held`` keeps their replies back from that moment, before
  the record exists, so the tick's reply step sends none of them first). The letter reads his words in the entries,
  not her notes, so it never waits for the paced annotations.
* One record per outage (``outage_down_since``): a waiting chat catch-up takes the entries in, and the two go as one
  message; with no unanswered chat message the letter is a record of its own (``outage-<UTC minute>``), sent as a
  message of its own. Once sent, each covered prompt's reply is marked skipped, ``letter``, and never sent; its notes
  are still written. A later scan of the chat starts after the rows it covered.
* Floor: no model, no letter (it waits, deferred, and the replies stay held: nothing late goes); three bad answers,
  a send refused six times, or no letter sent ``recovery.letter_deadline_minutes`` (120) after a model answered (the
  chat unreadable, the request failing while other steps' models answer), no letter either, never a fixed line in
  her voice: the note in her chat session, and the replies are released to the old path (paced, and dropped once
  stale). Two outages close together, the first's letter not yet written, make one letter. The deadline's clock
  stops while a bare ``/x`` window holds the letter (``off_record``), and a later outage of ``letter_min`` or more
  (one taken in) starts it again; a shorter one moves nothing. In all, a letter holds the replies at most
  ``hold_cap`` (three deadlines, under ``stale_hours``) from her first return (``first_back_at``). A catch-up of his
  chat messages alone holds no replies and has no deadline (up to ``max_age_hours``, as before).
* Never while a bare ``/x`` window is open, or while the privacy pass cannot say (fail closed): the whole message
  waits, checked again just before it is sent (a window opened while it was being written). His words come from the entries (``entries.his_text``: his blocks only, which hold nothing off the record)
  and the chat rows ``scan`` keeps (never a private one). ``channel.platform: none``: nothing at all.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import agent, channel, compose, context, entries, ledger, mirror, outage, prompts, validate
from .ports import ChannelError
from .config import Config
from .ports import ChatUnreadable, Row

ATTEMPTS = 3          # bad answers from a reachable model before falling back to a note
SEND_ATTEMPTS = 6     # failed sends before falling back to a note
BUDGET = 150
CONTEXT_ROWS = 8      # conversation rows shown from before the outage
LOOKBACK_EXTRA = timedelta(hours=24)  # read this much further back than max_age to find her last real reply
ENTRY_CHARS = 6000    # his words from the entries shown to the letter, in all (each entry gets its share)


def enabled(cfg: Config) -> bool:
    return cfg.recovery.get("enabled", True) is not False and bool(cfg.chat_id) and cfg.has_channel


def letter_on(cfg: Config) -> bool:
    """Whether a catch-up after an outage also covers his entries, in place of their annotation replies: the catch-up
    can run (a chat, a transcript), ``recovery.letter`` is on, the breaker knows outages, and replies are sent at all."""
    return (enabled(cfg) and cfg.recovery.get("letter", True) is not False and outage.breaker_on(cfg)
            and cfg.telegram.get("annotation_replies", True) is not False and cfg.transcript().source is not None)


def letter_min(cfg: Config) -> timedelta:
    try:
        minutes = float(cfg.recovery.get("letter_min_minutes", 60))
    except (TypeError, ValueError):
        minutes = 60.0
    return timedelta(minutes=max(0.0, minutes))


def letter_deadline(cfg: Config) -> timedelta:
    """How long after a model answered the letter may still go (``recovery.letter_deadline_minutes``, 120): past it
    there is none, and the replies it held go the old way while they are not stale (``stale_hours``)."""
    try:
        minutes = float(cfg.recovery.get("letter_deadline_minutes", 120))
    except (TypeError, ValueError):
        minutes = 120.0
    return timedelta(minutes=minutes if minutes > 0 else 120.0)


def max_age(cfg: Config) -> timedelta:
    return timedelta(hours=float(cfg.recovery.get("max_age_hours", 48)))


def find(data: dict, rid: str) -> dict | None:
    return next((r for r in data.get("recovery") or [] if r.get("id") == rid), None)


# ── reading the chat (read-only) ─────────────────────────────────────────────

def his_words(row: Row) -> str:
    """His words in a user row, or '' when the row is not his (mirrors, platform frames, internal notices) or is
    a slash command (not a message to answer). His message as the pack shows it (``Row.meta["loose"]``)."""
    if row.meta.get("internal") or row.kind == "mirror" or row.meta.get("frame"):
        return ""
    text = row.meta.get("loose") or ""
    return "" if text.startswith("/") else text  # a slash command is not a message to answer


def scan(cfg: Config, db: Path | None, now: datetime, after_row: int = 0) -> dict:
    """What the chat says about the last outage, after row ``after_row`` (rows already answered).

    Returns {"his": [...], "failures": [...], "before": [...], "answered_after": row id of her last real reply,
    "pending": his newest message has no answer yet}. ``his`` are his unanswered messages
    (row, at, message id, text); ``before`` the conversation leading up to them (who, at, text). Retired rows
    (a rewind) are not history and are left out."""
    since = (now - max_age(cfg) - LOOKBACK_EXTRA).timestamp()
    after = datetime.fromtimestamp(since, timezone.utc)
    his: list[dict] = []
    failures: list[dict] = []
    said: list[tuple[str, datetime, str]] = []  # the real conversation, both sides, in order
    last_real = 0
    answered = True  # whether his newest message (if any) has been answered, by anything
    from . import conversations, privacy
    state = conversations.snapshot(cfg)
    pv = privacy.covering(cfg, after, now, db=db)  # 72 h: past the default 48
    if pv.closed:
        raise ChatUnreadable(f"the privacy pass could not run ({getattr(pv, 'why', '')})")
    newest = 0  # the newest row read (a letter of entries alone covers the chat up to here)
    for row in cfg.transcript(db).rows(since=after):
        if not row.meta.get("active", True) or not row.at > after:
            continue
        row_id, at = row.id, row.at
        newest = max(newest, row_id)
        if row.role == "assistant":
            text = row.text
            if not text or row.kind == "tool_step":
                continue  # a tool-call step, not a reply
            answered = True
            if row.kind == "failed_turn":
                if row_id > after_row and his:
                    failures.append({"row": row_id, "at": at, "outage": bool(row.meta.get("outage"))})
                continue
            last_real = row_id
            if not conversations.row_is_off(cfg, pv, row):  # her answer in a private turn is private too
                said.append((cfg.names.companion, at, text))
            his, failures = [], []  # a real reply: everything before it was answered
            continue
        if conversations.row_is_off(cfg, pv, row, state):
            continue  # never a catch-up candidate, never context for one
        words = his_words(row)
        if not words:
            continue
        answered = False
        said.append((cfg.names.user or "them", at, words))
        if row_id > after_row:
            mid = row.meta.get("stored_id")
            his.append({"row": row_id, "at": at, "message_id": mid, "text": words})
    first = his[0]["at"] if his else None
    before = [b for b in said if first is None or b[1] < first][-CONTEXT_ROWS:]
    return {"his": his, "failures": failures, "before": before, "answered_after": last_real,
            "pending": not answered, "newest": newest}


def unanswered(found: dict) -> bool:
    """An outage left his messages answered only by failure lines, and the newest was answered too."""
    return bool(found["his"] and found["failures"] and not found["pending"]
                and found["failures"][-1]["row"] > found["his"][-1]["row"])


# ── one catch-up per outage ──────────────────────────────────────────────────

def _fmt(cfg: Config, at: datetime) -> str:
    return at.astimezone(cfg.tz).strftime("%-d %b %H:%M")


def window(cfg: Config, rec: dict, end: datetime) -> str:
    """From the outage's start to its end: when a model answered again (``back_at``, once the breaker knows), else
    ``end``."""
    start = datetime.fromisoformat(rec["offline_since"])
    if rec.get("back_at"):
        end = datetime.fromisoformat(rec["back_at"])
    return f"{_fmt(cfg, start)} to {_fmt(cfg, end)} {end.astimezone(cfg.tz).strftime('%Z')}"


def _covered_rows(data: dict) -> int:
    return max([int(r.get("last_row") or 0) for r in data.get("recovery") or []] or [0])


def _note(cfg: Config, rec: dict, now: datetime, why: str, log: list[str]) -> None:
    """No reply (too old, or no good one): tell the live companion he wrote while she was away."""
    n = cfg.names
    lines = [f"- {m['at_local']}: {context.clean_user_text(m['text'])[:300]}" for m in rec.get("messages") or []]
    ents = rec.get("entries") or []
    lines += [f"- {e['at_local']}: journal entry {e['stem']}" for e in ents]
    if rec.get("messages"):
        what = (f"{n.companion} was offline from {window(cfg, rec, now)}; {n.user_or_them} wrote meanwhile and got "
                f"only an error line back. No catch-up reply was sent ({why})")
    else:
        what = (f"{n.companion} was offline from {window(cfg, rec, now)}; {n.user_or_them} wrote {_entries(len(ents))} "
                f"meanwhile. No catch-up message was sent ({why})")
    if ents:
        what += _released(cfg, rec, now)
    with ledger.locked(cfg.ledger_path) as data:
        r = find(data, rec["id"])
        if r is not None:
            r.update(status="noted", noted_at=now.isoformat(), note_reason=why)
            outage.clear(r)
    mirrored = mirror.to_chat(cfg, what, "\n".join(lines), log, ref=("recovery", rec["id"], None))
    log.append(f"{rec['id']}: no catch-up sent ({why}); noted in the chat session" if mirrored
               else f"{rec['id']}: no catch-up sent ({why})")


def _released(cfg: Config, rec: dict, now: datetime) -> str:
    """What becomes of the replies a letter that is not sent was to be in place of: released, they go the old way, but
    only while they are not stale (``tick._skip_stale_reply``); said only as far as it is true."""
    data = ledger.snapshot(cfg.ledger_path)
    owed = [p for p in (ledger.find(data, pid) for pid in rec.get("prompts") or []) if p is not None
            and not (p.get("annotation_reply") or {}).get("message_id")
            and not (p.get("annotation_reply") or {}).get("skipped")]
    if not owed:
        return ""
    late = outage.stale_after(cfg)
    live = [p for p in owed if (w := _newest_words(p)) is not None and now - w <= late]
    if len(live) == len(owed):
        return "; the replies to the entries go to the chat as usual"
    if live:
        return (f"; {len(live)} of the {len(owed)} replies to the entries go to the chat as usual, the others are too "
                "late and stay in the entries")
    return "; the replies to the entries are too late to send and stay in the entries"


def _skip(cfg: Config, rec: dict, now: datetime, why: str, log: list[str]) -> None:
    with ledger.locked(cfg.ledger_path) as d:
        r = find(d, rec["id"])
        if r is not None:
            r.update(status="skipped", skipped_at=now.isoformat(), note_reason=why)
            outage.clear(r)
    log.append(f"{rec['id']}: no catch-up ({why})")


def _open(cfg: Config, found: dict, now: datetime) -> dict:
    """The waiting record for this outage, created or extended with messages he sent since."""
    msgs = [{"row": m["row"], "at": m["at"].isoformat(), "at_local": _fmt(cfg, m["at"]) + " "
             + m["at"].astimezone(cfg.tz).strftime("%Z"), "message_id": m["message_id"], "text": m["text"][:2000]}
            for m in found["his"]]
    since = min(found["his"][0]["at"], found["failures"][0]["at"])
    with ledger.locked(cfg.ledger_path) as data:
        recs = data.setdefault("recovery", [])
        rec = next((r for r in recs if r.get("status") == "waiting"), None)
        if rec is None:
            rec = {"id": f"outage-{found['his'][0]['row']}", "status": "waiting", "detected_at": now.isoformat(),
                   "offline_since": since.isoformat()}
            recs.append(rec)
        if any(f["outage"] for f in found["failures"]):
            rec["outage_seen"] = True  # a failed turn names the provider error
        rec.update(messages=msgs, last_row=found["failures"][-1]["row"],
                   reply_to=next((m["message_id"] for m in reversed(msgs) if m["message_id"]), None),
                   context=[[who, _fmt(cfg, at), text[:600]] for who, at, text in found["before"]])
        return dict(rec)


# ── the letter: his entries from the outage, in the same one message ─────────

def _entries(n: int) -> str:
    return f"{n} journal entr{'y' if n == 1 else 'ies'}"


def _newest_words(p: dict) -> datetime | None:
    """When his newest words in a prompt's captures were written (what its annotation reply would answer)."""
    times = []
    for c in p.get("captures") or []:
        try:
            at = datetime.fromisoformat(c["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if at.tzinfo is not None:
            times.append(at)
    return max(times, default=None)


def _letter_outage(cfg: Config, now: datetime) -> tuple[datetime, datetime | None] | None:
    """The breaker's outage a letter is for: open (no end yet) or ended within ``max_age``, and ``letter_min`` long
    (so far, while open)."""
    found = outage.span(cfg)
    if found is None:
        return None
    down, back = found
    try:
        if (back or now) - down < letter_min(cfg) or (back is not None and now - back > max_age(cfg)):
            return None
    except TypeError:
        return None
    return down, back


def _iso(value) -> datetime | None:
    try:
        when = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else None


def _back(cfg: Config, rec: dict, now: datetime) -> datetime | None:
    """When a model last came back for a letter record: the breaker's, when its outage is the record's, or a later one
    of ``letter_min`` or more (so far, while it lasts: ``_attach`` takes that one in); None while that outage lasts.
    A shorter later outage (the letter's own failed request can trip the breaker) is never taken in, so it moves
    nothing. Else (the breaker off, its last outage released, or a short one) the record's ``back_at``, or when it was
    made."""
    key = _iso(rec.get("outage_down_since"))
    span = outage.span(cfg)
    try:
        if span is not None and key is not None and span[0] >= key \
                and (span[0] == key or (span[1] or now) - span[0] >= letter_min(cfg)):
            return span[1]
    except TypeError:
        pass
    return _iso(rec.get("back_at")) or _iso(rec.get("detected_at"))


def hold_cap(cfg: Config) -> timedelta:
    """The longest a letter may hold the replies in all, from the first time a model came back for it, whatever
    moved or paused the deadline since (later outages, ``/x`` windows): three deadlines, and an hour under
    ``stale_hours``, so the replies it lets go are still sent."""
    return max(timedelta(0), min(3 * letter_deadline(cfg), outage.stale_after(cfg) - timedelta(hours=1)))


def _spans(rec: dict, now: datetime) -> list[tuple[datetime, datetime]]:
    """The record's off-the-record spans (``off_record``: ``[since, until]``, ``until`` None while open), merged."""
    out = []
    for item in rec.get("off_record") or []:
        try:
            since, until = _iso(item[0]), (_iso(item[1]) if item[1] else now)
        except (IndexError, TypeError):
            continue
        if since is not None and until is not None and until > since:
            out.append((since, until))
    merged: list[tuple[datetime, datetime]] = []
    for since, until in sorted(out):
        if merged and since <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], until))
        else:
            merged.append((since, until))
    return merged


def _paused(rec: dict, frm: datetime, now: datetime) -> timedelta:
    """How much of ``frm`` to ``now`` a bare ``/x`` window held the letter (the deadline clock stops in it)."""
    total = timedelta(0)
    for since, until in _spans(rec, now):
        lo, hi = max(since, frm), min(until, now)
        if hi > lo:
            total += hi - lo
    return total


def _overdue(cfg: Config, rec: dict, now: datetime) -> bool:
    """A waiting letter (a record tied to an outage, holding replies) not sent ``letter_deadline`` after a model
    answered, not counting time a bare ``/x`` window held it; or held ``hold_cap`` in all since a model first came
    back for it: there is to be none (``run`` notes it before anything else, so an unreadable chat or a request that
    keeps failing cannot hold the replies for longer, and ``held`` lets them go from that moment). A record that holds
    no replies (his chat messages alone) has no deadline: the deadline is there to let replies go, and a chat
    catch-up keeps trying as before, up to ``max_age_hours``."""
    if not rec.get("outage_down_since") or rec.get("status") != "waiting" or not rec.get("prompts"):
        return False
    try:
        first = _iso(rec.get("first_back_at"))
        if first is not None and now - first >= hold_cap(cfg):
            return True
        back = _back(cfg, rec, now)
        return back is not None and now - back - _paused(rec, back, now) >= letter_deadline(cfg)
    except TypeError:
        return False


def _covered(cfg: Config, data: dict, down: datetime, end: datetime) -> list[dict]:
    """The prompts whose annotation reply the letter is in place of: his words from ``annotate_after_minutes`` before
    the outage began (their notes fell due in it) to its end; a reply still owed (not sent, not skipped); one that is
    annotated at all (not a conversation note, unless those are); not a program's phase (a run has its own flow)."""
    from . import conversations
    lo = down - timedelta(minutes=cfg.annotate_after_minutes)
    out = []
    for p in data.get("prompts") or []:
        if not isinstance(p, dict) or not p.get("captures") or p.get("program"):
            continue
        reply = p.get("annotation_reply") or {}
        if reply.get("message_id") or reply.get("skipped"):
            continue
        if p.get("annotate") is False or (conversations.is_auto(p) and not conversations.annotate_auto(cfg)):
            continue
        newest = _newest_words(p)
        if newest is not None and lo <= newest <= end:
            out.append(p)
    return out


def _material(cfg: Config, covered: list[dict]) -> list[dict]:
    """His words in the covered entries, one item per entry file, oldest first: his blocks only (``his_text``: never
    her notes or folded replies), each cut to its share of ``ENTRY_CHARS``."""
    items: dict[str, dict] = {}
    for p in sorted(covered, key=lambda q: _newest_words(q)):
        path = Path(p.get("entry_path") or "")
        if not path.is_file():
            continue
        item = items.setdefault(str(path), {"stem": path.stem, "path": path})
        item["at"] = _newest_words(p)
    share = max(400, ENTRY_CHARS // max(1, len(items)))
    out = []
    for item in items.values():
        text = entries.his_text(item["path"]).strip()
        if text:
            out.append({"stem": item["stem"], "at": item["at"].isoformat(),
                        "at_local": _fmt(cfg, item["at"]) + " " + item["at"].astimezone(cfg.tz).strftime("%Z"),
                        "text": text[:share]})
    return out


def held(cfg: Config, now: datetime) -> set[str]:
    """The prompts whose annotation reply waits for the letter (``tick.reply_annotations`` sends none of them): those a
    waiting record covers, and, from the moment a model answered and before any record exists, those the breaker's
    outage covers. A record that ended without a letter (noted, skipped) holds nothing: they go as before; nor does one
    past ``letter_deadline`` (``_overdue``), nor the outage itself past it with no record made (the chat unreadable)."""
    if not letter_on(cfg):
        return set()
    data = ledger.snapshot(cfg.ledger_path)
    recs = [r for r in data.get("recovery") or [] if isinstance(r, dict)]
    out = {pid for r in recs if r.get("status") == "waiting" and not _overdue(cfg, r, now)
           for pid in r.get("prompts") or []}
    found = _letter_outage(cfg, now)
    if found is not None and not any(r.get("outage_down_since") == found[0].isoformat() for r in recs) \
            and (found[1] is None or now - found[1] < letter_deadline(cfg)):
        out |= {p["id"] for p in _covered(cfg, data, found[0], found[1] or now)}
    return out


def _attach(cfg: Config, now: datetime, rec: dict | None, found: dict) -> dict | None:
    """Take the entries he wrote in the breaker's outage into this outage's record (``rec``, the waiting chat
    catch-up), or open a record for them alone. Returns the record to act on (``rec`` itself when there is nothing to
    add), or None when there is none. Until its text is written the list is taken again on each pass, so an entry
    written while the outage still lasts is in it too; and a record of an earlier outage not yet written takes in this
    one (from the first's start to this one's end: one letter, not two minutes apart). Past ``letter_deadline`` no
    letter is begun. An entries-only record has no ``messages`` key at all (older code reads its first message)."""
    if not letter_on(cfg):
        return rec
    span = _letter_outage(cfg, now)
    if span is None:
        return rec
    down, back = span
    key = down.isoformat()
    lo = down - timedelta(minutes=cfg.annotate_after_minutes)
    with ledger.locked(cfg.ledger_path) as data:
        recs = data.setdefault("recovery", [])
        if any(r.get("outage_down_since") == key and r.get("status") != "waiting" for r in recs):
            return rec  # this outage's letter is done (sent, noted or skipped)
        target = find(data, rec["id"]) if rec else None
        if target is not None and target.get("text"):
            return dict(target)  # written already: what it covers is fixed
        if back is not None and now - back >= letter_deadline(cfg) and (target is None
                                                                        or target.get("outage_down_since") != key
                                                                        or not target.get("prompts")):
            return dict(target) if target is not None else None  # too late to begin one: the replies go as before
            # (a chat catch-up tied to this outage holding no replies stays one: it takes none in this late)
        first = down
        earlier = _iso(target.get("outage_down_since")) if target is not None else None
        if earlier is not None and earlier < down:
            first = earlier  # an earlier outage's letter, not yet written: one letter for both
            lo = first - timedelta(minutes=cfg.annotate_after_minutes)
        covered = _covered(cfg, data, first, back or now)
        if target is None:
            if not covered:
                return None
            target = {"id": f"outage-{down.astimezone(timezone.utc):%Y%m%d%H%M}", "status": "waiting",
                      "detected_at": now.isoformat(), "offline_since": key, "reply_to": None, "context": []}
            recs.append(target)
        elif not covered and not any(lo <= datetime.fromisoformat(m["at"]) <= (back or now)
                                     for m in target.get("messages") or []):
            return dict(target)  # the chat's failed turns were not in this outage
        target.update(outage_down_since=key, outage_seen=True, prompts=[p["id"] for p in covered],
                      entries=_material(cfg, covered))
        if back is not None:
            target["back_at"] = back.isoformat()
            target.setdefault("first_back_at", back.isoformat())  # (``hold_cap`` counts from here, never moved)
        else:
            target.pop("back_at", None)  # (taken in by an outage that still lasts)
        if down < datetime.fromisoformat(target["offline_since"]):
            target["offline_since"] = key
        if not target.get("messages"):
            upto = int(found.get("newest") or 0)
            if found.get("pending") and found.get("his"):
                upto = min(upto, int(found["his"][0]["row"]) - 1)  # his message whose live turn still runs (and any
                # unanswered before it): if that turn fails, the chat catch-up must still see them
            target["last_row"] = max(int(target.get("last_row") or 0), upto)
        return dict(target)


def _drop_messages(cfg: Config, rec: dict, log: list[str]) -> dict:
    """He has had a real answer in the chat since: his messages are answered, his entries are not."""
    with ledger.locked(cfg.ledger_path) as d:
        r = find(d, rec["id"])
        if r is None:
            return rec
        r.pop("messages", None)  # (left out, never ``[]``: older code reads the first message of a waiting record)
        r.update(reply_to=None, context=[])
        rec = dict(r)
    log.append(f"{rec['id']}: {cfg.names.possessive} messages were answered live since; the catch-up keeps "
               f"{cfg.names.possessive} journal entries")
    return rec


def _off_record(cfg: Config, now: datetime, rec: dict | None = None) -> bool:
    """A bare ``/x`` window is open, or the privacy pass cannot say (fail closed): no catch-up goes now. A window that
    began after ``now`` (while the catch-up was being written) is open too. For a letter holding replies (``rec``),
    the windows the pass sees are kept on the record (``off_record``): its deadline does not run in them, so neither
    the letter nor the replies it lets go land in his off-the-record time. (A pass that cannot run keeps nothing: the
    deadline runs, as the chat being unreadable must not hold the replies.)"""
    from . import privacy
    pv = privacy.view(cfg, now)
    if pv.closed:
        return True
    off = pv.open_at(now) or (bool(pv.window) and pv.window["since"] > now)
    if rec is not None and rec.get("prompts") and rec.get("outage_down_since") and rec.get("status") == "waiting":
        _keep_off(cfg, rec, pv, now, off)
    return off


def _keep_off(cfg: Config, rec: dict, pv, now: datetime, off: bool) -> None:
    """Keep the off-the-record windows since a model came back on the letter's record: ``[since, until]``, ``until``
    None while one is open; a window that has ended since is closed at its end (its last activity and the quiet
    gap, or a prompt send), or ``now``."""
    frm = _iso(rec.get("first_back_at")) or _iso(rec.get("back_at")) or now - hold_cap(cfg)
    seen = []
    for since, end in pv.windows:
        if end is None:
            w = pv.window or {}
            end = None if off else min([now] + ([w["last"] + pv.quiet] if w.get("last") else [])
                                       + [t for t in pv.sends if since < t <= now])
        if end is None or end > frm:
            seen.append([since.isoformat(), end.isoformat() if end is not None else None])
    with ledger.locked(cfg.ledger_path) as d:
        r = find(d, rec["id"])
        if r is None:
            return
        spans = [list(item) for item in r.get("off_record") or []]
        for since, until in seen:
            same = next((item for item in spans if item and item[0] == since), None)
            if same is None:
                spans.append([since, until])
            else:
                same[1:] = [until]
        for item in spans:  # one open before, not among the windows seen now: it has ended
            if len(item) > 1 and item[1] is None and not any(item[0] == s for s, _ in seen):
                item[1] = now.isoformat()
        if spans != (r.get("off_record") or []):
            r["off_record"] = spans


def _duration(span: timedelta) -> str:
    minutes = round(span.total_seconds() / 60)
    return f"{minutes // 60:g} h" if minutes % 60 == 0 else f"{minutes} min"


def _summary(rec: dict) -> str:
    parts = []
    if rec.get("messages"):
        parts.append(f"{len(rec['messages'])} unanswered message(s)")
    if rec.get("entries"):
        parts.append(_entries(len(rec["entries"])))
    return ", ".join(parts)


def _corpus(cfg: Config, rec: dict) -> str:
    """His words the catch-up was shown, for its quote check: his messages, his lines of the conversation before,
    and his words in the entries (never her notes)."""
    his = cfg.names.user or "them"  # (the label ``scan`` gives his lines of the conversation before; hers say her name)
    return "\n".join([m["text"] for m in rec.get("messages") or []]
                     + [c[2] for c in rec.get("context") or [] if c[0] == his]
                     + [e["text"] for e in rec.get("entries") or []])


# ── one catch-up per outage ──────────────────────────────────────────────────

def run(cfg: Config, now: datetime, log: list[str], db: Path | None = None) -> None:
    """One pass: look for an outage that left him unanswered (his chat messages, or entries he wrote while she was
    away); answer it once when a model is back."""
    chat = cfg.transcript(db)
    if not enabled(cfg) or chat.source is None:
        return
    data = ledger.snapshot(cfg.ledger_path)
    waiting = next((r for r in data.get("recovery") or [] if r.get("status") == "waiting"), None)
    if waiting and _overdue(cfg, waiting, now):  # (before the scan: an unreadable chat must not hold the replies)
        _note(cfg, waiting, now, f"none sent within {_duration(letter_deadline(cfg))} of the model's return", log)
        return
    floor = _covered_rows({"recovery": [r for r in data.get("recovery") or [] if r.get("status") != "waiting"]})
    try:
        found = scan(cfg, db, now, floor)
    except chat.errors as e:
        log.append(f"outage catch-up skipped: sessions database unavailable ({type(e).__name__})")
        return
    if waiting and waiting.get("messages") \
            and found["answered_after"] > int(waiting["messages"][0].get("row") or 0):
        if not waiting.get("entries"):
            _skip(cfg, waiting, now, "answered live since", log)  # he has had a real answer: nothing to catch up on
            return
        waiting = _drop_messages(cfg, waiting, log)
    if unanswered(found):
        rec = _open(cfg, found, now)
    elif waiting and waiting.get("messages"):
        return  # his newest message is still being answered
    else:
        rec = waiting
    rec = _attach(cfg, now, rec, found)
    if rec is None:
        return
    times = [datetime.fromisoformat(m["at"]) for m in (rec.get("messages") or []) + (rec.get("entries") or [])]
    if not times:
        _skip(cfg, rec, now, "nothing left to answer", log)
        return
    if now - max(times) > max_age(cfg):
        _note(cfg, rec, now, f"older than {max_age(cfg).total_seconds() / 3600:.0f} h", log)
        return
    if chat.busy():
        return  # she is answering something right now: don't talk over it
    if _off_record(cfg, now, rec):
        return  # he is talking off the record (or the pass cannot say): the catch-up waits for the window to close
    if rec.get("text"):  # written earlier; only the send failed
        _send(cfg, rec, now, log)
        return
    if outage.waiting(rec, now, cfg) and not agent.answered_this_run():
        return  # the model was unreachable moments ago; another step would have found it back
    if not rec.get("outage_seen") and agent.answered_this_run():
        _skip(cfg, rec, now, "no sign of an outage: the turn failed for another reason", log)
        return
    query = prompts.catchup_request(cfg, rec, window(cfg, rec, now))
    try:
        raw = agent.ask(cfg, query, BUDGET, toolsets="none")
    except agent.AgentUnavailable as e:
        with ledger.locked(cfg.ledger_path) as d:
            r = find(d, rec["id"])
            if r is not None:
                outage.defer(r, now, str(e))
                if (outage.known(cfg) or not outage.breaker_on(cfg)) and not isinstance(e, outage.RequestRefused):
                    r["outage_seen"] = True  # the model really was unreachable (a probe found none either; a refused
                    # request ran no model, so it is no sign of one)
        log.append(f"{rec['id']}: catch-up deferred, model still unreachable ({e})")
        return
    if not rec.get("outage_seen"):  # the model answered at once and no row says it had failed
        _skip(cfg, rec, now, "no sign of an outage: the turn failed for another reason", log)
        return
    ok, reason = validate.check_catchup(raw, _corpus(cfg, rec))
    with ledger.locked(cfg.ledger_path) as d:
        r = find(d, rec["id"])
        if r is None:
            return
        outage.clear(r)  # the model answered, well or badly
        if ok:
            r.update(text=raw.strip(), written_at=now.isoformat(), recovered_at=now.isoformat(),
                     window=window(cfg, rec, now))
        else:
            r["attempts"] = r.get("attempts", 0) + 1
            r["error"] = reason[:300]
        rec = dict(r)
    if not ok:
        log.append(f"{rec['id']}: catch-up attempt {rec['attempts']}/{ATTEMPTS} failed ({reason})")
        if rec["attempts"] >= ATTEMPTS:
            _note(cfg, rec, now, f"no usable reply after {ATTEMPTS} attempts: {reason}", log)
        return
    if _off_record(cfg, now, rec):  # again: a window he opened while it was being written (the text waits, checked)
        log.append(f"{rec['id']}: catch-up written; it waits for the off-the-record window to close")
        return
    _send(cfg, rec, now, log)


def _send(cfg: Config, rec: dict, now: datetime, log: list[str]) -> None:
    error, mid = None, None
    try:
        mid = cfg.channel().send(cfg.chat_id, compose.md_to_html(rec["text"]), reply_to=rec.get("reply_to"))
    except ChannelError as e:
        error = str(e)
    with ledger.locked(cfg.ledger_path) as d:
        r = find(d, rec["id"])
        if r is None:
            return
        if mid:
            r.update(status="sent", message_id=mid, sent_at=now.isoformat())
            r.pop("send_error", None)
            for pid in r.get("prompts") or []:  # the letter is in place of their replies: none goes on its own
                q = ledger.find(d, pid)
                reply = (q or {}).get("annotation_reply") or {}
                if q is not None and not reply.get("message_id") and not reply.get("skipped"):
                    q.setdefault("annotation_reply", {}).update(skipped="letter", skipped_at=now.isoformat(),
                                                                letter=r["id"])
        else:
            r["send_attempts"] = r.get("send_attempts", 0) + 1
            r["send_error"] = (error or "")[:300]
        tries = r.get("send_attempts", 0)
    if not mid:
        log.append(f"{rec['id']}: catch-up send attempt {tries}/{SEND_ATTEMPTS} failed ({error})")
        if tries >= SEND_ATTEMPTS:
            _note(cfg, rec, now, f"{channel.label(cfg)} refused it {tries} times", log)
        return
    log.append(f"sent the catch-up for {rec['id']} ({_summary(rec)})")
    n, ents = cfg.names, rec.get("entries") or []
    when = rec.get("window") or window(cfg, rec, now)
    if not ents:
        what = (f"{n.companion} was offline from {when}; this reply was sent after reconnecting, to {n.possessive} "
                "messages from that time")
    else:
        about = (f"{n.possessive} messages and the " if rec.get("messages") else "the ") \
            + f"{_entries(len(ents))} {n.subject} wrote in that time"
        what = (f"{n.companion} was offline from {when}; this message was sent after reconnecting, about {about} (the "
                "notes on them are in the entries; their replies were not sent one by one)")
    mirror.to_chat(cfg, what, rec["text"], log, ref=("recovery", rec["id"], None))
