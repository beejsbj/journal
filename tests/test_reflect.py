"""Day reflections and week reviews: when they are written, what they hold, that quotes are real,
and that each is sent to Telegram once."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CHAT, add_prompt, make_cfg

from journal import capture, context, entries, ledger, reflect, telegram, tick, validate

IST = ZoneInfo("Asia/Kolkata")
GOOD = ('**What stood out** — The rain, and a walk in it anyway.\n\n'
        '**Patterns and tensions** — You wrote "rain all day, walked anyway" and, about the evening, '
        '“the soup was good … the bus can wait”. That reads as patience held lightly (inference).\n\n'
        '**To carry forward** — What would being there look like, on your terms?\n'
        'FOLLOW-UP: ask how the visit went')


class FakeBot:
    def __init__(self, fail=0):
        self.calls, self.fail, self.next_id = [], fail, 700

    def __call__(self, cfg, method, payload):
        self.calls.append((method, payload))
        if self.fail:
            self.fail -= 1
            raise telegram.TelegramError(f"{method}: Bad Gateway")
        self.next_id += 1
        return {"message_id": self.next_id}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.asked = []
        self.answer = GOOD
        self.bot = FakeBot()
        self.patches = [mock.patch("journal.agent.ask", self.ask),
                        mock.patch.object(telegram, "_call", self.bot),
                        mock.patch.object(telegram, "token", lambda cfg: "T")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def ask(self, cfg, query, budget, toolsets=None):
        self.asked.append((query, toolsets))
        return self.answer

    def prompt(self, kind, day, hour, mid, opener):
        sent = datetime.fromisoformat(f"{day}T{hour}:01:00+05:30").astimezone(timezone.utc)
        add_prompt(self.cfg, kind=kind, local_date=day, sent=sent, message_id=mid)
        with ledger.locked(self.cfg.ledger_path) as data:
            p = ledger.find(data, f"{day}-{kind}")
            p.update(opener=opener, sent_at_local=f"{hour}:01 IST")
        return sent

    def answer_prompt(self, kind, day, words, at, mid, annotate=True):
        info = capture.capture(self.cfg, CHAT, mid, words, at, "text", str(int(mid) + 1000))
        if annotate:
            entries.write_annotations(Path(info["path"]), "- He walked in the rain.", self.cfg.names)
            with ledger.locked(self.cfg.ledger_path) as data:
                ledger.find(data, f"{day}-{kind}")["annotated_at"] = at.isoformat()
        return info

    def reflect(self, now_local):
        log = []
        alerts = reflect.run(self.cfg, now_local.astimezone(timezone.utc), log)
        return alerts, log

    def rec(self, rid):
        return reflect.find(ledger.snapshot(self.cfg.ledger_path), rid)

    def day(self):
        self.prompt("morning", "2026-10-14", "06", "10", "Morning, Theo 🌻 how was the night?")
        sent = self.prompt("evening", "2026-10-14", "23", "11", "Evening 🌻 let the day land.")
        return sent


class DayReflectionTest(Base):
    def test_written_when_the_evening_is_annotated_and_sent_once(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway. the soup was good. "
                           "But idk. the bus can wait", sent + timedelta(minutes=10), "11")
        alerts, log = self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.assertEqual(alerts, [])
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)
        self.assertIn("sent 2026-10-14-reflection to Telegram", log)
        body = (self.cfg.root / "2026-10-14-reflection.md").read_text()
        self.assertTrue(body.startswith("---\ndate: 2026-10-14\nkind: reflection\ntype: journal\n"
                                        "source: iris-journal\nauthor: iris\n---\n\n# Iris's reflection — Wed 14 Oct 2026\n\n"
                                        "_From [[2026-10-14-evening]] · written 14 Oct 23:45 IST_\n\n**What stood out**"))
        self.assertNotIn("FOLLOW-UP", body)
        self.assertNotIn("[!note]", body)  # no folded list of the day's prompts: unanswered openers wash away
        self.assertNotIn("how was the night?", body)
        self.assertTrue(body.rstrip().endswith("on your terms?"))
        query, toolsets = self.asked[0]
        self.assertEqual(toolsets, "none")
        self.assertIn("rain all day, walked anyway", query)
        self.assertIn("- He walked in the rain.", query)
        self.assertNotIn("[!question]", query)  # the question bank is not an input
        self.assertNotIn("notebook.json", query)
        method, payload = self.bot.calls[0]
        self.assertEqual(method, "sendMessage")
        self.assertTrue(payload["text"].startswith("<b>🌻 Iris's reflection — Wed 14 Oct 2026</b>\n\n"
                                                   "<blockquote expandable><b>What stood out</b>"))
        self.assertNotIn("disable_notification", payload)
        self.assertNotIn("Today's prompts", payload["text"])
        self.assertEqual(self.rec("2026-10-14-reflection")["telegram"]["message_id"], "701")
        self.assertEqual(self.reflect(datetime(2026, 10, 15, 4, 30, tzinfo=IST)), ([], []))  # idempotent
        self.assertEqual((len(self.asked), len(self.bot.calls)), (1, 1))

    def test_fallback_next_morning_when_the_evening_was_never_answered(self):
        self.day()
        self.answer_prompt("morning", "2026-10-14", "rain all day, walked anyway. the soup was good and "
                           "the bus can wait", datetime(2026, 10, 14, 7, 0, tzinfo=IST), "10")
        self.assertEqual(self.reflect(datetime(2026, 10, 14, 23, 50, tzinfo=IST)), ([], []))
        self.assertEqual(self.reflect(datetime(2026, 10, 15, 3, 55, tzinfo=IST)), ([], []))
        alerts, log = self.reflect(datetime(2026, 10, 15, 4, 5, tzinfo=IST))
        self.assertIn("wrote 2026-10-14-reflection.md (fallback) (+1 notebook)", log)
        self.assertTrue(self.bot.calls[0][1]["disable_notification"])
        self.assertTrue(self.rec("2026-10-14-reflection")["telegram"]["silent"])

    def test_empty_day_skipped(self):
        self.day()  # prompts sent, nothing answered
        self.assertEqual(self.reflect(datetime(2026, 10, 15, 5, 0, tzinfo=IST)), ([], []))
        self.assertEqual(self.asked, [])
        self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())

    def test_waits_while_an_entry_is_still_being_annotated(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway", sent + timedelta(hours=4),
                           "11", annotate=False)
        self.assertEqual(self.reflect(datetime(2026, 10, 15, 4, 5, tzinfo=IST)), ([], []))
        self.assertEqual(self.asked, [])

    def test_invented_quote_writes_nothing_retries_then_alerts(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway", sent + timedelta(minutes=10), "11")
        self.answer = GOOD.replace("rain all day, walked anyway", "I miss the lake house")
        for n in (1, 2):
            alerts, log = self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
            self.assertEqual(alerts, [])
            self.assertIn(f"attempt {n}/3 failed (quote not in the entries: 'I miss the lake house')", log[0])
        alerts, _ = self.reflect(datetime(2026, 10, 14, 23, 50, tzinfo=IST))
        self.assertEqual(len(alerts), 1)
        self.assertIn("2026-10-14-reflection was not written after 3 attempts", alerts[0])
        self.assertEqual(self.reflect(datetime(2026, 10, 15, 4, 5, tzinfo=IST)), ([], []))  # alerted once, then quiet
        self.assertFalse(list(self.cfg.root.glob("*-reflection*.md")))
        self.assertEqual(self.bot.calls, [])

    def test_failed_agent_degrades_to_no_reflection(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway", sent + timedelta(minutes=10), "11")
        self.answer = None
        alerts, log = self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.assertIn("attempt 1/3 failed (agent failed)", log[0])

    def test_alert_reaches_the_tick_exit_code(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway", sent + timedelta(minutes=10), "11")
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections"] = [{"id": "2026-10-14-reflection", "kind": "reflection", "local_date": "2026-10-14",
                                    "attempts": 2}]
            data["reflections_since"] = "2026-10-14"
        self.answer = "too short"
        now = datetime(2026, 10, 14, 23, 45, tzinfo=IST)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return now.astimezone(tz) if tz else now
        with mock.patch.object(tick, "datetime", Clock), mock.patch.object(tick, "send_due", lambda *a, **k: None), \
                mock.patch.object(tick, "fold", lambda *a, **k: None):
            code, log = tick.run(self.cfg)
        self.assertEqual(code, 1)
        self.assertTrue(any(l.startswith("ALERT: 2026-10-14-reflection was not written") for l in log), log)

    def test_disabled(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway", sent + timedelta(minutes=10), "11")
        self.cfg.reflections["day"] = {"enabled": False}
        self.assertEqual(self.reflect(datetime(2026, 10, 15, 4, 5, tzinfo=IST)), ([], []))

    def test_first_run_has_no_backlog(self):
        self.prompt("evening", "2026-10-10", "23", "5", "Evening")
        self.answer_prompt("evening", "2026-10-10", "rain all day, walked anyway", datetime(2026, 10, 10, 23, 30,
                                                                                           tzinfo=IST), "5")
        self.assertEqual(self.reflect(datetime(2026, 10, 14, 12, 0, tzinfo=IST)), ([], []))


class StatusDoctorTest(Base):
    def test_status_and_doctor_show_reflections(self):
        from journal import status
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway. the soup was good; "
                           "the bus can wait", sent + timedelta(minutes=10), "11")
        self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        s = status.status(self.cfg)
        self.assertEqual(s["reflections"]["recent"][0]["id"], "2026-10-14-reflection")
        self.assertIn("Reflections: 2026-10-14-reflection (sent)", status.format_status(s))
        rows = {n: (st, d) for n, st, d in status.doctor(self.cfg, token=False)}
        self.assertEqual(rows["day reflection"][0], "ok")
        self.assertIn("fallback 04:00", rows["day reflection"][1])
        self.assertEqual(rows["sweep"][0], "ok")  # standalone: skipped
        self.cfg.agent["enabled"] = False
        self.cfg.reflections["week"] = {"enabled": False}
        rows = {n: (st, d) for n, st, d in status.doctor(self.cfg, token=False)}
        self.assertEqual(rows["day reflection"][0], "warn")
        self.assertEqual(rows["week review"], ("ok", "off"))


class WeekReviewTest(Base):
    def test_week_review_after_the_weekly_entry_with_day_reflections_as_input(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections_since"] = "2026-10-12"
        sent = self.prompt("evening", "2026-10-14", "23", "11", "Evening")
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway. the soup was good; "
                           "the bus can wait", sent + timedelta(minutes=10), "11")
        self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))  # the day reflection
        wk = self.prompt("weekly", "2026-10-18", "20", "20", "End of the week.")
        self.answer_prompt("weekly", "2026-10-18", "a soft week", wk + timedelta(minutes=10), "20")
        alerts, log = self.reflect(datetime(2026, 10, 18, 20, 40, tzinfo=IST))
        self.assertIn("wrote 2026-10-18-week.md (annotated) (+1 notebook)", log)
        body = (self.cfg.root / "2026-10-18-week.md").read_text()
        self.assertIn("kind: week\n", body)
        self.assertIn("# Iris's week review — 12–18 Oct 2026", body)
        self.assertIn("[[2026-10-14-evening]], [[2026-10-18-weekly]], [[2026-10-14-reflection]]", body)
        self.assertNotIn("[!note]", body)
        query = self.asked[-1][0]
        self.assertIn("### reflection:2026-10-14-reflection", query)
        self.assertIn("a soft week", query)
        self.assertIn("week review for the week of 12–18 Oct 2026", query)

    def test_week_fallback_the_day_after(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections_since"] = "2026-10-12"
        self.cfg.reflections["day"] = {"enabled": False}
        self.prompt("morning", "2026-10-13", "06", "9", "Morning")
        self.answer_prompt("morning", "2026-10-13", "rain all day, walked anyway; the soup was good "
                           "the bus can wait", datetime(2026, 10, 13, 7, 0, tzinfo=IST), "9")
        self.assertEqual(self.reflect(datetime(2026, 10, 18, 23, 0, tzinfo=IST)), ([], []))
        alerts, log = self.reflect(datetime(2026, 10, 19, 4, 1, tzinfo=IST))
        self.assertIn("wrote 2026-10-18-week.md (fallback) (+1 notebook)", log)
        self.assertTrue(self.bot.calls[-1][1]["disable_notification"])


class FilesAndContextTest(Base):
    def test_kinds_hand_written_files_and_context(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway; the soup was good "
                           "the bus can wait", sent + timedelta(minutes=10), "11")
        mine = self.cfg.root / "2026-10-14-reflection.md"
        mine.write_text("my own reflection, by hand\n")
        self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.assertEqual(mine.read_text(), "my own reflection, by hand\n")  # never overwritten
        made = self.cfg.root / "2026-10-14-reflection-2.md"
        self.assertTrue(entries.is_analysis(made))
        self.assertIsNone(entries.kind_of(mine))
        self.assertEqual(entries.kind_of(made), "reflection")
        self.assertNotIn(made, entries.entries_on(self.cfg.root, "2026-10-14"))
        self.cfg.sources["weather"] = {"enabled": False}
        pack = context.build(self.cfg, datetime(2026, 10, 15, 6, 0, tzinfo=IST))
        self.assertIn("## Your earlier analysis (your own reflections, not Theo's words)\n"
                      "### reflection:2026-10-14-reflection-2 (your day reflection)\n**What stood out**", pack)
        self.assertNotIn("journal:2026-10-14-reflection", pack)  # not listed as one of his entries
        later = context.build(self.cfg, datetime(2026, 10, 19, 6, 0, tzinfo=IST))
        self.assertNotIn("reflection:2026-10-14", later)  # older than three days
        ok, _ = validate.check("morning", {"opener": "Morning", "citations": [
            {"source": "reflection:2026-10-14-reflection-2", "quote": "I made this up"}]}, pack)
        self.assertFalse(ok)


class ValidatorTest(unittest.TestCase):
    CORPUS = "### journal:x\nI'm tired — but it's fine. The bus can wait, honestly."

    def check(self, text):
        return validate.check_reflection("reflection", "x" * 80 + "\n" + text, self.CORPUS)

    def test_quotes(self):
        self.assertTrue(self.check('You said "I’m tired – but it’s fine."')[0])
        self.assertTrue(self.check("You said “I'm tired … honestly”")[0])
        self.assertTrue(self.check("> The bus can wait")[0])
        self.assertFalse(self.check('You said "I am exhausted"')[0])
        self.assertFalse(self.check("> I never said this")[0])
        self.assertFalse(validate.check_reflection("reflection", "short", self.CORPUS)[0])
        self.assertIn("too long", validate.check_reflection("reflection", "a " * 2000, self.CORPUS)[1])
        self.assertFalse(validate.check_reflection("reflection", '{"opener": "x"}' + "x" * 90, self.CORPUS)[0])


if __name__ == "__main__":
    unittest.main()
