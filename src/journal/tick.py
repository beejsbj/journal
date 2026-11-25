"""The journal tick: run every few minutes (a Hermes ``no_agent`` cron job, or any scheduler).

1. Send any practice that is due in the user's local time (``send_due``: the library's scheduled practices, each
   prompt delivered by ``send_prompt``, which owns retries, ``sent.jsonl``, re-sends and the mirror).
   Then capture replies the gateway hook missed (``sweep``: the busy path), write the conversation since the
   last tick into its entries (``conversations``: both sides, threaded), move program runs on (``programs``: a
   settled phase answer the spawn missed, a phase owed a send), and transcribe audio files and, when
   ``images.describe`` is on, describe the pictures kept with replies.
2. Fold prompts that were answered, or have been visible for ``collapse_hours`` (a failed fold is retried with a
   doubling wait for about a day, ``fold``).
3. Write the companion's annotations for captured entries that don't have them yet.
4. Reply to each annotated message in the chat with its annotations; write and send the day
   reflection and week review when due (``reflect``); after a model outage that left his chat messages
   unanswered, or his entries without their replies, send one catch-up message once a model answers again, in place
   of those replies (``recovery``: the letter).
   Everything sent is also mirrored into the companion's chat session (``mirror``).
5. Prune the ledger and ``sent.jsonl`` (same retention, ``KEEP_DAYS``) and sweep the notebook. Every
   delivered prompt is also appended to ``sent.jsonl``, the engine's private memory of what it sent.
6. Tell the operator when the journal breaks (``ops``): incidents judged from what the steps recorded (the outage
   breaker, model steps falling to their floor, undelivered prompts, folds given up, a step that keeps crashing),
   under the lock; filed as GitHub issues or through the operator's command, from fixed templates only, after the
   lock is released (``ops.deliver_due``), so a slow ``gh`` never holds up a tick or a send. Never fails the tick.

The chat face's work the steps above call (``send_prompt``, ``fold``, ``reply_annotations``) lives in ``journal/chat/``
(issue #43); this module still exports those names, so ``tick.send_prompt`` and the rest are the same objects.

Silent when there is nothing to report; prints a line per action otherwise. The run exits non-zero,
so the scheduler alerts, when a prompt could not be delivered after ``SEND_ATTEMPTS`` ticks, when a
step crashed, when another bounded retry ran out, or when the model (or speech-to-text) has been
unreachable for ``outage.ALERT_AFTER`` (once; see ``Alert`` and ``outage``). An unreachable model defers
annotations, reflections and transcripts without using up their attempts; once one model run has found it, the
outage is known to every later step and tick (``outage``'s breaker): no model run starts until a probe, a few minutes
on and then less and less often, finds a model again. Held work then comes back in order, a few model calls a step
and a few chat messages a tick and an hour, and a message gone stale is recorded as skipped and not sent.
"""
from __future__ import annotations

import contextlib
import contextvars
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import (agent, audio, capture, channel, compose, context, conversations, entries, images, ledger, library,
               mirror, notebook, ops, outage, privacy, programs, prompts, recovery, reflect, schedule, slots, standing, sweep,
               ticklock, today, validate, version)
from .alert import Alert
# The chat face's steps and names (issue #43, CHAT-1: moved there unchanged). The tick still exports each of them, so
# ``tick.send_prompt``, ``tick.fold``, ``tick.SEND_ATTEMPTS`` … are the same objects as before.
from .chat.fold import (FOLD_ATTEMPTS, FOLD_RETRY_MINUTES, FOLD_SLACK, FOLD_STATE, _fold_edit,  # noqa: F401
                        _fold_owed_again, _folds_due_again, fold)
from .chat.replies import (REPLY_ATTEMPTS, _newest_words, _reply_owed, _reply_target,  # noqa: F401
                           _skip_annotation_replies, _skip_stale_reply, reply_annotations)
from .chat.send import (ATTACHED, EARLIER_OPENERS, SEND_ATTEMPTS, SENT_FIELDS, SendFailed,  # noqa: F401
                        _archive_sent, _attach, count_send_failure, prune_sent, send_prompt)
from .config import Config
from .library import Practice

ANNOTATE_ATTEMPTS = 3
AUDIO_ATTEMPTS = 3
IMAGE_ATTEMPTS = 2
KEEP_DAYS = 120
ERRORS_KEPT = 200


# the packs ``_opener`` builds while ``send_due`` holds them back until its pre-send check (rule 2 of OPEN-PROMPTS)
_PACKS: contextvars.ContextVar[list | None] = contextvars.ContextVar("journal_tick_packs", default=None)


