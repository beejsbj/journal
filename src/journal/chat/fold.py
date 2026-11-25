"""The chat face's fold: a sent prompt's message is edited once it was answered or its window passed (``fold``), with
a doubling retry for a failed edit and a give-up the operator hears of.

Moved out of ``tick.py`` unchanged (issue #43, CHAT-1); ``tick`` still exports every name here."""
from __future__ import annotations

from datetime import datetime, timedelta

from .. import capture, channel, compose, ledger, library
from ..config import Config
from ..ports import ChannelError

# A fold that fails is tried again on a later tick, the wait doubling from five minutes (5, 10, 20 … 640 minutes):
# FOLD_ATTEMPTS tries over about 21 hours (``FOLD_RETRY_MINUTES``' sum), so a night's token-helper or network outage
# heals on its own; a refusal for good (``ChannelError.permanent``) gives up at once. See ``fold``.
FOLD_RETRY_MINUTES = (5, 10, 20, 40, 80, 160, 320, 640)  # the wait after the 1st … 8th failure
FOLD_ATTEMPTS = len(FOLD_RETRY_MINUTES) + 1
FOLD_SLACK = timedelta(minutes=1)  # a retry is due a little early, so a tick that starts a few seconds soon still tries
# a fold's account; a new message clears it (``fold_gave_up_captures``: his replies counted when a fold was given up
# after its attempts, so a later one earns it one more try)
FOLD_STATE = ("fold_attempts", "fold_error", "fold_retry_at", "fold_gave_up", "fold_gave_up_captures")


def fold(cfg: Config, now: datetime, log: list[str]) -> None:
    """Fold answered or expired prompts (each after its own window, ``capture.window``): a practice's prompt keeps its
    header and the opener it was sent with and puts a status line where the questions were; a program phase shrinks
    to its header line. Where the message cannot be edited (a platform without edits, or a prompt sent on another
    platform before the channel moved) it is closed in the ledger all the same and stays as it was sent.

    A failed edit is retried on later ticks with a doubling wait (``FOLD_RETRY_MINUTES``; the record keeps
    ``fold_attempts``, the last ``fold_error`` and ``fold_retry_at``, the time it is next due), one try a due tick, and
    after the first failure that may pass (``ChannelError.permanent`` false: the token, the network) no other message
    is edited this tick, since they would fail the same way. A fold that can never succeed (a permanent refusal) or
    has failed ``FOLD_ATTEMPTS`` times is given up: the record is closed as if folded, the message stays as sent, and
    ``fold_gave_up`` says so (``at``, ``reason``: ``permanent`` | ``attempts``, ``attempts``, ``error``). A permanent
    give-up is never tried again unless the prompt is sent again. One after its attempts gets one more try each time
    a reply of his is captured to the prompt after it (``fold_gave_up_captures`` counts them; the try failing gives it
    up again, so it cannot loop). A fold that succeeds clears the retry fields, and since the channel works again,
    makes the folds waiting out a backoff due on the next tick (still one try each a tick)."""
    todo = []
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        if not p.get("message_id"):
            continue
        again = False  # a fold given up after its attempts, owed one more try by a later reply
        if p.get("fold_gave_up"):
            again = _fold_owed_again(p)
            if not again:
                continue
        expired = not p.get("collapsed_at") and now - datetime.fromisoformat(p["sent_at"]) >= capture.window(cfg, p)
        if p.get("phase_open") and not expired:
            continue  # a program phase still taking answers folds when the run moves on (or when it expires)
        if p.get("fold_retry_at") and now + FOLD_SLACK < datetime.fromisoformat(p["fold_retry_at"]):
            continue  # a failed fold waits its turn
        if expired or p.get("needs_edit") or again:
            todo.append(p)
    chat = cfg.channel() if todo else None
    held = False  # a failure that may pass: the messages after it wait for the next tick, untried, attempts unspent
    through = False  # an edit went through: the channel works, so folds waiting out a backoff need not wait
    failed = set()  # ... except one that failed this very tick
    for p in todo:  # network calls happen outside the ledger lock
        answered = bool(p.get("captures"))
        status = "answered ✓" if answered else "missed — reply anytime"
        error = None
        editable = chat.caps.edit and channel.same_chat(cfg, p)
        if editable and held:
            continue
        try:
            if editable:
                _fold_edit(cfg, chat, p, status, log)
        except ChannelError as e:
            error = e
        gave_up = wait = None
        with ledger.locked(cfg.ledger_path) as data:
            q = ledger.find(data, p["id"])
            if q is None:
                continue
            if error is not None:
                attempts = q["fold_attempts"] = q.get("fold_attempts", 0) + 1
                q["fold_error"] = str(error)
                if error.permanent or attempts >= FOLD_ATTEMPTS or q.get("fold_gave_up"):
                    q.pop("fold_retry_at", None)
                    gave_up = q["fold_gave_up"] = {"at": now.isoformat(), "attempts": attempts, "error": str(error),
                                                   "reason": "permanent" if error.permanent else "attempts"}
                    if error.permanent:
                        q.pop("fold_gave_up_captures", None)
                    else:  # his replies so far; the next one earns one more try (``_fold_owed_again``)
                        q["fold_gave_up_captures"] = len(p.get("captures") or [])
                else:
                    wait = FOLD_RETRY_MINUTES[attempts - 1]
                    q["fold_retry_at"] = (now + timedelta(minutes=wait)).isoformat()
            else:
                for k in FOLD_STATE:
                    q.pop(k, None)
            if wait is None:  # folded, closed, or given up
                q["collapsed_at"] = q.get("collapsed_at") or now.isoformat()
                if gave_up or len(q.get("captures", [])) == len(p.get("captures", [])):
                    q["needs_edit"] = False  # a reply that landed mid-edit keeps its flag (a fold given up is done)
        if error is None:
            through = through or editable
            log.append(f"folded {p['id']} ({status})" if editable else
                       f"closed {p['id']} ({status}; {channel.platform_of(p)} cannot edit it here, so it stays as sent)")
            continue
        held = held or not error.permanent
        failed.add(p["id"])
        if gave_up:
            why = "refused for good" if error.permanent else f"{gave_up['attempts']} attempts"
            log.append(f"{p['id']}: fold failed ({error}); given up ({why}): closed, the message stays as sent")
        else:
            log.append(f"{p['id']}: fold failed ({error}); trying again in {wait} min")
    if through:
        _folds_due_again(cfg, now, log, failed)


