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


if __name__ == "__main__":
    import unittest
    unittest.main()
