"""Conversation threads: the chat itself, both sides, saved into entries by code (not as a chat log)."""
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import CHAT, CleanEnv, add_prompt, make_cfg, write_config
from test_sweep import FakeSessions, redirected

from journal import capture, context, conversations, entries, ledger, mirror, privacy, reflect, sweep, tick
from journal.config import load
from journal.hermes import commands, hooks
from journal.validate import norm

T0 = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)  # 19:30 IST


def m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class _NearT0(datetime):
    """``privacy.view`` recomputes the last 48 hours from the real clock, so a case dated around ``T0`` that asks a
    reader without a ``now`` (``context.companion_reply_after``) stopped seeing its rows once the calendar passed
    T0 + 48 h (2026-10-17). The pass is pinned two hours after T0 instead."""

    @classmethod
    def now(cls, tz=None):
        at = T0 + timedelta(hours=2)
        return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)


def near_t0():
    privacy._CACHE.clear()
    return mock.patch.object(privacy, "datetime", _NearT0)


def off_at(cfg, t: datetime, now: datetime | None = None) -> bool:
    """What every reader is told: is this moment inside an off-the-record window?"""
    privacy._CACHE.clear()
    return privacy.view(cfg, now or t).at(t)


def conv(body: str) -> str:
    """The conversation section as it reads (without the engine's hidden key lines)."""
    return entries.ENGINE_MARK.sub("", entries.conversation(body)).strip()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = make_cfg(t)
        self.db = FakeSessions(t / "state.db")
        self.cfg.hermes["state_db"] = str(self.db.path)
        conversations.run(self.cfg, T0 - timedelta(hours=1), [])  # first run: the cursor starts here

    def tearDown(self):
        self.tmp.cleanup()

    def his(self, text, at, mid, **kw):
        self.db.add(text, at, mid, **kw)

    def hers(self, text, at, tool_calls=None, display_kind=None):
        self.db.add(text, at, role="assistant", display_kind=display_kind)
        if tool_calls:
            self.db.sql("update messages set tool_calls = ? where id = (select max(id) from messages)", tool_calls)

    def tick(self, at):
        log = []
        conversations.run(self.cfg, at, log)
        return log

    def note(self, n=1):
        return self.cfg.root / (f"2026-10-15-j.md" if n == 1 else f"2026-10-15-j-{n}.md")


class ThreadTest(Base):
    def test_a_chat_becomes_an_auto_note_not_a_chat_log(self):
        self.his("thinking about the bank call tomorrow", m(0), "500")
        self.hers("What time is it booked for?", m(1))
        self.his("6:30, and I keep putting off the paperwork", m(4), "501")
        self.hers("Want to do the paperwork together tonight?\n\nTen minutes, no more.", m(5))
        log = self.tick(m(10))
        self.assertEqual(log, ["conversation → new note 2026-10-15-j.md: +2 from Theo, +2 from Iris"])
        body = self.note().read_text()
        self.assertIn("kind: j\n", body)
        self.assertIn("capture: auto\n", body)
        self.assertIn("_Freeform — saved from the conversation._", body)
        self.assertEqual(entries.raw_entry(body), "### 19:30 IST · text\n\nthinking about the bank call tomorrow")
        section = conv(body)
        self.assertEqual(section, "> [!quote]- Iris · 19:31\n> What time is it booked for?\n\n"
                               "**19:34** — 6:30, and I keep putting off the paperwork\n\n"
                               "> [!quote]- Iris · 19:35\n> Want to do the paperwork together tonight?\n>\n"
                               "> Ten minutes, no more.")
        self.assertNotIn("annotations", body)  # an auto note isn't annotated: no pending section
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-j")
        self.assertEqual((p["capture"], p["annotate"], p["captures"][0]["rule"]), ("auto", False, "auto"))

    def test_incremental_across_ticks_without_duplicates(self):
        self.his("first", m(0), "510")
        self.hers("reply one", m(1))
        self.tick(m(4))
        self.his("second, a bit later", m(10), "511")
        self.hers("reply two", m(10.5))  # not settled at the next tick: waits
        self.tick(m(12.2))
        body = self.note().read_text()
        self.assertIn("second, a bit later", body)
        self.assertNotIn("reply two", body)
        self.tick(m(14))
        self.tick(m(20))
        body = self.note().read_text()
        self.assertEqual(body.count("reply one"), 1)
        self.assertEqual(body.count("reply two"), 1)
        self.assertEqual(body.count("second, a bit later"), 1)
        with conversations.locked(self.cfg) as st:  # a lost cursor (or a replay) never writes twice
            st["cursor"]["last_id"] = 0
            st["cursor"]["thread"] = None
        self.tick(m(25))
        self.assertEqual(self.note().read_text(), body)
        self.assertFalse(self.note(2).exists())

    def test_quiet_gap_starts_a_new_thread(self):
        self.his("morning thought", m(0), "520")
        self.hers("noted", m(1))
        self.his("much later, another thing", m(50), "521")  # 49 min of silence > 45
        self.hers("tell me", m(51))
        self.tick(m(60))
        self.assertNotIn("another thing", self.note().read_text())
        second = self.note(2).read_text()
        self.assertIn("much later, another thing", entries.raw_entry(second))
        self.assertIn("> tell me", second)

    def test_a_prompt_send_ends_the_thread_and_its_reply_takes_the_rest(self):
        self.his("idle chat", m(0), "530")
        self.hers("mm", m(1))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(3), message_id="600")
        capture.capture(self.cfg, CHAT, "600", "the day was long", m(5), "text", "531")  # the hook saved it
        self.his('[Replying to: "🌻 Evening journal"]\n\n[Journal reply — saved …]\n\nthe day was long', m(5), "531")
        self.hers("Long how?", m(6))
        self.his("the bank, mostly", m(8), "532")
        self.hers("Ah, the bank.", m(9))
        self.tick(m(15))
        evening = (self.cfg.root / "2026-10-15-evening.md").read_text()
        self.assertEqual(entries.raw_entry(evening).count("the day was long"), 1)
        section = conv(evening)
        self.assertTrue(section.startswith("> [!quote]- Iris · 19:36\n> Long how?"), section)
        self.assertIn("**19:38** — the bank, mostly", section)
        self.assertIn("> Ah, the bank.", section)
        # the conversation sits between his words and the annotations
        self.assertLess(evening.index("## Theo"), evening.index("## Conversation"))
        self.assertLess(evening.index("## Conversation"), evening.index("## Iris's annotations"))
        self.assertNotIn("bank", self.note().read_text())  # the earlier chat kept its own note

    def test_a_j_note_takes_the_rest_of_its_thread(self):
        info = capture.capture_note(self.cfg, "can't sleep", m(0), "text", "540")
        self.his(capture.note_context(self.cfg, info) + "can't sleep", m(0), "540")
        self.hers("What's keeping you up?", m(1))
        self.his("the flight", m(3), "541")
        self.tick(m(8))
        body = self.note().read_text()
        self.assertIn("capture: /j\n", body)
        self.assertEqual(entries.raw_entry(body).count("can't sleep"), 1)
        self.assertIn("> What's keeping you up?", body)
        self.assertIn("**19:33** — the flight", body)
        self.assertFalse(self.note(2).exists())

    def test_a_direct_reply_mid_thread_moves_the_rest_there(self):
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=m(-300), message_id="610")
        self.his("random chat", m(0), "550")
        self.hers("sure", m(1))
        capture.capture(self.cfg, CHAT, "610", "slept fine actually", m(3), "text", "551")
        self.his("[Journal reply — saved …]\n\nslept fine actually", m(3), "551")
        self.hers("Good to hear.", m(4))
        self.his("and dreamt of rain", m(5), "552")
        self.tick(m(10))
        note = self.note().read_text()
        morning = (self.cfg.root / "2026-10-15-morning.md").read_text()
        self.assertIn("> sure", note)
        self.assertNotIn("Good to hear", note)
        self.assertIn("> Good to hear.", morning)
        self.assertIn("— and dreamt of rain", morning)

    def test_what_is_never_saved(self):
        self.his("[Cron delivery: Something] text", m(-1), None)  # (a cron delivery also ends a thread)
        self.his("hello", m(0), "560")
        self.hers("hi!", m(0.5))
        self.his("/model", m(1), "561")
        self.hers("Model switched.", m(1.2))  # whatever answers a command is not saved
        self.his("[IMPORTANT: Background process done]", m(1.5), None)
        self.hers("The process finished.", m(1.6))
        self.his(mirror.frame("Iris's annotations on x", "- a bullet"), m(1.7), None)
        self.his("still here", m(2), "562")
        self.hers("", m(2.1), tool_calls='[{"id": "t"}]')  # a tool step without text
        self.hers("Let me look that up.", m(2.2), tool_calls='[{"id": "t2"}]')  # interim narration of the work
        self.db.add("tool output", m(2.3), role="tool")
        self.hers("Your request was not processed. Send it again if you still want me to carry it out.", m(2.4),
                  display_kind="failed_turn")
        self.hers("Found it.", m(2.5))
        self.his("[System note: The previous turn was interrupted]", m(2.6), None)
        self.tick(m(10))
        body = self.note().read_text()
        for gone in ("/model", "Model switched", "Background", "process finished", "a bullet", "tool output",
                     "not processed", "Cron", "System note", "Let me look that up."):
            self.assertNotIn(gone, body)
        for kept in ("hello", "hi!", "still here", "Found it."):
            self.assertIn(kept, body)

    def test_only_her_last_text_of_a_turn_is_her_reply(self):
        self.his("can you check the logs", m(0), "595")
        self.hers("echo-probe-do-not-reply", m(1))  # text rows a turn's tools left behind
        self.hers("", m(1.2), tool_calls='[{"id": "t"}]')  # and then the turn went on working
        self.his("never mind that", m(1.3), "597")
        self.hers("a probe again", m(1.4))
        self.hers("Operation interrupted: waiting for model response (30s)", m(1.5))
        self.hers("Checked: all quiet.", m(2))
        self.tick(m(8))
        section = conv(self.note().read_text())
        self.assertNotIn("probe", section)
        self.assertNotIn("Operation interrupted", section)
        self.assertEqual(section, "**19:31** — never mind that\n\n> [!quote]- Iris · 19:32\n> Checked: all quiet.")

    def test_a_reply_waits_while_her_turn_is_still_running(self):
        import time
        self.his("a question", m(0), "596")
        self.hers("first part", m(1))
        self.db.sql("insert into session_turn_leases values ('tg', 'x', ?, ?)", time.time(), time.time() + 3600)
        self.tick(m(5))  # she is mid-turn: held, not written
        self.assertNotIn("first part", self.note().read_text())
        self.hers("the actual answer", m(6))
        self.db.sql("delete from session_turn_leases")
        self.tick(m(10))
        section = conv(self.note().read_text())
        self.assertEqual(section, "> [!quote]- Iris · 19:36\n> the actual answer")

    def test_a_voice_note_in_conversation_keeps_its_audio(self):
        cache = Path(self.tmp.name) / "cache.ogg"
        cache.write_bytes(b"OggS-voice")
        conversations.stash_audio(self.cfg, "571", str(cache), ".ogg", {"duration": 151}, m(2))
        cache.unlink()  # Hermes' cache is gone by now; the stash kept a copy
        self.his("one thing", m(0), "570")
        self.his('"and a voice note about the walk"', m(2), "571")
        self.hers("Lovely walk.", m(3))
        self.tick(m(8))
        body = self.note().read_text()
        self.assertIn("**19:32** · voice transcript · 2:31\n\n![[2026-10-15-j.ogg]]\n\nand a voice note about the walk",
                      body)
        self.assertEqual((self.cfg.root / "2026-10-15-j.ogg").read_bytes(), b"OggS-voice")
        self.assertEqual(conversations.snapshot(self.cfg).get("audio"), {})
        self.assertEqual(list((self.cfg.state_dir / "audio-stash").iterdir()), [])

    def test_the_stash_ages_out_on_a_tick_that_reads_no_chat(self):
        cache = Path(self.tmp.name) / "cache.ogg"
        cache.write_bytes(b"OggS-voice")
        dest = conversations.stash_audio(self.cfg, "571", str(cache), ".ogg", {}, m(0))
        for source in (None, "gone"):  # no transcript at all, then a sessions database that cannot be read
            conversations.stash_audio(self.cfg, "571", str(cache), ".ogg", {}, m(0))
            self.cfg.adapter = "none" if source is None else "auto"
            self.cfg.hermes["state_db"] = None if source is None else str(Path(self.tmp.name) / "gone.db")
            conversations.run(self.cfg, m(0) + timedelta(days=conversations.STASH_DAYS - 1), [])
            self.assertTrue(Path(dest).exists())  # still within the days it is kept
            conversations.run(self.cfg, m(0) + timedelta(days=conversations.STASH_DAYS + 1), [])
            self.assertFalse(Path(dest).exists())
            self.assertEqual(conversations.snapshot(self.cfg).get("audio"), {})

    def test_an_audio_file_in_conversation_is_transcribed_by_the_tick(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-10), message_id="620")
        capture.capture(self.cfg, CHAT, "620", "evening words", m(0), "text", "580")
        self.his("[Journal reply — saved …]\n\nevening words", m(0), "580")
        f = Path(self.tmp.name) / "audio_abc.mp3"
        f.write_bytes(b"mp3")
        self.his(f"[The user sent an audio file attachment: 'walk.m4a'. It is saved at: {f}. Its content is not "
                 "inlined here. If the user's request involves what the audio contains, transcribe it.]", m(2), "581")
        self.cfg.audio["command"] = ["/bin/sh", "-c", "echo 'the walk, transcribed'"]
        self.cfg.audio["ffprobe"] = "/nonexistent"
        self.tick(m(8))
        path = self.cfg.root / "2026-10-15-evening.md"
        self.assertIn('**19:32** · audio file "walk.m4a"\n\n![[2026-10-15-evening.mp3]]\n\n' + conversations.audio.TRANSCRIBING,
                      path.read_text())
        tick.process_audio(self.cfg, m(9), [])
        section = conv(path.read_text())
        self.assertIn('**19:32** · audio file "walk.m4a"\n\n![[2026-10-15-evening.mp3]]\n\nthe walk, transcribed', section)


