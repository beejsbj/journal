"""The ``journal`` command line.

    journal [--config FILE] [--hermes-home DIR] <command>

    tick                     one scheduler tick: send due practices, fold, annotate, reply, prune
    send <id>                send a practice now (any id in the library; waits for a running tick); --dry-run previews
    sweep                    capture replies the gateway hook missed (busy path), from the sessions DB
    backfill-conversations   save past chat (both sides) into the journal by the thread rules; dry run by default
    status                   last tick, due rituals, open prompts, notebook, recent errors
    today                    the today block the companion gets at the start of a thread (--json: the data)
    doctor                   configuration, journal root, token, sources (and Hermes cron when under Hermes)
    practices                the practice library: list, show <id>, validate [FILE…], render <id|FILE>, fork <id>,
                             reset <id> (back to the shipped built-in), diff <id> (the default against your edit)
    config                   settings the person may change: list | get KEY | set KEY VALUE (the web app's allow-list)
    serve                    the private web app and its API (foreground); serve pair | token --rotate | check,
                             serve --print-unit (the systemd user unit from this config)
    program                  programs and situational sessions: start <id> [--session N], next|skip|back|pause|resume|
                             abandon [RUN], status, settle RUN, insights [RUN|ID]
    mcp                      the journal's tools for an MCP client (Claude Desktop, Claude Code, …) over stdio;
                             needs the optional extra journal[mcp]
    migrate-config           convert an old journal.json (read-only on the source); a `schedule` block becomes forked
                             practice files
    migrate-state            copy an old state directory (never moves)

Exit codes: 0 ok; 1 a check or delivery failed; 2 unusable configuration or arguments.
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from . import config as jconfig


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="journal", description="Deterministic guided journaling over a chat.")
    p.add_argument("--version", action="version", version=f"journal {__version__}")
    p.add_argument("--config", metavar="FILE", help="JSON config file (default: $JOURNAL_CONFIG, the Hermes "
                                                    "`config_file` setting, or ~/.config/journal/config.json)")
    p.add_argument("--hermes-home", metavar="DIR", help="Hermes profile home to read settings and state from "
                                                        "(default: $HERMES_HOME; unset = standalone)")
    subs = p.add_subparsers(dest="command", metavar="<command>")
    t = subs.add_parser("tick", help="Run one tick (what the scheduler runs every few minutes)")
    t.add_argument("--dry-run", action="store_true", help="Preview due sends; change nothing")
    subs.add_parser("sweep", help="Capture journal replies the gateway hook missed (reads the Hermes sessions DB)")
    add_backfill_parser(subs)
    s = subs.add_parser("send", help="Send a practice now")
    s.add_argument("ritual", metavar="ID", help="a practice id (journal practices list)")
    s.add_argument("--dry-run", action="store_true", help="Print the message instead of sending it")
    s.add_argument("--hold-off-record", action="store_true",
                   help="Send nothing while a bare /x window is open (a model asking for him: MCP's journal_start)")
    st = subs.add_parser("status", help="Show what the journal is doing")
    st.add_argument("--json", action="store_true")
    td = subs.add_parser("today", help="Print the today block the companion gets at the start of a thread")
    td.add_argument("--json", action="store_true", help="The data behind it (what GET /today will serve)")
    d = subs.add_parser("doctor", help="Check that everything the journal needs is in place")
    d.add_argument("--json", action="store_true")
    d.add_argument("--deep", action="store_true",
                   help="Also run each enabled context source once and count how the newest 50 chat rows read")
    add_practices_parser(subs)
    add_serve_parser(subs)
    add_program_parser(subs)
    subs.add_parser("mcp", help="Serve the journal's tools to an MCP client over stdio (needs journal[mcp])")
    add_config_parser(subs)
    mc = subs.add_parser("migrate-config", help="Convert an old journal.json to the new format")
    mc.add_argument("--from", dest="src", required=True, metavar="FILE")
    mc.add_argument("--to", dest="dest", metavar="FILE", help="Write here (default: print to stdout)")
    mc.add_argument("--merge", metavar="FILE", help="JSON overlay applied on top (deployment values)")
    mc.add_argument("--force", action="store_true", help="Overwrite --to if it exists")
    mc.add_argument("--practices-dir", metavar="DIR",
                    help="Where a `schedule` block's forked practice files go (default: where the new config "
                         "puts practices_dir; written only with --to). If that is not where the config would "
                         "point, it is recorded in the new config")
    ms = subs.add_parser("migrate-state", help="Copy an old state directory into the new one")
    ms.add_argument("--from", dest="src", required=True, metavar="DIR")
    ms.add_argument("--to", dest="dest", metavar="DIR", help="Default: the configured state dir")
    ms.add_argument("--force", action="store_true", help="Replace existing files (kept as .bak-<time>)")
    ms.add_argument("--no-lock", action="store_true", help="Don't wait for the old tick/ledger locks")
    return p


def add_backfill_parser(subs) -> None:
    b = subs.add_parser("backfill-conversations", help="Save past chat into the journal by the conversation rules "
                                                       "(a dry run unless --write)")
    b.add_argument("--since", required=True, metavar="YYYY-MM-DD", help="Local date to start from")
    mode = b.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="List what would be written (the default)")
    mode.add_argument("--write", action="store_true", help="Write it")


def add_practices_parser(subs) -> None:
    pr = subs.add_parser("practices", help="List, show, check, render, fork or reset practice definitions")
    ps = pr.add_subparsers(dest="practices_command", metavar="<action>")
    li = ps.add_parser("list", help="Every practice: built-ins and your own files, with problems")
    li.add_argument("--json", action="store_true")
    sh = ps.add_parser("show", help="One practice: schedule, opener, questions with their ids")
    sh.add_argument("practice", metavar="ID")
    sh.add_argument("--json", action="store_true")
    va = ps.add_parser("validate", help="Check practice files (default: everything in the practices directory)")
    va.add_argument("files", nargs="*", metavar="FILE")
    re_ = ps.add_parser("render", help="Print the canonical Markdown of a practice (an id, or a file to tidy)")
    re_.add_argument("target", metavar="ID|FILE")
    fo = ps.add_parser("fork", help="Copy a built-in (a practice, program or situational one) into the practices "
                                    "directory to edit it in place")
    fo.add_argument("practice", metavar="ID")
    rs = ps.add_parser("reset", help="Remove your edit of a built-in: the shipped one applies again")
    rs.add_argument("practice", metavar="ID")
    df = ps.add_parser("diff", help="Your edit of a built-in against the shipped default, and what changed in the "
                                    "default since you edited it")
    df.add_argument("practice", metavar="ID")


def add_config_parser(subs) -> None:
    cf = subs.add_parser("config", help="List, read or change the settings the web app's Settings page may change")
    cs = cf.add_subparsers(dest="config_command", metavar="<action>")
    li = cs.add_parser("list", help="Every setting: its value, the layer that owns it, and whether it can be changed")
    li.add_argument("--json", action="store_true")
    ge = cs.add_parser("get", help="One setting (or a group such as windows)")
    ge.add_argument("key", metavar="KEY", help="dotted, as in `journal config list`")
    ge.add_argument("--json", action="store_true")
    se = cs.add_parser("set", help="Change a setting in the config file (checked exactly as the web app checks it)")
    se.add_argument("key", metavar="KEY")
    se.add_argument("value", metavar="VALUE", help="JSON (5, true, '{\"lat\": 1, \"lon\": 2}') or, when it is not "
                                                    "JSON, plain text (Europe/London)")
    se.add_argument("--json", action="store_true")


CONFIG_ACTIONS = ("list", "get", "set")
PRACTICE_ACTIONS = ("list", "show", "validate", "render", "fork", "reset", "diff")
PROGRAM_ACTIONS = ("start", "next", "skip", "back", "pause", "resume", "abandon", "status", "settle", "insights",
                   "refresh-summary")


def add_program_parser(subs) -> None:
    pr = subs.add_parser("program", help="Programs and situational sessions: start, move on, pause, status, insights")
    ps = pr.add_subparsers(dest="program_command", metavar="<action>")
    st = ps.add_parser("start", help="Start a program (or resume its run): an id or alias from `program status`")
    st.add_argument("target", metavar="ID")
    st.add_argument("--session", type=int, metavar="N", help="Jump to session N")
    st.add_argument("--new", action="store_true", help="Only if the program has no run in progress (what the web "
                    "app sends, so two requests at once start one run)")
    for name, what in (("next", "Move the run on to the next phase now"), ("skip", "Skip the open phase"),
                       ("back", "Send the previous phase again"), ("pause", "Pause the run"),
                       ("resume", "Resume a paused run"), ("abandon", "End the run where it is (its entries stay)")):
        a = ps.add_parser(name, help=what)
        a.add_argument("target", nargs="?", metavar="RUN", help="A run id (default: the run last active)")
        if name == "resume":
            a.add_argument("--paused", action="store_true", help="Only if the run is on a break")
        if name in ("next", "skip", "back"):
            a.add_argument("--expect", metavar="S:P:V", help="Only if the run is still at session S, phase P, "
                           "visit V (the phase the command was given for); otherwise do nothing")
    se = ps.add_parser("settle", help="Wait programs.settle_minutes, then move the run on if he has gone quiet")
    se.add_argument("target", metavar="RUN")
    se.add_argument("--now", action="store_true", help="Check at once instead of waiting first")
    ins = ps.add_parser("insights", help="Compile the insights document of a run (or of a program's latest run)")
    ins.add_argument("target", nargs="?", metavar="RUN|ID")
    rs = ps.add_parser("refresh-summary", help="Write a completed session's ## Summary again from his words as they "
                       "are now (what the web app spawns after he edits a phase answer)")
    rs.add_argument("target", metavar="RUN")
    rs.add_argument("--session", type=int, required=True, metavar="N")
    s = ps.add_parser("status", help="The programs in the library and the runs in progress")
    s.add_argument("--json", action="store_true")
    for sub in ps.choices.values():
        sub.add_argument("--notify", action="store_true", help=argparse.SUPPRESS)  # errors go to the chat


def _notify(cfg: jconfig.Config, text: str) -> None:
    """Tell him in the chat (a spawned ``/next`` that could not run): best effort, plain text."""
    try:
        cfg.channel().send(cfg.chat_id, html.escape(text, quote=False))
    except Exception:  # noqa: BLE001 - the log has it
        pass


def program(args, cfg: jconfig.Config) -> int:
    """``journal program …``: every transition waits for the tick's lock (``programs.locked``)."""
    import time

    from . import programs, tick
    action = getattr(args, "program_command", None)
    if action not in PROGRAM_ACTIONS:
        print("usage: journal program {" + ",".join(PROGRAM_ACTIONS) + "}", file=sys.stderr)
        return 2
    target = getattr(args, "target", None)
    notify = getattr(args, "notify", False)
    if action == "status":
        if getattr(args, "json", False):
            print(json.dumps(programs.status(cfg), indent=2, ensure_ascii=False))
        else:
            print(programs.status_text(cfg))
        return 0
    if action == "settle" and not getattr(args, "now", False):
        time.sleep(jconfig.settle_minutes(cfg) * 60 + 2)  # outside the lock: a later message restarts nothing
    log: list[str] = []
    code = 0
    pending = None
    if action == "refresh-summary":   # one waiting refresh per session is enough: it reads the entry once it has the lock
        run = programs.find_run(cfg, target or "")   # (the run's own id names the lock file, never the argument)
        if run is None:
            print(f"journal: no run called “{target}”; `journal program status` lists them.", file=sys.stderr)
            return 2
        target = run["id"]
        try:
            pending = programs.claim_refresh(cfg, target, args.session)
        except OSError as e:
            print(f"journal: cannot take the refresh lock in {cfg.state_dir}: {e}", file=sys.stderr)
            return 1
        if pending is None:
            return 0
    try:
        expect = programs.parse_where(args.expect) if getattr(args, "expect", None) else None
        with programs.locked(cfg):
            if pending is not None:   # (the tick's lock is held: an edit saved from here on needs a refresh of its own)
                programs.release_refresh(pending)
                pending = None
            now = datetime.now(timezone.utc)
            if action == "start":
                programs.start(cfg, target, now, log, session=getattr(args, "session", None),
                               only_new=getattr(args, "new", False))
            elif action == "next":
                run, _ = programs.require(cfg, target)
                if not programs.advance(cfg, run["id"], now, log, reason="next", expect=expect):
                    log.append("nothing to move on")
            elif action == "settle":
                if not programs.settle(cfg, target, now, log):
                    log.append("not settled yet (or already moved on)")
            elif action == "refresh-summary":
                programs.refresh_summary(cfg, target, args.session, log)
            elif action == "insights":
                path = programs.insights(cfg, target, now, log)
                if notify:
                    _notify(cfg, f"Your insights document is in the journal: {path.name}")
            elif action in ("skip", "back"):
                getattr(programs, action)(cfg, target, now, log, expect=expect)
            elif action == "resume":
                programs.resume(cfg, target, now, log, only_paused=getattr(args, "paused", False))
            else:
                getattr(programs, {"pause": "pause", "abandon": "abandon"}[action])(
                    cfg, target, now, log)
    except programs.ProgramError as e:
        print(f"journal: {e}", file=sys.stderr)
        if notify:
            _notify(cfg, str(e))
        return 2
    except tick.SendFailed as e:
        log.append(f"ALERT: {e}")
        code = 1
    except Exception as e:  # noqa: BLE001 - say it in the chat when he asked for it there
        print(f"journal: program {action} failed: {type(e).__name__}: {e}", file=sys.stderr)
        if notify:
            _notify(cfg, f"Couldn't do that ({type(e).__name__}); `journal doctor` and manual-trigger.log say why.")
        return 1
    if log:
        print("\n".join(log))
    return code


