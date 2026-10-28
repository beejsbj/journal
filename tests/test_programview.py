"""The companion's surface for programs (WP6): the program block on a phase-answer turn, the budget rule it joins,
and ``journal_program`` (status and propose, both reads). The run is driven the way test_programs drives it: no
model, Telegram as a list of calls, the clock held."""
import dataclasses
import hashlib
import json
import re
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from helpers import CHAT, ROOT

from journal import audio, capture, entries, hookstate, inbound, notebook, programs, programview, today, turncontext
from journal.hermes import commands as hcommands
from journal import tools as jtools
from journal.hermes import hooks, runtime, tools
from test_programs import Base, T0

COMPANION_SAYS = "COMPANION-REPLY-MUST-NOT-APPEAR"


def tree(*roots: Path) -> dict[str, str]:
    """Every file under the roots, by path, with its checksum: what 'nothing was written' compares."""
    out = {}
    for root in roots:
        for p in sorted(Path(root).rglob("*")):
            if p.is_file():
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class Driven(Base):
    """A fix-life run with his first answer saved, and the program block queued as the dispatch hook queues it."""

    def turn(self, text="hello", platform="telegram"):
        return turncontext.Turn(platform=platform, session_id="s1", turn_id="t1", user_message=text, at=self.now)

    def ref(self, info):
        p = info["prompt"]
        return {"run": p["run"], "session": p["session"], "phase": p["phase"], "prompt": p["id"]}

    def answered(self, text="I keep saying yes to things I resent.", **kw):
        self.start()
        info = self.answer(text, **kw)
        self.assertIsNotNone(info)
        return info

    def queue(self, info, note="[Journal reply — program reply: x]\n\n"):
        hookstate.remember_program(self.cfg, note, self.ref(info))
        return note + "my words"


