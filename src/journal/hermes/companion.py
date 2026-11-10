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


class HermesCompanion(CommandCompanion):
    """A one-shot chat in the configured profile: its model chain, tools and MCP servers."""

    def argv(self, query_file: Path, budget: int, toolsets: str | None, image: str | None = None) -> list[str]:
        cfg = self.cfg
        return [cfg.hermes_command(), *cfg.profile_args(), "chat", "--oneshot", "--query-file", str(query_file),
                "--run-budget", str(budget), "--source", "tool", "-Q",
                *(["-t", toolsets] if toolsets else []), *(["--image", image] if image else [])]

    def takes_image(self) -> bool:
        return True  # ``hermes chat --image``: attached natively to a vision model, else pre-described
