"""What #4's re-take costs a view (review of PRIV4): bounded, not a read back to the conversation's start.

A compaction copy whose original the view did not read is answered by a pass over a slice around the original
(``privacy._originals``), not by re-taking the view from the original's time. Over a synthetic sessions database
with Hermes' own indexes (one long-lived conversation whose first exchange a recent compaction carried), the view
reads a bounded number of rows, and its time stays within a small factor of the same view with no such copy.

As a harness (prints the numbers; no assertion), from a checkout's ``tests/``::

    python3 tests/test_privacy_retake_cost.py [days] [rows]      # default 60 days, 20000 rows
"""
import sqlite3
import statistics
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import make_cfg

from journal import privacy

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)
CHAT = "100200300"

# Hermes' sessions and messages tables (the columns the journal reads) and the indexes on them, as its schema has
SCHEMA = """
create table sessions (id text primary key, source text, chat_id text, session_key text, user_id text,
                       started_at real, ended_at real, parent_session_id text, end_reason text);
create table messages (id integer primary key autoincrement, session_id text not null, role text not null,
                       content text, tool_calls text, timestamp real not null, platform_message_id text,
                       display_kind text, display_metadata text, active integer not null default 1,
                       compacted integer not null default 0, display_order integer, display_identity blob);
create index idx_sessions_source on sessions(source);
create index idx_sessions_source_id on sessions(source, id);
create index idx_sessions_parent on sessions(parent_session_id);
create index idx_sessions_started on sessions(started_at desc);
create index idx_messages_session on messages(session_id, timestamp);
create index idx_messages_session_id on messages(session_id, id);
create index idx_messages_session_active on messages(session_id, active, timestamp);
"""


def build(path: Path, days: int, rows: int, copy: bool) -> None:
    """One conversation of ``rows`` rows over ``days`` days ending at NOW, both sides, all public. With ``copy``,
    a compaction ten minutes ago rotated it into a child session that carries its first exchange (days old: the
    view does not read it) and its last one."""
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    begin = NOW - timedelta(days=days)
    con.execute("insert into sessions values ('tg', 'telegram', ?, 'k', ?, ?, null, null, null)",
                (CHAT, CHAT, begin.timestamp()))
    step = (NOW - timedelta(minutes=30) - begin) / rows
    data = []
    for i in range(rows):
        at = (begin + step * i).timestamp()
        if i % 2 == 0:
            data.append(("tg", "user", f"public words number {i}", at, str(1000 + i)))
        else:
            data.append(("tg", "assistant", f"her public answer number {i}", at, None))
    con.executemany("insert into messages (session_id, role, content, timestamp, platform_message_id) "
                    "values (?, ?, ?, ?, ?)", data)
    if copy:
        at = (NOW - timedelta(minutes=10)).timestamp()
        con.execute("update sessions set ended_at = ?, end_reason = 'compression' where id = 'tg'", (at,))
        con.execute("insert into sessions values ('tg2', 'telegram', ?, 'k', ?, ?, null, 'tg', null)",
                    (CHAT, CHAT, at))
        carried = [data[0], data[1], data[-2], data[-1]]
        con.executemany("insert into messages (session_id, role, content, timestamp, platform_message_id) "
                        "values ('tg2', ?, ?, ?, ?)", [(r, c, at, mid) for _s, r, c, _t, mid in carried])
    con.commit()
    con.close()


class Bench:
    def __init__(self, days: int, rows: int, copy: bool):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.cfg = make_cfg(t)
        self.db = t / "state.db"
        build(self.db, days, rows, copy)
        self.cfg.hermes["state_db"] = str(self.db)
        privacy.update(self.cfg, NOW - timedelta(minutes=5))  # a tick saved the pass five minutes ago

    def view(self) -> privacy.View:
        privacy._CACHE.clear()  # every view uncached, as the gateway's first after a change
        return privacy.view(self.cfg, NOW)

    def seconds(self, n: int = 9) -> float:
        out = []
        for _ in range(n):
            t0 = time.perf_counter()
            v = self.view()
            out.append(time.perf_counter() - t0)
            assert not v.closed, getattr(v, "why", "")
        return statistics.median(out)

    def close(self):
        self.tmp.cleanup()


class RetakeCostTest(unittest.TestCase):
    """10 days of 6000 rows, so the suite stays quick: the copy's original is 10 days old, five times
    ``RECOMPUTE``."""
    DAYS, ROWS = 10, 6000

    def setUp(self):
        self.plain = Bench(self.DAYS, self.ROWS, copy=False)
        self.copied = Bench(self.DAYS, self.ROWS, copy=True)

    def tearDown(self):
        self.plain.close()
        self.copied.close()

    def test_the_copy_is_answered_and_public(self):
        v = self.copied.view()
        self.assertFalse(v.closed, getattr(v, "why", ""))
        self.assertEqual(len(v.copies), 4)
        self.assertEqual(v.private, {})
        self.assertEqual(v.start, NOW - timedelta(minutes=5) - privacy.RECOMPUTE)  # the view did not move back

    def test_the_tick_logs_the_re_take(self):
        """In the tick's log (``last-tick.json``), as counts: never a row's words."""
        log = []
        privacy.update(self.copied.cfg, NOW, log)
        self.assertEqual(len(log), 1, log)
        self.assertRegex(log[0], r"^privacy: answered 2 compaction-copy original\(s\) older than the view from 1 "
                                 r"slice\(s\) of \d+ rows$")
        log = []
        privacy.update(self.plain.cfg, NOW, log)
        self.assertEqual(log, [])

    def test_the_rows_read_are_bounded(self):
        """The view reads its own hours, the originals by id, and a slice around them: not the ten days."""
        chat = self.copied.cfg.transcript()
        real = type(chat).rows
        read = []

        def counting(t, *a, **k):
            out = real(t, *a, **k)
            read.append(len(out))
            return out

        from unittest import mock
        with mock.patch.object(type(chat), "rows", counting):
            self.copied.view()
        per_day = self.ROWS / self.DAYS
        # the view's own 48 hours, and a slice of RECOMPUTE before the original and after it: three times 48 hours
        self.assertLess(sum(read), per_day * 2 * 4, read)
        self.assertLess(sum(read), self.ROWS / 2, read)

    def test_the_view_stays_within_a_small_factor(self):
        base, with_copy = self.plain.seconds(), self.copied.seconds()
        self.assertLess(with_copy, base * 4 + 0.05, (base, with_copy))


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    rows = int(sys.argv[2]) if len(sys.argv) > 2 else 20000
    for copy in (False, True):
        b = Bench(days, rows, copy)
        try:
            v = b.view()
            print(f"{days} days, {rows} rows, copy={copy}: {b.seconds() * 1000:.0f} ms per uncached view "
                  f"(copies {len(v.copies)}, private {len(v.private)}, closed {v.closed})")
        finally:
            b.close()
