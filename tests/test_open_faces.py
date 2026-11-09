"""OPEN-FACES (docs/OPEN-PROMPTS.md, package 2): a practice opened on a face is a view (``actions.view``: the web app's
Write now, ``journal open``, MCP's ``journal_start``), words for an entry already written go through that entry
(``actions.add``: ``journal write --entry``), and the CLI writes his words through ``actions`` (``journal write``,
from ``$EDITOR`` as git commit does, or stdin). Nothing a face writes is a chat boundary, and every write refuses
``/x``, the engine's marker lines and the transcript placeholder."""
import contextlib
import hashlib
import io
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from test_open_core import WED, Base, at

from journal import actions, audio, cli, ledger


def checksum(cfg) -> str:
    """Every journal file and the state files a write could touch: what a view or a refusal leaves as it was."""
    h = hashlib.sha256()
    for d in (Path(cfg.root), Path(cfg.state_dir)):
        for f in sorted(d.rglob("*")) if d.exists() else ():
            if f.is_file() and not f.name.endswith(".lock"):
                h.update(str(f).encode() + f.read_bytes())
    return h.hexdigest()


class ViewTest(Base):
    def test_opening_a_due_practice_shows_its_plain_opener_and_questions_and_writes_nothing(self):
        before = checksum(self.cfg)
        v = actions.view(self.cfg, "water", now=at(10))
        self.assertEqual((v["practice"], v["title"], v["emoji"]), ("water", "Water", "💧"))
        self.assertEqual(v["prompt"]["opener"], "Water, Sam 💧 One line.")
        self.assertEqual(v["prompt"]["questions"], [{"id": "1", "text": "Did you drink enough?", "section": None}])
        self.assertEqual((v["slot"]["id"], v["slot"]["state"], v["slot"]["step"], v["slot"]["at"]),
                         ("2026-10-14-water", "due", 1, "09:00"))
        self.assertIn("09:00", v["fills"])
        self.assertEqual(checksum(self.cfg), before)
        self.assertFalse(ledger.snapshot(self.cfg.ledger_path)["prompts"])

    def test_the_view_says_which_slot_writing_now_would_fill(self):
        self.answer("water", "a glass", at(9, 30))
        v = actions.view(self.cfg, "water", now=at(10))
        self.assertEqual((v["slot"]["id"], v["slot"]["step"]), ("2026-10-14-water-2", 3))   # early: the 15:00 slot
        self.assertIn("15:00", v["fills"])
        self.answer("water", "another", at(15, 30))
        v = actions.view(self.cfg, "water", now=at(16))
        self.assertEqual((v["slot"]["id"], v["slot"]["step"], v["slot"]["at"]), ("2026-10-14-water-3", 4, None))
        self.assertIn("extra", v["fills"])

    def test_an_open_delivered_prompt_is_shown_as_it_was_sent(self):
        self.send(at(9))
        v = actions.view(self.cfg, "water", now=at(9, 10))
        self.assertEqual((v["slot"]["id"], v["slot"]["state"], v["slot"]["delivered"]), ("2026-10-14-water", "open", True))
        self.assertEqual(v["prompt"]["opener"], self.prompt("2026-10-14-water")["opener"])
        self.assertTrue(v["prompt"]["sent_at_local"])

    def test_a_slot_of_another_day_is_named_plainly(self):
        self.send(at(23), only="evening")
        v = actions.view(self.cfg, "evening", now=at(4, 30) + timedelta(days=1))   # 04:30 Thursday: Wednesday's is open (6 h)
        self.assertEqual((v["slot"]["date"], v["slot"]["step"]), ("2026-10-14", 1))
        self.assertEqual(v["fills"], "Answers yesterday's prompt in the chat, sent at 23:00.")

    def test_a_practice_turned_off_is_refused_as_send_refuses_it(self):
        (Path(self.cfg.practices_dir) / "quiet.md").write_text(
            "---\nkind: practice\nid: quiet\ntitle: Quiet\nenabled: false\n---\n- One?\n", encoding="utf-8")
        before = checksum(self.cfg)
        for call in (lambda: actions.view(self.cfg, "quiet", now=at(10)),
                     lambda: actions.answer(self.cfg, "quiet", "words", via="web", now=at(10))):
            with self.assertRaises(actions.Refused) as e:
                call()
            self.assertEqual(e.exception.code, "disabled")
        self.assertEqual(checksum(self.cfg), before)

    def test_a_program_or_unknown_id_is_refused(self):
        for word in ("nope", "fix-life", ""):
            with self.subTest(word), self.assertRaises(actions.Refused) as e:
                actions.view(self.cfg, word, now=at(10))
            self.assertEqual(e.exception.code, "not-a-practice")


