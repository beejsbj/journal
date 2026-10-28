"""Slash commands: /j, /x (off the record), /start <id> to send a practice now (/morning … /weekly too) or to start
a program, the program commands /next, /skip, /back, /break (pause), /abandon <id> and /program (status), and /web
(the web app's address).

Hermes owns ``/start`` itself (it acknowledges Telegram's start ping, and refuses a plugin command of that name),
so the practice command is registered as ``/journal-start`` and the dispatch hook rewrites ``/start <id>`` in the
journal's chat to it before Hermes routes the message (``routed_start``); ``/start`` alone stays Hermes'. The id is
resolved against the library when the command arrives (``journal.commands.start``), so a practice he adds needs no
restart; the ritual commands are registered at gateway start and send their id the same way.

Hermes also owns ``/pause`` (its emergency stop) and ``/status``, so the program commands use names it does not:
``/break`` pauses the run, ``/program`` says where it is (a run picks up again with ``/start <id>``). They are
registered as they are, so the phase's reply keyboard (which sends them as typed text) works while she is idle.
Hermes runs plugin commands only on its idle path: while she is mid-reply (the moment right after he answers a
phase, which is when he taps ``/next``) an unregistered command is steered into her turn as text. So the dispatch
hook, which runs before that check, handles them itself in the journal's chat (``routed_program``,
``run_program_command``) and drops the event.

What each command does is the engine's (``journal.commands.SPECS``); this adapter registers them, turns a handler's
call into ``spec.run(cfg, raw_args, spawn)`` and supplies ``spawn``. A send runs the engine in a detached background process (``python -m journal send <id>``, ``python -m journal
program <action>``), so it survives a gateway restart and waits for a running tick instead of racing it. Its output
goes to ``manual-trigger.log`` in the state dir.
"""
from __future__ import annotations

import logging
import subprocess

from .. import commands as jcommands
from ..commands import PROGRAM_COMMANDS, ROUTED, routed_program, routed_start  # noqa: F401 (the hook, tests)
from . import runtime

log = logging.getLogger("journal.hermes")


def spawn(cfg, *args: str) -> None:
    """Run ``python -m journal <args>`` detached (it outlives a gateway restart and waits for a running tick); its
    output goes to ``manual-trigger.log``. Raises when it cannot be started."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    with open(cfg.state_dir / "manual-trigger.log", "a") as out:
        subprocess.Popen([runtime.python(), "-m", "journal", *args], cwd=str(cfg.state_dir),
                         stdout=out, stderr=out, start_new_session=True, env=runtime.child_env(cfg))


def adapt(spec: jcommands.Spec):
    """A Hermes command handler for an engine command: the configuration is loaded when the command arrives (a
    practice he adds is startable at once, and a configuration that fails says so in the chat)."""
    def handler(raw_args: str = "", **_kw):
        cfg = None
        if spec.needs_config:
            try:
                cfg = runtime.load_config()
            except Exception as e:
                log.exception("/%s: configuration failed", spec.name)
                return spec.unconfigured(raw_args, e)
        return spec.run(cfg, raw_args, spawn)  # (spawn looked up now: the module's, whatever replaced it)
    return handler


start = adapt(jcommands.start_spec())
program_status = adapt(jcommands.program_status_spec())
off_record = adapt(jcommands.off_record_spec())
j_usage = adapt(jcommands.j_spec())


def program_action(name: str, action: str):
    """``/next`` ``/skip`` ``/back`` ``/break``."""
    desc = next(d for n, _a, d in PROGRAM_COMMANDS if n == name)
    return adapt(jcommands.program_spec(name, action, desc))


def trigger(kind: str):
    """A ritual's own command (``/evening``): ``/start evening``."""
    return adapt(jcommands.trigger_spec(kind, kind))


def opt_out_name() -> str:
    try:
        return jcommands.opt_out_name(runtime.load_config())
    except Exception:
        return jcommands.opt_out_name(None)


def register(ctx) -> None:
    try:
        cfg = runtime.load_config()
    except Exception:  # unconfigured yet: the default names; the commands themselves say so when run
        cfg = None
    for spec in jcommands.specs(cfg):
        ctx.register_command(spec.name, adapt(spec), description=spec.description)
