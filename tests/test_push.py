"""Web Push from ``journal serve`` (OPEN-PROMPTS *Push notifications*, rule 12; issue #19): the setting, the keys and
subscriptions in ``serve-push.json``, which slots a pass pushes (once each, ``push-sent.json``), what a push says, and
what it never writes. The cryptography itself is ``test_webpush.py``; here the send is a recorder, so the slot rules
run without the extra ``journal[push]``. The routes are ``ServeCase`` requests at the end."""
import hashlib
import importlib.util
import json
import os
import stat
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from helpers import CleanEnv, make_cfg
from serve_helpers import ServeCase

from journal import config as jconfig
from journal import ledger
from journal.serve import push, webpush

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None
IST = ZoneInfo("Asia/Kolkata")
MORNING = datetime(2026, 10, 15, 6, 0, tzinfo=IST)   # the built-in morning's slot (Thursday)
P256DH = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"  # RFC 8291's
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
PHONE = {"endpoint": "https://fcm.googleapis.com/fcm/send/device-one", "keys": {"p256dh": P256DH, "auth": AUTH}}
TABLET = {"endpoint": "https://web.push.apple.com/device-two", "keys": {"p256dh": P256DH, "auth": AUTH}}


class Recorder:
    """A stand-in for ``webpush.send``: records (endpoint, payload) and answers what ``answers`` says per endpoint."""

    def __init__(self, answers=None):
        self.calls, self.answers = [], answers or {}

    def __call__(self, subscription, payload, *, vapid_private, subject, ttl=3600, timeout=10):
        self.calls.append((subscription["endpoint"], json.loads(payload)))
        return self.answers.get(subscription["endpoint"], webpush.Result("sent", 201))


def tree(*dirs: Path) -> dict:
    """Every file under ``dirs`` with its checksum."""
    out = {}
    for d in dirs:
        for p in sorted(Path(d).rglob("*")):
            if p.is_file():
                out[str(p)] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class PushCase(CleanEnv):
    platform = "none"

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), platform=self.platform)
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        # keys and subscriptions written as the routes would, so no pass here needs cryptography
        (self.cfg.state_dir / push.STATE).write_text(json.dumps(
            {"vapid": {"private": "test-private", "public": "test-public"}, "subscriptions": [PHONE]}), "utf-8")

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def run_at(self, when: datetime, send=None):
        send = send or Recorder()
        return push.run_once(self.cfg, when.astimezone(timezone.utc), send=send), send


class SettingTest(unittest.TestCase):
    def cfg(self, value):
        tmp = tempfile.mkdtemp()
        data = {"root": tmp, "state_dir": tmp}
        if value is not None:
            data["push"] = value
        return jconfig.from_dict(data)

    def test_when_defaults_to_no_chat(self):
        cfg = self.cfg(None)
        self.assertEqual(push.when(cfg), "no-chat")
        self.assertEqual(cfg.warnings, [])

    def test_always_is_taken(self):
        self.assertEqual(push.when(self.cfg({"when": "always"})), "always")

    def test_anything_else_is_no_chat_and_said(self):
        cfg = self.cfg({"when": "sometimes"})
        self.assertEqual(push.when(cfg), "no-chat")
        self.assertTrue(any("push.when" in w for w in cfg.warnings), cfg.warnings)
        cfg = self.cfg("always")
        self.assertEqual(push.when(cfg), "no-chat")
        self.assertTrue(any("push" in w for w in cfg.warnings), cfg.warnings)

    def test_active_with_no_chat_or_always(self):
        tmp = Path(tempfile.mkdtemp())
        self.assertTrue(push.active(make_cfg(tmp, platform="none")))
        chat = make_cfg(tmp)
        self.assertFalse(push.active(chat))
        chat.push["when"] = "always"
        self.assertTrue(push.active(chat))


