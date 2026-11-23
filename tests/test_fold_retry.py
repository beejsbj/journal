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

    def reply(self, pid="2026-10-14-morning", at=T0):
        """A reply of his captured to the prompt, as ``capture`` records one (it asks for a fold again)."""
        with ledger.locked(self.cfg.ledger_path) as data:
            q = ledger.find(data, pid)
            q.setdefault("captures", []).append({"at": at.isoformat(), "rule": "reply", "source": "text"})
            q["needs_edit"] = True

    def give_up_after_attempts(self, bot):
        """Fail the morning's fold until it is given up after its attempts; returns the time of the last try."""
        now = T0
        while "fold_gave_up" not in self.prompt():
            self.fold(now, bot)
            now += TICK
        self.assertEqual(self.prompt()["fold_gave_up"]["reason"], "attempts")
        return now

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


    def test_a_chat_wide_refusal_gives_up_nothing_and_holds_the_others(self):
        # the bot blocked: every fold fails alike, so none is given up in the first tick and only one is tried
        add_prompt(self.cfg, sent=SENT, message_id="5")
        add_prompt(self.cfg, kind="evening", sent=SENT + timedelta(minutes=1), message_id="6")
        add_prompt(self.cfg, kind="midweek", sent=SENT + timedelta(minutes=2), message_id="7")
        pids = ("2026-10-14-morning", "2026-10-14-evening", "2026-10-14-midweek")
        bot = Bot("Forbidden: bot was blocked by the user")
        log = self.fold(T0, bot)
        self.assertEqual(len(bot.calls), 1)
        for pid in pids:
            p = self.prompt(pid)
            self.assertNotIn("fold_gave_up", p)
            self.assertIsNone(p.get("collapsed_at"))
        self.assertTrue(any("trying again in 5 min" in l for l in log))
        self.assertFalse(any("given up" in l for l in log))
        for i in range(1, 12):  # an hour blocked: still nothing given up, one try a tick at most
            before = len(bot.calls)
            self.fold(T0 + i * TICK, bot)
            self.assertLessEqual(len(bot.calls) - before, 1)
        self.assertFalse(any("fold_gave_up" in self.prompt(pid) for pid in pids))
        bot.fail = None  # unblocked: every fold goes through
        for i in range(12, 20):
            self.fold(T0 + i * TICK, bot)
        for pid in pids:
            p = self.prompt(pid)
            self.assertTrue(p.get("collapsed_at"))
            for k in tick.FOLD_STATE:
                self.assertNotIn(k, p)

    def test_a_fold_given_up_after_its_attempts_gets_one_more_try_after_a_reply(self):
        add_prompt(self.cfg, sent=SENT, message_id="5")
        bot = Bot("URLError: <urlopen error [Errno 104] Connection reset by peer>")
        now = self.give_up_after_attempts(bot)
        p = self.prompt()
        self.assertEqual(p["fold_gave_up_captures"], 0)
        tried = len(bot.calls)
        for i in range(12):  # given up: nothing tried without a new reply
            self.fold(now + i * TICK, bot)
        self.assertEqual(len(bot.calls), tried)
        now += 12 * TICK
        self.reply(at=now)
        log = self.fold(now, bot)  # one new try; it fails, so it is given up again
        self.assertEqual(len(bot.calls), tried + 1)
        p = self.prompt()
        self.assertEqual(p["fold_gave_up"], {"at": now.isoformat(), "reason": "attempts",
                                             "attempts": tick.FOLD_ATTEMPTS + 1, "error": p["fold_error"]})
        self.assertEqual(p["fold_gave_up_captures"], 1)
        self.assertFalse(p.get("needs_edit"))
        self.assertTrue(any("given up" in l for l in log))
        for i in range(1, 12):  # one try a reply, never a loop
            self.fold(now + i * TICK, bot)
        self.assertEqual(len(bot.calls), tried + 1)
        now += 12 * TICK
        bot.fail = None  # the network is back and he replies again: the fold goes through
        self.reply(at=now)
        self.assertIn("folded 2026-10-14-morning (answered ✓)", self.fold(now, bot))
        p = self.prompt()
        for k in tick.FOLD_STATE:
            self.assertNotIn(k, p)
        self.assertFalse(p.get("needs_edit"))

    def test_a_permanent_give_up_is_not_tried_again_after_a_reply(self):
        add_prompt(self.cfg, sent=SENT, message_id="5")
        bot = Bot("Bad Request: message to edit not found")
        self.fold(T0, bot)
        self.reply(at=T0 + TICK)
        for i in range(1, 6):
            self.fold(T0 + i * TICK, bot)
        self.assertEqual(len(bot.calls), 1)
        self.assertEqual(self.prompt()["fold_gave_up"]["reason"], "permanent")
        self.assertNotIn("fold_gave_up_captures", self.prompt())

    def test_a_fold_that_goes_through_makes_the_waiting_ones_due_next_tick(self):
        add_prompt(self.cfg, sent=SENT, message_id="5")
        add_prompt(self.cfg, kind="evening", sent=SENT + timedelta(minutes=1), message_id="6")
        add_prompt(self.cfg, kind="midweek", sent=SENT + timedelta(minutes=2), message_id="7")
        far = (T0 + timedelta(minutes=80)).isoformat()
        with ledger.locked(self.cfg.ledger_path) as data:  # the morning waits out a long backoff after the network
            ledger.find(data, "2026-10-14-morning").update(fold_attempts=4, fold_error="editMessageText: URLError: x",
                                                          fold_retry_at=far)

        def bot(cfg, method, payload):  # the channel is back; the midweek's own edit fails this tick
            if payload["message_id"] == 7:
                raise telegram.TelegramError(f"{method}: Internal Server Error")
            return True
        log = self.fold(T0, bot)
        self.assertIn("folded 2026-10-14-evening (missed — reply anytime)", log)
        self.assertEqual(self.prompt()["fold_retry_at"], T0.isoformat())  # due on the next tick
        self.assertEqual(self.prompt()["fold_attempts"], 4)  # untried: nothing spent
        self.assertIsNone(self.prompt().get("collapsed_at"))
        self.assertEqual(self.prompt("2026-10-14-midweek")["fold_retry_at"], (T0 + TICK).isoformat())  # its own wait
        self.assertTrue(any("1 fold(s) waiting to retry are due on the next tick" in l for l in log))
        self.assertIn("folded 2026-10-14-morning (missed — reply anytime)", self.fold(T0 + TICK, bot))
        for k in tick.FOLD_STATE:
            self.assertNotIn(k, self.prompt())

    def test_a_message_not_modified_is_a_fold_that_went_through(self):
        # the edit finds the message already folded (an earlier try landed but its answer was lost): no failure
        add_prompt(self.cfg, sent=SENT, message_id="5")
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, "2026-10-14-morning").update(fold_attempts=2, fold_error="editMessageText: URLError: x",
                                                          fold_retry_at=T0.isoformat())
        bot = Bot("Bad Request: message is not modified: specified new message content and reply markup are exactly "
                  "the same as a current content and reply markup of the message")
        log = self.fold(T0, bot)
        self.assertEqual(len(bot.calls), 1)
        p = self.prompt()
        self.assertTrue(p.get("collapsed_at"))
        self.assertFalse(p.get("needs_edit"))
        for k in tick.FOLD_STATE:
            self.assertNotIn(k, p)
        self.assertIn("folded 2026-10-14-morning (missed — reply anytime)", log)
        self.assertFalse(any(tick.files_as_error(l) for l in log))


