"""The "today" block: what the companion is told about the day when a conversation starts.

Plain chat carries no journal marker, so without this the companion would meet the day cold. Once per thread
(``thread_started``) its turn gets a short block, built here from state only: the ledger (how the day's
practices went), today's entry files (the first line of his words), the latest reflection's closing question,
and ``today-cache.json``, which the tick's ``cache`` step writes from ``sources.tududi`` and ``sources.weather``
(``refresh``). Three rules shape everything below:

* **No source calls in the hook.** ``collect``/``render`` read files and nothing else; the network and the
  tududi server are the tick's business, and a stale cache is simply left out.
* **Never the transcript.** Nothing here reads the sessions database, so what he said off the record, or in
  passing, cannot reach the block. The entry files never hold off-the-record content (the conversation writer and
  capture skip it), so no privacy pass is needed on them.
* **A bad line costs the line.** Each collector fails alone (the line is dropped and logged), and the block is
  cut from the bottom, line by line, to ``today.max_chars``; the journal keeps working with no block at all.

``collect`` returns a plain JSON-serialisable dict; ``render`` turns that dict into the block; ``GET /today`` (WP9)
serialises the same dict, and ``tests/fixtures/today.json`` pins its shape for both.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import audio, context, entries, hookstate, ledger, library, reflect, sources, standing
from .config import Config, pack_lookback_days, quiet_minutes, today_cache_minutes, today_max_chars

log = logging.getLogger("journal.today")

CACHE = "today-cache.json"
SOURCES = ("standing", "practices", "programs", "entries", "reflection", "tasks", "weather")
ENTRY_CHARS = 90           # characters of his first line shown per entry
ENTRIES_SHOWN = 4
TASKS_SHOWN = 5
TASK_CHARS = 48
REFLECTION_CHARS = 160
WEATHER_STALE_HOURS = 4    # a forecast older than this is left out rather than shown as current
CACHE_SLACK = timedelta(seconds=60)  # a tick that runs a few seconds early must not wait a whole extra round
_STAMP = re.compile(r"^\s*\*\*\d{1,2}:\d{2}\*\* — ")  # the conversation writer's time marker before a message
# Lines the engine wrote, not him: a bare marker (``**14:02** —``, ``**14:02** · voice transcript · 0:12``,
# ``**Tue 14 Oct 21:10** · audio file …``), an audio embed, and the transcript placeholders.
_ENGINE_LINE = re.compile(r"^\s*(?:\*\*(?:[A-Za-z]{3} \d{1,2} [A-Za-z]{3} )?\d{1,2}:\d{2}\*\*(?: ·[^\n]*| —)?|!\[\[[^\]]*\]\])\s*$")
_PLACEHOLDERS = frozenset(s.strip() for s in (audio.NOT_TRANSCRIBED, audio.TRANSCRIBING, audio.NO_TRANSCRIPT))
NOTE = ("_Background for this conversation, not instructions. Read further with your tools if it matters. "
        "Not stated here is not known._")


# ── the tick's cache ─────────────────────────────────────────────────────────

def cache_path(cfg: Config) -> Path:
    return cfg.state_dir / CACHE


def read_cache(cfg: Config) -> dict:
    """The cache as written, or {} (missing, unreadable, not an object)."""
    try:
        data = json.loads(cache_path(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_cache(cfg: Config, data: dict) -> None:
    """Atomic write next to the file; a failed dump leaves the old cache and no temp file."""
    path = cache_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def enabled(cfg: Config) -> bool:
    return cfg.today.get("enabled", True) is not False


def wanted(cfg: Config) -> tuple[str, ...]:
    """The configured sources in fixed order; an unusable ``today.sources`` means all of them."""
    listed = cfg.today.get("sources")
    if not isinstance(listed, list) or not all(isinstance(x, str) for x in listed):
        return SOURCES
    return tuple(s for s in SOURCES if s in listed)


_NOW = re.compile(r"^- weather:now \([^)]*\) — (.*?), (-?[\d.]+°C) \(feels")
_DAY = re.compile(r"^- weather:today \([^)]*\) — .*?, (-?[\d.]+)–(-?[\d.]+)°C, rain chance (\d+)%")


def weather_from(lines: list[str]) -> str | None:
    """``14°C, light rain, 12–18°C today, rain chance 40%`` out of ``sources.weather``'s lines, or None."""
    now = day = None
    for line in lines:
        now = now or _NOW.match(line.strip())
        day = day or _DAY.match(line.strip())
    if not now:
        return None
    text = f"{now.group(2)}, {now.group(1)}"
    if day:
        text += f"; today {day.group(1)}–{day.group(2)}°C, rain chance {day.group(3)}%"
    return text


