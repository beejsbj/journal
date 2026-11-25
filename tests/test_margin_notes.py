"""MARGIN-NOTES (issue #31, DECISIONS.md): the annotation step writes margin notes.

* The one request that asks for her analysis may ask for notes too, each ``NOTE <key>: …`` on one block of his; code
  keeps at most one a block and three an entry, only on blocks that exist, have their key and are not very short,
  and never on a block that already has an open or dismissed note.
* Every note is checked as her other words are (no quote of his that is not his, not the agent's failure copy, one
  short line) and written through ``margin.render``; anything else is dropped, and a NOTE line never stays in her
  analysis. Silence is a fine answer; the floor is no note, never a broken file.
* Timing, attempts and the outage deferral are the annotation's own: nothing is written while the model is
  unreachable, and nothing is asked twice.
* Her notes so far are shown to her apart from the entry, with their states; his replies in them are labelled his.
* Old entries (no block keys) are annotated exactly as before; notes go to the file only, also with no chat.
"""
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import add_prompt, make_cfg, unkeyed

from journal import agent, capture, document, entries, ledger, margin, margin_notes, prompts, tick

UNREACHABLE = "exit 1: the model endpoint is not running"
LONG = [
    "I walked to the harbour before work and the boats were still tied up in the fog.",
    "My sister called about the house again and I said I would think about it this week.",
    "At lunch I finally finished the chapter on tides that I have been carrying around for a month.",
    "The evening was quiet, I cooked the lentils my mother used to make and ate them slowly.",
    "Before bed I wrote down three things I want to stop doing, and the list felt lighter than expected.",
]
SHORT = "Tired, fine."
DRAFT = "- He walked to the harbour; the fog stayed with him."
PID = "2026-10-14-morning"


