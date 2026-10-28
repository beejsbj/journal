"""Capture mode (CAPMODE): ``capture.mode`` is ``everything`` (the default: every chat thread is journal material,
exactly as before) or ``arm`` (the journal is one arm of a general agent: replies to prompts, /j notes, program
answers, voice notes answering a prompt and the companion's explicit saves are journaled, with the conversation
that follows them; ordinary chat that answers no prompt is not written into any entry). /x holds in both."""
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

import test_conversations as tc
import test_sweep
from helpers import CHAT, add_prompt, make_cfg

from journal import (agent, capture, config as jconfig, context, conversations, entries, ledger, privacy, programs,
                     status, sweep, telegram)
from journal.config import from_dict

m, conv = tc.m, tc.conv


def arm(cfg):
    cfg.capture = {"mode": "arm"}
    return cfg


class SweepFirst:
    """A tick runs the sweep before the conversation writer, and under arm the writer waits for the sweep to have
    passed a message of his (a reply the hook missed is the sweep's to file); a case that ticks the writer alone must
    do the same."""

    def tick(self, at):
        log = []
        sweep.run(self.cfg, at, log)
        conversations.run(self.cfg, at, log)
        return [l for l in log if l.startswith("conversation")]


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def load(self, **over):
        return from_dict({"root": str(self.root / "J"), "state_dir": str(self.root / "s"), **over})

    def test_the_default_is_everything(self):
        cfg = self.load()
        self.assertEqual(jconfig.capture_mode(cfg), "everything")
        self.assertEqual(jconfig.DEFAULTS["capture"], {"mode": "everything"})
        self.assertEqual(jconfig.capture_mode(make_cfg(self.root)), "everything")  # a Config built by hand too
        self.assertFalse([p for p in jconfig.problems(cfg) + cfg.warnings if p.startswith("capture")])

    def test_arm_is_read(self):
        for value in ("arm", " Arm "):
            self.assertEqual(jconfig.capture_mode(self.load(capture={"mode": value})), "arm")

    def test_an_unknown_mode_means_everything_and_doctor_says_so(self):
        for bad in ({"mode": "some"}, {"mode": 3}, "arm"):
            cfg = self.load(capture=bad)
            self.assertEqual(jconfig.capture_mode(cfg), "everything", bad)
            said = jconfig.problems(cfg) + cfg.warnings  # (doctor shows both, as config rows)
            self.assertTrue([p for p in said if p.startswith("capture")], bad)


