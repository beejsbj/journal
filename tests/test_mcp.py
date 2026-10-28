"""``journal mcp``: the tool handlers called directly (no SDK, no network) over the temporary journal ``journal serve``
is tested on (``serve_helpers``: Sam writes, Ada is the companion), then, when the SDK is installed, the same tools
through ``MCPServer.call_tool`` in process. What must hold: a save lands verbatim and only appends; ``/x`` and the
engine's marker are refused with nothing written; an entry's two voices are labelled apart; no tool opens the chat
transcript; a send is a spawned ``journal send <id>``; ``journal mcp`` without the extra says how to install it."""
import ast
import asyncio
import contextlib
import fcntl
import hashlib
import importlib.util
import io
import json
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from helpers import CleanEnv, add_prompt, make_cfg
from serve_helpers import HERS, IST, NOW, build_journal

from journal import cli, ledger
from journal.mcp import handlers
from journal.serve import api, document

HAS_SDK = importlib.util.find_spec("mcp") is not None and importlib.util.find_spec("mcp.server.mcpserver") is not None


def _digest(root: Path) -> dict[str, str]:
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob("*.md"))}


class Case(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = build_journal(Path(self.tmp.name))
        self.spawned = []

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def spawn(self, cfg, args):
        self.spawned.append(args)
        return str(cfg.state_dir / "manual-trigger.log")


class ReadTest(Case):
    def test_today_is_the_block_rendered(self):
        text = handlers.today_text(self.cfg, NOW)
        self.assertTrue(text.startswith("## Journal: today — Thu 15 Oct 2026, 09:12 IST"), text)
        self.assertIn("2026-10-15-j (\"call the harbour master\")", text)
        self.assertNotIn(HERS, text)

    def test_today_answers_when_the_chat_block_is_off(self):
        self.cfg.today["enabled"] = False
        self.assertIn("## Journal: today", handlers.today_text(self.cfg, NOW))

    def test_a_short_budget_does_not_say_nothing_was_recorded(self):
        """``render_data`` returns "" for "nothing to say" and for "the header and one line do not fit"; only the
        first is an empty day. The MCP face has no turn budget to keep."""
        self.cfg.today["max_chars"] = 150
        text = handlers.today_text(self.cfg, NOW)
        self.assertNotIn("nothing recorded", text)
        self.assertIn("2026-10-15-j", text)

    def test_an_empty_day_says_so(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(handlers.today, "lines_of", return_value=[]):
            text = handlers.today_text(make_cfg(Path(tmp)), NOW)
        self.assertIn("nothing recorded yet today", text)

    def test_entries_are_the_web_apps_summaries(self):
        got = handlers.entry_list(self.cfg, NOW)
        stems = [i["stem"] for i in got["items"]]
        self.assertEqual(stems, ["2026-10-15-j", "2026-10-15-fix-life-s01", "2026-10-14-evening"])
        self.assertIsNone(got["next"])
        lib = __import__("journal.library", fromlist=["load"]).load(self.cfg)
        self.assertEqual(got["items"], [document.summary(self.cfg, p, lib) for p, _ in api.filter_entries(self.cfg)])
        # his own pages (2026-10-14.md, a hand-written -thoughts.md) are never listed
        self.assertNotIn("2026-10-14", stems)
        self.assertNotIn("2026-10-14-thoughts", stems)

    def test_search_reads_his_words_only(self):
        found = [i["stem"] for i in handlers.entry_list(self.cfg, NOW, query="HARBOUR")["items"]]
        self.assertEqual(found, ["2026-10-15-j", "2026-10-14-evening"])
        self.assertEqual(handlers.entry_list(self.cfg, NOW, query=HERS)["items"], [])
        self.assertEqual([i["stem"] for i in handlers.entry_list(self.cfg, NOW, kind="j")["items"]], ["2026-10-15-j"])

    def test_entries_page_and_filter_by_date(self):
        first = handlers.entry_list(self.cfg, NOW, limit=2)
        self.assertEqual(len(first["items"]), 2)
        rest = handlers.entry_list(self.cfg, NOW, limit=2, cursor=first["next"])
        self.assertEqual([i["stem"] for i in rest["items"]], ["2026-10-14-evening"])
        self.assertEqual([i["stem"] for i in handlers.entry_list(self.cfg, NOW, date_to="2026-10-14")["items"]],
                         ["2026-10-14-evening"])
        with self.assertRaises(handlers.Refused):
            handlers.entry_list(self.cfg, NOW, date_from="last week")
        with self.assertRaises(handlers.Refused):
            handlers.entry_list(self.cfg, NOW, date_from="2026-10-15", date_to="2026-10-14")
        with self.assertRaises(handlers.Refused):
            handlers.entry_list(self.cfg, NOW, query="x" * 201)

    def test_unknown_or_hand_written_entries_are_refused(self):
        for stem in ("2026-10-14", "2026-10-14-thoughts", "2026-10-13-evening", "../state/x", None):
            with self.subTest(stem), self.assertRaises(handlers.Refused):
                handlers.entry_text(self.cfg, stem)

    def test_practices_and_programs(self):
        rows = {p["id"]: p for p in handlers.practice_list(self.cfg)["practices"]}
        self.assertTrue({"morning", "evening", "midweek", "weekly", "gratitude"} <= set(rows))
        self.assertEqual(rows["gratitude"]["source"], "user")
        self.assertEqual(rows["gratitude"]["schedule"], "daily 21:30")
        status = handlers.program_status(self.cfg, NOW)
        self.assertIn("fix-life", [p["id"] for p in status["programs"]])
        self.assertIn("text", status)


class VoicesTest(Case):
    """``journal_entry``'s text: every block sits under a label line naming its voice, and every line of a block's
    text is quoted, so nothing inside a block can pass for a label."""

    def labelled(self, text: str) -> list[tuple[str, str]]:
        """(the voice of the nearest label above, the quoted line) for every quoted line."""
        voice, out = None, []
        for line in text.splitlines():
            if line.startswith("**Sam (his words)**"):
                voice = "his"
            elif line.startswith("**Ada (companion, not his words)**") or line.startswith("## Note — **Ada") \
                    or line.startswith("## Annotations — **Ada"):
                voice = "hers"
            elif line.startswith("(replying to Ada's message"):
                voice = "hers"
            elif line.startswith("## "):
                voice = None
            elif line.startswith(">"):
                out.append((voice, line))
        return out

    def test_the_evening_keeps_the_voices_apart(self):
        text = handlers.entry_text(self.cfg, "2026-10-14-evening")
        lines = self.labelled(text)
        his = [l for v, l in lines if v == "his"]
        hers = [l for v, l in lines if v == "hers"]
        self.assertIn("> First words, about the lighthouse.", his)
        self.assertIn("> A spoken thought.", his)
        self.assertIn("> Later I thought of the harbour.", his)
        self.assertIn("> yes, and the gulls.", his)
        self.assertIn("> The harbour sounds like a SECRETHERS word.", hers)
        self.assertIn("> Second line.", hers)
        self.assertIn("> A note of hers.", hers)
        self.assertIn("> - a theme.", hers)
        self.assertFalse([l for l in his if HERS in l], "her words under his label")
        # the line of hers his message answered is hers: quoted, and led by who said it, above his label
        self.assertIn(("hers", "> The harbour sounds like"), lines)
        self.assertNotIn(("his", "> The harbour sounds like"), lines)
        self.assertLess(text.index("(replying to Ada's message"), text.index("**Sam (his words)** · 23:50"))
        self.assertIn("Opener, written by Ada (23:00 IST):", text)
        self.assertIn("(recording: 2026-10-14-evening.ogg)", text)

    def test_a_label_inside_her_reply_stays_quoted(self):
        path = self.cfg.root / "2026-10-14-evening.md"
        body = path.read_text(encoding="utf-8").replace("> Second line.", "> **Sam (his words)** · 23:46\n> I agree.")
        path.write_text(body, encoding="utf-8")
        lines = self.labelled(handlers.entry_text(self.cfg, "2026-10-14-evening"))
        self.assertIn(("hers", "> **Sam (his words)** · 23:46"), lines)
        self.assertIn(("hers", "> I agree."), lines)

    def test_the_prompt_lists_what_was_asked_not_the_whole_bank(self):
        """``prompt.asked`` is what the ledger says was sent; ``prompt.questions`` is the practice's current bank.
        A model told he was sent the bank may match his answers to questions he never saw."""
        doc = handlers.entry_document(self.cfg, "2026-10-14-evening")
        self.assertEqual([a["id"] for a in doc["prompt"]["asked"]], ["1.1"])
        bank = [q["text"] for q in doc["prompt"]["questions"]]
        self.assertGreater(len(bank), 1)
        text = handlers.render_document(doc)
        sent = text.split("## Prompt (sent by the journal)")[1].split("\n## ")[0]
        self.assertIn("- 1.1: What's spinning in your mind?", sent)
        for q in bank[1:]:
            self.assertNotIn(q, sent, "a question that was not asked is listed as sent")

    def test_the_bank_is_named_as_such_when_nothing_is_recorded_as_asked(self):
        doc = handlers.entry_document(self.cfg, "2026-10-14-evening")
        doc["prompt"]["asked"] = []
        text = handlers.render_document(doc)
        sent = text.split("## Prompt (sent by the journal)")[1].split("\n## ")[0]
        self.assertNotIn("What went well today?", sent)
        self.assertIn("## The practice's questions (not necessarily asked)", text)
        self.assertIn("- 2.1: What went well today?", text.split("not necessarily asked")[1])

    def test_the_text_is_the_document_his_blocks_verbatim(self):
        doc = handlers.entry_document(self.cfg, "2026-10-14-evening")
        text = handlers.render_document(doc)
        for block in doc["blocks"]:
            for line in block["text"].split("\n"):
                self.assertIn(f"> {line}" if line else ">", text)
        self.assertEqual({b["kind"] for b in doc["blocks"]}, {"his", "hers"})

    def test_a_program_session_labels_its_phases_and_summary(self):
        text = handlers.entry_text(self.cfg, "2026-10-15-fix-life-s01")
        self.assertIn("**Sam (his words)** · 06:14 IST · text · phase 1", text)
        self.assertIn("## Summary (written by the journal from Sam's answers)", text)


class SaveTest(Case):
    def test_a_note_lands_verbatim_and_appends_only(self):
        before = _digest(self.cfg.root)
        words = "  Odd   spacing, a *star*, ünïcode — and\ttabs.\n\nSecond paragraph.\n- a list line"
        got = handlers.save(self.cfg, NOW, words)
        self.assertEqual(got["rule"], "/j")
        self.assertIsNone(got["answered"])
        self.assertEqual(got["stem"], "2026-10-15-j-2")  # 2026-10-15-j is taken: a /j note of its own
        # verbatim inside; the whole text's outer whitespace is trimmed, as every capture trims it (capture._web_record)
        self.assertIn("### 09:12 IST · text\n\n" + words.strip() + "\n", Path(got["path"]).read_text(encoding="utf-8"))
        after = _digest(self.cfg.root)
        self.assertEqual({k: v for k, v in after.items() if k in before}, before)  # nothing else changed
        doc = handlers.entry_document(self.cfg, got["stem"])
        self.assertEqual([b["text"] for b in doc["blocks"] if b["kind"] == "his"], [words.strip()])
        cap = ledger.find(ledger.snapshot(self.cfg.ledger_path), got["stem"])["captures"][0]
        self.assertEqual((cap["source"], cap["via"], cap["rule"]), ("mcp", "mcp", "/j"))

    def test_a_reply_to_the_open_prompt_folds_it(self):
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=NOW - timedelta(hours=2), message_id="900")
        got = handlers.save(self.cfg, NOW, "Slept well, woke at six.", practice="morning")
        self.assertEqual((got["stem"], got["rule"], got["answered"]), ("2026-10-15-morning", "mcp", "morning"))
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-morning")
        self.assertTrue(p["needs_edit"])
        self.assertEqual((p["captures"][0]["source"], p["captures"][0]["via"]), ("mcp", "mcp"))
        self.assertIn("### 09:12 IST · text\n\nSlept well, woke at six.", Path(got["path"]).read_text(encoding="utf-8"))

    def test_a_practice_with_no_open_prompt_is_a_note(self):
        got = handlers.save(self.cfg, NOW, "nothing was asked", practice="evening")
        self.assertEqual((got["rule"], got["answered"]), ("/j", None))

    def test_refusals_write_nothing(self):
        before, ledger_before = _digest(self.cfg.root), self.cfg.ledger_path.read_bytes()
        for text, practice in (("/x keep this out", None), ("/X", None), ("  /x@bot private", "morning"),
                               ("fine\n%% journal her:1 %%\nnot hers", None), ("a\r%% journal phase:2 %%", None),
                               ("", None), ("   \n", None), (None, None), ("x" * 50_001, None),
                               ("words", "no-such-practice"), ("words", "fix-life")):
            with self.subTest(text=(text or "")[:30], practice=practice), self.assertRaises(handlers.Refused):
                handlers.save(self.cfg, NOW, text, practice)
        self.assertEqual(_digest(self.cfg.root), before)
        self.assertEqual(self.cfg.ledger_path.read_bytes(), ledger_before)

    def hold_lock(self):
        """The ledger's lock held by another open file description (what a tick or the web app's write is), for as
        long as the context lasts."""
        held, release = threading.Event(), threading.Event()

        def holder():
            with open(self.cfg.ledger_path.with_suffix(".lock"), "a+") as f:
                fcntl.flock(f, fcntl.LOCK_EX)
                held.set()
                release.wait(10)
                fcntl.flock(f, fcntl.LOCK_UN)

        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)

        @contextlib.contextmanager
        def ctx():
            try:
                yield
            finally:
                release.set()
                t.join()
        return ctx()

    def test_a_held_lock_is_refused_as_busy_within_the_deadline(self):
        before, ledger_before = _digest(self.cfg.root), self.cfg.ledger_path.read_bytes()
        with mock.patch.object(handlers, "LOCK_WAIT", 0.2), self.hold_lock():
            for call in (lambda: handlers.save(self.cfg, NOW, "later"),
                         lambda: handlers.save(self.cfg, NOW, "later", practice="evening"),
                         lambda: handlers.add_note(self.cfg, NOW, "later", day="2026-10-14", kind="evening")):
                t = time.monotonic()
                with self.assertRaisesRegex(handlers.Refused, "busy"):
                    call()
                self.assertLess(time.monotonic() - t, 2.0)
        self.assertEqual(_digest(self.cfg.root), before)
        self.assertEqual(self.cfg.ledger_path.read_bytes(), ledger_before)


