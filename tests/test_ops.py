"""The operator's notice (``ops.py``): incidents judged from what the engine records, delivered as GitHub issues
through a mocked ``gh`` (never a real one), and nothing personal in any notice."""
import fcntl
import json
import re
import socket
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, add_prompt, make_cfg

from journal import ledger, ops, outage, status, telegram, tick
from journal import config as jconfig

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
REPO = "example-owner/example-ops"
OTHER = "example-owner/elsewhere"
HHMM = re.compile(r"\b\d{1,2}:\d{2}\b")  # a time of day: never in a notice (it would give away his UTC offset)

# Text that must never reach a notice: words of his and hers, a personal practice name, slot ids, a path, a host.
WORDS = "I told Marisol about the grief I carry for my brother"
PRACTICE = "grief-letters-for-marisol"
RAW_MODEL_ERROR = ('exit 1: Error code: 400 - {"type": "invalid_params", "message": "unknown parameter toolsets in '
                   f'request: {WORDS} /home/theo/Notes/Journal/2026-11-17-{PRACTICE}.md"}}')
RAW_SEND_ERROR = f"Bad Request: chat not found while sending '{WORDS}' to {CHAT}"
RAW_FOLD_ERROR = f"Bad Request: message to edit not found ({WORDS})"


def personal_strings(cfg) -> list[str]:
    return [WORDS, "Marisol", "brother", PRACTICE, "grief", "2026-11-17-", "/home/", str(cfg.root), str(cfg.state_dir),
            CHAT, cfg.names.user, cfg.names.companion, socket.gethostname(), "Bad Request", "unknown parameter"]


class Gh:
    """Records every subprocess the ops notice starts; answers like gh would."""

    def __init__(self, fail: str | None = None):
        self.calls: list[tuple[list[str], str]] = []
        self.timeouts: list[float] = []
        self.fail = fail
        self.next_issue = 41
        self.open_issues: list[dict] = []  # what ``gh issue list`` answers
        self.hang_create = False  # the create goes through on GitHub, and gh times out before it says so
        self.during = None  # called inside each call (a test looks at the world while gh runs)

    def __call__(self, argv, input=None, timeout=None, **kw):
        self.calls.append((list(argv), input or ""))
        self.timeouts.append(timeout)
        if self.during:
            self.during(argv)
        if self.fail:
            return subprocess.CompletedProcess(argv, 1, "", self.fail)
        if argv[1:3] == ["issue", "create"]:
            self.next_issue += 1
            self.open_issues.append({"number": self.next_issue, "body": input or ""})
            if self.hang_create:
                raise subprocess.TimeoutExpired(argv, timeout)
            return subprocess.CompletedProcess(argv, 0, f"https://github.com/{REPO}/issues/{self.next_issue}\n", "")
        if argv[1:3] == ["issue", "list"]:
            return subprocess.CompletedProcess(argv, 0, json.dumps(self.open_issues), "")
        return subprocess.CompletedProcess(argv, 0, "", "")

    def verbs(self) -> list[str]:
        return [" ".join(a[1:3]) for a, _ in self.calls]


class OpsBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), ops={"repo": REPO, "gh": "/usr/bin/gh"})
        self.gh = Gh()
        self.patch = mock.patch("journal.ops.subprocess.run", self.gh)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def run_ops(self, at=NOW, **kw) -> list[str]:
        log: list[str] = []
        ops.run(self.cfg, at, log, **kw)
        return log

    def state(self) -> dict:
        return ledger.read_state(self.cfg.state_dir / ops.STATE)

    def incident(self, kind):
        return (self.state().get("incidents") or {}).get(kind)

    def assert_private(self):
        """No call's argv or stdin carries anything personal (the whole point of the fixed templates), nor a time of
        day."""
        self.assertTrue(self.gh.calls)
        for argv, stdin in self.gh.calls:
            text = "\n".join(argv) + "\n" + stdin
            for s in personal_strings(self.cfg):
                self.assertNotIn(s, text, (argv, stdin))
            self.assertIsNone(HHMM.search(text), (argv, stdin))
        for t in self.gh.timeouts:
            self.assertLessEqual(t, ops.CALL_SECONDS)

    def prompt(self, n, *, source="iris", sent=None, **extra):
        sent = sent or NOW - timedelta(hours=10 - n)
        p = {"id": f"2026-11-17-{PRACTICE}-{n}", "kind": PRACTICE, "local_date": "2026-11-17", "chat_id": CHAT,
             "message_id": str(500 + n), "sent_at": sent.isoformat(), "scheduled": sent.isoformat(),
             "opener": WORDS, "opener_source": source, "plain_text": WORDS, **extra}
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append(p)
        return p


