"""Journal configuration: built-in defaults, an optional JSON file, and Hermes plugin settings.

Layers, lowest to highest precedence:

1. ``DEFAULTS`` below.
2. A JSON config file. Its path is the first of: the ``path`` argument (``journal --config``),
   ``$JOURNAL_CONFIG``, the Hermes setting ``config_file``, and, standalone only,
   ``$XDG_CONFIG_HOME/journal/config.json`` when that file exists.
3. Hermes plugin settings (``plugins.entries.journal.settings`` in the profile's ``config.yaml``),
   when running under Hermes. Read in-process through the plugin context, or from ``config.yaml``
   by the cron tick, or from ``$JOURNAL_HERMES_SETTINGS`` (a JSON snapshot the plugin hands to the
   processes it spawns).

Nested objects merge key by key; anything else (lists, scalars) replaces the lower layer.
``Config.origins`` records which layer supplied each top-level key, so writes (travel updates)
go back to the layer that owns the value.

Schedules are local wall-clock times in ``timezone``; travel changes ``timezone`` and nothing
else needs converting. They live in the practice files (``library``), not here: a ``schedule`` key left in a
config layer is not read (``from_dict`` warns, ``migrate-config`` moves it into forked practice files).
"""
from __future__ import annotations

import copy
import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

PLUGIN_ID = "journal"

DEFAULTS: dict[str, Any] = {
    "root": None,  # required: the journal directory; one Markdown file per entry lands here
    "user": {"name": "", "pronouns": "they/them/their"},
    "companion": {"name": "Companion", "emoji": "📓"},
    "timezone": "UTC",
    "location": "",
    "coordinates": None,  # {"lat": .., "lon": .., "place": .., "source"?: "openstreetmap"} for weather (and the credit)
    "lookup": {           # the keyless services "Use my location" asks (location.reverse_geocode)
        "reverse_url": "https://nominatim.openstreetmap.org/reverse",   # names the place at a position (OSM Nominatim)
        "timezone_url": "https://api.open-meteo.com/v1/forecast",       # the place's time zone (timezone=auto)
    },
    "windows": {
        "collapse_hours": 6.0,          # a prompt is "visible" (and folds) after this long
        "grace_hours": 2.0,             # how late a scheduled prompt may still be sent
        "opener_budget_seconds": 240,   # model time for the opener
        "annotate_after_minutes": 20,   # wait after the first reply before annotating
    },
    "channel": {                        # the journal's chat (CHANNELS): any Hermes gateway platform
        "platform": "telegram",         # telegram, discord, slack, signal, whatsapp, matrix, … (Hermes' Platform names)
        "chat_id": "",                  # the chat as Hermes names it; empty on telegram = telegram.chat_id
        "user_ids": [],                 # his ids on the platform; empty = everyone in the chat is him (a DM)
    },
    "telegram": {
        "chat_id": "",
        "token_env": "JOURNAL_TELEGRAM_TOKEN",
        "token_command": None,          # argv list (or string run by the shell) that prints the token
        "token_key": "TELEGRAM_BOT_TOKEN",  # read KEY=value from the command's output; else its first line
        "annotation_replies": True,     # reply to each captured message with its annotations
    },
    "agent": {
        "enabled": True,
        "command": None,  # argv template; None = Hermes one-shot chat. Placeholders: {query_file} {budget} {toolsets}
    },
    "hermes": {
        "profile": None,          # profile for `hermes -p <profile> chat`; None = the profile running the plugin
        "command": None,          # hermes executable; None = found on PATH or ~/.local/bin/hermes
        "state_db": None,         # sessions database for chat history; None = <HERMES_HOME>/state.db
        "cron_schedule": "*/5 * * * *",
        "failure_deliver": None,  # cron failure notices; None = <channel platform>:<chat_id>
        "mirror_to_chat": True,   # mirror what the journal sends into the companion's chat session
        "legacy_plugins": [],     # other plugins that capture the same chat; doctor fails while one is enabled
    },
    "sources": {
        "weather": {"enabled": True},
        "telegram_history": {"enabled": True, "days": 7, "limit": 25},
        "tududi": {"enabled": False, "command": None},
        "scrobbler": {"enabled": False, "command": "chat-scrobbler"},
        "listenbrainz": {"enabled": False, "user": ""},
        "storyteller": {"enabled": False, "db": ""},
        "audiobookshelf": {"enabled": False, "db": ""},
        "grimmory": {"enabled": False, "container": "", "database": "booklore"},
        "not_connected": "",
    },
    "guard": {
        "enabled": True,
        "protected_paths": [],
        "allowed_write_paths": [],
        "protected_tokens": [".hermes"],
        "protect_journal_root": True,
        "block_execute_code": False,
        "restrict_hermes_cli": True,
        "block_system_commands": True,
        "escalate_to": "",
    },
    "prompts": {"reading_guidance": ""},
    "entries": {"frontmatter_source": "journal"},
    "audio": {
        "transcribe": True,   # transcribe audio files (voice notes are transcribed by the gateway)
        "command": None,      # argv template with {file} printing a transcript; None = Hermes STT
        "ffprobe": None,      # ffprobe for the recording time; None = found on PATH
    },
    "images": {
        "max_mb": 20,         # a photo or image file larger than this is not kept (its caption still is)
        "describe": False,    # the tick asks the companion for a one-line description, added as her note
    },
    "reflections": {
        "day": {"enabled": True, "fallback_time": "04:00"},   # after the evening entry is annotated, else next morning
        "week": {"enabled": True, "fallback_time": "04:00"},  # after the weekly entry is annotated, else the day after
    },
    "sweep": {"enabled": True, "lookback_hours": 48},  # capture replies the gateway hook missed (busy path)
    "recovery": {"enabled": True, "max_age_hours": 48},  # one catch-up reply after a model outage left him unanswered
    "capture": {                   # what gets journaled (capture_mode): "everything" or "arm"
        "mode": "everything",      # everything: every chat thread; arm: prompt replies, /j notes, program answers
    },                             # and the conversation after them, never ordinary chat
    "conversations": {             # everything said in the chat (both sides) saved into entries, as threads
        "enabled": True,
        "quiet_minutes": 45,       # a thread ends after this much silence (or when a journal prompt is sent)
        "opt_out_prefix": "/x",    # a message starting with this is off the record (alone: the rest of the thread)
        "annotate_auto": False,    # annotate note entries started automatically from conversation
    },
    "today": {                     # the once-per-thread "today" block (today.py); the tick caches tasks and weather
        "enabled": True,
        "max_chars": 1200,         # hard cap on the rendered block
        "sources": ["standing", "practices", "programs", "entries", "reflection", "tasks", "weather"],
        "cache_minutes": 30,       # how often the tick refreshes today-cache.json
    },
    "pack": {                      # the context pack an opener is written from (context.py, standing.py)
        "max_chars": 32000,        # the whole pack; over it, the oldest material drops first
        "entries": 6,              # his newest entries, by count whatever their age
        "lookback_days": 14,       # unanswered prompts (and his chat, for "last heard") reach back at most this far
        "returning_days": 3,       # a gap this long since he was last heard from asks the opener to welcome him back
        "pointers": [],            # extra "where to look further" lines, after the ones made from config
    },
    "serve": {                     # `journal serve`: the private web app and its API (serve/)
        "bind": "127.0.0.1",       # loopback for dev and tests; any wider address is the operator's call
        "port": 8790,
        "host": "",                # canonical host name: the Host check and the cookie's scope; "" = no Host check
        "public_port": None,       # the HTTPS port the site is published on when it is not 443 (None = 443)
        "token_env": "JOURNAL_SERVE_TOKEN",
        "token_command": None,     # argv list (or string run by the shell) that prints the token
        "token_key": "JOURNAL_SERVE_TOKEN",
        "allow_write": True,       # false (anything but true): every route that changes the journal answers 405
        "assets_dir": None,        # the built web app; None = the packaged serve/static
        "trusted_proxies": [],     # CIDRs whose X-Forwarded-* headers are believed
        "socket": None,            # absolute path of a Unix socket to listen on as well (for `tailscale serve`)
        "tailscale_users": [],     # Tailscale logins signed in by their identity header, on the socket only
    },
    "programs": {                  # ordered sessions of phases (programs.py)
        "settle_minutes": 2,       # a phase answer advances the run after this much quiet (/next: at once)
        "reflect": True,           # ask the model for a reflect phase's construction (template when it can't)
        "bridges": False,          # a one-paragraph model bridge before an ask phase, quoting his last answer
        "keyboard": True,          # send /next /skip /back /break as reply-keyboard buttons with each phase
        "synthesis": False,        # add the companion's validated synthesis to a program's insights document
    },
    "state_dir": None,
    "practices_dir": None,  # user-defined and forked practice files; default <state_dir>/practices
    "adapter": "auto",  # the agent the journal runs beside: auto | hermes | none (Config.transcript/companion)
}
ADAPTERS = ("auto", "hermes", "none")
CAPTURE_MODES = ("everything", "arm")

