"""The companion through Hermes: a one-shot chat in the profile (``hermes chat --oneshot``), as a ``Companion``.

Evidence for the exit codes: ``hermes chat --oneshot -Q`` exits 0 only when the turn completed; a failed or
partial turn prints its ``final_response`` (the localized failure copy, e.g. ``generic_failed``, "The AI model
service isn't reachable right now", "Provider said: …") on stdout and exits 1, and a turn that never ran (no
credentials, agent init failed) exits 1 too (``hermes_cli/cli_single_query.py`` ``_single_query_exit_code``,
``_run_quiet_single_query``). The run itself (query file, timeout, exit status, output) is ``agent.CommandCompanion``'s;
this adapter only says which command, and what Hermes' failure copy looks like.
"""
from __future__ import annotations

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