class AppendOnlyTest(Base):
    """README *Guarantees*: his words are append-only for the engine. The conversation writer is an engine path that
    writes into entries a second time, long after his reply was saved; whatever it adds, a block of his already in the
    entry stays byte for byte."""

    AWKWARD = ("  leading spaces, trailing spaces   \n\ttabbed line, ünïcode — dashes\n"
               "## Conversation\n> a quote-looking line\n**19:99** — a line shaped like a chat turn\n\n\nlast line  ")

    def chat_after(self, start: int):
        self.his("the chat goes on", m(start), "801")
        self.hers("I hear you.", m(start + 1))
        self.his("and on", m(start + 3), "802")
        self.hers("Still here.", m(start + 4))

    def assert_kept(self, before: str, after: str) -> None:
        """Everything of the entry up to the end of his words, as the file held it, is the start of the new file: no
        byte of it moved (``raw_entry`` alone would trim and drop the engine's key lines)."""
        span = entries.words_span(before)  # the parser's own bound of his words, as raw_entry uses
        self.assertIsNotNone(span)
        kept = before[:span[1]]
        self.assertIn(self.cfg.names.words_heading, kept)
        self.assertTrue(after.startswith(kept), "bytes of the entry before the conversation moved")

    def test_a_reply_already_in_an_entry_is_untouched_by_the_conversation_writer(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-10), message_id="700")
        capture.capture(self.cfg, CHAT, "700", self.AWKWARD, m(0), "text", "800")
        self.his("[Journal reply — saved …]\n\n" + self.AWKWARD, m(0), "800")
        path = self.cfg.root / "2026-10-15-evening.md"
        before = path.read_text(encoding="utf-8")
        words = entries.raw_entry(before)
        self.assertIn(self.AWKWARD.rstrip(), words)  # (capture trims the end) the fixture is really in the file, awkward bytes and all
        self.chat_after(1)
        for at in (m(10), m(20)):  # a second tick, after the thread went quiet, writes more of the same chat
            self.tick(at)
        after = path.read_text(encoding="utf-8")
        self.assertIn("> I hear you.", conv(after))  # the writer did run, into this entry
        self.assertEqual(entries.raw_entry(after), words)  # his section, byte for byte
        self.assert_kept(before, after)
        self.assertEqual(after.count(words), 1)
        self.assertLess(after.index(words), after.index(entries.CONVERSATION_MARK))

    def test_a_note_of_his_is_untouched_when_its_chat_continues(self):
        info = capture.capture_note(self.cfg, self.AWKWARD, m(0), "text", "810")
        self.his(capture.note_context(self.cfg, info) + self.AWKWARD, m(0), "810")
        path = Path(info["path"])
        before = path.read_text(encoding="utf-8")
        words = entries.raw_entry(before)
        self.assertIn("last line", words)
        self.chat_after(1)
        self.tick(m(10))
        after = path.read_text(encoding="utf-8")
        self.assertIn("> Still here.", after)
        self.assertEqual(entries.raw_entry(after), words)
        self.assert_kept(before, after)


class RootTest(Base):
    def test_never_writes_outside_the_journal_root(self):
        elsewhere = Path(self.tmp.name) / "elsewhere"
        elsewhere.mkdir()
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-10), message_id="630")
        capture.capture(self.cfg, CHAT, "630", "evening words", m(0), "text", "590")
        real = self.cfg.root / "2026-10-15-evening.md"
        outside = elsewhere / "2026-10-15-evening.md"
        outside.write_text(real.read_text())
        with ledger.locked(self.cfg.ledger_path) as data:  # e.g. a ledger copied from another install
            ledger.find(data, "2026-10-15-evening")["entry_path"] = str(outside)
        before = outside.read_text()
        self.his("[Journal reply — saved …]\n\nevening words", m(0), "590")
        self.hers("tell me more", m(1))
        self.tick(m(5))
        self.assertEqual(outside.read_text(), before)
        self.assertIn("> tell me more", real.read_text())  # the same name in this root


class ReadersTest(Base):
    def setUp(self):
        super().setUp()
        self.his("the first thing I said", m(0), "700")
        self.hers("HER WORDS that are not his", m(1))
        self.his("my follow-up words", m(3), "701")
        self.tick(m(8))
        self.body = self.note().read_text()

    def test_his_words_keep_his_lines_and_drop_hers(self):
        self.assertNotIn("HER WORDS", entries.raw_entry(self.body))
        self.assertNotIn("follow-up", entries.raw_entry(self.body))
        words = entries.his_words(self.body)
        self.assertIn("the first thing I said", words)
        self.assertIn("my follow-up words", words)
        self.assertNotIn("HER WORDS", words)
        self.assertNotIn("HER WORDS", entries.without_companion_replies(self.body))
        self.assertIn("my follow-up words", entries.without_companion_replies(self.body))

    def test_pack_and_verifier(self):
        pack = "\n".join(context.journal_entries(self.cfg))
        self.assertIn("my follow-up words", pack)
        self.assertNotIn("HER WORDS", pack)
        verify = context.make_verifier(self.cfg, db=Path(self.tmp.name) / "none.db")
        self.assertTrue(verify("journal:2026-10-15-j", norm("my follow-up words")))
        self.assertFalse(verify("journal:2026-10-15-j", norm("HER WORDS that are not his")))

    def test_annotation_input_and_reflection_input(self):
        seen = []
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-j")
        self.assertFalse(conversations.annotate_auto(self.cfg))
        with mock.patch("journal.agent.ask", lambda cfg, q, *a, **k: seen.append(q) or "- x"):
            tick.annotate(self.cfg, m(60), [])
        self.assertEqual(seen, [])  # an auto note is not annotated
        self.cfg.conversations["annotate_auto"] = True
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, p["id"])["annotate"] = True
        entries.write_annotations(self.note(), "_Pending._", self.cfg.names)
        with mock.patch("journal.agent.ask", lambda cfg, q, *a, **k: seen.append(q) or "- x"), \
                mock.patch("journal.context.companion_reply_after", lambda *a, **k: None):
            tick.annotate(self.cfg, m(60), [])
        self.assertEqual(len(seen), 1)
        self.assertIn("my follow-up words", seen[0])
        self.assertNotIn("HER WORDS", seen[0])
        block = reflect._input_block(self.cfg, [self.note()])
        self.assertIn("[Iris's chat reply, 19:31: her words, not Theo's] HER WORDS that are not his", block)
        self.assertNotIn("HER WORDS", reflect._his_corpus(block))
        self.assertIn("my follow-up words", reflect._his_corpus(block))

    def test_reflection_trims_her_replies_first(self):
        long_reply = "\n".join(f"her line {i} " + "x" * 80 for i in range(200))
        entries.append_conversation(self.note(), conversations.her_block(self.cfg, m(4), long_reply, key="uid:long"), self.cfg.names)
        entries.append_conversation(self.note(), conversations.his_block(self.cfg, m(6), "HIS LAST WORDS"),
                                    self.cfg.names)
        text = reflect._entry_text(self.cfg, self.note())
        self.assertLessEqual(len(text), reflect.INPUT_CAP + 60)
        self.assertIn("HIS LAST WORDS", text)
        self.assertNotIn("her line 199", text)


