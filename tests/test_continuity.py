"""Continuity for day reflections and week reviews (DECISIONS.md, CONTINUITY): what she reads of what came before (her
last few analyses, what he wrote back to each, the notebook threads the engine filed from the journal), the one tool
she is given (``journal_search``: ``test_search.py``), only his words in quotation marks (a quote that is not his, or
an irregular mark, is a bad answer: retried, and a reflection that never passes is never written; the reason is
said in numbers, never in the reflection's words), a reflection written late bounded by its own period, one voice
across the requests, and the engine's own model steps getting nothing from the journal's hook."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import test_conversations
from helpers import CHAT, add_prompt, make_cfg, real_ask
from test_reflect import GOOD, IST, Base

from journal import (agent, capture, conversations, entries, hookstate, ledger, notebook, prompts, reflect, search,
                     status, sweep, tools, validate)
from journal.config import Names

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

    def test_with_no_chat_there_is_no_reply_to_speak_of(self):
        self.analysis("reflection", "2026-10-13", "**What I notice** — the move.\n\n**To carry forward** — Settled?")
        self.evening()
        self.cfg.platform = "none"  # (``channel.platform: none``: the reflection stays in its file)
        query, log = self.query()
        self.assertIn("2026-10-14-reflection: not sent: no chat (in its file)", log)
        self.assertIn("### reflection:2026-10-13-reflection (your day reflection, Tue 13 Oct)\nYou began: ", query)
        self.assertIn("You asked him: Settled?", query)
        self.assertIn("He will read it in\nthe journal: it is", query)
        for absent in ("in the chat", "wrote back", "None"):
            self.assertNotIn(absent, query)

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
    """Only his words in quotation marks: a quote that is not his is a bad answer and retried; a reflection that never
    passes is never written, as before."""

    def tick(self, minute):
        return self.reflect(datetime(2026, 10, 14, 23, minute, tzinfo=IST))

    def never_written(self, answer):
        """Three attempts at ``answer``, each a bad one; then the alert, and no file."""
        self.answer = answer
        for n, minute in ((1, 45), (2, 50), (3, 55)):
            alerts, log = self.tick(minute)
            self.assertTrue(any(f"reflection attempt {n}/3 failed" in l for l in log), log)
        self.assertEqual(len(alerts), 1)
        self.assertIn("2026-10-14-reflection was not written after 3 attempts", alerts[0])
        self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())
        self.assertEqual(self.bot.calls, [])
        return log

    def test_an_invented_quote_is_retried_and_never_written(self):
        self.evening()
        log = self.never_written(GOOD.replace("rain all day, walked anyway", "I miss the lake house"))
        self.assertIn("2026-10-14-reflection: reflection attempt 3/3 failed (a quote not in his words at character "
                      "96 (line 3), 23 characters long)", log)

    def test_a_rejection_is_said_in_numbers_never_in_the_reflections_words(self):
        # (review, 2026-11-10: the tick's output and the alert leave the state dir, and ``error`` is what ``journal
        # status`` and the web app show; a rejected reflection's text, which may be his words, goes to none of them)
        self.evening()
        for answer, reason in (
                (GOOD.replace("That reads as patience held lightly (inference).",
                              "You said the soup was good” and meant it."),
                 "irregular quote mark U+201D at character 216 (line 3)"),
                (GOOD.replace("rain all day, walked anyway", "I miss the lake house"),
                 "a quote not in his words at character 96 (line 3), 23 characters long")):
            with self.subTest(reason):
                with ledger.locked(self.cfg.ledger_path) as data:
                    data["reflections"] = []
                self.answer = answer
                said = []
                for minute in (45, 50, 55):
                    alerts, log = self.tick(minute)
                    said += alerts + log
                rec = self.rec("2026-10-14-reflection")
                self.assertEqual(rec["error"], reason)
                self.assertEqual(alerts, [f"2026-10-14-reflection was not written after 3 attempts: {reason}"])
                shown = str(status.status(self.cfg, datetime(2026, 10, 14, 23, 56, tzinfo=IST), web=True)["reflections"])
                self.assertIn(reason, shown)
                for line in [*said, shown]:
                    for words in ("lake house", "soup", "meant it", "rain all day"):
                        self.assertNotIn(words, line)
                # the start of a quote that was not his stays in the ledger alone (as main kept it), shown nowhere
                self.assertEqual(rec.get("rejected_quote"), "I miss the lake house" if "not in his" in reason else None)
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections"] = [dict(rec, attempts=1)]  # (attempts used up are not tried again: one left here)
        self.answer = GOOD
        _, log = self.tick(59)
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)
        self.assertNotIn("rejected_quote", self.rec("2026-10-14-reflection"))  # gone once a reflection is written

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

    def test_his_sentence_with_its_own_quotation_marks_is_written_as_he_wrote_it(self):
        self.evening('rain all day. Mother called. She said "come home" and I said no, not yet. the bus can wait')
        self.answer = GOOD.replace('"rain all day, walked anyway"', '"She said "come home" and I said no, not yet"') \
            .replace("“the soup was good … the bus can wait”", '"the bus can wait"')
        _, log = self.tick(45)
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)
        self.assertIn('You wrote "She said "come home" and I said no, not yet" and',
                      (self.cfg.root / "2026-10-14-reflection.md").read_text())

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
                self.assertTrue(any("failed (a quote not in his words at character" in l for l in log), log)
                self.assertEqual(self.rec("2026-10-14-reflection")["rejected_quote"], quote)
                self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())

    def test_a_quote_stitched_from_several_entries_is_not_his(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-09-20",
                   sent=datetime(2026, 9, 20, 17, 31, tzinfo=timezone.utc), message_id="3")
        capture.capture(self.cfg, CHAT, "3", "I want to sleep early for once",
                        datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc), "text", "1003")
        self.evening()
        self.answer = GOOD.replace("rain all day, walked anyway", "I want … to quit … the bus can wait")
        _, log = self.tick(45)
        self.assertTrue(any("failed (a quote not in his words at character" in l for l in log), log)
        self.assertEqual(self.rec("2026-10-14-reflection")["rejected_quote"], "I want … to quit … the bus can wait")

    def test_a_blockquote_line_that_is_not_his_is_a_bad_answer(self):
        self.evening()
        self.never_written(GOOD.replace("**To carry forward**", "> I never said this\n\n**To carry forward**"))

    IRREGULAR = ('You wrote "I adore naps” and slept.',                          # mixed: straight opens, curly closes
                 'You wrote "I love resting\non Sundays" and slept.',              # across a line break
                 'You wrote ”I adore naps“ and slept.',                            # curly marks the wrong way round
                 'You wrote „I adore naps“ and slept.',                            # low-9
                 'You wrote "I told her "I adore naps" and left early" on Tuesday.',  # a quote inside a quote
                 'You wrote " I never loved any of it " on Tuesday.',              # spaces inside the marks
                 'You wrote «I adore naps» and slept.',
                 'You wrote ˮI adore napsˮ and slept.',                            # the double apostrophe (a review)
                 'You said “so much. That reads as rest.')                         # a stray curly mark

    def test_an_irregular_double_quote_mark_is_a_bad_answer(self):
        self.evening()
        for text in self.IRREGULAR:
            with self.subTest(text):
                with ledger.locked(self.cfg.ledger_path) as data:
                    data["reflections"] = []
                self.bot.calls.clear()
                log = self.never_written(GOOD.replace("That reads as patience held lightly (inference).", text))
                self.assertTrue(any("failed (irregular quote mark" in l for l in log), log)

    def test_apostrophes_possessives_and_code_pass(self):
        self.evening()
        self.answer = GOOD.replace("That reads as", "You're right that Theo's walk, the kids’ room, the kids' toys, "
                                                   "the '90s, rock 'n' roll and `ls -la` aside, it's fine. "
                                                   "See <https://example.org> &amp; more. That reads as")
        _, log = self.tick(45)
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)

    def test_an_answer_that_is_no_reflection_still_writes_nothing(self):
        self.evening()
        self.answer = "too short"
        _, log = self.tick(45)
        self.assertIn("2026-10-14-reflection: reflection attempt 1/3 failed (empty or too short)", log)
        self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())


class LateReflectionTest(Earlier):
    """A reflection written after its period (the morning fallback, an outage's retry) reads the journal as it stood
    at the period's end: no later entry in its quotes, its search or its notebook threads (review, 2026-11-10)."""

    def setUp(self):
        super().setUp()
        self.evening()
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15",
                   sent=datetime(2026, 10, 15, 0, 31, tzinfo=timezone.utc), message_id="21")
        capture.capture(self.cfg, CHAT, "21", "the lake house, finally. I slept there",
                        datetime(2026, 10, 15, 1, 0, tzinfo=timezone.utc), "text", "1021")
        notebook.add(self.cfg, "ask about the soup recipe", source="2026-10-14-evening", origin="annotation")
        notebook.add(self.cfg, "ask how the lake house was", source="2026-10-15-morning", origin="annotation")

    def late(self, minute):
        return self.reflect(datetime(2026, 10, 15, 8, minute, tzinfo=IST))  # the morning after

    def test_it_cannot_quote_what_he_wrote_after_its_period(self):
        self.answer = GOOD.replace("rain all day, walked anyway", "the lake house, finally")
        _, log = self.late(0)
        self.assertEqual(log, ["2026-10-14-reflection: reflection attempt 1/3 failed (a quote not in his words at "
                               "character 96 (line 3), 25 characters long)"])
        self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())
        self.assertTrue(any("the lake house, finally" in t for t in search.his_texts(self.cfg)))  # (his, but later)
        self.answer = GOOD
        _, log = self.late(5)
        self.assertIn("wrote 2026-10-14-reflection.md (annotated) (+1 notebook)", log)

    def test_its_search_and_its_notebook_threads_stop_there_too(self):
        self.late(0)
        query = self.asked[-1][0]
        self.assertEqual(self.env, {search.UPTO_ENV: "2026-10-14"})
        self.assertIn("in every entry up to the end of the day", query)
        self.assertIn("- ask about the soup recipe [from 2026-10-14-evening]", query)
        self.assertNotIn("lake house", query)  # neither the later entry nor the thread filed from it