def add_serve_parser(subs) -> None:
    sv = subs.add_parser("serve", help="Run the private web app and its API (journal serve pair: pair a browser)")
    sv.add_argument("--bind", metavar="HOST", help="Listen address (default: serve.bind, 127.0.0.1)")
    sv.add_argument("--port", type=int, metavar="N", help="Port (default: serve.port, 8790)")
    sv.add_argument("--dev", action="store_true", help="Development: CORS for the Vite dev server, cookies without "
                                                       "Secure over plain HTTP")
    sv.add_argument("--access-log", action="store_true", help="Log method, path, status and bytes (never bodies)")
    sv.add_argument("--print-unit", action="store_true", help="Print the systemd user unit for this configuration")
    ss = sv.add_subparsers(dest="serve_command", metavar="<action>")
    ss.add_parser("pair", help="Print a one-time link that pairs a browser (valid 10 minutes)")
    tk = ss.add_parser("token", help="Manage the serve token")
    tk.add_argument("--rotate", action="store_true", help="New cookie key (every browser pairs again) and, when the "
                                                          "token is a file, a new token")
    ss.add_parser("check", help="Bind address, token, web app, same version as the tick")


def serve(args, cfg: jconfig.Config, cfg_loader) -> int:
    """``journal serve …``. The server itself runs in the foreground until stopped (systemd keeps it up)."""
    from .serve import auth, unit
    action = getattr(args, "serve_command", None)
    if getattr(args, "print_unit", False):
        sys.stdout.write(unit.render(cfg))
        return 0
    if action == "pair":
        try:
            tok, where = auth.resolve_token(cfg, create=True)
            if not tok:
                print(f"journal: no serve token ({where}); pairing needs one", file=sys.stderr)
                return 1
            auth.cookie_key(cfg)
            code = auth.new_pairing(cfg)
        except OSError as e:
            print(f"journal: cannot write the pairing in {cfg.state_dir}: {e}", file=sys.stderr)
            return 1
        print(auth.pair_url(cfg, code))
        print(f"Open it on the device within {auth.PAIR_TTL // 60} minutes; it works once.", file=sys.stderr)
        return 0
    if action == "token":
        if not getattr(args, "rotate", False):
            tok, where = auth.resolve_token(cfg)
            print(f"serve token: {'from ' + where if tok else where}")
            return 0 if tok or auth.file_backed(cfg) else 1
        try:
            print("\n".join(auth.rotate(cfg)))
        except OSError as e:
            print(f"journal: cannot rotate in {cfg.state_dir}: {e}", file=sys.stderr)
            return 1
        return 0
    if action == "check":
        from .status import exit_code, format_doctor
        rows = serve_check(cfg)
        print(format_doctor(rows))
        return exit_code(rows)
    from .serve import app as serve_app
    bind = args.bind or str(cfg.serve.get("bind") or "127.0.0.1")
    port = args.port if args.port is not None else jconfig.serve_port(cfg)
    if port is None or not 0 < port < 65536:
        return _err(f"serve.port must be a port number 1-65535 (got {cfg.serve.get('port')!r})")
    sock = jconfig.serve_socket(cfg)
    if cfg.serve.get("socket") is not None and sock is None:   # set but unusable: no silent TCP-only start
        return _err(next(p for p in jconfig.serve_problems(cfg) if p.startswith("serve.socket")))
    application = serve_app.App(cfg_loader, dev=bool(args.dev), access_log=bool(args.access_log))
    ok, why = application.prime()
    if not ok:
        print(f"journal serve: {why}", file=sys.stderr)
        return 1
    return serve_app.run(application, bind, port, socket_path=sock)