class NoteTest(Case):
    def test_her_note_goes_under_her_heading(self):
        got = handlers.add_note(self.cfg, NOW, "A thread about the sea, inferred.", day="2026-10-14", kind="evening")
        self.assertEqual((got["stem"], got["voice"]), ("2026-10-14-evening", "companion"))
        body = Path(got["path"]).read_text(encoding="utf-8")
        self.assertTrue(body.rstrip().endswith("### 09:12 IST\n\nA thread about the sea, inferred."))
        doc = handlers.entry_document(self.cfg, "2026-10-14-evening")
        self.assertIn("A thread about the sea, inferred.", [n["text"] for n in doc["analysis"]["notes"]])
        self.assertFalse([b for b in doc["blocks"] if "the sea, inferred" in b["text"]])

    def handwritten(self, name: str) -> tuple[Path, bytes]:
        path = self.cfg.root / name
        path.write_text("# My own evening page\n\nSomething I wrote myself, with no front matter.\n", encoding="utf-8")
        return path, path.read_bytes()

    def test_a_hand_written_page_with_a_ritual_name_never_receives_her_note(self):
        """The web app answers 409 not-engine-entry to any write on such a file: this face follows its rules."""
        page, was = self.handwritten("2026-10-13-evening.md")
        other, other_was = self.handwritten("2026-10-12-morning.md")
        before = _digest(self.cfg.root)
        for day, kind in (("2026-10-13", "evening"), ("2026-10-12", None), ("2026-10-12", "morning")):
            with self.subTest(day=day, kind=kind), self.assertRaisesRegex(handlers.Refused, "yours.*nothing was saved"):
                handlers.add_note(self.cfg, NOW, "Ada's inference.", day=day, kind=kind)
        self.assertEqual((page.read_bytes(), other.read_bytes()), (was, other_was))
        self.assertEqual(_digest(self.cfg.root), before)

    def test_a_day_with_no_entry_gets_a_note_entry_of_hers(self):
        got = handlers.add_note(self.cfg, NOW, "A first note.", day="2026-10-20")
        self.assertEqual(got["stem"], "2026-10-20-note")
        self.assertIn("A first note.", Path(got["path"]).read_text(encoding="utf-8"))

    def test_an_off_the_record_note_is_refused(self):
        before = _digest(self.cfg.root)
        for note in ("/x he said this off the record", "  /X@bot more", "/x"):
            with self.subTest(note), self.assertRaisesRegex(handlers.Refused, "off-the-record"):
                handlers.add_note(self.cfg, NOW, note)
        self.assertEqual(_digest(self.cfg.root), before)

    def test_refused_notes(self):
        before = _digest(self.cfg.root)
        for note, day, kind in (("", None, None), ("x\n%% journal his:9 %%", None, None),
                                ("ok", "yesterday", None), ("ok", None, "nonsense")):
            with self.subTest(note=note, day=day, kind=kind), self.assertRaises(handlers.Refused):
                handlers.add_note(self.cfg, NOW, note, day, kind)
        self.assertEqual(_digest(self.cfg.root), before)


