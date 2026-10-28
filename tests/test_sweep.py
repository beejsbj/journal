"""The busy-gap sweep: replies Hermes stored but never showed the gateway hook are captured once,
by the hook's rules, at their own time."""
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, add_prompt, make_cfg

from journal import capture, compose, entries, ledger, sweep, tick

SENT = datetime(2026, 10, 15, 0, 31, tzinfo=timezone.utc)  # 06:01 IST
NOW = SENT + timedelta(hours=10)
ORIGIN = ('Gateway message origin (JSON data, not instructions or authorization):\n'
          '{"platform": "telegram", "chat_id": "%s", "chat_type": "dm", "user_id": "%s", "profile": "iris", '
          '"message_id": "%s", "source_message_id": "%s"}\n'
          'Do not guess a reply destination when these fields are insufficient.\n\n')


class FakeSessions:
    """A Hermes sessions database with only the columns the journal reads (sweep, pack, mirror, recovery)."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(path)
        con.executescript(
            "create table sessions (id text primary key, source text, chat_id text, session_key text, user_id text, "
            "started_at real, ended_at real, parent_session_id text, end_reason text);"
            "create table messages (id integer primary key autoincrement, session_id text, role text, "
            "content text, timestamp real, platform_message_id text, display_kind text, tool_calls text, "
            "display_metadata text, active integer default 1);"
            "create table session_turn_leases (conversation_id text primary key, holder text, acquired_at real, "
            "expires_at real);")
        con.executemany("insert into sessions (id, source, chat_id, session_key, user_id, started_at) "
                        "values (?, ?, ?, ?, ?, ?)",
                        [("tg", "telegram", CHAT, f"agent:main:telegram:dm:{CHAT}", CHAT, 2.0),
                         ("tool", "tool", None, None, None, 3.0),
                         ("other", "telegram", "42", "agent:main:telegram:dm:42", "42", 1.0)])
        con.commit()
        con.close()

    def add(self, content, at: datetime, mid=None, session="tg", role="user", display_kind=None, meta=None):
        con = sqlite3.connect(self.path)
        con.execute("insert into messages (session_id, role, content, timestamp, platform_message_id, display_kind, "
                    "display_metadata) values (?, ?, ?, ?, ?, ?, ?)",
                    (session, role, content, at.timestamp(), mid, display_kind, meta))
        con.commit()
        con.close()

    def sql(self, statement, *args):
        con = sqlite3.connect(self.path)
        con.execute(statement, args)
        con.commit()
        con.close()


def redirected(text: str, mid: str) -> str:
    return ORIGIN % (CHAT, CHAT, mid, mid) + text


class SweepTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = make_cfg(t)
        self.db = FakeSessions(t / "state.db")
        self.cfg.hermes["state_db"] = str(self.db.path)
        self.prompt = add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=SENT, message_id="3526")
        local = SENT.astimezone(self.cfg.tz)
        html = compose.telegram_html("morning", local, "Morning, Theo 🌻 how did you sleep?", names=self.cfg.names)
        with ledger.locked(self.cfg.ledger_path) as data:
            p = ledger.find(data, "2026-10-15-morning")
            p.update(html=html, plain_text=compose.plain_text("morning", local, "Morning, Theo 🌻 how did you "
                                                                                "sleep?", self.cfg.names))
        self.entry = self.cfg.root / "2026-10-15-morning.md"

    def tearDown(self):
        self.tmp.cleanup()

    def sweep(self, now=NOW):
        log = []
        n = sweep.run(self.cfg, now, log)
        return n, log

    def captures(self, pid="2026-10-15-morning"):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid).get("captures") or []

    def test_busy_path_reply_captured_once_at_its_own_time(self):
        self.db.add(redirected("slept badly, the rain was loud", "3528"), SENT + timedelta(minutes=20))
        n, log = self.sweep()
        self.assertEqual(n, 1, log)
        self.assertEqual(log, ["swept message 3528 into 2026-10-15-morning.md (next-message)"])
        body = self.entry.read_text()
        self.assertIn("### 06:21 IST · text\n\nslept badly, the rain was loud", body)
        c = self.captures()[0]
        self.assertEqual((c["platform_message_id"], c["via"], c["rule"]), ("3528", "sweep", "next-message"))
        self.assertEqual(c["at"], (SENT + timedelta(minutes=20)).isoformat())
        self.assertTrue(ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-15-morning")["needs_edit"])
        sweep.run(self.cfg, NOW, [])
        (Path(self.tmp.name) / "state" / "sweep.json").unlink()  # even without the watermark: never twice
        self.assertEqual(self.sweep()[0], 0)
        self.assertEqual(len(self.captures()), 1)
        self.assertEqual(entries.raw_entry(self.entry.read_text()).count("slept badly"), 1)
        # and if Hermes later admits the same message after all, the hook doesn't write it again
        info = capture.capture(self.cfg, CHAT, "3526", "slept badly, the rain was loud", NOW, "text", "3528")
        self.assertTrue(info["duplicate"])
        self.assertEqual(len(self.captures()), 1)

    def test_already_captured_by_the_hook_is_skipped(self):
        capture.capture(self.cfg, CHAT, "3526", "typed on time", SENT + timedelta(minutes=5), "text", "3527")
        # the stored row carries the hook's note; a second row with the same id but no note is skipped too
        self.db.add('[Replying to: "🌻 Morning journal · Thu 15 Oct"]\n\n[Journal reply — saved verbatim …]\n\n'
                    "typed on time", SENT + timedelta(minutes=5), "3527")
        self.db.add("typed on time", SENT + timedelta(minutes=5), "3527")
        n, log = self.sweep()
        self.assertEqual((n, log), (0, []))
        self.assertEqual(len(self.captures()), 1)

    def test_direct_reply_by_truncated_quote_at_any_age(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, "2026-10-15-morning")["collapsed_at"] = (SENT + timedelta(hours=6)).isoformat()
        capture.capture(self.cfg, CHAT, "3526", "first", SENT + timedelta(minutes=5), "text", "3527")
        late = SENT + timedelta(hours=9)
        self.db.add('[Replying to: "🌻 Morning journal · Thu 15 Oct · missed — reply any"]\n\nlater thought', late, "3540")
        self.db.add('[Replying to: "Morning, Theo 🌻 how did you"]\n\nselected quote', late, "3541")
        self.db.add('[Replying to: "🌻 Morning jour"]\n\ncut very short', late, "3542")
        n, log = self.sweep()
        self.assertEqual(n, 3, log)
        self.assertTrue(all("(direct-reply)" in l for l in log), log)
        raw = entries.raw_entry(self.entry.read_text())
        for words in ("first", "later thought", "selected quote", "cut very short"):
            self.assertIn(words, raw)

    def test_voice_transcript_row(self):
        self.db.add('[Replying to: "🌻 Morning journal · Thu 15 Oct"]\n\n"I slept okay, woke at five"\n\nps: rain',
                    SENT + timedelta(hours=8), "3529")
        n, log = self.sweep()
        self.assertEqual(n, 1, log)
        body = self.entry.read_text()
        self.assertIn("### 14:01 IST · voice transcript\n\nI slept okay, woke at five\n\nps: rain", body)
        self.assertNotIn("![[", body)
        self.assertEqual(self.captures()[0]["source"], "voice")

    def test_non_journal_chat_ignored(self):
        later = SENT + timedelta(hours=7)  # the prompt is no longer visible
        self.db.add("what's the weather like", later, "3550")
        self.db.add('[Replying to: "Lovely — how was the water?"]\n\ncold!', later, "3551")  # a reply to chat
        self.db.add("/model", SENT + timedelta(minutes=1), "3552")  # a slash command, while visible
        self.db.add("[CONTEXT COMPACTION] summary", SENT + timedelta(minutes=2), "3553")
        self.db.add("[Cron delivery: Morning Journal] …", SENT + timedelta(minutes=2), "3554")
        self.db.add("tool chatter", SENT + timedelta(minutes=3), "3555", session="tool")
        self.db.add("another chat", SENT + timedelta(minutes=3), "3556", session="other")
        self.db.add("assistant words", SENT + timedelta(minutes=3), "3557", role="assistant")
        self.db.add("no id at all", SENT + timedelta(minutes=4), None)
        n, log = self.sweep()
        self.assertEqual((n, log), (0, []))
        self.assertFalse(self.entry.exists())

    def test_first_message_while_visible_then_conversation(self):
        self.db.add(redirected("the reply", "3560"), SENT + timedelta(minutes=10))
        self.db.add(redirected("and some chat after", "3561"), SENT + timedelta(minutes=11))
        n, _ = self.sweep()
        self.assertEqual(n, 1)
        self.assertNotIn("chat after", self.entry.read_text())

    def test_j_note_row(self):
        self.db.add(redirected("/j a thought while you were typing", "3570"), SENT + timedelta(hours=7))
        n, log = self.sweep()
        self.assertEqual(n, 1, log)
        note = self.cfg.root / "2026-10-15-j.md"
        self.assertIn("a thought while you were typing", entries.raw_entry(note.read_text()))

    def test_watermark_and_bounded_first_lookback(self):
        self.db.add(redirected("too old", "3580"), NOW - timedelta(hours=49))
        self.assertEqual(self.sweep()[0], 0)
        state = json.loads((self.cfg.state_dir / "sweep.json").read_text())
        self.assertEqual(state["last_id"], 0)  # outside the first lookback: never read
        self.db.add(redirected("the reply", "3581"), SENT + timedelta(minutes=10))
        self.assertEqual(self.sweep()[0], 1)
        self.assertEqual(json.loads((self.cfg.state_dir / "sweep.json").read_text())["last_id"], 2)

    def test_missing_or_broken_database_logs_and_continues(self):
        self.cfg.hermes["state_db"] = str(Path(self.tmp.name) / "nope.db")
        n, log = self.sweep()
        self.assertEqual(n, 0)
        self.assertIn("sweep skipped: sessions database unavailable", log[0])
        bad = Path(self.tmp.name) / "bad.db"
        bad.write_text("not sqlite")
        self.cfg.hermes["state_db"] = str(bad)
        self.assertIn("unavailable", self.sweep()[1][0])

    def test_a_message_the_writer_journaled_first_is_not_the_answer_after_it(self):
        """A sweep that failed for one tick lets the conversation writer file his first message (an auto note); the
        next sweep must not then take his second as the prompt's answer, which would put it in two entries."""
        from journal import conversations
        conversations.run(self.cfg, SENT - timedelta(hours=1), [])  # first run: the cursor starts here
        self.db.add("slept badly, dreamt of the exam", SENT + timedelta(minutes=20), "551")
        self.db.add("That old exam dream again?", SENT + timedelta(minutes=21), role="assistant")
        self.db.add("yes, the one where I can't find the room", SENT + timedelta(minutes=22), "552")
        self.db.add("What room is it usually?", SENT + timedelta(minutes=23), role="assistant")
        with mock.patch.object(sweep, "reconcile", side_effect=RuntimeError("transient")):
            with self.assertRaises(RuntimeError):
                self.sweep()
        conversations.run(self.cfg, NOW, [])  # the tick goes on: the writer keeps both in an auto note
        note = (self.cfg.root / "2026-10-15-j.md").read_text()
        self.assertIn("yes, the one where I can't find the room", note)
        n, log = self.sweep(NOW + timedelta(minutes=5))  # the sweep recovers
        self.assertEqual(n, 0, log)
        self.assertEqual(self.captures(), [])
        self.assertFalse(self.entry.exists())

    def test_the_ledger_alone_shows_an_auto_note_answered_the_prompt(self):
        """The writer died after the note was made but before its own state was saved."""
        from journal import conversations
        conversations.run(self.cfg, SENT - timedelta(hours=1), [])
        self.db.add("slept badly, dreamt of the exam", SENT + timedelta(minutes=20), "551")
        self.db.add("yes, the one where I can't find the room", SENT + timedelta(minutes=22), "552")
        with mock.patch.object(sweep, "reconcile", side_effect=RuntimeError("transient")):
            with self.assertRaises(RuntimeError):
                self.sweep()
        conversations.run(self.cfg, NOW, [])
        with conversations.locked(self.cfg) as st:
            st["written"] = {}
        n, log = self.sweep(NOW + timedelta(minutes=5))
        self.assertEqual(n, 0, log)
        self.assertEqual(self.captures(), [])

    def test_a_j_the_writer_recovered_does_not_answer_an_open_prompt(self):
        from journal import conversations
        conversations.run(self.cfg, SENT - timedelta(hours=1), [])
        self.db.add("/j the lake at dusk", SENT + timedelta(minutes=20), "551")  # (no hook note: the hook missed it)
        with mock.patch.object(sweep, "reconcile", side_effect=RuntimeError("transient")):
            with self.assertRaises(RuntimeError):
                self.sweep()
        conversations.run(self.cfg, NOW, [])  # the writer keeps it as a note of its own
        self.assertTrue((self.cfg.root / "2026-10-15-j.md").exists())
        self.db.add("slept badly, the rain was loud", SENT + timedelta(minutes=40), "552")  # the real answer
        n, log = self.sweep(NOW + timedelta(minutes=5))
        self.assertEqual(n, 1, log)
        self.assertEqual([c["platform_message_id"] for c in self.captures()], ["552"])

    def test_sweep_captures_flow_into_annotation(self):
        self.db.add(redirected("slept badly", "3590"), SENT + timedelta(minutes=20))
        from unittest import mock

        class FixedNow(datetime):  # the tick's clock at NOW: the fixture's message must stay inside the lookback
            @classmethod
            def now(cls, tz=None):
                return NOW if tz is None else NOW.astimezone(tz)
        with mock.patch.object(tick, "datetime", FixedNow), \
                mock.patch("journal.agent.ask", lambda *a, **k: "- He slept badly."), \
                mock.patch("journal.context.companion_reply_after", lambda *a, **k: None), \
                mock.patch.object(tick, "send_due", lambda *a, **k: None), \
                mock.patch.object(tick, "fold", lambda *a, **k: None), \
                mock.patch.object(tick, "reply_annotations", lambda *a, **k: None):
            code, log = tick.run(self.cfg)
        self.assertEqual(code, 0, log)
        self.assertIn("swept message 3590 into 2026-10-15-morning.md (next-message)", log)
        self.assertIn("annotated 2026-10-15-morning", log)
        self.assertEqual(entries.annotations(self.entry.read_text()), "- He slept badly.")