class ErrorClassTest(unittest.TestCase):
    def test_a_raw_error_becomes_a_class_from_a_fixed_vocabulary(self):
        self.assertEqual(ops.error_class(RAW_MODEL_ERROR), "exit 1 http 400 invalid_params (toolsets)")
        self.assertEqual(ops.error_class("timed out after 240 s"), "timeout")
        self.assertEqual(ops.error_class(RAW_SEND_ERROR), "chat not found")
        self.assertEqual(ops.error_class(RAW_FOLD_ERROR), "message not found")
        self.assertEqual(ops.error_class(WORDS), "unclassified")
        self.assertEqual(ops.error_class(None), "unclassified")
        for raw in (RAW_MODEL_ERROR, RAW_SEND_ERROR, RAW_FOLD_ERROR):
            for s in ("Marisol", "grief", "/home", CHAT, PRACTICE):
                self.assertNotIn(s, ops.error_class(raw))

    def test_only_the_errors_own_prefix_is_read(self):
        quoted = "Bad Request: chat not found while sending 'the connection timed out at 429, I held on, forbidden'"
        self.assertEqual(ops.error_class(quoted), "chat not found")
        self.assertEqual(ops.error_class(f"sendMessage: “timeout 500 {WORDS}”"), "unclassified")
        self.assertEqual(ops.error_class("I ran 400 m and held my breath"), "unclassified")  # loose number, not a prefix
        self.assertEqual(ops.error_class("held: exit 1: 429 rate_limit_error"),
                         "exit 1 http 429 rate_limit_error held by the breaker")
        self.assertEqual(ops.error_class("upheld: nothing"), "unclassified")
        self.assertEqual(ops.error_class("exit 1 HTTP 401: Bad credentials"), "exit 1 http 401")

    def test_a_stored_class_outside_the_vocabulary_renders_unclassified(self):
        inc = {"opened_at": NOW.isoformat(), "facts": {"since": NOW.isoformat(), "error": "exit 1 marisol"}}
        self.assertIn("| Error class | unclassified |", ops.body("breaker_open", "open", inc, NOW))
        inc["facts"]["error"] = "exit 1 http 400 invalid_params (toolsets)"
        self.assertIn("| Error class | exit 1 http 400 invalid_params (toolsets) |",
                      ops.body("breaker_open", "open", inc, NOW))

    def test_no_notice_carries_a_time_of_day(self):
        facts = {"since": (NOW - timedelta(hours=26, minutes=17)).isoformat(), "probes_failed": 3,
                 "plain_openers": 2, "annotations_failed": 1,
                 "last_success": (NOW - timedelta(days=3, minutes=41)).isoformat(), "undelivered": 2, "folds": 1,
                 "newest": (NOW - timedelta(minutes=13)).isoformat(), "steps": ["fold"], "ticks": 6,
                 "exception": ["KeyError"], "error": "timeout"}
        inc = {"opened_at": (NOW - timedelta(hours=26, minutes=17)).isoformat(), "facts": facts}
        for kind in ops.KINDS:
            for action in ("open", "update", "reopen", "resolve"):
                text = ops.title(kind) + "\n" + ops.body(kind, action, inc, NOW)
                self.assertIsNone(HHMM.search(text), (kind, action, text))
        text = ops.body("model_floor", "open", inc, NOW)
        self.assertIn("| Since | 2026-11-19 UTC, 26 h ago |", text)
        self.assertIn("| Last model success | 2026-11-17 UTC, 3 d ago |", text)
        self.assertIn("Still open, 26 h after it was opened.", ops.body("model_floor", "update", inc, NOW))
        self.assertIn("it was open for 26 h", ops.body("model_floor", "resolve", inc, NOW))


