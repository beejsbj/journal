"""CATCH-UP-LETTER (#42, the owner's decision 1 of 2026-11-15): after a model outage the companion sends ONE message
once she is back ("I was away, I read what you wrote"), in place of a trickle of late annotation replies. Her notes
are still written into the entries; only the chat side changes. The letter is the outage catch-up (``recovery``),
widened to the entries he wrote while she was away: one message per outage, idempotent across ticks and restarts,
held while a bare ``/x`` window is open (or the privacy pass cannot say), nothing with ``channel.platform: none``,
and with no usable letter the replies go as they did before (the floor)."""
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, CleanEnv, add_prompt, make_cfg
from test_mirror import Recorder, under_hermes
from test_recovery import NOTICE, PROVIDER_DOWN
from test_reflect import FakeBot
from test_sweep import FakeSessions

from journal import agent, capture, entries, ledger, outage, privacy, recovery, telegram, tick

T0 = datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc)  # 09:30 IST: the outage begins
BACK = T0 + timedelta(hours=6)                             # 15:30 IST: a model answers again
LETTER = ("I was away most of today and I'm back now. I read what you wrote: the sea again in the morning, and the "
          "long walk later that felt lighter. How are you now?")
NOTES = "- He dreamt of the sea and slept badly."


class Base(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = make_cfg(t)
        self.cfg.annotate_after_minutes = 20
        self.db = FakeSessions(t / "hermes-home" / "state.db")
        self.mirror = Recorder()
        under_hermes(self, self.cfg, self.mirror, self.db)
        self.bot = FakeBot()
        self.asked = []
        self.answers = [LETTER]
        for p in (mock.patch.object(telegram, "_call", self.bot), mock.patch.object(telegram, "token", lambda c: "T"),
                  mock.patch.object(agent, "ask", self.ask), mock.patch.object(agent, "_answered", False)):
            p.start()
            self.addCleanup(p.stop)

    def ask(self, cfg, query, budget, toolsets=None):
        self.asked.append((query, toolsets))
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    # ── the story: an outage of six hours, two entries written in it ──────────

    def entry(self, kind, sent, words, mid, pmid):
        add_prompt(self.cfg, kind=kind, local_date="2026-10-15", sent=sent, message_id=mid)
        capture.capture(self.cfg, CHAT, mid, words, sent + timedelta(minutes=5), "text", pmid)
        self.db.add(words, sent + timedelta(minutes=5), mid=pmid)  # (his reply is a chat message too …)
        self.db.add(NOTICE, sent + timedelta(minutes=5, seconds=30), role="assistant", display_kind="failed_turn",
                    meta=PROVIDER_DOWN)  # (… and Hermes could not answer it)
        return f"2026-10-15-{kind}"

    def outage_with_entries(self, back=BACK):
        self.db.add("morning!", T0 - timedelta(hours=1), mid="1")
        self.db.add("Morning. Sleep well?", T0 - timedelta(hours=1) + timedelta(seconds=20), role="assistant")
        outage.trip(self.cfg, "exit 1: 503", T0)
        self.morning = self.entry("morning", T0 + timedelta(hours=1), "slept badly, the sea again", "100", "11")
        self.evening = self.entry("evening", T0 + timedelta(hours=4), "a long walk, felt lighter", "101", "12")
        outage.answered(self.cfg, back)

    def annotated(self, pid, at):
        """The annotation step wrote her notes into the entry (as ``tick.annotate`` does once a model answers)."""
        with ledger.locked(self.cfg.ledger_path) as data:
            p = ledger.find(data, pid)
            entries.write_annotations(Path(p["entry_path"]), NOTES, self.cfg.names)
            p["annotated_at"] = at.isoformat()

    def recover(self, now):
        log = []
        agent._answered = False  # (a tick is a fresh process)
        recovery.run(self.cfg, now, log)
        return log

    def reply(self, now):
        log = []
        tick.reply_annotations(self.cfg, now, log)
        return log

    def prompt(self, pid):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)

    def recs(self):
        return ledger.snapshot(self.cfg.ledger_path).get("recovery") or []

    def sends(self):
        return [p for m, p in self.bot.calls if m == "sendMessage"]