class AddTest(Base):
    def setUp(self):
        super().setUp()
        self.stem = Path(self.answer("water", "first glass", at(9, 30))["path"]).stem

    def test_words_for_an_entry_already_written_go_into_it(self):
        info = actions.add(self.cfg, self.stem, "and a second thought", at(11), via="cli", now=at(11))
        body = Path(info["path"]).read_text(encoding="utf-8")
        self.assertEqual(Path(info["path"]).stem, self.stem)
        self.assertLess(body.index("first glass"), body.index("and a second thought"))
        cap = self.prompt(self.stem)["captures"][-1]
        self.assertEqual((cap["via"], cap["rule"], info["rule"]), ("cli", "cli", "cli"))
        # not choosing the practice again: the 15:00 slot is untouched
        self.assertIsNone(self.prompt("2026-10-14-water-2"))

    def test_the_refusals_hold_and_write_nothing(self):
        before = checksum(self.cfg)
        for text, code in (("/x not this", "off-the-record"), ("fine\n/x and this", "off-the-record"),
                           ("%% journal her:1 %%", "reserved-line"), (f"a\n{audio.TRANSCRIBING}", "placeholder"),
                           ("  \n", "empty")):
            with self.subTest(text), self.assertRaises(actions.Refused) as e:
                actions.add(self.cfg, self.stem, text, via="cli", now=at(11))
            self.assertEqual(e.exception.code, code)
        self.assertEqual(checksum(self.cfg), before)

    def test_only_an_entry_the_engine_wrote_and_not_a_programs(self):
        (self.cfg.root / "2026-10-13-evening.md").write_text("# my own page\n", encoding="utf-8")
        (self.cfg.root / "2026-10-14-fix-life-s01.md").write_text(
            "---\ndate: 2026-10-14\nkind: fix-life\nprogram: fix-life\nrun: r1\ntype: journal\n---\n\n# Session\n",
            encoding="utf-8")
        before = checksum(self.cfg)
        for stem, code in (("2026-10-13-evening", "not-engine-entry"), ("2026-10-14-nothing", "not-an-entry"),
                           ("../etc", "not-an-entry"), ("2026-10-14-fix-life-s01", "program-entry")):
            with self.subTest(stem), self.assertRaises(actions.Refused) as e:
                actions.add(self.cfg, stem, "words", via="cli", now=at(11))
            self.assertEqual(e.exception.code, code)
        self.assertEqual(checksum(self.cfg), before)

    def test_words_dated_before_the_entrys_day_are_refused(self):
        with self.assertRaises(actions.Refused) as e:
            actions.add(self.cfg, self.stem, "words", at(23, day=WED - timedelta(days=1)), via="cli", now=at(11))
        self.assertEqual(e.exception.code, "bad-time")

    def test_no_face_write_is_a_chat_boundary(self):
        actions.add(self.cfg, self.stem, "more", via="cli", now=at(11))
        actions.answer(self.cfg, "morning", "slept", at(11), via="cli", now=at(11))
        actions.note(self.cfg, "a note", at(11), via="cli", now=at(11))
        for name in ("sent.jsonl", "send-times.json"):
            self.assertFalse((self.cfg.state_dir / name).exists(), name)
        self.assertEqual(self.bot.calls, [])