class DoubleQuoteMarksTest(Earlier):
    """Every double quote mark, of any kind, outside a well-formed pair is irregular (a class: review rounds 4 and
    5); Obsidian's blockquote shapes are checked. Each input wrote an invented line into a saved reflection on attempt 1
    of an earlier round. Single quotes, HTML and paraphrase are not judged in this PR (an open issue)."""

    INVENTED = "I hate my job"

    def tick(self, minute):
        return self.reflect(datetime(2026, 10, 14, 23, minute, tzinfo=IST))

    INPUTS = [f"You wrote ″{INVENTED}″ today.", f"You wrote ＂{INVENTED}＂ today.", f"You wrote ❝{INVENTED}❞ today.",
              f"You wrote 〝{INVENTED}〞 today.", f"You wrote 「{INVENTED}」 today.", f"You wrote 『{INVENTED}』 today.",
              f"You wrote ˮ{INVENTED}ˮ today.", f"You wrote ‶{INVENTED}‶ today.", f"You wrote «{INVENTED}» today."]
    BLOCKS = [f"   > {INVENTED}", f"- > {INVENTED}", f"1. > {INVENTED}",
              f"> rain all day, walked anyway\n{INVENTED}"]  # (lazy continuation: Obsidian renders it in the quote)

    def test_none_of_them_is_ever_saved(self):
        self.evening()
        for text in self.INPUTS:
            self.run_one(GOOD.replace("That reads as patience held lightly (inference).", text), text)
        for text in self.BLOCKS:
            self.run_one(GOOD.replace("**To carry forward**", text + "\n\n**To carry forward**"), text)

    def run_one(self, answer, text):
        with self.subTest(text):
            with ledger.locked(self.cfg.ledger_path) as data:
                data["reflections"] = []
            self.answer = answer
            for n, minute in ((1, 45), (2, 50), (3, 55)):
                _, log = self.tick(minute)
                self.assertTrue(any(f"reflection attempt {n}/3 failed" in l for l in log), log)
            self.assertFalse((self.cfg.root / "2026-10-14-reflection.md").exists())

    def test_the_class_covers_every_double_mark_and_no_single_one(self):
        import unicodedata
        docs = [f"{self.INVENTED}, he said"]
        pipf = [chr(c) for c in range(0x110000) if unicodedata.category(chr(c)) in ("Pi", "Pf")]
        doubles = [c for c in pipf if "DOUBLE" in unicodedata.name(c)]
        self.assertTrue({"“", "”", "«", "»", "‟"} <= set(doubles))
        for c in pipf:
            with self.subTest(hex(ord(c))):
                self.assertEqual(validate.quote_like(c), c in doubles)
        for c in doubles + list("\"＂„″‴⁗‶‷ʺˮ❝❞〝〞〟「」『』﹁﹂﹃﹄｢｣🙶🙷🙸⹂"):
            with self.subTest(hex(ord(c))):
                self.assertTrue(validate.quote_like(c))
                for text in (f"The {c}word here.", f"The word{c} here."):
                    self.assertTrue(any(q.startswith("irregular") for _, _, q in validate.unfound(text, docs)), text)
        for c in "‘’‛‚‹›❛❜❮❯'`＇′":
            with self.subTest(hex(ord(c))):
                self.assertFalse(validate.quote_like(c))
        self.assertEqual(validate.unfound(f'He said "{self.INVENTED}".', docs), [])
        self.assertEqual(validate.unfound(f"He said “{self.INVENTED}”.", docs), [])

    def test_single_quotes_html_and_paraphrase_are_left_to_the_follow_up(self):
        # (not judged in this PR: an open issue, DECISIONS.md CONTINUITY; checked here so the gap is on record)
        docs = ["rain all day"]
        for text in (f"You wrote ‘{self.INVENTED}’.", f"You wrote '{self.INVENTED}'.", f"You wrote `{self.INVENTED}`.",
                     f"You wrote &quot;{self.INVENTED}&quot;.", f"You wrote <q>{self.INVENTED}</q>.",
                     f"You wrote: {self.INVENTED}.", "the kids’ room", "the '90s", "<https://example.org>"):
            with self.subTest(text):
                self.assertEqual(validate.unfound(text, docs), [])

    def test_blockquote_shapes_and_their_lazy_lines_are_checked(self):
        docs = ["rain all day, walked anyway"]
        for text in self.BLOCKS:
            with self.subTest(text):
                self.assertEqual(len(validate.unfound(text, docs)), 1)
        self.assertEqual(validate.unfound("> rain all day, walked anyway", docs), [])
        self.assertEqual(validate.unfound("- > rain all day,\n  walked anyway", docs), [])
        self.assertEqual(validate.unfound("    > four spaces is code, not a quote", docs), [])

    def test_the_program_and_catch_up_checks_use_the_same_check(self):
        corpus = "I want to sleep, honestly. " + "x " * 200 + "Then to quit."
        for text in (f"You wrote ″{self.INVENTED}″ today, so rest.", 'You said "No" and slept.',
                     '"I want … to quit" is what you said.', f"You wrote «{self.INVENTED}», so rest.",
                     f'You wrote "{self.INVENTED}", so rest.'):
            with self.subTest(text):
                self.assertFalse(validate.check_catchup(text, corpus)[0])
                self.assertFalse(validate.check_reflect_phase(text, corpus)[0])
                self.assertFalse(validate.check_reflection("insights", "x" * 80 + "\n" + text, corpus)[0])
                self.assertFalse(validate.check_bridge(text.rstrip("."), corpus)[0])
        self.assertTrue(validate.check_catchup('You said "I want to sleep, honestly" and you should.', corpus)[0])
        self.assertTrue(validate.check_catchup("You said you wanted to sleep; do.", corpus)[0])  # (paraphrase)


