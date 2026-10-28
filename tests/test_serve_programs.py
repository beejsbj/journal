"""``journal serve`` and programs (WP10, wired after WP4): the run routes read the engine's run files, a web block on
a program's open phase is that phase's answer and moves the run on by the program's own rule (the spawn is recorded,
and the settle it would run is run here by hand), and the transitions are spawned ``journal program …`` only from a
state they mean something in. Telegram is a list of calls; the model is unreachable."""
import re
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path
from unittest import mock

from serve_helpers import NOW
from test_serve_writes import WriteCase, checksum

from journal import agent, entries, ledger, library, privacy, programs, telegram


class ProgramCase(WriteCase):
    stub_privacy = True   # the settle's privacy pass is stubbed; NoChatTest turns that off so it can see a read

    def setUp(self):
        super().setUp()
        self.sent = []

        def call(cfg, method, payload):
            self.sent.append((method, payload))
            return {"message_id": 900 + len(self.sent)}
        def no_model(*a, **k):
            raise agent.AgentUnavailable("no model in this test")
        for p in (mock.patch.object(telegram, "_call", call), mock.patch.object(agent, "ask", no_model), mock.patch.object(telegram, "token", lambda c: "T"),
                  *([mock.patch.object(programs, "_off_the_record", lambda cfg, now: False)] if self.stub_privacy else [])):
            p.start()
            self.addCleanup(p.stop)
        self.log: list[str] = []
        self.run_ = programs.start(self.cfg, "fix-life", NOW, self.log)
        self.rid = self.run_["id"]
        self.spawned.clear()

    @property
    def stem(self):
        return programs.find_run(self.cfg, self.rid)["sessions"]["1"]["entry"]

    def run_file(self):
        return programs.find_run(self.cfg, self.rid)

    def block(self, body, want=201, **kw):
        return self.ok("POST", f"/api/v1/entries/{self.stem}/blocks", body, want=want, **kw)


class RunsReadTest(ProgramCase):
    def test_a_run_is_listed_with_its_sessions_and_where(self):
        rows = self.get_json("/api/v1/runs")
        self.assertEqual([r["id"] for r in rows], [self.rid])
        r = rows[0]
        self.assertEqual((r["program"], r["status"], r["current"], r["where"]),
                         ("fix-life", "active", {"session": 1, "phase": 1}, "1:1:1"))
        self.assertEqual(len(r["sessions"]), 10)
        self.assertEqual((r["sessions"][0]["status"], r["sessions"][0]["entry"], r["sessions"][1]["status"]),
                         ("in_progress", self.stem, "not-started"))
        self.assertIsNone(r["sessions"][1]["entry"])
        self.assertEqual(self.get_json("/api/v1/runs?status=paused"), [])
        self.assertEqual(len(self.get_json("/api/v1/runs?program=fix-life&status=active")), 1)
        self.assertEqual(self.get_json("/api/v1/runs?status=nope", status=400)["error"], "bad-request")

    def test_insights_weeks_carry_the_program_progress_of_the_week(self):
        """FRONTEND 1.6 asks for program progress per week; the route returned {} for every week (WP9's stub)."""
        day = NOW.astimezone(self.cfg.tz).date()
        rows = self.get_json(f"/api/v1/insights/weeks?from={day}&to={day}")
        self.assertEqual(rows[0]["program_progress"], {"fix-life": "0 of 10 sessions done"})
        run = self.run_file()
        run["sessions"]["1"].update(status="completed", completed_at=NOW.isoformat())
        programs._save(self.cfg, run)
        rows = self.get_json(f"/api/v1/insights/weeks?from={day}&to={day}")
        self.assertEqual(rows[0]["program_progress"], {"fix-life": "1 of 10 sessions done, 1 this week"})
        old = day - timedelta(days=21)    # three weeks before the run began: nothing to say
        rows = self.get_json(f"/api/v1/insights/weeks?from={old}&to={old}")
        self.assertEqual(rows[0]["program_progress"], {})

    def test_a_phase_that_is_owed_a_send_is_not_open(self):
        run = self.run_file()
        run["sessions"]["1"]["phases"]["1"]["send_due"] = True
        programs._save(self.cfg, run)
        ph = self.get_json(f"/api/v1/runs/{self.rid}")["phase"]
        self.assertEqual((ph["delivered"], ph["open"]), (False, False))
        self.block({"text": "x", "phase": 1}, want=409)

    def test_a_situational_run_idle_past_its_expiry_reads_as_expired_before_the_tick_closes_it(self):
        programs.pause(self.cfg, self.rid, NOW - timedelta(days=2), self.log)  # (one session at a time: ONE-PROGRAM)
        old = programs.start(self.cfg, "post-session-debrief", NOW - timedelta(days=2), self.log)
        programs.start(self.cfg, "fix-life", NOW, self.log)     # the idle debrief does not stop fix-life going on
        self.assertEqual(self.run_of(old["id"])["status"], "active")     # the file still says so
        self.assertEqual([r["id"] for r in self.get_json("/api/v1/runs?status=expired")], [old["id"]])
        self.assertEqual([r["id"] for r in self.get_json("/api/v1/runs?status=active")], [self.rid])
        one = self.get_json(f"/api/v1/runs/{old['id']}")
        self.assertEqual((one["status"], one["keyboard"]), ("expired", []))
        self.assertIsNone(one["phase"])

    def run_of(self, rid):
        return programs.find_run(self.cfg, rid)

    def test_one_run_carries_the_open_phase_and_the_controls(self):
        one = self.get_json(f"/api/v1/runs/{self.rid}")
        ph = one["phase"]
        self.assertEqual((ph["program"], ph["session"], ph["phase"], ph["open"], ph["answered"]),
                         ("fix-life", 1, 1, True, False))
        self.assertTrue(ph["header"] and ph["blocks"] and ph["sent_text"])
        self.assertEqual(one["keyboard"], ["next", "skip", "pause", "abandon"])   # no /back on the first phase
        self.assertEqual(self.request("GET", "/api/v1/runs/nope")[0], 404)
        programs.pause(self.cfg, self.rid, NOW, self.log)
        paused = self.get_json(f"/api/v1/runs/{self.rid}")
        self.assertEqual((paused["status"], paused["keyboard"], paused["phase"]["open"]), ("paused", ["resume", "abandon"], False))

    def test_insights_are_404_until_compiled(self):
        self.assertEqual(self.request("GET", f"/api/v1/runs/{self.rid}/insights")[0], 404)
        self.block({"text": "Something I do not want.", "phase": 1})
        path = programs.insights(self.cfg, self.rid, NOW, self.log)
        got = self.get_json(f"/api/v1/runs/{self.rid}/insights")
        self.assertEqual(got["stem"], path.stem)
        self.assertIn("Something I do not want.", got["body"])
        self.assertFalse(got["body"].startswith("---"))