TOP_LEVEL_KEYS = tuple(DEFAULTS) + ("config_file",)


class ConfigError(ValueError):
    """The configuration cannot be used (missing root, bad timezone, unreadable file)."""


class HermesOwned(ConfigError):
    """A setting lives in Hermes settings, which no journal face edits (the companion never edits Hermes settings;
    the person does, with ``hermes config set``). ``name`` is the dotted key; ``hint`` says what to run (with
    ``-p <profile>`` when the settings read are a named profile's: without it the line would change the shell's
    default profile)."""

    def __init__(self, name: str, profile: str | None = None):
        self.name = name
        self.hint = hermes_set_line(name, profile)
        super().__init__(f"`{name}` is set in Hermes settings; change it with `{self.hint} ...`")


def hermes_set_line(name: str, profile: str | None = None) -> str:
    """The command that sets a plugin setting (without a value), in ``profile`` when one is named."""
    return f"hermes{f' -p {profile}' if profile else ''} config set plugins.entries.{PLUGIN_ID}.settings.{name}"


# ── helpers ──────────────────────────────────────────────────────────────────

def deep_merge(base: Mapping, over: Mapping) -> dict:
    """``over`` on top of ``base``; dicts merge recursively, everything else replaces."""
    out = copy.deepcopy(dict(base))
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _env(name: str, environ: Mapping[str, str] | None = None) -> str:
    return (environ if environ is not None else os.environ).get(name, "").strip()


def xdg_config_path(environ: Mapping[str, str] | None = None) -> Path:
    base = _env("XDG_CONFIG_HOME", environ) or str(Path.home() / ".config")
    return Path(base) / "journal" / "config.json"


def xdg_state_dir(environ: Mapping[str, str] | None = None) -> Path:
    base = _env("XDG_STATE_HOME", environ) or str(Path.home() / ".local" / "state")
    return Path(base) / "journal"


def hermes_data_root(hermes_home: Path) -> Path:
    """``~/.hermes`` for both ``~/.hermes`` and ``~/.hermes/profiles/<name>``."""
    home = Path(hermes_home)
    return home.parent.parent if home.parent.name == "profiles" else home


def profile_from_home(hermes_home: Path | None) -> str | None:
    """The ``-p`` name for a profile home (``.../profiles/<name>``); None for the default/custom home."""
    if hermes_home is None:
        return None
    home = Path(hermes_home)
    return home.name if home.parent.name == "profiles" else None


def read_json_file(path: Path) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"config file {path} is not readable JSON: {e}") from None
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must hold a JSON object")
    return data


def write_json_file(path: Path, data: Mapping) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)


