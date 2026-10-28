"""External, read-only context sources for the pack.

Each returns a list of short dated lines and reads only what ``sources.<name>`` configures; a
source that is disabled is never called, and one that is missing or failing raises (the pack
records it as unavailable). Nothing here writes anywhere.

| Source | Settings |
| --- | --- |
| weather | ``coordinates`` (Open-Meteo, no key) |
| tududi | ``sources.tududi.command``: argv of a tududi MCP stdio server |
| scrobbler | ``sources.scrobbler.command``: the chat-scrobbler CLI |
| listenbrainz | ``sources.listenbrainz.user`` |
| reading | ``sources.storyteller.db``, ``sources.audiobookshelf.db`` (SQLite, opened read-only), ``sources.grimmory.container`` / ``database`` (MariaDB in Docker; credentials stay in the container) |
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import Config

READERS = ("storyteller", "grimmory", "audiobookshelf")


def _argv(cmd) -> list[str]:
    if not cmd:
        raise RuntimeError("no command configured")
    return [cmd] if isinstance(cmd, str) else [str(x) for x in cmd]


def _mcp_call(command, tool: str, arguments: dict) -> dict:
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "journal", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": tool, "arguments": arguments}},
    ]
    out = subprocess.run(_argv(command), input="\n".join(json.dumps(m) for m in msgs) + "\n",
                         capture_output=True, text=True, timeout=20)
    for line in out.stdout.splitlines():
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == 2:
            if "error" in msg:
                raise RuntimeError(str(msg["error"])[:150])
            return json.loads(msg["result"]["content"][0]["text"])
    raise RuntimeError("no tools/call reply from tududi")


def tududi(cfg: Config, now_local: datetime) -> list[str]:
    data = _mcp_call(cfg.source("tududi").get("command"), "list_tasks", {"type": "all", "limit": 200})
    today = now_local.date().isoformat()
    week_ago = (now_local - timedelta(days=7)).isoformat()
    lines = []
    idle = 0  # habits never once completed: a list of them only invites "you haven't …" hooks
    for t in data.get("tasks", []):
        name = t.get("name", "")
        project = (t.get("Project") or {}).get("name") if isinstance(t.get("Project"), dict) else None
        tag = f" [{project}]" if project else ""
        if t.get("completed_at") and t["completed_at"] >= week_ago:
            lines.append(f"- tududi: done {t['completed_at'][:10]} — {name}{tag}")
        elif t.get("habit_mode"):
            streak, last = t.get("habit_current_streak") or 0, t.get("habit_last_completion_at")
            if not streak and not last:
                idle += 1
                continue
            lines.append(f"- tududi habit: {name} (streak {streak}, last {(last or 'never')[:10]})")
        elif t.get("due_date") and t["due_date"][:10] <= today:
            lines.append(f"- tududi: due {t['due_date'][:10]} — {name}{tag}")
        else:
            lines.append(f"- tududi: open — {name}{tag}")
    lines = lines[:30]
    if idle:
        lines.append(f"- tududi: {idle} more habit{'s' if idle != 1 else ''} never completed yet (not listed)")
    return lines


def tududi_due(cfg: Config, now_local: datetime) -> list[str]:
    """Names of the open tasks due today or earlier, from the structured reply (not ``tududi``'s display lines,
    which are cut to 30 in API order before any due filter and mix in done work). Habits and completed tasks
    are never due."""
    data = _mcp_call(cfg.source("tududi").get("command"), "list_tasks", {"type": "all", "limit": 200})
    today = now_local.date().isoformat()
    names = []
    for t in data.get("tasks", []):
        due = t.get("due_date")
        if (isinstance(due, str) and due[:10] <= today and due[:10] and not t.get("completed_at")
                and not t.get("habit_mode") and str(t.get("name") or "").strip()):
            names.append(str(t["name"]).strip())
    return names


def scrobbler(cfg: Config, now_local: datetime) -> list[str]:
    cmd = _argv(cfg.source("scrobbler").get("command") or "chat-scrobbler")
    out = subprocess.run([*cmd, "list", "--limit", "50", "--json"], capture_output=True, text=True, timeout=20)
    since = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    lines = []
    for s in json.loads(out.stdout or "[]"):
        if str(s.get("updated_at", "")) >= since:
            lines.append(f"- scrobbler:{s['id']} ({s.get('source')}, {str(s.get('updated_at'))[:16]}) — {s.get('title')}")
    return lines[:12]


def _curl(url: str, *extra: str) -> str:
    out = subprocess.run(["curl", "-4", *extra, "-s", "--max-time", "15", url],
                         capture_output=True, text=True, timeout=20)
    return out.stdout


def listenbrainz(cfg: Config, now_local: datetime) -> list[str]:
    user = (cfg.source("listenbrainz").get("user") or "").strip()
    if not user:
        raise RuntimeError("sources.listenbrainz.user is not set")
    since = int((datetime.now(timezone.utc) - timedelta(days=2)).timestamp())
    body = _curl(f"https://api.listenbrainz.org/1/user/{urllib.parse.quote(user)}/listens?count=15&min_ts={since}",
                 "--tls-max", "1.2")
    listens = json.loads(body or "{}").get("payload", {}).get("listens", [])
    lines = []
    for l in listens:
        md = l.get("track_metadata", {})
        when = datetime.fromtimestamp(l.get("listened_at", 0), timezone.utc).astimezone(cfg.tz)
        lines.append(f"- listenbrainz:{when.isoformat(timespec='minutes')} — {md.get('artist_name')} · {md.get('track_name')}")
    return lines


def _sqlite(path: str, sql: str, args: tuple = ()) -> list:
    if not path or not Path(path).is_file():
        raise FileNotFoundError(path or "no database configured")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def reading(cfg: Config, now_local: datetime) -> list[str]:
    """What the user has actually been reading or listening to, across the configured readers."""
    since = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    lines: list[tuple[str, str]] = []
    errors = []
    if cfg.source_enabled("storyteller"):
        try:  # read-along ebooks; positions are UTC
            for updated, title, locator in _sqlite(cfg.source("storyteller").get("db", ""),
                    """select p.updated_at, b.title, p.locator from position p join book b on b.uuid = p.book_uuid
                       where p.updated_at >= ? order by p.updated_at desc limit 8""", (since,)):
                pct = json.loads(locator).get("locations", {}).get("totalProgression")
                pct_s = f"{round(pct * 100)}%" if isinstance(pct, (int, float)) else "?"
                local = datetime.fromisoformat(updated).replace(tzinfo=timezone.utc).astimezone(cfg.tz)
                lines.append((local.isoformat(), f"- book:{title} — Storyteller, {pct_s} through, last read "
                                                  f"{local.isoformat(timespec='minutes')}"))
        except Exception as e:
            errors.append(f"Storyteller unavailable ({type(e).__name__})")
    if cfg.source_enabled("grimmory"):
        try:  # ebook library in MariaDB; credentials stay inside the container
            g = cfg.source("grimmory")
            container, database = g.get("container") or "", g.get("database") or "booklore"
            if not container:
                raise RuntimeError("sources.grimmory.container is not set")
            sql = ("select p.last_read_time, m.title, coalesce(p.epub_progress_percent, p.pdf_progress_percent, "
                   "p.koreader_progress_percent, p.kobo_progress_percent), p.read_status from user_book_progress p "
                   f"left join book_metadata m on m.book_id = p.book_id where p.last_read_time >= '{since}' "
                   "order by p.last_read_time desc limit 8")
            out = subprocess.run(["docker", "exec", container, "sh", "-c",
                                  f'mariadb -u"$MYSQL_USER" -p"$MYSQL_PASSWORD" {database} -N -B -e "{sql}"'],
                                 capture_output=True, text=True, timeout=20)
            for row in out.stdout.splitlines():
                when, title, pct, status = (row.split("\t") + ["", "", "", ""])[:4]
                pct_s = f"{float(pct):.0f}%" if pct not in ("", "NULL") else "?"
                lines.append((when, f"- book:{title} — Grimmory, {pct_s} ({status.lower()}), last read {when[:16]}"))
        except Exception as e:
            errors.append(f"Grimmory unavailable ({type(e).__name__})")
    if cfg.source_enabled("audiobookshelf"):
        try:  # never touch its users table
            for updated, title, audio, ebook, finished in _sqlite(cfg.source("audiobookshelf").get("db", ""),
                    """select mp.updatedAt, b.title, round(mp.currentTime / nullif(mp.duration, 0), 3), mp.ebookProgress,
                              mp.isFinished from mediaProgresses mp left join books b on b.id = mp.mediaItemId
                       where mp.updatedAt >= ? order by mp.updatedAt desc limit 5""", (since,)):
                pct = audio if audio is not None else ebook
                pct_s = "finished" if finished else (f"{round(float(pct) * 100)}%" if pct is not None else "?")
                lines.append((str(updated), f"- book:{title} — Audiobookshelf, {pct_s}, last {str(updated)[:16]}"))
        except Exception as e:
            errors.append(f"Audiobookshelf unavailable ({type(e).__name__})")
    out = [l for _, l in sorted(lines, reverse=True)]
    return out + ([f"_{'; '.join(errors)}_"] if errors else [])


WMO = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "fog", 48: "freezing fog",
       51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
       66: "freezing rain", 67: "heavy freezing rain", 71: "light snow", 73: "snow", 75: "heavy snow",
       77: "snow grains", 80: "light showers", 81: "showers", 82: "violent showers", 85: "snow showers",
       86: "heavy snow showers", 95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}


def weather(cfg: Config, now_local: datetime) -> list[str]:
    """Open-Meteo (no key): now, today and tomorrow at the configured coordinates."""
    if not cfg.coordinates:
        return ["_no coordinates in the journal configuration_"]
    lat, lon = cfg.coordinates
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
           "&current=temperature_2m,apparent_temperature,weather_code"
           "&daily=temperature_2m_max,temperature_2m_min,precipitation_probability_max,weather_code,sunrise,sunset"
           f"&timezone={cfg.timezone.replace('/', '%2F')}&forecast_days=2")
    d = json.loads(_curl(url))
    cur, day = d["current"], d["daily"]
    lines = [f"- weather:now ({cur['time']}) — {WMO.get(cur['weather_code'], 'code ' + str(cur['weather_code']))}, "
             f"{cur['temperature_2m']}°C (feels {cur['apparent_temperature']}°C)"]
    for i, label in enumerate(("today", "tomorrow")):
        lines.append(f"- weather:{label} ({day['time'][i]}) — {WMO.get(day['weather_code'][i], '?')}, "
                     f"{day['temperature_2m_min'][i]}–{day['temperature_2m_max'][i]}°C, "
                     f"rain chance {day['precipitation_probability_max'][i]}%, "
                     f"sunrise {day['sunrise'][i][-5:]}, sunset {day['sunset'][i][-5:]}")
    return lines


ALL = [
    ("weather", "Weather where they are (Open-Meteo)", weather),
    ("tududi", "tududi — open tasks, habits, done this week", tududi),
    ("scrobbler", "AI chats in the last 3 days (chat-scrobbler: ChatGPT/Claude/Gemini browser chats)", scrobbler),
    ("listenbrainz", "Music in the last 2 days (ListenBrainz)", listenbrainz),
    ("reading", "Reading in the last 30 days (Storyteller, Grimmory, Audiobookshelf)", reading),
]


def _runnable(argv: list[str]) -> bool:
    """The executable resolves, and any absolute-path arguments (a script it runs) exist."""
    exe, rest = argv[0], argv[1:]
    found = shutil.which(exe) is not None or Path(exe).is_file()
    return found and all(Path(a).exists() for a in rest if a.startswith("/"))


def check(cfg: Config) -> list[tuple[str, bool, str]]:
    """Static reachability for doctor: (source, ok, detail). Runs nothing, opens nothing for write."""
    out = []
    if cfg.source_enabled("weather"):
        out.append(("weather", bool(cfg.coordinates),
                    "coordinates set" if cfg.coordinates else "no coordinates: weather will be skipped"))
    if cfg.source_enabled("telegram_history"):
        db = cfg.state_db
        if db is None:
            out.append(("telegram_history", True, "skipped: no Hermes sessions database outside Hermes"))
        else:
            ok = Path(db).is_file()
            out.append(("telegram_history", ok, str(db) if ok else f"no sessions database at {db}"))
    if cfg.source_enabled("tududi"):
        cmd = cfg.source("tududi").get("command")
        ok = bool(cmd) and _runnable(_argv(cmd))
        out.append(("tududi", ok, "command found" if ok else "sources.tududi.command missing or not found"))
    if cfg.source_enabled("scrobbler"):
        exe = _argv(cfg.source("scrobbler").get("command") or "chat-scrobbler")[0]
        ok = _runnable([exe])
        out.append(("scrobbler", ok, exe if ok else f"{exe} not found"))
    if cfg.source_enabled("listenbrainz"):
        user = cfg.source("listenbrainz").get("user")
        out.append(("listenbrainz", bool(user), f"user {user}" if user else "sources.listenbrainz.user not set"))
    for name in ("storyteller", "audiobookshelf"):
        if cfg.source_enabled(name):
            db = cfg.source(name).get("db") or ""
            ok = bool(db) and Path(db).is_file() and os.access(db, os.R_OK)
            out.append((name, ok, db if ok else f"database {db or '(unset)'} not readable"))
    if cfg.source_enabled("grimmory"):
        g = cfg.source("grimmory")
        ok = bool(g.get("container")) and shutil.which("docker") is not None
        out.append(("grimmory", ok, f"container {g.get('container')}" if ok else "container unset or docker not found"))
    return out
