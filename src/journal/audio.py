"""Audio kept beside entries: copying it in, what is known about it, and transcribing audio files.

Voice notes are transcribed by the gateway (Hermes STT) before the companion sees them. Audio files
and recordings sent as documents are not, so the tick transcribes those after capture
(``tick.process_audio``), never the gateway hook: speech-to-text can take minutes.

Transcription backends, first that applies:

1. ``audio.command``: an argv template with ``{file}``; prints the transcript on stdout.
2. Under Hermes: the profile's own speech-to-text (``tools.transcription_tools.transcribe_audio``, the
   function the gateway uses for voice notes), imported in-process when the tick runs as a Hermes
   cron script.

``audio.transcribe: false`` turns it off; the block then says the file was not transcribed.

Heading metadata comes from what Telegram reports (file name, duration, forward date) and, when
``ffprobe`` is available, the file's own recording time.
"""
from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

from .config import Config

AUDIO_EXT = {  # mimetypes' table misses several audio types on some platforms
    "audio/ogg": ".ogg", "audio/opus": ".opus", "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a", "audio/m4a": ".m4a", "audio/aac": ".aac", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/wave": ".wav", "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/webm": ".webm", "audio/amr": ".amr",
    "audio/3gpp": ".3gp",
}
_TYPE_OF_EXT = {ext: mime for mime, ext in AUDIO_EXT.items()}
NOT_TRANSCRIBED = "_(audio file — not transcribed)_"
TRANSCRIBING = "_(audio file — transcript pending)_"
NO_TRANSCRIPT = "(voice note — no transcript was available)"
EMPTY_TRANSCRIPT = "empty transcript"


def extension_for(mime: str) -> str:
    base = (mime or "").split(";")[0].strip().lower()
    return AUDIO_EXT.get(base) or (mimetypes.guess_extension(base) if base else None) or ".ogg"


def media_type(name: str) -> str | None:
    """The audio type of a file name, or None when it is not audio. One test for "is this a recording" wherever the
    web app asks: ``/media`` serves exactly the names this accepts, and the entry document treats exactly these
    embeds as audio (``hooks.original_extension`` keeps the sender's suffix, so ``.oga`` or ``.aif`` can be kept)."""
    ext = os.path.splitext(name)[1].lower()
    mime = _TYPE_OF_EXT.get(ext) or mimetypes.guess_type(name)[0] or ""
    return mime if mime.startswith("audio/") or ext in _TYPE_OF_EXT else None


def keep(cfg: Config, media: str, entry_path: str, ext: str) -> str:
    """Copy the audio next to its entry, same name: 2026-10-13-evening.ogg (then -2, -3 …). Raises on failure."""
    stem = os.path.splitext(os.path.basename(entry_path))[0]
    n = 1
    while True:  # claim the name atomically (O_EXCL): the gateway and the tick may both be keeping audio
        name = f"{stem}{ext}" if n == 1 else f"{stem}-{n}{ext}"
        dest = os.path.join(str(cfg.root), name)
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            n += 1
            continue
        break
    try:
        with os.fdopen(fd, "wb") as out, open(media, "rb") as src:
            shutil.copyfileobj(src, out)
        shutil.copystat(media, dest)
        os.chmod(dest, 0o644)  # like the entries; Hermes caches audio 0600
    except BaseException:
        os.unlink(dest)
        raise
    return name


# ── metadata ─────────────────────────────────────────────────────────────────

def _seconds(value) -> int | None:
    """Telegram durations: int seconds, or a timedelta (python-telegram-bot 22 can return either)."""
    if value is None:
        return None
    if isinstance(value, timedelta):
        return int(value.total_seconds())
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _iso(value) -> str | None:
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return None


def event_meta(event) -> dict:
    """What the Telegram message says about its audio: original file name, duration, forward date.

    Read from the python-telegram-bot ``Message`` Hermes keeps as ``event.raw_message``: ``voice``,
    ``audio`` and ``document`` (``file_name``, ``duration``) and ``forward_origin.date``. Unknown
    fields are left out.
    """
    raw = getattr(event, "raw_message", None)
    if raw is None:
        return {}
    meta: dict = {}
    for attr in ("voice", "audio", "document", "video_note"):
        obj = getattr(raw, attr, None)
        if obj is None:
            continue
        name = getattr(obj, "file_name", None)
        if isinstance(name, str) and name.strip() and "file_name" not in meta:
            meta["file_name"] = name.strip()
        secs = _seconds(getattr(obj, "duration", None))
        if secs is not None and "duration" not in meta:
            meta["duration"] = secs
    origin = getattr(raw, "forward_origin", None)
    fwd = _iso(getattr(origin, "date", None)) if origin is not None else _iso(getattr(raw, "forward_date", None))
    if fwd:
        meta["forwarded_at"] = fwd
    return meta


def clock(seconds: int) -> str:
    """2:31, 12:04, 1:02:09."""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _when(iso: str, tz) -> str | None:
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).strftime("%-d %b %H:%M") if tz else dt.strftime("%-d %b %H:%M")


