"""WP4 review findings, each pinned by a test that failed before its fix: a phase send never closes a bare-/x window,
an open phase takes every message only while it is the newest prompt, /break stops a phase taking everything, a
command is bound to the phase it was given for (and runs even while she is mid-reply), a quoted folded phase header
finds its phase, finishing a jumped-to session does not end the run, a session's bad annotation is one attempt, and a
re-sent split phase keeps every message id."""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import CHAT, add_prompt

from journal import capture, cli, compose, entries, ledger, library, privacy, programs, sweep, tick
from journal.hermes import commands as hcommands
from journal.hermes import hooks
from test_programs import Base, T0


class HookBase(Base):
    """Messages go through the dispatch hook, with the hook's clock held at the simulated moment."""

    def setUp(self):
        super().setUp()
        test = self
        from datetime import datetime as _dt

        class Clock(_dt):
            @classmethod
            def now(cls, tz=None):
                return test.now.astimezone(tz) if tz else test.now
        self.spawned = []
        for p in (mock.patch.object(hooks, "datetime", Clock), mock.patch.object(hooks.runtime, "load_config", lambda: self.cfg),
                  mock.patch.object(hcommands.runtime, "load_config", lambda: self.cfg),
                  mock.patch.object(hcommands, "spawn", lambda cfg, *a: self.spawned.append(a))):
            p.start()
            self.addCleanup(p.stop)
        self.n = 100

    def msg(self, text, reply_to=None):
        privacy._CACHE.clear()
        self.n += 1
        ev = SimpleNamespace(source=SimpleNamespace(platform="telegram", chat_id=CHAT), text=text,
                             message_id=str(self.n), reply_to_message_id=reply_to, message_type="TEXT", media_urls=[])
        return hooks.on_dispatch(ev)


class PrivacyTest(HookBase):
    def test_a_settle_waits_while_a_bare_x_window_is_open(self):
        self.start()
        self.now = T0 + timedelta(minutes=1)
        self.msg("my on-record answer to phase one")
        self.now = T0 + timedelta(minutes=1, seconds=30)
        self.msg("/x")
        self.now = T0 + timedelta(minutes=2)
        self.msg("private A: something I don't want kept")
        self.now = T0 + timedelta(minutes=3, seconds=5)  # the settle from the on-record answer fires
        rid = self.the_run()["id"]
        self.assertFalse(programs.settle(self.cfg, rid, self.now, self.log))
        self.assertEqual(self.where(), (1, 1))
        self.assertEqual(len(self.bot.phase_sends()), 1)  # no phase 2 went out to close his window
        self.now = T0 + timedelta(minutes=3, seconds=30)
        out = self.msg("private B: continuing the private thought")
        self.assertIn("off the record", out["text"].lower())
        body = self.entry(1).read_text(encoding="utf-8")
        self.assertNotIn("private A", body)
        self.assertNotIn("private B", body)
        self.assertEqual(entries.phase_answers(body), {1: "my on-record answer to phase one"})
        # the tick keeps trying; once the window has gone quiet the phase moves on
        privacy._CACHE.clear()
        self.now = T0 + timedelta(minutes=3, seconds=30) + timedelta(minutes=46)
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.where(), (1, 2))

    def test_a_pass_that_cannot_run_holds_the_settle(self):
        self.start()
        self.answer("done")
        with mock.patch.object(privacy, "view", lambda *a, **k: privacy.Closed("test")):
            self.assertFalse(self.settle())
        self.assertEqual(self.where(), (1, 1))
        privacy._CACHE.clear()
        self.assertTrue(self.settle())