class PhaseBlockTest(ProgramCase):
    def test_a_web_block_answers_the_open_phase_and_the_settle_is_spawned(self):
        self.assertFalse((self.cfg.root / f"{self.stem}.md").exists())     # no answer yet: no entry file
        got = self.block({"text": "The life I do not want.\n\nSecond line.", "phase": 1})
        self.assertEqual(got["advance"], "settle")
        self.assertEqual(self.spawned, [["program", "settle", self.rid]])
        body = self.entry(self.stem)
        self.assertIn("%% journal phase:1 %%\n### 09:12 IST · text\n\nThe life I do not want.\n\nSecond line.\n", body)
        p = self.prompt(f"{self.rid}-s01-p1")
        self.assertEqual((p["captures"][0]["rule"], p["captures"][0]["source"], len(p["captures"])), ("web", "web", 1))
        self.assertTrue(p["needs_edit"])    # the Telegram message folds, as after a Telegram answer
        # a second block (a correction) joins the same phase
        self.block({"text": "And one more thing.", "phase": 1})
        self.assertEqual(programs.answers(self.cfg, self.run_, 1)[1], "The life I do not want.\n\nSecond line.\n\nAnd one more thing.")
        doc = self.ok("GET", f"/api/v1/entries/{self.stem}")
        self.assertEqual([(b["phase"], b["text"]) for b in doc["blocks"] if b["kind"] == "his"],
                         [(1, "The life I do not want.\n\nSecond line."), (1, "And one more thing.")])

    def test_the_run_moves_on_when_the_spawned_settle_runs(self):
        self.block({"text": "Not this.", "phase": 1})
        self.assertEqual(programs.find_run(self.cfg, self.rid)["current"]["phase"], 1)
        self.assertFalse(programs.settle(self.cfg, self.rid, NOW + timedelta(minutes=1), self.log))   # still settling
        self.assertTrue(programs.settle(self.cfg, self.rid, NOW + timedelta(minutes=3), self.log))
        self.assertEqual(self.run_file()["current"], {"session": 1, "phase": 2})
        self.assertFalse(self.prompt(f"{self.rid}-s01-p1")["phase_open"])
        # the next phase is open for a web answer, the first no longer is
        self.block({"text": "Late.", "phase": 1}, want=409)
        self.block({"text": "The life I want.", "phase": 2})
        self.assertIn("%% journal phase:2 %%", self.entry(self.stem))

    def test_a_program_that_waits_for_next_spawns_nothing(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, f"{self.rid}-s01-p1")["advance"] = "next"
        got = self.block({"text": "Waiting for /next.", "phase": 1})
        self.assertEqual((got["advance"], self.spawned), ("next", []))

    def test_a_spawn_that_cannot_start_leaves_the_block_saved_for_the_tick(self):
        def broken(cfg, args):
            raise OSError("no fork")
        self.app.spawn = broken
        got = self.block({"text": "Saved anyway.", "phase": 1})
        self.assertEqual(got["advance"], "tick")
        self.assertIn("Saved anyway.", self.entry(self.stem))

    def test_only_the_open_phase_takes_a_block_and_a_refusal_writes_nothing(self):
        before = (checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes())
        for body, want, slug in (({"text": "x", "phase": 2}, 409, "phase-not-open"),
                                 ({"text": "x", "phase": 0}, 409, "phase-not-open"),
                                 ({"text": "x"}, 400, "bad-request"), ({"text": "x", "phase": "1"}, 400, "bad-request"),
                                 ({"text": "x", "phase": True}, 400, "bad-request"),
                                 ({"text": "/x private", "phase": 1}, 422, "off-the-record"),
                                 ({"text": "%% journal phase:2 %%\nforged", "phase": 1}, 422, "reserved-line")):
            with self.subTest(body=body):
                got = self.refused("POST", f"/api/v1/entries/{self.stem}/blocks", body, want=want)
                self.assertEqual(got["error"], slug)
        self.assertEqual((checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes()), before)
        self.assertEqual(self.spawned, [])
        # a phase named on an entry that is not a program's
        self.refused("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "x", "phase": 1}, want=400)

    def test_a_run_on_a_break_or_ended_takes_no_block(self):
        programs.pause(self.cfg, self.rid, NOW, self.log)
        got = self.refused("POST", f"/api/v1/entries/{self.stem}/blocks", {"text": "x", "phase": 1}, want=409)
        self.assertEqual((got["error"], got["run_status"]), ("phase-not-open", "paused"))
        programs.resume(self.cfg, self.rid, NOW, self.log)
        self.block({"text": "Back from the break.", "phase": 1})
        programs.abandon(self.cfg, self.rid, NOW, self.log)
        self.refused("POST", f"/api/v1/entries/{self.stem}/blocks", {"text": "x", "phase": 1}, want=409)
        self.assertNotIn("\nx\n", self.entry(self.stem))

    def test_a_retry_with_the_same_client_id_is_answered_even_after_the_run_moved_on(self):
        first = self.block({"text": "Once.", "phase": 1, "client_id": "c-1"})
        self.assertTrue(programs.settle(self.cfg, self.rid, NOW + timedelta(minutes=3), self.log))
        again = self.block({"text": "Once.", "phase": 1, "client_id": "c-1"}, want=200)
        self.assertEqual(again["key"], first["key"])
        self.assertNotIn("advance", again)
        self.assertEqual(self.entry(self.stem).count("Once."), 1)
        self.assertEqual(self.spawned, [["program", "settle", self.rid]])

    def test_his_own_file_at_the_session_s_name_is_never_written_to(self):
        (self.cfg.root / f"{self.stem}.md").write_text("# mine\n", encoding="utf-8")
        got = self.block({"text": "Mine, not the page's.", "phase": 1})
        self.assertNotEqual(got["stem"], self.stem)
        self.assertEqual((self.cfg.root / f"{self.stem}.md").read_text(encoding="utf-8"), "# mine\n")
        self.assertIn("Mine, not the page's.", self.entry(got["stem"]))


    def test_a_phase_block_is_stamped_with_the_server_clock_whatever_at_says(self):
        """``at`` backdates a note, but a phase's answer cannot predate the phase: the capture takes the server's
        clock (a day-old ``at`` would otherwise date it before the prompt was sent)."""
        self.block({"text": "From yesterday.", "phase": 1, "at": (NOW - timedelta(days=1)).isoformat()})
        p = self.prompt(f"{self.rid}-s01-p1")
        self.assertEqual(p["captures"][0]["at"], NOW.isoformat())
        self.assertNotEqual(p["captures"][0]["at"], (NOW - timedelta(days=1)).isoformat())
        self.refused("POST", f"/api/v1/entries/{self.stem}/blocks", {"text": "x", "phase": 1, "at": "yesterday"}, want=400)

    def test_a_backdated_block_after_a_back_still_lets_the_run_settle(self):
        self.block({"text": "First.", "phase": 1})
        self.assertTrue(programs.settle(self.cfg, self.rid, NOW + timedelta(minutes=3), self.log))
        programs.back(self.cfg, self.rid, NOW + timedelta(minutes=4), self.log)   # phase 1 again, reopened
        self.app.clock = lambda: NOW + timedelta(minutes=5)
        self.block({"text": "Second visit.", "phase": 1, "at": (NOW + timedelta(minutes=2)).isoformat()})
        self.assertTrue(programs.settle(self.cfg, self.rid, NOW + timedelta(minutes=10), self.log))
        self.assertEqual(self.run_file()["current"], {"session": 1, "phase": 2})

    def test_a_retry_after_the_answer_was_filed_away_from_his_file_is_answered(self):
        planned = self.stem
        (self.cfg.root / f"{planned}.md").write_text("# mine\n", encoding="utf-8")
        first = self.block({"text": "Mine, not the page's.", "phase": 1, "client_id": "c-9"})
        self.assertNotEqual(first["stem"], planned)
        again = self.block({"text": "Mine, not the page's.", "phase": 1, "client_id": "c-9"}, want=200)
        self.assertEqual((again["key"], again["stem"]), (first["key"], first["stem"]))
        self.assertEqual(self.entry(first["stem"]).count("Mine, not the page's."), 1)
        self.assertEqual((self.cfg.root / f"{planned}.md").read_text(encoding="utf-8"), "# mine\n")
        self.assertEqual(self.spawned, [["program", "settle", self.rid]])
        # a retry at the filed name is the same answer
        again = self.ok("POST", f"/api/v1/entries/{first['stem']}/blocks",
                        {"text": "Mine, not the page's.", "phase": 1, "client_id": "c-9"}, want=200)
        self.assertEqual(again["stem"], first["stem"])

    def test_a_duplicate_reply_names_the_stem_for_a_program_entry(self):
        first = self.block({"text": "Once.", "phase": 1, "client_id": "c-2"})
        again = self.block({"text": "Once.", "phase": 1, "client_id": "c-2"}, want=200)
        self.assertEqual(again["stem"], first["stem"])


class ChangedDefinitionTest(ProgramCase):
    def shrink(self, sessions=2):
        """His own copy of fix-life (same id, so it replaces the built-in) with fewer sessions."""
        src = library.BUILTIN_PROGRAMS / "fix-life"
        dst = Path(self.cfg.practices_dir) / "fix-life"
        shutil.copytree(src, dst)
        pm = dst / "program.md"
        names = sorted(p.stem for p in (dst / "sessions").glob("*.md"))[:sessions]
        pm.write_text(re.sub(r"^sessions: .*$", "sessions: " + ", ".join(names), pm.read_text(encoding="utf-8"),
                             flags=re.M), encoding="utf-8")
        for f in sorted((dst / "sessions").glob("*.md"))[sessions:]:
            f.unlink()
        self.assertEqual(len(library.load(self.cfg).programs["fix-life"].sessions), sessions)

    def test_a_run_on_a_session_the_edited_definition_no_longer_has_still_lists(self):
        programs.start(self.cfg, "fix-life", NOW, self.log, session=5)
        self.shrink()
        rows = self.get_json("/api/v1/runs")
        self.assertEqual([r["id"] for r in rows], [self.rid])
        self.assertIn("definition", rows[0]["text"])
        one = self.get_json(f"/api/v1/runs/{self.rid}")
        self.assertEqual(one["current"]["session"], 5)
        self.assertIn("definition", one["text"])
        programs.abandon(self.cfg, self.rid, NOW, self.log)    # an ended run stays in the list for good
        self.assertEqual(len(self.get_json("/api/v1/runs")), 1)

    def test_the_chat_status_of_such_a_run_does_not_raise_either(self):
        programs.start(self.cfg, "fix-life", NOW, self.log, session=5)
        self.shrink()
        self.assertIn("definition", programs.status_text(self.cfg, NOW))


class SessionNameTest(ProgramCase):
    def test_an_abandoned_run_does_not_shadow_the_new_run_that_has_the_same_day_and_program(self):
        old_stem = self.stem
        programs.abandon(self.cfg, self.rid, NOW, self.log)
        new = programs.start(self.cfg, "fix-life", NOW, self.log)
        self.assertNotEqual(new["id"], self.rid)
        new_stem = programs.find_run(self.cfg, new["id"])["sessions"]["1"]["entry"]
        self.assertNotEqual(new_stem, old_stem)     # the planned name is never shared by two runs
        got = self.ok("POST", f"/api/v1/entries/{new_stem}/blocks", {"text": "The new run.", "phase": 1}, want=201)
        self.assertEqual(got["stem"], new_stem)


class OrphanedRunTest(ProgramCase):
    def orphan(self):
        run = self.run_file()
        programs.run_path(self.cfg, run["program"], run["id"]).unlink()
        run["program"] = "gone-program"      # its program file was deleted by hand, or no longer parses
        programs._save(self.cfg, run)

    def test_only_abandon_is_offered_and_taken_for_a_run_whose_definition_is_gone(self):
        self.orphan()
        one = self.get_json(f"/api/v1/runs/{self.rid}")
        self.assertEqual(one["keyboard"], ["abandon"])
        for action in ("next", "skip", "pause", "insights"):
            with self.subTest(action):
                got = self.refused("POST", f"/api/v1/runs/{self.rid}/{action}", None, want=409)
                self.assertEqual(got["error"], "wrong-state")
                self.assertIn("definition", got["detail"])
        self.assertEqual(self.spawned, [])
        self.ok("POST", f"/api/v1/runs/{self.rid}/abandon", want=202)
        self.assertEqual(self.spawned, [["program", "abandon", self.rid]])


    def test_a_phase_block_is_refused_for_a_run_whose_definition_is_gone(self):
        self.orphan()
        before = (checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes())
        got = self.refused("POST", f"/api/v1/entries/{self.stem}/blocks", {"text": "x", "phase": 1}, want=409)
        self.assertEqual(got["error"], "phase-not-open")
        self.assertIn("definition", got["detail"])
        self.assertEqual(((checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes()), self.spawned),
                         (before, []))


class RunStartTest(ProgramCase):
    def test_start_is_a_spawn_and_names_a_run_in_progress(self):
        got = self.refused("POST", "/api/v1/runs", {"program": "fix-life"}, want=409)
        self.assertEqual((got["error"], got["run"]), ("active-run", self.rid))
        programs.abandon(self.cfg, self.rid, NOW, self.log)
        got = self.ok("POST", "/api/v1/runs", {"program": "fix-life"}, want=202)
        self.assertTrue(got["spawned"])
        self.assertEqual(self.spawned, [["program", "start", "fix-life", "--new"]])
        self.ok("POST", "/api/v1/runs", {"program": "fix-life", "session": 4}, want=202)
        self.assertEqual(self.spawned[-1], ["program", "start", "fix-life", "--new", "--session", "4"])

    def test_bad_requests(self):
        for body, want in (({}, 400), ({"program": 3}, 400), ({"program": "no-such"}, 404),
                           ({"program": "fix-life", "session": 11}, 400), ({"program": "fix-life", "session": 0}, 400),
                           ({"program": "fix-life", "session": "2"}, 400), ({"program": "fix-life", "session": True}, 400)):
            with self.subTest(body=body):
                self.refused("POST", "/api/v1/runs", body, want=want)
        self.assertEqual(self.spawned, [])


class RunActionTest(ProgramCase):
    def post(self, action, body=None, want=202, rid=None):
        return self.ok("POST", f"/api/v1/runs/{rid or self.rid}/{action}", body, want=want) if want < 400 else \
            self.refused("POST", f"/api/v1/runs/{rid or self.rid}/{action}", body, want=want)

    def test_each_action_spawns_the_program_command_for_this_run(self):
        for action in ("next", "skip", "pause", "abandon", "insights"):
            self.post(action)
        self.assertEqual(self.spawned, [["program", a, self.rid] for a in ("next", "skip", "pause", "abandon", "insights")])

    def test_resume_only_for_a_run_on_a_break(self):
        self.assertEqual(self.post("resume", want=409)["error"], "wrong-state")
        programs.pause(self.cfg, self.rid, NOW, self.log)
        self.post("resume")
        self.assertEqual(self.spawned, [["program", "resume", self.rid, "--paused"]])
        for action in ("next", "skip", "back", "pause"):
            self.assertEqual(self.post(action, want=409)["error"], "wrong-state")

    def test_back_needs_a_phase_before_it(self):
        self.assertIn("first phase", self.post("back", want=409)["detail"])
        self.assertTrue(programs.settle(self.cfg, self.rid, NOW + timedelta(minutes=3), self.log) is False)  # nothing answered
        self.block({"text": "x", "phase": 1})
        programs.settle(self.cfg, self.rid, NOW + timedelta(minutes=3), self.log)
        self.spawned.clear()
        self.post("back")
        self.assertEqual(self.spawned, [["program", "back", self.rid]])

    def test_expect_binds_a_transition_to_the_phase_that_was_read(self):
        self.post("next", {"expect": "1:1:1"})
        self.assertEqual(self.spawned, [["program", "next", self.rid, "--expect", "1:1:1"]])
        got = self.post("skip", {"expect": "1:2:1"}, want=409)
        self.assertEqual((got["error"], got["where"]), ("moved-on", "1:1:1"))
        for bad in ("1:1", "x", 7, "1:1:1:1"):
            self.post("next", {"expect": bad}, want=400)
        self.post("pause", {"expect": "1:1:1"}, want=400)
        self.assertEqual(len(self.spawned), 1)

    def test_a_run_that_is_over_answers_conflict_and_an_unknown_one_404(self):
        programs.abandon(self.cfg, self.rid, NOW, self.log)
        for action in ("next", "skip", "back", "pause", "resume", "abandon"):
            with self.subTest(action):
                self.assertEqual(self.post(action, want=409)["error"], "wrong-state")
        self.post("insights")    # compiling what was written is fine for a run that ended
        self.post("next", want=404, rid="2026-10-15-ffff")
        self.post("explode", want=404)
        self.assertEqual(len(self.spawned), 1)

    def test_a_spawn_that_cannot_start_is_503_and_nothing_else_changes(self):
        def broken(cfg, args):
            raise OSError("no fork")
        self.app.spawn = broken
        self.assertEqual(self.post("next", want=503)["error"], "engine-unavailable")
        self.assertEqual(self.run_file()["status"], "active")


class ReadOnlyProgramTest(ProgramCase):
    serve = {"allow_write": False}

    def test_no_program_write_without_allow_write(self):
        before = (checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes())
        for path, body in ((f"/api/v1/entries/{self.stem}/blocks", {"text": "x", "phase": 1}),
                           ("/api/v1/runs", {"program": "fix-life"}), (f"/api/v1/runs/{self.rid}/next", None)):
            self.refused("POST", path, body, want=405)
        self.assertEqual((checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes()), before)
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.request("GET", "/api/v1/runs")[0], 200)


