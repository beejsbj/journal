"""OPEN-CORE (docs/OPEN-PROMPTS.md): his words on a face fill the practice's slot (``actions``), the tick's schedule
counts a slot answered on any face as done and checks again before it sends, a delivery that loses that race attaches
without overwriting, a deliberate send takes its slot from ``slots.resolve``, and ``channel.platform: none`` sends,
writes and counts nothing. The chat side reads delivered records only, so nothing a face writes is a chat boundary."""
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CHAT, NEUTRAL, CleanEnv, make_cfg

from journal import (actions, agent, audio, capture, cli, context, conversations, entries, hookstate, ledger, privacy,
                     programs, reflect, slots, status, sweep, telegram, tick)

IST = ZoneInfo("Asia/Kolkata")
WED = datetime(2026, 10, 14, 0, 0, tzinfo=IST)  # a Wednesday
WATER = """---
kind: practice
id: water
title: Water
emoji: 💧
schedule.1.time: 09:00
schedule.2.time: 15:00
collapse_hours: 2
opener.mode: plain
opener.plain: {greet:Water} One line.
---
- Did you drink enough?
"""
TICK_OPENER = "Morning 🌿 the tick's own opener, about the rain."


def at(h, m=0, day=WED):
    return day.replace(hour=h, minute=m)


class Bot:
    """Telegram as a list of calls; every send gets the next message id. ``before_send`` runs as a send goes out."""

    def __init__(self):
        self.calls, self.next_id, self.before_send = [], 700, None

    def __call__(self, cfg, method, payload):
        if method == "sendMessage" and self.before_send is not None:
            hook, self.before_send = self.before_send, None
            hook()
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
        self.cfg = make_cfg(Path(self.tmp.name), names=NEUTRAL)
        self.cfg.sources["weather"] = {"enabled": False}
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "water.md").write_text(WATER, encoding="utf-8")
        self.bot = Bot()
        self.asked = []
        test = self

        class Clock(datetime):  # the tick stamps sends with the wall clock: hold it at the moment being simulated
            @classmethod
            def now(cls, tz=None):
                return test.now.astimezone(tz) if tz else test.now

        def unreachable(cfg, query, *a, **k):
            self.asked.append(query)
            raise agent.AgentUnavailable("no model in this test")
        self.ask = unreachable
        for p in (mock.patch.object(telegram, "_call", self.bot), mock.patch.object(telegram, "token", lambda c: "T"),
                  mock.patch.object(agent, "ask", lambda *a, **k: self.ask(*a, **k)),
                  mock.patch.object(tick, "datetime", Clock),
                  mock.patch.object(context, "build", lambda c, n, *a: "PACK")):
            p.start()
            self.addCleanup(p.stop)
        self.now = at(0)

    def send(self, when, **kw):
        self.now = when
        log = []
        tick.send_due(self.cfg, when, log, **kw)
        return log

    def answer(self, practice, text, when, via="web", **kw):
        self.now = when
        return actions.answer(self.cfg, practice, text, when, via=via, now=kw.pop("now", when), **kw)

    def prompt(self, pid):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)

    def prompts(self):
        return ledger.snapshot(self.cfg.ledger_path)["prompts"]