class BlockTest(Driven):
    def test_the_block_holds_the_stance_the_phase_and_his_words_only(self):
        info = self.answered()
        text = programview.block(self.cfg, self.ref(info), self.now)
        session = self.prog().session(1)
        self.assertTrue(text.startswith("## Journal: program session\nYou are Ada. For this session, work as "))
        self.assertIn(session.role, text)
        self.assertIn(f"Mission: {session.mission}", text)
        self.assertIn("keep your own voice", text)
        self.assertIn("Fix your entire life in 1 day, session 1/10", text)
        self.assertIn("phase 1/6 “Current Reality Excavation”", text)
        self.assertIn("I keep saying yes to things I resent.", text)
        self.assertIn(info["prompt"]["phase_text"][:60], text)  # the phase as it was sent
        self.assertLessEqual(len(text), programview.MAX_CHARS)

    def test_her_conversation_section_is_not_in_the_block(self):
        """The real conversation section (marked heading, her callout, placed before the annotations) sits between
        the phase blocks once phase 2 is answered; the block holds both phases and none of her text."""
        self.answered("first thought")
        entry = self.entry(1)
        entries.append_conversation(entry, f"%% journal her:abc %%\n> [!quote]- Ada · 10:00\n> {COMPANION_SAYS}\n\n")
        self.assertIn(COMPANION_SAYS, entry.read_text(encoding="utf-8"))
        self.settle()
        info = self.answer("the answer to phase two")
        text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertIn("first thought", text)
        self.assertIn("the answer to phase two", text)
        self.assertNotIn(COMPANION_SAYS, text)
        self.assertNotIn("Ada ·", text)

    def test_a_second_message_and_earlier_phases_are_there_too(self):
        self.answered("first thought")
        self.answer("a correction", after=timedelta(seconds=30))
        self.settle()
        self.assertEqual(self.where(), (1, 2))
        info = self.answer("the answer to phase two")
        text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertIn("first thought\n\na correction", text)
        self.assertIn("the answer to phase two", text)
        self.assertLess(text.index("first thought"), text.index("the answer to phase two"))
        self.assertIn("(this reply)", text)

    def test_the_cap_holds_with_long_answers_and_the_newest_reply_is_kept_first(self):
        self.answered("early " + "alpha " * 400)
        self.settle()
        info = self.answer("LATEST-START " + "omega " * 900)
        text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertLessEqual(len(text), programview.MAX_CHARS)
        self.assertIn("LATEST-START omega", text)  # cut at the end, never at the start
        self.assertIn("[…]", text)

    def test_a_hand_written_file_cannot_crowd_out_his_words(self):
        info = self.answered()
        huge = programview.STANCE_CHARS * 20
        session = self.prog().session(1)
        longer = dataclasses.replace(self.prog(), sessions=(dataclasses.replace(session, role="r" * huge,
                                                                                 mission="m" * huge),)
                                     + self.prog().sessions[1:])
        lib = mock.Mock()
        lib.program.return_value = longer
        with mock.patch.object(programview.library, "load", return_value=lib):
            text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertLessEqual(len(text), programview.MAX_CHARS)
        self.assertIn("I keep saying yes to things I resent.", text)

    def test_long_titles_cannot_crowd_out_his_words(self):
        """Titles show up on the "now" line and in the phase labels; a hand-written file with very long ones must
        leave the current reply in the block, whole or cut, never dropped by the final cap."""
        self.answered("early " + "alpha " * 100)
        self.settle()
        info = self.answer("LATEST-START " + "omega " * 400)
        prog = self.prog()
        long = "T" * 400
        sessions = (dataclasses.replace(prog.session(1), title=long, role="r" * 5000, mission="m" * 5000,
                                        phases=tuple(dataclasses.replace(ph, title=long)
                                                     for ph in prog.session(1).phases)),) + prog.sessions[1:]
        lib = mock.Mock()
        lib.program.return_value = dataclasses.replace(prog, title=long, sessions=sessions)
        with mock.patch.object(programview.library, "load", return_value=lib):
            text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertLessEqual(len(text), programview.MAX_CHARS)
        self.assertIn("LATEST-START omega", text)
        self.assertNotIn("T" * (programview.TITLE_CHARS + 1), text)

    def test_how_the_run_moves_on_is_on_the_now_line(self):
        self.start("conversation-prep")  # advance: next
        text = programview.block(self.cfg, self.ref(self.answer("with my sister")), self.now)
        line = next(l for l in text.splitlines() if l.startswith("- now:"))
        self.assertIn("moves on when he sends /next", line)
        self.assertNotIn("minute", line)

    def test_a_late_reply_to_an_earlier_phase_still_says_where_the_run_is(self):
        """He replies to phase 1's message while phase 3 is open: the now-line is the run's position, and says the
        reply answers the earlier phase, so she is not told he is at phase 1."""
        info1 = self.answered("first")
        self.settle()
        self.answer("second")
        self.settle()
        self.assertEqual(self.where(), (1, 3))
        p1 = info1["prompt"]
        self.now += timedelta(minutes=1)
        info = capture.capture(self.cfg, CHAT, str(p1["message_id"]), "late add to phase one", self.now, "text", "9999")
        self.assertEqual(info["prompt"]["phase"], 1)
        text = programview.block(self.cfg, self.ref(info), self.now)
        line = next(l for l in text.splitlines() if l.startswith("- now:"))
        self.assertIn("phase 3/6", line)
        self.assertIn("this reply answers phase 1", line)
        self.assertIn("late add to phase one", text)

    def test_a_reply_to_the_open_phase_has_no_earlier_note(self):
        text = programview.block(self.cfg, self.ref(self.answered()), self.now)
        line = next(l for l in text.splitlines() if l.startswith("- now:"))
        self.assertNotIn("this reply answers", line)

    def test_the_advance_clause_is_grammatical_for_every_pronoun_set(self):
        for pronouns, quiet, sends in (("he/him/his", "quiet from him,", "he sends /next"),
                                       ("she/her/her", "quiet from her,", "she sends /next"),
                                       ("they/them/their", "quiet from them,", "they send /next")):
            self.cfg.names = dataclasses.replace(self.cfg.names, pronouns=pronouns)
            fix = programview.advance_clause(self.cfg, self.prog("fix-life"), self.cfg.names)
            prep = programview.advance_clause(self.cfg, self.prog("conversation-prep"), self.cfg.names)
            self.assertIn(quiet, fix)
            self.assertIn(sends, prep)
            self.assertEqual(self.cfg.names.verb("send") in ("send", "sends"), True)

    def test_a_recording_note_is_not_his_words(self):
        info = self.answered(audio.TRANSCRIBING)
        text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertNotIn(audio.TRANSCRIBING, text)
        self.assertIn("nothing saved yet", text)

    def test_an_untranscribed_recording_beside_typed_words_leaves_only_the_words(self):
        """The phase's blocks are joined for the reader; the engine's note must be dropped block by block, not only
        when the whole joined answer is the note."""
        self.answered("my typed words")
        info = self.answer(audio.NOT_TRANSCRIBED, after=timedelta(seconds=20), source="audio")
        text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertIn("my typed words", text)
        for note in programview.NOT_WORDS:
            self.assertNotIn(note, text)

    def test_a_voice_note_with_a_caption_and_no_transcript_leaves_only_the_caption(self):
        self.start()
        info = self.answer(f"{audio.NO_TRANSCRIPT}\n\nmy caption", source="voice")
        text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertIn("my caption", text)
        for note in programview.NOT_WORDS:
            self.assertNotIn(note, text)

    def test_each_note_alone_or_joined_is_never_shown_as_his_words(self):
        """Every note, on its own, before a block of words, after one, and inside one; the filter is exercised
        directly, so removing it fails here."""
        for note in programview.NOT_WORDS:
            for blocks in ([note], ["real words", note], [note, "real words"], [f"{note}\n\nreal words"]):
                got = programview._words({1: blocks})
                text = "\n".join(got.values())
                self.assertNotIn(note, text, (note, blocks))
                if len(blocks) > 1 or blocks[0] != note:
                    self.assertEqual(text, "real words", (note, blocks))
                else:
                    self.assertEqual(got, {})

    def test_a_situational_run_has_no_session_line_and_a_default_stance(self):
        self.start("conversation-prep")
        info = self.answer("with my sister, about the house")
        text = programview.block(self.cfg, self.ref(info), self.now)
        self.assertIn("Conversation prep, phase 1/", text)
        self.assertNotIn("session 1/", text)
        self.assertIn("with my sister, about the house", text)
        self.assertIn("work as a careful guide", text)

    def test_a_missing_run_or_definition_is_no_block(self):
        info = self.answered()
        self.assertIsNone(programview.block(self.cfg, {**self.ref(info), "run": "no-such-run"}, self.now))
        self.assertIsNone(programview.block(self.cfg, {**self.ref(info), "phase": 99}, self.now))
        self.assertIsNone(programview.block(self.cfg, {}, self.now))