class NoChatTest(ProgramCase):
    stub_privacy = False   # so a read of the privacy pass by the request would reach the mock below

    def test_the_run_routes_never_open_the_chat_transcript_or_the_privacy_pass(self):
        """A program's routes read run files and the ledger; the settle that follows a block runs in the spawned
        process, which is where the chat is consulted (``programs.settle``), never in a request."""
        reached = []

        def refuse(*a, **k):    # recorded, not raised: privacy.view fails closed and would swallow an exception
            reached.append(a)
            raise sqlite3.OperationalError("journal serve reached for the chat transcript")
        with mock.patch.object(sqlite3, "connect", refuse), mock.patch.object(privacy, "view", refuse):
            self.get_json("/api/v1/runs")
            self.get_json(f"/api/v1/runs/{self.rid}")
            self.block({"text": "no chat read", "phase": 1})
            self.ok("POST", f"/api/v1/runs/{self.rid}/pause", want=202)
            self.ok("POST", f"/api/v1/runs/{self.rid}/next", want=202)
        self.assertEqual(reached, [])


class ProgramIdIsNotAPracticeTest(ProgramCase):
    """One id space: a practice saved under a program's id (or alias) would drop the program from the library and
    strand its run (review of WP10). The gate refuses it, in the dry run too, and writes nothing."""

    def raw(self, pid):
        return f"---\nkind: practice\nid: {pid}\ntitle: Fix it\nschedule.time: 07:30\n---\n## Body\n- One thing?\n"

    def test_put_with_a_program_id_is_refused_and_the_run_keeps_its_controls(self):
        before = sorted(p.name for p in self.cfg.practices_dir.iterdir())
        keyboard = self.get_json(f"/api/v1/runs/{self.rid}")["keyboard"]
        for pid in ("fix-life", "conversation-prep", "post-session-debrief", "prep", "debrief"):
            with self.subTest(pid):
                is_id = pid in ("fix-life", "conversation-prep", "post-session-debrief")
                got = self.refused("PUT", f"/api/v1/library/{pid}", {"raw": self.raw(pid)}, want=409 if is_id else 422)
                self.assertEqual(got["error"], "is-program" if is_id else "invalid")
                dry = self.ok("PUT", f"/api/v1/library/{pid}?validate=1", {"raw": self.raw(pid)})
                self.assertFalse(dry["valid"])
                self.assertTrue(any("program" in p for p in dry["problems"]), dry["problems"])
        self.assertEqual(sorted(p.name for p in self.cfg.practices_dir.iterdir()), before)
        self.assertIn("fix-life", library.load(self.cfg).programs)
        self.assertEqual(self.get_json(f"/api/v1/runs/{self.rid}")["keyboard"], keyboard)

    def test_a_user_program_or_situational_file_holds_its_id_too(self):
        src = Path(library.BUILTIN_SITUATIONAL, "conversation-prep.md").read_text(encoding="utf-8")
        (self.cfg.practices_dir / "my-situation.md").write_text(
            src.replace("id: conversation-prep", "id: my-situation").replace("aliases: prep", "aliases: mine"),
            encoding="utf-8")
        self.assertIn("my-situation", library.load(self.cfg).programs)
        self.refused("PUT", "/api/v1/library/my-situation", {"raw": self.raw("my-situation")}, want=409)
        self.refused("PUT", "/api/v1/library/mine", {"raw": self.raw("mine")}, want=422)

    def test_a_definition_of_his_that_does_not_parse_still_holds_its_id(self):
        """A program with a session file missing for a moment is not in the library, but its id is not free: a
        practice saved under it would hide the program for good once it is mended, and strand its runs."""
        d = self.cfg.practices_dir / "my-program"
        d.mkdir()
        (d / "program.md").write_text("---\nkind: program\nid: my-program\ntitle: Mine\naliases: mp\n"
                                      "sessions: 01-missing\n---\nBody.\n", encoding="utf-8")
        self.assertNotIn("my-program", library.load(self.cfg).programs)
        self.refused("PUT", "/api/v1/library/my-program", {"raw": self.raw("my-program")}, want=409)
        self.refused("PUT", "/api/v1/library/mp", {"raw": self.raw("mp")}, want=422)
        (self.cfg.practices_dir / "my-note.md").write_text(    # a situational file with a typo in it
            "---\nkind: situational\nid: my-note\ntitle: ???\nbroken: [\n---\n", encoding="utf-8")
        self.refused("PUT", "/api/v1/library/my-note", {"raw": self.raw("my-note")}, want=409)
        (self.cfg.practices_dir / "other-name.md").write_text(    # kind misspelled, file name unlike its id
            "---\nkind: situtational\nid: my-third\ntitle: T\naliases: third\n---\n## Phase 1\n**1.1** Q?\n", encoding="utf-8")
        self.refused("PUT", "/api/v1/library/my-third", {"raw": self.raw("my-third")}, want=409)
        self.refused("PUT", "/api/v1/library/third", {"raw": self.raw("third")}, want=422)
        (self.cfg.practices_dir / "no-id.md").write_text(      # a situational definition whose id is being repaired
            "---\nkind: situational\ntitle: T\naliases: fourth\n---\n## Phase 1\n**1.1** Q?\n", encoding="utf-8")
        self.refused("PUT", "/api/v1/library/fourth", {"raw": self.raw("fourth")}, want=422)
        self.ok("PUT", "/api/v1/library/stretching", {"raw": self.raw("stretching")})   # an ordinary id is still free

    def test_a_practice_may_still_be_saved_and_edited_under_its_own_id(self):
        self.ok("PUT", "/api/v1/library/stretching", {"raw": self.raw("stretching")})
        got = self.ok("GET", "/api/v1/library/stretching")
        self.ok("PUT", "/api/v1/library/stretching", {"raw": got["raw"], "mtime": got["mtime"]})


