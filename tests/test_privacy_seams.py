"""Privacy at the seams: holes a read-only review of the stack found, each pinned by a test that failed before its fix.

(1) A phase owed a send (its send failed) was delivered by the tick's step with no off-the-record check: the send
ended a bare-/x window and his next private message became the new phase's answer. (2) The settle checked the
window, then spent a reflect phase's model call before sending: a /x that arrived meanwhile was ended by the send.
(4) Inside a window, a failure to record the message as off made it fall through to capture. (5) A busy-path bare
/x stored with no time was dropped by the sessions database's time-bounded read, so the pass opened no window.
(6) An off-the-record message merged into a voice note's turn was written into the entry with the transcript."""
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, CleanEnv, FakeTranscript, add_prompt, make_cfg

from journal import conversations, entries, inbound, privacy, programs, sweep, telegram, turncontext
from journal.hermes.transcript import HermesTranscript
from journal.ports import ChatUnreadable
from test_programs import NAMES, T0
from test_programs_review import HookBase

REFLECT = "Picture an ordinary Tuesday five years on, built from what you wrote above, and stay with it."


class AutomaticSendsTest(HookBase):
    """An automatic send never closes a /x window: the phase stays owed and goes out once the window has closed."""

    def test_an_owed_send_waits_for_the_window(self):
        self.start()
        rid = self.the_run()["id"]
        self.now = T0 + timedelta(minutes=1)
        self.msg("my on-record answer to phase one")
        self.now = T0 + timedelta(minutes=2)  # his /next, with Telegram failing for that send: phase 2 is owed

        def down(cfg, method, payload):
            raise telegram.TelegramError("502 Bad Gateway")
        with mock.patch.object(telegram, "_call", down):
            programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.assertTrue(self.the_run()["sessions"]["1"]["phases"]["2"].get("send_due"))
        self.now = T0 + timedelta(minutes=3)
        self.msg("/x")
        self.now = T0 + timedelta(minutes=4)
        self.msg("private A: the thing I don't want kept")
        self.now = T0 + timedelta(minutes=15)  # the next tick
        privacy._CACHE.clear()
        sends = len(self.bot.phase_sends())
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.phase_sends()), sends)  # held: the window is his
        self.assertTrue(self.the_run()["sessions"]["1"]["phases"]["2"].get("send_due"))
        self.assertTrue(any("off the record" in line for line in self.log))
        self.now = T0 + timedelta(minutes=16)
        out = self.msg("private B: still the private thought")
        self.assertIn("off the record", out["text"].lower())
        body = self.entry(1).read_text(encoding="utf-8")
        self.assertNotIn("private A", body)
        self.assertNotIn("private B", body)
        # after the window's first quiet_minutes of silence the tick delivers the phase
        self.now = T0 + timedelta(minutes=16 + 46)
        privacy._CACHE.clear()
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.phase_sends()), sends + 1)
        self.assertFalse(self.the_run()["sessions"]["1"]["phases"]["2"].get("send_due"))

    def test_a_ritual_sent_in_the_same_tick_gets_his_reply_before_the_held_phase_goes(self):
        """The tick sends rituals first, so a ritual that ends the window is sent after the tick's ``now`` but before
        the programs step reads the clock. The held phase must not take the ritual's place in that tick: his
        next message belongs to the ritual until the next tick has put the phase after it."""
        self.start()
        rid = self.the_run()["id"]
        self.now = T0 + timedelta(minutes=1)
        self.msg("my on-record answer to phase one")
        self.now = T0 + timedelta(minutes=2)

        def down(cfg, method, payload):
            raise telegram.TelegramError("502 Bad Gateway")
        with mock.patch.object(telegram, "_call", down):
            programs.advance(self.cfg, rid, self.now, self.log, reason="next")
        self.now = T0 + timedelta(minutes=3)
        self.msg("/x")
        self.now = T0 + timedelta(minutes=4)
        self.msg("private A")
        tick = T0 + timedelta(minutes=20)  # the tick's ``now``; its send step then sends the evening ritual
        add_prompt(self.cfg, kind="evening", local_date="2026-10-20", sent=tick + timedelta(seconds=2),
                   message_id="9001")
        sends = len(self.bot.phase_sends())
        self.now = tick + timedelta(seconds=30)  # (the clock when the programs step runs, after the send step)
        privacy._CACHE.clear()
        programs.step(self.cfg, tick, self.log)
        self.assertEqual(len(self.bot.phase_sends()), sends)  # the ritual is the newest message: phase 2 waits
        self.assertTrue(self.the_run()["sessions"]["1"]["phases"]["2"].get("send_due"))
        self.now = tick + timedelta(minutes=5)  # the next tick: the window is closed at both times
        privacy._CACHE.clear()
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.phase_sends()), sends + 1)

    def test_a_bare_x_during_the_reflect_model_call_holds_the_send(self):
        self.start()
        rid = self.the_run()["id"]
        self.now = T0 + timedelta(minutes=1)
        self.msg("my on-record answer to phase one")
        calls = []

        def model(query):
            calls.append(query)
            if len(calls) == 1:  # he goes off the record while she writes
                self.now = T0 + timedelta(minutes=3, seconds=10)
                self.msg("/x")
                self.now = T0 + timedelta(minutes=3, seconds=20)
                self.msg("private A: said while she was writing")
                self.now = T0 + timedelta(minutes=3, seconds=40)  # the model answers
            return REFLECT
        self.model = model
        self.now = T0 + timedelta(minutes=3, seconds=5)
        privacy._CACHE.clear()
        sends = len(self.bot.phase_sends())
        programs.settle(self.cfg, rid, self.now, self.log)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(self.bot.phase_sends()), sends)  # not sent: it would have ended his window
        self.assertEqual(self.where(), (1, 2))
        self.assertTrue(self.the_run()["sessions"]["1"]["phases"]["2"].get("send_due"))
        self.now = T0 + timedelta(minutes=4)
        out = self.msg("private B: carrying on privately")
        self.assertIn("off the record", out["text"].lower())
        # a tick inside the window holds it again, without asking the model first
        self.now = T0 + timedelta(minutes=10)
        privacy._CACHE.clear()
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual((len(calls), len(self.bot.phase_sends())), (1, sends))
        body = self.entry(1).read_text(encoding="utf-8")
        self.assertNotIn("private A", body)
        self.assertNotIn("private B", body)
        self.assertEqual(entries.phase_answers(body), {1: "my on-record answer to phase one"})
        self.now = T0 + timedelta(minutes=4 + 46)
        privacy._CACHE.clear()
        programs.step(self.cfg, self.now, self.log)
        self.assertEqual(len(self.bot.phase_sends()), sends + 1)

    def test_his_own_next_still_goes_out_inside_a_window(self):
        self.start()
        rid = self.the_run()["id"]
        self.now = T0 + timedelta(minutes=1)
        self.msg("my on-record answer to phase one")
        self.now = T0 + timedelta(minutes=2)
        self.msg("/x")
        self.now = T0 + timedelta(minutes=3)
        privacy._CACHE.clear()
        sends = len(self.bot.phase_sends())
        self.assertTrue(programs.advance(self.cfg, rid, self.now, self.log, reason="next"))
        self.assertEqual(len(self.bot.phase_sends()), sends + 1)


