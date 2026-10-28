"""Ask the companion (a full agent: its model chain, tools and MCP servers) for text.

The agent only ever returns text here; code decides what to do with it. There are two kinds of
"nothing usable", and callers treat them differently:

* **No answer** (the process could not run or timed out, exited non-zero, or printed the agent's own
  failure text): ``ask`` raises ``AgentUnavailable`` (``ports.CompanionUnavailable``). This is an outage (no
  model or provider reachable, a crashed run); the caller defers the work and retries later without spending an
  attempt.
* **A bad answer** (the model answered, but it is empty, or fails validation): ``ask`` returns the text
  or None, and the caller counts an attempt as before.

Anything that looks like the agent's failure text is never returned as an answer, even with exit 0.

``ask(cfg, …)`` is the facade every model step calls; it asks ``cfg.companion()``: by default the Hermes adapter's
one-shot chat in the configured profile (``journal.hermes.companion``), and with ``agent.command`` any other command
(``CommandCompanion``: an argv list; ``{query_file}``, ``{budget}`` and ``{toolsets}`` are filled in).
``agent.enabled: false`` turns model steps off entirely.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from .config import Config
from .ports import CompanionUnavailable

DETAIL_CAP = 200
_answered = False  # a model answered in this process (one tick): it is reachable again


AgentUnavailable = CompanionUnavailable  # the engine's name for it (one class)


def is_failure_text(text: str | None) -> bool:
    """Whether ``text`` is the agent reporting a failure rather than a model answer (the Hermes adapter knows the
    copy: ``journal.hermes.companion``)."""
    from .hermes.companion import is_failure_text as failure
    return failure(text)


def __getattr__(name: str):
    if name in ("FAILED_TURN_KIND", "FAILED_TURN_NOTICES"):  # moved to the transcript adapter with the row reading
        from .hermes import transcript
        return getattr(transcript, name)
    raise AttributeError(name)


def _detail(out: subprocess.CompletedProcess) -> str:
    """One line saying why a run failed: Hermes' failure copy on stdout, else the last stderr line."""
    for stream in (out.stdout, out.stderr):
        lines = [l.strip() for l in (stream or "").splitlines()
                 if l.strip() and not l.strip().startswith("session_id:")]
        if lines:
            return f"exit {out.returncode}: {lines[0] if stream is out.stdout else lines[-1]}"[:DETAIL_CAP]
    return f"exit {out.returncode}"


# Provider-owned shapes only. A status number counts after the prefixes Hermes and the SDKs print ("Error code:
# 429", "HTTP 502", "status 503"); an error class counts only as the value of a structured ``"type"`` field or as
# the token right after "Error code: NNN -". A bare "503" or "family_error" in prose matches neither.
_STATUS = re.compile(r"\b(?:error code|http(?: status)?|status(?: code)?)[:= ]+([45]\d\d)\b")
_ERROR_CLASS = re.compile(r"""["']type["']\s*:\s*["']([a-z]{2,20}(?:_[a-z]{2,20}){0,3}_error)["']"""
                          r"""|\berror code: [45]\d\d\s+-\s+([a-z]{2,20}(?:_[a-z]{2,20}){0,3}_error)\b""")
_LABELS = ("timed out", "could not run the agent", "agent reported a failure")


def failure_reason(message: str) -> str:
    """A short, single-line reason for an ``AgentUnavailable`` message, safe to put in a log: ``exit 1: 429
    rate_limit_error``, ``killed by signal 9``, ``timed out``, ``could not run the agent``, or "" when nothing is
    recognised.

    The message may carry stderr or Hermes' failure copy, and a failing run can echo what it was given (the
    prompt, his earlier words, a provider's quoted request). So this is a whitelist, not a truncation, and it
    reads only provider-owned shapes: the label of how the run failed (``exit N``, a negative status as the signal
    that killed it, a timeout, an agent that could not start, a failure Hermes printed); an HTTP 4xx/5xx number
    after ``Error code:``, ``HTTP`` or ``status``; and a ``*_error`` class from a structured ``"type"`` field or
    right after ``Error code: NNN -``. A number or ``*_error`` word loose in prose (which is where an echo of his
    words would put one) matches nothing. Everything else, whatever the length, is dropped, so no prose from the
    message reaches a log line."""
    low = (message or "").lower().lstrip()
    m = re.match(r"exit (-?\d{1,3})\b", low)
    if m and m.group(1).startswith("-"):
        head = f"killed by signal {m.group(1)[1:]}"
    else:
        head = f"exit {m.group(1)}" if m else next((label for label in _LABELS if low.startswith(label)), "")
    classes = [a or b for a, b in _ERROR_CLASS.findall(low)]
    found = list(dict.fromkeys([*_STATUS.findall(low), *classes]))
    return (": ".join(x for x in (head, " ".join(found)) if x))[:DETAIL_CAP]