class DeleteGuardTest(ProgramCase):
    """DELETE refuses to remove the file of a definition that a run is in the middle of; and a practice that hides
    a program (a hand-made file) is deleted freely, because deleting it is what gives the program back."""

    def situation(self):
        src = Path(library.BUILTIN_SITUATIONAL, "conversation-prep.md").read_text(encoding="utf-8")
        path = self.cfg.practices_dir / "my-situation.md"
        path.write_text(src.replace("id: conversation-prep", "id: my-situation").replace("aliases: prep", ""),
                        encoding="utf-8")
        programs.pause(self.cfg, self.rid, NOW, self.log)  # (one session at a time: ONE-PROGRAM)
        run = programs.start(self.cfg, "my-situation", NOW, self.log)
        return path, run

    def test_a_user_definition_with_an_active_run_is_409_until_the_run_is_over(self):
        path, run = self.situation()
        got = self.refused("DELETE", "/api/v1/library/my-situation", want=409)
        self.assertEqual((got["error"], got["runs"]), ("active-run", [run["id"]]))
        self.assertTrue(path.exists())
        programs.abandon(self.cfg, run["id"], NOW, self.log)
        self.ok("DELETE", "/api/v1/library/my-situation", want=204)
        self.assertFalse(path.exists())

    def test_a_practice_file_hiding_a_program_can_be_deleted_to_get_the_program_back(self):
        (self.cfg.practices_dir / "fix-life.md").write_text(
            "---\nkind: practice\nid: fix-life\ntitle: Fix it\nschedule.time: 07:30\n---\n## Body\n- One thing?\n",
            encoding="utf-8")
        self.assertNotIn("fix-life", library.load(self.cfg).programs)
        self.assertEqual(self.get_json(f"/api/v1/runs/{self.rid}")["keyboard"], ["abandon"])
        self.ok("DELETE", "/api/v1/library/fix-life", want=204)
        self.assertIn("fix-life", library.load(self.cfg).programs)
        self.assertIn("next", self.get_json(f"/api/v1/runs/{self.rid}")["keyboard"])