def _opener(cfg: Config, practice: Practice | str, now_local: datetime, pid: str,
            log: list[str] | None = None) -> tuple[str, str, list, list]:
    """(opener, source, citations, asked) for a practice's prompt. ``opener.mode: plain`` asks no model; otherwise
    the companion's opener, validated, or the plain one whenever that fails (an opener is never deferred).

    When the model call fails, the source stays ``plain:agent-failed`` (it is stored in the ledger and
    ``sent.jsonl``), and ``log`` gets one line saying why: ``agent.failure_reason``, a whitelist (exit status,
    HTTP status, provider error class) that can never carry his words or the prompt, capped at 200 characters.
    A failure with nothing recognisable adds no line. The line contains "failed", so ``run`` also files it in
    ``errors.jsonl`` (kept to ``ERRORS_KEPT`` records) and ``journal status`` shows it under "Recent errors":
    wanted, since that is where one looks for why a prompt went out plain.

    The context pack is saved under ``pid`` (``context.save``), unless the caller holds packs (``_PACKS``, set by
    ``send_due``): then it is kept there to be saved once the caller knows the prompt goes out, so a send skipped by
    the pre-send check leaves no pack behind."""
    practice = library.resolve(practice, cfg)
    plain = compose.plain_opener(practice, cfg.names)
    if practice.opener["mode"] == "plain":
        return plain, "plain:practice", [], []
    try:
        stand = standing.collect(cfg, now_local)  # once, for the pack and the request's returning stance
    except Exception:  # noqa: BLE001 - never block an opener: the pack goes without it, the request without the stance
        stand = None
    pack = context.build(cfg, now_local, stand)
    held = _PACKS.get()
    if held is None:
        context.save(cfg, pid, pack)
    else:
        held.append(pack)
    # the run the stand already holds (one read of the ledger); context.unanswered never raises either
    missed = stand["missed"]["run"] if stand else context.unanswered(cfg, now_local)
    try:
        query = prompts.opener_request(cfg, practice, now_local, pack, practice.bank_with_ids(), missed, stand)
    except Exception as e:  # noqa: BLE001 - an odd ledger record must never stop the prompt: ask without the counts
        if log is not None:  # the class only, never ledger content; "failed" files it in errors.jsonl
            log.append(f"{pid}: opener request failed with the standing data ({type(e).__name__}); asked without it")
        query = prompts.opener_request(cfg, practice, now_local, pack, practice.bank_with_ids())
    try:
        raw = agent.ask(cfg, query, cfg.opener_budget_seconds, trip_at_once=True)
    except agent.AgentUnavailable as e:  # an opener is never deferred: the prompt goes out on time, plain
        reason = agent.failure_reason(str(e))
        if reason and log is not None:
            log.append(f"{pid}: opener: plain:agent-failed: {reason}"[:200])
        return plain, "plain:agent-failed", [], []
    if raw is None:
        return plain, "plain:agent-failed", [], []
    data = validate.parse(raw)
    verifier = context.make_verifier(cfg)
    ok, reason = validate.check(practice, data, pack, verifier)
    if log is not None:  # a quote of his filed under another entry's name passed: say so (names only, no quote)
        log.extend(f"{pid}: opener: {note}"[:200] for note in getattr(verifier, "notes", []))
    if not ok:
        return plain, f"plain:{reason}", [], []
    source = (cfg.names.companion or "companion").lower()  # recorded as "opener: <name>" in the entry
    return data["opener"].strip(), source, data.get("citations") or [], validate.asked(practice, data)


def _practice_fields(p: Practice) -> dict:
    """What a prompt record carries about its practice beyond ``kind`` (``library.record_fields``)."""
    return library.record_fields(p)


