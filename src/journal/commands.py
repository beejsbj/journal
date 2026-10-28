"""What the journal's commands mean, independent of any chat platform: ``/start <id>``, ``/j``, ``/x``, the four
ritual sends, the program commands (``/next`` ``/skip`` ``/back`` ``/break`` ``/abandon`` ``/program``) and ``/web``.
``SPECS`` lists them; an adapter registers each under its platform's mechanism and supplies the one thing the engine
cannot, ``spawn`` (how to run ``python -m journal <args>`` detached, so a send survives a restart of the gateway and
waits for a running tick). A spec's ``run(cfg, raw_args, spawn)`` returns the text to answer with, or None for nothing.

``/start <id>``: the id is resolved against the library when the command arrives, not when the adapter registered it, so a
practice he adds (or forks, or turns off) is startable at once with no restart. The four ritual commands
(``/morning`` …, and ``/morning-journal``, ``/evening-journal``) are the same thing with the id given. An adapter
registers the commands, calls ``start`` and, when it names a practice, spawns ``journal send <id>``; when it names a
program or a situational definition (by id or alias), ``journal program start <id>`` (``/start fix-life 3`` jumps to
session 3, ``/start fix-life insights`` compiles the insights document).
"""
from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass

from . import library
from .config import Config
from .library import Library, Practice, Program

log = logging.getLogger("journal.commands")
START = "start"
ROUTED = "journal-start"  # the registered name of /start <id> where the platform owns /start itself
Spawn = Callable[..., None]  # spawn(cfg, *argv): run ``python -m journal *argv`` detached; raises when it cannot
_START = re.compile(r"^\s*/start(?:@\w+)?[ \t]+(\S.*)$", re.I | re.S)
_PROGRAM = re.compile(r"^\s*/(next|skip|back|break|program)(?:@\w+)?\s*$", re.I)
# Older command names of two rituals, still registered: /start accepts them too.
ALIASES = {"morning-journal": "morning", "evening-journal": "evening"}


@dataclass(frozen=True)
class Start:
    """The answer to ``/start <words>``: a practice to send, a program to start (``session``: jump there;
    ``insights``: compile its document instead), or (both None) what to say instead."""
    practice: Practice | None
    reply: str = ""
    program: Program | None = None
    session: int | None = None
    insights: bool = False


def menu(lib: Library) -> str:
    """The startable practices and programs, as he would type them."""
    items = [f"/start {p.id} ({p.title})" for p in lib.enabled()]
    items += [f"/start {p.aliases[0] if p.aliases else p.id} ({p.title})" for p in lib.enabled_programs()]
    return "You have: " + ", ".join(items) + "."


def _program_start(lib: Library, prog: Program, rest: list[str]) -> Start:
    if not prog.enabled:
        return Start(None, f"{prog.title} is turned off (enabled: false in its file). {menu(lib)}")
    words = [w.lower() for w in rest if w.lower() != "session"]
    if not words:
        return Start(None, program=prog)
    if words[0] == "insights" and not prog.situational:
        return Start(None, program=prog, insights=True)
    if words[0].isascii() and words[0].isdigit() and not prog.situational:
        n = int(words[0])
        if 1 <= n <= len(prog.sessions):
            return Start(None, program=prog, session=n)
        return Start(None, f"{prog.title} has sessions 1 to {len(prog.sessions)}.")
    hint = f" (/start {prog.id} 3 jumps to session 3; /start {prog.id} insights compiles the insights)" \
        if not prog.situational else ""
    return Start(None, f"/start {prog.aliases[0] if prog.aliases else prog.id} takes no “{rest[0]}”{hint}.")


def start(cfg: Config, words: str) -> Start:
    """Resolve the first word after ``/start`` (case-insensitive; the two legacy ritual aliases accepted)."""
    lib = library.load(cfg)
    word = (words or "").strip().split(maxsplit=1)[0].lower() if (words or "").strip() else ""
    if not word:
        return Start(None, f"Which practice? {menu(lib)}")
    pid = ALIASES.get(word, word)
    try:
        practice = lib.get(pid)
    except library.UnknownDefinition:
        try:
            prog = lib.program(word)
        except library.UnknownDefinition:
            return Start(None, f"There's no practice called “{word}”. {menu(lib)}")
        return _program_start(lib, prog, (words or "").split()[1:])
    if not practice.enabled:
        return Start(None, f"{practice.title} is turned off (enabled: false in its file). {menu(lib)}")
    return Start(practice)