class BuiltinProgramEditTest(ProgramCase):
    """BUILTIN-EDIT: a built-in program is edited in place, one file at a time (his copy of the whole directory is
    made by the first save), and reset to the default. A run in progress keeps its structure: words may change and
    apply from the next send, a change to its sessions, phases or question numbers waits for the run to end."""

    FILE = "sessions/01-anti-vision.md"

    def session_text(self, view, n=1):
        return next(s for s in view["sessions"] if s["number"] == n)

    def test_a_wording_edit_applies_under_the_run_and_reset_brings_the_default_back(self):
        view = self.get_json("/api/v1/library/fix-life")
        s1 = self.session_text(view)
        self.assertEqual(s1["file"], self.FILE)
        packaged = Path(library.BUILTIN_PROGRAMS, "fix-life", self.FILE)
        shipped = packaged.read_bytes()
        edited = s1["raw"].replace("Current Reality Excavation", "Where You Are")
        dry = self.ok("PUT", "/api/v1/library/fix-life?validate=1", {"file": self.FILE, "raw": edited})
        self.assertEqual((dry["valid"], dry["problems"]), (True, []))
        got = self.ok("PUT", "/api/v1/library/fix-life", {"file": self.FILE, "raw": edited, "mtime": s1["mtime"]})
        self.assertEqual(got["definition"]["source"], "fork")
        self.assertEqual(self.session_text(got)["phases"][0]["title"], "Where You Are")
        self.assertEqual(got["override"]["default_changed"], False)
        mine = self.cfg.practices_dir / "fix-life"
        self.assertEqual(len(list((mine / "sessions").glob("*.md"))), 10)     # the whole program, one id
        self.assertEqual(packaged.read_bytes(), shipped)
        self.assertEqual(programs.find_run(self.cfg, self.rid)["status"], "active")
        self.assertEqual(library.load(self.cfg).program("fix-life").session(1).phase(1).title, "Where You Are")
        diff = self.get_json("/api/v1/library/fix-life/default")["diff"]["yours"]
        self.assertIn("+## Phase 1: Where You Are", diff)
        # the next save of that file is checked against his file's mtime
        again = self.session_text(got)
        self.assertEqual(self.refused("PUT", "/api/v1/library/fix-life", {"file": self.FILE, "raw": edited,
                                                                         "mtime": s1["mtime"]}, want=409)["error"],
                         "mtime")
        self.ok("PUT", "/api/v1/library/fix-life", {"file": self.FILE, "raw": edited, "mtime": again["mtime"]})
        self.ok("DELETE", "/api/v1/library/fix-life", want=204)                # same structure: the run allows it
        self.assertFalse(mine.exists())
        back = self.get_json("/api/v1/library/fix-life")
        self.assertEqual((back["definition"]["source"], back["override"]), ("builtin", None))

    def test_a_structural_edit_waits_for_the_run_to_end(self):
        view = self.get_json("/api/v1/library/fix-life")
        program_md = view["raw"]
        fewer = program_md.replace(", 10-one-day-reset", "")
        dry = self.ok("PUT", "/api/v1/library/fix-life?validate=1", {"file": "program.md", "raw": fewer})
        self.assertFalse(dry["valid"])
        self.assertTrue(any("run in progress" in p for p in dry["problems"]), dry["problems"])
        got = self.refused("PUT", "/api/v1/library/fix-life", {"file": "program.md", "raw": fewer}, want=409)
        self.assertEqual((got["error"], got["runs"]), ("active-run", [self.rid]))
        self.assertFalse((self.cfg.practices_dir / "fix-life").exists())
        s1 = self.session_text(view)
        moved = s1["raw"].replace("## Phase 2: The 5-Year Tuesday\ntype: reflect", "## Phase 2: The 5-Year Tuesday\ntype: ask")
        self.assertNotEqual(moved, s1["raw"])                                     # a phase changes its type
        self.refused("PUT", "/api/v1/library/fix-life", {"file": self.FILE, "raw": moved}, want=409)
        programs.pause(self.cfg, self.rid, NOW, self.log)                         # a break is still in progress
        self.refused("PUT", "/api/v1/library/fix-life", {"file": "program.md", "raw": fewer}, want=409)
        programs.abandon(self.cfg, self.rid, NOW, self.log)
        got = self.ok("PUT", "/api/v1/library/fix-life", {"file": "program.md", "raw": fewer})
        self.assertEqual(got["definition"]["sessions"], 9)

    def test_a_reset_that_would_change_the_structure_waits_for_the_run_too(self):
        programs.abandon(self.cfg, self.rid, NOW, self.log)
        view = self.get_json("/api/v1/library/fix-life")
        fewer = view["raw"].replace(", 10-one-day-reset", "")
        self.ok("PUT", "/api/v1/library/fix-life", {"file": "program.md", "raw": fewer})
        run = programs.start(self.cfg, "fix-life", NOW, self.log)
        got = self.refused("DELETE", "/api/v1/library/fix-life", want=409)
        self.assertEqual((got["error"], got["runs"]), ("active-run", [run["id"]]))
        self.assertTrue((self.cfg.practices_dir / "fix-life" / "program.md").is_file())
        programs.abandon(self.cfg, run["id"], NOW, self.log)
        self.ok("DELETE", "/api/v1/library/fix-life", want=204)

    def test_a_draft_that_does_not_load_or_changes_its_id_writes_nothing(self):
        view = self.get_json("/api/v1/library/fix-life")
        for body in ({"file": "program.md", "raw": "no front matter"},
                     {"file": "program.md", "raw": view["raw"].replace("id: fix-life", "id: fix-it")},
                     {"file": self.FILE, "raw": self.session_text(view)["raw"].replace("kind: session", "kind: nope")},
                     {"file": "program.md", "raw": view["raw"].replace("insights: compile", "insights: compile\nx: 1")}):
            with self.subTest(body["raw"][:30]):
                got = self.refused("PUT", "/api/v1/library/fix-life", body, want=422)
                self.assertTrue(got["problems"])
        for name in ("../program.md", "sessions/../../x.md", "sessions/99-none.md", "notes.txt"):
            with self.subTest(name):
                self.refused("PUT", "/api/v1/library/fix-life", {"file": name, "raw": view["raw"]}, want=404)
        self.refused("PUT", "/api/v1/library/fix-life", {"file": 3, "raw": view["raw"]}, want=400)
        self.assertFalse((self.cfg.practices_dir / "fix-life").exists())
        self.assertEqual([p.name for p in self.cfg.practices_dir.iterdir() if p.name.startswith(".fix-life")], [])

    def test_a_situational_definition_is_one_file(self):
        view = self.get_json("/api/v1/library/conversation-prep")
        edited = view["raw"].replace("Who is it with", "Who's it with")
        got = self.ok("PUT", "/api/v1/library/conversation-prep", {"raw": edited})
        self.assertEqual(got["definition"]["source"], "fork")
        self.assertIn("Who's it with", (self.cfg.practices_dir / "conversation-prep.md").read_text(encoding="utf-8"))
        self.assertEqual(library.load(self.cfg).program("prep").id, "conversation-prep")   # same /start word
        self.refused("PUT", "/api/v1/library/conversation-prep", {"raw": edited, "file": "other.md",
                                                                  "mtime": got["mtime"]}, want=404)
        self.ok("DELETE", "/api/v1/library/conversation-prep", want=204)
        self.assertFalse((self.cfg.practices_dir / "conversation-prep.md").exists())