def answered_this_run() -> bool:
    """Whether a model run answered (well or badly) earlier in this process: the outage is over."""
    return _answered


class CommandCompanion:
    """``ports.Companion`` that runs a command: ``agent.command`` (an argv template), or, subclassed, the Hermes
    adapter's one-shot chat. The query goes in a private file under ``state_dir``; the run gets ``budget`` plus a
    minute; its output is read the way Hermes' one-shot prints it (a trailing ``session_id:`` line and helper notes
    dropped), which a wrapper of it also prints."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def argv(self, query_file: Path, budget: int, toolsets: str | None, image: str | None = None) -> list[str]:
        fill = {"query_file": str(query_file), "budget": str(budget), "toolsets": toolsets or "", "image": image or ""}
        return [str(part).format(**fill) for part in self.cfg.agent.get("command") or []]

    def takes_image(self) -> bool:
        """Whether a picture can be attached to a question: a custom ``agent.command`` says so with ``{image}``."""
        return any("{image}" in str(part) for part in self.cfg.agent.get("command") or [])

    def ask(self, query: str, budget: int, toolsets: str | None = None, image: str | None = None) -> str | None:
        """The agent's text, or None for an empty answer; raises ``AgentUnavailable`` on an outage. With ``image``
        (a path) the picture goes with the question; a companion that cannot take one answers None, never a
        description of something it has not seen."""
        cfg = self.cfg
        if image and not self.takes_image():
            return None
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", suffix=".md", dir=cfg.state_dir, delete=False) as fh:
            fh.write(query)
            qpath = Path(fh.name)
        try:
            argv = self.argv(qpath, budget, toolsets, image) if image else self.argv(qpath, budget, toolsets)
            out = subprocess.run(argv, capture_output=True, text=True,
                                 timeout=budget + 60, cwd=str(cfg.state_dir), env=cfg.hermes_env())
        except subprocess.TimeoutExpired as e:
            raise AgentUnavailable(f"timed out after {e.timeout:.0f}s") from e
        except OSError as e:
            raise AgentUnavailable(f"could not run the agent: {e}"[:DETAIL_CAP]) from e
        finally:
            qpath.unlink(missing_ok=True)
        if out.returncode != 0:
            raise AgentUnavailable(_detail(out))
        text = re.sub(r"\n*session_id:\s*\S+\s*$", "", out.stdout.strip())
        text = "\n".join(l for l in text.splitlines()
                         if not l.startswith(("  Command helper:", "Warning: Unknown toolsets")))
        text = text.strip()
        if is_failure_text(text):  # a failure the agent printed with exit 0: never an answer
            raise AgentUnavailable(f"agent reported a failure: {text.splitlines()[0]}"[:DETAIL_CAP])
        return text or None


class NoCompanion:
    """``adapter: none`` with no ``agent.command``: there is no companion to ask, which waits like an outage."""

    def __init__(self, cfg: Config):
        self.cfg = cfg

    def ask(self, query: str, budget: int, toolsets: str | None = None, image: str | None = None) -> str | None:
        raise AgentUnavailable("could not run the agent: adapter is none and agent.command is not set")


def ask(cfg: Config, query: str, budget: int, toolsets: str | None = None, image: str | None = None) -> str | None:
    """The agent's text; None when it is off or answered with nothing; raises ``AgentUnavailable`` on an outage.
    ``image``: a picture to attach (only when given, so a companion that predates it is called as before)."""
    if cfg.agent.get("enabled") is False:
        return None
    companion = cfg.companion()
    takes = getattr(companion, "takes_image", None)
    if image and callable(takes) and not takes():
        return None  # nothing ran, so nothing answered: this is no evidence that the model is reachable
    text = companion.ask(query, budget, toolsets, **({"image": image} if image else {}))
    global _answered
    _answered = True
    return text
