"""Hermes integration: ``register(ctx)`` wires the journal into a Hermes profile.

- hooks: ``pre_gateway_dispatch`` (capture), ``pre_llm_call`` (voice transcripts, notebook context),
  ``pre_tool_call`` (guard)
- tools: ``journal_add_note``, ``journal_set_location``, ``journal_notebook``, ``journal_program`` (status and
  propose: reads)
- slash commands: ``/j``, ``/x`` (off the record; the configured opt-out prefix), ``/start <id>`` (registered as
  ``/journal-start``: Hermes owns ``/start``), ``/morning`` ``/evening`` ``/midweek`` ``/weekly`` (+
  ``/morning-journal``, ``/evening-journal``), and for programs ``/next`` ``/skip`` ``/back`` ``/break``
  ``/abandon`` ``/program`` (Hermes owns ``/pause`` and ``/status``), and ``/web`` (the web app's address)
- skill: ``journal:journal-system``, rendered with this profile's names, its practices (the "Kinds" list) and its
  programs (the "Programs" list)
- CLI: ``hermes journal setup | doctor | uninstall | status | today | send | sweep | backfill-conversations |
  practices | program | config``
"""
from __future__ import annotations

import logging
import re
import tempfile
from pathlib import Path

from .. import config as jconfig
from . import commands, hooks, runtime, tools

log = logging.getLogger("journal.hermes")
SKILL_NAME = "journal-system"
SKILL_TEMPLATE = Path(__file__).resolve().parents[1] / "skills" / SKILL_NAME
# ``{{#arm}}…{{/arm}}``: text for one capture mode only (``capture.mode``); the other mode's blocks are dropped
_MODE_BLOCK = re.compile(r"\{\{#(" + "|".join(jconfig.CAPTURE_MODES) + r")\}\}(.*?)\{\{/\1\}\}", re.S)


def _names_and_cfg():
    try:
        cfg = runtime.load_config()
        return cfg.names, cfg
    except Exception as e:  # unconfigured yet: register with neutral wording; doctor explains
        log.info("journal: configuration not usable yet (%s); registering with defaults", e)
        return jconfig.Names(), None


def kinds(cfg) -> str:
    """The skill's "Kinds" list: one line per enabled practice, its ``companion:`` stance as written in its file
    (no pronoun tokens: a file is his text). Rendered when the gateway starts; a practice added later is still
    sent and startable, and joins this list at the next restart. Without a usable config, the built-ins."""
    from .. import library
    try:
        practices = library.load(cfg).enabled() if cfg is not None else list(library.builtins().values())
    except Exception:  # noqa: BLE001 - a skill with the built-ins beats no skill
        log.exception("journal: could not read the practice library for the skill")
        practices = list(library.builtins().values())
    return "\n".join(f"- **{p.title}** (`/start {p.id}`)" + (f": {p.companion}." if p.companion else ".")
                     for p in practices)


def programs_list(cfg) -> str:
    """The skill's "Programs" list: one line per enabled program or situational session, its title and the line
    that starts it, and how it moves on (a settle program after ``programs.settle_minutes`` of quiet, an
    ``advance: next`` one only on /next). Rendered when the gateway starts, like ``kinds``; without a usable config,
    the built-ins."""
    from .. import library, programview
    try:
        progs = library.load(cfg).enabled_programs() if cfg is not None else \
            [p for p in library.builtin_programs().values() if p.enabled]
    except Exception:  # noqa: BLE001 - a skill with the built-ins beats no skill
        log.exception("journal: could not read the program library for the skill")
        progs = [p for p in library.builtin_programs().values() if p.enabled]
    names = cfg.names if cfg is not None else jconfig.Names()
    return "\n".join(f"- **{p.title}** (`/start {p.aliases[0] if p.aliases else p.id}`)"
                     + (f": {len(p.sessions)} sessions" if not p.situational else ": a situational session")
                     + f"; {programview.advance_clause(cfg, p, names)}."
                     for p in progs) or "- (none are enabled)"


def skill_tokens(names: jconfig.Names, cfg) -> dict[str, str]:
    esc = ((cfg.guard.get("escalate_to") if cfg else "") or "").strip()
    reading = ""
    if cfg is not None:
        from ..prompts import reading_guidance
        reading = reading_guidance(cfg)
    where = (f"`{cfg.config_file}`" if cfg is not None and cfg.config_file else
             "the journal's settings (`plugins.entries.journal.settings`)")
    return {
        "companion": names.companion,
        "user": names.user or "the user",
        "User": (names.user or "The user"),
        "users": names.user_possessive,
        "Users": names.user_possessive_title,
        "they": names.subject, "them": names.object, "their": names.possessive,
        "Their": names.possessive.capitalize(),
        "reading_guidance": reading or "Good places to look: older entries in the journal folder, and anything "
                                       "else your read-only tools can reach.",
        "config_location": where,
        "ask_for_changes": f"ask {esc}" if esc else f"tell {names.user or 'the user'} what to change",
        "escalate": f", or offer to ask {esc}" if esc else "",
        "collapse_hours": f"{cfg.collapse_hours:g}" if cfg else "6",
        "annotate_minutes": str(cfg.annotate_after_minutes) if cfg else "20",
        "quiet_minutes": f"{jconfig.quiet_minutes(cfg):g}" if cfg else "45",
        "opt_out": jconfig.opt_out_prefix(cfg) if cfg else "/x",
        "capture_mode": jconfig.capture_mode(cfg) if cfg else "everything",
        "kinds": kinds(cfg),
        "programs": programs_list(cfg),
        **_chat_tokens(cfg),
    }