class OpenPhaseTest(HookBase):
    def _evening_after_phase(self):
        self.start()
        sent = T0 + timedelta(minutes=30)
        add_prompt(self.cfg, kind="evening", local_date="2026-10-20", sent=sent, message_id="9001")
        self.now = sent
        return sent

    def test_a_ritual_sent_after_an_open_phase_keeps_its_replies(self):
        self._evening_after_phase()
        first = self.answer("my evening answer", after=timedelta(minutes=5))
        self.assertEqual(first["prompt"]["kind"], "evening")
        second = self.answer("and one more thing about my evening", after=timedelta(minutes=1))
        self.assertIsNone(second)  # conversation, as it was before programs: not a phase answer
        self.assertFalse(self.settle())
        self.assertEqual(self.where(), (1, 1))
        self.assertEqual(entries.phase_answers(self.entry(1).read_text(encoding="utf-8")) if self.entry(1).is_file()
                         else {}, {})

    def test_the_sweep_agrees_at_the_message_time(self):
        sent = self._evening_after_phase()
        rid = self.the_run()["id"]
        self.answer("my evening answer", after=timedelta(minutes=5))
        with ledger.locked(self.cfg.ledger_path) as data:
            self.assertIsNone(sweep.visible_at(self.cfg, data, self.now + timedelta(minutes=1)))
            # before the evening went out the phase took everything, and a replay of that moment says so
            self.assertEqual(sweep.visible_at(self.cfg, data, sent - timedelta(minutes=10))["id"], f"{rid}-s01-p1")

    def test_a_direct_reply_to_the_phase_still_lands_there(self):
        self._evening_after_phase()
        rid = self.the_run()["id"]
        mid = self.prompt(f"{rid}-s01-p1")["message_id"]
        info = self.answer("an answer to the phase, quoted", reply_to=mid)
        self.assertEqual((info["prompt"]["id"], info["rule"]), (f"{rid}-s01-p1", "direct-reply"))

    def test_the_phase_still_takes_every_message_while_it_is_the_newest_prompt(self):
        self.start()
        self.answer("first part")
        self.assertEqual(self.answer("a correction")["prompt"]["phase"], 1)


class BreakTest(HookBase):
    def test_a_break_stops_the_phase_taking_every_message(self):
        self.start()
        rid = self.the_run()["id"]
        programs.pause(self.cfg, None, self.now, self.log)
        self.assertFalse(self.prompt(f"{rid}-s01-p1")["phase_open"])
        got = [self.answer(t, after=timedelta(minutes=m)) for t, m in
               (("unrelated: what's the weather like?", 1), ("also remind me to call mum", 10), ("ok bye", 30))]
        self.assertEqual(got, [None, None, None])
        self.now += timedelta(hours=1)
        programs.start(self.cfg, "fix-life", self.now, self.log)  # /start fix-life: the phase comes again
        self.assertEqual(self.where(), (1, 1))
        self.assertNotIn("weather", self.entry(1).read_text(encoding="utf-8") if self.entry(1).is_file() else "")

    def test_a_direct_reply_on_the_break_is_carried_on_from_by_start(self):
        self.start()
        rid = self.the_run()["id"]
        mid = self.prompt(f"{rid}-s01-p1")["message_id"]
        programs.pause(self.cfg, None, self.now, self.log)
        self.assertEqual(self.answer("my answer", reply_to=mid)["rule"], "direct-reply")
        self.start()
        self.assertEqual(self.where(), (1, 2))

    def test_the_note_does_not_promise_a_next_phase_while_paused(self):
        self.start()
        rid = self.the_run()["id"]
        mid = self.prompt(f"{rid}-s01-p1")["message_id"]
        running = self.msg("answer one", reply_to=mid)["text"]
        self.assertIn("sends the next phase itself", running)
        programs.pause(self.cfg, None, self.now, self.log)
        self.now += timedelta(minutes=1)
        paused = self.msg("answer two", reply_to=mid)["text"]
        self.assertNotIn("sends the next phase itself", paused)
        self.assertIn("on a break", paused)