class WhichSlotsTest(PushCase):
    def test_a_slot_whose_time_has_come_is_pushed_once(self):
        pushed, sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertEqual(pushed, ["2026-10-15-morning"])
        self.assertEqual([e for e, _p in sent.calls], [PHONE["endpoint"]])
        again, sent = self.run_at(MORNING + timedelta(minutes=2))
        self.assertEqual((again, sent.calls), ([], []))

    def test_pushed_once_across_a_restart(self):
        """The marker is a file: a new serve process does not push the slot again."""
        self.run_at(MORNING + timedelta(minutes=1))
        self.assertIn("2026-10-15-morning", json.loads((self.cfg.state_dir / push.SENT).read_text())["slots"])
        again, _sent = self.run_at(MORNING + timedelta(minutes=30))
        self.assertEqual(again, [])

    def test_upcoming_is_not_pushed(self):
        pushed, sent = self.run_at(MORNING - timedelta(minutes=1))
        self.assertEqual((pushed, sent.calls), ([], []))

    def test_past_grace_hours_is_not_pushed(self):
        self.cfg.grace_hours = 2
        pushed, _sent = self.run_at(MORNING + timedelta(hours=2, minutes=1))
        self.assertEqual(pushed, [])
        pushed, _sent = self.run_at(MORNING + timedelta(hours=1, minutes=59))
        self.assertEqual(pushed, ["2026-10-15-morning"])

    def test_an_answered_slot_is_not_pushed(self):
        """Done early on a face (OPEN-PROMPTS step 3): the record has his words, so there is nothing to nudge."""
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": "2026-10-15-morning", "stem": "2026-10-15-morning", "kind": "morning",
                                    "local_date": "2026-10-15", "message_id": None, "opened_via": "web",
                                    "sent_at": (MORNING - timedelta(minutes=20)).isoformat(),
                                    "captures": [{"at": (MORNING - timedelta(minutes=20)).isoformat(), "via": "web"}]})
        pushed, sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertEqual((pushed, sent.calls), ([], []))

    def test_a_practice_turned_off_is_not_pushed(self):
        self.cfg.practices_dir.mkdir(parents=True, exist_ok=True)
        from journal import library
        shipped = library.shipped("morning").files["morning.md"]
        (self.cfg.practices_dir / "morning.md").write_text(
            shipped.replace("title: Morning journal\n", "title: Morning journal\nenabled: false\n"), encoding="utf-8")
        self.assertFalse(library.resolve("morning", self.cfg).enabled)
        pushed, _sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertEqual(pushed, [])

    def test_last_nights_slot_inside_its_grace_is_pushed_after_midnight(self):
        evening = datetime(2026, 10, 14, 23, 0, tzinfo=IST)
        pushed, _sent = self.run_at(evening + timedelta(minutes=70))   # 00:10 on the 15th
        self.assertEqual(pushed, ["2026-10-14-evening"])

    def test_nothing_without_subscriptions(self):
        (self.cfg.state_dir / push.STATE).write_text(json.dumps(
            {"vapid": {"private": "k", "public": "p"}, "subscriptions": []}), "utf-8")
        pushed, sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertEqual((pushed, sent.calls), ([], []))
        self.assertFalse((self.cfg.state_dir / push.SENT).exists())

    def test_nothing_without_keys(self):
        (self.cfg.state_dir / push.STATE).unlink()
        pushed, sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertEqual((pushed, sent.calls), ([], []))

    def test_without_the_extra_a_pass_does_nothing(self):
        with mock.patch.object(webpush, "available", return_value=False):
            self.assertEqual(push.run_once(self.cfg, (MORNING + timedelta(minutes=1)).astimezone(timezone.utc)), [])
        self.assertFalse((self.cfg.state_dir / push.SENT).exists())

    def test_the_marker_is_pruned(self):
        old = (MORNING - timedelta(days=push.KEEP_DAYS + 1)).isoformat()
        (self.cfg.state_dir / push.SENT).write_text(json.dumps({"slots": {"2026-10-10-morning": old}}), "utf-8")
        self.run_at(MORNING + timedelta(minutes=1))
        slots = json.loads((self.cfg.state_dir / push.SENT).read_text())["slots"]
        self.assertEqual(set(slots), {"2026-10-15-morning"})


