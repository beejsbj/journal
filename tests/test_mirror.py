"""What the journal sends is mirrored into the companion's own chat session (user role, framed), best-effort;
and those mirrored rows are never read back as his words: not by the pack, the sweep or the quote verifier."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CHAT, CleanEnv, add_prompt, make_cfg
from test_reflect import Base as ReflectBase, FakeBot
from test_sweep import FakeSessions

from journal import agent, capture, context, entries, ledger, mirror, reflect, sweep, telegram, tick, validate

IST = ZoneInfo("Asia/Kolkata")


class Recorder:
    """Stands in for gateway.mirror.mirror_to_session; records every call."""

    def __init__(self, result=True, exc=None):
        self.calls, self.result, self.exc = [], result, exc

    def __call__(self, platform, chat_id, text, source_label, role, session_id):
        self.calls.append({"platform": platform, "chat_id": chat_id, "text": text, "source": source_label, "role": role,
                           "session_id": session_id})
        if self.exc:
            raise self.exc
        return self.result


def under_hermes(test, cfg, recorder, db: FakeSessions | None = None):
    """cfg runs in a (temporary) Hermes profile home whose state.db is ``db`` (a fresh one by default), with
    mirror_to_session replaced by ``recorder``."""
    if db is None:
        db = FakeSessions(Path(test.tmp.name) / "hermes-home" / "state.db")
    home = db.path.parent
    cfg.hermes_home = home
    cfg.hermes["state_db"] = None
    env = mock.patch.dict(os.environ, {"HERMES_HOME": str(home)})
    rec = mock.patch.object(mirror, "_mirror_to_session", recorder)
    for p in (env, rec):
        p.start()
        test.addCleanup(p.stop)


class ToChatTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))

    def test_standalone_is_a_no_op(self):
        log = []
        self.assertFalse(mirror.to_chat(self.cfg, "Iris sent the morning prompt at 06:01 IST", "body", log))
        self.assertEqual(log, [])  # helpers make a real mirror call raise, so nothing was attempted

    def test_user_role_and_framing(self):
        rec = Recorder()
        under_hermes(self, self.cfg, rec)
        self.assertTrue(mirror.to_chat(self.cfg, "Iris sent the morning prompt at 06:01 IST", "\nHello\n"))
        self.assertEqual(rec.calls, [{"platform": "telegram", "chat_id": CHAT, "source": "journal", "role": "user",
                                      "session_id": "tg",
                                      "text": "[Journal: Iris sent the morning prompt at 06:01 IST]\nHello"}])
        self.assertTrue(mirror.is_mirror(rec.calls[0]["text"]))
        self.assertFalse(mirror.is_mirror("[Journal reply — saved]\n\nmy words"))  # the hook's note is different

    def test_switch_off_and_a_foreign_hermes_home(self):
        rec = Recorder()
        under_hermes(self, self.cfg, rec)
        self.cfg.hermes["mirror_to_chat"] = False
        self.assertFalse(mirror.to_chat(self.cfg, "x", "y"))
        self.cfg.hermes["mirror_to_chat"] = True
        with mock.patch.dict(os.environ, {"HERMES_HOME": str(Path(self.tmp.name) / "another-profile")}):
            log = []
            self.assertFalse(mirror.to_chat(self.cfg, "x", "y", log))
            self.assertIn("HERMES_HOME is not the journal's profile home", log[0])
        self.assertEqual(rec.calls, [])

    def test_failures_are_contained(self):
        FakeSessions(Path(self.tmp.name) / "state.db")
        for recorder, why in ((Recorder(result=False), "Hermes did not write the row"),
                              (Recorder(exc=RuntimeError("database is locked")), "RuntimeError: database is locked")):
            with self.subTest(why=why):
                with mock.patch.object(mirror, "_mirror_to_session", recorder), \
                        mock.patch.dict(os.environ, {"HERMES_HOME": str(Path(self.tmp.name))}):
                    self.cfg.hermes_home = Path(self.tmp.name)
                    log = []
                    self.assertFalse(mirror.to_chat(self.cfg, "Iris sent x", "y", log))
                    self.assertIn(why, log[0])
                    self.assertNotIn("failed", log[0])  # not an error line for errors.jsonl

    def test_no_live_session_or_a_foreign_database(self):
        db = FakeSessions(Path(self.tmp.name) / "hermes-home" / "state.db")
        under_hermes(self, self.cfg, Recorder(), db)
        db.sql("update sessions set ended_at = 5 where id = 'tg'")
        log = []
        self.assertFalse(mirror.to_chat(self.cfg, "x", "y", log))
        self.assertIn("no live chat session for the chat", log[0])
        db.sql("update sessions set ended_at = null where id = 'tg'")
        self.cfg.hermes["state_db"] = str(Path(self.tmp.name) / "elsewhere.db")
        log = []
        self.assertFalse(mirror.to_chat(self.cfg, "x", "y", log))
        self.assertIn("hermes.state_db is not the profile's own database", log[0])

    def test_newest_live_session_wins_and_other_users_refuse(self):
        db = FakeSessions(Path(self.tmp.name) / "hermes-home" / "state.db")
        rec = Recorder()
        under_hermes(self, self.cfg, rec, db)
        db.sql("insert into sessions (id, source, chat_id, session_key, user_id, started_at) values "
               "('tg2', 'telegram', ?, 'agent:iris:telegram:dm:x', ?, 9.0)", CHAT, CHAT)
        mirror.to_chat(self.cfg, "x", "y")
        self.assertEqual(rec.calls[-1]["session_id"], "tg2")
        db.sql("update sessions set user_id = 'someone-else' where id = 'tg'")
        log = []
        self.assertFalse(mirror.to_chat(self.cfg, "x", "y", log))
        self.assertIn("several people's sessions share the chat", log[0])

    def test_queued_while_a_turn_runs_then_flushed_in_order(self):
        db = FakeSessions(Path(self.tmp.name) / "hermes-home" / "state.db")
        rec = Recorder()
        under_hermes(self, self.cfg, rec, db)
        add_prompt(self.cfg, kind="evening", local_date="2026-10-14", message_id="5")
        db.sql("insert into session_turn_leases values ('tg', 'pid=1:turn=a', 0, ?)", datetime.now().timestamp() + 300)
        log = []
        self.assertEqual(mirror.to_chat(self.cfg, "first", "1", log, ref=("prompts", "2026-10-14-evening", None)),
                         "queued")
        self.assertEqual(mirror.to_chat(self.cfg, "second", "2", log), "queued")
        self.assertEqual(rec.calls, [])
        self.assertEqual(ledger.snapshot(self.cfg.ledger_path)["prompts"][0]["mirrored"], "queued")
        mirror.flush(self.cfg, datetime.now().astimezone(), log)  # still mid-turn
        self.assertEqual(rec.calls, [])
        db.sql("update session_turn_leases set expires_at = 1")  # the turn ended
        log = []
        mirror.flush(self.cfg, datetime.now().astimezone(), log)
        self.assertEqual([c["text"] for c in rec.calls], ["[Journal: first]\n1", "[Journal: second]\n2"])
        self.assertEqual(log, ["mirrored (after a turn ended): first", "mirrored (after a turn ended): second"])
        self.assertIs(ledger.snapshot(self.cfg.ledger_path)["prompts"][0]["mirrored"], True)
        self.assertFalse((self.cfg.state_dir / mirror.QUEUE).exists())

    def test_a_compressed_conversation_is_leased_under_its_root(self):
        db = FakeSessions(Path(self.tmp.name) / "hermes-home" / "state.db")
        db.sql("update sessions set ended_at = 4, end_reason = 'compression' where id = 'tg'")
        db.sql("insert into sessions (id, source, chat_id, session_key, user_id, started_at, parent_session_id) values "
               "('tg-2', 'telegram', ?, 'k', ?, 5.0, 'tg')", CHAT, CHAT)
        db.sql("insert into session_turn_leases values ('tg', 'pid=1', 0, ?)", datetime.now().timestamp() + 300)
        self.assertTrue(mirror.turn_running(db.path, "tg-2"))
        self.assertFalse(mirror.turn_running(db.path, "other"))

    def test_stale_queue_items_are_dropped(self):
        under_hermes(self, self.cfg, Recorder())
        mirror._queue(self.cfg, {"text": "[Journal: x]\ny", "what": "x", "ref": None,
                                 "queued_at": (datetime.now().astimezone() - timedelta(hours=13)).isoformat()})
        log = []
        mirror.flush(self.cfg, datetime.now().astimezone(), log)
        self.assertEqual(log, ["mirror dropped, never got a turn boundary: x"])

    def test_without_hermes_in_this_process_the_tick_writes_it(self):
        db = FakeSessions(Path(self.tmp.name) / "hermes-home" / "state.db")
        under_hermes(self, self.cfg, Recorder(exc=ImportError("no gateway")), db)  # a manual /morning child process
        log = []
        self.assertEqual(mirror.to_chat(self.cfg, "Iris sent the morning prompt again at 09:12 IST", "body", log),
                         "queued")
        rec = Recorder()
        with mock.patch.object(mirror, "_mirror_to_session", rec):  # the next tick, on Hermes' Python
            mirror.flush(self.cfg, datetime.now().astimezone(), log)
        self.assertEqual(rec.calls[0]["text"], "[Journal: Iris sent the morning prompt again at 09:12 IST]\nbody")
        mirror._queue(self.cfg, {"text": "[Journal: y]\nz", "what": "y", "ref": None,
                                 "queued_at": datetime.now().astimezone().isoformat()})
        log = []
        mirror.flush(self.cfg, datetime.now().astimezone(), log)  # a tick that cannot import Hermes either: dropped
        self.assertEqual(log, ["not mirrored into the chat session (Hermes is not importable here): y"])
        self.assertFalse((self.cfg.state_dir / mirror.QUEUE).exists())

    def test_with_no_transcript_a_queued_mirror_is_not_recorded_as_mirrored(self):
        rec = Recorder()
        under_hermes(self, self.cfg, rec)
        self.cfg.adapter = "none"  # a profile home, but no chat to write into
        add_prompt(self.cfg, kind="evening", local_date="2026-10-14", message_id="5")
        mirror._queue(self.cfg, {"text": "[Journal: x]\ny", "what": "the evening prompt",
                                 "ref": ["prompts", "2026-10-14-evening", None],
                                 "queued_at": datetime.now().astimezone().isoformat()})
        log = []
        mirror.flush(self.cfg, datetime.now().astimezone(), log)
        self.assertEqual(rec.calls, [])
        self.assertEqual(log, ["not mirrored into the chat session (no chat session): the evening prompt"])
        self.assertIs(ledger.snapshot(self.cfg.ledger_path)["prompts"][0]["mirrored"], False)
        self.assertFalse((self.cfg.state_dir / mirror.QUEUE).exists())

    def test_long_bodies_are_capped(self):
        text = mirror.frame("w", "line\n" * 5000)
        self.assertLess(len(text), mirror.CAP + 40)
        self.assertTrue(text.endswith("\n[…]"))


class SendMirrorTest(CleanEnv):
    """Every successful send is mirrored once, after it is in the ledger; a failed mirror never fails the send."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.sources["weather"] = {"enabled": False}
        self.bot = FakeBot()
        for p in (mock.patch.object(telegram, "_call", self.bot), mock.patch.object(telegram, "token", lambda c: "T"),
                  mock.patch.object(agent, "ask", lambda *a, **k: None)):  # no model: the plain opener
            p.start()
            self.addCleanup(p.stop)

    def send(self, kind="evening", when=datetime(2026, 10, 14, 23, 1, tzinfo=IST)):
        log = []
        tick.send_due(self.cfg, when, log, only=kind)
        return log

    def prompt(self, pid="2026-10-14-evening"):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)

    def test_a_prompt_is_mirrored_with_its_full_text(self):
        rec = Recorder()
        under_hermes(self, self.cfg, rec)
        self.send()
        self.assertEqual(len(rec.calls), 1)
        call = rec.calls[0]
        head, body = call["text"].split("\n", 1)
        self.assertRegex(head, r"^\[Journal: Iris sent the evening prompt at \d\d:\d\d IST\]$")
        self.assertEqual(call["role"], "user")
        self.assertEqual(body, self.prompt()["plain_text"])
        self.assertIn("Evening, Theo 🌻", body)  # the opener ...
        self.assertIn("What's spinning in your mind?", body)  # ... and the question bank
        self.assertIs(self.prompt()["mirrored"], True)

    def test_a_manual_resend_says_so(self):
        rec = Recorder()
        under_hermes(self, self.cfg, rec)
        self.send()
        self.send()
        self.assertIn("sent the evening prompt again at", rec.calls[1]["text"].split("\n")[0])

    def test_a_failed_mirror_never_fails_the_send(self):
        under_hermes(self, self.cfg, Recorder(exc=RuntimeError("boom")))
        log = self.send()
        p = self.prompt()
        self.assertTrue(p["message_id"])
        self.assertIs(p["mirrored"], False)
        self.assertTrue(any(l.startswith("sent 2026-10-14-evening") for l in log))
        self.assertTrue(any("not mirrored" in l for l in log))

    def test_standalone_records_not_mirrored(self):
        self.send()
        self.assertIs(self.prompt()["mirrored"], False)

    def test_dry_run_mirrors_nothing(self):
        rec = Recorder()
        under_hermes(self, self.cfg, rec)
        tick.send_due(self.cfg, datetime(2026, 10, 14, 23, 1, tzinfo=IST), [], only="evening", dry_run=True)
        self.assertEqual(rec.calls, [])