class PermanentTest(unittest.TestCase):
    def test_what_the_api_refuses_for_good(self):
        for text in ("editMessageText: Bad Request: message to edit not found",
                     "editMessageText: Bad Request: message can't be edited",
                     "editMessageText: Bad Request: MESSAGE_ID_INVALID",
                     "editMessageText: HTTP 400"):
            self.assertTrue(telegram.TelegramError(text).permanent, text)
        # a refusal of the whole chat holds for every message alike and passes when the chat is open again
        for text in ("editMessageText: Forbidden: bot was blocked by the user",
                     "editMessageText: Forbidden: bot was kicked from the group chat",
                     "editMessageText: Forbidden: user is deactivated",
                     "editMessageText: HTTP 403",
                     "editMessageText: Bad Request: chat not found",
                     "editMessageText: Bad Request: group chat was upgraded to a supergroup chat",
                     "editMessageText: Bad Request: not enough rights to send text messages to the chat",
                     "editMessageText: Bad Request: CHAT_WRITE_FORBIDDEN",
                     "editMessageText: Bad Request: PEER_ID_INVALID"):
            self.assertFalse(telegram.TelegramError(text).permanent, text)
        for text in ("bot token unavailable: token_command printed no TELEGRAM_BOT_TOKEN (two attempts)",
                     "editMessageText: URLError: <urlopen error [Errno 104] Connection reset by peer>",
                     "editMessageText: Too Many Requests: retry after 30",
                     "editMessageText: Unauthorized",
                     "editMessageText: Internal Server Error",
                     "editMessageText: HTTP 502",
                     "editMessageText: TimeoutError: The read operation timed out"):
            self.assertFalse(telegram.TelegramError(text).permanent, text)
        self.assertFalse(ChannelError("hermes: send failed").permanent)  # other channels: may pass, unless they say


class TokenHelperTest(CleanEnv):
    def test_a_failing_helper_says_its_exit_status_and_nothing_it_printed(self):
        cmd = ["/bin/sh", "-c", "echo out-SECRET=1; echo more-SECRET; echo err-SECRET >&2; exit 3"]
        tok, where = telegram.resolve_secret({"token_command": cmd}, "TELEGRAM_BOT_TOKEN", "telegram", {})
        self.assertIsNone(tok)
        self.assertEqual(where, "token_command exited with status 3 and printed no TELEGRAM_BOT_TOKEN (two attempts)")
        self.assertNotIn("SECRET", where)

    def test_a_helper_that_exits_cleanly_with_no_token_says_only_that(self):
        cmd = ["/bin/sh", "-c", "echo A=1; echo B=2"]
        self.assertEqual(telegram.resolve_secret({"token_command": cmd}, "TELEGRAM_BOT_TOKEN", "telegram", {}),
                         (None, "token_command printed no TELEGRAM_BOT_TOKEN (two attempts)"))


if __name__ == "__main__":
    unittest.main()
