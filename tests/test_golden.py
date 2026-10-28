"""The four built-in rituals must stay byte-identical to what the engine sent before practices became files.

``tests/golden/`` was captured from the ``questions.py`` constants (removed in WP13) and ``compose`` on the commit before WP1
(1412843): the numbered bank lines, the ``[id]`` bank the opener request sees, the id → question map that
``asked:`` is checked against, the Telegram HTML and archived plain text for a fixed date and opener, and the
plain (no-model) opener for four shapes of names. A change here is a change to what he receives, so it is
never made by accident: regenerate the goldens only on purpose, with the reason in the commit.

``PracticeFilesTest`` holds the practice files themselves to the same goldens and to the code they replace
in WP2 (opener shapes and limits, the times the rituals had as config defaults), so the engine can switch to reading them without
anything he receives changing."""
import json
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import helpers  # noqa: F401  (puts src/ on the path; keeps Telegram unreachable)

from journal import compose, library, prompts, validate
from journal.config import Names

GOLDEN = Path(__file__).resolve().parent / "golden"
KINDS = ("morning", "evening", "midweek", "weekly")
# The times the four rituals had as config defaults before practices were files (``config.DEFAULT_SCHEDULE``, removed
# in WP13): the packaged files must keep saying them.
TIMES = {
    "morning": {"time": "06:00"},
    "evening": {"time": "23:00"},
    "midweek": {"time": "12:00", "weekday": 2},  # Wednesday
    "weekly": {"time": "20:00", "weekday": 6},   # Sunday
}
FIXED = datetime(2026, 10, 14, 23, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
OPENER = "Tea & <toast>? **Bold** and _soft_ — a line.\nSecond line."
# The four shapes the greeting takes: default names, a user and an emoji, a user and no emoji, neither.
OPENER_NAMES = (Names(), Names(user="Sam", companion="Ada", emoji="🌿"), Names(user="Sam", emoji=""),
                Names(user="", emoji=""))


def golden(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8")


class GoldenBanksTest(unittest.TestCase):
    def test_bank_lines(self):
        for k in KINDS:
            with self.subTest(k):
                self.assertEqual("\n".join(library.builtins()[k].bank_lines()) + "\n", golden(f"{k}.bank_lines.txt"))

    def test_bank_with_ids(self):
        for k in KINDS:
            with self.subTest(k):
                self.assertEqual(library.builtins()[k].bank_with_ids() + "\n", golden(f"{k}.bank_with_ids.txt"))

    def test_bank_items(self):
        for k in KINDS:
            with self.subTest(k):
                want = json.loads(golden(f"{k}.bank_items.json"))
                self.assertEqual(library.builtins()[k].items(), want)
                self.assertEqual(list(library.builtins()[k].items()), list(want))  # order too

    def test_bank_text_is_the_lines_joined(self):
        for k in KINDS:
            self.assertEqual(library.builtins()[k].bank_text() + "\n", golden(f"{k}.bank_lines.txt"))


class GoldenMessagesTest(unittest.TestCase):
    def test_telegram_html(self):
        for k in KINDS:
            with self.subTest(k):
                self.assertEqual(compose.telegram_html(k, FIXED, OPENER), golden(f"{k}.telegram.html"))

    def test_plain_text(self):
        for k in KINDS:
            with self.subTest(k):
                self.assertEqual(compose.plain_text(k, FIXED, OPENER), golden(f"{k}.plain.txt"))

    def test_plain_opener(self):
        for k in KINDS:
            with self.subTest(k):
                got = "\n".join(compose.plain_opener(k, n) for n in OPENER_NAMES) + "\n"
                self.assertEqual(got, golden(f"{k}.plain_opener.txt"))


class PracticeFilesTest(unittest.TestCase):
    """The packaged files carry what the engine still reads from code until WP2; they must say the same."""

    def test_the_files_are_the_four_rituals_in_order(self):
        self.assertEqual(list(library.builtins()), list(KINDS))

    def test_plain_opener_from_the_file(self):
        for k in KINDS:
            with self.subTest(k):
                p = library.builtins()[k]
                got = "\n".join(p.plain_opener(n) for n in OPENER_NAMES) + "\n"
                self.assertEqual(got, golden(f"{k}.plain_opener.txt"))

    def test_opener_shape_limit_schedule_and_windows(self):
        for k in KINDS:
            with self.subTest(k):
                p = library.builtins()[k]
                self.assertEqual(p.opener["shape"], prompts.OPENER_SHAPE[k])
                self.assertEqual(p.opener["limit"], validate.LIMITS[k])
                self.assertEqual(p.opener["mode"], "companion")
                want = TIMES[k]
                self.assertEqual(p.schedule, (library.Slot(want["time"], want.get("weekday")),))
                self.assertEqual((p.collapse_hours, p.annotate, p.emoji, p.enabled), (6.0, True, "", True))
        self.assertEqual({k: library.builtins()[k].closes for k in KINDS},
                         {"morning": None, "evening": "day", "midweek": None, "weekly": "week"})

    def test_the_generator_would_change_nothing(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("gen_builtins", GOLDEN.parents[1] / "tools" / "gen_builtins.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        self.assertEqual(gen.main(["--check"]), 0)


if __name__ == "__main__":
    unittest.main()