class AnswerTest(Base):
    def test_words_on_a_face_fill_the_due_slot_as_the_practices_entry(self):
        info = self.answer("water", "two glasses", at(10))
        self.assertEqual((info["slot"], info["step"], info["rule"]), ("2026-10-14-water", 1, "web"))
        p = self.prompt("2026-10-14-water")
        self.assertIsNone(p["message_id"])
        self.assertEqual((p["opened_via"], p["kind"], p["local_date"], p["opener"], p["opener_source"]),
                         ("web", "water", "2026-10-14", "Water, Sam 💧 One line.", "plain:web"))
        self.assertEqual(p["questions"], [{"id": "1", "text": "Did you drink enough?", "section": None}])
        self.assertEqual(p["sent_at"], at(10).isoformat())
        self.assertFalse(p["needs_edit"])  # no chat message to fold
        self.assertEqual(p["captures"][0]["via"], "web")
        body = Path(info["path"]).read_text(encoding="utf-8")
        self.assertEqual(Path(info["path"]).name, "2026-10-14-water.md")
        self.assertIn("two glasses", body)
        self.assertIn("_Written 10:00 IST in the web app · opener: plain:web_", body)
        self.assertEqual(slots.day(self.cfg, "2026-10-14", at(10, 5), practice="water")[0].state, "answered")

    def test_each_face_writes_its_own_rule_and_via(self):
        for via, when in (("cli", at(9, 30)), ("mcp", at(15, 30))):
            info = self.answer("water", f"from {via}", when, via=via)
            cap = info["prompt"]["captures"][-1]
            self.assertEqual((info["rule"], cap["via"], cap["source"], info["prompt"]["opened_via"]),
                             (via, via, via, via))

    def test_an_answered_practice_chosen_again_is_an_extra_entry_never_a_note(self):
        self.answer("morning", "slept well", at(7))
        info = self.answer("morning", "and a second thought", at(8))
        self.assertEqual((info["slot"], Path(info["path"]).name), ("2026-10-14-morning-2", "2026-10-14-morning-2.md"))
        self.assertNotIn("j", {p["kind"] for p in self.prompts()})

    def test_an_unscheduled_answer_before_yesterday_is_an_extra_on_that_day(self):
        info = self.answer("morning", "remembered later", at(7, day=WED - timedelta(days=3)), now=at(12))
        self.assertEqual(info["slot"], "2026-10-11-morning-2")
        self.assertIsNone(self.prompt("2026-10-11-morning"))   # the old miss stays a miss

    def test_words_into_an_open_delivered_prompt_fold_it_as_a_chat_reply_would(self):
        self.send(at(9, 1))
        info = self.answer("water", "a litre", at(9, 20))
        p = self.prompt("2026-10-14-water")
        self.assertEqual((info["step"], p["message_id"], p["needs_edit"]), (1, "701", True))
        self.assertNotIn("opened_via", p)

    def test_the_record_a_failed_send_left_is_filled_in_and_leaves_the_undelivered_list(self):
        with mock.patch.object(telegram, "send_html", side_effect=telegram.TelegramError("Bad Gateway")):
            self.send(at(9, 1))
        self.assertEqual([u["id"] for u in status.status(self.cfg, at(9, 2))["undelivered"]], ["2026-10-14-water"])
        info = self.answer("water", "a litre anyway", at(9, 30))
        p = self.prompt("2026-10-14-water")
        self.assertEqual((info["slot"], p["opener"], p["stem"], p["opened_via"], p["send_attempts"]),
                         ("2026-10-14-water", "Water, Sam 💧 One line.", "2026-10-14-water", "web", 1))
        self.assertEqual(p["questions"][0]["text"], "Did you drink enough?")
        self.assertEqual(status.status(self.cfg, at(9, 31))["undelivered"], [])

    def test_words_dated_before_a_slot_whose_send_failed_fill_it_early(self):
        self.answer("water", "a glass", at(9, 30))
        with mock.patch.object(telegram, "send_html", side_effect=telegram.TelegramError("Bad Gateway")):
            self.send(at(15, 1))
        info = self.answer("water", "another, before three", at(14, 45), now=at(15, 10))
        p = self.prompt("2026-10-14-water-2")
        self.assertEqual((info["slot"], info["step"], p["opened_via"], p["send_attempts"]),
                         ("2026-10-14-water-2", 3, "web", 1))
        self.assertIsNone(self.prompt("2026-10-14-water-3"))
        self.assertEqual(status.status(self.cfg, at(15, 11))["undelivered"], [])

    def test_a_retried_client_id_writes_once(self):
        first = self.answer("water", "once", at(10), client_id="c-1")
        again = self.answer("water", "once", at(10), client_id="c-1")
        self.assertTrue(again["duplicate"])
        self.assertEqual(Path(first["path"]).read_text(encoding="utf-8").count("once"), 1)

    def test_a_note_is_a_j_note_of_its_own(self):
        info = actions.note(self.cfg, "call the bank", at(11), via="cli", now=at(11))
        self.assertEqual((Path(info["path"]).name, info["rule"]), ("2026-10-14-j.md", "/j"))
        self.assertEqual(info["prompt"]["captures"][0]["via"], "cli")

    def test_the_three_refusals_hold_for_every_face_and_write_nothing(self):
        cases = {"off-the-record": ["/x a secret", "fine\n/x a secret"],
                 "reserved-line": ["mine\n%% journal phase:1 %%\nmore"],
                 "placeholder": [f"before {audio.TRANSCRIBING} after"]}
        for code, texts in cases.items():
            for text in texts:
                for via in actions.FACES:
                    with self.subTest(code=code, via=via, text=text):
                        with self.assertRaises(actions.Refused) as e:
                            actions.answer(self.cfg, "water", text, at(10), via=via, now=at(10))
                        self.assertEqual(e.exception.code, code)
                        with self.assertRaises(actions.Refused) as e:
                            actions.note(self.cfg, text, at(10), via=via, now=at(10))
                        self.assertEqual(e.exception.code, code)
        self.assertEqual(self.prompts(), [])
        self.assertEqual(list(self.cfg.root.iterdir()), [])

    def test_a_program_or_unknown_id_is_not_a_practice(self):
        for word in ("nope", "fix-life"):
            with self.assertRaises(actions.Refused) as e:
                actions.answer(self.cfg, word, "x", at(10), via="web", now=at(10))
            self.assertEqual(e.exception.code, "not-a-practice")


