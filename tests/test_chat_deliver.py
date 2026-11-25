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


if __name__ == "__main__":
    unittest.main()


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
