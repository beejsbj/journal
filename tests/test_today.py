"""The today block (today.py) and the turn context budget (turncontext.py)."""
import json
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CHAT, CleanEnv, add_prompt, make_cfg, write_config

from journal import capture, hookstate, ledger, notebook, reflect, sources, status, tick, today, turncontext
from journal.hermes import hooks

IST = ZoneInfo("Asia/Kolkata")
FIXTURES = Path(__file__).resolve().parent / "fixtures"
SECRET = "SECRET-FROM-THE-CHAT-TRANSCRIPT"


def at(h, m=0, day=15):
    return datetime(2026, 10, day, h, m, tzinfo=IST).astimezone(timezone.utc)


def turn(cfg, when, text="hello", platform="telegram"):
    return turncontext.Turn(platform=platform, session_id="s1", turn_id="t", user_message=text, at=when)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.sources["weather"] = {"enabled": False}

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, when, text="hello", platform="telegram"):
        return turncontext.build(self.cfg, turn(self.cfg, when, text, platform))


class ThreadStartTest(Base):
    def test_first_message_gets_the_block_and_the_next_does_not(self):
        first = self.build(at(9, 0))
        self.assertIn("## Journal: today — Thu 15 Oct 2026, 09:00 IST", first)
        self.assertIsNone(self.build(at(9, 5)))

    def test_44_minutes_is_the_same_thread_46_is_a_new_one(self):
        self.build(at(9, 0))
        self.assertIsNone(self.build(at(9, 44)))      # 44 since his last message
        self.assertIsNone(self.build(at(10, 28)))     # 44 again, from the 09:44 message
        self.assertIn("## Journal: today", self.build(at(11, 14)))  # 46 since 10:28

    def test_quiet_minutes_is_shared_with_conversations(self):
        self.cfg.conversations["quiet_minutes"] = 10
        self.build(at(9, 0))
        self.assertIn("## Journal: today", self.build(at(9, 11)))

    def test_a_prompt_send_between_two_messages_starts_a_thread(self):
        self.build(at(9, 0))
        today.note_send(self.cfg, at(9, 10))
        self.assertIn("## Journal: today", self.build(at(9, 12)))
        self.assertIsNone(self.build(at(9, 13)))

    def test_a_send_before_his_last_message_does_not(self):
        today.note_send(self.cfg, at(8, 0))
        self.build(at(9, 0))
        self.assertIsNone(self.build(at(9, 5)))

    def test_the_tick_stamps_a_send(self):
        record = {"id": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15", "scheduled": "x",
                  "chat_id": CHAT, "html": "h", "plain_text": "p"}
        with mock.patch("journal.telegram.send_html", return_value="500"), \
                mock.patch("journal.mirror.to_chat"):
            self.assertTrue(tick.send_prompt(self.cfg, record, []))
        self.assertGreater(hookstate.stamps(self.cfg)["last_send"], 0)

    def test_not_from_a_cli_or_cron_turn(self):
        self.assertIsNone(self.build(at(9, 0), platform="cli"))
        self.assertEqual(hookstate.stamps(self.cfg), {})   # and it did not spend the thread start either
        self.assertIn("## Journal: today", self.build(at(9, 1)))

    def test_off_switch(self):
        self.cfg.today["enabled"] = False
        self.assertIsNone(self.build(at(9, 0)))

    def test_status_counts_blocks(self):
        self.build(at(9, 0))
        self.build(at(11, 0))
        s = status.status(self.cfg, at(11, 30))
        self.assertEqual((s["today"]["count"], s["today"]["last"]), (2, "11:00"))
        self.assertIn("Today block: 2 blocks today, last 11:00", status.format_status(s))


class DayChangeTest(Base):
    def test_a_chat_that_runs_past_midnight_meets_the_new_day(self):
        for day in (15, 16):
            (self.cfg.root / f"2026-10-{day}-j.md").write_text(
                "---\ntype: journal\nkind: j\n---\n# Note\n\n## Theo\n\nsomething on the day\n", encoding="utf-8")
        self.assertIn("Thu 15 Oct", self.build(at(23, 30)))
        self.assertIsNone(self.build(at(23, 50)))
        self.assertIn("Fri 16 Oct", self.build(at(0, 10, day=16)))
        self.assertIsNone(self.build(at(0, 40, day=16)))

    def test_a_start_that_found_nothing_to_say_is_not_spent(self):
        self.cfg.root.mkdir(parents=True, exist_ok=True)
        with mock.patch.object(today, "render", return_value=""):
            self.assertIsNone(self.build(at(9, 0)))
        self.assertIn("## Journal: today", self.build(at(9, 5)))
        self.assertIsNone(self.build(at(9, 10)))