class OffRecordTest(CleanEnv):
    """/x: off the record through the hook, and the tick never writes it."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.db = FakeSessions(t / "state.db")
        conf = write_config(t / "journal.json", {
            "root": str(t / "Journal"), "state_dir": str(t / "state"), "timezone": "Asia/Kolkata",
            "telegram": {"chat_id": CHAT}, "user": {"name": "Theo", "pronouns": "he/him/his"},
            "companion": {"name": "Iris"}, "hermes": {"state_db": str(self.db.path)}})
        os.environ["JOURNAL_CONFIG"] = str(conf)
        self.cfg = load()
        self.cfg.root.mkdir(parents=True)
        self.now = datetime.now(timezone.utc)
        conversations.run(self.cfg, self.now - timedelta(hours=1), [])

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def event(self, text, mid, reply_to=None, kind="TEXT", media=None, types=None):
        return SimpleNamespace(text=text, message_type=SimpleNamespace(name=kind), message_id=mid,
                               reply_to_message_id=reply_to, media_urls=media or [], media_types=types or [],
                               raw_message=None,
                               source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id=CHAT))

    def send(self, text, mid, at, **kw):
        """What the gateway does: the hook, then the stored row (the hook's rewrite, if any)."""
        out = hooks.on_dispatch(event=self.event(text, mid, **kw))
        if out is None and text.strip() in ("/x",):
            return out  # a gateway command: no row is stored
        self.db.add(out["text"] if out else text, at, mid)
        return out

    def test_one_message_off_the_record(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=self.now - timedelta(minutes=5),
                   message_id="800")
        t = self.now - timedelta(minutes=4)
        out = self.send("/x between us: the thing with my cousin", "801", t, reply_to="800")
        self.assertTrue(out["text"].startswith("[Off the record — Theo marked this message /x"))
        self.assertTrue(out["text"].endswith("between us: the thing with my cousin"))
        self.assertNotIn("/x", out["text"].split("]\n\n", 1)[1])
        self.assertFalse((self.cfg.root / "2026-10-15-evening.md").exists())  # not captured as a journal reply
        self.db.add("I hear you about your cousin.", t + timedelta(seconds=30), role="assistant")
        self.send("ok, unrelated: lunch was good", "802", t + timedelta(minutes=2))
        self.db.add("What did you have?", t + timedelta(minutes=2.5), role="assistant")
        conversations.run(self.cfg, self.now + timedelta(minutes=3), [])
        texts = "".join(p.read_text() for p in self.cfg.root.glob("*.md"))
        self.assertNotIn("cousin", texts)
        self.assertIn("lunch was good", texts)
        self.assertIn("What did you have?", texts)
        self.assertIn("801", conversations.snapshot(self.cfg)["off"])
        # nor in the context pack or the quote checker
        pack = "\n".join(context.telegram_messages(self.cfg))
        self.assertNotIn("cousin", pack)
        self.assertIn("lunch was good", pack)
        self.assertFalse(context.make_verifier(self.cfg)("telegram:x", norm("the thing with my cousin")))

    def test_bare_x_holds_the_thread_until_it_goes_quiet(self):
        t = self.now - timedelta(minutes=100)
        self.send("before, on the record", "810", t)
        self.db.add("noted", t + timedelta(seconds=20), role="assistant")
        with mock.patch.object(hooks, "datetime") as dt:
            dt.now.side_effect = lambda tz=None: t + timedelta(minutes=1)
            self.assertIsNone(self.send("/x", "811", t + timedelta(minutes=1)))  # (the command's reply: tested below)
            dt.now.side_effect = lambda tz=None: t + timedelta(minutes=2)
            out = self.send("now the private part", "812", t + timedelta(minutes=2))
            self.assertTrue(out["text"].startswith("[Off the record — this conversation is off the record"))
            self.db.add("private reply", t + timedelta(minutes=2.5), role="assistant")
            dt.now.side_effect = lambda tz=None: t + timedelta(minutes=3)
            self.assertTrue(self.send("/j but this one I want kept", "813", t + timedelta(minutes=3))["text"]
                            .startswith("[Journal note"))
            dt.now.side_effect = lambda tz=None: t + timedelta(minutes=60)  # 57 quiet minutes later
            out = self.send("back on the record", "814", t + timedelta(minutes=60))
            self.assertIsNone(out)
        conversations.run(self.cfg, self.now, [])
        texts = "".join(p.read_text() for p in self.cfg.root.glob("*.md"))
        self.assertIn("before, on the record", texts)
        self.assertIn("but this one I want kept", texts)
        self.assertIn("back on the record", texts)
        for gone in ("private part", "private reply"):
            self.assertNotIn(gone, texts)

    def test_the_hook_fails_closed_and_the_command_never_claims_what_it_did_not_do(self):
        with mock.patch.object(conversations, "note_off", side_effect=OSError("disk full")):
            out = hooks.on_dispatch(event=self.event("/x my secret", "840"))
            self.assertTrue(out["text"].startswith("[Off the record"))
            self.assertTrue(out["text"].endswith("my secret"))
            self.assertIn("Couldn't take this conversation off the record", commands.off_record(""))
        self.assertIn("Off the record:", commands.off_record(""))  # records it itself when the hook didn't
        self.assertTrue(conversations.snapshot(self.cfg).get("off_threads"))

    def test_journal_writing_tools_refuse_an_off_record_turn(self):
        note = capture.off_record_note(self.cfg)
        hooks.on_llm_call(user_message=note + "remember my secret", platform="telegram", session_id="s1",
                          turn_id="t1")
        out = hooks.on_tool_call(tool_name="journal_add_note", args={"note": "his secret"}, session_id="s1",
                                 turn_id="t1")
        self.assertEqual(out["action"], "block")
        self.assertIn("Off the record", out["message"])
        out = hooks.on_tool_call(tool_name="journal_notebook", args={"action": "add", "text": "secret"},
                                 session_id="s1", turn_id="t1")
        self.assertEqual(out["action"], "block")
        self.assertIsNone(hooks.on_tool_call(tool_name="journal_notebook", args={"action": "list"},
                                             session_id="s1", turn_id="t1"))
        hooks.on_llm_call(user_message="a public question", platform="telegram", session_id="s1", turn_id="t2")
        self.db.add("a public question", self.now, "850")
        self.assertIsNone(hooks.on_tool_call(tool_name="journal_add_note", args={"note": "fine"},
                                             session_id="s1", turn_id="t2"))
        # a /x message steered into a running turn never passed the hook: the sessions DB still says so
        steer = "[OUT-OF-BAND USER MESSAGE — a direct message]\n/x and one private thing\n[/OUT-OF-BAND USER MESSAGE]"
        self.db.add(steer, self.now, "851", display_kind="steer")
        out = hooks.on_tool_call(tool_name="journal_add_note", args={"note": "the private thing"},
                                 session_id="s1", turn_id="t2")
        self.assertEqual(out["action"], "block")
        self.assertEqual(list(self.cfg.root.glob("*.md")), [])

    def test_a_later_public_steer_does_not_clear_an_earlier_x_in_the_same_turn(self):
        self.db.add("a question", self.now - timedelta(minutes=3), "860")
        steer = "[OUT-OF-BAND USER MESSAGE — a direct message]\n{}\n[/OUT-OF-BAND USER MESSAGE]"
        self.db.add(steer.format("/x a private aside"), self.now - timedelta(minutes=2), "861", display_kind="steer")
        self.db.add(steer.format("and a public follow-up"), self.now - timedelta(minutes=1), "862", display_kind="steer")
        out = hooks.on_tool_call(tool_name="journal_add_note", args={"note": "the aside"}, session_id="s9",
                                 turn_id="t9")
        self.assertEqual(out["action"], "block")
        self.db.add("Here's my answer.", self.now, role="assistant")  # the turn ends
        self.db.add("a new public turn", self.now, "863")
        self.assertIsNone(hooks.on_tool_call(tool_name="journal_add_note", args={"note": "fine"}, session_id="s9",
                                             turn_id="t10"))

    def test_the_tool_guard_fails_closed(self):
        with mock.patch.object(privacy, "view", lambda *a, **k: privacy.Closed("db gone")):
            out = hooks.on_tool_call(tool_name="journal_add_note", args={"note": "x"}, session_id="s3", turn_id="t3")
        self.assertEqual(out["action"], "block")
        with mock.patch.object(hooks.runtime, "load_config", side_effect=RuntimeError("no config")):
            out = hooks.on_tool_call(tool_name="journal_notebook", args={"action": "add", "text": "x"})
        self.assertEqual(out["action"], "block")

    def test_an_audio_document_captioned_x_is_one_off_record_message(self):
        f = Path(self.tmp.name) / "doc_rec.flac"
        f.write_bytes(b"flac")
        ev = self.event("/x", "870", kind="DOCUMENT", media=[str(f)], types=["audio/flac"])
        out = hooks.on_dispatch(event=ev)
        self.assertTrue(out["text"].startswith("[Off the record — Theo marked this message /x"))
        st = conversations.snapshot(self.cfg)
        self.assertFalse(st.get("off_threads"))
        self.assertIn("870", st["off"])
        self.assertNotIn("audio", st)  # not stashed: it is never written

    def test_busy_path_rows_in_an_off_thread_are_not_swept(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=self.now - timedelta(minutes=10),
                   message_id="820")
        self.cfg.hermes["state_db"] = str(self.db.path)
        conversations.note_off(self.cfg, "821", self.now - timedelta(minutes=8), thread_start=True)
        self.db.add(redirected("an off-record thought while she typed", "822"), self.now - timedelta(minutes=7))
        self.db.add("/x a single off-record line", self.now - timedelta(minutes=6), "823")
        log = []
        sweep.run(self.cfg, self.now, log)
        self.assertEqual(log, [])
        self.assertFalse((self.cfg.root / "2026-10-15-evening.md").exists())

    def test_chat_voice_note_is_stashed_at_dispatch(self):
        f = Path(self.tmp.name) / "audio_x.ogg"
        f.write_bytes(b"OggS")
        self.assertIsNone(hooks.on_dispatch(event=self.event("", "830", kind="VOICE", media=[str(f)],
                                                             types=["audio/ogg"])))
        stash = conversations.snapshot(self.cfg)["audio"]["830"]
        self.assertTrue(Path(stash["file"]).read_bytes() == b"OggS")


class PackInjectionTest(CleanEnv):
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
        self.prompt = add_prompt(self.cfg, kind="evening", local_date="2026-10-15",
                                 sent=datetime.now(timezone.utc) - timedelta(minutes=30), message_id="900")
        context.save(self.cfg, self.prompt["id"],
                     "# Context pack\nGenerated now.\n\n## Recent journal entries (newest first)\n"
                     + "### journal:x\n" + "raw journal words " * 300 + "\n\n## Weather\nRain, 9°C, PACKFACT\n")

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def event(self, text, mid, reply_to=None):
        return SimpleNamespace(text=text, message_type=SimpleNamespace(name="TEXT"), message_id=mid,
                               reply_to_message_id=reply_to, media_urls=[], media_types=[], raw_message=None,
                               source=SimpleNamespace(platform=SimpleNamespace(value="telegram"), chat_id=CHAT))

    def test_only_on_the_captured_reply_turn(self):
        out = hooks.on_dispatch(event=self.event("rainy, tired", "901", reply_to="900"))
        ctx = hooks.on_llm_call(user_message=out["text"], platform="telegram")["context"]
        self.assertIn("Journal: the context your opener for this evening prompt was written from", ctx)
        self.assertIn("PACKFACT", ctx)
        self.assertLess(len(ctx), context.TURN_PACK_CAP + 600)
        self.assertLess(ctx.count("raw journal words"), 40)  # the big journal excerpts are cut
        self.assertIsNone(hooks.on_llm_call(user_message=out["text"], platform="telegram"))  # once
        self.assertIsNone(hooks.on_dispatch(event=self.event("just chatting", "902")))
        self.assertIsNone(hooks.on_llm_call(user_message="just chatting", platform="telegram"))

    def test_j_note_gets_a_fresh_pack_and_the_new_marker(self):
        out = hooks.on_dispatch(event=self.event("/j a passing thought", "903"))
        self.assertIn("read around it before you answer", out["text"])
        self.assertEqual(context.clean_user_text(out["text"]), "a passing thought")
        ctx = hooks.on_llm_call(user_message=out["text"], platform="telegram")["context"]
        self.assertIn("the context your most recent journal opener was written from", ctx)
        old = self.cfg.state_dir / "packs" / f"{self.prompt['id']}.md"
        os.utime(old, (old.stat().st_atime, old.stat().st_mtime - 13 * 3600))
        out = hooks.on_dispatch(event=self.event("/j another", "904"))
        self.assertIsNone(hooks.on_llm_call(user_message=out["text"], platform="telegram"))  # older than 12 h


class BackfillTest(Base):
    def setUp(self):
        super().setUp()
        (self.cfg.state_dir / "conversations.json").unlink()  # as before any live tick
        day = datetime(2026, 9, 25, 12, 0, tzinfo=timezone.utc)
        # an entry saved long ago without message ids (an old backfill): his words are already there
        old = {"id": "2026-09-25-morning", "kind": "morning", "local_date": "2026-09-25", "chat_id": CHAT,
               "sent_at": (day - timedelta(hours=6)).isoformat(), "backfilled": True, "opener": ""}
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append(old)
            capture.record(self.cfg, old, "backfill", "What even is my plan, I keep asking", day)
        self.db.add("What even is my plan, I keep asking", day, "300")
        self.db.add("A big question. What brought it up today?", day + timedelta(minutes=1), role="assistant")
        self.db.add("the conversation with my neighbour", day + timedelta(minutes=3), "301")
        self.db.add("evening: rain all day, read a little", day + timedelta(hours=6), "302")
        self.db.add("Sounds cozy.", day + timedelta(hours=6, minutes=1), role="assistant")
        self.db.add("/new", day + timedelta(hours=6, minutes=2), "303")

    def test_dry_run_lists_without_writing_then_write_is_idempotent(self):
        before = sorted(p.name for p in self.cfg.root.iterdir())
        morning = (self.cfg.root / "2026-09-25-morning.md").read_text()
        lines = conversations.backfill(self.cfg, date(2026, 9, 1), write=False, now=T0)
        self.assertIn("dry run", lines[0])
        self.assertIn("  append 2026-09-25-morning.md    1 from Theo,   1 from Iris  “the conversation with my neighbour”",
                      lines)
        self.assertIn("  create 2026-09-25-j.md          1 from Theo,   1 from Iris  “evening: rain all day, read a "
                      "little”", lines)
        self.assertTrue(any(l.startswith("  not written: ") and "1 captured" in l and "1 command" in l for l in lines),
                        lines)
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), before)  # nothing written
        self.assertEqual((self.cfg.root / "2026-09-25-morning.md").read_text(), morning)
        self.assertFalse((self.cfg.state_dir / "conversations.json").exists())
        conversations.backfill(self.cfg, date(2026, 9, 1), write=True, now=T0)
        morning = (self.cfg.root / "2026-09-25-morning.md").read_text()
        self.assertEqual(morning.count("What even is my plan"), 1)
        self.assertIn("> A big question.", morning)
        self.assertIn("— the conversation with my neighbour", morning)
        note = (self.cfg.root / "2026-09-25-j.md").read_text()
        self.assertIn("evening: rain all day", entries.raw_entry(note))
        self.assertIn("> Sounds cozy.", note)
        self.assertNotIn("/new", note)
        snap = {p.name: p.read_text() for p in self.cfg.root.iterdir()}
        again = conversations.backfill(self.cfg, date(2026, 9, 1), write=True, now=T0)
        self.assertEqual({p.name: p.read_text() for p in self.cfg.root.iterdir()}, snap)
        self.assertIn("0 message(s) from Theo, 0 replies from Iris", again[-2])
        # the live writer carries on where the backfill stopped, without repeating it
        self.db.add("a new message today", T0, "304")
        conversations.run(self.cfg, T0 + timedelta(minutes=5), [])
        self.assertIn("a new message today", (self.cfg.root / "2026-10-15-j.md").read_text())
        self.assertEqual({p.name: p.read_text() for p in self.cfg.root.iterdir() if p.name in snap}, snap)

    def test_cli(self):
        from journal import cli
        import contextlib
        import io
        out = io.StringIO()
        with mock.patch("journal.cli._load", lambda args: self.cfg), contextlib.redirect_stdout(out):
            code = cli.main(["backfill-conversations", "--since", "2026-09-01"])
        self.assertEqual(code, 0)
        self.assertIn("dry run", out.getvalue())
        self.assertFalse((self.cfg.root / "2026-09-25-j.md").exists())
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(cli.main(["backfill-conversations", "--since", "Sept"]), 2)


