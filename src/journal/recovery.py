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
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import agent, channel, compose, context, ledger, mirror, outage, prompts, validate
from .ports import ChannelError
from .config import Config
from .ports import ChatUnreadable, Row

ATTEMPTS = 3          # bad answers from a reachable model before falling back to a note
SEND_ATTEMPTS = 6     # failed sends before falling back to a note
BUDGET = 150
CONTEXT_ROWS = 8      # conversation rows shown from before the outage
LOOKBACK_EXTRA = timedelta(hours=24)  # read this much further back than max_age to find her last real reply


def enabled(cfg: Config) -> bool:
    return cfg.recovery.get("enabled", True) is not False and bool(cfg.chat_id)


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
    for row in cfg.transcript(db).rows(since=after):
        if not row.meta.get("active", True) or not row.at > after:
            continue
        row_id, at = row.id, row.at
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
            "pending": not answered}


def unanswered(found: dict) -> bool:
    """An outage left his messages answered only by failure lines, and the newest was answered too."""
    return bool(found["his"] and found["failures"] and not found["pending"]
                and found["failures"][-1]["row"] > found["his"][-1]["row"])


# ── one catch-up per outage ──────────────────────────────────────────────────

def _fmt(cfg: Config, at: datetime) -> str:
    return at.astimezone(cfg.tz).strftime("%-d %b %H:%M")


def window(cfg: Config, rec: dict, end: datetime) -> str:
    start = datetime.fromisoformat(rec["offline_since"])
    return f"{_fmt(cfg, start)} to {_fmt(cfg, end)} {end.astimezone(cfg.tz).strftime('%Z')}"


def _covered(data: dict) -> int:
    return max([int(r.get("last_row") or 0) for r in data.get("recovery") or []] or [0])


def _note(cfg: Config, rec: dict, now: datetime, why: str, log: list[str]) -> None:
    """No reply (too old, or no good one): tell the live companion he wrote while she was away."""
    n = cfg.names
    lines = [f"- {m['at_local']}: {context.clean_user_text(m['text'])[:300]}" for m in rec["messages"]]
    what = (f"{n.companion} was offline from {window(cfg, rec, now)}; {n.user_or_them} wrote meanwhile and got "
            f"only an error line back. No catch-up reply was sent ({why})")
    with ledger.locked(cfg.ledger_path) as data:
        r = find(data, rec["id"])
        if r is not None:
            r.update(status="noted", noted_at=now.isoformat(), note_reason=why)
            outage.clear(r)
    mirrored = mirror.to_chat(cfg, what, "\n".join(lines), log, ref=("recovery", rec["id"], None))
    log.append(f"{rec['id']}: no catch-up sent ({why}); noted in the chat session" if mirrored
               else f"{rec['id']}: no catch-up sent ({why})")


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


def run(cfg: Config, now: datetime, log: list[str], db: Path | None = None) -> None:
    """One pass: look for an outage that left him unanswered; answer it once when a model is back."""
    chat = cfg.transcript(db)
    if not enabled(cfg) or chat.source is None:
        return
    data = ledger.snapshot(cfg.ledger_path)
    waiting = next((r for r in data.get("recovery") or [] if r.get("status") == "waiting"), None)
    floor = _covered({"recovery": [r for r in data.get("recovery") or [] if r.get("status") != "waiting"]})
    try:
        found = scan(cfg, db, now, floor)
    except chat.errors as e:
        log.append(f"outage catch-up skipped: sessions database unavailable ({type(e).__name__})")
        return
    if waiting and found["answered_after"] > int(waiting.get("messages", [{}])[0].get("row") or 0):
        _skip(cfg, waiting, now, "answered live since", log)  # he has had a real answer: nothing to catch up on
        return
    if not unanswered(found):
        return  # nothing missed, or his newest message is still being answered
    rec = _open(cfg, found, now)
    newest = datetime.fromisoformat(rec["messages"][-1]["at"])
    if now - newest > max_age(cfg):
        _note(cfg, rec, now, f"older than {max_age(cfg).total_seconds() / 3600:.0f} h", log)
        return
    if chat.busy():
        return  # she is answering something right now: don't talk over it
    if rec.get("text"):  # written earlier; only the send failed
        _send(cfg, rec, now, log)
        return
    if outage.waiting(rec, now) and not agent.answered_this_run():
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
                r["outage_seen"] = True  # the model really was unreachable
        log.append(f"{rec['id']}: catch-up deferred, model still unreachable ({e})")
        return
    if not rec.get("outage_seen"):  # the model answered at once and no row says it had failed
        _skip(cfg, rec, now, "no sign of an outage: the turn failed for another reason", log)
        return
    corpus = "\n".join([m["text"] for m in rec["messages"]] + [c[2] for c in rec.get("context") or []])
    ok, reason = validate.check_catchup(raw, corpus)
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
        else:
            r["send_attempts"] = r.get("send_attempts", 0) + 1
            r["send_error"] = (error or "")[:300]
        tries = r.get("send_attempts", 0)
    if not mid:
        log.append(f"{rec['id']}: catch-up send attempt {tries}/{SEND_ATTEMPTS} failed ({error})")
        if tries >= SEND_ATTEMPTS:
            _note(cfg, rec, now, f"{channel.label(cfg)} refused it {tries} times", log)
        return
    log.append(f"sent the catch-up for {rec['id']} ({len(rec['messages'])} unanswered message(s))")
    what = (f"{cfg.names.companion} was offline from {rec.get('window') or window(cfg, rec, now)}; this reply was "
            f"sent after reconnecting, to {cfg.names.possessive} messages from that time")
    mirror.to_chat(cfg, what, rec["text"], log, ref=("recovery", rec["id"], None))