class LongRunTest(Earlier):
    def setUp(self):
        super().setUp()
        self.envs = []
        self.evening()

    def ask(self, cfg, query, budget, toolsets=None, env=None):
        self.asked.append((query, toolsets, budget))
        self.envs.append(env)
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

    def test_after_a_deferred_timeout_the_next_try_goes_without_the_search(self):
        with mock.patch.object(agent, "answered_this_run", return_value=False):
            self.reflect(datetime(2026, 10, 14, 23, 45, tzinfo=IST))
            self.reflect(datetime(2026, 10, 15, 0, 20, tzinfo=IST))  # retried after 30 minutes
        (q1, tools1, budget1), (q2, tools2, budget2) = self.asked
        self.assertEqual((tools1, budget1), (tools.READ_TOOLSET, reflect.BUDGET))
        self.assertEqual((tools2, budget2), ("none", reflect.RETRY_BUDGET))
        self.assertIn("journal_search", q1)
        self.assertNotIn("journal_search", q2)
        self.assertNotIn("look it up", q2)
        self.assertEqual(self.rec("2026-10-14-reflection")["defer_error"], "timed out after 360s")
        self.assertEqual(self.envs, [{"JOURNAL_SEARCH_UPTO": "2026-10-14"}, None])  # (no search, nothing to bound)