def read_hermes_settings(hermes_home: Path | None, environ: Mapping[str, str] | None = None) -> tuple[dict, list[str]]:
    """The plugin's settings for out-of-process callers (tick, CLI). Returns (settings, warnings)."""
    snapshot = _env("JOURNAL_HERMES_SETTINGS", environ)
    if snapshot:
        try:
            data = json.loads(snapshot)
            return (data if isinstance(data, dict) else {}), []
        except json.JSONDecodeError:
            return {}, ["JOURNAL_HERMES_SETTINGS is not valid JSON; ignored"]
    if hermes_home is None:
        return {}, []
    path = Path(hermes_home) / "config.yaml"
    if not path.is_file():
        return {}, []
    try:
        import yaml  # Hermes ships PyYAML; standalone installs may not
    except ImportError:
        return {}, [f"PyYAML is not installed, so Hermes settings in {path} were not read"]
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as e:  # a broken config.yaml must not take the journal down with it
        return {}, [f"could not parse {path}: {type(e).__name__}"]
    entry = ((doc.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) if isinstance(doc, dict) else None
    if not isinstance(entry, dict):
        return {}, []
    merged = deep_merge(entry.get("config") or {}, entry.get("settings") or {})  # legacy `config` subtree
    return merged, []


# ── names ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Names:
    """How entries, prompts and messages refer to the people involved."""
    user: str = ""
    companion: str = "Companion"
    emoji: str = "📓"
    pronouns: str = "they/them/their"
    frontmatter_source: str = "journal"

    def _pron(self, i: int) -> str:
        parts = [p.strip() for p in (self.pronouns or "").split("/") if p.strip()]
        defaults = ("they", "them", "their")
        return parts[i] if len(parts) > i else defaults[i]

    @property
    def subject(self) -> str:   # he / she / they
        return self._pron(0)

    def verb(self, base: str) -> str:
        """``base`` (a regular verb) agreeing with the subject pronoun: he sends, she sends, they send."""
        return base if self.subject.lower() in ("they", "we", "you", "i") else base + "s"

    @property
    def object(self) -> str:    # him / her / them
        return self._pron(1)

    @property
    def possessive(self) -> str:  # his / her / their
        return self._pron(2)

    @property
    def user_or_them(self) -> str:
        return self.user or "the user"

    @property
    def user_possessive(self) -> str:
        return f"{self.user}'s" if self.user else "the user's"

    @property
    def user_possessive_title(self) -> str:  # sentence-initial form
        s = self.user_possessive
        return s[0].upper() + s[1:]

    @property
    def words_heading(self) -> str:
        return f"## {self.user}" if self.user else "## Entry"

    @property
    def no_words(self) -> str:
        return f"_Nothing from {self.user} yet._" if self.user else "_Nothing yet._"

    @property
    def annotations_heading(self) -> str:
        return f"## {self.companion}'s annotations"

    @property
    def notes_heading(self) -> str:
        return f"## {self.companion}'s notes"


# ── the config object ────────────────────────────────────────────────────────

@dataclass
class Config:
    root: Path
    timezone: str = "UTC"
    location: str = ""
    coordinates: tuple[float, float] | None = None  # (lat, lon) for weather
    chat_id: str = ""
    platform: str = "telegram"  # the journal chat's platform (channel.platform); chat_id is its chat
    owner_ids: tuple = ()       # his ids on that platform (channel.user_ids); empty: the chat is his
    profile: str | None = None
    collapse_hours: float = 6.0
    grace_hours: float = 2.0
    opener_budget_seconds: int = 240
    annotate_after_minutes: int = 20
    state_dir: Path = field(default_factory=lambda: xdg_state_dir())
    names: Names = field(default_factory=Names)
    telegram: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["telegram"]))
    agent: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["agent"]))
    hermes: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["hermes"]))
    sources: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["sources"]))
    guard: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["guard"]))
    prompts: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["prompts"]))
    audio: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["audio"]))
    images: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["images"]))
    reflections: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["reflections"]))
    sweep: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["sweep"]))
    recovery: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["recovery"]))
    capture: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["capture"]))
    conversations: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["conversations"]))
    today: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["today"]))
    pack: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["pack"]))
    serve: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["serve"]))
    programs: dict = field(default_factory=lambda: copy.deepcopy(DEFAULTS["programs"]))
    practices_dir: Path | None = None  # None only until from_dict resolves it; see ``practices_path``
    hermes_home: Path | None = None
    adapter: str = "auto"
    config_file: Path | None = None
    origins: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    raw: dict = field(default_factory=dict)

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def ledger_path(self) -> Path:
        return self.state_dir / "ledger.json"

    def __post_init__(self):
        if self.practices_dir is None:  # a Config built by hand (tests) gets the same default as from_dict
            self.practices_dir = self.state_dir / "practices"

    @property
    def under_hermes(self) -> bool:
        return self.hermes_home is not None

    def hermes_command(self) -> str:
        import shutil
        cmd = self.hermes.get("command")
        if cmd:
            return str(cmd)
        found = shutil.which("hermes")
        if found:
            return found
        local = Path.home() / ".local" / "bin" / "hermes"
        return str(local) if local.exists() else "hermes"

    def profile_args(self) -> list[str]:
        """``-p <profile>`` for the agent's one-shot chats, only when ``hermes.profile`` names one.

        Without it the chat runs in the exact home the journal runs under (``HERMES_HOME``, see
        ``hermes_env``); a name derived from a path could resolve against a different Hermes root.
        """
        return ["-p", self.profile] if self.profile else []

    def hermes_env(self) -> dict[str, str]:
        """Environment for Hermes subprocesses: the exact profile home unless a profile is named."""
        env = dict(os.environ)
        if self.profile:
            env.pop("HERMES_HOME", None)  # let -p resolve the named profile
        elif self.hermes_home is not None:
            env["HERMES_HOME"] = str(self.hermes_home)
        return env

    @property
    def state_db(self) -> Path | None:
        """Hermes' sessions database, the chat transcript; None standalone or with ``adapter: none``."""
        if self.adapter == "none":
            return None
        db = self.hermes.get("state_db")
        if db:
            return Path(db).expanduser()
        return self.hermes_home / "state.db" if self.hermes_home else None

    def transcript(self, db: Path | str | None = None):
        """The chat transcript every reader of the chat goes through (``ports.Transcript``): Hermes' sessions
        database (``db``, else ``state_db``) when there is one, else ``ports.NullTranscript`` (nothing to read).
        ``hermes.state_db`` set without Hermes running is read too, as it always was."""
        from .ports import NullTranscript
        db = (db or self.state_db) if self.adapter != "none" else None
        if db is None:
            return NullTranscript()
        from .hermes.transcript import HermesTranscript
        return HermesTranscript(self, db)

    def channel(self):
        """Where the engine sends (``ports.Channel``): the Telegram Bot API on Telegram, else Hermes' own senders for
        the configured platform; with ``adapter: none`` off Telegram, nothing (every send is refused)."""
        if self.platform == "telegram":
            from .telegram import TelegramChannel
            return TelegramChannel(self)
        if self.adapter == "none":
            from .ports import NoChannel
            return NoChannel(self.platform)
        from .hermes.channel import HermesChannel
        return HermesChannel(self)

    def companion(self):
        """Who answers a model step (``ports.Companion``): ``agent.command`` when set, else the Hermes adapter's
        one-shot chat; with ``adapter: none`` and no command, nobody (every ask waits, like an outage)."""
        from . import agent
        if self.agent.get("command"):
            return agent.CommandCompanion(self)
        if self.adapter == "none":
            return agent.NoCompanion(self)
        from .hermes.companion import HermesCompanion
        return HermesCompanion(self)

    def source(self, name: str) -> dict:
        return dict(self.sources.get(name) or {})

    def source_enabled(self, name: str) -> bool:
        return bool(self.source(name).get("enabled"))


def _coords(value) -> tuple[float, float] | None:
    if isinstance(value, Mapping) and "lat" in value and "lon" in value:
        return float(value["lat"]), float(value["lon"])
    return None