class WindowNoteOffFailureTest(HookBase):
    def test_a_window_message_stays_off_the_record_when_recording_it_fails(self):
        self.start()
        self.now = T0 + timedelta(minutes=1)
        self.msg("/x")
        self.now = T0 + timedelta(minutes=2)
        with mock.patch.object(conversations, "note_off", side_effect=OSError("No space left on device")):
            out = self.msg("private A: inside the window")
        self.assertIn("off the record", out["text"].lower())
        self.assertIn("private A: inside the window", out["text"])
        body = self.entry(1).read_text(encoding="utf-8") if self.entry(1).is_file() else ""
        self.assertNotIn("private A", body)


def _sessions_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        "create table sessions (id text primary key, source text, chat_id text, session_key text, "
        "user_id text, started_at real, ended_at real, parent_session_id text, end_reason text);"
        "create table messages (id integer primary key autoincrement, session_id text, role text, "
        "content text, timestamp real, platform_message_id text, display_kind text, tool_calls text, "
        "display_metadata text, active integer default 1);")
    con.execute("insert into sessions values ('s', 'telegram', ?, 'k', 'u', 1.0, null, null, null)", (CHAT,))
    con.commit()
    con.close()


def _put(db: Path, role: str, text: str, ts, pmid: str) -> int:
    con = sqlite3.connect(db)
    cur = con.execute("insert into messages (session_id, role, content, timestamp, platform_message_id) "
                      "values ('s', ?, ?, ?, ?)", (role, text, ts, pmid))
    con.commit()
    con.close()
    return cur.lastrowid


