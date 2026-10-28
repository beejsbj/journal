"""The companion sees her own recent openers (so she doesn't repeat herself), and they are never evidence
of what he said."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from helpers import CHAT, add_prompt, make_cfg

from journal import capture, context, ledger, library, prompts, sources, validate

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 15, 6, 30, tzinfo=IST)
LINE = "long tiring day, then a quiet evening with tea"


def sent_at(day, hour):
    return datetime.fromisoformat(f"{day}T{hour}:01:00+05:30").astimezone(timezone.utc)


class RecentOpenersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.sources["weather"] = {"enabled": False}

    def tearDown(self):
        self.tmp.cleanup()

    def prompt(self, kind, day, hour, opener, mid, source="iris", **extra):
        add_prompt(self.cfg, kind=kind, local_date=day, sent=sent_at(day, hour), message_id=mid)
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, f"{day}-{kind}").update(opener=opener, opener_source=source, **extra)

    def test_lists_her_prompts_from_the_last_three_days_newest_first(self):
        self.prompt("morning", "2026-10-14", "06", f"Morning 🌻 you said \"{LINE}\". What would make today easy?", "1")
        self.prompt("evening", "2026-10-14", "23", f"Evening 🌻 {LINE}: how did it land?\n## sneaky heading", "2")
        self.prompt("midweek", "2026-10-14", "13", "Midweek, plain.", "3", source="plain:agent-failed")
        self.prompt("evening", "2026-10-10", "23", "Too old to matter.", "4")
        self.prompt("morning", "2026-10-15", "06", "Fresh one, no reply yet.", "5")
        with ledger.locked(self.cfg.ledger_path) as data:  # the 14th morning was answered
            ledger.find(data, "2026-10-14-morning")["captures"] = [{"at": sent_at("2026-10-14", "07").isoformat()}]
            ledger.find(data, "2026-10-14-evening").pop("captures", None)
        lines = context.recent_openers(self.cfg, NOW)
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].startswith("- [you] Thu 15 Oct 06:01 · Morning journal prompt · no reply yet: Fresh one"))
        self.assertTrue(lines[1].startswith("- [you] Wed 14 Oct 23:01 · Evening journal prompt · missed, never answered: Evening"))
        self.assertIn("how did it land? ## sneaky heading", lines[1])  # one line: can't open a pack section
        self.assertEqual(lines[2], "- [you] Wed 14 Oct 13:01 · Midweek check-in prompt · missed, never answered: "
                                   "(the stock opener)")  # the shape counts; the stock text doesn't
        self.assertTrue(lines[3].startswith("- [you] Wed 14 Oct 06:01 · Morning journal prompt · answered: Morning 🌻"))
        self.assertNotIn("Too old", "\n".join(lines))
        self.assertEqual([p["id"] for p in context.unanswered(self.cfg, NOW)],
                         ["2026-10-15-morning", "2026-10-14-evening", "2026-10-14-midweek"])  # up to the answered one

    def test_the_section_is_in_the_pack_and_capped(self):
        for i in range(3):
            self.prompt("morning", f"2026-10-{13 + i}", "06", "Long opener about a lake and a book. " * 40, str(i))
        pack = context.build(self.cfg, NOW)
        head = ("## Recent conversation with him, oldest first: what you sent him through the journal (last 3 days). "
                "Lines starting \"[you]\" are your own words, not Theo's: never evidence of anything he said")
        self.assertIn(head, pack)
        section = pack.split(head)[1].split("\n## ")[0]
        self.assertLessEqual(len(section), context.MINE_CAP + 5)
        self.assertEqual(section.count("\n- [you] "), 3)
        self.assertIn(" …", section)  # each is shortened
        self.assertLess(section.index("Tue 13 Oct"), section.index("Thu 15 Oct"))  # oldest first
        self.assertIn("## Recent journal entries", pack.split(head)[0])

    def test_the_total_is_capped_by_dropping_the_oldest(self):
        for i in range(3):
            self.prompt("morning", f"2026-10-{13 + i}", "06", f"opener {i} " + "word " * 120, str(i))
        for n in range(1, 4):  # several kinds per day
            self.prompt("evening", f"2026-10-{12 + n}", "22", f"evening {n} " + "word " * 120, str(10 + n))
        lines = context.recent_openers(self.cfg, NOW)
        self.assertLessEqual(sum(len(l) + 1 for l in lines), context.MINE_CAP)
        self.assertLess(len(lines), 6)
        self.assertIn("15 Oct", lines[0])

    def test_nothing_recent(self):
        self.assertIn("## Recent conversation", context.build(self.cfg, NOW))
        self.assertEqual(context.recent_openers(self.cfg, NOW), [])
        self.assertEqual(context.unanswered(self.cfg, NOW), [])

    def test_the_prompt_tells_her_not_to_repeat_and_that_missed_openers_did_not_land(self):
        q = prompts.opener_request(self.cfg, "evening", NOW, "PACK", library.builtins()["evening"].bank_with_ids())
        self.assertIn('The pack\'s "Recent conversation" section', q)
        self.assertIn("don't reuse a line or quote from them, and don't ask a question you\nhave already asked", q)
        self.assertIn("A missed prompt is signal", q)
        self.assertIn("never cite them, or treat\nthem as evidence of what he said or did", q)
        self.assertNotIn("got no reply", q)
        self.assertNotIn("Iris", prompts.opener_request(make_cfg(Path(self.tmp.name), names=type(self.cfg.names)(
            user="Sam", companion="Ada", pronouns="they/them/their")), "evening", NOW, "PACK", library.builtins()["evening"].bank_with_ids()))


class NotEvidenceTest(unittest.TestCase):
    """Quoting her own opener must not pass as quoting him."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.sources["weather"] = {"enabled": False}
        self.opener = "Evening 🌻 a lovely thing about quiet rivers and the slow hours"
        add_prompt(self.cfg, kind="evening", local_date="2026-10-14", sent=sent_at("2026-10-14", "23"), message_id="7")
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, "2026-10-14-evening")["opener"] = self.opener
        self.info = capture.capture(self.cfg, self.cfg.chat_id, "7", "I walked home in the rain", sent_at("2026-10-14", "23") + timedelta(minutes=9),
                                    "text", "70")

    def tearDown(self):
        self.tmp.cleanup()

    def cite(self, source, quote, pack, verify=None):
        return validate.check("morning", {"opener": "Morning 🌻", "citations": [{"source": source, "quote": quote}]},
                              pack, verify)

    def test_her_opener_is_in_the_entry_file_but_not_in_what_the_verifier_checks_against(self):
        self.assertIn("quiet rivers", Path(self.info["path"]).read_text())
        verify = context.make_verifier(self.cfg)
        self.assertTrue(verify("journal:2026-10-14-evening", validate.norm("I walked home in the rain")))
        self.assertFalse(verify("journal:2026-10-14-evening", validate.norm("a lovely thing about quiet rivers")))

    def test_the_pack_section_is_not_citable(self):
        pack = context.build(self.cfg, NOW)
        self.assertIn("quiet rivers", pack)  # she can see it ...
        ok, why = self.cite("journal:2026-10-14-evening", "a lovely thing about quiet rivers", pack)
        self.assertEqual((ok, why), (False, "unverified quote from journal:2026-10-14-evening"))  # ... not cite it
        self.assertFalse(self.cite("telegram:2026-10-14T23:00", "the slow hours", pack, context.make_verifier(self.cfg))[0])
        self.assertTrue(self.cite("journal:2026-10-14-evening", "I walked home in the rain", pack,
                                  context.make_verifier(self.cfg))[0])  # his own words still are
        self.assertNotIn("quiet rivers", validate.citable(pack))
        self.assertIn("I walked home in the rain", validate.citable(pack))  # only the section is cut

    def test_his_quotes_inside_her_opener_still_verify_against_his_own_records(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, "2026-10-14-evening")["opener"] = f'You wrote "{LINE}".'
        capture.capture(self.cfg, self.cfg.chat_id, "7", LINE, sent_at("2026-10-14", "23") + timedelta(minutes=12),
                        "text", "71")
        pack = context.build(self.cfg, NOW)
        self.assertTrue(self.cite("journal:2026-10-14-evening", LINE, pack, context.make_verifier(self.cfg))[0])

    def test_telegram_reply_quotes_of_her_opener_are_not_his_words(self):
        db = Path(self.tmp.name) / "state.db"
        con = sqlite3.connect(db)
        con.executescript("create table sessions (id text, source text, chat_id text);"
                          "create table messages (session_id text, role text, timestamp real, content text, tool_calls text);")
        con.execute("insert into sessions values ('s', 'telegram', ?)", (CHAT,))
        ts = (datetime.now() - timedelta(hours=2)).timestamp()
        con.execute("insert into messages values ('s', 'user', ?, ?, '')", (
            ts, f'[Replying to: "{self.opener}"]\n\nI went to the lake'))
        con.commit()
        con.close()
        self.cfg.hermes["state_db"] = str(db)
        pack = context.build(self.cfg, datetime.now(IST))
        self.assertIn("— I went to the lake", pack)
        section = validate.citable(pack)
        self.assertIn("I went to the lake", section)
        self.assertNotIn("quiet rivers", section)  # his messages in the pack carry only his words
        verify = context.make_verifier(self.cfg)
        self.assertTrue(verify("telegram:x", validate.norm("I went to the lake")))
        self.assertFalse(verify("telegram:x", validate.norm("quiet rivers")))
        self.assertEqual(context.clean_user_text('[Replying to: "a\nb"]\n\nhello'), "hello")