class ChatInstallTest(PushCase):
    platform = "telegram"

    def test_no_chat_pushes_nothing_while_a_chat_is_configured(self):
        pushed, sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertEqual((pushed, sent.calls), ([], []))

    def test_always_pushes_a_slot_the_tick_delivered_before_the_first_look(self):
        """Eligibility is the schedule and the marker, never the *due* state, which a delivery a moment earlier hides."""
        self.cfg.push["when"] = "always"
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": "2026-10-15-morning", "stem": "2026-10-15-morning", "kind": "morning",
                                    "local_date": "2026-10-15", "chat_id": self.cfg.chat_id, "message_id": "900",
                                    "sent_at": MORNING.isoformat(), "opener": "Morning"})
        pushed, sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertEqual(pushed, ["2026-10-15-morning"])
        self.assertEqual(len(sent.calls), 1)


class WhatAPushSaysTest(PushCase):
    def test_the_payload_is_the_title_and_due_now(self):
        _pushed, sent = self.run_at(MORNING + timedelta(minutes=1))
        _endpoint, payload = sent.calls[0]
        self.assertEqual(payload, {"title": "Morning journal", "body": "due now", "practice": "morning"})

    def test_never_his_words_or_an_opener(self):
        with ledger.locked(self.cfg.ledger_path) as data:   # a failed send's record carrying an opener
            data["prompts"].append({"id": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15",
                                    "scheduled": MORNING.isoformat(), "send_attempts": 1,
                                    "opener": "SECRET-OPENER words of hers"})
        _pushed, sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertNotIn("SECRET", json.dumps(sent.calls))


class SubscriptionsAnswerTest(PushCase):
    def test_gone_drops_the_subscription(self):
        (self.cfg.state_dir / push.STATE).write_text(json.dumps(
            {"vapid": {"private": "k", "public": "p"}, "subscriptions": [PHONE, TABLET]}), "utf-8")
        send = Recorder({PHONE["endpoint"]: webpush.Result("gone", 410)})
        pushed, _ = self.run_at(MORNING + timedelta(minutes=1), send)
        self.assertEqual(pushed, ["2026-10-15-morning"])
        self.assertEqual([s["endpoint"] for s in push.subscriptions(self.cfg)], [TABLET["endpoint"]])

    def test_a_404_drops_it_too(self):
        send = Recorder({PHONE["endpoint"]: webpush.Result("gone", 404)})
        self.run_at(MORNING + timedelta(minutes=1), send)
        self.assertEqual(push.subscriptions(self.cfg), [])

    def test_a_failure_keeps_it_and_tries_the_slot_again(self):
        send = Recorder({PHONE["endpoint"]: webpush.Result("failed", 503, "unavailable")})
        pushed, _ = self.run_at(MORNING + timedelta(minutes=1), send)
        self.assertEqual(pushed, [])
        self.assertEqual(len(push.subscriptions(self.cfg)), 1)
        pushed, _ = self.run_at(MORNING + timedelta(minutes=2))
        self.assertEqual(pushed, ["2026-10-15-morning"])

    def test_a_send_that_raises_is_a_failure_not_a_crash(self):
        def boom(*a, **k):
            raise ValueError("bad key")
        pushed, _ = self.run_at(MORNING + timedelta(minutes=1), boom)
        self.assertEqual(pushed, [])


class NotADeliveryTest(PushCase):
    """Rule 12: a push writes no prompt record and nothing in ``sent.jsonl``, ``send-times.json`` or the thread stamps,
    so it is never a chat boundary and never ends a ``/x`` window. Only its own two files change."""

    def test_a_push_writes_nothing_but_its_own_files(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": "2026-10-14-evening", "kind": "evening", "local_date": "2026-10-14",
                                    "sent_at": datetime(2026, 10, 14, 23, 5, tzinfo=IST).isoformat(),
                                    "message_id": None, "opened_via": "web", "captures": [{"via": "web"}]})
        before = tree(self.cfg.state_dir, self.cfg.root)
        pushed, _sent = self.run_at(MORNING + timedelta(minutes=1))
        self.assertEqual(pushed, ["2026-10-15-morning"])
        after = tree(self.cfg.state_dir, self.cfg.root)
        changed = {Path(p).name for p in set(before) | set(after) if before.get(p) != after.get(p)}
        self.assertLessEqual(changed, {push.SENT, push.STATE, "push-sent.lock", "serve-push.lock"}, changed)
        for name in ("ledger.json", "sent.jsonl", "send-times.json", "hook-state.json", "tick.lock"):
            self.assertNotIn(name, changed)
        self.assertFalse((self.cfg.state_dir / "sent.jsonl").exists())
        self.assertFalse((self.cfg.state_dir / "send-times.json").exists())
        self.assertFalse((self.cfg.state_dir / "hook-state.json").exists())


