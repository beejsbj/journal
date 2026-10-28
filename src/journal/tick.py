"""The journal tick: run every few minutes (a Hermes ``no_agent`` cron job, or any scheduler).

1. Send any practice that is due in the user's local time (``send_due``: the library's scheduled practices, each
   prompt delivered by ``send_prompt``, which owns retries, ``sent.jsonl``, re-sends and the mirror).
   Then capture replies the gateway hook missed (``sweep``: the busy path), write the conversation since the
   last tick into its entries (``conversations``: both sides, threaded), move program runs on (``programs``: a
   settled phase answer the spawn missed, a phase owed a send), and transcribe audio files and, when
   ``images.describe`` is on, describe the pictures kept with replies.
2. Fold prompts that were answered, or have been visible for ``collapse_hours``.
3. Write the companion's annotations for captured entries that don't have them yet.
4. Reply to each annotated message in the chat with its annotations; write and send the day
   reflection and week review when due (``reflect``); after a model outage that left his chat messages
   unanswered, send one catch-up reply once a model answers again (``recovery``).
   Everything sent is also mirrored into the companion's chat session (``mirror``).
5. Prune the ledger and ``sent.jsonl`` (same retention, ``KEEP_DAYS``) and sweep the notebook. Every
   delivered prompt is also appended to ``sent.jsonl``, the engine's private memory of what it sent.

Silent when there is nothing to report; prints a line per action otherwise. The run exits non-zero,
so the scheduler alerts, when a prompt could not be delivered after ``SEND_ATTEMPTS`` ticks, when a
step crashed, when another bounded retry ran out, or when the model (or speech-to-text) has been
unreachable for ``outage.ALERT_AFTER`` (once; see ``Alert`` and ``outage``). An unreachable model defers
annotations, reflections and transcripts without using up their attempts.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import (agent, audio, capture, channel, compose, context, conversations, entries, images, ledger, library,
               mirror, notebook, outage, privacy, programs, prompts, recovery, reflect, schedule, standing, sweep, today,
               validate, version)
from .config import Config
from .library import Practice
from .ports import ChannelError, Keyboard

ANNOTATE_ATTEMPTS = 3
AUDIO_ATTEMPTS = 3
IMAGE_ATTEMPTS = 2
SEND_ATTEMPTS = 3
FOLD_ATTEMPTS = 6
EARLIER_OPENERS = 5  # the openers of earlier sends of one prompt a record keeps (so a quote of a folded one matches)
REPLY_ATTEMPTS = 6
KEEP_DAYS = 120
ERRORS_KEPT = 200


class Alert(RuntimeError):
    """A bounded retry ran out: reported once, and the run exits 1 so the scheduler alerts."""


class SendFailed(Alert):
    """A prompt could not be delivered after SEND_ATTEMPTS ticks."""


def _opener(cfg: Config, practice: Practice | str, now_local: datetime, pid: str,
            log: list[str] | None = None) -> tuple[str, str, list, list]:
    """(opener, source, citations, asked) for a practice's prompt. ``opener.mode: plain`` asks no model; otherwise
    the companion's opener, validated, or the plain one whenever that fails (an opener is never deferred).

    When the model call fails, the source stays ``plain:agent-failed`` (it is stored in the ledger and
    ``sent.jsonl``), and ``log`` gets one line saying why: ``agent.failure_reason``, a whitelist (exit status,
    HTTP status, provider error class) that can never carry his words or the prompt, capped at 200 characters.
    A failure with nothing recognisable adds no line. The line contains "failed", so ``run`` also files it in
    ``errors.jsonl`` (kept to ``ERRORS_KEPT`` records) and ``journal status`` shows it under "Recent errors":
    wanted, since that is where one looks for why a prompt went out plain."""
    practice = library.resolve(practice, cfg)
    plain = compose.plain_opener(practice, cfg.names)
    if practice.opener["mode"] == "plain":
        return plain, "plain:practice", [], []
    try:
        stand = standing.collect(cfg, now_local)  # once, for the pack and the request's returning stance
    except Exception:  # noqa: BLE001 - never block an opener: the pack goes without it, the request without the stance
        stand = None
    pack = context.build(cfg, now_local, stand)
    context.save(cfg, pid, pack)
    # the run the stand already holds (one read of the ledger); context.unanswered never raises either
    missed = stand["missed"]["run"] if stand else context.unanswered(cfg, now_local)
    try:
        query = prompts.opener_request(cfg, practice, now_local, pack, practice.bank_with_ids(), missed, stand)
    except Exception as e:  # noqa: BLE001 - an odd ledger record must never stop the prompt: ask without the counts
        if log is not None:  # the class only, never ledger content; "failed" files it in errors.jsonl
            log.append(f"{pid}: opener request failed with the standing data ({type(e).__name__}); asked without it")
        query = prompts.opener_request(cfg, practice, now_local, pack, practice.bank_with_ids())
    try:
        raw = agent.ask(cfg, query, cfg.opener_budget_seconds)
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


SENT_FIELDS = ("id", "kind", "local_date", "scheduled", "sent_at", "sent_at_local", "chat_id", "message_id",
               "previous_message_ids", "opener", "opener_source", "asked")


def _archive_sent(cfg: Config, record: dict) -> None:
    """Append every delivered prompt to ``sent.jsonl`` (pruned with the ledger, to KEEP_DAYS): the engine's
    own memory of what it sent, not an archive. The journal never holds an unanswered prompt's opener."""
    try:
        path = cfg.state_dir / "sent.jsonl"
        line = json.dumps({k: record[k] for k in SENT_FIELDS if k in record}, ensure_ascii=False)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass  # the ledger still has it; never fail a delivered send over the archive