class BudgetTest(Base):
    def setUp(self):
        super().setUp()
        notebook.add(self.cfg, "ask about the lake")

    def test_a_pack_turn_gets_no_today_and_the_next_thread_start_does(self):
        hookstate.remember_pack(self.cfg, "[NOTE-1]", "PACK BLOCK")
        out = self.build(at(9, 0), text="[NOTE-1] rainy")
        self.assertIn("PACK BLOCK", out)
        self.assertIn("ask about the lake", out)
        self.assertNotIn("## Journal: today", out)
        self.assertEqual(out.count("\n\n"), 1)                    # two blocks: notebook + pack
        self.assertNotIn("## Journal: today", self.build(at(9, 10)) or "")   # same thread: not given late
        self.assertIn("## Journal: today", self.build(at(10, 30)))           # next thread start

    def test_notebook_plus_today_is_two_blocks(self):
        out = self.build(at(9, 0))
        self.assertIn("ask about the lake", out)
        self.assertIn("## Journal: today", out)
        self.assertEqual(out.count("\n\n"), 1)
        self.assertEqual(turncontext.BUDGET, {"blocks": 2, "today_with_pack": False})

    def test_never_more_than_the_budget(self):
        with mock.patch.object(turncontext, "_pack", return_value="PACK"), \
                mock.patch.object(turncontext, "_today", return_value="TODAY"):
            out = self.build(at(9, 0))
        self.assertEqual(out.split("\n\n")[1:], ["PACK"])
        with mock.patch.dict(turncontext.BUDGET, {"today_with_pack": True}), \
                mock.patch.object(turncontext, "_pack", return_value="PACK"), \
                mock.patch.object(turncontext, "_today", return_value="TODAY"):
            self.assertEqual(len(self.build(at(9, 0)).split("\n\n")), 2)   # notebook + pack; today cut by the budget

    def test_an_exception_builds_no_block_and_costs_nothing_else(self):
        with mock.patch.object(today, "render", side_effect=RuntimeError("boom")):
            out = self.build(at(9, 0))
        self.assertIn("ask about the lake", out)
        self.assertNotIn("Journal: today", out)

    def test_a_notebook_failure_costs_only_the_notebook(self):
        with mock.patch.object(notebook, "render", side_effect=OSError("gone")):
            self.assertIn("## Journal: today", self.build(at(9, 0)))