class AnnotationAndReflectionMirrorTest(ReflectBase):
    def setUp(self):
        super().setUp()
        self.rec = Recorder()
        under_hermes(self, self.cfg, self.rec)

    def test_annotation_reply_is_mirrored(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway", sent + timedelta(minutes=9), "11")
        tick.reply_annotations(self.cfg, sent + timedelta(hours=1), [])
        call = self.rec.calls[-1]
        self.assertEqual(call["text"], "[Journal: Iris's annotations on 2026-10-14-evening, sent at 00:01 IST as a "
                                       "reply to his entry]\n- He walked in the rain.")
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-evening")
        self.assertIs(p["annotation_reply"]["mirrored"], True)
        tick.reply_annotations(self.cfg, sent + timedelta(hours=2), [])
        self.assertEqual(len(self.rec.calls), 1)  # sent once, mirrored once

    def test_reflection_is_mirrored(self):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", "rain all day, walked anyway. the soup was good. "
                                                    "the bus can wait", sent + timedelta(minutes=9), "11")
        self.reflect(datetime(2026, 10, 15, 0, 30, tzinfo=IST))
        call = self.rec.calls[-1]
        head, body = call["text"].split("\n", 1)
        self.assertEqual(head, "[Journal: Iris's reflection — Wed 14 Oct 2026, sent at 00:30 IST]")
        self.assertIn("**What stood out**", body)
        self.assertNotIn("FOLLOW-UP", body)
        self.assertIs(self.rec_of()["telegram"]["mirrored"], True)

    def rec_of(self):
        return self.rec_ledger("2026-10-14-reflection")

    def rec_ledger(self, rid):
        return reflect.find(ledger.snapshot(self.cfg.ledger_path), rid)