class StaleCommandTest(HookBase):
    def test_a_next_given_for_a_phase_the_settle_has_left_does_nothing(self):
        self.start()
        self.answer("phase one answer")
        run = self.the_run()
        given_for = programs.where(run)  # what the plugin records when he taps /next
        self.assertTrue(self.settle())  # the settle gets there first
        self.assertEqual(self.where(), (1, 2))
        moved = programs.advance(self.cfg, run["id"], self.now, self.log, reason="next",
                                 expect=programs.parse_where(given_for))
        self.assertFalse(moved)
        self.assertEqual(self.where(), (1, 2))
        self.assertNotIn("skipped", self.the_run()["sessions"]["1"]["phases"]["2"])
        self.assertIn("already moved on", self.log[-1])

    def test_two_skip_taps_skip_one_phase(self):
        self.start()
        given_for = programs.parse_where(programs.where(self.the_run()))
        self.assertTrue(programs.skip(self.cfg, None, self.now, self.log, expect=given_for))
        self.assertFalse(programs.skip(self.cfg, None, self.now, self.log, expect=given_for))
        self.assertEqual(self.where(), (1, 2))
        self.assertNotIn("skipped", self.the_run()["sessions"]["1"]["phases"]["2"])

    def test_a_back_given_for_an_earlier_phase_does_nothing(self):
        self.start()
        self.answer("one")
        self.settle()  # at (1, 2)
        old = (1, 1, 1)
        self.assertFalse(programs.back(self.cfg, None, self.now, self.log, expect=old))
        self.assertEqual(self.where(), (1, 2))

    def test_a_visit_makes_an_earlier_tap_stale_after_a_back(self):
        self.start()
        self.answer("one")
        self.settle()  # (1, 2), visit 1
        tapped = programs.parse_where(programs.where(self.the_run()))
        programs.back(self.cfg, None, self.now, self.log)  # (1, 1) again
        programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next")  # (1, 2) again, visit 2
        self.assertEqual(self.where(), (1, 2))
        self.assertFalse(programs.skip(self.cfg, None, self.now, self.log, expect=tapped))

    def test_a_next_that_lost_the_race_with_the_session_end_does_not_start_the_next_session(self):
        self.start(session=10)
        for _ in range(4):
            programs.skip(self.cfg, None, self.now, self.log)
        tapped = programs.parse_where(programs.where(self.the_run()))  # the last phase
        programs.skip(self.cfg, None, self.now, self.log)  # the session completes (another path)
        self.assertEqual(self.the_run()["sessions"]["10"]["status"], "completed")
        before = len(self.bot.phase_sends())
        self.assertFalse(programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next",
                                          expect=tapped))
        self.assertEqual(len(self.bot.phase_sends()), before)

    def test_the_plugin_binds_the_command_to_the_phase_and_the_cli_takes_it(self):
        self.start()
        rid = self.the_run()["id"]
        hcommands.program_action("next", "next")("")
        self.assertEqual(self.spawned[-1], ("program", "next", rid, "--notify", "--expect", "1:1:1"))
        hcommands.program_action("break", "pause")("")
        self.assertEqual(self.spawned[-1], ("program", "pause", rid, "--notify"))
        self.answer("done")
        self.settle()
        args = cli.build_parser().parse_args(["program", "skip", "--expect", "1:1:1"])
        with mock.patch("sys.stdout"):
            self.assertEqual(cli.run(args, cfg_loader=lambda: self.cfg), 0)
        self.assertEqual(self.where(), (1, 2))  # refused: the run is past 1:1
        args = cli.build_parser().parse_args(["program", "skip", "--expect", "nonsense"])
        with mock.patch("sys.stdout"), mock.patch("sys.stderr"):
            self.assertEqual(cli.run(args, cfg_loader=lambda: self.cfg), 2)


class BusyPathTest(HookBase):
    """Hermes runs plugin commands only when she is idle; the hook runs them before that check."""

    def test_program_commands_are_run_by_the_hook_and_dropped(self):
        self.start()
        rid = self.the_run()["id"]
        out = self.msg("/next")
        self.assertEqual(out["action"], "skip")
        self.assertEqual(self.spawned[-1], ("program", "next", rid, "--notify", "--expect", "1:1:1"))
        self.assertEqual(self.msg("/skip@journal_bot")["action"], "skip")
        self.assertEqual(self.spawned[-1][1], "skip")
        self.assertEqual(self.msg("/back")["action"], "skip")
        self.assertEqual(self.msg("/break")["action"], "skip")
        self.assertEqual(self.spawned[-1], ("program", "pause", rid, "--notify"))
        sent = len(self.bot.sends())
        self.assertEqual(self.msg("/program")["action"], "skip")
        self.assertIn("Fix your entire life in 1 day", self.bot.sends()[sent]["text"])  # answered in the chat

    def test_nothing_running_is_said_in_the_chat(self):
        self.assertEqual(self.msg("/next")["action"], "skip")
        self.assertIn("No program is running", self.bot.sends()[-1]["text"])

    def test_only_the_bare_commands_in_the_journal_chat(self):
        self.start()
        self.assertIsNone(self.msg("/next week I want to rest"))  # text, an answer
        self.assertEqual(self.spawned, [])
        ev = SimpleNamespace(source=SimpleNamespace(platform="telegram", chat_id="42"), text="/next", message_id="9",
                             reply_to_message_id=None, message_type="TEXT", media_urls=[])
        self.assertIsNone(hooks.on_dispatch(ev))  # not his chat: Hermes' business

    def test_a_command_is_not_his_words(self):
        self.start()
        rid = self.the_run()["id"]
        self.msg("/next")
        self.assertIsNone(self.prompt(f"{rid}-s01-p1").get("captures"))


