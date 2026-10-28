"""A second transcript behind the port (WP7).

The conversation, privacy, sweep and recovery suites run here a second time, unchanged, with every
``cfg.transcript()`` answered by ``helpers.FakeTranscript``: the same fixture rows, but which rows belong to the chat,
their order, the time and id bounds and the compaction copies are worked out in Python instead of by the Hermes
adapter's SQL. Both runs must be green: the readers depend on the port, not on how a transcript is stored.

``PortOnlyTest`` goes further: a transcript of hand-made ``Row``s and no sessions database at all, with
``sqlite3.connect`` made to fail, drives the writer, the privacy pass, the sweep, the outage catch-up and the pack.
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import test_conversations
import test_privacy_coverage
import test_recovery
import test_sweep
from helpers import FakeTranscript, FakeTranscriptMixin, add_prompt, make_cfg

from journal import context, conversations, entries, privacy, recovery, sweep
from journal.config import Config, Names
from journal.ports import Row

SUITES = (test_conversations, test_privacy_coverage, test_sweep, test_recovery)


def _faked(module) -> dict:
    out = {}
    for name, cls in vars(module).items():
        if (isinstance(cls, type) and issubclass(cls, unittest.TestCase) and cls.__module__ == module.__name__
                and any(n.startswith("test") for n in dir(cls))):
            key = f"Fake{module.__name__.removeprefix('test_').title().replace('_', '')}{name}"
            out[key] = type(key, (FakeTranscriptMixin, cls), {"__module__": __name__, "__qualname__": key})
    return out


for _suite in SUITES:
    _made = _faked(_suite)
    assert not set(_made) & set(globals()), f"a class name clash would drop tests: {set(_made) & set(globals())}"
    globals().update(_made)


def test_every_suite_test_is_run_a_second_time():
    """Two suites that share a class name (``ParseTest``) must both be run, not the later one twice."""
    import inspect
    for suite in SUITES:
        for name, cls in vars(suite).items():
            if (inspect.isclass(cls) and issubclass(cls, unittest.TestCase) and cls.__module__ == suite.__name__
                    and any(n.startswith("test") for n in dir(cls))):
                twin = [c for c in vars(sys.modules[__name__]).values()
                        if inspect.isclass(c) and len(c.__mro__) > 1 and c.__mro__[2:3] == (cls,)]
                assert len(twin) == 1, (suite.__name__, name, twin)


NAMES = Names(user="Sam", companion="Ada", emoji="📓", pronouns="they/them/their")


def his(rid, at, text, mid, **kw):
    return Row(rid, at, "user", kw.pop("kind", "his"), text, mid, stored=kw.pop("stored", text),
               meta={"stored_id": mid, "loose": text, "uid": None, "active": True, **kw})


def hers(rid, at, text, kind="hers", **kw):
    return Row(rid, at, "assistant", kind, text, None, stored=text,
               meta={"uid": f"u{rid}", "active": True, "ends_turn": kind != "tool_step", "reply": kind == "hers",
                     **kw})


class PortOnlyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), names=NAMES)
        self.t0 = (datetime.now(timezone.utc) - timedelta(hours=3)).replace(microsecond=0)
        self.rows = []
        self.chat = FakeTranscript(source="memory", load=lambda: _snapshot(self.rows))
        for p in (mock.patch.object(Config, "transcript", lambda cfg, db=None: self.chat),
                  mock.patch("sqlite3.connect", self.no_sql)):
            p.start()
            self.addCleanup(p.stop)
        privacy._CACHE.clear()

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def no_sql(*a, **k):
        raise AssertionError("a reader opened a database instead of asking the transcript")

    def at(self, minutes):
        return self.t0 + timedelta(minutes=minutes)

    def add(self, row):
        self.rows.append(row)

    def test_the_engine_reads_the_chat_only_through_the_port(self):
        conversations.run(self.cfg, self.at(-60), [])  # the writer's first run: its cursor
        self.add(his(1, self.at(0), "thinking about the long walk home", "101"))
        self.add(hers(2, self.at(1), "Which way did you go?"))
        self.add(his(3, self.at(2), "/x the thing I keep to myself", "102"))
        self.add(hers(4, self.at(3), "I hear you."))
        self.add(his(5, self.at(4), "back to the walk: the river path", "103"))
        self.add(hers(6, self.at(5), "Lovely."))
        log = []
        privacy.update(self.cfg, self.at(10), log)
        v = privacy.view(self.cfg, self.at(10))
        self.assertTrue(v.row(3) and v.row(4))  # his /x message and her answer to it
        self.assertFalse(v.row(1) or v.row(5))
        self.assertGreater(conversations.run(self.cfg, self.at(10), log), 0, log)
        body = "".join(p.read_text() for p in self.cfg.root.glob("*.md"))
        self.assertIn("thinking about the long walk home", body)
        self.assertIn("the river path", body)
        self.assertIn("Which way did you go?", body)
        self.assertNotIn("keep to myself", body)
        self.assertNotIn("I hear you", body)
        self.assertEqual(conversations.snapshot(self.cfg)["db"], "memory")
        lines = context.telegram_messages(self.cfg)
        self.assertEqual(len(lines), 2)
        self.assertNotIn("keep to myself", " ".join(lines))
        verify = context.make_verifier(self.cfg)
        self.assertTrue(verify("telegram:x", "the river path"))
        self.assertFalse(verify("telegram:x", "keep to myself"))
        self.assertEqual(context.companion_reply_after(self.cfg, self.at(0)), "Which way did you go?")

    def test_the_sweep_files_a_busy_path_reply_through_the_port(self):
        add_prompt(self.cfg, kind="evening", local_date=self.at(0).astimezone(self.cfg.tz).date().isoformat(),
                   sent=self.at(0), message_id="200")
        self.add(his(1, self.at(5), "slow and heavy, then better", "201"))
        log = []
        self.assertEqual(sweep.run(self.cfg, self.at(10), log), 1, log)
        self.assertEqual(sweep.run(self.cfg, self.at(11), []), 0)  # never twice
        entry = next(self.cfg.root.glob("*-evening.md"))
        self.assertIn("slow and heavy, then better", entries.raw_entry(entry.read_text()))

    def test_the_outage_catch_up_reads_through_the_port(self):
        self.add(his(1, self.at(0), "hi, back home", "301"))
        self.add(hers(2, self.at(1), "Welcome back"))
        self.add(his(3, self.at(30), "rough day", "302"))
        self.add(hers(4, self.at(31), "Your request was not processed.", kind="failed_turn", outage=True))
        found = recovery.scan(self.cfg, None, self.at(40))
        self.assertEqual([m["text"] for m in found["his"]], ["rough day"])
        self.assertTrue(recovery.unanswered(found))
        self.assertEqual(found["failures"][0]["outage"], True)


def _snapshot(rows):
    from helpers import _Snapshot
    return _Snapshot(sorted(rows, key=lambda r: r.id))


if __name__ == "__main__":
    unittest.main()