class AnchoringTest(unittest.TestCase):
    """A missed prompt is signal; an opener need not quote his words to be grounded."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.sources["weather"] = {"enabled": False}

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_prompt_names_the_missed_run(self):
        for kind, day, hour, mid in (("morning", "2026-10-14", "06", "1"), ("midweek", "2026-10-14", "12", "2"),
                                     ("evening", "2026-10-13", "23", "3")):
            add_prompt(self.cfg, kind=kind, local_date=day, sent=sent_at(day, hour), message_id=mid)
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, "2026-10-13-evening")["captures"] = [{"at": sent_at("2026-10-13", "23").isoformat()}]
        missed = context.unanswered(self.cfg, NOW)
        q = prompts.opener_request(self.cfg, "evening", NOW, "PACK", library.builtins()["evening"].bank_with_ids(), missed)
        self.assertIn("His last 2 prompts got no reply: midweek (Wed 12:01), morning (Wed 06:01). A missed prompt "
                      "is signal, not a gap to fill.", q)
        self.assertIn("don't come back to what they were about, and don't re-quote the line they quoted", q)
        self.assertIn("You don't have to quote his words.", q)
        one = prompts.missed_note(self.cfg, missed[:1], NOW)
        self.assertEqual(one, "His last prompt got no reply: midweek (Wed 12:01). ")

    def check(self, opener, citations):
        return validate.check("evening", {"opener": opener, "citations": citations}, "## Pack\n- book:Moby-Dick",
                              lambda s, q: False)

    def test_openers_grounded_in_other_facts_pass_without_his_words(self):
        self.assertEqual(self.check("Evening 🌻 rain all day here. What was cozy about it?",
                                    [{"source": "weather:today", "quote": "showers, rain chance 100%"}]), (True, "ok"))
        self.assertEqual(self.check("Evening 🌻 you were 19% into Moby-Dick this morning: still with it?",
                                    [{"source": "book:Moby-Dick", "quote": "19% through"}]), (True, "ok"))
        self.assertEqual(self.check("Evening 🌻 what simple joys touched you today?", []), (True, "ok"))  # plain
        self.assertEqual(self.check("Evening 🌻 you said it was a long day.", []), (False, "claim without citation"))
        self.assertEqual(self.check("Evening 🌻 you said it was a long day.",
                                    [{"source": "telegram:2026-10-14T23:14", "quote": "long tiring day"}]),
                         (False, "unverified quote from telegram:2026-10-14T23:14"))  # his words are still checked

    def test_idle_tududi_habits_become_one_line(self):
        tasks = [{"name": "Water", "habit_mode": True, "habit_current_streak": 0, "habit_last_completion_at": None},
                 {"name": "Stretch", "habit_mode": True, "habit_current_streak": 0},
                 {"name": "Reading", "habit_mode": True, "habit_current_streak": 1,
                  "habit_last_completion_at": "2026-10-03T08:00:00Z"},
                 {"name": "Walk", "habit_mode": True, "habit_current_streak": 0,
                  "habit_last_completion_at": "2026-10-01T08:00:00Z"},
                 {"name": "Renew passport", "Project": {"name": "Admin"}}]
        from unittest import mock
        with mock.patch.object(sources, "_mcp_call", lambda *a: {"tasks": tasks}):
            lines = sources.tududi(self.cfg, NOW)
        self.assertEqual(lines, ["- tududi habit: Reading (streak 1, last 2026-10-03)",
                                 "- tududi habit: Walk (streak 0, last 2026-10-01)",
                                 "- tududi: open — Renew passport [Admin]",
                                 "- tududi: 2 more habits never completed yet (not listed)"])


if __name__ == "__main__":
    unittest.main()
