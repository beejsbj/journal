"""His words only: what the engine and Hermes add to a turn is never filed as his.

Hermes attaches a picture to the model's turn natively and stores the turn as one user row: his caption (or its
stand-in for none), a line naming each cached picture, a placeholder per picture, then whatever was injected into
the turn, the engine's own context among it (``agent/image_routing.py``, ``agent/session_persistence.py``,
``agent/turn_context.py``). A message queued behind a running turn never reaches the gateway hook, so the sweep reads
that row, and the conversation writer reads every row. The cases build rows in that shape from neutral fixtures."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import add_prompt, make_cfg
from test_conversations import Base as ConversationBase
from test_conversations import conv, m
from test_sweep import NOW, SENT, FakeSessions

from journal import context, entries, ledger, notebook, privacy, sweep, today, turncontext
from journal.hermes import transcript

JPEG = b"\xff\xd8\xff\xe0 a picture"


def native(caption, paths, injected=""):
    """The row Hermes stores for a turn with pictures attached natively: the first text part, one placeholder per
    picture, then what was injected into the turn as one more part."""
    first = (caption or transcript.NATIVE_STAND_IN) + "\n\n" + "\n".join(f"[Image attached at: {p}]" for p in paths)
    return "\n".join([first, *["[screenshot]"] * len(paths), *([injected] if injected else [])])


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
        self.cache = Path(self.tmp.name) / "hermes" / "cache" / "images"

    def tearDown(self):
        self.tmp.cleanup()

    def assertNoneOfTheTurnIn(self, text):
        for leak in ("private notebook", "garden plan", "What do you see", "Image attached", "[screenshot]",
                     str(self.cache), turncontext.OPEN, turncontext.CLOSE):
            self.assertNotIn(leak, text)


class ReadTest(Fixture):
    """The adapter's reading of one stored row."""

    def row(self, content, mid="901"):
        return transcript.to_row({"id": 1, "ts": SENT.timestamp(), "role": "user", "content": content, "pmid": mid})

    def test_a_captionless_picture_carrying_the_turns_context_has_no_words_of_his(self):
        for injected in (self.notes, turncontext.fence(self.notes)):  # stored before the fence, and after
            row = self.row(native("", ["/c/img_a.jpg"], injected))
            self.assertEqual((row.kind, row.text, row.meta["loose"]), ("empty", "", ""))
            self.assertEqual(row.meta["images"], ["/c/img_a.jpg"])
            self.assertNotIn("withheld", row.meta)
            self.assertEqual(sweep.message(row)["images"], ["/c/img_a.jpg"])

    def test_his_caption_is_his_words_and_nothing_after_the_pictures_is(self):
        injected = "<memory-context>\nrecalled\n</memory-context>\n\n" + turncontext.fence(self.notes)
        row = self.row(native("the walk by the river", ["/c/a.jpg", "/c/b.png"], injected))
        self.assertEqual((row.kind, row.text, row.meta["loose"]), ("his", "the walk by the river",
                                                                    "the walk by the river"))
        self.assertEqual(row.meta["images"], ["/c/a.jpg", "/c/b.png"])
        self.assertEqual(sweep.message(row)["text"], "the walk by the river")

    def test_the_envelopes_in_front_still_parse(self):
        quoted = '[Replying to: "Morning, how did you sleep?"]\n\n'
        row = self.row(native(quoted.strip(), ["/c/a.jpg"], self.notes))
        self.assertEqual((row.kind, row.quote, row.text), ("empty", "Morning, how did you sleep?", ""))
        row = self.row(native(quoted + "badly", ["/c/a.jpg"], self.notes))
        self.assertEqual((row.kind, row.quote, row.text), ("his", "Morning, how did you sleep?", "badly"))

    def test_a_fence_is_taken_off_wherever_it_is_and_however_it_was_cut(self):
        fenced = turncontext.fence(self.notes)
        spilled = (f"[plugin hook output truncated — 12,000 chars; full content saved to /s/1.txt]\n--- head ---\n"
                   f"{fenced[:60]}\n--- tail ---\n{fenced[-40:]}")
        for stored in ("plain words\n\n" + fenced, "plain words\n\n" + spilled, f"plain words\n\n{fenced[:80]}"):
            row = self.row(stored)
            self.assertEqual((row.kind, row.text, row.meta["loose"]), ("his", "plain words", "plain words"))

    def test_what_still_looks_added_is_withheld_never_his(self):
        for stored, why in (
                ("plain words\n\n" + self.notes, "an engine block"),  # no fence, no picture: nothing to cut it by
                (native("x", ["/c/a.jpg"]) + "\n[screenshot]", "a picture placeholder"),  # placeholders don't add up
                ("plain words\n" + turncontext.CLOSE, "the engine's context fence"),
                ("## Journal: today — Thu 15 Oct 2026\n- weather: rain", "an engine block")):
            row = self.row(stored)
            self.assertEqual((row.kind, row.meta["withheld"], row.meta["caption"], row.meta["loose"]),
                             ("system", why, "", ""))
            self.assertIsNone(sweep.message(row))

    def test_an_opt_out_in_a_withheld_row_still_counts(self):
        row = self.row("/x keep this between us\n\n" + self.notes)
        self.assertEqual(row.meta["withheld"], "an engine block")
        self.assertTrue(privacy.row_marked(self.cfg, row, set()))

    def test_a_row_the_hook_saved_stays_saved_without_the_context(self):
        stored = native("[Journal reply — saved verbatim to the evening entry.]\n\nthe lake", ["/c/a.jpg"],
                        turncontext.fence(self.notes))
        row = self.row(stored)
        self.assertEqual(row.kind, "handled")
        self.assertEqual(row.meta["loose"], "the lake")
        self.assertNoneOfTheTurnIn(row.text)


