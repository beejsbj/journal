"""The Hermes transcript adapter reads every row the way the readers read it before the ports existed.

``tests/golden/hermes_rows.json`` was generated from the code before WP7 (``sweep.read``, ``conversations.classify``
and ``_caption``, ``context.clean_user_text``, ``recovery._his_text`` and ``_outage_sign``, ``privacy.marked`` and
``compute``, and the readers' own SQL filters run by SQLite) over an anonymised corpus of every row shape those
readers handled. Each case replays one raw row through ``journal.hermes.transcript.to_row`` and checks the reading
each reader now takes from the ``Row`` against what that reader did then."""
import dataclasses
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import ROOT

from journal import conversations, ledger, privacy, recovery, sweep
from journal.config import Config, Names
from journal.hermes import transcript
from journal.ports import ChatUnreadable, NullTranscript, Row, Transcript

GOLDEN = json.loads((ROOT / "tests" / "golden" / "hermes_rows.json").read_text(encoding="utf-8"))
NAMES = Names(user="Sam", companion="Ada", emoji="📓", pronouns="they/them/their")


class Open:
    """A privacy view that marks nothing: the per-row reading alone."""
    closed = False

    def row(self, rid):
        return False


def event(ev):
    if ev is None:
        return None
    d = dataclasses.asdict(ev)
    d["at"] = ev.at.isoformat()
    return d


class GoldenTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = Config(root=t / "Journal", state_dir=t / "state", chat_id=GOLDEN["chat_id"], names=NAMES,
                          timezone="UTC")
        self.cfg.root.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def cases(self):
        for case in GOLDEN["rows"]:
            yield case["raw"], case["expected"], transcript.to_row(case["raw"])

    def test_every_row_reads_as_it_did(self):
        data = ledger.snapshot(self.cfg.ledger_path)
        saved = conversations._Saved(self.cfg, data)
        for raw, exp, row in self.cases():
            with self.subTest(id=raw["id"], content=(raw["content"] or "")[:40]):
                self.assertIsInstance(row, Row)
                self.assertEqual(event(conversations.classify(self.cfg, row, data, Open(), saved, {})),
                                 exp["classify"])
                if raw["role"] == "user":
                    read = exp["read"]
                    cron = read["kind"] == "system" and read["text"].startswith("[Cron delivery:")
                    self.assertEqual(row.kind, "boundary" if cron else read["kind"])
                    self.assertEqual((row.text, row.platform_message_id, row.quote, row.source, row.audio_path,
                                      row.meta.get("file_name")),
                                     (read["text"], read["message_id"], read["quote"], read["source"],
                                      read["audio_path"], read.get("file_name")))
                    if row.kind == "system":  # (a delivery is a boundary: its caption was never read)
                        self.assertEqual(row.meta["caption"], exp["caption"])
                    self.assertEqual(row.meta["loose"], exp["loose"])
                    self.assertEqual(recovery.his_words(row), exp["his_text"])
                    self.assertEqual(not row.meta["delivery"] and row.kind != "mirror", exp["pack"])
                    self.assertEqual(sweep.message(row), exp["parse"])
                    self.assertEqual(privacy.row_marked(self.cfg, row, set()), exp["marked"])
                    self.assertEqual(privacy.row_marked(self.cfg, row, {"13", "31", "46", "52"}),
                                     exp["marked_by_id"])
                    self.assertEqual(bool(row.meta["notice"]) or row.kind in ("mirror", "empty", "system", "boundary"),
                                     exp["privacy_skip"])
                else:
                    self.assertIn(row.kind, ("hers", "tool_step", "failed_turn", "turn_end", "frame", "empty"))
                    self.assertEqual(row.meta["ends_turn"], exp["ends"])
                    got = ("skip" if not row.text or row.kind == "tool_step" else
                           "failure" if row.kind == "failed_turn" else "real")
                    self.assertEqual(got, exp["recovery"])
                    self.assertEqual(row.meta["outage"], exp["outage"])
                    self.assertEqual(row.meta["reply"], exp["reply"])

    def test_the_privacy_pass_over_the_corpus_is_unchanged(self):
        c = GOLDEN["compute"]
        rows = [row for _raw, _exp, row in self.cases()]
        v = privacy.compute(self.cfg, rows, [datetime.fromisoformat(t) for t in c["hook_starts"]],
                            [datetime.fromisoformat(t) for t in c["sends"]], set(c["off_ids"]))
        self.assertEqual(sorted(v.private), c["private"])
        self.assertEqual({str(k): o for k, o in sorted(v.copies.items())}, c["copies"])
        self.assertEqual([[s.isoformat(), e.isoformat() if e else None] for s, e in v.windows], c["windows"])
        self.assertEqual((v.taint, v.answered), (c["taint"], c["answered"]))
        self.assertEqual({k: x.isoformat() for k, x in v.window.items()} if v.window else None, c["window"])

    def test_a_row_without_a_usable_time_is_no_row(self):
        self.assertIsNone(transcript.to_row({"id": 1, "ts": None, "role": "user", "content": "x"}))
        self.assertIsNone(transcript.to_row({"id": 1, "ts": "soon", "role": "user", "content": "x"}))