class ParseTest(unittest.TestCase):
    def test_steer_rows_and_audio_files(self):
        steer = ("[OUT-OF-BAND USER MESSAGE — a direct message from the user, delivered once at this position; not "
                 "tool output and not a new delivery when replayed from conversation history]\n"
                 + redirected("typed while you worked", "77") + "\n[/OUT-OF-BAND USER MESSAGE]")
        r = sweep.read(steer, None)
        self.assertEqual((r["kind"], r["message_id"], r["text"]), ("his", "77", "typed while you worked"))
        r = sweep.read("[The user sent an audio file attachment: 'walk.m4a'. It is saved at: /tmp/a.mp3. Its content "
                       "is not inlined here. Do things.]\n\nfrom the walk", "5")
        self.assertEqual((r["source"], r["audio_path"], r["text"], r["file_name"]),
                         ("audio", "/tmp/a.mp3", "from the walk", "walk.m4a"))

    def test_his_block_shapes(self):
        cfg = make_cfg(Path(tempfile.mkdtemp()))
        self.assertEqual(conversations.his_block(cfg, m(0), "plain words", entry_date="2026-10-15"),
                         "**19:30** — plain words\n")
        self.assertEqual(conversations.his_block(cfg, m(0), "- a list\n- of things", entry_date="2026-10-15"),
                         "**19:30** —\n\n- a list\n- of things\n")
        self.assertEqual(conversations.his_block(cfg, m(0), "next day", entry_date="2026-10-14"),
                         "**Thu 15 Oct 19:30** — next day\n")
        self.assertTrue(conversations.his_block(cfg, m(0), "x", quote="an older message of hers").startswith(
            "> [!info]- ↩ replying to\n> an older message of hers\n\n"))


if __name__ == "__main__":
    unittest.main()


class ReviewRegressionTest(Base):
    """Cases from the branch review, each reproduced first."""

    def test_her_combined_reply_to_an_off_record_message_is_not_saved(self):
        self.his("start of the thread here", m(0), "1")
        self.hers("ok, go on", m(1))
        conversations.note_off(self.cfg, "2", m(3))
        self.his(capture.off_record_note(self.cfg) + "my secret is X", m(3), "2")
        steer = "[OUT-OF-BAND USER MESSAGE — a direct message]\nand also the weather\n[/OUT-OF-BAND USER MESSAGE]"
        self.db.add(steer, m(3.5), "3", display_kind="steer")
        self.hers("About your secret X: I won't tell. And the weather is nice.", m(5))
        self.his("thanks, new topic", m(7), "4")
        self.hers("Sure, what is it?", m(8))
        self.tick(m(12))
        body = self.note().read_text()
        self.assertNotIn("secret", body)
        self.assertIn("and also the weather", body)  # his words are kept; her reply in that turn is not
        self.assertIn("> Sure, what is it?", body)  # the next turn is on the record again

    def test_a_short_line_inside_a_saved_entry_is_still_written(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-60), message_id="600")
        capture.capture(self.cfg, CHAT, "600", "honestly thank you so much for listening to all that", m(-55), "text",
                        "50")
        self.his("thank you so much", m(0), "51")
        self.hers("anytime", m(1))
        self.tick(m(10))
        texts = "".join(p.read_text() for p in self.cfg.root.glob("*.md"))
        self.assertIn("thank you so much\n", texts.replace("— thank you so much", "\nthank you so much"))
        self.assertIn("thank you so much", entries.raw_entry(self.note().read_text()))
        self.assertIn("> anytime", self.note().read_text())

    def test_a_failed_tick_keeps_what_it_wrote_and_a_replay_writes_nothing_twice(self):
        cache = Path(self.tmp.name) / "v.ogg"
        cache.write_bytes(b"OggS")
        conversations.stash_audio(self.cfg, "2", str(cache), ".ogg", {}, m(1))
        self.his("first words", m(0), "1")
        self.his('"a voice note"', m(1), "2")
        self.hers("reply", m(2))
        real, calls = conversations.classify, []

        def flaky(*a, **k):
            calls.append(1)
            if len(calls) == 3:
                raise RuntimeError("boom")
            return real(*a, **k)
        with mock.patch.object(conversations, "classify", flaky):
            with self.assertRaises(RuntimeError):  # the real error, not an UnboundLocalError
                self.tick(m(10))
        st = conversations.snapshot(self.cfg)
        self.assertIn("1", st["written"])
        self.assertEqual(st["cursor"]["last_id"], 0)  # not advanced: the batch is replayed
        with conversations.locked(self.cfg) as st:  # as if it died before even remembering the keys
            st["written"] = {}
        self.tick(m(11))
        self.tick(m(12))
        body = self.note().read_text()
        self.assertEqual(body.count("a voice note"), 1)
        self.assertEqual(body.count("first words"), 1)
        self.assertEqual(body.count("> reply"), 1)
        self.assertEqual(sorted(p.name for p in self.cfg.root.glob("*.ogg")), ["2026-10-15-j.ogg"])

    def test_identical_messages_in_the_same_minute_are_both_kept(self):
        self.his("start of thread words", m(0), "80")
        self.his("ok", m(1), "81")
        self.his("ok", m(1.2), "82")
        self.hers("❤️", m(1.3))
        self.his("and", m(1.4), "83")
        self.hers("❤️", m(1.5))
        self.tick(m(10))
        section = conv(self.note().read_text())
        self.assertEqual(section.count("— ok"), 2)
        self.assertEqual(section.count("> ❤️"), 2)

    def test_a_turn_longer_than_the_quiet_period_keeps_its_reply(self):
        self.his("can you research this", m(0), "70")
        self.hers("", m(20), tool_calls='[{"x": 1}]')
        self.hers("", m(40), tool_calls='[{"x": 2}]')
        self.hers("Here is what I found", m(50))
        self.tick(m(60))
        self.assertIn("> Here is what I found", self.note().read_text())

    def test_interim_text_is_not_flushed_while_later_rows_are_settling(self):
        self.his("hello there", m(0), "60")
        self.hers("let me check something", m(1))
        self.hers("Here is the real answer", m(8.5))
        self.tick(m(10))
        self.tick(m(15))
        body = self.note().read_text()
        self.assertNotIn("let me check something", body)
        self.assertIn("> Here is the real answer", body)

    def test_a_missing_home_starts_a_new_note_and_nothing_is_lost(self):
        self.his("the first", m(0), "90")
        self.hers("hi", m(1))
        self.tick(m(5))
        self.note().unlink()  # renamed or deleted in Obsidian
        self.his("the second", m(6), "91")
        self.hers("still here", m(7))
        self.tick(m(12))
        second = self.note(2).read_text()
        self.assertIn("the second", entries.raw_entry(second))
        self.assertIn("> still here", second)
        self.assertEqual(conversations.snapshot(self.cfg)["written"]["91"]["entry"], "2026-10-15-j-2")

    def test_an_off_record_window_ends_at_the_next_prompt(self):
        conversations.note_off(self.cfg, "x1", m(0), thread_start=True)
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(10), message_id="640")
        self.assertTrue(off_at(self.cfg, m(5), m(20)))
        self.assertFalse(off_at(self.cfg, m(12), m(20)))

    def test_his_own_conversation_heading_never_moves_his_words(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-10), message_id="650")
        capture.capture(self.cfg, CHAT, "650", "part one\n\n## Conversation\n\npart two of my words", m(0), "text",
                        "100")
        path = self.cfg.root / "2026-10-15-evening.md"
        entries.append_conversation(path, conversations.his_block(self.cfg, m(30), "later message"), self.cfg.names)
        capture.capture(self.cfg, CHAT, "650", "a later direct reply", m(31), "text", "101")
        body = path.read_text()
        words = entries.raw_entry(body)
        self.assertLess(words.index("part one"), words.index("part two of my words"))
        self.assertLess(words.index("part two of my words"), words.index("a later direct reply"))
        self.assertEqual(conv(body), "**20:00** — later message")
        self.assertLess(body.index("a later direct reply"), body.index(entries.CONVERSATION_MARK))

    def test_the_verifier_skips_a_busy_path_message_in_an_off_thread(self):
        now = datetime.now(timezone.utc)
        conversations.note_off(self.cfg, None, now - timedelta(minutes=3), thread_start=True)
        self.db.add("private words while she was typing", now - timedelta(minutes=2), None)
        self.db.add("public words from earlier", now - timedelta(hours=3), "99")
        verify = context.make_verifier(self.cfg)
        self.assertFalse(verify("telegram:x", norm("private words while she was typing")))
        self.assertTrue(verify("telegram:x", norm("public words from earlier")))

    def test_a_dry_run_backfill_creates_nothing(self):
        self.cfg.state_dir = Path(self.tmp.name) / "fresh-state"
        code, lines = tick.run_backfill(self.cfg, date(2026, 10, 1), write=False)
        self.assertEqual(code, 0)
        self.assertFalse(self.cfg.state_dir.exists())

    def test_annotation_alignment_skips_her_reply_to_an_off_record_message(self):
        self.his(capture.off_record_note(self.cfg) + "private", m(1), "110")
        self.hers("about the private thing", m(2))
        self.his("ordinary", m(3), "111")
        self.hers("about the ordinary thing", m(4))
        with near_t0():
            self.assertEqual(context.companion_reply_after(self.cfg, m(0.5)), "about the ordinary thing")

    def test_rows_compaction_stores_again_are_read_once(self):
        self.his("words from august", m(0), None)
        self.hers("an august reply", m(1))
        self.tick(m(5))
        self.his("a new thread, much later", m(120), "130")
        self.his("words from august", m(0), None)  # compaction re-inserted the tail: same time and content, new id
        self.hers("an august reply", m(1))
        self.his("[Cron delivery: Evening] old text", m(-30), None)  # an old cron row stored again: no boundary
        self.his("still the new thread", m(121), "131")
        self.tick(m(130))
        self.assertEqual(self.note().read_text().count("words from august"), 1)
        second = self.note(2).read_text()
        self.assertNotIn("august", second)
        self.assertIn("— still the new thread", second)

    def test_words_already_in_an_entry_older_than_the_ledger_are_not_written_again(self):
        old = self.cfg.root / "2026-10-15-morning.md"  # written before the ledger existed: no record of it
        old.write_text("---\ndate: 2026-10-15\nkind: morning\ntype: journal\nsource: x\n---\n\n# Morning\n\n"
                       "## Prompt\n\nx\n\n## Theo\n\n### 2026-10-15 19:30 IST · text\n\nan old saved reply, "
                       "word for word ok\n\n## Iris's annotations\n\n- a\n")
        self.his("word for word", m(-2), "139")  # inside that block, but a short phrase, not it: written
        self.hers("noted", m(-1.5))
        self.his("an old saved reply, word for word", m(0), "140")
        self.hers("a reply to it", m(1))
        self.tick(m(10))
        self.assertEqual(entries.raw_entry(self.note().read_text()), "### 19:28 IST · text\n\nword for word")
        self.assertNotIn("an old saved reply", self.note().read_text())
        self.assertIn("> a reply to it", old.read_text())

    def test_a_queued_message_with_an_earlier_time_keeps_the_stored_order(self):
        # Hermes stores a user row with its Telegram send time; a message queued during a turn is stored
        # after her reply but carries the earlier time.
        self.his("message A", m(0), "120")
        self.hers("reply to A", m(5))
        self.his("message B, sent while she worked", m(2), "121")
        self.hers("reply to B", m(6))
        self.tick(m(10))
        self.assertEqual(conv(self.note().read_text()),
                         "> [!quote]- Iris · 19:35\n> reply to A\n\n**19:32** — message B, sent while she worked\n\n"
                         "> [!quote]- Iris · 19:36\n> reply to B")


