"""``guard.escalate_to`` names who looks after the setup; nothing carries a message to them, so no wording may promise
that they will be asked or will look into anything (#42, finding 4). The names come from config."""
import re
import tempfile
import unittest
from pathlib import Path

from helpers import make_cfg

from journal import commands, guard
from journal import hermes as plugin

# what would promise an escalation: someone being asked, told, or looking into it
PROMISES = re.compile(r"\b(ask|asks|asked|asking|look into|looks into|will|escalat\w*|reach|contact|notify)\b", re.I)


class EscalateWordingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.guard = {**self.cfg.guard, "escalate_to": "Max"}

    def tearDown(self):
        self.tmp.cleanup()

    def texts(self) -> dict[str, str]:
        tokens = plugin.skill_tokens(self.cfg.names, self.cfg)
        skill = plugin.render_skill(Path(self.tmp.name) / "skill", tokens).read_text(encoding="utf-8")
        broken = next(l for l in skill.splitlines() if "If something looks broken" in l)
        changes = next(l for l in skill.splitlines() if "Changing journal times or questions" in l)
        return {"guard": guard.refusal(self.cfg),
                "failed": commands._failed(self.cfg, "start the evening journal", OSError("no fork")),
                "skill: broken": broken.split("Never claim")[0], "skill: changes": changes,
                "token escalate": tokens["escalate"], "token ask_for_changes": tokens["ask_for_changes"]}

    def test_the_person_is_named_and_nothing_promises_an_escalation(self):
        for where, text in self.texts().items():
            with self.subTest(where):
                self.assertIn("Max", text)
                self.assertIn("looks after the setup", text)
                self.assertIsNone(PROMISES.search(text), text)

    def test_with_nobody_named_the_wording_stands_without_them(self):
        self.cfg.guard = {**self.cfg.guard, "escalate_to": ""}
        texts = self.texts()
        for where, text in texts.items():
            with self.subTest(where):
                self.assertNotIn("Max", text)
                self.assertNotIn("looks after the setup", text)
                self.assertIsNone(PROMISES.search(text), text)
        self.assertIn("Tell Theo what you found.", texts["guard"])
        self.assertIn("`journal doctor` can say why.", texts["failed"])


if __name__ == "__main__":
    unittest.main()