def _practice_fields(p: Practice) -> dict:
    """What a prompt record carries about its practice beyond ``kind``. Nothing for a built-in, so the rituals'
    records are what they always were (and ``windows.collapse_hours`` keeps governing them); a practice of his own
    records its title (a fold or a late reply still has it if the file is renamed or deleted), its window, and
    ``annotate: false`` when it asks for no annotations."""
    out: dict = {}
    if not p.builtin:
        out.update(title=p.title, collapse_hours=p.collapse_hours)
        if p.emoji:
            out["emoji"] = p.emoji
    if not p.annotate:
        out["annotate"] = False
    return out


def send_due(cfg: Config, now_local: datetime, log: list[str], only: str | None = None, dry_run: bool = False,
             hold_off_record: bool = False) -> None:
    """Send every practice slot due now (or, with ``only``, that practice at once: ``journal send <id>``, which
    ``/start <id>`` spawns). An unknown ``only`` raises ``library.UnknownDefinition``. With ``hold_off_record`` the
    off-the-record check is made again after the opener is built, just before delivery: the model call can take
    minutes, and a bare ``/x`` sent meanwhile would be ended by this send (``_held_off_record``)."""
    snap = ledger.snapshot(cfg.ledger_path)["prompts"]
    done = {p["id"] for p in snap if p.get("message_id") or p.get("send_attempts", 0) >= SEND_ATTEMPTS
            or p.get("backfilled")}
    if only:
        due = [schedule.Due(library.resolve(only, cfg), 1, now_local)]
    else:
        due = schedule.slots_due(cfg, now_local, done)
    names = cfg.names
    for d in due:
        practice, local_date, pid = d.practice, d.local_date, d.prompt_id
        opener, source, cites, asked = _opener(cfg, practice, now_local, pid, log)
        html = compose.telegram_html(practice, now_local, opener, names=names)
        questions = library.question_snapshot(practice)  # what this prompt carries, kept as sent (library.snapshot_sections)
        record = {
            "id": pid, "stem": entries.stem_for(local_date, practice.id, d.slot), "kind": practice.id,
            "local_date": local_date, "scheduled": d.at.isoformat(),
            "chat_id": cfg.chat_id, "opener": opener, "opener_source": source, "citations": cites, "asked": asked,
            "plain_text": compose.plain_text(practice, now_local, opener, names), "html": html,
            **_practice_fields(practice),
            "questions": questions or None,  # always written: a re-send over the cap must clear the old set
        }
        if dry_run:
            log.append(f"[dry-run] {pid} opener_source={source}\n{html}")
            continue
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
                        practice, now_local, capture.stored_opener(cfg, old), "sent again below", names),
                    refold_bare=lambda old, practice=practice: compose.telegram_html(
                        practice, now_local, None, "sent again below", names))