class BudgetTest(Driven):
    def test_notebook_plus_program_block_and_no_today_no_pack(self):
        info = self.answered()
        notebook.add(self.cfg, "ask how the call went")
        message = self.queue(info)
        stray = "PACK-OF-AN-OPENER"
        hookstate.remember_pack(self.cfg, "[Journal reply — x]", stray)  # a stray pack for another note
        out = turncontext.build(self.cfg, self.turn(message))
        self.assertIn("ask how the call went", out)
        self.assertIn("## Journal: program session", out)
        self.assertNotIn("## Journal: today", out)
        self.assertNotIn(stray, out)
        self.assertEqual(out.count("\n## Journal"), 1)  # two blocks: the notebook and the program block
        self.assertEqual(turncontext.BUDGET["blocks"], 2)

    def test_the_block_comes_once_and_the_next_thread_start_gets_today(self):
        info = self.answered()
        # starting the run stamped its prompt's send with the wall clock; the fixture's "now" is a fixed instant, and a
        # send stamped after it would open a new thread for the second build (this test failed after 10:00 UTC on the
        # day the fixture's instant was written): the prompt went out before his answer
        hookstate.stamp(self.cfg, last_send=(self.now - timedelta(hours=1)).timestamp())
        message = self.queue(info)
        first = turncontext.build(self.cfg, self.turn(message))
        self.assertIn("program session", first)
        again = turncontext.build(self.cfg, self.turn(message))  # the same message again: nothing queued any more
        self.assertFalse(again and "program session" in again)
        self.assertEqual(hookstate.summary(self.cfg)["pending_programs"], 0)
        # the thread start was spent by the program turn and no today block was shown: it waits for the next thread
        self.assertEqual(today.injections(self.cfg, self.now)["count"], 0)
        self.assertFalse(today.begin(self.cfg, self.now + timedelta(minutes=1)))
        self.assertTrue(today.begin(self.cfg, self.now + timedelta(minutes=1, hours=1)))

    def test_a_turn_without_a_queued_block_is_as_before(self):
        self.answered()
        out = turncontext.build(self.cfg, self.turn("just chatting"))
        self.assertFalse(out and "program session" in out)

    def test_not_for_a_cli_or_cron_turn(self):
        info = self.answered()
        message = self.queue(info)
        out = turncontext.build(self.cfg, self.turn(message, platform="cron"))
        self.assertFalse(out and "program session" in out)

    def test_a_failing_block_costs_only_the_block(self):
        info = self.answered()
        message = self.queue(info)
        notebook.add(self.cfg, "a thread to keep")
        with mock.patch.object(programview, "block", side_effect=RuntimeError("boom")):
            out = turncontext.build(self.cfg, self.turn(message))
        self.assertIn("a thread to keep", out)
        self.assertNotIn("program session", out)

    def test_a_pointer_expires(self):
        info = self.answered()
        hookstate.remember_program(self.cfg, "[note]", self.ref(info), now=1000.0)
        self.assertIsNone(hookstate.take_program(self.cfg, "[note] hi", now=1000.0 + hookstate.PACK_TTL + 1))


