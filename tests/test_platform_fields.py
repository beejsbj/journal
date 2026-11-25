"""PLATFORM-FIELDS release 1 (issue #50): the fields named for Telegram are read under their platform-neutral names too.

A reflection's send record is ``telegram`` on disk and the source of his chat messages is ``telegram_history``;
release 1 reads ``chat`` and ``chat_history`` as well (new first) and still writes the old names. Release 2 writes the
new ones, which is safe only if every reader already reads them: these tests are that proof. A record with only the
new name must be read, everywhere, as the same record with the old name; and a reader that missed it would send a
reflection twice."""
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CleanEnv, make_cfg, write_config

from journal import config as jconfig
from journal import conversations, entries, ledger, mirror, outage, reflect, standing, status, telegram
from journal.config import load

NOW = datetime(2026, 10, 15, 6, 0, tzinfo=timezone.utc)
SRC = Path(__file__).resolve().parent.parent / "src" / "journal"


def rename_sends(cfg, old: str, new: str) -> None:
    """Every reflection's send record, kept under ``new`` instead of ``old`` (what release 2 would have written)."""
    with ledger.locked(cfg.ledger_path) as data:
        for rec in data.get("reflections") or []:
            if old in rec:
                rec[new] = rec.pop(old)


class Fixture(unittest.TestCase):
    """A ledger with a reflection of every state a send record takes: sent, skipped for no chat, skipped as stale,
    and written but owed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.sent_at = NOW - timedelta(hours=3)
        self.path = self.cfg.root / "2026-10-14-reflection.md"
        entries.write_file(self.path, reflect.render(self.cfg, "reflection", datetime(2026, 10, 14).date(),
                                                     "**What stood out** — The rain.", [], self.sent_at))
        self.week = self.cfg.root / "2026-10-14-week.md"
        entries.write_file(self.week, reflect.render(self.cfg, "week", datetime(2026, 10, 14).date(),
                                                     "**The week** — Rain.", [], self.sent_at))
        recs = [
            {"id": "2026-10-14-reflection", "kind": "reflection", "local_date": "2026-10-14", "attempts": 1,
             "written_at": self.sent_at.isoformat(), "path": str(self.path),
             "telegram": {"message_id": "701", "sent_at": self.sent_at.isoformat(), "mirrored": True}},
            {"id": "2026-10-12-reflection", "kind": "reflection", "local_date": "2026-10-12",
             "written_at": (NOW - timedelta(days=3)).isoformat(),
             "telegram": {"skipped": "no chat", "skipped_at": (NOW - timedelta(hours=2)).isoformat()}},
            {"id": "2026-10-11-reflection", "kind": "reflection", "local_date": "2026-10-11",
             "written_at": (NOW - timedelta(hours=1)).isoformat(),
             "telegram": {"skipped": "stale", "skipped_at": (NOW - timedelta(hours=1)).isoformat()}},
            {"id": "2026-10-14-week", "kind": "week", "local_date": "2026-10-14", "path": str(self.week),
             "written_at": (NOW - timedelta(hours=1)).isoformat(), "telegram": {"attempts": 1, "error": "down"}},
        ]
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections"] = recs

    def tearDown(self):
        self.tmp.cleanup()

    def data(self) -> dict:
        return ledger.snapshot(self.cfg.ledger_path)


class ReadersTest(Fixture):
    def readings(self) -> dict:
        """What every reader of a reflection's send makes of the ledger as it stands."""
        data = self.data()
        now_local = NOW.astimezone(self.cfg.tz)
        writer = conversations._Writer(self.cfg, {}, write=False, annotate=False, via="conversation")
        from journal import context
        return {
            "standing.without_chat": standing.without_chat(data, NOW),
            "standing.too_late": standing.too_late(data, NOW),
            "outage._sent_times": outage._sent_times(data),
            "reflect._owed": {r["id"]: reflect._owed(r) for r in data["reflections"]},
            "status._reflections": status._reflections(self.cfg, data),
            "context.her_messages": [text for _, text in context.her_messages(self.cfg, now_local)],
            "conversations._sends": [(s, ref) for s, _, _, ref in writer._sends()],
        }

    def test_a_record_with_only_the_new_name_reads_as_the_old_one_does(self):
        before = self.readings()
        # (the fixture reads as intended: each reader finds something, so equal is not equal-and-empty)
        self.assertEqual(before["standing.without_chat"]["reflections"], 1)
        self.assertEqual(before["standing.too_late"]["reflections"], 1)
        self.assertEqual(before["outage._sent_times"], [self.sent_at.isoformat()])
        self.assertEqual(before["reflect._owed"], {"2026-10-14-reflection": False, "2026-10-12-reflection": False,
                                                    "2026-10-11-reflection": False, "2026-10-14-week": True})
        sent = [r for r in before["status._reflections"]["recent"] if r["id"] == "2026-10-14-reflection"][0]
        self.assertTrue(sent["sent"])
        self.assertTrue(any("your day reflection" in t for t in before["context.her_messages"]))
        self.assertEqual([ref for _, ref in before["conversations._sends"]], ["2026-10-14-reflection"])

        rename_sends(self.cfg, "telegram", "chat")
        self.assertTrue(all("telegram" not in r for r in self.data()["reflections"]))
        self.assertEqual(self.readings(), before)

    def test_the_new_name_wins_when_a_record_somehow_holds_both(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections"][0]["chat"] = {"message_id": "999", "sent_at": self.sent_at.isoformat()}
        self.assertEqual(ledger.send_of(self.data()["reflections"][0])["message_id"], "999")
        with ledger.locked(self.cfg.ledger_path) as data:
            del data["reflections"][0]["chat"]
        self.assertEqual(ledger.send_of(self.data()["reflections"][0])["message_id"], "701")

    def test_a_reader_never_makes_a_key_by_looking(self):
        rec = {"id": "x"}
        self.assertEqual(ledger.send_of(rec), {})
        self.assertEqual(ledger.send_of(None), {})
        self.assertEqual(ledger.send_of({"telegram": "damaged", "chat": []}), {})
        self.assertEqual(rec, {"id": "x"})

    def test_a_writer_changes_the_dict_the_readers_read_and_makes_the_old_name_when_there_is_none(self):
        fresh = {"id": "x"}
        ledger.send_slot(fresh)["attempts"] = 1
        self.assertEqual(fresh, {"id": "x", "telegram": {"attempts": 1}})  # release 1 writes the old name
        both = {"chat": {"message_id": "5"}}
        ledger.send_slot(both)["mirrored"] = True
        self.assertEqual(both, {"chat": {"message_id": "5", "mirrored": True}})  # never half under each name


class NothingIsSentTwiceTest(Fixture):
    """The hazard the two releases exist for: a reflection sent under the new name must not read as unsent."""

    def send(self):
        calls = []

        def call(cfg, method, payload):
            calls.append((method, payload))
            return {"message_id": 800 + len(calls)}

        with mock.patch.object(telegram, "_call", call), mock.patch.object(telegram, "token", lambda c: "T"), \
                mock.patch("journal.mirror.to_chat", lambda *a, **k: True):
            log: list[str] = []
            alerts = reflect.send(self.cfg, NOW, log)
        return calls, alerts, log

    def test_a_reflection_sent_under_the_new_name_is_not_sent_again(self):
        with ledger.locked(self.cfg.ledger_path) as data:  # only the sent one, and only under the new name
            data["reflections"] = data["reflections"][:1]
        rename_sends(self.cfg, "telegram", "chat")
        calls, alerts, _ = self.send()
        self.assertEqual((calls, alerts), ([], []))
        self.assertEqual(self.data()["reflections"][0], {**self.data()["reflections"][0], "chat": {
            "message_id": "701", "sent_at": self.sent_at.isoformat(), "mirrored": True}})

    def test_a_reflection_still_owed_is_sent_once_and_recorded_under_the_old_name(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["reflections"] = data["reflections"][3:]
        calls, _, _ = self.send()
        self.assertEqual(len(calls), 1)
        rec = self.data()["reflections"][0]
        self.assertEqual(rec["telegram"]["message_id"], "801")  # release 1 writes what it always wrote
        self.assertNotIn("chat", rec)
        self.assertEqual(self.send()[0], [])

    def test_a_skip_is_recorded_in_the_send_record_the_record_already_has(self):
        with ledger.locked(self.cfg.ledger_path) as data:  # an owed one, whose failed attempts a release 2 tick wrote
            data["reflections"] = [dict(data["reflections"][3])]
        rename_sends(self.cfg, "telegram", "chat")
        self.cfg.platform = jconfig.NO_CHANNEL  # no chat: the owed analysis is skipped
        log: list[str] = []
        reflect.send(self.cfg, NOW, log)
        rec = self.data()["reflections"][0]
        self.assertEqual(rec["chat"], {"attempts": 1, "error": "down", "skipped": "no chat",
                                       "skipped_at": NOW.isoformat()})
        self.assertNotIn("telegram", rec)

    def test_the_mirror_marks_the_send_record_whatever_name_the_queue_carried(self):
        for key in ("telegram", "chat"):
            with self.subTest(queued_under=key):
                rename_sends(self.cfg, "chat", "telegram")
                with ledger.locked(self.cfg.ledger_path) as data:
                    data["reflections"][0]["telegram"].pop("mirrored", None)
                mirror._mark(self.cfg, ("reflections", "2026-10-14-reflection", key), "queued")
                rec = self.data()["reflections"][0]
                self.assertEqual(rec["telegram"]["mirrored"], "queued")
                self.assertNotIn("chat", rec)
                rename_sends(self.cfg, "telegram", "chat")
                mirror._mark(self.cfg, ("reflections", "2026-10-14-reflection", key), True)
                rec = self.data()["reflections"][0]
                self.assertIs(rec["chat"]["mirrored"], True)
                self.assertNotIn("telegram", rec)


class TheFenceTest(unittest.TestCase):
    """No reader or writer spells the old name for itself: the ledger's two functions are the one place that does,
    so release 2 is one line there, and a new reader cannot miss the new name by copying an old pattern."""

    PATTERN = re.compile(r"""(?:get|setdefault|pop)\(\s*["']telegram["']|\[\s*["']telegram["']\s*\]""")

    def test_only_the_ledger_names_the_send_record(self):
        found = []
        for path in sorted(SRC.rglob("*.py")):
            if path.name in ("ledger.py", "config.py", "migrate.py"):  # (the last two: the ``telegram`` config section)
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if self.PATTERN.search(line):
                    found.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
        # (the mirror's reference to the send record is a name in a tuple, the one writer named for release 2)
        self.assertEqual(found, [], "a ledger send record is read through ledger.send_of and changed through "
                                    "ledger.send_slot")

    def test_the_mirror_reference_is_the_one_other_place(self):
        refs = [f"{p.relative_to(SRC)}" for p in sorted(SRC.rglob("*.py"))
                if re.search(r'ref=\([^)]*"telegram"', p.read_text(encoding="utf-8"))]
        self.assertEqual(refs, ["reflect.py"])


class SourceAliasTest(CleanEnv):
    def cfg(self, sources):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        return load(write_config(Path(t.name) / "c.json", {"root": t.name, "sources": sources}))

    def test_the_default_is_the_same_under_both_names(self):
        cfg = self.cfg({})
        for name in ("telegram_history", "chat_history"):
            self.assertEqual(cfg.source(name), {"enabled": True, "days": 7, "limit": 25})
            self.assertTrue(cfg.source_enabled(name))

    def test_the_old_name_still_configures_it(self):
        cfg = self.cfg({"telegram_history": {"enabled": False, "days": 3}})
        for name in ("telegram_history", "chat_history"):
            self.assertFalse(cfg.source_enabled(name))
            self.assertEqual(cfg.source(name)["days"], 3)

    def test_the_new_name_configures_it_over_the_old(self):
        cfg = self.cfg({"telegram_history": {"days": 3, "limit": 10}, "chat_history": {"days": 5}})
        for name in ("telegram_history", "chat_history"):
            self.assertEqual(cfg.source(name), {"enabled": True, "days": 5, "limit": 10})
        off = self.cfg({"chat_history": {"enabled": False}})
        self.assertFalse(off.source_enabled("telegram_history"))  # (what the pack and the doctor ask for)

    def test_other_sources_are_untouched(self):
        cfg = self.cfg({"chat_history": {"days": 5}, "listenbrainz": {"enabled": True, "user": "sam"}})
        self.assertEqual(cfg.source("listenbrainz"), {"enabled": True, "user": "sam"})
        self.assertEqual(cfg.source("nonesuch"), {})

    def test_no_warning_yet_for_either_name(self):
        for sources in ({"telegram_history": {"days": 3}}, {"chat_history": {"days": 3}}):
            cfg = self.cfg(sources)
            self.assertFalse([p for p in jconfig.problems(cfg) if "history" in p])


if __name__ == "__main__":
    unittest.main()
