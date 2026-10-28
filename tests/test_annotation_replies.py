"""Annotations are also sent to Telegram as a reply to the message they annotate: once, with retries,
never blocking the file."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import add_prompt, make_cfg

from journal import agent, capture, entries, ledger, telegram, tick

DRAFT = "- He slept badly but felt clear.\n- FOLLOW-UP: ask about the lake walk"


class FakeBot:
    """Stands in for the Bot API at the HTTP boundary and records every call."""

    def __init__(self, fail_times: int = 0):
        self.calls = []
        self.fail_times = fail_times
        self.next_id = 900

    def __call__(self, cfg, method, payload):
        self.calls.append((method, payload))
        if self.fail_times > 0:
            self.fail_times -= 1
            raise telegram.TelegramError(f"{method}: Bad Gateway")
        self.next_id += 1
        return {"message_id": self.next_id}


class AnnotationReplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.annotate_after_minutes = 0
        self.sent = datetime.now(timezone.utc) - timedelta(hours=1)
        add_prompt(self.cfg, sent=self.sent)
        self.info = capture.capture(self.cfg, self.cfg.chat_id, "100", "slept badly, clear head",
                                    self.sent + timedelta(minutes=5), "text", "4242")
        self.patches = [mock.patch.object(agent, "ask", lambda *a, **k: DRAFT),
                        mock.patch("journal.context.companion_reply_after", lambda *a, **k: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def tick_steps(self, bot):
        with mock.patch.object(telegram, "_call", bot), mock.patch.object(telegram, "token", lambda cfg: "T"):
            log = []
            now = datetime.now(timezone.utc)
            tick.annotate(self.cfg, now, log)
            tick.reply_annotations(self.cfg, now, log)
            return log

    def prompt(self):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-morning")

    def test_reply_sent_once_to_the_captured_message(self):
        bot = FakeBot()
        log = self.tick_steps(bot)
        self.assertIn("annotated 2026-10-14-morning (+1 notebook)", log)
        self.assertIn("replied to 2026-10-14-morning with its annotations", log)
        method, payload = bot.calls[-1]
        self.assertEqual(method, "sendMessage")
        self.assertEqual(payload["chat_id"], self.cfg.chat_id)
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertEqual(payload["reply_parameters"], {"message_id": 4242, "allow_sending_without_reply": True})
        self.assertTrue(payload["text"].startswith("<b>🌻 Iris's notes on your morning entry</b>"))
        self.assertIn("• He slept badly but felt clear.", payload["text"])
        self.assertNotIn("FOLLOW-UP", payload["text"])
        p = self.prompt()
        self.assertEqual(p["annotation_reply"]["message_id"], "901")
        self.assertEqual(p["annotation_reply"]["reply_to"], "4242")
        self.assertIn("He slept badly", Path(self.info["path"]).read_text())
        self.assertEqual(self.tick_steps(bot), [])  # later ticks never resend
        self.assertEqual(len(bot.calls), 1)

    def test_notebook_failure_does_not_hold_back_completion(self):
        from journal import notebook
        with mock.patch.object(notebook, "add", side_effect=OSError("read-only")):
            log = self.tick_steps(FakeBot())
        self.assertTrue(any("follow-up not saved to the notebook" in l for l in log))
        self.assertIn("replied to 2026-10-14-morning with its annotations", log)
        self.assertTrue(self.prompt()["annotated_at"])

    def test_failures_retry_then_stop_and_never_block_the_file(self):
        bot = FakeBot(fail_times=100)
        log = self.tick_steps(bot)
        self.assertIn("annotated 2026-10-14-morning (+1 notebook)", log)
        self.assertIn("He slept badly", entries.annotations(Path(self.info["path"]).read_text()))
        for _ in range(tick.REPLY_ATTEMPTS + 3):
            self.tick_steps(bot)
        self.assertEqual(len(bot.calls), tick.REPLY_ATTEMPTS)
        r = self.prompt()["annotation_reply"]
        self.assertEqual(r["attempts"], tick.REPLY_ATTEMPTS)
        self.assertIn("Bad Gateway", r["error"])
        self.assertNotIn("message_id", r)

    def test_recovers_after_a_transient_failure(self):
        bot = FakeBot(fail_times=1)
        self.tick_steps(bot)
        self.tick_steps(bot)
        r = self.prompt()["annotation_reply"]
        self.assertEqual(r["message_id"], "901")
        self.assertNotIn("error", r)

    def test_can_be_turned_off(self):
        self.cfg.telegram["annotation_replies"] = False
        bot = FakeBot()
        self.tick_steps(bot)
        self.assertEqual(bot.calls, [])
        self.assertIsNotNone(self.prompt().get("annotated_at"))

    def test_entries_annotated_before_the_upgrade_are_not_sent(self):
        entries.write_annotations(Path(self.info["path"]), "- old annotation")
        bot = FakeBot()
        self.tick_steps(bot)  # nothing pending, and no annotated_at was ever recorded
        self.assertEqual(bot.calls, [])

    def test_note_entries_get_the_note_header(self):
        info = capture.capture_note(self.cfg, "a thought about the lake", self.sent, "text", "77")
        bot = FakeBot()
        self.tick_steps(bot)
        texts = [p["text"] for _m, p in bot.calls]
        self.assertTrue(any(t.startswith("<b>🌻 Iris's notes on your note</b>") for t in texts))
        replies = [p["reply_parameters"]["message_id"] for _m, p in bot.calls]
        self.assertIn(77, replies)
        del info

    def test_no_platform_message_id_sends_without_reply(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            for c in ledger.find(data, "2026-10-14-morning")["captures"]:
                c["platform_message_id"] = None
        bot = FakeBot()
        self.tick_steps(bot)
        self.assertNotIn("reply_parameters", bot.calls[-1][1])

    def test_only_followups_is_a_failed_attempt_not_an_empty_annotation(self):
        with mock.patch.object(agent, "ask", lambda *a, **k: "FOLLOW-UP: ask later"):
            log = self.tick_steps(FakeBot())
        self.assertIn("2026-10-14-morning: annotation attempt failed", log)
        self.assertTrue(entries.annotations_pending(Path(self.info["path"]).read_text()))
        from journal import notebook
        self.assertEqual(notebook.open_items(self.cfg), [])

    # a web capture (with the client id a retrying browser sends) must not stop the reply step: its id is not a
    # Telegram message, so the annotations go to the chat unthreaded and later prompts still get theirs
    def test_a_web_capture_with_a_client_id_does_not_break_the_reply_step(self):
        self.sent2 = self.sent + timedelta(minutes=30)
        add_prompt(self.cfg, kind="evening", local_date="2026-10-14", sent=self.sent2, message_id="200")
        capture.capture_web(self.cfg, "evening", "words from the browser", self.sent2 + timedelta(minutes=5),
                            client_id="abc123")
        bot = FakeBot()
        log = self.tick_steps(bot)
        self.assertEqual(len([c for c in bot.calls if c[0] == "sendMessage"]), 2, log)
        by_kind = {p["kind"]: p for p in ledger.snapshot(self.cfg.ledger_path)["prompts"]}
        self.assertTrue(by_kind["morning"]["annotation_reply"]["message_id"])
        self.assertTrue(by_kind["evening"]["annotation_reply"]["message_id"])
        self.assertIsNone(by_kind["evening"]["annotation_reply"]["reply_to"])
        web_call = next(c for c in bot.calls if "evening entry" in c[1].get("text", ""))
        self.assertNotIn("reply_parameters", web_call[1])

    def test_a_reply_target_that_is_not_a_telegram_id_is_a_failed_attempt_not_a_crash(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"][0]["captures"][0]["platform_message_id"] = "web:legacy"
        bot = FakeBot()
        self.tick_steps(bot)  # must not raise
        self.assertTrue(self.prompt()["annotation_reply"]["message_id"])


class TelegramPayloadTest(unittest.TestCase):
    def test_parse_error_falls_back_to_plain_text_and_keeps_reply(self):
        calls = []

        def api(cfg, method, payload):
            calls.append(dict(payload))
            if "parse_mode" in payload:
                raise telegram.TelegramError("sendMessage: Bad Request: can't parse entities")
            return {"message_id": 5}
        with tempfile.TemporaryDirectory() as t, mock.patch.object(telegram, "_call", api):
            mid = telegram.send_html(make_cfg(Path(t)), "1", "<b>hi &amp; bye</b>", reply_to="12")
        self.assertEqual(mid, "5")
        self.assertEqual(calls[1]["text"], "hi & bye")
        self.assertEqual(calls[1]["reply_parameters"]["message_id"], 12)


if __name__ == "__main__":
    unittest.main()
