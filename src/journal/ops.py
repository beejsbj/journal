"""The operator's notice: the journal says, in code and with no model, when it breaks.

The journal is meant to run with nobody watching it, and its own alerts (a tick that exits 1, a line in
``errors.jsonl``) reach nobody: the scheduler's failure delivery goes to the journal's chat by default, the chat that
is broken when sends fail, and the person there is not the operator. So after every scheduled tick (and from
``journal ops check``, which an independent scheduler can run) this module looks at what the engine already records,
keeps **incidents** in ``ops.json`` under ``state_dir`` (read and written under ``ledger.locked_state``), and tells
the operator through a GitHub issue (the ``gh`` CLI, run as a subprocess, so the core stays stdlib) or an argv
command of the operator's (``ops.command``).

**Incidents.** One per kind at a time (``KINDS``): opened when its condition holds, a notice filed once (``open``),
a comment at most every ``ops.update_hours`` (6) while it lasts (``update``), and closed when the condition clears
(``resolve``). An incident that clears before its notice went out is dropped with nothing filed; one that comes back
before its close went out is the same incident. A notice that cannot be delivered is logged (a line with "failed",
which the tick files in ``errors.jsonl``) and tried again later, every 30 minutes and then less often; it never
breaks the tick. With no ``ops.repo`` and no ``ops.command`` nothing is delivered, and the incidents are still kept
and shown by ``journal status`` (the operator can look; nothing is lost when delivery is set up later).

The kinds (thresholds under ``ops.*``):

- ``breaker_open``: the outage breaker (``outage``) has been open longer than ``breaker_hours`` (2).
- ``model_floor``: with the breaker shut, ``floor_run`` (3) model steps in a row fell to their floor since the last
  model success: a scheduled opener that went out plain because the model failed or its answer was rejected, an
  annotation given up after its attempts. A request the provider refuses falls to its floor without opening the
  breaker (``outage.request_failed``), so this is what catches that class. While the breaker is open the incident is
  held as it is (``breaker_open`` speaks for the outage); the count starts again after the breaker closes.
- ``send_failing``: the newest scheduled prompts were not delivered after all their attempts (a chat or bot token
  that stopped working). Only with a chat (``channel.platform`` other than ``none``).
- ``fold_given_up``: a prompt's fold was given up for good within ``fold_hours`` (72). The one field read is the
  ledger's ``fold_gave_up_at``, which ``tick.fold`` writes when it stops trying.
- ``step_failing``: a step of the tick crashed in ``step_ticks`` (6) scheduled ticks in a row.
- ``tick_stale``: no scheduled tick has finished for ``tick_stale_hours`` (1). A tick cannot see its own absence, so
  only ``journal ops check`` (run by a scheduler other than the tick's) can open it; every tick closes it.

**Privacy: the target repository may be public.** Every title and body comes from the fixed templates below; the
only values in them are a kind, times in UTC, counts, step names from the tick's own list, exception class names and
an error class from ``error_class``, which builds its answer from a fixed vocabulary and digits and never copies text
from the error. No entry text, no words of his or the companion's, nothing from the chat, no slot id, practice name,
path, host or person's name reaches a notice. ``test_ops.py`` feeds personal-looking text through every kind and
checks the argv and stdin of each call.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ledger, outage
from .config import Config

STATE = "ops.json"
RUN_LOCK = "ops-run.lock"
KINDS = ("breaker_open", "model_floor", "send_failing", "fold_given_up", "step_failing", "tick_stale")
DEFAULTS = {"breaker_hours": 2, "floor_run": 3, "fold_hours": 72, "step_ticks": 6, "tick_stale_hours": 1,
            "update_hours": 6}
RETRY_FIRST = timedelta(minutes=30)
PER_PASS = 4          # notices delivered in one pass at most (each a subprocess with a timeout)
TIMEOUT = 60          # seconds a gh call or the operator's command may take
SEND_RECENT = timedelta(days=7)  # an undelivered prompt older than this no longer says the chat is down
CLOSED_KEPT = 20
LABEL_COLOR = "B60205"
HOLD = object()       # a kind not judged in this pass: its incident stays as it is

# The tick's steps (``tick.run``): the only step names a notice may carry. Anything else is "other".
STEP_NAMES = ("send", "interrupts", "privacy", "sweep", "conversations", "programs", "fold", "audio", "images",
              "annotate", "reply", "reflect", "recover", "mirror", "cache", "prune", "notebook")

TITLES = {
    "breaker_open": "journal ops: model outage, the breaker is open",
    "model_floor": "journal ops: model steps keep falling to their floor",
    "send_failing": "journal ops: prompts are not being delivered",
    "fold_given_up": "journal ops: a prompt fold was given up",
    "step_failing": "journal ops: a tick step keeps crashing",
    "tick_stale": "journal ops: the tick has stopped running",
}
SAYS = {
    "breaker_open": "No model has answered since the time below; model work is held, and prompts go out with the "
                    "plain opener.",
    "model_floor": "Model steps fail without the breaker opening: openers go out plain and annotations are given "
                   "up. A request the provider refuses (a changed parameter) looks like this.",
    "send_failing": "Scheduled prompts were not delivered to the chat after all their attempts.",
    "fold_given_up": "Prompt messages could not be folded and the fold was given up for good.",
    "step_failing": "A step of the tick crashed in every tick of the run counted below.",
    "tick_stale": "No scheduled tick has finished since the time below: the scheduler, or the tick, has stopped.",
}
CLEARED = {
    "breaker_open": "a model answered again and the breaker closed",
    "model_floor": "a model step succeeded again",
    "send_failing": "a scheduled prompt was delivered again",
    "fold_given_up": "no fold has been given up within the window",
    "step_failing": "the step ran without crashing",
    "tick_stale": "a scheduled tick finished again",
}
HINTS = {
    "breaker_open": "`journal status` (model outage line), the companion's own logs.",
    "model_floor": "`journal status` (recent errors), `journal doctor`.",
    "send_failing": "`journal doctor` (channel and token rows), `journal status` (undelivered).",
    "fold_given_up": "`journal status`; the ledger's `fold_error` on the prompt records.",
    "step_failing": "`journal status` (last run, recent errors).",
    "tick_stale": "the scheduler that runs `journal tick`, and `journal status` (last run).",
}
LABELS = (  # fact key, label, how it renders
    ("since", "Since", "time"), ("probes_failed", "Probes failed", "int"),
    ("plain_openers", "Openers sent plain", "int"), ("annotations_failed", "Annotations given up", "int"),
    ("last_success", "Last model success", "time"), ("undelivered", "Prompts undelivered", "int"),
    ("folds", "Folds given up", "int"), ("newest", "Newest", "time"), ("steps", "Steps", "steps"),
    ("ticks", "Ticks in a row", "int"), ("exception", "Exception class", "names"),
    ("last_tick", "Last tick", "time"), ("minutes", "Minutes since", "int"), ("error", "Error class", "class"),
)

# ── error classes: a fixed vocabulary, never a copy of the error ────────────

_PROVIDER_CLASSES = ("invalid_request_error", "invalid_params", "rate_limit_error", "authentication_error",
                     "permission_error", "not_found_error", "overloaded_error", "api_error", "timeout_error",
                     "context_length_exceeded", "server_error")
_PHRASES = (  # (what the error says, what a notice may say)
    ("timed out", "timeout"), ("timeout", "timeout"), ("could not run", "agent did not start"),
    ("message to edit not found", "message not found"), ("message can't be edited", "not editable"),
    ("message is not modified", "not modified"), ("can't parse entities", "parse error"),
    ("chat not found", "chat not found"), ("bot was blocked", "bot blocked"), ("unauthorized", "unauthorized"),
    ("forbidden", "forbidden"), ("too many requests", "rate limited"), ("connection", "connection error"),
    ("name resolution", "dns error"), ("held", "held by the breaker"),
)
_HTTP = re.compile(r"\b(400|401|403|404|408|409|413|422|429|500|502|503|504|529)\b")
_CLASS_SHAPE = re.compile(r"[a-z0-9 _(),.:-]{1,120}")
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,40}")


def error_class(text) -> str:
    """A short class for an error, built only from a fixed vocabulary and digits: ``exit 1 http 400 invalid_params
    (toolsets)``, ``timeout``, ``message not found``; ``unclassified`` when nothing is recognised. Nothing of the
    error's own text is copied, so a raw error that quotes his words, a prompt or a path gives at most these words."""
    low = str(text or "").lower()
    parts: list[str] = []
    m = re.search(r"\bkilled by signal (\d{1,2})\b", low)
    if m:
        parts.append(f"killed by signal {int(m.group(1))}")
    m = re.search(r"\bexit (-?\d{1,3})\b", low)
    if m and not parts:
        parts.append(f"exit {int(m.group(1))}")
    parts += [f"http {c}" for c in dict.fromkeys(_HTTP.findall(low))][:2]
    parts += [c for c in _PROVIDER_CLASSES if c in low][:2]
    parts += [said for phrase, said in _PHRASES if phrase in low]
    if "toolset" in low:
        parts.append("(toolsets)")
    out = " ".join(dict.fromkeys(parts))
    return out[:120] if out else "unclassified"


