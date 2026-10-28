"""Where the plugin meets Hermes: the active profile home, plugin settings, and spawned processes.

Only documented Hermes surfaces are used: ``ctx.get_config`` / ``ctx.set_config`` for this plugin's
settings, ``hermes_constants.get_hermes_home`` for the active (possibly multiplexed) profile home,
``agent.secret_scope.get_secret`` to hand a declared token to processes the plugin spawns.
Everything degrades to standalone behaviour when Hermes isn't importable (tests, plain Python).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from .. import config as jconfig

log = logging.getLogger("journal.hermes")
_ctx = None
_MISSING = object()


def bind(ctx) -> None:
    global _ctx
    _ctx = ctx


def unbind() -> None:
    global _ctx
    _ctx = None


def hermes_home() -> Path | None:
    """The active profile home. Hermes' resolver (multiplex-aware) only when the plugin is actually
    loaded by Hermes; otherwise ``$HERMES_HOME`` or nothing (importable Hermes code alone proves nothing)."""
    if _ctx is not None:
        try:
            from hermes_constants import get_hermes_home
            return Path(get_hermes_home())
        except Exception:
            pass
    env = os.environ.get("HERMES_HOME", "").strip()
    return Path(env) if env else None


def settings() -> dict | None:
    """This plugin's Hermes settings through the plugin context; None when there is no context."""
    if _ctx is None:
        return None
    out: dict[str, Any] = {}
    for key in jconfig.TOP_LEVEL_KEYS:
        try:
            value = _ctx.get_config(key, _MISSING)
        except Exception:
            continue
        if value is not _MISSING and value is not None:
            out[key] = json.loads(json.dumps(value))  # detach from Hermes' cached config
    return out


def load_config() -> jconfig.Config:
    return jconfig.load(hermes_home=hermes_home(), settings=settings())


_FAILED: list[str] = []  # the last configuration failure the dispatch hook logged


def config_for_event(log) -> jconfig.Config | None:
    """The configuration for one incoming message, or None when it cannot load. The hook loads it for every message
    on every platform (only then does it know which platform is the journal's), so a broken configuration is logged
    once per distinct failure, with its traceback, rather than for each message; doctor says it in full."""
    try:
        cfg = load_config()
    except Exception as e:
        why = f"{type(e).__name__}: {e}"
        if _FAILED[-1:] != [why]:
            log.exception("journal: the configuration could not load; messages pass through uncaptured until it "
                          "does (logged once)")
            _FAILED[:] = [why]
        return None
    _FAILED.clear()
    return cfg


def settings_writer():
    """``ctx.set_config`` when available, so travel updates land in Hermes settings when they live there."""
    if _ctx is None:
        return None

    def write(key: str, value: Any) -> None:
        _ctx.set_config(key, value)
    return write


def src_dir() -> Path:
    """The directory holding the ``journal`` package this process imported."""
    return Path(__file__).resolve().parents[2]


def child_env(cfg: jconfig.Config) -> dict[str, str]:
    """Environment for a journal process spawned from the plugin (a manual /morning send).

    The gateway's interpreter may lack PyYAML on a child's path, so the settings travel as JSON; a
    token Hermes holds for this profile travels as the variable the config names.
    """
    env = dict(os.environ)
    src = str(src_dir())
    env["PYTHONPATH"] = os.pathsep.join([src, *[p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]])
    home = hermes_home()
    if home is not None:
        env["HERMES_HOME"] = str(home)
    snap = settings()
    if snap is not None:
        env["JOURNAL_HERMES_SETTINGS"] = json.dumps(snap)
    name = cfg.telegram.get("token_env") or ""
    if name and not env.get(name):
        try:
            from agent.secret_scope import get_secret
            value = get_secret(name)
            if value:
                env[name] = value
        except Exception:
            pass
    return env


def session_value(name: str) -> str | None:
    """A value of the gateway session this call runs in (``HERMES_SESSION_<name>``: ``CHAT_ID``, ``USER_ID`` …), or
    None: outside the gateway, or a Hermes that does not bind it. The ``pre_llm_call`` hook gets the platform but not
    the chat, and the journal's turn is one chat's (``channel.turn_ours``)."""
    try:
        from gateway.session_context import get_session_env
        value = get_session_env(f"HERMES_SESSION_{name}", "")
    except Exception:
        return None
    return str(value) if value else None


def python() -> str:
    return sys.executable
