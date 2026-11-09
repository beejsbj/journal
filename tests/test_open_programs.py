"""OPEN-PROGRAMS (docs/OPEN-PROMPTS.md, *How it would be built* 4; issue #20): programs and interrupts with no chat.

A phase becomes current with no delivery: ``programs`` opens it (its ledger record, ``message_id: None``, ``sent_at``
the moment it became current, ``phase_open``) and then delivers it when there is a chat. ``send_due`` means "owed a
delivery". The ``/x`` hold covers the delivery only, so a run answered on a face moves on while the chat is off the
record. Interrupts open at their time and can be answered on a face. With ``channel.platform: none`` nothing is sent,
attempted, counted or alerted, and a ten-session run completes from the web app alone."""
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from serve_helpers import NOW
from test_serve_writes import WriteCase

from journal import agent, audio, capture, entries, ledger, privacy, programs, schedule, status, telegram
import test_interrupts as ti
import test_programs as tp
import test_programs_review as rv

UTC = timezone.utc
CONFIRM = "2026-10-27 07:00"


def no_chat(cfg):
    cfg.platform, cfg.chat_id = "none", ""
    return cfg


def sent_lines(cfg) -> list[dict]:
    path = Path(cfg.state_dir) / "sent.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


# ── the engine, with no chat ─────────────────────────────────────────────────

class NoChatPhaseTest(tp.Base):
    def setUp(self):
        super().setUp()
        no_chat(self.cfg)

    def face(self, text, after=timedelta(minutes=1)):
        """His words for the open phase, as the web app writes them (``serve/writes.block_add``)."""
        self.now += after
        run = self.the_run()
        s, p = self.where()
        pid = run["sessions"][str(s)]["phases"][str(p)]["prompt_id"]
        return capture.add_web_block(self.cfg, programs.entry_stem(self.cfg, run, s), text, self.now, phase_prompt=pid)

    def test_a_phase_becomes_current_with_no_delivery(self):
        self.start()
        rid = self.the_run()["id"]
        ph = self.the_run()["sessions"]["1"]["phases"]["1"]
        self.assertEqual(ph["prompt_id"], f"{rid}-s01-p1")
        self.assertFalse(ph.get("send_due"))  # nothing is owed: there is no chat
        self.assertTrue(programs.phase_opened(ph))
        rec = self.prompt(f"{rid}-s01-p1")
        self.assertIsNone(rec["message_id"])
        self.assertTrue(rec["phase_open"])
        self.assertEqual(rec["sent_at"], self.now.isoformat())  # the moment it became current (capture.sent_by reads it)
        self.assertTrue(rec["html"] and rec["phase_text"])
        self.assertNotIn("send_attempts", rec)
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(sent_lines(self.cfg), [])
        self.assertFalse((Path(self.cfg.state_dir) / "send-times.json").exists())

    def test_a_face_answer_settles_and_the_next_phase_opens(self):
        self.start()
        self.face("my answer to phase one")
        self.assertTrue(self.settle())
        self.assertEqual(self.where(), (1, 2))
        rid = self.the_run()["id"]
        self.assertFalse(self.prompt(f"{rid}-s01-p1")["phase_open"])
        self.assertTrue(self.prompt(f"{rid}-s01-p2")["phase_open"])
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(entries.phase_answers(self.entry(1).read_text(encoding="utf-8")),
                         {1: "my answer to phase one"})

    def test_the_tick_step_owes_nothing_and_alerts_nothing(self):
        self.start()
        for _ in range(5):
            self.now += timedelta(minutes=5)
            programs.step(self.cfg, self.now, self.log)  # (an Alert would raise)
        self.assertEqual(self.bot.calls, [])
        self.assertNotIn("send_attempts", self.prompt(f"{self.the_run()['id']}-s01-p1"))

    def test_a_reflect_phase_is_built_by_the_engine_when_it_opens(self):
        self.model = lambda q: "Picture an ordinary Tuesday five years on, and stay with it."
        self.start()
        self.face("words of mine for this phase")  # session 1: phase 2 is the reflect phase
        self.assertTrue(self.settle())
        self.assertEqual(self.where(), (1, 2))
        rec = self.prompt(f"{self.the_run()['id']}-s01-p2")
        self.assertIn("Picture an ordinary Tuesday", rec["phase_text"])
        self.assertEqual(len(self.asked), 1)

    def test_next_moves_on_from_an_unanswered_open_phase(self):
        self.start()
        rid = self.the_run()["id"]
        self.assertTrue(programs.advance(self.cfg, rid, self.now, self.log, reason="next"))
        self.assertEqual(self.where(), (1, 2))

    def test_back_reopens_the_earlier_phase_for_a_face(self):
        self.start()
        self.face("first try")
        self.assertTrue(self.settle())
        rid = self.the_run()["id"]
        self.assertTrue(programs.back(self.cfg, rid, self.now, self.log))
        self.assertEqual(self.where(), (1, 1))
        self.assertTrue(self.prompt(f"{rid}-s01-p1")["phase_open"])
        self.assertFalse(self.prompt(f"{rid}-s01-p2")["phase_open"])
        self.face("second try", after=timedelta(minutes=1))
        self.assertTrue(self.settle())
        self.assertEqual(self.where(), (1, 2))

    def test_describe_says_open_since_it_became_current(self):
        self.start()
        self.assertIn("phase 1/6 open since 15:30", programs.describe(self.cfg, self.the_run(), self.now))

    def test_a_run_before_this_package_still_reads_as_delivered(self):
        """A phase record a pre-OPEN-PROGRAMS run left: ``prompt_id`` and no ``send_due``, no ``opened_at``."""
        ph = {"prompt_id": "x-s01-p1", "visit": 1, "send_due": False, "sent_at": NOW.isoformat()}
        self.assertTrue(programs.phase_opened(ph))
        self.assertFalse(programs.phase_opened({**ph, "send_due": True}))  # owed under the old code: not yet sent
        self.assertFalse(programs.phase_opened({"visit": 1, "send_due": True}))


