"""Privacy and his-words seams found by the window-D review, each pinned by a test that failed before its fix.

(2) The sweep skipped its off-the-record check for any row that starts with /j, so a busy-path burst of a /j note
and a /x message on a later line was saved with the /x words. (3) A captionless photo (a ``system`` placeholder or
an empty native row) inside a bare-/x window was never marked private: the stashed picture was kept and embedded,
and Hermes' vision description reached the telegram_history pack. (4) /x on a later line is off the record on
every face, as in the chat. (5) The audio fill matched the recording's embed anywhere, even mid-line in words he
edited. (6) Smaller edges: a description after the embed was deleted, the transcribing placeholder saved as his
words, a conversation photo filed on phase 1's prompt, a private stashed picture kept for days, an unusable
``quiet_minutes`` raising past the window check, and MCP's journal_start inside a window."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import make_cfg

from journal import agent, audio, context, conversations, entries, privacy, sweep, tick
from journal.mcp import handlers as mcp_handlers
from journal.serve import blocks, document
from serve_helpers import IST, NEUTRAL, NOW as SERVE_NOW
from test_conversations import Base, m, near_t0, _NearT0
from test_images import NOW as IMAGES_NOW, ReceiveBase, note as image_note
from test_mcp import Case as McpCase
from test_serve_blocks import EVENING_STEM, EditCase


class SweepJInsideTest(Base):
    """A busy-path row holding a /j note and, on a later line, a /x message: the hook, the pass and the writer take
    the whole row as off the record; the sweep must too."""

    def test_a_j_row_with_x_on_a_later_line_is_not_swept(self):
        for mode in ("everything", "arm"):
            with self.subTest(mode=mode):
                self.cfg.capture["mode"] = mode
                self.his("/j keep this line\n/x but PRIVATE-WORDS here", m(0), f"60{len(mode)}")
                log = []
                sweep.run(self.cfg, m(5), log)
                conversations.run(self.cfg, m(8), log)
                leaked = [p.name for p in self.cfg.root.glob("*.md") if "PRIVATE-WORDS" in p.read_text()]
                self.assertEqual(leaked, [])
                self.assertFalse([line for line in log if line.startswith("swept")], log)

    def test_a_plain_j_inside_a_window_is_still_swept(self):
        self.his("/x", m(0), "610")
        self.his("/j this one is kept", m(1), "611")
        log = []
        sweep.run(self.cfg, m(5), log)
        self.assertTrue(any("swept message 611" in line for line in log), log)


class WindowPhotoTest(Base):
    """A captionless photo inside a bare-/x window the hook did not record (a busy-path /x row, a stash the hook
    made thinking it public): the pass marks it private, so the picture is never kept nor embedded."""

    def window(self):
        self.his("one thing", m(0), "570")
        self.his("/x", m(1), "571")  # a bare /x stored as a row: the hook never saw it
        self.hers("Okay, off the record.", m(1.5))

    def stash(self):
        cache = Path(self.tmp.name) / "cache.jpg"
        cache.write_bytes(b"\xff\xd8SECRET")
        conversations.stash_images(self.cfg, "572", [(str(cache), ".jpg")], m(2))

    def check(self):
        self.hers("I see it.", m(3))
        self.tick(m(8))
        rows = self.cfg.transcript().rows(since=m(-10))
        photo = next(r.id for r in rows if r.platform_message_id == "572")
        self.assertIn(photo, privacy.view(self.cfg, m(8)).private)
        self.assertEqual(sorted(p.name for p in self.cfg.root.glob("*.jpg")), [])
        for p in self.cfg.root.glob("*.md"):
            self.assertNotIn("![[", p.read_text())

    def test_a_text_mode_placeholder_with_no_caption(self):
        self.window()
        self.stash()
        self.his(image_note("A private thing."), m(2), "572")
        self.check()

    def test_a_native_row_with_no_text(self):
        self.window()
        self.stash()
        self.his("", m(2), "572")
        self.check()

    def test_a_photo_before_the_window_stays_public(self):
        self.stash()
        self.his("", m(0), "572")
        self.his("/x", m(1), "573")
        self.tick(m(8))
        self.assertEqual(sorted(p.name for p in self.cfg.root.glob("*.jpg")), ["2026-10-15-j.jpg"])

    def test_a_photo_keeps_the_window_open_for_her_reply(self):
        """The photo came 40 minutes into the window and her reply 10 minutes after it: past ``quiet_minutes`` (45)
        from the activity before the photo, but not from the photo, which is his activity."""
        self.window()
        self.stash()
        self.his("", m(41), "572")
        self.hers("SECRET-REPLY about the picture.", m(51))
        self.his("a later private line", m(70), "573")
        self.hers("SECRET-TOO.", m(71))
        self.tick(m(120))
        saved = "".join(p.read_text() for p in self.cfg.root.glob("*.md"))
        self.assertNotIn("SECRET", saved)
        rows = self.cfg.transcript().rows(since=m(-10))
        private = privacy.view(self.cfg, m(120)).private
        for r in rows:
            if r.role == "assistant" and "SECRET" in r.text or r.platform_message_id == "573":
                self.assertIn(r.id, private, r.text)

    def stashed(self):
        d = self.cfg.state_dir / "audio-stash"
        return (sorted(p.name for p in d.iterdir()) if d.is_dir() else [],
                sorted(conversations.snapshot(self.cfg).get("images") or {}))

    def test_a_private_stashed_picture_is_purged_at_the_tick_not_days_later(self):
        for caption in ("private caption", ""):
            for mode in ("everything", "arm"):
                with self.subTest(caption=caption, mode=mode):
                    self.tearDown()
                    self.setUp()
                    self.cfg.capture["mode"] = mode
                    self.window()
                    self.stash()
                    self.his(image_note("x") + (f"\n\n{caption}" if caption else ""), m(2), "572")
                    sweep.run(self.cfg, m(8), [])  # (the tick's order: under arm the writer waits for the sweep)
                    self.tick(m(8))
                    self.assertEqual(self.stashed(), ([], []))
                    self.assertEqual(sorted(p.name for p in self.cfg.root.glob("*.jpg")), [])  # (not kept either)

    def test_the_vision_description_stays_out_of_the_pack(self):
        self.window()
        self.his(image_note("SECRET-DESCRIPTION of a private letter."), m(2), "572")
        # (the pack reads its 7-day horizon from the real clock: pinned beside T0, as privacy is)
        with near_t0(), mock.patch.object(context, "datetime", _NearT0):
            lines = [line for _at, line in context._his_messages(self.cfg)]
        self.assertTrue(lines and "one thing" in lines[0], lines)
        self.assertFalse([line for line in lines if "SECRET-DESCRIPTION" in line], lines)


LATER_LINE = ("fine\n/x secret on a later line", "fine\n  /X secret", "a\nb\n/x@bot c")


class WebLaterLineTest(EditCase):
    """/x on a later line holds on the web as in the chat (the hook's opt_out_inside rule): 422 and nothing written,
    for a new entry, a new block and an edit of a block."""

    def test_every_web_write_refuses_it(self):
        before = self.entry(EVENING_STEM)
        for text in LATER_LINE:
            with self.subTest(text=text):
                got = self.refused("POST", f"/api/v1/entries/{EVENING_STEM}/blocks", {"text": text}, want=422)
                self.assertEqual(got["error"], "off-the-record")
                got = self.patch("words:0", text, want=422)
                self.assertEqual(got["error"], "off-the-record")
                got = self.refused("POST", "/api/v1/entries", {"text": text}, want=422)
                self.assertEqual(got["error"], "off-the-record")
        self.assertEqual(self.entry(EVENING_STEM), before)

    def test_a_slash_x_mid_line_is_still_words(self):
        self.patch("words:0", "I typed /x in the middle of a line, which is no command")


class McpLaterLineTest(McpCase):
    def test_save_and_note_refuse_it(self):
        before = sorted(p.read_bytes() for p in self.cfg.root.glob("*.md"))
        for text in LATER_LINE:
            with self.subTest(text=text):
                with self.assertRaisesRegex(mcp_handlers.Refused, "off-the-record"):
                    mcp_handlers.save(self.cfg, SERVE_NOW, text)
                with self.assertRaisesRegex(mcp_handlers.Refused, "off-the-record"):
                    mcp_handlers.save(self.cfg, SERVE_NOW, text, "morning")
                with self.assertRaisesRegex(mcp_handlers.Refused, "off-the-record"):
                    mcp_handlers.add_note(self.cfg, SERVE_NOW, text)
        self.assertEqual(sorted(p.read_bytes() for p in self.cfg.root.glob("*.md")), before)


class FillAudioTest(unittest.TestCase):
    """The tick's transcript goes only into the recording's own block: the embed as a whole line directly under an
    engine heading of a recording. His words that mention the embed (an edit from the web app) are never touched."""

    STEM = "2026-10-14-evening"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name), NEUTRAL)
        prompt = {"stem": self.STEM, "kind": "evening", "local_date": "2026-10-14", "sent_at_local": "23:00 IST",
                  "opener": "hi"}
        t = lambda h, mi: datetime(2026, 10, 14, h, mi, tzinfo=IST)  # noqa: E731
        self.path = entries.record_reply(self.cfg.root, prompt, "**Today** was long.", t(20, 0), "text",
                                         names=NEUTRAL)
        entries.record_reply(self.cfg.root, prompt, audio.TRANSCRIBING, t(20, 5), "audio", f"{self.STEM}.ogg",
                             names=NEUTRAL)

    def edit_first(self, words):
        body = self.path.read_text()
        b = next(x for x in document.blocks_of(body, self.STEM) if x["key"] == "words:0")
        self.path.write_text(blocks.edit(body, "words:0", words, was=b["text"], at=b["at"], stem=self.STEM))

    def fill(self):
        return entries.fill_audio_block(self.path, f"{self.STEM}.ogg", "the transcript",
                                        "### 20:05 IST · audio file · 0:30", audio.TRANSCRIBING)

    def his_blocks(self):
        return [x["text"] for x in document.blocks_of(self.path.read_text(), self.STEM)]

    def test_an_embed_he_mentions_mid_line_is_not_the_block(self):
        mine = f"**Today** was long; I said more in ![[{self.STEM}.ogg]] below."
        self.edit_first(mine)
        self.assertTrue(self.fill())
        self.assertEqual(self.his_blocks(), [mine, "the transcript"])
        self.assertIn("### 20:05 IST · audio file · 0:30\n\n![[", self.path.read_text())

    def by_hand(self, old, new):
        """An edit in his notes app (the web app refuses an embed line of his own, and removing a recording that is
        still being transcribed)."""
        body = self.path.read_text()
        self.assertIn(old, body)
        self.path.write_text(body.replace(old, new, 1))

    def test_an_embed_line_under_his_text_heading_is_not_the_block(self):
        self.by_hand("**Today** was long.", f"listen:\n\n![[{self.STEM}.ogg]]")
        self.assertTrue(self.fill())
        self.assertIn(f"### 20:00 IST · text\n\nlisten:\n\n![[{self.STEM}.ogg]]\n\n### 20:05 IST · audio file · 0:30"
                      f"\n\n![[{self.STEM}.ogg]]\n\nthe transcript", self.path.read_text())

    def test_no_recording_block_writes_nothing(self):
        self.by_hand(f"![[{self.STEM}.ogg]]\n\n{audio.TRANSCRIBING}", f"it was ![[{self.STEM}.ogg]]")
        before = self.path.read_bytes()
        self.assertFalse(self.fill())
        self.assertEqual(self.path.read_bytes(), before)

    def test_the_conversation_block_is_still_found(self):
        blk = conversations.his_block(self.cfg, datetime(2026, 10, 14, 21, 0, tzinfo=IST), audio.TRANSCRIBING,
                                      "audio", "x.ogg", None, "2026-10-14", key="900")
        entries.append_conversation(self.path, blk, NEUTRAL)
        self.assertTrue(entries.fill_audio_block(self.path, "x.ogg", "talked", "**21:00** · audio file · 0:10",
                                                 audio.TRANSCRIBING))
        self.assertIn("**21:00** · audio file · 0:10\n\n![[x.ogg]]\n\ntalked", self.path.read_text())

    def test_the_tick_says_when_the_block_is_gone_and_writes_nothing(self):
        from journal import ledger, tick
        (self.cfg.root / f"{self.STEM}.ogg").write_bytes(b"ogg")
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": self.STEM, "kind": "evening", "local_date": "2026-10-14",
                                    "entry_path": str(self.path), "captures": [
                                        {"at": datetime(2026, 10, 14, 20, 5, tzinfo=IST).isoformat(),
                                         "rule": "direct-reply", "source": "audio", "audio": f"{self.STEM}.ogg",
                                         "transcript": "pending"}]})
        self.edit_first(f"**Today** was long; I said more in ![[{self.STEM}.ogg]] below.")
        self.by_hand(f"![[{self.STEM}.ogg]]\n\n{audio.TRANSCRIBING}", "my own words")
        before = self.path.read_bytes()
        log = []
        with mock.patch.object(audio, "transcribe", lambda cfg, path: ("the transcript", "")), \
                mock.patch.object(audio, "probe", lambda cfg, path: {}):
            tick.process_audio(self.cfg, SERVE_NOW, log)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(any("no block" in line and "nothing written" in line for line in log), log)


class DescribeGoneTest(ReceiveBase):
    """He removed the photo's block (the web app) before the tick described it: the picture is not handed to the
    model, and no description of it is written into the entry."""

    def setUp(self):
        super().setUp()
        self.cfg.images["describe"] = True
        self.recv("the lake", reply_to="700")
        self.path = self.cfg.root / "2026-10-14-evening.md"
        self.calls = []

    def remove_photo_block(self):
        body = self.path.read_text()
        b = next(x for x in document.blocks_of(body, self.path.stem) if x["source"] == "photo")
        self.path.write_text(blocks.remove(body, b["key"], was=b["text"], at=b["at"], stem=self.path.stem))

    def ask(self, before=None):
        def fake(cfg, query, budget, toolsets=None, image=None):
            self.calls.append(image)
            if before:
                before()
            return "A frozen lake under a pale sky."
        return mock.patch.object(agent, "ask", fake)

    def capture(self):
        from journal import ledger
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), self.prompt["id"])["captures"][0]

    def test_a_removed_block_is_not_described(self):
        self.remove_photo_block()
        log = []
        with self.ask():
            tick.process_images(self.cfg, IMAGES_NOW, log)
        self.assertEqual(self.calls, [])
        self.assertNotIn("frozen lake", self.path.read_text())
        self.assertEqual(self.capture()["describe"], "none")

    def test_a_block_removed_while_the_model_answers_gets_no_note(self):
        with self.ask(before=self.remove_photo_block):
            tick.process_images(self.cfg, IMAGES_NOW, [])
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn("frozen lake", self.path.read_text())
        self.assertEqual(self.capture()["describe"], "none")


class PlaceholderWordsTest(EditCase):
    """The engine's "transcript pending" line is not his words: saved as a block's text, the tick would take the
    block for a recording waiting for its transcript, and the web app would lock it as one."""

    def test_the_placeholder_is_refused_and_nothing_written(self):
        before = self.entry(EVENING_STEM)
        for text in (audio.TRANSCRIBING, f"my words\n\n{audio.TRANSCRIBING}"):
            with self.subTest(text=text):
                got = self.patch("words:0", text, want=422)
                self.assertEqual(got["error"], "placeholder")
                got = self.refused("POST", f"/api/v1/entries/{EVENING_STEM}/blocks", {"text": text}, want=422)
                self.assertEqual(got["error"], "placeholder")
        self.assertEqual(self.entry(EVENING_STEM), before)


class McpPlaceholderTest(McpCase):
    """The web refuses the transcript-pending line as his words; MCP does too, or the web app would then lock that
    block for good (no recording will ever fill it)."""

    def test_save_and_note_refuse_it(self):
        before = sorted(p.read_bytes() for p in self.cfg.root.glob("*.md"))
        for text in (audio.TRANSCRIBING, f"he dictated this\n\n{audio.TRANSCRIBING}"):
            with self.subTest(text=text):
                for call in (lambda: mcp_handlers.save(self.cfg, SERVE_NOW, text),
                             lambda: mcp_handlers.save(self.cfg, SERVE_NOW, text, "morning"),
                             lambda: mcp_handlers.add_note(self.cfg, SERVE_NOW, text)):
                    with self.assertRaisesRegex(mcp_handlers.Refused, "transcribed"):
                        call()
        self.assertEqual(sorted(p.read_bytes() for p in self.cfg.root.glob("*.md")), before)


class SessionConversationCaptureTest(unittest.TestCase):
    """A picture or a recording he sends in the chat after a session's phases closed lands in the session entry's
    conversation. Its capture is the conversation's, never phase 1's answer: the phases' blocks and captures still
    pair one for one."""

    STEM = "2026-10-15-fix-life-s01"
    BASE = {"stem": STEM, "kind": "fix-life", "local_date": "2026-10-15", "program": "fix-life", "run": "r1",
            "session": 1, "session_count": 3, "session_title": "Vision", "title": "Fix life"}

    def setUp(self):
        from journal import ledger
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name), NEUTRAL)
        t = lambda h, mi: datetime(2026, 10, 15, h, mi, tzinfo=IST)  # noqa: E731
        self.t = t
        self.path = entries.record_reply(self.cfg.root, {**self.BASE, "phase": 1, "phase_title": "Wake"},
                                         "tomorrow 7:00", t(6, 0), "text", names=NEUTRAL)
        entries.record_reply(self.cfg.root, {**self.BASE, "phase": 2, "phase_title": "Two"}, "two", t(6, 10),
                             "text", names=NEUTRAL)
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"] += [
                {"id": "r1-s01-p1", **self.BASE, "phase": 1, "entry_path": str(self.path),
                 "captures": [{"at": t(6, 0).isoformat()}]},
                {"id": "r1-s01-p2", **self.BASE, "phase": 2, "entry_path": str(self.path),
                 "captures": [{"at": t(6, 10).isoformat()}]}]
        self.writer = conversations._Writer(self.cfg, {}, write=True, annotate=False, via="conversation")

    def captures(self):
        from journal import ledger
        return {q["id"]: q["captures"] for q in ledger.snapshot(self.cfg.ledger_path)["prompts"]}

    def test_a_picture_is_not_filed_as_phase_one(self):
        from journal import ledger
        ev = conversations.Event(self.t(9, 0), 5, "his", key="777", mid="777", source="photo")
        self.writer._pending_description(ev, str(self.path), [f"{self.STEM}.jpg"])
        self.writer._pending_description(ev, str(self.path), [f"{self.STEM}.jpg"])  # a replay files it once
        caps = self.captures()
        self.assertEqual(len(caps["r1-s01-p1"]), 1)
        filed = [c for cs in caps.values() for c in cs if c.get("rule") == "conversation"]
        self.assertEqual([(c["images"], c["describe"]) for c in filed], [([f"{self.STEM}.jpg"], "pending")])
        for pid in caps:
            self.assertEqual(len(ledger.answers(ledger.find(ledger.snapshot(self.cfg.ledger_path), pid))), 1)

    def test_a_recording_is_not_filed_as_phase_one(self):
        ev = conversations.Event(self.t(9, 0), 5, "his", key="778", mid="778", source="audio")
        self.writer._pending_transcript(ev, str(self.path), f"{self.STEM}.ogg", {})
        self.writer._pending_transcript(ev, str(self.path), f"{self.STEM}.ogg", {})
        caps = self.captures()
        self.assertEqual(len(caps["r1-s01-p1"]), 1)
        self.assertEqual(len([c for cs in caps.values() for c in cs if c.get("rule") == "conversation"]), 1)

    def test_an_entry_of_a_practice_still_files_on_its_prompt(self):
        from journal import ledger
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].insert(0, {"id": "2026-10-15-evening", "kind": "evening", "local_date": "2026-10-15",
                                       "entry_path": str(self.cfg.root / "e.md"), "captures": [{"at": "x"}]})
        (self.cfg.root / "e.md").write_text("e")
        ev = conversations.Event(self.t(9, 0), 5, "his", key="779", mid="779", source="photo")
        self.writer._pending_description(ev, str(self.cfg.root / "e.md"), ["e.jpg"])
        self.assertEqual([c.get("rule") for c in self.captures()["2026-10-15-evening"]], [None, "conversation"])

    def test_a_phase_with_a_conversation_capture_still_reads_its_blocks(self):
        from journal import programs
        run = {"id": "r1", "program": "fix-life", "sessions": {"1": {"phases": {"1": {"prompt_id": "r1-s01-p1"}}}}}
        with mock.patch.object(programs, "entry_path", lambda cfg, run, s: self.path):
            before = programs._visit_blocks(self.cfg, run, 1, 1)
            from journal import ledger
            with ledger.locked(self.cfg.ledger_path) as data:  # (as the old writer filed it: on phase 1)
                data["prompts"][0]["captures"].append({"at": self.t(9, 0).isoformat(), "rule": "conversation",
                                                       "source": "photo", "images": ["x.jpg"]})
            self.assertEqual(programs._visit_blocks(self.cfg, run, 1, 1), before)
        self.assertTrue(before)



class UnreadableEntryDescriptionTest(unittest.TestCase):
    """An entry that cannot be read just now says nothing about his block: the description stays pending."""

    def test_an_unreadable_entry_is_not_a_removed_block(self):
        from journal import images
        with tempfile.TemporaryDirectory() as d:
            entry = Path(d) / "2026-10-14-evening.md"
            entry.write_text("![[a.jpg]]\n")
            self.assertIs(tick._embedded(entry, "a.jpg"), True)
            self.assertIs(tick._embedded(entry, "b.jpg"), False)
            self.assertIs(tick._embedded(Path(d) / "gone.md", "a.jpg"), False)
            with mock.patch.object(Path, "read_text", mock.Mock(side_effect=PermissionError("denied"))):
                self.assertIsNone(tick._embedded(entry, "a.jpg"))

    def test_the_capture_stays_pending_while_the_entry_is_unreadable(self):
        from journal import ledger
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(Path(d), NEUTRAL)
            cfg.images["describe"] = True
            entry = cfg.root / "2026-10-14-evening.md"
            entry.write_text("![[a.jpg]]\n")
            (cfg.root / "a.jpg").write_bytes(b"\xff\xd8x")
            with ledger.locked(cfg.ledger_path) as data:
                data["prompts"].append({"id": "p1", "entry_path": str(entry), "captures": [
                    {"at": "2026-10-14T20:00:00+00:00", "images": ["a.jpg"], "describe": "pending"}]})
            asked = []
            with mock.patch.object(tick.images, "describing", lambda c: True), \
                    mock.patch.object(tick, "_embedded", lambda e, n: None), \
                    mock.patch.object(tick.agent, "ask", lambda *a, **k: asked.append(a)):
                tick.process_images(cfg, datetime(2026, 10, 14, 21, 0, tzinfo=timezone.utc), [])
            cap = ledger.snapshot(cfg.ledger_path)["prompts"][0]["captures"][0]
            self.assertEqual((cap["describe"], asked), ("pending", []))


class SessionAudioLabelTest(unittest.TestCase):
    """A recording sent on the day after a session entry's date: the writer labels it with its date, and the tick,
    filling in the transcript, keeps that label though the capture is filed on the newest phase's prompt (whose own
    date is the later day)."""

    STEM = "2026-10-15-fix-life-s01"
    BASE = {"stem": STEM, "kind": "fix-life", "program": "fix-life", "run": "r1", "session": 1, "session_count": 3,
            "session_title": "Vision", "title": "Fix life"}

    def test_the_label_keeps_its_date(self):
        from journal import ledger
        with tempfile.TemporaryDirectory() as d:
            cfg = make_cfg(Path(d), NEUTRAL)
            cfg.audio["command"] = "true {file}"
            t1 = datetime(2026, 10, 15, 6, 0, tzinfo=IST)
            t2 = datetime(2026, 10, 16, 6, 10, tzinfo=IST)
            one = {**self.BASE, "local_date": "2026-10-15", "phase": 1, "phase_title": "One"}
            two = {**self.BASE, "local_date": "2026-10-16", "phase": 2, "phase_title": "Two"}
            path = entries.record_reply(cfg.root, one, "one", t1, "text", names=NEUTRAL)
            entries.record_reply(cfg.root, two, "two", t2, "text", names=NEUTRAL)
            with ledger.locked(cfg.ledger_path) as data:
                data["prompts"] += [{"id": "r1-s01-p1", **one, "entry_path": str(path),
                                     "captures": [{"at": t1.isoformat()}]},
                                    {"id": "r1-s01-p2", **two, "entry_path": str(path),
                                     "captures": [{"at": t2.isoformat()}]}]
            w = conversations._Writer(cfg, {}, write=True, annotate=False, via="conversation")
            at = datetime(2026, 10, 16, 9, 0, tzinfo=IST)
            (cfg.root / f"{self.STEM}.ogg").write_bytes(b"ogg")
            entries.append_conversation(path, conversations.his_block(
                cfg, at, audio.TRANSCRIBING, "audio", f"{self.STEM}.ogg", {}, w._entry_date(str(path), at), key="900"),
                NEUTRAL)
            w._pending_transcript(conversations.Event(at, 5, "his", key="900", mid="900", source="audio"),
                                  str(path), f"{self.STEM}.ogg", {})
            label = lambda: [ln for ln in path.read_text().splitlines() if ln.startswith("**") and "audio file" in ln]  # noqa: E731
            written = label()
            self.assertIn("Fri 16 Oct 09:00", written[0])
            with mock.patch.object(audio, "transcribe", lambda c, p: ("the words", "")), \
                    mock.patch.object(audio, "transcription_enabled", lambda c: True), \
                    mock.patch.object(audio, "probe", lambda c, p: {}):
                tick.process_audio(cfg, datetime(2026, 10, 16, 10, 0, tzinfo=IST), [])
            self.assertIn("the words", path.read_text())
            self.assertEqual(label(), written)


class UnusableQuietTest(ReceiveBase):
    """An unusable ``conversations.quiet_minutes`` (not a number, infinite, too large for a date) must not make the
    hook's window check raise: the failure path it raised into ignores the window and captured the message."""

    def off_record_in_window(self, value):
        from journal import ledger
        conversations.note_off(self.cfg, "690", IMAGES_NOW - timedelta(minutes=2), thread_start=True)
        self.cfg.conversations["quiet_minutes"] = value
        out = self.recv("the lake", reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertTrue(out.text.startswith("[Off the record"), out.text[:80])
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), self.prompt["id"])
        self.assertEqual(p.get("captures") or [], [])
        self.assertEqual(self.kept(), [])

    def test_infinite(self):
        self.off_record_in_window(float("inf"))

    def test_too_large_for_a_date(self):
        self.off_record_in_window(1e300)

    def test_not_a_number(self):
        self.off_record_in_window("soon")

    def test_nan(self):
        self.off_record_in_window(float("nan"))

    def test_the_value_is_read_safely(self):
        from journal import config
        for value, want in ((float("inf"), config.QUIET_MAX), (1e300, config.QUIET_MAX), ("soon", 45.0),
                            (float("nan"), 45.0), (-3, 45.0), (30, 30.0), (config.QUIET_MAX, config.QUIET_MAX)):
            with self.subTest(value=value):
                self.cfg.conversations["quiet_minutes"] = value
                self.assertEqual(config.quiet_minutes(self.cfg), want)
                conversations.quiet(self.cfg) + IMAGES_NOW  # representable, and no date arithmetic overflows
                bad = any("quiet_minutes" in p for p in config.problems(self.cfg))
                self.assertEqual(bad, value not in (30, config.QUIET_MAX))

    def test_an_integer_too_big_for_a_float_is_the_cap_and_doctor_names_it(self):
        from journal import config, status
        self.cfg.hermes["state_db"] = str(Path(self.tmp.name) / "state.db")
        for value, want in ((10 ** 400, config.QUIET_MAX), (-10 ** 400, 45.0)):
            with self.subTest(value=value):
                self.cfg.conversations["quiet_minutes"] = value
                self.assertEqual(config.quiet_minutes(self.cfg), want)
                self.assertTrue([p for p in config.problems(self.cfg) if "quiet_minutes" in p and "number" not in p])
                row = next(r for r in status.doctor(self.cfg) if r[0] == "conversations")
                self.assertIn("is not usable", row[2])

    def test_the_window_check_fails_closed_when_its_fallback_cannot_run(self):
        with mock.patch.object(privacy, "view", lambda *a, **k: privacy.Closed("down")), \
                mock.patch.object(conversations, "snapshot", mock.Mock(side_effect=OSError("unreadable"))):
            self.assertTrue(privacy.window_open(self.cfg, IMAGES_NOW))