class CollectTest(Base):
    def setUp(self):
        super().setUp()
        self.cfg.location = "Hillside"
        self.cfg.sources["tududi"] = {"enabled": True, "command": ["x"]}

    def answered_morning(self):
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=at(6, 0), message_id="11")
        capture.capture(self.cfg, CHAT, "11", "slept badly, but the light was good", at(6, 41), "text", "12")

    def test_practices_entries_and_the_rest(self):
        self.answered_morning()
        data = today.collect(self.cfg, at(9, 12))
        rows = {r["id"]: r for r in data["practices"]["rows"]}
        self.assertEqual((rows["morning"]["state"], rows["morning"]["sent"], rows["morning"]["answered"]),
                         ("answered", "06:00", "06:41"))
        self.assertEqual(rows["evening"], {"id": "evening", "state": "upcoming", "at": "23:00"})
        self.assertNotIn("weekly", rows)   # Sundays only
        self.assertEqual(data["entries"], [{"stem": "2026-10-15-morning",
                                            "first_line": "slept badly, but the light was good"}])
        text = today.render(self.cfg, at(9, 12))
        self.assertIn("- practices: morning sent 06:00, answered 06:41 · evening at 23:00", text)
        self.assertIn('- entries today: 2026-10-15-morning ("slept badly, but the light was good")', text)
        self.assertTrue(text.endswith(text.rstrip()))

    def test_a_voice_reply_shows_his_words_not_the_audio_embed(self):
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=at(6, 0), message_id="11")
        capture.capture_to(self.cfg, "2026-10-15-morning", "reply", "slept badly but the light was good", at(6, 41),
                           "voice", "12", audio="2026-10-15-morning-0641.ogg", meta={"duration": 12})
        first = today.collect(self.cfg, at(9, 12))["entries"][0]["first_line"]
        self.assertEqual(first, "slept badly but the light was good")

    def test_engine_lines_are_never_the_first_line(self):
        for line in ("**14:02** · voice transcript · 0:12", "![[a.ogg]]", "_(audio file — transcript pending)_",
                     "_(audio file — not transcribed)_", "(voice note — no transcript was available)",
                     '**Tue 14 Oct 21:10** · audio file "w.m4a" · 1:02', "**14:02** —", "", "# Heading"):
            self.assertFalse(today._is_his_line(line), line)
        for line in ("real words", "**bold** start of his sentence", "**14:02** — hello", "- a list"):
            self.assertTrue(today._is_his_line(line), line)

    def test_open_and_missed_prompts(self):
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=at(2, 0), message_id="11")
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=at(9, 0), message_id="12")
        rows = {r["id"]: r["state"] for r in today.collect(self.cfg, at(9, 12))["practices"]["rows"]}
        self.assertEqual(rows, {"morning": "missed", "evening": "open"})
        text = today.render(self.cfg, at(9, 12))
        self.assertIn("morning sent 02:00 (missed, reply anytime)", text)
        self.assertIn("- open prompt awaiting a reply: evening", text)

    def test_reflection_question(self):
        body = "**What stood out** — x.\n\n**To carry forward** — What would it take? And what else?"
        (self.cfg.root / "2026-10-14-reflection.md").write_text(
            reflect.render(self.cfg, "reflection", datetime(2026, 10, 14).date(), body, [], at(4)), encoding="utf-8")
        data = today.collect(self.cfg, at(9, 12))
        self.assertEqual(data["reflection"], {"date": "2026-10-14", "asked": "What would it take?"})
        self.assertIn("- yesterday's reflection asked: \"What would it take?\"", today.render(self.cfg, at(9, 12)))

    def test_stems_name_each_slots_entry_and_the_reflection_file(self):
        from types import SimpleNamespace
        from journal import library
        slot = lambda t: SimpleNamespace(time=t, weekday=None)   # noqa: E731
        two = SimpleNamespace(id="evening", schedule=[slot("08:00"), slot("09:00"), slot("23:00")])
        for n, hour in ((1, 8), (2, 9)):
            p = add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=at(hour, 0), message_id=str(n))
            with ledger.locked(self.cfg.ledger_path) as data:
                data["prompts"][-1]["id"] = "2026-10-15-evening" + ("" if n == 1 else f"-{n}")
        (self.cfg.root / "2026-10-14-reflection.md").write_text("hand-written\n", encoding="utf-8")
        body = "**What stood out** — x.\n\n**To carry forward** — What would it take?"
        (self.cfg.root / "2026-10-14-reflection-2.md").write_text(
            reflect.render(self.cfg, "reflection", datetime(2026, 10, 14).date(), body, [], at(4)), encoding="utf-8")
        with mock.patch.object(library, "scheduled", return_value=[two]):
            plain = today.collect(self.cfg, at(9, 12))
            named = today.collect(self.cfg, at(9, 12), stems=True)
        rows = [(r["id"], r["state"], r.get("stem")) for r in named["practices"]["rows"]]
        self.assertEqual(rows, [("evening", "open", "2026-10-15-evening"), ("evening", "open", "2026-10-15-evening-2"),
                                ("evening", "upcoming", None)])
        self.assertEqual(named["reflection"]["stem"], "2026-10-14-reflection-2")
        for r in named["practices"]["rows"]:
            r.pop("stem", None)
        named["reflection"].pop("stem")
        self.assertEqual(named, plain)    # the block's own dict is what it was

    def test_cache_lines_and_staleness(self):
        today._write_cache(self.cfg, {
            "at": at(9, 0).isoformat(),
            "tasks": {"date": "2026-10-15", "count": 3, "names": ["Call the bank", "Draft the quarterly report", "Walk"],
                      "at": at(9, 0).isoformat()},
            "weather": {"text": "14°C, light rain", "at": at(9, 0).isoformat()}})
        text = today.render(self.cfg, at(9, 12))
        self.assertIn("- tasks due today: 3 — Call the bank; Draft the quarterly report; Walk", text)
        self.assertIn("- weather: 14°C, light rain", text)
        later = today.render(self.cfg, at(13, 30))   # forecast 4.5 h old
        self.assertNotIn("weather", later)
        self.assertIn("tasks due today", later)
        self.assertNotIn("tasks due today", today.render(self.cfg, at(9, 12, day=16)))   # yesterday's list

    def test_a_failing_collector_drops_its_line_only(self):
        self.answered_morning()
        with mock.patch.object(today, "_entries", side_effect=RuntimeError("boom")):
            data = today.collect(self.cfg, at(9, 12))
        self.assertEqual(data["entries"], [])
        self.assertIn("- practices:", today.render_data(data))
        for name in ("_practices", "_reflection", "_tasks", "_weather"):
            with mock.patch.object(today, name, side_effect=RuntimeError("boom")):
                today.render(self.cfg, at(9, 12))   # never raises

    def test_cap_is_enforced_from_the_bottom(self):
        today._write_cache(self.cfg, {"tasks": {"date": "2026-10-15", "count": 3, "names": ["a", "b", "c"],
                                                "at": at(9).isoformat()},
                                      "weather": {"text": "14°C", "at": at(9).isoformat()}})
        self.answered_morning()
        full = today.render(self.cfg, at(9, 12))
        self.assertIn("weather", full)
        self.cfg.today["max_chars"] = len(full) - 1
        cut = today.render(self.cfg, at(9, 12))
        self.assertLessEqual(len(cut), len(full) - 1)
        self.assertNotIn("weather", cut)
        self.assertTrue(full.startswith(cut))             # whole lines gone from the bottom, nothing mid-line
        self.cfg.today["max_chars"] = 10                  # below the floor of 200: the floor applies
        self.assertLessEqual(len(today.render(self.cfg, at(9, 12))), 200)

    def test_the_default_cap_holds_for_long_entries(self):
        self.answered_morning()
        for i in range(6):
            (self.cfg.root / f"2026-10-15-j-{i}.md").write_text(
                "---\ntype: journal\nkind: j\n---\n# Note\n\n## Theo\n\n" + "word " * 200 + "\n", encoding="utf-8")
        self.assertLessEqual(len(today.render(self.cfg, at(9, 12))), 1200)

    def test_sources_setting(self):
        self.answered_morning()
        self.cfg.today["sources"] = ["entries"]
        text = today.render(self.cfg, at(9, 12))
        self.assertIn("entries today", text)
        self.assertNotIn("- practices", text)

    def test_empty_day_is_no_block(self):
        self.cfg.today["sources"] = ["entries"]
        self.assertEqual(today.render(self.cfg, at(9, 12)), "")
        self.assertIsNone(self.build(at(9, 12)))