def _chat_tokens(cfg) -> dict[str, str]:
    """How the skill names the journal's chat and what the engine does to its messages there (CHANNELS): Telegram's
    wording as it always was; on a platform that cannot edit or reply from outside the gateway, what happens
    instead."""
    from .. import channel
    if cfg is None or cfg.platform == "telegram":
        return {"chat": "Telegram", "folds_old": "folds old prompts in Telegram",
                "annotations_back": "sends those annotations back as a reply to the message they annotate",
                "folding": 'the Telegram prompt shrinks to its header line ("answered ✓" / "missed — reply anytime")',
                "annotation_sent": "sent as a Telegram reply to that message"}
    name = channel.label(cfg)
    return {"chat": name, "folds_old": f"closes old prompts (it cannot edit {name} messages, so they stay as sent)",
            "annotations_back": f"sends those annotations to {name} as a message of their own",
            "folding": f"the prompt is closed: it stays in {name} as it was sent (the engine cannot edit it there)",
            "annotation_sent": f"sent to {name} as a message of its own"}


def render_skill(dest: Path, tokens: dict[str, str]) -> Path:
    """Copy the skill template to ``dest`` with ``{{token}}`` placeholders filled in, keeping only the
    ``{{#<mode>}}…{{/<mode>}}`` blocks of the configured capture mode (what she is told is saved must be what is)."""
    dest.mkdir(parents=True, exist_ok=True)
    mode = tokens.get("capture_mode") or "everything"
    for src in SKILL_TEMPLATE.rglob("*.md"):
        text = _MODE_BLOCK.sub(lambda m: m.group(2) if m.group(1) == mode else "", src.read_text(encoding="utf-8"))
        for k, v in tokens.items():
            text = text.replace("{{" + k + "}}", v)
        out = dest / src.relative_to(SKILL_TEMPLATE)
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.is_file() or out.read_text(encoding="utf-8") != text:
            out.write_text(text, encoding="utf-8")
    return dest / "SKILL.md"


def _register_skill(ctx, names, cfg) -> None:
    try:
        home = runtime.hermes_home()
        base = cfg.state_dir if cfg is not None else (home / "plugin-data" / jconfig.PLUGIN_ID if home else None)
        path = render_skill(Path(base) / "skill" / SKILL_NAME, skill_tokens(names, cfg)) if base else None
    except Exception:
        log.exception("journal: could not render the skill into the state directory; rendering it to a temporary one")
        try:  # (the raw template holds both capture modes' paragraphs and the unfilled tokens: never register that)
            path = render_skill(Path(tempfile.mkdtemp(prefix="journal-skill-")) / SKILL_NAME, skill_tokens(names, cfg))
        except Exception:
            log.exception("journal: could not render the skill; registering the template")
            path = None
    saves = ("(the conversation too, unless /x)" if cfg is None or jconfig.capture_mode(cfg) == "everything" else
             "(replies, /j notes and the conversation after them; not ordinary chat)")
    ctx.register_skill(SKILL_NAME, path or (SKILL_TEMPLATE / "SKILL.md"),
                       description="How to converse when a journal reply or /j note arrives, what the engine saves "
                                   f"{saves}, and what it does.")


def _cli_setup(subparser) -> None:
    subs = subparser.add_subparsers(dest="journal_command")
    p = subs.add_parser("setup", help="Write the cron stub and create the every-5-minutes tick job (idempotent)")
    p.add_argument("--paused", action="store_true", help="Create the job paused")
    p.add_argument("--config-file", help="Point the plugin's `config_file` setting at this JSON file first")
    p = subs.add_parser("doctor", help="Check configuration, journal root, token, sources and the cron job")
    p.add_argument("--json", action="store_true")
    p.add_argument("--deep", action="store_true",
                   help="Also run each enabled context source once and count how the newest 50 chat rows read")
    subs.add_parser("uninstall", help="Remove the cron job and stub; keep the journal, state and settings")
    p = subs.add_parser("status", help="Last tick, due rituals, open prompts, notebook, recent errors")
    p.add_argument("--json", action="store_true")
    p = subs.add_parser("today", help="Print the today block the companion gets at the start of a thread")
    p.add_argument("--json", action="store_true")
    subs.add_parser("sweep", help="Capture journal replies the gateway hook missed (busy path)")
    from ..cli import add_backfill_parser, add_config_parser, add_practices_parser, add_program_parser
    add_backfill_parser(subs)
    add_practices_parser(subs)
    add_program_parser(subs)
    add_config_parser(subs)
    p = subs.add_parser("send", help="Send a practice now (any id in the library)")
    p.add_argument("ritual", metavar="ID")
    p.add_argument("--dry-run", action="store_true")
    subs.add_parser("deliver", help="(the engine's) one message into a chat on any platform: JSON {platform, chat_id, "
                                    "text} on stdin, the message id out; Hermes' senders without its mirror")
    subparser.set_defaults(func=_cli_handle)


def _cli_handle(args) -> int:
    if getattr(args, "journal_command", None) == "deliver":  # needs no journal configuration: the caller says where
        from . import deliver
        return deliver.main()
    from .. import cli
    return cli.hermes_command(args)


def register(ctx) -> None:
    runtime.bind(ctx)
    names, cfg = _names_and_cfg()
    tools.register(ctx, names)
    commands.register(ctx)
    ctx.register_hook("pre_gateway_dispatch", hooks.on_dispatch)
    ctx.register_hook("pre_llm_call", hooks.on_llm_call)
    ctx.register_hook("pre_tool_call", hooks.on_tool_call)
    if hasattr(ctx, "register_skill"):
        _register_skill(ctx, names, cfg)
    if hasattr(ctx, "register_cli_command"):
        ctx.register_cli_command(name="journal", help="Guided journal: setup, doctor, status, send, practices",
                                 setup_fn=_cli_setup, handler_fn=_cli_handle,
                                 description="Set up and inspect the journal plugin for this profile.")