class HookTest(Driven):
    """The gateway hooks end to end: dispatch captures and queues, the LLM hook injects."""

    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(hooks.runtime, "load_config", lambda: self.cfg),
                  mock.patch.object(hcommands, "spawn", lambda cfg, *a: None)):
            p.start()
            self.addCleanup(p.stop)

    def event(self, text, message_id="88", reply_to=None):
        return SimpleNamespace(source=SimpleNamespace(platform="telegram", chat_id=CHAT), text=text,
                               message_id=message_id, reply_to_message_id=reply_to, message_type="TEXT",
                               media_urls=[])

    def test_a_typed_phase_answer_gets_the_block_on_its_turn(self):
        self.start()
        notebook.add(self.cfg, "ask how the call went")
        out = hooks.on_dispatch(self.event("what I wrote at dusk"))
        self.assertTrue(out["text"].startswith("[Journal reply — program reply:"))
        ctx = hooks.on_llm_call(user_message=out["text"], platform="telegram", session_id="s1", turn_id="t1")
        self.assertIn("## Journal: program session", ctx["context"])
        self.assertIn("what I wrote at dusk", ctx["context"])
        self.assertIn("ask how the call went", ctx["context"])
        self.assertNotIn("## Journal: today", ctx["context"])

    def test_a_voice_phase_answer_carries_its_transcript_into_the_block(self):
        self.start()
        voice = SimpleNamespace(source=SimpleNamespace(platform="telegram", chat_id=CHAT), text="",
                                message_id="90", reply_to_message_id=None, message_type="VOICE",
                                media_urls=[], media_types=[], raw_message=None)
        out = hooks.on_dispatch(voice)
        self.assertIsNotNone(out)
        message = out["text"] + '"words from the voice note"'
        ctx = hooks.on_llm_call(user_message=message, platform="telegram", session_id="s1", turn_id="t1")
        self.assertIn("words from the voice note", ctx["context"])  # the transcript was written before the turn
        self.assertIn("## Journal: program session", ctx["context"])

    def test_an_off_record_message_is_never_in_the_block(self):
        self.start()
        out = hooks.on_dispatch(self.event("/x the thing I will not keep"))
        self.assertIn("Off the record", out["text"])
        ctx = hooks.on_llm_call(user_message=out["text"], platform="telegram", session_id="s1", turn_id="t1")
        self.assertFalse(ctx and "program session" in (ctx.get("context") or ""))
        self.assertEqual(hookstate.summary(self.cfg)["pending_programs"], 0)

    def test_an_interrupt_reply_queues_no_block(self):
        inbound._remember_program(self.cfg, "[n]", {"run": "r", "session": 10, "phase": 3, "interrupt": 2, "id": "x"})
        self.assertEqual(hookstate.summary(self.cfg)["pending_programs"], 0)

    def test_a_plain_journal_reply_still_gets_its_pack_not_a_block(self):
        from helpers import add_prompt
        prompt = add_prompt(self.cfg, "morning", "2026-10-20", sent=T0, message_id="100")
        with mock.patch("journal.context.pack_for_turn", return_value="THE-PACK"):
            out = hooks.on_dispatch(self.event("slept badly", reply_to=prompt["message_id"]))
        ctx = hooks.on_llm_call(user_message=out["text"], platform="telegram", session_id="s1", turn_id="t1")
        self.assertIn("THE-PACK", ctx["context"])
        self.assertNotIn("program session", ctx["context"])


