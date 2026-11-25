"""``chat.deliver`` (issue #43, CHAT-2): one operation for every message the engine sends on its own. Send, then the
caller's ``record``, then the mirror of what ``record`` returned; the rules (no chat, ``/x``, pacing, staleness, the
catch-up letter's hold) stay with the callers, which the conversion tests below show are each routed through it."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import make_cfg

from journal import mirror
from journal.chat.deliver import Delivery, Mirror, deliver
from journal.ports import ChannelError


class FakeChat:
    """A channel that records every send (its keywords as given) and fails while ``fail`` has items."""

    def __init__(self, events, fail=()):
        self.events, self.fail, self.sends = events, list(fail), []

    def send(self, chat_id, html, **kw):
        self.events.append("send")
        self.sends.append((chat_id, html, kw))
        if self.fail:
            raise self.fail.pop(0)
        return str(700 + len(self.sends))


class DeliverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.events: list = []
        self.told: list = []
        p = mock.patch.object(mirror, "to_chat", lambda cfg, what, body, log, **k: (
            self.events.append("mirror"), self.told.append((what, body, k)), log.append("mirror line")))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def record(self, told=Mirror("what", "body", ("prompts", "p", None))):
        def rec(d: Delivery):
            self.events.append(("record", d.message_id, None if d.error is None else str(d.error)))
            return told if d.message_id else None
        return rec

    def test_send_then_record_then_mirror_in_that_order(self):
        chat, log = FakeChat(self.events), []
        d = deliver(self.cfg, "<b>hi</b>", log, record=self.record(), chat=chat)
        self.assertEqual(self.events, ["send", ("record", "701", None), "mirror"])
        self.assertEqual(chat.sends, [(self.cfg.chat_id, "<b>hi</b>", {})])  # no keyword the caller did not pass
        self.assertEqual(self.told, [("what", "body", {"ref": ("prompts", "p", None)})])
        self.assertEqual((d.message_id, d.error, d.chat, d.told), ("701", None, chat, Mirror("what", "body",
                                                                                              ("prompts", "p", None))))

    def test_keywords_and_destination_pass_through_as_given(self):
        chat = FakeChat(self.events)
        deliver(self.cfg, "x", [], record=self.record(), chat=chat, to="555", reply_to=None, silent=True)
        self.assertEqual(chat.sends, [("555", "x", {"reply_to": None, "silent": True})])

    def test_a_mirror_without_a_ref_is_framed_without_one(self):
        deliver(self.cfg, "x", [], record=self.record(Mirror("what", "body")), chat=FakeChat(self.events))
        self.assertEqual(self.told, [("what", "body", {})])

    def test_a_failed_send_is_handed_to_record_and_nothing_is_mirrored(self):
        chat = FakeChat(self.events, [ChannelError("Bad Gateway")])
        d = deliver(self.cfg, "x", [], record=self.record(), chat=chat)
        self.assertEqual(self.events, ["send", ("record", None, "Bad Gateway")])
        self.assertIsNone(d.told)

    def test_record_returning_none_mirrors_nothing_even_when_sent(self):
        deliver(self.cfg, "x", [], record=lambda d: None, chat=FakeChat(self.events))
        self.assertEqual(self.events, ["send"])

    def test_an_error_outside_catch_propagates_without_record(self):
        with self.assertRaises(ValueError):
            deliver(self.cfg, "x", [], record=self.record(), chat=FakeChat(self.events, [ValueError("odd")]))
        self.assertEqual(self.events, ["send"])
        deliver(self.cfg, "x", [], record=self.record(), chat=FakeChat(self.events, [ValueError("odd")]),
                catch=(ChannelError, ValueError))
        self.assertEqual(self.events[-1], ("record", None, "odd"))

    def test_fallback_sends_its_text_once_after_a_channel_error(self):
        chat, seen = FakeChat(self.events, [ChannelError("can't parse entities")]), []
        d = deliver(self.cfg, "rich", [], record=self.record(), chat=chat,
                    fallback=lambda e: seen.append(str(e)) or "plain")
        self.assertEqual(seen, ["can't parse entities"])
        self.assertEqual([s[1] for s in chat.sends], ["rich", "plain"])
        self.assertEqual(d.message_id, "702")
        chat = FakeChat(self.events, [ChannelError("a"), ChannelError("b")])
        d = deliver(self.cfg, "rich", [], record=self.record(), chat=chat, fallback=lambda e: "plain")
        self.assertEqual(str(d.error), "b")  # the second failure is the one handed on

    def test_the_channel_is_made_inside_the_send(self):
        with mock.patch.object(type(self.cfg), "channel", side_effect=ChannelError("no way to send")):
            d = deliver(self.cfg, "x", [], record=self.record())
        self.assertEqual((d.chat, str(d.error)), (None, "no way to send"))
        self.assertEqual(self.events, [("record", None, "no way to send")])

    def test_record_may_raise(self):
        def boom(d):
            raise RuntimeError("alert")
        with self.assertRaises(RuntimeError):
            deliver(self.cfg, "x", [], record=boom, chat=FakeChat(self.events))
        self.assertEqual(self.events, ["send"])



class Spy:
    """Wraps ``deliver`` where a sender calls it, recording each call, and lets the real one run."""

    def __init__(self):
        self.calls = []

    def __call__(self, cfg, html, log, **kw):
        self.calls.append((html, kw))
        return deliver(cfg, html, log, **kw)


class ConversionCase(unittest.TestCase):
    """Each kind of send goes through ``deliver``: one call per message, with the keywords it always sent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))
        self.events: list = []
        self.chat = FakeChat(self.events)
        self.told: list = []
        for p in (mock.patch.object(type(self.cfg), "channel", lambda cfg: self.chat),
                  mock.patch.object(mirror, "to_chat", lambda cfg, what, body, log, **k: (
                      self.events.append("mirror"), self.told.append((what, body, k.get("ref"))))[-1] and None)):
            p.start()
            self.addCleanup(p.stop)

    def spy(self, module):
        s = Spy()
        p = mock.patch.object(module, "deliver", s)
        p.start()
        self.addCleanup(p.stop)
        return s


