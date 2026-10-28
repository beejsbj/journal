"""`journal status` and `journal doctor`.

status: last tick, rituals due now and next, open prompts, pending annotations and replies,
recent reflections, the last sweep, notebook size, recent errors. Reads state only.

doctor: configuration valid, journal root and state dir writable, the practices directory and what the
practice library made of it, bot token obtainable (never
printed), agent command present, reflections, the sweep's database, audio transcription and ffprobe,
context sources reachable, and under Hermes the plugin, stub and
cron job. Static by default; ``deep`` also runs each enabled context source once and reads the newest chat rows
back through the transcript (``transcript_sample``: counts by kind, never words).
"""
from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config as jconfig
from . import audio, capture, entries, hookstate, ledger, library, notebook, outage, schedule, sources, telegram
from .config import Config


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _annotations_pending(path: str | None) -> bool:
    """Read the entry itself: entries annotated before `annotated_at` existed are not pending."""
    try:
        return bool(path) and entries.annotations_pending(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return False


def next_occurrences(cfg: Config, now_local: datetime) -> dict[str, str]:
    """id → the next time each scheduled practice goes out (its soonest slot), in library order."""
    out = {}
    for p in library.scheduled(cfg):
        soonest = None
        for slot in p.schedule:
            hh, mm = (int(x) for x in slot.time.split(":"))
            for d in range(0, 8):
                at = (now_local + timedelta(days=d)).replace(hour=hh, minute=mm, second=0, microsecond=0)
                if (slot.weekday is None or at.weekday() == slot.weekday) and at > now_local:
                    soonest = at if soonest is None or at < soonest else soonest
                    break
        if soonest is not None:
            out[p.id] = soonest.strftime("%a %d %b %H:%M %Z")
    return out


def status(cfg: Config, now: datetime | None = None, *, web: bool = False) -> dict:
    """What the journal is doing. ``web``: for ``journal serve``, leave out everything read from the chat side
    (the privacy pass, ``conversations.json``, the mirror queue, the notebook, the hook's records), which no web
    route may read (API.md Decision 2)."""
    now = now or datetime.now(timezone.utc)
    now_local = now.astimezone(cfg.tz)
    data = ledger.snapshot(cfg.ledger_path)
    prompts = data["prompts"]
    sent_ids = {p["id"] for p in prompts if p.get("message_id")}
    visible, pending_ann, pending_reply, failed = [], [], [], []
    for p in prompts:
        if p.get("message_id") and not p.get("collapsed_at") and p.get("sent_at") \
                and now - datetime.fromisoformat(p["sent_at"]) < capture.window(cfg, p):
            visible.append({"id": p["id"], "sent_at_local": p.get("sent_at_local"),
                            "answered": bool(p.get("captures"))})
        if p.get("captures") and not p.get("annotated_at") and p.get("annotate_attempts", 0) < 3 \
                and _annotations_pending(p.get("entry_path")):
            pending_ann.append(p["id"])
        r = p.get("annotation_reply") or {}
        if p.get("annotated_at") and not r.get("message_id") and cfg.telegram.get("annotation_replies", True):
            pending_reply.append({"id": p["id"], "attempts": r.get("attempts", 0), "error": r.get("error")})
        if p.get("send_attempts") and not p.get("message_id"):
            failed.append({"id": p["id"], "attempts": p["send_attempts"], "error": p.get("send_error")})
    waiting = []  # work parked by an unreachable model or speech-to-text backend (see outage.py)
    for p in prompts:
        for what, rec in [("annotation", p)] + [(f"transcript {c.get('audio')}", c) for c in p.get("captures") or []]:
            if outage.deferred(rec) and not (what == "annotation" and p.get("annotated_at")) \
                    and not (what != "annotation" and rec.get("transcript") != "pending"):
                waiting.append({"id": p["id"], "what": what, "detail": outage.describe(rec, cfg.tz)})
    waiting += [{"id": r["id"], "what": "reflection", "detail": outage.describe(r, cfg.tz)}
                for r in data.get("reflections") or [] if outage.deferred(r) and not r.get("written_at")]
    errors_path = cfg.state_dir / "errors.jsonl"
    errors = []
    if errors_path.is_file():
        for line in errors_path.read_text(encoding="utf-8").splitlines()[-5:]:
            try:
                errors.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    out = {
        "now_local": now_local.isoformat(timespec="minutes"),
        "timezone": cfg.timezone, "location": cfg.location,
        "root": str(cfg.root), "state_dir": str(cfg.state_dir),
        "config_file": str(cfg.config_file) if cfg.config_file else None,
        "hermes_home": str(cfg.hermes_home) if cfg.hermes_home else None,
        "last_tick": _read_json(cfg.state_dir / "last-tick.json"),
        "due_now": [k for k, _ in schedule.due(cfg, now_local, sent_ids)],
        "next": next_occurrences(cfg, now_local),
        "open_prompts": visible,
        "pending_annotations": pending_ann,
        "pending_annotation_replies": pending_reply,
        "deferred": waiting,
        "undelivered": failed,
        "reflections": _reflections(cfg, data),
        "sweep": _read_json(cfg.state_dir / "sweep.json"),
        "capture_mode": jconfig.capture_mode(cfg),
        "conversations": None if web else _conversation_status(cfg),
        "mirror_queued": None if web else len(_read_json(cfg.state_dir / "mirror-queue.json") or []),
        "catch_up": next(({k: r.get(k) for k in ("id", "status", "sent_at", "noted_at", "skipped_at", "note_reason",
                                                 "deferred_since", "attempts")}
                          for r in reversed(data.get("recovery") or [])), None),
        "notebook_open": None if web else len(notebook.open_items(cfg)),
        "hook_state": None if web else _hook_state(cfg),
        "today": _today_status(cfg, now),
        "programs": _program_runs(cfg, now),
        "recent_errors": errors,
        "warnings": cfg.warnings,
    }
    if web:
        for key in ("conversations", "mirror_queued", "notebook_open", "hook_state"):
            out.pop(key)
    return out


def _program_runs(cfg: Config, now: datetime) -> list[dict]:
    """The program runs in progress, one line each (``programs.describe``); [] when none or unreadable."""
    try:
        from . import programs
        return programs.status(cfg, now)["runs"]
    except Exception:  # noqa: BLE001 - status only
        return []


def _today_status(cfg: Config, now: datetime) -> dict | None:
    """The today block's switch, injections today and the age of the tick's cache; None if it can't be read."""
    from . import today
    try:
        cache = today.read_cache(cfg)
        try:
            age = round((now - datetime.fromisoformat(cache["at"])).total_seconds() / 60)
        except (KeyError, TypeError, ValueError):
            age = None
        return {"enabled": today.enabled(cfg), **today.injections(cfg, now), "cache_age_minutes": age}
    except Exception:  # noqa: BLE001 - status only
        return None


def _hook_state(cfg: Config) -> dict | None:
    try:
        return hookstate.summary(cfg)
    except Exception:  # noqa: BLE001 - status only
        return None


def _reflections(cfg: Config, data: dict) -> dict:
    recs = sorted(data.get("reflections") or [], key=lambda r: r.get("local_date") or "")[-4:]
    rows = []
    for r in recs:
        tg = r.get("telegram") or {}
        rows.append({"id": r.get("id"), "written": bool(r.get("written_at")), "trigger": r.get("trigger"),
                     "attempts": r.get("attempts", 0), "error": r.get("error"),
                     "sent": bool(tg.get("message_id")), "send_error": tg.get("error")})
    on = {k: (cfg.reflections.get(k) or {}).get("enabled", True) is not False for k in ("day", "week")}
    return {"day_enabled": on["day"], "week_enabled": on["week"], "recent": rows}


def _reflection_line(s: dict) -> str | None:
    r = s.get("reflections") or {}
    if not r:
        return None
    if not r.get("day_enabled") and not r.get("week_enabled"):
        return "Reflections: off"
    items = []
    for x in r.get("recent") or []:
        if x["written"]:
            state = "sent" if x["sent"] else f"written, not sent{': ' + x['send_error'] if x.get('send_error') else ''}"
        else:
            state = f"{x['attempts']} failed attempt(s){': ' + x['error'] if x.get('error') else ''}"
        items.append(f"{x['id']} ({state})")
    return "Reflections: " + (", ".join(items) or "none yet")


def _num(value) -> float | None:
    try:
        return float(value)
    except OverflowError:  # an integer too big for a float: far above any real value
        return float("inf") if isinstance(value, int) and value > 0 else None
    except (TypeError, ValueError):
        return None


def _conversation_status(cfg: Config) -> dict | None:
    from . import conversations
    if not conversations.enabled(cfg):
        return None
    st = conversations.snapshot(cfg)
    if not st:
        return None
    cur = st.get("cursor") or {}
    return {"written": len(st.get("written") or {}), "replies": len(st.get("her") or {}),
            "last_id": cur.get("last_id"), "open": bool(cur.get("thread")), "off_now": _off_now(cfg)}


def _off_now(cfg: Config) -> bool:
    from . import privacy
    try:
        return privacy.window_open(cfg, datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 - status only
        return False


CAPTURE_MODE_SAYS = {  # what each ``capture.mode`` journals, as status and doctor say it
    "everything": "every chat thread is journaled, as part of an entry or a note of its own; /x keeps a message out",
    "arm": "prompt replies, /j notes, program answers and the conversation after them are journaled; "
           "ordinary chat is not",
}


def format_status(s: dict) -> str:
    lines = [f"Journal status — {s['now_local']} ({s['timezone']}{', ' + s['location'] if s['location'] else ''})",
             f"  journal: {s['root']}", f"  state:   {s['state_dir']}"]
    if s.get("config_file"):
        lines.append(f"  config:  {s['config_file']}")
    if s.get("hermes_home"):
        lines.append(f"  hermes:  {s['hermes_home']}")
    t = s.get("last_tick")
    if t:
        lines.append(f"Last run: {t.get('at')} ({t.get('mode')}, exit {t.get('exit')})"
                     + "".join(f"\n    {l.splitlines()[0]}" for l in t.get("lines", [])[-5:]))
    else:
        lines.append("Last run: none recorded")
    lines.append("Due now: " + (", ".join(s["due_now"]) or "nothing"))
    lines.append("Next: " + (", ".join(f"{k} {v}" for k, v in s["next"].items()) or "nothing scheduled"))
    lines.append("Open prompts: " + (", ".join(f"{p['id']}{' (answered)' if p['answered'] else ''}"
                                               for p in s["open_prompts"]) or "none"))
    if s["pending_annotations"]:
        lines.append("Awaiting annotations: " + ", ".join(s["pending_annotations"]))
    if s["pending_annotation_replies"]:
        lines.append("Annotation replies not yet sent: " + ", ".join(
            f"{r['id']} ({r['attempts']} tries{': ' + r['error'] if r.get('error') else ''})"
            for r in s["pending_annotation_replies"]))
    if s.get("deferred"):
        lines.append("Deferred by an outage (model or speech-to-text): " + ", ".join(
            f"{d['id']} {d['what']} ({d['detail']})" for d in s["deferred"]))
    if s["undelivered"]:
        lines.append("Undelivered: " + ", ".join(f"{u['id']} ({u['attempts']} tries: {u['error']})"
                                                 for u in s["undelivered"]))
    line = _reflection_line(s)
    if line:
        lines.append(line)
    if s.get("sweep"):
        lines.append(f"Sweep: last {str(s['sweep'].get('at', ''))[:16]}, up to row {s['sweep'].get('last_id')}")
    if s.get("capture_mode"):
        lines.append(f"Capture mode: {s['capture_mode']} ({CAPTURE_MODE_SAYS[s['capture_mode']]})")
    c = s.get("conversations")
    if c:
        lines.append(f"Conversations: {c['written']} message(s) and {c['replies']} repl(ies) saved"
                     + (f", up to row {c['last_id']}" if c.get("last_id") is not None else "")
                     + (" · a thread is open" if c.get("open") else "")
                     + (" · off the record now" if c.get("off_now") else ""))
    if s.get("mirror_queued"):
        lines.append(f"Mirror: {s['mirror_queued']} send(s) waiting to go into the chat session")
    c = s.get("catch_up")
    if c:
        when = c.get("sent_at") or c.get("noted_at") or c.get("skipped_at") or c.get("deferred_since") or ""
        why = f": {c['note_reason']}" if c.get("note_reason") else ""
        lines.append(f"Outage catch-up: {c['id']} {c['status']} {str(when)[:16]}{why}")
    for r in s.get("programs") or []:
        lines.append(f"Program: {r['text']} (run {r['id']})")
    lines.append(f"Notebook: {s['notebook_open']} open item(s)")
    h = s.get("hook_state") or {}
    if (h.get("pending_voice") or h.get("pending_packs") or h.get("pending_programs") or h.get("off_turns")
            or h.get("readable") is False):
        lines.append(f"Hook state: {h.get('pending_voice', 0)} voice note(s) awaiting transcript, "
                     f"{h.get('pending_packs', 0)} pack(s) and {h.get('pending_programs', 0)} program block(s) queued, "
                     f"{h.get('off_turns', 0)} off-record turn(s)"
                     + ("" if h.get("readable", True) else " · file unreadable"))
    t = s.get("today")
    if t:
        if not t["enabled"]:
            lines.append("Today block: off")
        else:
            lines.append(f"Today block: {t['count']} block{'' if t['count'] == 1 else 's'} today"
                         + (f", last {t['last']}" if t.get("last") else "")
                         + (f" · cache {t['cache_age_minutes']} min old" if t.get("cache_age_minutes") is not None
                            else " · no cache yet"))
    if s["recent_errors"]:
        lines.append("Recent errors:")
        lines += [f"    {e.get('at', '')[:16]} {e.get('error', '')}" for e in s["recent_errors"]]
    for w in s.get("warnings") or []:
        lines.append(f"Warning: {w}")
    return "\n".join(lines)


# ── doctor ───────────────────────────────────────────────────────────────────

def _channel_row(cfg: Config) -> tuple[str, str, str]:
    """Where the journal sends and captures (``channel``), how, and what that platform cannot do here."""
    where = f"{cfg.platform}:{cfg.chat_id}" if cfg.chat_id else f"{cfg.platform} (no chat set)"
    if cfg.owner_ids:
        where += f", from {', '.join(cfg.owner_ids)}"
    if cfg.platform == "telegram":
        return ("channel", "ok" if cfg.chat_id else "fail", f"{where}; sent through the Telegram Bot API")
    if cfg.adapter == "none":
        return ("channel", "fail", f"{where}; nothing can send there with adapter: none (only Telegram is direct)")
    return ("channel", "ok" if cfg.chat_id else "fail",
            f"{where}; sent through `hermes journal deliver` (Hermes' senders): no fold, no reply-to, no buttons")


def _writable_dir(path: Path) -> tuple[bool, str]:
    if path.is_dir():
        ok = os.access(path, os.W_OK | os.X_OK)
        return ok, str(path) if ok else f"{path} not writable"
    parent = next((p for p in path.parents if p.exists()), None)
    ok = parent is not None and os.access(parent, os.W_OK)
    return ok, (f"{path} will be created" if ok else f"{path} missing and cannot be created")


def doctor(cfg: Config, deep: bool = False, token: bool = True, web: bool = False) -> list[tuple[str, str, str]]:
    """Rows of (check, ok|warn|fail, detail). Never prints or returns a token. ``token=False``: no secret is
    resolved (the web's ``/doctor``); ``web``: no row read from the chat side (the hook's records)."""
    rows: list[tuple[str, str, str]] = [("config", "ok", "loaded" + (f" from {cfg.config_file}" if cfg.config_file else
                                                                      " (no config file)"))]
    for p in jconfig.problems(cfg):
        rows.append(("config", "fail" if "chat_id" in p else "warn", p))
    for w in cfg.warnings:
        rows.append(("config", "warn", w))
    if cfg.root.is_dir():
        ok = os.access(cfg.root, os.W_OK | os.X_OK)
        rows.append(("journal root", "ok" if ok else "fail", f"{cfg.root}" + ("" if ok else " is not writable")))
    else:
        rows.append(("journal root", "fail", f"{cfg.root} does not exist"))
    ok, detail = _writable_dir(cfg.state_dir)
    rows.append(("state dir", "ok" if ok else "fail", detail))
    if not web:
        rows.append(_hook_state_row(cfg))
    rows += _library_rows(cfg)
    rows.append(_channel_row(cfg))
    if token and cfg.platform == "telegram":
        tok, where = telegram.resolve_token(cfg)
        rows.append(("telegram token", "ok" if tok else "fail", f"obtained from {where}" if tok else where))
        del tok
    if cfg.agent.get("enabled") is False:
        rows.append(("agent", "warn", "model steps disabled: plain openers, no annotations"))
    elif cfg.agent.get("command"):
        exe = str(cfg.agent["command"][0])
        found = shutil.which(exe) is not None or Path(exe).is_file()
        rows.append(("agent", "ok" if found else "fail", exe if found else f"{exe} not found"))
    elif cfg.adapter == "none":
        rows.append(("agent", "warn", "no companion: model steps wait (set agent.command)"))
    else:
        exe = cfg.hermes_command()
        found = shutil.which(exe) is not None or Path(exe).is_file()
        rows.append(("agent", "ok" if found else "fail",
                     f"{exe} {' '.join(cfg.profile_args())}".strip() if found else f"{exe} not found"))
    agent_on = cfg.agent.get("enabled") is not False
    for which, kind in (("day", "reflection"), ("week", "week")):
        spec = cfg.reflections.get(which) or {}
        if spec.get("enabled", True) is False:
            rows.append((f"{which} {'reflection' if which == 'day' else 'review'}", "ok", "off"))
            continue
        when = spec.get("fallback_time") or "04:00"
        try:
            ends = library.closer(cfg, which)
        except Exception:  # noqa: BLE001 - the library row says why
            ends = None
        fall = f"fallback {when} the next day" if which == "day" else f"fallback {when} the day after the week"
        timing = (f"after the {ends.id} entry is annotated, {fall}" if ends else
                  f"no practice says closes: {which}, so only at the {fall}")
        rows.append((f"{which} {'reflection' if which == 'day' else 'review'}", "ok" if agent_on else "warn",
                     timing if agent_on else "on, but model steps are disabled: none will be written"))
    if cfg.sweep.get("enabled", True) is False:
        rows.append(("sweep", "warn", "off: replies sent while the companion is busy are not captured"))
    elif cfg.state_db is None:
        rows.append(("sweep", "ok", "skipped: no Hermes sessions database outside Hermes"))
    else:
        ok = Path(cfg.state_db).is_file()
        rows.append(("sweep", "ok" if ok else "warn",
                     f"reads {cfg.state_db} (read-only)" if ok else f"no sessions database at {cfg.state_db}"))
    conv = cfg.conversations
    if conv.get("enabled", True) is False:
        rows.append(("conversations", "ok", "off: only journal replies and /j notes are saved"))
    elif cfg.state_db is None:
        rows.append(("conversations", "ok", "skipped: no Hermes sessions database outside Hermes"))
    else:
        rows.append(("conversations", "ok",
                     f"saved by thread (ends after {jconfig.quiet_minutes(cfg):g} min quiet"
                     + ("" if jconfig.quiet_minutes(cfg) == _num(conv.get("quiet_minutes", 45))
                        else f"; quiet_minutes {conv.get('quiet_minutes')!r} is not usable") + "); "
                     f"{jconfig.opt_out_prefix(cfg)} is off the record; auto notes "
                     + ("annotated" if conv.get("annotate_auto") is True else "not annotated")))
    mode = jconfig.capture_mode(cfg)
    rows.append(("capture mode", "ok", f"{mode}: {CAPTURE_MODE_SAYS[mode]}"))
    st, detail = audio.backend(cfg)
    rows.append(("audio transcription", st, detail))
    probe = audio.ffprobe_path(cfg)
    rows.append(("ffprobe", "ok" if probe else "warn",
                 probe or "not found: audio headings won't carry the recording time"))
    for name, ok, detail in sources.check(cfg):
        rows.append((f"source {name}", "ok" if ok else "warn", detail))
    rows += serve_rows(cfg, token=token)
    if deep and not web:
        rows.append(transcript_sample(cfg))
    if deep:
        now_local = datetime.now(cfg.tz)
        for name, _title, fn in sources.ALL:
            enabled = any(cfg.source_enabled(s) for s in sources.READERS) if name == "reading" else cfg.source_enabled(name)
            if not enabled:
                continue
            try:
                lines = fn(cfg, now_local)
                rows.append((f"source {name} (run)", "ok", f"{len(lines)} line(s)"))
            except Exception as e:
                rows.append((f"source {name} (run)", "warn", f"{type(e).__name__}: {str(e)[:120]}"))
    return rows


SAMPLE = 50  # rows of the chat ``doctor --deep`` reads back through the transcript


def transcript_sample(cfg: Config) -> tuple[str, str, str]:
    """``doctor --deep``: the newest ``SAMPLE`` rows of the chat as the transcript reads them, counted by kind (never
    their words). Many of his rows reading as ``empty`` or ``system`` is the sign that the agent changed how it
    stores messages and the adapter no longer recognises them; none read as his words at all is a warning."""
    chat = cfg.transcript()
    if chat.source is None:
        return ("transcript sample", "ok", "no chat transcript to read (standalone)")
    try:
        sample = chat.tail(SAMPLE)
    except chat.errors as e:
        return ("transcript sample", "warn", f"could not read {chat.source} ({type(e).__name__}: {str(e)[:120]})")
    his = [r.kind for r in sample if r.role == "user"]
    hers = [r.kind for r in sample if r.role == "assistant"]
    blank = sum(1 for k in his if k in ("empty", "system"))
    words = sum(1 for k in his if k in ("his", "handled"))
    n = cfg.names
    detail = (f"newest {len(sample)} row(s): {len(his)} {n.user_possessive} ({words} {n.possessive} words or saved "
              f"replies, {his.count('mirror')} journal mirrors, {his.count('boundary')} deliveries, {blank} read as "
              f"empty or system), {len(hers)} {n.companion}'s ({hers.count('hers')} replies)")
    candidates = [k for k in his if k not in ("mirror", "boundary")]  # what could have been his message
    if candidates and not words:
        return ("transcript sample", "warn", detail + f": none of {n.user_possessive} rows read as {n.possessive} "
                                                      "words; has the agent changed how it stores messages?")
    return ("transcript sample", "ok", detail)


def serve_rows(cfg: Config, token: bool = True) -> list[tuple[str, str, str]]:
    """``journal serve``'s rows: its token (``token``: resolved, never shown), whether its bind address exists on
    this host, the web app build, and whether this code is the code the tick last ran (two installs, one pin)."""
    from .serve import auth, static
    rows = []
    if token:
        tok, where = auth.resolve_token(cfg)
        if tok:
            rows.append(("serve token", "ok", f"obtained from {where}"))
        elif auth.file_backed(cfg):
            rows.append(("serve token", "ok", f"not created yet: `journal serve` writes {auth.token_file(cfg)} "
                                              "at its first start"))
        else:
            rows.append(("serve token", "warn", where))
        del tok
    rows.append(_bind_row(cfg))
    rows += _socket_rows(cfg)
    if static.built(cfg):
        info = static.build_info(cfg)
        detail = f"web app in {static.assets_dir(cfg)}" + (f" (built from web/ {info['source'][:12]})" if info else "")
        web = None if cfg.serve.get("assets_dir") else static.checkout_web()
        if info and web and static.web_source_hash(web) != info["source"]:  # a checkout: web/ moved on, static/ did not
            rows.append(("serve assets", "warn", detail + "; web/ has changed since this build: rebuild it (the web "
                                                  "workflow) and commit static/"))
        else:
            rows.append(("serve assets", "ok", detail))
    elif cfg.serve.get("assets_dir"):
        rows.append(("serve assets", "warn", f"serve.assets_dir {static.assets_dir(cfg)} has no index.html: the "
                                             "placeholder page is served"))
    else:
        rows.append(("serve assets", "ok", "the web app is not built into this install yet: the placeholder page "
                                           "(pairing only) is served"))
    rows.append(version_row(cfg))
    return rows


def _bind_row(cfg: Config) -> tuple[str, str, str]:
    import socket
    bind, port = cfg.serve.get("bind"), jconfig.serve_port(cfg)
    if not isinstance(bind, str) or port is None:
        return ("serve bind", "warn", "serve.bind or serve.port is unusable (see the config rows)")
    try:
        family = socket.AF_INET6 if ":" in bind else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.bind((bind, 0))  # the address only: the port may be in use by a running journal serve
    except OSError as e:
        return ("serve bind", "warn", f"{bind} is not an address of this host yet ({e.strerror}): journal serve "
                                      "retries for 60 s, then exits for systemd to restart it")
    return ("serve bind", "ok", f"{bind}:{port}")


def _socket_rows(cfg: Config) -> list[tuple[str, str, str]]:
    """``serve socket`` and ``serve identity``, only when ``serve.socket`` or ``serve.tailscale_users`` is set: the
    socket's path (a file in the way is a failure: the server would not start), and whose Tailscale login the
    socket signs in."""
    import stat
    raw_sock, raw_users = cfg.serve.get("socket"), cfg.serve.get("tailscale_users")
    if raw_sock is None and not raw_users:
        return []
    rows = []
    sock = jconfig.serve_socket(cfg)
    users = sorted(jconfig.serve_tailscale_users(cfg))
    if raw_sock is not None:
        if sock is None:
            rows.append(("serve socket", "warn", "serve.socket is unusable (see the config rows): journal serve will "
                                                 "not start"))
        else:
            try:
                st = os.lstat(sock)
            except FileNotFoundError:
                rows.append(("serve socket", "ok", f"unix:{sock} (made 0600 by journal serve at start)"))
            except OSError as e:
                rows.append(("serve socket", "warn", f"unix:{sock} cannot be read ({e.strerror})"))
            else:
                if not stat.S_ISSOCK(st.st_mode):
                    rows.append(("serve socket", "fail", f"{sock} exists and is not a socket: journal serve will not "
                                                         "remove it, so it cannot listen there"))
                elif stat.S_IMODE(st.st_mode) & 0o077 or st.st_uid != os.getuid():
                    rows.append(("serve socket", "warn", f"unix:{sock} is mode {stat.S_IMODE(st.st_mode):o}, owner "
                                                         f"{st.st_uid}: it should be 0600 and this user's"))
                else:
                    rows.append(("serve socket", "ok", f"unix:{sock} (0600)"))
    if sock is None:
        if users:
            rows.append(("serve identity", "warn", "serve.tailscale_users is set but serve.socket is not: the "
                                                   "Tailscale header is believed only on the socket, so it signs no "
                                                   "one in"))
    elif users:
        rows.append(("serve identity", "ok", f"Tailscale logins signed in on the socket: {', '.join(users)}"))
    else:
        rows.append(("serve identity", "ok", "no serve.tailscale_users: the socket takes the token or a paired "
                                             "cookie only"))
    return rows


def version_row(cfg: Config) -> tuple[str, str, str]:
    """``same version as tick``: this process's code against the fingerprint the last tick recorded."""
    from . import version
    last = _read_json(cfg.state_dir / "last-tick.json")
    mine = version.stamp()
    if not isinstance(last, dict):
        return ("same version as tick", "ok", f"no tick has run yet (this is {mine['version']} {mine['code']})")
    if not last.get("code"):
        return ("same version as tick", "warn", f"the last tick ({last.get('at', '?')}) predates version stamps; "
                                                "the next tick records its own")
    if (last.get("version"), last.get("code")) == (mine["version"], mine["code"]):
        return ("same version as tick", "ok", f"{mine['version']} {mine['code']}")
    return ("same version as tick", "fail", f"this is {mine['version']} {mine['code']}, the tick last ran "
                                            f"{last.get('version')} {last.get('code')}: pin both installs to one "
                                            "commit (DEPLOY.md)")


def _hook_state_row(cfg: Config) -> tuple[str, str, str]:
    """What the gateway hook keeps between callbacks (voice notes awaiting STT, queued packs, off-record
    turns) lives on disk so a restart keeps it; a file that exists but can't be read means it was lost once."""
    try:
        h = hookstate.summary(cfg)
    except Exception as e:  # noqa: BLE001 - doctor reports, never raises
        return ("hook state", "warn", f"{hookstate.path(cfg)}: {type(e).__name__}: {str(e)[:120]}")
    if not h["readable"]:
        return ("hook state", "warn", f"{h['path']} exists but is not readable JSON: its records are lost and the "
                                      "file is rewritten on the next hook write")
    counts = (f"{h['pending_voice']} voice note(s) awaiting transcript, {h['pending_packs']} pack(s) and "
              f"{h.get('pending_programs', 0)} program block(s) queued, "
              f"{h['off_turns']} off-record turn(s)")
    return ("hook state", "ok", f"{h['path']} ({counts})" if h["exists"] else f"{h['path']} not written yet ({counts})")


def _library_rows(cfg: Config) -> list[tuple[str, str, str]]:
    """Where his practice files live and what the library made of them. A broken user file is a warning, not a
    failure: the built-in of the same id is still sent. Unreadable package data fails (an install fault)."""
    from . import library
    directory = Path(cfg.practices_dir)
    try:
        lib = library.load(cfg)
    except Exception as e:  # noqa: BLE001 - doctor reports, never raises
        lib, failure = None, f"{type(e).__name__}: {e}" if not isinstance(e, RuntimeError) else str(e)
    users = [p for p in lib.practices.values() if not p.builtin] if lib else []
    count = (f"{len(users)} user practice{'s' if len(users) != 1 else ''}" if users else "no user practices yet")
    if cfg.origins.get("practices_dir") == "env":
        count += f"; from {jconfig.ENV_KEYS['practices_dir']}"
    if directory.is_dir():
        ok = os.access(directory, os.W_OK | os.X_OK)
        rows = [("practices dir", "ok" if ok else "warn",
                 f"{directory} ({count})" if ok else f"{directory} ({count}) is not writable: fork cannot save here")]
    else:
        ok, detail = _writable_dir(directory)
        rows = [("practices dir", "ok" if ok else "warn", f"{detail} ({count})" if ok else detail)]
    if lib is None:
        return rows + [("library", "fail", failure)]

    def label(p) -> str:
        tags = {"builtin": [], "user": ["user"], "fork": ["user", "overrides the built-in"]}[lib.source(p)]
        tags += [] if p.enabled else ["disabled"]
        return f"{p.id} ({', '.join(tags)})" if tags else p.id
    builtin = len(lib.practices) - len(users)
    split = f"{builtin} built-in" + (f", {len(users)} user" if users else "")
    rows.append(("library", "ok", f"{len(lib.practices)} practices ({split}): "
                                  + ", ".join(label(p) for p in lib.practices.values())))
    if lib.programs:
        rows.append(("programs", "ok", ", ".join(
            f"{p.id} ({'situational' if p.situational else f'{len(p.sessions)} sessions'}"
            f"{'' if p.enabled else ', disabled'})" for p in lib.programs.values())))
    rows += [("library", "warn", msg) for msg in lib.problems]
    return rows


def format_doctor(rows: list[tuple[str, str, str]]) -> str:
    mark = {"ok": "✓", "warn": "!", "fail": "✗"}
    return "\n".join(f"{mark.get(s, '?')} {name}: {detail}" for name, s, detail in rows)


def exit_code(rows: list[tuple[str, str, str]]) -> int:
    return 1 if any(s == "fail" for _, s, _ in rows) else 0