class BreakerTest(OpsBase):
    def trip(self, at):
        outage.trip(self.cfg, RAW_MODEL_ERROR, at)

    def test_open_longer_than_the_threshold_files_one_issue_updates_then_closes(self):
        self.trip(NOW - timedelta(hours=1))
        self.run_ops()
        self.assertIsNone(self.incident("breaker_open"))  # 1 h: not yet
        self.assertEqual(self.gh.calls, [])
        self.run_ops(NOW + timedelta(hours=1, minutes=5))
        self.assertEqual(self.gh.verbs(), ["label create", "issue create"])
        argv, body = self.gh.calls[1]
        self.assertEqual(argv[:5], ["/usr/bin/gh", "issue", "create", "--repo", REPO])
        self.assertIn("journal-ops", argv)
        self.assertIn("exit 1 http 400 invalid_params (toolsets)", body)
        self.assertIn(f"| Since | {_date(NOW - timedelta(hours=1))} UTC, 2 h ago |", body)
        self.assertIn(ops.marker("breaker_open", self.incident("breaker_open")), body)
        inc = self.incident("breaker_open")
        self.assertEqual((inc["issue"], inc["pending"], inc["delivered"]), (42, None, True))
        # idempotent: later ticks inside update_hours say nothing more, and no second issue is made
        for m in (10, 60, 300):
            self.run_ops(NOW + timedelta(hours=1, minutes=5 + m))
        self.assertEqual(len(self.gh.calls), 2)
        self.run_ops(NOW + timedelta(hours=7, minutes=10))
        self.assertEqual(self.gh.verbs()[-1], "issue comment")
        self.assertEqual(self.gh.calls[-1][0][3], "42")
        self.assertIn("Still open", self.gh.calls[-1][1])
        outage.answered(self.cfg, NOW + timedelta(hours=8))
        self.run_ops(NOW + timedelta(hours=8, minutes=5))
        argv, _ = self.gh.calls[-1]
        self.assertEqual(argv[1:4], ["issue", "close", "42"])
        self.assertIn("breaker closed", argv[-1])
        self.assertIsNone(self.incident("breaker_open"))
        self.assertEqual(self.state()["closed"][-1]["issue"], 42)
        self.run_ops(NOW + timedelta(hours=9))
        self.assertEqual(len(self.gh.calls), 4)  # label once, open, one update, close: nothing after
        self.assert_private()

    def test_one_that_clears_before_it_was_filed_files_nothing(self):
        self.cfg.ops = {}  # delivery off: kept, not sent
        self.trip(NOW - timedelta(hours=3))
        self.run_ops()
        self.assertEqual(self.incident("breaker_open")["pending"], "open")
        outage.answered(self.cfg, NOW)
        self.cfg.ops = {"repo": REPO}
        self.run_ops(NOW + timedelta(minutes=5))
        self.assertIsNone(self.incident("breaker_open"))
        self.assertEqual(self.gh.calls, [])

    def test_no_model_by_design_is_no_incident(self):
        self.cfg.agent = {"enabled": False}
        self.trip(NOW - timedelta(hours=5))
        self.run_ops()
        self.assertIsNone(self.incident("breaker_open"))


class ModelFloorTest(OpsBase):
    def test_three_plain_openers_in_a_row_without_the_breaker(self):
        self.prompt(1)  # a model opener: the last success
        for n in (2, 3):
            self.prompt(n, source="plain:agent-failed")
        with open(self.cfg.state_dir / "errors.jsonl", "a") as fh:  # what the tick files for an opener that fell
            fh.write(json.dumps({"at": NOW.isoformat(), "mode": "tick",
                                 "error": f"2026-11-17-{PRACTICE}-3: opener: plain:agent-failed: {RAW_MODEL_ERROR}"})
                     + "\n")
        self.run_ops()
        self.assertIsNone(self.incident("model_floor"))  # two: not yet
        self.prompt(4, source="plain:uncited quote")  # a rejected answer falls to its floor too
        self.prompt(5, source="plain:practice")  # plain by design: neither
        self.run_ops()
        inc = self.incident("model_floor")
        self.assertEqual((inc["facts"]["plain_openers"], inc["facts"]["annotations_failed"]), (3, 0))
        body = self.gh.calls[-1][1]
        self.assertIn("| Openers sent plain | 3 |", body)
        self.assertIn("exit 1 http 400 invalid_params (toolsets)", body)
        self.assertIn(f"| Last model success | {_date(NOW - timedelta(hours=9))} UTC, 9 h ago |", body)
        self.prompt(6, source="iris")
        self.run_ops(NOW + timedelta(minutes=5))
        self.assertEqual(self.gh.verbs()[-1], "issue close")
        self.assert_private()

    def test_held_while_the_breaker_is_open_and_counted_afresh_after(self):
        for n in (1, 2, 3):
            self.prompt(n, source="plain:agent-failed")
        outage.trip(self.cfg, "timed out", NOW - timedelta(minutes=30))
        self.run_ops()
        self.assertIsNone(self.incident("model_floor"))
        outage.answered(self.cfg, NOW + timedelta(minutes=1))
        self.run_ops(NOW + timedelta(minutes=5))
        self.assertIsNone(self.incident("model_floor"))  # the floors under the breaker were its, not this kind's

    def test_annotations_given_up_count_with_no_chat(self):
        cfg = self.cfg
        cfg.platform = "none"
        for n in (1, 2, 3):
            with ledger.locked(cfg.ledger_path) as data:
                data["prompts"].append({"id": f"2026-11-17-{PRACTICE}-{n}", "kind": PRACTICE, "opened_via": "web",
                                        "local_date": "2026-11-17", "annotate_attempts": tick.ANNOTATE_ATTEMPTS,
                                        "captures": [{"at": (NOW - timedelta(hours=n)).isoformat(), "text": WORDS}]})
        self.run_ops()
        self.assertEqual(self.incident("model_floor")["facts"]["annotations_failed"], 3)
        self.assertIn("issue create", self.gh.verbs())
        self.assert_private()