class ArmWriterTest(SweepFirst, tc.Base):
    """The conversation writer in ``arm`` mode."""

    def setUp(self):
        super().setUp()
        arm(self.cfg)

    def files(self):
        return sorted(p.name for p in self.cfg.root.iterdir())

    def test_unrelated_chat_makes_no_entry(self):
        self.his("thinking about the bank call tomorrow", m(0), "500")
        self.hers("What time is it booked for?", m(1))
        self.his("6:30, and I keep putting off the paperwork", m(4), "501")
        self.hers("Want to do the paperwork together tonight?", m(5))
        log = self.tick(m(10))
        self.assertEqual(log, [])
        self.assertEqual(self.files(), [])
        self.assertEqual(ledger.snapshot(self.cfg.ledger_path)["prompts"], [])
        self.assertFalse(conversations.snapshot(self.cfg).get("written"))
        # and the cursor moved on: a later tick does not pick it up either
        self.assertEqual(self.tick(m(60)), [])
        self.assertEqual(self.files(), [])

    def test_a_prompt_reply_and_its_conversation_are_saved(self):
        self.his("idle chat", m(0), "530")
        self.hers("mm", m(1))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(3), message_id="600")
        capture.capture(self.cfg, CHAT, "600", "the day was long", m(5), "text", "531")  # the hook saved it
        self.his('[Replying to: "🌻 Evening journal"]\n\n[Journal reply — saved …]\n\nthe day was long', m(5), "531")
        self.hers("Long how?", m(6))
        self.his("the bank, mostly", m(8), "532")
        self.hers("Ah, the bank.", m(9))
        self.tick(m(15))
        evening = (self.cfg.root / "2026-10-15-evening.md").read_text()
        self.assertEqual(entries.raw_entry(evening).count("the day was long"), 1)
        section = conv(evening)
        self.assertTrue(section.startswith("> [!quote]- Iris · 19:36\n> Long how?"), section)
        self.assertIn("**19:38** — the bank, mostly", section)
        self.assertIn("> Ah, the bank.", section)
        self.assertNotIn("idle chat", evening)
        self.assertEqual(self.files(), ["2026-10-15-evening.md"])  # the chat before the prompt made no note

    def test_a_direct_reply_mid_thread_takes_the_rest_but_not_what_came_before(self):
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=m(-800), message_id="610")  # (past its window)
        self.his("random chat", m(0), "550")
        self.hers("sure", m(1))
        capture.capture(self.cfg, CHAT, "610", "slept fine actually", m(3), "text", "551")
        self.his("[Journal reply — saved …]\n\nslept fine actually", m(3), "551")
        self.hers("Good to hear.", m(4))
        self.his("and dreamt of rain", m(5), "552")
        self.tick(m(10))
        morning = (self.cfg.root / "2026-10-15-morning.md").read_text()
        self.assertIn("> Good to hear.", morning)
        self.assertIn("— and dreamt of rain", morning)
        self.assertNotIn("random chat", morning)
        self.assertNotIn("sure", conv(morning))
        self.assertEqual(self.files(), ["2026-10-15-morning.md"])

    def test_a_j_note_and_its_conversation_are_saved(self):
        info = capture.capture_note(self.cfg, "can't sleep", m(0), "text", "540")
        self.his(capture.note_context(self.cfg, info) + "can't sleep", m(0), "540")
        self.hers("What's keeping you up?", m(1))
        self.his("the flight", m(3), "541")
        self.tick(m(8))
        body = self.note().read_text()
        self.assertIn("capture: /j\n", body)
        self.assertIn("> What's keeping you up?", body)
        self.assertIn("**19:33** — the flight", body)
        self.assertFalse(self.note(2).exists())

    def test_a_j_the_hook_and_sweep_missed_is_still_a_note(self):
        self.his("/j the lake at dusk", m(0), None)  # no platform id: neither the hook nor the sweep took it
        self.hers("Lovely.", m(1))
        self.tick(m(8))
        body = self.note().read_text()
        self.assertIn("the lake at dusk", entries.raw_entry(body))
        self.assertIn("> Lovely.", body)

    def test_after_a_quiet_gap_the_chat_is_ordinary_again(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-2), message_id="600")
        capture.capture(self.cfg, CHAT, "600", "evening words", m(0), "text", "531")
        self.his("[Journal reply — saved …]\n\nevening words", m(0), "531")
        self.hers("Thanks for that.", m(1))
        self.his("much later, about the football", m(60), "532")  # past quiet_minutes: a new thread
        self.hers("Who won?", m(61))
        self.tick(m(70))
        evening = (self.cfg.root / "2026-10-15-evening.md").read_text()
        self.assertIn("> Thanks for that.", evening)
        self.assertNotIn("football", evening)
        self.assertNotIn("Who won", evening)
        self.assertEqual(self.files(), ["2026-10-15-evening.md"])

    def test_off_the_record_still_holds_inside_a_saved_thread(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-2), message_id="600")
        capture.capture(self.cfg, CHAT, "600", "evening words", m(0), "text", "531")
        self.his("[Journal reply — saved …]\n\nevening words", m(0), "531")
        self.hers("Thanks.", m(1))
        self.his("/x the private part", m(2), "532")
        self.hers("Understood, kept out.", m(3))
        self.his("back on the record", m(4), "533")
        self.tick(m(10))
        evening = (self.cfg.root / "2026-10-15-evening.md").read_text()
        self.assertNotIn("private part", evening)
        self.assertNotIn("kept out", evening)
        self.assertIn("— back on the record", evening)

    def test_unrelated_chat_audio_is_not_kept(self):
        cache = Path(self.tmp.name) / "cache.ogg"
        cache.write_bytes(b"OggS-voice")
        dest = conversations.stash_audio(self.cfg, "571", str(cache), ".ogg", {"duration": 5}, m(0))
        self.his('"a voice note about nothing in particular"', m(0), "571")
        self.tick(m(8))
        self.assertEqual(self.files(), [])
        self.assertFalse(Path(dest).exists())  # dropped now, not three days from now
        self.assertEqual(conversations.snapshot(self.cfg).get("audio"), {})

    def test_unrelated_chat_pictures_are_not_kept(self):
        cache = Path(self.tmp.name) / "cache.jpg"
        cache.write_bytes(b"\xff\xd8chat")
        conversations.stash_images(self.cfg, "572", [(str(cache), ".jpg")], m(0))
        from test_images import note
        self.his(note("A lake at dusk.") + "\n\nthe walk", m(0), "572")
        self.tick(m(8))
        self.assertEqual(self.files(), [])
        self.assertEqual(conversations.snapshot(self.cfg).get("images"), {})  # dropped now, not three days from now
        self.assertEqual(list((self.cfg.state_dir / "audio-stash").iterdir()), [])

    def test_a_backfill_keeps_a_prompt_reply_the_ledger_never_got(self):
        (self.cfg.state_dir / "conversations.json").unlink()
        add_prompt(self.cfg, kind="morning", local_date="2026-10-14", sent=m(-1500), message_id="610")
        self.his('[Replying to: "Morning journal\n\n1. How did you sleep?"]\n\nslept badly, the rain was loud',
                 m(-1400), "300")  # a busy-path reply the hook and the sweep never saw
        self.hers("The rain again.", m(-1399))
        self.his("plain chat from yesterday", m(-1300), "301")
        self.hers("noted", m(-1299))
        lines = conversations.backfill(self.cfg, date(2026, 10, 14), write=True, now=m(0))
        self.assertTrue([l for l in lines if "create 2026-10-14-morning.md" in l and "1 from Theo" in l], lines)
        morning = (self.cfg.root / "2026-10-14-morning.md").read_text()
        self.assertIn("slept badly, the rain was loud", entries.raw_entry(morning))
        self.assertNotIn("plain chat", "".join(p.read_text() for p in self.cfg.root.glob("*.md")))

    def test_a_dry_run_backfill_plans_the_same_prompt_reply(self):
        (self.cfg.state_dir / "conversations.json").unlink()
        add_prompt(self.cfg, kind="morning", local_date="2026-10-14", sent=m(-1500), message_id="610")
        self.his('[Replying to: "Morning journal\n\n1. How did you sleep?"]\n\nslept badly, the rain was loud',
                 m(-1400), "300")
        self.his("plain chat from yesterday", m(-1300), "301")
        lines = conversations.backfill(self.cfg, date(2026, 10, 14), write=False, now=m(0))
        self.assertTrue([l for l in lines if "2026-10-14-morning.md" in l and "1 from Theo" in l], lines)
        self.assertFalse(list(self.cfg.root.glob("*.md")))  # (nothing written)
        self.assertEqual(ledger.snapshot(self.cfg.ledger_path)["prompts"][0].get("captures"), None)

    def test_a_dry_run_backfill_counts_every_direct_reply_to_a_prompt(self):
        (self.cfg.state_dir / "conversations.json").unlink()
        add_prompt(self.cfg, kind="morning", local_date="2026-10-14", sent=m(-1500), message_id="610")
        quote = '[Replying to: "Morning journal\n\n1. How did you sleep?"]\n\n'
        self.his(quote + "slept badly, the rain was loud", m(-1400), "300")
        self.his(quote + "and one more thing about the dream", m(-1300), "301")  # a new thread (a long gap)
        lines = conversations.backfill(self.cfg, date(2026, 10, 14), write=False, now=m(0))
        self.assertTrue([l for l in lines if "2 from Theo" in l and "2026-10-14-morning.md" in l], lines)

    def test_a_backfill_follows_the_mode(self):
        (self.cfg.state_dir / "conversations.json").unlink()
        self.his("plain chat from yesterday", m(-1440), "300")
        self.hers("noted", m(-1439))
        lines = conversations.backfill(self.cfg, date(2026, 10, 14), write=True, now=m(0))
        self.assertEqual(self.files(), [])
        self.assertIn("0 new note entries", lines[-2] if lines[-1].startswith("  not written") else lines[-1])