def serve_check(cfg: jconfig.Config) -> list[tuple[str, str, str]]:
    """``journal serve check``: the serve rows of doctor, plus whether the port is free or already answered by a
    journal serve (its /healthz)."""
    import socket
    import urllib.request

    from .status import serve_rows
    rows = [("config", "warn", p) for p in jconfig.serve_problems(cfg) + list(cfg.warnings)]
    rows += serve_rows(cfg)
    bind, port = str(cfg.serve.get("bind") or "127.0.0.1"), jconfig.serve_port(cfg)
    if port is not None:
        family = socket.AF_INET6 if ":" in bind else socket.AF_INET
        try:
            with socket.socket(family, socket.SOCK_STREAM) as s:
                s.bind((bind, port))
            rows.append(("serve port", "ok", f"{bind}:{port} is free"))
        except OSError as e:
            host = f"[{bind}]" if ":" in bind else bind
            try:
                with urllib.request.urlopen(f"http://{host}:{port}/healthz", timeout=3) as r:
                    health = json.loads(r.read().decode())
                rows.append(("serve port", "ok", f"journal serve is running there ({health.get('version')} "
                                                 f"{health.get('code')}; tick last at {health.get('tick_last_at')})"))
            except Exception:  # noqa: BLE001 - anything else on the port is the finding
                rows.append(("serve port", "fail" if e.errno == 98 else "warn",
                             f"{bind}:{port}: {e.strerror} and no journal serve answers there"))
    return rows


