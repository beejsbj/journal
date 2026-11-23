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

An outage, once found, is known across processes (``outage``'s breaker): while it lasts ``ask`` starts no run and
raises ``outage.ModelHeld`` (an ``AgentUnavailable``) until a probe is due, then probes once before the run. A run
that fails (other than by timing out) opens the breaker only when a probe run at once fails too: when the probe
answers, the request failed on its own (one picture, one prompt too long), nothing else is held, and a failure the
request caused (``outage.request_failed``: a 4xx other than 408 and 429) comes back as None, a bad answer. A request
the agent refused before any model ran (Hermes' ``invalid_params``) raises ``outage.RequestRefused``: no probe, never
the breaker, one line in ``errors.jsonl``; the caller falls back as for an unavailable model, for that record alone.

``ask(cfg, …)`` is the facade every model step calls; it asks ``cfg.companion()``: by default the Hermes adapter's
one-shot chat in the configured profile (``journal.hermes.companion``), and with ``agent.command`` any other command
(``CommandCompanion``: an argv list; ``{query_file}``, ``{budget}`` and ``{toolsets}`` are filled in).
``agent.enabled: false`` turns model steps off entirely.

Every run carries ``JOURNAL_MODEL_STEP=1`` in its environment and the same fact as the first line of its query
(``step_mark``: ``[journal model step]``, with the search bound when the step has one): the journal's own hook
(``hermes.hooks.on_llm_call``) then adds nothing to the turn, so a model step sees only the query the engine wrote
(the context pack an opener is given already holds what it should; the notebook and the today block are for her chat).
A step may add variables of its own (``env``): the reflection's carries ``search.UPTO_ENV``, its period's last day,
which the mark carries too. The query carries it because the environment does not always arrive: a Hermes that runs
every one-shot inside its gateway keeps the client's environment to an allowlist of its own and never sets it in
the process the hooks and tools run in (HERMES-ONESHOT-ENV, DECISIONS.md).
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from . import outage
from .config import Config
from .ports import CompanionUnavailable
from .search import UPTO_ENV

DETAIL_CAP = 200
MODEL_STEP_ENV = "JOURNAL_MODEL_STEP"  # set in every run's environment: the engine's own model step, not a turn of his
STEP_MARK = "[journal model step]"  # the first line of every run's query: the same fact, for a process with no env
_STEP_LINE = re.compile(r"\[journal model step(?:; search up to (\d{4}-\d\d-\d\d))?\]")
_answered = False  # a model answered in this process (one tick): it is reachable again


AgentUnavailable = CompanionUnavailable  # the engine's name for it (one class)


# Hermes' user-facing failure copy (locales/en.yaml ``gateway.*``: generic_failed, unreachable,
# connection_interrupted, provider_kept_failing, no_credentials, no_response …) opens with a warning
# sign and points at /retry, `hermes doctor` or `hermes logs`; provider rejections add "Provider said:". Kept here, not
# in the Hermes adapter, so the engine runs (and recognises an agent's failure printed with exit 0) without it.
FAILURE_MARKS = ("⚠", "⏱")
FAILURE_PHRASES = ("(`hermes logs`)", "`hermes doctor` on the host", "use /retry", "provider said:",
                   "couldn't finish this reply")


def step_mark(env: dict[str, str] | None = None) -> str:
    """The line that opens every run's query: ``[journal model step]``, or with the step's search bound
    (``UPTO_ENV``, a YYYY-MM-DD) ``[journal model step; search up to 2026-10-14]``."""
    upto = (env or {}).get(UPTO_ENV) or ""
    return f"[journal model step; search up to {upto}]" if re.fullmatch(r"\d{4}-\d\d-\d\d", upto) else STEP_MARK


def read_step_mark(text) -> tuple[bool, str | None]:
    """Whether ``text`` (a turn's user message: a string, or a list of content parts) carries ``step_mark``'s line,
    and the search bound it names. Any line of it counts (an agent may put something before the query, such as a
    picture's description), but only a whole line: ``(False, None)`` otherwise."""
    if isinstance(text, (list, tuple)):
        text = "\n".join(p if isinstance(p, str) else str(p.get("text") or "") if isinstance(p, dict) else ""
                         for p in text)
    if not isinstance(text, str) or "[journal model step" not in text:
        return False, None
    for line in text.splitlines():
        m = _STEP_LINE.fullmatch(line.strip())
        if m:
            return True, m.group(1)
    return False, None


def is_failure_text(text: str | None) -> bool:
    """Whether ``text`` is the agent reporting a failure rather than a model answer (Hermes' failure copy)."""
    t = (text or "").strip()
    if not t:
        return False
    if t.startswith(FAILURE_MARKS):
        return True
    low = t.lower()
    return len(t) < 800 and any(p in low for p in FAILURE_PHRASES)


def __getattr__(name: str):
    if name in ("FAILED_TURN_KIND", "FAILED_TURN_NOTICES"):  # moved to the transcript adapter with the row reading
        from .hermes import transcript
        return getattr(transcript, name)
    raise AttributeError(name)


_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")  # colour, cursor, title


def _detail(out: subprocess.CompletedProcess) -> str:
    """One line saying why a run failed: Hermes' failure copy on stdout (its first paragraph, as one line: a terminal's
    copy may be wrapped), else the last stderr line; terminal colour and control codes dropped either way."""
    for stream in (out.stdout, out.stderr):
        text = _ANSI.sub("", stream or "")
        if stream is out.stdout:
            paras = [" ".join(l.strip() for l in para.splitlines() if l.strip())
                     for para in re.split(r"\n\s*\n", text)]
            lines = [p for p in paras if p and not p.startswith("session_id:")]
        else:
            lines = [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("session_id:")]
        if lines:
            return f"exit {out.returncode}: {lines[0] if stream is out.stdout else lines[-1]}"[:DETAIL_CAP]
    return f"exit {out.returncode}"


# Provider-owned shapes only. A status number counts after the prefixes Hermes and the SDKs print ("Error code:
# 429", "HTTP 502", "status 503"); an error class counts only as the value of a structured ``"type"`` field or as
# the token right after "Error code: NNN -". A bare "503" or "family_error" in prose matches neither.
_STATUS = re.compile(r"\b(?:error code|http(?: status)?|status(?: code)?)[:= ]+([45]\d\d)\b")
_ERROR_CLASS = re.compile(r"""["']type["']\s*:\s*["']([a-z]{2,20}(?:_[a-z]{2,20}){0,3}_error)["']"""
                          r"""|\berror code: [45]\d\d\s+-\s+([a-z]{2,20}(?:_[a-z]{2,20}){0,3}_error)\b""")
# Hermes' own refusal of a launch parameter (``agent_init_failed``: "Hermes couldn't start the model connection:
# invalid_params (toolsets: unknown or invalid toolset name: …)"): its wire code, and the field it names, read only
# right after that copy. The detail after the field (which echoes what was sent) is never read.
_REFUSAL = re.compile(r"couldn.t start the model connection: (invalid_params)\b(?: \(([a-z_]{2,40}):)?")
_LABELS = ("timed out", "could not run the agent", "agent reported a failure")
_ITEM = r"(?:[45]\d\d|[a-z]{2,20}(?:_[a-z]{2,20}){0,3}_error|invalid_params)"
_MADE = re.compile(rf"(?:(?:exit -?\d{{1,3}}|killed by signal \d{{1,3}}|{'|'.join(_LABELS)}|model unreachable)"
                   rf"(?:: {_ITEM}(?: {_ITEM})*)?|{_ITEM}(?: {_ITEM})*)")  # what ``failure_reason`` returns


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
    low = _ANSI.sub("", message or "").lower().lstrip()
    if outage.held(low):  # ``ModelHeld``: "held: " and a reason this function made (its grammar), else "held" alone
        rest = low[len(outage.HELD) + 1:].strip()
        return f"{outage.HELD}: {rest}"[:DETAIL_CAP] if _MADE.fullmatch(rest) else outage.HELD
    m = re.match(r"exit (-?\d{1,3})\b", low)
    if m and m.group(1).startswith("-"):
        head = f"killed by signal {m.group(1)[1:]}"
    else:
        head = f"exit {m.group(1)}" if m else next((label for label in _LABELS if low.startswith(label)), "")
    classes = [a or b for a, b in _ERROR_CLASS.findall(low)]
    refusal = [m.group(1)] if (m := _REFUSAL.search(low)) else []
    found = list(dict.fromkeys([*_STATUS.findall(low), *classes, *refusal]))
    return (": ".join(x for x in (head, " ".join(found)) if x))[:DETAIL_CAP]


def refused_field(message: str) -> str:
    """The launch parameter a refused request named (Hermes' field name, e.g. ``toolsets``), or "": for the log."""
    m = _REFUSAL.search(_ANSI.sub("", message or "").lower())
    return (m.group(2) or "") if m else ""


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

    def ask(self, query: str, budget: int, toolsets: str | None = None, image: str | None = None,
            env: dict[str, str] | None = None) -> str | None:
        """The agent's text, or None for an empty answer; raises ``AgentUnavailable`` on an outage. With ``image``
        (a path) the picture goes with the question; a companion that cannot take one answers None, never a
        description of something it has not seen. ``env``: more variables for this run's environment."""
        cfg = self.cfg
        if image and not self.takes_image():
            return None
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", suffix=".md", dir=cfg.state_dir, delete=False) as fh:
            fh.write(f"{step_mark(env)}\n\n{query}")
            qpath = Path(fh.name)
        try:
            argv = self.argv(qpath, budget, toolsets, image) if image else self.argv(qpath, budget, toolsets)
            out = subprocess.run(argv, capture_output=True, text=True, timeout=budget + 60, cwd=str(cfg.state_dir),
                                 env={**cfg.hermes_env(), **(env or {}), MODEL_STEP_ENV: "1"})
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

    def ask(self, query: str, budget: int, toolsets: str | None = None, image: str | None = None,
            env: dict[str, str] | None = None) -> str | None:
        raise AgentUnavailable("could not run the agent: adapter is none and agent.command is not set")


def ask(cfg: Config, query: str, budget: int, toolsets: str | None = None, image: str | None = None,
        env: dict[str, str] | None = None, trip_at_once: bool = False) -> str | None:
    """The agent's text; None when it is off or answered with nothing; raises ``AgentUnavailable`` on an outage.
    ``image``: a picture to attach; ``env``: variables for the run's environment (each passed on only when given, so
    a companion that predates it is called as before). ``trip_at_once``: a failed run opens the breaker with no
    confirming probe (the opener: a scheduled prompt must not wait out a probe before it goes out plain)."""
    if cfg.agent.get("enabled") is False:
        return None
    companion = cfg.companion()
    takes = getattr(companion, "takes_image", None)
    if image and callable(takes) and not takes():
        return None  # nothing ran, so nothing answered: this is no evidence that the model is reachable
    global _answered
    probed = outage.gate(cfg, companion)  # an outage is known: held (``ModelHeld``), or a probe answered just now
    if probed:
        _answered = True
    try:
        text = companion.ask(query, budget, toolsets, **({"image": image} if image else {}),
                             **({"env": env} if env else {}))
    except AgentUnavailable as e:
        reason = failure_reason(str(e)) or "model unreachable"
        if outage.refused(reason):  # the agent refused the request itself: no model ran, so this says nothing of one
            outage.note_refused(cfg, "a model step", reason, refused_field(str(e)))
            raise outage.RequestRefused(str(e)) from e  # (never trips or holds the breaker, ``trip_at_once`` or not)
        if str(e).startswith("timed out") and _answered:
            raise  # (a long run after an answer is no outage)
        if trip_at_once and not probed:
            outage.trip(cfg, reason)
            raise
        seen = probed or outage.confirm(cfg, companion)
        if seen is None:
            raise  # the probe was refused too: no evidence of an outage, so nothing is held and this record waits alone
        if not seen:  # the probe found no model either: an outage
            outage.trip(cfg, reason)
            raise
        _answered = True  # a model answers: this request failed on its own, and the breaker stays shut
        outage.answered(cfg)  # (one another process opened meanwhile is over too)
        if outage.request_failed(reason):
            return None  # (a bad request: the caller counts it as an attempt, so the work ends at its limit)
        raise
    _answered = True
    outage.answered(cfg)  # (an outage another process found is over)
    return text