class EverythingUnchangedTest(tc.Base):
    """The default, spelled out: the same chat that ``arm`` leaves alone is an auto note."""

    def test_an_explicit_everything_is_the_default(self):
        self.cfg.capture = {"mode": "everything"}
        self.his("thinking about the bank call tomorrow", m(0), "500")
        self.hers("What time is it booked for?", m(1))
        log = self.tick(m(10))
        self.assertEqual(log, ["conversation → new note 2026-10-15-j.md: +1 from Theo, +1 from Iris"])


class ArmProgramTest(SweepFirst, tc.Base):
    """A program phase answer is a reply to a prompt: saved, and the conversation after it goes into its session."""

    def setUp(self):
        super().setUp()
        arm(self.cfg)
        self.next_id = 700

        def bot(cfg, method, payload):
            self.next_id += 1
            return {"message_id": self.next_id}

        def unreachable(*a, **k):
            raise agent.AgentUnavailable("no model in this test")
        for p in (mock.patch.object(telegram, "_call", bot), mock.patch.object(telegram, "token", lambda c: "T"),
                  mock.patch.object(agent, "ask", unreachable)):
            p.start()
            self.addCleanup(p.stop)

    def test_a_phase_answer_and_its_conversation_are_saved(self):
        self.his("chat before the program", m(-10), "880")
        programs.start(self.cfg, "fix-life", m(-5), [])
        info = capture.capture(self.cfg, CHAT, None, "my first answer", m(0), "text", "881")  # the hook took it
        self.assertTrue(info and info.get("path"), info)
        self.his("[Journal reply — program reply: …]\n\nmy first answer", m(0), "881")
        self.hers("That is a real place to start.", m(1))
        self.tick(m(8))
        body = Path(info["path"]).read_text()
        self.assertIn("my first answer", body)
        self.assertIn("> That is a real place to start.", body)
        self.assertNotIn("chat before the program", "".join(p.read_text() for p in self.cfg.root.glob("*.md")))


