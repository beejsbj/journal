"""Audio capture: voice notes (transcribed by Hermes), audio files and audio documents (kept as sent)."""
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from helpers import CHAT, CleanEnv, add_prompt, write_config

from journal import audio, entries, hookstate, ledger
from journal.config import load
from journal.hermes import hooks


class AudioCaptureTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        conf = write_config(t / "journal.json", {
            "root": str(t / "Journal"), "state_dir": str(t / "state"), "timezone": "Asia/Kolkata",
            "telegram": {"chat_id": CHAT}, "user": {"name": "Theo"}, "companion": {"name": "Iris"}})
        os.environ["JOURNAL_CONFIG"] = str(conf)
        self.cfg = load()
        self.cfg.root.mkdir(parents=True)
        self.cache = t / "cache"
        self.cache.mkdir()
        add_prompt(self.cfg, kind="evening", sent=datetime.now(timezone.utc) - timedelta(hours=1), message_id="300")

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def media(self, name: str, data: bytes = b"\x00audio") -> str:
        p = self.cache / name
        p.write_bytes(data)
        return str(p)

    def event(self, kind, media, types, text="", reply_to=None, raw=None, mid="9"):
        return SimpleNamespace(text=text, message_type=SimpleNamespace(name=kind), message_id=mid,
                               reply_to_message_id=reply_to, media_urls=media, media_types=types, raw_message=raw,
                               source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id=CHAT))

    def entry(self) -> str:
        return (self.cfg.root / "2026-10-14-evening.md").read_text()

    def test_voice_note_waits_for_the_transcript(self):
        out = hooks.on_dispatch(event=self.event("VOICE", [self.media("aud_1.ogg")], ["audio/ogg"], reply_to="300"))
        self.assertTrue(out["text"].startswith("[Journal reply"))
        self.assertFalse((self.cfg.root / "2026-10-14-evening.md").exists())  # nothing until STT
        self.assertTrue((self.cfg.root / "2026-10-14-evening.ogg").exists())
        hooks.on_llm_call(user_message='"long day, good swim"\n\n' + out["text"], platform="telegram")
        body = self.entry()
        self.assertIn("· voice transcript\n\n![[2026-10-14-evening.ogg]]\n\nlong day, good swim", body)

    def test_audio_file_recorded_at_once_with_its_original_extension(self):
        raw = SimpleNamespace(audio=SimpleNamespace(file_name="Recording 12.M4A"), document=None, voice=None)
        out = hooks.on_dispatch(event=self.event("AUDIO", [self.media("aud_x.mp3", b"m4a-bytes")], ["audio/mp3"],
                                                 text="from the walk", reply_to="300", raw=raw))
        self.assertTrue(out["text"].startswith("[Journal reply"))
        saved = self.cfg.root / "2026-10-14-evening.m4a"
        self.assertEqual(saved.read_bytes(), b"m4a-bytes")  # bytes kept exactly as they arrived
        body = self.entry()
        self.assertIn('· audio file "Recording 12.M4A"\n\n![[2026-10-14-evening.m4a]]\n\nfrom the walk', body)
        cap = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-evening")["captures"][0]
        self.assertEqual((cap["source"], cap["platform_message_id"], cap["audio"]), ("audio", "9", "2026-10-14-evening.m4a"))
        self.assertEqual(hookstate.snapshot(self.cfg)["voice"], {})  # nothing waits for a transcript

    def test_audio_document_with_audio_mime(self):
        raw = SimpleNamespace(audio=None, voice=None, document=SimpleNamespace(file_name="session.flac"))
        hooks.on_dispatch(event=self.event("DOCUMENT", [self.media("doc_abc_session.flac")], ["audio/flac"],
                                           reply_to="300", raw=raw))
        body = self.entry()
        self.assertIn("![[2026-10-14-evening.flac]]", body)
        self.assertIn(audio.NOT_TRANSCRIBED, body)

    def test_extension_from_mime_when_nothing_else_says(self):
        hooks.on_dispatch(event=self.event("DOCUMENT", [self.media("blob")], ["audio/wav"], reply_to="300"))
        self.assertTrue(any(p.suffix == ".wav" for p in self.cfg.root.iterdir()), list(self.cfg.root.iterdir()))

    def test_non_audio_document_is_not_audio(self):
        out = hooks.on_dispatch(event=self.event("DOCUMENT", [self.media("notes.pdf")], ["application/pdf"],
                                                 text="see attached", reply_to="300"))
        self.assertTrue(out["text"].startswith("[Journal reply"))  # captured as a text reply
        self.assertNotIn("![[", self.entry())

    def test_second_recording_gets_a_numbered_name(self):
        for n in range(2):
            hooks.on_dispatch(event=self.event("AUDIO", [self.media(f"a{n}.ogg")], ["audio/ogg"], reply_to="300",
                                               mid=str(n)))
        names = sorted(p.name for p in self.cfg.root.iterdir() if p.suffix == ".ogg")
        self.assertEqual(names, ["2026-10-14-evening-2.ogg", "2026-10-14-evening.ogg"])
        self.assertEqual(self.entry().count("· audio file"), 2)

    def test_j_audio_file_is_its_own_note(self):
        hooks.on_dispatch(event=self.event("AUDIO", [self.media("x.opus")], ["audio/opus"], text="/j field recording"))
        note = next(self.cfg.root.glob("*-j.md"))
        self.assertIn("![[" + note.stem + ".opus]]", note.read_text())
        self.assertIn("field recording", entries.raw_entry(note.read_text()))

    def test_two_pending_voice_notes_each_find_their_transcript(self):
        add_prompt(self.cfg, kind="morning", sent=datetime.now(timezone.utc) - timedelta(minutes=30), message_id="301")
        a = hooks.on_dispatch(event=self.event("VOICE", [self.media("v1.ogg")], ["audio/ogg"], reply_to="300", mid="1"))
        b = hooks.on_dispatch(event=self.event("VOICE", [self.media("v2.ogg")], ["audio/ogg"], reply_to="301", mid="2"))
        hooks.on_llm_call(user_message="morning words\n\n" + b["text"], platform="telegram")
        hooks.on_llm_call(user_message="evening words\n\n" + a["text"], platform="telegram")
        self.assertIn("evening words", self.entry())
        self.assertIn("morning words", (self.cfg.root / "2026-10-14-morning.md").read_text())

    def test_voice_from_another_chat_or_outside_a_prompt_is_ignored(self):
        e = self.event("VOICE", [self.media("v.ogg")], ["audio/ogg"])
        e.source.chat_id = "123"
        self.assertIsNone(hooks.on_dispatch(event=e))
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].clear()
        self.assertIsNone(hooks.on_dispatch(event=self.event("AUDIO", [self.media("v.ogg")], ["audio/ogg"])))
        self.assertEqual(list(self.cfg.root.iterdir()), [])

    def stashed_after_chat_audio(self, adapter):
        t = Path(self.tmp.name)
        conf = write_config(t / "journal.json", {
            "root": str(t / "Journal"), "state_dir": str(t / "state"), "timezone": "Asia/Kolkata",
            "telegram": {"chat_id": CHAT}, "hermes": {"state_db": str(t / "state.db")}, "adapter": adapter})
        with ledger.locked(load().ledger_path) as data:
            data["prompts"].clear()  # a voice note in the chat, answering no prompt
        hooks.on_dispatch(event=self.event("VOICE", [self.media("chat.ogg")], ["audio/ogg"], mid="77"))
        stash = t / "state" / "audio-stash"
        return stash.is_dir() and any(stash.iterdir())

    def test_chat_audio_is_stashed_for_the_writer_only_when_there_is_a_chat_to_read(self):
        self.assertTrue(self.stashed_after_chat_audio("auto"))

    def test_with_no_transcript_chat_audio_is_not_stashed(self):
        self.assertFalse(self.stashed_after_chat_audio("none"))  # nothing would ever write or prune it

    def test_x_holds_when_the_attachment_cannot_be_read(self):
        """A media/metadata bug in the translation must not turn an /x message into an unmarked pass-through."""
        raw = SimpleNamespace(voice=SimpleNamespace(duration=float("inf")), audio=None, document=None)
        ev = self.event("AUDIO", [self.media("aud_x.mp3")], ["audio/mpeg"], text="/x my secret", raw=raw, mid="55")
        with self.assertRaises(OverflowError):
            audio.event_meta(ev)  # (the reading really does raise)
        out = hooks.on_dispatch(event=ev)
        self.assertEqual(out["action"], "rewrite")
        self.assertTrue(out["text"].startswith("[Off the record"))
        self.assertIn("my secret", out["text"])
        self.assertFalse((self.cfg.root / "2026-10-14-evening.md").exists())

    def test_a_reply_still_saves_when_only_its_metadata_cannot_be_read(self):
        raw = SimpleNamespace(voice=SimpleNamespace(duration=float("inf")), audio=None, document=None)
        out = hooks.on_dispatch(event=self.event("AUDIO", [self.media("aud_x.mp3")], ["audio/mpeg"], text="from the walk",
                                                 reply_to="300", raw=raw))
        self.assertTrue(out["text"].startswith("[Journal reply"))
        self.assertIn("from the walk", self.entry())