class SweepTest(Base):
    def test_a_reply_quoting_a_folded_phase_goes_to_that_phase(self):
        import html as H
        import re
        self.start()
        rid = self.the_run()["id"]
        self.answer("phase one answer")
        self.settle()
        tick.fold(self.cfg, self.now, self.log)
        folded = compose.folded_phase_html(self.prompt(f"{rid}-s01-p1"), "answered ✓", self.cfg.names)
        quote = H.unescape(re.sub(r"<[^>]+>", "", folded))
        msg = {"message_id": "8888", "quote": quote, "text": "an addition to my phase-one answer", "source": "text",
               "audio_path": None}
        info = sweep.reconcile(self.cfg, msg, self.now + timedelta(minutes=1))
        self.assertEqual((info["prompt"]["phase"], info["rule"]), (1, "direct-reply"))


class JumpTest(Base):
    def test_finishing_a_jumped_to_session_does_not_end_the_run(self):
        self.start()
        first = self.the_run()["id"]
        self.answer("s1p1")
        self.settle()
        self.now += timedelta(hours=1)
        self.start(session=10)
        for _ in range(5):
            programs.skip(self.cfg, None, self.now, self.log)
        run = programs.find_run(self.cfg, first)
        self.assertEqual(run["status"], "active")
        self.assertEqual(run["sessions"]["10"]["status"], "completed")
        self.assertIsNone(run.get("insights_entry"))
        self.assertEqual(run["current"], {"session": 1, "phase": 1})  # the session he left, not a new run
        self.assertEqual(run["sessions"]["1"]["status"], "left")
        self.assertIn("continues with session 1 ", self.bot.summaries()[-1]["text"])
        self.now += timedelta(hours=1)
        again = self.start()
        self.assertEqual(again["id"], first)
        self.assertEqual(self.where(), (1, 1))
        self.assertEqual(self.the_run()["sessions"]["1"]["status"], "in_progress")
        self.assertIn("s1p1", self.entry(1).read_text(encoding="utf-8"))  # the same entry, his words kept

    def test_the_run_completes_when_every_session_is_done(self):
        self.start()
        first = self.the_run()["id"]
        prog = self.prog()
        for s in range(1, 11):
            if s > 1:
                self.now += timedelta(hours=1)
                self.start()
            for _ in prog.session(s).phases:
                programs.skip(self.cfg, None, self.now, self.log)
        self.assertEqual(programs.find_run(self.cfg, first)["status"], "completed")


class AnnotateTest(Base):
    def test_a_bad_answer_is_one_attempt_for_the_session_entry(self):
        self.start()
        for p in range(1, 7):
            self.answer(f"answer {p}")
            self.settle()
        self.assertEqual(self.the_run()["sessions"]["1"]["status"], "completed")
        self.asked.clear()
        self.model = lambda q: '{"not": "prose"}'
        for _ in range(tick.ANNOTATE_ATTEMPTS):
            self.now += timedelta(hours=1)
            before = len(self.asked)
            tick.annotate(self.cfg, self.now, self.log)
            self.assertEqual(len(self.asked) - before, 1)  # one call a tick, however many phases the session has
        self.now += timedelta(hours=1)
        before = len(self.asked)
        tick.annotate(self.cfg, self.now, self.log)
        self.assertEqual(len(self.asked), before)  # given up for the entry