MCP_MISSING = ("journal mcp needs the MCP Python SDK (mcp 2.x), an optional extra. Reinstall the journal with `mcp` "
               "added to the extras you already install, at the same ref as your plugin: journal[mcp] on its own, or "
               "journal[hermes,mcp] where the Hermes settings are read (a reinstall without hermes drops PyYAML), "
               "e.g. uv tool install --force 'journal[hermes,mcp] @ git+https://github.com/beejsbj/journal@<ref>' "
               "or pip install 'journal[mcp] @ git+https://github.com/beejsbj/journal@<ref>'.")


def mcp(cfg_loader) -> int:
    """``journal mcp``: the MCP server over stdio, its configuration reloaded for every call (the same resolution as
    every other command). stdout is the protocol, so only stderr is written to here."""
    try:
        from .mcp import server
    except ImportError as e:
        if not (e.name or "").split(".")[0] == "mcp":
            raise
        print(f"journal: {MCP_MISSING}", file=sys.stderr)
        return 1
    return server.run(cfg_loader)


def _edited_note(cfg: jconfig.Config, pid: str) -> str:
    """``edited`` for a built-in he has edited, with ``, default changed`` when the shipped one has changed since."""
    from . import library
    status = library.edit_status(cfg, pid) or {}
    return "edited, default changed" if status.get("default_changed") else "edited"