class SubscribeTest(PushCase):
    def test_a_subscription_is_kept_once_per_endpoint(self):
        now = MORNING.astimezone(timezone.utc)
        self.assertTrue(push.subscribe(self.cfg, TABLET, now))
        self.assertFalse(push.subscribe(self.cfg, TABLET, now))   # the same device again: no second copy
        self.assertEqual([s["endpoint"] for s in push.subscriptions(self.cfg)], [PHONE["endpoint"], TABLET["endpoint"]])
        self.assertTrue(push.unsubscribe(self.cfg, PHONE["endpoint"]))
        self.assertFalse(push.unsubscribe(self.cfg, PHONE["endpoint"]))
        self.assertEqual([s["endpoint"] for s in push.subscriptions(self.cfg)], [TABLET["endpoint"]])

    def test_the_file_is_private(self):
        push.subscribe(self.cfg, TABLET, MORNING)
        mode = stat.S_IMODE(os.stat(self.cfg.state_dir / push.STATE).st_mode)
        self.assertEqual(mode, 0o600)

    def test_what_a_subscription_must_be(self):
        bad = [
            {},
            {"endpoint": "http://fcm.googleapis.com/fcm/send/x", "keys": PHONE["keys"]},          # not https
            {"endpoint": "https://push.example.org/x", "keys": PHONE["keys"]},                   # not a push service
            {"endpoint": "https://127.0.0.1/x", "keys": PHONE["keys"]},
            {"endpoint": "https://fcm.googleapis.com.evil.example/x", "keys": PHONE["keys"]},
            {"endpoint": "https://user@fcm.googleapis.com/x", "keys": PHONE["keys"]},
            {"endpoint": PHONE["endpoint"], "keys": {"p256dh": "short", "auth": AUTH}},
            {"endpoint": PHONE["endpoint"], "keys": {"p256dh": P256DH, "auth": "AAAA"}},
            {"endpoint": PHONE["endpoint"]},
            {"endpoint": PHONE["endpoint"] + "x" * 2048, "keys": PHONE["keys"]},
        ]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(push.Invalid):
                    push.subscribe(self.cfg, body, MORNING)
        for host in ("https://fcm.googleapis.com/fcm/send/a", "https://updates.push.services.mozilla.com/wpush/v2/a",
                     "https://web.push.apple.com/a", "https://wns2-par02p.notify.windows.com/w/?token=a"):
            with self.subTest(host=host):
                self.assertTrue(push.subscribe(self.cfg, {"endpoint": host, "keys": PHONE["keys"]}, MORNING))

    def test_at_most_a_few_devices_the_oldest_goes(self):
        for n in range(push.MAX_SUBSCRIPTIONS + 2):
            push.subscribe(self.cfg, {"endpoint": f"https://fcm.googleapis.com/fcm/send/d{n}", "keys": PHONE["keys"]},
                           MORNING + timedelta(minutes=n))
        subs = push.subscriptions(self.cfg)
        self.assertEqual(len(subs), push.MAX_SUBSCRIPTIONS)
        self.assertNotIn(PHONE["endpoint"], [s["endpoint"] for s in subs])