def count_send_failure(cfg: Config, record: dict, error: Exception, log: list[str]) -> int:
    """A send of ``record`` failed: count the attempt on its ledger entry (created with ``scheduled`` so prune keeps
    the count) and raise ``SendFailed`` on the ``SEND_ATTEMPTS``-th. Returns the attempts so far."""
    pid = record["id"]
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, pid)
        if q is None:  # "scheduled" keeps prune() from dropping the attempt count
            q = channel.stamp(cfg, {"id": pid, "kind": record["kind"], "local_date": record["local_date"],
                                    "chat_id": cfg.chat_id, "scheduled": record["scheduled"]})
            data["prompts"].append(q)
        q["send_attempts"] = q.get("send_attempts", 0) + 1
        q["send_error"] = str(error)[:300]
        attempts = q["send_attempts"]
    log.append(f"{pid}: send attempt {attempts}/{SEND_ATTEMPTS} failed ({error})")
    if attempts >= SEND_ATTEMPTS:
        raise SendFailed(f"{pid} was not delivered after {attempts} attempts: {error}") from error
    return attempts


def send_prompt(cfg: Config, record: dict, log: list[str], *, retry=None, refold=None, refold_bare=None,
                label: str | None = None, keyboard: Keyboard | None = None) -> bool:
    """Deliver one prepared prompt record and keep the ledger's account of it. Returns True when it was delivered.

    ``record`` holds at least ``id``, ``kind``, ``local_date``, ``scheduled``, ``chat_id``, ``html`` and
    ``plain_text``; every field is stored as given (and the platform, off Telegram: ``channel.stamp``). A send error
    (``ports.ChannelError``) retries once at once with ``retry()``'s fields
    (the plain opener) when given; a send that still fails counts an attempt on the record (``send_attempts``,
    created with ``scheduled`` so prune keeps the count) and raises ``SendFailed`` on the ``SEND_ATTEMPTS``-th. A
    delivered record is stored (a second send of the same id makes the new message current: the old one still
    takes replies, is listed in ``previous_message_ids``, and is edited to ``refold(old)``'s text, ``old`` being the record it replaced, as it stood; if Telegram cannot parse
    that, ``refold_bare(old)``'s; a platform that cannot edit leaves it as sent), appended to
    ``sent.jsonl`` and mirrored into the companion's chat (``label``: what was sent, default "the <kind> prompt").
    Practices use it through ``send_due``; program phases and one-off interrupts will too."""
    pid, kind, names = record["id"], record["kind"], cfg.names
    channel.stamp(cfg, record)
    chat = cfg.channel()
    try:
        try:
            record["message_id"] = chat.send(cfg.chat_id, record["html"], keyboard=keyboard)
        except ChannelError as e:
            if retry is None:
                raise
            log.append(f"{pid}: send failed ({e}); retrying with the plain opener")
            record.update(retry())
            record["message_id"] = chat.send(cfg.chat_id, record["html"], keyboard=keyboard)
    except Exception as e:
        count_send_failure(cfg, record, e, log)
        return False
    record["sent_at"] = datetime.now(timezone.utc).isoformat()
    record["sent_at_local"] = datetime.now(timezone.utc).astimezone(cfg.tz).strftime("%H:%M %Z")
    replaced = None
    with ledger.locked(cfg.ledger_path) as data:
        q = ledger.find(data, pid)
        if q is None:
            data["prompts"].append(record)
        elif not q.get("message_id"):
            q.update(record)
        else:  # manual re-trigger: the new message becomes current; the old one still accepts replies
            replaced = q["message_id"]
            old = dict(q)
            before = [*(q.get("previous_message_ids") or []), replaced]
            if q.get("opener") and not q.get("program"):  # the replaced message keeps that opener when it folds
                q["earlier_openers"] = [*(q.get("earlier_openers") or []), q["opener"]][-EARLIER_OPENERS:]
            if q.get("program") and q.get("sent_at"):  # (practices' records stay as they were)
                # the replaced message was current from then on: the sweep matches a message by its send time
                q["earlier_sent_at"] = [*(q.get("earlier_sent_at") or []), q["sent_at"]]
            # the entry stays where its replies already are (``capture.entry_stem`` may have moved it off a file
            # of his): the new record's default stem must not send the next reply elsewhere
            q.update({k: v for k, v in record.items() if k != "id" and not (k == "stem" and q.get("stem"))})
            # every earlier message still takes replies: the ones before this re-send, the message it replaced, and
            # the earlier halves of a phase that went out in two or more messages (``record`` carries the new ones)
            q["previous_message_ids"] = list(dict.fromkeys([*before, *(record.get("previous_message_ids") or [])]))
            q.pop("collapsed_at", None)
            q["needs_edit"] = False
        if q is not None and "platform" not in record:  # sent on Telegram now (its records name none): a platform
            q.pop("platform", None)  # an earlier send on another one wrote would make it that chat's (``same_chat``)
    today.note_send(cfg)  # his next message starts a thread (and gets the today block)
    _archive_sent(cfg, {**record, "id": pid, **({"previous_message_ids": [replaced]} if replaced else {})})
    if replaced and refold is not None and chat.caps.edit and channel.same_chat(cfg, old):
        try:
            try:
                chat.edit(cfg.chat_id, replaced, refold(old))
            except ChannelError as e:
                if refold_bare is None or "parse entities" not in str(e):
                    raise
                chat.edit(cfg.chat_id, replaced, refold_bare(old))  # without the opener
        except ChannelError:
            pass
    log.append(f"sent {pid} (opener: {record.get('opener_source')}, wove {len(record.get('asked') or [])} questions)")
    what = (f"{names.companion} sent {label or f'the {kind} prompt'}{' again' if replaced else ''} at "
            f"{mirror.stamp(cfg, datetime.fromisoformat(record['sent_at']))}")
    mirror.to_chat(cfg, what, record["plain_text"], log, ref=("prompts", pid, None))
    return True


