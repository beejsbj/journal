"""CATCH-UP-LETTER (#42, the owner's decision 1 of 2026-11-15): after a model outage the companion sends ONE message
once she is back ("I was away, I read what you wrote"), in place of a trickle of late annotation replies. Her notes
are still written into the entries; only the chat side changes. The letter is the outage catch-up (``recovery``),
widened to the entries he wrote while she was away: one message per outage, idempotent across ticks and restarts,
held while a bare ``/x`` window is open (or the privacy pass cannot say), nothing with ``channel.platform: none``,
and with no usable letter the replies go as they did before (the floor)."""
import importlib.util
import subprocess
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


    def test_a_quote_of_his_words_in_an_entry_passes(self):
        # words written on a face only (no chat message): the quote check must know them from the entry
        outage.trip(self.cfg, "exit 1: 503", T0)
        capture.capture_web(self.cfg, None, "the harbour lights at dusk, again", T0 + timedelta(hours=2))
        outage.answered(self.cfg, BACK)
        self.answers = ['Back now. You wrote "the harbour lights at dusk" — what did they bring up?']
        self.assertEqual(self.recover(BACK), ["sent the catch-up for outage-202610150400 (1 journal entry)"])

    def test_two_outages_close_together_make_one_letter(self):
        outage.trip(self.cfg, "exit 1: 503", T0)
        self.morning = self.entry("morning", T0 + timedelta(hours=1), "slept badly, the sea again", "100", "11")
        back1 = T0 + timedelta(hours=2)
        outage.answered(self.cfg, back1)
        self.answers = [outage.ModelHeld("held: exit 1")]  # down again before the first letter was written
        self.recover(back1)
        (first,) = self.recs()
        self.assertEqual((first["status"], first["prompts"]), ("waiting", [self.morning]))
        outage.trip(self.cfg, "exit 1: 503", back1 + timedelta(minutes=10))
        self.evening = self.entry("evening", back1 + timedelta(hours=1), "a long walk, felt lighter", "101", "12")
        back2 = back1 + timedelta(hours=3)  # (past the first's deadline: a later outage pauses it)
        outage.answered(self.cfg, back2)
        self.answers = [LETTER]
        self.recover(back2)
        self.recover(back2 + timedelta(minutes=5))
        self.assertEqual(len(self.sends()), 1)
        (rec,) = self.recs()
        self.assertEqual((rec["status"], rec["id"]), ("sent", first["id"]))
        self.assertEqual(sorted(rec["prompts"]), sorted([self.morning, self.evening]))
        self.assertIn("You were offline from 15 Oct 09:30 to 15 Oct 14:30 IST", self.asked[-1][0])
        for pid in (self.morning, self.evening):
            self.assertEqual(self.prompt(pid)["annotation_reply"]["skipped"], "letter")


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

    def test_six_refused_sends_release_the_replies(self):
        self.outage_with_entries()
        self.annotated(self.morning, BACK)
        self.bot.fail = recovery.SEND_ATTEMPTS
        for i in range(recovery.SEND_ATTEMPTS):
            self.recover(BACK + timedelta(minutes=5 * i))
        self.assertEqual(len(self.asked), 1)  # written once; only the send failed
        self.assertEqual(self.recs()[0]["status"], "noted")
        self.assertIn("refused it 6 times); the replies to the entries go to the chat as usual",
                      self.mirror.calls[-1]["text"])
        self.assertEqual(self.reply(BACK + timedelta(minutes=30)), [f"replied to {self.morning} with its annotations"])

    def test_a_request_that_keeps_failing_gives_way_at_the_deadline(self):
        # the letter's own request times out while other steps' models answer: deferred every 30 minutes, never an
        # attempt counted; past recovery.letter_deadline_minutes (120) there is no letter and the replies go
        self.outage_with_entries()
        self.annotated(self.morning, BACK)
        self.answers = [agent.AgentUnavailable("timed out after 150 s")]
        for minutes in (0, 30, 60, 90):
            self.assertIn("catch-up deferred", self.recover(BACK + timedelta(minutes=minutes))[0])
            self.assertEqual(self.reply(BACK + timedelta(minutes=minutes, seconds=1))[0],
                             "1 annotation reply waits for the catch-up message after the outage")
        late = BACK + timedelta(hours=2)
        self.assertEqual(self.reply(late), [f"replied to {self.morning} with its annotations"])  # (held lets go first)
        self.assertEqual(self.recover(late), ["outage-3: no catch-up sent (none sent within 2 h of the model's "
                                              "return); noted in the chat session"])
        self.assertEqual(self.recover(late + timedelta(minutes=30)), [])
        self.assertEqual((len(self.asked), len(self.sends())), (4, 1))  # no letter after the deadline

    def test_an_unreadable_chat_holds_the_replies_only_until_the_deadline(self):
        self.outage_with_entries()
        self.annotated(self.morning, BACK)
        self.answers = [agent.AgentUnavailable("timed out after 150 s")]
        self.recover(BACK)  # the letter's record waits
        self.assertEqual(self.recs()[0]["status"], "waiting")
        with mock.patch.object(privacy, "covering", lambda *a, **k: privacy.Closed("database is locked")):
            self.assertEqual(self.recover(BACK + timedelta(minutes=90)),
                             ["outage catch-up skipped: sessions database unavailable (ChatUnreadable)"])
            self.assertEqual(self.reply(BACK + timedelta(minutes=90))[0],
                             "1 annotation reply waits for the catch-up message after the outage")
            late = BACK + timedelta(hours=2)
            self.assertEqual(self.reply(late), [f"replied to {self.morning} with its annotations"])
            self.assertEqual(self.recover(late), ["outage-3: no catch-up sent (none sent within 2 h of the model's "
                                                  "return); noted in the chat session"])
        self.assertEqual(self.recs()[0]["status"], "noted")
        self.assertIn("the replies to the entries go to the chat as usual", self.mirror.calls[-1]["text"])

    def test_no_record_by_the_deadline_begins_no_letter(self):
        # the chat unreadable from the moment she is back: no record is ever made; the replies go at the deadline,
        # and once the chat reads again no late letter takes them (the chat catch-up goes as before)
        self.outage_with_entries()
        self.annotated(self.morning, BACK)
        with mock.patch.object(privacy, "covering", lambda *a, **k: privacy.Closed("database is locked")):
            self.recover(BACK)
            self.assertEqual(self.reply(BACK)[0], "1 annotation reply waits for the catch-up message after the outage")
            self.assertEqual(self.reply(BACK + timedelta(hours=2)),
                             [f"replied to {self.morning} with its annotations"])
        self.recover(BACK + timedelta(hours=2, minutes=5))
        self.assertFalse(any(r.get("entries") or r.get("prompts") for r in self.recs()))

    def test_a_note_never_says_stale_replies_go(self):
        # an outage of 26 h: his words are past stale_hours (24) when she is back; no usable letter
        outage.trip(self.cfg, "exit 1: 503", T0)
        pid = self.entry("morning", T0 + timedelta(minutes=30), "slept badly, the sea again", "100", "11")
        back = T0 + timedelta(hours=26)
        outage.answered(self.cfg, back)
        self.answers = ['You said "I never slept at all".']
        for i in range(recovery.ATTEMPTS):
            self.recover(back + timedelta(minutes=i))
        note = self.mirror.calls[-1]["text"]
        self.assertIn("the replies to the entries are too late to send and stay in the entries", note)
        self.assertNotIn("go to the chat as usual", note)
        self.annotated(pid, back + timedelta(minutes=5))
        self.assertIn("annotation reply not sent: stale", self.reply(back + timedelta(minutes=5))[0])

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


    def test_an_x_window_in_the_outage_and_entry_words_written_on_a_face(self):
        outage.trip(self.cfg, "exit 1: 503", T0)
        at = T0 + timedelta(hours=2)
        self.db.add("/x", at, mid="20")
        self.db.add("something private about work", at + timedelta(minutes=1), mid="21")
        self.db.add(NOTICE, at + timedelta(minutes=1, seconds=30), role="assistant", display_kind="failed_turn",
                    meta=PROVIDER_DOWN)
        capture.capture_web(self.cfg, None, "the harbour lights at dusk, again", T0 + timedelta(hours=4))
        outage.answered(self.cfg, BACK)
        self.answers = ['Back now. You wrote "the harbour lights at dusk" — what did they bring up?']
        self.recover(BACK)
        (query, _), = self.asked
        self.assertNotIn("something private", query)
        self.assertIn("the harbour lights at dusk, again", query)  # (from the entry: it was never a chat message)
        self.assertEqual(len(self.sends()), 1)

    def test_a_window_opened_while_it_is_written_holds_the_send(self):
        self.outage_with_entries()

        def ask(cfg, query, budget, toolsets=None):  # (he types /x while the model writes, up to 150 s)
            self.asked.append((query, toolsets))
            self.db.add("/x", BACK + timedelta(seconds=30), mid="20")
            self.db.add("private now", BACK + timedelta(seconds=40), mid="21")
            return LETTER
        with mock.patch.object(agent, "ask", ask):
            self.assertEqual(self.recover(BACK),
                             ["outage-3: catch-up written; it waits for the off-the-record window to close"])
        self.assertEqual(self.sends(), [])
        self.recover(BACK + timedelta(hours=1))  # quiet since: the window has closed; the checked text goes
        self.assertEqual([s["text"] for s in self.sends()], [LETTER])
        self.assertEqual(len(self.asked), 1)

    def test_a_pass_closed_for_the_scan_alone_sends_nothing(self):
        # entries alone; the pass cannot run over the scan's 72 h, though it can for now: nothing goes
        outage.trip(self.cfg, "exit 1: 503", T0)
        capture.capture_web(self.cfg, None, "the harbour lights at dusk, again", T0 + timedelta(hours=2))
        outage.answered(self.cfg, BACK)
        with mock.patch.object(privacy, "covering", lambda *a, **k: privacy.Closed("no database")):
            self.assertEqual(self.recover(BACK),
                             ["outage catch-up skipped: sessions database unavailable (ChatUnreadable)"])
        self.assertEqual((self.asked, self.sends()), ([], []))

    def test_a_pass_closed_at_the_send_alone_sends_nothing(self):
        # the scan's pass runs, the one asked just before the send cannot: nothing goes
        outage.trip(self.cfg, "exit 1: 503", T0)
        capture.capture_web(self.cfg, None, "the harbour lights at dusk, again", T0 + timedelta(hours=2))
        outage.answered(self.cfg, BACK)
        view = privacy.view
        with mock.patch.object(privacy, "covering", lambda cfg, oldest, now=None, db=None:
                               view(cfg, now, since=oldest, db=db)), \
                mock.patch.object(privacy, "view", lambda *a, **k: privacy.Closed("no database")):
            self.assertEqual(self.recover(BACK), [])
        self.assertEqual((self.asked, self.sends()), ([], []))
        self.assertEqual(self.recs()[0]["status"], "waiting")