class ToolTest(Driven):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(runtime, "load_config", lambda: self.cfg)
        p.start()
        self.addCleanup(p.stop)

    def call(self, **params):
        return json.loads(tools.journal_program(params))

    def test_status_lists_the_library_and_the_runs(self):
        self.start()
        out = self.call(action="status")
        self.assertTrue(out["success"])
        self.assertIn("fix-life", {p["id"] for p in out["programs"]})
        self.assertEqual([r["program"] for r in out["runs"]], ["fix-life"])
        self.assertIn("session 1/10", out["text"])
        self.assertEqual(self.call(action="status")["runs"], out["runs"])

    def test_propose_returns_the_line_and_starts_nothing(self):
        before = tree(self.cfg.state_dir, self.cfg.root)
        out = self.call(action="propose", program="fix-life")
        self.assertEqual((out["success"], out["line"], out["program"]), (True, "/start fix-life", "fix-life"))
        self.assertIn("Nothing has been started", out["note"])
        prep = self.call(action="propose", program="PREP")  # an alias, case-insensitively
        self.assertEqual(prep["line"], "/start prep")
        self.assertEqual(tree(self.cfg.state_dir, self.cfg.root), before)
        self.assertEqual(programs.all_runs(self.cfg), [])

    def test_propose_never_mutates_a_run_in_progress(self):
        self.answered()
        before = tree(self.cfg.state_dir, self.cfg.root)
        out = self.call(action="propose", program="fix-life")
        self.assertIn("running", out)
        self.assertIn("picks it up", out["note"])
        self.assertEqual(self.call(action="propose")["success"], True)
        self.assertEqual(self.call(action="status")["success"], True)
        self.assertEqual(tree(self.cfg.state_dir, self.cfg.root), before)

    def test_propose_says_when_another_program_is_unfinished(self):
        """One program at a time (DECISIONS.md, ONE-PROGRAM): the line she would offer is refused while another
        program is unfinished, so the answer says so; a situational one is never refused."""
        from test_one_program import add_course
        add_course(self.cfg)
        self.start()
        out = self.call(action="propose", program="course")
        self.assertTrue(out["success"])
        self.assertIn("/abandon fix-life", out["refused"])
        self.assertIn("would be refused", out["note"])
        self.assertNotIn("refused", self.call(action="propose", program="prep"))
        self.assertNotIn("refused", self.call(action="propose", program="fix-life"))

    def test_propose_without_a_program_lists_what_can_be_started(self):
        out = self.call(action="propose")
        self.assertIn("/start fix-life", {o["line"] for o in out["options"]})

    def test_propose_refuses_what_is_not_a_startable_program(self):
        for word in ("nonesuch", "morning", "fix-life 3", "fix-life insights"):
            out = self.call(action="propose", program=word)
            self.assertFalse(out["success"], word)
        self.assertEqual(programs.all_runs(self.cfg), [])

    def test_propose_refuses_a_program_turned_off(self):
        d = self.cfg.practices_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "tidy.md").write_text("---\nkind: situational\nid: tidy\ntitle: Tidy\nenabled: false\n---\n"
                                   "## Phase 1: Now\n**1.1** What is out of place?\n", encoding="utf-8")
        out = self.call(action="propose", program="tidy")
        self.assertFalse(out["success"])
        self.assertIn("turned off", out["error"])

    def test_an_unknown_action_and_a_broken_config_do_not_raise(self):
        self.assertFalse(self.call(action="start")["success"])
        with mock.patch.object(runtime, "load_config", side_effect=RuntimeError("no config")):
            self.assertFalse(json.loads(tools.journal_program({"action": "status"}))["success"])

    def test_both_actions_are_reads_in_an_off_record_turn(self):
        hookstate.mark_off_turn(self.cfg, "s1", "t1")
        for action in ("status", "propose"):
            self.assertIsNone(jtools.refusal(self.cfg, "journal_program", {"action": action}, jtools.ToolTurn("s1", "t1")))
        # while a write of hers in the same turn is still refused
        self.assertIn("Off the record", jtools.refusal(self.cfg, "journal_add_note", {"note": "x"}, jtools.ToolTurn("s1", "t1")))
        with mock.patch.object(hooks.runtime, "load_config", lambda: self.cfg):
            self.assertIsNone(hooks.on_tool_call(tool_name="journal_program", args={"action": "status"},
                                                 session_id="s1", turn_id="t1"))