def fold(cfg: Config, now: datetime, log: list[str]) -> None:
    """Fold answered or expired prompts (each after its own window, ``capture.window``): a practice's prompt keeps its
    header and the opener it was sent with and puts a status line where the questions were; a program phase shrinks
    to its header line. Where the message cannot be edited (a platform without edits, or a prompt sent on another
    platform before the channel moved) it is closed in the ledger all the same and stays as it was sent."""
    todo = []
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        if not p.get("message_id"):
            continue
        expired = not p.get("collapsed_at") and now - datetime.fromisoformat(p["sent_at"]) >= capture.window(cfg, p)
        if p.get("phase_open") and not expired:
            continue  # a program phase still taking answers folds when the run moves on (or when it expires)
        if expired or p.get("needs_edit"):
            todo.append(p)
    chat = cfg.channel() if todo else None
    for p in todo:  # network calls happen outside the ledger lock
        answered = bool(p.get("captures"))
        status = "answered ✓" if answered else "missed — reply anytime"
        error = None
        editable = chat.caps.edit and channel.same_chat(cfg, p)
        try:
            if editable:
                _fold_edit(cfg, chat, p, status, log)
        except ChannelError as e:
            error = str(e)
            log.append(f"{p['id']}: fold failed ({e})")
        with ledger.locked(cfg.ledger_path) as data:
            q = ledger.find(data, p["id"])
            if q is None:
                continue
            if error:  # retry on later ticks; give up after FOLD_ATTEMPTS so a deleted message can't loop
                q["fold_error"] = error
                q["fold_attempts"] = q.get("fold_attempts", 0) + 1
                if q["fold_attempts"] < FOLD_ATTEMPTS:
                    continue
            q["collapsed_at"] = q.get("collapsed_at") or now.isoformat()
            if len(q.get("captures", [])) == len(p.get("captures", [])):
                q["needs_edit"] = False  # a reply that landed mid-edit keeps its flag
        if not error:
            log.append(f"folded {p['id']} ({status})" if editable else
                       f"closed {p['id']} ({status}; {channel.platform_of(p)} cannot edit it here, so it stays as sent)")


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
        folded = compose.telegram_html(practice, sent_local, capture.stored_opener(cfg, p), status, cfg.names)
        bare = compose.telegram_html(practice, sent_local, None, status, cfg.names)
    try:
        chat.edit(p["chat_id"], p["message_id"], folded)
    except ChannelError as e:
        if bare is None or "parse entities" not in str(e):
            raise
        log.append(f"{p['id']}: the opener could not be parsed ({e}); folding without it")
        chat.edit(p["chat_id"], p["message_id"], bare)