# --- routing: which messages are commands -------------------------------------------------------------------------

def routed_start(text: str) -> str | None:
    """``/start <id>`` as ``/journal-start <id>``; None for anything else, ``/start`` alone included. (Where the
    platform owns ``/start`` it is renamed on the way in; the id is resolved when the command runs.)"""
    m = _START.match(text or "")
    return f"/{ROUTED} {m.group(1)}" if m else None


def routed_program(text: str) -> str | None:
    """The program command a message is exactly (``next``, ``skip``, ``back``, ``break``, ``program``), else None.
    (Not ``/web``: it goes through the platform's own command path, where its gating applies.)"""
    m = _PROGRAM.match(text or "")
    return m.group(1).lower() if m else None


# --- what the commands do ------------------------------------------------------------------------------------------

def _failed(cfg: Config, what: str, e: Exception) -> str:
    who = (cfg.guard.get("escalate_to") or "").strip()
    return (f"Couldn't {what} ({type(e).__name__})." + (f" {who} can look into it." if who else
                                                         " `journal doctor` can say why."))


def send_practice(cfg: Config, pid: str, spawn: Spawn) -> str:
    """Send ``pid`` now, through the engine, in the background; what to answer."""
    try:
        spawn(cfg, "send", pid)
        lead = f"{cfg.names.emoji} " if cfg.names.emoji else ""
        return f"{lead}Your {pid} journal is on its way."
    except Exception as e:
        log.exception("manual %s trigger failed", pid)
        return _failed(cfg, f"start the {pid} journal", e)


def _busy(cfg: Config, program_id: str) -> str | None:
    """The refusal when another program is unfinished (``programs.one_at_a_time``), said as the command's own reply,
    in the channel it came from; None to go ahead. Read without the tick's lock: the spawned command asks again under
    it, and that answer is the one that counts (a refusal there comes back through ``--notify``). A read that fails
    goes ahead for the same reason."""
    try:
        from datetime import datetime, timezone

        from . import programs
        run = programs.current_run(cfg, program_id)
        if run is not None and programs.is_expired(cfg, run, datetime.now(timezone.utc)):
            run = None  # (``start`` closes it and begins a new one)
        other = programs.blocking(cfg, program_id, run)
        return programs.busy_text(cfg, other, program_id) if other else None
    except Exception:  # noqa: BLE001 - the child decides under the lock
        log.exception("/start %s: could not check for an unfinished program", program_id)
        return None


def _start_program(cfg: Config, found: Start, spawn: Spawn) -> str:
    """``/start <program>``: start or resume it (or jump to a session, or compile the insights) in the background."""
    prog = found.program
    lead = f"{prog.emoji or cfg.names.emoji} " if (prog.emoji or cfg.names.emoji) else ""
    try:
        if found.insights:
            spawn(cfg, "program", "insights", prog.id, "--notify")
            return f"{lead}Compiling your {prog.title} insights into the journal."
        busy = _busy(cfg, prog.id)
        if busy:
            return busy
        spawn(cfg, "program", "start", prog.id, "--notify",
              *(["--session", str(found.session)] if found.session else []))
    except Exception as e:
        log.exception("/start %s failed", prog.id)
        return _failed(cfg, f"start {prog.title}", e)
    return f"{lead}{prog.title}" + (f": session {found.session}" if found.session else "") + " is on its way."


def start_reply(cfg: Config, raw_args: str, spawn: Spawn) -> str:
    """``/start <id>``: send that practice (or start that program) now, or say which ones there are."""
    word = (raw_args or "").strip().split(maxsplit=1)[0] if (raw_args or "").strip() else "a practice"
    try:
        found = start(cfg, raw_args)
    except Exception as e:  # the library could not load at all (a broken install): say so, don't go silent
        log.exception("/start %s: the practice library failed", word)
        return (f"Couldn't start {word}: the practice library didn't load ({type(e).__name__}); "
                "`journal doctor` says why.")
    if found.program is not None:
        return _start_program(cfg, found, spawn)
    return send_practice(cfg, found.practice.id, spawn) if found.practice else found.reply


