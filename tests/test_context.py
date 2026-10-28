"""The context pack only includes configured sources, and a failing source never blocks it."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CHAT, make_cfg

from journal import context, sources

IST = ZoneInfo("Asia/Kolkata")


class PackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.now = datetime(2026, 10, 14, 6, 0, tzinfo=IST)

    def tearDown(self):
        self.tmp.cleanup()

    def test_standalone_defaults(self):
        self.cfg.sources["weather"] = {"enabled": False}
        pack = context.build(self.cfg, self.now)
        self.assertIn("Theo's current location per journal config: not set.", pack)
        self.assertIn("## Recent journal entries", pack)
        self.assertIn("## Your private notebook", pack)
        self.assertNotIn("Telegram messages", pack)  # no Hermes sessions DB outside Hermes
        self.assertNotIn("tududi", pack)
        self.assertNotIn("## Not connected", pack)

    def test_enabled_sources_appear_and_failures_are_contained(self):
        self.cfg.sources["weather"] = {"enabled": False}
        self.cfg.sources["tududi"] = {"enabled": True, "command": ["/nonexistent/mcp"]}
        self.cfg.sources["storyteller"] = {"enabled": True, "db": "/nonexistent.db"}
        self.cfg.sources["not_connected"] = "Calendar is not connected."
        pack = context.build(self.cfg, self.now)
        self.assertIn("## tududi — open tasks, habits, done this week\n_unavailable: FileNotFoundError", pack)
        self.assertIn("_Storyteller unavailable (FileNotFoundError)_", pack)
        self.assertIn("## Not connected\nCalendar is not connected.", pack)

    def test_telegram_history_from_the_sessions_db(self):
        db = Path(self.tmp.name) / "state.db"
        con = sqlite3.connect(db)
        con.executescript("create table sessions (id text, source text, chat_id text);"
                          "create table messages (session_id text, role text, timestamp real, content text, tool_calls text);")
        con.execute("insert into sessions values ('s', 'telegram', ?)", (CHAT,))
        ts = (datetime.now() - timedelta(hours=2)).timestamp()
        con.execute("insert into messages values ('s', 'user', ?, ?, '')",
                    (ts, "[Journal reply — saved …]\n\nwent to the lake"))
        con.execute("insert into messages values ('s', 'assistant', ?, 'Lovely — how was the water?', '')", (ts + 60,))
        con.execute("insert into sessions values ('other', 'telegram', '111')")  # someone else's chat
        con.execute("insert into messages values ('other', 'user', ?, 'a stranger wrote this', '')", (ts,))
        con.execute("insert into messages values ('other', 'assistant', ?, 'reply to a stranger', '')", (ts + 30,))
        con.commit()
        con.close()
        self.cfg.hermes["state_db"] = str(db)
        self.cfg.sources["weather"] = {"enabled": False}
        pack = context.build(self.cfg, self.now)
        self.assertIn("## Recent conversation with him, oldest first: his Telegram messages (last 7 days) and what "
                      "you sent him through the journal (last 3 days).", pack)
        self.assertIn("— went to the lake", pack)
        self.assertNotIn("[Journal reply", pack)
        reply = context.companion_reply_after(self.cfg, datetime.fromtimestamp(ts).astimezone(IST))
        self.assertEqual(reply, "Lovely — how was the water?")
        self.assertNotIn("stranger", pack)
        other = context.companion_reply_after(self.cfg, datetime.fromtimestamp(ts).astimezone(IST), chat_id="111")
        self.assertIsNone(other)  # a prompt recorded in another chat reads nothing: the privacy pass doesn't cover it
        verify = context.make_verifier(self.cfg)
        from journal.validate import norm
        self.assertTrue(verify("telegram:x", norm("went to the lake")))
        self.assertFalse(verify("telegram:x", norm("a stranger wrote this")))

    def test_listenbrainz_needs_a_user_and_uses_it(self):
        self.cfg.sources["listenbrainz"] = {"enabled": True, "user": ""}
        with self.assertRaises(RuntimeError):
            sources.listenbrainz(self.cfg, self.now)
        self.cfg.sources["listenbrainz"]["user"] = "sam"
        seen = []
        with mock.patch.object(sources, "_curl", lambda url, *a: seen.append(url) or '{"payload": {"listens": []}}'):
            self.assertEqual(sources.listenbrainz(self.cfg, self.now), [])
        self.assertIn("/1/user/sam/listens", seen[0])

    def test_static_checks(self):
        self.cfg.sources["scrobbler"] = {"enabled": True, "command": "definitely-not-installed-xyz"}
        self.cfg.sources["grimmory"] = {"enabled": True, "container": ""}
        rows = {name: ok for name, ok, _ in sources.check(self.cfg)}
        self.assertFalse(rows["scrobbler"])
        self.assertFalse(rows["grimmory"])
        self.assertTrue(rows["telegram_history"])  # skipped, not failing, outside Hermes


if __name__ == "__main__":
    unittest.main()