class SummaryRefreshTest(ProgramCase):
    """An edit of a phase answer after the session completed has the entry's ``## Summary`` written again from his
    words (SUMMARY-REFRESH): spawned like every transition (it takes the tick's lock), run here by hand."""

    def setUp(self):
        super().setUp()
        self.prog = library.load(self.cfg).program("fix-life")

    def complete(self, skip_last=False):
        """Answer every phase of session 1 through the web and move on; the last move completes the session."""
        phases = len(self.prog.session(1).phases)
        for n in range(1, phases + 1):
            self.block({"text": f"Answer {n} as first written.", "phase": n})
            if skip_last and n == phases:
                return
            programs.advance(self.cfg, self.rid, NOW + timedelta(minutes=n), self.log, reason="next")
        self.assertEqual(self.run_file()["sessions"]["1"]["status"], "completed")
        self.spawned.clear()
        self.sent.clear()

    def edit(self, phase, text, want=200):
        doc = self.ok("GET", f"/api/v1/entries/{self.stem}")
        b = next(b for b in doc["blocks"] if b.get("phase") == phase)
        body = {"text": text, "was": b["text"], "at": b["at"]}
        url = f"/api/v1/entries/{self.stem}/blocks/{b['key']}"
        return self.ok("PATCH", url, body, want=want) if want == 200 else self.refused("PATCH", url, body, want=want)

    def refresh(self):
        """What the spawned command does: ``journal program refresh-summary`` under the tick's lock."""
        argv = self.spawned[-1]
        self.assertEqual(argv[:2], ["program", "refresh-summary"])
        out = []
        with programs.locked(self.cfg):
            ran = programs.refresh_summary(self.cfg, argv[2], int(argv[4]), out)
        return ran, out

    def test_the_summary_is_written_again_from_the_edited_words_and_nothing_else_moves(self):
        self.complete()
        before = self.entry(self.stem)
        old_summary = entries.summary_text(before)
        self.assertIn("Answer 1 as first written.", old_summary)
        got = self.edit(1, "Answer 1 as I mean it.")
        self.assertEqual(got["summary"], "refreshing")
        self.assertEqual(self.spawned, [["program", "refresh-summary", self.rid, "--session", "1"]])
        self.assertEqual(entries.summary_text(self.entry(self.stem)), old_summary)   # the request itself leaves it
        run_before, ledger_before = self.run_file(), (self.cfg.state_dir / "ledger.json").read_bytes()
        ran, _ = self.refresh()
        self.assertTrue(ran)
        after = self.entry(self.stem)
        new_summary = entries.summary_text(after)
        self.assertIn("Answer 1 as I mean it.", new_summary)
        self.assertNotIn("Answer 1 as first written", new_summary)
        self.assertEqual(new_summary, old_summary.replace("Answer 1 as first written.", "Answer 1 as I mean it."))
        # every byte outside the summary is as the edit left it: his blocks, annotations, front matter
        a, b = after.index("## Summary"), before.index("## Summary")
        self.assertEqual(after[:a], before[:b].replace("Answer 1 as first written.", "Answer 1 as I mean it."))
        end = lambda s, i: i + (s[i + 1:].find("\n## ") + 1 if "\n## " in s[i + 1:] else len(s))   # noqa: E731
        self.assertEqual(after[end(after, a):], before[end(before, b):])
        self.assertEqual(entries.phase_answers(after)[2], "Answer 2 as first written.")
        # the engine's own state: the run, the ledger and the message already sent
        run_after = self.run_file()
        self.assertEqual(run_after["sessions"]["1"]["summary_sha"], entries.summary_digest(after))   # what it wrote
        self.assertNotEqual(run_after["sessions"]["1"]["summary_sha"], run_before["sessions"]["1"]["summary_sha"])
        run_after["sessions"]["1"]["summary_sha"] = run_before["sessions"]["1"]["summary_sha"]
        self.assertEqual(run_after, run_before)      # the run file gains the digest and nothing else
        self.assertEqual((self.cfg.state_dir / "ledger.json").read_bytes(), ledger_before)
        self.assertEqual(self.sent, [])

    def test_the_summary_stays_the_engines_marked_section_and_a_second_run_changes_nothing(self):
        self.complete()
        self.edit(1, "Mine, again.")
        self.refresh()
        once = self.entry(self.stem)
        self.assertIn("## Summary\n\n%% journal:summary %%\n", once)
        self.assertEqual(entries.words_span(once)[1] <= once.index("## Summary"), True)   # his words end before it
        self.refresh()
        self.assertEqual(self.entry(self.stem), once)

    def test_an_insights_document_compiled_again_reads_the_new_words(self):
        self.complete()
        self.edit(1, "Rewritten answer.")
        self.refresh()
        path = programs.insights(self.cfg, self.rid, NOW + timedelta(days=1), self.log)
        text = path.read_text(encoding="utf-8")
        self.assertIn("Rewritten answer.", text)
        self.assertNotIn("Answer 1 as first written", text)

    def test_a_session_still_open_is_not_refreshed(self):
        self.block({"text": "Only the first.", "phase": 1})
        self.spawned.clear()
        got = self.edit(1, "Changed my mind.")
        self.assertNotIn("summary", got)     # nothing is promised: there is no summary yet
        ran = programs.refresh_summary(self.cfg, self.rid, 1, [])
        self.assertFalse(ran)
        self.assertNotIn("## Summary", self.entry(self.stem))

    def test_an_edit_that_lands_while_the_session_is_completing_is_not_lost(self):
        """Review (CodeRabbit): ``complete_session`` reads the answers, then marks the session completed. An edit saved
        between the two sees an open session; the command is spawned for it anyway and, run after the completion
        (it waits for the tick's lock), writes the summary from the edited words."""
        phases = len(self.prog.session(1).phases)
        for n in range(1, phases):
            self.block({"text": f"Answer {n} as first written.", "phase": n})
            programs.advance(self.cfg, self.rid, NOW + timedelta(minutes=n), self.log, reason="next")
        self.block({"text": f"Answer {phases} as first written.", "phase": phases})
        real, got = programs._write_summary, {}

        def write_then_edit(cfg, prog, run, s):
            out = real(cfg, prog, run, s)      # the completion has written its summary from the words as they were...
            self.spawned.clear()
            got.update(self.edit(1, "Edited during the completion."))   # ...and the edit lands before it marks the session
            return out
        with mock.patch.object(programs, "_write_summary", write_then_edit):
            programs.advance(self.cfg, self.rid, NOW + timedelta(minutes=phases), self.log, reason="next")
        self.assertEqual(self.run_file()["sessions"]["1"]["status"], "completed")
        self.assertNotIn("Edited during the completion.", entries.summary_text(self.entry(self.stem)))   # stale, so far
        self.assertEqual(self.spawned, [["program", "refresh-summary", self.rid, "--session", "1"]])
        self.assertNotIn("summary", got)
        self.assertTrue(self.refresh()[0])
        self.assertIn("Edited during the completion.", entries.summary_text(self.entry(self.stem)))

    def test_a_session_whose_summary_waits_for_a_transcript_is_left_to_the_tick(self):
        self.complete()
        run = self.run_file()
        run["sessions"]["1"]["summary_audio_pending"] = True
        programs._save(self.cfg, run)
        before = self.entry(self.stem)
        got = self.edit(1, "Edited while a transcript is pending.")
        self.assertNotIn("summary", got)      # nothing is promised...
        self.assertEqual(self.spawned, [["program", "refresh-summary", self.rid, "--session", "1"]])   # ...but it is sent for
        self.assertEqual(entries.summary_text(self.entry(self.stem)), entries.summary_text(before))
        out = []
        self.assertFalse(programs.refresh_summary(self.cfg, self.rid, 1, out))   # the command, under the lock, agrees
        self.assertEqual(out, [])
        # the tick's refill, once the transcript is in, writes it from the words as they are then
        run = self.run_file()
        programs._refill_summary(self.cfg, self.prog, run, "1", run["sessions"]["1"], self.log)
        self.assertIn("Edited while a transcript is pending.", entries.summary_text(self.entry(self.stem)))
        self.assertEqual(self.run_file()["sessions"]["1"]["summary_sha"], entries.summary_digest(self.entry(self.stem)))

    def test_an_edit_that_lands_while_the_refill_writes_is_not_lost(self):
        """Review probe P5: ``_refill_summary`` renders the summary from the words, writes it and clears its flag. An
        edit saved while it renders saw the flag and spawned nothing; the command is now spawned for any completed
        session and, run after the refill (it waits for the tick's lock), writes the edited words."""
        self.complete()
        run = self.run_file()
        run["sessions"]["1"]["summary_audio_pending"] = True
        programs._save(self.cfg, run)
        real, fired, got = programs.fill_template, [], {}

        def fill_then_edit(cfg, prog, run, s, template):
            out = real(cfg, prog, run, s, template)
            if not fired:
                fired.append(1)
                self.spawned.clear()
                got.update(self.edit(1, "EDIT DURING REFILL."))
            return out
        with mock.patch.object(programs, "fill_template", fill_then_edit):
            run = self.run_file()
            programs._refill_summary(self.cfg, self.prog, run, "1", run["sessions"]["1"], self.log)
        self.assertNotIn("summary_audio_pending", self.run_file()["sessions"]["1"])
        self.assertIn("EDIT DURING REFILL.", entries.phase_answers(self.entry(self.stem))[1])
        self.assertNotIn("EDIT DURING REFILL.", entries.summary_text(self.entry(self.stem)))    # stale, so far
        self.assertEqual(self.spawned, [["program", "refresh-summary", self.rid, "--session", "1"]])
        self.assertTrue(self.refresh()[0])
        self.assertIn("EDIT DURING REFILL.", entries.summary_text(self.entry(self.stem)))

    def test_a_block_that_is_not_a_phase_answer_does_not_refresh(self):
        self.complete()
        entries.append_conversation(self.cfg.root / f"{self.stem}.md",
                                    "%% journal his:900 %%\n**10:05** — A later thought.", self.cfg.names)
        self.spawned.clear()
        doc = self.ok("GET", f"/api/v1/entries/{self.stem}")
        b = next(b for b in doc["blocks"] if b["key"] == "his:900")
        url = f"/api/v1/entries/{self.stem}/blocks/{b['key']}"
        self.assertIsNone(b.get("phase"))
        before = entries.summary_text(self.entry(self.stem))
        got = self.ok("PATCH", url, {"text": "A later thought, reworded.", "was": b["text"], "at": b["at"]})
        self.assertNotIn("summary", got)
        self.assertEqual(self.spawned, [])
        self.assertEqual(entries.summary_text(self.entry(self.stem)), before)

    def test_deleting_an_answer_is_still_409_and_spawns_nothing(self):
        self.complete()
        doc = self.ok("GET", f"/api/v1/entries/{self.stem}")
        b = next(b for b in doc["blocks"] if b.get("phase") == 1)
        before = checksum(self.cfg.root)
        got = self.refused("DELETE", f"/api/v1/entries/{self.stem}/blocks/{b['key']}",
                           {"was": b["text"], "at": b["at"]}, want=409)
        self.assertEqual(got["error"], "program-answer")
        self.assertEqual((self.spawned, checksum(self.cfg.root)), ([], before))

    def test_a_spawn_that_cannot_start_leaves_the_edit_saved_and_says_so(self):
        self.complete()

        def broken(cfg, args):
            raise OSError("no fork")
        self.app.spawn = broken
        got = self.edit(1, "Saved anyway.")
        self.assertEqual(got["summary"], "not-started")
        self.assertIn("Saved anyway.", self.entry(self.stem))
        self.assertIn("as first written", entries.summary_text(self.entry(self.stem)))   # the old words, until the next write

    def test_the_command_does_nothing_for_a_session_that_was_reopened_or_a_run_that_is_gone(self):
        self.complete()
        self.edit(1, "Edited.")
        run = self.run_file()
        run["sessions"]["1"]["status"] = "in_progress"      # (a re-run of the session began before the command got the lock)
        programs._save(self.cfg, run)
        before = self.entry(self.stem)
        ran, _ = self.refresh()
        self.assertFalse(ran)
        self.assertEqual(self.entry(self.stem), before)
        self.assertFalse(programs.refresh_summary(self.cfg, "no-such-run", 1, []))

    def test_the_spawned_argv_parses_and_the_command_waits_for_the_tick_lock(self):
        """Review probe P7: the argv the web app spawns is parsed by the real parser, and the command does nothing
        until the tick's lock is free."""
        import fcntl
        import threading

        from journal import cli
        self.complete()
        self.edit(1, "Through the command.")
        args = cli.build_parser().parse_args(["--config", "/nonexistent.json", "--hermes-home", "/tmp/hh",
                                              *self.spawned[-1]])
        self.assertEqual((args.command, args.program_command, args.target, args.session),
                         ("program", "refresh-summary", self.rid, 1))
        holder = open(self.cfg.state_dir / "tick.lock", "a+")
        fcntl.flock(holder, fcntl.LOCK_EX)
        res = {}
        with mock.patch("builtins.print"):
            worker = threading.Thread(target=lambda: res.setdefault("code", cli.program(args, self.cfg)))
            worker.start()
            worker.join(1.0)
            waiting = worker.is_alive()
            unchanged = "Through the command." not in entries.summary_text(self.entry(self.stem))
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
            worker.join(10)
        self.assertTrue(waiting)
        self.assertTrue(unchanged)
        self.assertEqual(res, {"code": 0})
        self.assertIn("Through the command.", entries.summary_text(self.entry(self.stem)))

    def test_a_second_command_for_the_same_session_exits_while_one_is_waiting(self):
        """Coalescing: a refresh waiting for the tick's lock holds the session's pending lock until it has the tick's
        lock; another for the session exits quietly (the waiting one reads the entry later and sees both edits)."""
        import argparse
        import fcntl
        import threading

        from journal import cli
        self.complete()
        self.edit(1, "First edit.")
        args = argparse.Namespace(program_command="refresh-summary", target=self.rid, session=1, notify=False)
        holder = open(self.cfg.state_dir / "tick.lock", "a+")
        fcntl.flock(holder, fcntl.LOCK_EX)
        out = {}
        with mock.patch("builtins.print") as shown:
            first = threading.Thread(target=lambda: out.setdefault("first", cli.program(args, self.cfg)))
            first.start()
            first.join(1.0)
            self.assertTrue(first.is_alive())
            self.edit(1, "Second edit.")
            self.assertEqual(cli.program(args, self.cfg), 0)       # finds the pending lock held and goes away
            shown.assert_not_called()
            self.assertTrue(first.is_alive())
            fcntl.flock(holder, fcntl.LOCK_UN)
            holder.close()
            first.join(10)
        self.assertEqual(out, {"first": 0})
        self.assertIn("Second edit.", entries.summary_text(self.entry(self.stem)))    # the one that waited saw both
        # and once it has run the pending lock is free again
        again = programs.claim_refresh(self.cfg, self.rid, 1)
        self.assertIsNotNone(again)
        programs.release_refresh(again)

    # -- his words come before a fresher summary (review probes P1, P2, P2b, P3b) --

    def hand_edit(self, fn):
        path = self.cfg.root / f"{self.stem}.md"
        path.write_text(fn(path.read_text(encoding="utf-8")), encoding="utf-8")
        return path.read_text(encoding="utf-8")

    def summary_end(self, body):
        a = body.index("## Summary")
        nxt = body.find("\n## ", a + 1)
        return a, (nxt if nxt != -1 else len(body))

    def left_alone(self, hand, edited, phase=2, words="Answer 2 edited."):
        """The edit is saved, the refresh is refused and the entry is as the edit left it, byte for byte."""
        self.spawned.clear()
        got = self.edit(phase, words)
        self.last_reason = got["summary_reason"]
        self.assertEqual(got["summary"], "kept")        # the reply is honest: the command would refuse, so none is sent
        self.assertTrue(got["summary_reason"])
        self.assertEqual(self.spawned, [])
        after_edit = self.entry(self.stem)
        out = []
        with programs.locked(self.cfg):
            ran = programs.refresh_summary(self.cfg, self.rid, 1, out)
        self.assertFalse(ran)
        self.assertEqual(self.entry(self.stem), after_edit)
        self.assertEqual(len(out), 1)
        self.assertIn("left as it is", out[0])
        return out[0]

    def test_text_he_typed_under_the_summary_is_never_replaced(self):
        self.complete()
        hand = self.hand_edit(lambda b: b[:self.summary_end(b)[1]].rstrip("\n")
                              + "\n\nMY OWN LATER REFLECTION, typed in Obsidian.\n\n### My notes\n\nMORE OF MINE.\n"
                              + b[self.summary_end(b)[1]:])
        why = self.left_alone(hand, "Answer 2 edited.")
        self.assertIn("not what the engine wrote", why)
        self.assertIn("not what the engine wrote", self.last_reason)
        after = self.entry(self.stem)
        self.assertIn("MY OWN LATER REFLECTION", after)
        self.assertIn("MORE OF MINE.", after)

    def test_a_word_he_changed_inside_the_summary_is_left_as_he_wrote_it(self):
        self.complete()
        self.hand_edit(lambda b: b.replace("Repeated complaints:", "Things I keep saying:"))
        self.left_alone(None, None)
        self.assertIn("Things I keep saying:", self.entry(self.stem))

    def test_a_summary_he_moved_between_his_answers_takes_nothing_with_it(self):
        self.complete()

        def move(b):
            a, e = self.summary_end(b)
            sec, rest = b[a:e], b[:a] + b[e + 1:]
            k = rest.index("%% journal phase:2 %%")
            return rest[:k] + sec.rstrip("\n") + "\n\nMY NOTE between.\n\n" + rest[k:]
        before = self.hand_edit(move)
        blocks_before = entries.phase_answers(before)
        self.left_alone(before, None, phase=1, words="Answer 1 edited.")
        after = self.entry(self.stem)
        self.assertIn("MY NOTE between.", after)
        self.assertEqual({n: v for n, v in entries.phase_answers(after).items() if n != 1},
                         {n: v for n, v in blocks_before.items() if n != 1})
        self.assertNotIn("_(skipped)_", entries.summary_text(after).split("Key findings")[0])

    def test_a_summary_whose_mark_he_deleted_is_not_written_again(self):
        self.complete()
        hand = self.hand_edit(lambda b: b.replace("%% journal:summary %%\n", ""))
        self.assertEqual(hand.count("## Summary"), 1)
        why = self.left_alone(hand, None)
        self.assertIn("no mark", why)
        self.assertEqual(self.entry(self.stem).count("## Summary"), 1)    # none was added beside it

    def test_a_summary_he_deleted_is_not_made_again(self):
        self.complete()
        self.hand_edit(lambda b: b[:self.summary_end(b)[0]] + b[self.summary_end(b)[1] + 1:])
        self.left_alone(None, None)
        self.assertNotIn("## Summary", self.entry(self.stem))

    def test_a_block_of_his_found_under_the_summary_is_not_moved(self):
        self.complete()

        def stray(b):
            a, e = self.summary_end(b)
            return b[:e].rstrip("\n") + "\n\n%% journal phase:2 %%\n### 10:15 IST · text\n\nA late answer.\n" + b[e:]
        self.hand_edit(stray)
        self.left_alone(None, None)
        self.assertEqual(self.entry(self.stem).count("A late answer."), 1)
        self.assertLess(self.entry(self.stem).index("## Summary"), self.entry(self.stem).index("A late answer."))

    def test_a_session_completed_before_the_digest_was_kept_is_left_alone(self):
        self.complete()
        run = self.run_file()
        del run["sessions"]["1"]["summary_sha"]      # (what a run completed by the earlier code looks like)
        programs._save(self.cfg, run)
        why = self.left_alone(None, None, phase=1, words="Answer 1 edited.")
        self.assertIn("no record", why)

    def test_the_digest_is_kept_by_the_completion_the_refill_and_the_refresh(self):
        self.complete()
        digest = lambda: entries.summary_digest(self.entry(self.stem))   # noqa: E731
        self.assertEqual(self.run_file()["sessions"]["1"]["summary_sha"], digest())
        run = self.run_file()
        run["sessions"]["1"]["summary_audio_pending"] = True
        programs._save(self.cfg, run)
        self.edit(1, "Answer 1, for the refill.")
        run = self.run_file()
        programs._refill_summary(self.cfg, self.prog, run, "1", run["sessions"]["1"], self.log)
        self.assertEqual(self.run_file()["sessions"]["1"]["summary_sha"], digest())
        self.assertIn("for the refill", entries.summary_text(self.entry(self.stem)))
        self.edit(1, "Answer 1, for the refresh.")
        self.assertTrue(self.refresh()[0])
        self.assertEqual(self.run_file()["sessions"]["1"]["summary_sha"], digest())

    def test_the_notes_the_tick_adds_after_the_summary_do_not_count_as_a_change(self):
        self.complete()
        path = self.cfg.root / f"{self.stem}.md"
        entries.append_conversation(path, "%% journal his:900 %%\n**10:05** — A later thought.", self.cfg.names)
        path.write_text(path.read_text(encoding="utf-8").rstrip("\n") + "\n\n## Sam's notes\n\nA note.\n",
                        encoding="utf-8")
        self.edit(1, "Answer 1, after a note.")
        self.assertTrue(self.refresh()[0])
        self.assertIn("after a note", entries.summary_text(self.entry(self.stem)))

    def test_the_reply_names_a_session_with_no_digest_as_kept(self):
        self.complete()
        run = self.run_file()
        del run["sessions"]["1"]["summary_sha"]
        programs._save(self.cfg, run)
        got = self.edit(1, "Legacy edit.")
        self.assertEqual((got["summary"], self.spawned), ("kept", []))
        self.assertIn("no record", got["summary_reason"])

    def test_an_editor_that_strips_trailing_whitespace_does_not_stop_the_refresh(self):
        """Review probe N3: an answer with a markdown hard break (two trailing spaces) is copied into the summary; an
        editor that strips trailing whitespace on save must not make the section look changed."""
        phases = len(self.prog.session(1).phases)
        for n in range(1, phases + 1):
            self.block({"text": f"Line one of {n}.  \nLine two.", "phase": n})
            programs.advance(self.cfg, self.rid, NOW + timedelta(minutes=n), self.log, reason="next")
        body = self.entry(self.stem)
        self.assertTrue(any(line.endswith(" ") for line in entries.summary_section(body).split("\n")))
        stripped = "\n".join(line.rstrip() for line in body.split("\n"))
        self.assertNotEqual(stripped, body)
        (self.cfg.root / f"{self.stem}.md").write_text(stripped, encoding="utf-8")
        self.spawned.clear()
        got = self.edit(1, "Edited after the strip.")
        self.assertEqual(got["summary"], "refreshing")
        self.assertTrue(self.refresh()[0])
        self.assertIn("Edited after the strip.", entries.summary_text(self.entry(self.stem)))

    def test_the_digest_ignores_trailing_whitespace_and_nothing_else(self):
        self.complete()
        body = self.entry(self.stem)
        digest = entries.summary_digest(body)
        a = body.index("%% journal:summary %%")
        self.assertEqual(entries.summary_digest(body[:a] + "%% journal:summary %%   " + body[a + 21:]), digest)
        self.assertIsNone(entries.summary_digest(body.replace("\n", "\r\n")))   # (not found by its mark: refused)
        self.assertNotEqual(entries.summary_digest(body.replace("Key findings", "Key  findings")), digest)

    def test_an_edit_after_the_first_command_read_the_entry_gets_a_refresh_of_its_own(self):
        """Review probe N6: a command that has the tick's lock has let go of the pending lock, so an edit saved after
        it read the answers spawns a command that is not coalesced away and writes the second edit."""
        import threading

        from journal import cli
        self.complete()
        self.edit(1, "Edit one.")
        args = cli.build_parser().parse_args(["program", "refresh-summary", self.rid, "--session", "1"])
        real, fired, res = programs.fill_template, [], {}

        def fill_then_edit(cfg, prog, run, s, template):
            out = real(cfg, prog, run, s, template)
            if not fired:
                fired.append(1)
                self.edit(1, "Edit two.")        # lands after the first command read the answers
                second = threading.Thread(target=lambda: res.setdefault("B", cli.program(args, self.cfg)))
                second.start()
                second.join(0.5)
                res["second"], res["waited"] = second, second.is_alive()   # it waits for the tick's lock: not coalesced
            return out
        with mock.patch("builtins.print"), mock.patch.object(programs, "fill_template", fill_then_edit):
            res["A"] = cli.program(args, self.cfg)
            res["second"].join(10)
        self.assertTrue(res["waited"])
        self.assertEqual((res["A"], res["B"]), (0, 0))
        self.assertIn("Edit two.", entries.summary_text(self.entry(self.stem)))

    def test_a_run_that_does_not_exist_is_a_message_not_a_traceback_and_names_no_file(self):
        import argparse
        import io
        from contextlib import redirect_stderr

        from journal import cli
        before = sorted(p.name for p in self.cfg.state_dir.iterdir())
        for target in ("no-such-run", "../../etc/x", ""):
            err = io.StringIO()
            args = argparse.Namespace(program_command="refresh-summary", target=target, session=1, notify=False)
            with redirect_stderr(err):
                self.assertEqual(cli.program(args, self.cfg), 2)
            self.assertIn("no run called", err.getvalue())
        self.assertEqual(sorted(p.name for p in self.cfg.state_dir.iterdir()), before)    # no lock file was made