class ArmHandoffTest(SweepFirst, tc.Base):
    """Under ``arm`` the writer never drops a message of his the sweep has not passed: it may be a reply the hook
    missed (the busy path), which the sweep files into the prompt's entry and the writer then follows."""

    def setUp(self):
        super().setUp()
        arm(self.cfg)
        add_prompt(self.cfg, kind="morning", local_date="2026-10-15", sent=m(-5), message_id="610")
        self.his("slept badly, dreamt of the exam", m(0), "551")  # the hook missed it (busy path)
        self.hers("That old exam dream again?", m(1))
        self.his("yes, the one where I can't find the room", m(2), "552")
        self.hers("What room is it usually?", m(3))

    def files(self):
        return sorted(p.name for p in self.cfg.root.iterdir())

    def check_morning(self):
        morning = (self.cfg.root / "2026-10-15-morning.md").read_text()
        self.assertEqual(entries.raw_entry(morning).count("slept badly, dreamt of the exam"), 1)
        section = conv(morning)
        self.assertIn("> That old exam dream again?", section)
        self.assertIn("— yes, the one where I can't find the room", section)
        self.assertIn("> What room is it usually?", section)
        self.assertEqual(self.files(), ["2026-10-15-morning.md"])  # no auto note beside it

    def test_a_sweep_that_failed_one_tick_loses_nothing(self):
        with mock.patch.object(sweep, "reconcile", side_effect=RuntimeError("transient")):
            with self.assertRaises(RuntimeError):
                sweep.run(self.cfg, m(10), [])
            log = []
            conversations.run(self.cfg, m(10), log)  # the tick goes on to the writer
        self.assertEqual(self.files(), [])  # it waited: nothing dropped, nothing filed as an auto note
        self.assertTrue([l for l in log if "waiting for the sweep" in l], log)
        self.tick(m(15))  # the sweep recovers and files 551; the writer follows
        self.check_morning()

    def test_a_sweep_that_has_not_run_yet_is_waited_for(self):
        conversations.run(self.cfg, m(10), [])  # (no sweep state at all)
        self.assertEqual(self.files(), [])
        self.tick(m(15))
        self.check_morning()

    def test_with_the_sweep_off_the_writer_files_the_reply_where_the_sweep_would(self):
        self.cfg.sweep = {"enabled": False}
        self.tick(m(10))
        self.check_morning()

    def test_with_the_sweep_off_a_row_stored_twice_is_written_once(self):
        self.cfg.sweep = {"enabled": False}
        self.db.add("slept badly, dreamt of the exam", m(0), "551")  # (the same Telegram message, stored again)
        self.tick(m(10))
        morning = (self.cfg.root / "2026-10-15-morning.md").read_text()
        self.assertEqual(morning.count("slept badly, dreamt of the exam"), 1)

    def test_with_the_sweep_off_ordinary_chat_is_still_not_journaled(self):
        self.cfg.sweep = {"enabled": False}
        (self.cfg.state_dir / "conversations.json").unlink()
        capture_cfg_prompt = ledger.snapshot(self.cfg.ledger_path)["prompts"][0]
        with ledger.locked(self.cfg.ledger_path) as data:  # the morning prompt is answered and folded already
            data["prompts"][0]["collapsed_at"] = m(-1).isoformat()
        self.tick(m(10))
        self.assertEqual(self.files(), [], capture_cfg_prompt)

    def test_an_unusable_lookback_means_48_hours_for_the_sweep_and_the_writer_alike(self):
        self.assertEqual(sweep.lookback(self.cfg), timedelta(hours=48))
        for bad in (None, "bad", -3, 0, float("inf"), "x"):
            self.cfg.sweep = {"enabled": True, "lookback_hours": bad}
            self.assertEqual(sweep.lookback(self.cfg), timedelta(hours=48), bad)
        self.cfg.sweep = {"enabled": True, "lookback_hours": "bad"}
        self.tick(m(10))  # the sweep runs (it used to raise on this value) and the writer follows it
        self.check_morning()