class SkillTest(Base):
    def render(self):
        import journal.hermes as plugin
        dest = Path(self.tmp.name) / "skill"
        return plugin.render_skill(dest, plugin.skill_tokens(self.cfg.names, self.cfg)).read_text(encoding="utf-8")

    def test_the_programs_section_names_the_programs_and_the_rules(self):
        text = self.render()
        self.assertNotIn("{{", text)
        self.assertIn("## Programs", text)
        for title in ("Fix your entire life in 1 day", "Conversation prep", "Post-session debrief"):
            self.assertIn(f"**{title}**", text)
        self.assertIn("`/start fix-life`", text)
        self.assertIn("`/start prep`", text)  # an alias, as he would type it
        # the rules she is told: stance not persona, never advance, questions as authored
        self.assertIn("You never rewrite, skip, reorder or advance a question", text)
        self.assertIn("not a claim about who you are", text)
        self.assertIn("never repeat the role text", text)
        self.assertIn("**`journal_program`** is read-only", text)

    def test_each_program_line_says_how_it_moves_on(self):
        """fix-life settles after programs.settle_minutes of quiet; prep and debrief declare advance: next and wait
        for /next. The skill is her only guide on a later turn, so it must not promise one for the other."""
        self.cfg.programs["settle_minutes"] = 7
        lines = {l.split("**")[1]: l for l in self.render().splitlines() if l.startswith("- **")}
        self.assertIn("7 minute(s) of quiet", lines["Fix your entire life in 1 day"])
        for title in ("Conversation prep", "Post-session debrief"):
            self.assertIn("moves on when he sends /next", lines[title])
            self.assertNotIn("minute", lines[title])
        self.assertNotIn("a couple of minutes", self.render())

    def test_the_skill_and_the_reply_note_agree_with_they(self):
        self.cfg.names = dataclasses.replace(self.cfg.names, pronouns="they/them/their")
        lines = {l.split("**")[1]: l for l in self.render().splitlines() if l.startswith("- **")}
        self.assertIn("quiet from them,", lines["Fix your entire life in 1 day"])
        self.assertIn("when they send /next", lines["Conversation prep"])
        self.assertNotIn("they sends", self.render())
        self.start("conversation-prep")
        info = self.answer("with my sister")
        self.assertIn("when they send /next", programs.reply_note(self.cfg, info))

    def test_a_program_of_his_own_is_listed_and_a_turned_off_one_is_not(self):
        d = self.cfg.practices_dir
        d.mkdir(parents=True, exist_ok=True)
        (d / "tidy.md").write_text("---\nkind: situational\nid: tidy\ntitle: Tidy the desk\n---\n"
                                   "## Phase 1: Now\n**1.1** What is out of place?\n", encoding="utf-8")
        (d / "gone.md").write_text("---\nkind: situational\nid: gone\ntitle: Gone for now\nenabled: false\n---\n"
                                   "## Phase 1: Now\n**1.1** What is out of place?\n", encoding="utf-8")
        text = self.render()
        self.assertIn("**Tidy the desk** (`/start tidy`)", text)
        self.assertNotIn("Gone for now", text)

    def test_without_a_usable_config_the_built_ins_are_listed(self):
        import journal.hermes as plugin
        self.assertIn("**Fix your entire life in 1 day**", plugin.programs_list(None))


class RegistrationTest(unittest.TestCase):
    def test_plugin_yaml_provides_the_registered_tools(self):
        manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text(encoding="utf-8"))
        self.assertIn("journal_program", manifest["provides_tools"])
        from journal import tools as jtools
        from journal.config import Names
        self.assertEqual(set(manifest["provides_tools"]), {s.name for s in jtools.specs(Names())})

    def test_the_schema_offers_status_and_propose_only(self):
        from journal import tools as jtools
        from journal.config import Names
        schema = jtools.program_schema(Names(user="Sam", companion="Ada"))
        self.assertEqual(schema["name"], "journal_program")
        self.assertEqual(schema["parameters"]["properties"]["action"]["enum"], ["status", "propose"])
        self.assertEqual(schema["parameters"]["required"], ["action"])


if __name__ == "__main__":
    unittest.main()
