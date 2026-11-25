"""The one-session programs shipped beside conversation prep (ONE-SESSION-PROGRAMS, issue #1): a thought record,
setting a boundary, working through a conflict. Each is a one-file definition (``kind: situational``, the spelling
the format reads today; PROGRAM-KINDS keeps it for good), so each one loads with no problem, starts by its
``/start`` word, never holds the one-program slot (it works while fix-life is unfinished), goes phase by phase on
``/next`` and ends with a summary filled from his own words. Telegram is a list of calls; the model is unreachable."""
import re
from datetime import timedelta

from test_programs import Base

from journal import library, programdefs, programs

NEW = {"thought-record": "thought", "boundary-setting": "boundary", "conflict-resolution": "conflict"}


class DefinitionsTest(Base):
    def test_each_loads_as_a_one_off_with_no_problem(self):
        problems: list[str] = []
        shipped = library.builtin_programs(problems)
        self.assertEqual(problems, [])
        for pid, alias in NEW.items():
            with self.subTest(pid):
                prog = shipped[pid]
                self.assertTrue(prog.situational and prog.builtin)
                self.assertEqual((prog.advance, prog.expires_hours, prog.group, prog.aliases),
                                 ("next", 24.0, "situational", (alias,)))
                self.assertEqual(len(prog.sessions), 1)
                self.assertLessEqual(len(prog.title), programdefs.TITLE_LIMIT)
                self.assertEqual(library.load(self.cfg).program(alias).id, pid)  # the /start word

    def test_every_summary_slot_names_a_question_of_the_file_one_per_phase(self):
        """He answers a phase in one message, which fills its first slot whole: a second slot of the same phase would
        only say "(in the answer above)" (PRACTICES.md §4.7)."""
        for pid in NEW:
            with self.subTest(pid):
                session = library.builtin_programs()[pid].sessions[0]
                ids = {q.id for ph in session.phases for q in ph.questions}
                slots = re.findall(r"\{(\d+\.\d+)\}", session.summary)
                self.assertTrue(slots)
                self.assertLessEqual(set(slots), ids)
                phases = [s.split(".")[0] for s in slots]
                self.assertEqual(len(phases), len(set(phases)))

    def test_the_lineage_is_named_and_never_sent(self):
        """``framework:`` carries the source; a one-file definition's front matter is never sent to him."""
        for pid in NEW:
            with self.subTest(pid):
                session = library.builtin_programs()[pid].sessions[0]
                self.assertTrue(session.framework)
                self.assertTrue(session.role and session.mission)  # the companion's stance for the session


class RunTest(Base):
    def run_through(self, pid):
        """Start ``pid``, answer every phase and /next past it; the run as it ends."""
        self.now += timedelta(minutes=5)  # (started after the open fix-life phase, not in the same instant)
        run = self.start(pid)
        session = self.prog(pid).sessions[0]
        for ph in session.phases:
            self.assertEqual(self.where(), (1, ph.number))
            self.answer(f"my answer to phase {ph.number} of {pid}")
            self.now += timedelta(minutes=1)
            programs.advance(self.cfg, run["id"], self.now, self.log, reason="next")
        return programs.find_run(self.cfg, run["id"])

    def test_each_runs_beside_an_unfinished_fix_life_and_ends_with_his_words(self):
        fix = self.start()  # fix-life mid-session holds the slot; the one-offs never ask for it
        for pid in NEW:
            with self.subTest(pid):
                run = self.run_through(pid)
                self.assertEqual(run["status"], "completed")
                text = programs.entry_path(self.cfg, run, 1).read_text(encoding="utf-8")
                summary = text.split("## Summary", 1)[1].split("\n## ", 1)[0]
                for n in (1, 2, 3, 4):  # every summary takes one slot from each phase, filled with his words
                    self.assertIn(f"my answer to phase {n} of {pid}", summary)
                self.assertNotIn("_(", summary)  # nothing skipped, nothing "(in the answer above)"
        self.assertEqual(programs.find_run(self.cfg, fix["id"])["status"], "active")
