"""His words only: what the engine and Hermes add to a turn is never filed as his.

Hermes attaches a picture to the model's turn natively and stores the turn as one user row: his caption (or its
stand-in for none), a line naming each cached picture, a placeholder per picture, then whatever was injected into
the turn, the engine's own context among it (``agent/image_routing.py``, ``agent/session_persistence.py``,
``agent/turn_context.py``). A message queued behind a running turn never reaches the gateway hook, so the sweep reads
that row, and the conversation writer reads every row. The cases build rows in that shape from neutral fixtures."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import add_prompt, make_cfg
from test_conversations import Base as ConversationBase
from test_conversations import conv, m
from test_sweep import NOW, SENT, FakeSessions

from journal import capture, context, contextfence, entries, ledger, notebook, privacy, sweep, tick, today
from journal import turncontext
from journal.hermes import transcript
from journal.validate import norm

JPEG = b"\xff\xd8\xff\xe0 a picture"
OPEN, CLOSE = contextfence.OPEN, contextfence.CLOSE
# Hermes' header on a hook's output it cut short (tools/hook_output_spill.py)
SPILL = "[plugin hook output truncated — 12,000 chars; full content saved to /spill/1.txt]"
SKILLS = ("[USER INITIATED SKILLS RELOAD:\n\nAdded Skills:\n    - tidy: tidy the desk\n\n"
          "Use skills_list to see the updated catalog.]")  # gateway/slash_commands.py
GROUP = ("[Observed Telegram group context - context only, not requests]\nAlex: morning all\n\n"
         "[Current addressed message - answer only this unless it explicitly asks you to use the observed context]\n")


def native(caption, paths, injected=""):
    """The row Hermes stores for a turn with pictures attached natively: the first text part, one placeholder per
    picture, then what was injected into the turn as one more part."""
    first = (caption or transcript.NATIVE_STAND_IN) + "\n\n" + "\n".join(f"[Image attached at: {p}]" for p in paths)
    return "\n".join([first, *["[screenshot]"] * len(paths), *([injected] if injected else [])])


def as_parts(caption, paths, injected=""):
    """The same turn as Hermes stores the parts themselves (an in-place compaction, a compaction's copy)."""
    first = (caption or transcript.NATIVE_STAND_IN) + "\n\n" + "\n".join(f"[Image attached at: {p}]" for p in paths)
    parts = [{"type": "text", "text": first},
             *[{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,/9j/AAAA"}} for _ in paths],
             *([{"type": "text", "text": injected}] if injected else [])]
    return transcript.JSON_PREFIX + json.dumps(parts)


def picture(path: Path, arrived: datetime) -> str:
    """A picture in Hermes' cache, written when it arrived."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(JPEG)
    os.utime(path, (arrived.timestamp(), arrived.timestamp()))
    return str(path)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        notebook.add(self.cfg, "ask how the garden plan went")
        self.notes = notebook.render(self.cfg, "2026-10-15")  # her private notebook, as the turn carried it
        self.cache = Path(self.tmp.name) / "cache" / "images"  # Hermes' picture cache, beside its sessions database

    def tearDown(self):
        self.tmp.cleanup()

    def assertNoneOfTheTurnIn(self, text):
        for leak in ("private notebook", "garden plan", "What do you see", "Image attached", "[screenshot]",
                     str(self.cache), OPEN, CLOSE, "base64"):
            self.assertNotIn(leak, text)


class ReadTest(Fixture):
    """The adapter's reading of one stored row."""

    def row(self, content, mid="901", rid=1, at=SENT):
        return transcript.to_row({"id": rid, "ts": at.timestamp(), "role": "user", "content": content, "pmid": mid})

    def assertWithheld(self, row, why):
        self.assertEqual((row.kind, row.meta.get("withheld"), row.meta["caption"], row.meta["loose"]),
                         ("system", why, "", ""))
        self.assertIsNone(sweep.message(row))

    def test_a_captionless_picture_carrying_the_turns_context_has_no_words_of_his(self):
        for stored in (native("", ["/c/img_a.jpg"], self.notes),  # stored before the fence
                       native("", ["/c/img_a.jpg"], contextfence.wrap(self.notes)),
                       as_parts("", ["/c/img_a.jpg"], contextfence.wrap(self.notes))):
            row = self.row(stored)
            self.assertEqual((row.kind, row.text, row.meta["loose"]), ("empty", "", ""))
            self.assertEqual(row.meta["images"], ["/c/img_a.jpg"])
            self.assertNotIn("withheld", row.meta)
            self.assertEqual(sweep.message(row)["images"], ["/c/img_a.jpg"])
            self.assertNotIn("base64", row.stored)  # (parts stored as JSON: read as Hermes' text of them)

    def test_his_caption_is_his_words_and_nothing_after_the_pictures_is(self):
        injected = "<memory-context>\nrecalled\n</memory-context>\n\n" + contextfence.wrap(self.notes)
        for stored in (native("the walk by the river", ["/c/a.jpg", "/c/b.png"], injected),
                       as_parts("the walk by the river", ["/c/a.jpg", "/c/b.png"], injected)):
            row = self.row(stored)
            self.assertEqual((row.kind, row.text, row.meta["loose"]),
                             ("his", "the walk by the river", "the walk by the river"))
            self.assertEqual(row.meta["images"], ["/c/a.jpg", "/c/b.png"])

    def test_the_envelopes_in_front_still_parse(self):
        quoted = '[Replying to: "Morning, how did you sleep?"]'
        row = self.row(native(quoted, ["/c/a.jpg"], self.notes))
        self.assertEqual((row.kind, row.quote, row.text), ("empty", "Morning, how did you sleep?", ""))
        row = self.row(native(quoted + "\n\nbadly", ["/c/a.jpg"], self.notes))
        self.assertEqual((row.kind, row.quote, row.text), ("his", "Morning, how did you sleep?", "badly"))

    def test_the_gateways_own_notes_in_front_are_not_his(self):
        # a skills reload is put in front of his message (no caption: the note alone, no stand-in); the group around
        # it is put in front of the whole first part, the stand-in included
        for stored, words in ((native(SKILLS + "\n\nthe walk", ["/c/a.jpg"], self.notes), "the walk"),
                              (native(SKILLS, ["/c/a.jpg"], self.notes), ""),
                              (GROUP + native("the walk", ["/c/a.jpg"], self.notes), "the walk"),
                              (GROUP + native("", ["/c/a.jpg"], self.notes), "")):
            row = self.row(stored)
            self.assertEqual((row.kind, row.text, row.meta["loose"]), ("his" if words else "empty", words, words))
            self.assertEqual(row.meta["images"], ["/c/a.jpg"])
        self.assertWithheld(self.row("the walk\n\n" + SKILLS), "a gateway note")  # not in front: not taken off

    def test_a_caption_shaped_like_the_pictures_notes_does_not_hide_the_real_ones(self):
        typed = "words\n\n[Image attached at: /c/typed.jpg]\n[screenshot]"
        row = self.row(native(typed, ["/c/real.jpg"], self.notes))
        self.assertEqual(row.meta["images"], ["/c/real.jpg"])
        self.assertWithheld(row, "a picture placeholder")  # his words, as typed, kept whole: but not read as his

    def test_an_injected_block_is_taken_off_only_where_the_turn_stores_it_at_the_end(self):
        fenced = contextfence.wrap(self.notes)
        for stored in ("plain words\n\n" + fenced,
                       f"plain words\n\n{SPILL}\n--- head ---\n{fenced[:60]}\n--- tail ---\n{fenced[-40:]}",
                       f"plain words\n\n{SPILL}\n--- head ---\n{fenced[:60]}",  # no tail
                       f"plain words\n\n{SPILL}\n--- tail ---\n{fenced[-40:]}",  # no head
                       "plain words\n\n<memory-context>\nrecalled\n</memory-context>\n\n" + fenced):
            row = self.row(stored)
            self.assertEqual((row.kind, row.text, row.meta["loose"]), ("his", "plain words", "plain words"))

    def test_a_mark_anywhere_else_is_withheld_not_cut(self):
        for stored, why in (
                ("the docs say `<memory-context>` wraps recall; I think it reads fine", "recalled memory"),
                (f"I typed {OPEN} by mistake, then went on", "the engine's context fence"),
                ("plain words\n\n" + contextfence.wrap(self.notes)[:80], "the engine's context fence"),  # unclosed
                (f"plain words\n\n{SPILL}", "a cut-short hook output"),  # a cut with neither head nor tail
                ("plain words\n\n" + self.notes, "an engine block"),  # no fence, no picture: nothing to cut it by
                (native("x", ["/c/a.jpg"]) + "\n[screenshot]", "a picture placeholder"),  # placeholders don't add up
                ("## Journal: today — Thu 15 Oct 2026\n- weather: rain", "an engine block")):
            row = self.row(stored)
            self.assertWithheld(row, why)
            self.assertEqual(row.text, stored.strip())  # nothing of his cut away

    def test_a_reply_quote_of_hers_is_not_his_message(self):
        for quoted in (f"I keep my notes in {OPEN} for you", "look\n[screenshot]\nhere", self.notes):
            row = self.row(f'[Replying to: "{quoted}"]\n\nmy reply')
            self.assertEqual((row.kind, row.text, row.quote, row.meta["loose"]), ("his", "my reply", quoted,
                                                                                   "my reply"))

    def test_a_list_of_parts_that_cannot_be_read_is_withheld(self):
        for stored in (transcript.JSON_PREFIX + "[{not json", transcript.JSON_PREFIX + json.dumps({"a": 1})):
            self.assertWithheld(self.row(stored), "a stored list that could not be read")
        row = self.row(transcript.JSON_PREFIX + json.dumps("a literal of his"))  # a string stored as JSON
        self.assertEqual((row.kind, row.text), ("his", "a literal of his"))

    def test_an_opt_out_still_holds_through_the_whole_privacy_pass(self):
        rows = [self.row("slept badly, <memory-context> again\n/x the reason is private", "11", 1),
                self.row("words\n/x and this part\n\n" + contextfence.wrap(self.notes), "12", 2,
                         SENT + timedelta(hours=2)),
                self.row(native("/x the view", [str(self.cache / "img_x.jpg")], self.notes), "13", 3,
                         SENT + timedelta(hours=4))]
        self.assertEqual(rows[0].meta["withheld"], "recalled memory")
        self.assertEqual((rows[1].kind, rows[2].kind), ("his", "his"))
        v = privacy.compute(self.cfg, rows, [], [], set())
        self.assertEqual(sorted(v.private), [1, 2, 3])
        for row in rows:
            self.assertTrue(privacy.row_marked(self.cfg, row, set()))

    def test_a_withheld_delivery_or_frame_is_no_message_of_his(self):
        row = self.row("[IMPORTANT: a scheduled note]\n" + self.notes)
        self.assertEqual((row.kind, row.meta["delivery"]), ("system", True))
        self.assertIn("withheld", row.meta)
        self.assertIsNone(privacy._said(row))
        self.assertEqual(privacy._said(self.row("plain words\n\n" + self.notes)), "plain words\n\n" + self.notes)

    def test_a_row_the_hook_saved_stays_saved_without_the_context(self):
        stored = native("[Journal reply — saved verbatim to the evening entry.]\n\nthe lake", ["/c/a.jpg"],
                        contextfence.wrap(self.notes))
        row = self.row(stored)
        self.assertEqual((row.kind, row.meta["loose"]), ("handled", "the lake"))
        self.assertNoneOfTheTurnIn(row.text)
        # one that still carries a mark: saved by the hook all the same, but read back by nothing
        row = self.row("[Journal reply — saved verbatim to the evening entry.]\n\nthe lake\n\n" + self.notes)
        self.assertEqual((row.kind, row.meta["withheld"], row.meta["loose"]), ("handled", "an engine block", ""))
        self.assertIsNone(sweep.message(row))


class FenceTest(Fixture):
    def test_the_turns_context_is_fenced(self):
        # (a turn outside the journal's chat: the notebook alone, every turn)
        out = turncontext.build(self.cfg, turncontext.Turn(platform="cli", session_id="s", turn_id="t",
                                                           user_message="hi", at=SENT))
        self.assertTrue(out.startswith(OPEN + "\nYour private notebook — "), out[:80])
        self.assertTrue(out.endswith("\n" + CLOSE))
        self.assertEqual(transcript.trailing("hi\n\n" + out), "hi")

    def test_every_block_opens_with_a_head_the_transcript_knows(self):
        blocks = [self.notes, today.render_data({"date": "2026-10-15", "time": "09:00", "timezone": "IST",
                                                 "weather": "rain"})]
        packs = self.cfg.state_dir / "packs"
        packs.mkdir(parents=True)
        (packs / "2026-10-15-morning.md").write_text("## Recent journal entries\n- a line\n", encoding="utf-8")
        blocks.append(context.pack_for_turn(self.cfg, {"id": "2026-10-15-morning", "kind": "morning"}, SENT))
        for block in blocks:  # (the program block: test_programview, BudgetTest)
            self.assertTrue(block.startswith(contextfence.HEADS), block[:60])


class SweepBase(Fixture):
    def setUp(self):
        super().setUp()
        self.db = FakeSessions(Path(self.tmp.name) / "state.db")
        self.cfg.hermes["state_db"] = str(self.db.path)
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=SENT, message_id="3526")
        self.entry = self.cfg.root / "2026-10-15-morning.md"
        self.arrived = SENT + timedelta(minutes=7)  # 06:08 IST
        self.stored = self.arrived + timedelta(minutes=31)  # when the queued turn ran

    def sweep(self, now=NOW):
        log = []
        return sweep.run(self.cfg, now, log), log

    def captures(self, pid="2026-10-15-morning"):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid).get("captures") or []


class SweepTest(SweepBase):
    """The case that was found: a picture with no caption, sent while a turn was stuck, queued and run half an hour
    later, its row carrying her notebook."""

    def test_the_picture_answers_the_prompt_when_he_sent_it_and_her_notebook_stays_hers(self):
        pic = picture(self.cache / "img_0a1b2c.jpg", self.arrived)
        self.db.add(native("", [pic], self.notes), self.stored, "3530")
        n, log = self.sweep()
        self.assertEqual(n, 1, log)
        body = self.entry.read_text()
        self.assertIn("### 06:08 IST · photo\n\n![[2026-10-15-morning.jpg]]\n", body)
        self.assertNoneOfTheTurnIn(body)
        self.assertEqual((self.cfg.root / "2026-10-15-morning.jpg").read_bytes(), JPEG)
        c = self.captures()[0]
        self.assertEqual((c["at"], c["source"], c["images"], c["via"]),
                         (self.arrived.isoformat(), "photo", ["2026-10-15-morning.jpg"], "sweep"))

    def test_stored_as_parts_it_reads_the_same(self):
        pic = picture(self.cache / "img_4b5c6d.jpg", self.arrived)
        self.db.add(as_parts("", [pic], contextfence.wrap(self.notes)), self.stored, "3540")
        self.assertEqual(self.sweep()[0], 1)
        body = self.entry.read_text()
        self.assertIn("### 06:08 IST · photo\n\n![[2026-10-15-morning.jpg]]\n", body)
        self.assertNoneOfTheTurnIn(body)

    def test_a_caption_is_saved_under_its_picture(self):
        pic = picture(self.cache / "img_3d4e5f.jpg", self.arrived)
        self.db.add(native("the sky this morning", [pic], contextfence.wrap(self.notes)), self.arrived, "3531")
        self.assertEqual(self.sweep()[0], 1)
        body = self.entry.read_text()
        self.assertIn("### 06:08 IST · photo\n\n![[2026-10-15-morning.jpg]]\n\nthe sky this morning", body)
        self.assertNoneOfTheTurnIn(body)

    def test_a_picture_gone_from_the_cache_leaves_the_caption_or_nothing_and_says_so(self):
        self.db.add(native("", [str(self.cache / "img_gone.jpg")], self.notes), self.stored, "3532")
        n, log = self.sweep()
        self.assertEqual(n, 0)
        self.assertFalse(self.entry.exists())
        self.assertEqual(log, ["sweep: a picture of message 3532 was not kept (gone from Hermes' picture cache)"])
        self.db.add(native("still here", [str(self.cache / "img_gone2.jpg")], self.notes), self.stored, "3533")
        self.assertEqual(self.sweep()[0], 1)
        body = self.entry.read_text()
        self.assertIn("· text\n\nstill here", body)
        self.assertNoneOfTheTurnIn(body)

    def test_a_picture_outside_hermes_cache_is_never_copied(self):
        elsewhere = picture(Path(self.tmp.name) / "elsewhere" / "img_9.jpg", self.arrived)
        through = str(self.cache / ".." / ".." / "elsewhere" / "img_9.jpg")  # a path that climbs out of the cache
        self.db.add(native("the view", [elsewhere, through], self.notes), self.stored, "3541")
        n, log = self.sweep()
        self.assertEqual(n, 1, log)
        self.assertEqual(log[:2], ["sweep: a picture of message 3541 was not kept (not in Hermes' picture cache)"] * 2)
        self.assertFalse(list(self.cfg.root.glob("*.jpg")))
        c = self.captures()[0]
        self.assertEqual((c["at"], c["source"]), (self.stored.isoformat(), "text"))  # (its file time is not read)

    def test_a_picture_too_large_is_said_and_his_caption_kept(self):
        pic = picture(self.cache / "img_big.jpg", self.arrived)
        self.cfg.images["max_mb"] = 0.000001
        self.db.add(native("a big one", [pic], self.notes), self.stored, "3542")
        n, log = self.sweep()
        self.assertEqual(n, 1)
        self.assertIn("sweep: a picture of message 3542 was not kept (over images.max_mb)", log)
        self.assertIn("· text\n\na big one", self.entry.read_text())
        self.assertFalse(list(self.cfg.root.glob("*.jpg")))

    def test_a_copy_left_behind_by_a_failed_record_is_removed(self):
        pic = picture(self.cache / "img_rec.jpg", self.arrived)
        self.db.add(native("", [pic], self.notes), self.stored, "3543")
        with mock.patch.object(capture, "record", side_effect=RuntimeError("disk full")):
            with self.assertRaises(RuntimeError):
                self.sweep()
        self.assertFalse(list(self.cfg.root.glob("*.jpg")))
        with mock.patch.object(capture, "record", return_value=None):
            self.assertEqual(sweep.reconcile(self.cfg, {"message_id": "3543", "text": "", "quote": None,
                                                        "source": "text", "audio_path": None, "images": [pic]},
                                             self.arrived), None)
        self.assertFalse(list(self.cfg.root.glob("*.jpg")))

    def test_a_picture_that_arrived_off_the_record_is_not_captured(self):
        self.db.add("/x", SENT + timedelta(minutes=5), "3534")  # a bare /x on the busy path opens a window
        pic = picture(self.cache / "img_6a7b8c.jpg", self.arrived)
        self.db.add(native("", [pic], self.notes), SENT + timedelta(hours=3), "3535")  # stored long after it closed
        self.assertEqual(self.sweep()[0], 0)
        self.assertFalse(self.entry.exists())

    def test_a_withheld_row_is_never_captured_and_goes_to_the_errors(self):
        self.db.add("slept well\n\n" + self.notes, SENT + timedelta(minutes=9), "3536")
        n, log = self.sweep()
        self.assertEqual(n, 0)
        self.assertFalse(self.entry.exists())
        self.assertEqual(log, ["sweep: withheld row 1 (message 3536): what is left of it still carries an engine "
                               "block, so it is not read as his words"])
        self.assertTrue(tick.files_as_error(log[0]))
        self.assertNotIn("slept", log[0])

    def test_a_note_with_a_picture_keeps_the_picture(self):
        pic = picture(self.cache / "img_9d0e1f.jpg", SENT + timedelta(hours=7))
        self.db.add(native("/j", [pic], self.notes), SENT + timedelta(hours=7, minutes=20), "3537")
        self.assertEqual(self.sweep()[0], 1)
        body = (self.cfg.root / "2026-10-15-j.md").read_text()
        self.assertIn("· photo\n\n![[2026-10-15-j.jpg]]", body)
        self.assertNoneOfTheTurnIn(body)


class ArrivalTest(Fixture):
    """``sweep.arrival``: the earliest file time between the sweep's horizon and the row's own time."""

    def setUp(self):
        super().setUp()
        self.row = transcript.to_row({"id": 1, "ts": (SENT + timedelta(hours=1)).timestamp(), "role": "user",
                                      "content": "", "pmid": "1"})
        self.since = SENT - timedelta(hours=1)

    def at(self, *times):
        return sweep.arrival(self.row, [picture(self.cache / f"img_{i}.jpg", t) for i, t in enumerate(times)],
                             self.since)

    def test_the_bounds(self):
        self.assertEqual(self.at(SENT + timedelta(minutes=5)), SENT + timedelta(minutes=5))
        self.assertEqual(self.at(self.since - timedelta(minutes=1)), self.row.at)  # before the horizon
        self.assertEqual(self.at(self.row.at + timedelta(minutes=1)), self.row.at)  # after the row's own time
        self.assertEqual(self.at(self.row.at), self.row.at)
        self.assertEqual(self.at(SENT + timedelta(minutes=20), SENT + timedelta(minutes=5),
                                 self.since - timedelta(hours=2)), SENT + timedelta(minutes=5))  # several: earliest
        self.assertEqual(sweep.arrival(self.row, [str(self.cache / "img_none.jpg")], self.since), self.row.at)


class ReadersTest(SweepBase):
    """What the companion is shown of his chat (the context pack) and what she may quote as his (the quote checker)
    never hold her notebook, from a row that carried it."""

    def test_the_pack_and_the_quote_checker_never_take_her_notebook_for_his_words(self):
        now = datetime.now(timezone.utc)  # (the pack reads the last days by the wall clock)
        self.db.add(native("the river at dawn", [str(self.cache / "img_r.jpg")], self.notes),
                    now - timedelta(hours=3), "3550")
        self.db.add("slept well\n\n" + self.notes, now - timedelta(hours=2), "3551")  # withheld
        self.db.add(native("", [str(self.cache / "img_s.jpg")], contextfence.wrap(self.notes)),
                    now - timedelta(hours=1), "3552")
        pack = "\n".join(context.telegram_messages(self.cfg))
        self.assertIn("the river at dawn", pack)
        self.assertNotIn("garden plan", pack)
        self.assertNotIn("slept well", pack)
        verify = context.make_verifier(self.cfg)
        self.assertTrue(verify("telegram:x", norm("the river at dawn")))
        self.assertFalse(verify("telegram:x", norm("ask how the garden plan went")))
        self.assertFalse(verify("telegram:x", norm("open threads you meant to pick up with")))


class WriterTest(ConversationBase):
    """The conversation writer reads the same rows."""

    def setUp(self):
        super().setUp()
        notebook.add(self.cfg, "ask how the garden plan went")
        self.injected = contextfence.wrap(notebook.render(self.cfg, "2026-10-15"))
        self.cache = Path(self.tmp.name) / "cache" / "images"

    def test_a_native_picture_in_chat_writes_his_caption_and_none_of_the_turn(self):
        self.his("one thing", m(0), "570")
        self.his(native("the walk", [str(self.cache / "img_a.jpg")], self.injected), m(2), "571")
        self.hers("Lovely.", m(3))
        self.tick(m(8))
        body = self.note().read_text()
        self.assertIn("**19:32** — the walk", conv(body))
        for leak in ("garden plan", "private notebook", "Image attached", "[screenshot]", "What do you see"):
            self.assertNotIn(leak, body)

    def test_a_swept_captionless_picture_is_the_reply_the_chat_after_it_follows(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-10), message_id="690")
        pic = picture(self.cache / "img_b.jpg", m(1))
        self.his(native("", [pic], self.injected), m(2), "691")
        self.hers("What a sky.", m(3))
        self.his("it was, the whole way home", m(5), "692")
        log = []
        sweep.run(self.cfg, m(12), log)
        self.tick(m(12))
        evening = self.cfg.root / "2026-10-15-evening.md"
        body = evening.read_text()
        self.assertIn("![[2026-10-15-evening.jpg]]", entries.raw_entry(body))
        self.assertIn("it was, the whole way home", conv(body))
        self.assertFalse(self.note().exists())
        self.assertNotIn("garden plan", body)


if __name__ == "__main__":
    unittest.main()