class ResendTest(Base):
    def test_a_resent_split_phase_keeps_every_message_that_takes_replies(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        long_intro = ("This is a long introduction line that goes on. " * 120).strip()
        (d / "longone.md").write_text(
            "---\nkind: situational\nid: longone\ntitle: Long one\nadvance: next\n---\n"
            f"## Phase 1: Big\n### Introduction\n{long_intro}\n### Questions\n**1.1** What now?\n\n"
            "## Summary\n- {1.1}\n", encoding="utf-8")
        self.start("longone")
        rid = self.the_run()["id"]
        p = self.prompt(f"{rid}-s01-p1")
        first_ids = [*p["previous_message_ids"], p["message_id"]]
        self.assertEqual(len(first_ids), 2)
        self.now += timedelta(minutes=10)
        self.start("longone")  # /start longone again: the open phase is sent again
        p = self.prompt(f"{rid}-s01-p1")
        self.assertNotIn(p["message_id"], first_ids)
        self.assertTrue(set(first_ids) <= set(p["previous_message_ids"]))
        info = capture.capture(self.cfg, CHAT, first_ids[1], "a direct reply to the first copy",
                               self.now + timedelta(hours=4), "text", "6001")
        self.assertEqual((info["prompt"]["id"], info["rule"]), (f"{rid}-s01-p1", "direct-reply"))


# ── findings of the independent (Codex) review ───────────────────────────────

LONG = ("---\nkind: situational\nid: longone\ntitle: Long one\nadvance: next\n---\n"
        "## Phase 1: Big\n### Introduction\n{intro}\n### Questions\n**1.1** What now?\n\n## Summary\n- {{1.1}}\n")


def _long_program(cfg):
    d = Path(cfg.practices_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / "longone.md").write_text(LONG.format(intro=("This is a long introduction line that goes on. " * 120).strip()),
                                  encoding="utf-8")


class ReviewTwoTest(Base):
    def test_answering_an_older_run_makes_it_the_current_one(self):
        self.start()
        fix = self.the_run()["id"]
        mid = self.prompt(f"{fix}-s01-p1")["message_id"]
        self.now += timedelta(minutes=10)
        programs.pause(self.cfg, fix, self.now, self.log)  # (one session at a time: ONE-PROGRAM)
        self.start("prep")  # a later transition in another program
        run = programs.find_run(self.cfg, fix)   # both mid-session, as two runs started before ONE-PROGRAM can be
        run.update(status="active", paused_since=None)
        programs._save(self.cfg, run)
        self.assertEqual(programs.current_run(self.cfg)["program"], "conversation-prep")
        self.answer("back to my life", after=timedelta(minutes=5), reply_to=mid)
        self.assertEqual(programs.current_run(self.cfg)["id"], fix)

    def test_a_fresh_answer_keeps_a_situational_run_from_expiring(self):
        self.start("prep")
        rid = self.the_run()["id"]
        self.answer("on the project", after=timedelta(hours=2))
        programs.step(self.cfg, self.now + timedelta(hours=11), self.log)  # 13 h after the start, 11 after the answer
        self.assertEqual(programs.find_run(self.cfg, rid)["status"], "active")

    def test_a_chunk_that_fails_to_send_is_counted_like_any_failed_send(self):
        from journal import telegram
        _long_program(self.cfg)

        def down(cfg, method, payload):
            raise telegram.TelegramError("boom")
        with mock.patch.object(telegram, "_call", down):
            self.start("longone")
            rid = self.the_run()["id"]
            self.assertEqual(self.prompt(f"{rid}-s01-p1")["send_attempts"], 1)
            programs.step(self.cfg, self.now, self.log)
            self.assertEqual(self.prompt(f"{rid}-s01-p1")["send_attempts"], 2)
            with self.assertRaises(tick.SendFailed):  # the alert, as for the last message of a phase
                programs.step(self.cfg, self.now, self.log)

    def test_a_chunk_already_sent_is_not_sent_again_when_the_last_message_is_retried(self):
        from journal import telegram
        _long_program(self.cfg)
        real = self.bot.__call__
        calls = {"n": 0}

        def flaky(cfg, method, payload):
            if method == "sendMessage":
                calls["n"] += 1
                if calls["n"] == 2:  # the last message of the phase, once
                    raise telegram.TelegramError("boom")
            return real(cfg, method, payload)
        with mock.patch.object(telegram, "_call", flaky):
            self.start("longone")
            rid = self.the_run()["id"]
            self.assertFalse(self.prompt(f"{rid}-s01-p1").get("message_id"))
            programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.sends()), 2)  # chunk, last message: the chunk was not sent a second time
        p = self.prompt(f"{rid}-s01-p1")
        self.assertTrue(p["message_id"])
        self.assertEqual(len(p["previous_message_ids"]), 1)

    def test_a_retried_summary_keeps_the_next_session_line_and_the_document(self):
        from journal import telegram
        real = self.bot.__call__
        down = {"on": True}

        def flaky(cfg, method, payload):
            if method == "sendMessage" and (payload.get("reply_markup") or {}).get("remove_keyboard") and down["on"]:
                raise telegram.TelegramError("boom")
            return real(cfg, method, payload)
        self.start()
        with mock.patch.object(telegram, "_call", flaky):
            for p in range(1, 7):
                self.answer(f"answer {p}")
                self.settle()
            self.assertEqual(self.bot.summaries(), [])
            down["on"] = False
            programs.step(self.cfg, self.now, self.log)
        text = self.bot.summaries()[-1]["text"]
        self.assertIn("Next: /start fix-life continues with session 2", text)

    def test_a_bad_annotate_flag_rejects_the_file(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "maybe.md").write_text("---\nkind: situational\nid: maybe\ntitle: Maybe\nannotate: maybe\n---\n"
                                    "## Phase 1: A\n**1.1** Q\n\n## Summary\n- {1.1}\n", encoding="utf-8")
        lib = library.load(self.cfg)
        self.assertTrue(any("annotate" in p for p in lib.problems))
        self.assertNotIn("maybe", [p.id for p in lib.programs.values()])

    def test_an_answer_missed_before_a_resend_is_still_swept_into_the_phase(self):
        self.start()
        rid = self.the_run()["id"]
        missed = self.now + timedelta(minutes=5)  # the hook never saw it (busy path)
        self.now += timedelta(minutes=10)
        self.start()  # /start fix-life: the open phase is sent again, its sent_at moves
        msg = {"message_id": "7001", "quote": "", "text": "an answer the hook missed", "source": "text",
               "audio_path": None}
        info = sweep.reconcile(self.cfg, msg, missed)
        self.assertEqual((info["prompt"]["id"], info["rule"]), (f"{rid}-s01-p1", "next-message"))

    def test_a_session_whose_completion_died_is_finished_by_the_tick(self):
        self.start()
        for p in range(1, 6):
            self.answer(f"answer {p}")
            self.settle()
        self.answer("answer 6")
        with mock.patch.object(entries, "write_summary", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.settle()
        self.assertEqual(self.the_run()["sessions"]["1"]["status"], "in_progress")
        self.assertEqual(self.bot.summaries(), [])
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.the_run()["sessions"]["1"]["status"], "completed")
        self.assertEqual(len(self.bot.summaries()), 1)
        self.assertIn("## Summary", self.entry(1).read_text(encoding="utf-8"))

    def test_a_session_entry_that_capture_moved_off_a_file_of_his_is_followed(self):
        self.start()
        planned = entries.entry_path(self.cfg.root, self.the_run()["sessions"]["1"]["entry"])
        planned.write_text("# my own file, written before the session's first answer\n", encoding="utf-8")
        for p in range(1, 7):
            self.answer(f"answer {p}")
            self.settle()
        self.assertEqual(planned.read_text(encoding="utf-8"), "# my own file, written before the session's first answer\n")
        moved = self.entry(1)
        self.assertNotEqual(moved, planned)
        body = moved.read_text(encoding="utf-8")
        self.assertEqual(sorted(entries.phase_answers(body)), [1, 2, 3, 4, 5, 6])
        self.assertIn("## Summary", body)
        self.assertEqual(self.the_run()["sessions"]["1"]["status"], "completed")

    def test_a_queued_skip_or_back_does_not_move_a_paused_run(self):
        self.start()
        programs.pause(self.cfg, None, self.now, self.log)
        with self.assertRaises(programs.ProgramError):
            programs.back(self.cfg, None, self.now, self.log)
        with self.assertRaises(programs.ProgramError):
            programs.skip(self.cfg, None, self.now, self.log)
        run = self.the_run()
        self.assertEqual(run["status"], "paused")
        self.assertNotIn("skipped", run["sessions"]["1"]["phases"]["1"])
        self.assertEqual(self.where(), (1, 1))