class ScheduleTest(Base):
    def test_a_face_answer_before_the_slot_stops_its_send(self):
        info = self.answer("water", "early", at(8))
        self.assertEqual((info["slot"], info["step"]), ("2026-10-14-water", 3))
        self.send(at(9, 1))
        self.assertEqual(self.bot.texts(), [])
        self.assertIsNone(self.prompt("2026-10-14-water")["message_id"])

    def test_a_face_answer_during_the_openers_model_call_stops_the_send_and_leaves_no_pack(self):
        def answers_meanwhile(cfg, query, *a, **k):
            actions.answer(self.cfg, "morning", "wrote it in the web app", at(6, 1), via="web", now=at(6, 1))
            raise agent.AgentUnavailable("slow model")
        self.ask = answers_meanwhile
        log = self.send(at(6, 1))
        self.assertEqual(self.bot.texts(), [])
        self.assertIn("2026-10-14-morning: not sent: answered meanwhile in the web app", log)
        self.assertFalse((self.cfg.state_dir / "packs" / "2026-10-14-morning.md").exists())
        self.assertFalse((self.cfg.state_dir / "sent.jsonl").exists())

    def test_a_scheduled_send_still_saves_its_pack(self):
        self.send(at(6, 1))
        self.assertEqual((self.cfg.state_dir / "packs" / "2026-10-14-morning.md").read_text(), "PACK")

    def test_a_send_that_loses_the_race_attaches_without_overwriting(self):
        self.bot.before_send = lambda: actions.answer(self.cfg, "morning", "the web app was quicker", at(6, 1),
                                                      via="web", now=at(6, 1))
        with mock.patch.object(tick, "_opener", lambda *a: (TICK_OPENER, "ada", [], [])):
            self.send(at(6, 1))
        p = self.prompt("2026-10-14-morning")
        self.assertEqual(p["message_id"], "701")
        self.assertEqual((p["opener_source"], p["opened_via"]), ("plain:web", "web"))
        self.assertTrue(p["opener"].startswith("Morning, Sam"), p["opener"])   # the entry keeps the face's opener
        self.assertEqual(p["sent_opener"], TICK_OPENER)
        self.assertIn("the tick's own opener", p["sent_plain_text"])
        self.assertIn("the tick's own opener", p["sent_html"])
        self.assertEqual(p["sent_at"], at(6, 1).astimezone(timezone.utc).isoformat())   # the delivery's time
        self.assertTrue(p["needs_edit"])
        self.assertEqual(len(p["captures"]), 1)
        self.assertEqual(p["stem"], "2026-10-14-morning")
        # the next tick folds the message to "answered", with the opener it was sent with
        tick.fold(self.cfg, at(6, 10), [])
        folded = self.bot.texts("editMessageText")
        self.assertEqual(len(folded), 1)
        self.assertIn("the tick's own opener", folded[0])
        self.assertIn("answered ✓", folded[0])
        # the sweep knows a quote of the message as sent
        self.assertIn(sweep.words(TICK_OPENER), " ".join(sweep.prompt_texts(self.cfg, p)))

    def test_a_failed_send_onto_a_face_answer_counts_nothing_and_alerts_nothing(self):
        """Review round 1: a send that fails after he answered on a face owes nothing: no attempt, no alert, at any
        count."""
        self.answer("morning", "the web app was quicker", at(6, 1))
        record = {"id": "2026-10-14-morning", "kind": "morning", "local_date": "2026-10-14",
                  "scheduled": at(6).isoformat()}
        for _ in range(tick.SEND_ATTEMPTS + 1):
            log = []
            tick.count_send_failure(self.cfg, record, telegram.TelegramError("Bad Gateway"), log)
            self.assertIn("not counted", log[0])
        self.assertNotIn("send_attempts", self.prompt("2026-10-14-morning"))

    def test_a_deliberate_start_after_a_face_answer_makes_an_extra_entry(self):
        self.answer("morning", "slept well", at(7))
        before = dict(self.prompt("2026-10-14-morning"))
        self.send(at(7, 10), only="morning")
        self.assertEqual(self.prompt("2026-10-14-morning"), before)   # the answered record is untouched
        extra = self.prompt("2026-10-14-morning-2")
        self.assertEqual((extra["message_id"], extra["stem"]), ("701", "2026-10-14-morning-2"))

    def test_a_deliberate_send_answered_during_its_opener_goes_to_an_extra_entry(self):
        """Review round 1: /start resolves its slot before the opener's model call; words saved on a face meanwhile
        make that slot answered, so the send resolves again and never attaches to his record (rule 3)."""
        def answers_meanwhile(cfg, query, *a, **k):
            actions.answer(self.cfg, "morning", "wrote it in the web app", at(7, 1), via="web", now=at(7, 1))
            raise agent.AgentUnavailable("slow model")
        self.ask = answers_meanwhile
        self.send(at(7, 1), only="morning")
        face = self.prompt("2026-10-14-morning")
        self.assertEqual((face["message_id"], face.get("sent_opener"), face.get("needs_edit")), (None, None, False))
        extra = self.prompt("2026-10-14-morning-2")
        self.assertEqual((extra["message_id"], extra["stem"]), ("701", "2026-10-14-morning-2"))
        self.assertTrue((self.cfg.state_dir / "packs" / "2026-10-14-morning-2.md").exists())
        self.assertFalse((self.cfg.state_dir / "packs" / "2026-10-14-morning.md").exists())

    def test_start_after_an_answer_delivers_the_next_slot_that_fits_and_the_tick_skips_it(self):
        self.answer("water", "a glass", at(9, 30))
        self.send(at(10), only="water")
        p = self.prompt("2026-10-14-water-2")
        self.assertEqual((p["message_id"], p["stem"]), ("701", "2026-10-14-water-2"))
        self.send(at(15, 1))
        self.assertEqual(len(self.bot.texts()), 1)   # the 15:00 slot went out at 10:00

    def test_start_takes_the_slot_that_is_due_not_always_slot_one(self):
        self.send(at(15, 30), only="water")
        self.assertIsNotNone(self.prompt("2026-10-14-water-2"))
        self.assertIsNone(self.prompt("2026-10-14-water"))

    def test_a_late_direct_reply_to_an_answered_or_expired_prompt_is_still_kept(self):
        self.send(at(9, 1))
        capture.capture(self.cfg, CHAT, None, "first", at(9, 5).astimezone(timezone.utc), "text", "11")
        late = capture.capture(self.cfg, CHAT, "701", "much later", at(20).astimezone(timezone.utc), "text", "12")
        self.assertEqual((late["rule"], late["prompt"]["id"]), ("direct-reply", "2026-10-14-water"))