class ArmBeyondTheSweepTest(SweepFirst, tc.Base):
    """A message older than the sweep still looks (``sweep.lookback_hours``): nobody else would file it."""

    def setUp(self):
        super().setUp()
        arm(self.cfg)
        self.cfg.sweep = {"enabled": True, "lookback_hours": 1}

    def test_a_reply_older_than_the_lookback_is_filed_by_the_writer(self):
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-200), message_id="600")
        self.his('[Replying to: "Morning journal\n\n1. How did you sleep?"]\n\nlong day, mostly the bank', m(-90), "520")
        self.hers("The bank again.", m(-89))
        self.tick(m(10))
        evening = (self.cfg.root / "2026-10-15-evening.md").read_text()
        self.assertIn("long day, mostly the bank", entries.raw_entry(evening))
        self.assertIn("> The bank again.", conv(evening))

    def test_ordinary_chat_older_than_the_lookback_is_not_waited_for(self):
        self.his("chat from before the lookback", m(-600), "540")
        log = []
        conversations.run(self.cfg, m(10), log)
        self.assertFalse([l for l in log if "waiting" in l], log)
        self.assertEqual(list(self.cfg.root.iterdir()), [])


class ArmOwnMessagesTest(SweepFirst, tc.Base):
    """His reply to the journal's own words (an annotation reply, a day reflection) is journal material under arm."""

    def setUp(self):
        super().setUp()
        arm(self.cfg)
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(-2), message_id="600")
        capture.capture(self.cfg, CHAT, "600", "the day was long", m(0), "text", "531")
        self.his("[Journal reply — saved …]\n\nthe day was long", m(0), "531")
        self.hers("Long how?", m(1))
        self.tick(m(10))
        self.evening = self.cfg.root / "2026-10-15-evening.md"
        self.annotation = "You named the bank three times and never the call itself."
        entries.write_annotations(self.evening, self.annotation, self.cfg.names)

    def sent(self, key, rec):
        with ledger.locked(self.cfg.ledger_path) as data:
            if key == "annotation_reply":
                data["prompts"][0][key] = rec
            else:
                data.setdefault("reflections", []).append(rec)

    def test_a_reply_to_the_annotations_joins_its_entry(self):
        self.sent("annotation_reply", {"message_id": "710", "sent_at": m(300).isoformat()})
        self.his(f'[Replying to: "Iris\'s notes on your evening entry\n{self.annotation}"]\n\n'
                 "that's fair, I was avoiding the call", m(600), "700")
        self.hers("What makes the call the hard part?", m(601))
        self.his("my dad used to make those calls", m(603), "701")
        self.tick(m(620))
        section = conv(self.evening.read_text())
        self.assertIn("— that's fair, I was avoiding the call", section)
        self.assertIn("> What makes the call the hard part?", section)
        self.assertIn("— my dad used to make those calls", section)
        self.assertFalse((self.cfg.root / "2026-10-16-j.md").exists())

    def test_a_reply_to_a_day_reflection_starts_a_note_and_keeps_its_thread(self):
        refl = self.cfg.root / "2026-10-15-reflection.md"
        refl.write_text("# Iris's reflection\n\n_From [[2026-10-15-evening]]_\n\n"
                        "You circled the bank all day and never named the call itself.\n", encoding="utf-8")
        self.sent("reflections", {"id": "reflection-2026-10-15", "kind": "reflection", "local_date": "2026-10-15",
                                  "path": str(refl), "written_at": m(300).isoformat(),
                                  "telegram": {"message_id": "711", "sent_at": m(310).isoformat()}})
        self.his('[Replying to: "Iris\'s reflection — Thu 15 Oct 2026 You circled the bank all day and never named '
                 'the call itself."]\n\nthat\'s fair. I was avoiding the call, not the bank', m(600), "700")
        self.hers("What makes the call the hard part?", m(601))
        self.his("my dad used to make those calls", m(603), "701")
        self.tick(m(620))
        note = (self.cfg.root / "2026-10-16-j.md").read_text()
        self.assertIn("I was avoiding the call, not the bank", entries.raw_entry(note))
        self.assertIn("> What makes the call the hard part?", conv(note))
        self.assertIn("— my dad used to make those calls", conv(note))

    def test_a_ledger_with_old_paths_still_recognises_the_reply(self):
        self.sent("annotation_reply", {"message_id": "710", "sent_at": m(300).isoformat()})
        with ledger.locked(self.cfg.ledger_path) as data:  # the journal was moved: the recorded path is gone
            data["prompts"][0]["entry_path"] = "/old/place/Journal/2026-10-15-evening.md"
        self.his(f'[Replying to: "Iris\'s notes on your evening entry\n{self.annotation}"]\n\nthat\'s fair', m(600), "700")
        self.tick(m(620))
        self.assertIn("— that's fair", conv(self.evening.read_text()))

    def test_with_the_sweep_off_a_reply_to_the_annotations_while_a_prompt_is_open_follows_the_hook(self):
        """``capture.match`` files it on the open prompt before anything else; the fallback does the same."""
        self.cfg.sweep = {"enabled": False}
        add_prompt(self.cfg, kind="midweek", local_date="2026-10-15", sent=m(590), message_id="620")
        self.sent("annotation_reply", {"message_id": "710", "sent_at": m(300).isoformat()})
        self.his(f'[Replying to: "Iris\'s notes on your evening entry\n{self.annotation}"]\n\nthat\'s fair', m(600), "700")
        self.tick(m(620))
        self.assertIn("that's fair", entries.raw_entry((self.cfg.root / "2026-10-15-midweek.md").read_text()))
        self.assertNotIn("that's fair", self.evening.read_text())

    def test_a_quote_of_only_the_generic_title_does_not_pick_an_entry(self):
        """Two evening entries were annotated; the title alone fits both annotation replies."""
        add_prompt(self.cfg, kind="evening", local_date="2026-10-14", sent=m(-1500), message_id="590")
        older = self.cfg.root / "2026-10-14-evening.md"
        older.write_text("---\nkind: evening\n---\n\n## Theo\n\n### 19:30 IST · text\n\nan older day\n\n"
                         "## Iris's annotations\n\nYou were tired.\n", encoding="utf-8")
        with ledger.locked(self.cfg.ledger_path) as data:
            old = next(p for p in data["prompts"] if p["local_date"] == "2026-10-14")
            old.update(entry_path=str(older), stem="2026-10-14-evening",
                       annotation_reply={"message_id": "705", "sent_at": m(-1000).isoformat()})
        self.sent("annotation_reply", {"message_id": "710", "sent_at": m(300).isoformat()})
        self.his('[Replying to: "Iris\'s notes on your evening entry"]\n\nI meant the one from Wednesday', m(600), "700")
        self.tick(m(620))
        self.assertNotIn("from Wednesday", self.evening.read_text())  # not filed on the newest by default
        self.assertNotIn("from Wednesday", older.read_text())
        note = (self.cfg.root / "2026-10-16-j.md").read_text()
        self.assertIn("I meant the one from Wednesday", entries.raw_entry(note))

    def test_a_short_quote_that_happens_to_be_in_the_annotations_is_still_ordinary(self):
        entries.write_annotations(self.evening, "Thank you for saying it plainly. " + self.annotation, self.cfg.names)
        self.sent("annotation_reply", {"message_id": "710", "sent_at": m(300).isoformat()})
        self.his('[Replying to: "thank you"]\n\nsee you at six', m(600), "700")
        self.hers("See you.", m(601))
        self.tick(m(620))
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), ["2026-10-15-evening.md"])
        self.assertNotIn("see you at six", self.evening.read_text())

    def test_a_quote_of_ordinary_chat_is_still_ordinary(self):
        self.sent("annotation_reply", {"message_id": "710", "sent_at": m(300).isoformat()})
        self.his('[Replying to: "what time is the quiz tonight at the pub"]\n\nsix, I think', m(600), "700")
        self.hers("Six it is.", m(601))
        self.tick(m(620))
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), ["2026-10-15-evening.md"])