@unittest.skipUnless(HAS_CRYPTO, "cryptography (journal[push]) is not installed")
class KeysTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), platform="none")

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_made_once_kept_private_and_stable(self):
        first = push.keys(self.cfg)
        self.assertEqual(webpush.public_key(first["private"]), first["public"])
        self.assertEqual(push.keys(self.cfg), first)
        mode = stat.S_IMODE(os.stat(self.cfg.state_dir / push.STATE).st_mode)
        self.assertEqual(mode, 0o600)

    def test_not_made_when_asked_not_to(self):
        self.assertIsNone(push.keys(self.cfg, create=False))
        self.assertFalse((self.cfg.state_dir / push.STATE).exists())

    def test_a_real_pass_encrypts_for_the_subscription(self):
        """End to end with the real ``webpush.send`` patched at the HTTP layer: the body is aes128gcm, signed."""
        push.keys(self.cfg)
        push.subscribe(self.cfg, PHONE, MORNING)
        seen = []

        class Response:
            status = 201

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def opened(request, timeout=10):
            seen.append(request)
            return Response()
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=lambda req, timeout=10: opened(req)):
            pushed = push.run_once(self.cfg, (MORNING + timedelta(minutes=1)).astimezone(timezone.utc))
        self.assertEqual(pushed, ["2026-10-15-morning"])
        self.assertEqual(seen[0].full_url, PHONE["endpoint"])
        self.assertEqual(seen[0].headers["Content-encoding"], "aes128gcm")
        self.assertTrue(seen[0].headers["Authorization"].startswith("vapid t="))


class WithoutTheExtraTest(CleanEnv):
    def test_keys_are_none_and_said(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = make_cfg(Path(tmp.name), platform="none")
        with mock.patch.object(webpush, "available", return_value=False):
            self.assertIsNone(push.keys(cfg))
            info = push.info(cfg)
        self.assertFalse(info["available"])
        self.assertIn("journal[push]", info["detail"])
        self.assertIsNone(info["public_key"])


class ThreadTest(PushCase):
    def test_a_first_pass_at_start_and_it_stops_with_the_server(self):
        stopping = threading.Event()
        send = Recorder()
        passed = threading.Event()

        class App:
            pass
        app = App()
        app.stopping = stopping
        app.config = lambda: self.cfg
        app.clock = lambda: (MORNING + timedelta(minutes=1)).astimezone(timezone.utc)
        app.log = lambda line: None
        thread = push.start(app, interval=0.05, send=send, after_pass=passed.set)
        self.assertTrue(passed.wait(5))
        stopping.set()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual([e for e, _p in send.calls], [PHONE["endpoint"]])   # once, however many passes ran
        self.assertFalse((self.cfg.state_dir / "tick.lock").exists())

    def test_a_pass_that_fails_is_logged_and_the_thread_goes_on(self):
        stopping = threading.Event()
        lines, passes = [], []

        class App:
            pass
        app = App()
        app.stopping = stopping
        app.config = lambda: self.cfg
        app.clock = lambda: datetime.now(timezone.utc)
        app.log = lines.append

        def after():
            passes.append(1)
            if len(passes) >= 2:
                stopping.set()
        with mock.patch.object(push, "run_once", side_effect=RuntimeError("disk")):
            thread = push.start(app, interval=0.01, after_pass=after)
            thread.join(5)
        self.assertGreaterEqual(len(passes), 2)
        self.assertTrue(any("push" in line and "RuntimeError" in line for line in lines), lines)


class ServeStartsItTest(CleanEnv):
    def test_journal_serve_starts_the_push_thread(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from journal import cli
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = make_cfg(Path(tmp.name), platform="none")
        args = cli.build_parser().parse_args(["serve", "--port", "8799"])
        with mock.patch("journal.serve.app.run", return_value=0) as run, \
                mock.patch("journal.serve.push.start") as start, redirect_stdout(io.StringIO()), \
                redirect_stderr(io.StringIO()):
            self.assertEqual(cli.run(args, cfg_loader=lambda: cfg), 0)
        start.assert_called_once()
        self.assertIs(start.call_args.args[0], run.call_args.args[0])   # the server's own App


class DoctorTest(CleanEnv):
    def rows(self, cfg):
        from journal import status
        return {n: (s, d) for n, s, d in status.doctor(cfg, token=False, web=True)}

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), platform="none")

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_without_the_extra(self):
        with mock.patch.object(webpush, "available", return_value=False):
            state, detail = self.rows(self.cfg)["serve push"]
        self.assertEqual(state, "ok")
        self.assertIn("journal[push]", detail)

    def test_with_keys_and_subscriptions(self):
        self.cfg.state_dir.mkdir(parents=True, exist_ok=True)
        (self.cfg.state_dir / push.STATE).write_text(json.dumps(
            {"vapid": {"private": "k", "public": "p"}, "subscriptions": [PHONE, TABLET]}), "utf-8")
        with mock.patch.object(webpush, "available", return_value=True):
            state, detail = self.rows(self.cfg)["serve push"]
        self.assertEqual(state, "ok")
        self.assertIn("2 devices", detail)
        self.assertIn("no-chat", detail)

    def test_no_keys_yet(self):
        with mock.patch.object(webpush, "available", return_value=True):
            state, detail = self.rows(self.cfg)["serve push"]
        self.assertEqual(state, "ok")
        self.assertIn("first start", detail)
        self.assertFalse((self.cfg.state_dir / push.STATE).exists())   # doctor makes nothing

    def test_a_chat_with_no_chat_pushes_nothing_and_says_so(self):
        chat = make_cfg(Path(self.tmp.name))
        (chat.state_dir).mkdir(parents=True, exist_ok=True)
        (chat.state_dir / push.STATE).write_text(json.dumps(
            {"vapid": {"private": "k", "public": "p"}, "subscriptions": [PHONE]}), "utf-8")
        with mock.patch.object(webpush, "available", return_value=True):
            state, detail = self.rows(chat)["serve push"]
        self.assertEqual(state, "warn")
        self.assertIn("push.when", detail)