class NullTimeTest(CleanEnv):
    """A row with no time is not skipped by a time-bounded read: the chat is unreadable, and privacy fails closed."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.cfg = make_cfg(t, names=NAMES)
        self.db = t / "state.db"
        self.cfg.hermes["state_db"] = str(self.db)
        _sessions_db(self.db)
        self.t0 = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)

    def test_a_busy_path_bare_x_with_no_time_is_not_swept(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-20", sent=self.t0 - timedelta(minutes=10),
                   message_id="9001")
        _put(self.db, "user", "/x", None, "501")
        _put(self.db, "user", "the private thing after it", self.t0.timestamp() + 60, "502")
        now = self.t0 + timedelta(minutes=20)
        privacy._CACHE.clear()
        self.assertTrue(privacy.view(self.cfg, now).closed)
        sweep.run(self.cfg, now, [])
        body = "".join(p.read_text(encoding="utf-8") for p in self.cfg.root.glob("*.md"))
        self.assertNotIn("the private thing", body)

    def test_both_transcripts_agree_on_a_row_with_no_time(self):
        early = _put(self.db, "user", "an early message", self.t0.timestamp(), "500")
        _put(self.db, "user", "/x", None, "501")
        _put(self.db, "user", "after it", self.t0.timestamp() + 60, "502")
        since = self.t0 + timedelta(minutes=30)  # after both timed rows: only the untimed one could be in range
        for chat in (HermesTranscript(self.cfg, self.db), FakeTranscript.over_sessions(self.cfg, self.db)):
            with self.subTest(type(chat).__name__):
                with self.assertRaises(ChatUnreadable):
                    chat.rows(since=since)
                with self.assertRaises(ChatUnreadable):
                    chat.rows()
                with self.assertRaises(ChatUnreadable):
                    chat.tail(2)
                # rows wholly before it, by id, are still readable
                self.assertEqual([r.text for r in chat.rows(upto_id=early)], ["an early message"])


class MergedVoiceTurnTest(CleanEnv):
    def test_an_off_the_record_message_merged_into_a_voice_turn_is_not_written(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            cfg = make_cfg(t, names=NAMES)
            now = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)
            add_prompt(cfg, kind="evening", local_date="2026-10-14", sent=now - timedelta(minutes=20),
                       message_id="700")
            media = t / "v.ogg"
            media.write_bytes(b"\x00ogg")
            v = inbound.receive(cfg, inbound.Inbound(platform="telegram", chat_id=CHAT, message_id="901", text="",
                                                     at=now, kind="voice", media_path=str(media), mime="audio/ogg",
                                                     reply_to="700"))
            x = inbound.receive(cfg, inbound.Inbound(platform="telegram", chat_id=CHAT, message_id="902",
                                                     text="/x a private aside", at=now + timedelta(seconds=5)))
            self.assertEqual((v.action, x.action), ("rewrite", "rewrite"))
            merged = '"slept badly"\n\n' + v.text + "\n\n" + x.text
            inbound.begin_turn(cfg, turncontext.Turn("telegram", "s", "t", merged, now + timedelta(seconds=10)))
            body = (cfg.root / "2026-10-14-evening.md").read_text(encoding="utf-8")
            self.assertIn("slept badly", body)
            self.assertNotIn("a private aside", body)
            self.assertNotIn("Off the record", body)