# ── settings ────────────────────────────────────────────────────────────────

def settings(cfg: Config) -> dict:
    return cfg.ops if isinstance(getattr(cfg, "ops", None), dict) else {}


def _number(cfg: Config, key: str) -> float:
    try:
        value = float(settings(cfg).get(key, DEFAULTS[key]))
    except (TypeError, ValueError):
        return float(DEFAULTS[key])
    return value if value > 0 else float(DEFAULTS[key])


def repo(cfg: Config) -> str:
    value = str(settings(cfg).get("repo") or "").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) else ""


def label(cfg: Config) -> str:
    value = str(settings(cfg).get("label") or "journal-ops").strip()
    return value if re.fullmatch(r"[A-Za-z0-9_. :-]{1,50}", value) else "journal-ops"


def command(cfg: Config) -> list[str] | None:
    cmd = settings(cfg).get("command")
    if isinstance(cmd, list) and cmd and all(isinstance(x, str) and x for x in cmd):
        return list(cmd)
    return None


def gh_path(cfg: Config) -> str:
    exe = str(settings(cfg).get("gh") or "").strip()
    return exe or shutil.which("gh") or "gh"


def delivery(cfg: Config) -> str:
    """``command``, ``github`` or ``off`` (no ``ops.repo`` and no ``ops.command``: incidents are kept, not sent)."""
    if command(cfg):
        return "command"
    return "github" if repo(cfg) else "off"


