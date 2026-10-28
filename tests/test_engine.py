"""The engine's behaviour, ported from the original suite."""
import json
import os
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import PERSON, CHAT, CleanEnv, add_prompt, guard_cfg, make_cfg, write_config

from journal import (capture, compose, context, entries, guard, ledger, library, location, schedule, telegram, tick,
                     validate)
from journal import inbound
from journal.config import Config, load
from journal.hermes import commands, hooks

IST = ZoneInfo("Asia/Kolkata")


class ConfigTest(CleanEnv):
    def test_load_defaults_and_overrides(self):
        with tempfile.TemporaryDirectory() as t:
            p = write_config(Path(t) / "journal.json", {"version": 1, "root": t, "timezone": "America/Chicago",
                                                        "schedule": {"morning": {"time": "07:30"}}})
            cfg = load(p)
            # WP13: the times live in the practice files; a `schedule` key is not read (and is named in the warnings)
            self.assertFalse(hasattr(cfg, "schedule"))
            self.assertEqual(library.get(cfg, "evening").schedule, (library.Slot("23:00"),))
            self.assertEqual(library.get(cfg, "morning").schedule, (library.Slot("06:00"),))
            self.assertTrue(any("`schedule` in the config is no longer read" in w for w in cfg.warnings))
            self.assertEqual(str(cfg.tz), "America/Chicago")


class ScheduleTest(unittest.TestCase):
    def setUp(self):
        self.cfg = Config(root=Path("/tmp/x"))

    def test_morning_due_in_local_time_once(self):
        now = datetime(2026, 10, 14, 6, 3, tzinfo=IST)
        self.assertEqual([k for k, _ in schedule.due(self.cfg, now, set())], ["morning"])
        self.assertEqual(schedule.due(self.cfg, now, {"2026-10-14-morning"}), [])

    def test_outside_grace_not_due(self):
        self.assertEqual(schedule.due(self.cfg, datetime(2026, 10, 14, 8, 30, tzinfo=IST), set()), [])

    def test_weekly_only_on_sunday(self):
        sun = datetime(2026, 10, 18, 20, 5, tzinfo=IST)
        mon = datetime(2026, 10, 19, 20, 5, tzinfo=IST)
        self.assertIn("weekly", [k for k, _ in schedule.due(self.cfg, sun, set())])
        self.assertNotIn("weekly", [k for k, _ in schedule.due(self.cfg, mon, set())])


class ComposeTest(unittest.TestCase):
    def test_bank_folded_and_escaped(self):
        html = compose.telegram_html("morning", datetime(2026, 10, 14, 6, 0, tzinfo=IST), "Tea & <toast>?")
        self.assertIn("<blockquote expandable>1. How did you sleep?", html)
        ev = compose.telegram_html("evening", datetime(2026, 10, 14, 23, 0, tzinfo=IST), "x")
        self.assertIn("<b>1. Brain Dump &amp; Letting Go</b>\n- What's spinning in your mind?", ev)
        self.assertIn("Tea &amp; &lt;toast&gt;?", html)
        self.assertIn("11. What would make today deeply satisfying?", html)

    def test_folded_state_has_no_opener(self):
        html = compose.telegram_html("evening", datetime(2026, 10, 14, 23, 0, tzinfo=IST), None, "missed — reply anytime")
        self.assertIn("missed — reply anytime", html)
        self.assertNotIn("<blockquote", html)  # folded = header only

    def test_header_and_plain_openers_use_names(self):
        self.assertTrue(compose.header("morning", datetime(2026, 10, 14, 6, 0, tzinfo=IST), names=PERSON)
                        .startswith("🌻 Morning journal · Wed 14 Oct"))
        self.assertEqual(compose.plain_opener("morning", PERSON),
                         "Morning, Theo 🌻 Start wherever something feels alive — one line or a voice note is plenty.")


