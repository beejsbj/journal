"""HERMES-ONESHOT-ENV (DECISIONS.md): the engine's one-shots are known in the gateway, where their environment never
arrives.

A Hermes whose gateway runs every one-shot keeps the client's environment per session, filtered to its own allowlist,
and never sets it in the gateway's ``os.environ`` (``agent/session_client_env.py``). So ``JOURNAL_MODEL_STEP`` and
``JOURNAL_SEARCH_UPTO`` did not reach the hook or the tool there: every model step got her notebook block, and a
reflection's ``journal_search`` had no date bound. The run's query now carries both (``agent.step_mark``), and the
hook treats any finite one-shot, and any turn it cannot read, as a step. Here nothing is in ``os.environ``: what the
gateway would see.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import CHAT, make_cfg, real_ask
import test_search

from journal import agent, notebook, search
from journal.hermes import hooks, runtime, steps
from journal.hermes import tools as hermes_tools


def no_step_env():
    env = {k: v for k, v in os.environ.items() if k not in (agent.MODEL_STEP_ENV, search.UPTO_ENV)}
    return mock.patch.dict(os.environ, env, clear=True)


class MarkTest(unittest.TestCase):
    def test_the_mark(self):
        self.assertEqual(agent.step_mark(), "[journal model step]")
        self.assertEqual(agent.step_mark({search.UPTO_ENV: "2026-10-14"}), "[journal model step; search up to 2026-10-14]")
        self.assertEqual(agent.step_mark({search.UPTO_ENV: "soon"}), "[journal model step]")  # (only a date)
        read = agent.read_step_mark
        self.assertEqual(read("[journal model step]\n\nWrite the annotations."), (True, None))
        self.assertEqual(read("[journal model step; search up to 2026-10-14]\n\nq"), (True, "2026-10-14"))
        self.assertEqual(read("A picture: a cat.\n  [journal model step]  \nq"), (True, None))  # (any whole line)
        self.assertEqual(read([{"type": "text", "text": "[journal model step]\nq"}, {"type": "image_url"}]),
                         (True, None))  # (a message with a picture: a list of parts)
        for text in ("he wrote [journal model step] in a sentence", "[journal model step; search up to soon]",
                     "", None, 5, "hello"):
            with self.subTest(text):
                self.assertEqual(read(text), (False, None))

    def test_every_run_writes_it_first_in_its_query(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp))
            cfg.agent["command"] = ["echo", "{query_file}"]
            seen = []

            def run(argv, **kw):
                seen.append(Path(argv[1]).read_text())
                return mock.Mock(returncode=0, stdout="an answer", stderr="")
            with mock.patch("journal.agent.subprocess.run", run):
                real_ask(cfg, "Describe the day.", 10, "none")
                real_ask(cfg, "Write the reflection.", 10, "journal_read", env={search.UPTO_ENV: "2026-10-14"})
                real_ask(cfg, "Write it plain.", 10, "none", env=None)
        self.assertEqual(seen, ["[journal model step]\n\nDescribe the day.",
                                "[journal model step; search up to 2026-10-14]\n\nWrite the reflection.",
                                "[journal model step]\n\nWrite it plain."])


class HookTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        notebook.add(self.cfg, "a thread from chat")
        self.patches = [no_step_env(), mock.patch.object(runtime, "load_config", return_value=self.cfg),
                        mock.patch.object(steps, "finite_turn", return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def llm(self, message, platform="cli", turn="t1", session="s1"):
        with mock.patch.object(runtime, "session_value", side_effect={"CHAT_ID": CHAT}.get):
            return hooks.on_llm_call(user_message=message, platform=platform, session_id=session, turn_id=turn)

    def test_a_marked_query_gets_no_context_with_no_environment(self):
        with mock.patch("journal.turncontext.build", side_effect=AssertionError("built a turn context")):
            self.assertIsNone(self.llm(agent.step_mark() + "\n\nThe outage probe: say ok."))
            self.assertIsNone(self.llm([{"type": "text", "text": agent.step_mark() + "\nDescribe it."}], turn="t2"))

    def test_any_finite_turn_gets_none_either(self):
        """A one-shot whose mark did not arrive, or anyone's ``hermes chat -q``: Hermes says it is finite."""
        with mock.patch.object(steps, "finite_turn", return_value=True), \
                mock.patch("journal.turncontext.build", side_effect=AssertionError("built a turn context")):
            self.assertIsNone(self.llm("Write the annotations."))

    def test_a_turn_it_cannot_read_gets_none(self):
        with mock.patch("journal.turncontext.build", side_effect=AssertionError("built a turn context")):
            for message in (None, 5, {"text": "?"}):
                with self.subTest(message):
                    self.assertIsNone(self.llm(message))

    def test_her_chat_keeps_its_notebook(self):
        self.assertIn("a thread from chat", self.llm("hello", platform="telegram")["context"])
        self.assertIn("a thread from chat", self.llm("hello from the terminal", platform="cli", turn="t2")["context"])
        self.assertIn("a thread from chat", self.llm([{"type": "text", "text": "a picture of mine"}], turn="t3")["context"])

    def test_a_marked_line_of_his_is_the_safe_side(self):
        """A message of his that copies the mark exactly is taken for a step: no context that one turn."""
        self.assertIsNone(self.llm("[journal model step]", platform="telegram"))

    def test_the_old_environment_still_counts(self):
        with mock.patch.dict(os.environ, {agent.MODEL_STEP_ENV: "1"}), \
                mock.patch("journal.turncontext.build", side_effect=AssertionError("built a turn context")):
            self.assertIsNone(self.llm("Write the annotations."))