class AudioFileTranscriptionTest(CleanEnv):
    """Audio files the gateway won't transcribe: the tick transcribes them and fills in the block."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.ffprobe = t / "ffprobe"
        self.ffprobe.write_text('#!/bin/sh\necho \'{"format": {"duration": "731.2", "tags": '
                                '{"creation_time": "2026-10-14T15:40:00.000000Z"}}}\'\n')
        self.ffprobe.chmod(0o755)
        self.conf = {"root": str(t / "Journal"), "state_dir": str(t / "state"), "timezone": "Asia/Kolkata",
                     "telegram": {"chat_id": CHAT}, "user": {"name": "Theo"}, "companion": {"name": "Iris"},
                     "audio": {"command": ["/bin/sh", "-c", "echo 'walked by the lake'"],
                               "ffprobe": str(self.ffprobe)}}
        self.load()
        self.cfg.root.mkdir(parents=True)
        self.cache = t / "cache"
        self.cache.mkdir()
        add_prompt(self.cfg, kind="evening", sent=datetime.now(timezone.utc) - timedelta(hours=1), message_id="300")

    def load(self):
        os.environ["JOURNAL_CONFIG"] = str(write_config(Path(self.tmp.name) / "journal.json", self.conf))
        self.cfg = load()

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def send(self, text="", raw=None, mid="9", kind="AUDIO"):
        media = self.cache / f"a{mid}.m4a"
        media.write_bytes(b"m4a")
        raw = raw or SimpleNamespace(audio=SimpleNamespace(file_name="walk.m4a", duration=724), document=None,
                                     voice=None, forward_origin=None)
        ev = SimpleNamespace(text=text, message_type=SimpleNamespace(name=kind), message_id=mid,
                             reply_to_message_id="300", media_urls=[str(media)], media_types=["audio/mp4"],
                             raw_message=raw, source=SimpleNamespace(platform=SimpleNamespace(value="telegram"),
                                                                     chat_id=CHAT))
        return hooks.on_dispatch(event=ev)

    def entry(self) -> str:
        return (self.cfg.root / "2026-10-14-evening.md").read_text()

    def capture(self):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-evening")["captures"][0]

    def test_transcribed_by_the_tick_with_metadata_in_the_heading(self):
        from journal import tick
        self.send()
        self.assertIn('· audio file "walk.m4a" · 12:04\n\n![[2026-10-14-evening.m4a]]\n\n'
                      + audio.TRANSCRIBING, self.entry())
        self.assertEqual(self.capture()["transcript"], "pending")
        log = []
        self.cfg.annotate_after_minutes = 0
        tick.annotate(self.cfg, datetime.now(timezone.utc), log)  # waits for the transcript (no agent call)
        tick.process_audio(self.cfg, datetime.now(timezone.utc), log)
        body = self.entry()
        self.assertIn('· audio file "walk.m4a" · 12:04 · recorded 14 Oct 21:10\n\n![[2026-10-14-evening.m4a]]\n\n'
                      'walked by the lake\n\n## Iris', body)
        self.assertNotIn("pending", body)
        c = self.capture()
        self.assertEqual((c["transcript"], c["meta"]["duration"]), ("done", 724))  # Telegram's duration wins
        self.assertEqual(log, ["transcribed 2026-10-14-evening.m4a for 2026-10-14-evening"])
        tick.process_audio(self.cfg, datetime.now(timezone.utc), log)  # done: nothing more
        self.assertEqual(len(log), 1)

    def test_caption_stays_after_the_transcript(self):
        from journal import tick
        self.send(text="from the walk")
        tick.process_audio(self.cfg, datetime.now(timezone.utc), [])
        self.assertIn("![[2026-10-14-evening.m4a]]\n\nwalked by the lake\n\nfrom the walk\n\n", self.entry())

    def around_the_recording(self, caption: str):
        """Fill a recording's block between two text replies of his; return what stood before the block, what stood
        after the engine-owned lines, and the block as filled."""
        from journal import capture, tick
        first = "first words,  with spacing   and\ttabs — ünïcode\n\n## Not a heading of the engine's"
        capture.capture(self.cfg, CHAT, "300", first, datetime.now(timezone.utc) - timedelta(minutes=50), "text", "7")
        self.send(text=caption)
        capture.capture(self.cfg, CHAT, "300", "words after the recording", datetime.now(timezone.utc), "text", "8")
        before = self.entry()
        embed = "![[2026-10-14-evening.m4a]]"
        pre = before[:before.rfind("\n### ", 0, before.index(embed)) + 1]   # up to the recording's heading line
        rest = before.split(embed + "\n\n", 1)[1]
        post = rest[len(audio.TRANSCRIBING):] if rest.startswith(audio.TRANSCRIBING) else rest
        self.assertIn("first words,  with spacing", pre)
        self.assertIn("words after the recording", post)
        tick.process_audio(self.cfg, datetime.now(timezone.utc), [])
        after = self.entry()
        self.assertNotEqual(after, before)
        self.assertTrue(after.startswith(pre), "his words before the recording moved")
        self.assertTrue(after.endswith(post), "his words after the recording moved")
        middle = after[len(pre):len(after) - len(post)]
        self.assertTrue(middle.startswith("### "))
        self.assertEqual(middle.count("\n### "), 0)  # only the recording's own block changed
        return middle

    def test_his_blocks_around_the_recording_stay_byte_for_byte(self):
        """README *Guarantees*: his words are append-only for the engine. Filling the placeholder
        (``tick.process_audio`` -> ``entries.fill_audio_block``) changes the recording's heading line and the
        placeholder line, and nothing else: his text before it and after it stay byte for byte."""
        middle = self.around_the_recording("")
        self.assertTrue(middle.rstrip().endswith("walked by the lake"), middle)

    def test_his_caption_under_the_recording_stays_byte_for_byte(self):
        """The same, when he sent a caption: the transcript goes in above it and the caption is carried over."""
        middle = self.around_the_recording("a caption,   as typed\n- with a list")
        self.assertTrue(middle.endswith("walked by the lake\n\n"), middle)
        self.assertIn("a caption,   as typed\n- with a list", self.entry())

    def test_a_recording_embed_in_his_own_words_is_never_filled(self):
        """The same guarantee from the other side: when the engine's block is gone and only his words carry the
        embed (he edited the entry), the tick finds no block to fill and writes nothing."""
        from journal import tick
        self.send()
        body = self.entry()
        heading = body[body.rfind("\n### ", 0, body.index("![[")) + 1:body.index("\n", body.index("· audio file"))]
        edited = body.replace(heading, "### 12:00 IST · text")  # his now: a text block that holds the embed line
        (self.cfg.root / "2026-10-14-evening.md").write_text(edited, encoding="utf-8")
        log = []
        tick.process_audio(self.cfg, datetime.now(timezone.utc), log)
        self.assertEqual(self.entry(), edited)
        self.assertTrue(any("nothing written" in line for line in log), log)

    def test_empty_transcripts_use_attempts_then_say_not_transcribed(self):
        from journal import tick
        self.conf["audio"]["command"] = ["/bin/sh", "-c", "true"]  # runs fine, hears nothing
        self.load()
        self.send()
        for attempt in range(1, tick.AUDIO_ATTEMPTS + 1):
            log = []
            tick.process_audio(self.cfg, datetime.now(timezone.utc), log)
        self.assertIn("not transcribed (empty transcript)", log[-1])
        body = self.entry()
        self.assertEqual(body.count(audio.NOT_TRANSCRIBED), 1)
        self.assertNotIn(audio.TRANSCRIBING, body)
        self.assertEqual(self.capture()["transcript"], "none")

    def test_a_failing_backend_defers_without_using_attempts(self):
        from journal import tick
        self.conf["audio"]["command"] = ["/bin/sh", "-c", "exit 3"]
        self.load()
        self.send()
        t0 = datetime.now(timezone.utc)
        log = []
        tick.process_audio(self.cfg, t0, log)
        self.assertEqual(log, ["2026-10-14-evening: transcription deferred, speech-to-text unreachable (exit 3)"])
        c = self.capture()
        self.assertEqual((c["transcript"], c.get("transcribe_attempts", 0), c["deferred_since"]),
                         ("pending", 0, t0.isoformat()))
        tick.process_audio(self.cfg, t0 + timedelta(minutes=10), log)  # backs off
        self.assertEqual(len(log), 1)
        for n in range(1, tick.AUDIO_ATTEMPTS + 3):  # outages never use the attempts
            tick.process_audio(self.cfg, t0 + timedelta(minutes=31 * n), [])
        c = self.capture()
        self.assertEqual((c["transcript"], c.get("transcribe_attempts", 0)), ("pending", 0))
        self.assertIn(audio.TRANSCRIBING, self.entry())
        self.conf["audio"]["command"] = ["/bin/sh", "-c", "echo 'walked by the lake'"]  # the backend is back
        self.load()
        tick.process_audio(self.cfg, t0 + timedelta(hours=4), log)
        c = self.capture()
        self.assertEqual((c["transcript"], c["transcribe_attempts"]), ("done", 1))
        self.assertNotIn("deferred_since", c)
        self.assertIn("walked by the lake", self.entry())

    def test_one_alert_after_six_hours_and_given_up_after_seven_days(self):
        from journal import tick
        self.conf["audio"]["command"] = ["/bin/sh", "-c", "exit 3"]
        self.load()
        self.send()
        t0 = datetime.now(timezone.utc)
        tick.process_audio(self.cfg, t0, [])
        tick.process_audio(self.cfg, t0 + timedelta(hours=5, minutes=40), [])
        with self.assertRaises(tick.Alert) as cm:
            tick.process_audio(self.cfg, t0 + timedelta(hours=6, minutes=20), [])
        self.assertIn("is still not transcribed: speech-to-text has been unreachable since", str(cm.exception))
        tick.process_audio(self.cfg, t0 + timedelta(hours=7), [])  # no second alert
        with self.assertRaises(tick.Alert) as cm:
            tick.process_audio(self.cfg, t0 + timedelta(days=7, minutes=1), [])
        self.assertIn("was not transcribed: speech-to-text was unreachable for 7 days", str(cm.exception))
        self.assertEqual(self.capture()["transcript"], "none")
        self.assertEqual(self.entry().count(audio.NOT_TRANSCRIBED), 1)

    def test_which_failures_are_outages(self):
        from journal import audio
        self.assertTrue(all(audio.is_outage(w) for w in ("exit 3", "TimeoutExpired", "RuntimeError: boom",
                                                         "No STT provider is reachable")))
        self.assertFalse(any(audio.is_outage(w) for w in ("ok", "unavailable", "unavailable (ImportError)",
                                                          "empty transcript")))

    def test_transcription_off_keeps_one_placeholder_and_still_probes(self):
        from journal import tick
        self.conf["audio"]["transcribe"] = False
        self.load()
        self.send()
        tick.process_audio(self.cfg, datetime.now(timezone.utc), [])
        body = self.entry()
        self.assertEqual(body.count(audio.NOT_TRANSCRIBED), 1)
        self.assertIn("· recorded 14 Oct 21:10", body)

    def test_voice_heading_has_duration_and_forward_date(self):
        raw = SimpleNamespace(voice=SimpleNamespace(duration=timedelta(seconds=151)), audio=None, document=None,
                              forward_origin=SimpleNamespace(date=datetime(2026, 10, 14, 15, 40, tzinfo=timezone.utc)))
        out = self.send(raw=raw, kind="VOICE")
        hooks.on_llm_call(user_message='"a forwarded thought"\n\n' + out["text"], platform="telegram")
        self.assertIn("· voice transcript · 2:31 · forwarded from 14 Oct 21:10\n\n![[2026-10-14-evening.m4a]]\n\n"
                      "a forwarded thought", self.entry())


class LabelTest(unittest.TestCase):
    def test_labels(self):
        from journal import audio
        self.assertEqual(audio.label("text", {"duration": 3}), "text")
        self.assertEqual(audio.label("voice", {}), "voice transcript")
        self.assertEqual(audio.label("audio", {"file_name": "x.wav", "duration": 3729}), 'audio file "x.wav" · 1:02:09')
        self.assertEqual(audio.clock(5), "0:05")