def _transcribing(p: dict) -> bool:
    return any(c.get("transcript") == "pending" for c in p.get("captures") or [])


def process_audio(cfg: Config, now: datetime, log: list[str]) -> None:
    """Audio files kept with a reply: read the recording time (ffprobe), transcribe, fill in the block.

    A failing backend is an outage: the file waits (``outage``: retried at most every 30 minutes for up to
    7 days) without using an attempt. An empty transcript is a bad result and counts as one."""
    alerts: list[str] = []
    down = False
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        if down:
            break
        entry = Path(p.get("entry_path") or "")
        for i, c in enumerate(p.get("captures") or []):
            if c.get("transcript") != "pending" or not c.get("audio"):
                continue
            if outage.waiting(c, now):
                continue
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
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        entry = Path(p.get("entry_path") or "")
        for i, c in enumerate(p.get("captures") or []):
            if c.get("describe") != "pending" or not c.get("images") or outage.waiting(c, now):
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
    """Write the companion's annotations. A model that cannot be reached defers the entry (retried at most
    every 30 minutes, up to 7 days, no attempt used); a bad answer from a reachable model uses an attempt."""
    alerts: list[str] = []
    snap = ledger.snapshot(cfg.ledger_path)
    asked: set[Path] = set()  # a session's phase prompts share one entry: ask about it once a tick
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
        if _transcribing(p):  # annotate the words, not the placeholder
            continue
        first = datetime.fromisoformat(p["captures"][0]["at"])
        if now - first < timedelta(minutes=cfg.annotate_after_minutes):
            continue
        if outage.waiting(p, now):
            continue
        body = path.read_text(encoding="utf-8")
        if not entries.annotations_pending(body):
            continue
        reply = context.companion_reply_after(cfg, first, chat_id=p.get("chat_id"), platform=channel.platform_of(p))
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
            for f in followups:  # the annotations are written: a notebook failure must not hold back completion
                try:
                    notebook.add(cfg, f, source=path.stem)
                except Exception as e:  # noqa: BLE001
                    log.append(f"{p['id']}: follow-up not saved to the notebook ({type(e).__name__}: {e})")
            log.append(f"annotated {p['id']}" + (f" (+{len(followups)} notebook)" if followups else ""))
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


def _reply_target(p: dict) -> str | None:
    """The captured message the annotations answer: the first capture that has a Telegram message id (digits; a
    capture from anywhere else has none, and the annotations then go to the chat unthreaded)."""
    for c in p.get("captures") or []:
        mid = str(c.get("platform_message_id") or "")
        if mid.isdigit():
            return mid
    return None


