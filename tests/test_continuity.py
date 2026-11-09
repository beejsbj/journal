"""Continuity for day reflections and week reviews (DECISIONS.md, CONTINUITY): what she reads of what came before (her
last few analyses, what he wrote back to each, the notebook threads the engine filed from the journal), the one tool
she is given (``journal_search``: ``test_search.py``), only his words in quotation marks (retried, and on the last
attempt the sentence goes, not the reflection), one voice across the requests, and the engine's own model steps
getting nothing from the journal's hook."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import test_conversations
from helpers import CHAT, add_prompt, make_cfg
from test_reflect import GOOD, IST, Base

from journal import (agent, capture, conversations, entries, ledger, notebook, prompts, reflect, sweep, tools,
                     validate)

tc_m = test_conversations.m


class Earlier(Base):
    """A reflection test with her earlier analyses written straight into the journal (and, when sent, the ledger)."""

    def analysis(self, kind, day, body, sent=True):
        d = date.fromisoformat(day)
        written = datetime.combine(d, datetime.min.time(), tzinfo=IST) + timedelta(hours=23, minutes=50)
        path = self.cfg.root / f"{day}-{kind}.md"
        entries.write_file(path, reflect.render(self.cfg, kind, d, body, [], written))
        if not sent:  # (a file of hers with no send on record: nothing he could have replied to)
            return path
        rec = {"id": f"{day}-{kind}", "kind": kind, "local_date": day, "attempts": 1, "written_at": written.isoformat(),
               "path": str(path), "telegram": {"message_id": f"5{d.day}", "sent_at": written.isoformat()}}
        with ledger.locked(self.cfg.ledger_path) as data:
            data.setdefault("reflections", []).append(rec)
        return path

    def evening(self, words="rain all day, walked anyway. the soup was good. But idk. the bus can wait"):
        sent = self.day()
        self.answer_prompt("evening", "2026-10-14", words, sent + timedelta(minutes=10), "11")

    def query(self):
        alerts, log = self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.assertEqual(alerts, [])
        return self.asked[-1][0], log


class ContinuityTest(Earlier):
    def test_she_reads_her_last_three_reflections_and_the_weeks_review_with_their_questions(self):
        for n in (9, 10, 11, 12, 13):
            self.analysis("reflection", f"2026-10-{n:02d}", f"**What I notice** — the day of the {n}th.\n\n"
                                                           f"**To carry forward** — Question {n}?")
        self.analysis("week", "2026-10-11", "**What I notice** — a slow week.\n\n**To carry forward** — Week question?")
        self.evening()
        query, _ = self.query()
        self.assertEqual(self.asked[-1][1], tools.READ_TOOLSET)  # her one tool, journal_search, and nothing else
        for n in (11, 12, 13):
            self.assertIn(f"### reflection:2026-10-{n}-reflection (your day reflection,", query)
            self.assertIn(f"You asked him: Question {n}?", query)
        self.assertNotIn("the day of the 10th", query)  # three earlier reflections, not four
        self.assertIn("### week:2026-10-11-week (your week review, Sun 11 Oct)", query)
        order = [query.index(s) for s in ("reflection:2026-10-11-reflection", "week:2026-10-11-week",
                                          "reflection:2026-10-12-reflection", "reflection:2026-10-13-reflection")]
        self.assertEqual(order, sorted(order))  # oldest first, as they happened
        self.assertIn("(his words):\n- (none in the chat; he may have taken it up in an entry)", query)
        self.assertLess(query.index("## Before the day"), query.index("## The entries for the day"))

    def test_nothing_before_says_so(self):
        self.evening()
        query, _ = self.query()
        self.assertIn("## Before the day (your own words, except his replies, which are marked as his)\n"
                      "(nothing yet: this is your first)", query)

    def test_his_reply_to_an_earlier_reflection_is_found_through_the_entries(self):
        body = ("**What I notice** — the quiet after the move felt like a held breath, and you let it be.\n\n"
                "**To carry forward** — What would you keep of the quiet?")
        self.analysis("reflection", "2026-10-13", body)
        self.analysis("reflection", "2026-10-12", "**What I notice** — an older day that he never answered.")
        at = datetime(2026, 10, 14, 8, 10, tzinfo=IST)
        note = self.cfg.root / "2026-10-14-j.md"
        note.write_text("---\ntype: journal\nkind: j\n---\n# Note\n\n## Conversation\n\n"
                        + conversations.his_block(self.cfg, at, "I kept the window open all evening. That, I think.",
                                                  quote="the quiet after the move felt like a held breath, and",
                                                  key="m55")
                        + "\n" + conversations.his_block(self.cfg, at, "not this one",
                                                         quote="some other message of hers about the weather and rain",
                                                         key="m56")
                        + "\n" + conversations.his_block(self.cfg, at, "too short to say", quote="the quiet after",
                                                         key="m57"))
        self.evening()
        query, _ = self.query()
        part = query[query.index("### reflection:2026-10-13-reflection"):query.index("## The entries for the day")]
        self.assertIn("You asked him: What would you keep of the quiet?", part)
        self.assertIn("What Theo wrote back to it in the chat (his words):\n"
                      "- [2026-10-14-j] **08:10** — I kept the window open all evening. That, I think.", part)
        self.assertNotIn("not this one", part)       # a reply to another message of hers
        self.assertNotIn("too short to say", part)   # a quote this short says nothing of what it answers
        older = query[query.index("### reflection:2026-10-12-reflection"):]
        self.assertIn("- (none in the chat;", older)

    def test_an_unsent_reflection_has_no_replies_to_look_for(self):
        self.analysis("reflection", "2026-10-13", "**What I notice** — the quiet after the move felt like a held "
                                                  "breath.", sent=False)
        note = self.cfg.root / "2026-10-14-j.md"
        note.write_text("---\ntype: journal\nkind: j\n---\n# Note\n\n## Conversation\n\n"
                        + conversations.his_block(self.cfg, datetime(2026, 10, 14, 8, 10, tzinfo=IST), "yes, held",
                                                  quote="the quiet after the move felt like a held breath", key="m1"))
        self.evening()
        query, _ = self.query()
        self.assertIn("- (none in the chat;", query)

    def test_only_notebook_threads_the_engine_filed_from_the_journal(self):
        self.analysis("reflection", "2026-10-13", "**What I notice** — the move.")
        self.evening()
        notebook.add(self.cfg, "ask whether the move settled", source="2026-10-13-reflection", origin="reflection")
        notebook.add(self.cfg, "ask about the soup recipe", source="2026-10-14-evening", origin="annotation")
        notebook.add(self.cfg, "what he told me about the private thing", source="2026-10-14-evening")  # chat: hers
        notebook.add(self.cfg, "a thread from a file that is gone", source="2026-09-01-evening", origin="annotation")
        notebook.add(self.cfg, "a path, not a file of the journal", source="../elsewhere", origin="annotation")
        query, _ = self.query()
        self.assertIn("### Open threads in your notebook (from the journal; your words)\n"
                      "- ask whether the move settled [from 2026-10-13-reflection] — noted", query)
        self.assertIn("- ask about the soup recipe [from 2026-10-14-evening]", query)
        for absent in ("the private thing", "a file that is gone", "not a file of the journal"):
            self.assertNotIn(absent, query)

    def test_her_follow_ups_are_filed_on_the_record(self):
        self.evening()
        self.query()
        item = notebook.open_items(self.cfg)[0]
        self.assertEqual((item["text"], item["source"], item["origin"]),
                         ("ask how the visit went", "2026-10-14-reflection", "reflection"))
        self.assertEqual([i["id"] for i in notebook.on_record(self.cfg)], [item["id"]])
        with mock.patch.object(tools, "_off_record_turn", return_value=False):
            tool = json.loads(tools.call(self.cfg, "journal_notebook", {"action": "add", "text": "from chat",
                                                                         "source": "2026-10-14-evening",
                                                                         "origin": "annotation"}))
        self.assertNotIn("origin", tool["item"])  # the model cannot claim the engine's origin through the tool
        self.assertEqual(len(notebook.on_record(self.cfg)), 1)

    def test_the_week_review_reads_the_two_reviews_before_it(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections_since"] = "2026-10-12"
        for day in ("2026-09-27", "2026-10-04", "2026-10-11"):
            self.analysis("week", day, f"**What I notice** — the week ending {day}.")
        wk = self.prompt("weekly", "2026-10-18", "20", "20", "End of the week.")
        self.answer_prompt("weekly", "2026-10-18", "a soft week", wk + timedelta(minutes=10), "20")
        self.reflect(datetime(2026, 10, 18, 20, 40, tzinfo=IST))
        query, toolsets = self.asked[-1]
        self.assertEqual(toolsets, tools.READ_TOOLSET)
        self.assertIn("the week ending 2026-10-11", query)
        self.assertIn("the week ending 2026-10-04", query)
        self.assertNotIn("the week ending 2026-09-27", query)
        self.assertIn("## Before the week", query)


class QuotesTest(Earlier):
    """Only his words in quotation marks: a quote that is not his is a bad answer and retried; on the last attempt the
    sentence that holds it goes, so a stubborn misquote costs a sentence, not the reflection."""

    def tick(self, minute):
        return self.reflect(datetime(2026, 10, 14, 23, minute, tzinfo=IST))

    def test_an_invented_quote_is_retried_then_its_sentence_dropped_on_the_last_attempt(self):
        self.evening()
        self.answer = GOOD.replace("rain all day, walked anyway", "I miss the lake house")
        for n, minute in ((1, 45), (2, 50)):
            alerts, log = self.tick(minute)
            self.assertEqual(alerts, [])
            self.assertIn(f"2026-10-14-reflection: reflection attempt {n}/3 failed (quote not in his words: "
                          "'I miss the lake house')", log)
            self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())
        alerts, log = self.tick(55)
        self.assertEqual(alerts, [])
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook) (last attempt: 1 sentence with a quote "
                      "not in his words dropped)", log)
        body = (self.cfg.root / "2026-10-14-reflection.md").read_text()
        self.assertNotIn("lake house", body)
        self.assertIn("**Patterns and tensions** — That reads as patience held lightly (inference).", body)
        self.assertIn("**What stood out** — The rain, and a walk in it anyway.", body)
        self.assertEqual(self.rec("2026-10-14-reflection")["struck"], ["I miss the lake house"])
        self.assertEqual(len(self.bot.calls), 1)
        self.assertNotIn("lake house", self.bot.calls[0][1]["text"])

    def test_his_words_reached_through_search_keep_their_marks(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-09-20",
                   sent=datetime(2026, 9, 20, 17, 31, tzinfo=timezone.utc), message_id="3")
        capture.capture(self.cfg, CHAT, "3", "the harbour at dusk, every time, like a door",
                        datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc), "text", "1003")
        self.evening()
        self.answer = GOOD.replace("“the soup was good … the bus can wait”", '"the harbour at dusk … like a door"')
        _, log = self.tick(45)
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)
        body = (self.cfg.root / "2026-10-14-reflection.md").read_text()
        self.assertIn('"the harbour at dusk … like a door"', body)  # his words, in an entry weeks ago
        self.assertNotIn("struck", self.rec("2026-10-14-reflection"))

    def test_her_words_never_count_as_his(self):
        old = add_prompt(self.cfg, kind="evening", local_date="2026-09-20",
                         sent=datetime(2026, 9, 20, 17, 31, tzinfo=timezone.utc), message_id="3")
        capture.capture(self.cfg, CHAT, "3", "the harbour again", datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc),
                        "text", "1003")
        entries.write_annotations(self.cfg.root / f"{old['id']}.md", "- He lingered by the water.", self.cfg.names)
        self.analysis("reflection", "2026-10-13", "**What I notice** — circling a decision you have already made.\n\n"
                                                  "**To carry forward** — What would being there look like?")
        notebook.add(self.cfg, "ask whether the move settled", source="2026-10-13-reflection", origin="reflection")
        self.evening()
        for quote in ("He lingered by the water",                    # her old annotation
                      "circling a decision you have already made",   # her own earlier reflection, in her inputs
                      "What would being there look like?",           # her question, quoted back
                      "ask whether the move settled",                # her notebook thread
                      "He walked in the rain"):                      # today's annotation, in the entry she reads
            with self.subTest(quote):
                with ledger.locked(self.cfg.ledger_path) as data:
                    data["reflections"] = [r for r in data.get("reflections") or [] if r["id"] != "2026-10-14-reflection"]
                self.answer = GOOD.replace("rain all day, walked anyway", quote)
                _, log = self.tick(45)
                self.assertTrue(any("failed (quote not in his words:" in l for l in log), log)
                self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())

    def test_a_quote_stitched_from_several_entries_is_not_his(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-09-20",
                   sent=datetime(2026, 9, 20, 17, 31, tzinfo=timezone.utc), message_id="3")
        capture.capture(self.cfg, CHAT, "3", "I want to sleep early for once",
                        datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc), "text", "1003")
        self.evening()
        self.answer = GOOD.replace("rain all day, walked anyway", "I want … to quit … the bus can wait")
        _, log = self.tick(45)
        self.assertTrue(any("failed (quote not in his words: 'I want … to quit … the bus can wait')" in l
                            for l in log), log)

    def test_a_blockquote_line_that_is_not_his_goes_on_the_last_attempt(self):
        self.evening()
        self.answer = GOOD.replace("**To carry forward**", "> I never said this\n\n**To carry forward**")
        self.tick(45)
        self.tick(50)
        _, log = self.tick(55)
        body = (self.cfg.root / "2026-10-14-reflection.md").read_text()
        self.assertNotIn("I never said this", body)
        self.assertIn("**To carry forward** — What would being there look like, on your terms?", body)

    def test_a_stray_quote_mark_is_dropped(self):
        self.evening()
        self.answer = GOOD.replace("That reads as", "You said “so much. That reads as")
        _, log = self.tick(45)
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook) (1 stray quote mark dropped)", log)
        self.assertIn("You said so much. That reads as", (self.cfg.root / "2026-10-14-reflection.md").read_text())

    def test_an_answer_that_is_no_reflection_still_writes_nothing(self):
        self.evening()
        self.answer = "too short"
        _, log = self.tick(45)
        self.assertIn("2026-10-14-reflection: reflection attempt 1/3 failed (empty or too short)", log)
        self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())


class LongRunTest(Earlier):
    def setUp(self):
        super().setUp()
        self.evening()

    def ask(self, cfg, query, budget, toolsets=None):
        self.asked.append((query, toolsets, budget))
        raise agent.AgentUnavailable(f"timed out after {budget + 60}s")

    def test_a_timeout_after_the_model_answered_this_tick_uses_an_attempt(self):
        with mock.patch.object(agent, "answered_this_run", return_value=True):
            alerts, log = self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.assertEqual(self.asked[0][2], reflect.BUDGET)
        self.assertIn("2026-10-14-reflection: reflection attempt 1/3 failed (timed out after 540s)", log)
        rec = self.rec("2026-10-14-reflection")
        self.assertEqual(rec["attempts"], 1)
        self.assertNotIn("deferred_since", rec)

    def test_a_timeout_with_no_other_answer_is_still_an_outage(self):
        with mock.patch.object(agent, "answered_this_run", return_value=False):
            _, log = self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
        self.assertIn("2026-10-14-reflection: reflection deferred, model unreachable (timed out after 540s)", log)
        rec = self.rec("2026-10-14-reflection")
        self.assertNotIn("attempts", rec)
        self.assertIn("deferred_since", rec)


class QuoteCheckTest(unittest.TestCase):
    DOCS = ["rain all day, walked anyway. the soup was good. But idk. the bus can wait",
            "I want to sleep. the window was open"]

    def bad(self, text, docs=None, wider=None):
        return [q for _, _, q in validate.unfound(text, self.DOCS if docs is None else docs, wider)]

    def test_straight_quotes_pair_left_to_right_whatever_their_length(self):
        self.assertEqual(self.bad('You said "No" and then "I never loved any of it" last night.'),
                         ["I never loved any of it"])
        self.assertEqual(self.bad('You wrote " I never loved any of it " on Tuesday.'), ["I never loved any of it"])
        self.assertEqual(self.bad('"she told me "I never loved any of it" and left"'), ["she told me", "and left"])
        self.assertEqual(self.bad('You wrote "rain all day, walked anyway" and “the soup was good … the bus can wait”.'),
                         [])
        # the other checks read the same pairs: the catch-up's and a program's quote checks see the misquote too
        self.assertFalse(validate.check_catchup('You said "No" and then "I never loved any of it" last night.',
                                                "No, and then I left. I loved some of it")[0])

    def test_a_stray_mark_is_dropped(self):
        self.assertEqual(validate.drop_stray_marks('You said “I never rest, and "then" more " left.'),
                         ('You said I never rest, and "then" more  left.', 2))

    def test_fragments_must_be_in_one_document_in_order(self):
        self.assertEqual(self.bad("“the soup … the bus”"), [])
        self.assertEqual(self.bad("“the bus … the soup”"), ["the bus … the soup"])        # out of order
        self.assertEqual(self.bad("“I want … the bus can wait”"), ["I want … the bus can wait"])  # two documents

    def test_the_wider_documents_are_read_once_and_only_when_needed(self):
        calls = []

        def wider():
            calls.append(1)
            return ["I miss the lake house. I never rest."]
        self.assertEqual(self.bad('"rain all day"', wider=wider), [])
        self.assertEqual(calls, [])
        self.assertEqual(self.bad('“I miss the lake house” and "I never rest" and "made up words"', wider=wider),
                         ["made up words"])
        self.assertEqual(calls, [1])

    def test_strike_drops_the_sentence_with_its_attribution(self):
        text = ('**What I notice** — Fine day. You wrote, "I am done pretending…" and it showed. Then rain.\n'
                '- As you put it, *"a slow kind of drowning"*. The walk helped.\n'
                '> I never rest\n'
                'End.')
        out = validate.strike(text, validate.unfound(text, self.DOCS))
        self.assertEqual(out, "**What I notice** — Fine day. Then rain.\n- The walk helped.\nEnd.")

    def test_the_other_checks_still_reject_a_misquote(self):
        text = "x" * 80 + '\nYou said "I am exhausted"'
        self.assertFalse(validate.check_reflection("insights", text, "nothing")[0])
        self.assertTrue(validate.check_reflection("reflection", text, "nothing", misquotes=False)[0])


class ReplyThroughConversationsTest(test_conversations.Base):
    """His reply to a sent reflection, filed by the conversation writer as it really is (a note of its own), reaches
    the next reflection as his reply."""

    def run_both(self, mode):
        self.cfg.capture = {"mode": mode}
        refl = self.cfg.root / "2026-10-15-reflection.md"
        entries.write_file(refl, reflect.render(self.cfg, "reflection", date(2026, 10, 15),
                                                "**What I notice** — You circled the bank all day and never named the "
                                                "call itself.\n\n**To carry forward** — What is the call asking of you?",
                                                [], tc_m(300)))
        with ledger.locked(self.cfg.ledger_path) as data:
            data.setdefault("reflections", []).append(
                {"id": "2026-10-15-reflection", "kind": "reflection", "local_date": "2026-10-15", "path": str(refl),
                 "written_at": tc_m(300).isoformat(), "telegram": {"message_id": "711", "sent_at": tc_m(310).isoformat()}})
        self.his('[Replying to: "Iris\'s reflection — Thu 15 Oct 2026 What I notice — You circled the bank all day '
                 'and never named the call itself."]\n\nthat\'s fair. I was avoiding the call, not the bank',
                 tc_m(600), "700")
        self.hers("What makes the call the hard part?", tc_m(601))
        self.his("my dad used to make those calls", tc_m(603), "701")
        log = []
        sweep.run(self.cfg, tc_m(620), log)
        conversations.run(self.cfg, tc_m(620), log)
        data = ledger.snapshot(self.cfg.ledger_path)
        note = ledger.find(data, "2026-10-16-j")
        self.assertEqual(note["captures"][0]["answers"], "2026-10-15-reflection")
        before, his = reflect.continuity(self.cfg, "reflection", date(2026, 10, 16), data)
        self.assertIn("You asked him: What is the call asking of you?", before)
        self.assertIn("What Theo wrote back to it in the chat (his words):\n- [2026-10-16-j] ", before)
        self.assertIn("I was avoiding the call, not the bank", before)
        self.assertIn("my dad used to make those calls", before)  # the thread that followed, his lines only
        self.assertNotIn("What makes the call the hard part?", before)  # her chat reply is not his
        self.assertNotIn("(none in the chat", before)
        self.assertTrue(any("I was avoiding the call, not the bank" in h for h in his))

    def test_under_capture_everything(self):
        self.run_both("everything")

    def test_under_capture_arm(self):
        self.run_both("arm")

    def test_a_plain_message_is_no_reply(self):
        self.his("just thinking about the bank today, nothing else", tc_m(600), "700")
        conversations.run(self.cfg, tc_m(620), [])
        note = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-16-j")
        self.assertNotIn("answers", note["captures"][0])


class VoiceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_one_voice_in_the_four_requests(self):
        v = prompts.voice(self.cfg)
        now = datetime(2026, 10, 14, 23, 0, tzinfo=IST)
        asks = {"opener": prompts.opener_request(self.cfg, "evening", now, "PACK", "BANK"),
                "annotation": prompts.annotation_request(self.cfg, "words", None),
                "reflection": prompts.reflection_request(self.cfg, "reflection", "Wednesday", "IN", ""),
                "week": prompts.reflection_request(self.cfg, "week", "the week", "IN", "")}
        for name, text in asks.items():
            with self.subTest(name):
                self.assertIn(v, text)
                self.assertNotIn("Mark inference as inference", text)
                self.assertNotIn("mark inference as inference", text)
        self.assertIn('"I notice…"', v)

    def test_the_reflection_asks_for_her_reading_with_few_quotes(self):
        text = prompts.reflection_request(self.cfg, "reflection", "Wednesday", "IN", "BEFORE")
        for part in ("**What I notice**", "**What moved**", "**To carry forward**", "journal_search",
                     f"at most\n{prompts.QUOTES_ASKED}", "don't string his quotes together", "read past it"):
            self.assertIn(part, text)
        self.assertNotIn("is thrown away", text)
        self.assertNotIn("loses its quotation marks", text)  # the model is not told how a misquote is handled
        self.assertIn("words go in quotation marks; your own, earlier or now, you say in your own way", text)
        self.assertIn("## Before the day (your own words, except his replies, which are marked as his)\nBEFORE", text)

    def test_the_annotation_is_about_him_in_the_third_person_and_her_reading_in_the_first(self):
        self.assertIn("about him in the third person; your reading stays yours, in the first person",
                      prompts.annotation_request(self.cfg, "w", None))

    def test_a_transcript_guess_only_where_annotations_stay_in_the_file(self):
        self.assertIn("don't comment on the transcription", prompts.annotation_request(self.cfg, "w", None))
        self.cfg.telegram["annotation_replies"] = False
        self.assertIn("you may note it once", prompts.annotation_request(self.cfg, "w", None))


class ModelStepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_every_run_is_marked_as_the_engines_own(self):
        self.cfg.agent["command"] = ["echo", "{query_file}"]
        seen = {}

        def run(argv, **kw):
            seen.update(kw["env"])
            return mock.Mock(returncode=0, stdout="an answer", stderr="")
        with mock.patch("journal.agent.subprocess.run", run):
            self.assertEqual(agent.CommandCompanion(self.cfg).ask("q", 10, "none"), "an answer")
        self.assertEqual(seen[agent.MODEL_STEP_ENV], "1")

    def test_the_journals_hook_adds_nothing_to_a_model_step(self):
        from journal.hermes import hooks, runtime
        with mock.patch.dict(os.environ, {agent.MODEL_STEP_ENV: "1"}), \
                mock.patch.object(runtime, "load_config", side_effect=AssertionError("read the config")), \
                mock.patch("journal.turncontext.build", side_effect=AssertionError("built a turn context")):
            self.assertIsNone(hooks.on_llm_call(user_message="Write the reflection", platform="cli",
                                                session_id="s1", turn_id="t1"))
        notebook.add(self.cfg, "a thread from chat")
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(runtime, "load_config",
                                                                              return_value=self.cfg):
            os.environ.pop(agent.MODEL_STEP_ENV, None)
            out = hooks.on_llm_call(user_message="hello", platform="cli", session_id="s1", turn_id="t1")
        self.assertIn("a thread from chat", out["context"])  # her own turns keep their notebook, as before


if __name__ == "__main__":
    unittest.main()