class NoChatInterruptTest(ti.Base):
    def setUp(self):
        super().setUp()
        no_chat(self.cfg)

    def face(self, text):
        run = self.the_run()
        s, p = self.where()
        self.now += timedelta(minutes=1)
        return capture.add_web_block(self.cfg, programs.entry_stem(self.cfg, run, s), text, self.now,
                                     phase_prompt=run["sessions"][str(s)]["phases"][str(p)]["prompt_id"])

    def reach(self):
        self.start(session=10)
        for text in ("S10P1: words", "S10P2: words", "2026-11-02 07:00", "S10P4: words", "S10P5: words"):
            self.face(text)
            self.assertTrue(self.settle())
        return self.the_run()["id"]

    def test_an_interrupt_opens_at_its_time_and_is_answered_on_a_face(self):
        rid = self.reach()
        self.assertEqual(self.tick_at(self.local("2026-11-02T08:59")), [])
        self.assertIsNone(self.prompt(f"{rid}-s10-i1"))  # not yet its time
        self.assertEqual(self.tick_at(self.local("2026-11-02T09:01")), [])
        rec = self.prompt(f"{rid}-s10-i1")
        self.assertIsNotNone(rec)
        self.assertIsNone(rec["message_id"])
        self.assertEqual(rec["sent_at"], self.now.isoformat())
        self.assertEqual(self.items()[0]["status"], "open")
        self.assertEqual(self.bot.calls, [])
        self.assertEqual(sent_lines(self.cfg), [])
        stem = programs.entry_stem(self.cfg, self.the_run(), 10)
        info = capture.add_web_block(self.cfg, stem, "my phone, honestly", self.now + timedelta(minutes=3),
                                     interrupt_prompt=f"{rid}-s10-i1")
        self.assertEqual(info["prompt"]["id"], f"{rid}-s10-i1")
        body = self.entry(10).read_text(encoding="utf-8")
        self.assertIn("%% journal interrupt:1 %%", body)
        self.assertEqual(entries.interrupt_answers(body), {1: "my phone, honestly"})
        # later ticks leave it open; the insights document has his answer
        self.tick_at(self.local("2026-11-02T13:00"))
        self.assertEqual(self.items()[0]["status"], "open")
        text = programs.insights(self.cfg, rid, self.now, self.log).read_text(encoding="utf-8")
        self.assertIn("my phone, honestly", text)

    def test_an_interrupt_the_tick_never_reached_in_time_is_missed_quietly(self):
        rid = self.reach()
        self.assertEqual(self.tick_at(self.local("2026-11-03T12:00")), [])  # every grace window has passed
        self.assertEqual({i["status"] for i in self.items()}, {"missed"})
        self.assertIsNone(self.prompt(f"{rid}-s10-i1"))
        self.assertEqual(self.bot.calls, [])

    def test_a_face_answer_to_an_interrupt_not_yet_open_is_refused(self):
        rid = self.reach()
        stem = programs.entry_stem(self.cfg, self.the_run(), 10)
        with self.assertRaises(capture.PhaseClosed):
            capture.add_web_block(self.cfg, stem, "too early", self.now, interrupt_prompt=f"{rid}-s10-i1")


