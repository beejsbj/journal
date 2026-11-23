"""The companion through Hermes: a one-shot chat in the profile (``hermes chat --oneshot``), as a ``Companion``.

Evidence for the exit codes: ``hermes chat --oneshot -Q`` exits 0 only when the turn completed; a failed or
partial turn prints its ``final_response`` (the localized failure copy, e.g. ``generic_failed``, "The AI model
service isn't reachable right now", "Provider said: …") on stdout and exits 1, and a turn that never ran (no
credentials, agent init failed) exits 1 too (``hermes_cli/cli_single_query.py`` ``_single_query_exit_code``,
``_run_quiet_single_query``). The run itself (query file, timeout, exit status, output) is ``agent.CommandCompanion``'s;
this adapter only says which command, and what Hermes' failure copy looks like.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from ..agent import CommandCompanion
from ..agent import is_failure_text  # noqa: F401 - Hermes' failure copy, kept in the engine (it runs without Hermes)


# The engine's word for a step with no tools is ``"none"`` (``agent.ask(..., toolsets="none")``, the outage probe).
# Hermes has no toolset of that name: since its gateway runs every one-shot (``gateway/session_policy.py``
# ``_resolve_toolsets``), an unknown name is refused (``invalid_params``) and no model runs; before, it warned
# ("Unknown toolsets: none") and ran with no tools. ``no_mcp`` is a name Hermes accepts (``saved_toolset_resolver``:
# the sentinel of a saved list) and, alone in a pick, resolves to no toolsets at all; an older Hermes does not know it
# and ran with no tools, as with ``none``. (An empty pick, ``-t ""``, is "no tools" to the new gateway but the
# profile's default tools to an older Hermes, so it is not used.) ``tests/test_hermes_argv_contract.py`` pins this.
NO_TOOLS = "no_mcp"


# ``-t no_mcp`` is no toolsets only on a profile where Hermes adds none by itself (HERMES-ONESHOT-ENV, M1): with xAI
# credentials it adds ``x_search`` to a pick that names no toolset of the profile's (``hermes_cli/tools_config.py``
# ``_composite_toolsets``), and with a context engine other than the default (``context.engine``) it adds
# ``context_engine`` to every pick (the reflection's ``journal_read`` too). The engine cannot ask Hermes for less, so
# ``journal doctor`` says when either holds (``toolset_hazards``), and the live contract test shows both.
XAI_STORE_KEY = "xai-oauth"


def toolset_hazards(home: Path | None, environ: dict | None = None) -> list[str]:
    """What in the profile at ``home`` adds tools to the engine's "no tools" one-shots: the reasons, or []. Reads the
    profile's files only (``config.yaml``, ``.env``, ``auth.json``, and the root's ``auth.json``); never a value."""
    if home is None:
        return []
    home = Path(home)
    environ = os.environ if environ is None else environ
    found = []
    engine = _context_engine(home / "config.yaml")
    if engine:
        found.append(f"context.engine is {engine!r}: Hermes adds its context_engine tools to every one-shot pick, "
                     "the reflection's included (set it back to compressor for the journal's profile, or accept it)")
    if _xai_credentials(home, environ):
        found.append("the profile has xAI credentials: Hermes adds x_search to the engine's no-tools one-shots "
                     "(annotations, picture descriptions, the probe)")
    return found


def _context_engine(path: Path) -> str:
    try:
        import yaml
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # (no file, no PyYAML, a broken file: nothing known, nothing claimed)
        return ""
    ctx = doc.get("context") if isinstance(doc, dict) else None
    name = str((ctx or {}).get("engine") or "compressor").strip().lower() if isinstance(ctx, dict) else "compressor"
    return "" if name in ("", "compressor") else name


def _xai_credentials(home: Path, environ) -> bool:
    if str(environ.get("XAI_API_KEY") or "").strip():
        return True
    try:
        for line in (home / ".env").read_text(encoding="utf-8").splitlines():
            key, _, value = line.strip().removeprefix("export ").partition("=")
            if key.strip() == "XAI_API_KEY" and value.strip().strip("'\"").strip():
                return True
    except OSError:
        pass
    root = home.parent.parent if home.parent.name == "profiles" else home
    for store in {home / "auth.json", root / "auth.json"}:
        try:
            data = json.loads(store.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and any(isinstance(data.get(k), dict) and data[k].get(XAI_STORE_KEY)
                                          for k in ("providers", "credential_pool")):
            return True
    return False


def hermes_toolsets(toolsets: str | None) -> str | None:
    """The ``-t`` value for the engine's ``toolsets``: ``"none"`` becomes ``NO_TOOLS``; other names (the plugin's
    ``journal_read``) pass through, as Hermes knows them once the plugin is loaded."""
    if not toolsets:
        return None
    names = [n.strip() for n in toolsets.split(",") if n.strip()]
    names = [n for n in names if n != "none"] or [NO_TOOLS]
    return ",".join(names)


class HermesCompanion(CommandCompanion):
    """A one-shot chat in the configured profile: its model chain, tools and MCP servers."""

    def argv(self, query_file: Path, budget: int, toolsets: str | None, image: str | None = None) -> list[str]:
        cfg = self.cfg
        picked = hermes_toolsets(toolsets)
        return [cfg.hermes_command(), *cfg.profile_args(), "chat", "--oneshot", "--query-file", str(query_file),
                "--run-budget", str(budget), "--source", "tool", "-Q",
                *(["-t", picked] if picked else []), *(["--image", image] if image else [])]

    def takes_image(self) -> bool:
        return True  # ``hermes chat --image``: attached natively to a vision model, else pre-described

    def doctor_rows(self) -> list[tuple[str, str, str]]:
        """``journal doctor``'s rows for the one-shots' tools (``toolset_hazards``): a warning each, or one ok."""
        cfg = self.cfg
        home = cfg.hermes_home
        if cfg.profile:
            from ..config import hermes_data_root
            base = hermes_data_root(home) if home is not None else Path.home() / ".hermes"
            home = base / "profiles" / cfg.profile
        found = toolset_hazards(home, cfg.hermes_env())
        if home is None:
            return [("one-shot tools", "ok", "no Hermes profile home known here: not checked")]
        return [("one-shot tools", "warn", why) for why in found] or [
            ("one-shot tools", "ok", f"-t {NO_TOOLS} is no tools in {home} (no xAI credentials, default context engine)")]