class Clocked(Base):
    """The CLI over the same journal, its clock held at ``self.now``."""

    def setUp(self):
        super().setUp()
        test = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return test.now.astimezone(tz) if tz else test.now
        p = mock.patch.object(cli, "datetime", Clock)
        p.start()
        self.addCleanup(p.stop)

    def cli(self, *argv, stdin="", tty=False):
        args = cli.build_parser().parse_args(list(argv))
        out, err = io.StringIO(), io.StringIO()
        fake_in = io.StringIO(stdin)
        fake_in.isatty = lambda: tty
        with mock.patch.object(sys, "stdin", fake_in), contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = cli.run(args, cfg_loader=lambda: self.cfg)
        return code, out.getvalue(), err.getvalue()

    def editor(self, body: str) -> str:
        """An ``$EDITOR`` that writes ``body`` above what the file holds (git's template stays below the scissors),
        and keeps a copy of the template it was given."""
        script = Path(self.tmp.name) / "editor.py"
        seen = Path(self.tmp.name) / "seen.txt"
        script.write_text("import sys, pathlib\n"
                          "p = pathlib.Path(sys.argv[1])\n"
                          f"pathlib.Path({str(seen)!r}).write_text(p.read_text())\n"
                          f"p.write_text({body!r} + p.read_text())\n", encoding="utf-8")
        return f"{sys.executable} {script}"


class OpenCommandTest(Clocked):
    def test_journal_open_prints_the_practice_and_writes_nothing(self):
        self.now = at(10)
        before = checksum(self.cfg)
        code, out, _ = self.cli("open", "water")
        self.assertEqual(code, 0)
        for part in ("Water", "Water, Sam 💧 One line.", "Did you drink enough?", "09:00", "journal write water"):
            self.assertIn(part, out)
        self.assertEqual(checksum(self.cfg), before)
        code, out, _ = self.cli("open", "water", "--json")
        self.assertEqual(json.loads(out)["slot"]["id"], "2026-10-14-water")

    def test_an_unknown_practice_is_an_argument_error(self):
        self.now = at(10)
        code, _, err = self.cli("open", "nope")
        self.assertEqual(code, 2)
        self.assertIn("nope", err)


class WriteCommandTest(Clocked):
    def test_stdin_answers_the_practice_through_actions(self):
        self.now = at(10)
        code, out, _ = self.cli("write", "water", stdin="two glasses\n")
        self.assertEqual(code, 0, out)
        p = self.prompt("2026-10-14-water")
        self.assertEqual((p["opened_via"], p["captures"][0]["via"], p["captures"][0]["rule"]), ("cli", "cli", "cli"))
        self.assertIn("two glasses", (self.cfg.root / "2026-10-14-water.md").read_text(encoding="utf-8"))
        self.assertIn("2026-10-14-water.md", out)

    def test_a_dash_reads_stdin_even_at_a_terminal(self):
        self.now = at(10)
        code, _, _ = self.cli("write", "water", "-", stdin="from a pipe", tty=True)
        self.assertEqual(code, 0)
        self.assertIn("from a pipe", (self.cfg.root / "2026-10-14-water.md").read_text(encoding="utf-8"))

    def test_without_a_practice_it_is_a_note(self):
        self.now = at(10)
        code, out, _ = self.cli("write", "-", stdin="call the dentist")
        self.assertEqual(code, 0, out)
        self.assertEqual([p["kind"] for p in self.prompts()], ["j"])
        self.assertIn("call the dentist", (self.cfg.root / "2026-10-14-j.md").read_text(encoding="utf-8"))

    def test_entry_appends_to_an_entry_already_written(self):
        self.now = at(10)
        self.cli("write", "water", stdin="first")
        self.now = at(11)
        code, _, err = self.cli("write", "--entry", "2026-10-14-water", stdin="second")
        self.assertEqual(code, 0, err)
        body = (self.cfg.root / "2026-10-14-water.md").read_text(encoding="utf-8")
        self.assertLess(body.index("first"), body.index("second"))
        self.assertIsNone(self.prompt("2026-10-14-water-2"))

    def test_at_backdates_the_words(self):
        self.now = at(10)
        code, _, err = self.cli("write", "water", "--at", at(9, 20).isoformat(), stdin="earlier")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.prompt("2026-10-14-water")["sent_at"], at(9, 20).isoformat())
        code, _, err = self.cli("write", "--at", "09:40", "-", stdin="a clock time is today's, in the journal's zone")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.prompt("2026-10-14-j")["sent_at"], at(9, 40).isoformat())

    def test_the_editor_opens_like_git_commit_and_the_template_is_left_out(self):
        self.now = at(10)
        with mock.patch.dict(os.environ, {"EDITOR": self.editor("# not a comment: a heading of his\nwords\n"),
                                          "VISUAL": ""}):
            code, out, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 0, (out, err))
        seen = (Path(self.tmp.name) / "seen.txt").read_text(encoding="utf-8")
        self.assertIn(">8", seen)
        self.assertIn("Did you drink enough?", seen)
        body = (self.cfg.root / "2026-10-14-water.md").read_text(encoding="utf-8")
        self.assertIn("# not a comment: a heading of his\nwords", body)
        self.assertNotIn(">8", body)
        self.assertNotIn("Write above this line", body)

    def test_an_empty_editor_saves_nothing(self):
        self.now = at(10)
        before = checksum(self.cfg)
        with mock.patch.dict(os.environ, {"EDITOR": self.editor("\n\n"), "VISUAL": ""}):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 1)
        self.assertIn("nothing was saved", err)
        self.assertEqual(checksum(self.cfg), before)

    def test_refusals_exit_1_and_write_nothing(self):
        self.now = at(10)
        before = checksum(self.cfg)
        for stdin in ("/x private", "ok\n/x private", "%% journal her:1 %%", audio.TRANSCRIBING):
            with self.subTest(stdin):
                code, _, err = self.cli("write", "water", stdin=stdin)
                self.assertEqual(code, 1)
                self.assertIn("nothing was saved", err)
        self.assertEqual(checksum(self.cfg), before)

    def test_argument_errors_exit_2(self):
        self.now = at(10)
        before = checksum(self.cfg)
        for argv in (("write", "nope"), ("write", "water", "--entry", "2026-10-14-water"),
                     ("write", "water", "--at", "yesterday"), ("write", "--entry", "2026-10-14-nothing")):
            with self.subTest(argv):
                self.assertEqual(self.cli(*argv, stdin="x")[0], 2)
        self.assertEqual(checksum(self.cfg), before)