def send_due(cfg: Config, now_local: datetime, log: list[str], only: str | None = None, dry_run: bool = False,
             hold_off_record: bool = False) -> None:
    """Send every practice slot due now (or, with ``only``, that practice at once: ``journal send <id>``, which
    ``/start <id>`` spawns). An unknown ``only`` raises ``library.UnknownDefinition``. With ``hold_off_record`` the
    off-the-record check is made again after the opener is built, just before delivery: the model call can take
    minutes, and a bare ``/x`` sent meanwhile would be ended by this send (``_held_off_record``).

    A slot is done when it was delivered, ran out of attempts, was backfilled, or was answered on any face (rule 1 of
    OPEN-PROMPTS). A scheduled send reads its record again under the ledger lock just before it goes (rule 2): words
    saved on a face during the opener's model call stop it, and its pack is saved only after that check. A deliberate
    send (``only``) takes its slot from ``slots.resolve`` (rule 3): the practice's due or open slot, its missed one,
    the next one later today, else an extra entry; never an answered record, and it resolves again if he answered
    that slot on a face while its opener was built (``_resolve_again``). With ``channel.platform: none`` nothing
    is sent, written or counted (rule 6)."""
    if not cfg.has_channel and not dry_run:
        if only:
            log.append(f"{only}: not sent: this journal has no chat (channel.platform: none)")
        return
    snap = ledger.snapshot(cfg.ledger_path)
    done = {p["id"] for p in snap["prompts"] if p.get("message_id") or p.get("send_attempts", 0) >= SEND_ATTEMPTS
            or p.get("backfilled") or p.get("captures")}
    if only:
        practice = library.resolve(only, cfg)
        slot = slots.resolve(cfg, practice, now_local, now=now_local, data=snap, delivery=True)
        due = [(practice, slot.local_date, slot.id, now_local)]
    else:
        due = [(d.practice, d.local_date, d.prompt_id, d.at) for d in schedule.slots_due(cfg, now_local, done)]
    names = cfg.names
    for practice, local_date, pid, scheduled in due:
        packs: list[str] = []
        token = _PACKS.set(packs)
        try:
            opener, source, cites, asked = _opener(cfg, practice, now_local, pid, log)
        finally:
            _PACKS.reset(token)
        if only and not dry_run:
            pid, local_date = _resolve_again(cfg, practice, pid, local_date, now_local, log)
        html = compose.telegram_html(practice, now_local, opener, names=names)
        questions = library.question_snapshot(practice)  # what this prompt carries, kept as sent (library.snapshot_sections)
        record = {
            "id": pid, "stem": pid, "kind": practice.id,
            "local_date": local_date, "scheduled": scheduled.isoformat(),
            "chat_id": cfg.chat_id, "opener": opener, "opener_source": source, "citations": cites, "asked": asked,
            "plain_text": compose.plain_text(practice, now_local, opener, names), "html": html,
            **_practice_fields(practice),
            "questions": questions or None,  # always written: a re-send over the cap must clear the old set
        }
        if dry_run:
            for pack in packs:
                context.save(cfg, pid, pack)
            log.append(f"[dry-run] {pid} opener_source={source}\n{html}")
            continue
        if not only and _answered_meanwhile(cfg, pid, log):
            continue
        for pack in packs:
            context.save(cfg, pid, pack)
        if hold_off_record and _held_off_record(cfg, datetime.now(timezone.utc), pid, log):
            continue

        def plain(practice=practice) -> dict:
            # the plain opener weaves nothing in and cites nothing: the model's asked/citations go with its opener,
            # or the entry would say "Woven in" for questions he was never asked
            op = compose.plain_opener(practice, names)
            return {"opener": op, "opener_source": "plain:send-retry", "asked": [], "citations": [],
                    "html": compose.telegram_html(practice, now_local, op, names=names),
                    "plain_text": compose.plain_text(practice, now_local, op, names)}
        send_prompt(cfg, record, log, retry=plain,
                    # (the record the re-send replaced, as it stood: the old message keeps its opener)
                    refold=lambda old, practice=practice: compose.telegram_html(
                        practice, now_local, capture.stored_opener(cfg, old, sent=True), "sent again below", names),
                    refold_bare=lambda old, practice=practice: compose.telegram_html(
                        practice, now_local, None, "sent again below", names))


def _resolve_again(cfg: Config, practice: Practice, pid: str, local_date: str, now_local: datetime,
                   log: list[str]) -> tuple[str, str]:
    """A deliberate send's slot, read again under the ledger lock once its opener is built: ``slots.resolve`` never
    gives an answered slot, so if he answered it meanwhile (on a face, or in the chat; the model call can take
    minutes), the send resolves again (the next slot that fits, else an extra entry). So a deliberate delivery never
    attaches to his record from a face, nor re-sends a prompt he has just answered (rule 3). (pid, local_date)."""
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, pid)
        if not (q and (q.get("captures") or (q.get("opened_via") and not q.get("message_id")))):
            return pid, local_date
        slot = slots.resolve(cfg, practice, now_local, now=now_local, data=data, delivery=True)
    via = q.get("opened_via")
    log.append(f"{pid}: answered meanwhile" + (f" in {slots.FACES.get(via, via)}" if via else "")
               + f"; sending {slot.id}")
    return slot.id, slot.local_date


def _answered_meanwhile(cfg: Config, pid: str, log: list[str]) -> bool:
    """Rule 2's check, under the ledger lock, just before a scheduled send: the slot was answered on a face while the
    opener was built (the model call can take minutes). Then nothing is sent."""
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, pid)
        answered = bool(q and q.get("captures"))
        via = (q or {}).get("opened_via")
    if answered:
        log.append(f"{pid}: not sent: answered meanwhile" + (f" in {slots.FACES.get(via, via)}" if via else ""))
    return answered


def _transcribing(p: dict) -> bool:
    return any(c.get("transcript") == "pending" for c in p.get("captures") or [])


