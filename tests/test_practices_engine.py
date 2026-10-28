"""The engine keyed by Practice (WP2): a practice of his own is scheduled, sent, captured, folded and started like
the rituals; a ``schedule`` key left in the config is not read (WP13) and doctor says so; ``/start <id>`` resolves at call time;
reflections follow ``closes:``; the skill lists the library."""
import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CHAT, CleanEnv, make_cfg, write_config

from journal import (agent, capture, cli, compose, config, context, entries, ledger, library, location, prompts,
                     reflect, schedule, status, telegram, tick, validate)
from journal import commands as jcommands
from journal.config import Names, load
from journal.hermes import commands, hooks, runtime

IST = ZoneInfo("Asia/Kolkata")
NAMES = Names(user="Sam", companion="Ada", emoji="🌿", pronouns="he/him/his", frontmatter_source="journal")
GRATITUDE = """---
kind: practice
id: gratitude
title: Daily gratitude
schedule.time: 21:30
---
- Three things today, and why each one mattered.
- Who made one of them possible?
"""
WATER = """---
kind: practice
id: water
title: Water
emoji: 💧
schedule.1.time: 09:00
schedule.2.time: 15:00
collapse_hours: 2
annotate: false
opener.mode: plain
opener.plain: {greet:Water} One line.
companion: a nudge, no more
---
- Did you drink enough?
"""
WED = datetime(2026, 10, 14, 0, 0, tzinfo=IST)  # a Wednesday


def at(h, m=1, day=WED):
    return day.replace(hour=h, minute=m)


class Bot:
    """Telegram as a list of calls; every send gets the next message id."""

    def __init__(self):
        self.calls, self.next_id = [], 700

    def __call__(self, cfg, method, payload):
        self.calls.append((method, payload))
        self.next_id += 1
        return {"message_id": self.next_id}

    def texts(self, method="sendMessage"):
        return [p["text"] for m, p in self.calls if m == method]


class Base(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name), names=NAMES)
        self.cfg.sources["weather"] = {"enabled": False}
        self.bot = Bot()
        self.asked = []
        self.ids = iter(range(5000, 6000))
        test = self

        class Clock(datetime):  # the tick stamps sends with the wall clock: hold it at the send being simulated
            @classmethod
            def now(cls, tz=None):
                return test.now.astimezone(tz) if tz else test.now

        def unreachable(cfg, query, *a, **k):
            self.asked.append(query)
            raise agent.AgentUnavailable("no model in this test")
        for p in (mock.patch.object(telegram, "_call", self.bot), mock.patch.object(telegram, "token", lambda c: "T"),
                  mock.patch.object(agent, "ask", unreachable), mock.patch.object(tick, "datetime", Clock),
                  mock.patch.object(context, "build", lambda c, n, *a: "PACK")):
            p.start()
            self.addCleanup(p.stop)
        self.now = at(0)

    def send(self, when, **kw):
        self.now = when
        tick.send_due(self.cfg, when, [], **kw)

    def write(self, name, text):
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        path = d / name
        path.write_text(text, encoding="utf-8")
        return path

    def due(self, when):
        return [k for k, _ in schedule.due(self.cfg, when, set())]

    def prompt(self, pid):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)