# ── the engine, with a chat: the /x hold covers the delivery only ────────────

class ChatHoldTest(rv.HookBase):
    def web(self, text):
        run = self.the_run()
        s, p = self.where()
        pid = run["sessions"][str(s)]["phases"][str(p)]["prompt_id"]
        return capture.add_web_block(self.cfg, programs.entry_stem(self.cfg, run, s), text, self.now, phase_prompt=pid)

    def window(self):
        """He goes off the record in the chat and keeps talking there."""
        self.msg("/x")
        self.now += timedelta(seconds=30)
        self.msg("private A: said in the chat")

    def test_a_run_answered_on_a_face_moves_on_while_the_chat_is_off_the_record(self):
        self.start()
        rid = self.the_run()["id"]
        self.now = tp.T0 + timedelta(minutes=1)
        self.window()
        self.now = tp.T0 + timedelta(minutes=2)
        self.web("my answer, written in the web app")
        sends = len(self.bot.phase_sends())
        self.now = tp.T0 + timedelta(minutes=5)
        privacy._CACHE.clear()
        self.assertTrue(programs.settle(self.cfg, rid, self.now, self.log))
        self.assertEqual(self.where(), (1, 2))  # current, and answerable on a face …
        ph = self.the_run()["sessions"]["1"]["phases"]["2"]
        self.assertTrue(programs.phase_opened(ph))
        self.assertTrue(ph["send_due"])  # … but its delivery waits for the window
        self.assertEqual(len(self.bot.phase_sends()), sends)
        rec = self.prompt(f"{rid}-s01-p2")
        self.assertIsNone(rec["message_id"])
        self.assertTrue(any("held back" in line for line in self.log))
        # answered on the face too, and moved on again: still nothing in the chat, and no chat boundary
        self.now = tp.T0 + timedelta(minutes=6)
        self.web("phase two, also in the web app")
        self.now = tp.T0 + timedelta(minutes=10)
        privacy._CACHE.clear()
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(self.where(), (1, 3))
        self.assertEqual(len(self.bot.phase_sends()), sends)
        self.assertFalse(self.the_run()["sessions"]["1"]["phases"]["2"].get("send_due"))  # nothing owed: it is behind him
        self.assertEqual([line["id"] for line in sent_lines(self.cfg)], [f"{rid}-s01-p1"])
        # once the window has closed the tick delivers the phase that is current
        self.now = tp.T0 + timedelta(minutes=10 + 46)
        privacy._CACHE.clear()
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.phase_sends()), sends + 1)
        rec = self.prompt(f"{rid}-s01-p3")
        self.assertTrue(rec["message_id"])
        self.assertEqual(rec["sent_at"], self.now.isoformat())  # the boundary is the delivery, never earlier
        self.assertFalse(self.the_run()["sessions"]["1"]["phases"]["3"]["send_due"])
        body = self.entry(1).read_text(encoding="utf-8")
        self.assertNotIn("private A", body)

    def test_a_settle_on_a_chat_answer_still_waits_for_the_window(self):
        self.start()
        rid = self.the_run()["id"]
        self.now = tp.T0 + timedelta(minutes=1)
        self.msg("my on-record answer in the chat")
        self.now = tp.T0 + timedelta(minutes=2)
        self.window()
        self.now = tp.T0 + timedelta(minutes=4)
        privacy._CACHE.clear()
        self.assertFalse(programs.settle(self.cfg, rid, self.now, self.log))
        self.assertEqual(self.where(), (1, 1))

    def test_an_owed_delivery_of_a_phase_answered_on_a_face_is_not_sent(self):
        self.start()
        rid = self.the_run()["id"]
        self.now = tp.T0 + timedelta(minutes=1)
        self.web("phase one in the web app")

        def down(cfg, method, payload):
            raise telegram.TelegramError("502 Bad Gateway")
        self.now = tp.T0 + timedelta(minutes=4)
        with mock.patch.object(telegram, "_call", down):
            self.assertTrue(programs.settle(self.cfg, rid, self.now, self.log))
        self.assertTrue(self.the_run()["sessions"]["1"]["phases"]["2"]["send_due"])
        self.now = tp.T0 + timedelta(minutes=5)
        self.web("phase two, while the chat was down")
        sends = len(self.bot.phase_sends())
        self.now = tp.T0 + timedelta(minutes=6)  # the tick, before the settle's two minutes are up
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.phase_sends()), sends)  # he has answered it: nothing is owed
        self.assertFalse(self.the_run()["sessions"]["1"]["phases"]["2"].get("send_due"))
        self.assertIsNone(self.prompt(f"{rid}-s01-p2")["message_id"])

    def test_an_undelivered_phase_takes_no_chat_message(self):
        self.start()
        rid = self.the_run()["id"]
        self.now = tp.T0 + timedelta(minutes=1)
        self.web("phase one in the web app")
        self.now = tp.T0 + timedelta(minutes=2)
        self.window()  # (the delivery of phase 2 will be held)
        self.now = tp.T0 + timedelta(minutes=4)
        privacy._CACHE.clear()
        self.assertTrue(programs.settle(self.cfg, rid, self.now, self.log))
        with ledger.locked(self.cfg.ledger_path) as data:
            got, _ = capture.match(data, self.cfg, tp.CHAT, None, self.now + timedelta(minutes=1))
            self.assertNotEqual((got or {}).get("id"), f"{rid}-s01-p2")
        self.assertNotIn(self.prompt(f"{rid}-s01-p2")["sent_at"],
                         [t.isoformat() for t in privacy.send_times(self.cfg)])