class NoSourcesNoTranscriptTest(Base):
    def test_sources_and_the_sessions_database_are_never_touched(self):
        db = Path(self.tmp.name) / "state.db"
        con = sqlite3.connect(db)
        con.executescript("create table sessions (id text, source text, chat_id text);"
                          "create table messages (session_id text, role text, timestamp real, content text);")
        con.execute("insert into sessions values ('s1', 'telegram', ?)", (CHAT,))
        con.execute("insert into messages values ('s1', 'user', ?, ?)", (at(8).timestamp(), SECRET))
        con.commit()
        con.close()
        self.cfg.hermes_home = Path(self.tmp.name)
        self.cfg.sources["tududi"] = {"enabled": True, "command": ["x"]}
        self.cfg.sources["weather"] = {"enabled": True}
        self.cfg.coordinates = (1.0, 2.0)
        boom = mock.Mock(side_effect=AssertionError("a source or the database was touched in the hook"))
        with mock.patch.object(sources, "tududi_due", boom), mock.patch.object(sources, "weather", boom), \
                mock.patch.object(sources, "_mcp_call", boom), mock.patch.object(sources, "_curl", boom), \
                mock.patch("sqlite3.connect", boom), mock.patch("subprocess.run", boom):
            out = self.build(at(9, 0))
        self.assertIn("## Journal: today", out)
        self.assertNotIn(SECRET, out)
        boom.assert_not_called()