class RoutesTest(ServeCase):
    def test_the_state_of_push_for_settings(self):
        got = self.get_json("/api/v1/push")
        self.assertEqual(set(got), {"available", "detail", "public_key", "when", "active", "subscriptions"})
        self.assertEqual((got["when"], got["active"], got["subscriptions"]), ("no-chat", False, 0))
        self.assertEqual(got["available"], HAS_CRYPTO)
        if HAS_CRYPTO:
            self.assertEqual(len(webpush.unb64u(got["public_key"])), 65)

    def test_subscribe_and_unsubscribe(self):
        code, _h, body = self.request("POST", "/api/v1/push/subscriptions", PHONE, headers={"Origin": self.origin})
        self.assertEqual(code, 201, body)
        code, _h, _b = self.request("POST", "/api/v1/push/subscriptions", PHONE, headers={"Origin": self.origin})
        self.assertEqual(code, 200)
        self.assertEqual(self.get_json("/api/v1/push")["subscriptions"], 1)
        code, _h, _b = self.request("DELETE", "/api/v1/push/subscriptions", {"endpoint": PHONE["endpoint"]},
                                    headers={"Origin": self.origin})
        self.assertEqual(code, 204)
        self.assertEqual(self.get_json("/api/v1/push")["subscriptions"], 0)
        code, _h, _b = self.request("DELETE", "/api/v1/push/subscriptions", {"endpoint": PHONE["endpoint"]},
                                    headers={"Origin": self.origin})
        self.assertEqual(code, 204)   # gone already: the same answer, nothing to undo

    def test_a_bad_subscription_is_422(self):
        code, _h, body = self.request("POST", "/api/v1/push/subscriptions",
                                      {"endpoint": "https://push.example.org/x", "keys": PHONE["keys"]},
                                      headers={"Origin": self.origin})
        self.assertEqual(code, 422, body)
        self.assertEqual(json.loads(body)["error"], "bad-subscription")

    def test_they_need_the_token_and_the_sites_origin(self):
        self.assertEqual(self.request("GET", "/api/v1/push", token=False)[0], 401)
        self.assertEqual(self.request("POST", "/api/v1/push/subscriptions", PHONE)[0], 403)   # no Origin
        self.assertEqual(self.request("POST", "/api/v1/push/subscriptions", PHONE, token=False,
                                      headers={"Origin": self.origin})[0], 401)

    def test_a_read_only_server_still_takes_a_subscription(self):
        """A subscription changes no entry, ledger or definition: ``serve.allow_write: false`` keeps nudges."""
        self.cfg.serve["allow_write"] = False
        code, _h, body = self.request("POST", "/api/v1/push/subscriptions", PHONE, headers={"Origin": self.origin})
        self.assertEqual(code, 201, body)


if __name__ == "__main__":
    unittest.main()