def from_dict(data: Mapping, *, hermes_home: Path | None = None, config_file: Path | None = None,
              origins: dict | None = None, warnings: list | None = None,
              environ: Mapping[str, str] | None = None) -> Config:
    """Build a Config from a (merged) settings mapping; raises ConfigError when unusable."""
    merged = deep_merge(DEFAULTS, data)
    root = merged.get("root")
    if not isinstance(root, str) or not root.strip():
        raise ConfigError("`root` (the journal directory) is not set")
    windows = merged.get("windows") or {}
    user = merged.get("user") or {}
    comp = merged.get("companion") or {}
    entries_cfg = merged.get("entries") or {}
    for key in ("state_dir", "practices_dir"):
        # Path(5) raises TypeError, which no caller expects from config loading: the tick would die with a traceback.
        value = merged.get(key)
        if value is not None and not isinstance(value, (str, os.PathLike)):
            raise ConfigError(f"`{key}` must be a path (got {value!r})")
    state = _env("JOURNAL_STATE_DIR", environ) or merged.get("state_dir")
    if state:
        state_dir = Path(state).expanduser()
    elif hermes_home is not None:
        state_dir = Path(hermes_home) / "plugin-data" / PLUGIN_ID
    else:
        state_dir = xdg_state_dir(environ)
    practices_env = _env("JOURNAL_PRACTICES_DIR", environ)
    practices = practices_env or merged.get("practices_dir")
    practices_dir = Path(practices).expanduser() if practices else state_dir / "practices"
    if origins is not None and practices_env:
        origins = {**origins, "practices_dir": "env"}
    telegram = dict(merged.get("telegram") or {})
    telegram["chat_id"] = str(telegram.get("chat_id") or "")
    warnings_in: list[str] = []
    platform, chat_id, owners = _channel(merged.get("channel"), telegram["chat_id"], warnings_in)
    hermes = dict(merged.get("hermes") or {})
    warnings = list(warnings or []) + warnings_in
    if data.get("schedule"):  # retired (WP13): not an override any more, so it must not look like one
        if (origins or {}).get("schedule") == "hermes":  # in the profile's config.yaml, which no command here edits
            home = f"--hermes-home {hermes_home} " if hermes_home else "--hermes-home <profile> "
            where = (f"`journal {home}migrate-config --from <the config file> --to <new file>` reads it from the "
                     "plugin settings too and moves it into forked practice files; then "
                     "`hermes config unset plugins.entries.journal.settings.schedule`")
        else:
            where = ("`journal migrate-config --from <the config file> --to <new file>` moves it into forked "
                     "practice files, then remove the key")
        warnings.append("`schedule` in the config is no longer read: when a practice is sent is in its file "
                        f"(`schedule.time`); {where}")
    today_cfg = merged.get("today")
    if today_cfg is False:
        today_cfg = {"enabled": False}  # the natural way to say "off"
    elif today_cfg is None:
        today_cfg = {}
    elif not isinstance(today_cfg, Mapping):
        warnings.append(f"today must be an object like {{\"enabled\": false}} (got {today_cfg!r}); defaults are used")
        today_cfg = {}
    adapter = merged.get("adapter")
    adapter = "auto" if adapter is None else adapter
    if adapter not in ADAPTERS:
        warnings.append(f"adapter must be one of {', '.join(ADAPTERS)} (got {adapter!r}); auto is used"
                        + (": plainchat is not built yet" if adapter == "plainchat" else ""))
        adapter = "auto"
    capture_cfg = merged.get("capture")
    if capture_cfg is None:
        capture_cfg = {}
    elif not isinstance(capture_cfg, Mapping):
        warnings.append(f"capture must be an object like {{\"mode\": \"arm\"}} (got {capture_cfg!r}); "
                        "everything is journaled")
        capture_cfg = {}
    capture_cfg = dict(capture_cfg)
    pack_cfg = merged.get("pack")
    if not isinstance(pack_cfg, Mapping):
        warnings.append(f"pack must be an object like {{\"max_chars\": 32000}} (got {pack_cfg!r}); defaults are used")
        pack_cfg = DEFAULTS["pack"]
    pack_cfg = copy.deepcopy(dict(pack_cfg))
    serve_cfg = merged.get("serve")
    if not isinstance(serve_cfg, Mapping):
        warnings.append(f"serve must be an object like {{\"port\": 8790}} (got {serve_cfg!r}); defaults are used")
        serve_cfg = DEFAULTS["serve"]
    serve_cfg = copy.deepcopy(dict(serve_cfg))
    bind_env = _env("JOURNAL_SERVE_BIND", environ)
    if bind_env:  # an environment override for one run; never written back (like JOURNAL_STATE_DIR)
        serve_cfg["bind"] = bind_env
    try:
        cfg = Config(
            root=Path(root).expanduser(),
            timezone=str(merged.get("timezone") or "UTC"),
            location=str(merged.get("location") or ""),
            coordinates=_coords(merged.get("coordinates")),
            chat_id=chat_id,
            platform=platform,
            owner_ids=owners,
            profile=hermes.get("profile") or None,
            collapse_hours=float(windows.get("collapse_hours", 6)),
            grace_hours=float(windows.get("grace_hours", 2)),
            opener_budget_seconds=int(windows.get("opener_budget_seconds", 240)),
            annotate_after_minutes=int(windows.get("annotate_after_minutes", 20)),
            state_dir=state_dir,
            practices_dir=practices_dir,
            names=Names(user=str(user.get("name") or ""), companion=str(comp.get("name") or "Companion"),
                        emoji=str(comp.get("emoji") or ""), pronouns=str(user.get("pronouns") or "they/them/their"),
                        frontmatter_source=str(entries_cfg.get("frontmatter_source") or "journal")),
            telegram=telegram,
            agent=dict(merged.get("agent") or {}),
            hermes=hermes,
            sources=dict(merged.get("sources") or {}),
            guard=dict(merged.get("guard") or {}),
            prompts=dict(merged.get("prompts") or {}),
            audio=dict(merged.get("audio") or {}),
            images=dict(merged.get("images") or {}),
            reflections=copy.deepcopy(dict(merged.get("reflections") or {})),
            sweep=dict(merged.get("sweep") or {}),
            recovery=dict(merged.get("recovery") or {}),
            capture=capture_cfg,
            conversations=dict(merged.get("conversations") or {}),
            today=dict(today_cfg),
            pack=pack_cfg,
            serve=serve_cfg,
            programs=dict(merged.get("programs") or {}) if isinstance(merged.get("programs"), Mapping) else {},
            hermes_home=Path(hermes_home) if hermes_home is not None else None,
            adapter=adapter,
            config_file=Path(config_file) if config_file else None,
            origins=dict(origins or {}),
            warnings=warnings,
            raw=merged,
        )
    except (TypeError, ValueError) as e:
        raise ConfigError(f"invalid value in configuration: {e}") from None
    try:
        ZoneInfo(cfg.timezone)
    except Exception:
        raise ConfigError(f"unknown timezone {cfg.timezone!r}") from None
    return cfg