class LetterTest(Base):
    def test_one_letter_in_place_of_the_late_replies(self):
        self.outage_with_entries()
        self.annotated(self.morning, BACK)  # the tick's annotate step, just before: her notes, in the entry
        self.assertEqual(self.reply(BACK), ["1 annotation reply waits for the catch-up message after the outage"])
        self.assertEqual(self.sends(), [])  # held for the letter, not sent on its own

        log = self.recover(BACK)
        self.assertEqual(log, ["sent the catch-up for outage-3 (2 unanswered message(s), 2 journal entries)"])
        (send,) = self.sends()
        self.assertEqual(send["text"], LETTER)
        self.assertEqual(send["reply_parameters"]["message_id"], 12)  # his last unanswered message
        (query, toolsets), = self.asked
        self.assertEqual(toolsets, "none")
        self.assertIn("You were offline from 15 Oct 09:30 to 15 Oct 15:30 IST", query)
        self.assertIn("slept badly, the sea again", query)
        self.assertIn("a long walk, felt lighter", query)
        self.assertIn("in his journal", query)
        self.assertNotIn(NOTES.lstrip("- "), query)  # her notes are not material: what HE wrote is
        for pid in (self.morning, self.evening):  # covered by the letter: their replies never go on their own
            self.assertEqual(self.prompt(pid)["annotation_reply"]["skipped"], "letter")
        (call,) = self.mirror.calls
        self.assertTrue(call["text"].startswith("[Journal: Iris was offline from 15 Oct 09:30 to 15 Oct 15:30 IST; "
                                                "this message was sent after reconnecting, about his messages and "
                                                "the 2 journal entries he wrote in that time"))
        self.assertTrue(call["text"].endswith("\n" + LETTER))

        # the evening entry is annotated later (the paced catch-up): the note is written, nothing is sent
        self.annotated(self.evening, BACK + timedelta(minutes=5))
        self.assertEqual(self.reply(BACK + timedelta(minutes=5)), [])
        self.assertIn(NOTES, entries.annotations(Path(self.prompt(self.evening)["entry_path"]).read_text()))
        self.assertEqual(len(self.sends()), 1)

    def test_at_most_one_per_outage_across_ticks_and_restarts(self):
        self.outage_with_entries()
        self.recover(BACK)
        for minutes in (5, 10, 60, 600):  # later ticks, each a fresh process
            self.assertEqual(self.recover(BACK + timedelta(minutes=minutes)), [])
            self.reply(BACK + timedelta(minutes=minutes))
        self.assertEqual(len(self.sends()), 1)
        self.assertEqual(len(self.asked), 1)
        (rec,) = self.recs()
        self.assertEqual((rec["status"], rec["outage_down_since"]), ("sent", T0.isoformat()))

    def test_entries_alone_get_a_letter_of_their_own(self):
        # his words reached the journal on a face (no chat message, so no failed turn): the letter still goes,
        # as a message of its own
        outage.trip(self.cfg, "exit 1: 503", T0)
        capture.capture_web(self.cfg, None, "wrote this in the app, the sea again", T0 + timedelta(hours=2))
        outage.answered(self.cfg, BACK)
        self.answers = ["Back now. I read your note from the app; the sea again. What is it about the sea lately?"]
        log = self.recover(BACK)
        self.assertEqual(log, ["sent the catch-up for outage-202610150400 (1 journal entry)"])
        (send,) = self.sends()
        self.assertNotIn("reply_parameters", send)
        self.assertIn("wrote this in the app, the sea again", self.asked[0][0])
        self.assertNotIn("got only an automatic error line back", self.asked[0][0])
        self.assertIn("about the 1 journal entry he wrote in that time", self.mirror.calls[0]["text"])

    def test_the_letter_and_the_chat_catch_up_are_one_message(self):
        self.outage_with_entries()
        self.db.add("are you there?", T0 + timedelta(hours=5), mid="13")
        self.db.add(NOTICE, T0 + timedelta(hours=5, seconds=30), role="assistant", display_kind="failed_turn",
                    meta=PROVIDER_DOWN)
        log = self.recover(BACK)
        self.assertEqual(log, ["sent the catch-up for outage-3 (3 unanswered message(s), 2 journal entries)"])
        self.assertEqual(len(self.sends()), 1)
        self.assertIn("are you there?", self.asked[0][0])

    def test_answered_live_since_keeps_the_entries(self):
        # he talked with her live once she was back: his chat messages were answered, his entries were not
        self.outage_with_entries()
        self.db.add("ok, you work again?", BACK + timedelta(minutes=1), mid="14")
        self.db.add("I'm here.", BACK + timedelta(minutes=1, seconds=10), role="assistant")
        self.answers = ["Back now, and I read your entries from today: the sea again, then a walk that helped."]
        log = self.recover(BACK + timedelta(minutes=5))
        self.assertEqual(log, ["sent the catch-up for outage-202610150400 (2 journal entries)"])
        self.assertNotIn("got only an automatic error line back", self.asked[0][0])

    def test_quotes_must_be_his_words(self):
        self.outage_with_entries()
        self.annotated(self.morning, BACK)
        self.answers = ['Back. You wrote "He dreamt of the sea and slept badly".']  # her note, not his words
        log = self.recover(BACK)
        self.assertEqual(log, ["outage-3: catch-up attempt 1/3 failed (quote not in his messages: 'He dreamt of the "
                               "sea and slept badly')"])
        self.answers = ['Back. You wrote "slept badly, the sea again" — and then a walk. How is today?']
        self.assertEqual(self.recover(BACK + timedelta(minutes=1)),
                         ["sent the catch-up for outage-3 (2 unanswered message(s), 2 journal entries)"])