class UserPracticeTest(Base):
    def test_a_user_practice_at_2130_is_due_and_sent_with_the_plain_opener_and_its_bank(self):
        self.write("gratitude.md", GRATITUDE)
        self.assertEqual(self.due(at(21, 31)), ["gratitude"])
        self.assertEqual(self.due(at(21, 29)), [])
        self.send(at(21, 31))
        html = self.bot.texts()[0]
        self.assertTrue(html.startswith("<b>🌿 Daily gratitude · Wed 14 Oct</b>\n\nDaily gratitude. A sentence or two "
                                        "is enough.\n\n<blockquote expandable>1. Three things today"), html)
        self.assertIn("2. Who made one of them possible?</blockquote>", html)
        p = self.prompt("2026-10-14-gratitude")
        self.assertEqual((p["kind"], p["stem"], p["opener_source"]), ("gratitude", "2026-10-14-gratitude",
                                                                       "plain:agent-failed"))
        self.assertEqual((p["title"], p["collapse_hours"]), ("Daily gratitude", 6.0))
        self.assertNotIn("annotate", p)
        self.assertIn("writing the opening of Sam's gratitude journal prompt", self.asked[0])
        self.assertIn("[1] Three things today", self.asked[0])
        self.assertEqual(self.due(at(21, 36)), ["gratitude"])  # schedule.due itself knows nothing of the ledger
        self.send(at(21, 36))  # ... but the tick does: sent once
        self.assertEqual(len(self.bot.texts()), 1)

    def test_his_reply_lands_in_an_entry_of_that_practice(self):
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        mid = self.prompt("2026-10-14-gratitude")["message_id"]
        info = capture.capture(self.cfg, CHAT, mid, "the rain, the bread, a friend's call", at(21, 40).astimezone(timezone.utc),
                               "text", "9001")
        body = Path(info["path"]).read_text()
        self.assertEqual(Path(info["path"]).name, "2026-10-14-gratitude.md")
        self.assertTrue(body.startswith("---\ndate: 2026-10-14\nkind: gratitude\npractice: gratitude\ntype: journal\n"))
        self.assertIn("# Daily gratitude — Wed 14 Oct 2026", body)
        self.assertIn("> [!question]- Questions\n> 1. Three things today, and why each one mattered.\n", body)
        # recognised by its front matter (a user id is not known by name alone), so notes and packs find it
        self.assertNotIn("gratitude", entries.known_ids())
        self.assertEqual(entries.kind_of(Path(info["path"])), "gratitude")
        note = capture.add_companion_note(self.cfg, "Bread again (inference).", at(22).astimezone(timezone.utc),
                                          "2026-10-14", "gratitude")
        self.assertEqual(note, info["path"])

    def test_a_hand_written_file_with_a_practice_id_is_still_his(self):
        self.write("gratitude.md", GRATITUDE)
        mine = self.cfg.root / "2026-10-14-gratitude.md"
        mine.write_text("my own list\n", encoding="utf-8")
        self.assertIsNone(entries.kind_of(mine))
        path = capture.add_companion_note(self.cfg, "x", at(22).astimezone(timezone.utc), "2026-10-14")
        self.assertNotEqual(path, str(mine))
        # a reply to that practice's prompt never lands in his file: the entry takes the next free name
        self.send(at(21, 31))
        sent = self.prompt("2026-10-14-gratitude")
        planned = capture.planned_info(self.cfg, sent, ledger.snapshot(self.cfg.ledger_path))["path"]
        info = capture.capture(self.cfg, CHAT, sent["message_id"], "the rain", at(21, 40).astimezone(timezone.utc),
                               "text", "9101")
        self.assertEqual(mine.read_text(), "my own list\n")
        self.assertEqual(Path(info["path"]).name, "2026-10-14-gratitude-2.md")
        self.assertEqual(planned, info["path"])  # what a voice note is told (and kept beside) before it is written
        p = self.prompt("2026-10-14-gratitude")
        self.assertEqual((p["stem"], p["entry_path"]), ("2026-10-14-gratitude-2", info["path"]))
        self.assertEqual(entries.kind_of(Path(info["path"])), "gratitude")
        again = capture.capture(self.cfg, CHAT, sent["message_id"], "and the bread",
                                at(21, 50).astimezone(timezone.utc), "text", "9102")
        self.assertEqual(again["path"], info["path"])
        self.assertIn("and the bread", entries.raw_entry(Path(info["path"]).read_text()))
        self.assertEqual(mine.read_text(), "my own list\n")

    def test_a_hand_written_file_is_never_taken_for_a_ritual_or_a_slot_entry(self):
        mine = self.cfg.root / "2026-10-14-evening.md"
        mine.write_text("---\nkind: evening\n---\nwritten by hand\n", encoding="utf-8")  # no type: journal
        self.send(at(23, 1), only="evening")
        mid = self.prompt("2026-10-14-evening")["message_id"]
        info = capture.capture(self.cfg, CHAT, mid, "a long day", at(23, 10).astimezone(timezone.utc), "text", "1")
        self.assertEqual(Path(info["path"]).name, "2026-10-14-evening-2.md")
        self.assertEqual(mine.read_text(), "---\nkind: evening\n---\nwritten by hand\n")
        # two slots: the fresh name skips every slot's own, so the second slot still gets 2026-10-14-water-2
        self.write("water.md", WATER)
        (self.cfg.root / "2026-10-14-water.md").write_text("mine\n", encoding="utf-8")
        self.send(at(9, 1))
        first = capture.capture(self.cfg, CHAT, self.prompt("2026-10-14-water")["message_id"], "one litre",
                                at(9, 5).astimezone(timezone.utc), "text", "2")
        self.send(at(15, 1))
        second = capture.capture(self.cfg, CHAT, self.prompt("2026-10-14-water-2")["message_id"], "two litres",
                                 at(15, 5).astimezone(timezone.utc), "text", "3")
        self.assertEqual((Path(first["path"]).name, Path(second["path"]).name),
                         ("2026-10-14-water-3.md", "2026-10-14-water-2.md"))
        self.assertNotIn("two litres", Path(first["path"]).read_text())

    def test_a_re_send_keeps_a_diverted_entry(self):
        self.write("gratitude.md", GRATITUDE)
        mine = self.cfg.root / "2026-10-14-gratitude.md"
        mine.write_text("my own list\n", encoding="utf-8")
        self.send(at(21, 31))
        first = capture.capture(self.cfg, CHAT, self.prompt("2026-10-14-gratitude")["message_id"], "the rain",
                                at(21, 40).astimezone(timezone.utc), "text", "9201")
        self.send(at(22, 0), only="gratitude")  # /start gratitude the same day: the new message becomes current
        p = self.prompt("2026-10-14-gratitude")
        self.assertEqual(p["stem"], "2026-10-14-gratitude-2")
        planned = capture.planned_info(self.cfg, p, ledger.snapshot(self.cfg.ledger_path))["path"]
        again = capture.capture(self.cfg, CHAT, p["message_id"], "and the bread", at(22, 5).astimezone(timezone.utc),
                                "text", "9202")
        self.assertEqual((again["path"], planned), (first["path"], first["path"]))
        self.assertEqual(mine.read_text(), "my own list\n")
        self.assertIn("and the bread", entries.raw_entry(Path(first["path"]).read_text()))

    def test_her_note_goes_to_the_engine_entry_not_his_file_of_the_same_name(self):
        mine = self.cfg.root / "2026-10-14-evening.md"
        mine.write_text("written by hand\n", encoding="utf-8")
        self.send(at(23, 1), only="evening")
        info = capture.capture(self.cfg, CHAT, self.prompt("2026-10-14-evening")["message_id"], "a long day",
                               at(23, 10).astimezone(timezone.utc), "text", "1")
        mine.write_text("written by hand, edited later\n", encoding="utf-8")  # his file is the newest of the day
        os.utime(info["path"], (at(23, 10).timestamp(), at(23, 10).timestamp()))
        os.utime(mine, (at(23, 30).timestamp(), at(23, 30).timestamp()))
        path = capture.add_companion_note(self.cfg, "Long day (inference).", at(23, 40).astimezone(timezone.utc),
                                          "2026-10-14", "evening")
        self.assertEqual(path, info["path"])
        self.assertEqual(mine.read_text(), "written by hand, edited later\n")

    def test_an_entry_the_engine_already_wrote_for_the_practice_is_still_used(self):
        # the ledger lost the prompt (a state reset) but its entry is the engine's: a new prompt keeps writing there
        self.send(at(23, 1), only="evening")
        mid = self.prompt("2026-10-14-evening")["message_id"]
        first = capture.capture(self.cfg, CHAT, mid, "first words", at(23, 10).astimezone(timezone.utc), "text", "1")
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"] = []
        self.send(at(23, 20), only="evening")
        mid = self.prompt("2026-10-14-evening")["message_id"]
        again = capture.capture(self.cfg, CHAT, mid, "more words", at(23, 30).astimezone(timezone.utc), "text", "2")
        self.assertEqual(again["path"], first["path"])
        self.assertIn("first words", Path(again["path"]).read_text())

    def test_several_slots_plain_mode_own_window_and_no_annotations(self):
        self.write("water.md", WATER)
        self.send(at(9, 1))
        self.send(at(15, 2))
        first, second = self.prompt("2026-10-14-water"), self.prompt("2026-10-14-water-2")
        self.assertEqual((first["stem"], second["stem"]), ("2026-10-14-water", "2026-10-14-water-2"))
        self.assertEqual(self.asked, [])  # opener.mode: plain asks no model
        self.assertEqual(first["opener_source"], "plain:practice")
        self.assertTrue(self.bot.texts()[0].startswith("<b>💧 Water · Wed 14 Oct</b>\n\nWater, Sam 💧 One line."))
        self.assertEqual((first["collapse_hours"], first["annotate"], first["emoji"]), (2.0, False, "💧"))
        # folds after its own two hours, not windows.collapse_hours
        sent = datetime.fromisoformat(first["sent_at"])
        tick.fold(self.cfg, sent + timedelta(hours=1, minutes=59), [])
        self.assertEqual(self.bot.texts("editMessageText"), [])
        tick.fold(self.cfg, sent + timedelta(hours=2, minutes=1), [])
        self.assertEqual(self.bot.texts("editMessageText")[0],
                         "<b>💧 Water · Wed 14 Oct</b>\n\nWater, Sam 💧 One line.\n\n<i>missed — reply anytime</i>")
        # a reply is saved, with no pending annotations section, and the tick never annotates it
        info = capture.capture(self.cfg, CHAT, second["message_id"], "two litres", sent + timedelta(hours=6), "text", "5")
        body = Path(info["path"]).read_text()
        self.assertNotIn("annotations", body)
        self.assertIs(self.prompt("2026-10-14-water-2")["annotate"], False)

    def test_built_ins_still_fold_on_the_configured_window(self):
        self.cfg.collapse_hours = 4
        self.send(at(23, 1), only="evening")
        p = self.prompt("2026-10-14-evening")
        self.assertNotIn("collapse_hours", p)  # a built-in records none: windows.collapse_hours governs it
        sent = datetime.fromisoformat(p["sent_at"])
        tick.fold(self.cfg, sent + timedelta(hours=4, minutes=1), [])
        self.assertEqual(len(self.bot.texts("editMessageText")), 1)

    def test_a_deleted_practice_still_folds_under_its_recorded_title(self):
        path = self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        path.unlink()
        sent = datetime.fromisoformat(self.prompt("2026-10-14-gratitude")["sent_at"])
        tick.fold(self.cfg, sent + timedelta(hours=7), [])
        edited = self.bot.texts("editMessageText")
        self.assertEqual(len(edited), 1)
        self.assertEqual(edited[0], "<b>🌿 Daily gratitude · Wed 14 Oct</b>\n\nDaily gratitude. A sentence or two is "
                                    "enough.\n\n<i>missed — reply anytime</i>")  # the recorded title and opener
        info = capture.capture(self.cfg, CHAT, self.prompt("2026-10-14-gratitude")["message_id"], "late",
                               sent + timedelta(hours=8), "text", "77")
        body = Path(info["path"]).read_text()
        self.assertIn("# Daily gratitude — Wed 14 Oct 2026", body)
        self.assertNotIn("[!question]", body)  # the bank is gone with the file; his words are not
        self.assertIn("late", entries.raw_entry(body))

    def test_a_fork_of_a_built_in_is_what_is_sent(self):
        fork = library.fork(self.cfg, "midweek")
        fork.write_text(fork.read_text().replace("## 1. Energy Check", "## 1. Energy, honestly"), encoding="utf-8")
        self.send(at(12, 1))
        self.assertIn("<b>1. Energy, honestly</b>", self.bot.texts()[0])

    def test_the_status_and_travel_views_list_every_scheduled_practice(self):
        self.write("gratitude.md", GRATITUDE)
        nxt = status.next_occurrences(self.cfg, at(21, 0))
        self.assertEqual(list(nxt), ["morning", "gratitude", "evening", "midweek", "weekly"])  # library order
        self.assertEqual(nxt["gratitude"], "Wed 14 Oct 21:30 IST")
        self.assertEqual(status.status(self.cfg, at(21, 31).astimezone(timezone.utc))["due_now"], ["gratitude"])
        conf = write_config(Path(self.tmp.name) / "c.json", {"root": str(self.cfg.root),
                                                             "state_dir": str(self.cfg.state_dir)})
        hit = {"name": "Chicago", "latitude": 41.9, "longitude": -87.6, "timezone": "America/Chicago"}
        with mock.patch.object(location, "geocode", lambda q: hit):
            r = location.set_location(load(conf), "Chicago", reload=lambda: load(conf))
        self.assertEqual(r["schedule_local"]["gratitude"], "21:30")
        self.assertEqual(r["schedule_local"]["weekly"], "20:00 (Sun)")