# The program commands: their names, the ``journal program`` action each spawns, and what Telegram's menu says.
PROGRAM_COMMANDS = (
    ("next", "next", "Program: move on to the next phase now"),
    ("skip", "skip", "Program: skip this phase"),
    ("back", "back", "Program: go back to the previous phase"),
    ("break", "pause", "Program: take a break (/start <id> picks it up again; it stays unfinished)"),
)


def program_action_reply(cfg: Config, name: str, action: str, spawn: Spawn) -> str | None:
    """``/next`` ``/skip`` ``/back`` ``/break``: act on the run he last did something in, in the background. The
    next phase arriving is the answer, so they say nothing on success (``/break`` confirms)."""
    try:
        from . import programs
        run = programs.current_run(cfg)
    except Exception as e:
        log.exception("/%s: could not read the program runs", name)
        return _failed(cfg, f"/{name}", e)
    if run is None:
        return "No program is running. /program lists them; /start <id> begins one."
    if action != "pause" and run.get("status") == "paused":
        return f"It's on a break: /start {run['program']} picks it up where you left off."
    try:
        # bound to the phase it was given for: a tap that lands after the run moved on (the settle, a second
        # tap, the tick holding the lock) does nothing instead of skipping the phase that arrived meanwhile
        where = ["--expect", programs.where(run)] if action in ("next", "skip", "back") else []
        spawn(cfg, "program", action, run["id"], "--notify", *where)
    except Exception as e:
        log.exception("/%s failed", name)
        return _failed(cfg, f"/{name}", e)
    if action == "pause":
        return f"On a break. /start {run['program']} picks it up where you left off."
    return None


ABANDON_USAGE = "Name the run to end: /abandon <id>. Its entries stay; /program says what is running."


def abandon_reply(cfg: Config, raw_args: str, spawn: Spawn) -> str:
    """``/abandon <id>``: end that program's run where it is (``journal program abandon``), in the background. An
    ended run is not undone, so it has to be named: ``/abandon`` alone ends nothing and says what could be ended. The
    run's entries stay; finishing or abandoning a program is what lets another one start (DECISIONS.md,
    ONE-PROGRAM)."""
    try:
        from . import programs
        runs = programs.active_runs(cfg)
        lib = library.load(cfg)
    except Exception as e:
        log.exception("/abandon: could not read the program runs")
        return _failed(cfg, "/abandon", e)

    def word_of(run: dict) -> str:
        try:
            prog = lib.program(run["program"])
            return prog.aliases[0] if prog.aliases else prog.id
        except library.UnknownDefinition:
            return run["program"]
    words = (raw_args or "").split()
    word = words[0].lower() if words else ""
    try:
        prog = lib.program(word) if word else None
    except library.UnknownDefinition:
        prog = None
    pid = prog.id if prog else word  # (a run whose definition is gone is still named by its program id)
    mine = [r for r in runs if r["program"] == pid]
    # finished, with session 10's interrupts still to come: abandoning cancels them (the run stays completed)
    holding = [r for r in reversed(programs.all_runs(cfg))
               if r.get("status") == "completed" and programs.pending_interrupts(cfg, r)]
    held = None if mine or not word else next((r for r in holding if r["program"] == pid), None)
    if not runs and not holding:
        return "No program is running, so there is nothing to abandon."
    named = [f"/abandon {word_of(r)}" for r in runs]
    named += [f"/abandon {word_of(r)} (cancels its interrupts still to come)" for r in holding
              if r["program"] not in {x["program"] for x in runs}]
    if not word:
        return "Which one? " + ", ".join(named) + ". Its entries stay."
    if not mine and held is None:
        return f"There's no {word} run to abandon. You can name: " + ", ".join(named) + "."
    run = held or programs.current_run(cfg, pid) or mine[-1]
    try:
        spawn(cfg, "program", "abandon", run["id"], "--notify")
    except Exception as e:
        log.exception("/abandon %s failed", pid)
        return _failed(cfg, f"abandon {word}", e)
    title = prog.title if prog else word
    if held is not None:
        n = len(programs.pending_interrupts(cfg, held))
        return f"Cancelling the {n} interrupt(s) still to come from {title}. Its entries stay in the journal."
    again = f"; /start {word_of(run)} begins it afresh" if prog else ""
    back = ""
    if prog is not None and prog.situational:  # a program's phase it took the chat from: how to pick it up
        from datetime import datetime, timezone
        back = programs.still_open_line(cfg, prog, datetime.now(timezone.utc))
    return f"Ending {title} where it is. Its entries stay in the journal{again}." + (f" {back}" if back else "")