class PrivacyTest(Base):
    """Rule 7: saving words on a face is never a chat boundary."""

    def test_a_face_answer_writes_no_send_and_no_thread_stamp(self):
        before = (privacy.send_times(self.cfg), conversations._send_times(self.cfg))
        self.answer("water", "a litre", at(10))
        actions.note(self.cfg, "a note", at(10, 5), via="mcp", now=at(10, 5))
        self.assertEqual((privacy.send_times(self.cfg), conversations._send_times(self.cfg)), before)
        tick.prune(self.cfg, at(10, 10).astimezone(timezone.utc))
        self.assertEqual(json.loads((self.cfg.state_dir / privacy.SENDS).read_text()) if
                         (self.cfg.state_dir / privacy.SENDS).exists() else [], [])
        self.assertFalse((self.cfg.state_dir / "sent.jsonl").exists())
        self.assertNotIn("last_send", hookstate.stamps(self.cfg))

    def test_a_face_answer_does_not_take_his_next_chat_message(self):
        self.answer("water", "a litre", at(10))
        data = ledger.snapshot(self.cfg.ledger_path)
        self.assertEqual(capture.match(data, self.cfg, CHAT, None, at(10, 1).astimezone(timezone.utc)), (None, ""))
        self.assertIsNone(capture.capture(self.cfg, CHAT, None, "just chatting", at(10, 2).astimezone(timezone.utc),
                                          "text", "55"))

    def test_a_later_delivery_is_a_boundary_at_its_delivery_time(self):
        self.bot.before_send = lambda: actions.answer(self.cfg, "morning", "quicker", at(6, 1), via="web",
                                                      now=at(6, 1))
        self.send(at(6, 3))
        sent = self.prompt("2026-10-14-morning")["sent_at"]
        self.assertEqual(sent, at(6, 3).astimezone(timezone.utc).isoformat())
        self.assertEqual(conversations._send_times(self.cfg), [sent])

    def test_the_conversation_writer_does_not_file_chat_that_repeats_face_words(self):
        words = "the long walk by the river made the whole afternoon feel lighter than it had any right to be"
        self.answer("water", words, at(10))
        saved = conversations._Saved(self.cfg, ledger.snapshot(self.cfg.ledger_path))
        self.assertIsNone(saved.find(words, at(10, 30)))

    def test_annotations_of_face_words_read_no_chat_reply(self):
        self.answer("morning", "slept badly", at(6, 30))
        seen, reads = [], []
        self.ask = lambda cfg, query, *a, **k: seen.append(query) or "Restless night (inference)."
        with mock.patch.object(context, "companion_reply_after", lambda *a, **k: reads.append(a) or "UNRELATED"):
            tick.annotate(self.cfg, at(7).astimezone(timezone.utc), [])
        self.assertEqual(reads, [])
        self.assertNotIn("UNRELATED", seen[0])