# ── the second round of the independent review ───────────────────────────────

class ReviewThreeTest(Base):
    def _flaky_summaries(self, down):
        from journal import telegram
        real = self.bot.__call__

        def flaky(cfg, method, payload):
            if method == "sendMessage" and (payload.get("reply_markup") or {}).get("remove_keyboard") and down["on"]:
                raise telegram.TelegramError("boom")
            return real(cfg, method, payload)
        p = mock.patch.object(telegram, "_call", flaky)
        p.start()
        self.addCleanup(p.stop)

    def _finish_session_ten(self):
        self.start(session=10)
        for i in range(5):
            self.answer(f"my answer {i}")
            programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next")

    def test_an_audio_answer_not_yet_transcribed_does_not_advance_the_phase(self):
        self.cfg.audio["command"] = ["transcribe"]  # a backend is configured: the file waits for its transcript
        self.start()
        rid = self.the_run()["id"]
        self.now += timedelta(minutes=1)
        capture.capture_to(self.cfg, f"{rid}-s01-p1", "next-message", "_Transcribing._", self.now, "audio", "55",
                           "Journal/x.ogg", None, transcript="pending")
        self.assertFalse(self.settle())
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, f"{rid}-s01-p1")["captures"][0]["transcript"] = "done"
        self.assertTrue(self.settle())

    def test_a_retried_summary_does_not_double_the_document(self):
        down = {"on": True}
        self._flaky_summaries(down)
        self._finish_session_ten()
        self.assertEqual(self.bot.summaries(), [])
        down["on"] = False
        programs.step(self.cfg, self.now, self.log)
        sent = self.bot.summaries()
        self.assertEqual(len(sent), 1)
        title = self.prog().session(10).document_title
        self.assertEqual(sent[0]["text"].count(title), 1)  # the document once, not inline and again as its block

    def test_a_completed_runs_summary_is_retried(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "onephase.md").write_text("---\nkind: situational\nid: onephase\ntitle: One phase\n---\n"
                                       "## Phase 1: A\n**1.1** Q\n\n## Summary\n- {1.1}\n", encoding="utf-8")
        down = {"on": True}
        self._flaky_summaries(down)
        self.start("onephase")
        self.answer("words")
        rid = self.the_run()["id"]
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.assertEqual(programs.find_run(self.cfg, rid)["status"], "completed")
        self.assertEqual(self.bot.summaries(), [])
        down["on"] = False
        programs.step(self.cfg, self.now, self.log)  # the run is complete; its completion message is still owed
        self.assertEqual(len(self.bot.summaries()), 1)

    def test_a_summary_that_cannot_be_delivered_alerts_once(self):
        self._flaky_summaries({"on": True})
        self._finish_session_ten()
        programs.step(self.cfg, self.now, self.log)  # attempt 2
        with self.assertRaises(tick.Alert):
            programs.step(self.cfg, self.now, self.log)  # attempt 3
        programs.step(self.cfg, self.now, self.log)  # given up: quiet

    def test_the_insights_document_he_wrote_in_is_not_replaced(self):
        self.start()
        self.answer("words of mine")
        rid = self.the_run()["id"]
        path = programs.insights(self.cfg, rid, self.now, self.log)
        again = programs.insights(self.cfg, rid, self.now, self.log)
        self.assertEqual(again, path)  # untouched since the engine wrote it: made again in place
        path.write_text(path.read_text(encoding="utf-8") + "\nMy own note on this.\n", encoding="utf-8")
        newer = programs.insights(self.cfg, rid, self.now, self.log)
        self.assertNotEqual(newer, path)
        self.assertIn("My own note on this.", path.read_text(encoding="utf-8"))
        self.assertTrue(newer.name.endswith("-insights-2.md"))

    def test_start_on_an_active_run_sends_the_phase_again_instead_of_advancing(self):
        self.start()
        self.answer("an answer before the settle")
        self.start()  # /start fix-life one minute later
        self.assertEqual(self.where(), (1, 1))
        self.assertEqual(len(self.bot.phase_sends()), 2)

    def test_the_sweep_knows_when_a_phase_was_open_across_a_break(self):
        self.start()
        rid = self.the_run()["id"]
        t0 = self.now
        self.now = t0 + timedelta(minutes=6)
        programs.pause(self.cfg, None, self.now, self.log)
        self.now = t0 + timedelta(hours=1)
        self.start()  # /start fix-life: the phase is sent again and open again
        msg = {"quote": "", "source": "text", "audio_path": None}
        before = sweep.reconcile(self.cfg, {**msg, "message_id": "1", "text": "an answer the hook missed"},
                                 t0 + timedelta(minutes=5))
        self.assertEqual((before["prompt"]["id"], before["rule"]), (f"{rid}-s01-p1", "next-message"))
        during = sweep.reconcile(self.cfg, {**msg, "message_id": "2", "text": "chat during the break"},
                                 t0 + timedelta(minutes=30))
        self.assertIsNone(during)
        after = sweep.reconcile(self.cfg, {**msg, "message_id": "3", "text": "back again"},
                                self.now + timedelta(minutes=1))
        self.assertEqual(after["prompt"]["id"], f"{rid}-s01-p1")

    def test_a_jump_closes_the_old_phase_even_if_the_new_send_fails(self):
        from journal import telegram
        self.start()
        rid = self.the_run()["id"]

        def down(cfg, method, payload):
            raise telegram.TelegramError("boom")
        with mock.patch.object(telegram, "_call", down):
            self.start(session=3)
        self.assertFalse(self.prompt(f"{rid}-s01-p1")["phase_open"])
        self.assertIsNone(self.answer("chat while the jump's phase is not delivered"))