def problems(cfg: Config) -> list[str]:
    """What is wrong with the ``ops`` settings (for ``journal doctor``)."""
    s, out = settings(cfg), []
    if s.get("repo") and not repo(cfg):
        out.append(f"ops.repo must look like owner/name (got {s.get('repo')!r}); no notice is filed")
    if s.get("command") is not None and not command(cfg):
        out.append("ops.command must be a list of strings (an argv); it is ignored")
    for key in DEFAULTS:
        if key in s:
            try:
                ok = float(s[key]) > 0
            except (TypeError, ValueError):
                ok = False
            if not ok:
                out.append(f"ops.{key} must be a positive number (got {s[key]!r}); {DEFAULTS[key]} is used")
    return out


# ── times ───────────────────────────────────────────────────────────────────

def _at(value) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return when if when.tzinfo is not None else None


def _iso(when: datetime) -> str:
    return when.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def _utc(value) -> str:
    when = _at(value)
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if when else "unknown"


# ── what the engine records, judged ─────────────────────────────────────────

def _practice_record(p: dict) -> bool:
    """A scheduled practice's prompt: not a note, a program phase or an interrupt."""
    from .entries import FREEFORM_IDS
    return bool(p.get("id")) and p.get("kind") not in FREEFORM_IDS and not p.get("program") \
        and not p.get("interrupt") and not p.get("run")


_NOT_A_FLOOR = ("practice", "send-retry", "web", "cli", "mcp")  # plain by design, a send's retry, or a face's