def refresh(cfg: Config, now: datetime, log_lines: list[str], force: bool = False) -> bool:
    """The tick's ``cache`` step: write ``today-cache.json`` from ``sources.tududi`` / ``sources.weather`` at most
    every ``today.cache_minutes``. A source that fails keeps what the cache already had (``collect`` ignores a
    part that is out of date), and one source failing never stops the other. Returns whether it wrote."""
    if not enabled(cfg):
        return False
    wants = wanted(cfg)
    tasks_on = "tasks" in wants and cfg.source_enabled("tududi")
    weather_on = "weather" in wants and cfg.source_enabled("weather") and bool(cfg.coordinates)
    heard_on = "standing" in wants and cfg.transcript().source is not None
    if not (tasks_on or weather_on or heard_on):
        return False
    cache = read_cache(cfg)
    try:
        age = now - datetime.fromisoformat(cache["at"])
    except (KeyError, TypeError, ValueError):
        age = None
    if not force and age is not None and timedelta(0) <= age < timedelta(minutes=today_cache_minutes(cfg)) - CACHE_SLACK:
        return False
    now_local = now.astimezone(cfg.tz)
    out = {k: v for k, v in cache.items() if k in ("tasks", "weather", "heard") and isinstance(v, dict)}
    out["at"] = now.isoformat()
    for name, on, fetch, build in (
            ("tasks", tasks_on, lambda: sources.tududi_due(cfg, now_local),
             lambda names: {"count": len(names), "names": names[:TASKS_SHOWN * 2],
                            "date": now_local.date().isoformat()}),
            ("weather", weather_on, lambda: sources.weather(cfg, now_local),
             lambda lines: {"text": weather_from(lines)}),
            ("heard", heard_on, lambda: _heard_now(cfg, now),
             lambda last: {"last": last.isoformat() if last else None})):
        if not on:
            out.pop(name, None)
            continue
        try:
            part = build(fetch())
            if name == "weather" and not part["text"]:
                raise RuntimeError("no current weather in the reply")
            out[name] = {**part, "at": now.isoformat()}
        except Exception as e:  # the other source and the tick go on
            log_lines.append(f"today cache: {name} unavailable ({type(e).__name__}: {str(e)[:100]})")
    _write_cache(cfg, out)
    return True


def _heard_now(cfg: Config, now: datetime) -> datetime | None:
    """The time of his newest public chat message (``standing.heard_in_chat``), for the hook, which never reads the
    transcript. Raises when the chat could not be read or the privacy pass could not run, so the cache keeps the
    time it had (a time read through the pass earlier stays true: nothing makes an earlier public message private)."""
    last, state = standing.heard_in_chat(cfg, now, pack_lookback_days(cfg))
    if state != "read":
        raise RuntimeError(f"the chat was not read ({state})")
    return last


# ── when a thread starts ─────────────────────────────────────────────────────