class CodexReviewTest(Base):
    """Cases from the first (earlier PR 7) review, each reproduced first."""

    def test_an_expired_off_window_stays_on_record_when_the_hook_clears_it(self):
        conversations.note_off(self.cfg, "x1", m(0), thread_start=True)
        self.assertFalse(privacy.window_open(self.cfg, m(60)))  # 60 quiet minutes: it ends
        self.assertTrue(off_at(self.cfg, m(5), m(60)))  # a busy-path row from inside it
        self.assertFalse(off_at(self.cfg, m(50), m(60)))

    def test_annotation_alignment_skips_a_busy_path_reply_in_an_off_thread(self):
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        self.his("private, steered in while she typed", m(2), None)  # no marker, no id: the hook never saw it
        self.hers("a private reply", m(3))
        with near_t0():
            self.assertIsNone(context.companion_reply_after(self.cfg, m(1)))

    def test_his_own_callout_stays_his_words(self):
        self.his("first", m(0), "150")
        self.his("> [!note] Important\n> remember this", m(1), "151")
        self.hers("I will.", m(2))
        self.tick(m(10))
        body = self.note().read_text()
        words = entries.his_words(body)
        self.assertIn("> [!note] Important\n> remember this", words)
        self.assertNotIn("I will.", words)
        self.assertIn("remember this", entries.without_companion_replies(body))
        self.assertIn("remember this", reflect._his_corpus(reflect._input_block(self.cfg, [self.note()])))

    def test_a_replayed_audio_block_still_queues_its_transcript(self):
        self.cfg.audio["command"] = ["/bin/sh", "-c", "echo 'transcribed'"]
        self.cfg.audio["ffprobe"] = "/nonexistent"
        f = Path(self.tmp.name) / "audio_a.mp3"
        f.write_bytes(b"mp3")
        self.his("first words", m(0), "160")
        self.his(f"[The user sent an audio file attachment: 'a.m4a'. It is saved at: {f}. Its content is not inlined "
                 "here.]", m(1), "161")
        self.tick(m(10))
        with ledger.locked(self.cfg.ledger_path) as data:  # as if it stopped before queueing the transcript
            p = ledger.find(data, "2026-10-15-j")
            p["captures"] = [c for c in p["captures"] if c.get("rule") != "conversation"]
        with conversations.locked(self.cfg) as st:
            st["written"].pop("161")
            st["cursor"]["last_id"] = 1
        self.tick(m(11))
        caps = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-j")["captures"]
        self.assertEqual([c["transcript"] for c in caps if c.get("rule") == "conversation"], ["pending"])
        tick.process_audio(self.cfg, m(12), [])
        self.assertIn("transcribed", conv(self.note().read_text()))
        self.assertEqual(self.note().read_text().count("![[2026-10-15-j.mp3]]"), 1)

    def test_same_second_identical_messages_are_two_messages(self):
        self.his("start", m(0), "170")
        self.his("ok", m(1), "171")
        self.his("ok", m(1), "172")  # same second, same words, another Telegram message
        self.tick(m(10))
        self.assertEqual(conv(self.note().read_text()).count("— ok"), 2)

    def test_an_opt_out_prefix_cannot_take_over_a_journal_command(self):
        from journal import config
        for bad in ("/j", "/journal", "/morning", "/weekly"):
            self.cfg.conversations["opt_out_prefix"] = bad
            self.assertTrue(any("opt_out_prefix" in p for p in config.problems(self.cfg)), bad)
            self.assertEqual(config.opt_out_prefix(self.cfg), "/x")
        self.cfg.conversations["opt_out_prefix"] = "/j"
        self.assertIsNone(capture.opt_out(self.cfg, "/j a note"))
        self.assertEqual(capture.opt_out(self.cfg, "/x private"), "private")
        self.cfg.conversations["opt_out_prefix"] = "/private"
        self.assertEqual(config.problems(self.cfg), [p for p in config.problems(self.cfg) if "opt_out" not in p])

    def test_a_user_named_conversation(self):
        from journal.config import Names
        self.cfg.names = Names(user="Conversation", companion="Iris")
        info = capture.capture_note(self.cfg, "a note from someone called Conversation", m(0), "text", "180")
        body = Path(info["path"]).read_text()
        self.assertIn("## Conversation\n", body)
        self.assertIn("a note from someone called Conversation", entries.raw_entry(body))
        entries.append_conversation(Path(info["path"]), conversations.his_block(self.cfg, m(5), "later"),
                                    self.cfg.names)
        body = Path(info["path"]).read_text()
        self.assertIn("a note from someone called Conversation", entries.raw_entry(body))
        self.assertEqual(conv(body), "**19:35** — later")

    def test_a_replaced_database_at_the_same_path_is_noticed(self):
        self.his("one", m(0), "190")
        self.his("two", m(1), "191")
        self.tick(m(10))
        self.db.path.unlink()  # restored or rebuilt: other rows, at least as many
        self.db = FakeSessions(self.db.path)
        for i in range(4):
            self.his(f"old history {i}", m(-5000 + i), str(300 + i))
        log = self.tick(m(20))
        self.assertIn("conversations: the sessions database was replaced; starting again from now", log)
        texts = "".join(p.read_text() for p in self.cfg.root.glob("*.md"))
        self.assertNotIn("old history", texts)
        self.his("new after the restore", m(21), "400")
        self.tick(m(30))
        self.assertIn("new after the restore", "".join(p.read_text() for p in self.cfg.root.glob("*.md")))

    def test_a_failed_turn_after_x_ends_the_taint(self):
        self.his("a public start", m(0), "200")
        self.hers("hello", m(1))
        conversations.note_off(self.cfg, "201", m(2))
        self.his(capture.off_record_note(self.cfg) + "private", m(2), "201")
        self.hers("Your request was not processed. Send it again if you still want me to carry it out.", m(3),
                  display_kind="failed_turn")
        self.his("a public question", m(5), "202")
        self.hers("a public answer", m(6))
        self.tick(m(12))
        body = self.note().read_text()
        self.assertNotIn("private", body)
        self.assertIn("> a public answer", body)


