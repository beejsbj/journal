"""CHANNELS: the journal's chat on any Hermes platform, with the same guarantees on each.

A neutral second platform (``discord``, chat ``D_CHAT``, owner ``D_USER``) beside Telegram: the config resolves the
channel (Telegram's own config unchanged), only the channel's messages, turns and transcript rows are the journal's,
``/x`` and the privacy pass hold there as on Telegram, the sweep and the conversation writer read only its rows, and
what the platform cannot do (edit, reply, keyboard) degrades. The ``Channel`` port's two adapters are tested at
their edges: the Bot API payloads Telegram has always had, and ``hermes journal deliver`` (both halves; Hermes
itself is a fake here)."""
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import PERSON, CHAT, CleanEnv, FakeTranscript, add_prompt, make_cfg, write_config

from journal import (capture, channel, compose, config, conversations, entries, hookstate, inbound, ledger, mirror,
                     privacy, programs, status, sweep, telegram, tick, turncontext, validate)
from journal.config import load
from journal.hermes import channel as hchannel
from journal.hermes import cron, deliver, hooks, runtime
from journal.hermes.transcript import HermesTranscript, live_session
from journal.ports import Caps, ChannelError, Keyboard, NoChannel

D_CHAT = "1180000000000000001"   # a direct-message channel id, as Discord numbers them
D_USER = "990000000000000002"    # his user id there
D_OTHER = "990000000000000003"   # someone else
NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)  # 14:30 IST


def discord(cfg, owners=(D_USER,)):
    cfg.platform, cfg.chat_id, cfg.owner_ids = "discord", D_CHAT, tuple(owners)
    return cfg


class Sent:
    """A ``ports.Channel`` that records what it was asked to do (no network)."""
    blocking = False

    def __init__(self, platform="discord", caps=Caps()):
        self.platform, self.caps = platform, caps
        self.sends, self.edits, self.n = [], [], 500

    def send(self, chat_id, html, *, reply_to=None, silent=False, keyboard=None):
        self.n += 1
        self.sends.append({"chat_id": chat_id, "html": html, "reply_to": reply_to, "silent": silent,
                           "keyboard": keyboard, "id": str(self.n)})
        return str(self.n)

    def edit(self, chat_id, message_id, html):
        if not self.caps.edit:
            raise AssertionError("edited on a channel that cannot edit")
        self.edits.append((chat_id, message_id, html))


# ── configuration ────────────────────────────────────────────────────────────

class ConfigTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.t = Path(self.tmp.name)

    def load(self, data):
        return load(write_config(self.t / "c.json", {"root": str(self.t / "J"), **data}))

    def test_without_a_channel_it_is_telegram_and_telegram_chat_id(self):
        cfg = self.load({"telegram": {"chat_id": CHAT}})
        self.assertEqual((cfg.platform, cfg.chat_id, cfg.owner_ids), ("telegram", CHAT, ()))
        self.assertIsInstance(cfg.channel(), telegram.TelegramChannel)
        self.assertEqual(cfg.warnings, [])

    def test_telegram_with_no_chat_of_its_own_keeps_telegram_chat_id(self):
        cfg = self.load({"telegram": {"chat_id": CHAT}, "channel": {"platform": "telegram", "chat_id": ""}})
        self.assertEqual((cfg.platform, cfg.chat_id), ("telegram", CHAT))

    def test_another_platform(self):
        cfg = self.load({"telegram": {"chat_id": CHAT},
                         "channel": {"platform": "Discord", "chat_id": int(D_CHAT), "user_ids": int(D_USER)}})
        self.assertEqual((cfg.platform, cfg.chat_id, cfg.owner_ids), ("discord", D_CHAT, (D_USER,)))
        self.assertIsInstance(cfg.channel(), hchannel.HermesChannel)
        self.assertEqual(cfg.channel().caps, Caps())

    def test_another_platform_without_a_chat_is_a_problem_not_telegrams_chat(self):
        cfg = self.load({"telegram": {"chat_id": CHAT}, "channel": {"platform": "discord"}})
        self.assertEqual(cfg.chat_id, "")
        self.assertTrue(any(p.startswith("channel.chat_id is not set") for p in config.problems(cfg)))

    def test_no_owner_ids_off_telegram_is_named(self):
        cfg = self.load({"channel": {"platform": "discord", "chat_id": D_CHAT}})
        self.assertTrue(any("channel.user_ids is empty" in p for p in config.problems(cfg)))
        tg = self.load({"telegram": {"chat_id": CHAT}})
        self.assertFalse(any("user_ids" in p for p in config.problems(tg)))

    def test_an_unreadable_channel_falls_back_to_telegram_and_says_so(self):
        for bad in ("discord", {"platform": "dis cord!"}, {"platform": 5}):
            with self.subTest(bad=bad):
                cfg = self.load({"telegram": {"chat_id": CHAT}, "channel": bad})
                self.assertEqual((cfg.platform, cfg.chat_id), ("telegram", CHAT))
                self.assertTrue(any(w.startswith("channel") for w in cfg.warnings))

    def test_adapter_none_off_telegram_sends_nothing(self):
        cfg = self.load({"adapter": "none", "channel": {"platform": "discord", "chat_id": D_CHAT}})
        chat = cfg.channel()
        self.assertIsInstance(chat, NoChannel)
        with self.assertRaises(ChannelError):
            chat.send(D_CHAT, "hello")

    def test_cron_failures_go_to_the_channel(self):
        self.assertEqual(cron.failure_target(self.load({"telegram": {"chat_id": CHAT}})), f"telegram:{CHAT}")
        cfg = self.load({"channel": {"platform": "discord", "chat_id": D_CHAT}})
        self.assertEqual(cron.failure_target(cfg), f"discord:{D_CHAT}")

    def test_doctor_says_where_and_how(self):
        tg = self.load({"telegram": {"chat_id": CHAT}})
        row = status._channel_row(tg)
        self.assertEqual(row[:2], ("channel", "ok"))
        self.assertIn("Telegram Bot API", row[2])
        dc = self.load({"channel": {"platform": "discord", "chat_id": D_CHAT, "user_ids": [D_USER]}})
        row = status._channel_row(dc)
        self.assertIn(f"discord:{D_CHAT}", row[2])
        self.assertIn("hermes journal deliver", row[2])
        self.assertIn("no fold", row[2])