def _model_expected(cfg: Config) -> bool:
    """Whether model steps are meant to run at all (off, or no companion with no command: plain by design)."""
    if cfg.agent.get("enabled") is False:
        return False
    return not (cfg.adapter == "none" and not cfg.agent.get("command"))


def _breaker(cfg: Config, now: datetime):
    if not _model_expected(cfg):
        return None
    st = outage.known(cfg)
    if st is None:
        return None
    down = _at(st.get("down_since"))
    if down is None or now - down < timedelta(hours=_number(cfg, "breaker_hours")):
        return None
    return {"since": _iso(down), "probes_failed": outage.probes(st), "error": error_class(st.get("last_error"))}


def _model_floor(cfg: Config, data: dict, now: datetime, annotate_attempts: int):
    if not _model_expected(cfg):
        return None
    if outage.known(cfg) is not None:
        return HOLD  # the breaker speaks for the outage; this one neither opens nor closes meanwhile
    successes: list[datetime] = []
    floors: list[tuple[datetime, str]] = []
    for p in data.get("prompts") or []:
        if not isinstance(p, dict):
            continue
        if p.get("message_id") and _practice_record(p):
            source, at = str(p.get("sent_opener_source") or p.get("opener_source") or ""), _at(p.get("sent_at"))
            if at is not None and source.startswith("plain:"):
                if source.split(":", 1)[1] not in _NOT_A_FLOOR:
                    floors.append((at, "opener"))
            elif at is not None and source and not source.startswith("plain") \
                    and source not in ("program", "/j", "auto"):
                successes.append(at)
        if p.get("annotated_at"):
            if (at := _at(p["annotated_at"])) is not None:
                successes.append(at)
        elif p.get("captures") and _int(p.get("annotate_attempts")) >= annotate_attempts:
            last = (p["captures"][-1] or {}) if isinstance(p["captures"], list) else {}
            if (at := _at(last.get("at") if isinstance(last, dict) else None)) is not None:
                floors.append((at, "annotation"))
    for r in data.get("reflections") or []:
        if isinstance(r, dict) and (at := _at(r.get("written_at"))) is not None:
            successes.append(at)
    back = _at((ledger.read_state(cfg.state_dir / outage.STATE).get("last") or {}).get("answered_at"))
    reset = max([*successes, *([back] if back else [])], default=None)
    after = [(at, what) for at, what in floors if reset is None or at > reset]
    if len(after) < int(_number(cfg, "floor_run")):
        return None
    return {"since": _iso(min(at for at, _ in after)),
            "plain_openers": sum(1 for _, w in after if w == "opener"),
            "annotations_failed": sum(1 for _, w in after if w == "annotation"),
            "last_success": _iso(max(successes)) if successes else "",
            "error": _latest_opener_error(cfg)}


def _latest_opener_error(cfg: Config) -> str:
    """The class of the newest opener failure the tick filed in ``errors.jsonl`` (``plain:agent-failed: <reason>``,
    a reason ``agent.failure_reason`` already reduced to a whitelist), or ``unknown``."""
    path = cfg.state_dir / "errors.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-200:]
    except OSError:
        return "unknown"
    for line in reversed(lines):
        try:
            text = str(json.loads(line).get("error") or "")
        except (ValueError, AttributeError):
            continue
        if "plain:agent-failed:" in text:
            return error_class(text.split("plain:agent-failed:", 1)[1])
    return "unknown"


def _send_failing(cfg: Config, data: dict, now: datetime, send_attempts: int):
    if not cfg.has_channel:
        return None
    seen: list[tuple[datetime, bool, dict]] = []
    for p in data.get("prompts") or []:
        if not isinstance(p, dict) or not _practice_record(p):
            continue
        at = _at(p.get("scheduled")) or _at(p.get("sent_at"))
        if at is None:
            continue
        if p.get("message_id"):
            seen.append((at, True, p))
        elif not p.get("captures") and _int(p.get("send_attempts")) >= send_attempts:
            seen.append((at, False, p))
    seen.sort(key=lambda x: x[0])
    run = []
    for at, delivered, p in reversed(seen):
        if delivered:
            break
        run.append((at, p))
    if not run or now - run[0][0] > SEND_RECENT:
        return None
    return {"since": _iso(run[-1][0]), "undelivered": len(run), "error": error_class(run[0][1].get("send_error"))}


