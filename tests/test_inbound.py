"""WP8: the engine handles an incoming message (``inbound.receive``) and a tool call (``tools.call``) with no agent in
sight. Every message here is a plain ``Inbound`` dataclass; no Hermes object, event or hook is involved (the hook
tests in test_hermes / test_conversations / test_programs … drive the same code through the adapter)."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import CHAT, CleanEnv, add_prompt, make_cfg
from test_programs import Base as ProgramBase

from journal import (audio, capture, commands, context, conversations, entries, hookstate, inbound, location, notebook,
                     tools, turncontext)
from journal.config import load

NOW = datetime(2026, 10, 14, 9, 0, tzinfo=timezone.utc)  # a Wednesday, 14:30 IST


def msg(text="", **kw) -> inbound.Inbound:
    kw.setdefault("platform", "telegram")
    kw.setdefault("chat_id", CHAT)
    kw.setdefault("message_id", "900")
    kw.setdefault("at", NOW)
    return inbound.Inbound(text=text, **kw)


class Spawns:
    def __init__(self):
        self.calls = []

    def __call__(self, cfg, *args):
        self.calls.append(args)


class ReceiveBase(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))
        self.spawn = Spawns()
        self.prompt = add_prompt(self.cfg, kind="evening", local_date="2026-10-14", sent=NOW - timedelta(minutes=20),
                                 message_id="700")

    def files(self):
        return sorted(p.name for p in self.cfg.root.glob("*.md"))

    def entry(self):
        return (self.cfg.root / "2026-10-14-evening.md").read_text(encoding="utf-8")

    def recv(self, text="", **kw):
        return inbound.receive(self.cfg, msg(text, **kw), spawn=self.spawn)


class TextTest(ReceiveBase):
    def test_a_reply_is_saved_verbatim_and_the_companion_gets_the_note_and_the_pack(self):
        context.save(self.cfg, self.prompt["id"], "# Context pack\n\n## Weather\nRain, PACKFACT\n")
        out = self.recv("slept badly", reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertTrue(out.text.startswith("[Journal reply"))
        self.assertTrue(out.text.endswith("slept badly"))
        self.assertEqual(out.captured["prompt"]["id"], self.prompt["id"])
        self.assertIn("slept badly", entries.his_words(self.entry()))
        self.assertEqual(hookstate.summary(self.cfg)["pending_packs"], 1)
        self.assertIn("PACKFACT", turncontext.build(self.cfg, turncontext.Turn(
            "telegram", "s", "t", out.text, NOW)))

    def test_a_message_that_answers_nothing_passes_untouched(self):
        self.assertEqual(self.recv("hello there", reply_to="31337", at=NOW + timedelta(days=3)), inbound.PASS)
        self.assertEqual(self.files(), [])

    def test_other_platforms_and_other_chats_are_not_the_journals(self):
        self.assertEqual(self.recv("slept badly", platform="discord", reply_to="700"), inbound.PASS)
        self.assertEqual(self.recv("slept badly", chat_id="42", reply_to="700"), inbound.PASS)
        self.assertEqual(self.recv("/j a note", chat_id="42"), inbound.PASS)
        self.assertEqual(self.files(), [])

    def test_j_writes_a_note_and_an_empty_j_does_not(self):
        out = self.recv("/j remember the lake")
        self.assertTrue(out.text.startswith("[Journal note"))
        self.assertTrue(out.text.endswith("remember the lake"))
        self.assertEqual(self.files(), ["2026-10-14-j.md"])
        self.assertIn("remember the lake", entries.his_words((self.cfg.root / "2026-10-14-j.md").read_text()))
        self.assertEqual(self.recv("/j   ", message_id="901"), inbound.PASS)

    def test_start_is_routed_to_the_plugin_command_and_captures_nothing(self):
        out = self.recv("/start evening")
        self.assertEqual((out.action, out.text), ("rewrite", "/journal-start evening"))
        self.assertEqual(self.recv("/start"), inbound.PASS)  # bare /start stays the platform's
        self.assertEqual(self.files(), [])

    def test_a_bug_in_capture_passes_the_message_through(self):
        with mock.patch.object(capture, "capture", side_effect=RuntimeError("boom")):
            self.assertEqual(self.recv("slept badly", reply_to="700"), inbound.PASS)


class OffRecordTest(ReceiveBase):
    def test_x_with_text_is_stripped_marked_and_not_captured(self):
        out = self.recv("/x the thing I will not keep", reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertTrue(out.text.endswith("the thing I will not keep"))
        self.assertNotIn("/x", out.text.split("]", 1)[1])
        self.assertEqual(self.files(), [])
        self.assertIn("900", conversations.snapshot(self.cfg)["off"])

    def test_a_bare_x_opens_a_window_that_marks_what_follows_but_not_a_j(self):
        self.assertEqual(self.recv("/x"), inbound.PASS)  # the registered command answers it
        self.assertTrue(conversations.snapshot(self.cfg)["off_threads"])
        later = NOW + timedelta(minutes=2)
        out = self.recv("now the private part", message_id="901", reply_to="700", at=later)
        self.assertTrue(out.text.startswith("[Off the record — this conversation is off the record"))
        self.assertEqual(self.files(), [])  # even as a reply to a prompt: nothing captured
        kept = self.recv("/j but this I want kept", message_id="902", at=later + timedelta(minutes=1))
        self.assertTrue(kept.text.startswith("[Journal note"))

    def test_a_bare_x_with_an_attachment_is_one_off_record_message_not_a_window(self):
        out = self.recv("/x", has_media=True, kind="photo")
        self.assertEqual(out.action, "rewrite")
        self.assertFalse(conversations.snapshot(self.cfg).get("off_threads"))

    def test_when_recording_it_fails_the_message_still_goes_through_marked(self):
        with mock.patch.object(conversations, "note_off", side_effect=OSError("disk full")):
            out = self.recv("/x my secret", reply_to="700")
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertTrue(out.text.endswith("my secret"))
        self.assertEqual(self.files(), [])

    def test_an_audio_attachment_captioned_x_is_marked_even_when_recording_it_fails(self):
        """The bare-/x attachment branch fails closed too: a voice note or audio file must not reach the journal
        (nothing kept, nothing pending for the transcript) because the off-record record could not be written."""
        for kind, name in (("voice", "v.ogg"), ("audio", "a.mp3")):
            with self.subTest(kind=kind):
                media = Path(self.tmp.name) / name
                media.write_bytes(b"\x00ogg")
                with mock.patch.object(conversations, "note_off", side_effect=OSError("disk full")):
                    out = self.recv("/x", kind=kind, media_path=str(media), mime="audio/ogg", has_media=True,
                                    reply_to="700")
                self.assertEqual(out.action, "rewrite")
                self.assertTrue(out.text.startswith("[Off the record"))
                self.assertEqual(hookstate.summary(self.cfg)["pending_voice"], 0)
                self.assertEqual(self.files(), [])
                self.assertEqual([p.name for p in self.cfg.root.glob("*.ogg")] +
                                 [p.name for p in self.cfg.root.glob("*.mp3")], [])

    def test_the_command_is_run_before_the_privacy_pass_even_for_an_audio_caption(self):
        out = self.recv("/x", kind="voice", media_path=None, has_media=True)
        self.assertEqual(out.action, "rewrite")
        self.assertEqual(hookstate.summary(self.cfg)["pending_voice"], 0)


class AudioTest(ReceiveBase):
    def setUp(self):
        super().setUp()
        self.cache = Path(self.tmp.name) / "cache"
        self.cache.mkdir()
        self.media = self.cache / "v.ogg"
        self.media.write_bytes(b"\x00ogg")

    def test_a_voice_note_is_kept_and_written_when_its_transcript_reaches_the_turn(self):
        out = self.recv(kind="voice", media_path=str(self.media), mime="audio/ogg", reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertEqual(hookstate.summary(self.cfg)["pending_voice"], 1)
        self.assertNotIn("slept badly", "".join(p.read_text() for p in self.cfg.root.glob("*.md")))
        inbound.begin_turn(self.cfg, turncontext.Turn("telegram", "s", "t", '"slept badly, long walk"\n\n' + out.text,
                                                      NOW))
        self.assertIn("![[2026-10-14-evening.ogg]]\n\nslept badly, long walk", self.entry())
        self.assertEqual(hookstate.summary(self.cfg)["pending_voice"], 0)
        self.assertTrue((self.cfg.root / "2026-10-14-evening.ogg").is_file())

    def test_an_audio_file_is_recorded_at_once_with_the_senders_extension(self):
        media = self.cache / "blob"
        media.write_bytes(b"x")
        out = self.recv(kind="audio", media_path=str(media), mime="audio/mpeg", file_name="walk.M4A", reply_to="700",
                        meta={"file_name": "walk.M4A", "duration": 12})
        self.assertEqual(out.action, "rewrite")
        self.assertIn("![[2026-10-14-evening.m4a]]", self.entry())
        self.assertTrue(audio.TRANSCRIBING in self.entry() or audio.NOT_TRANSCRIBED in self.entry())
        self.assertEqual(hookstate.summary(self.cfg)["pending_voice"], 0)

    def test_audio_that_answers_nothing_passes(self):
        self.assertEqual(self.recv(kind="voice", media_path=str(self.media), reply_to="31337",
                                   at=NOW + timedelta(days=3)), inbound.PASS)

    def test_the_extension_follows_the_name_then_the_file_then_the_mime(self):
        m = msg(file_name="a.Aif")
        self.assertEqual(inbound.original_extension(m, "/c/x.ogg", "audio/ogg"), ".aif")
        self.assertEqual(inbound.original_extension(msg(), "/c/x.OGG", "audio/mpeg"), ".ogg")
        self.assertEqual(inbound.original_extension(msg(), "/c/blob", "audio/mpeg"), audio.extension_for("audio/mpeg"))

    def test_a_voice_note_the_state_file_cannot_hold_is_left_to_the_sweep(self):
        with mock.patch.object(hookstate, "remember_voice", side_effect=OSError("read-only state dir")):
            out = self.recv(kind="voice", media_path=str(self.media), mime="audio/ogg", reply_to="700")
        self.assertEqual(out, inbound.PASS)
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), [])


class ProgramTest(ProgramBase):
    """A program phase answer, and the commands that move a run, through ``receive``."""

    def setUp(self):
        super().setUp()
        self.spawned = Spawns()

    def recv(self, text, **kw):
        kw.setdefault("message_id", "88")
        return inbound.receive(self.cfg, msg(text, at=self.now, **kw), spawn=self.spawned)

    def test_a_phase_answer_gets_the_program_note_and_spawns_the_settle(self):
        self.start()
        rid = self.the_run()["id"]
        out = self.recv("my answer")
        self.assertTrue(out.text.startswith("[Journal reply — program reply: Sam's answer to Fix your entire life"))
        self.assertTrue(out.text.endswith("my answer"))
        self.assertEqual(self.spawned.calls, [("program", "settle", rid)])
        self.assertEqual(hookstate.summary(self.cfg)["pending_programs"], 1)
        self.assertEqual(out.captured["prompt"]["run"], rid)

    def test_an_adapter_that_cannot_spawn_still_saves_the_answer(self):
        self.start()
        out = inbound.receive(self.cfg, msg("my answer", at=self.now), spawn=None)
        self.assertEqual(out.action, "rewrite")
        self.assertIn("my answer", self.entry().read_text(encoding="utf-8"))

    def test_an_adapter_that_cannot_spawn_leaves_the_action_commands_to_the_platform(self):
        self.start()
        sent = len(self.bot.sends())
        for cmd in ("/next", "/skip", "/back", "/break"):
            with self.subTest(cmd):
                self.assertEqual(inbound.receive(self.cfg, msg(cmd, at=self.now), spawn=None), inbound.PASS)
        self.assertEqual(len(self.bot.sends()), sent)  # nothing was claimed, so no "couldn't" went to the chat
        self.assertEqual(inbound.receive(self.cfg, msg("/program", at=self.now), spawn=None).action, "drop")
        # /web is not answered here, before the platform's gating: it goes on to the registered command
        self.cfg.serve["host"] = "journal.example.org"
        sent = len(self.bot.sends())
        self.assertNotEqual(inbound.receive(self.cfg, msg("/web", at=self.now), spawn=None).action, "drop")
        self.assertEqual(len(self.bot.sends()), sent)

    def test_an_off_record_answer_leaves_the_phase_open_and_spawns_nothing(self):
        self.start()
        out = self.recv("/x a private answer")
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertEqual(self.spawned.calls, [])
        self.assertIsNone(self.prompt(f"{self.the_run()['id']}-s01-p1").get("captures"))

    def test_program_commands_are_run_and_dropped_and_answered_in_the_chat(self):
        self.start()
        rid = self.the_run()["id"]
        out = self.recv("/next")
        self.assertEqual((out.action, out.reason), ("drop", "journal /next"))
        self.assertEqual(self.spawned.calls[-1], ("program", "next", rid, "--notify", "--expect", "1:1:1"))
        sent = len(self.bot.sends())
        self.assertEqual(self.recv("/program").action, "drop")
        self.assertIn("Fix your entire life in 1 day", self.bot.sends()[sent]["text"])
        self.assertIsNone(self.prompt(f"{rid}-s01-p1").get("captures"))  # a command is never his words

    def test_a_program_command_that_cannot_run_goes_on_to_the_platform(self):
        self.start()
        with mock.patch.object(commands, "run_program_command", side_effect=RuntimeError("boom")):
            self.assertEqual(self.recv("/next"), inbound.PASS)


class ToolCallTest(ReceiveBase):
    def off_turn(self):
        hookstate.mark_off_turn(self.cfg, "s1", "t1")
        return tools.ToolTurn("s1", "t1")

    def call(self, name, params, turn=None):
        return json.loads(tools.call(self.cfg, name, params, turn))

    def test_writes_work_in_an_ordinary_turn(self):
        out = self.call("journal_add_note", {"note": "Pattern worth keeping (inference)."}, tools.ToolTurn("s1", "t1"))
        self.assertTrue(out["success"], out)
        self.assertIn("Pattern worth keeping", (self.cfg.root / out["path"]).read_text())  # (absolute or relative)
        self.assertTrue(self.call("journal_notebook", {"action": "add", "text": "ask about the lake"})["success"])

    def test_writes_are_refused_in_an_off_record_turn_and_nothing_is_saved(self):
        turn = self.off_turn()
        out = self.call("journal_add_note", {"note": "his secret"}, turn)
        self.assertEqual((out["success"], out["off_record"]), (False, True))
        self.assertIn("Off the record", out["error"])
        self.assertEqual(self.files(), [])
        for params in ({"action": "add", "text": "his secret"}, {"action": "resolve", "id": "x"}):
            self.assertTrue(self.call("journal_notebook", params, turn)["off_record"])
        self.assertEqual(notebook.open_items(self.cfg), [])

    def test_reads_are_allowed_in_an_off_record_turn(self):
        notebook.add(self.cfg, "an open thread", "", None)
        turn = self.off_turn()
        listed = self.call("journal_notebook", {"action": "list"}, turn)
        self.assertEqual([i["text"] for i in listed["open"]], ["an open thread"])
        self.assertTrue(self.call("journal_notebook", {}, turn)["success"])  # (no action reads)
        self.assertTrue(self.call("journal_program", {"action": "status"}, turn)["success"])
        self.assertIn("error", self.call("journal_program", {"action": "propose", "program": "nothing"}, turn))

    def test_a_bare_x_window_refuses_a_write_with_no_turn_at_all(self):
        conversations.note_off(self.cfg, "811", datetime.now(timezone.utc), thread_start=True)
        out = self.call("journal_add_note", {"note": "x"})
        self.assertTrue(out["off_record"], out)

    def test_it_fails_closed_when_it_cannot_tell(self):
        with mock.patch.object(hookstate, "off_turn", side_effect=OSError("unreadable")):
            out = self.call("journal_add_note", {"note": "x"}, tools.ToolTurn("s1", "t1"))
            self.assertIn("couldn't check", out["error"])
            self.assertEqual(self.files(), [])
            self.assertTrue(self.call("journal_notebook", {"action": "list"})["success"])  # a read needs no check

    def test_the_refusal_is_also_what_a_hook_ahead_of_the_call_uses(self):
        turn = self.off_turn()
        self.assertIn("Off the record", tools.refusal(self.cfg, "journal_add_note", {"note": "x"}, turn))
        self.assertIn("Off the record", tools.refusal(self.cfg, "journal_set_location", {"place": "Oslo"}, turn))
        self.assertIsNone(tools.refusal(self.cfg, "some_other_tool", {}, turn))

    def test_an_unknown_tool_and_odd_params_do_not_raise(self):
        self.assertFalse(self.call("journal_nope", {})["success"])
        self.assertTrue(json.loads(tools.call(self.cfg, "journal_program", None))["success"])  # (None: no params)
        self.assertFalse(self.call("journal_program", {"action": "start"})["success"])

    def test_the_hermes_handler_refuses_too_even_if_the_hook_ahead_of_it_failed_open(self):
        from journal.hermes import runtime
        from journal.hermes import tools as htools
        self.off_turn()
        with mock.patch.object(runtime, "load_config", lambda: self.cfg):
            out = json.loads(htools.journal_add_note({"note": "his secret"}, session_id="s1", turn_id="t1", task_id="x"))
            self.assertTrue(out["off_record"], out)
            self.assertEqual(self.files(), [])
            self.assertTrue(json.loads(htools.journal_notebook({"action": "list"}, session_id="s1"))["success"])
        with mock.patch.object(runtime, "load_config", side_effect=RuntimeError("no config")):
            self.assertFalse(json.loads(htools.journal_add_note({"note": "x"}))["success"])

    def located(self):
        """A config on disk (so a location change would show), and the bytes that must not move."""
        from helpers import write_config
        conf = write_config(Path(self.tmp.name) / "journal.json", {
            "root": str(self.cfg.root), "state_dir": str(self.cfg.state_dir), "chat_id": CHAT,
            "timezone": "Asia/Kolkata", "location": "Hillside"})
        return load(conf), conf

    def test_a_move_said_off_the_record_changes_nothing(self):
        cfg, conf = self.located()
        before = conf.read_bytes()
        hit = {"name": "Chicago", "admin1": "Illinois", "country": "United States",
               "latitude": 41.9, "longitude": -87.6, "timezone": "America/Chicago"}
        reloaded = mock.Mock(side_effect=lambda: load(conf))
        written = mock.Mock()
        with mock.patch.object(location, "geocode", return_value=hit) as geocode:
            out = json.loads(tools.call(cfg, "journal_set_location", {"place": "Chicago"}, self.off_turn(),
                                        reload=reloaded, settings_writer=written))
            self.assertEqual((out["success"], out["off_record"]), (False, True))
            self.assertIn("Off the record", out["error"])
            self.assertIn("on the record", out["error"])  # (a move can be said on the record later)
            geocode.assert_not_called()
        reloaded.assert_not_called()
        written.assert_not_called()
        self.assertEqual(conf.read_bytes(), before)
        self.assertFalse((cfg.state_dir / "location-history.jsonl").exists())
        again = load(conf)
        self.assertEqual((again.location, again.timezone), ("Hillside", "Asia/Kolkata"))
        self.assertIsNone(again.raw.get("coordinates"))
        # and on the record the same call still moves the journal
        with mock.patch.object(location, "geocode", return_value=hit):
            ok = json.loads(tools.call(cfg, "journal_set_location", {"place": "Chicago"},
                                       tools.ToolTurn("s2", "t2"), reload=lambda: load(conf)))
        self.assertTrue(ok["success"], ok)
        self.assertEqual(load(conf).timezone, "America/Chicago")
        self.assertTrue((cfg.state_dir / "location-history.jsonl").exists())

    def test_a_bare_x_window_refuses_a_move_with_no_turn_at_all(self):
        cfg, conf = self.located()
        conversations.note_off(cfg, "811", datetime.now(timezone.utc), thread_start=True)
        with mock.patch.object(location, "geocode") as geocode:
            out = json.loads(tools.call(cfg, "journal_set_location", {"place": "Chicago"}))
        self.assertTrue(out["off_record"], out)
        geocode.assert_not_called()
        self.assertEqual(load(conf).timezone, "Asia/Kolkata")

    def test_a_move_is_refused_when_the_off_record_check_raises(self):
        cfg, conf = self.located()
        before = conf.read_bytes()
        with mock.patch.object(hookstate, "off_turn", side_effect=OSError("unreadable")), \
                mock.patch.object(location, "geocode") as geocode:
            out = json.loads(tools.call(cfg, "journal_set_location", {"place": "Chicago"}, tools.ToolTurn("s1", "t1")))
            self.assertFalse(out["success"])
            self.assertIn("couldn't check", out["error"])
            geocode.assert_not_called()
        self.assertEqual(conf.read_bytes(), before)
        self.assertFalse((cfg.state_dir / "location-history.jsonl").exists())

    def test_the_hermes_route_refuses_a_move_too(self):
        from journal.hermes import hooks, runtime
        from journal.hermes import tools as htools
        cfg, conf = self.located()
        self.off_turn()
        before = conf.read_bytes()
        with mock.patch.object(runtime, "load_config", lambda: cfg), mock.patch.object(location, "geocode") as geocode:
            # the hook ahead of the call turns it away ...
            blocked = hooks.on_tool_call(tool_name="journal_set_location", args={"place": "Chicago"},
                                         session_id="s1", turn_id="t1")
            self.assertEqual(blocked["action"], "block")
            self.assertIn("Off the record", blocked["message"])
            # ... and the handler refuses again if the hook failed open
            out = json.loads(htools.journal_set_location({"place": "Chicago"}, session_id="s1", turn_id="t1"))
            self.assertTrue(out["off_record"], out)
            geocode.assert_not_called()
        self.assertEqual(conf.read_bytes(), before)
        # a config that will not load blocks the write in the hook too
        with mock.patch.object(runtime, "load_config", side_effect=RuntimeError("no config")):
            blocked = hooks.on_tool_call(tool_name="journal_set_location", args={"place": "Chicago"})
            self.assertEqual(blocked["action"], "block")
            self.assertFalse(json.loads(htools.journal_set_location({"place": "Chicago"}))["success"])

    def test_the_specs_are_the_registration_list(self):
        from journal.config import Names
        specs = tools.specs(Names(user="Sam", companion="Ada"))
        self.assertEqual([s.name for s in specs], ["journal_notebook", "journal_set_location", "journal_add_note",
                                                   "journal_program"])
        self.assertEqual({s.name for s in specs}, set(tools.HANDLERS))
        self.assertTrue(all(s.schema["name"] == s.name for s in specs))

    def files(self):
        return sorted(p.name for p in self.cfg.root.glob("*.md"))


class CommandSpecTest(ReceiveBase):
    def test_the_list_an_adapter_registers(self):
        self.assertEqual([s.name for s in commands.SPECS],
                         ["j", "x", "morning", "evening", "midweek", "weekly", "morning-journal", "evening-journal",
                          "journal-start", "next", "skip", "back", "break", "abandon", "program", "web"])
        self.assertTrue(all(s.description for s in commands.SPECS))

    def test_the_off_the_record_command_is_named_by_the_configured_prefix(self):
        self.cfg.conversations["opt_out_prefix"] = "/hush"
        self.assertEqual([s.name for s in commands.specs(self.cfg)][1], "hush")

    def spec(self, name):
        return next(s for s in commands.SPECS if s.name == name)

    def test_a_ritual_command_spawns_the_send_through_whatever_spawn_the_adapter_gives(self):
        spawn = Spawns()
        reply = self.spec("evening").run(self.cfg, "", spawn)
        self.assertIn("Your evening journal is on its way", reply)
        self.assertEqual(spawn.calls, [("send", "evening")])
        self.assertEqual(self.spec("morning-journal").run(self.cfg, "", spawn).count("morning"), 1)
        self.assertEqual(spawn.calls[-1], ("send", "morning"))

    def test_start_names_the_practices_when_asked_for_none_and_for_an_unknown_one(self):
        spawn = Spawns()
        self.assertIn("Which practice?", self.spec("journal-start").run(self.cfg, "", spawn))
        self.assertIn("no practice called", self.spec("journal-start").run(self.cfg, "nope", spawn))
        self.assertEqual(spawn.calls, [])
        self.spec("journal-start").run(self.cfg, "fix-life 2", spawn)
        self.assertEqual(spawn.calls, [("program", "start", "fix-life", "--notify", "--session", "2")])

    def test_a_spawn_that_fails_is_said_in_the_reply_not_raised(self):
        def broken(cfg, *a):
            raise OSError("no fork")
        self.assertIn("Couldn't start the evening journal (OSError)", self.spec("evening").run(self.cfg, "", broken))

    def test_j_needs_no_configuration_and_x_records_the_thread_or_says_it_could_not(self):
        j = self.spec("j")
        self.assertFalse(j.needs_config)
        self.assertIn("/j <anything worth keeping>", j.run(None, "", Spawns()))
        x = self.spec("x")
        self.assertIn("Off the record:", x.run(self.cfg, "", Spawns()))
        self.assertTrue(conversations.snapshot(self.cfg).get("off_threads"))
        with mock.patch.object(conversations, "snapshot", side_effect=OSError("no")):
            self.assertEqual(x.run(self.cfg, "", Spawns()), commands.OFF_RECORD_FAILED)


if __name__ == "__main__":
    unittest.main()