class PromptTest(ConversionCase):
    def record(self):
        return {"id": "2026-10-14-evening", "kind": "evening", "local_date": "2026-10-14",
                "scheduled": "2026-10-14T17:00:00+00:00", "chat_id": self.cfg.chat_id, "html": "<b>Evening</b>",
                "plain_text": "Evening"}

    def test_a_prompt_is_one_delivery(self):
        from journal.chat import send
        from journal import compose, ledger
        spy, log = self.spy(send), []
        self.assertTrue(send.send_prompt(self.cfg, self.record(), log, keyboard=compose.KEYBOARD))
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(self.chat.sends, [(self.cfg.chat_id, "<b>Evening</b>", {"keyboard": compose.KEYBOARD})])
        self.assertEqual(self.events, ["send", "mirror"])
        self.assertEqual(self.told[0][1:], ("Evening", ("prompts", "2026-10-14-evening", None)))
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-evening")
        self.assertEqual(p["message_id"], "701")

    def test_a_failed_prompt_falls_back_once_then_is_counted(self):
        from journal.chat import send
        from journal import ledger
        self.chat.fail = [ChannelError("can't parse entities"), ChannelError("Bad Gateway")]
        log = []
        plain = {"html": "<b>plain</b>", "opener": "plain", "opener_source": "plain:send-retry"}
        self.assertFalse(send.send_prompt(self.cfg, self.record(), log, retry=lambda: plain))
        self.assertEqual([s[1] for s in self.chat.sends], ["<b>Evening</b>", "<b>plain</b>"])
        self.assertEqual(self.told, [])  # nothing delivered, nothing told
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-evening")
        self.assertEqual((p["send_attempts"], p["send_error"]), (1, "Bad Gateway"))
        self.assertEqual(log[0], "2026-10-14-evening: send failed (can't parse entities); retrying with the plain opener")