class SendAndFoldTest(OpsBase):
    def test_undelivered_prompts_open_an_incident_and_a_delivery_closes_it(self):
        self.prompt(1)
        for n in (2, 3):
            p = self.prompt(n)
            with ledger.locked(self.cfg.ledger_path) as data:
                q = ledger.find(data, p["id"])
                q.pop("message_id")
                q.update(send_attempts=tick.SEND_ATTEMPTS, send_error=RAW_SEND_ERROR)
        self.run_ops()
        inc = self.incident("send_failing")
        self.assertEqual((inc["facts"]["undelivered"], inc["facts"]["error"]), (2, "chat not found"))
        self.prompt(4)
        self.run_ops(NOW + timedelta(minutes=5))
        self.assertIsNone(self.incident("send_failing"))
        self.assertEqual(self.gh.verbs()[-1], "issue close")
        self.assert_private()

    def test_no_chat_no_send_incident(self):
        self.cfg.platform = "none"
        p = self.prompt(1)
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, p["id"]).update(message_id=None, send_attempts=3, send_error=RAW_SEND_ERROR)
        self.run_ops()
        self.assertIsNone(self.incident("send_failing"))

    def test_a_fold_given_up_is_read_from_its_record(self):
        sent = datetime.now(timezone.utc) - timedelta(hours=7)
        add_prompt(self.cfg, sent=sent, message_id="5")

        def boom(*a, **k):  # a permanent refusal: the fold gives up at once
            raise telegram.TelegramError(f"editMessageText: {RAW_FOLD_ERROR}")
        with mock.patch.object(telegram, "edit_html", boom):
            tick.fold(self.cfg, datetime.now(timezone.utc), [])
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-morning")
        self.assertEqual(p["fold_gave_up"]["reason"], "permanent")
        gave = datetime.fromisoformat(p["fold_gave_up"]["at"])
        self.run_ops(gave + timedelta(minutes=5))
        inc = self.incident("fold_given_up")
        self.assertEqual((inc["facts"]["folds"], inc["facts"]["error"], inc["facts"]["reason"]),
                         (1, "message not found", "permanent"))
        self.assertIn("| Given up because | refused for good |", self.gh.calls[-1][1])
        with ledger.locked(self.cfg.ledger_path) as data:  # a reason outside tick.fold's two words is left out
            ledger.find(data, "2026-10-14-morning")["fold_gave_up"]["reason"] = WORDS
        self.assertNotIn("reason", ops._fold_given_up(self.cfg, ledger.snapshot(self.cfg.ledger_path),
                                                      gave + timedelta(minutes=6)))
        self.run_ops(gave + timedelta(hours=73))
        self.assertIsNone(self.incident("fold_given_up"))  # the window passed with no other
        self.assertEqual(self.gh.verbs()[-1], "issue close")
        self.assert_private()


