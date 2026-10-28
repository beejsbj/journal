"""The hook's between-callback memory on disk: deadlines, one-shot takes, a damaged file, stamps."""
import json
import tempfile
import unittest
from pathlib import Path

from helpers import make_cfg

from journal import hookstate, ledger


class HookStateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_voice_notes_are_taken_once_oldest_first_and_expire(self):
        hookstate.remember_voice(self.cfg, "2", {"pid": "b", "note": "[Journal reply — x]"}, now=1000.0)
        hookstate.remember_voice(self.cfg, "1", {"pid": "a", "note": "[Journal reply — x]"}, now=900.0)
        hookstate.remember_voice(self.cfg, "3", {"pid": "c", "note": "[Journal note — y]"}, now=1000.0)
        self.assertIsNone(hookstate.take_voice(self.cfg, "nothing of ours", now=1001.0))
        self.assertEqual(hookstate.take_voice(self.cfg, "words [Journal reply — x]", now=1001.0)["pid"], "a")
        self.assertEqual(hookstate.take_voice(self.cfg, "words [Journal reply — x]", now=1001.0)["pid"], "b")
        self.assertIsNone(hookstate.take_voice(self.cfg, "words [Journal reply — x]", now=1001.0))
        # the third waits for its transcript until its deadline, then is dropped on the next access
        self.assertEqual(hookstate.summary(self.cfg, now=1000.0 + hookstate.VOICE_TTL)["pending_voice"], 1)
        self.assertEqual(hookstate.summary(self.cfg, now=1001.0 + hookstate.VOICE_TTL)["pending_voice"], 0)
        self.assertIsNone(hookstate.take_voice(self.cfg, "[Journal note — y]", now=1001.0 + hookstate.VOICE_TTL))

    def test_packs_and_off_turns(self):
        hookstate.remember_pack(self.cfg, "[note A]", "block A", now=10.0)
        self.assertIsNone(hookstate.take_pack(self.cfg, "[note B]", now=11.0))
        self.assertEqual(hookstate.take_pack(self.cfg, "hi [note A]", now=11.0), "block A")
        self.assertIsNone(hookstate.take_pack(self.cfg, "hi [note A]", now=11.0))
        hookstate.remember_pack(self.cfg, "[note C]", "block C", now=10.0)
        self.assertIsNone(hookstate.take_pack(self.cfg, "[note C]", now=11.0 + hookstate.PACK_TTL))
        hookstate.mark_off_turn(self.cfg, "s1", "t1", now=10.0)
        self.assertEqual(hookstate.off_turn(self.cfg, "s1", now=11.0), {"turn": "t1", "deadline": 10.0 + hookstate.OFF_TURN_TTL})
        self.assertIsNone(hookstate.off_turn(self.cfg, "s2", now=11.0))
        self.assertIsNone(hookstate.off_turn(self.cfg, "s1", now=11.0 + hookstate.OFF_TURN_TTL))
        hookstate.clear_off_turn(self.cfg, "s1", now=11.0)
        self.assertIsNone(hookstate.off_turn(self.cfg, "s1", now=11.0))

    def test_file_shape_lock_and_stamps(self):
        hookstate.remember_pack(self.cfg, "[n]", "b", now=10.0)
        hookstate.stamp(self.cfg, last_his="2026-10-14T09:00:00+00:00")
        path = hookstate.path(self.cfg)
        self.assertEqual(path, self.cfg.state_dir / "hook-state.json")
        self.assertTrue(path.with_suffix(".lock").exists())  # ledger.locked_state's lock beside it
        data = json.loads(path.read_text())
        self.assertEqual(sorted(data), ["off_turns", "packs", "programs", "stamps", "voice"])
        self.assertEqual(hookstate.stamps(self.cfg), {"last_his": "2026-10-14T09:00:00+00:00"})
        self.assertEqual(oct(path.stat().st_mode & 0o777), oct(0o600))

    def test_a_damaged_file_starts_over_and_doctor_can_tell(self):
        path = hookstate.path(self.cfg)
        path.parent.mkdir(parents=True)
        path.write_text("{broken")
        s = hookstate.summary(self.cfg)
        self.assertEqual((s["exists"], s["readable"], s["pending_voice"]), (True, False, 0))
        self.assertIsNone(hookstate.take_voice(self.cfg, "anything"))  # never raises on a damaged file
        self.assertTrue(hookstate.summary(self.cfg)["readable"])  # that access rewrote it as valid JSON
        path.write_text("[1, 2]")  # valid JSON of the wrong shape
        self.assertFalse(hookstate.summary(self.cfg)["readable"])
        path.write_bytes(b"\xff\xfe{")  # not UTF-8: as damaged as not-JSON, never an exception
        self.assertFalse(hookstate.summary(self.cfg)["readable"])
        self.assertIsNone(hookstate.take_voice(self.cfg, "anything"))
        self.assertTrue(hookstate.summary(self.cfg)["readable"])

    def test_summary_before_any_write(self):
        s = hookstate.summary(self.cfg)
        self.assertEqual((s["exists"], s["readable"], s["pending_voice"], s["pending_packs"], s["off_turns"]),
                         (False, True, 0, 0, 0))
        self.assertFalse(hookstate.path(self.cfg).exists())  # reading never creates the file

    def test_one_bad_deadline_is_dropped_not_fatal(self):
        # A hand edit (or another writer's slip) must not make every access raise: the privacy guard reads a
        # raising check as "refuse every journal write", and nothing would ever rewrite the file to heal it.
        path = hookstate.path(self.cfg)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"voice": {"bad": {"note": "[n]", "deadline": "soon"},
                                              "good": {"note": "[g]", "deadline": 2000.0}},
                                    "packs": {"p": {"block": "b", "deadline": None}}}))
        self.assertEqual(hookstate.summary(self.cfg, now=1000.0)["pending_voice"], 1)
        self.assertIsNone(hookstate.take_voice(self.cfg, "[n]", now=1000.0))
        self.assertEqual(hookstate.take_voice(self.cfg, "[g]", now=1000.0)["note"], "[g]")
        hookstate.remember_voice(self.cfg, "k", {"note": "[q]"}, now=1000.0)
        self.assertEqual(sorted(json.loads(path.read_text())["voice"]), ["k"])  # the bad record is gone for good

    def test_an_access_that_changes_nothing_does_not_rewrite_the_file(self):
        hookstate.remember_pack(self.cfg, "[n]", "b", now=10.0)
        path = hookstate.path(self.cfg)
        before = (path.stat().st_ino, path.stat().st_mtime_ns)
        self.assertIsNone(hookstate.take_voice(self.cfg, "nothing of ours", now=11.0))
        self.assertIsNone(hookstate.take_pack(self.cfg, "nothing of ours", now=11.0))
        hookstate.clear_off_turn(self.cfg, "never marked", now=11.0)
        self.assertEqual((path.stat().st_ino, path.stat().st_mtime_ns), before)  # an atomic write would replace it
        self.assertIsNone(hookstate.take_pack(self.cfg, "x", now=11.0 + hookstate.PACK_TTL))  # expiry is a change
        self.assertNotEqual((path.stat().st_ino, path.stat().st_mtime_ns), before)

    def test_a_failed_write_leaves_the_file_intact_and_no_temp_file(self):
        hookstate.remember_pack(self.cfg, "[n]", "b", now=10.0)
        with ledger.locked(self.cfg.ledger_path):
            pass  # the ledger exists, empty
        with self.assertRaises(TypeError):
            hookstate.remember_voice(self.cfg, "k", {"note": "[q]", "meta": {"d": b"bytes"}}, now=10.0)
        with self.assertRaises(TypeError):
            with ledger.locked(self.cfg.ledger_path) as data:
                data["prompts"].append({"bad": b"bytes"})
        state_dir = self.cfg.state_dir
        self.assertEqual(sorted(p.name for p in state_dir.iterdir()),
                         ["hook-state.json", "hook-state.lock", "ledger.json", "ledger.lock"])
        self.assertEqual(hookstate.summary(self.cfg, now=10.0)["pending_packs"], 1)
        self.assertEqual(ledger.snapshot(self.cfg.ledger_path)["prompts"], [])


if __name__ == "__main__":
    unittest.main()