class FloorTest(Base):
    def test_no_usable_letter_sends_the_replies_as_before(self):
        self.outage_with_entries()
        self.annotated(self.morning, BACK)
        self.answers = ['You said "I never slept at all".']  # never his words: refused each time
        for i in range(recovery.ATTEMPTS):
            self.recover(BACK + timedelta(minutes=i))
        self.assertEqual(self.sends(), [])  # no letter, and no fixed line in her voice either
        self.assertEqual(self.recs()[0]["status"], "noted")
        self.assertIn("no usable reply after 3 attempts", self.mirror.calls[-1]["text"])
        log = self.reply(BACK + timedelta(minutes=5))  # released: the old path, paced and never stale
        self.assertEqual(log[0], f"replied to {self.morning} with its annotations")
        self.assertNotIn("skipped", self.prompt(self.morning)["annotation_reply"])

    def test_no_model_no_letter_and_the_replies_stay_held(self):
        self.outage_with_entries()
        self.annotated(self.morning, BACK)
        self.answers = [outage.ModelHeld("held: exit 1")]  # down again before the letter could be written
        self.assertIn("catch-up deferred, model still unreachable", self.recover(BACK)[0])
        self.assertEqual(self.reply(BACK + timedelta(minutes=5))[0],
                         "1 annotation reply waits for the catch-up message after the outage")
        self.assertEqual(self.sends(), [])

    def test_a_short_outage_writes_no_letter(self):
        outage.trip(self.cfg, "exit 1: 503", T0)
        pid = self.entry("morning", T0, "slept badly, the sea again", "100", "11")
        outage.answered(self.cfg, T0 + timedelta(minutes=40))  # under recovery.letter_min_minutes (60)
        self.annotated(pid, T0 + timedelta(minutes=45))
        self.assertEqual(self.reply(T0 + timedelta(minutes=45)), [f"replied to {pid} with its annotations"])
        self.recover(T0 + timedelta(minutes=45))  # (the chat catch-up for his message goes as before)
        self.assertFalse(any(r.get("entries") for r in self.recs()))

    def test_switched_off_the_replies_go_as_before(self):
        self.outage_with_entries()
        self.cfg.recovery["letter"] = False
        self.annotated(self.morning, BACK)
        self.assertEqual(self.reply(BACK), [f"replied to {self.morning} with its annotations"])
        self.recover(BACK)
        self.assertFalse(any(r.get("entries") for r in self.recs()))

    def test_without_the_breaker_the_replies_go_as_before(self):
        self.outage_with_entries()
        self.cfg.recovery["breaker"] = False  # (no outage known across ticks: nothing says which words it spanned)
        self.annotated(self.morning, BACK)
        self.assertEqual(self.reply(BACK), [f"replied to {self.morning} with its annotations"])
        self.recover(BACK)
        self.assertFalse(any(r.get("entries") for r in self.recs()))


class PrivacyTest(Base):
    def test_an_outage_spanning_an_x_window_never_shows_its_words(self):
        outage.trip(self.cfg, "exit 1: 503", T0)
        self.morning = self.entry("morning", T0 + timedelta(hours=1), "slept badly, the sea again", "100", "11")
        at = T0 + timedelta(hours=2)  # a bare /x, then private words, while she was away
        self.db.add("/x", at, mid="20")
        self.db.add("something private about work", at + timedelta(minutes=1), mid="21")
        self.db.add(NOTICE, at + timedelta(minutes=1, seconds=30), role="assistant", display_kind="failed_turn",
                    meta=PROVIDER_DOWN)
        self.evening = self.entry("evening", T0 + timedelta(hours=4), "a long walk, felt lighter", "101", "12")
        outage.answered(self.cfg, BACK)
        self.recover(BACK)
        (query, _), = self.asked
        self.assertNotIn("something private", query)
        self.assertIn("slept badly, the sea again", query)
        self.assertIn("a long walk, felt lighter", query)
        self.assertEqual(len(self.sends()), 1)

    def test_held_while_a_window_is_open_now(self):
        self.outage_with_entries()
        self.db.add("/x", BACK - timedelta(minutes=5), mid="20")
        self.db.add("private, still talking", BACK - timedelta(minutes=4), mid="21")
        self.db.add("I hear you.", BACK - timedelta(minutes=3), role="assistant")
        self.assertEqual(self.recover(BACK), [])  # a bare /x is holding the chat: the letter waits
        self.assertEqual((self.asked, self.sends()), ([], []))
        self.recover(BACK + timedelta(hours=1))  # quiet since: the window has closed
        self.assertEqual(len(self.sends()), 1)
        self.assertNotIn("private, still talking", self.asked[0][0])

    def test_a_privacy_pass_that_cannot_run_sends_nothing(self):
        self.outage_with_entries()
        with mock.patch.object(privacy, "view", lambda *a, **k: privacy.Closed("no database")):
            self.recover(BACK)
            self.reply(BACK)
        self.assertEqual((self.asked, self.sends()), ([], []))


class NoChatTest(Base):
    def test_platform_none_sends_nothing_and_asks_nothing(self):
        self.outage_with_entries()
        self.cfg.platform = "none"
        self.annotated(self.morning, BACK)
        self.assertEqual(self.recover(BACK), [])
        self.reply(BACK)
        self.assertEqual((self.asked, self.sends(), self.recs()), ([], [], []))
        self.assertEqual(self.prompt(self.morning)["annotation_reply"]["skipped"], "no chat")  # as before


if __name__ == "__main__":
    import unittest
    unittest.main()