class StepsAndStaleTickTest(OpsBase):
    def test_a_step_that_crashes_tick_after_tick(self):
        for i in range(5):
            self.run_ops(NOW + timedelta(minutes=5 * i), crashed={"annotate": "KeyError", WORDS: "Boom"})
        self.assertIsNone(self.incident("step_failing"))
        self.run_ops(NOW + timedelta(minutes=25), crashed={"annotate": "KeyError"})
        inc = self.incident("step_failing")
        self.assertEqual((inc["facts"]["steps"], inc["facts"]["ticks"]), (["annotate"], 6))
        self.assertIn("| Steps | annotate |", self.gh.calls[-1][1])
        self.assertIn("| Exception class | KeyError |", self.gh.calls[-1][1])
        self.run_ops(NOW + timedelta(minutes=30), crashed={})
        self.assertIsNone(self.incident("step_failing"))
        self.assert_private()

    def test_a_stopped_tick_is_seen_only_from_outside(self):
        self.run_ops(NOW - timedelta(hours=2))  # the last scheduled tick
        self.assertEqual(ops.check(self.cfg, NOW, dry_run=True)[0].split("\n")[0],
                         "[dry-run] open tick_stale: journal ops: the tick has stopped running")
        self.assertEqual(self.gh.calls, [])  # a dry run sends nothing
        ops.check(self.cfg, NOW)
        self.assertEqual(self.incident("tick_stale")["facts"]["since"], (NOW - timedelta(hours=2)).isoformat())
        self.assertIn("| Since | 2026-11-20 UTC, 2 h ago |", self.gh.calls[-1][1])
        self.run_ops(NOW + timedelta(minutes=5))  # a tick again: closed
        self.assertIsNone(self.incident("tick_stale"))
        self.assertEqual(self.gh.verbs(), ["label create", "issue create", "issue close"])
        self.assert_private()

    def test_a_hung_tick_does_not_stop_the_check(self):
        self.run_ops(NOW - timedelta(hours=3))
        with open(self.cfg.state_dir / "tick.lock", "a+") as fh:
            import fcntl
            fcntl.flock(fh, fcntl.LOCK_EX)  # a tick holding its lock for hours
            ops.check(self.cfg, NOW)
        self.assertIsNotNone(self.incident("tick_stale"))


class DeliveryTest(OpsBase):
    def test_a_failed_notice_is_logged_by_class_retried_later_and_never_raises(self):
        self.gh.fail = f"HTTP 401: Bad credentials for {WORDS}"
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        log = self.run_ops()
        self.assertEqual(log[-1], "ops: notice open for breaker_open failed (exit 1 http 401); tried again later")
        self.assertTrue(tick.files_as_error(log[-1]))  # the tick files it in errors.jsonl
        self.assertEqual(self.incident("breaker_open")["pending"], "open")
        n = len(self.gh.calls)
        self.run_ops(NOW + timedelta(minutes=10))
        self.assertEqual(len(self.gh.calls), n)  # waits before trying again
        self.gh.fail = None
        self.run_ops(NOW + timedelta(minutes=31))
        self.assertEqual(self.incident("breaker_open")["issue"], 42)
        self.assertEqual(self.gh.verbs().count("issue create"), 1)

    def test_a_crash_inside_the_pass_is_a_log_line(self):
        with mock.patch("journal.ops.judge", side_effect=RuntimeError(WORDS)):
            log = self.run_ops()
        self.assertEqual(log, ["ops: pass failed (RuntimeError)"])

    def test_gh_missing_is_a_failure_like_any_other(self):
        with mock.patch("journal.ops.subprocess.run", side_effect=FileNotFoundError("gh")):
            outage.trip(self.cfg, "timed out", NOW - timedelta(hours=3))
            log = self.run_ops()
        self.assertIn("ops: notice open for breaker_open failed (not found); tried again later", log)

    def test_the_operators_command_gets_the_same_fixed_text(self):
        self.cfg.ops = {"command": ["/opt/notify", "{action}", "{kind}", "{title}"]}
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        self.run_ops()
        argv, stdin = self.gh.calls[0]
        self.assertEqual(argv, ["/opt/notify", "open", "breaker_open", ops.TITLES["breaker_open"]])
        self.assertIn("invalid_params (toolsets)", stdin)
        self.assert_private()

    def test_off_keeps_incidents_for_status_and_sends_nothing(self):
        self.cfg.ops = {}
        self.cfg.platform = "none"  # ops is not the journal's chat: it works with none
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        self.assertEqual(self.run_ops(), ["ops: incident breaker_open opened"])
        self.assertEqual(self.gh.calls, [])
        s = status.status(self.cfg, NOW)
        self.assertEqual(s["ops"]["delivery"], "off")
        self.assertEqual(s["ops"]["incidents"][0]["kind"], "breaker_open")
        self.assertIn("Ops incidents (not sent: ops.repo is not set): breaker_open (since", status.format_status(s))
        rows = [r for r in status.doctor(self.cfg, token=False) if r[0] == "ops notice"]
        self.assertEqual(rows[0][1], "ok")

    def test_tick_runs_the_pass_and_survives_it(self):
        quiet = ("send_due", "fold", "process_audio", "process_images", "annotate", "reply_annotations", "prune",
                 "_reflect")
        with mock.patch.multiple(tick, **{n: mock.DEFAULT for n in quiet}), \
                mock.patch.multiple("journal.tick.programs", send_due_interrupts=mock.DEFAULT, step=mock.DEFAULT), \
                mock.patch("journal.tick.privacy.update"), mock.patch("journal.tick.sweep.run"), \
                mock.patch("journal.tick.conversations.run"), mock.patch("journal.tick.recovery.run"), \
                mock.patch("journal.tick.mirror.flush"), mock.patch("journal.tick.today.refresh"), \
                mock.patch("journal.tick.notebook.sweep"):
            tick.fold.side_effect = KeyError("x")
            code, log = tick.run(self.cfg)
            self.assertEqual(code, 1)
            self.assertEqual(self.state()["steps"]["fold"]["ticks"], 1)
            with mock.patch("journal.ops._assess", side_effect=OSError("disk")):
                code, log = tick.run(self.cfg)
            self.assertIn("ops: pass failed (OSError)", log)
            self.assertEqual(code, 1)  # (the fold's crash; the ops pass changes no exit code)