class DisabledAndRetiredKeyTest(Base):
    def test_a_disabled_built_in_is_not_due_and_does_not_start(self):
        fork = library.fork(self.cfg, "evening")
        fork.write_text(fork.read_text().replace("---\n## 1.", "enabled: false\n---\n## 1.", 1), encoding="utf-8")
        self.assertFalse(library.get(self.cfg, "evening").enabled)
        self.assertNotIn("evening", self.due(at(23, 1)))
        found = jcommands.start(self.cfg, "evening")
        self.assertIsNone(found.practice)
        self.assertIn("Evening journal is turned off", found.reply)

    def test_a_schedule_key_in_the_config_is_not_read_and_doctor_names_it(self):
        conf = write_config(Path(self.tmp.name) / "c.json", {"root": str(self.cfg.root),
                                                             "state_dir": str(self.cfg.state_dir),
                                                             "schedule": {"evening": {"time": "22:00"},
                                                                          "morning": {"enabled": False}}})
        cfg = load(conf)
        self.assertFalse(hasattr(cfg, "schedule"))
        self.assertEqual(schedule.due(cfg, at(22, 1), set()), [])  # the file says 23:00, not the old key
        self.assertEqual([k for k, _ in schedule.due(cfg, at(6, 1), set())], ["morning"])  # enabled: false is not read
        self.assertEqual([k for k, _ in schedule.due(cfg, at(23, 1), set())], ["evening"])
        rows = [(s, d) for n, s, d in status.doctor(cfg, token=False) if n == "config"]
        self.assertTrue(any(s == "warn" and d.startswith("`schedule` in the config is no longer read") and
                            "journal migrate-config" in d for s, d in rows), rows)

    def test_a_value_of_any_shape_is_named_not_obeyed(self):
        for value in ({"evening": "23:30"}, ["x"], "07:00", 5):
            with self.subTest(value=value):
                conf = write_config(Path(self.tmp.name) / "c.json", {"root": str(self.cfg.root), "schedule": value})
                cfg = load(conf)  # loads: a stale key must never stop every send
                self.assertEqual(library.get(cfg, "evening").schedule, (library.Slot("23:00"),))
                self.assertTrue(any("no longer read" in w for w in cfg.warnings))

    def test_a_config_without_the_key_has_nothing_to_say(self):
        conf = write_config(Path(self.tmp.name) / "c.json", {"root": str(self.cfg.root)})
        cfg = load(conf)
        self.assertEqual([w for w in cfg.warnings if "schedule" in w], [])
        self.assertEqual([p for p in config.problems(cfg) if "schedule" in p], [])
        for empty in ({}, None):  # (the empty default the old plugin form saved)
            conf = write_config(Path(self.tmp.name) / "c.json", {"root": str(self.cfg.root), "schedule": empty})
            self.assertEqual([w for w in load(conf).warnings if "schedule" in w], [])

    def test_a_fork_decides_the_times_and_the_week_s_end(self):
        fork = library.fork(self.cfg, "weekly")
        fork.write_text(fork.read_text().replace("schedule.weekday: 6", "schedule.weekday: 5"), encoding="utf-8")
        self.assertEqual(reflect.week_end_day(self.cfg), 5)
        self.assertEqual(library.get(self.cfg, "weekly").schedule, (library.Slot("20:00", 5),))
        fork.write_text(fork.read_text().replace("schedule.time: 20:00\n", "").replace("schedule.weekday: 5\n", ""),
                        encoding="utf-8")
        self.assertEqual(library.get(self.cfg, "weekly").schedule, ())  # on demand only: Sunday ends the week
        self.assertEqual(reflect.week_end_day(self.cfg), 6)