class CodexReviewTwoTest(Base):
    """Cases from the second review (earlier PR 7), each reproduced first."""

    def test_an_open_off_window_follows_busy_path_activity(self):
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        for t in (30, 60, 90):  # steered in while she worked: the hook saw none of them
            self.his(f"private part {t}", m(t), None)
        self.assertTrue(off_at(self.cfg, m(90), m(150)))
        self.assertTrue(off_at(self.cfg, m(120), m(150)))
        self.assertFalse(off_at(self.cfg, m(140), m(150)))  # 50 quiet minutes after m(90)

    def test_a_backfill_with_nothing_settled_still_hands_over_its_start(self):
        (self.cfg.state_dir / "conversations.json").unlink()  # before any live tick
        self.his("just now, not settled yet", m(0), "210")
        self.hers("and a reply", m(0.5))
        conversations.backfill(self.cfg, date(2026, 10, 15), write=True, now=m(1))
        conversations.run(self.cfg, m(6), [])
        body = self.note().read_text()
        self.assertIn("just now, not settled yet", body)
        self.assertIn("> and a reply", body)

    def test_doctor_survives_a_malformed_quiet_minutes(self):
        from journal import config, status
        self.cfg.conversations["quiet_minutes"] = "soon"
        rows = {name: detail for name, _s, detail in status.doctor(self.cfg)}
        self.assertIn("'soon' is not usable", rows["conversations"])
        self.assertTrue(any("quiet_minutes" in p for p in config.problems(self.cfg)))
        self.assertEqual(conversations.quiet(self.cfg), timedelta(minutes=45))
        self.tick(m(10))  # the writer runs too

    def test_captioned_audio_in_conversation_is_transcribed_and_keeps_its_caption(self):
        self.cfg.audio["command"] = ["/bin/sh", "-c", "echo 'what the recording says'"]
        self.cfg.audio["ffprobe"] = "/nonexistent"
        f = Path(self.tmp.name) / "audio_c.mp3"
        f.write_bytes(b"mp3")
        self.his("first", m(0), "220")
        self.his(f"[The user sent an audio file attachment: 'c.m4a'. It is saved at: {f}. Its content is not inlined "
                 "here.]\n\nfrom the walk", m(1), "221")
        self.tick(m(10))
        caps = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-j")["captures"]
        self.assertEqual([c["transcript"] for c in caps if c.get("rule") == "conversation"], ["pending"])
        tick.process_audio(self.cfg, m(11), [])
        self.assertIn("![[2026-10-15-j.mp3]]\n\nwhat the recording says\n\nfrom the walk", conv(self.note().read_text()))

    def test_activity_after_a_quiet_gap_cannot_revive_an_off_window(self):
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        self.his("unhooked, after the window had lapsed", m(50), None)
        self.assertFalse(privacy.window_open(self.cfg, m(51)))  # 50 quiet minutes broke the chain
        self.assertFalse(off_at(self.cfg, m(51), m(52)))
        self.assertTrue(off_at(self.cfg, m(10), m(52)))  # the window it really held


class CodexReviewThreeTest(Base):
    """Cases from the third review (earlier PR 7), each reproduced first."""

    def test_a_repeated_bare_x_keeps_the_open_window(self):
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        self.his("private, steered in", m(30), None)
        conversations.note_off(self.cfg, None, m(40), thread_start=True)  # /x again, the window still open
        privacy._CACHE.clear()
        v = privacy.view(self.cfg, m(41))
        self.assertEqual([w[0] for w in v.windows], [m(0)])  # one window, from the first /x
        self.assertTrue(off_at(self.cfg, m(30), m(41)))

    def test_a_lapsed_window_is_recorded_before_a_new_bare_x(self):
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        conversations.note_off(self.cfg, None, m(100), thread_start=True)
        privacy._CACHE.clear()
        v = privacy.view(self.cfg, m(101))
        self.assertEqual([w[0] for w in v.windows], [m(0), m(100)])
        self.assertTrue(off_at(self.cfg, m(10), m(101)))
        self.assertFalse(off_at(self.cfg, m(50), m(101)))

    def test_the_prefix_cannot_be_a_hermes_command(self):
        from journal import config
        for bad in ("/new", "/reset", "/model", "/stop", "/retry", "/undo", "/status", "/help", "/sessions",
                    "/resume", "/btw", "/voice", "/title", "/save"):
            self.cfg.conversations["opt_out_prefix"] = bad
            self.assertIn("Hermes", " ".join(config.problems(self.cfg)), bad)
            self.assertEqual(config.opt_out_prefix(self.cfg), "/x")

    def test_audio_names_are_claimed_atomically(self):
        from journal import audio
        src = Path(self.tmp.name) / "a.ogg"
        src.write_bytes(b"first")
        audio.keep(self.cfg, str(src), "2026-10-15-j.md", ".ogg")
        src.write_bytes(b"second")
        with mock.patch("os.path.exists", lambda p: False):  # another process checked before the first copy landed
            name = audio.keep(self.cfg, str(src), "2026-10-15-j.md", ".ogg")
        self.assertEqual(name, "2026-10-15-j-2.ogg")
        self.assertEqual((self.cfg.root / "2026-10-15-j.ogg").read_bytes(), b"first")
        self.assertEqual((self.cfg.root / name).read_bytes(), b"second")

    def test_an_in_flight_reply_stays_with_its_thread_across_a_prompt_send(self):
        self.his("a question just before the prompt", m(0), "230")
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(1), message_id="660")
        self.hers("the answer, after the prompt went out", m(2))
        self.his("a new message after the prompt", m(4), "231")
        self.tick(m(10))
        first = self.note().read_text()
        self.assertIn("> the answer, after the prompt went out", first)
        self.assertNotIn("a new message after the prompt", first)
        # (no capture: the hook would have taken it as the evening reply; here it opens its own thread)
        self.assertIn("a new message after the prompt", entries.raw_entry(self.note(2).read_text()))

    def test_kept_audio_keys_are_pruned(self):
        st = {"kept": {"old": {"name": "x.ogg", "at": (T0 - timedelta(days=500)).isoformat()},
                       "new": {"name": "y.ogg", "at": T0.isoformat()}}}
        conversations._prune(st, T0)
        self.assertEqual(list(st["kept"]), ["new"])

    def test_the_outage_catch_up_never_sees_an_off_record_turn(self):
        from journal import recovery
        self.cfg.recovery = {"enabled": True, "max_age_hours": 48}
        self.his("an earlier public message", m(0), "240")
        self.hers("a public answer", m(1))
        self.his(capture.off_record_note(self.cfg) + "a private message", m(2), "241")
        self.hers("Your request was not processed. Send it again if you still want me to carry it out.", m(3),
                  display_kind="failed_turn")
        found = recovery.scan(self.cfg, self.db.path, m(10))
        self.assertEqual(found["his"], [])
        self.his("a public message later", m(5), "242")
        self.hers("Your request was not processed. Send it again if you still want me to carry it out.", m(6),
                  display_kind="failed_turn")
        found = recovery.scan(self.cfg, self.db.path, m(10))
        self.assertEqual([x["text"] for x in found["his"]], ["a public message later"])
        self.assertNotIn("private", " ".join(t for _w, _a, t in found["before"]))

    def test_readers_tolerate_a_legacy_schema(self):
        import sqlite3 as sq
        from journal import recovery
        old = Path(self.tmp.name) / "old.db"
        con = sq.connect(old)
        con.executescript("create table sessions (id text, source text, chat_id text);"
                          "create table messages (id integer primary key, session_id text, role text, content text, "
                          "timestamp real);")
        con.execute("insert into sessions values ('s', 'telegram', ?)", (CHAT,))
        con.execute("insert into messages (session_id, role, content, timestamp) values ('s', 'user', ?, ?)",
                    ("/x a private aside, steered in", m(0).timestamp()))
        con.commit()
        con.close()
        self.cfg.hermes["state_db"] = str(old)
        privacy._CACHE.clear()
        self.assertTrue(privacy.view(self.cfg, m(1)).tainted())  # the privacy check works on it
        self.assertEqual(len(conversations.rows(self.cfg, old, 0)), 1)
        self.assertEqual(len(sweep.rows(self.cfg, old, 0, 0)), 1)
        recovery.scan(self.cfg, old, m(10))
        context.companion_reply_after(self.cfg, m(-1))
        self.assertFalse(context.make_verifier(self.cfg)("telegram:x", norm("a private aside")))


    def test_a_note_started_by_a_row_without_an_id_is_not_started_twice_on_replay(self):
        self.his("no telegram id on this one", m(0), None)
        self.hers("a reply", m(1))
        self.tick(m(10))
        with conversations.locked(self.cfg) as st:  # as if it died before remembering anything
            st["written"], st["her"], st["cursor"]["last_id"], st["cursor"]["thread"] = {}, {}, 0, None
        self.tick(m(11))
        self.assertFalse(self.note(2).exists())
        self.assertEqual(self.note().read_text().count("a reply"), 1)

    def test_companion_notes_and_annotations_write_under_the_entry_lock(self):
        held = []
        real = ledger.locked

        def spy(path):
            held.append(str(path))
            return real(path)
        with mock.patch.object(ledger, "locked", spy):
            capture.add_companion_note(self.cfg, "a note of hers", m(0))
        self.assertIn(str(self.cfg.ledger_path), held)


class CodexReviewFourTest(Base):
    """Cases from the fourth review (earlier PR 7), each reproduced first, on the single privacy pass."""

    def steer(self, text, at, mid):
        self.db.add("[OUT-OF-BAND USER MESSAGE — a direct message]\n" + text + "\n[/OUT-OF-BAND USER MESSAGE]", at, mid,
                    display_kind="steer")

    def test_a_mixed_turn_stays_tainted_for_annotation_alignment(self):
        self.his("a question", m(0), "300")
        self.steer("/x the private part", m(1), "301")
        self.steer("and a public part", m(2), "302")
        self.hers("an answer to all of it", m(3))
        with near_t0():
            self.assertIsNone(context.companion_reply_after(self.cfg, m(-1)))

    def test_prompt_boundaries_outlive_the_ledger(self):
        old = m(-200 * 24 * 60)
        add_prompt(self.cfg, kind="evening", local_date="2026-03-29", sent=old + timedelta(minutes=1), message_id="1")
        tick.prune(self.cfg, T0)  # the ledger keeps 120 days; the send time is kept for good
        self.assertEqual(ledger.snapshot(self.cfg.ledger_path)["prompts"], [])
        (self.cfg.state_dir / "conversations.json").unlink()
        self.his("before the old prompt", old, "310")
        self.his("after the old prompt", old + timedelta(minutes=2), "311")
        lines = conversations.backfill(self.cfg, date(2026, 3, 1), write=False, now=T0)
        self.assertEqual(sum(1 for l in lines if l.startswith("  create ")), 2, lines)

    def test_a_busy_path_bare_x_holds_the_window_before_the_sweep(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(0), message_id="670")
        self.steer("/x", m(1), "320")
        self.steer("something private while she typed", m(2), "321")
        log = []
        sweep.run(self.cfg, m(5), log)
        self.assertEqual(log, [])
        self.assertFalse((self.cfg.root / "2026-10-15-evening.md").exists())

    def test_the_backfill_handoff_is_one_state_write(self):
        (self.cfg.state_dir / "conversations.json").unlink()
        self.his("old words", m(-60), "330")
        real, calls = conversations.locked, []

        def flaky(cfg):
            calls.append(1)
            if len(calls) > 1:
                raise KeyboardInterrupt("stopped between writes")
            return real(cfg)
        with mock.patch.object(conversations, "locked", flaky):
            try:
                conversations.backfill(self.cfg, date(2026, 10, 15), write=True, now=T0)
            except KeyboardInterrupt:
                pass
        st = conversations.snapshot(self.cfg)
        self.assertEqual((st.get("db"), bool(st.get("cursor")), "start_id" in st), (str(self.db.path), True, True))

    def test_a_caption_starting_with_a_bracket_is_kept(self):
        self.his("first", m(0), "340")
        self.his("[The user sent a document: 'plan.pdf'. It is saved at: /tmp/p.pdf. Its text is not inlined here.]"
                 "\n\n[draft] send this tomorrow", m(1), "341")
        self.tick(m(10))
        self.assertIn("— [draft] send this tomorrow", conv(self.note().read_text()))

    def test_held_text_is_dropped_when_the_turn_fails(self):
        self.his("hello", m(0), "350")
        self.hers("a first bit of text", m(1))
        self.hers("Your request was not processed. Send it again if you still want me to carry it out.", m(2),
                  display_kind="failed_turn")
        self.his("are you there?", m(3), "351")
        self.tick(m(10))
        self.assertNotIn("a first bit of text", self.note().read_text())

    def test_off_record_windows_are_end_exclusive(self):
        conversations.note_off(self.cfg, None, m(0), thread_start=True)
        self.assertTrue(off_at(self.cfg, m(44.9), m(50)))
        self.assertFalse(off_at(self.cfg, m(45), m(50)))  # exactly quiet_minutes after the last activity: public
        self.his("exactly at the end", m(45), "360")
        self.tick(m(50))
        self.assertIn("exactly at the end", self.note().read_text())
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(60), message_id="680")
        conversations.note_off(self.cfg, None, m(55), thread_start=True)
        self.assertFalse(off_at(self.cfg, m(60), m(61)))  # the prompt's own moment is public