def _fold_given_up(cfg: Config, data: dict, now: datetime):
    window = timedelta(hours=_number(cfg, "fold_hours"))
    gave = [(at, p) for p in data.get("prompts") or [] if isinstance(p, dict)
            and (at := _at(p.get("fold_gave_up_at"))) is not None and now - at < window]
    if not gave:
        return None
    at, newest = max(gave, key=lambda x: x[0])
    return {"since": _iso(min(a for a, _ in gave)), "folds": len(gave), "newest": _iso(at),
            "error": error_class(newest.get("fold_error"))}


def _step_failing(cfg: Config, st: dict):
    streaks = st.get("steps") if isinstance(st.get("steps"), dict) else {}
    need = int(_number(cfg, "step_ticks"))
    bad = {k: v for k, v in streaks.items() if isinstance(v, dict) and _int(v.get("ticks")) >= need}
    if not bad:
        return None
    since = min((_at(v.get("since")) for v in bad.values() if _at(v.get("since"))), default=None)
    return {"since": _iso(since) if since else "", "steps": sorted(bad),
            "ticks": max(_int(v.get("ticks")) for v in bad.values()),
            "exception": sorted({str(v.get("class") or "") for v in bad.values()} - {""})}


def _tick_stale(cfg: Config, st: dict, now: datetime):
    last = _at(st.get("tick_at"))
    if last is None:
        last = _at((ledger.read_state(cfg.state_dir / "last-tick.json") or {}).get("at"))
    if last is None or now - last < timedelta(hours=_number(cfg, "tick_stale_hours")):
        return None  # (a journal that never ticked has nothing to say yet)
    return {"since": _iso(last), "last_tick": _iso(last), "minutes": int((now - last).total_seconds() // 60)}


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def judge(cfg: Config, now: datetime, st: dict, *, from_tick: bool) -> dict:
    """kind → facts (the condition holds), None (it does not) or ``HOLD`` (not judged now)."""
    from . import tick  # (its retry limits; at call time, since the tick imports this module)
    data = ledger.snapshot(cfg.ledger_path)
    found = {}
    for kind, judge_one in (
            ("breaker_open", lambda: _breaker(cfg, now)),
            ("model_floor", lambda: _model_floor(cfg, data, now, tick.ANNOTATE_ATTEMPTS)),
            ("send_failing", lambda: _send_failing(cfg, data, now, tick.SEND_ATTEMPTS)),
            ("fold_given_up", lambda: _fold_given_up(cfg, data, now)),
            ("step_failing", lambda: _step_failing(cfg, st) if from_tick else HOLD),
            ("tick_stale", lambda: None if from_tick else _tick_stale(cfg, st, now))):
        try:
            found[kind] = judge_one()
        except Exception:  # noqa: BLE001 - one odd record must not stop the others: this kind waits
            found[kind] = HOLD
    return found


# ── incidents ───────────────────────────────────────────────────────────────

def _close(st: dict, kind: str, inc: dict, now: datetime) -> None:
    st.setdefault("closed", []).append({"kind": kind, "opened_at": inc.get("opened_at"),
                                        "resolved_at": inc.get("resolved_at") or _iso(now),
                                        "issue": inc.get("issue")})
    st["closed"] = st["closed"][-CLOSED_KEPT:]
    st.get("incidents", {}).pop(kind, None)


def reconcile(cfg: Config, st: dict, found: dict, now: datetime) -> None:
    """Move each incident on by what was found (in place, under the state lock)."""
    incs = st.setdefault("incidents", {})
    every = timedelta(hours=_number(cfg, "update_hours"))
    for kind in KINDS:
        facts, inc = found.get(kind, HOLD), incs.get(kind)
        if facts is HOLD:
            continue
        if facts is None:
            if inc is None or inc.get("resolved_at"):
                continue
            if inc.get("delivered"):
                inc.update(resolved_at=_iso(now), pending="resolve", tries=0)
                inc.pop("next_try_at", None)
            else:
                inc["resolved_at"] = _iso(now)
                _close(st, kind, inc, now)  # never filed: nothing to close
            continue
        if inc is None:
            incs[kind] = {"opened_at": _iso(now), "facts": facts, "seen_at": _iso(now), "pending": "open",
                          "tries": 0, "delivered": False, "issue": None}
            continue
        inc.update(facts=facts, seen_at=_iso(now))
        if inc.get("resolved_at"):  # back before its close went out: the same incident goes on
            inc.pop("resolved_at")
            inc["pending"] = None if inc.get("delivered") else "open"
            inc["tries"] = 0
            inc.pop("next_try_at", None)
        if inc.get("delivered") and not inc.get("pending"):
            last = _at(inc.get("notified_at"))
            if last is None or now - last >= every:
                inc.update(pending="update", tries=0)


def note_tick(st: dict, now: datetime, crashed: dict[str, str]) -> None:
    """A scheduled tick finished: its time, and each step's run of crashed ticks (a step that ran clean ends its
    run). ``crashed``: step name → exception class name."""
    st["tick_at"] = _iso(now)
    steps = st.get("steps") if isinstance(st.get("steps"), dict) else {}
    out = {}
    for name, cls in crashed.items():
        name = name if name in STEP_NAMES else "other"
        cls = cls if _NAME.fullmatch(str(cls or "")) else "Exception"
        prev = steps.get(name) if isinstance(steps.get(name), dict) else {}
        out[name] = {"since": prev.get("since") or _iso(now), "ticks": _int(prev.get("ticks")) + 1, "class": cls}
    st["steps"] = out


# ── the notice: fixed templates ─────────────────────────────────────────────

def _value(how: str, value) -> str | None:
    if how == "int":
        return str(_int(value)) if isinstance(value, (int, str)) else None
    if how == "time":
        return _utc(value) if value else "none recorded"
    if how == "steps":
        return ", ".join(v if v in STEP_NAMES else "other" for v in value or []) or None
    if how == "names":
        return ", ".join(v for v in value or [] if _NAME.fullmatch(str(v))) or None
    if how == "class":
        text = str(value or "")
        return text if _CLASS_SHAPE.fullmatch(text) else "unclassified"
    return None


def _table(kind: str, facts: dict) -> str:
    rows = [f"| Kind | `{kind}` |"]
    for key, name, how in LABELS:
        if key in (facts or {}):
            shown = _value(how, facts[key])
            if shown is not None:
                rows.append(f"| {name} | {shown} |")
    return "| | |\n| --- | --- |\n" + "\n".join(rows)


FOOTER = ("<sub>Filed by the journal's ops notice from fixed templates: kinds, times, counts and error classes "
          "only.</sub>")


def title(kind: str) -> str:
    return TITLES[kind]


def body(kind: str, action: str, inc: dict, now: datetime) -> str:
    facts = inc.get("facts") or {}
    if action == "resolve":
        opened = _at(inc.get("opened_at"))
        hours = f"; it was open for {int((now - opened).total_seconds() // 3600)} h" if opened else ""
        return f"Resolved at {_utc(_iso(now))}: {CLEARED[kind]}{hours}.\n\n{FOOTER}"
    head = SAYS[kind] if action == "open" else f"Still open at {_utc(_iso(now))}."
    return f"{head}\n\n{_table(kind, facts)}\n\nWhere to look on the host: {HINTS[kind]}\n\n{FOOTER}"


# ── delivery ────────────────────────────────────────────────────────────────

class Undelivered(RuntimeError):
    """A notice did not go out; its text is an error class (``error_class``), safe for a log line."""


def _env() -> dict:
    return {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_NO_UPDATE_NOTIFIER": "1", "NO_COLOR": "1"}


def _call(argv: list[str], stdin: str = "") -> str:
    try:
        out = subprocess.run(argv, input=stdin, capture_output=True, text=True, timeout=TIMEOUT, env=_env())
    except FileNotFoundError:
        raise Undelivered("not found") from None
    except subprocess.TimeoutExpired:
        raise Undelivered("timeout") from None
    except OSError as e:
        raise Undelivered(type(e).__name__) from None
    if out.returncode != 0:
        raise Undelivered(error_class(f"exit {out.returncode} {out.stderr or ''}"))
    return out.stdout or ""


def _gh_label(cfg: Config, st: dict) -> None:
    """Make sure the label exists (once per repo and label; ``--force`` leaves an existing one as it is)."""
    key = f"{repo(cfg)} {label(cfg)}"
    if st.get("label_ready") == key:
        return
    _call([gh_path(cfg), "label", "create", label(cfg), "--repo", repo(cfg), "--color", LABEL_COLOR,
           "--description", "Notices from the journal engine", "--force"])
    st["label_ready"] = key


def deliver(cfg: Config, st: dict, kind: str, inc: dict, action: str, now: datetime) -> int | None:
    """Send one notice. Returns the issue number after ``open`` (None when it is not known); raises
    ``Undelivered``."""
    text = body(kind, action, inc, now)
    cmd = command(cfg)
    if cmd:
        values = {"action": action, "kind": kind, "title": title(kind), "issue": str(inc.get("issue") or "")}
        fill = re.compile(r"\{(action|kind|title|issue)\}")  # (any other brace stays as written)
        out = _call([fill.sub(lambda m: values[m.group(1)], part) for part in cmd], text)
        m = re.search(r"(?:/issues/|#)(\d+)", out) or re.fullmatch(r"\s*(\d+)\s*", out)
        return int(m.group(1)) if m else None
    gh, where = gh_path(cfg), ["--repo", repo(cfg)]
    if action == "open":
        _gh_label(cfg, st)
        out = _call([gh, "issue", "create", *where, "--title", title(kind), "--label", label(cfg),
                     "--body-file", "-"], text)
        m = re.search(r"/issues/(\d+)", out)
        return int(m.group(1)) if m else None
    if not inc.get("issue"):
        return None  # filed with no number known: nothing to comment on or close
    if action == "update":
        _call([gh, "issue", "comment", str(inc["issue"]), *where, "--body-file", "-"], text)
    else:
        _call([gh, "issue", "close", str(inc["issue"]), *where, "--comment", text])
    return inc.get("issue")


def _retry_wait(cfg: Config, tries: int) -> timedelta:
    return min(RETRY_FIRST * 2 ** max(0, min(tries - 1, 8)), timedelta(hours=_number(cfg, "update_hours")))


def _due(st: dict, now: datetime) -> list[tuple[str, str]]:
    out = []
    for kind in KINDS:
        inc = (st.get("incidents") or {}).get(kind)
        if not inc or not inc.get("pending"):
            continue
        wait = _at(inc.get("next_try_at"))
        if wait is None or now >= wait:
            out.append((kind, inc["pending"]))
    return out[:PER_PASS]


@contextlib.contextmanager
def _run_lock(cfg: Config):
    """One ops pass at a time (the tick's and ``journal ops check``'s); a pass that finds one running skips. It is
    not ``tick.lock``, so a hung tick cannot stop ``journal ops check`` from saying so."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / RUN_LOCK, "a+") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def run(cfg: Config, now: datetime, log: list[str], *, crashed: dict[str, str] | None = None,
        from_tick: bool = True) -> None:
    """One pass: judge every kind, move the incidents on, deliver what is due. Never raises: a failure is a log line
    with "failed" (the tick files it in ``errors.jsonl``) and is tried again on a later pass."""
    try:
        _run(cfg, now, log, crashed, from_tick)
    except Exception as e:  # noqa: BLE001 - the ops notice must never break the tick
        log.append(f"ops: pass failed ({type(e).__name__})")


def _run(cfg: Config, now: datetime, log: list[str], crashed, from_tick: bool) -> None:
    path = cfg.state_dir / STATE
    with _run_lock(cfg) as mine:
        if not mine:
            return
        found = judge(cfg, now, ledger.read_state(path), from_tick=from_tick)
        with ledger.locked_state(path) as st:
            if from_tick:
                note_tick(st, now, crashed or {})
                if found.get("step_failing") is not HOLD:  # (judged on the streaks just written)
                    found["step_failing"] = _step_failing(cfg, st)
            had = set(st.get("incidents") or {})
            reconcile(cfg, st, found, now)
            log.extend(f"ops: incident {kind} opened" for kind in st.get("incidents") or {} if kind not in had)
            todo = _due(st, now) if delivery(cfg) != "off" else []
        for kind, action in todo:
            with ledger.locked_state(path) as st:
                inc = dict((st.get("incidents") or {}).get(kind) or {})
                label_state = {"label_ready": st.get("label_ready")}
            if inc.get("pending") != action:
                continue
            try:
                issue = deliver(cfg, label_state, kind, inc, action, now)
                error = None
            except Undelivered as e:
                issue, error = None, str(e)
            with ledger.locked_state(path) as st:
                if label_state.get("label_ready"):
                    st["label_ready"] = label_state["label_ready"]
                cur = (st.get("incidents") or {}).get(kind)
                if cur is None or cur.get("pending") != action:
                    continue
                if error:
                    cur["tries"] = _int(cur.get("tries")) + 1
                    cur["error"] = error
                    cur["next_try_at"] = _iso(now + _retry_wait(cfg, cur["tries"]))
                    log.append(f"ops: notice {action} for {kind} failed ({error}); tried again later")
                    continue
                cur.pop("error", None)
                cur.pop("next_try_at", None)
                cur.update(pending=None, tries=0, notified_at=_iso(now))
                if action == "open":
                    cur.update(delivered=True, issue=issue)
                if action == "resolve":
                    _close(st, kind, cur, now)
                where = f" (#{issue})" if issue else ""
                log.append(f"ops: notice {action} for {kind}{where}")


def check(cfg: Config, now: datetime | None = None, *, dry_run: bool = False) -> list[str]:
    """``journal ops check``: one pass from outside the tick (``tick_stale`` is judged only here). With ``dry_run``
    nothing is written or sent: the notices a pass would open are printed as they would be filed."""
    now = now or datetime.now(timezone.utc)
    log: list[str] = []
    if not dry_run:
        run(cfg, now, log, from_tick=False)
        return log
    st = json.loads(json.dumps(ledger.read_state(cfg.state_dir / STATE)))
    reconcile(cfg, st, judge(cfg, now, st, from_tick=False), now)
    for kind, action in _due(st, now):
        inc = st["incidents"][kind]
        log.append(f"[dry-run] {action} {kind}: {title(kind)}\n{body(kind, action, inc, now)}")
    return log or ["[dry-run] nothing to file"]


# ── status ──────────────────────────────────────────────────────────────────

def summary(cfg: Config) -> dict:
    """For ``journal status``: how notices go out, and each open incident (kind, since, issue, state)."""
    st = ledger.read_state(cfg.state_dir / STATE)
    rows = []
    for kind in KINDS:
        inc = (st.get("incidents") or {}).get(kind)
        if not inc:
            continue
        state = {"open": "not filed yet", "update": "update owed", "resolve": "close owed"}.get(
            inc.get("pending") or "", "filed")
        rows.append({"kind": kind, "since": (inc.get("facts") or {}).get("since") or inc.get("opened_at"),
                     "opened_at": inc.get("opened_at"), "issue": inc.get("issue"), "state": state,
                     "resolved_at": inc.get("resolved_at"), "error": inc.get("error")})
    return {"delivery": delivery(cfg), "repo": repo(cfg) or None, "incidents": rows}


def format_summary(s: dict) -> str | None:
    if not s.get("incidents"):
        return None
    how = {"off": "not sent: ops.repo is not set", "command": "via ops.command",
           "github": f"issues in {s.get('repo')}"}.get(s.get("delivery"), "")
    parts = []
    for r in s["incidents"]:
        bits = [f"since {_utc(r.get('since'))}", r["state"]]
        if r.get("issue"):
            bits.append(f"#{r['issue']}")
        if r.get("error"):
            bits.append(f"last try: {r['error']}")
        parts.append(f"{r['kind']} ({', '.join(bits)})")
    return f"Ops incidents ({how}): " + "; ".join(parts)