class RollbackTest(Base):
    """A letter still waiting when the code is rolled back: the older ``recovery.run`` reads a waiting record's first
    message (``waiting.get("messages", [{}])[0]``), so a record of entries alone has no ``messages`` key at all."""
    BEFORE = "d6c7f06"  # main just before CATCH-UP-LETTER

    def waiting_letter(self):
        self.db.add("morning!", T0 - timedelta(hours=1), mid="1")
        self.db.add("Morning. Sleep well?", T0 - timedelta(hours=1) + timedelta(seconds=20), role="assistant")
        outage.trip(self.cfg, "exit 1: 503", T0)
        capture.capture_web(self.cfg, None, "wrote this in the app, the sea again", T0 + timedelta(hours=2))
        outage.answered(self.cfg, BACK)
        self.answers = [agent.AgentUnavailable("timed out after 150 s")]
        self.recover(BACK)
        (rec,) = self.recs()
        self.assertEqual(rec["status"], "waiting")
        return rec

    def before_letter(self):
        """The older module, from git history (skipped where the history is not there, as in a shallow clone)."""
        try:
            source = subprocess.run(["git", "show", f"{self.BEFORE}:src/journal/recovery.py"], capture_output=True,
                                    text=True, check=True, cwd=Path(__file__).resolve().parents[1]).stdout
        except (OSError, subprocess.CalledProcessError):
            self.skipTest(f"{self.BEFORE} is not in this clone's history")
        spec = importlib.util.spec_from_loader("journal._recovery_before_letter", loader=None)
        module = importlib.util.module_from_spec(spec)
        module.__package__ = "journal"
        exec(compile(source, "recovery.py@" + self.BEFORE, "exec"), module.__dict__)
        return module

    def test_an_entries_only_record_has_no_messages_key(self):
        rec = self.waiting_letter()
        self.assertNotIn("messages", rec)
        self.assertEqual(int(rec.get("messages", [{}])[0].get("row") or 0), 0)  # (the older code's read)

    def test_a_record_whose_messages_were_answered_live_has_no_messages_key(self):
        self.outage_with_entries()
        self.answers = [agent.AgentUnavailable("timed out after 150 s")]
        self.recover(BACK)  # the record waits with his chat messages and the entries
        self.assertEqual(len(self.recs()[0]["messages"]), 2)
        self.db.add("ok, you work again?", BACK + timedelta(minutes=1), mid="14")
        self.db.add("I'm here.", BACK + timedelta(minutes=1, seconds=10), role="assistant")
        log = self.recover(BACK + timedelta(minutes=35))
        self.assertIn("messages were answered live since; the catch-up keeps his journal entries", log[0])
        (rec,) = self.recs()
        self.assertEqual(rec["status"], "waiting")
        self.assertNotIn("messages", rec)
        log = []
        self.before_letter().run(self.cfg, BACK + timedelta(minutes=40), log)  # (the older code, on this shape)
        self.assertEqual(log, [f"{rec['id']}: no catch-up (answered live since)"])

    def test_the_older_code_runs_on_a_waiting_letter(self):
        rec = self.waiting_letter()
        older = self.before_letter()
        log = []
        older.run(self.cfg, BACK + timedelta(minutes=5), log)  # (raised IndexError on ``messages: []``)
        self.assertEqual(log, [f"{rec['id']}: no catch-up (answered live since)"])


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