def practices(args, cfg: jconfig.Config) -> int:
    """``journal practices …``: read-only except ``fork`` (writes his copy of a built-in under ``practices_dir``) and
    ``reset`` (removes it)."""
    from . import library
    action = getattr(args, "practices_command", None)
    if action not in PRACTICE_ACTIONS:
        return _err(f"practices needs one of: {', '.join(PRACTICE_ACTIONS)}")
    try:
        lib = library.load(cfg)
    except RuntimeError as e:  # the packaged built-ins are unreadable: an install fault
        print(f"journal: {e}", file=sys.stderr)
        return 1
    known = ", ".join(lib.practices)
    if action == "list":
        if getattr(args, "json", False):
            print(json.dumps({"practices_dir": str(lib.practices_dir), "problems": lib.problems,
                              "practices": [lib.to_dict(p) for p in lib.practices.values()]},
                             indent=2, ensure_ascii=False))
            return 0
        label = {"builtin": lambda p: "built-in", "user": lambda p: "user", "fork": lambda p: _edited_note(cfg, p.id)}
        rows = [(p.id, p.title, library.describe_schedule(p.schedule) + ("" if p.enabled else " · disabled"),
                 label[lib.source(p)](p), "" if p.builtin else str(p.path))
                for p in lib.practices.values()]
        widths = [max(len(r[i]) for r in rows) for i in range(4)]
        for r in rows:
            print("  ".join(c.ljust(w) for c, w in zip(r, widths + [0])).rstrip())
        for msg in lib.problems:
            print(f"! {msg}")
        return 0
    if action == "show":
        try:
            p = lib.get(args.practice)
        except library.UnknownDefinition:
            return _err(f"no practice {args.practice!r} (known: {known})")
        if getattr(args, "json", False):
            print(json.dumps({**lib.to_dict(p), "raw": p.raw}, indent=2, ensure_ascii=False))
            return 0
        source = {"builtin": "built-in", "fork": "your edit of the built-in", "user": "yours"}[lib.source(p)]
        if lib.source(p) == "fork" and (library.edit_status(cfg, p.id) or {}).get("default_changed"):
            source += f"; the default has changed since you edited it: journal practices diff {p.id}"
        o = p.opener
        print(f"{p.id} · {p.title} ({source}{'' if p.enabled else ', disabled'})")
        print(f"file: {p.path}")
        print(f"schedule: {library.describe_schedule(p.schedule)}")
        print(f"closes: {p.closes or '-'} · annotate: {'yes' if p.annotate else 'no'} · "
              f"folds after {p.collapse_hours:g} h" + (f" · emoji: {p.emoji}" if p.emoji else ""))
        print(f"opener: {o['mode']}, {o['shape']}, at most {o['limit']} characters")
        print(f"plain opener: {p.plain_opener(cfg.names)}")
        if p.companion:
            print(f"companion: {p.companion}")
        print()
        print(p.bank_with_ids())
        return 0
    if action == "validate":
        if not args.files:
            if lib.problems:
                print("\n".join(lib.problems))
                return 1
            users = sum(not p.builtin for p in lib.practices.values())
            print(f"ok: {len(lib.practices)} practices ({len(lib.practices) - users} built-in, {users} user) "
                  f"in {lib.practices_dir}")
            return 0
        failed = False
        for f in args.files:
            problems = library.validate_file(Path(f), cfg)
            failed = failed or bool(problems)
            if problems:
                print("\n".join(f"{f}: {m}" for m in problems))
                continue
            parsed = library.parse(Path(f).read_text(encoding="utf-8"))
            print(f"{f}: ok" + (f" (replaces the built-in {parsed.id!r})" if parsed.id in lib.builtins else ""))
        return 1 if failed else 0
    if action == "render":
        target = args.target
        if target.endswith(".md") or "/" in target or Path(target).is_file():
            try:
                text = Path(target).read_text(encoding="utf-8")
                definition = library.parse(text, Path(target))
            except (OSError, UnicodeDecodeError) as e:
                print(f"journal: {target}: cannot read: {type(e).__name__}", file=sys.stderr)
                return 1
            except library.ParseError as e:
                print(f"journal: {target}: {e}", file=sys.stderr)
                return 1
        else:
            try:
                definition = lib.get(target)
            except library.UnknownDefinition:
                return _err(f"no practice {target!r} (known: {known})")
        sys.stdout.write(library.render(definition))
        return 0
    shipped = [*lib.builtins, *library.builtin_programs()]
    if action == "reset":
        return _reset(cfg, args.practice, shipped)
    if action == "diff":
        try:
            diff = library.default_diff(cfg, args.practice)
        except library.UnknownDefinition:
            return _err(f"only a built-in has a default ({', '.join(shipped)}); {args.practice!r} is not one")
        if diff["yours"] is None:
            print(f"{args.practice}: not edited; the shipped default is in force")
            return 0
        status = library.edit_status(cfg, args.practice) or {}
        if diff["since"]:
            print(f"# what changed in the default since you edited it ({status.get('edited_at') or 'date not kept'})")
            sys.stdout.write(diff["since"])
        elif status.get("default_changed") is None:
            print("# the default your edit began from was not kept: whether it changed since is not known")
        else:
            print("# the default has not changed since you edited it")
        print("# the default now against your edit")
        sys.stdout.write(diff["yours"] or "(the same)\n")
        return 0
    try:  # fork
        dest = library.fork(cfg, args.practice)
    except library.UnknownDefinition:
        return _err(f"only a built-in can be forked ({', '.join(shipped)}); {args.practice!r} is not one")
    except library.AlreadyForked as e:
        print(f"journal: {e}; `journal practices reset {args.practice}` goes back to the built-in", file=sys.stderr)
        return 1
    except OSError as e:  # unwritable, or a file where the directory should be: say where, not "already forked"
        print(f"journal: cannot fork into {cfg.practices_dir}: {e}", file=sys.stderr)
        return 1
    note = library.window_note(cfg, args.practice) if args.practice in lib.builtins else ""
    print(f"forked {args.practice} to {dest}; edit it there (it replaces the built-in under the same id), "
          f"`journal practices reset {args.practice}` to go back to the built-in{note}")
    return 0


