"""SENT-AS-SENT: what was sent is what is kept.

* A finished prompt's Telegram message keeps its header and the opener it went out with; only the question set (and
  the hint) gives way to a short status line. The opener is the send record's own; a fold reads nothing from the chat,
  so nothing said off the record (``/x``) can reach it.
* A send record keeps the question set (id, text, section) it carried, and the entry page reads that first: a
  practice edited after its prompt went out does not change the questions shown under the old opener.
"""
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from test_practices_engine import GRATITUDE, Base, at

from journal import compose, ledger, library, privacy, sweep, telegram, tick
from journal.serve import document

LIVE = datetime(2026, 10, 14, 21, 31)


class FoldTest(Base):
    def fold_after(self, pid, hours=7):
        sent = datetime.fromisoformat(self.prompt(pid)["sent_at"])
        tick.fold(self.cfg, sent + timedelta(hours=hours), [])
        return self.bot.texts("editMessageText")

    def test_a_missed_prompt_keeps_header_and_opener_and_loses_the_questions(self):
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        sent = self.bot.texts()[0]
        self.assertIn("<blockquote expandable>", sent)
        self.assertEqual(self.fold_after("2026-10-14-gratitude"), [
            "<b>🌿 Daily gratitude · Wed 14 Oct</b>\n\nDaily gratitude. A sentence or two is enough.\n\n"
            "<i>missed — reply anytime</i>"])

    def test_an_answered_prompt_folds_to_answered(self):
        from journal import capture
        from helpers import CHAT
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        p = self.prompt("2026-10-14-gratitude")
        capture.capture(self.cfg, CHAT, p["message_id"], "grateful for tea", at(21, 40).astimezone(None), "text", "9")
        texts = self.fold_after("2026-10-14-gratitude")
        self.assertEqual(len(texts), 1)
        self.assertTrue(texts[0].endswith("\n\n<i>answered ✓</i>"), texts[0])
        self.assertIn("Daily gratitude. A sentence or two is enough.", texts[0])

    def test_the_opener_is_the_one_sent_also_when_it_fell_back_to_the_plain_one(self):
        # the model's opener is rejected by Telegram: the plain one is sent (``plain:send-retry``) and is what folds
        self.write("gratitude.md", GRATITUDE)
        real = self.bot.__call__
        calls = []

        def picky(cfg, method, payload):
            calls.append(method)
            if method == "sendMessage" and len(calls) == 1:
                raise telegram.TelegramError("Bad Request: message is too long")
            return real(cfg, method, payload)
        model = "A warm model opener that Telegram refused."
        with mock.patch.object(tick, "_opener", lambda *a, **k: (model, "ada", [], [])), \
                mock.patch.object(telegram, "_call", picky):
            self.send(at(21, 31))
        p = self.prompt("2026-10-14-gratitude")
        self.assertEqual((p["opener_source"], p["opener"]), ("plain:send-retry", "Daily gratitude. A sentence or two is enough."))
        with mock.patch.object(telegram, "_call", picky):
            texts = self.fold_after("2026-10-14-gratitude")
        self.assertIn("A sentence or two is enough.", texts[0])
        self.assertNotIn(model, texts[0])

    def test_markup_and_entities_in_the_opener_are_escaped_as_at_send_time(self):
        self.write("gratitude.md", GRATITUDE)
        opener = "Tea & <toast>? **Bold** and _soft_ — a line."
        with mock.patch.object(tick, "_opener", lambda *a, **k: (opener, "ada", [], [])):
            self.send(at(21, 31))
        sent = self.bot.texts()[0]
        folded = self.fold_after("2026-10-14-gratitude")[0]
        self.assertIn("Tea &amp; &lt;toast&gt;? <b>Bold</b> and <i>soft</i> — a line.", folded)
        self.assertIn(folded.split("\n\n")[1], sent)  # the same opener text, byte for byte

    def test_the_fold_stays_within_telegram_s_limit(self):
        long = "x" * 5000
        head = compose.telegram_html("evening", LIVE, long, "answered ✓")
        self.assertLessEqual(len(head), compose.TELEGRAM_LIMIT)
        self.assertNotIn("xxxx", head)  # an opener that cannot fit is dropped, as folds used to be
        fits = compose.telegram_html("evening", LIVE, "x" * 1100, "answered ✓")
        self.assertIn("x" * 1100, fits)
        self.assertLessEqual(len(fits), compose.TELEGRAM_LIMIT)

    def test_a_record_without_an_opener_folds_to_header_and_status(self):
        self.assertEqual(compose.telegram_html("evening", LIVE, "", "answered ✓"),
                         "<b>🌿 Evening journal · Wed 14 Oct</b>\n\n<i>answered ✓</i>".replace("🌿", compose.DEFAULT_NAMES.emoji))

    def test_a_fold_reads_nothing_from_the_chat(self):
        # /x: the chat is private, a bare /x window may be open. The fold's text comes from the send record and the
        # library; it never asks the privacy pass, the transcript or the pack, so there is nothing for it to expose.
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        boom = AssertionError("a fold read the chat")
        with mock.patch.object(privacy, "view", side_effect=boom), mock.patch.object(privacy, "compute", side_effect=boom), \
                mock.patch.object(type(self.cfg), "transcript", side_effect=boom, create=True), \
                mock.patch("journal.context.build", side_effect=boom):
            texts = self.fold_after("2026-10-14-gratitude")
        record = self.prompt("2026-10-14-gratitude")
        self.assertEqual(len(texts), 1)
        self.assertEqual(texts[0].split("\n\n")[1], record["opener"])
        self.assertEqual(len(texts[0].split("\n\n")), 3)  # header, the record's opener, the status: nothing else

    def test_an_opener_that_echoes_the_opt_out_is_not_put_back_by_a_fold(self):
        # the same narrow check the web app makes (``capture.stored_opener``): such an opener is withheld whole
        self.write("gratitude.md", GRATITUDE)
        for opener in ("Fine.\n/x a secret she repeated", "Earlier [Off the record — kept private; it stays out of the journal.] ok"):
            with self.subTest(opener=opener):
                self.bot.calls.clear()
                with mock.patch.object(tick, "_opener", lambda *a, **k: (opener, "ada", [], [])):
                    self.send(at(21, 31), only="gratitude")
                texts = self.fold_after("2026-10-14-gratitude")
                self.assertEqual(texts[-1], "<b>🌿 Daily gratitude · Wed 14 Oct</b>\n\n<i>missed — reply anytime</i>")
                with ledger.locked(self.cfg.ledger_path) as data:
                    data["prompts"] = []

    def test_a_re_sent_prompt_s_old_message_keeps_its_opener_too(self):
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        first = self.prompt("2026-10-14-gratitude")["message_id"]
        self.send(at(21, 40), only="gratitude")
        edits = [p for m, p in self.bot.calls if m == "editMessageText"]
        self.assertEqual([str(e["message_id"]) for e in edits], [first])
        self.assertEqual(edits[0]["text"], "<b>🌿 Daily gratitude · Wed 14 Oct</b>\n\nDaily gratitude. A sentence or two "
                                           "is enough.\n\n<i>sent again below</i>")

    def test_a_re_send_with_another_opener_still_matches_a_quote_of_the_old_message(self):
        self.write("gratitude.md", GRATITUDE)
        with mock.patch.object(tick, "_opener", lambda *a, **k: ("The first opener, about tea.", "ada", [], [])):
            self.send(at(21, 31))
        with mock.patch.object(tick, "_opener", lambda *a, **k: ("A second opener, about rain.", "ada", [], [])):
            self.send(at(21, 40), only="gratitude")
        old = self.bot.texts("editMessageText")[0]
        self.assertIn("The first opener, about tea.", old)
        p = self.prompt("2026-10-14-gratitude")
        self.assertEqual(p["earlier_openers"], ["The first opener, about tea."])
        self.assertIn(sweep.words(old), sweep.prompt_texts(self.cfg, p))

    def test_a_damaged_record_never_breaks_the_quote_matcher(self):
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        p = dict(self.prompt("2026-10-14-gratitude"))
        for opener, earlier in ((["x"], None), ("ok", [["y"], {"z": 1}, None, 3]), (None, "text")):
            with self.subTest(opener=opener, earlier=earlier):
                texts = sweep.prompt_texts(self.cfg, {**p, "opener": opener, "earlier_openers": earlier})
                self.assertTrue(texts)

    def test_a_re_send_edit_applies_the_same_opener_check_as_a_fold(self):
        self.write("gratitude.md", GRATITUDE)
        with mock.patch.object(tick, "_opener", lambda *a, **k: ("Fine.\n/x a secret", "ada", [], [])):
            self.send(at(21, 31))
        self.send(at(21, 40), only="gratitude")
        self.assertEqual(self.bot.texts("editMessageText"),
                         ["<b>🌿 Daily gratitude · Wed 14 Oct</b>\n\n<i>sent again below</i>"])

    CROSSED = "Today **felt _heavy** at work_ maybe."  # md_to_html: crossed tags, which Telegram refuses

    def picky(self, also_plain=False):
        """Telegram refusing crossed tags in HTML (and, with ``also_plain``, any text holding the opener)."""
        real = self.bot.__call__

        def call(cfg, method, payload):
            text = payload["text"]
            crossed = payload.get("parse_mode") == "HTML" and "<b>felt <i>heavy</b>" in text
            if crossed or (also_plain and "heavy" in text):
                raise telegram.TelegramError(f"{method}: Bad Request: can't parse entities: unexpected end tag")
            return real(cfg, method, payload)
        return call

    def test_an_opener_with_crossed_tags_still_folds_as_plain_text(self):
        self.write("gratitude.md", GRATITUDE)
        with mock.patch.object(tick, "_opener", lambda *a, **k: (self.CROSSED, "ada", [], [])), \
                mock.patch.object(telegram, "_call", self.picky()):
            self.send(at(21, 31))
            log = []
            sent = datetime.fromisoformat(self.prompt("2026-10-14-gratitude")["sent_at"])
            tick.fold(self.cfg, sent + timedelta(hours=7), log)
        p = self.prompt("2026-10-14-gratitude")
        self.assertTrue(p.get("collapsed_at"))
        self.assertNotIn("fold_attempts", p)
        edit = [c for m, c in self.bot.calls if m == "editMessageText"][-1]
        self.assertNotIn("parse_mode", edit)  # the plain-text retry, as the send made
        self.assertIn("Today felt heavy at work maybe.", edit["text"])
        self.assertNotIn("<blockquote", edit["text"])

    def test_a_fold_that_cannot_be_parsed_even_as_plain_text_drops_the_opener(self):
        self.write("gratitude.md", GRATITUDE)
        with mock.patch.object(tick, "_opener", lambda *a, **k: (self.CROSSED, "ada", [], [])), \
                mock.patch.object(telegram, "_call", self.picky()):
            self.send(at(21, 31))
        log = []
        sent = datetime.fromisoformat(self.prompt("2026-10-14-gratitude")["sent_at"])
        with mock.patch.object(telegram, "_call", self.picky(also_plain=True)):
            tick.fold(self.cfg, sent + timedelta(hours=7), log)
        p = self.prompt("2026-10-14-gratitude")
        self.assertTrue(p.get("collapsed_at"))
        self.assertNotIn("fold_attempts", p)
        edit = [c for m, c in self.bot.calls if m == "editMessageText"][-1]
        self.assertEqual(edit["text"], "<b>🌿 Daily gratitude · Wed 14 Oct</b>\n\n<i>missed — reply anytime</i>")
        self.assertTrue(any("folding without it" in l for l in log))

    def test_the_re_send_edit_also_falls_back(self):
        self.write("gratitude.md", GRATITUDE)
        with mock.patch.object(tick, "_opener", lambda *a, **k: (self.CROSSED, "ada", [], [])), \
                mock.patch.object(telegram, "_call", self.picky()):
            self.send(at(21, 31))
        first = self.prompt("2026-10-14-gratitude")["message_id"]
        with mock.patch.object(telegram, "_call", self.picky(also_plain=True)):
            self.send(at(21, 40), only="gratitude")
        edits = [c for m, c in self.bot.calls if m == "editMessageText" and str(c["message_id"]) == str(first)]
        self.assertEqual(edits[-1]["text"], "<b>🌿 Daily gratitude · Wed 14 Oct</b>\n\n<i>sent again below</i>")

    def test_edit_html_retries_unparsable_markup_as_plain_text_and_only_that(self):
        calls = []

        def call(cfg, method, payload):
            calls.append(dict(payload))
            if payload.get("parse_mode") == "HTML":
                raise telegram.TelegramError("editMessageText: Bad Request: can't parse entities")
            return {}
        with mock.patch.object(telegram, "_call", call):
            telegram.edit_html(self.cfg, "1", "2", "<b>a &amp; <i>b</b></i>")
        self.assertEqual([c.get("parse_mode") for c in calls], ["HTML", None])
        self.assertEqual(calls[1]["text"], "a & b")
        with mock.patch.object(telegram, "_call", side_effect=telegram.TelegramError("editMessageText: Bad Request: x")):
            with self.assertRaises(telegram.TelegramError):
                telegram.edit_html(self.cfg, "1", "2", "x")

    def test_a_phase_message_still_folds_to_its_header_line(self):
        rec = {"id": "r-s01-p1", "kind": "prog", "title": "Prog", "session": 1, "session_count": 2, "phase": 1,
               "phase_count": 3, "opener": "", "emoji": "🧠"}
        self.assertEqual(compose.folded_phase_html(rec, "answered ✓", self.cfg.names),
                         "<b>🧠 Prog · Session 1/2 · Phase 1/3 · answered ✓</b>")

    def test_the_busy_path_still_matches_a_quote_of_any_state_of_the_message(self):
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        p = self.prompt("2026-10-14-gratitude")
        folded = self.fold_after("2026-10-14-gratitude")[0]
        texts = sweep.prompt_texts(self.cfg, self.prompt("2026-10-14-gratitude"))
        self.assertIn(sweep.words(folded), texts)  # the folded message as it stands
        self.assertIn(sweep.words("🌿 Daily gratitude · Wed 14 Oct · missed — reply anytime"), texts)  # as it used to
        self.assertEqual(texts[0], sweep.words("🌿 Daily gratitude · Wed 14 Oct"))
        self.assertTrue(p)