class AnnotationReplyTest(ConversionCase):
    def annotated(self):
        """A prompt answered an hour ago, its annotations written (the annotate step, with a model stand-in)."""
        from datetime import datetime, timedelta, timezone
        from helpers import add_prompt
        from journal import agent, capture, tick
        self.cfg.annotate_after_minutes = 0
        sent = datetime.now(timezone.utc) - timedelta(hours=1)
        add_prompt(self.cfg, sent=sent)
        capture.capture(self.cfg, self.cfg.chat_id, "100", "slept badly, clear head", sent + timedelta(minutes=5),
                        "text", "4242")
        with mock.patch.object(agent, "ask", lambda *a, **k: "- He slept badly but felt clear."), \
                mock.patch("journal.context.companion_reply_after", lambda *a, **k: None):
            tick.annotate(self.cfg, datetime.now(timezone.utc), [])
        return datetime.now(timezone.utc)

    def test_an_annotation_reply_is_one_delivery(self):
        from journal import ledger
        from journal.chat import replies
        now, spy, log = self.annotated(), self.spy(replies), []
        replies.reply_annotations(self.cfg, now, log)
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(len(self.chat.sends), 1)
        chat_id, html, kw = self.chat.sends[0]
        self.assertEqual((chat_id, kw), (self.cfg.chat_id, {"reply_to": "4242"}))
        self.assertIn("He slept badly but felt clear.", html)
        self.assertEqual(self.events, ["send", "mirror"])
        self.assertEqual(self.told[0][2], ("prompts", "2026-10-14-morning", "annotation_reply"))
        self.assertEqual(log, ["replied to 2026-10-14-morning with its annotations"])
        r = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-morning")["annotation_reply"]
        self.assertEqual((r["message_id"], r["reply_to"]), ("701", "4242"))

    def test_a_failed_reply_counts_an_attempt_and_tells_nothing(self):
        from journal import ledger
        from journal.chat import replies
        now, log = self.annotated(), []
        self.chat.fail = [ValueError("chat id")]  # (a ValueError is a reply's own failure too, as before)
        replies.reply_annotations(self.cfg, now, log)
        self.assertEqual(self.told, [])
        self.assertEqual(log, ["2026-10-14-morning: annotation reply failed (chat id)"])
        r = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-morning")["annotation_reply"]
        self.assertEqual((r["attempts"], r["error"]), (1, "chat id"))


class ReflectionTest(ConversionCase):
    def owed(self, trigger="schedule"):
        """A day reflection written last night and not sent yet."""
        from datetime import datetime, timezone
        from journal import ledger
        self.now = datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc)  # 06:30 in the helpers' zone, the morning after
        path = Path(self.tmp.name) / "reflection.md"
        path.write_text("A quiet day, mostly writing.\n", encoding="utf-8")
        with ledger.locked(self.cfg.ledger_path) as data:
            data.setdefault("reflections", []).append(
                {"id": "reflection-2026-10-13", "kind": "reflection", "local_date": "2026-10-13", "path": str(path),
                 "written_at": self.now.isoformat(), "trigger": trigger})

    def test_a_reflection_is_one_delivery(self):
        from journal import ledger, reflect
        self.owed(trigger="fallback")
        spy, log = self.spy(reflect), []
        self.assertEqual(reflect.send(self.cfg, self.now, log), [])
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(len(self.chat.sends), 1)
        self.assertEqual(self.chat.sends[0][2], {"silent": True})  # the overnight fallback goes silently, as before
        self.assertIn("A quiet day, mostly writing.", self.chat.sends[0][1])
        self.assertEqual(self.events, ["send", "mirror"])
        self.assertEqual(self.told[0][1:], ("A quiet day, mostly writing.",
                                            ("reflections", "reflection-2026-10-13", ledger.SEND_KEY)))
        self.assertEqual(log, ["sent reflection-2026-10-13 to Telegram"])
        sent = ledger.send_of(ledger.snapshot(self.cfg.ledger_path)["reflections"][0])
        self.assertEqual((sent["message_id"], sent["silent"]), ("701", True))

    def test_a_failed_reflection_counts_and_alerts_on_the_last_attempt(self):
        from journal import ledger, reflect
        self.owed()
        for n in range(reflect.SEND_ATTEMPTS):
            self.chat.fail = [ChannelError("Bad Gateway")]
            log = []
            alerts = reflect.send(self.cfg, self.now, log)
            self.assertEqual(log, ["reflection-2026-10-13: Telegram send failed (Bad Gateway)"])
        self.assertEqual(alerts, [f"reflection-2026-10-13 could not be sent to Telegram after "
                                  f"{reflect.SEND_ATTEMPTS} attempts: Bad Gateway"])
        self.assertEqual(self.chat.sends[0][2], {"silent": False})
        self.assertEqual(self.told, [])
        sent = ledger.send_of(ledger.snapshot(self.cfg.ledger_path)["reflections"][0])
        self.assertEqual(sent["attempts"], reflect.SEND_ATTEMPTS)

    def test_a_reflection_whose_file_is_gone_is_counted_without_a_send(self):
        from journal import ledger, reflect
        self.owed()
        Path(self.tmp.name, "reflection.md").unlink()
        spy, log = self.spy(reflect), []
        reflect.send(self.cfg, self.now, log)
        self.assertEqual((spy.calls, self.chat.sends, self.told), ([], [], []))
        self.assertEqual(log, ["reflection-2026-10-13: Telegram send failed (the file is gone)"])
        sent = ledger.send_of(ledger.snapshot(self.cfg.ledger_path)["reflections"][0])
        self.assertEqual((sent["attempts"], sent["error"]), (1, "the file is gone"))