class FenceTest(Fixture):
    def test_the_turns_context_is_fenced(self):
        # (a turn outside the journal's chat: the notebook alone, every turn)
        out = turncontext.build(self.cfg, turncontext.Turn(platform="cli", session_id="s", turn_id="t",
                                                           user_message="hi", at=SENT))
        self.assertTrue(out.startswith(turncontext.OPEN + "\nYour private notebook — "), out[:80])
        self.assertTrue(out.endswith("\n" + turncontext.CLOSE))
        self.assertEqual(transcript.unwrap("hi\n\n" + out), ("hi\n\n", [], None))

    def test_every_block_opens_with_a_head_the_transcript_knows(self):
        blocks = [self.notes, today.render_data({"date": "2026-10-15", "time": "09:00", "timezone": "IST",
                                                 "weather": "rain"})]
        packs = self.cfg.state_dir / "packs"
        packs.mkdir(parents=True)
        (packs / "2026-10-15-morning.md").write_text("## Recent journal entries\n- a line\n", encoding="utf-8")
        blocks.append(context.pack_for_turn(self.cfg, {"id": "2026-10-15-morning", "kind": "morning"}, SENT))
        for block in blocks:  # (the program block: test_programview, BudgetTest)
            self.assertTrue(block.startswith(turncontext.HEADS), block[:60])


class SweepTest(Fixture):
    """The case that was found: a picture with no caption, sent while a turn was stuck, queued and run half an hour
    later, its row carrying her notebook."""

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

    def test_a_caption_is_saved_under_its_picture(self):
        pic = picture(self.cache / "img_3d4e5f.jpg", self.arrived)
        self.db.add(native("the sky this morning", [pic], turncontext.fence(self.notes)), self.arrived, "3531")
        self.assertEqual(self.sweep()[0], 1)
        body = self.entry.read_text()
        self.assertIn("### 06:08 IST · photo\n\n![[2026-10-15-morning.jpg]]\n\nthe sky this morning", body)
        self.assertNoneOfTheTurnIn(body)

    def test_a_picture_gone_from_the_cache_leaves_the_caption_or_nothing(self):
        self.db.add(native("", [str(self.cache / "img_gone.jpg")], self.notes), self.stored, "3532")
        self.assertEqual(self.sweep()[0], 0)
        self.assertFalse(self.entry.exists())
        self.db.add(native("still here", [str(self.cache / "img_gone2.jpg")], self.notes), self.stored, "3533")
        self.assertEqual(self.sweep()[0], 1)
        body = self.entry.read_text()
        self.assertIn("· text\n\nstill here", body)
        self.assertNoneOfTheTurnIn(body)

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
        self.assertEqual(log, ["sweep: row 1 (message 3536) still carries an engine block once unwrapped; failed "
                               "closed, not read as his words"])  # ("failed": the tick files it in errors.jsonl)

    def test_a_note_with_a_picture_keeps_the_picture(self):
        pic = picture(self.cache / "img_9d0e1f.jpg", SENT + timedelta(hours=7))
        self.db.add(native("/j", [pic], self.notes), SENT + timedelta(hours=7, minutes=20), "3537")
        self.assertEqual(self.sweep()[0], 1)
        body = (self.cfg.root / "2026-10-15-j.md").read_text()
        self.assertIn("· photo\n\n![[2026-10-15-j.jpg]]", body)
        self.assertNoneOfTheTurnIn(body)


class WriterTest(ConversationBase):
    """The conversation writer reads the same rows."""

    def setUp(self):
        super().setUp()
        notebook.add(self.cfg, "ask how the garden plan went")
        self.injected = turncontext.fence(notebook.render(self.cfg, "2026-10-15"))
        self.cache = Path(self.tmp.name) / "hermes" / "cache" / "images"

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