class SnapshotTest(Base):
    def entry_document(self):
        from helpers import CHAT
        from journal import capture
        p = self.prompt("2026-10-14-gratitude")
        info = capture.capture(self.cfg, CHAT, p["message_id"], "grateful for tea", at(21, 40), "text", "9")
        return document.document(self.cfg, Path(info["path"]), library.load(self.cfg))

    def test_the_send_record_keeps_the_question_set(self):
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        self.assertEqual(self.prompt("2026-10-14-gratitude")["questions"], [
            {"id": "1", "text": "Three things today, and why each one mattered.", "section": None},
            {"id": "2", "text": "Who made one of them possible?", "section": None}])

    def test_sections_are_kept_with_their_titles(self):
        self.send(at(23, 1), only="evening")
        qs = self.prompt("2026-10-14-evening")["questions"]
        self.assertEqual(qs[0], {"id": "1.1", "text": "What's spinning in your mind?", "section": "Brain Dump & Letting Go"})
        self.assertEqual(len(qs), len(library.builtins()["evening"].questions))

    def test_a_practice_edited_after_the_send_keeps_its_old_questions_on_the_entry_page(self):
        path = self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        path.write_text(GRATITUDE.replace("Who made one of them possible?", "What did you let go of?")
                        + "- A third, new one.\n", encoding="utf-8")
        doc = self.entry_document()
        self.assertEqual([q["text"] for q in doc["prompt"]["questions"]],
                         ["Three things today, and why each one mattered.", "Who made one of them possible?"])
        self.assertEqual(doc["prompt"]["opener"], "Daily gratitude. A sentence or two is enough.")

    def test_a_deleted_practice_still_shows_what_was_asked(self):
        path = self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        path.unlink()
        doc = self.entry_document()
        self.assertEqual([q["id"] for q in doc["prompt"]["questions"]], ["1", "2"])
        self.assertEqual(doc["prompt"]["phases"], [])

    def test_an_older_record_falls_back_to_the_library(self):
        self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        with ledger.locked(self.cfg.ledger_path) as data:
            del ledger.find(data, "2026-10-14-gratitude")["questions"]  # sent before snapshots
        doc = self.entry_document()
        self.assertEqual([q["id"] for q in doc["prompt"]["questions"]], ["1", "2"])

    def test_a_malformed_snapshot_is_ignored_not_trusted(self):
        for bad in ("text", [], [1], [{"id": 1, "text": "x"}], [{"id": "1", "text": "x", "section": 3}], None):
            with self.subTest(bad=bad):
                self.assertIsNone(library.snapshot_sections({"questions": bad}))
        self.assertIsNone(library.snapshot_sections(None))

    def test_a_huge_bank_is_not_snapshotted(self):
        p = library.builtins()["evening"]
        with mock.patch.object(library, "SNAPSHOT_MAX_CHARS", 100):
            self.assertEqual(library.question_snapshot(p), [])
        self.assertTrue(library.question_snapshot(p))

    def test_the_snapshot_is_small_next_to_what_the_record_already_holds(self):
        self.send(at(23, 1), only="evening")
        p = self.prompt("2026-10-14-evening")
        import json
        self.assertLess(len(json.dumps(p["questions"], ensure_ascii=False)),
                        len(p["plain_text"]) + len(p["html"]))

    def test_a_re_send_over_the_cap_clears_the_old_set(self):
        path = self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        self.assertTrue(self.prompt("2026-10-14-gratitude")["questions"])
        path.write_text(GRATITUDE + "- A third, new one.\n", encoding="utf-8")
        with mock.patch.object(library, "SNAPSHOT_MAX_CHARS", 90):
            self.send(at(21, 40), only="gratitude")
        self.assertIsNone(self.prompt("2026-10-14-gratitude")["questions"])
        self.assertIsNone(library.snapshot_sections(self.prompt("2026-10-14-gratitude")))  # the library answers

    def test_a_re_send_keeps_the_set_the_new_message_carried(self):
        path = self.write("gratitude.md", GRATITUDE)
        self.send(at(21, 31))
        path.write_text(GRATITUDE + "- A third, new one.\n", encoding="utf-8")
        self.send(at(21, 40), only="gratitude")
        self.assertEqual(len(self.prompt("2026-10-14-gratitude")["questions"]), 3)


if __name__ == "__main__":
    unittest.main()