def process_audio(cfg: Config, now: datetime, log: list[str]) -> None:
    """Audio files kept with a reply: read the recording time (ffprobe), transcribe, fill in the block.

    A failing backend is an outage: the file waits (``outage``: retried at most every 30 minutes for up to
    7 days) without using an attempt. An empty transcript is a bad result and counts as one."""
    alerts: list[str] = []
    down, ran = False, 0
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        if down:
            break
        entry = Path(p.get("entry_path") or "")
        for i, c in enumerate(p.get("captures") or []):
            if c.get("transcript") != "pending" or not c.get("audio"):
                continue
            if outage.waiting(c, now):
                continue
            if ran:  # between transcriptions (``ticklock``)
                ticklock.pause()
            ran += 1
            path = cfg.root / c["audio"]
            meta = dict(c.get("meta") or {})
            for k, v in audio.probe(cfg, str(path)).items() if path.is_file() else ():
                meta.setdefault(k, v)  # Telegram's duration wins; the file adds its recording time
            text, why = audio.transcribe(cfg, str(path)) if path.is_file() else (None, "unavailable")
            backend_down = text is None and audio.is_outage(why)
            attempts = c.get("transcribe_attempts", 0) + (0 if backend_down else 1)
            final = (text is not None or why.startswith("unavailable") or attempts >= AUDIO_ATTEMPTS
                     or (backend_down and outage.expired(c, now)))
            if not final:
                alert, since = False, ""
                with ledger.locked(cfg.ledger_path) as data:
                    q = ledger.find(data, p["id"])
                    if q is not None and i < len(q.get("captures") or []):
                        cap = q["captures"][i]
                        if backend_down:
                            alert, since = outage.defer(cap, now, why), outage.describe(cap, cfg.tz)
                        else:  # the backend answered, badly: a real attempt
                            outage.clear(cap)
                            cap.update(transcribe_attempts=attempts, transcribe_error=why[:200])
                if backend_down:
                    log.append(f"{p['id']}: transcription deferred, speech-to-text unreachable ({why})")
                    if alert:
                        alerts.append(f"{c['audio']} ({p['id']}) is still not transcribed: speech-to-text has been "
                                      f"unreachable {since}; retrying every 30 minutes for up to 7 days")
                    down = True  # the backend is down for the rest of this tick too
                    break
                log.append(f"{p['id']}: transcription attempt {attempts}/{AUDIO_ATTEMPTS} failed ({why})")
                continue
            if backend_down:  # the outage outlasted the retry window: keep the file, say it was not transcribed
                alerts.append(f"{c['audio']} ({p['id']}) was not transcribed: speech-to-text was unreachable "
                              f"for {outage.GIVE_UP_AFTER.days} days ({why})")
            at = datetime.fromisoformat(c["at"]).astimezone(cfg.tz)
            heading = (conversations.marker(cfg, at, "audio", meta, conversations.entry_date_of(entry))  # a line of it
                       if c.get("rule") == "conversation" else entries.block_heading(at, "audio", meta))
            with ledger.locked(cfg.ledger_path) as data:  # the hook appends to entries under this lock too
                q = ledger.find(data, p["id"])
                if q is None or i >= len(q.get("captures") or []):
                    continue
                if entry.is_file() and not entries.fill_audio_block(
                        entry, c["audio"], text if text is not None else audio.NOT_TRANSCRIBED, heading,
                        audio.TRANSCRIBING, replace_only=text is None):
                    # (he removed the block, or only his own words name the recording: they are never filled)
                    log.append(f"{p['id']}: no block of {c['audio']} in {entry.name}; nothing written")
                q["captures"][i].update(meta=meta, transcript="done" if text is not None else "none",
                                        transcribe_attempts=attempts,
                                        **({"transcribe_error": why[:200]} if text is None else {}))
                if text is not None:
                    outage.clear(q["captures"][i])
            log.append(f"transcribed {c['audio']} for {p['id']}" if text is not None else
                       f"{p['id']}: {c['audio']} not transcribed ({why})")
    if alerts:
        raise Alert("; ".join(alerts))


def _embedded(entry: Path, name: str) -> bool | None:
    """Whether the entry still embeds the picture as a line of its own (the engine's embed): he can remove the block
    from the web app, and a picture whose block is gone is not his to describe any more. None when the entry cannot
    be read just now (a permissions or filesystem fault): that proves nothing, so the description stays pending;
    only a successful read, or an entry that is gone, says the block is."""
    try:
        body = entry.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:
        return None
    return f"![[{name}]]" in (line.rstrip() for line in body.splitlines())