class ArmSwitchTest(SweepFirst, tc.Base):
    """Switching to ``arm`` while a chat-started thread goes on: the rest of it is ordinary chat."""

    def test_a_thread_in_an_auto_note_stops_being_written_after_the_switch(self):
        self.his("chat about the football", m(0), "500")
        self.hers("Who's playing?", m(1))
        self.tick(m(5))
        self.assertTrue(self.note().exists())
        arm(self.cfg)
        for i in range(3):  # gaps under quiet_minutes: one thread all along
            self.his(f"general-agent request {i}: draft my email to the landlord", m(30 + 30 * i), str(600 + i))
            self.hers(f"Here is draft {i}", m(31 + 30 * i))
            self.tick(m(36 + 30 * i))
        body = self.note().read_text()
        self.assertNotIn("landlord", body)
        self.assertNotIn("Here is draft", body)
        self.assertIn("chat about the football", body)  # what was written before stays

    def test_a_reply_that_arrives_after_the_switch_is_not_added_to_the_chat_note(self):
        self.his("chat about the football", m(0), "500")
        self.tick(m(3))  # his message is in the auto note; her reply is not there yet
        self.hers("Who's playing?", m(4))
        arm(self.cfg)
        self.tick(m(10))
        body = self.note().read_text()
        self.assertIn("chat about the football", body)
        self.assertNotIn("Who's playing?", body)

    def test_a_reply_to_the_annotations_of_a_chat_note_keeps_its_thread(self):
        """(annotate_auto) the auto note is annotated and answered: journal material, so its thread stays in it."""
        self.cfg.conversations = {**self.cfg.conversations, "annotate_auto": True}
        self.his("chat about the football", m(0), "500")
        self.hers("Who's playing?", m(1))
        self.tick(m(5))
        arm(self.cfg)
        note = self.note()
        annotation = "You keep coming back to the football when the week has been heavy."
        entries.write_annotations(note, annotation, self.cfg.names)
        with ledger.locked(self.cfg.ledger_path) as data:
            p = next(q for q in data["prompts"] if q.get("capture") == "auto")
            p["annotation_reply"] = {"message_id": "710", "sent_at": m(20).isoformat()}
        self.his(f'[Replying to: "Iris\'s notes on your note\n{annotation}"]\n\nyes, it is a way of not thinking',
                 m(120), "700")
        self.hers("What would you be thinking about?", m(121))
        self.his("the job, mostly", m(125), "701")
        self.hers("Say more.", m(126))
        self.tick(m(140))
        section = conv(note.read_text())
        self.assertIn("— yes, it is a way of not thinking", section)
        self.assertIn("> What would you be thinking about?", section)
        self.assertIn("— the job, mostly", section)
        self.assertIn("> Say more.", section)

    def test_a_reply_held_while_the_turn_ran_is_not_added_after_the_switch(self):
        self.his("chat about the football", m(0), "500")
        self.hers("Who's playing?", m(1))
        import time
        self.db.sql("insert into session_turn_leases values ('tg', 'x', ?, ?)", time.time(), time.time() + 3600)
        self.tick(m(5))  # her reply is held: the turn is still running
        self.assertNotIn("Who's playing?", self.note().read_text())
        arm(self.cfg)
        self.db.sql("delete from session_turn_leases")
        self.tick(m(10))
        self.assertNotIn("Who's playing?", self.note().read_text())

    def test_a_replay_after_a_crash_still_knows_the_note_is_journal_material(self):
        arm(self.cfg)
        self.his("/j the lake at dusk", m(0), None)
        self.hers("Lovely.", m(1))
        self.tick(m(8))
        with conversations.locked(self.cfg) as st:  # the state was lost after the note was committed
            st["cursor"]["last_id"] = 0
            st["cursor"]["thread"] = None
        self.his("the light on the water", m(10), "541")
        self.hers("Say more.", m(11))
        self.tick(m(20))
        body = self.note().read_text()
        self.assertEqual(body.count("the lake at dusk"), 1)
        self.assertIn("— the light on the water", body)
        self.assertIn("> Say more.", body)

    def test_a_recovered_j_is_annotated_like_one_the_hook_saved(self):
        arm(self.cfg)
        self.his("/j the lake at dusk", m(0), None)
        self.tick(m(8))
        self.assertTrue(ledger.snapshot(self.cfg.ledger_path)["prompts"][0]["annotate"])
        self.assertIn("annotations", self.note().read_text().lower())

    def test_a_j_note_keeps_its_thread_in_arm(self):
        arm(self.cfg)
        self.his("/j the lake at dusk", m(0), None)
        self.hers("Lovely.", m(1))
        self.his("the light on the water", m(5), "541")
        self.hers("Say more.", m(6))
        self.tick(m(12))
        body = self.note().read_text()
        self.assertIn("> Lovely.", body)
        self.assertIn("— the light on the water", body)
        self.assertIn("> Say more.", body)