def program_status_reply(cfg: Config) -> str:
    """``/program``: where each run is, and what can be started (read-only, so it answers at once)."""
    try:
        from . import programs
        return programs.status_text(cfg)
    except Exception as e:
        log.exception("/program failed")
        return f"Couldn't read the programs ({type(e).__name__}); `journal doctor` says why."


def run_program_command(cfg: Config, name: str, spawn: Spawn) -> str | None:
    """A program command run on arrival (an adapter whose platform would only run it while the companion is idle
    runs it itself, before that check): what it answers, None for nothing to say."""
    if name == "program":
        return program_status_reply(cfg)
    for cmd, action, _desc in PROGRAM_COMMANDS:
        if cmd == name:
            return program_action_reply(cfg, name, action, spawn)
    return None


def trigger_reply(cfg: Config, kind: str, spawn: Spawn) -> str:
    """A ritual's own command (``/evening``): ``/start evening``."""
    try:
        found = start(cfg, kind)
    except Exception:  # the library could not load: still try, the send itself will report it
        log.exception("manual %s trigger: the practice library failed", kind)
        return send_practice(cfg, kind, spawn)
    return send_practice(cfg, kind, spawn) if found.practice else found.reply


def web_reply(cfg: Config) -> str:
    """``/web``: the web app's address (``config.serve_site``, as serve builds it), or plainly that there is none.
    Never a pairing link or a token: the chat's history is not a place for a secret. A browser signs in by its
    Tailscale identity where serve is set up for it; otherwise pairing stays a step on the server
    (``journal serve pair``)."""
    from .config import serve_host_ok, serve_site, serve_socket, serve_tailscale_users
    host = cfg.serve.get("host")
    if not host:
        return "The web app isn't set up here: serve.host isn't set, so there's no address to give."
    if not serve_host_ok(host):
        return "The web app's address isn't usable (serve.host is not a bare host name); `journal doctor` says why."
    if serve_tailscale_users(cfg) and serve_socket(cfg) is not None:
        how = "On a device signed in to Tailscale it lets you straight in."
    else:
        how = "A browser that isn't paired yet is paired from the server, with `journal serve pair`."
    return f"Your journal on the web: {serve_site(cfg)}\n{how}"


J_USAGE = ("Write it right after the command: /j <anything worth keeping>. A voice note or audio file captioned /j "
           "works too.")  # (text after /j is captured on arrival; this answers a bare /j)
OFF_RECORD_FAILED = ("Couldn't take this conversation off the record (the journal's state isn't writable). Start "
                     "each private message with /x instead, and ask for it to be looked into.")


def off_record_reply(cfg: Config, raw_args: str = "") -> str:
    """``/x``. "/x <text>" is rewritten on arrival (``inbound``), so this normally runs only for a bare /x, which
    arrival has recorded as the start of an off-the-record thread. It says so only when that record is really there
    (arrival fails open); otherwise it records it itself, or says it couldn't."""
    from datetime import datetime, timedelta, timezone
    from . import conversations
    from .config import quiet_minutes
    try:
        minutes = f"{quiet_minutes(cfg):g}"
        now = datetime.now(timezone.utc)
        starts = (conversations.snapshot(cfg).get("off_threads") or {}).values()
        recent = any(timedelta(0) <= now - datetime.fromisoformat(t) < timedelta(minutes=2) for t in starts)
        if not recent:
            conversations.note_off(cfg, None, now, thread_start=True)
    except Exception:
        log.exception("journal: /x could not record the thread as off the record")
        return OFF_RECORD_FAILED
    if (raw_args or "").strip():  # arrival didn't rewrite it: only this message was meant
        return ("That message didn't reach me, but it isn't saved. Nothing more from this conversation goes into "
                f"the journal until it has been quiet for {minutes} minutes; send it again without the command.")
    return (f"Off the record: nothing more from this conversation goes into the journal until it has been quiet "
            f"for {minutes} minutes (or the next journal prompt). /j still saves a note.")