class OutageTest(Base):
    """An off-the-record exchange stays out of the file however long the tick was away. The view recomputes the
    last 48 hours and trusts what the tick persisted for anything older; rows that came after the last persisted
    pass but before ``now - 48 h`` had neither, and counted as public (a tick outage, or a pass that stayed closed,
    longer than 48 h wrote the /x exchange)."""

    def exchange(self):
        self.his("start of the thread here", m(0), "1")
        self.hers("ok, go on", m(1))
        conversations.note_off(self.cfg, "2", m(3))  # the hook records the /x message
        self.his(capture.off_record_note(self.cfg) + "my secret is X", m(3), "2")
        self.hers("About your secret X: I won't tell.", m(5))
        self.his("thanks, new topic", m(7), "4")
        self.hers("Sure, what is it?", m(8))

    def after_gap(self, hours):
        self.exchange()
        privacy._CACHE.clear()
        at = m(12) + timedelta(hours=hours)
        privacy.update(self.cfg, at, [])  # the tick persists the pass first, then the writer runs
        self.tick(at)
        return "".join(p.read_text() for p in self.cfg.root.glob("*.md"))

    def test_the_secret_stays_out_whatever_the_gap(self):
        for hours in (0, 47, 50, 200):
            with self.subTest(gap_hours=hours):
                self.tearDown()
                self.setUp()
                body = self.after_gap(hours)
                self.assertIn("new topic", body)  # the public part was written
                self.assertNotIn("secret", body)
                self.assertNotIn("won't tell", body)

    def test_a_view_after_days_without_a_pass_covers_what_was_never_persisted(self):
        privacy.update(self.cfg, m(-30), [])  # the last pass that ran, before the exchange
        self.exchange()
        privacy._CACHE.clear()
        pv = privacy.view(self.cfg, m(12) + timedelta(hours=100))  # no pass since (a tick outage, or it was closed)
        self.assertFalse(pv.closed)
        self.assertEqual(sorted(pv.private), [3, 4])

    def test_the_cursor_stands_in_only_while_no_pass_is_saved(self):
        """Each pass saves everything up to its own time, so a cursor behind it adds nothing and a stale one (the
        writer off, or another database) must not make every reader, the hook on each turn included, read weeks
        of the transcript."""
        now = m(0) + timedelta(hours=1)
        stale = (m(0) - timedelta(days=30)).isoformat()
        with conversations.locked(self.cfg) as st:
            st["cursor"]["last_at"] = stale
        state = conversations.snapshot(self.cfg)
        self.assertEqual(privacy._unpersisted_from(self.cfg, state, self.db.path, now),
                         m(0) - timedelta(days=30) - privacy.RECOMPUTE)  # no pass saved: the cursor stands in
        self.cfg.conversations["enabled"] = False
        self.assertEqual(privacy._unpersisted_from(self.cfg, state, self.db.path, now), now)
        self.cfg.conversations["enabled"] = True
        self.assertEqual(privacy._unpersisted_from(self.cfg, state, Path("/elsewhere.db"), now), now)
        privacy.update(self.cfg, m(0), [])  # a pass is saved: it, not the cursor, bounds the unpersisted rows
        state = conversations.snapshot(self.cfg)
        self.assertEqual(privacy._unpersisted_from(self.cfg, state, self.db.path, now), m(0) - privacy.RECOMPUTE)


