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

# Hermes' user-facing failure copy (locales/en.yaml ``gateway.*``: generic_failed, unreachable,
# connection_interrupted, provider_kept_failing, no_credentials, no_response …) opens with a warning
# sign and points at /retry, `hermes doctor` or `hermes logs`; provider rejections add "Provider said:".
_FAILURE_MARKS = ("⚠", "⏱")
_FAILURE_PHRASES = ("(`hermes logs`)", "`hermes doctor` on the host", "use /retry", "provider said:",
                    "couldn't finish this reply")


def is_failure_text(text: str | None) -> bool:
    """Whether ``text`` is Hermes reporting a failure rather than a model answer."""
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith(_FAILURE_MARKS):
        return True
    low = t.lower()
    return len(t) < 800 and any(p in low for p in _FAILURE_PHRASES)


class HermesCompanion(CommandCompanion):
    """A one-shot chat in the configured profile: its model chain, tools and MCP servers."""

    def argv(self, query_file: Path, budget: int, toolsets: str | None, image: str | None = None) -> list[str]:
        cfg = self.cfg
        return [cfg.hermes_command(), *cfg.profile_args(), "chat", "--oneshot", "--query-file", str(query_file),
                "--run-budget", str(budget), "--source", "tool", "-Q",
                *(["-t", toolsets] if toolsets else []), *(["--image", image] if image else [])]

    def takes_image(self) -> bool:
        return True  # ``hermes chat --image``: attached natively to a vision model, else pre-described
