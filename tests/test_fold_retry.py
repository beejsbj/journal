"""A fold that fails is retried with a doubling wait across ticks, and given up for good only when it can never succeed
(a refusal from the platform) or has failed ``tick.FOLD_ATTEMPTS`` times (``tick.fold``, ``fold_gave_up``)."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CleanEnv, add_prompt, make_cfg

from journal import ledger, telegram, tick
from journal.ports import ChannelError

SENT = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)
T0 = SENT + timedelta(hours=7)  # past the morning's window: the fold is due
TICK = timedelta(minutes=5)


class Bot:
    """``telegram._call`` as a fake: ``fail`` (a string, or None to succeed) decides each edit; ``calls`` keeps them."""

    def __init__(self, fail=None):
        self.fail = fail
        self.calls = []

    def __call__(self, cfg, method, payload):
        self.calls.append((method, payload))
        if self.fail:
            raise telegram.TelegramError(f"{method}: {self.fail}")
        return {"message_id": payload.get("message_id", 1)}


class FoldRetryTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = make_cfg(Path(self._tmp.name))

    def prompt(self, pid="2026-10-14-morning"):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)

    def fold(self, now, bot):
        log = []
        with mock.patch.object(telegram, "_call", bot):
            tick.fold(self.cfg, now, log)
        return log

    def test_a_token_that_could_not_be_had_heals_on_a_later_tick(self):
        # the failure seen live: the token helper printed nothing, so the edit never reached the API
        add_prompt(self.cfg, sent=SENT, message_id="5")
        tokens = iter([(None, "token_command printed no TELEGRAM_BOT_TOKEN (two attempts)"), ("123:abc", "env")])
        calls = []

        def call(cfg, method, payload):  # the real call's first step, the token (``telegram.token``), then the API
            telegram.token(cfg)
            calls.append(payload)
            return True

        with mock.patch.object(telegram, "resolve_token", lambda cfg: next(tokens)), \
                mock.patch.object(telegram, "_call", call):
            log = []
            tick.fold(self.cfg, T0, log)
            p = self.prompt()
            self.assertIsNone(p.get("collapsed_at"))
            self.assertEqual(p["fold_attempts"], 1)
            self.assertIn("bot token unavailable", p["fold_error"])
            self.assertEqual(p["fold_retry_at"], (T0 + TICK).isoformat())
            self.assertNotIn("fold_gave_up", p)
            self.assertTrue(any("fold failed" in l and "trying again in 5 min" in l for l in log))
            self.assertTrue(all(tick.files_as_error(l) for l in log))  # (each failure goes to errors.jsonl)
            tick.fold(self.cfg, T0 + timedelta(minutes=2), [])  # not due yet: nothing is tried
            self.assertEqual(self.prompt()["fold_attempts"], 1)
            tick.fold(self.cfg, T0 + TICK - timedelta(seconds=20), log)  # the next tick, a few seconds early
        p = self.prompt()
        self.assertTrue(p.get("collapsed_at"))
        self.assertFalse(p.get("needs_edit"))
        for k in tick.FOLD_STATE:  # a fold that went through leaves no stale failure behind
            self.assertNotIn(k, p)
        self.assertEqual([c["message_id"] for c in calls], [5])
        self.assertIn("folded 2026-10-14-morning (missed — reply anytime)", log)

    def test_a_refusal_for_good_gives_up_at_once(self):
        add_prompt(self.cfg, sent=SENT, message_id="5")
        bot = Bot("Bad Request: message to edit not found")
        log = self.fold(T0, bot)
        p = self.prompt()
        self.assertEqual(p["fold_gave_up"], {"at": T0.isoformat(), "reason": "permanent", "attempts": 1,
                                             "error": "editMessageText: Bad Request: message to edit not found"})
        self.assertTrue(p.get("collapsed_at"))
        self.assertFalse(p.get("needs_edit"))
        self.assertNotIn("fold_retry_at", p)
        self.assertTrue(any("given up (refused for good)" in l and "fold failed" in l for l in log))
        p_before = self.prompt()
        with ledger.locked(self.cfg.ledger_path) as data:  # even a reply that asks for a fold again tries nothing
            ledger.find(data, p["id"])["needs_edit"] = True
        for i in range(1, 20):
            self.fold(T0 + i * TICK, bot)
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(self.prompt()["fold_gave_up"], p_before["fold_gave_up"])

    def test_transient_failures_give_up_after_the_last_attempt_within_a_day(self):
        add_prompt(self.cfg, sent=SENT, message_id="5")
        bot = Bot("URLError: <urlopen error [Errno 104] Connection reset by peer>")
        tried_at = []
        now = T0
        while now < T0 + timedelta(days=3):  # a tick every five minutes, for three days
            before = len(bot.calls)
            self.fold(now, bot)
            if len(bot.calls) > before:
                tried_at.append(now)
            now += TICK
        p = self.prompt()
        self.assertEqual(len(tried_at), tick.FOLD_ATTEMPTS)
        self.assertEqual(p["fold_gave_up"]["reason"], "attempts")
        self.assertEqual(p["fold_gave_up"]["attempts"], tick.FOLD_ATTEMPTS)
        self.assertEqual(p["fold_gave_up"]["at"], tried_at[-1].isoformat())
        self.assertTrue(p.get("collapsed_at"))
        # the stretch: first try to last within a day, each wait doubling the one before
        self.assertLess(tried_at[-1] - tried_at[0], timedelta(days=1))
        waits = [(b - a) for a, b in zip(tried_at, tried_at[1:])]
        self.assertEqual(waits, [timedelta(minutes=m) for m in tick.FOLD_RETRY_MINUTES])

    def test_one_try_a_due_tick_and_no_storm_across_prompts(self):
        add_prompt(self.cfg, sent=SENT, message_id="5")
        add_prompt(self.cfg, kind="evening", sent=SENT + timedelta(minutes=1), message_id="6")
        add_prompt(self.cfg, kind="midweek", sent=SENT + timedelta(minutes=2), message_id="7")
        bot = Bot("Too Many Requests: retry after 30")
        per_tick = []
        for i in range(12):  # an hour of ticks with the API refusing for now
            before = len(bot.calls)
            self.fold(T0 + i * TICK, bot)
            per_tick.append(len(bot.calls) - before)
        self.assertTrue(all(n <= 1 for n in per_tick), per_tick)  # after one failure that may pass, the rest wait
        tried = {}
        for _, payload in bot.calls:
            tried[payload["message_id"]] = tried.get(payload["message_id"], 0) + 1
        self.assertEqual(set(tried), {5, 6, 7})  # each still gets its turn as the others wait theirs
        for pid in ("2026-10-14-morning", "2026-10-14-evening", "2026-10-14-midweek"):
            p = self.prompt(pid)
            self.assertNotIn("fold_gave_up", p)
            self.assertEqual(p["fold_attempts"], tried[int(p["message_id"])])  # an untried tick spends no attempt
        bot.fail = None  # the limit lifts: every one folds as it comes due again
        for i in range(12, 40):
            self.fold(T0 + i * TICK, bot)
        for pid in ("2026-10-14-morning", "2026-10-14-evening", "2026-10-14-midweek"):
            p = self.prompt(pid)
            self.assertTrue(p.get("collapsed_at"))
            self.assertNotIn("fold_attempts", p)

    def test_a_refused_fold_does_not_hold_the_others(self):
        add_prompt(self.cfg, sent=SENT, message_id="5")
        add_prompt(self.cfg, kind="evening", sent=SENT + timedelta(minutes=1), message_id="6")

        def bot(cfg, method, payload):
            if payload["message_id"] == 5:
                raise telegram.TelegramError(f"{method}: Bad Request: message can't be edited")
            return True
        log = self.fold(T0, bot)
        self.assertEqual(self.prompt()["fold_gave_up"]["reason"], "permanent")
        self.assertTrue(self.prompt("2026-10-14-evening").get("collapsed_at"))
        self.assertIn("folded 2026-10-14-evening (missed — reply anytime)", log)

    def test_a_new_send_of_the_prompt_folds_afresh(self):
        add_prompt(self.cfg, sent=SENT, message_id="5")
        self.fold(T0, Bot("Bad Request: message to edit not found"))
        self.assertIn("fold_gave_up", self.prompt())
        record = {"id": "2026-10-14-morning", "kind": "morning", "local_date": "2026-10-14", "scheduled": True,
                  "chat_id": self.cfg.chat_id, "html": "<b>Morning</b>", "plain_text": "Morning"}
        with mock.patch.object(telegram, "send_html", lambda *a, **k: "9"), \
                mock.patch.object(tick.mirror, "to_chat", lambda *a, **k: None):
            self.assertTrue(tick.send_prompt(self.cfg, record, []))
        p = self.prompt()
        self.assertEqual(p["message_id"], "9")
        for k in tick.FOLD_STATE:
            self.assertNotIn(k, p)


class PermanentTest(unittest.TestCase):
    def test_what_the_api_refuses_for_good(self):
        for text in ("editMessageText: Bad Request: message to edit not found",
                     "editMessageText: Bad Request: message can't be edited",
                     "editMessageText: Bad Request: MESSAGE_ID_INVALID",
                     "editMessageText: Forbidden: bot was blocked by the user",
                     "editMessageText: HTTP 400"):
            self.assertTrue(telegram.TelegramError(text).permanent, text)
        for text in ("bot token unavailable: token_command printed no TELEGRAM_BOT_TOKEN (two attempts)",
                     "editMessageText: URLError: <urlopen error [Errno 104] Connection reset by peer>",
                     "editMessageText: Too Many Requests: retry after 30",
                     "editMessageText: Unauthorized",
                     "editMessageText: Internal Server Error",
                     "editMessageText: HTTP 502",
                     "editMessageText: TimeoutError: The read operation timed out"):
            self.assertFalse(telegram.TelegramError(text).permanent, text)
        self.assertFalse(ChannelError("hermes: send failed").permanent)  # other channels: may pass, unless they say


if __name__ == "__main__":
    unittest.main()