class BacklogTest(Base):
    """Backlog issues from the October 2026 review audit, each reproduced first."""

    def test_a_backfill_sees_a_prompt_sent_before_an_earlier_id_rows_later_time(self):
        """Queued rows: rows go by id, and a queued message of his is stored after a later one with its earlier send time.
        A prompt sent between the last row's time and the latest time handled is still a thread boundary."""
        (self.cfg.state_dir / "conversations.json").unlink()  # before any live tick
        self.his("message A", m(0), "700")
        self.hers("reply to A", m(1))
        self.his("message B, after the prompt", m(5), "701")
        self.his("message C, queued while she worked", m(2), "702")  # stored last, sent earlier
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(4), message_id="710")
        conversations.backfill(self.cfg, date(2026, 10, 15), write=True, now=m(30))
        self.assertNotIn("message B", self.note().read_text())  # the prompt ended A's thread, as live
        self.assertIn("message B, after the prompt", self.note(2).read_text())

    def test_an_audio_file_in_a_relocated_entry_is_still_transcribed(self):
        """Other roots: a ledger copied from another machine records the entry under the old root; the writer appends to the
        same name in this journal, so the transcript must be queued (and filled) there too."""
        self.cfg.audio["command"] = ["/bin/sh", "-c", "echo 'transcribed words'"]
        self.cfg.audio["ffprobe"] = "/nonexistent"
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-10), message_id="720")
        capture.capture(self.cfg, CHAT, "720", "the day was long", m(0), "text", "721")
        with ledger.locked(self.cfg.ledger_path) as data:  # as if the ledger came from the old machine
            ledger.find(data, "2026-10-15-evening")["entry_path"] = "/old/machine/journal/2026-10-15-evening.md"
        self.his('[Journal reply — saved …]\n\nthe day was long', m(0), "721")
        f = Path(self.tmp.name) / "audio_b.mp3"
        f.write_bytes(b"mp3")
        self.his(f"[The user sent an audio file attachment: 'b.m4a'. It is saved at: {f}. Its content is not inlined "
                 "here.]", m(1), "722")
        self.tick(m(10))
        caps = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-evening")["captures"]
        self.assertEqual([c["transcript"] for c in caps if c.get("rule") == "conversation"], ["pending"])
        tick.process_audio(self.cfg, m(12), [])
        self.assertIn("transcribed words", conv((self.cfg.root / "2026-10-15-evening.md").read_text()))

    def test_an_old_backfilled_recording_is_kept_until_it_is_transcribed(self):
        """Prune: a backfill of a message older than the ledger keeps (120 days) files it on a prompt dated then; one
        bad transcription must not let the same tick's prune drop the record and leave it pending for good."""
        (self.cfg.state_dir / "conversations.json").unlink()  # before any live tick
        old = T0 - timedelta(days=200)
        f = Path(self.tmp.name) / "audio_c.mp3"
        f.write_bytes(b"mp3")
        self.his(f"[The user sent an audio file attachment: 'c.m4a'. It is saved at: {f}. Its content is not inlined "
                 "here.]", old, "730")
        self.cfg.audio["ffprobe"] = "/nonexistent"
        self.cfg.audio["command"] = ["/bin/sh", "-c", "true"]  # the backend answers, but hears nothing: one attempt
        conversations.backfill(self.cfg, old.date(), write=True, now=T0)
        stem = old.astimezone(self.cfg.tz).date().isoformat() + "-j"
        self.assertTrue(tick._transcribing(ledger.find(ledger.snapshot(self.cfg.ledger_path), stem)))
        tick.process_audio(self.cfg, T0, [])
        tick.prune(self.cfg, T0)
        self.assertIsNotNone(ledger.find(ledger.snapshot(self.cfg.ledger_path), stem))
        self.cfg.audio["command"] = ["/bin/sh", "-c", "echo 'heard at last'"]
        tick.process_audio(self.cfg, T0 + timedelta(minutes=5), [])
        self.assertIn("heard at last", (self.cfg.root / f"{stem}.md").read_text())
        tick.prune(self.cfg, T0 + timedelta(minutes=5))  # nothing left to do: it goes with its date
        self.assertIsNone(ledger.find(ledger.snapshot(self.cfg.ledger_path), stem))

    def test_an_old_record_with_a_picture_to_describe_is_kept_while_pictures_are_described(self):
        """Prune, pictures: the images step only runs while descriptions are on, so only then is the record kept."""
        old = (T0 - timedelta(days=200)).isoformat()
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": "2026-03-29-j", "kind": "j", "local_date": "2026-03-29", "sent_at": old,
                                    "captures": [{"at": old, "images": ["2026-03-29-j.jpg"], "describe": "pending"}]})
        self.cfg.images["describe"] = True
        self.cfg.agent["enabled"] = False  # (only for now: when it is back, the images step describes it)
        tick.prune(self.cfg, T0)
        self.assertIsNotNone(ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-03-29-j"))
        self.cfg.images["describe"] = False  # nothing will ever describe it: it goes with its date
        tick.prune(self.cfg, T0)
        self.assertIsNone(ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-03-29-j"))

    def test_his_own_annotations_heading_in_an_unannotated_note_stays_his_words(self):
        """End of words: an auto note has no annotations section (``annotate_auto`` off), so a heading of that shape in his
        words was read as the section boundary: his words were cut there, the conversation went in above the rest of
        them, and the rest read as her annotations."""
        words = "my plan for the week\n\n## Iris's annotations\n\nwhat I hope she says\n\n## Iris's notes\n\nand a list"
        self.his(words, m(0), "740")
        self.hers("Shall we go through it?", m(1))
        self.his("yes, later", m(3), "741")
        self.tick(m(10))
        entries.add_companion_note(self.note(), "a note of hers", m(11).astimezone(self.cfg.tz), None, self.cfg.names)
        body = self.note().read_text()
        self.assertEqual(entries.raw_entry(body), "### 19:30 IST · text\n\n" + words)
        self.assertEqual(conv(body), "> [!quote]- Iris · 19:31\n> Shall we go through it?\n\n**19:33** — yes, later")
        self.assertEqual(entries.annotations(body), "")
        self.assertLess(body.index("and a list"), body.index("## Conversation"))
        span = entries.notes_span(body)
        self.assertIn("a note of hers", body[span[0]:span[1]])
        self.assertNotIn("and a list", body[span[0]:span[1]])

    def test_a_steer_does_not_write_her_interim_text(self):
        """Steers: a message steered into her running turn is not the end of it: text she held before it is still not
        her reply if the turn goes on to another tool step, or ends with a later text."""
        steer = "[OUT-OF-BAND USER MESSAGE — a direct message]\n{}\n[/OUT-OF-BAND USER MESSAGE]"
        self.his("can you check the train times?", m(0), "750")
        self.hers("Let me think about which line.", m(1))  # interim text, no tool call: held
        self.db.add(steer.format("the 9am one"), m(2), "751", display_kind="steer")
        self.hers("Checking the 9am now.", m(3), tool_calls='[{"id": "t"}]')  # the turn goes on working
        self.hers("The 9am runs on time.", m(4))
        self.his("thanks", m(6), "752")
        self.hers("Any time.", m(7))
        self.db.add(steer.format("and the 10am?"), m(7.5), "753", display_kind="steer")
        self.hers("The 10am is late.", m(8))  # a later text replaces the held one
        self.tick(m(15))
        section = conv(self.note().read_text())
        self.assertNotIn("Let me think", section)
        self.assertNotIn("Any time.", section)
        self.assertIn("— the 9am one", section)
        self.assertIn("> The 9am runs on time.", section)
        self.assertIn("> The 10am is late.", section)

    def test_text_held_across_a_steer_is_written_when_her_turn_ends_there(self):
        """Steers: held is not dropped: when nothing more of her turn comes, the text she held is her reply."""
        self.his("one question", m(0), "760")
        self.hers("The answer is yes.", m(1))
        self.steer_in("oh and thanks", m(1.5), "761")
        self.tick(m(10))
        section = conv(self.note().read_text())
        self.assertIn("> The answer is yes.", section)
        self.assertIn("— oh and thanks", section)

    def steer_in(self, text, at, mid):
        self.db.add("[OUT-OF-BAND USER MESSAGE — a direct message]\n" + text + "\n[/OUT-OF-BAND USER MESSAGE]", at, mid,
                    display_kind="steer")


class BacklogReviewTest(Base):
    """Findings of the independent review (earlier PR 76), each reproduced first."""

    def note_with_mark(self):
        self.his("first words", m(0), "800")
        self.tick(m(10))
        body = self.note().read_text()
        self.assertIn(entries.WORDS_END_MARK, body)
        return body

    def test_a_pasted_end_of_words_line_in_his_message_moves_nothing(self):
        self.his("first words", m(0), "800")
        self.his(f"I pasted this:\n\n{entries.WORDS_END_MARK}\n\nand kept typing", m(1), "801")
        self.his("a third message", m(2), "802")
        self.tick(m(10))
        section = conv(self.note().read_text())
        self.assertIn("and kept typing", section)
        self.assertTrue(section.endswith("— a third message"), section)

    def test_her_notes_and_annotations_cannot_carry_the_line(self):
        self.note_with_mark()
        entries.add_companion_note(self.note(), f"a note\n\n{entries.WORDS_END_MARK}", m(11).astimezone(self.cfg.tz),
                                   None, self.cfg.names)
        entries.write_annotations(self.note(), f"- one\n\n{entries.WORDS_END_MARK}", self.cfg.names)
        body = self.note().read_text()
        self.assertEqual(body.count(entries.WORDS_END_MARK), 1)
        self.assertEqual(entries.raw_entry(body), "### 19:30 IST · text\n\nfirst words")

    def test_a_reply_appended_after_the_line_by_older_code_is_still_his_words(self):
        """A rollback (or the gateway before its restart) appends after the line: the file then reads as before."""
        body = self.note_with_mark()
        self.note().write_text(body.rstrip() + "\n\n### 20:00 IST · text\n\na late reply\n", encoding="utf-8")
        self.assertIn("a late reply", entries.raw_entry(self.note().read_text()))

    def steer(self, text, at, mid):
        self.db.add("[OUT-OF-BAND USER MESSAGE — a direct message]\n" + text + "\n[/OUT-OF-BAND USER MESSAGE]", at, mid,
                    display_kind="steer")

    def busy(self, on=True):
        import time
        if on:
            self.db.sql("insert into session_turn_leases values ('tg', 'x', ?, ?)", time.time(), time.time() + 3600)
        else:
            self.db.sql("delete from session_turn_leases")

    def everything(self):
        return "".join(p.read_text() for p in self.cfg.root.glob("*.md"))

    def test_text_held_across_a_steer_stays_with_the_entry_it_was_held_for(self):
        self.his("a question", m(0), "810")
        self.hers("an interim thought", m(1))
        self.busy()
        self.tick(m(5))  # she is mid-turn: held
        self.note().unlink()  # he deleted the note meanwhile: the steered message starts another
        self.steer("and another thing", m(6), "811")
        self.busy(False)
        self.tick(m(12))
        self.assertIn("and another thing", self.note(2).read_text())
        self.assertNotIn("an interim thought", self.everything())

    def test_text_held_across_a_steer_that_leaves_the_thread_without_an_entry_fails_nothing(self):
        self.cfg.capture["mode"] = "arm"
        self.cfg.sweep["enabled"] = False  # (the writer files what the sweep would: no waiting for it here)
        info = capture.capture_note(self.cfg, "a note to keep", m(0), "text", "820")
        self.his(capture.note_context(self.cfg, info) + "a note to keep", m(0), "820")
        self.hers("an interim thought", m(1))
        self.busy()
        self.tick(m(5))
        self.note().unlink()
        self.steer("just chatting now", m(6), "821")  # (arm) ordinary chat: the thread has no entry now
        self.busy(False)
        log = self.tick(m(12))
        self.assertNotIn("an interim thought", self.everything())
        self.assertIsNone((conversations.snapshot(self.cfg)["cursor"]["thread"] or {}).get("pending"), log)
        self.assertEqual(conversations.snapshot(self.cfg)["cursor"]["last_at"], (m(12) - conversations.SETTLE).isoformat())

    def test_a_steer_into_a_turn_a_prompt_send_interrupted_writes_no_interim_text(self):
        self.his("a question", m(0), "830")
        self.hers("an interim thought", m(1))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(2), message_id="840")
        self.steer("and one more thing", m(3), "831")
        self.hers("Looking.", m(4), tool_calls='[{"id": "t"}]')
        self.hers("The real answer.", m(5))
        self.tick(m(12))
        first = self.note().read_text()
        self.assertNotIn("an interim thought", self.everything())
        self.assertIn("> The real answer.", conv(first))

    def test_a_conversation_older_code_added_below_the_line_is_still_read(self):
        """Re-review: a rollback's tick opens ``## Conversation`` below the line; moving forward again reads it."""
        body = self.note_with_mark()
        self.note().write_text(body.rstrip() + "\n\n## Conversation\n\n%% journal:conversation %%\n\n"
                               "%% journal his:809 %%\n**20:00** — said after a rollback\n", encoding="utf-8")
        self.assertEqual(conv(self.note().read_text()), "**20:00** — said after a rollback")
        entries.append_conversation(self.note(), conversations.his_block(self.cfg, m(31), "and after it",
                                                                          key="810"), self.cfg.names)
        body = self.note().read_text()
        self.assertEqual(body.count("## Conversation"), 1)
        self.assertTrue(conv(body).endswith("— and after it"), body)

    def test_a_pasted_line_followed_by_a_heading_of_his_cuts_nothing(self):
        """Re-review: in an annotated entry (no line of the engine's) a pasted one ended his words."""
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-10), message_id="870")
        words = f"before\n\n{entries.WORDS_END_MARK}\n\n## My own heading\n\nafter"
        capture.capture(self.cfg, CHAT, "870", words, m(0), "text", "871")
        capture.capture(self.cfg, CHAT, "870", "a second reply", m(1), "text", "872")
        body = (self.cfg.root / "2026-10-15-evening.md").read_text()
        words = entries.raw_entry(body)
        self.assertIn("## My own heading\n\nafter", words)
        self.assertLess(words.index("after"), words.index("a second reply"))

    def test_a_captured_reply_steered_into_a_draining_turn_takes_her_reply(self):
        """Re-review: a journal reply moves the thread, steered in or not; her final text goes with it."""
        self.his("a question", m(0), "850")
        self.hers("an interim thought", m(1))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(2), message_id="860")
        capture.capture(self.cfg, CHAT, "860", "my evening answer", m(3), "text", "851")  # the hook saved it
        self.steer("[Journal reply — saved …]\n\nmy evening answer", m(3), "851")
        self.hers("Thank you for the evening answer.", m(5))
        self.tick(m(12))
        evening = (self.cfg.root / "2026-10-15-evening.md").read_text()
        self.assertIn("> Thank you for the evening answer.", conv(evening))
        self.assertNotIn("Thank you", self.note().read_text())

    def test_text_held_before_the_upgrade_is_written_where_it_was_held(self):
        """CodeRabbit: held text saved by older code has no entry of its own; it is written before a steer moves on."""
        self.his("a question", m(0), "880")
        self.hers("an interim thought", m(1))
        self.busy()
        self.tick(m(5))
        with conversations.locked(self.cfg) as st:  # as older code saved it
            st["cursor"]["thread"]["pending"].pop("home")
        self.note().unlink()  # he deleted the note: the steered message starts another
        self.steer("and another thing", m(6), "881")
        self.busy(False)
        self.tick(m(12))
        self.assertIn("and another thing", self.note(2).read_text())
        self.assertNotIn("an interim thought", self.everything())

    def test_a_steer_into_a_draining_turn_stays_with_her_reply(self):
        """CodeRabbit: a message steered into the turn a prompt send left running belongs to that turn's thread."""
        self.his("a question", m(0), "890")
        self.hers("an interim thought", m(1))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(2), message_id="895")
        self.steer("and one more thing", m(3), "891")
        self.hers("The real answer.", m(5))
        self.tick(m(12))
        section = conv(self.note().read_text())
        self.assertIn("— and one more thing", section)
        self.assertTrue(section.endswith("> The real answer."), section)
        self.assertNotIn("an interim thought", self.everything())
        self.assertFalse(self.note(2).exists())

    def test_held_ordinary_chat_is_not_written_after_a_steer_switched_under_arm(self):
        """CodeRabbit: under arm, a steered /j makes its own note journal material, not the chat note she held for."""
        self.his("just chatting", m(0), "900")
        self.hers("an interim thought", m(1))
        self.busy()
        self.tick(m(5))  # held for the chat note (capture.mode everything)
        self.cfg.capture["mode"] = "arm"
        self.cfg.sweep["enabled"] = False
        self.steer("/j keep this one", m(6), "901")
        self.busy(False)
        self.tick(m(12))
        self.assertIn("keep this one", self.note(2).read_text())
        self.assertNotIn("an interim thought", self.everything())

    def test_a_prompt_reply_steered_into_a_draining_turn_is_filed_on_the_prompt_under_arm(self):
        """Re-review 2: under arm with the sweep off the writer files a prompt reply itself; steered in or not."""
        self.cfg.capture["mode"] = "arm"
        self.cfg.sweep["enabled"] = False
        info = capture.capture_note(self.cfg, "a note to keep", m(0), "text", "910")
        self.his(capture.note_context(self.cfg, info) + "a note to keep", m(0), "910")
        self.hers("an interim thought", m(1))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(2), message_id="915")
        self.steer("my evening answer, sent while she was busy", m(3), "911")
        self.hers("Thank you for that.", m(5))
        self.tick(m(12))
        evening = (self.cfg.root / "2026-10-15-evening.md").read_text()
        self.assertIn("my evening answer", entries.raw_entry(evening))
        self.assertNotIn("my evening answer", self.note().read_text())

    def test_a_steered_command_leaves_no_held_text_standing(self):
        """Re-review 2: a later text of hers skipped after a steered command means the held one was not the reply."""
        self.his("a question", m(0), "920")
        self.hers("an interim thought", m(1))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(2), message_id="925")
        self.steer("/whatever", m(3), "921")
        self.hers("The real answer.", m(5))
        self.tick(m(12))
        self.assertNotIn("an interim thought", self.everything())