class ValidateTest(unittest.TestCase):
    pack = "### journal:2026-08-25-morning\nI want to call Alex about the shop"

    def test_plain_opener_ok(self):
        self.assertTrue(validate.check("morning", {"opener": "Morning 🌻 What's alive today?", "citations": []}, self.pack)[0])

    def test_uncited_claim_rejected(self):
        ok, why = validate.check("morning", {"opener": "Garden day today. Yesterday you mentioned feeling scattered.",
                                             "citations": []}, self.pack)
        self.assertFalse(ok)
        self.assertEqual(why, "claim without citation")

    def test_fabricated_quote_rejected(self):
        d = {"opener": "You mentioned the shop.", "citations": [{"source": "journal:2026-08-25-morning",
                                                                    "quote": "I hate being early"}]}
        self.assertFalse(validate.check("morning", d, self.pack)[0])

    def test_real_quote_accepted(self):
        d = {"opener": "In August you wrote about calling Alex — did that happen?",
             "citations": [{"source": "journal:2026-08-25-morning", "quote": "call Alex about the shop"}]}
        self.assertTrue(validate.check("morning", d, self.pack)[0])

    def test_parse_tolerates_wrapping(self):
        self.assertEqual(validate.parse('```json\n{"opener": "hi", "citations": []}\n```')["opener"], "hi")


class CaptureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.sent = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)
        add_prompt(self.cfg, sent=self.sent)

    def tearDown(self):
        self.tmp.cleanup()

    def test_next_message_inside_window_captured_once(self):
        now = self.sent + timedelta(hours=2)
        info = capture.capture(self.cfg, self.cfg.chat_id, None, "Slept badly.\nStill at the lake.", now)
        self.assertEqual(info["rule"], "next-message")
        body = Path(info["path"]).read_text()
        self.assertIn("Slept badly.\nStill at the lake.", body)
        self.assertIn("# Morning journal", body)
        self.assertTrue(info["path"].endswith("/2026-10-14-morning.md"))
        self.assertIsNone(capture.capture(self.cfg, self.cfg.chat_id, None, "ok thanks", now + timedelta(minutes=2)))

    def test_after_window_only_direct_reply(self):
        late = self.sent + timedelta(hours=14)
        self.assertIsNone(capture.capture(self.cfg, self.cfg.chat_id, None, "random chat", late))
        info = capture.capture(self.cfg, self.cfg.chat_id, "100", "late answer", late)
        self.assertEqual(info["rule"], "direct-reply")

    def test_direct_replies_append(self):
        capture.capture(self.cfg, self.cfg.chat_id, "100", "first", self.sent + timedelta(hours=1))
        info = capture.capture(self.cfg, self.cfg.chat_id, "100", "second", self.sent + timedelta(hours=20))
        body = Path(info["path"]).read_text()
        self.assertLess(body.index("first"), body.index("second"))
        self.assertLess(body.index("second"), body.index("## Iris's annotations"))

    def test_other_chat_and_commands_ignored(self):
        now = self.sent + timedelta(hours=1)
        self.assertIsNone(capture.capture(self.cfg, "999", None, "hi", now))
        self.assertIsNone(capture.capture(self.cfg, self.cfg.chat_id, None, "/new", now))

    def test_most_recent_visible_prompt_wins(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-14",
                   sent=self.sent + timedelta(hours=3), message_id="200")
        info = capture.capture(self.cfg, self.cfg.chat_id, None, "evening thoughts", self.sent + timedelta(hours=4))
        self.assertEqual(info["prompt"]["kind"], "evening")

    def test_annotations_written_without_touching_raw(self):
        info = capture.capture(self.cfg, self.cfg.chat_id, None, "raw words", self.sent + timedelta(hours=1))
        path = Path(info["path"])
        self.assertTrue(entries.annotations_pending(path.read_text()))
        entries.write_annotations(path, "- Pattern: sleep")
        body = path.read_text()
        self.assertIn("raw words", entries.raw_entry(body))
        self.assertIn("- Pattern: sleep", body)
        self.assertFalse(entries.annotations_pending(body))

    def test_no_chat_configured_captures_nothing(self):
        self.cfg.chat_id = ""
        self.assertIsNone(capture.capture(self.cfg, "", None, "hi", self.sent + timedelta(hours=1)))