class CliTest(Base):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        args = cli.build_parser().parse_args(list(argv))
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.run(args, cfg_loader=lambda: self.cfg)
        return code, out.getvalue(), err.getvalue()

    def test_practices_has_no_migrate_schedule_any_more(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["practices", "migrate-schedule"])

    def test_a_fork_keeps_the_configured_window(self):
        self.cfg.collapse_hours = 3
        code, out, _ = self.run_cli("practices", "fork", "evening")
        self.assertEqual(library.get(self.cfg, "evening").collapse_hours, 3.0)
        self.assertIn("collapse_hours: 3 (windows.collapse_hours", out)
        self.send(at(23, 1))
        p = self.prompt("2026-10-14-evening")
        self.assertEqual(capture.window(self.cfg, p), timedelta(hours=3))
        later = datetime.fromisoformat(p["sent_at"]) + timedelta(hours=4)
        self.assertIsNone(capture.capture(self.cfg, CHAT, None, "just chatting", later, "text", "8"))  # chat, as before
        code, out, _ = self.run_cli("practices", "fork", "midweek")
        self.assertEqual(library.get(self.cfg, "midweek").collapse_hours, 3.0)
        self.assertIn("collapse_hours: 3 (windows.collapse_hours", out)

    def test_show_and_list_say_the_file_s_schedule(self):
        self.write("gratitude.md", GRATITUDE)
        code, out, _ = self.run_cli("practices", "show", "gratitude")
        self.assertIn("schedule: daily 21:30\n", out)
        code, out, _ = self.run_cli("practices", "list")
        self.assertIn("daily 21:30", out)

    def test_send_takes_any_library_id(self):
        self.write("gratitude.md", GRATITUDE)
        code, out, _ = self.run_cli("send", "gratitude", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("[dry-run] ", out)
        self.assertIn("Daily gratitude", out)
        code, _, err = self.run_cli("send", "nope")
        self.assertEqual(code, 2)
        self.assertIn("no practice 'nope' (known: morning, gratitude, evening, midweek, weekly)", err)


class ForkTest(Base):
    def test_a_second_fork_goes_into_a_directory_that_already_holds_practices(self):
        self.write("gratitude.md", GRATITUDE)
        library.fork(self.cfg, "evening")
        dest = library.fork(self.cfg, "morning")
        self.assertEqual(dest.name, "morning.md")
        self.assertEqual({p.id for p in library.load(self.cfg).practices.values() if not p.builtin},
                         {"gratitude", "evening", "morning"})
        with self.assertRaises(library.AlreadyForked):
            library.fork(self.cfg, "evening")
        # a file of another name that defines the id is a fork too
        self.write("my-midweek.md", library.render(library.builtins()["midweek"]))
        with self.assertRaises(library.AlreadyForked):
            library.fork(self.cfg, "midweek")


class SlotIdTest(Base):
    def test_a_practice_named_like_another_s_later_slot_is_refused(self):
        self.write("water.md", WATER)  # slots 09:00 and 15:00: prompts 2026-10-14-water and 2026-10-14-water-2
        self.write("water-2.md", GRATITUDE.replace("id: gratitude", "id: water-2").replace("21:30", "08:00"))
        lib = library.load(self.cfg)
        self.assertNotIn("water-2", lib.practices)
        self.assertTrue(any("id 'water-2' is the prompt id of slot 2 of 'water'" in p and "ignored" in p
                            for p in lib.problems), lib.problems)
        self.send(at(8, 1))
        self.send(at(9, 1))
        self.send(at(15, 1))
        self.assertEqual(self.prompt("2026-10-14-water-2")["kind"], "water")  # water's second slot went out
        self.assertEqual(len(self.bot.texts()), 2)
        self.assertIsNone(jcommands.start(self.cfg, "water-2").practice)

    def test_validating_the_refused_file_says_why(self):
        self.write("water.md", WATER)
        path = self.write("water-2.md", GRATITUDE.replace("id: gratitude", "id: water-2"))
        problems = library.validate_file(path, self.cfg)
        self.assertTrue(any("'water-2' is the prompt id of slot 2 of 'water'" in m for m in problems), problems)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli.run(cli.build_parser().parse_args(["practices", "validate", str(path)]), cfg_loader=lambda: self.cfg)
        self.assertEqual(code, 1)
        self.assertNotIn(": ok", out.getvalue())

    def test_a_suffix_past_the_slots_is_only_a_warning(self):
        self.write("water.md", WATER)
        self.write("water-3.md", GRATITUDE.replace("id: gratitude", "id: water-3"))
        lib = library.load(self.cfg)
        self.assertIn("water-3", lib.practices)
        self.assertTrue(any("ids 'water' and 'water-3' are both defined" in p for p in lib.problems))

    def test_the_repeated_ids_of_notes_and_analyses_are_reserved(self):
        for pid in ("j-2", "note-3", "reflection-2", "week-2"):
            with self.subTest(pid):
                path = self.write(f"{pid}.md", GRATITUDE.replace("id: gratitude", f"id: {pid}"))
                self.assertNotIn(pid, library.load(self.cfg).practices)
                self.assertIn(f"id {pid!r} is reserved", " ".join(library.validate_file(path, self.cfg)))
                path.unlink()


class SendRetryTest(Base):
    def test_the_plain_retry_drops_what_the_model_s_opener_wove_in(self):
        asked = [{"id": "1.1", "question": "What gave you energy today?"}]
        cites = [{"claim": "rain", "source": "weather"}]
        calls = []

        def flaky(cfg, chat_id, text, *a, **k):
            calls.append(text)
            if len(calls) == 1:
                raise telegram.TelegramError("Bad Request: message is too long")
            return "800"
        log = []
        with mock.patch.object(tick, "_opener", lambda *a: ("A woven opener.", "ada", cites, asked)), \
                mock.patch.object(telegram, "send_html", flaky):
            self.now = at(23, 1)
            tick.send_due(self.cfg, at(23, 1), log, only="evening")
        p = self.prompt("2026-10-14-evening")
        self.assertEqual((p["opener_source"], p["asked"], p["citations"]), ("plain:send-retry", [], []))
        self.assertNotIn("A woven opener.", p["plain_text"])
        self.assertIn("sent 2026-10-14-evening (opener: plain:send-retry, wove 0 questions)", log)
        info = capture.capture(self.cfg, CHAT, "800", "fine", at(23, 10).astimezone(timezone.utc), "text", "1")
        self.assertNotIn("Woven in", Path(info["path"]).read_text())


class StartCommandTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.practices = t / "practices"
        conf = write_config(t / "journal.json", {
            "root": str(t / "journal"), "state_dir": str(t / "state"), "practices_dir": str(self.practices),
            "telegram": {"chat_id": CHAT}, "user": {"name": "Sam"}, "companion": {"name": "Ada", "emoji": "🌿"}})
        os.environ["JOURNAL_CONFIG"] = str(conf)
        (t / "journal").mkdir()
        self.spawned = []
        p = mock.patch.object(subprocess, "Popen", lambda args, **kw: self.spawned.append(args))
        p.start()
        self.addCleanup(p.stop)

    def event(self, text, chat=CHAT):
        return SimpleNamespace(text=text, message_type=SimpleNamespace(name="TEXT"), message_id="9",
                               reply_to_message_id=None, media_urls=[], media_types=[], raw_message=None,
                               source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id=chat,
                                                      user_id=chat))

    def test_a_practice_added_after_registration_starts_with_no_restart(self):
        handlers = {}

        class Ctx:
            def register_command(self, name, handler, description=""):
                handlers[name] = handler
        commands.register(Ctx())
        self.practices.mkdir()
        (self.practices / "gratitude.md").write_text(GRATITUDE, encoding="utf-8")
        self.assertEqual(handlers["journal-start"]("gratitude"), "🌿 Your gratitude journal is on its way.")
        self.assertEqual(self.spawned[-1][-4:], ["-m", "journal", "send", "gratitude"])
        self.assertEqual(handlers["journal-start"]("Evening-Journal"), "🌿 Your evening journal is on its way.")
        self.assertEqual(self.spawned[-1][-1], "evening")
        self.assertEqual(handlers["evening"](""), "🌿 Your evening journal is on its way.")  # the ritual alias

    def test_an_unknown_id_answers_with_the_library(self):
        reply = commands.start("nope")
        # WP4: the list names the programs and situational definitions after the practices (deliberate change)
        self.assertEqual(reply, "There's no practice called “nope”. You have: /start morning (Morning journal), "
                                "/start evening (Evening journal), /start midweek (Midweek check-in), "
                                "/start weekly (Weekly pattern scan), /start fix-life (Fix your entire life in 1 day), "
                                "/start prep (Conversation prep), /start debrief (Post-session debrief).")
        self.assertEqual(self.spawned, [])
        self.assertTrue(commands.start("").startswith("Which practice? You have: /start morning"))

    def test_the_hook_routes_start_with_an_id_and_leaves_the_bare_ping_to_hermes(self):
        self.assertEqual(hooks.on_dispatch(event=self.event("/start gratitude")),
                         {"action": "rewrite", "text": "/journal-start gratitude"})
        self.assertEqual(hooks.on_dispatch(event=self.event("/start@SomeBot evening")),
                         {"action": "rewrite", "text": "/journal-start evening"})
        self.assertIsNone(hooks.on_dispatch(event=self.event("/start")))  # Telegram's start ping: Hermes' own
        self.assertIsNone(hooks.on_dispatch(event=self.event("/start gratitude", chat="123")))  # not the journal's chat
        self.assertIsNone(hooks.on_dispatch(event=self.event("/startle me")))
        self.assertEqual(list(Path(self.tmp.name, "journal").iterdir()), [])  # a command is never captured

    def test_unconfigured_start_explains(self):
        os.environ["JOURNAL_CONFIG"] = "/nonexistent/journal.json"
        self.assertIn("isn't configured", commands.start("gratitude"))

    def test_start_and_the_new_commands_are_not_an_opt_out(self):
        for name in ("/start", "/next", "/skip", "/back", "/pause", "/status", "/journal_start", "/morning"):
            with self.subTest(name):
                self.assertTrue(config.opt_out_problem(name))
        self.assertEqual(config.opt_out_problem("/x"), "")

    def test_the_opt_out_verdict_does_not_depend_on_what_the_process_has_imported(self):
        import sys
        import types
        fake = types.ModuleType("hermes_cli.commands")
        fake.COMMAND_REGISTRY = [types.SimpleNamespace(name="offrec", aliases=())]
        with mock.patch.dict(sys.modules, {"hermes_cli.commands": fake}):
            self.assertEqual(config.opt_out_problem("/offrec"), "")