class EditorReviewTest(Clocked):
    """Review round 1 (findings 1–4 and the nits): the editor path of ``journal write`` checks what it can before he
    writes, keeps his words when they are refused after, reads the clock when he saves, and cuts the template only at
    its own line."""

    def edit_with(self, body: str, then=None):
        """Stand in for the editor: write ``body`` above the template, then run ``then`` (time passing while he
        writes)."""
        test = self

        def fake(argv):
            path = Path(argv[-1])
            test.seen = path.read_text(encoding="utf-8")
            path.write_text(body + test.seen, encoding="utf-8")
            if then:
                then()
            return 0
        return mock.patch.object(cli, "_edit", fake)

    def no_editor(self):
        return mock.patch.object(cli, "_edit", side_effect=AssertionError("the editor opened"))

    def test_a_time_in_the_future_is_refused_before_the_editor_opens(self):
        self.now = at(10)
        with self.no_editor():
            code, _, err = self.cli("write", "water", "--at", at(12).isoformat(), tty=True)
        self.assertEqual(code, 2)
        self.assertIn("future", err)

    def test_an_entry_that_cannot_take_words_is_refused_before_the_editor_opens(self):
        self.now = at(10)
        (self.cfg.root / "2026-10-14-fix-life-s01.md").write_text(
            "---\ndate: 2026-10-14\nkind: fix-life\nprogram: fix-life\nrun: r1\ntype: journal\n---\n\n# S\n",
            encoding="utf-8")
        self.cli("write", "water", stdin="first")
        for argv in (("--entry", "2026-10-14-fix-life-s01"),
                     ("--entry", "2026-10-14-water", "--at", at(23, day=WED - timedelta(days=1)).isoformat())):
            with self.subTest(argv), self.no_editor():
                self.assertEqual(self.cli("write", *argv, tty=True)[0], 2)

    def test_a_clock_time_after_midnight_is_the_latest_such_time_not_ahead(self):
        self.now = at(0, 30) + timedelta(days=1)     # 00:30 on Thursday
        code, _, err = self.cli("write", "-", "--at", "23:30", stdin="late words")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.prompt("2026-10-14-j")["sent_at"], at(23, 30).isoformat())   # Wednesday's 23:30

    def test_the_clock_is_read_when_he_saves_not_when_the_editor_opened(self):
        self.now = at(8, 55)    # the view says the 09:00 slot will be filled early; the tick sends it while he writes
        def tick_sends():
            self.send(at(9))
            self.now = at(9, 10)
        with self.edit_with("two glasses\n", then=tick_sends):
            code, out, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 0, err)
        p = self.prompt("2026-10-14-water")
        self.assertTrue(p["message_id"])
        self.assertEqual(len(p["captures"]), 1)        # his words answer the prompt the chat now has
        self.assertIsNone(self.prompt("2026-10-14-water-2"))   # and the 15:00 slot is left alone

    def test_words_refused_after_the_editor_are_kept_but_never_off_the_record_ones(self):
        self.now = at(10)
        with self.edit_with("fine\n%% journal her:1 %%\n"):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 1)
        kept = Path(err.split("kept in ")[1].split()[0].rstrip("."))
        self.assertIn("%% journal her:1 %%", kept.read_text(encoding="utf-8"))
        kept.unlink()
        with self.edit_with("/x private\n"):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 1)
        self.assertNotIn("kept in", err)
        self.assertFalse(self.prompts())

    def test_without_the_scissors_line_the_template_is_still_left_out(self):
        self.now = at(10)
        test = self

        def drop_scissors(argv):
            path = Path(argv[-1])
            body = path.read_text(encoding="utf-8").replace(cli.SCISSORS + "\n", "")
            path.write_text("my words\n" + body, encoding="utf-8")
            return 0
        with mock.patch.object(cli, "_edit", drop_scissors):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 0, err)
        body = (self.cfg.root / "2026-10-14-water.md").read_text(encoding="utf-8")
        self.assertIn("my words", body)
        self.assertNotIn("Write above this line", body)
        self.assertNotIn("Water, Sam 💧 One line.\n\n#", body)

    def test_scissors_inside_a_line_of_his_is_his_words(self):
        self.now = at(10)
        with self.edit_with(f"he quoted {cli.SCISSORS} once\nand more\n"):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 0, err)
        self.assertIn("and more", (self.cfg.root / "2026-10-14-water.md").read_text(encoding="utf-8"))

    def test_a_time_out_of_range_is_an_argument_error_not_a_traceback(self):
        self.now = at(10)
        code, _, err = self.cli("write", "water", "--at", "0001-01-01T03:00", stdin="x")
        self.assertEqual(code, 2)
        self.assertNotIn("Traceback", err)
        self.assertFalse(self.prompts())

    def test_nits(self):
        self.now = at(10)
        with self.edit_with("a note\n"):
            self.assertEqual(self.cli("write", tty=True)[0], 0)
        self.assertNotIn("None", self.seen)
        self.assertEqual(self.cli("write", "j", stdin="a j is a note")[0], 0)   # as serve and MCP take j
        self.assertEqual([p["kind"] for p in self.prompts()], ["j", "j"])
        with mock.patch.object(cli, "_edit", side_effect=FileNotFoundError("vi")):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 1)
        self.assertIn("nothing was saved", err)