class McpStartWindowTest(McpCase):
    """journal_start is a model acting for him, so it holds while a bare-/x window is open, as the automatic sends do.
    The MCP face does not read the chat (its fence), so the spawned send asks the privacy pass and holds itself, and
    the tool's answer says so."""

    def test_the_send_is_spawned_to_hold_inside_a_window(self):
        got = mcp_handlers.start(self.cfg, "evening", spawn=self.spawn)
        self.assertEqual(self.spawned, [["send", "evening", "--hold-off-record"]])
        self.assertIn("off the record", got["note"])

    def test_the_cli_takes_the_flag(self):
        from journal import cli
        args = cli.build_parser().parse_args(["send", "evening", "--hold-off-record"])
        self.assertTrue(args.hold_off_record)
        self.assertFalse(cli.build_parser().parse_args(["send", "evening"]).hold_off_record)


class HeldSendTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name), NEUTRAL)
        self.sent = []

    def send(self, **kw):
        def fake(cfg, now_local, log, only=None, dry_run=False, hold_off_record=False):
            self.sent.append(only)
        with mock.patch.object(tick, "send_due", fake):
            return tick.run(self.cfg, only="evening", **kw)

    def test_inside_a_window_nothing_is_sent_and_the_log_says_why(self):
        conversations.note_off(self.cfg, None, datetime.now(timezone.utc) - timedelta(minutes=1), thread_start=True)
        code, log = self.send(hold_off_record=True)
        self.assertEqual((code, self.sent), (0, []))
        self.assertTrue(any("held" in line and "off the record" in line for line in log), log)

    def test_a_pass_that_cannot_run_holds_it(self):
        with mock.patch.object(privacy, "view", lambda *a, **k: privacy.Closed("down")):
            _code, _log = self.send(hold_off_record=True)
        self.assertEqual(self.sent, [])

    def test_outside_a_window_it_goes(self):
        self.send(hold_off_record=True)
        self.assertEqual(self.sent, ["evening"])

    def test_a_window_opened_while_the_opener_is_built_holds_the_send(self):
        """The opener's model call can take minutes: a bare /x sent meanwhile is checked again before delivery."""
        sent = []

        def opener(cfg, practice, now_local, pid, log):
            conversations.note_off(cfg, None, datetime.now(timezone.utc) - timedelta(seconds=5), thread_start=True)
            return "an opener", "model", [], []

        with mock.patch.object(tick, "_opener", opener), \
                mock.patch.object(tick, "send_prompt", lambda *a, **k: sent.append(a) or True):
            code, log = tick.run(self.cfg, only="evening", hold_off_record=True)
            self.assertEqual((code, sent), (0, []))
            self.assertTrue(any("held" in line and "off the record" in line for line in log), log)
            sent.clear()
            conversations.note_off(self.cfg, None, datetime.now(timezone.utc) - timedelta(hours=9), thread_start=True)
            tick.run(self.cfg, only="evening")  # his own send: not held, though a window is open
            self.assertEqual(len(sent), 1)

    def test_his_own_send_is_not_held(self):
        conversations.note_off(self.cfg, None, datetime.now(timezone.utc) - timedelta(minutes=1), thread_start=True)
        self.send()
        self.assertEqual(self.sent, ["evening"])