def _reset(cfg: jconfig.Config, pid: str, shipped: list[str]) -> int:
    """``journal practices reset <id>``: his edit of a built-in removed, the shipped one in force again. A program's run
    in progress keeps its structure, so a reset that would change it is refused until the run ends."""
    from . import library
    ship = library.shipped(pid) if library.ID.fullmatch(pid) else None
    if ship is None:
        return _err(f"only a built-in can be reset ({', '.join(shipped)}); {pid!r} is not one (a practice of your "
                    "own is deleted by removing its file)")
    try:
        removed = library.reset(cfg, pid)
    except library.RunInProgress as e:
        print(f"journal: {e}", file=sys.stderr)
        return 1
    except OSError as e:
        print(f"journal: cannot reset {pid} in {cfg.practices_dir}: {e}", file=sys.stderr)
        return 1
    if removed is None:
        print(f"{pid}: not edited; the shipped built-in is already in force")
        return 0
    print(f"reset {pid}: removed {removed}; the shipped built-in is in force from the next tick")
    return 0


def parse_value(text: str):
    """What was typed after ``config set KEY``: JSON when it is JSON (``5``, ``true``, ``{"lat": 1}``), else the
    text itself, so ``Europe/London`` needs no quotes and ``'"5"'`` is the way to a string that looks like a number."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _shown(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def config_command(args, cfg: jconfig.Config, cfg_loader) -> int:
    """``journal config list|get|set``: the same allow-list, validation and write as the web app's Settings page
    (``settings.apply``). Exit 0 ok; 2 a key or value that is not allowed; 1 a change that cannot be made here
    (Hermes or the environment owns the key, or the config file cannot be written). Hermes settings are never
    edited: for a key they own, the command to run is printed."""
    from . import settings
    action = getattr(args, "config_command", None)
    if action not in CONFIG_ACTIONS:
        print("usage: journal config {list,get,set}", file=sys.stderr)
        return 2
    as_json = getattr(args, "json", False)
    if action == "list":
        rows = settings.rows(cfg)
        if as_json:
            print(json.dumps({"config_file": str(cfg.config_file) if cfg.config_file else None, "settings": rows},
                             indent=2, ensure_ascii=False))
            return 0
        width = max(len(r["key"]) for r in rows)
        for r in rows:
            note = "" if r["writable"] else f"  [{r['why']}]"
            print(f"{r['key'].ljust(width)}  {_shown(r['value'])}  ({r['layer']}){note}")
        return 0
    if not settings.allowed(args.key):
        return _err(f"{args.key!r} is not a setting that can be read or changed here "
                    f"(journal config list shows them)")
    if action == "get":
        value = settings.current(cfg, args.key)
        if as_json:
            row = {"key": args.key, "value": value, "layer": settings.layer(cfg, args.key)}
            print(json.dumps(row, indent=2, ensure_ascii=False))
        else:
            print(_shown(value))
        return 0
    try:
        done = settings.apply(cfg, {args.key: parse_value(args.value)}, reload=cfg_loader)
    except settings.SettingsError as e:
        if as_json:
            print(json.dumps({"error": e.code, "detail": e.message, "keys": e.keys, "problems": e.problems,
                              **({"command": e.command, "commands": e.commands} if e.commands else {})},
                             indent=2, ensure_ascii=False))
        else:
            print(f"journal: {e.message}", file=sys.stderr)
            for problem in e.problems:
                print(f"  {problem}", file=sys.stderr)
            for line in e.commands:
                print(f"  run: {line}", file=sys.stderr)
        return 2 if e.code in (settings.KEY_NOT_ALLOWED, settings.BAD_REQUEST, settings.INVALID) else 1
    if as_json:
        print(json.dumps({"written": done["written"], **({"location": done["location"]} if done["location"] else {})},
                         indent=2, ensure_ascii=False))
    else:
        for key, layer in done["written"].items():
            print(f"{key} = {_shown(settings.current(_reloaded(cfg_loader, cfg), key))}  (written to the "
                  f"{'config file' if layer == 'file' else layer})")
    return 0


def _reloaded(cfg_loader, cfg: jconfig.Config) -> jconfig.Config:
    """The configuration as it reads after a write (what a later command would see); the old one if it cannot be read."""
    try:
        return cfg_loader()
    except jconfig.ConfigError:
        return cfg


def _load(args) -> jconfig.Config:
    return jconfig.load(args.config, hermes_home=args.hermes_home)


def _err(msg: str) -> int:
    print(f"journal: {msg}", file=sys.stderr)
    return 2


def run(args, cfg_loader=None, hermes_checks=None) -> int:
    cmd = args.command
    if cmd == "migrate-config":
        from .migrate import migrate_config
        try:
            new, notes = migrate_config(Path(args.src), Path(args.dest) if args.dest else None,
                                        Path(args.merge) if args.merge else None, args.force,
                                        practices_dir=Path(args.practices_dir) if args.practices_dir else None,
                                        hermes_home=Path(home) if (home := args.hermes_home or os.environ.get("HERMES_HOME")) else None)
        except (jconfig.ConfigError, FileExistsError, ValueError, OSError) as e:
            return _err(str(e))
        if not args.dest:
            print(json.dumps(new, indent=2, ensure_ascii=False))
        for n in notes:
            print(n, file=sys.stderr)
        return 0
    load = cfg_loader or (lambda: _load(args))
    try:
        cfg = load()
    except jconfig.ConfigError as e:
        return _err(f"configuration problem: {e}")
    if cmd == "migrate-state":
        from .migrate import migrate_state
        dest = Path(args.dest) if args.dest else cfg.state_dir
        try:
            for line in migrate_state(Path(args.src), dest, args.force, not args.no_lock,
                                      note=lambda s: print(s, file=sys.stderr)):
                print(line)
        except (FileNotFoundError, FileExistsError, ValueError, OSError) as e:
            return _err(str(e))
        return 0
    if cmd == "sweep":
        from . import tick
        code, log = tick.run_sweep(cfg)
        print("\n".join(log))
        return code
    if cmd == "backfill-conversations":
        from datetime import date
        from . import tick
        try:
            since = date.fromisoformat(args.since)
        except ValueError:
            return _err(f"--since must be a date like 2026-08-01 (got {args.since!r})")
        try:
            code, log = tick.run_backfill(cfg, since, write=bool(getattr(args, "write", False)))
        except (OSError, sqlite3.Error, RuntimeError) as e:  # the sessions DB, the journal or the privacy pass
            print(f"journal: backfill failed: {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        print("\n".join(log))
        return code
    if cmd == "send":
        from . import library
        try:
            practice = library.load(cfg).get(args.ritual)
        except library.UnknownDefinition:
            return _err(f"no practice {args.ritual!r} (known: {', '.join(library.load(cfg).practices)})")
        except RuntimeError as e:  # the packaged built-ins are unreadable: an install fault
            print(f"journal: {e}", file=sys.stderr)
            return 1
        if not practice.enabled:
            return _err(f"{practice.id!r} is turned off (enabled: false in {practice.path})")
    if cmd in ("tick", "send"):
        from . import tick
        only = args.ritual if cmd == "send" else None
        code, log = tick.run(cfg, only=only, dry_run=args.dry_run,
                             hold_off_record=cmd == "send" and args.hold_off_record)
        if log:
            print("\n".join(log))
        return code
    if cmd == "status":
        from .status import format_status, status
        s = status(cfg)
        print(json.dumps(s, indent=2, ensure_ascii=False) if args.json else format_status(s))
        return 0
    if cmd == "today":
        from . import today
        now = datetime.now(timezone.utc)
        if args.json:
            print(json.dumps(today.collect(cfg, now), indent=2, ensure_ascii=False))
            return 0
        block = today.render(cfg, now)
        print(block or ("The today block is off (today.enabled)." if not today.enabled(cfg)
                        else "Nothing to say about today yet: the block would be empty."))
        return 0
    if cmd == "practices":
        return practices(args, cfg)
    if cmd == "serve":
        return serve(args, cfg, load)
    if cmd == "program":
        return program(args, cfg)
    if cmd == "mcp":
        return mcp(load)
    if cmd == "config":
        return config_command(args, cfg, load)
    if cmd == "doctor":
        from .status import doctor, exit_code, format_doctor
        rows = doctor(cfg, deep=args.deep)
        if hermes_checks is not None:
            rows += hermes_checks(cfg)
        elif cfg.hermes_home is not None:
            from .hermes import cron
            rows += cron.checks(cfg, Path(__file__).resolve().parent.parent)
        print(json.dumps([{"check": n, "status": s, "detail": d} for n, s, d in rows], indent=2)
              if args.json else format_doctor(rows))
        return exit_code(rows)
    return _err(f"unknown command {cmd!r}")


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if not args.command:
        parser.print_help()
        return 2
    return run(args)


def hermes_command(args) -> int:
    """``hermes journal <command>``: the same commands, in the plugin's profile, plus setup/uninstall."""
    from .hermes import cron, runtime
    sub = getattr(args, "journal_command", None)
    if not sub:
        print("usage: hermes journal {setup,doctor,uninstall,status,today,send,sweep,backfill-conversations,practices,"
              "program,config}")
        return 2
    if sub == "setup" and getattr(args, "config_file", None):
        path = Path(args.config_file).expanduser().resolve()
        try:
            jconfig.read_json_file(path)
        except jconfig.ConfigError as e:
            return _err(str(e))
        writer = runtime.settings_writer()
        if writer is None:
            return _err("no plugin context to save the config_file setting in")
        writer("config_file", str(path))
        print(f"set plugins.entries.journal.settings.config_file = {path}")
    try:
        cfg = runtime.load_config()
    except jconfig.ConfigError as e:
        return _err(f"configuration problem: {e}\nSet the plugin's settings "
                    "(`hermes config set plugins.entries.journal.settings.root …`) or point `config_file` at a "
                    "JSON file (`hermes journal setup --config-file FILE`).")
    src = runtime.src_dir()
    if sub == "setup":
        problems = [p for p in jconfig.problems(cfg)]
        if problems:
            for p in problems:
                print(f"! {p}")
        code, lines = cron.setup(cfg, src, paused=getattr(args, "paused", False))
        print("\n".join(lines))
        if code == 0:
            print("Next: `hermes journal doctor`. Restart the gateway if the plugin was enabled while it ran.")
        return code
    if sub == "uninstall":
        code, lines = cron.uninstall(cfg)
        print("\n".join(lines))
        return code
    ns = argparse.Namespace(command=sub, json=getattr(args, "json", False), deep=getattr(args, "deep", False),
                            ritual=getattr(args, "ritual", None), dry_run=getattr(args, "dry_run", False),
                            since=getattr(args, "since", None), write=getattr(args, "write", False),
                            practices_command=getattr(args, "practices_command", None),
                            practice=getattr(args, "practice", None), files=getattr(args, "files", None) or [],
                            target=getattr(args, "target", None), config=None, hermes_home=None,
                            program_command=getattr(args, "program_command", None),
                            session=getattr(args, "session", None), notify=getattr(args, "notify", False),
                            now=getattr(args, "now", False), expect=getattr(args, "expect", None),
                            config_command=getattr(args, "config_command", None), key=getattr(args, "key", None),
                            value=getattr(args, "value", None))
    # `config set` reads the settings again afterwards to say what is in force; the other commands use the one loaded
    again = runtime.load_config if sub == "config" else (lambda: cfg)
    return run(ns, cfg_loader=again, hermes_checks=lambda c: cron.checks(c, src))


if __name__ == "__main__":
    raise SystemExit(main())