class ReviewFourTest(Base):
    def test_without_a_transcriber_a_kept_audio_answer_settles_on_time(self):
        self.start()
        rid = self.the_run()["id"]
        self.now += timedelta(minutes=1)
        capture.capture_to(self.cfg, f"{rid}-s01-p1", "next-message", "(not transcribed)", self.now, "audio", "55",
                           "Journal/x.ogg", None, transcript="pending")  # (the hook marks it pending when kept)
        self.assertTrue(self.settle())

    def test_after_a_back_the_old_answer_does_not_settle_the_reopened_phase(self):
        self.start()
        self.answer("first answer")
        rid = self.the_run()["id"]
        programs.advance(self.cfg, rid, self.now, self.log, reason="next")  # /next: command key, not the capture's
        self.now += timedelta(minutes=10)
        programs.back(self.cfg, None, self.now, self.log)
        self.assertEqual(self.where(), (1, 1))
        self.assertFalse(self.settle(timedelta(minutes=10)))
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.where(), (1, 1))
        self.answer("a revised answer")
        self.assertTrue(self.settle())
        self.assertEqual(self.where(), (1, 2))

    def test_a_completion_that_died_before_its_message_is_sent_by_the_tick(self):
        self.start(session=10)
        with mock.patch.object(programs, "_send_summary", side_effect=RuntimeError("died")):
            with self.assertRaises(RuntimeError):
                for i in range(5):
                    self.answer(f"my answer {i}")
                    programs.advance(self.cfg, self.the_run()["id"], self.now, self.log, reason="next")
        self.assertEqual(self.bot.summaries(), [])
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.summaries()), 1)