def process_images(cfg: Config, now: datetime, log: list[str]) -> None:
    """``images.describe``: ask the companion (no tools, the picture attached) for a one-line description of each
    picture kept with a reply, and add it under her notes in the entry, marked as hers. Never his words, never in
    the block he wrote; a description that is not one short line without quotation marks is dropped
    (``images.valid_description``), and two bad answers end the attempts. A model that cannot be reached defers the
    picture (retried at most every 30 minutes, for up to 7 days, no attempt used) without raising an alert: a
    description is a nicety, not something the tick should fail over."""
    if not images.describing(cfg) or cfg.agent.get("enabled") is False:
        return
    limit, asked = outage.per_tick(cfg), 0  # (the paced catch-up: oldest first, a few a tick)
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        if asked >= limit:
            break
        entry = Path(p.get("entry_path") or "")
        for i, c in enumerate(p.get("captures") or []):
            if asked >= limit:
                break
            if c.get("describe") != "pending" or not c.get("images") or outage.waiting(c, now, cfg):
                continue
            done = list(c.get("described") or [])
            wanted = [n for n in c["images"] if images.describable(n)]  # (HEIC is kept, but cannot be handed over)
            state: dict = {}
            for name in wanted:
                if name in done:
                    continue
                path = cfg.root / name
                if not path.is_file() or not _embedded(entry, name):
                    continue  # (gone, or he removed its block, or the entry cannot be read now: not handed over)
                if asked >= limit:
                    log.append(f"image descriptions: {limit} asked for this tick; the rest wait for the next "
                               "(paced catch-up)")
                    break
                if asked:  # between model calls (``ticklock``); the block is checked again before the note is written
                    ticklock.pause()
                asked += 1
                try:
                    text = agent.ask(cfg, images.describe_request(name), 120, toolsets="none", image=str(path))
                except agent.AgentUnavailable as e:
                    with ledger.locked(cfg.ledger_path) as data:
                        q = ledger.find(data, p["id"])
                        if q is not None and i < len(q.get("captures") or []):
                            cap = q["captures"][i]
                            cap["described"] = done  # (the pictures already described are not asked again)
                            outage.defer(cap, now, str(e))
                            if outage.expired(cap, now):
                                cap["describe"] = "none"
                    log.append(f"{p['id']}: image description deferred, model unreachable ({e})")
                    return  # the model is down for the other pictures too
                description = images.valid_description(text)
                if description is None:
                    state["attempts"] = c.get("describe_attempts", 0) + 1
                    log.append(f"{p['id']}: image description attempt {state['attempts']}/{IMAGE_ATTEMPTS} failed")
                    break
                note = images.note_text(name, description)
                with ledger.locked(cfg.ledger_path):  # the hook and the conversation writer append under this lock
                    if not _embedded(entry, name):  # removed while the model answered (or unreadable: next tick)
                        continue
                    if note not in entry.read_text(encoding="utf-8"):
                        entries.add_companion_note(entry, note, now.astimezone(cfg.tz), None, cfg.names)
                done.append(name)
                log.append(f"described {name} for {p['id']}")
            with ledger.locked(cfg.ledger_path) as data:
                q = ledger.find(data, p["id"])
                if q is None or i >= len(q.get("captures") or []):
                    continue
                cap = q["captures"][i]
                outage.clear(cap)
                cap["described"] = done
                if "attempts" in state:
                    cap["describe_attempts"] = state["attempts"]
                left = [n for n in wanted if n not in done]
                if set(done) >= set(wanted) or state.get("attempts", 0) >= IMAGE_ATTEMPTS \
                        or any(not (cfg.root / n).is_file() or _embedded(entry, n) is False for n in left):
                    cap["describe"] = "done" if wanted and set(done) >= set(wanted) else "none"