MIRRORED = ("[Journal: Iris sent the evening prompt at 23:01 IST]\n🌻 Evening journal · Wed 14 Oct\n\n"
            "Evening 🌻 the river was loud with rain and the lamps came on early.\n\n1. Brain Dump")


class NeverHisWordsTest(unittest.TestCase):
    """A mirrored row is the companion's own text sitting in a user row: no reader may take it for his."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = make_cfg(t)
        self.cfg.sources["weather"] = {"enabled": False}
        self.db = FakeSessions(t / "state.db")
        self.cfg.hermes["state_db"] = str(self.db.path)
        self.sent = datetime.now(timezone.utc) - timedelta(hours=1)
        self.local = self.sent.astimezone(IST)
        add_prompt(self.cfg, kind="evening", local_date=self.local.date().isoformat(), sent=self.sent, message_id="50")
        self.db.add(MIRRORED, self.sent + timedelta(seconds=2))  # the mirror: no platform message id
        self.db.add("[Journal: Iris sent x]\nnow with an id", self.sent + timedelta(seconds=3), mid="51")
        self.db.add("the lamps were warm tonight", self.sent + timedelta(minutes=10), mid="52")

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_sweep_never_captures_one(self):
        self.assertIsNone(sweep.parse(MIRRORED, None))
        self.assertIsNone(sweep.parse("[Journal: Iris sent x]\nwords", "51"))
        log = []
        sweep.run(self.cfg, datetime.now(timezone.utc), log)
        p = ledger.snapshot(self.cfg.ledger_path)["prompts"][0]
        self.assertEqual([c["platform_message_id"] for c in p["captures"]], ["52"], log)  # his words, not hers
        body = Path(p["entry_path"]).read_text()
        self.assertIn("the lamps were warm tonight", body)
        self.assertNotIn("river was loud", body)

    def test_the_pack_and_the_verifier_ignore_it(self):
        self.assertEqual([l.split(" — ", 1)[1] for l in context.telegram_messages(self.cfg)],
                         ["the lamps were warm tonight"])
        pack = context.build(self.cfg, datetime.now(IST))
        self.assertNotIn("river was loud", pack)
        verify = context.make_verifier(self.cfg)
        self.assertTrue(verify("telegram:x", validate.norm("the lamps were warm tonight")))
        self.assertFalse(verify("telegram:x", validate.norm("the river was loud with rain")))
        self.assertFalse(verify("telegram:x", validate.norm("now with an id")))


class ConversationShapeTest(ReflectBase):
    """The pack shows his messages and her journal messages interleaved by time; hers are marked and not citable."""

    def test_interleaved_oldest_first(self):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        day = (now - timedelta(hours=3)).astimezone(IST).date().isoformat()
        sent = now - timedelta(hours=3)
        add_prompt(self.cfg, kind="evening", local_date=day, sent=sent, message_id="60")
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, f"{day}-evening").update(opener="Evening 🌻 what was cozy today?", opener_source="iris")
        info = capture.capture(self.cfg, CHAT, "60", "tea and the window", sent + timedelta(minutes=5), "text", "61")
        entries.write_annotations(Path(info["path"]), "- He found comfort in tea.", self.cfg.names)
        with ledger.locked(self.cfg.ledger_path) as data:
            p = ledger.find(data, f"{day}-evening")
            p["annotation_reply"] = {"message_id": "62", "sent_at": (sent + timedelta(minutes=40)).isoformat()}
        db = FakeSessions(Path(self.tmp.name) / "state.db")
        self.cfg.hermes["state_db"] = str(db.path)
        db.add("tea and the window", sent + timedelta(minutes=5), mid="61")
        db.add("[Journal: Iris's annotations on x]\n- He found comfort in tea.", sent + timedelta(minutes=40))
        db.add("also, the cat came back", sent + timedelta(minutes=50), mid="63")
        self.cfg.sources["weather"] = {"enabled": False}
        lines = context.conversation(self.cfg, now.astimezone(IST))
        self.assertEqual(len(lines), 4, lines)
        self.assertTrue(lines[0].startswith("- [you] ") and "Evening journal prompt · answered: Evening 🌻" in lines[0])
        self.assertTrue(lines[1].startswith("- telegram:") and lines[1].endswith("— tea and the window"))
        self.assertTrue(lines[2].startswith("- [you] ") and "your annotations on his evening entry (a reply to it): "
                                                            "- He found comfort in tea." in lines[2])
        self.assertTrue(lines[3].endswith("— also, the cat came back"))
        pack = context.build(self.cfg, now.astimezone(IST))
        self.assertNotIn("what was cozy", validate.citable(pack))
        self.assertIn("also, the cat came back", validate.citable(pack))


class HookNoteTest(unittest.TestCase):
    """The hook's note quotes her opener; wherever it sits, it never counts as his words."""

    def test_after_a_reply_quote_and_after_a_transcript(self):
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        p = add_prompt(cfg, kind="morning", local_date="2026-10-15")
        p["opener"] = 'Morning 🌻 you wrote "a slow [start]" — a coffee day?'
        note = capture.context_note(cfg, {"prompt": p, "path": "/j/2026-10-15-morning.md"})
        for content in (f'[Replying to: "🌻 Morning journal · Thu 15 Oct"]\n\n"slept in, breakfast now"\n\n{note}',
                        f'{note}slept in, breakfast now', f'"slept in, breakfast now"\n\n{note}'.rstrip()):
            with self.subTest(content=content[:30]):
                text = context.clean_user_text(content)
                self.assertIn("slept in, breakfast now", text)
                self.assertNotIn("coffee", text)
                self.assertNotIn("Journal reply", text)
        j = capture.note_context(cfg, {"path": "/j/x.md"}) + "trying out this journal"
        self.assertEqual(context.clean_user_text(j), "trying out this journal")


if __name__ == "__main__":
    unittest.main()