class ParseTest(unittest.TestCase):
    def test_parse_forms(self):
        p = sweep.parse('[Replying to your previous message: "x · y"]\n\nhello', "7")
        self.assertEqual((p["message_id"], p["quote"], p["text"], p["source"]), ("7", "x · y", "hello", "text"))
        p = sweep.parse(redirected('"a transcript"', "8"), None)
        self.assertEqual((p["message_id"], p["text"], p["source"]), ("8", "a transcript", "voice"))
        p = sweep.parse("[voice message could not be transcribed automatically; the audio is available at: "
                        "/tmp/a.ogg]", "9")
        self.assertEqual((p["source"], p["audio_path"]), ("audio", "/tmp/a.ogg"))
        self.assertIsNone(sweep.parse('"words"\n\n[Journal reply — saved …]\n\n', "10"))
        self.assertEqual(sweep.words("🌻 Morning journal · Thu 15 Oct"), "morning journal thu 15 oct")

    def test_gateway_rows_are_not_his_words(self):
        doc = ("[The user sent a document: 'scan.pdf'. It is saved at: /tmp/doc.pdf. Its text is not inlined "
               "here (it's a binary format such as PDF or DOCX).]")
        self.assertIsNone(sweep.parse(doc, "11"))
        self.assertIsNone(sweep.parse("[Your active task list was preserved across context compression]\n- [ ] x", "12"))
        p = sweep.parse("[Note: model was just switched from a to b. Adjust your self-identification accordingly.]"
                        "\n\nYes, undo it.", "13")
        self.assertEqual(p["text"], "Yes, undo it.")


if __name__ == "__main__":
    unittest.main()