def annotate(cfg: Config, now: datetime, log: list[str]) -> None:
    """Write the companion's annotations, oldest first, at most ``recovery.catchup_per_tick`` a tick. A model that
    cannot be reached defers the entry (retried at most every 30 minutes, up to 7 days, no attempt used; while the
    breaker holds, at no cost and the moment a model answers); a bad answer from a reachable model uses an attempt."""
    alerts: list[str] = []
    snap = ledger.snapshot(cfg.ledger_path)
    asked: set[Path] = set()  # a session's phase prompts share one entry: ask about it once a tick
    limit = outage.per_tick(cfg)  # (the paced catch-up: oldest first, a few a tick)
    for p in snap["prompts"]:
        path = Path(p.get("entry_path") or "")
        if not p.get("captures") or not path.is_file() or p.get("annotate_attempts", 0) >= ANNOTATE_ATTEMPTS:
            continue
        if p.get("program") and path in asked:
            continue
        if p.get("annotate") is False or (conversations.is_auto(p) and not conversations.annotate_auto(cfg)):
            continue  # a note saved from conversation: the day reflection covers it
        if p.get("program") and not p.get("session_complete"):
            continue  # a program session is annotated once, when it is complete (not phase by phase)
        if any(_transcribing(q) for q in _entry_prompts(snap, p)):  # the words, not the placeholder (any phase's)
            continue
        first = datetime.fromisoformat(p["captures"][0]["at"])
        if now - _last_words(snap, p) < timedelta(minutes=cfg.annotate_after_minutes):
            continue  # he may still be writing: wait for quiet, so his later words are annotated with the rest
        if outage.waiting(p, now, cfg):
            continue
        if asked:  # between model calls (``ticklock``); the entry is read after it
            ticklock.pause()
        body = path.read_text(encoding="utf-8")
        if not entries.annotations_pending(body):
            continue
        if len(asked) >= limit:
            log.append(f"annotations: {limit} asked for this tick; the rest wait for the next (paced catch-up)")
            break
        # her next chat reply aligns the annotations with a chat answer; words saved on a face have none, and the
        # chat after them is unrelated (OPEN-PROMPTS rule 9)
        reply = context.companion_reply_after(cfg, first, chat_id=p.get("chat_id"), platform=channel.platform_of(p)) \
            if not capture.by_face(p["captures"][0]) else None
        asked.add(path)
        try:
            text = agent.ask(cfg, prompts.annotation_request(cfg, entries.without_companion_replies(body), reply), 180,
                             toolsets="none")
        except agent.AgentUnavailable as e:
            alert, gave_up, since = False, False, ""
            with ledger.locked(cfg.ledger_path) as data:
                q = ledger.find(data, p["id"])
                if q is not None:
                    alert, since = outage.defer(q, now, str(e)), outage.describe(q, cfg.tz)
                    if outage.expired(q, now):
                        gave_up = True
                        q["annotate_attempts"] = ANNOTATE_ATTEMPTS
            log.append(f"{p['id']}: annotation deferred, model unreachable ({e})")
            if gave_up:
                alerts.append(f"{p['id']} was not annotated: the model was unreachable for "
                              f"{outage.GIVE_UP_AFTER.days} days ({e})")
            elif alert:
                alerts.append(f"{p['id']} is still not annotated: the model has been unreachable {since}; "
                              f"retrying every 30 minutes for up to 7 days")
            break  # the model is down for the other entries too; don't wait out another timeout
        ok = bool(text) and len(text) < 5000 and not text.lstrip().startswith("{")
        if ok:
            text, followups = notebook.split_followups(text)
            ok = bool(text.strip())
        if ok:
            with ledger.locked(cfg.ledger_path):  # the hook and the conversation writer append under this lock
                entries.write_annotations(path, text, cfg.names)
            added = 0
            for f in followups:  # the annotations are written: a notebook failure must not hold back completion
                try:
                    if notebook.add(cfg, f, source=path.stem, origin="annotation").get("duplicate"):
                        log.append(f"{p['id']}: follow-up already in the notebook, not added")
                    else:
                        added += 1
                except Exception as e:  # noqa: BLE001
                    log.append(f"{p['id']}: follow-up not saved to the notebook ({type(e).__name__}: {e})")
            log.append(f"annotated {p['id']}" + (f" (+{added} notebook)" if added else ""))
        with ledger.locked(cfg.ledger_path) as data:
            q = ledger.find(data, p["id"])
            if q is not None:
                outage.clear(q)  # the model answered, well or badly
                q["annotate_attempts"] = q.get("annotate_attempts", 0) + 1
                if ok:
                    q["annotated_at"] = now.isoformat()
            if not ok and p.get("program"):
                # a session's phase prompts all point at one entry: the bad answer is the entry's attempt, not one
                # per phase (six model calls of 180 s under the tick lock for one entry)
                for sib in data["prompts"]:
                    if sib is not q and sib.get("entry_path") == p.get("entry_path") \
                            and (sib.get("run"), sib.get("session")) == (p.get("run"), p.get("session")):
                        sib["annotate_attempts"] = sib.get("annotate_attempts", 0) + 1
        if not ok:
            log.append(f"{p['id']}: annotation attempt failed")
    if alerts:
        raise Alert("; ".join(alerts))


def _entry_prompts(snap: dict, p: dict) -> list[dict]:
    """``p`` and every other prompt filed in its entry (a program session's phases share one)."""
    path = p.get("entry_path")
    return [q for q in snap["prompts"] if q is p or (path and q.get("entry_path") == path)]


def _last_words(snap: dict, p: dict) -> datetime:
    """When his newest words in ``p``'s entry were captured: the newest capture of any prompt filed in that entry, by
    time, not list order (#42, finding 3)."""
    return max(t for t in map(_newest_words, _entry_prompts(snap, p)) if t is not None)


def _reflect(cfg: Config, now: datetime, log: list[str]) -> None:
    alerts = reflect.run(cfg, now, log)
    if alerts:
        raise Alert("; ".join(alerts))


def _when(p: dict) -> str:
    """When a prompt happened, for pruning: sent, else scheduled, else its local date."""
    return p.get("sent_at") or p.get("scheduled") or p.get("local_date") or ""


def _work_left(cfg: Config, p: dict) -> bool:
    """Whether a prompt still has background work queued that a later tick will finish: a transcript (the audio step
    always ends one, with a transcript or without), or a description while pictures are described (the companion
    switched off only for a while keeps it too: the images step describes it once she is back). A backfill of an old
    message files that work on a prompt dated then, so its date alone would let ``prune`` drop it first."""
    if _transcribing(p):
        return True
    return images.describing(cfg) and any(c.get("describe") == "pending" for c in p.get("captures") or [])


def prune(cfg: Config, now: datetime) -> None:
    cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
    # prompt boundaries outlive the ledger: the conversation threads (and a backfill) need them for good
    privacy.remember_sends(cfg, [t.isoformat() for t in privacy.send_times(cfg)])
    active = programs.active_ids(cfg)  # a program run in progress keeps every phase prompt, however old
    with ledger.locked(cfg.ledger_path) as data:
        data["prompts"] = [p for p in data["prompts"]
                           if _when(p) >= cutoff or p.get("run") in active or _work_left(cfg, p)]
        if data.get("reflections"):
            data["reflections"] = [r for r in data["reflections"] if (r.get("local_date") or "") >= cutoff[:10]]
        if data.get("recovery"):
            data["recovery"] = [r for r in data["recovery"] if (r.get("detected_at") or "") >= cutoff
                                or r.get("status") == "waiting"]
    prune_sent(cfg, cutoff)
    errors = cfg.state_dir / "errors.jsonl"
    if errors.is_file():
        lines = errors.read_text(encoding="utf-8").splitlines()
        if len(lines) > ERRORS_KEPT:
            errors.write_text("\n".join(lines[-ERRORS_KEPT:]) + "\n", encoding="utf-8")