class GuardTest(unittest.TestCase):
    """The guard against a neutral layout: a temporary home with a Hermes profile ``ada``, a journal folder and a
    config file. Nothing here names a real install."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name).resolve()
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})  # so `~/` in a command means this home
        env.start()
        self.addCleanup(env.stop)
        self.cfg = guard_cfg(self.home)
        self.hermes = self.home / ".hermes"
        self.profile = self.hermes / "profiles" / "ada"
        self.journal = self.cfg.root
        self.config_file = self.cfg.config_file

    def blocked(self, tool, args):
        return guard.check(self.cfg, tool, args) is not None

    def test_blocks_hermes_settings(self):
        self.assertTrue(self.blocked("terminal", {"command": "hermes config set model.default x"}))
        self.assertTrue(self.blocked("terminal", {"command": "cd /tmp && hermes -p ada cron edit abc --prompt x"}))
        self.assertTrue(self.blocked("patch", {"path": str(self.hermes / "config.yaml")}))
        self.assertTrue(self.blocked("write_file", {"path": str(self.profile / "config.yaml")}))
        self.assertTrue(self.blocked("write_file", {"path": str(self.journal / "2026" / "10" / "x.md")}))
        self.assertTrue(self.blocked("terminal", {"command": "sed -i s/a/b/ ~/.hermes/config.yaml"}))
        self.assertTrue(self.blocked("terminal", {"command": "systemctl --user restart hermes-gateway"}))
        self.assertTrue(self.blocked("skill_manage", {"action": "edit", "name": "morning-journal"}))
        self.assertTrue(self.blocked("cronjob_manage", {"action": "create"}))
        self.assertTrue(self.blocked("execute_code", {"code": "print(1)"}))
        self.assertTrue(self.blocked("write_file", {"path": str(self.config_file)}))
        self.assertTrue(self.blocked("terminal", {"command": f"cp x {self.config_file}"}))

    def test_blocks_the_journal_cli_changing_settings(self):
        """`journal config set` rewrites the config file through the journal's own code: no path, no write token."""
        for cmd in ("journal config set windows.collapse_hours 72",
                    "journal config set reflections.day.enabled false --json",
                    f"journal --config {self.config_file} config set today.enabled false",
                    "journal --config=/tmp/other.json config   set today.enabled false",
                    "python3 -m journal config set today.enabled false",
                    "python -m journal --hermes-home /tmp/h config set location Lisbon",
                    "uv run journal config set today.sources '[]'",
                    f"{self.home}/.local/bin/journal config set windows.grace_hours 9",
                    "cd /tmp && journal config set windows.grace_hours 9",
                    "echo hi | journal config set windows.grace_hours 9",
                    "python3 -m journal.cli config set today.enabled false",
                    "bash -c \"journal config set windows.grace_hours 9\"",
                    "sh -c 'journal --config /tmp/c.json config set windows.grace_hours 9'",
                    "\"journal\" config set timezone UTC",
                    "python -m 'journal' config set timezone UTC",
                    "journal 'config' \"set\" timezone UTC",
                    "hermes journal config set windows.collapse_hours 72"):
            with self.subTest(cmd):
                self.assertTrue(self.blocked("terminal", {"command": cmd}))
                self.assertTrue(self.blocked("process_manage", {"command": cmd}))

    def test_allows_reading_and_escalation(self):
        self.assertFalse(self.blocked("terminal", {"command": "journal config list --json"}))
        self.assertFalse(self.blocked("terminal", {"command": "journal --config /tmp/c.json config get windows"}))
        self.assertFalse(self.blocked("terminal", {"command": "journal today"}))
        self.assertFalse(self.blocked("terminal", {"command": "grep -rn 'config set' ~/notes/my-journal-ideas.md"}))
        self.assertFalse(self.blocked("terminal", {"command": "hermes -p ops chat --oneshot --query-file /tmp/q -Q"}))
        self.assertFalse(self.blocked("terminal", {"command": "hermes -p ada cron list"}))
        self.assertFalse(self.blocked("terminal", {"command": "cat ~/.hermes/profiles/ada/config.yaml 2>/dev/null"}))
        self.assertFalse(self.blocked("terminal", {"command": "some-search-tool search 'plan' --json"}))
        self.assertFalse(self.blocked("write_file", {"path": str(self.home / "notes" / "Other" / "idea.md")}))
        self.assertFalse(self.blocked("write_file", {"path": str(self.profile / "cache" / "scratch" / "h.txt")}))
        self.assertFalse(self.blocked("read_file", {"path": str(self.hermes / "config.yaml")}))
        self.assertFalse(self.blocked("skill_manage", {"action": "view", "name": "journal-system"}))

    def test_refusal_names_people_from_config(self):
        msg = guard.check(self.cfg, "cronjob_manage", {"action": "create"})
        self.assertIn("Ada doesn't change Hermes settings", msg)
        self.assertIn("Tell Sam what you found, or ask the operator", msg)

    def test_defaults_are_gentler_and_can_be_disabled(self):
        cfg = Config(root=Path("/tmp/j"), state_dir=Path("/tmp/state"))
        self.assertIsNone(guard.check(cfg, "execute_code", {"code": "1"}))  # off by default
        self.assertIsNotNone(guard.check(cfg, "write_file", {"path": "/tmp/j/x.md"}))  # journal root protected
        cfg.guard["enabled"] = False
        self.assertIsNone(guard.check(cfg, "write_file", {"path": "/tmp/j/x.md"}))