def reply_annotations(cfg: Config, now: datetime, log: list[str]) -> None:
    """Send each new annotation to the chat as a reply to the message it annotates (as a message of its own where the
    platform has no replies, or the prompt was made on another platform); exactly once."""
    if not cfg.telegram.get("annotation_replies", True):
        return
    chat = None
    for p in ledger.snapshot(cfg.ledger_path)["prompts"]:
        reply = p.get("annotation_reply") or {}
        if not p.get("annotated_at") or reply.get("message_id") or reply.get("attempts", 0) >= REPLY_ATTEMPTS:
            continue
        path = Path(p.get("entry_path") or "")
        text = entries.annotations(path.read_text(encoding="utf-8")) if path.is_file() else ""
        error, mid = None, None
        if not text:
            error = "no annotations in the entry"
        else:
            try:
                chat = chat or cfg.channel()
                here = channel.same_chat(cfg, p)  # (a prompt made before the channel moved: no reply into the old chat)
                mid = chat.send((p.get("chat_id") or cfg.chat_id) if here else cfg.chat_id,
                                compose.annotation_html(p["kind"], text, cfg.names),
                                reply_to=_reply_target(p) if here else None)
            except (ChannelError, ValueError) as e:  # one prompt's bad reply never stops the others'
                error = str(e)
        with ledger.locked(cfg.ledger_path) as data:
            q = ledger.find(data, p["id"])
            if q is None:
                continue
            r = q.setdefault("annotation_reply", {})
            if mid:
                r.update(message_id=mid, sent_at=now.isoformat(), reply_to=_reply_target(q))
                r.pop("error", None)
            else:
                r["attempts"] = r.get("attempts", 0) + 1
                r["error"] = (error or "")[:300]
        if mid:
            log.append(f"replied to {p['id']} with its annotations")
            what = (f"{cfg.names.companion}'s annotations on {Path(p.get('entry_path') or p['id']).stem}, sent at "
                    f"{mirror.stamp(cfg, now)} as a reply to {cfg.names.possessive} entry")
            mirror.to_chat(cfg, what, text, log, ref=("prompts", p["id"], "annotation_reply"))
        else:
            log.append(f"{p['id']}: annotation reply failed ({error})")


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


def prune_sent(cfg: Config, cutoff: str) -> None:
    """Drop ``sent.jsonl`` lines sent before ``cutoff`` (ISO); the file is rewritten only when something goes."""
    path = cfg.state_dir / "sent.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        keep = []
        for line in lines:
            try:
                when = json.loads(line).get("sent_at") or ""
            except (json.JSONDecodeError, AttributeError):
                when = ""
            if not when or when >= cutoff:  # no readable date: keep rather than lose it by accident
                keep.append(line)
        if len(keep) == len(lines):
            return
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sent-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(l + "\n" for l in keep))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass  # engine-private memory; the ledger is what matters


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


def run_backfill(cfg: Config, since, write: bool) -> tuple[int, list[str]]:
    """``journal backfill-conversations``: the conversation writer over past messages (a dry run unless
    ``write``), waiting for a running tick so the two never write the same entry at once. A dry run only
    reads: no lock, nothing created (not even the state dir)."""
    if not write:
        return 0, conversations.backfill(cfg, since, False)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "tick.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        return 0, conversations.backfill(cfg, since, write)


def run_sweep(cfg: Config) -> tuple[int, list[str]]:
    """``journal sweep``: one reconciliation pass now, waiting for a running tick."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "tick.lock", "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
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


def run(cfg: Config, only: str | None = None, dry_run: bool = False,
        hold_off_record: bool = False) -> tuple[int, list[str]]:
    """One tick (or a manual send of ``only``). Returns (exit code, log lines).

    A scheduled tick skips this round when another tick holds the lock; a manual send waits for it. With
    ``hold_off_record`` a manual send is not made while a bare ``/x`` window is open (``_held_off_record``).
    """
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "tick.lock", "a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX if only else fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0, []
        now = datetime.now(timezone.utc)
        now_local = now.astimezone(cfg.tz)
        log: list[str] = []
        errors: list[str] = []
        alerts = broken = 0
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
        for name, step in steps:
            try:
                step()
            except Alert as e:
                alerts += 1
                log.append(f"ALERT: {e}")
                errors.append(str(e))
            except Exception as e:  # one failing step must not stop the others
                broken += 1
                log.append(f"error: {name}: {type(e).__name__}: {e}")
                errors.append(f"{name}: {type(e).__name__}: {e}")
        errors += [l for l in log if "failed" in l and l not in errors]
        # Exit 1 (so the scheduler's failure delivery fires) for an alert or a step that crashed.
        # Retryable soft failures ("fold failed", a reply that will be retried) stay exit 0.
        code = 1 if alerts or broken else 0
        if not dry_run:
            _record_run(cfg, now, f"send {only}" if only else "tick", log, errors, code)
        return code, log