class ReviewFiveTest(Base):
    def test_a_generated_insights_file_without_a_noted_hash_is_not_replaced(self):
        self.start()
        self.answer("words of mine")
        rid = self.the_run()["id"]
        path = programs.insights(self.cfg, rid, self.now, self.log)
        run = self.the_run(rid)
        run.pop("insights_sha")  # the engine died between writing the file and noting it
        programs._save(self.cfg, run)
        path.write_text(path.read_text(encoding="utf-8") + "\nMine.\n", encoding="utf-8")
        again = programs.insights(self.cfg, rid, self.now, self.log)
        self.assertNotEqual(again, path)
        self.assertIn("Mine.", path.read_text(encoding="utf-8"))

    def test_a_colliding_run_id_is_not_reused(self):
        with mock.patch.object(programs.secrets, "token_hex", side_effect=["aaaaaa", "aaaaaa", "bbbbbb"]):
            first = self.start()["id"]
            programs.abandon(self.cfg, None, self.now, self.log)
            second = self.start()["id"]
        self.assertNotEqual(first, second)
        self.assertEqual(programs.find_run(self.cfg, first)["status"], "abandoned")

    def test_the_engines_note_about_a_recording_is_not_put_in_a_sentence(self):
        from journal import audio
        self.assertIsNone(programs._short(audio.NOT_TRANSCRIBED))
        self.assertIsNone(programs._short(audio.NO_TRANSCRIPT))
        self.assertEqual(programs._short("a few words"), "a few words")

    def test_a_program_named_like_a_practices_numbered_entry_is_ignored(self):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "morning-2.md").write_text("---\nkind: situational\nid: morning-2\ntitle: X\n---\n"
                                        "## Phase 1: A\n**1.1** Q\n\n## Summary\n- {1.1}\n", encoding="utf-8")
        lib = library.load(self.cfg)
        self.assertNotIn("morning-2", lib.programs)
        self.assertTrue(any("morning-2" in p and "reads as entry 2" in p for p in lib.problems))

    def test_a_long_blockquote_is_cut_with_its_tags_closed(self):
        prog = self.prog()
        parts = [(None, "\n".join(f"> a quoted line number {i} that goes on a bit" for i in range(300)))]
        for m in compose.phase_messages(prog, 1, 2, parts):
            self.assertLessEqual(len(m), compose.TELEGRAM_LIMIT)
            self.assertEqual(m.count("<blockquote>"), m.count("</blockquote>"))
            self.assertEqual(m.count("<b>"), m.count("</b>"))


class ReviewSixTest(Base):
    def test_a_completed_session_with_every_phase_skipped_is_not_called_incomplete(self):
        self.start(session=10)
        for _ in range(5):
            programs.skip(self.cfg, None, self.now, self.log)
        self.assertEqual(self.the_run()["sessions"]["10"]["status"], "completed")
        path = programs.insights(self.cfg, self.the_run()["id"], self.now, self.log)
        body = path.read_text(encoding="utf-8")
        self.assertNotIn("_(not completed)_", body)
        self.assertIn(programs.SKIPPED, body)