class StartTest(Case):
    def test_a_practice_is_spawned_as_journal_send(self):
        got = handlers.start(self.cfg, "evening", spawn=self.spawn)
        self.assertEqual(self.spawned, [["send", "evening", "--hold-off-record"]])
        self.assertEqual((got["spawned"], got["practice"]), (True, "evening"))
        handlers.start(self.cfg, "evening-journal", spawn=self.spawn)  # the legacy alias, as /start takes it
        self.assertEqual(self.spawned[-1], ["send", "evening", "--hold-off-record"])

    def test_what_is_not_sent(self):
        (self.cfg.practices_dir / "quiet.md").write_text(
            "---\nkind: practice\nid: quiet\ntitle: Quiet\nenabled: false\n---\n- One?\n", encoding="utf-8")
        for word in ("nope", "fix-life", "quiet", "", "evening now"):
            with self.subTest(word), self.assertRaises(handlers.Refused):
                handlers.start(self.cfg, word, spawn=self.spawn)
        self.assertEqual(self.spawned, [])

    def test_a_spawn_that_cannot_start_is_refused(self):
        def broken(cfg, args):
            raise OSError("no")
        with self.assertRaises(handlers.Refused):
            handlers.start(self.cfg, "evening", spawn=broken)


class PrivacyTest(Case):
    """No tool opens the chat transcript: every handler runs with ``sqlite3.connect`` made to fail, and the journal
    beside it has a sessions database holding a chat thread (``build_journal``)."""

    def test_no_tool_opens_a_database(self):
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=NOW - timedelta(hours=2), message_id="900")
        calls = []

        def refuse(*a, **k):
            calls.append(a)
            raise AssertionError("a tool opened a database")

        with mock.patch.object(sqlite3, "connect", refuse):
            runs = [lambda: handlers.today_text(self.cfg, NOW),
                    lambda: handlers.entry_list(self.cfg, NOW, query="harbour"),
                    lambda: handlers.entry_text(self.cfg, "2026-10-14-evening"),
                    lambda: handlers.entry_text(self.cfg, "2026-10-15-fix-life-s01"),
                    lambda: handlers.practice_list(self.cfg),
                    lambda: handlers.program_status(self.cfg, NOW),
                    lambda: handlers.save(self.cfg, NOW, "a line", "morning"),
                    lambda: handlers.save(self.cfg, NOW, "another"),
                    lambda: handlers.add_note(self.cfg, NOW, "hers"),
                    lambda: handlers.start(self.cfg, "evening", spawn=self.spawn),
                    lambda: handlers.recent_text(self.cfg, NOW)]
            for run in runs:
                run()
            with self.assertRaises(handlers.Refused):
                handlers.save(self.cfg, NOW, "/x no")
        self.assertEqual(calls, [])


