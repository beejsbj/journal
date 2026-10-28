"""The MCP server of ``journal mcp``: ``handlers`` registered with the official MCP Python SDK (``mcp`` 2.x, the
optional extra ``journal[mcp]``), served over stdio.

This module is the only one that imports the SDK; ``cli`` imports it only for ``journal mcp`` and explains how to
install the extra when it is missing, so the core stays stdlib-only. The configuration is loaded again for every
call (``load``: the CLI's own resolution of ``--config`` / ``--hermes-home``), so a move that changed the timezone
or a practice added since the client connected is seen at once. stdout is the protocol: nothing here prints, the
engine logs to stderr, and a spawned send writes to ``manual-trigger.log``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from .. import __version__
from ..config import Config, opt_out_prefix
from . import handlers

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
APPEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
SEND = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)

TOOLS = ("journal_today", "journal_entries", "journal_entry", "journal_save", "journal_practices", "journal_start",
         "journal_program_status", "journal_add_note")


def _answer(call: Callable):
    """``call()``, with a refusal given to the client as the tool's error text (``ToolError``: the SDK shows its
    message; any other exception is a crash, which the client sees only as "Error executing tool")."""
    try:
        return call()
    except handlers.Refused as e:
        raise ToolError(str(e)) from None


def instructions(cfg: Config) -> str:
    n = cfg.names
    who = n.user or "the person"
    off = opt_out_prefix(cfg)
    return (
        f"The journal of {who}: guided practices sent on a schedule, and entries that keep two voices apart: "
        f"{n.possessive} own words, verbatim, and {n.companion}'s (the companion's) replies, annotations and notes. "
        f"Read with journal_today, journal_entries and journal_entry. journal_save appends words {who} wrote or "
        f"dictated to you, exactly as given; never your own words, a summary or a paraphrase. Your own observations "
        f"go through journal_add_note, which files them as the companion's notes, apart from {n.possessive} words. "
        f"Text starting with the off-the-record command ({off}), or with a line starting with it, is never saved; "
        f"after a bare {off}, nothing more from that conversation goes through journal_save or journal_add_note. "
        "Nothing in the journal is ever edited or deleted from here."
    )


def build(load: Callable[[], Config], *, clock: Callable[[], datetime] | None = None,
          spawn: Callable | None = None) -> MCPServer:
    """The server, its tools reading the configuration from ``load()`` on every call. ``clock`` and ``spawn`` are for
    tests (the defaults: the real time, ``serve.writes.spawn_journal``)."""
    now = clock or (lambda: datetime.now(timezone.utc))
    cfg0 = load()
    n = cfg0.names
    who, hers = n.user or "the person", n.companion
    off = opt_out_prefix(cfg0)   # the configured off-the-record command: the descriptions are read at connect time
    server = MCPServer(name="journal", title="Journal", version=__version__, instructions=instructions(cfg0))

    @server.tool(name="journal_today", title="Today in the journal", annotations=READ, description=(
        f"What the journal knows about today: which practices were sent and answered, an open prompt awaiting "
        f"{n.possessive} reply, a program in progress, today's entries (stem and first line), the latest "
        "reflection's question, tasks and weather when configured. Read only."), structured_output=False)
    def journal_today() -> str:
        return _answer(lambda: handlers.today_text(load(), now()))

    @server.tool(name="journal_entries", title="List or search entries", annotations=READ, description=(
        f"{who}'s journal entries, newest first: stem, date, kind, title and the first line of {n.possessive} words. "
        f"query searches {n.possessive} words only (case-insensitive substring; {hers}'s replies are never "
        "searched). kind filters by entry id (a practice id such as evening, j for a /j note, a program id). "
        "date_from/date_to are YYYY-MM-DD. Page with cursor = the next value returned. Read only."))
    def journal_entries(query: str = "", kind: str = "", date_from: str = "", date_to: str = "", limit: int = 20,
                        cursor: int = 0) -> dict:
        return _answer(lambda: handlers.entry_list(load(), now(), query=query or None, kind=kind or None,
                                                   date_from=date_from or None, date_to=date_to or None, limit=limit,
                                                   cursor=cursor))

    @server.tool(name="journal_entry", title="Read one entry", annotations=READ, description=(
        f"One entry as a two-voice document: every block is labelled with whose words it is, {who}'s own "
        f"(verbatim) or {hers}'s (the companion's replies, annotations and notes), with the prompt it answers. "
        "stem is the entry's name, e.g. 2026-10-14-evening. as_json returns the structured document instead "
        "(each block's kind is his or hers). Read only."), structured_output=False)
    def journal_entry(stem: str, as_json: bool = False) -> str | dict:
        cfg = load()
        return _answer(lambda: handlers.entry_document(cfg, stem) if as_json else handlers.entry_text(cfg, stem))

    @server.tool(name="journal_save", title="Save his words", annotations=APPEND, description=(
        f"Append words {who} wrote or dictated to you to the journal, verbatim, exactly as given: never your own "
        f"words, a summary or a paraphrase. With practice (an id from journal_practices), they answer that "
        "practice's open prompt when one is waiting; otherwise, or without practice, they become a /j note of "
        f"their own. Nothing already in the journal changes. Text starting with {off} (off the record), or with a "
        "line starting with it, is refused and nothing is saved. Returns the entry's stem."))
    def journal_save(text: str, practice: str = "") -> dict:
        return _answer(lambda: handlers.save(load(), now(), text, practice or None))

    @server.tool(name="journal_practices", title="List practices", annotations=READ, description=(
        "The practice library: each practice's id, title, schedule, whether it is on, and whether it is built in, "
        "a fork of a built-in, or one of the user's own. Read only."))
    def journal_practices() -> dict:
        return _answer(lambda: handlers.practice_list(load()))

    @server.tool(name="journal_start", title="Send a practice now", annotations=SEND, description=(
        f"Send a practice to {who} now, as /start <id> does in the chat: the journal sends its opener and questions "
        "in the usual chat, in the background. practice is an id from journal_practices. While the chat is off the "
        f"record (a bare {off}), nothing is sent. Programs are not started here: {who} begins one in the chat or the "
        f"web app. Use it only when {who} asks for it."))
    def journal_start(practice: str) -> dict:
        return _answer(lambda: handlers.start(load(), practice, **({"spawn": spawn} if spawn else {})))

    @server.tool(name="journal_program_status", title="Program status", annotations=READ, description=(
        "The journal's programs (guided multi-session runs and situational sessions) and any run in progress, "
        "with where it is: what /program shows in the chat. Read only."))
    def journal_program_status() -> dict:
        return _answer(lambda: handlers.program_status(load(), now()))

    @server.tool(name="journal_add_note", title="Add a companion note", annotations=APPEND, description=(
        f"Add a note in the companion's voice to an entry: an observation or a connection worth keeping. It is "
        f"appended, timestamped, under \"{hers}'s notes\" in that day's entry, marked as the companion's, and never "
        f"changes {who}'s words. date is YYYY-MM-DD (default today); kind picks the entry of that day (a practice "
        "id, or note for a freeform one; default the most recent). Mark inference as inference. Never from an "
        f"off-the-record ({off}) exchange."))
    def journal_add_note(note: str, date: str = "", kind: str = "") -> dict:
        return _answer(lambda: handlers.add_note(load(), now(), note, date or None, kind or None))

    @server.resource("journal://today", name="today", title="Today in the journal", mime_type="text/markdown",
                     description="The today block (journal_today).")
    def today_resource() -> str:
        return handlers.today_text(load(), now())

    @server.resource("journal://entries/recent", name="recent-entries", title="Recent entries",
                     mime_type="application/json", description="The newest 20 entries (journal_entries).")
    def recent_resource() -> str:
        return handlers.recent_text(load(), now())

    return server


def run(load: Callable[[], Config]) -> int:
    """``journal mcp``: serve over stdio until the client closes the stream."""
    build(load).run("stdio")
    return 0