def _channel(value, telegram_chat: str, warnings: list) -> tuple[str, str, tuple]:
    """(platform, chat id, owner ids) from the ``channel`` setting. Absent, or Telegram with no chat of its own: the
    Telegram chat of ``telegram.chat_id``, as before there was a channel. A setting that cannot be read is said in
    ``warnings`` and means that default (it never moves the journal to another chat by accident)."""
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        warnings.append(f"channel must be an object like {{\"platform\": \"discord\", \"chat_id\": \"…\"}} "
                        f"(got {value!r}); Telegram (telegram.chat_id) is used")
        value = {}
    platform = value.get("platform")
    platform = "telegram" if platform in (None, "") else platform
    if not isinstance(platform, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", platform.strip().lower()):
        warnings.append(f"channel.platform must be a Hermes platform name such as telegram or discord "
                        f"(got {platform!r}); telegram is used")
        platform, value = "telegram", {}
    platform = platform.strip().lower()
    chat = value.get("chat_id")
    chat = "" if chat is None else str(chat).strip()
    if platform == "telegram" and not chat:
        chat = telegram_chat
    users = value.get("user_ids") or []
    if isinstance(users, (str, int)):
        users = [users]
    if not isinstance(users, (list, tuple)):
        warnings.append(f"channel.user_ids must be a list of ids (got {users!r}); it is left empty")
        users = []
    owners = tuple(dict.fromkeys(str(u).strip() for u in users if str(u).strip()))
    return platform, chat, owners


def resolve_config_file(path: Path | str | None, settings: Mapping, hermes_home: Path | None,
                        environ: Mapping[str, str] | None = None) -> tuple[Path | None, bool]:
    """(path, explicit). An explicit path must exist; the standalone default may be absent."""
    if path:
        return Path(path).expanduser(), True
    env = _env("JOURNAL_CONFIG", environ)
    if env:
        return Path(env).expanduser(), True
    if settings.get("config_file"):
        return Path(str(settings["config_file"])).expanduser(), True
    if hermes_home is None:
        return xdg_config_path(environ), False
    return None, False


def load(path: Path | str | None = None, *, hermes_home: Path | str | None = None,
         settings: Mapping | None = None, environ: Mapping[str, str] | None = None) -> Config:
    """Load the effective configuration.

    ``hermes_home``: the Hermes profile home when running under Hermes (None = standalone, unless
    ``$HERMES_HOME`` is set). ``settings``: the plugin's Hermes settings when the caller already has
    them (the in-process plugin); otherwise they are read from ``config.yaml``.
    """
    if hermes_home is None and _env("HERMES_HOME", environ):
        hermes_home = _env("HERMES_HOME", environ)
    home = Path(hermes_home).expanduser() if hermes_home else None
    warnings: list[str] = []
    if settings is None:
        settings, warnings = read_hermes_settings(home, environ)
    settings = copy.deepcopy(dict(settings or {}))
    file_path, explicit = resolve_config_file(path, settings, home, environ)
    file_data: dict = {}
    if file_path is not None and (explicit or file_path.is_file()):
        file_data = read_json_file(file_path)
        file_data.pop("version", None)
    elif not explicit:
        file_path = None
    # An empty `root`/`state_dir` in settings (the schema default a settings form may save) means unset,
    # never "override the config file with nothing".
    hermes_layer = {k: v for k, v in settings.items()
                    if k != "config_file" and not (k in ("root", "state_dir") and v in ("", None))}
    origins = {k: "default" for k in DEFAULTS}
    for k in file_data:
        origins[k] = "file"
    for k in hermes_layer:
        origins[k] = "hermes"
    merged = deep_merge(file_data, hermes_layer)
    return from_dict(merged, hermes_home=home, config_file=file_path, origins=origins,
                     warnings=warnings, environ=environ)


# ── validation (doctor) ──────────────────────────────────────────────────────

_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def problems(cfg: Config) -> list[str]:
    """Human-readable problems that don't stop loading but would stop the journal working."""
    out = []
    paths = {"root": cfg.root, "state_dir": cfg.state_dir}
    if cfg.practices_dir != cfg.state_dir / "practices":  # the default follows state_dir: one warning is enough
        paths["practices_dir"] = cfg.practices_dir
    for key, path in paths.items():
        # The cron tick, the gateway and a shell run from different directories, so each would use its own.
        if not Path(path).is_absolute():
            out.append(f"{key} is relative ({path}): each process would resolve it against its own working "
                       "directory; use an absolute path")
    if not cfg.chat_id:
        out.append("telegram.chat_id is not set: prompts cannot be sent and replies are not captured"
                   if cfg.platform == "telegram" else
                   f"channel.chat_id is not set: prompts cannot be sent to {cfg.platform} and replies are not captured")
    elif cfg.platform != "telegram" and not cfg.owner_ids:
        out.append(f"channel.user_ids is empty: everything said in {cfg.platform} chat {cfg.chat_id} is taken as "
                   f"{cfg.names.possessive}; set it to {cfg.names.possessive} own id there unless that chat is a "
                   "direct-message chat with the companion")
    if cfg.collapse_hours <= 0:
        out.append("windows.collapse_hours must be positive")
    if cfg.grace_hours < MIN_GRACE_HOURS:
        out.append(f"windows.grace_hours is {cfg.grace_hours:g}: under {MIN_GRACE_HOURS:g} h (one tick) a scheduled "
                   "prompt can be missed and never sent")
    try:
        quiet = float(cfg.conversations.get("quiet_minutes", 45))
        if math.isnan(quiet):
            raise ValueError("nan")
        if quiet <= 0:
            out.append("conversations.quiet_minutes must be positive")
        elif quiet > QUIET_MAX:
            out.append(f"conversations.quiet_minutes is over a year ({quiet:g}); {QUIET_MAX:g} is used")
    except OverflowError:  # (an integer literal too big for a float)
        raw = cfg.conversations.get("quiet_minutes")
        out.append(f"conversations.quiet_minutes is over a year; {QUIET_MAX:g} is used" if raw > 0
                   else "conversations.quiet_minutes must be positive")
    except (TypeError, ValueError):
        out.append(f"conversations.quiet_minutes must be a number (got {cfg.conversations.get('quiet_minutes')!r})")
    prefix = cfg.conversations.get("opt_out_prefix", "/x")
    why = opt_out_problem(prefix)
    if why:
        out.append(f"conversations.opt_out_prefix {why} (got {prefix!r}); /x is used instead")
    mode = cfg.capture.get("mode", "everything")
    if not _capture_mode_of(mode):
        out.append(f"capture.mode must be one of {', '.join(CAPTURE_MODES)} (got {mode!r}); everything is used")
    out += today_problems(cfg)
    out += pack_problems(cfg)
    out += serve_problems(cfg)
    if "settle_minutes" in cfg.programs and _positive(cfg.programs["settle_minutes"]) is None:
        out.append(f"programs.settle_minutes must be a positive number (got {cfg.programs['settle_minutes']!r}); "
                   "2 is used")
    cmd = cfg.agent.get("command")
    if cmd is not None and not (isinstance(cmd, list) and all(isinstance(x, str) for x in cmd)):
        out.append("agent.command must be a list of strings")
    acmd = cfg.audio.get("command")
    if acmd is not None and not (isinstance(acmd, list) and all(isinstance(x, str) for x in acmd)):
        out.append("audio.command must be a list of strings")
    if "max_mb" in cfg.images and _positive(cfg.images["max_mb"]) is None:
        out.append(f"images.max_mb must be a positive number (got {cfg.images['max_mb']!r}); 20 is used")
    if "describe" in cfg.images and not isinstance(cfg.images["describe"], bool):
        out.append(f"images.describe must be true or false (got {cfg.images['describe']!r}); it stays off")
    for which in ("day", "week"):
        spec = cfg.reflections.get(which) or {}
        if spec.get("enabled", True) and not _TIME.match(str(spec.get("fallback_time", "04:00"))):
            out.append(f"reflections.{which}.fallback_time must be HH:MM (got {spec.get('fallback_time')!r})")
    return out


def _capture_mode_of(value) -> str | None:
    """``value`` as a capture mode (case and spaces aside), else None."""
    mode = value.strip().lower() if isinstance(value, str) else None
    return mode if mode in CAPTURE_MODES else None


def capture_mode(cfg: "Config") -> str:
    """What gets journaled: ``everything`` (the default: every chat thread is journal material, saved into the
    entry it started from or a note of its own) or ``arm`` (the journal is one arm of a general agent: replies to
    prompts, /j notes, program answers and the companion's explicit saves are journaled, with the conversation that
    follows them, and ordinary chat is not). An unusable value means ``everything``, today's behaviour, and doctor
    says so: a typo must not quietly stop his conversations being kept."""
    return _capture_mode_of(cfg.capture.get("mode", "everything")) or "everything"


def _positive(value) -> float | None:
    """``value`` as a positive finite number, else None (a bool is not a number here)."""
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 and n == n and n != float("inf") else None


def today_max_chars(cfg: "Config") -> int:
    """``today.max_chars`` as a usable cap (at least 200), else the default 1200 (doctor reports a bad value)."""
    n = _positive(cfg.today.get("max_chars", 1200))
    return max(200, int(n)) if n is not None else 1200


def settle_minutes(cfg: "Config") -> float:
    """``programs.settle_minutes`` as a positive number, else the default 2 (doctor reports a bad value)."""
    return _positive(cfg.programs.get("settle_minutes", 2)) or 2.0


def today_cache_minutes(cfg: "Config") -> float:
    """``today.cache_minutes`` as a positive number, else the default 30 (doctor reports a bad value)."""
    return _positive(cfg.today.get("cache_minutes", 30)) or 30.0


def today_problems(cfg: "Config") -> list[str]:
    out = []
    if _positive(cfg.today.get("max_chars", 1200)) is None:
        out.append(f"today.max_chars must be a positive number (got {cfg.today.get('max_chars')!r}); 1200 is used")
    elif _positive(cfg.today.get("max_chars", 1200)) < 200:
        out.append(f"today.max_chars {cfg.today.get('max_chars')!r} is too small for the block; 200 is used")
    if _positive(cfg.today.get("cache_minutes", 30)) is None:
        out.append(f"today.cache_minutes must be a positive number (got {cfg.today.get('cache_minutes')!r}); "
                   "30 is used")
    if "enabled" in cfg.today and not isinstance(cfg.today["enabled"], bool):
        out.append(f"today.enabled must be true or false (got {cfg.today['enabled']!r}); the block stays on")
    sources = cfg.today.get("sources", [])
    if not isinstance(sources, list) or not all(isinstance(x, str) for x in sources):
        out.append("today.sources must be a list of names; every source is used")
    else:
        known = TODAY_SOURCES
        unknown = [x for x in sources if x not in known]
        if unknown:
            out.append(f"today.sources has unknown names {unknown} (known: {', '.join(known)}); they are ignored")
    return out


# ── the context pack ─────────────────────────────────────────────────────────

PACK_MIN_CHARS = 8000      # under this the pack's fixed parts (where things stand, two openers, the pointers) crowd out his words
PACK_MAX_ENTRIES = 50
PACK_MAX_LOOKBACK = 120    # days: the ledger keeps prompts this long (``tick.KEEP_DAYS``)


def _whole(value) -> int | None:
    """``value`` as a positive whole number (``6`` or ``6.0``), else None."""
    n = _positive(value)
    return int(n) if n is not None and n == int(n) else None


def pack_max_chars(cfg: "Config") -> int:
    """``pack.max_chars``: at least ``PACK_MIN_CHARS``; an unusable value means the default 32000 (doctor says so)."""
    n = _whole(cfg.pack.get("max_chars", 32000))
    return max(PACK_MIN_CHARS, n) if n is not None else 32000


def pack_entries(cfg: "Config") -> int:
    """``pack.entries``: how many of his newest entries the pack carries, whatever their age (1 to 50; default 6)."""
    n = _whole(cfg.pack.get("entries", 6))
    return min(PACK_MAX_ENTRIES, n) if n is not None else 6


def pack_lookback_days(cfg: "Config") -> int:
    """``pack.lookback_days``: how far back unanswered prompts are counted and his chat is read for "last heard"
    (1 to 120; default 14)."""
    n = _whole(cfg.pack.get("lookback_days", 14))
    return min(PACK_MAX_LOOKBACK, n) if n is not None else 14


def pack_returning_days(cfg: "Config") -> int:
    """``pack.returning_days``: the gap, in days since he was last heard from on the record, from which the opener
    is asked to welcome him back (default 3)."""
    n = _whole(cfg.pack.get("returning_days", 3))
    return n if n is not None else 3


def pack_pointers(cfg: "Config") -> list[str]:
    """``pack.pointers``: extra "where to look further" lines, as written (blank and non-text ones left out)."""
    value = cfg.pack.get("pointers") or []
    if not isinstance(value, list):
        return []
    return [" ".join(x.split()) for x in value if isinstance(x, str) and x.strip()]


def pack_problems(cfg: "Config") -> list[str]:
    out = []
    for key, default, fn in (("max_chars", 32000, pack_max_chars), ("entries", 6, pack_entries),
                             ("lookback_days", 14, pack_lookback_days), ("returning_days", 3, pack_returning_days)):
        if key not in cfg.pack:
            continue
        if _whole(cfg.pack[key]) is None:
            out.append(f"pack.{key} must be a positive whole number (got {cfg.pack[key]!r}); {default} is used")
        elif fn(cfg) != _whole(cfg.pack[key]):
            out.append(f"pack.{key} {cfg.pack[key]!r} is out of range; {fn(cfg)} is used")
    pointers = cfg.pack.get("pointers")
    if pointers is not None and not (isinstance(pointers, list) and all(isinstance(x, str) for x in pointers)):
        out.append("pack.pointers must be a list of lines (text); the ones that are not text are left out")
    return out


def serve_port(cfg: "Config") -> int | None:
    """``serve.port`` as a usable TCP port, else None (doctor and ``journal serve`` say why)."""
    port = cfg.serve.get("port", 8790)
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536:
        return None
    return port


def serve_public_port(cfg: "Config") -> int | None:
    """``serve.public_port`` as a usable TCP port, else None: unset, 443 spelled out, or unusable (doctor says
    which), so the site's URLs carry no port."""
    port = cfg.serve.get("public_port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 < port < 65536 or port == 443:
        return None
    return port


def serve_host_ok(host) -> bool:
    r"""Whether ``serve.host`` is a bare host name (``journal.example.org``): no scheme, port, path, space or other
    URL delimiter (``#``, ``?``, ``@``, ``\``, brackets), so a URL built on it names that host and the path after it."""
    return isinstance(host, str) and not re.search(r"[/:\s#?@\\\[\]]", host)


def serve_site(cfg: "Config") -> str:
    """The web app's base URL as a browser reaches it: ``https://<serve.host>`` when a host is set (with
    ``:<serve.public_port>`` when the site is published on a port other than 443), else the bind address (a dev
    or quarantine setup). The one place it is built: serve's Origin check and pairing link (``serve.auth.site``)
    and the chat's ``/web`` all read it here."""
    host = (cfg.serve.get("host") or "").strip()
    if host:
        public = serve_public_port(cfg)
        return f"https://{host}:{public}" if public else f"https://{host}"
    bind = str(cfg.serve.get("bind") or "127.0.0.1")
    bind = f"[{bind}]" if ":" in bind else bind
    return f"http://{bind}:{serve_port(cfg) or 8790}"


def serve_proxies(cfg: "Config") -> list:
    """``serve.trusted_proxies`` as networks; an entry that is not a CIDR is skipped (doctor names it)."""
    import ipaddress
    out = []
    listed = cfg.serve.get("trusted_proxies") or []
    for item in listed if isinstance(listed, list) else []:
        try:
            out.append(ipaddress.ip_network(str(item), strict=False))
        except ValueError:
            continue
    return out


SOCKET_PATH_MAX = 107   # bytes: Linux's sun_path is 108, with the terminating NUL


def serve_socket(cfg: "Config") -> Path | None:
    """``serve.socket`` as a usable socket path, normalised (``a/../b`` is ``b``, so the server, the unit's
    ``RuntimeDirectory=`` and doctor name one path and no stray directory is made), else None: unset, or not
    absolute, too long for ``AF_UNIX``, or not a string (doctor says which; ``journal serve`` refuses to start rather
    than run without it)."""
    raw = cfg.serve.get("socket")
    if not isinstance(raw, str) or not raw.startswith("/") or "\0" in raw:
        return None
    path = os.path.normpath(raw)
    if len(os.fsencode(path)) > SOCKET_PATH_MAX:
        return None
    return Path(path)


def serve_tailscale_users(cfg: "Config") -> frozenset:
    """``serve.tailscale_users`` as the logins that are signed in on the socket: strings with no space or comma
    (an identity header carries one login), trimmed. Anything else is skipped and named by doctor; a value that
    is not a list signs no one in (fail closed)."""
    listed = cfg.serve.get("tailscale_users")
    if not isinstance(listed, list):
        return frozenset()
    return frozenset(x.strip() for x in listed if _login_ok(x))


def _login_ok(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not re.search(r"[\s,\x00-\x1f\x7f]", value.strip())


def serve_problems(cfg: "Config") -> list[str]:
    out = []
    if serve_port(cfg) is None:
        out.append(f"serve.port must be a port number 1-65535 (got {cfg.serve.get('port')!r})")
    public = cfg.serve.get("public_port")
    if public is not None and (isinstance(public, bool) or not isinstance(public, int) or not 0 < public < 65536):
        out.append(f"serve.public_port must be a port number 1-65535, or absent for 443 (got {public!r}); "
                   "it is ignored")
    bind = cfg.serve.get("bind")
    if not isinstance(bind, str) or not bind.strip():
        out.append(f"serve.bind must be an address like 127.0.0.1 (got {bind!r})")
    host = cfg.serve.get("host") or ""
    if not serve_host_ok(host):
        out.append(f"serve.host must be a bare host name like journal.example.org (got {host!r})")
    if not isinstance(cfg.serve.get("allow_write", True), bool):
        out.append(f"serve.allow_write must be true or false (got {cfg.serve.get('allow_write')!r}); "
                   "anything but true leaves the server read-only")
    listed = cfg.serve.get("trusted_proxies") or []
    if not isinstance(listed, list):
        out.append(f"serve.trusted_proxies must be a list of CIDRs (got {listed!r}); no proxy is trusted")
    elif len(serve_proxies(cfg)) != len(listed):
        out.append(f"serve.trusted_proxies has entries that are not CIDRs ({listed!r}); they are ignored")
    sock = cfg.serve.get("socket")
    if sock is not None and serve_socket(cfg) is None:
        out.append(f"serve.socket must be an absolute path of at most {SOCKET_PATH_MAX} bytes, like "
                   f"/run/user/1000/journal-serve/serve.sock (got {sock!r}); journal serve will not start with it")
    users = cfg.serve.get("tailscale_users")
    if users is not None and not isinstance(users, list):
        out.append(f"serve.tailscale_users must be a list of Tailscale logins like [\"someone@example.com\"] "
                   f"(got {users!r}); no one is signed in by identity")
    elif isinstance(users, list) and not all(_login_ok(x) for x in users):
        out.append(f"serve.tailscale_users has entries that are not logins ({users!r}); they are ignored")
    return out


# The journal's own slash commands (and /journal, the long form of /j): an opt-out prefix must not take one over.
# ``start`` … ``status`` are the names the spec gave the practice and program commands. Hermes owns ``/start``,
# ``/pause`` and ``/status``, so the plugin registers ``journal-start`` (and routes ``/start <id>`` to it), and the
# program commands are ``/next``, ``/skip``, ``/back``, ``/break`` (pause) and ``/program`` (status).
# The built-in rituals' ids (each keeps its own command) are added from the packaged files by ``opt_out_problem``; a
# user practice has no command of its own, only ``/start <id>``, so its id does not reserve a name here (the library
# refuses a practice named after the opt-out).
RESERVED_COMMANDS = ("j", "journal", "start", "next", "skip", "back", "pause", "status", "journal-start", "break",
                     "abandon", "program", "web", "morning-journal", "evening-journal")
# Hermes' own slash commands and aliases (hermes_cli/commands.py COMMAND_REGISTRY, as of Oct 2026).
HERMES_COMMANDS = (
    "agents", "approvals", "approve", "battery", "bg", "blueprint", "bp", "branch", "browser", "btw", "bundles",
    "busy", "clear", "codex_runtime", "commands", "compact", "compose", "compress", "config", "context", "copy",
    "cron", "ctx", "curator", "debug", "deny", "diff", "egress", "exit", "export", "fast", "focus", "footer",
    "fork", "gateway", "generate_pet", "goal", "handoff", "hatch", "hb", "heartbeat", "help", "history", "image",
    "import", "indicator", "init", "insights", "journey", "kanban", "learn", "learning", "login", "loop", "memory",
    "memory_graph", "moa", "model", "new", "palette", "paste", "pause", "personality", "pet", "plan", "platform",
    "platforms", "plugins", "proactive", "profile", "prompt", "q", "queue", "quit", "reasoning", "redraw", "refine",
    "reload", "reload_mcp", "reload_skills", "reset", "restart", "resume", "retry", "review", "rollback", "s",
    "save", "sb", "sessions", "set_home", "sethome", "short", "skills", "skin", "snap", "snapshot", "start",
    "status", "statusbar", "steer", "stop", "subgoal", "subscription", "suggest", "suggestions", "tasks",
    "timestamps", "title", "tools", "toolsets", "topic", "topup", "ts", "undo", "update", "upgrade", "usage", "v",
    "verbose", "version", "voice", "wake", "whoami", "worktree", "yolo")


def opt_out_problem(prefix) -> str:
    """Why ``conversations.opt_out_prefix`` can't be used, or ''.

    The same verdict in every process: Hermes' names come from the dated table alone, never its live
    ``COMMAND_REGISTRY`` (which only the gateway has loaded). Otherwise the gateway could fall back to /x while the
    cron tick honoured the configured prefix, and the two would disagree on which messages are off the record.
    Cost: a command Hermes adds after the table's date can be taken as the prefix until the table is updated (the
    command then belongs to the opt-out; doctor cannot tell)."""
    if not isinstance(prefix, str) or not re.fullmatch(r"/[A-Za-z0-9_]{1,32}", prefix or ""):
        return "must be a slash command like /x"
    name = prefix[1:].lower()
    if name.replace("-", "_") in HERMES_COMMANDS:  # first: /start and /status are Hermes' before they are ours
        return "would take over one of Hermes' own commands"
    from . import library  # (library imports this module: at call time only)
    try:
        rituals = library.builtin_ids()
    except RuntimeError:  # the packaged files are unreadable: doctor says so; /x itself must not depend on them
        rituals = ()
    taken = (*RESERVED_COMMANDS, *rituals)
    if name in taken or name.replace("_", "-") in taken:
        return "would take over one of the journal's own commands"
    return ""


QUIET_MAX = 525_600.0  # minutes (a year): ``quiet_minutes`` above it is read as it, so window arithmetic never overflows


def quiet_minutes(cfg: "Config") -> float:
    """``conversations.quiet_minutes`` as a positive number, else the default 45 (doctor reports a bad value). A
    value above ``QUIET_MAX`` (infinity included) is read as ``QUIET_MAX``: an off-the-record window ends at the
    first silence this long, and a huge one made ``timedelta`` (or a time plus it) raise, in the hook's window check
    too, whose failure path does not consider the window. Capped rather than reset to 45, which would shorten the
    privacy he asked for."""
    raw = cfg.conversations.get("quiet_minutes", 45)
    try:
        value = float(raw)
    except OverflowError:  # an integer literal too big for a float is a huge number, not a missing one
        return QUIET_MAX if isinstance(raw, int) and raw > 0 else 45.0
    except (TypeError, ValueError):
        return 45.0
    if not value > 0:  # (NaN too)
        return 45.0
    return min(value, QUIET_MAX)


def opt_out_prefix(cfg: "Config") -> str:
    """The opt-out command in use: the configured one when it is valid, else /x."""
    prefix = cfg.conversations.get("opt_out_prefix") or "/x"
    return prefix if not opt_out_problem(prefix) else "/x"


# ── writes (travel) ──────────────────────────────────────────────────────────

# Keys the environment may override (paths only; never written back). ``state_dir`` keeps its older behaviour
# of not being recorded as env-owned, since nothing writes it.
ENV_KEYS = {"practices_dir": "JOURNAL_PRACTICES_DIR"}

SettingsWriter = Callable[[str, Any], None]


def file_target(cfg: Config) -> Path:
    """The config file a write goes to: the one in use, else the standalone default (created on the first write)."""
    return Path(cfg.config_file) if cfg.config_file else xdg_config_path()


def write_values(cfg: Config, values: Mapping[str, Any], hermes_writer: SettingsWriter | None = None) -> dict[str, str]:
    """Persist top-level keys to the layer that owns each one; returns {key: layer written}.

    A key set in Hermes settings is written there (needs ``hermes_writer``); a key from the config
    file goes back to that file; a key nobody set yet goes to Hermes settings when available,
    otherwise to the config file (created at the standalone default path if needed).
    """
    written: dict[str, str] = {}
    file_updates: dict[str, Any] = {}
    for key, value in values.items():
        origin = cfg.origins.get(key, "default")
        if origin == "env":
            raise ConfigError(f"`{key}` comes from the environment ({ENV_KEYS[key]}); change it there, not in a config layer")
        if origin == "hermes" or (origin == "default" and hermes_writer is not None and cfg.config_file is None):
            if hermes_writer is None:
                raise HermesOwned(key, profile_from_home(cfg.hermes_home))
            hermes_writer(key, value)
            written[key] = "hermes"
        else:
            file_updates[key] = value
            written[key] = "file"
    if file_updates:
        path = file_target(cfg)
        data = read_json_file(path) if Path(path).is_file() else {}
        data.update(file_updates)
        write_json_file(path, data)
    return written


# ── writes from the web app (``PATCH /api/v1/settings``) ─────────────────────

def _number(value, *, minimum: float = 0.0, whole: bool = False, inclusive: bool = False,
            maximum: float = math.inf) -> bool:
    """A finite number in ``(minimum, maximum]`` (``[minimum, maximum]`` with ``inclusive``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if whole and int(value) != value:
        return False
    return (value >= minimum if inclusive else value > minimum) and value <= maximum


MIN_GRACE_HOURS = 0.25   # the cron tick runs every 15 minutes: a send slot's grace must span at least one tick


def _flag(value) -> bool:
    return isinstance(value, bool)


def _clock(value) -> bool:
    return isinstance(value, str) and bool(_TIME.match(value))


TODAY_SOURCES = ("standing", "practices", "programs", "entries", "reflection", "tasks", "weather")


def _zone(value) -> bool:
    try:
        ZoneInfo(value)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError, ValueError, TypeError
        return False
    return True


def _coordinates(value) -> bool:
    return (isinstance(value, Mapping) and set(value) <= {"lat", "lon", "place", "source"}
            and isinstance(value.get("source", ""), str)
            and _number(value.get("lat"), minimum=-90, inclusive=True) and value["lat"] <= 90
            and _number(value.get("lon"), minimum=-180, inclusive=True) and value["lon"] <= 180
            and isinstance(value.get("place", ""), str))


_REFLECTION = {"enabled": _flag, "fallback_time": _clock}

# What the web app may change, as a tree: a function is a leaf that says whether a value is acceptable, a dict
# lists the keys below it. Anything not here is refused (telegram, guard, hermes, agent, serve, root, state_dir,
# practices_dir and every secret never are).
WEB_WRITABLE: dict[str, Any] = {
    "location": lambda v: isinstance(v, str) and 0 < len(v.strip()) <= 200,
    "timezone": _zone,
    "coordinates": _coordinates,
    # Ranges that keep the rituals working: a grace shorter than the tick (``MIN_GRACE_HOURS``) can miss every send
    # slot, and a window of 1e300 hours overflows ``timedelta`` in the hook.
    "windows": {"collapse_hours": lambda v: _number(v, minimum=0.25, inclusive=True, maximum=72),
                "grace_hours": lambda v: _number(v, minimum=MIN_GRACE_HOURS, inclusive=True, maximum=24),
                "opener_budget_seconds": lambda v: _number(v, whole=True, maximum=900),
                "annotate_after_minutes": lambda v: _number(v, whole=True, inclusive=True, maximum=10_080)},
    "reflections": {"day": _REFLECTION, "week": _REFLECTION},
    "conversations": {"quiet_minutes": lambda v: _number(v, maximum=1_440)},
    "programs": {"settle_minutes": lambda v: _number(v, maximum=1_440)},
    "today": {"enabled": _flag, "max_chars": lambda v: _number(v, minimum=200, whole=True, inclusive=True, maximum=20_000),
              "cache_minutes": lambda v: _number(v, maximum=1_440),
              "sources": lambda v: isinstance(v, list) and all(x in TODAY_SOURCES for x in v)},
}
WEB_WHOLE = ("location", "timezone", "coordinates")  # written as given, never merged into what is there


def effective_today(cfg: "Config") -> dict:
    """The ``today`` settings in force: the defaults under what ``cfg.today`` holds (``today: false`` in a file reads
    as ``{"enabled": false}`` there, with the rest at its defaults)."""
    return {**copy.deepcopy(DEFAULTS["today"]), **cfg.today}


def nest(raw: Mapping) -> dict:
    """``{"conversations.quiet_minutes": 30}`` as ``{"conversations": {"quiet_minutes": 30}}``."""
    out: dict[str, Any] = {}
    for key, value in raw.items():
        *head, last = str(key).split(".")
        node = out
        for part in head:
            below = node.get(part)
            if not isinstance(below, dict):
                below = node[part] = {}
            node = below
        if isinstance(value, Mapping) and isinstance(node.get(last), dict):
            node[last] = deep_merge(node[last], value)
        else:
            node[last] = value
    return out


def web_patch(raw: Mapping) -> tuple[dict, list[str], list[str]]:
    """Check a settings change from the web app: ``(patch, refused, problems)``. ``patch`` is the change as nested
    top-level keys holding only what changes (``{"windows": {"collapse_hours": 5}}``); dotted keys are accepted as
    their nested form. ``refused`` names the keys the web may not write; ``problems`` says what is wrong with a
    value. A change with either of the two writes nothing."""
    refused: list[str] = []
    problems: list[str] = []

    def walk(schema, value, path: str):
        if callable(schema):
            if schema(value):
                return value
            problems.append(f"{path}: {value!r} is not an allowed value")
            return None
        if not isinstance(value, Mapping):
            problems.append(f"{path} must be an object")
            return None
        out = {}
        for name, v in value.items():
            if name not in schema:
                refused.append(f"{path}.{name}")
                continue
            got = walk(schema[name], v, f"{path}.{name}")
            if got is not None:
                out[name] = got
        return out or None

    patch: dict[str, Any] = {}
    for top, value in nest(raw).items():
        if top not in WEB_WRITABLE:
            refused.append(top)
            continue
        got = walk(WEB_WRITABLE[top], value, top)
        if got is not None:
            patch[top] = got.strip() if isinstance(got, str) else got
    return patch, refused, problems


def _layer_value(cfg: Config, key: str, layer: str):
    """The value of top-level ``key`` in the layer ``write_values`` will write it to."""
    if layer == "hermes":
        return copy.deepcopy(cfg.raw.get(key))
    path = file_target(cfg)
    data = read_json_file(path) if Path(path).is_file() else {}
    return copy.deepcopy(data.get(key))


def check_writable(cfg: Config, patch: Mapping[str, Any], hermes_writer: SettingsWriter | None = None) -> dict[str, str]:
    """The layer each top-level key of a ``web_patch`` would be written to ({key: layer}), or ``ConfigError`` when
    one cannot be: Hermes-owned with no writer (the message carries the ``hermes config set`` line), env-owned, or
    bound for a config file that does not exist under Hermes (nothing reads a file made there)."""
    layers: dict[str, str] = {}
    for key, part in patch.items():
        origin = cfg.origins.get(key, "default")
        name = f"{key}.{next(iter(part))}" if isinstance(part, Mapping) and part and key not in WEB_WHOLE else key
        if origin == "env":
            raise ConfigError(f"`{key}` comes from the environment ({ENV_KEYS.get(key, key)}); change it there, "
                              "not in a config layer")
        no_file = hermes_writer is None and cfg.config_file is None and cfg.hermes_home is not None
        if hermes_writer is None and (origin == "hermes" or no_file):
            raise HermesOwned(name, profile_from_home(cfg.hermes_home))
        layers[key] = "hermes" if origin == "hermes" else "file"
    return layers


def patch_values(cfg: Config, patch: Mapping[str, Any], hermes_writer: SettingsWriter | None = None) -> dict[str, str]:
    """``write_values`` for a ``web_patch``: a nested change is merged into the value its layer already holds
    (``windows.collapse_hours`` leaves the other windows alone) and written as the top-level key. Returns
    {key: layer}; ``ConfigError`` (nothing written) as ``check_writable`` says."""
    layers = check_writable(cfg, patch, hermes_writer)
    values: dict[str, Any] = {}
    for key, part in patch.items():
        if isinstance(part, Mapping) and key not in WEB_WHOLE:
            current = _layer_value(cfg, key, layers[key])
            if key == "today" and current is False:
                current = {"enabled": False}  # as ``from_dict`` reads it: a change to one key keeps the block off
            values[key] = deep_merge(current if isinstance(current, Mapping) else {}, part)
        else:
            values[key] = part
    return write_values(cfg, values, hermes_writer)