class ClosesTest(Base):
    def answered(self, pid, words, when, annotate=True):
        kind = pid.split("-", 3)[3]
        sent = (when - timedelta(minutes=10)).astimezone(timezone.utc)
        mid = str(next(self.ids))
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": pid, "kind": kind, "local_date": pid[:10], "chat_id": CHAT,
                                    "message_id": mid, "sent_at": sent.isoformat(), "opener": "x",
                                    "opener_source": "ada"})
        info = capture.capture(self.cfg, CHAT, mid, words, when.astimezone(timezone.utc), "text", str(next(self.ids)))
        if annotate:
            entries.write_annotations(Path(info["path"]), "- noted.", self.cfg.names)
            with ledger.locked(self.cfg.ledger_path) as data:
                ledger.find(data, pid)["annotated_at"] = when.isoformat()

    def test_the_day_closes_on_a_renamed_evening_in_a_fork(self):
        fork = library.fork(self.cfg, "evening")
        fork.write_text(fork.read_text().replace("---\n## 1.", "enabled: false\n---\n## 1.", 1), encoding="utf-8")
        self.write("pages.md", GRATITUDE.replace("id: gratitude", "id: pages").replace("21:30", "23:30")
                   .replace("title: Daily gratitude", "title: Night pages\ncloses: day"))
        self.assertEqual(library.closer(self.cfg, "day").id, "pages")
        self.answered("2026-10-14-pages", "rain all day, walked anyway", at(23, 40))
        due = reflect.due(self.cfg, ledger.snapshot(self.cfg.ledger_path), at(23, 45))
        self.assertIn(("reflection", WED.date(), "annotated"), due)
        rows = {n: d for n, s, d in status.doctor(self.cfg, token=False)}
        self.assertEqual(rows["day reflection"], "after the pages entry is annotated, fallback 04:00 the next day")

    def test_with_nothing_closing_the_day_only_the_fallback_writes_it(self):
        fork = library.fork(self.cfg, "evening")
        fork.write_text(fork.read_text().replace("closes: day\n", ""), encoding="utf-8")
        self.assertIsNone(library.closer(self.cfg, "day"))
        self.answered("2026-10-14-evening", "a long day", at(23, 10))
        data = ledger.snapshot(self.cfg.ledger_path)
        self.assertEqual(reflect.due(self.cfg, data, at(23, 45)), [])
        self.assertIn(("reflection", WED.date(), "fallback"), reflect.due(self.cfg, data, at(4, 5, WED + timedelta(days=1))))

    def test_the_week_ends_on_the_closing_practice_s_weekday(self):
        self.assertEqual(reflect.week_end_day(self.cfg), 6)
        fork = library.fork(self.cfg, "weekly")
        fork.write_text(fork.read_text().replace("schedule.weekday: 6", "schedule.weekday: 5"), encoding="utf-8")
        self.assertEqual(reflect.week_end_day(self.cfg), 5)