class NoChannelTest(Base):
    def setUp(self):
        super().setUp()
        self.cfg.platform, self.cfg.chat_id = "none", ""

    def test_a_whole_day_of_ticks_writes_sends_and_counts_nothing(self):
        for h in range(0, 24):
            self.now = at(h, 1)
            code, log = tick.run(self.cfg)
            self.assertEqual(code, 0, log)
        self.assertEqual(self.prompts(), [])
        self.assertEqual(self.bot.calls, [])
        self.assertFalse((self.cfg.state_dir / "sent.jsonl").exists())
        # (the built-ins' window is windows.collapse_hours, six hours; water's own is two)
        states = {s.id: s.state for s in slots.day(self.cfg, "2026-10-14", at(15, 30))}
        self.assertEqual((states["2026-10-14-morning"], states["2026-10-14-water"], states["2026-10-14-water-2"],
                          states["2026-10-14-evening"]), ("missed", "missed", "due", "upcoming"))

    def test_a_deliberate_send_sends_nothing(self):
        log = self.send(at(10), only="morning")
        self.assertEqual((self.prompts(), self.bot.calls), ([], []))
        self.assertIn("no chat", log[0])

    def test_annotations_are_written_and_no_reply_is_attempted(self):
        info = self.answer("morning", "slept badly", at(6, 30))
        self.ask = lambda cfg, query, *a, **k: "Restless night (inference)."
        tick.annotate(self.cfg, at(7).astimezone(timezone.utc), [])
        tick.reply_annotations(self.cfg, at(7, 5).astimezone(timezone.utc), [])
        self.assertIn("Restless night", Path(info["path"]).read_text(encoding="utf-8"))
        self.assertNotIn("annotation_reply", self.prompt("2026-10-14-morning"))
        self.assertEqual(self.bot.calls, [])
        s = status.status(self.cfg, at(7, 6))
        self.assertEqual((s["pending_annotation_replies"], s["undelivered"]), ([], []))

    def test_reflections_notes_and_summaries_are_not_sent(self):
        """Review round 1: each path is given something to send, and the channel is never asked for."""
        asked = []
        with mock.patch.object(type(self.cfg), "channel", lambda cfg: asked.append(cfg) or self.fail("channel()")):
            written = Path(self.cfg.root) / "2026-10-13-reflection.md"
            written.parent.mkdir(parents=True, exist_ok=True)
            written.write_text("# A day\n\nIt went.\n", encoding="utf-8")
            with ledger.locked(self.cfg.ledger_path) as data:
                data.setdefault("reflections", []).append(
                    {"id": "2026-10-13-reflection", "kind": "day", "local_date": "2026-10-13", "path": str(written),
                     "written_at": at(5).isoformat()})
            self.assertEqual(reflect.send(self.cfg, at(7), []), [])
            self.assertNotIn("telegram", ledger.snapshot(self.cfg.ledger_path)["reflections"][0])
            self.assertFalse(programs._say_about(self.cfg, "fix-life", "<b>hi</b>", [], at(7)))
            cli._notify(self.cfg, "hello")
            run = {"id": "r1", "program": "fix-life"}
            with mock.patch.object(programs.compose, "summary_html", lambda *a: "<b>summary</b>"):
                self.assertFalse(programs._send_summary(self.cfg, mock.MagicMock(), run, 1, at(7), [], "done", "s",
                                                        "", ""))
            alerts = []
            programs._retry_summary(self.cfg, None, run, "1", {"status": "completed"}, at(7), [], alerts)
            self.assertEqual((run.get("sessions", {}).get("1", {}).get("summary_attempts"), alerts), (None, []))
        self.assertEqual((asked, self.bot.calls), ([], []))

    def test_doctor_says_there_is_no_chat_on_purpose(self):
        row = status._channel_row(self.cfg)
        self.assertEqual(row[1], "ok")
        self.assertIn("no chat", row[2])