class QuoteCheckTest(unittest.TestCase):
    DOCS = ["rain all day, walked anyway. the soup was good. But idk. the bus can wait",
            "I want to sleep. the window was open\n\nanother paragraph: go on, be brave, it is late"]

    def bad(self, text, docs=None, wider=None):
        return [q for _, _, q in validate.unfound(text, self.DOCS if docs is None else docs, wider)]

    def test_only_well_formed_double_quotation_counts(self):
        self.assertEqual(self.bad('You wrote "rain all day, walked anyway" and “the soup was good … the bus can wait”.'),
                         [])
        self.assertEqual(self.bad("> the window was open"), [])
        for text in QuotesTest.IRREGULAR + ('"she told me "I never loved any of it" and left"',
                                            'You wrote ”I adore naps” and slept.'):
            with self.subTest(text):
                found = self.bad(text)
                self.assertTrue(any(f.startswith("irregular quote mark") for f in found), found)
        self.assertEqual(self.bad("You're right; Theo's walk, the kids’ room, the '90s, `code`, don't."), [])

    def test_a_sentence_of_his_with_quotation_marks_of_its_own_can_be_quoted_whole(self):
        # (review, 2026-11-10: such a sentence could not be quoted in any form, and three identical answers lost the
        # reflection. A quotation may close at a later mark of its line when the whole of it is his verbatim.)
        his = ['Mother called. She said "come home" and I said no, I am not ready.\n\nrain all day, walked anyway.',
               "Later she said “wait… come home” and left."]
        for text in ('You wrote "She said "come home" and I said no" and left it there.',
                     "You wrote “She said \"come home\" and I said no”.",       # curly outside, his marks inside
                     'You wrote "she said "wait… come home" and left".',        # his curly marks, written straight
                     'You wrote "She said "come home"".',                       # his mark is the last thing quoted
                     'You wrote "She said "come home" and I said no." Then nothing more.',
                     '"rain all day" and "She said "come home" and I said no" both stayed with me.'):
            with self.subTest(text):
                self.assertEqual(self.bad(text, docs=his), [])
        for text, first in (('You wrote "She said "go away" and I said no".', "irregular quote mark U+0022 at"),
                            ('You wrote "I … "come home" and I said no".', "irregular quote mark U+0022 at"),  # stitched
                            ("You wrote \"She said 'come home' and I said no\".", "She said 'come home' and I said no"),
                            ('"She said "come home" and I said no" and "made up words".', "made up words")):
            with self.subTest(text):
                self.assertTrue(self.bad(text, docs=his)[0].startswith(first), self.bad(text, docs=his))
        calls = []

        def wider():
            calls.append(1)
            return ['Weeks ago: he told me "not yet" and laughed.']
        self.assertEqual(self.bad('You wrote "he told me "not yet" and laughed".', docs=his, wider=wider), [])
        self.assertEqual(self.bad('You wrote "rain all day" and "Mother called".', docs=his, wider=wider), [])
        self.assertEqual(calls, [1])  # (a line with no irregular mark is never read a second time)

    def test_a_short_quote_is_checked_as_a_whole_word(self):
        self.assertEqual(self.bad('You said "No".'), ["No"])                         # not a word of his here
        self.assertEqual(self.bad('You said "No".', docs=["No, I stayed in."]), [])
        self.assertEqual(self.bad('You said "no".', docs=["I know it."]), ["no"])     # (not the end of "know")
        self.assertEqual(self.bad('"Go … be … it"'), [])                             # (go on, be brave, it is late)
        self.assertEqual(self.bad('"it … be … go"'), ["it … be … go"])               # out of order

    def test_parts_around_an_ellipsis_must_be_in_one_paragraph_in_order_and_close(self):
        self.assertEqual(self.bad("“the soup … the bus”"), [])
        self.assertEqual(self.bad("“the bus … the soup”"), ["the bus … the soup"])                 # out of order
        self.assertEqual(self.bad("“I want … the bus can wait”"), ["I want … the bus can wait"])   # two documents
        self.assertEqual(self.bad("“the window … go on”"), ["the window … go on"])                 # two paragraphs
        far = ["I want " + "x " * 200 + "to quit"]
        self.assertEqual(self.bad("“I want … to quit”", docs=far), ["I want … to quit"])            # 400 apart
        self.assertEqual(self.bad("“I want … to quit”", docs=["I want, more than anything, to quit"]), [])

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

    def test_the_other_checks_reject_a_misquote_and_an_irregular_mark(self):
        text = "x" * 80 + '\nYou said "I am exhausted"'
        self.assertFalse(validate.check_reflection("insights", text, "nothing")[0])
        self.assertTrue(validate.check_reflection("reflection", text, "nothing", misquotes=False)[0])
        self.assertFalse(validate.check_catchup('You said "No" and then "I never loved any of it" last night.',
                                                "No, and then I left. I loved some of it")[0])
        ok, why = validate.check_catchup("You wrote ˮI adore napsˮ and slept. Glad you rested.", "I adore naps")
        self.assertFalse(ok)
        self.assertEqual(why, "irregular quote mark U+02EE at character 11 (line 1)")  # the mark and where, no words


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

    def test_a_reply_after_the_period_is_not_read_into_it(self):
        self.run_both("everything")
        data = ledger.snapshot(self.cfg.ledger_path)
        rec = reflect.find(data, "2026-10-15-reflection")
        path = self.cfg.root / "2026-10-15-reflection.md"
        self.assertTrue(reflect._replies_to(self.cfg, rec, path, date(2026, 10, 16), data))
        self.assertEqual(reflect._replies_to(self.cfg, rec, path, date(2026, 10, 15), data), [])  # (sent on the 16th)

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
        self.assertNotIn("is thrown away", text)  # the model is not told how a misquote is handled
        self.assertIn("Only his words go in quotation marks", text)
        self.assertIn('Put his words in straight\ndouble quotes "like this", on one line, and use no other quotation '
                      "marks anywhere", text)
        self.assertIn("## Before the day (your own words, except his replies, which are marked as his)\nBEFORE", text)

    def test_the_reflection_request_agrees_with_any_pronouns_and_with_no_chat(self):
        # (review, 2026-11-10: "They knows what they wrote; what they doesn't have yet", and on a no-chat install
        # "read it in the journal and in None" with "what he wrote back to it in the chat")
        they = make_cfg(Path(self.tmp.name) / "they", names=Names(user="Sam", companion="Ada"))
        self.assertIn("They know what they wrote; what they cannot get from their own entries is what you make of it.",
                      prompts.reflection_request(they, "reflection", "Wednesday", "IN", ""))
        self.assertIn("He knows what he wrote; what he cannot get from his own entries is what you make of it.",
                      prompts.reflection_request(self.cfg, "reflection", "Wednesday", "IN", ""))
        quiet = make_cfg(Path(self.tmp.name) / "quiet", platform="none")
        text = prompts.reflection_request(quiet, "reflection", "Wednesday", "IN", "")
        self.assertIn("He will read it in\nthe journal: it is your considered word", text)
        self.assertIn("copied exactly from his entries. Put his words", text)
        self.assertIn("## Before the day (your own words)\n(nothing yet: this is your first)", text)
        for absent in ("None", "in the chat", "his replies", "or replies", "wrote back"):
            self.assertNotIn(absent, text)

    def test_the_other_requests_state_the_rule_their_check_applies(self):
        # (review, 2026-11-10: their checks took the reflection's rule while their requests still said only "copy
        # the words exactly"; a catch-up that put her own earlier line in double quotes was not used, unwarned)
        from journal import programs
        prog = programs._program(self.cfg, "fix-life")
        session = prog.session(1)
        rec = {"messages": [{"at_local": "10:00", "text": "hi, back home now"}],
               "context": [["Iris", "09:00", "how was the drive?"], ["Theo", "09:01", "long"]]}
        asks = {"message": prompts.catchup_request(self.cfg, rec, "09:05 to 11:00"),
                "construction": prompts.reflect_phase_request(self.cfg, prog, session, session.phase(1), "an answer"),
                "paragraph": prompts.bridge_request(self.cfg, prog, session, "an answer"),
                "synthesis": prompts.insights_request(self.cfg, prog, "the document")}
        for what, text in asks.items():
            with self.subTest(what):
                self.assertIn("Double quotation marks are only for his exact words from ", text)
                self.assertIn("Put nothing else in double quotes: not words of your own (an earlier line of yours "
                              "included), not a title or a name, not a term", text)
                self.assertIn(f"one that is not around his exact words means the {what} is not used.", text)
                self.assertNotIn("thrown away", text)
        self.assertIn("from what Theo wrote below (his lines of the conversation and his messages after it)",
                      asks["message"])
        self.assertIn("from his answers in the document (not its titles, questions or summaries)", asks["synthesis"])
        # and the check does what the request says: her own line in double quotes is not his
        self.assertFalse(validate.check_catchup('Back now. "how was the drive?" was my last question.',
                                                "hi, back home now\nlong")[0])
        self.assertTrue(validate.check_catchup('Back now. A long drive, then; "back home now" is good to read.',
                                               "hi, back home now\nlong")[0])

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
            self.assertNotIn(search.UPTO_ENV, seen)
            # a step's own variables go with it (the reflection's search bound); the mark is the engine's to set
            more = {search.UPTO_ENV: "2026-10-14", agent.MODEL_STEP_ENV: "0"}
            self.assertEqual(real_ask(self.cfg, "q", 10, "none", env=more), "an answer")
        self.assertEqual((seen[agent.MODEL_STEP_ENV], seen[search.UPTO_ENV]), ("1", "2026-10-14"))

    def test_the_journals_hook_adds_nothing_to_a_model_step(self):
        from journal.hermes import hooks, runtime
        notebook.add(self.cfg, "a thread from chat")
        with mock.patch.dict(os.environ, {agent.MODEL_STEP_ENV: "1"}), \
                mock.patch.object(runtime, "load_config", return_value=self.cfg), \
                mock.patch("journal.turncontext.build", side_effect=AssertionError("built a turn context")):
            self.assertIsNone(hooks.on_llm_call(user_message="Write the reflection", platform="cli",
                                                session_id="s1", turn_id="t1"))
        with mock.patch.dict(os.environ, {}, clear=False), mock.patch.object(runtime, "load_config",
                                                                              return_value=self.cfg):
            os.environ.pop(agent.MODEL_STEP_ENV, None)
            out = hooks.on_llm_call(user_message="hello", platform="cli", session_id="s1", turn_id="t1")
        self.assertIn("a thread from chat", out["context"])  # her own turns keep their notebook, as before

    def test_the_switch_never_costs_a_turn_of_his_its_off_the_record_mark(self):
        # (review, 2026-11-10: the switch returned before ``inbound.begin_turn``, so a gateway that carried the
        # variable by mistake lost the mark and the voice capture of every turn; now only the injection is skipped)
        from journal.hermes import hooks, runtime
        notebook.add(self.cfg, "a thread from chat")
        private = capture.off_record_note(self.cfg) + "something he wants kept out"

        def turn(turn_id):
            with mock.patch.object(runtime, "load_config", return_value=self.cfg), \
                    mock.patch.object(runtime, "session_value", side_effect={"CHAT_ID": CHAT}.get):
                return hooks.on_llm_call(user_message=private, platform="telegram", session_id="s1", turn_id=turn_id)
        with mock.patch.dict(os.environ, {agent.MODEL_STEP_ENV: "1"}):
            self.assertIsNone(turn("t1"))  # nothing injected
            self.assertEqual(hookstate.off_turn(self.cfg, "s1")["turn"], "t1")  # and the turn is marked all the same
            with mock.patch.object(tools.privacy, "view", side_effect=AssertionError("the mark is enough")):
                refused = json.loads(tools.call(self.cfg, "journal_notebook", {"action": "add", "text": "from /x"},
                                                tools.ToolTurn("s1", "t1")))
        self.assertTrue(refused["off_record"])
        self.assertEqual([i["text"] for i in notebook.open_items(self.cfg)], ["a thread from chat"])


if __name__ == "__main__":
    unittest.main()