class ComposeByPracticeTest(unittest.TestCase):
    """The Practice path and the id path are the same bytes for the four rituals (the goldens hold the id path)."""

    def test_practice_and_id_compose_the_same(self):
        when = datetime(2026, 10, 14, 23, 0, tzinfo=IST)
        for pid, p in library.builtins().items():
            with self.subTest(pid):
                self.assertEqual(compose.telegram_html(p, when, "x", names=NAMES),
                                 compose.telegram_html(pid, when, "x", names=NAMES))
                self.assertEqual(compose.plain_text(p, when, "x", NAMES), compose.plain_text(pid, when, "x", NAMES))
                self.assertEqual(compose.plain_opener(p, NAMES), compose.plain_opener(pid, NAMES))
                self.assertEqual(validate.check(p, {"opener": "a" * p.opener["limit"]}, ""), (True, "ok"))
                self.assertFalse(validate.check(p, {"opener": "a" * (p.opener["limit"] + 1)}, "")[0])

    def test_a_practice_emoji_leads_its_header(self):
        p = library.parse(WATER)
        self.assertEqual(compose.header(p, datetime(2026, 10, 14, 9, 0), names=NAMES), "💧 Water · Wed 14 Oct")
        self.assertEqual(compose.header(library.parse(GRATITUDE), datetime(2026, 10, 14, 9, 0), names=NAMES),
                         "🌿 Daily gratitude · Wed 14 Oct")

    def test_the_opener_request_uses_the_practice_s_shape(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t), names=NAMES)
            p = library.parse(GRATITUDE)
            q = prompts.opener_request(cfg, p, datetime(2026, 10, 14, 21, 30, tzinfo=IST), "PACK", p.bank_with_ids())
        self.assertIn("Write a short opener (max ~900 characters) that would make", q)
        self.assertIn("[2] Who made one of them possible?", q)


class SkillKindsTest(CleanEnv):
    def test_the_kinds_section_lists_the_library_with_each_companion_line(self):
        from journal import hermes
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t), names=NAMES)
            d = Path(cfg.practices_dir)
            d.mkdir(parents=True)
            (d / "water.md").write_text(WATER, encoding="utf-8")
            path = hermes.render_skill(Path(t) / "skill", hermes.skill_tokens(NAMES, cfg))
            text = path.read_text()
        self.assertNotIn("{{", text)
        kinds = text.split("## Kinds\n", 1)[1].split("\n## ", 1)[0]
        self.assertEqual(kinds.strip().splitlines()[2:], [
            "- **Morning journal** (`/start morning`): orientation — where they are, what matters today.",
            "- **Water** (`/start water`): a nudge, no more.",
            "- **Evening journal** (`/start evening`): closure — process the day, release, carry forward one thread.",
            "- **Midweek check-in** (`/start midweek`): a two-minute pulse — energy, drift, support; offer at most one "
            "nudge.",
            "- **Weekly pattern scan** (`/start weekly`): patterns across the week — celebrate wins as well as gaps; "
            "one or two nudges at most."])
        self.assertIn("or now with `/start <id>`", text)


if __name__ == "__main__":
    unittest.main()