class ChatInterruptTest(ti.Base):
    def test_an_interrupt_held_by_the_window_is_open_on_a_face(self):
        rid = self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        before = len(self.sent_interrupts())
        with mock.patch.object(programs, "_off_the_record", return_value=True):
            self.tick_at(self.local("2026-11-02T09:01"))
        self.assertEqual(len(self.sent_interrupts()), before)
        rec = self.prompt(f"{rid}-s10-i1")
        self.assertIsNone(rec["message_id"])
        item = self.items()[0]
        self.assertEqual(item["status"], "pending")  # its delivery is still owed
        self.assertTrue(item["held_back"])
        stem = programs.entry_stem(self.cfg, self.the_run(), 10)
        capture.add_web_block(self.cfg, stem, "answered in the web app", self.now + timedelta(minutes=1),
                              interrupt_prompt=f"{rid}-s10-i1")
        # the window closes: an interrupt he has answered is not sent after all
        self.tick_at(self.local("2026-11-02T09:30"))
        self.assertEqual(len(self.sent_interrupts()), before)
        self.assertEqual(self.items()[0]["status"], "answered")
        self.assertIsNone(self.prompt(f"{rid}-s10-i1")["message_id"])

    def test_an_interrupt_still_goes_out_at_its_time(self):
        rid = self.reach_schedule_phase()
        self.confirm("2026-11-02 07:00")
        before = len(self.sent_interrupts())
        self.tick_at(self.local("2026-11-02T09:01"))
        self.assertEqual(len(self.sent_interrupts()), before + 1)
        self.assertEqual(self.items()[0]["status"], "sent")
        self.assertTrue(self.prompt(f"{rid}-s10-i1")["message_id"])