def label(source: str, meta: dict | None = None, tz=None) -> str:
    """The reply heading after the time, one line, unknowns omitted:
    ``voice transcript · 2:31`` / ``audio file "walk.m4a" · 12:04 · recorded 14 Oct 21:10`` / ``photo`` (an image he
    sent: ``images.py``)."""
    meta = meta or {}
    base = {"voice": "voice transcript", "audio": "audio file", "photo": "photo"}.get(source, "text")
    if source not in ("voice", "audio"):
        return base
    parts = [base + (f' "{meta["file_name"]}"' if source == "audio" and meta.get("file_name") else "")]
    if meta.get("duration"):
        parts.append(clock(meta["duration"]))
    rec = _when(meta["recorded_at"], tz) if meta.get("recorded_at") else None
    if rec:
        parts.append(f"recorded {rec}")
    fwd = _when(meta["forwarded_at"], tz) if meta.get("forwarded_at") else None
    if fwd:
        parts.append(f"forwarded from {fwd}")
    return " · ".join(parts)


def ffprobe_path(cfg: Config) -> str | None:
    configured = (cfg.audio.get("ffprobe") or "").strip()
    if configured:
        return configured if os.path.isfile(configured) else None
    return shutil.which("ffprobe")


_CREATION_TAGS = ("com.apple.quicktime.creationdate", "creation_time", "date")


def probe(cfg: Config, path: str) -> dict:
    """Recording time and duration from the file itself (ffprobe), when it says; {} otherwise."""
    exe = ffprobe_path(cfg)
    if not exe:
        return {}
    try:
        out = subprocess.run([exe, "-v", "quiet", "-print_format", "json", "-show_format", path],
                             capture_output=True, text=True, timeout=20)
        fmt = json.loads(out.stdout or "{}").get("format") or {}
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return {}
    meta: dict = {}
    tags = {str(k).lower(): v for k, v in (fmt.get("tags") or {}).items()}
    for key in _CREATION_TAGS:
        raw = str(tags.get(key) or "").strip()
        if not raw:
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.year > 1971:  # some encoders write the epoch
            meta["recorded_at"] = (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()
            break
    try:
        dur = float(fmt.get("duration"))
        if dur > 0:
            meta["duration"] = int(round(dur))
    except (TypeError, ValueError):
        pass
    return meta


# ── transcription ────────────────────────────────────────────────────────────

def transcription_enabled(cfg: Config) -> bool:
    """Whether audio files should wait for a transcript (a backend is configured or Hermes may provide one)."""
    if cfg.audio.get("transcribe") is False:
        return False
    return bool(cfg.audio.get("command")) or cfg.under_hermes


def is_outage(why: str) -> bool:
    """Whether a ``transcribe`` failure is the backend failing (an outage: defer and retry later).
    "unavailable" (no backend configured) and an empty transcript (the recording had no words) are not."""
    return not (why == "ok" or why.startswith("unavailable") or why == EMPTY_TRANSCRIPT)


def transcribe(cfg: Config, path: str) -> tuple[str | None, str]:
    """(transcript, why). ``why`` is "ok", "unavailable" (no backend; don't retry), "empty transcript"
    (the backend ran but heard nothing: counts as an attempt) or an error (the backend failed: ``is_outage``)."""
    if cfg.audio.get("transcribe") is False:
        return None, "unavailable"
    cmd = cfg.audio.get("command")
    if cmd:
        argv = [str(part).format(file=path) for part in (cmd if isinstance(cmd, list) else [cmd])]
        try:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=1800)
        except (OSError, subprocess.TimeoutExpired) as e:
            return None, f"{type(e).__name__}"
        text = (out.stdout or "").strip()
        if out.returncode != 0:
            return None, f"exit {out.returncode}"
        return (text, "ok") if text else (None, EMPTY_TRANSCRIPT)
    if not cfg.under_hermes:
        return None, "unavailable"
    try:
        from tools.transcription_tools import transcribe_audio  # Hermes' own STT, as for voice notes
    except Exception as e:  # not running inside Hermes' environment
        return None, f"unavailable ({type(e).__name__})"
    try:
        result = transcribe_audio(path, source="journal")
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:120]}"
    if not isinstance(result, dict) or not result.get("success"):
        return None, str((result or {}).get("error") or "transcription failed")[:200]
    text = str(result.get("transcript") or "").strip()
    return (text, "ok") if text else (None, EMPTY_TRANSCRIPT)


def backend(cfg: Config) -> tuple[str, str]:
    """For doctor: (status, detail) of audio-file transcription. Imports nothing heavy beyond a spec probe."""
    if cfg.audio.get("transcribe") is False:
        return "ok", "off (audio.transcribe: false): audio files are kept, not transcribed"
    cmd = cfg.audio.get("command")
    if cmd:
        exe = str((cmd if isinstance(cmd, list) else [cmd])[0])
        found = shutil.which(exe) is not None or os.path.isfile(exe)
        return ("ok", f"audio.command {exe}") if found else ("warn", f"audio.command {exe} not found")
    if cfg.under_hermes:
        return "ok", "Hermes speech-to-text, run by the tick (as for voice notes)"
    return "warn", "no backend outside Hermes: set audio.command to transcribe audio files"