# --- the list an adapter registers ---------------------------------------------------------------------------------

@dataclass(frozen=True)
class Spec:
    """One command: its registered ``name``, its menu ``description``, and ``run(cfg, raw_args, spawn)`` (cfg is
    None when ``needs_config`` is false). ``unconfigured(raw_args, exc)`` is what to answer when the adapter could
    not load the configuration for it."""
    name: str
    description: str
    run: Callable[[Config | None, str, Spawn], str | None]
    needs_config: bool = True
    unconfigured: Callable[[str, Exception], str] = lambda raw, e: f"The journal isn't configured ({type(e).__name__})."


def opt_out_name(cfg: Config | None) -> str:
    """The registered name of the off-the-record command: the configured prefix without its slash (default ``x``)."""
    from .config import opt_out_prefix
    try:
        prefix = opt_out_prefix(cfg) if cfg is not None else "/x"
    except Exception:
        prefix = "/x"
    return prefix.lstrip("/").lower() or "x"


def start_spec() -> Spec:
    def unconfigured(raw: str, e: Exception) -> str:
        word = (raw or "").strip().split(maxsplit=1)[0] if (raw or "").strip() else "a practice"
        return f"Couldn't start {word}: the journal isn't configured ({type(e).__name__})."
    return Spec(ROUTED, "Send a practice or start a program: /start <id> (alone: the list)",
                lambda cfg, raw, spawn: start_reply(cfg, raw, spawn), unconfigured=unconfigured)


def trigger_spec(name: str, kind: str) -> Spec:
    return Spec(name, f"Send the {kind} journal now", lambda cfg, raw, spawn: trigger_reply(cfg, kind, spawn),
                unconfigured=lambda raw, e: f"Couldn't start the {kind} journal: the journal isn't configured "
                                            f"({type(e).__name__}).")


def program_spec(name: str, action: str, description: str) -> Spec:
    return Spec(name, description, lambda cfg, raw, spawn: program_action_reply(cfg, name, action, spawn),
                unconfigured=lambda raw, e: f"Couldn't /{name}: the journal isn't configured ({type(e).__name__}).")


def j_spec() -> Spec:
    return Spec("j", "Save a note to today's journal: /j <text>", lambda cfg, raw, spawn: J_USAGE, needs_config=False)


def off_record_spec(cfg: Config | None = None) -> Spec:
    return Spec(opt_out_name(cfg), "Off the record: /x <text> for one message, /x alone for the rest of this chat",
                lambda cfg, raw, spawn: off_record_reply(cfg, raw), unconfigured=lambda raw, e: OFF_RECORD_FAILED)


def abandon_spec() -> Spec:
    return Spec("abandon", "Program: end a run where it is: /abandon <id> (its entries stay)",
                lambda cfg, raw, spawn: abandon_reply(cfg, raw, spawn),
                unconfigured=lambda raw, e: f"Couldn't /abandon: the journal isn't configured ({type(e).__name__}).")


def program_status_spec() -> Spec:
    return Spec("program", "Program: where you are, and what can be started",
                lambda cfg, raw, spawn: program_status_reply(cfg),
                unconfigured=lambda raw, e: f"Couldn't read the programs ({type(e).__name__}); `journal doctor` says why.")


def web_spec() -> Spec:
    return Spec("web", "The web app's address", lambda cfg, raw, spawn: web_reply(cfg),
                unconfigured=lambda raw, e: f"Couldn't give the web app's address: the journal isn't configured "
                                            f"({type(e).__name__}).")


def specs(cfg: Config | None = None) -> tuple[Spec, ...]:
    """Every command an adapter registers, in registration order. ``cfg`` only names the off-the-record command
    (``conversations.opt_out_prefix``); None gives the default ``/x``."""
    out = [j_spec(), off_record_spec(cfg)]
    out += [trigger_spec(kind, kind) for kind in library.builtin_ids()]
    out += [trigger_spec(alias, kind) for alias, kind in ALIASES.items()]
    out.append(start_spec())
    out += [program_spec(name, action, description) for name, action, description in PROGRAM_COMMANDS]
    out.append(abandon_spec())
    out.append(program_status_spec())
    out.append(web_spec())
    return tuple(out)


SPECS = specs()