EDITING = ("edit", "delete", "remove", "update", "replace", "rewrite", "patch", "set", "write", "overwrite", "amend",
           "modify", "change", "erase", "clear", "truncate")
# The whole surface, tool by tool. A tool added here is a decision about what an MCP client may do to the journal.
EXPECTED_TOOLS = {"journal_today", "journal_entries", "journal_entry", "journal_save", "journal_practices",
                  "journal_start", "journal_program_status", "journal_add_note"}


class NothingEditedTest(unittest.TestCase):
    """README *Guarantees*: the companion's words are never editable, on any surface. Over MCP that holds because the
    face has no tool that edits or deletes anything: it reads, appends his words (``journal_save``) and the companion's
    notes (``journal_add_note``, which only appends), and asks the engine to send. These read ``server.py`` as source,
    so they run without the SDK; ``ServerTest.test_no_tool_edits_or_deletes`` checks the registered tools too."""

    SERVER = Path(__file__).resolve().parents[1] / "src" / "journal" / "mcp" / "server.py"

    def registered(self) -> list[str]:
        names = []
        for node in ast.walk(ast.parse(self.SERVER.read_text(encoding="utf-8"))):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr == "tool":
                        names += [kw.value.value for kw in dec.keywords if kw.arg == "name"]
        return names

    def test_the_tool_list_has_no_edit_or_delete_tool(self):
        names = self.registered()
        self.assertEqual(set(names), EXPECTED_TOOLS)
        self.assertEqual(len(names), len(set(names)))
        for name in names:
            with self.subTest(name):
                self.assertFalse(set(name.split("_")) & set(EDITING), f"{name} reads as a tool that changes words")

    def test_the_server_says_nothing_is_edited_or_deleted(self):
        text = self.SERVER.read_text(encoding="utf-8")
        self.assertIn("Nothing in the journal is ever edited or deleted from here.", text)

    def test_the_handlers_offer_no_edit_of_a_block(self):
        handlers_py = self.SERVER.with_name("handlers.py")
        tree = ast.parse(handlers_py.read_text(encoding="utf-8"))
        public = [n.name for n in tree.body if isinstance(n, ast.FunctionDef) and not n.name.startswith("_")]
        self.assertTrue(public)
        self.assertEqual([n for n in public if set(n.split("_")) & set(EDITING)], [])
        # the splice that edits one block of his is serve.blocks' (the web app's Edit); handlers do not import it directly
        from test_serve_fence import imported
        self.assertNotIn("blocks", imported(handlers_py))