class StepNamesTest(OpsBase):
    def test_hostile_step_and_class_names_never_reach_a_notice(self):
        for i in range(6):
            self.run_ops(NOW + timedelta(minutes=5 * i), crashed={WORDS: "not a class!", PRACTICE: WORDS})
        inc = self.incident("step_failing")
        self.assertEqual((inc["facts"]["steps"], inc["facts"]["ticks"]), (["other"], 6))
        body = self.gh.calls[-1][1]
        self.assertIn("| Steps | other |", body)
        self.assertIn("| Exception class | Exception |", body)
        self.assert_private()

    def test_a_pass_that_finds_the_run_lock_held_still_counts_the_tick(self):
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.cfg.state_dir / ops.RUN_LOCK, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)  # a `journal ops check` running now
            for i in range(3):
                self.run_ops(NOW + timedelta(minutes=5 * i), crashed={"annotate": "KeyError"})
        st = self.state()
        self.assertEqual(st["steps"]["annotate"]["ticks"], 3)
        self.assertEqual(st["tick_at"], (NOW + timedelta(minutes=10)).isoformat())
        self.assertEqual(self.gh.calls, [])  # (the pass holding the lock judges and delivers)


class DeliveryOutsideTheTickTest(OpsBase):
    def test_the_tick_delivers_after_releasing_its_lock(self):
        free = []

        def look(argv):
            with open(self.cfg.state_dir / "tick.lock", "a+") as fh:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    fcntl.flock(fh, fcntl.LOCK_UN)
                    free.append(True)
                except BlockingIOError:
                    free.append(False)
        self.gh.during = look
        outage.trip(self.cfg, RAW_MODEL_ERROR, datetime.now(timezone.utc) - timedelta(hours=3))
        with _quiet_tick():
            code, log = tick.run(self.cfg)
        self.assertEqual(self.gh.verbs(), ["label create", "issue create"])
        self.assertEqual(free, [True, True])  # gh ran with tick.lock free: the next tick and a send are not held
        self.assertIn("ops: notice open for breaker_open (#42)", log)

    def test_a_failed_delivery_after_the_lock_still_lands_in_errors_jsonl(self):
        self.gh.fail = "HTTP 502: Bad gateway"
        outage.trip(self.cfg, RAW_MODEL_ERROR, datetime.now(timezone.utc) - timedelta(hours=3))
        with _quiet_tick():
            tick.run(self.cfg)
        errors = (self.cfg.state_dir / "errors.jsonl").read_text().splitlines()
        self.assertIn("ops: notice open for breaker_open failed (exit 1 http 502); tried again later",
                      [json.loads(l)["error"] for l in errors])

    def test_a_slow_gh_is_cut_off_by_the_pass_deadline(self):
        clock = [1000.0]

        def slow(argv):
            clock[0] += 15  # every gh call takes 15 s
        self.gh.during = slow
        for n in (1, 2, 3):
            self.prompt(n, source="plain:agent-failed")
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))  # breaker_open
        self.cfg.platform = "telegram"
        with mock.patch("journal.ops.time.monotonic", lambda: clock[0]):
            log = []
            ops.assess(self.cfg, NOW, log, crashed={"fold": "KeyError"})
            for kind in ("model_floor", "send_failing", "fold_given_up", "step_failing"):  # four more due
                with ledger.locked_state(self.cfg.state_dir / ops.STATE) as st:
                    st["incidents"][kind] = {"opened_at": NOW.isoformat(), "facts": {}, "pending": "open",
                                             "tries": 0, "delivered": False, "issue": None}
            ops.deliver_due(self.cfg, NOW, log)
        self.assertLessEqual(clock[0] - 1000.0, ops.PASS_SECONDS + ops.CALL_SECONDS)
        self.assertTrue(all(t <= ops.CALL_SECONDS for t in self.gh.timeouts))
        self.assertIn("ops: delivery out of time; the rest is sent on the next pass", log)
        pending = [k for k, v in self.state()["incidents"].items() if v.get("pending") == "open"]
        self.assertTrue(pending)  # left for the next pass, not counted as a failure
        self.assertTrue(all(not self.incident(k).get("tries") for k in pending))