def _fold_owed_again(p: dict) -> bool:
    """Whether a fold given up after its attempts is owed one more try: a reply of his was captured to the prompt
    since (a permanent refusal never is; a record given up before the count was kept is not)."""
    gave = p.get("fold_gave_up") or {}
    seen = p.get("fold_gave_up_captures")
    return gave.get("reason") == "attempts" and isinstance(seen, int) and len(p.get("captures") or []) > seen


def _folds_due_again(cfg: Config, now: datetime, log: list[str], failed: set) -> None:
    """After an edit went through, the folds waiting out a backoff after a failure that may pass (the token, the
    network) are due on the next tick rather than at the end of their wait: whatever failed them has passed. Not one
    that failed this tick (``failed``): its own failure is newer than the success."""
    moved = []
    with ledger.locked(cfg.ledger_path) as data:
        for q in data.get("prompts") or []:
            due = q.get("fold_retry_at")
            if due and q.get("id") not in failed and not q.get("fold_gave_up") and datetime.fromisoformat(due) > now:
                q["fold_retry_at"] = now.isoformat()
                moved.append(q["id"])
    if moved:
        log.append(f"an edit went through: {len(moved)} fold(s) waiting to retry are due on the next tick")


def _fold_edit(cfg: Config, chat, p: dict, status: str, log: list[str]) -> None:
    """Edit one prompt's message to its fold; raises ``ChannelError``."""
    bare = None  # the fold without the opener, for a message Telegram cannot parse with it
    if p.get("program"):  # a phase message: its header line, from what the record says
        folded = compose.folded_phase_html(p, "answered ✓" if p.get("captures") else "open — reply anytime", cfg.names)
    else:
        # the opener is the record's own (what was sent, the plain one after a fallback), under the check the
        # web app makes of it; nothing is read from the chat, so a fold never carries what was said off the record
        practice = library.for_prompt(cfg, p)
        sent_local = datetime.fromisoformat(p["sent_at"]).astimezone(cfg.tz)
        folded = compose.telegram_html(practice, sent_local, capture.stored_opener(cfg, p, sent=True), status,
                                       cfg.names)
        bare = compose.telegram_html(practice, sent_local, None, status, cfg.names)
    try:
        chat.edit(p["chat_id"], p["message_id"], folded)
    except ChannelError as e:
        if bare is None or "parse entities" not in str(e):
            raise
        log.append(f"{p['id']}: the opener could not be parsed ({e}); folding without it")
        chat.edit(p["chat_id"], p["message_id"], bare)