class CacheStepTest(Base):
    TASKS = {"tasks": [
        {"name": "Call the bank", "due_date": "2026-10-15", "Project": {"name": "Home"}},
        {"name": "Overdue thing", "due_date": "2026-10-14T00:00:00Z"},
        {"name": "Later", "due_date": "2026-10-20"}, {"name": "Someday"},
        {"name": "Done", "due_date": "2026-10-14", "completed_at": "2026-10-14T08:00:00Z"},
        {"name": "Old done", "due_date": "2026-09-01", "completed_at": "2026-09-02T08:00:00Z"},
        {"name": "Habit", "due_date": "2026-10-14", "habit_mode": True}]}

    WEATHER = ["- weather:now (2026-10-15T09:00) — light rain, 14.2°C (feels 13°C)",
               "- weather:today (2026-10-15) — rain, 11.0–18.5°C, rain chance 70%, sunrise 06:12, sunset 17:50",
               "- weather:tomorrow (2026-10-16) — clear, 9.0–17.0°C, rain chance 0%, sunrise 06:13, sunset 17:49"]

    def setUp(self):
        super().setUp()
        self.cfg.sources["tududi"] = {"enabled": True, "command": ["x"]}
        self.cfg.sources["weather"] = {"enabled": True}
        self.cfg.coordinates = (1.0, 2.0)

    def run_step(self, when, log=None, tududi=None, weather=None, tasks=None):
        with mock.patch.object(sources, "tududi_due", tududi) if tududi else \
                mock.patch.object(sources, "_mcp_call", mock.Mock(return_value=tasks or self.TASKS)), \
                mock.patch.object(sources, "weather", weather or mock.Mock(return_value=self.WEATHER)):
            return today.refresh(self.cfg, when, [] if log is None else log)

    def test_writes_tasks_and_weather(self):
        self.assertTrue(self.run_step(at(9, 0)))
        cache = today.read_cache(self.cfg)
        self.assertEqual(cache["tasks"]["names"], ["Call the bank", "Overdue thing"])
        self.assertEqual(cache["tasks"]["count"], 2)
        self.assertEqual(cache["weather"]["text"], "14.2°C, light rain; today 11.0–18.5°C, rain chance 70%")
        self.assertEqual(oct(os.stat(today.cache_path(self.cfg)).st_mode & 0o777), "0o600")

    def test_due_tasks_past_the_display_cut_still_count(self):
        many = {"tasks": [{"name": f"open {i}"} for i in range(35)]
                + [{"name": f"due {i}", "due_date": "2026-10-15"} for i in range(4)]}
        self.run_step(at(9, 0), tasks=many)
        cache = today.read_cache(self.cfg)
        self.assertEqual((cache["tasks"]["count"], cache["tasks"]["names"][0]), (4, "due 0"))

    def test_not_again_before_cache_minutes(self):
        self.assertTrue(self.run_step(at(9, 0)))
        tududi = mock.Mock(return_value=[])
        self.assertFalse(self.run_step(at(9, 20), tududi=tududi))
        tududi.assert_not_called()
        self.assertTrue(self.run_step(at(9, 30)))          # a tick a few seconds early still counts
        self.cfg.today["cache_minutes"] = 5
        self.assertTrue(self.run_step(at(9, 36)))

    def test_one_source_failing_keeps_the_other_and_the_old_value(self):
        self.run_step(at(9, 0))
        log = []
        self.run_step(at(10, 0), log=log, tududi=mock.Mock(side_effect=RuntimeError("down")),
                      weather=mock.Mock(return_value=["- weather:now (t) — clear, 20.0°C (feels 20°C)"]))
        cache = today.read_cache(self.cfg)
        self.assertEqual(cache["tasks"]["count"], 2)                 # kept
        self.assertEqual(cache["weather"]["text"], "20.0°C, clear")  # refreshed
        self.assertTrue(any("tasks unavailable" in l for l in log))

    def test_sources_off_or_unset_do_nothing(self):
        self.cfg.sources["tududi"] = {"enabled": False}
        self.cfg.coordinates = None
        self.assertFalse(self.run_step(at(9, 0)))
        self.assertFalse(today.cache_path(self.cfg).exists())

    def test_the_tick_has_a_cache_step(self):
        cfg = self.cfg
        with mock.patch.object(today, "refresh") as refresh, mock.patch.object(tick, "send_due"):
            code, _ = tick.run(cfg)
        refresh.assert_called_once()