# ── the web app ──────────────────────────────────────────────────────────────

class WebCase(WriteCase):
    """The web app over a journal with no chat (``serve`` as built), the engine's spawned children run by hand at a
    clock the test holds."""
    chat = False

    def setUp(self):
        super().setUp()
        if not self.chat:
            no_chat(self.cfg)
        self.now = NOW
        self.app.clock = lambda: self.now
        self.sent = []
        self.asked = []

        def call(cfg, method, payload):
            self.sent.append((method, payload))
            return {"message_id": 900 + len(self.sent)}

        def model(*a, **k):
            self.asked.append(a)
            raise agent.AgentUnavailable("no model in this test")
        test = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return test.now.astimezone(tz) if tz else test.now
        for p in (mock.patch.object(telegram, "_call", call), mock.patch.object(telegram, "token", lambda c: "T"),
                  mock.patch.object(agent, "ask", model), mock.patch.object(programs, "datetime", Clock)):
            p.start()
            self.addCleanup(p.stop)
        self.log: list[str] = []

    def children(self):
        """Run what the web app spawned (``journal program …``), as the child would, and forget it."""
        todo, self.spawned[:] = list(self.spawned), []
        for args in todo:
            self.assertEqual(args[0], "program", args)
            action, rest = args[1], args[2:]
            if action == "start":
                programs.start(self.cfg, rest[0], self.now, self.log, only_new="--new" in rest)
            elif action == "settle":
                self.now += timedelta(minutes=3)   # (the child waits programs.settle_minutes first)
                programs.settle(self.cfg, rest[0], self.now, self.log)
            elif action == "next":
                expect = programs.parse_where(rest[rest.index("--expect") + 1]) if "--expect" in rest else None
                programs.advance(self.cfg, rest[0], self.now, self.log, reason="next", expect=expect)
            else:
                self.fail(f"unexpected spawn {args}")

    def run_one(self, rid):
        return self.get_json(f"/api/v1/runs/{rid}")