class CliTest(Case):
    def test_journal_mcp_without_the_extra_says_how_to_install_it(self):
        err = io.StringIO()
        hidden = {k: None for k in ("mcp", "mcp.server", "mcp.server.mcpserver", "journal.mcp.server")}
        with mock.patch.dict(sys.modules, hidden), contextlib.redirect_stderr(err):
            sys.modules.pop("journal.mcp.server", None)
            code = cli.mcp(lambda: self.cfg)
        self.assertEqual(code, 1)
        self.assertIn("journal[mcp]", err.getvalue())
        # a reinstall that drops the hermes extra loses PyYAML (and so the Hermes settings layer): name both forms
        self.assertIn("journal[hermes,mcp]", err.getvalue())
        self.assertIn("extras", err.getvalue())

    def test_the_command_is_registered(self):
        self.assertEqual(cli.build_parser().parse_args(["--config", "x.json", "mcp"]).command, "mcp")


@unittest.skipUnless(HAS_SDK, "the MCP SDK (journal[mcp]) is not installed")
class ServerTest(Case):
    """The tools through the SDK's own dispatch, in process (no transport)."""

    def setUp(self):
        super().setUp()
        from journal.mcp import server
        self.server = server.build(lambda: self.cfg, clock=lambda: NOW, spawn=self.spawn)

    def call(self, name, args):
        return asyncio.run(self.server.call_tool(name, args))

    def test_the_off_the_record_command_named_is_the_configured_one(self):
        """The handlers refuse the configured command; the client is told that one, not a hard-coded /x, since after
        a bare command it is the client that must stop saving (this face cannot see the conversation)."""
        from journal.mcp import server
        self.cfg.conversations["opt_out_prefix"] = "/hush"
        srv = server.build(lambda: self.cfg, clock=lambda: NOW, spawn=self.spawn)
        tools = {t.name: t for t in asyncio.run(srv.list_tools())}
        texts = [server.instructions(self.cfg), tools["journal_save"].description,
                 tools["journal_add_note"].description]
        for text in texts:
            self.assertIn("/hush", text)
            self.assertNotIn("/x", text)
        self.assertIn("after a bare /hush", texts[0])
        from mcp.server.mcpserver.exceptions import ToolError
        with self.assertRaisesRegex(ToolError, "off-the-record"):
            asyncio.run(srv.call_tool("journal_save", {"text": "/hush private"}))

    def test_no_tool_edits_or_deletes(self):
        """README *Guarantees*: the companion's words are never editable, on any surface. The registered tools, as a
        client sees them: none edits or deletes, and the two that write only append."""
        tools = asyncio.run(self.server.list_tools())
        self.assertEqual({t.name for t in tools}, EXPECTED_TOOLS)
        for t in tools:
            with self.subTest(t.name):
                self.assertFalse(set(t.name.split("_")) & set(EDITING))
                self.assertFalse(t.annotations and t.annotations.destructive_hint is True)
                if t.name in ("journal_save", "journal_add_note"):
                    self.assertFalse(t.annotations.read_only_hint)
                    self.assertFalse(t.annotations.idempotent_hint)
        # what the two writing tools take: words to append and where, never a block to replace or a key to splice
        inputs = {t.name: set(t.input_schema.get("properties", {})) for t in tools}
        self.assertEqual(inputs["journal_save"], {"text", "practice"})
        self.assertEqual(inputs["journal_add_note"], {"note", "date", "kind"})

    def test_the_tools(self):
        from journal.mcp import server
        tools = asyncio.run(self.server.list_tools())
        self.assertEqual(sorted(t.name for t in tools), sorted(server.TOOLS))
        reads = {t.name for t in tools if t.annotations and t.annotations.read_only_hint}
        self.assertEqual(reads, {"journal_today", "journal_entries", "journal_entry", "journal_practices",
                                 "journal_program_status"})

    def test_calls(self):
        text = self.call("journal_today", {}).content[0].text
        self.assertIn("## Journal: today", text)
        entry = self.call("journal_entry", {"stem": "2026-10-14-evening"}).content[0].text
        self.assertIn("**Ada (companion, not his words)**", entry)
        saved = json.loads(self.call("journal_save", {"text": "from a client"}).content[0].text)
        self.assertIn("from a client", Path(saved["path"]).read_text(encoding="utf-8"))
        self.call("journal_start", {"practice": "morning"})
        self.assertEqual(self.spawned, [["send", "morning", "--hold-off-record"]])

    def test_a_refusal_is_the_tools_error_text(self):
        from mcp.server.mcpserver.exceptions import ToolError
        with self.assertRaisesRegex(ToolError, "off-the-record"):
            self.call("journal_save", {"text": "/x not this"})

    def test_resources(self):
        uris = sorted(str(r.uri) for r in asyncio.run(self.server.list_resources()))
        self.assertEqual(uris, ["journal://entries/recent", "journal://today"])
        got = list(asyncio.run(self.server.read_resource("journal://today")))
        self.assertIn("## Journal: today", got[0].content)