class FiniteTest(unittest.TestCase):
    def test_no_hermes_means_no_finite_fact(self):
        self.assertIsNone(steps.finite_turn())  # (Hermes is not importable here: no fact, not False)


class SearchBoundTest(unittest.TestCase):
    """The reflection's search in the gateway: the bound comes from its query, through the hook, to the tool."""

    entry = test_search.SearchTest.entry  # (its fixture: three entries of his, 20 Sep to 14 Oct)

    def setUp(self):
        test_search.SearchTest.setUp(self)
        self.patches = [no_step_env(), mock.patch.object(runtime, "load_config", return_value=self.cfg),
                        mock.patch.object(steps, "finite_turn", return_value=True)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def search(self, session, turn, **params):
        return json.loads(hermes_tools.journal_search({"query": "harbour", **params}, session_id=session, turn_id=turn,
                                                      task_id="x"))

    def test_the_bound_reaches_the_tool_by_the_turns_ids(self):
        self.assertIsNone(hooks.on_llm_call(user_message=agent.step_mark({search.UPTO_ENV: "2026-10-02"}) + "\n\nq",
                                            platform="cli", session_id="s-late", turn_id="t-late"))
        for session, turn in (("s-late", "t-late"), ("s-late", ""), ("", "t-late")):
            with self.subTest(session=session, turn=turn):
                out = self.search(session, turn, date_to="2026-12-31")
                self.assertEqual([r["entry"] for r in out["results"]], ["2026-10-02-morning", "2026-09-20-evening"])
                self.assertEqual(out["through"], "2026-10-02")

    def test_a_marked_run_with_no_bound_searches_everything(self):
        hooks.on_llm_call(user_message=agent.step_mark() + "\n\nq", platform="cli", session_id="s2", turn_id="t2")
        out = self.search("s2", "t2")
        self.assertEqual(len(out["results"]), 3)
        self.assertNotIn("through", out)

    def test_a_one_shot_the_hook_never_saw_does_not_search_unbounded(self):
        out = self.search("s-unseen", "t-unseen")
        self.assertEqual(out["success"], False)
        self.assertIn("search bound", out["error"])

    def test_her_chat_searches_as_before(self):
        with mock.patch.object(steps, "finite_turn", return_value=False):
            out = self.search("s-chat", "t-chat")
        self.assertEqual(len(out["results"]), 3)

    def test_the_old_environment_still_bounds_it(self):
        with mock.patch.dict(os.environ, {search.UPTO_ENV: "2026-10-02"}), \
                mock.patch.object(steps, "finite_turn", return_value=None):
            out = self.search("s-old", "t-old")
        self.assertEqual(out["through"], "2026-10-02")

    def test_the_memory_is_bounded(self):
        for n in range(steps.KEEP + 10):
            steps.note(agent.step_mark() + "\nq", f"s{n}", f"t{n}")
        self.assertLessEqual(len(steps._bounds), steps.KEEP)
        self.assertEqual(steps.search_upto("s0", "t0"), (False, None))


if __name__ == "__main__":
    unittest.main()