class WebOnlyTenSessionsTest(WebCase):
    def test_ten_sessions_from_the_web_app_alone(self):
        """``test_ten_sessions_with_no_model`` with no chat: started, answered and moved on from the web app."""
        self.ok("POST", "/api/v1/runs", {"program": "fix-life"}, want=202)
        self.children()
        rid = programs.current_run(self.cfg, "fix-life")["id"]
        prog = tp.library.load(self.cfg).program("fix-life")
        for s in range(1, 11):
            if s > 1:
                self.now += timedelta(hours=2)
                one = self.run_one(rid)
                self.assertIsNone(one["phase"])  # between sessions: Next begins the next one
                self.ok("POST", f"/api/v1/runs/{rid}/next", {"expect": one["where"]}, want=202)
                self.children()
            stem = programs.find_run(self.cfg, rid)["sessions"][str(s)]["entry"]
            for p in range(1, len(prog.session(s).phases) + 1):
                one = self.run_one(rid)
                ph = one["phase"]
                self.assertEqual((ph["session"], ph["phase"], ph["delivered"], ph["open"], ph["in_chat"]),
                                 (s, p, True, True, False))
                words = CONFIRM if (s, p) == (10, 3) else f"S{s}P{p}: words of mine for this phase"
                got = self.ok("POST", f"/api/v1/entries/{stem}/blocks", {"text": words, "phase": p}, want=201)
                self.assertEqual(got["advance"], "settle")
                self.children()
        run = programs.find_run(self.cfg, rid)
        self.assertEqual(run["status"], "completed")
        self.assertEqual(self.sent, [])  # nothing sent, attempted or counted
        self.assertEqual(sent_lines(self.cfg), [])
        prompts = ledger.snapshot(self.cfg.ledger_path)["prompts"]
        self.assertEqual(len([q for q in prompts if q.get("run") == rid and q.get("phase")]), 51)
        self.assertFalse(any(q.get("message_id") or q.get("send_attempts") for q in prompts if q.get("run") == rid))
        for s in range(1, 11):
            body = programs.entry_path(self.cfg, run, s).read_text(encoding="utf-8")
            self.assertRegex(entries.summary_text(body), rf"S{s}P[0-9]: words of mine for this phase")
        self.assertTrue(run.get("insights_entry"))
        self.assertEqual(len([i for i in run["scheduled"] if i["status"] == "pending"]), 8)
        self.assertEqual(status.status(self.cfg, self.now)["undelivered"], [])
        self.assertEqual(len(self.asked), 4)  # the four reflect constructions, asked by the engine, never by serve

        # the protocol day: each interrupt opens at its time and is answered in the web app
        stem = run["sessions"]["10"]["entry"]
        for n, at in enumerate(("09", "11"), 1):
            self.now = schedule.wall_clock(self.cfg, f"2026-10-27T{at}:01").astimezone(UTC)
            self.assertEqual(programs.send_interrupts(self.cfg, self.now, self.log), [])
            self.ok("POST", f"/api/v1/entries/{stem}/blocks", {"text": f"interrupt {n}, from the web", "interrupt": n},
                    want=201)
        self.assertEqual(self.spawned, [])  # an interrupt answer moves nothing
        body = programs.entry_path(self.cfg, run, 10).read_text(encoding="utf-8")
        self.assertEqual(entries.interrupt_answers(body), {1: "interrupt 1, from the web", 2: "interrupt 2, from the web"})
        self.assertEqual(self.sent, [])


class WebPhaseTest(WebCase):
    chat = True

    def setUp(self):
        super().setUp()
        self.rid = programs.start(self.cfg, "fix-life", self.now, self.log)["id"]
        self.stem = programs.find_run(self.cfg, self.rid)["sessions"]["1"]["entry"]

    def test_a_phase_owed_a_delivery_is_open_on_the_web(self):
        run = programs.find_run(self.cfg, self.rid)
        run["sessions"]["1"]["phases"]["1"]["send_due"] = True  # (its delivery held by a /x window, say)
        programs._save(self.cfg, run)
        ph = self.run_one(self.rid)["phase"]
        self.assertEqual((ph["delivered"], ph["open"], ph["in_chat"]), (True, True, False))
        self.ok("POST", f"/api/v1/entries/{self.stem}/blocks", {"text": "x", "phase": 1}, want=201)

    def test_a_phase_not_yet_current_is_not_open(self):
        run = programs.find_run(self.cfg, self.rid)
        programs._arrive(run, 1, 1)  # (what a transition leaves until the phase is opened)
        programs._save(self.cfg, run)
        ph = self.run_one(self.rid)["phase"]
        self.assertEqual((ph["delivered"], ph["open"]), (False, False))
        self.ok("POST", f"/api/v1/entries/{self.stem}/blocks", {"text": "x", "phase": 1}, want=409)

    def test_a_delivered_phase_is_in_the_chat(self):
        ph = self.run_one(self.rid)["phase"]
        self.assertEqual((ph["delivered"], ph["open"], ph["in_chat"]), (True, True, True))