@unittest.skipUnless(HAS_SDK, "the MCP SDK (journal[mcp]) is not installed")
class StdioTest(CleanEnv):
    """``journal --config FILE mcp`` as a client starts it: a real process over stdio, so anything the command printed
    to stdout would break the protocol here. The journal is a fresh temporary one."""

    def test_a_client_session(self):
        import os

        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client

        from helpers import ROOT
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Journal"
            root.mkdir()
            conf = Path(tmp) / "config.json"
            conf.write_text(json.dumps({"root": str(root), "state_dir": str(Path(tmp) / "state"), "timezone": "UTC",
                                        "user": {"name": "Sam"}, "companion": {"name": "Ada"}}),
                            encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k not in ("HERMES_HOME", "JOURNAL_CONFIG")}
            env["PYTHONPATH"] = str(ROOT / "src")
            params = StdioServerParameters(command=sys.executable, args=["-m", "journal", "--config", str(conf), "mcp"],
                                           env=env, cwd=tmp)

            async def session():
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as s:
                        init = await s.initialize()
                        tools = [t.name for t in (await s.list_tools()).tools]
                        saved = await s.call_tool("journal_save", {"text": "Words from a client."})
                        stem = json.loads(saved.content[0].text)["stem"]
                        entry = (await s.call_tool("journal_entry", {"stem": stem})).content[0].text
                        refused = await s.call_tool("journal_save", {"text": "/x not this"})
                        return init, tools, entry, refused

            init, tools, entry, refused = asyncio.run(asyncio.wait_for(session(), 60))
            self.assertEqual(init.server_info.name, "journal")
            self.assertIn("journal_save", tools)
            self.assertIn("**Sam (his words)**", entry)
            self.assertIn("> Words from a client.", entry)
            self.assertTrue(refused.is_error)
            self.assertIn("off-the-record", refused.content[0].text)
            self.assertEqual(len(list(root.glob("*.md"))), 1)


if __name__ == "__main__":
    unittest.main()