class EditorExitsTest(Clocked):
    """Review round 2: the editor's file lives in a private directory of its own, and on every way out of ``journal
    write`` it is either removed (off the record, or nothing of his in it) or kept and named: never left behind
    unnamed, and never with off-the-record words in it (finding 1, 5, 6); and the smaller ones (3, 4, 8)."""

    def setUp(self):
        super().setUp()
        self.tmpdir = Path(self.tmp.name) / "editor-tmp"
        self.tmpdir.mkdir()
        p = mock.patch("tempfile.tempdir", str(self.tmpdir))
        p.start()
        self.addCleanup(p.stop)
        self.now = at(10)

    def left(self) -> list[Path]:
        return [f for f in self.tmpdir.rglob("*") if f.is_file()]

    def editor(self, body: bytes | str, *, code=0, raise_after=None):
        def fake(argv):
            path = Path(argv[-1])
            old = path.read_bytes()
            path.write_bytes((body.encode() if isinstance(body, str) else body) + old)
            if raise_after is not None:
                raise raise_after
            return code
        return mock.patch.object(cli, "_edit", fake)

    def kept(self, err: str) -> Path:
        return Path(err.split("kept in ")[1].split()[0].rstrip(".;"))

    def test_an_interrupt_while_editing_off_the_record_words_leaves_nothing(self):
        with self.editor("/x the thing I do not want kept\n", raise_after=KeyboardInterrupt()), \
                self.assertRaises(KeyboardInterrupt):
            self.cli("write", "water", tty=True)
        self.assertEqual(self.left(), [])
        self.assertFalse(self.prompts())

    def test_an_interrupt_after_ordinary_words_keeps_them_and_says_where(self):
        err = io.StringIO()
        with self.editor("ordinary words\n", raise_after=KeyboardInterrupt()), \
                self.assertRaises(KeyboardInterrupt), contextlib.redirect_stderr(err):
            args = cli.build_parser().parse_args(["write", "water"])
            fake_in = io.StringIO("")
            fake_in.isatty = lambda: True
            with mock.patch.object(sys, "stdin", fake_in):
                cli.run(args, cfg_loader=lambda: self.cfg)
        self.assertIn("ordinary words", self.kept(err.getvalue()).read_text(encoding="utf-8"))

    def test_words_the_journal_cannot_read_leave_nothing_when_off_the_record(self):
        with self.editor(b"/x caf\xe9, not for the journal\n"):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertEqual(self.left(), [])

    def test_a_failure_at_the_save_keeps_his_words_and_names_the_file(self):
        with self.editor("ordinary words\n"), \
                mock.patch("journal.actions.answer", side_effect=PermissionError("the ledger")):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 1)
        self.assertNotIn("Traceback", err)
        self.assertIn("ordinary words", self.kept(err).read_text(encoding="utf-8"))

    def test_words_typed_below_the_scissors_are_kept_not_destroyed(self):
        def below(argv):
            path = Path(argv[-1])
            path.write_text(path.read_text(encoding="utf-8") + "my answer, under the question\n", encoding="utf-8")
            return 0
        with mock.patch.object(cli, "_edit", below):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 1)
        self.assertIn("my answer, under the question", self.kept(err).read_text(encoding="utf-8"))

    def test_an_editor_that_fails_after_he_saved_keeps_his_words(self):
        with self.editor("saved, then the editor failed\n", code=1):
            code, _, err = self.cli("write", "water", tty=True)
        self.assertEqual(code, 1)
        self.assertIn("saved, then the editor failed", self.kept(err).read_text(encoding="utf-8"))

    def test_nothing_is_left_after_a_save_an_off_the_record_refusal_or_an_untouched_template(self):
        with self.editor("two glasses\n"):
            self.assertEqual(self.cli("write", "water", tty=True)[0], 0)
        with self.editor("/x private\n"):
            self.assertEqual(self.cli("write", "water", tty=True)[0], 1)
        with self.editor(""):
            self.assertEqual(self.cli("write", "water", tty=True)[0], 1)
        self.assertEqual(self.left(), [])
        self.assertEqual(list(self.tmpdir.iterdir()), [])   # the private directory goes with the file

    def test_a_clock_time_within_the_slack_is_todays(self):
        code, _, err = self.cli("write", "water", "--at", "10:04", stdin="nearly now")
        self.assertEqual(code, 0, err)
        self.assertEqual(self.prompt("2026-10-14-water")["sent_at"], at(10, 4).isoformat())

    def test_the_template_names_the_slot_the_backdated_words_fill(self):
        seen = {}

        def look(argv):
            path = Path(argv[-1])
            seen["t"] = path.read_text(encoding="utf-8")
            path.write_text("earlier words\n" + seen["t"], encoding="utf-8")
            return 0
        monday = at(9, 30, day=WED - timedelta(days=2))
        with mock.patch.object(cli, "_edit", look):
            code, out, err = self.cli("write", "water", "--at", monday.isoformat(), tty=True)
        self.assertEqual(code, 0, err)
        self.assertIn("2026-10-12-water", out)
        self.assertIn("extra entry of this practice (2026-10-12-water", seen["t"])

    def test_a_slot_after_midnight_is_tonights_not_tomorrows(self):
        (Path(self.cfg.practices_dir) / "late.md").write_text(
            "---\nkind: practice\nid: late\ntitle: Late\nschedule.time: 00:30\n---\n- Still up?\n",
            encoding="utf-8")
        v = actions.view(self.cfg, "late", now=at(1) + timedelta(days=1))   # 01:00 Thursday: Wednesday's night
        self.assertEqual(v["fills"], "Fills the 00:30 slot, due now.")


if __name__ == "__main__":
    import unittest
    unittest.main()