class Base(unittest.TestCase):
    blocks = LONG[:2]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.cfg.annotate_after_minutes = 0
        self.sent = datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)
        add_prompt(self.cfg, sent=self.sent)
        for i, text in enumerate(self.blocks):
            self.info = capture.capture(self.cfg, self.cfg.chat_id, "100", text,
                                        self.sent + timedelta(minutes=5 + i), "text", f"42{i}")
        self.path = Path(self.info["path"])
        self.t0 = self.sent + timedelta(hours=1)
        self.asks: list[str] = []
        self.down = False
        self.answer = DRAFT
        self.patches = [mock.patch.object(agent, "ask", self.ask),
                        mock.patch("journal.context.companion_reply_after", lambda *a, **k: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def ask(self, cfg, question, *a, **k):
        self.asks.append(question)
        if self.down:
            raise agent.AgentUnavailable(UNREACHABLE)
        return self.answer(question) if callable(self.answer) else self.answer

    def body(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def keys(self) -> list[str]:
        return entries.BLOCK_KEY.findall(self.body())

    def step(self, delta=timedelta(0)) -> list[str]:
        log: list[str] = []
        tick.annotate(self.cfg, self.t0 + delta, log)
        return log

    def prompt(self) -> dict:
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), PID)

    def notes(self) -> list[margin.Note]:
        return margin.notes(self.body())


class WritesNotesTest(Base):
    def test_a_note_on_one_block_is_written_hers_and_anchored(self):
        k = self.keys()
        before = entries.his_words(self.body())
        self.answer = f"{DRAFT}\nNOTE {k[0]}: Which part of the fog stayed with you through the day?"
        log = self.step()
        body = self.body()
        (note,) = self.notes()
        self.assertEqual((note.anchor, note.state, note.text),
                         (f"block:{k[0]}", "open", "Which part of the fog stayed with you through the day?"))
        self.assertEqual(entries.annotations(body), DRAFT)               # the NOTE line left her analysis
        self.assertEqual(entries.his_words(body), before)                # his words as they were
        self.assertNotIn("fog stayed with you", entries.his_words(body))  # her note is never his
        self.assertIn("### Margin notes\n\n%% journal:margin %%\n\n%% journal note:", body)
        self.assertRegex(body, r"> \[!note\] Iris · 07:00 · on 06:05\n> Which part of the fog")
        margin_ = document.margin_of(body, self.path.stem, {b["anchor"] for b in document.blocks_of(body)})
        self.assertEqual(margin_[0]["anchor"], f"block:{k[0]}")
        self.assertTrue(any("annotated 2026-10-14-morning (+1 margin note)" in l for l in log), log)
        self.assertEqual(self.prompt()["annotate_attempts"], 1)
        self.assertTrue(self.prompt().get("annotated_at"))

    def test_the_request_labels_the_blocks_and_asks_for_notes(self):
        self.step()
        (q,) = self.asks
        for k in self.keys():
            self.assertIn(f"[block {k}]", q)
        self.assertIn("NOTE <key>: <your note>", q)
        self.assertNotIn("%% journal", q)
        self.assertIn("at most one note a block and\n  3 in all", q)

    def test_silence_is_a_fine_answer(self):
        log = self.step()
        self.assertEqual(self.notes(), [])
        self.assertNotIn("Margin notes", self.body())
        self.assertIn("annotated 2026-10-14-morning", log)

    def test_with_no_chat_the_notes_are_still_written(self):
        self.cfg.channel = {"platform": "none"}
        k = self.keys()
        self.answer = f"{DRAFT}\nNOTE {k[1]}: What would thinking about the house actually look like?"
        self.step()
        self.assertEqual([n.anchor for n in self.notes()], [f"block:{k[1]}"])

    def test_a_list_marker_and_the_label_form_are_read(self):
        k = self.keys()
        self.answer = (f"{DRAFT}\n- NOTE [block {k[0]}]: What did the harbour sound like then?\n"
                       f"* NOTE {k[1]}: Is this week the week for the house question?")
        self.step()
        self.assertEqual([n.anchor for n in self.notes()], [f"block:{k[0]}", f"block:{k[1]}"])

    def test_notes_never_go_to_the_chat_reply(self):
        k = self.keys()
        self.answer = f"{DRAFT}\nNOTE {k[0]}: What did the harbour sound like then?"
        self.step()
        # the annotation reply (``chat.replies``) reads her analysis alone
        self.assertNotIn("harbour sound", entries.annotations(self.body()))


class CapsTest(Base):
    blocks = LONG + [SHORT]

    def test_at_most_three_an_entry_and_one_a_block(self):
        k = self.keys()
        lines = [f"NOTE {k[0]}: First thought on the harbour and the fog.",
                 f"NOTE {k[0]}: A second note on the same harbour block.",
                 f"NOTE {k[1]}: What did you tell your sister in the end?",
                 f"NOTE {k[2]}: The chapter on tides: what stayed?",
                 f"NOTE {k[3]}: Lentils as a way back to your mother.",
                 f"NOTE {k[4]}: Which of the three is first?"]
        self.answer = DRAFT + "\n" + "\n".join(lines)
        log = self.step()
        self.assertEqual([n.anchor for n in self.notes()], [f"block:{x}" for x in k[:3]])
        self.assertEqual(self.notes()[0].text, "First thought on the harbour and the fog.")
        line = next(l for l in log if l.startswith("annotated"))
        self.assertIn("+3 margin notes", line)
        self.assertIn("1 (a second note on one block)", line)
        self.assertIn("2 (over 3 on the entry)", line)

    def test_a_very_short_block_takes_none_and_is_not_offered(self):
        short = self.keys()[5]
        self.answer = f"{DRAFT}\nNOTE {short}: Tired how?"
        log = self.step()
        self.assertEqual(self.notes(), [])
        self.assertNotIn(f"[block {short}]", self.asks[0])
        self.assertTrue(any("(a block that takes no note)" in l for l in log), log)


class AnchorsTest(Base):
    def test_a_key_that_is_not_in_the_entry_is_dropped(self):
        self.answer = f"{DRAFT}\nNOTE b00000000: A note on nothing at all here."
        log = self.step()
        self.assertEqual(self.notes(), [])
        self.assertEqual(entries.annotations(self.body()), DRAFT)
        self.assertTrue(any("(no such block)" in l for l in log), log)

    def test_a_block_gone_by_the_time_she_answered_takes_none(self):
        k = self.keys()

        def answer(q):   # he deletes the first block while the model is answering
            from journal.serve import blocks as web
            self.path.write_text(web.remove(self.body(), "words:0", stem=self.path.stem), encoding="utf-8")
            return f"{DRAFT}\nNOTE {k[0]}: What did the harbour sound like then?"
        self.answer = answer
        self.step()
        self.assertEqual(self.notes(), [])
        self.assertEqual(entries.annotations(self.body()), DRAFT)


class MalformedTest(Base):
    def test_a_line_that_is_not_a_note_is_dropped_and_leaves_her_analysis(self):
        k = self.keys()
        self.answer = (f"{DRAFT}\nNOTE: no key at all here\nNOTE {k[0]} no colon after the key\n"
                       f"NOTE {k[1]}:\nNOTE{k[0]}: glued to the word")
        log = self.step()
        self.assertEqual(self.notes(), [])
        self.assertEqual(entries.annotations(self.body()), DRAFT)
        self.assertNotRegex(entries.annotations(self.body()), r"(?m)^NOTE[ :]")
        self.assertTrue(any("(not the shape of a note)" in l for l in log), log)

    def test_a_bullet_that_begins_with_note_in_words_stays(self):
        self.answer = f"{DRAFT}\n- Note how the fog returns twice."
        self.step()
        self.assertIn("- Note how the fog returns twice.", entries.annotations(self.body()))

    def test_only_notes_is_no_analysis_and_uses_an_attempt(self):
        k = self.keys()
        before = self.body()
        self.answer = f"NOTE {k[0]}: What did the harbour sound like then?"
        log = self.step()
        self.assertEqual(self.body(), before)          # nothing written
        self.assertIn(f"{PID}: annotation attempt failed", log)
        self.assertEqual(self.prompt()["annotate_attempts"], 1)


class ValidationTest(Base):
    def test_a_note_that_misquotes_him_is_dropped(self):
        k = self.keys()
        self.answer = (f'{DRAFT}\nNOTE {k[0]}: You wrote "the boats were drifting in the sun" - why then?\n'
                       f'NOTE {k[1]}: You said "I would think about it this week". Will you?')
        log = self.step()
        self.assertEqual([n.anchor for n in self.notes()], [f"block:{k[1]}"])   # his words exactly: kept
        self.assertTrue(any("1 (a quote not in his words)" in l for l in log), log)

    def test_too_long_too_short_failure_copy_and_engine_lines_are_dropped(self):
        k = self.keys()
        failure = ("⚠️ The AI model service isn't reachable right now — the configured model endpoint is not running "
                   "or is unreachable.")
        for words in ("x" * (margin_notes.NOTE_LIMIT + 1), "Why?", failure, "%% journal his:1 %% sneaky",
                      "# A heading", "{\"json\": true}"):
            with self.subTest(words=words[:20]):
                notes, dropped = margin_notes.choose(self.cfg, self.body(), [(k[0], words)], self.t0)
                self.assertEqual(notes, [])
                self.assertEqual(sum(dropped.values()), 1)

    def test_what_is_written_reads_back_as_hers(self):
        k = self.keys()
        notes, dropped = margin_notes.choose(self.cfg, self.body(), [(k[0], "Which boats, and whose?")],
                                             self.t0.astimezone(self.cfg.tz))
        (md,) = notes
        self.assertEqual(margin.parse(md)[0].text, "Which boats, and whose?")
        self.assertTrue(entries._CALLOUT.match(md))


class OutageTest(Base):
    def test_an_unreachable_model_defers_with_no_attempt_and_no_note(self):
        k = self.keys()
        before = self.body()
        self.down = True
        log = self.step()
        self.assertTrue(any("annotation deferred, model unreachable" in l for l in log), log)
        self.assertEqual(self.body(), before)
        self.assertEqual(self.prompt().get("annotate_attempts", 0), 0)
        self.down = False
        self.answer = f"{DRAFT}\nNOTE {k[0]}: What did the harbour sound like then?"
        self.step(timedelta(minutes=31))
        self.assertEqual(len(self.notes()), 1)
        self.assertEqual(self.prompt()["annotate_attempts"], 1)
        self.assertNotIn("deferred_since", self.prompt())


class IdempotenceTest(Base):
    def test_a_later_tick_asks_nothing_and_writes_nothing(self):
        k = self.keys()
        self.answer = f"{DRAFT}\nNOTE {k[0]}: What did the harbour sound like then?"
        self.step()
        once = self.body()
        self.step(timedelta(minutes=5))
        self.step(timedelta(hours=2))
        self.assertEqual(len(self.asks), 1)
        self.assertEqual(self.body(), once)

    def asked_again(self, state: str) -> str:
        """Write a note on the first block, set it to ``state``, empty her analysis by hand (so the entry is
        annotated again) and have her propose a note on the same block: the body after."""
        k = self.keys()
        self.answer = f"{DRAFT}\nNOTE {k[0]}: Which part of the fog stayed with you?"
        self.step()
        body = self.body()
        if state != "open":
            old = margin.key_line(self.notes()[0])
            body = body.replace(old, old.replace("state:open", f"state:{state} closed:2026-10-14T07:00:00+05:30"))
        body = body.replace(DRAFT, entries.PENDING)
        self.path.write_text(body, encoding="utf-8")
        with ledger.locked(self.cfg.ledger_path) as data:
            q = ledger.find(data, PID)
            q.pop("annotated_at")
            q["annotate_attempts"] = 0
        self.answer = f"{DRAFT}\nNOTE {k[0]}: The fog again: which part stayed with you?"
        self.step(timedelta(hours=1))
        return self.asks[-1]

    def test_a_dismissed_point_is_not_raised_again(self):
        q = self.asked_again("dismissed")
        k = self.keys()
        self.assertEqual([n.state for n in self.notes()], ["dismissed"])       # no second note on that block
        self.assertIn(f"- Your note on [block {k[0]}] (dismissed): Which part of the fog stayed with you?", q)
        self.assertNotIn(f"[block {k[0]}]\n", q.split("## The entry")[1].split("## Your margin notes")[0])
        self.assertIn("least of all one he\n  dismissed", q)

    def test_an_open_note_is_not_doubled(self):
        self.asked_again("open")
        self.assertEqual(len(self.notes()), 1)

    def test_a_resolved_note_leaves_its_block_open_to_a_new_one(self):
        self.asked_again("resolved")
        self.assertEqual([n.state for n in self.notes()], ["resolved", "open"])


class HisRepliesLabelledTest(Base):
    def test_his_reply_in_a_thread_is_labelled_his_and_hers_hers(self):
        k = self.keys()
        self.answer = f"{DRAFT}\nNOTE {k[0]}: Which part of the fog stayed with you?"
        self.step()
        note = self.notes()[0]
        body = self.body().rstrip("\n") + (
            "\n\n%% journal his:web-1 %%\n**06:40** — The foghorn, mostly. PLOVERHIS\n\n"
            f"%% journal her:{note.id}-2 %%\n> [!note]- Ada · 06:41\n> Foghorns carry. WRENHERS\n")
        body = body.replace(DRAFT, entries.PENDING)
        self.path.write_text(body, encoding="utf-8")
        with ledger.locked(self.cfg.ledger_path) as data:
            q = ledger.find(data, PID)
            q.pop("annotated_at")
            q["annotate_attempts"] = 0
        self.answer = DRAFT
        self.step(timedelta(hours=1))
        q = self.asks[-1]
        entry_part, margin_part = q.split("## The entry")[1].split("## Your margin notes")
        self.assertNotIn("PLOVERHIS", entry_part)       # the margin is shown apart from the entry, labelled
        self.assertNotIn("WRENHERS", entry_part)
        self.assertIn("  - His reply (his own words): **06:40** — The foghorn, mostly. PLOVERHIS", margin_part)
        self.assertIn("  - Your reply: Foghorns carry. WRENHERS", margin_part)
        self.assertIn("only lines marked as his own words are his", margin_part)


class OffRecordTest(Base):
    def test_an_off_the_record_message_never_reaches_the_request(self):
        info = capture.capture(self.cfg, self.cfg.chat_id, "100", "/x MARLINSECRET is between us",
                               self.sent + timedelta(minutes=8), "text", "4299")
        self.assertIsNone(info)
        self.assertNotIn("MARLINSECRET", self.body())
        self.step()
        self.assertNotIn("MARLINSECRET", self.asks[0])


class OldEntryTest(Base):
    def test_an_entry_without_block_keys_is_annotated_as_before(self):
        old = unkeyed(self.body())
        self.path.write_text(old, encoding="utf-8")
        k = self.keys()
        self.assertEqual(k, [])
        self.answer = f"{DRAFT}\nNOTE b12345678: A note on a block with no key."
        log = self.step()
        self.assertTrue(any("dropped 1 (not asked for)" in l for l in log), log)
        self.assertNotIn("NOTE <key>", self.asks[0])                   # no notes asked of an old entry
        expected = Path(self.tmp.name) / "expected.md"
        expected.write_text(old, encoding="utf-8")
        entries.write_annotations(expected, DRAFT, self.cfg.names)
        self.assertEqual(self.body(), expected.read_text(encoding="utf-8"))   # byte for byte, as before

    def test_the_request_without_notes_is_todays(self):
        q = prompts.annotation_request(self.cfg, "ENTRY", None)
        self.assertNotIn("NOTE", q)
        self.assertNotIn("margin", q.lower())
        self.assertTrue(q.endswith("## The entry\nENTRY\n"))


class WriterTest(Base):
    def test_notes_append_to_an_existing_margin_and_keep_the_notes_section_after(self):
        k = self.keys()
        entries.add_companion_note(self.path, "A note of hers, KESTRELNOTE.", self.t0, None, self.cfg.names)
        body = self.body()
        (a,), _ = margin_notes.choose(self.cfg, body, [(k[0], "Which part of the fog stayed?")], self.t0)
        body = entries.with_annotations(body, DRAFT, self.cfg.names, [a])
        (b,), _ = margin_notes.choose(self.cfg, body, [(k[1], "What would you tell your sister?")],
                                      self.t0 + timedelta(minutes=1))
        body = entries.with_annotations(body, DRAFT, self.cfg.names, [b])
        self.assertEqual([n.anchor for n in margin.notes(body)], [f"block:{k[0]}", f"block:{k[1]}"])
        self.assertEqual(body.count("### Margin notes"), 1)
        self.assertLess(body.index("### Margin notes"), body.index("KESTRELNOTE"))
        self.assertEqual(entries.annotations(body), DRAFT)
        self.assertNotIn("\n\n\n", body)

    def test_a_result_that_would_not_read_back_writes_her_analysis_alone(self):
        k = self.keys()
        with mock.patch.object(margin_notes, "_sound", lambda *a: False):
            result = margin_notes.write(self.cfg, self.path, DRAFT, [(k[0], "Which part of the fog stayed?")],
                                        self.t0.astimezone(self.cfg.tz))
        self.assertEqual(self.notes(), [])
        self.assertEqual(entries.annotations(self.body()), DRAFT)
        self.assertEqual(result["the entry would not read back"], 1)

    def test_an_error_in_the_notes_writes_her_analysis_alone(self):
        k = self.keys()
        self.answer = f"{DRAFT}\nNOTE {k[0]}: Which part of the fog stayed?"
        with mock.patch.object(margin_notes, "eligible", side_effect=RuntimeError("odd file")):
            log = self.step()
        self.assertIn(f"{PID}: margin notes not offered (RuntimeError)", log)
        self.assertNotIn("NOTE <key>", self.asks[0])
        self.assertEqual((entries.annotations(self.body()), self.notes()), (DRAFT, []))

    def test_an_error_while_choosing_writes_her_analysis_alone(self):
        k = self.keys()
        with mock.patch.object(margin_notes, "choose", side_effect=RuntimeError("odd file")):
            result = margin_notes.write(self.cfg, self.path, DRAFT, [(k[0], "Which part of the fog stayed?")],
                                        self.t0.astimezone(self.cfg.tz))
        self.assertEqual((entries.annotations(self.body()), self.notes()), (DRAFT, []))
        self.assertEqual(result["an error (RuntimeError)"], 1)


if __name__ == "__main__":
    unittest.main()
