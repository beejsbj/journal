"""The companion may not change Hermes settings, schedules, skills or the journal engine.

Enforced through Hermes' ``pre_tool_call`` hook in the profiles where the plugin is enabled. It is
a guardrail against a "helpful" model fixing infrastructure, not a security boundary: the agent
still runs as the same Unix user, and a shell denylist cannot be complete. Reads stay allowed so
the companion can investigate and report; changes go to a person.

What is protected (``guard.*`` settings):

- always: the Hermes data root, the journal state dir and the config file;
- ``protect_journal_root``: the journal directory (the engine owns those files);
- ``protected_paths``: anything else; ``allowed_write_paths`` carves out exceptions
  (``<HERMES_HOME>/cache/scratch`` is always allowed);
- ``protected_tokens``: substrings that make a write-ish shell command suspicious;
- ``restrict_hermes_cli``: ``hermes`` subcommands other than chat, cron list/status/runs, status;
- ``block_system_commands``: systemctl, crontab, loginctl, journalctl --vacuum;
- ``block_execute_code``: the ``execute_code`` tool entirely;
- ``skill_manage`` and ``cronjob_manage`` writes are always refused;
- ``journal config set`` (and ``python -m journal ... config set``, with or without ``--config``) in a shell: it
  rewrites the config file through the journal's own code, so no path or write token shows in the command and the
  protected-path check cannot see it. ``config list`` and ``config get`` are reads and stay allowed.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from .config import Config, hermes_data_root

_HERMES_ALLOWED = re.compile(
    r"^\s*(?:\S*/)?hermes\s+(?:-p\s+\S+\s+)?(?:chat\b|cron\s+(?:list|status|runs)\b|status\b|--version\b|version\b)"
)
_WRITE_TOKENS = re.compile(
    r"((?<![0-9&])>(?!\s*/dev/null)|\btee\b|\bsed\s+-i|\bcp\b|\bmv\b|\brm\b|\bln\b|\bchmod\b|\bchown\b|\btruncate\b|\binstall\b"
    r"|\brsync\b|\bpython\d?\b|\bperl\b|\bdd\b|\bgit\s+(?:commit|push|checkout|reset|apply|am|stash|merge|rebase)\b)"
)
# The journal CLI's write action on the settings (``journal [--config F] config set KEY VALUE``, also
# ``python -m journal ...`` and ``uv run journal ...``): the config file is rewritten by the journal itself.
# ``journal`` may follow a quote (``bash -c "journal config set ..."``), be quoted itself (``"journal" config set``) and
# may be a module (``-m journal.cli``); ``config`` and ``set`` may be quoted too.
_CONFIG_SET = re.compile(r"""(?<![\w.-])journal(?:\.\w+)*['"]?(?:\s+\S+)*?\s+['"]?config['"]?\s+['"]?set\b""")
_SYSTEM = re.compile(r"\b(systemctl|crontab|loginctl|journalctl\s+--vacuum)\b")


def refusal(cfg: Config) -> str:
    n = cfg.names
    esc = (cfg.guard.get("escalate_to") or "").strip()
    ask = f", or ask {esc} to look into it" if esc else ""
    return (f"Blocked by the journal guard: {n.companion} doesn't change Hermes settings, cron jobs, skills, "
            f"plugins, model routing, or the journal engine. Tell {n.user or 'the user'} what you found{ask}.")


def _real(p) -> str:
    return os.path.realpath(os.path.expanduser(str(p)))


def protected_roots(cfg: Config) -> list[str]:
    g = cfg.guard
    roots = [str(p) for p in (g.get("protected_paths") or [])]
    if cfg.hermes_home is not None:
        roots.append(str(hermes_data_root(cfg.hermes_home)))
    roots.append(str(cfg.state_dir))
    if cfg.config_file:
        roots.append(str(cfg.config_file))
    if g.get("protect_journal_root", True):
        roots.append(str(cfg.root))
    out = []
    for r in roots:
        for form in (os.path.expanduser(r), _real(r)):
            if form and form not in out:
                out.append(form.rstrip("/") or "/")
    return out


def allowed_roots(cfg: Config) -> list[str]:
    allowed = [str(p) for p in (cfg.guard.get("allowed_write_paths") or [])]
    if cfg.hermes_home is not None:
        allowed.append(str(Path(cfg.hermes_home) / "cache" / "scratch"))
    return [_real(a) for a in allowed]


def _under(path: str, roots: list[str]) -> bool:
    return any(path == r or path.startswith(r.rstrip("/") + "/") for r in roots)


def _protected(cfg: Config, path: str) -> bool:
    try:
        real = _real(path)
    except (TypeError, ValueError):
        return True
    if _under(real, allowed_roots(cfg)):
        return False
    return _under(real, protected_roots(cfg)) or _under(os.path.expanduser(path), protected_roots(cfg))


def _mentions_protected(cfg: Config, cmd: str) -> bool:
    expanded = cmd.replace("~/", str(Path.home()) + "/")
    tokens = [t for t in (cfg.guard.get("protected_tokens") or []) if t]
    return any(r in expanded for r in protected_roots(cfg)) or any(t in cmd for t in tokens)


def _hermes_segments(cmd: str) -> list[str]:
    return [s for s in re.split(r"&&|\|\||;|\||\n|\$\(|`", cmd) if re.search(r"(^|/|\s)hermes(\s|$)", s)]


def _config_set_segments(cmd: str) -> list[str]:
    return [s for s in re.split(r"&&|\|\||;|\||\n|\$\(|`", cmd) if _CONFIG_SET.search(s)]


def check(cfg: Config, tool_name: str, args: dict) -> str | None:
    """Return a refusal message, or None to allow."""
    g = cfg.guard
    if not g.get("enabled", True):
        return None
    args = args or {}
    no = refusal(cfg)
    if tool_name == "execute_code":
        if g.get("block_execute_code"):
            return no + f" (execute_code is disabled for {cfg.names.companion}; use read-only tools.)"
        return None
    if tool_name in ("write_file", "patch"):
        path = args.get("path") or args.get("file_path") or ""
        if not path and isinstance(args.get("patch"), str):
            paths = re.findall(r"^\*\*\* (?:Update|Add|Delete) File: (.+)$", args["patch"], re.M)
            return no if any(_protected(cfg, p) for p in paths) else None
        return no if path and _protected(cfg, path) else None
    if tool_name == "skill_manage":
        return None if str(args.get("action", "")).lower() in ("view", "list", "read") else no
    if tool_name == "cronjob_manage":
        return None if str(args.get("action", "")).lower() in ("list", "status", "runs", "get") else no
    if tool_name in ("terminal", "process_manage"):
        cmd = str(args.get("command") or args.get("cmd") or "")
        if not cmd:
            return None
        if g.get("restrict_hermes_cli", True):
            for seg in _hermes_segments(cmd):
                if not _HERMES_ALLOWED.search(seg.strip()):
                    return no
        if _config_set_segments(cmd):
            return no
        if g.get("block_system_commands", True) and _SYSTEM.search(cmd):
            return no
        if _mentions_protected(cfg, cmd) and _WRITE_TOKENS.search(cmd):
            return no
    return None