class CorpusDB(unittest.TestCase):
    """A sessions database holding the corpus (and one row of another chat, and one in-place compaction copy)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = Config(root=t / "Journal", state_dir=t / "state", chat_id=GOLDEN["chat_id"], names=NAMES,
                          timezone="UTC")
        self.db = t / "state.db"
        con = sqlite3.connect(self.db)
        con.executescript(
            "create table sessions (id text primary key, source text, chat_id text, session_key text, user_id text, "
            "started_at real, ended_at real, parent_session_id text, end_reason text);"
            "create table messages (id integer primary key autoincrement, session_id text, role text, content text, "
            "timestamp real, platform_message_id text, display_kind text, tool_calls text, display_metadata text, "
            "active integer default 1);")
        con.execute("insert into sessions values ('s', 'telegram', ?, 'k', 'u', 1.0, null, null, null)",
                    (GOLDEN["chat_id"],))
        con.execute("insert into sessions values ('o', 'telegram', '42', 'k2', 'v', 1.0, null, null, null)")
        self.originals = [c["raw"] for c in GOLDEN["rows"] if not c["raw"]["copy"]]
        for r in self.originals:
            con.execute("insert into messages (id, session_id, role, content, timestamp, platform_message_id, "
                        "display_kind, tool_calls, display_metadata, active) values (?, 's', ?, ?, ?, ?, ?, ?, ?, ?)",
                        (r["id"], r["role"], r["content"], r["ts"], r["pmid"], r["display_kind"], r["tool_calls"],
                         r["display_metadata"], r["active"]))
        last = self.originals[-1]
        con.execute("insert into messages (session_id, role, content, timestamp) values ('o', 'user', 'other chat', ?)",
                    (last["ts"],))
        # an in-place compaction copy: same session, role, time, words and id
        first = self.originals[0]
        con.execute("insert into messages (session_id, role, content, timestamp, platform_message_id) "
                    "values ('s', 'user', ?, ?, ?)", (first["content"], first["ts"], first["pmid"]))
        con.commit()
        con.close()
        self.chat = self.cfg.transcript(self.db)

    def tearDown(self):
        self.tmp.cleanup()


class DatabaseTest(CorpusDB):
    """``HermesTranscript`` over the corpus: the same rows, in stored order, the compaction copies found by the
    database, and the cursor helpers."""

    def test_rows_are_the_normalised_corpus_in_stored_order(self):
        self.assertIsInstance(self.chat, Transcript)
        rows = self.chat.rows()
        self.assertEqual([r.id for r in rows][:len(self.originals)], [r["id"] for r in self.originals])
        expected = {r["id"]: transcript.to_row(r) for r in self.originals}
        for row in rows[:len(self.originals)]:
            want = expected[row.id]
            self.assertEqual((row.kind, row.text, row.platform_message_id, row.copy), (want.kind, want.text,
                                                                                    want.platform_message_id, None))
            self.assertEqual(dict(row.meta), dict(want.meta))
        copy = rows[-1]
        self.assertEqual((copy.copy, copy.text), (self.originals[0]["id"], "thinking about the call tomorrow"))
        self.assertNotIn("other chat", [r.text for r in rows])  # another chat's rows are never read

    def test_filters_and_cursor_helpers(self):
        rows = self.chat.rows()
        mid = rows[10]
        self.assertEqual([r.id for r in self.chat.rows(after_id=mid.id)], [r.id for r in rows if r.id > mid.id])
        self.assertEqual([r.id for r in self.chat.rows(since=mid.at)], [r.id for r in rows if r.at >= mid.at])
        self.assertEqual([r.id for r in self.chat.rows(upto_id=mid.id)], [r.id for r in rows if r.id <= mid.id])
        self.assertEqual([r.id for r in self.chat.tail(3)], [r.id for r in rows[-3:]])
        self.assertEqual(self.chat.max_id(), rows[-1].id)
        self.assertEqual(self.chat.anchor(mid.id), {"id": mid.id, "uid": None, "ts": mid.at.timestamp()})
        self.assertIsNone(self.chat.anchor(0))
        self.assertEqual(self.chat.first_id(mid.at), mid.id)
        self.assertEqual(self.chat.last_activity(mid.at), rows[9].at)
        self.assertIsNone(self.chat.last_activity(rows[0].at))
        self.assertFalse(self.chat.busy())  # no lease table: an older Hermes, never busy
        self.assertTrue(self.chat.available())
        self.assertEqual(self.chat.source, str(self.db))

    def test_a_missing_database_raises_what_the_readers_catch(self):
        chat = self.cfg.transcript(Path(self.tmp.name) / "nope.db")
        self.assertFalse(chat.available())
        with self.assertRaises(chat.errors):
            chat.rows()
        with self.assertRaises(FileNotFoundError):
            chat.max_id()

    def test_no_database_is_the_null_transcript(self):
        chat = self.cfg.transcript()  # standalone: no hermes_home, no hermes.state_db
        self.assertIsInstance(chat, NullTranscript)
        self.assertIsInstance(chat, Transcript)
        self.assertEqual((chat.source, chat.rows(), chat.tail(5), chat.max_id(), chat.anchor(3), chat.busy(),
                          chat.mirror("x"), chat.available()), (None, [], [], 0, None, False, False, False))
        self.cfg.hermes["state_db"] = str(self.db)
        self.assertEqual(self.cfg.transcript().source, str(self.db))
        self.cfg.adapter = "none"
        self.assertIsInstance(self.cfg.transcript(), NullTranscript)
        self.assertIsNone(self.cfg.state_db)
        self.assertIsInstance(self.cfg.transcript(self.db), NullTranscript)

    def test_the_old_tuple_readers_still_answer(self):
        tuples = conversations.rows(self.cfg, self.db, 0)
        self.assertEqual(len(tuples), len(self.originals) + 1)
        self.assertEqual(tuples[-1][8], self.originals[0]["id"])
        his = sweep.rows(self.cfg, self.db, 0, 0)
        self.assertEqual(len(his), sum(1 for r in self.originals if r["role"] == "user") + 1)
        self.assertEqual(sweep.read("hello", "5")["kind"], "his")


class UnreadableTimeTest(CorpusDB):
    """A row whose time cannot be read must not be skipped on the way to the privacy pass: a skipped bare ``/x``
    would let the words after it (and her answers) into the journal."""

    def put(self, role, text, ts, pmid):
        con = sqlite3.connect(self.db)
        con.execute("insert into messages (session_id, role, content, timestamp, platform_message_id) "
                    "values ('s', ?, ?, ?, ?)", (role, text, ts, pmid))
        con.commit()
        con.close()

    def test_rows_and_tail_refuse_the_chat(self):
        self.put("user", "/x", self.originals[-1]["ts"] * 1000, "9001")  # milliseconds: out of range as seconds
        for read in (self.chat.rows, lambda: self.chat.tail(5)):
            with self.assertRaises(ChatUnreadable):
                read()
        self.assertTrue(issubclass(ChatUnreadable, self.chat.errors))  # the readers' own catch
        self.assertEqual(len(self.chat.rows(after_id=self.chat.max_id())), 0)  # past the row, the chat reads

    def test_a_bare_x_with_no_readable_time_closes_the_pass(self):
        last = self.originals[-1]["ts"]
        self.put("user", "/x", last * 1000, "9001")
        self.put("user", "the private thing after it", last + 60, "9002")
        privacy._CACHE.clear()
        now = datetime.fromtimestamp(last, timezone.utc) + timedelta(hours=1)
        v = privacy.view(self.cfg, now, db=self.db)
        self.assertTrue(v.closed)
        self.assertIn("ChatUnreadable", v.why)


class DoctorSampleTest(CorpusDB):
    """``journal doctor --deep`` reads the newest rows back through the transcript and counts what they read as."""

    def setUp(self):
        super().setUp()
        self.cfg.hermes["state_db"] = str(self.db)
        self.cfg.sources = {"weather": {"enabled": False}, "telegram_history": {"enabled": True}}

    def sample(self, **kw):
        from journal import status
        return [r for r in status.doctor(self.cfg, deep=True, token=False, **kw) if r[0] == "transcript sample"]

    def test_counts_by_kind_never_words(self):
        (row,) = self.sample()
        self.assertEqual(row[1], "ok")
        self.assertIn("newest 50 row(s)", row[2])
        self.assertIn("read as empty or system", row[2])
        tail = self.chat.tail(50)
        blank = sum(1 for r in tail if r.role == "user" and r.kind in ("empty", "system"))
        self.assertIn(f"{blank} read as empty or system", row[2])
        for r in tail:
            if r.text and len(r.text) > 12:
                self.assertNotIn(r.text, row[2])

    def test_journal_mirrors_and_deliveries_alone_are_not_a_changed_store(self):
        """A few quiet days (prompts sent, nothing said back) must not read as 'the agent changed how it stores
        messages', which DEPLOY says to roll back on."""
        con = sqlite3.connect(self.db)
        con.execute("delete from messages where session_id = 's'")
        t = self.originals[0]["ts"]
        for i in range(5):
            con.execute("insert into messages (session_id, role, content, timestamp) values ('s', 'user', ?, ?)",
                        (f"[Journal: Ada sent the evening prompt at 2{i}:00 UTC]\nEvening journal", t + i))
        con.commit()
        con.close()
        (row,) = self.sample()
        self.assertEqual(row[1], "ok")
        self.assertIn("5 journal mirrors", row[2])

    def test_not_from_the_web_and_not_without_deep(self):
        from journal import status
        self.assertEqual(self.sample(web=True), [])
        self.assertEqual([r for r in status.doctor(self.cfg, token=False) if r[0] == "transcript sample"], [])

    def test_a_store_nothing_reads_as_his_words_in_warns(self):
        con = sqlite3.connect(self.db)
        con.execute("update messages set content = '[System note: x]' where role = 'user'")
        con.commit()
        con.close()
        (row,) = self.sample()
        self.assertEqual(row[1], "warn")
        self.cfg.hermes["state_db"] = str(Path(self.tmp.name) / "nope.db")
        self.assertEqual(self.sample()[0][1], "warn")
        self.cfg.hermes["state_db"] = None
        self.assertEqual(self.sample()[0][1:], ("ok", "no chat transcript to read (standalone)"))


if __name__ == "__main__":
    unittest.main()
