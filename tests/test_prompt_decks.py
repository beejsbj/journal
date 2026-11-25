"""The prompt decks of #58 (PROMPT-DECKS): three practices (Homework for Life, a weekly check-in, finitude) and four
one-file programs (five questions, the 3-2-1 shadow process, values, a mind sweep). None is scheduled, so nothing new
arrives in his chat by itself: the practices come only when asked (``/start <id>``, a face), the programs only when
started. No deck is a ritual: none gets a command of its own, and none is an entry id recognised by name alone.
Telegram is a list of calls; the model is unreachable."""
import re
from datetime import timedelta

from test_programs import Base

from journal import commands, entries, library, programs

PRACTICES = ("storyworthy", "check-in", "finitude")
PROGRAMS = {"five-questions": ("five",), "three-two-one": ("shadow",), "values": (), "mind-sweep": ("sweep",)}
RITUALS = ("morning", "evening", "midweek", "weekly")


class DecksTest(Base):
    def test_the_practices_load_unscheduled_in_their_group(self):
        lib = library.load(self.cfg)
        self.assertEqual(lib.problems, [])
        for pid in PRACTICES:
            with self.subTest(pid):
                p = lib.get(pid)
                self.assertTrue(p.builtin and p.enabled)
                self.assertEqual((p.kind, p.schedule, p.group), ("practice", (), "prompt decks"))
                self.assertTrue(p.items() and p.companion)

    def test_the_programs_load_as_one_offs_in_their_group(self):
        problems: list[str] = []
        shipped = library.builtin_programs(problems)
        self.assertEqual(problems, [])
        for pid, aliases in PROGRAMS.items():
            with self.subTest(pid):
                prog = shipped[pid]
                self.assertTrue(prog.situational and prog.builtin)
                self.assertEqual((prog.advance, prog.group, prog.aliases), ("next", "prompt decks", aliases))
                session = prog.sessions[0]
                self.assertTrue(session.framework and session.role and session.mission)
                ids = {q.id for ph in session.phases for q in ph.questions}
                slots = re.findall(r"\{(\d+\.\d+)\}", session.summary)
                self.assertTrue(slots)
                self.assertLessEqual(set(slots), ids)
                phases = [s.split(".")[0] for s in slots]  # one slot per phase: a second only says "(in the answer above)"
                self.assertEqual(sorted(phases, key=int), [str(ph.number) for ph in session.phases])
                for word in (pid, *aliases):
                    self.assertEqual(commands.start(self.cfg, word).program.id, pid)   # its /start word

    def test_a_practice_deck_is_sent_by_start(self):
        for pid in PRACTICES:
            with self.subTest(pid):
                self.assertEqual(commands.start(self.cfg, pid).practice.id, pid)


class NothingUninvitedTest(Base):
    def test_only_the_rituals_are_scheduled(self):
        self.assertEqual(tuple(p.id for p in library.load(self.cfg).scheduled()), RITUALS)

    def test_no_deck_is_a_ritual_a_command_or_a_name_alone(self):
        self.assertEqual(library.builtin_ids(), RITUALS)
        names = {s.name for s in commands.specs()}
        self.assertLessEqual(set(RITUALS), names)
        for pid in (*PRACTICES, *PROGRAMS):
            with self.subTest(pid):
                self.assertNotIn(pid, names)
                self.assertNotIn(pid, entries.known_ids())


class RunTest(Base):
    def test_each_program_runs_to_a_summary_of_his_words(self):
        for pid in PROGRAMS:
            with self.subTest(pid):
                self.now += timedelta(minutes=5)
                run = self.start(pid)
                for ph in self.prog(pid).sessions[0].phases:
                    self.assertEqual(self.where(), (1, ph.number))
                    self.answer(f"my answer to phase {ph.number} of {pid}")
                    self.now += timedelta(minutes=1)
                    programs.advance(self.cfg, run["id"], self.now, self.log, reason="next")
                run = programs.find_run(self.cfg, run["id"])
                self.assertEqual(run["status"], "completed")
                text = programs.entry_path(self.cfg, run, 1).read_text(encoding="utf-8")
                summary = text.split("## Summary", 1)[1].split("\n## ", 1)[0]
                for ph in self.prog(pid).sessions[0].phases:  # every phase's words, nothing skipped or "above"
                    self.assertIn(f"my answer to phase {ph.number} of {pid}", summary)
                self.assertNotIn("_(", summary)