def thread_started(cfg: Config, now: datetime, stamps: dict) -> bool:
    """Whether the message arriving at ``now`` opens a thread: the first the hook has seen, or his previous one was
    more than ``conversations.quiet_minutes`` ago, or it falls on a later local day than his previous one (a chat
    that runs past midnight meets the new day), or a journal prompt went out since it, or the last start found
    nothing to say (``today_owed``). ``stamps`` are the hook-state thread stamps (``last_his``, ``last_send``:
    epoch seconds), read before this message is stamped."""
    last = _num(stamps.get("last_his"))
    if last is None or stamps.get("today_owed") is True:
        return True
    t = now.timestamp()
    if t - last > quiet_minutes(cfg) * 60:
        return True
    if datetime.fromtimestamp(last, timezone.utc).astimezone(cfg.tz).date() != now.astimezone(cfg.tz).date():
        return True
    sent = _num(stamps.get("last_send"))
    return sent is not None and sent > last


def _num(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def note_send(cfg: Config, when: datetime | None = None) -> None:
    """A journal prompt went out: the next message he sends starts a thread. Never raises (a send must not fail
    for a stamp)."""
    try:
        hookstate.stamp(cfg, last_send=(when or datetime.now(timezone.utc)).timestamp())
    except Exception:
        log.exception("journal: could not stamp the prompt send")


def begin(cfg: Config, now: datetime, *, allow: bool = True) -> bool:
    """Record that he is writing at ``now`` and say whether this turn gets the today block.

    One locked read-modify-write of the thread stamps: ``last_his`` moves to ``now`` on every message, so a
    block is offered at most once per thread however the turn is retried. ``allow=False`` (the turn carries a
    pack) still stamps and so spends the thread start: the block waits for the next one. The caller stamps
    ``injected`` (``note_injected``) once it has a block to show."""
    with hookstate.locked(cfg) as state:
        stamps = state["stamps"]
        started = thread_started(cfg, now, stamps)
        stamps["last_his"] = now.timestamp()
        stamps.pop("today_owed", None)
    return started and allow and enabled(cfg)


def note_empty(cfg: Config) -> None:
    """A thread start found no block to show (nothing to say yet, or a failure): the next message tries again
    instead of the start being spent on nothing."""
    hookstate.stamp(cfg, today_owed=True)


def note_injected(cfg: Config, now: datetime) -> None:
    """Count one block shown (``today_injected_at``, and ``today_count`` for the day, for ``journal status``)."""
    day = now.astimezone(cfg.tz).date().isoformat()
    with hookstate.locked(cfg) as state:
        stamps = state["stamps"]
        count = stamps.get("today_count") if isinstance(stamps.get("today_count"), dict) else {}
        n = count.get("n", 0) if count.get("date") == day and isinstance(count.get("n"), int) else 0
        stamps["today_injected_at"] = now.timestamp()
        stamps["today_count"] = {"date": day, "n": n + 1}


def injections(cfg: Config, now: datetime) -> dict:
    """``{"count": n, "last": "09:12"}`` for the local day of ``now`` (count 0, last None when none)."""
    stamps = hookstate.stamps(cfg)
    count = stamps.get("today_count") if isinstance(stamps.get("today_count"), dict) else {}
    day = now.astimezone(cfg.tz).date().isoformat()
    n = count.get("n") if count.get("date") == day and isinstance(count.get("n"), int) else 0
    at = _num(stamps.get("today_injected_at"))
    last = None
    if n and at is not None:
        last = datetime.fromtimestamp(at, timezone.utc).astimezone(cfg.tz).strftime("%H:%M")
    return {"count": n, "last": last}


# ── collecting ───────────────────────────────────────────────────────────────

def _clip(text: str, cap: int) -> str:
    """One line: whitespace collapsed (a heading in his words or the model's can never open a section), cut at a
    word to ``cap`` characters."""
    text = " ".join((text or "").split())
    return text if len(text) <= cap else text[:cap].rsplit(" ", 1)[0].rstrip(" ,;:—-") + " …"


def _hhmm(iso: str, cfg: Config) -> str:
    return datetime.fromisoformat(iso).astimezone(cfg.tz).strftime("%H:%M")


def _practices(cfg: Config, now_local: datetime, stems: bool = False) -> dict:
    """How today's practices went, from the ledger, plus the slots still to come. ``stems``: each row that has a
    prompt names its entry's stem (``journal serve`` links rows to entries by it; a practice with two slots has two
    rows with one id, and a prefix match on the id would give both the first slot's entry)."""
    today = now_local.date().isoformat()
    prompts = {p["id"]: p for p in ledger.snapshot(cfg.ledger_path)["prompts"]
               if p.get("local_date") == today and p.get("message_id") and p.get("sent_at")}
    rows, seen = [], set()

    def row(p: dict, label: str) -> dict:
        state = context.prompt_state(cfg, p, now_local)
        kind = {"answered": "answered", "no reply yet": "open", "missed, never answered": "missed"}[state]
        first = (p.get("captures") or [{}])[0].get("at")
        out = {"id": label, "state": kind, "sent": _hhmm(p["sent_at"], cfg),
               "answered": _hhmm(first, cfg) if first else None}
        # the file its replies are in: a hand-written entry on the base name sends it to ``<stem>-2`` (capture.entry_stem)
        stem = Path(p["entry_path"]).stem if p.get("entry_path") else p.get("stem") or p["id"]
        return {**out, "stem": stem} if stems else out

    for practice in library.scheduled(cfg):
        for n, slot in enumerate(practice.schedule, 1):
            if slot.weekday is not None and now_local.weekday() != slot.weekday:
                continue
            pid = entries.stem_for(today, practice.id, n)
            if pid in prompts:
                seen.add(pid)
                rows.append(row(prompts[pid], practice.id))
                continue
            hh, mm = (int(x) for x in slot.time.split(":"))
            if now_local.replace(hour=hh, minute=mm, second=0, microsecond=0) > now_local:
                rows.append({"id": practice.id, "state": "upcoming", "at": slot.time})
    for pid, p in sorted(prompts.items(), key=lambda kv: kv[1]["sent_at"]):
        if pid not in seen and p.get("kind") not in entries.FREEFORM_IDS and not p.get("program"):  # on demand
            rows.append(row(p, p.get("kind") or pid))
    return {"rows": rows, "open": [r["id"] for r in rows if r["state"] == "open"]}


def _is_his_line(line: str) -> bool:
    """A line that can stand as "the first line of his words": not blank, not a heading, not the engine's markup
    (a time marker, an audio embed, a transcript placeholder: a file name is not what he said)."""
    return bool(line.strip()) and not line.lstrip().startswith("#") and not _ENGINE_LINE.match(line) \
        and line.strip() not in _PLACEHOLDERS


def first_line(words: str, cap: int = ENTRY_CHARS) -> str:
    """The first line of his words (``entries.his_words``) that he wrote, clipped to ``cap``; "" when none. Shared
    with ``journal serve``'s entry summaries, so the block and the web app preview an entry the same way."""
    first = next((_STAMP.sub("", l) for l in (words or "").splitlines() if _is_his_line(l)), "")
    return _clip(first, cap) if first.strip() else ""


def _entries(cfg: Config, now_local: datetime) -> list[dict]:
    out = []
    # by name, not by mtime (entries_on's order): the same state gives the same block, and the fixture holds
    for path in sorted(entries.entries_on(cfg.root, now_local.date().isoformat())):
        try:
            words = entries.his_words(path.read_text(encoding="utf-8"))
        except OSError:
            continue
        first = first_line(words)
        if first:
            out.append({"stem": path.stem, "first_line": first})
    return out


def _reflection(cfg: Config, now_local: datetime, stems: bool = False) -> dict | None:
    """The question the latest day reflection (today's or the last two days') closed with. ``stems``: also the
    reflection's own stem (``<date>-reflection-2`` when a hand-written file held the first name)."""
    path = reflect.latest(cfg, "reflection", now_local.date(), 2)
    if path is None:
        return None
    body = reflect.body_of(path.read_text(encoding="utf-8"))
    m = re.search(r"\*\*To carry forward\*\*\s*[—:-]*\s*(.+)", body, re.S)
    if not m:
        return None
    text = " ".join(m.group(1).split())
    q = re.match(r"(.+?\?)(?:\s|$)", text)
    asked = _clip(q.group(1) if q else text, REFLECTION_CHARS)
    if not asked:
        return None
    return {"date": path.stem[:10], "asked": asked, **({"stem": path.stem} if stems else {})}


def _cached(cfg: Config, now: datetime, name: str, fresh_hours: float | None = None) -> dict | None:
    """A part of the tick's cache, or None when it is missing, unreadable or older than ``fresh_hours``."""
    part = read_cache(cfg).get(name)
    if not isinstance(part, dict):
        return None
    if fresh_hours is not None:
        try:
            if now - datetime.fromisoformat(part["at"]) > timedelta(hours=fresh_hours):
                return None
        except (KeyError, TypeError, ValueError):
            return None
    return part


def _tasks(cfg: Config, now: datetime, now_local: datetime) -> dict | None:
    part = _cached(cfg, now, "tasks")
    if not part or part.get("date") != now_local.date().isoformat() or not part.get("count"):
        return None  # yesterday's list is not today's
    return {"count": int(part["count"]), "names": [str(n) for n in part.get("names") or []][:TASKS_SHOWN]}


def _weather(cfg: Config, now: datetime) -> str | None:
    part = _cached(cfg, now, "weather", WEATHER_STALE_HOURS)
    return str(part["text"]) if part and part.get("text") else None


def collect(cfg: Config, now: datetime, stems: bool = False, with_standing: bool = False) -> dict:
    """Everything the block (and ``GET /today``) says, as a plain dict. ``now`` is a timezone-aware moment.

    Keys always present: ``date``, ``weekday``, ``time``, ``timezone``, ``location``, then one per source
    (``practices`` as ``{rows, open}``, ``program``, ``entries``, ``reflection``, ``tasks``, ``weather``), each
    ``None``/empty when the source is off, has nothing, or failed. Reads state only: no source is called.
    ``stems`` adds the entry stem to each practice row and to the reflection (the web app links by them); the
    block and its goldens never ask for it. ``with_standing`` (the block only: ``render``) adds ``standing``, where
    things stand as the pack has it (``standing.today_lines``), from state and the time of his last public chat
    message the tick cached; ``GET /today`` and MCP's today text don't ask for it, so their shape is unchanged."""
    now_local = now.astimezone(cfg.tz)
    wants = wanted(cfg)
    out = {"date": now_local.date().isoformat(), "weekday": now_local.strftime("%a"),
           "time": now_local.strftime("%H:%M"), "timezone": now_local.strftime("%Z"), "location": cfg.location or None,
           "practices": {"rows": [], "open": []}, "program": None, "entries": [], "reflection": None,
           "tasks": None, "weather": None}
    for key, name, fetch in (
            ("practices", "practices", lambda: _practices(cfg, now_local, stems)),
            ("entries", "entries", lambda: _entries(cfg, now_local)),
            ("reflection", "reflection", lambda: _reflection(cfg, now_local, stems)),
            ("tasks", "tasks", lambda: _tasks(cfg, now, now_local)),
            ("weather", "weather", lambda: _weather(cfg, now))):
        if name not in wants:
            continue
        try:
            out[key] = fetch()
        except Exception:  # one collector failing drops its line, never the block
            log.exception("journal: today block: %s unavailable", name)
    if "programs" in wants:
        try:
            from . import programs
            out["program"] = programs.today_line(cfg, now)
        except Exception:  # noqa: BLE001 - the line is dropped, never the block
            log.exception("journal: today block: programs unavailable")
    if with_standing and "standing" in wants:
        try:
            out["standing"] = _standing(cfg, now)
        except Exception:  # noqa: BLE001 - the lines are dropped, never the block
            log.exception("journal: today block: standing unavailable")
    return out


def _standing(cfg: Config, now: datetime) -> dict:
    """``{"lines", "programs"}``: where things stand, with the chat part from the tick's cache (never the
    transcript here). ``programs`` says the lines carry the runs, so the block's own ``program`` line is not
    repeated."""
    part = _cached(cfg, now, "heard")
    last = None
    if part and part.get("last"):
        try:
            last = datetime.fromisoformat(part["last"])
        except (TypeError, ValueError):
            last = None
    data = standing.collect(cfg, now, chat=(last, "cached" if part else "none"))
    return {"lines": standing.today_lines(cfg, data), "programs": bool(data["programs"])}


# ── rendering ────────────────────────────────────────────────────────────────

def _practice_text(r: dict) -> str:
    if r["state"] == "upcoming":
        return f"{r['id']} at {r['at']}"
    if r["state"] == "answered":
        return f"{r['id']} sent {r['sent']}, answered {r['answered']}" if r.get("answered") \
            else f"{r['id']} sent {r['sent']}, answered"
    if r["state"] == "open":
        return f"{r['id']} sent {r['sent']}, no reply yet"
    return f"{r['id']} sent {r['sent']} (missed, reply anytime)"


def lines_of(data: dict) -> list[str]:
    """The block's body lines in their fixed order, each only when it has something."""
    lines = list((data.get("standing") or {}).get("lines") or [])
    rows = (data.get("practices") or {}).get("rows") or []
    if rows:
        lines.append("- practices: " + " · ".join(_practice_text(r) for r in rows))
    opened = (data.get("practices") or {}).get("open") or []
    if opened:
        lines.append("- open prompt awaiting a reply: " + ", ".join(opened))
    prog = data.get("program")
    if prog and prog.get("text") and not (data.get("standing") or {}).get("programs"):
        lines.append(f"- program: {prog['text']}")
    ents = data.get("entries") or []
    if ents:
        shown = ents[:ENTRIES_SHOWN]
        more = f" · (+{len(ents) - len(shown)} more)" if len(ents) > len(shown) else ""
        lines.append("- entries today: " + " · ".join(f'{e["stem"]} ("{e["first_line"]}")' for e in shown) + more)
    ref = data.get("reflection")
    if ref:
        lines.append(f"- {_when_word(ref['date'], data['date'])} reflection asked: \"{ref['asked']}\"")
    tasks = data.get("tasks")
    if tasks:
        names = [_clip(n, TASK_CHARS) for n in tasks["names"]]
        extra = tasks["count"] - len(names)
        lines.append(f"- tasks due today: {tasks['count']} — " + "; ".join(names) + (f"; +{extra} more" if extra > 0 else ""))
    if data.get("weather"):
        lines.append(f"- weather: {data['weather']}")
    return lines


def _when_word(ref_date: str, today: str) -> str:
    try:
        gap = (date.fromisoformat(today) - date.fromisoformat(ref_date)).days
    except ValueError:
        return "the latest"
    return {0: "today's", 1: "yesterday's"}.get(gap, f"the {date.fromisoformat(ref_date).strftime('%a')}")


def header_of(data: dict) -> str:
    d = datetime.strptime(data["date"], "%Y-%m-%d")
    where = f" · {data['location']}" if data.get("location") else ""
    return f"## Journal: today — {d.strftime('%a %-d %b %Y')}, {data['time']} {data['timezone']}{where}"


def render_data(data: dict, max_chars: int = 1200) -> str:
    """The block for ``collect``'s dict, cut from the bottom, a whole line at a time, to ``max_chars``. Empty
    when there is nothing to say (no line) or not even the header fits."""
    body = lines_of(data)
    if not body:
        return ""
    head = [header_of(data), NOTE]
    while body and len("\n".join(head + body)) > max_chars:
        body.pop()
    return "\n".join(head + body) if body else ""


def render(cfg: Config, now: datetime) -> str:
    """The thread-start block for ``now``, or "" (off, nothing to say, or any failure)."""
    if not enabled(cfg):
        return ""
    return render_data(collect(cfg, now, with_standing=True), today_max_chars(cfg))