class TranscriptTest(unittest.TestCase):
    def test_strips_note_reply_prefix_and_quotes(self):
        note = "[Journal reply — saved …]\n\n"
        msg = '[Replying to: "Morning"]\n\n"I slept okay, the neighbours were loud."\n\n' + note
        self.assertEqual(inbound.transcript(msg, {"note": note, "caption": ""}), "I slept okay, the neighbours were loud.")


class TickSendTest(unittest.TestCase):
    def test_failed_sends_retry_then_alert(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))

            def boom(*a, **k):
                raise telegram.TelegramError("Bad Gateway")
            with mock.patch.object(telegram, "send_html", boom), \
                    mock.patch.object(tick, "_opener", lambda *a: ("Morning 🌻", "plain:test", [], [])):
                now = datetime(2026, 10, 14, 6, 5, tzinfo=IST)
                for attempt in (1, 2):
                    log = []
                    tick.send_due(cfg, now, log)
                    self.assertIn(f"attempt {attempt}/3", log[-1])
                with self.assertRaises(tick.SendFailed):
                    tick.send_due(cfg, now, [])
                log = []
                tick.send_due(cfg, now, log)  # exhausted: no more attempts
                self.assertEqual(log, [])

    def test_full_ticks_count_attempts_and_alert_on_the_third(self):
        """Regression: prune() used to drop the timestamp-less failure record every tick, so the
        attempt count reset, the alert never fired, and a failing send repeated for the whole grace window."""
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))

            def boom(*a, **k):
                raise telegram.TelegramError("Bad Gateway")
            now = datetime(2026, 10, 14, 6, 5, tzinfo=IST)

            class Clock(datetime):
                @classmethod
                def now(cls, tz=None):
                    return now.astimezone(tz) if tz else now
            with mock.patch.object(telegram, "send_html", boom), mock.patch.object(tick, "datetime", Clock), \
                    mock.patch.object(tick, "_opener", lambda *a: ("Morning 🌻", "plain:test", [], [])):
                codes = [tick.run(cfg)[0] for _ in range(4)]
            self.assertEqual(codes, [0, 0, 1, 0])  # alert once, then stop trying
            p = ledger.find(ledger.snapshot(cfg.ledger_path), "2026-10-14-morning")
            self.assertEqual(p["send_attempts"], 3)

    def test_prune_keeps_recent_and_drops_old(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            now = datetime(2026, 10, 14, tzinfo=timezone.utc)
            with ledger.locked(cfg.ledger_path) as data:
                data["prompts"] += [{"id": "old", "sent_at": "2026-01-01T00:00:00+00:00"},
                                    {"id": "failing", "scheduled": "2026-10-14T06:00:00+05:30"},
                                    {"id": "dated", "local_date": "2026-10-13"}, {"id": "bare"}]
            tick.prune(cfg, now)
            self.assertEqual([p["id"] for p in ledger.snapshot(cfg.ledger_path)["prompts"]], ["failing", "dated"])

    def test_successful_send_recorded_once(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            sent = []
            asked = [{"id": "1", "question": "How did you sleep?", "as": "Sleep okay?"}]
            with mock.patch.object(telegram, "send_html", lambda c, chat, html, reply_to=None: sent.append(html) or "555"), \
                    mock.patch.object(tick, "_opener", lambda *a: ("Morning 🌻", "iris", [], asked)):
                now = datetime(2026, 10, 14, 6, 5, tzinfo=IST)
                tick.send_due(cfg, now, [])
                tick.send_due(cfg, now + timedelta(minutes=5), [])
                self.assertEqual(len(sent), 1)
                p = ledger.find(ledger.snapshot(cfg.ledger_path), "2026-10-14-morning")
                self.assertEqual(p["message_id"], "555")

    def test_opener_falls_back_to_plain_when_agent_fails(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            with mock.patch.object(context, "build", lambda c, n, *a: "pack"), \
                    mock.patch("journal.agent.ask", lambda *a, **k: None):
                opener, source, cites, asked = tick._opener(cfg, "evening", datetime(2026, 10, 14, 23, 0, tzinfo=IST),
                                                            "2026-10-14-evening")
            self.assertEqual(source, "plain:agent-failed")
            self.assertTrue(opener.startswith("Evening, Theo 🌻"))

    def test_validated_opener_recorded_under_companion_name(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            raw = json.dumps({"opener": "Evening 🌻 how did the day land?", "citations": [],
                              "asked": [{"id": "9.2", "as": "cozy?"}]})
            with mock.patch.object(context, "build", lambda c, n, *a: "pack"), \
                    mock.patch("journal.agent.ask", lambda *a, **k: raw):
                opener, source, _c, asked = tick._opener(cfg, "evening", datetime(2026, 10, 14, 23, 0, tzinfo=IST),
                                                         "2026-10-14-evening")
            self.assertEqual(source, "iris")
            self.assertEqual([a["id"] for a in asked], ["9.2"])


class VerifierTest(unittest.TestCase):
    def test_quotes_beyond_pack_verified_against_journal(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            f = cfg.root / "2026" / "07" / "2026-07-22-morning.md"
            f.parent.mkdir(parents=True)
            f.write_text("## Raw Entry\n\nI want more walks. Fewer screens.\n")
            verify = context.make_verifier(cfg)
            d = {"opener": "In July you wrote about wanting walks.",
                 "citations": [{"source": "journal:2026-07-22-morning", "quote": "I want more  walks."}]}
            self.assertTrue(validate.check("morning", d, "pack without it", verify)[0])
            d["citations"][0]["quote"] = "I want fewer walks."
            self.assertFalse(validate.check("morning", d, "pack without it", verify)[0])

    def test_telegram_quote_without_sessions_db_is_rejected_not_raised(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            verify = context.make_verifier(cfg)
            d = {"opener": "You said hi.", "citations": [{"source": "telegram:2026-10-12T02:26", "quote": "hi"}]}
            self.assertFalse(validate.check("morning", d, "pack", verify)[0])


class DispatchTest(CleanEnv):
    """Drive the gateway hooks with fake Telegram events against a temp journal."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        conf = write_config(t / "journal.json", {
            "root": str(t / "Journal"), "state_dir": str(t / "state"), "timezone": "Asia/Kolkata",
            "telegram": {"chat_id": CHAT}, "user": {"name": "Theo"}, "companion": {"name": "Iris", "emoji": "🌻"}})
        os.environ["JOURNAL_CONFIG"] = str(conf)
        self.cfg = load()
        self.cfg.root.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def event(self, text, kind="TEXT", reply_to=None, chat=CHAT, media=None, types=None, mid="9", raw=None):
        return SimpleNamespace(text=text, message_type=SimpleNamespace(name=kind), message_id=mid,
                               reply_to_message_id=reply_to, media_urls=media or [], media_types=types or [],
                               raw_message=raw,
                               source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id=chat,
                                                      user_id=chat))

    def test_j_text_saved_and_rewritten(self):
        out = hooks.on_dispatch(event=self.event("/j can't sleep, thinking about the coast"))
        self.assertEqual(out["action"], "rewrite")
        self.assertTrue(out["text"].startswith("[Journal note — Theo used /j"))
        self.assertFalse(out["text"].split("]\n\n", 1)[1].startswith("/"))
        notes = list(self.cfg.root.glob("*-j.md"))
        self.assertEqual(len(notes), 1)
        self.assertIn("can't sleep, thinking about the coast", notes[0].read_text())

    def test_plain_chat_untouched(self):
        self.assertIsNone(hooks.on_dispatch(event=self.event("hey iris")))
        self.assertIsNone(hooks.on_dispatch(event=self.event("/j hi", chat="123")))

    def test_direct_reply_to_prompt_saved(self):
        add_prompt(self.cfg, sent=datetime.now(timezone.utc) - timedelta(hours=30), message_id="77")
        out = hooks.on_dispatch(event=self.event("late answer", reply_to="77"))
        self.assertTrue(out["text"].startswith("[Journal reply"))
        self.assertIn("Respond as Iris following the journal:journal-system skill", out["text"])
        self.assertIn("late answer", (self.cfg.root / "2026-10-14-morning.md").read_text())

    def test_voice_j_saved_after_transcription(self):
        out = hooks.on_dispatch(event=self.event("/j", kind="VOICE"))
        note = out["text"]
        hooks.on_llm_call(user_message='"walked by the lake, felt light"\n\n' + note, platform="telegram")
        body = next(self.cfg.root.glob("*-j.md")).read_text()
        self.assertIn("walked by the lake, felt light", body)
        self.assertIn("voice transcript", body)

    def test_add_note_tool(self):
        from journal.hermes import tools
        res = json.loads(tools.journal_add_note({"note": "Pattern worth keeping (inference)."}))
        self.assertTrue(res["success"])
        self.assertIn("## Iris's notes", Path(res["path"]).read_text())

    def test_add_note_tool_kind_note_targets_freeform_entry(self):
        from journal.hermes import tools
        hooks.on_dispatch(event=self.event("/j a thought"))
        res = json.loads(tools.journal_add_note({"note": "Linked (inference).", "kind": "note"}))
        self.assertTrue(res["success"], res)
        self.assertTrue(res["path"].endswith("-j.md"))

    def test_guard_hook_blocks_and_fails_open(self):
        self.assertEqual(hooks.on_tool_call(tool_name="write_file",
                                            args={"path": str(self.cfg.root / "x.md")})["action"], "block")
        with mock.patch("journal.guard.check", side_effect=RuntimeError("bug")):
            self.assertIsNone(hooks.on_tool_call(tool_name="write_file", args={"path": "/x"}))

    def test_dispatch_fails_open_on_bad_config(self):
        os.environ["JOURNAL_CONFIG"] = str(Path(self.tmp.name) / "missing.json")
        self.assertIsNone(hooks.on_dispatch(event=self.event("hello")))

    def test_notebook_injected_into_turns(self):
        from journal import notebook
        notebook.add(self.cfg, "ask about the lake")
        out = hooks.on_llm_call(user_message="hi", platform="telegram")
        self.assertIn("ask about the lake", out["context"])
        self.assertIn("pick up with Theo", out["context"])


class LocationTest(CleanEnv):
    def test_set_location_updates_config_and_history(self):
        with tempfile.TemporaryDirectory() as t:
            conf = write_config(Path(t) / "journal.json", {"root": t, "state_dir": str(Path(t) / "state"),
                                                           "timezone": "Asia/Kolkata", "location": "Hillside"})
            hit = {"name": "Chicago", "admin1": "Illinois", "country": "United States",
                   "latitude": 41.9, "longitude": -87.6, "timezone": "America/Chicago"}
            with mock.patch.object(location, "geocode", lambda q: hit):
                r = location.set_location(load(conf), "Chicago", reload=lambda: load(conf))
            d = json.loads(conf.read_text())
            self.assertEqual(d["timezone"], "America/Chicago")
            self.assertEqual(d["coordinates"]["lat"], 41.9)
            self.assertEqual(r["previous"]["timezone"], "Asia/Kolkata")
            self.assertEqual(r["schedule_local"]["midweek"], "12:00 (Wed)")
            self.assertTrue((Path(t) / "state" / "location-history.jsonl").exists())


class FoldRetryTest(unittest.TestCase):
    def test_failed_fold_retries_then_gives_up(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            sent = datetime.now(timezone.utc) - timedelta(hours=7)
            add_prompt(cfg, sent=sent, message_id="5")

            def boom(*a, **k):
                raise telegram.TelegramError("timeout")
            with mock.patch.object(telegram, "edit_html", boom):
                for _ in range(tick.FOLD_ATTEMPTS - 1):
                    tick.fold(cfg, datetime.now(timezone.utc), [])
                    p = ledger.find(ledger.snapshot(cfg.ledger_path), "2026-10-14-morning")
                    self.assertIsNone(p.get("collapsed_at"))
                tick.fold(cfg, datetime.now(timezone.utc), [])
                p = ledger.find(ledger.snapshot(cfg.ledger_path), "2026-10-14-morning")
                self.assertIsNotNone(p.get("collapsed_at"))


class WovenQuestionsTest(unittest.TestCase):
    def test_ids_and_asked_filtering(self):
        self.assertEqual(library.builtins()["evening"].items()["9.2"], "What simple joys touched you today?")
        self.assertEqual(library.builtins()["morning"].items()["11"], "What would make today deeply satisfying?")
        d = {"opener": "x", "asked": [{"id": "9.2", "as": "What was cozy today?"}, {"id": "99", "as": "bogus"}]}
        got = validate.asked("evening", d)
        self.assertEqual([a["id"] for a in got], ["9.2"])
        self.assertEqual(got[0]["as"], "What was cozy today?")

    def test_longer_woven_opener_allowed_but_not_a_pasted_list(self):
        woven = "Evening, Theo. " + "What was cozy about the rain? " * 20
        self.assertTrue(validate.check("evening", {"opener": woven, "citations": []}, "")[0])
        pasted = "\n".join(f"{i}. q" for i in range(1, 8))
        self.assertFalse(validate.check("evening", {"opener": pasted, "citations": []}, "")[0])

    def test_manual_resend_keeps_old_message_replyable(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            ids = iter(["500", "501"])
            edits = []
            with mock.patch.object(telegram, "send_html", lambda c, chat, html, reply_to=None: next(ids)), \
                    mock.patch.object(telegram, "edit_html", lambda c, chat, mid, html: edits.append(mid)), \
                    mock.patch.object(tick, "_opener", lambda *a: ("Morning 🌻", "iris", [], [])):
                now = datetime(2026, 10, 14, 9, 0, tzinfo=IST)
                tick.send_due(cfg, now, [], only="morning")
                tick.send_due(cfg, now + timedelta(minutes=10), [], only="morning")
                data = ledger.snapshot(cfg.ledger_path)
                p = ledger.find(data, "2026-10-14-morning")
                self.assertEqual(p["message_id"], "501")
                self.assertEqual(edits, ["500"])  # old one marked "sent again below"
                self.assertIs(ledger.by_message_id(data, cfg.chat_id, "500"), p)

    def test_entry_records_woven_questions(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = make_cfg(Path(t))
            p = add_prompt(cfg, sent=datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc))
            with ledger.locked(cfg.ledger_path) as data:
                q = ledger.find(data, p["id"])
                q["sent_at_local"] = "06:00 IST"
                q["asked"] = [{"id": "1", "question": "How did you sleep?", "as": "Sleep okay?"}]
            info = capture.capture(cfg, cfg.chat_id, "100", "fine", datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc))
            self.assertIn("_Woven in: [1] How did you sleep?_", Path(info["path"]).read_text())


class CommandTest(CleanEnv):
    def test_commands_registered_and_spawn_engine(self):
        names = []

        class Ctx:
            def register_command(self, name, handler, description=""):
                names.append(name)
        commands.register(Ctx())
        self.assertEqual(names, ["j", "x", "morning", "evening", "midweek", "weekly", "morning-journal",
                                 "evening-journal", "journal-start",  # WP2: /start <id>, under a name Hermes allows
                                 "next", "skip", "back", "break", "abandon",  # WP4: program commands Hermes lacks
                                 "program",  # (abandon: ONE-PROGRAM-RUN)
                                 "web"])  # WEB-COMMAND
        calls = []
        with tempfile.TemporaryDirectory() as t:
            conf = write_config(Path(t) / "j.json", {"root": t, "state_dir": t, "companion": {"emoji": "🌻"}})
            os.environ["JOURNAL_CONFIG"] = str(conf)
            with mock.patch.object(subprocess, "Popen", lambda args, **kw: calls.append((args, kw))):
                msg = commands.trigger("evening")("")
        self.assertEqual(msg, "🌻 Your evening journal is on its way.")
        args, kw = calls[0]
        self.assertEqual(args[-4:], ["-m", "journal", "send", "evening"])
        self.assertIn(str(Path(__file__).resolve().parents[1] / "src"), kw["env"]["PYTHONPATH"])
        self.assertTrue(kw["start_new_session"])

    def test_unconfigured_trigger_explains(self):
        os.environ["JOURNAL_CONFIG"] = "/nonexistent/journal.json"
        self.assertIn("isn't configured", commands.trigger("morning")(""))


if __name__ == "__main__":
    unittest.main()