class WebInterruptTest(WebCase):
    def setUp(self):
        super().setUp()
        run = programs.start(self.cfg, "fix-life", self.now, self.log, session=10)
        self.rid = run["id"]
        self.stem = run["sessions"]["10"]["entry"]
        for p, words in ((1, "a"), (2, "b"), (3, CONFIRM), (4, "d"), (5, "e")):
            self.ok("POST", f"/api/v1/entries/{self.stem}/blocks", {"text": words, "phase": p}, want=201)
            self.children()
        self.now = schedule.wall_clock(self.cfg, "2026-10-27T09:01").astimezone(UTC)
        programs.send_interrupts(self.cfg, self.now, self.log)

    def block(self, body, want):
        return self.ok("POST", f"/api/v1/entries/{self.stem}/blocks", body, want=want)

    def test_an_open_interrupt_takes_a_block(self):
        got = self.block({"text": "my answer", "interrupt": 1}, 201)
        self.assertEqual(got["stem"], self.stem)
        self.assertNotIn("advance", got)
        self.assertEqual(self.spawned, [])

    def test_an_interrupt_not_yet_open_is_409(self):
        got = self.block({"text": "early", "interrupt": 2}, 409)
        self.assertEqual(got["error"], "interrupt-not-open")
        self.block({"text": "no such", "interrupt": 9}, 409)

    def test_a_bad_interrupt_or_both_keys_are_400(self):
        for body in ({"text": "x", "interrupt": "1"}, {"text": "x", "interrupt": True},
                     {"text": "x", "interrupt": 1, "phase": 5}):
            self.block(body, 400)

    def test_off_the_record_and_reserved_lines_are_refused(self):
        before = (self.cfg.root / f"{self.stem}.md").read_text(encoding="utf-8")
        for text, code in (("/x my private answer", "off-the-record"), ("fine\n/x but this", "off-the-record"),
                           ("%% journal phase:3 %%", "reserved-line"),
                           (audio.TRANSCRIBING, "placeholder")):
            got = self.block({"text": text, "interrupt": 1}, 422)
            self.assertEqual(got["error"], code)
        self.assertEqual((self.cfg.root / f"{self.stem}.md").read_text(encoding="utf-8"), before)
        self.assertEqual(ledger.answers(self.prompt(f"{self.rid}-s10-i1")), [])

    def test_answering_an_interrupt_reads_no_chat(self):
        """``test_serve_privacy``'s rule for the new body: the sessions database and the privacy pass stay closed."""
        def refuse(*a, **k):
            raise AssertionError("journal serve reached for the chat")
        with mock.patch.object(sqlite3, "connect", refuse), mock.patch.object(privacy, "view", refuse):
            self.block({"text": "my answer", "interrupt": 1}, 201)
            self.assertTrue(self.run_one(self.rid)["scheduled"])


class ChatScheduleNoteTest(ti.Base):
    def test_a_face_settled_schedule_phase_says_nothing_in_the_chat_during_a_window(self):
        """A face's answer now settles while the chat is off the record; the engine's notes about it wait too."""
        rid = self.reach_schedule_phase()
        stem = programs.entry_stem(self.cfg, self.the_run(), 10)
        self.now += timedelta(minutes=1)
        capture.add_web_block(self.cfg, stem, "2026-11-02 07:00", self.now, phase_prompt=f"{rid}-s10-p3")
        before = len(self.bot.sends())
        self.now += timedelta(minutes=3)
        with mock.patch.object(programs, "_off_the_record", return_value=True):
            self.assertTrue(programs.settle(self.cfg, rid, self.now, self.log))
        self.assertEqual(self.where(), (10, 4))
        self.assertEqual(len(self.items()), 8)  # the interrupts are timed …
        self.assertEqual(len(self.bot.sends()), before)  # … and neither the note nor phase 4 went to the chat
        self.assertTrue(any("note held back" in line for line in self.log))