def _record_run(cfg: Config, now: datetime, mode: str, log: list[str], errors: list[str], code: int) -> None:
    try:
        (cfg.state_dir / "last-tick.json").write_text(json.dumps(
            {"at": now.isoformat(), "mode": mode, "exit": code, "lines": log[-50:], **version.stamp()},
            ensure_ascii=False, indent=1),
            encoding="utf-8")
        if errors:
            with open(cfg.state_dir / "errors.jsonl", "a", encoding="utf-8") as fh:
                for e in errors:
                    fh.write(json.dumps({"at": now.isoformat(), "mode": mode, "error": e}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _record_errors(cfg: Config, now: datetime, mode: str, errors: list[str]) -> None:
    """Lines filed in ``errors.jsonl`` after the run was recorded (the ops notice's delivery, outside the lock)."""
    if not errors:
        return
    try:
        with open(cfg.state_dir / "errors.jsonl", "a", encoding="utf-8") as fh:
            for e in errors:
                fh.write(json.dumps({"at": now.isoformat(), "mode": mode, "error": e}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def run_backfill(cfg: Config, since, write: bool) -> tuple[int, list[str]]:
    """``journal backfill-conversations``: the conversation writer over past messages (a dry run unless
    ``write``), waiting for a running tick so the two never write the same entry at once. A dry run only
    reads: no lock, nothing created (not even the state dir)."""
    if not write:
        return 0, conversations.backfill(cfg, since, False)
    with ticklock.waited(cfg):
        return 0, conversations.backfill(cfg, since, write)


def run_sweep(cfg: Config) -> tuple[int, list[str]]:
    """``journal sweep``: one reconciliation pass now, waiting for a running tick."""
    with ticklock.waited(cfg):
        log: list[str] = []
        n = sweep.run(cfg, datetime.now(timezone.utc), log)
        if not log:
            log.append(f"swept: {n} message(s) captured")
        return 0, log


def _held_off_record(cfg: Config, now: datetime, only: str, log: list[str]) -> bool:
    """Whether a send asked for by a model acting for him (MCP's ``journal_start``: ``--hold-off-record``) must not go
    now: a bare ``/x`` window is open, or the privacy pass cannot say (fail closed). A prompt send ends such a window,
    so his next private message would be taken as the answer; the automatic sends hold the same way
    (``programs._off_the_record``). His own ``/start`` and the web app's Send are his acts and do not ask."""
    pv = privacy.view(cfg, now)
    if not (pv.closed or pv.open_at(now)):
        return False
    log.append(f"send {only} held: the chat is off the record (a bare /x window is open) or the privacy pass cannot "
               "say; nothing was sent. Ask again once the conversation has gone quiet.")
    return True


def _outage_news(cfg: Config, before: dict | None) -> list[str]:
    """A line when the breaker opened or closed during this run (``outage``), for ``last-tick.json`` and the log."""
    after = outage.known(cfg)
    if after and not before:
        return [f"model outage: no model answered ({after.get('last_error')}); model work is held until a probe "
                "answers, and prompts go out with the plain opener"]
    if before and not after:
        down = outage._when(before, "down_since")
        since = down.astimezone(cfg.tz).strftime("%-d %b %H:%M %Z") if down else "?"
        if outage.released(cfg):  # a refused probe released it: no model was heard from (HERMES-TOOLSETS)
            return [f"model outage released (down since {since}): the probe's own request was refused, so no model "
                    "was heard from; held work is tried again record by record, and a run that fails is found again"]
        return [f"model outage over: a model answered again (down since {since}); held work catches up, paced"]
    return []


def files_as_error(line: str) -> bool:
    """Whether a step's log line also goes to ``errors.jsonl``: one that says something failed, and a row the sweep
    withheld from his words (``sweep.WITHHELD``), which someone should look at."""
    return "failed" in line or line.startswith(sweep.WITHHELD)


# The scheduled tick's safe points between steps (``ticklock.pause``): before each of these, a waiting ``journal send``
# or ``journal program …`` (or sweep, or backfill) takes ``tick.lock`` and the tick takes it back after it, with the
# same ``now``. A step reads the ledger, runs, entries and the chat afresh when it starts, so what the waiter did is
# simply there. Every send stays under the lock and every ledger record of a send or an opening takes the real time
# (``send_prompt``, ``programs._open``), so the ledger's order of sends is the chat's. Not before ``send``: the tick's
# interrupts and its practices go out together, a ritual due in the same tick the newest message (as before). Not
# before ``sweep`` or ``conversations``: they read the chat right after the privacy pass, and call no model, so a
# yield there would gain a waiter nothing. A tick's ``now`` already lagged the clock by its model calls; a yield adds the
# waiter's time, and every comparison with ``now`` in the later steps errs towards "not yet" (the next tick's).
# Inside a step, the loops that call a model once per item yield between items too (``annotate``, ``process_images``,
# ``process_audio``, ``reflect.run``): no lock held there, and what they carry across items is theirs alone.
YIELDS_BEFORE = frozenset({"privacy", "programs", "fold", "audio", "images", "annotate", "reply", "reflect", "recover",
                           "mirror", "cache", "prune", "notebook"})


@contextlib.contextmanager
def _waited(cfg: Config):
    with ticklock.waited(cfg):
        yield True


def run(cfg: Config, only: str | None = None, dry_run: bool = False,
        hold_off_record: bool = False) -> tuple[int, list[str]]:
    """One tick (or a manual send of ``only``). Returns (exit code, log lines).

    A scheduled tick skips this round when another tick is running or the lock is held; a manual send waits for it.
    A scheduled tick lets a waiting send or program step go first at its safe points (``YIELDS_BEFORE``,
    ``ticklock``). With ``hold_off_record`` a manual send is not made while a bare ``/x`` window is open
    (``_held_off_record``).
    """
    hold = _waited(cfg) if only else ticklock.scheduled(cfg)
    with hold as ours:
        if not ours:
            return 0, []
        now = datetime.now(timezone.utc)
        now_local = now.astimezone(cfg.tz)
        log: list[str] = []
        errors: list[str] = []
        alerts = broken = 0
        crashed: dict[str, str] = {}  # step → exception class, for the ops notice's run of crashed ticks
        held = bool(only and hold_off_record)  # (checked here, before the opener's model call, and again after it)
        steps = [("send", lambda: None if held and _held_off_record(cfg, now, only, log)
                  else send_due(cfg, now_local, log, only, dry_run, **({"hold_off_record": True} if held else {})))]
        if not dry_run and not only:
            # the interrupts go before the practices: a ritual due in the same tick is then the newest message
            # (``tried``: the programs step looks again, for the ones a settle has just timed, not at these)
            tried: set[str] = set()
            steps.insert(0, ("interrupts", lambda: programs.send_due_interrupts(cfg, now, log, tried)))
            steps += [("privacy", lambda: privacy.update(cfg, now, log)),  # first: every later step reads it
                      ("sweep", lambda: sweep.run(cfg, now, log)),
                      ("conversations", lambda: conversations.run(cfg, now, log)),
                      ("programs", lambda: programs.step(cfg, now, log, tried=tried)),
                      ("fold", lambda: fold(cfg, now, log)), ("audio", lambda: process_audio(cfg, now, log)),
                      ("images", lambda: process_images(cfg, now, log)),
                      ("annotate", lambda: annotate(cfg, now, log)),
                      ("reply", lambda: reply_annotations(cfg, now, log)),
                      ("reflect", lambda: _reflect(cfg, now, log)),
                      ("recover", lambda: recovery.run(cfg, now, log)),
                      ("mirror", lambda: mirror.flush(cfg, now, log)),
                      ("cache", lambda: today.refresh(cfg, now, log)), ("prune", lambda: prune(cfg, now)),
                      ("notebook", lambda: notebook.sweep(cfg, now))]
        down = outage.known(cfg)  # (before and after: a line when this run found an outage, or saw one end)
        for name, step in steps:
            if name in YIELDS_BEFORE:
                ticklock.pause()  # (a send or program step waiting for tick.lock goes first: ``ticklock``)
            try:
                step()
            except Alert as e:
                alerts += 1
                log.append(f"ALERT: {e}")
                errors.append(str(e))
            except Exception as e:  # one failing step must not stop the others
                broken += 1
                crashed[name] = type(e).__name__
                log.append(f"error: {name}: {type(e).__name__}: {e}")
                errors.append(f"{name}: {type(e).__name__}: {e}")
        log.extend(_outage_news(cfg, down))
        notify = not dry_run and not only
        if notify:  # the operator's notice (ops.py), its local half: never raises; a failure is a "failed" line
            ops.assess(cfg, now, log, crashed=crashed)
        errors += [l for l in log if files_as_error(l) and l not in errors]
        # Exit 1 (so the scheduler's failure delivery fires) for an alert or a step that crashed.
        # Retryable soft failures ("fold failed", a reply that will be retried) stay exit 0.
        code = 1 if alerts or broken else 0
        if not dry_run:
            _record_run(cfg, now, f"send {only}" if only else "tick", log, errors, code)
    if notify:  # its delivery, with tick.lock released: a slow or hung gh holds up no tick and no send
        sent: list[str] = []
        ops.deliver_due(cfg, now, sent)
        log += sent
        _record_errors(cfg, now, "tick", [l for l in sent if files_as_error(l)])
    return code, log