class IdempotentOpenTest(OpsBase):
    def test_a_create_that_went_through_unrecorded_is_adopted_not_filed_again(self):
        self.gh.hang_create = True
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        log = self.run_ops()
        self.assertIn("ops: notice open for breaker_open failed (timeout); tried again later", log)
        self.assertEqual(len(self.gh.open_issues), 1)  # it is on GitHub all the same
        self.gh.hang_create = False
        self.run_ops(NOW + timedelta(minutes=31))
        self.assertEqual(self.gh.verbs().count("issue create"), 1)
        self.assertEqual(self.gh.verbs()[-1], "issue list")
        self.assertIn("--label", self.gh.calls[-1][0])
        inc = self.incident("breaker_open")
        self.assertEqual((inc["issue"], inc["delivered"], inc["pending"]), (42, True, None))
        self.assertIn("<!-- journal-ops kind=breaker_open opened=2026-11-20 -->", self.gh.open_issues[0]["body"])
        self.assert_private()

    def test_no_marker_match_files_it(self):
        self.gh.fail = "HTTP 502: Bad gateway"
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        self.run_ops()
        self.gh.fail = None
        self.gh.open_issues = [{"number": 7, "body": "<!-- journal-ops kind=model_floor opened=2026-11-20 -->"}]
        self.run_ops(NOW + timedelta(minutes=31))
        self.assertEqual(self.gh.verbs()[-2:], ["issue list", "issue create"])
        self.assertEqual(self.incident("breaker_open")["issue"], 42)

    def test_a_failed_create_makes_the_label_again_next_time(self):
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        self.gh.fail = "HTTP 404: Not Found"
        with mock.patch.object(ops, "_gh_label", lambda cfg, st, budget: st.update(label_ready="x")):
            self.run_ops()
        self.assertIsNone(self.state().get("label_ready"))
        self.gh.fail = None
        self.run_ops(NOW + timedelta(minutes=31))
        self.assertEqual(self.gh.verbs()[-3:], ["label create", "issue list", "issue create"])