class ArmPackTest(SweepFirst, tc.Base):
    """The context pack an opener is written from reads back only what the journal kept, under arm."""

    def setUp(self):
        super().setUp()
        arm(self.cfg)

    def pack(self):
        privacy._CACHE.clear()
        # the pack reads the last 7 days by the real clock (context._his_messages), so pin it beside the pass's: a case
        # dated around T0 stopped seeing its rows once the calendar passed T0 + 7 days
        with tc.near_t0(), mock.patch.object(context, "datetime", tc._NearT0):
            return context.telegram_messages(self.cfg)

    def test_ordinary_chat_is_not_read_back_into_the_pack(self):
        self.his("my landlord is threatening to keep the deposit", m(0), "900")
        self.hers("That sounds stressful.", m(1))
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(3), message_id="600")
        capture.capture(self.cfg, CHAT, "600", "the day was long", m(5), "text", "901")
        self.his("[Journal reply — saved …]\n\nthe day was long", m(5), "901")
        self.hers("Long how?", m(6))
        self.his("the bank, mostly", m(8), "902")
        self.tick(m(15))
        text = "\n".join(self.pack())
        self.assertNotIn("landlord", text)
        self.assertIn("the bank, mostly", text)

    def test_a_reply_the_hook_saved_is_read_back_whatever_the_ledger_holds(self):
        self.his("[Journal reply — saved …]\n\nthe day was long and strange", m(5), "901")  # (no ledger record)
        self.tick(m(15))
        self.assertIn("the day was long and strange", "\n".join(self.pack()))

    def test_everything_still_reads_all_of_it_back(self):
        self.cfg.capture = {"mode": "everything"}
        self.his("my landlord is threatening to keep the deposit", m(0), "900")
        self.tick(m(15))
        self.assertIn("landlord", "\n".join(self.pack()))


class SurfaceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))

    def render(self):
        import journal.hermes as plugin
        dest = Path(self.tmp.name) / "skill"
        return plugin.render_skill(dest, plugin.skill_tokens(self.cfg.names, self.cfg)).read_text(encoding="utf-8")

    def test_the_skill_says_what_is_saved_in_each_mode(self):
        everything = self.render()
        self.assertNotIn("{{", everything)
        self.assertIn("Any other thread becomes a note entry of its own", everything)
        self.assertIn("the conversation itself, both sides, unless he uses /x", everything)
        arm(self.cfg)
        text = self.render()
        self.assertNotIn("{{", text)
        self.assertNotIn("Any other thread becomes a note entry of its own", text)
        self.assertIn("Ordinary chat that answers no journal prompt is not saved", text)
        self.assertIn("`/j <text>` saves it", text)
        self.assertIn("`/x` is not needed for ordinary chat", text)
        self.assertIn("a reply to one of the journal's own messages", text)
        # the first message while a prompt is open is taken as its answer, even when it was about something else
        self.assertIn("The first message while a prompt is visible", text)
        self.assertIn("is taken as its answer", text)
        # what does not change: replies, /j, the follow-up conversation, off the record
        self.assertIn("Follow-ups are saved into the entry automatically, both sides", text)
        self.assertIn("**Off the record: `/x`.**", text)

    def test_doctor_and_status_show_the_mode(self):
        def row(cfg):
            return {n: (s, d) for n, s, d in status.doctor(cfg, token=False)}["capture mode"]
        st, detail = row(self.cfg)
        self.assertEqual(st, "ok")
        self.assertTrue(detail.startswith("everything"), detail)
        s = status.status(self.cfg)
        self.assertEqual(s["capture_mode"], "everything")
        self.assertIn("Capture mode: everything", status.format_status(s))
        arm(self.cfg)
        self.assertTrue(row(self.cfg)[1].startswith("arm"))
        s = status.status(self.cfg, web=True)
        self.assertEqual(s["capture_mode"], "arm")
        self.assertIn("Capture mode: arm", status.format_status(status.status(self.cfg)))


if __name__ == "__main__":
    unittest.main()