# ── whose message, which chat, which record ──────────────────────────────────

class IdentityTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = discord(make_cfg(Path(self.tmp.name)))

    def test_ours_is_platform_chat_and_owner(self):
        c = self.cfg
        self.assertTrue(channel.ours(c, "discord", D_CHAT, D_USER))
        self.assertTrue(channel.ours(c, SimpleNamespace(value="Discord"), D_CHAT, D_USER))
        self.assertFalse(channel.ours(c, "discord", D_CHAT, D_OTHER))   # someone else in the chat
        self.assertFalse(channel.ours(c, "discord", D_CHAT, None))      # a sender nobody named
        self.assertFalse(channel.ours(c, "discord", "42", D_USER))      # another chat
        self.assertFalse(channel.ours(c, "telegram", D_CHAT, D_USER))   # another platform
        self.assertFalse(channel.ours(c, "", D_CHAT, D_USER))           # an unknown platform: never (fails closed)
        c.owner_ids = ()
        self.assertTrue(channel.ours(c, "discord", D_CHAT, D_OTHER))    # no owners: the chat is his

    def test_a_turn_is_ours_in_the_chat_and_the_platform_alone_when_the_chat_is_unknown(self):
        c = self.cfg
        self.assertTrue(channel.turn_ours(c, "discord"))
        self.assertTrue(channel.turn_ours(c, "discord", D_CHAT, D_USER))
        self.assertFalse(channel.turn_ours(c, "discord", "42"))
        self.assertFalse(channel.turn_ours(c, "discord", D_CHAT, D_OTHER))
        self.assertFalse(channel.turn_ours(c, "telegram", D_CHAT))
        self.assertFalse(channel.turn_ours(c, "cli"))

    def test_records(self):
        old = {"chat_id": CHAT}  # made before CHANNELS: Telegram's
        self.assertEqual(channel.platform_of(old), "telegram")
        self.assertFalse(channel.same_chat(self.cfg, old))
        new = channel.stamp(self.cfg, {"chat_id": D_CHAT})
        self.assertEqual(new["platform"], "discord")
        self.assertTrue(channel.same_chat(self.cfg, new))
        tg = make_cfg(Path(self.tmp.name) / "tg")
        self.assertNotIn("platform", channel.stamp(tg, {"chat_id": CHAT}))  # Telegram's records stay as they were
        self.assertTrue(channel.same_chat(tg, old))
        # the same chat id on two platforms is two chats
        self.assertFalse(channel.same_chats({"chat_id": "7"}, {"chat_id": "7", "platform": "discord"}))

    def test_a_reply_by_id_is_looked_up_on_its_platform(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"] += [{"id": "a", "chat_id": "7", "message_id": "70"},
                                {"id": "b", "chat_id": "7", "message_id": "70", "platform": "discord"}]
        data = ledger.snapshot(self.cfg.ledger_path)
        self.assertEqual(ledger.by_message_id(data, "7", "70", "discord")["id"], "b")
        self.assertEqual(ledger.by_message_id(data, "7", "70", "telegram")["id"], "a")

    def test_labels_and_citations(self):
        self.assertEqual((channel.label(self.cfg), channel.cite(self.cfg)), ("Discord", "chat"))
        self.cfg.platform = "google_chat"
        self.assertEqual(channel.label(self.cfg), "Google Chat")
        self.cfg.platform = "some_new_one"
        self.assertEqual(channel.label(self.cfg), "Some New One")
        tg = make_cfg(Path(self.tmp.name) / "tg")
        self.assertEqual((channel.label(tg), channel.cite(tg)), ("Telegram", "telegram"))


# ── a message and a turn on another platform: the same rules ─────────────────

def dmsg(text="", **kw) -> inbound.Inbound:
    kw.setdefault("platform", "discord")
    kw.setdefault("chat_id", D_CHAT)
    kw.setdefault("user_id", D_USER)
    kw.setdefault("message_id", "1300000000000000900")
    kw.setdefault("at", NOW)
    return inbound.Inbound(text=text, **kw)


class InboundTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = discord(make_cfg(Path(self.tmp.name)))
        self.prompt = add_prompt(self.cfg, kind="evening", local_date="2026-10-14", sent=NOW - timedelta(minutes=20),
                                 message_id="1300000000000000700")
        self.spawned = []

    def recv(self, text="", **kw):
        return inbound.receive(self.cfg, dmsg(text, **kw), spawn=lambda cfg, *a: self.spawned.append(a))

    def files(self):
        return sorted(p.name for p in self.cfg.root.glob("*.md"))

    def body(self):
        return "".join(p.read_text(encoding="utf-8") for p in sorted(self.cfg.root.glob("*.md")))

    def test_the_prompt_names_its_platform(self):
        self.assertEqual(self.prompt["platform"], "discord")

    def test_a_direct_reply_is_saved_verbatim(self):
        out = self.recv("slept badly", reply_to="1300000000000000700", at=NOW + timedelta(days=2))
        self.assertEqual(out.action, "rewrite")
        self.assertEqual(out.captured["rule"], "direct-reply")
        self.assertIn("slept badly", entries.his_words(self.body()))

    def test_the_next_message_answers_the_open_prompt(self):
        out = self.recv("a long walk")
        self.assertEqual(out.captured["rule"], "next-message")
        self.assertIn("a long walk", self.body())

    def test_j_is_a_note(self):
        out = self.recv("/j the lake was cold")
        self.assertEqual(out.action, "rewrite")
        self.assertIn("the lake was cold", self.body())
        # the note's record names its platform too, so its annotation is aligned with her reply in this chat
        note = ledger.find(ledger.snapshot(self.cfg.ledger_path), out.captured["prompt"]["id"])
        self.assertEqual(note["platform"], "discord")
        self.assertTrue(channel.same_chat(self.cfg, note))

    def test_x_with_text_is_marked_and_never_saved(self):
        out = self.recv("/x my secret")
        self.assertEqual(out.action, "rewrite")
        self.assertIn("my secret", out.text)
        self.assertTrue(capture.OFF_RECORD.search(out.text))
        self.assertNotIn("my secret", self.body())

    def test_a_bare_x_holds_the_rest_of_the_thread(self):
        self.assertEqual(self.recv("/x", message_id="1300000000000000901").action, "pass")  # the command answers it
        out = self.recv("what I told nobody", message_id="1300000000000000902", at=NOW + timedelta(minutes=1))
        self.assertTrue(capture.OFF_RECORD.search(out.text))
        self.assertNotIn("what I told nobody", self.body())
        self.assertEqual(self.files(), [])

    def test_someone_else_in_the_chat_is_never_captured_or_marked(self):
        self.assertEqual(self.recv("their words", user_id=D_OTHER), inbound.PASS)
        self.assertEqual(self.recv("/j their note", user_id=D_OTHER), inbound.PASS)
        self.assertEqual(self.recv("/x theirs", user_id=D_OTHER), inbound.PASS)
        self.assertEqual(self.files(), [])

    def test_telegram_and_other_chats_are_not_the_journals_now(self):
        self.assertEqual(self.recv("slept badly", platform="telegram", chat_id=CHAT, user_id=CHAT), inbound.PASS)
        self.assertEqual(self.recv("slept badly", chat_id="42"), inbound.PASS)
        self.assertEqual(self.recv("slept badly", platform=""), inbound.PASS)
        self.assertEqual(self.files(), [])

    def test_a_prompt_made_on_telegram_takes_nothing_typed_here(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            p = ledger.find(data, self.prompt["id"])
            p.pop("platform")  # as if sent on Telegram, before the channel moved
            p["chat_id"] = D_CHAT  # (even a coinciding id)
        self.assertEqual(self.recv("a long walk"), inbound.PASS)
        self.assertEqual(self.recv("again", reply_to="1300000000000000700"), inbound.PASS)

    def test_the_turn_carries_the_pack_only_in_the_chat(self):
        from journal import context
        context.save(self.cfg, self.prompt["id"], "# Context pack\n\n## Weather\nRain, PACKFACT\n")
        out = self.recv("slept badly", reply_to="1300000000000000700")
        other = turncontext.Turn("discord", "s", "t", out.text, NOW, chat_id="42")
        self.assertNotIn("PACKFACT", turncontext.build(self.cfg, other) or "")
        turn = turncontext.Turn("discord", "s", "t", out.text, NOW, chat_id=D_CHAT, user_id=D_USER)
        self.assertIn("PACKFACT", turncontext.build(self.cfg, turn))

    def test_an_off_record_turn_is_marked_here_and_not_in_another_chat(self):
        out = self.recv("/x a secret")
        inbound.begin_turn(self.cfg, turncontext.Turn("discord", "s9", "t1", out.text, NOW, chat_id="42"))
        self.assertIsNone(hookstate.off_turn(self.cfg, "s9"))
        inbound.begin_turn(self.cfg, turncontext.Turn("discord", "s9", "t1", out.text, NOW, chat_id=D_CHAT))
        self.assertIsNotNone(hookstate.off_turn(self.cfg, "s9"))
        from journal import tools
        self.assertIsNotNone(tools.refusal(self.cfg, "journal_add_note", {"text": "x"}, tools.ToolTurn("s9", "t1")))


class HookTest(CleanEnv):
    """The Hermes hook on a Discord event: the event's platform, chat and sender reach ``receive``."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = discord(make_cfg(Path(self.tmp.name)))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-14", sent=datetime.now(timezone.utc)
                   - timedelta(minutes=20), message_id="1300000000000000700")
        patch = mock.patch.object(runtime, "load_config", lambda: self.cfg)
        patch.start()
        self.addCleanup(patch.stop)

    def event(self, text, platform="discord", chat=D_CHAT, user=D_USER):
        return SimpleNamespace(text=text, message_type=SimpleNamespace(name="TEXT"), message_id="1300000000000000950",
                               reply_to_message_id="1300000000000000700", media_urls=[], media_types=[],
                               raw_message=None, source=SimpleNamespace(platform=SimpleNamespace(value=platform),
                                                                        chat_id=chat, user_id=user))

    def test_his_reply_on_discord_is_captured(self):
        out = hooks.on_dispatch(event=self.event("slept badly"))
        self.assertEqual(out["action"], "rewrite")
        self.assertIn("slept badly", (self.cfg.root / "2026-10-14-evening.md").read_text(encoding="utf-8"))

    def test_telegram_and_another_person_pass_through(self):
        self.assertIsNone(hooks.on_dispatch(event=self.event("slept badly", platform="telegram", chat=CHAT)))
        self.assertIsNone(hooks.on_dispatch(event=self.event("slept badly", user=D_OTHER)))
        self.assertEqual(list(self.cfg.root.glob("*.md")), [])

    def test_the_turn_reads_its_chat_from_the_session(self):
        seen = []
        with mock.patch.object(runtime, "session_value", lambda name: {"CHAT_ID": "42", "USER_ID": D_USER}[name]), \
                mock.patch.object(turncontext, "build", lambda cfg, turn: seen.append(turn) or None):
            hooks.on_llm_call(user_message="hi", platform="discord", session_id="s", turn_id="t")
        self.assertEqual((seen[0].chat_id, seen[0].user_id), ("42", D_USER))

    def test_outside_the_gateway_there_is_no_session_value(self):
        self.assertIsNone(runtime.session_value("CHAT_ID"))  # (no Hermes here: the platform alone decides)

    def test_a_broken_configuration_is_logged_once_not_per_message(self):
        broken = mock.patch.object(runtime, "load_config", side_effect=config.ConfigError("`root` is not set"))
        with broken, self.assertLogs("journal.hermes", level="ERROR") as logs:
            for _ in range(3):
                self.assertIsNone(hooks.on_dispatch(event=self.event("hello", platform="slack", chat="C1")))
        self.assertEqual(len(logs.records), 1)


# ── the transcript and its readers: one platform's chat, never another's ─────

T0 = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc)  # 19:30 IST
GROUP = "1180000000000000077"  # a server channel where two people talk to her, each in a session of their own


def m(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


class TwoPlatforms(CleanEnv):
    """A sessions database holding his Telegram chat, his Discord DM and a Discord server channel he shares."""

    def setUp(self):
        super().setUp()
        from test_sweep import FakeSessions
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        t = Path(self.tmp.name)
        self.db = FakeSessions(t / "state.db")
        for sid, chat, user in (("dc", D_CHAT, D_USER), ("g-his", GROUP, D_USER), ("g-other", GROUP, D_OTHER)):
            self.db.sql("insert into sessions (id, source, chat_id, session_key, user_id, started_at) "
                        "values (?, 'discord', ?, ?, ?, 5.0)", sid, chat, f"agent:main:discord:x:{chat}:{user}", user)
        self.tg = make_cfg(t / "tg")
        self.dc = discord(make_cfg(t / "dc"))
        for cfg in (self.tg, self.dc):
            cfg.hermes["state_db"] = str(self.db.path)
        privacy._CACHE.clear()

    def his(self, text, at, mid, session):
        self.db.add(text, at, mid, session=session)

    def hers(self, text, at, session):
        self.db.add(text, at, role="assistant", session=session)

    def texts(self, cfg):
        return [r.text for r in cfg.transcript().rows() if r.text]

    def written(self, cfg):
        return "".join(p.read_text(encoding="utf-8") for p in sorted(cfg.root.glob("*.md")))


class TranscriptTest(TwoPlatforms):
    def setUp(self):
        super().setUp()
        self.his("on telegram", m(0), "10", "tg")
        self.hers("telegram reply", m(1), "tg")
        self.his("on discord", m(2), "1300000000000000010", "dc")
        self.hers("discord reply", m(3), "dc")
        self.his("his word in the server", m(4), "1300000000000000011", "g-his")
        self.his("someone else in the server", m(5), "1300000000000000012", "g-other")

    def test_each_channel_reads_its_own_chat_only(self):
        for name, make in (("hermes", lambda cfg: HermesTranscript(cfg, self.db.path)),
                           ("fake", lambda cfg: FakeTranscript.over_sessions(cfg, self.db.path))):
            with self.subTest(name):
                self.assertEqual([r.text for r in make(self.tg).rows()], ["on telegram", "telegram reply"])
                self.assertEqual([r.text for r in make(self.dc).rows()], ["on discord", "discord reply"])

    def test_in_a_shared_chat_only_the_owners_sessions_are_read(self):
        self.dc.chat_id = GROUP
        self.assertEqual(self.texts(self.dc), ["his word in the server"])
        self.dc.owner_ids = ()  # (no owners: everyone's sessions of the chat; doctor warns about this)
        self.assertEqual(self.texts(self.dc), ["his word in the server", "someone else in the server"])

    def test_the_cursor_helpers_and_the_live_session_are_the_channels(self):
        chat = HermesTranscript(self.dc, self.db.path)
        rows = chat.rows()
        self.assertEqual(chat.first_id(m(0)), rows[0].id)
        self.assertEqual(chat.last_activity(m(10)), m(3))
        self.assertEqual(live_session(D_CHAT, self.db.path, platform="discord"), "dc")
        self.assertEqual(live_session(GROUP, self.db.path, platform="discord", users=(D_USER,)), "g-his")
        self.assertEqual(live_session(CHAT, self.db.path), "tg")

    def test_the_mirror_goes_into_the_channels_session(self):
        home = Path(self.tmp.name) / "home"
        home.mkdir()
        (home / "state.db").write_bytes(self.db.path.read_bytes())
        self.dc.hermes_home, self.dc.hermes["state_db"] = home, None
        seen = []
        with mock.patch.dict("os.environ", {"HERMES_HOME": str(home)}), \
                mock.patch.object(mirror, "_mirror_to_session", lambda *a: seen.append(a) or True):
            self.assertTrue(self.dc.transcript().mirror("[Journal: hello]"))
        self.assertEqual(seen[0][:2], ("discord", D_CHAT))
        self.assertEqual(seen[0][-1], "dc")


class PrivacyTest(TwoPlatforms):
    """``/x`` on Discord holds exactly as on Telegram: one message, a bare-/x window, her reply in a tainted turn;
    and nothing said off the record in one platform's chat is read through the other's."""

    def test_a_bare_x_window_on_discord(self):
        self.his("before it", m(0), "1300000000000000020", "dc")
        self.his("/x", m(1), "1300000000000000021", "dc")
        self.his("the private thing", m(2), "1300000000000000022", "dc")
        self.hers("her answer to it", m(3), "dc")
        view = privacy.view(self.dc, m(10))
        private = {r.text for r in self.dc.transcript().rows() if r.id in view.private}
        self.assertEqual(private, {"/x", "the private thing", "her answer to it"})
        self.assertTrue(privacy.window_open(self.dc, m(10)))

    def test_one_x_message_on_discord_taints_her_turn(self):
        self.his("/x my secret", m(0), "1300000000000000030", "dc")
        self.hers("I'll keep it", m(1), "dc")
        self.his("now something public", m(5), "1300000000000000031", "dc")
        self.hers("public reply", m(6), "dc")
        view = privacy.view(self.dc, m(10))
        private = {r.text for r in self.dc.transcript().rows() if r.id in view.private}
        self.assertEqual(private, {"/x my secret", "I'll keep it"})

    def test_a_window_on_one_platform_never_reaches_rows_of_the_other(self):
        self.his("/x", m(0), "40", "tg")
        self.his("telegram private", m(1), "41", "tg")
        self.his("discord words", m(2), "1300000000000000042", "dc")
        tg = privacy.view(self.tg, m(10))
        dc = privacy.view(self.dc, m(10))
        self.assertTrue(tg.open_at(m(10)))
        self.assertFalse(dc.open_at(m(10)))  # Discord's chat has no window of its own
        self.assertFalse(dc.private)
        self.assertEqual({r.text for r in self.tg.transcript().rows() if r.id in tg.private}, {"/x", "telegram private"})

    def test_a_pass_that_cannot_run_closes_discord_too(self):
        with mock.patch("sqlite3.connect", side_effect=sqlite3.OperationalError("locked")):
            privacy._CACHE.clear()
            self.assertTrue(privacy.view(self.dc, m(10)).closed)


class ReadersTest(TwoPlatforms):
    """The sweep and the conversation writer read only the channel's chat (Iris's database holds Discord sessions
    beside her Telegram chat: under the Telegram channel none of their rows is ever read)."""

    def setUp(self):
        super().setUp()
        for cfg in (self.tg, self.dc):
            conversations.run(cfg, T0 - timedelta(hours=1), [])  # first run: the cursor starts here

    def test_the_sweep_captures_a_busy_path_reply_on_discord_only_into_discords_prompt(self):
        add_prompt(self.dc, kind="morning", local_date="2026-10-15", sent=m(-10), message_id="1300000000000000700")
        self.his("answered on discord", m(1), "1300000000000000050", "dc")
        self.his("said on telegram", m(2), "51", "tg")
        n = sweep.run(self.dc, m(30), [])
        self.assertEqual(n, 1)
        body = self.written(self.dc)
        self.assertIn("answered on discord", body)
        self.assertNotIn("said on telegram", body)

    def test_under_telegram_no_discord_row_is_ever_swept_or_written(self):
        add_prompt(self.tg, kind="morning", local_date="2026-10-15", sent=m(-10), message_id="700")
        self.his("said on discord", m(1), "1300000000000000060", "dc")
        self.hers("discord reply", m(2), "dc")
        self.assertEqual(sweep.run(self.tg, m(30), []), 0)
        conversations.run(self.tg, m(80), [])
        self.assertNotIn("discord", self.written(self.tg))

    def test_the_conversation_on_discord_is_written_and_telegrams_is_not(self):
        self.his("thinking about the bank call", m(0), "1300000000000000070", "dc")
        self.hers("What time is it booked for?", m(1), "dc")
        self.his("a telegram aside", m(2), "71", "tg")
        log = []
        conversations.run(self.dc, m(80), log)
        body = self.written(self.dc)
        self.assertIn("thinking about the bank call", body)
        self.assertIn("What time is it booked for?", body)
        self.assertNotIn("a telegram aside", body)

    def test_off_the_record_on_discord_is_never_written(self):
        self.his("/x", m(0), "1300000000000000080", "dc")
        self.his("keep this between us", m(1), "1300000000000000081", "dc")
        self.hers("of course", m(2), "dc")
        conversations.run(self.dc, m(80), [])
        sweep.run(self.dc, m(80), [])
        body = self.written(self.dc)
        self.assertNotIn("keep this between us", body)
        self.assertNotIn("of course", body)


# ── the Channel port: Telegram as it always was; another platform degrades ───

class TelegramChannelTest(CleanEnv):
    """The Bot API payloads are the ones the engine always sent (the keyboard's dict, reply parameters, silence)."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))
        self.calls = []

        def bot(cfg, method, payload):
            self.calls.append((method, payload))
            return {"message_id": 77}
        for p in (mock.patch.object(telegram, "_call", bot), mock.patch.object(telegram, "token", lambda cfg: "T")):
            p.start()
            self.addCleanup(p.stop)

    def test_the_keyboard_and_its_removal(self):
        chat = self.cfg.channel()
        self.assertEqual(chat.caps, Caps(edit=True, reply=True, keyboard=True, html=True))
        self.assertEqual(chat.send(CHAT, "<b>phase</b>", keyboard=compose.KEYBOARD), "77")
        self.assertEqual(self.calls[-1][1]["reply_markup"], {
            "keyboard": [[{"text": "/next"}, {"text": "/skip"}], [{"text": "/back"}, {"text": "/break"}]],
            "one_time_keyboard": True, "resize_keyboard": True, "is_persistent": False,
            "input_field_placeholder": "Answer in your own words"})
        chat.send(CHAT, "summary", keyboard=compose.REMOVE_KEYBOARD)
        self.assertEqual(self.calls[-1][1]["reply_markup"], {"remove_keyboard": True})
        chat.send(CHAT, "plain")
        self.assertNotIn("reply_markup", self.calls[-1][1])

    def test_reply_silence_and_edit(self):
        chat = self.cfg.channel()
        chat.send(CHAT, "notes", reply_to="4242", silent=True)
        payload = self.calls[-1][1]
        self.assertEqual(payload["reply_parameters"], {"message_id": 4242, "allow_sending_without_reply": True})
        self.assertTrue(payload["disable_notification"])
        chat.edit(CHAT, "77", "<b>folded</b>")
        self.assertEqual(self.calls[-1][0], "editMessageText")

    def test_a_telegram_error_is_a_channel_error(self):
        self.assertTrue(issubclass(telegram.TelegramError, ChannelError))


class HermesChannelTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = discord(make_cfg(Path(self.tmp.name)))
        self.cfg.profile = "iris"
        self.runs = []

    def run_with(self, stdout="", returncode=0, stderr="", raise_=None):
        def fake(argv, **kw):
            self.runs.append((argv, kw))
            if raise_ is not None:
                raise raise_
            return subprocess.CompletedProcess(argv, returncode, stdout, stderr)
        return mock.patch.object(hchannel.subprocess, "run", fake)

    def test_a_send_runs_the_deliver_command_with_plain_text_and_reads_the_id(self):
        chat = self.cfg.channel()
        with self.run_with(stdout='Hermes notice\n{"ok": true, "message_id": "1300000000000000555"}\n'):
            mid = chat.send(D_CHAT, "<b>Evening</b> &amp; you", reply_to="9", keyboard=compose.KEYBOARD, silent=True)
        self.assertEqual(mid, "1300000000000000555")
        argv, kw = self.runs[0]
        self.assertEqual(argv[1:], ["-p", "iris", "journal", "deliver"])
        self.assertEqual(json.loads(kw["input"]), {"platform": "discord", "chat_id": D_CHAT, "text": "Evening & you"})
        self.assertNotIn("HERMES_HOME", kw["env"])  # (-p names the profile)

    def test_failures_are_channel_errors_with_the_reason(self):
        chat = self.cfg.channel()
        cases = [({"stdout": '{"ok": false, "error": "Discord is not configured"}', "returncode": 1},
                  "Discord is not configured"),
                 ({"stdout": "", "stderr": "Traceback\nImportError: x", "returncode": 1}, "(exit 1: ImportError)"),
                 ({"stdout": "", "stderr": "HTTPError: https://api.example/bot123:secret/x", "returncode": 1},
                  "(exit 1: HTTPError)"),
                 ({"raise_": subprocess.TimeoutExpired(["hermes"], 90)}, "timed out"),
                 ({"raise_": FileNotFoundError("hermes")}, "could not run")]
        for kw, why in cases:
            with self.subTest(why), self.run_with(**kw), self.assertRaises(ChannelError) as e:
                chat.send(D_CHAT, "hello")
            self.assertIn(why, str(e.exception))
            self.assertNotIn("secret", str(e.exception))  # (a traceback's text never reaches the ledger or the log)
        with self.assertRaises(ChannelError):
            chat.edit(D_CHAT, "1", "x")


class DegradeTest(CleanEnv):
    """What a platform cannot do is left out, never failed: no edit closes a prompt without folding it, no reply
    sends the annotations on their own, and a prompt made on Telegram is never edited through Discord."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = discord(make_cfg(Path(self.tmp.name)))
        self.chat = Sent()
        patch = mock.patch.object(type(self.cfg), "channel", lambda cfg: self.chat)
        patch.start()
        self.addCleanup(patch.stop)
        mirror_off = mock.patch.object(mirror, "to_chat", lambda *a, **k: False)
        mirror_off.start()
        self.addCleanup(mirror_off.stop)

    def record(self, pid="2026-10-14-evening"):
        return {"id": pid, "kind": "evening", "local_date": "2026-10-14", "scheduled": NOW.isoformat(),
                "chat_id": self.cfg.chat_id, "html": "<b>Evening</b>", "plain_text": "Evening"}

    def test_a_prompt_is_sent_and_recorded_with_its_platform(self):
        log = []
        self.assertTrue(tick.send_prompt(self.cfg, self.record(), log, keyboard=compose.KEYBOARD))
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-evening")
        self.assertEqual((p["platform"], p["message_id"], p["chat_id"]), ("discord", "501", D_CHAT))
        self.assertEqual(self.chat.sends[0]["chat_id"], D_CHAT)

    def test_a_resend_leaves_the_earlier_message_alone(self):
        tick.send_prompt(self.cfg, self.record(), [], refold=lambda old: "folded")
        tick.send_prompt(self.cfg, self.record(), [], refold=lambda old: "folded")
        self.assertEqual(len(self.chat.sends), 2)
        self.assertEqual(self.chat.edits, [])
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-evening")
        self.assertEqual(p["previous_message_ids"], ["501"])

    def test_a_resend_after_moving_back_to_telegram_is_telegrams(self):
        tick.send_prompt(self.cfg, self.record(), [])  # sent on Discord: the record names it
        tg = make_cfg(Path(self.tmp.name))  # the channel key removed: Telegram again, same state
        self.chat = Sent(platform="telegram", caps=Caps(edit=True, reply=True, keyboard=True, html=True))
        with mock.patch.object(type(tg), "channel", lambda cfg: self.chat):
            tick.send_prompt(tg, {**self.record(), "chat_id": tg.chat_id}, [], refold=lambda old: "folded")
        p = ledger.find(ledger.snapshot(tg.ledger_path), "2026-10-14-evening")
        self.assertNotIn("platform", p)
        self.assertTrue(channel.same_chat(tg, p))
        self.assertEqual(self.chat.edits, [])  # (the earlier message was Discord's: not edited through Telegram)

    def test_no_edit_closes_the_prompt_without_folding(self):
        add_prompt(self.cfg, kind="evening", sent=NOW - timedelta(hours=7), message_id="1300000000000000700")
        log = []
        tick.fold(self.cfg, NOW, log)
        p = ledger.find(ledger.snapshot(self.cfg.ledger_path), "2026-10-14-evening")
        self.assertTrue(p["collapsed_at"])
        self.assertEqual(self.chat.edits, [])
        self.assertTrue(log[0].startswith("closed 2026-10-14-evening (missed — reply anytime; discord cannot edit"))
        # closed, it takes no next message (rule 2), as a fold would have; a direct reply still counts
        data = ledger.snapshot(self.cfg.ledger_path)
        self.assertEqual(capture.match(data, self.cfg, D_CHAT, None, NOW)[0], None)
        self.assertEqual(capture.match(data, self.cfg, D_CHAT, "1300000000000000700", NOW)[1], "direct-reply")

    def test_a_telegram_prompt_is_not_edited_through_another_platform(self):
        self.chat = Sent(caps=Caps(edit=True, reply=True))
        p = add_prompt(self.cfg, kind="evening", sent=NOW - timedelta(hours=7), message_id="700")
        with ledger.locked(self.cfg.ledger_path) as data:
            q = ledger.find(data, p["id"])
            q.pop("platform")
            q["chat_id"] = CHAT
        tick.fold(self.cfg, NOW, [])
        self.assertEqual(self.chat.edits, [])
        self.assertTrue(ledger.find(ledger.snapshot(self.cfg.ledger_path), p["id"])["collapsed_at"])

    def test_annotations_go_on_their_own_where_there_are_no_replies(self):
        p = add_prompt(self.cfg, kind="evening", sent=NOW - timedelta(hours=1), message_id="1300000000000000700")
        info = capture.capture(self.cfg, D_CHAT, "1300000000000000700", "slept badly", NOW - timedelta(minutes=50),
                               "text", "1300000000000000801")
        entry = Path(info["path"])
        entry.write_text(entry.read_text(encoding="utf-8").replace(
            self.cfg.names.annotations_heading + "\n", self.cfg.names.annotations_heading + "\n\n- She slept badly.\n"),
            encoding="utf-8")
        with ledger.locked(self.cfg.ledger_path) as data:
            ledger.find(data, p["id"])["annotated_at"] = NOW.isoformat()
        log = []
        tick.reply_annotations(self.cfg, NOW, log)
        self.assertIn(f"replied to {p['id']} with its annotations", log)
        sent = self.chat.sends[-1]
        self.assertEqual(sent["chat_id"], D_CHAT)
        # (the adapter drops a reply target it cannot use: Sent has no reply, as HermesChannel)
        self.assertIn("She slept badly", sent["html"])

    def test_the_phase_keyboard_is_offered_and_hermes_drops_it(self):
        # tick.send_prompt hands the keyboard on; HermesChannel's caps say it cannot show one, and its send ignores it
        tick.send_prompt(self.cfg, self.record(), [], keyboard=compose.KEYBOARD)
        self.assertEqual(self.chat.sends[0]["keyboard"], compose.KEYBOARD)
        self.assertFalse(hchannel.HermesChannel(self.cfg).caps.keyboard)
        self.assertIn("/next", compose.PHASE_HINT)  # the hint line carries the commands instead


class ComposeTest(unittest.TestCase):
    def test_plain_is_the_message_without_markup(self):
        self.assertEqual(compose.plain('<b>Evening</b> &amp; <i>you</i>\n<blockquote expandable>1. a &lt;b&gt;\n'
                                       '2. <a href="https://x.org">site</a></blockquote>'),
                         "Evening & you\n1. a <b>\n2. site (https://x.org)")

    def test_a_prompt_in_plain_text_does_not_offer_to_open_its_questions(self):
        markup = compose.telegram_html("evening", datetime(2026, 10, 14, 23, 0), "Evening.", names=PERSON)
        self.assertIn(compose.HINT, markup)
        text = compose.plain(markup)
        self.assertNotIn("Tap to open", text)
        self.assertTrue(text.endswith(compose.PLAIN_HINT))

    def test_media_tags_reach_the_deliver_command_as_text(self):
        # (Hermes' own send reads MEDIA:<path> as a file to upload; the deliver command never does)
        self.assertEqual(compose.plain("see MEDIA:/etc/passwd"), "see MEDIA:/etc/passwd")


# ── hermes journal deliver: Hermes' senders without its mirror ───────────────

class FakeHermes:
    """The Hermes modules ``deliver`` reaches, in ``sys.modules`` for one test."""

    def __init__(self, result=None, target_error=None, config_error=None, missing=(), denial=None, slack=None):
        self.routed, self.env_loaded, self.mirrored, self.authorized, self.slack = [], [], [], [], []
        smt = types.ModuleType("tools.send_message_tool")
        smt._resolve_tool_target = lambda target: (*target.split(":", 1), None, target_error)
        smt._resolve_platform_config = lambda name, cfg: ("ENUM:" + name, {"token": "secret"}, None, config_error)

        async def route(platform, pconfig, chat_id, message, thread_id=None, media_files=None, **kw):
            self.routed.append((platform, chat_id, message, thread_id, media_files))
            return result if result is not None else {"success": True, "message_id": "1300000000000000999"}
        smt._send_to_platform = route
        smt._mirror_sent_message = lambda *a: self.mirrored.append(a)
        smt._authorize_relay_target = lambda name, chat, thread, native_token=None: (
            self.authorized.append((name, chat, thread, native_token)) or denial)
        smt._slack_dm_chat_id = lambda pconfig, chat: (self.slack.append(chat) or slack or ("D" + chat, None))
        smt._sanitize_error_text = lambda text: str(text).replace("secret", "***")
        for name in missing:
            delattr(smt, name)
        tools_pkg = types.ModuleType("tools")
        tools_pkg.send_message_tool = smt
        send_cmd = types.ModuleType("hermes_cli.send_cmd")
        send_cmd._load_hermes_env = lambda: self.env_loaded.append(True)
        model_tools = types.ModuleType("model_tools")

        def run_async(coro):
            import asyncio
            return asyncio.run(coro)
        model_tools._run_async = run_async
        sanitize = types.ModuleType("agent.message_sanitization")
        sanitize._sanitize_surrogates = lambda text: text.replace("\ud800", "?")
        gw_config = types.ModuleType("gateway.config")
        gw_config.load_gateway_config = lambda: "GATEWAY"
        self.modules = {"tools": tools_pkg, "tools.send_message_tool": smt, "hermes_cli": types.ModuleType("hermes_cli"),
                        "hermes_cli.send_cmd": send_cmd, "model_tools": model_tools,
                        "gateway": types.ModuleType("gateway"), "gateway.config": gw_config,
                        "agent": types.ModuleType("agent"), "agent.message_sanitization": sanitize}

    def __enter__(self):
        self._patch = mock.patch.dict(sys.modules, self.modules)
        self._patch.start()
        return self

    def __exit__(self, *exc):
        self._patch.stop()


class DeliverTest(unittest.TestCase):
    def test_it_sends_through_the_router_without_mirror_or_media(self):
        with FakeHermes() as h:
            mid = deliver.deliver("Discord", D_CHAT, "see MEDIA:/etc/passwd")
        self.assertEqual(mid, "1300000000000000999")
        self.assertEqual(h.env_loaded, [True])
        self.assertEqual(h.routed, [("ENUM:discord", D_CHAT, "see MEDIA:/etc/passwd", None, None)])
        self.assertEqual(h.mirrored, [])
        self.assertEqual(h.authorized, [("discord", D_CHAT, None, None)])  # the relay guard ran, before the send

    def test_the_guards_send_message_puts_around_the_router_are_kept(self):
        with FakeHermes(denial="relay egress not authorized for this target") as h, \
                self.assertRaises(deliver.DeliverError) as e:
            deliver.deliver("discord", D_CHAT, "hello")
        self.assertIn("not authorized", str(e.exception))
        self.assertEqual(h.routed, [])
        with FakeHermes() as h:  # a Slack user target is opened as its DM, and that DM is what the guard checks
            deliver.deliver("slack", "U0123", "hello \ud800")
        self.assertEqual(h.slack, ["U0123"])
        self.assertEqual(h.authorized[0][1], "DU0123")
        self.assertEqual(h.routed[0][1:3], ("DU0123", "hello ?"))  # (lone surrogates scrubbed)
        with FakeHermes(result={"error": "401 for token=secret"}), self.assertRaises(deliver.DeliverError) as e:
            deliver.deliver("discord", D_CHAT, "hello")
        self.assertEqual(str(e.exception), "401 for token=***")  # redacted before it is printed or logged

    def test_what_hermes_refuses_is_a_deliver_error(self):
        for kw, why in (({"target_error": "unknown platform"}, "unknown platform"),
                        ({"config_error": "discord is not enabled"}, "not enabled"),
                        ({"result": {"error": "Forbidden"}}, "Forbidden"),
                        ({"result": {"success": True}}, "gave no id"),
                        ({"missing": ("_send_to_platform",)}, "no send path"),
                        ({"missing": ("_authorize_relay_target",)}, "no send path")):
            with self.subTest(why), FakeHermes(**kw), self.assertRaises(deliver.DeliverError) as e:
                deliver.deliver("discord", D_CHAT, "hello")
            self.assertIn(why, str(e.exception))
        with self.assertRaises(deliver.DeliverError):
            deliver.deliver("discord", "", "hello")

    def test_the_command_speaks_json(self):
        out = io.StringIO()
        with FakeHermes():
            code = deliver.main(io.StringIO(json.dumps({"platform": "discord", "chat_id": D_CHAT, "text": "hi"})), out)
        self.assertEqual((code, json.loads(out.getvalue())), (0, {"ok": True, "message_id": "1300000000000000999"}))
        out = io.StringIO()
        self.assertEqual(deliver.main(io.StringIO("not json"), out), 1)
        self.assertFalse(json.loads(out.getvalue())["ok"])

    def test_the_two_halves_agree(self):
        """``HermesChannel`` reads what ``main`` writes."""
        out = io.StringIO()
        with FakeHermes():
            deliver.main(io.StringIO(json.dumps({"platform": "discord", "chat_id": D_CHAT, "text": "hi"})), out)
        self.assertEqual(hchannel._answer(out.getvalue())["message_id"], "1300000000000000999")

    def test_the_plugin_cli_runs_it_without_the_journal_config(self):
        from journal import hermes as plugin
        with mock.patch.object(deliver, "main", lambda: 0) as _m, \
                mock.patch("journal.cli.hermes_command", side_effect=AssertionError("loaded the config")):
            self.assertEqual(plugin._cli_handle(SimpleNamespace(journal_command="deliver")), 0)


class AnswerOffTheLoopTest(CleanEnv):
    """A program command's answer is sent off the gateway's event loop when the channel's send starts a process."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = discord(make_cfg(Path(self.tmp.name)))

    def test_a_blocking_channel_answers_from_a_thread_and_telegram_inline(self):
        import threading
        seen = []

        class Slow(Sent):
            blocking = True

            def send(self, chat_id, html, **kw):
                seen.append(threading.current_thread().name)
                return super().send(chat_id, html, **kw)
        slow = Slow()
        with mock.patch.object(type(self.cfg), "channel", lambda cfg: slow):
            out = inbound.receive(self.cfg, dmsg("/program"), spawn=lambda *a: None)
            for t in threading.enumerate():
                if t.name == "journal-answer":
                    t.join(5)
        self.assertEqual(out.action, "drop")
        self.assertEqual(seen, ["journal-answer"])
        self.assertTrue(slow.sends)
        self.assertFalse(telegram.TelegramChannel.blocking)
        self.assertTrue(hchannel.HermesChannel.blocking)


class SplitPhaseAfterAMoveTest(unittest.TestCase):
    """The earlier parts of a split phase that a failed attempt already sent count only in the chat they went to: after
    the channel moves, the retry sends the whole phase in the new chat (CodeRabbit, PR #78)."""

    def setUp(self):
        import test_programs
        import test_programs_review
        self.base = test_programs.Base(methodName="setUp")  # (its fixture only: no test of its own runs here)
        self.base.setUp()
        self.addCleanup(self.base.tearDown)
        self.addCleanup(self.base.doCleanups)  # (its patches and temp dir are its own cleanups: run them first)
        test_programs_review._long_program(self.base.cfg)

    def test_the_parts_are_sent_again_in_the_new_chat(self):
        b, n = self.base, {"sends": 0}
        real = b.bot.__call__

        def flaky(cfg, method, payload):
            if method == "sendMessage":
                n["sends"] += 1
                if n["sends"] == 2:  # the phase's last message fails once, after its first part went out on Telegram
                    raise telegram.TelegramError("boom")
            return real(cfg, method, payload)
        with mock.patch.object(telegram, "_call", flaky):
            b.start("longone")
        rid = b.the_run()["id"]
        run = programs.find_run(b.cfg, rid)
        self.assertEqual(programs._phase(run, 1, 1)["chunk_chat"], f"telegram:{CHAT}")
        discord(b.cfg)
        sent = Sent()
        with mock.patch.object(type(b.cfg), "channel", lambda cfg: sent), \
                mock.patch.object(mirror, "to_chat", lambda *a, **k: False):
            programs.step(b.cfg, b.now, b.log)
        self.assertEqual(len(sent.sends), 2)  # the first part again, then the last: the whole phase in the new chat
        self.assertTrue(all(s["chat_id"] == D_CHAT for s in sent.sends))
        p = ledger.find(ledger.snapshot(b.cfg.ledger_path), f"{rid}-s01-p1")
        self.assertEqual((p["platform"], p["previous_message_ids"]), ("discord", [sent.sends[0]["id"]]))

    def test_a_record_of_parts_from_before_channels_is_telegrams(self):
        b = self.base
        with mock.patch.object(telegram, "_call", side_effect=[{"message_id": 801}, telegram.TelegramError("boom")]):
            b.start("longone")
        rid = b.the_run()["id"]
        run = programs.find_run(b.cfg, rid)
        programs._phase(run, 1, 1).pop("chunk_chat")  # as a run saved before CHANNELS keeps it
        programs._save(b.cfg, run)
        programs.step(b.cfg, b.now, b.log)  # (still Telegram: the part already sent is not sent again)
        p = ledger.find(ledger.snapshot(b.cfg.ledger_path), f"{rid}-s01-p1")
        self.assertEqual(p["previous_message_ids"], ["801"])