class RouteTest(OpsBase):
    def filed(self):
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        self.run_ops()
        inc = self.incident("breaker_open")
        self.assertEqual((inc["issue"], inc["route"]), (42, f"github:{REPO}"))
        self.cfg.ops = {"repo": OTHER, "gh": "/usr/bin/gh"}

    def assert_never_touched_42_elsewhere(self):
        for argv, _ in self.gh.calls:
            if OTHER in argv:
                self.assertNotIn("42", argv[3:4], argv)

    def test_an_update_after_the_repo_changed_files_again_in_the_new_repo(self):
        self.filed()
        log = self.run_ops(NOW + timedelta(hours=7))
        self.assertIn("ops: incident breaker_open filed again: its issue was on another route (ops.repo changed)", log)
        argv, _ = self.gh.calls[-1]
        self.assertEqual(argv[1:5], ["issue", "create", "--repo", OTHER])
        inc = self.incident("breaker_open")
        self.assertEqual((inc["issue"], inc["route"]), (43, f"github:{OTHER}"))
        self.assertNotIn("issue comment", self.gh.verbs())
        self.assert_never_touched_42_elsewhere()

    def test_a_close_after_the_repo_changed_is_dropped_never_sent_elsewhere(self):
        self.filed()
        outage.answered(self.cfg, NOW + timedelta(hours=1))
        log = self.run_ops(NOW + timedelta(hours=1, minutes=5))
        self.assertIn("ops: incident breaker_open cleared; its issue was filed on another route (ops.repo changed), "
                      "so no close is sent", log)
        self.assertNotIn("issue close", self.gh.verbs())
        self.assertIsNone(self.incident("breaker_open"))
        self.assert_never_touched_42_elsewhere()
        # and deliver() itself refuses, whatever asks it
        inc = {"issue": 42, "route": f"github:{REPO}", "opened_at": NOW.isoformat(), "facts": {}}
        with self.assertRaises(ops.Undelivered):
            ops.deliver(self.cfg, {}, "breaker_open", inc, "resolve", NOW)
        self.assertFalse(any(a[1:3] == ["issue", "close"] for a, _ in self.gh.calls))


class FlapTest(OpsBase):
    def test_back_within_hours_of_its_close_reopens_the_same_issue(self):
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        self.run_ops()
        outage.answered(self.cfg, NOW + timedelta(hours=1))
        self.run_ops(NOW + timedelta(hours=1, minutes=5))
        self.assertEqual(self.gh.verbs()[-1], "issue close")
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW + timedelta(hours=2))
        log = self.run_ops(NOW + timedelta(hours=4, minutes=5))
        self.assertEqual(self.gh.verbs()[-2:], ["issue reopen", "issue comment"])
        self.assertEqual(self.gh.calls[-2][0][3], "42")
        self.assertIn("so this issue is reopened", self.gh.calls[-1][1])
        self.assertIn("ops: notice reopen for breaker_open (#42)", log)
        self.assertEqual(self.gh.verbs().count("issue create"), 1)
        inc = self.incident("breaker_open")
        self.assertEqual((inc["issue"], inc["delivered"]), (42, True))
        self.assert_private()

    def test_back_long_after_its_close_files_a_new_issue(self):
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW - timedelta(hours=3))
        self.run_ops()
        outage.answered(self.cfg, NOW + timedelta(hours=1))
        self.run_ops(NOW + timedelta(hours=1, minutes=5))
        outage.trip(self.cfg, RAW_MODEL_ERROR, NOW + timedelta(hours=8))
        self.run_ops(NOW + timedelta(hours=10, minutes=5))
        self.assertEqual(self.gh.verbs()[-1], "issue create")
        self.assertEqual(self.incident("breaker_open")["issue"], 43)


def _quiet_tick():
    """Every tick step stubbed: the tick runs only its frame and the ops notice."""
    import contextlib
    stack = contextlib.ExitStack()
    quiet = ("send_due", "fold", "process_audio", "process_images", "annotate", "reply_annotations", "prune",
             "_reflect")
    stack.enter_context(mock.patch.multiple(tick, **{n: mock.DEFAULT for n in quiet}))
    stack.enter_context(mock.patch.multiple("journal.tick.programs", send_due_interrupts=mock.DEFAULT,
                                            step=mock.DEFAULT))
    for target in ("privacy.update", "sweep.run", "conversations.run", "recovery.run", "mirror.flush",
                   "today.refresh", "notebook.sweep"):
        stack.enter_context(mock.patch(f"journal.tick.{target}"))
    return stack


class ConfigTest(unittest.TestCase):
    def test_settings_load_and_are_checked(self):
        with tempfile.TemporaryDirectory() as t:
            cfg = jconfig.from_dict({"root": t, "ops": {"repo": REPO, "floor_run": 5}})
            self.assertEqual((ops.repo(cfg), ops._number(cfg, "floor_run"), ops.delivery(cfg)), (REPO, 5, "github"))
            bad = jconfig.from_dict({"root": t, "ops": {"repo": "not a repo", "update_hours": -1, "command": "x"}})
            self.assertEqual(ops.delivery(bad), "off")
            self.assertEqual(len(ops.problems(bad)), 3)
            self.assertEqual(ops.delivery(jconfig.from_dict({"root": t})), "off")


def _date(when: datetime) -> str:
    return when.astimezone(timezone.utc).strftime("%Y-%m-%d")


if __name__ == "__main__":
    unittest.main()
