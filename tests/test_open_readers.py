"""OPEN-CORE: the readers of practice records moved to slot states (docs/OPEN-PROMPTS.md, *The map*): Today's rows and
the today block, ``status``, ``standing.unanswered`` (and the opener's missed note), the web app's week strip and
insights; and the engine running alone, with no Hermes adapter, serve or MCP in the package."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from helpers import CHAT, NEUTRAL, ROOT, CleanEnv, make_cfg

from serve_helpers import NOW, ServeCase

from journal import actions, ledger, prompts, standing, status, today
from journal.serve import api

IST = ZoneInfo("Asia/Kolkata")
WED = datetime(2026, 10, 14, 0, 0, tzinfo=IST)


def at(h, m=0, day=WED):
    return day.replace(hour=h, minute=m)


class ReaderCase(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name), names=NEUTRAL)

    def add(self, **record):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append(record)
        return record

    def delivered(self, pid, kind, local_date, sent, captures=None):
        return self.add(id=pid, kind=kind, local_date=local_date, chat_id=CHAT, message_id="700",
                        sent_at=sent.astimezone(timezone.utc).isoformat(), opener="Hi", opener_source="ada",
                        **({"captures": captures} if captures else {}))


class TodayTest(ReaderCase):
    def test_rows_say_where_things_happened(self):
        actions.answer(self.cfg, "morning", "slept well", at(5, 40), via="web", now=at(5, 40))
        data = today.collect(self.cfg, at(12, 30).astimezone(timezone.utc))
        rows = data["practices"]["rows"]
        self.assertEqual(rows[0], {"id": "morning", "state": "answered", "at": "06:00", "answered": "05:40",
                                   "via": "web"})
        self.assertEqual(rows[1], {"id": "evening", "state": "upcoming", "at": "23:00"})
        self.assertEqual(rows[2], {"id": "midweek", "state": "due", "at": "12:00"})
        self.assertEqual(data["practices"]["open"], [])
        line = today.lines_of(data)[0]
        self.assertIn("morning answered at 05:40 in the web app", line)
        self.assertIn("midweek due at 12:00, not sent to the chat", line)
        self.assertNotIn("awaiting a reply", "\n".join(today.lines_of(data)))

    def test_only_a_delivered_prompt_is_awaiting_a_reply_in_the_chat(self):
        self.delivered("2026-10-14-midweek", "midweek", "2026-10-14", at(12))
        data = today.collect(self.cfg, at(12, 30).astimezone(timezone.utc))
        self.assertEqual(data["practices"]["open"], ["midweek"])
        self.assertIn("- open prompt awaiting a reply: midweek", today.lines_of(data))

    def test_a_slot_never_delivered_reads_missed_with_its_time(self):
        data = today.collect(self.cfg, at(20).astimezone(timezone.utc))
        morning = data["practices"]["rows"][0]
        self.assertEqual(morning, {"id": "morning", "state": "missed", "at": "06:00"})
        # (review round 2: the chat never got it, so it is no miss of a prompt he saw; say so)
        self.assertIn("morning at 06:00, not sent to the chat (missed)", today.lines_of(data)[0])

class ServedTodayTest(ServeCase):
    def test_a_due_row_is_served_as_due(self):
        """OPEN-FACES taught the PWA the fifth state, so ``GET /today`` serves a due row as ``collect`` has it (until
        then it was served as upcoming)."""
        d = Path(self.cfg.practices_dir)
        d.mkdir(parents=True, exist_ok=True)
        (d / "stretch.md").write_text("---\nkind: practice\nid: stretch\ntitle: Stretch\nschedule.time: 09:00\n---\n"
                                      "- How does the back feel?\n", encoding="utf-8")
        status, _, body = self.request("GET", "/api/v1/today")
        self.assertEqual(status, 200)
        rows = {r["id"]: r for r in json.loads(body)["practices"]["rows"]}
        self.assertEqual(rows["stretch"], {"id": "stretch", "state": "due", "at": "09:00"})
        core = {r["id"]: r for r in today.collect(self.cfg, NOW)["practices"]["rows"]}
        self.assertEqual(core["stretch"]["state"], "due")


class StandingTest(ReaderCase):
    def test_a_face_answer_ends_the_run_of_unanswered_prompts(self):
        self.delivered("2026-10-13-evening", "evening", "2026-10-13", at(23, day=WED - timedelta(days=1)))
        run, _, _ = standing.unanswered(self.cfg, at(12))
        self.assertEqual([p["id"] for p in run], ["2026-10-13-evening"])
        actions.answer(self.cfg, "morning", "slept well", at(7), via="cli", now=at(7))
        run, answered, _ = standing.unanswered(self.cfg, at(12))
        self.assertEqual((run, answered["id"]), ([], "2026-10-14-morning"))

    def test_derived_misses_and_a_failed_send_are_not_prompts_he_saw(self):
        self.add(id="2026-10-14-morning", kind="morning", local_date="2026-10-14", chat_id=CHAT, send_attempts=3,
                 scheduled=at(6).isoformat())
        run, answered, _ = standing.unanswered(self.cfg, at(20))
        self.assertEqual((run, answered), ([], None))
        self.assertEqual(prompts.missed_note(self.cfg, run, at(20)), "")

    def test_the_missed_note_survives_a_record_with_no_send_time(self):
        note = prompts.missed_note(self.cfg, [{"kind": "morning", "scheduled": at(6).isoformat()},
                                              {"kind": "evening", "local_date": "2026-10-13"}], at(20))
        self.assertIn("morning (Wed 06:00)", note)
        self.assertIn("evening (2026-10-13)", note)


class StatusTest(ReaderCase):
    def test_status_lists_todays_slots_and_a_face_answer_is_not_due(self):
        actions.answer(self.cfg, "midweek", "done early", at(11), via="web", now=at(11))
        s = status.status(self.cfg, at(12, 1).astimezone(timezone.utc))
        self.assertNotIn("midweek", s["due_now"])
        self.assertIn({"id": "2026-10-14-midweek", "practice": "midweek", "state": "answered", "via": "web"},
                      s["slots"])
        self.assertIn("2026-10-14-midweek answered (the web app)", status.format_status(s))


class HistoryTest(ReaderCase):
    def setUp(self):
        super().setUp()
        mon, tue = WED - timedelta(days=2), WED - timedelta(days=1)
        # the ledger starts on Monday: Sunday's slots read as nothing, not as misses
        self.delivered("2026-10-12-morning", "morning", "2026-10-12", at(6, day=mon),
                       captures=[{"at": at(6, 10, day=mon).isoformat()}])
        self.delivered("2026-10-12-evening", "evening", "2026-10-12", at(23, day=mon))
        actions.answer(self.cfg, "morning", "web words", at(6, 5, day=tue), via="web", now=at(6, 5, day=tue))

    def strip(self):
        return {d["date"]: (d["state"], d["answered"], d["missed"])
                for d in api.week_strip(self.cfg, at(12).astimezone(timezone.utc))}

    def test_the_week_strip_counts_a_face_answer(self):
        strip = self.strip()
        self.assertEqual(strip["2026-10-11"], ("none", 0, 0))
        self.assertEqual(strip["2026-10-12"], ("partial", 1, 1))
        # Tuesday: the morning answered in the web app; the evening the chat never got is no miss of his
        self.assertEqual(strip["2026-10-13"], ("answered", 1, 0))

    def test_with_no_chat_a_slot_that_went_by_is_a_miss(self):
        self.cfg.platform, self.cfg.chat_id = "none", ""
        strip = self.strip()
        self.assertEqual(strip["2026-10-11"], ("none", 0, 0))          # before the ledger knew the practices
        self.assertEqual(strip["2026-10-13"], ("partial", 1, 1))       # the evening went by unanswered
        self.assertEqual(strip["2026-10-14"], ("missed", 0, 1))        # this morning went by; the evening is to come


class EngineOnlyTest(unittest.TestCase):
    """With ``hermes/``, ``serve/`` and ``mcp/`` removed from a copy of the package, the engine imports, and
    ``today``, ``status``, ``doctor`` and ``tick`` run on a journal with no chat."""

    def test_the_engine_runs_without_its_faces(self):
        with tempfile.TemporaryDirectory() as t:
            pkg = Path(t) / "src" / "journal"
            shutil.copytree(ROOT / "src" / "journal", pkg,
                            ignore=shutil.ignore_patterns("hermes", "serve", "mcp", "__pycache__"))
            home = Path(t) / "home"
            conf = Path(t) / "journal.json"
            conf.write_text(json.dumps({"root": str(Path(t) / "Journal"), "state_dir": str(Path(t) / "state"),
                                        "timezone": "Asia/Kolkata", "adapter": "none",
                                        "channel": {"platform": "none"},
                                        "sources": {"weather": {"enabled": False}}}), encoding="utf-8")
            env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home), "PYTHONPATH": str(Path(t) / "src")}
            for argv in (["today"], ["status"], ["doctor"], ["tick"]):
                out = subprocess.run([sys.executable, "-m", "journal", "--config", str(conf), *argv], env=env,
                                     cwd=t, capture_output=True, text=True, timeout=120)
                self.assertNotIn("Traceback", out.stderr, (argv, out.stderr))
                if argv != ["doctor"]:   # (doctor exits 1 on a missing journal folder: that is its job)
                    self.assertEqual(out.returncode, 0, (argv, out.stdout, out.stderr))
            leaked = subprocess.run([sys.executable, "-I", "-c",
                                     "import sys; sys.path.insert(0, sys.argv[1]); import journal.tick, journal.today, "
                                     "journal.status, journal.actions, journal.slots, journal.agent; "
                                     # (an agent's failure printed with exit 0 is recognised without the adapter)
                                     "assert journal.agent.is_failure_text('⚠ the model is unreachable'); "
                                     "print(journal.__file__)",
                                     str(Path(t) / "src")],
                                    capture_output=True, text=True, timeout=60)
            # (the copy without its faces was imported, not the checkout or an installed journal)
            self.assertEqual(leaked.stdout.strip(), str(pkg / "__init__.py"), leaked.stderr)
            self.assertFalse((pkg / "serve").exists() or (pkg / "hermes").exists() or (pkg / "mcp").exists())


if __name__ == "__main__":
    unittest.main()