class FixtureTest(Base):
    """``collect`` for a fixed day equals tests/fixtures/today.json, which WP9's ``GET /today`` serialises too;
    set JOURNAL_WRITE_FIXTURES=1 to regenerate it after a deliberate change."""

    def setUp(self):
        super().setUp()
        self.cfg.location = "Hillside"
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=at(6), message_id="11")
        capture.capture(self.cfg, CHAT, "11", "slept badly, but the light was good", at(6, 41), "text", "12")
        (self.cfg.root / "2026-10-15-j.md").write_text(
            "---\ntype: journal\nkind: j\n---\n# Note\n\n## Theo\n\ncall the dentist re Thursday\n", encoding="utf-8")
        body = "**What stood out** — x.\n\n**To carry forward** — What would it take to let the Tuesday feeling in?"
        (self.cfg.root / "2026-10-14-reflection.md").write_text(
            reflect.render(self.cfg, "reflection", datetime(2026, 10, 14).date(), body, [], at(4)), encoding="utf-8")
        today._write_cache(self.cfg, {
            "at": at(9).isoformat(),
            "tasks": {"date": "2026-10-15", "count": 3, "names": ["Call the bank", "Draft the quarterly report", "Walk"],
                      "at": at(9).isoformat()},
            "weather": {"text": "14°C, light rain", "at": at(9).isoformat()}})
        # WP4: a fix-life run at session 3, phase 2 sent on Sunday evening and not answered yet
        run = {"program": "fix-life", "id": "2026-10-11-a1f3", "status": "active", "started_at": at(20, 0, 11).isoformat(),
               "last_activity_at": at(21, 14, 11).isoformat(), "current": {"session": 3, "phase": 2},
               "sessions": {"3": {"status": "in_progress", "entry": "2026-10-11-fix-life-s03",
                                  "phases": {"2": {"prompt_id": "2026-10-11-a1f3-s03-p2",
                                                   "sent_at": at(21, 14, 11).isoformat()}}}}}
        runs = self.cfg.state_dir / "runs" / "fix-life"
        runs.mkdir(parents=True)
        (runs / "2026-10-11-a1f3.json").write_text(json.dumps(run), encoding="utf-8")

    def test_collect_and_render_match_the_shared_fixture(self):
        data = today.collect(self.cfg, at(9, 12))  # what GET /today serialises: no ``standing`` (CONTEXT-PACK)
        text = today.render(self.cfg, at(9, 12))
        block = today.collect(self.cfg, at(9, 12), with_standing=True)  # what the block renders
        json.dumps(data)   # plain JSON
        json.dumps(block)
        if os.environ.get("JOURNAL_WRITE_FIXTURES"):
            FIXTURES.mkdir(exist_ok=True)
            (FIXTURES / "today.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            (FIXTURES / "today.txt").write_text(text + "\n", encoding="utf-8")
        self.assertEqual(data, json.loads((FIXTURES / "today.json").read_text(encoding="utf-8")))
        self.assertEqual(text + "\n", (FIXTURES / "today.txt").read_text(encoding="utf-8"))
        self.assertEqual(today.render_data(block), text)   # the block is a pure function of the dict
        self.assertEqual({k: v for k, v in block.items() if k != "standing"}, data)


class HookTest(CleanEnv):
    """The gateway hook end to end: ``on_llm_call`` returns the block in its context."""

    def test_on_llm_call_carries_the_block_once_per_thread(self):
        """The hook's clock is pinned to a quiet hour (14:00 IST, a Monday): on the real clock this failed every day
        from 23:00 to 23:59 IST, the evening ritual's hour, where the hook behaves differently."""
        class Quiet(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 26, 8, 30, tzinfo=timezone.utc)

        with tempfile.TemporaryDirectory() as t, mock.patch.object(hooks, "datetime", Quiet):
            conf = write_config(Path(t) / "j.json", {"root": t, "state_dir": str(Path(t) / "state"),
                                                     "timezone": "Asia/Kolkata", "telegram": {"chat_id": CHAT}})
            os.environ["JOURNAL_CONFIG"] = str(conf)
            first = hooks.on_llm_call(user_message="hi", platform="telegram", session_id="s", turn_id="1")
            self.assertIn("## Journal: today", first["context"])
            self.assertIsNone(hooks.on_llm_call(user_message="again", platform="telegram", session_id="s",
                                                turn_id="2"))
            self.assertIsNone(hooks.on_llm_call(user_message="cron", platform="cron"))


if __name__ == "__main__":
    unittest.main()