class CatchUpTest(ConversionCase):
    def written(self, prompts=()):
        """A catch-up letter written after an outage, not sent yet (its send failed or the window just closed)."""
        from datetime import datetime, timezone
        from journal import ledger
        self.now = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
        with ledger.locked(self.cfg.ledger_path) as data:
            data.setdefault("recovery", []).append(
                {"id": "recovery-1", "status": "waiting", "text": "Sorry, I was **away**.", "reply_to": "4242",
                 "window": "08:00 to 08:40", "messages": [{"at": self.now.isoformat(), "row": 3}],
                 "prompts": list(prompts)})

    def test_the_letter_is_one_delivery(self):
        from journal import ledger, recovery
        from helpers import add_prompt
        add_prompt(self.cfg)
        self.written(prompts=["2026-10-14-morning"])
        spy, log = self.spy(recovery), []
        recovery._send(self.cfg, ledger.snapshot(self.cfg.ledger_path)["recovery"][0], self.now, log)
        self.assertEqual(len(spy.calls), 1)
        self.assertEqual(self.chat.sends, [(self.cfg.chat_id, "Sorry, I was <b>away</b>.", {"reply_to": "4242"})])
        self.assertEqual(self.events, ["send", "mirror"])
        self.assertEqual(self.told[0][1:], ("Sorry, I was **away**.", ("recovery", "recovery-1", None)))
        self.assertIn("was offline from 08:00 to 08:40", self.told[0][0])
        self.assertEqual(log, ["sent the catch-up for recovery-1 (1 unanswered message(s))"])
        data = ledger.snapshot(self.cfg.ledger_path)
        r = data["recovery"][0]
        self.assertEqual((r["status"], r["message_id"]), ("sent", "701"))
        reply = ledger.find(data, "2026-10-14-morning")["annotation_reply"]
        self.assertEqual((reply["skipped"], reply["letter"]), ("letter", "recovery-1"))  # in place of its reply

    def test_a_failed_letter_counts_and_turns_into_a_note_on_the_last_attempt(self):
        from journal import ledger, recovery
        self.written()
        notes = []
        with mock.patch.object(recovery, "_note", lambda cfg, rec, now, why, log: notes.append(why)):
            for n in range(1, recovery.SEND_ATTEMPTS + 1):
                self.chat.fail = [ChannelError("Bad Gateway")]
                log = []
                recovery._send(self.cfg, ledger.snapshot(self.cfg.ledger_path)["recovery"][0], self.now, log)
                self.assertEqual(log, [f"recovery-1: catch-up send attempt {n}/{recovery.SEND_ATTEMPTS} failed "
                                       "(Bad Gateway)"])
        self.assertEqual(notes, [f"Telegram refused it {recovery.SEND_ATTEMPTS} times"])
        self.assertEqual(self.told, [])
        r = ledger.snapshot(self.cfg.ledger_path)["recovery"][0]
        self.assertEqual((r["status"], r["send_attempts"], r["send_error"]),
                         ("waiting", recovery.SEND_ATTEMPTS, "Bad Gateway"))


if __name__ == "__main__":
    unittest.main()
