"""``/x`` refused at the bottom of the write path, not only by each caller (issue #46; README *Guarantees*).

Every caller refuses off-the-record words before it writes and decides what to answer: ``actions.refuse`` on the
faces, ``inbound`` and the privacy pass in the chat, ``writes._his_words`` for an edit. Under them, the functions that
put his words into an entry refuse too, so a caller that forgot writes nothing: ``capture.record`` (every capture,
chat or face), ``capture.write_words`` (the one caller of ``entries.record_reply``), ``conversations.his_block`` (his
lines of a conversation) and ``serve.blocks.edit`` (an edit of a block of his). Each is called here directly, as a
forgetful caller would. Only the command in the text is the floor's: what falls in a ``/x`` window stays the pass's."""
import ast
import hashlib
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from helpers import NEUTRAL, ROOT, add_prompt, make_cfg

from journal import actions, capture, conversations, entries, ledger
from journal.serve import blocks, document

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=timezone.utc)
OFF = ("/x keep this out", "/x", "  /X@journal_bot out", "fine words\n/x and this line is off the record")
CUSTOM = ("/off not this", "fine\n/off not this either")
JUST_WORDS = ("a/x is not the command", "/xenon is a gas", "the command /x in the middle is just words")


def tree(root: Path) -> str:
    """A digest of every file under ``root`` (names and bytes)."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(str(p.relative_to(root)).encode() + b"\0" + p.read_bytes())
    return h.hexdigest()


class FloorCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), NEUTRAL)
        self.prompt = add_prompt(self.cfg, "morning", "2026-10-15", NOW, message_id="900")

    def tearDown(self):
        self.tmp.cleanup()

    def unchanged(self, run, texts=OFF, error=capture.OffRecord):
        """``run(text)`` refuses each of ``texts`` with ``error`` and leaves the journal and the state as they were."""
        for text in texts:
            with self.subTest(text=text):
                before = (tree(self.cfg.root), tree(self.cfg.state_dir))
                with self.assertRaises(error):
                    run(text)
                self.assertEqual((tree(self.cfg.root), tree(self.cfg.state_dir)), before)


class CaptureFloorTest(FloorCase):
    def test_record_refuses_with_the_ledger_and_the_entry_untouched(self):
        def run(text):
            with ledger.locked(self.cfg.ledger_path) as data:
                p = ledger.find(data, self.prompt["id"])
                was = dict(p)
                try:
                    capture.record(self.cfg, p, "reply", text, NOW, data=data)
                finally:
                    self.assertEqual(p, was)   # (not even the record's stem was set)
        self.unchanged(run)

    def test_the_chat_callers_that_do_not_check_reach_the_floor(self):
        """``capture.capture``, ``capture_to`` and ``capture_note`` leave ``/x`` to ``inbound``; called without it,
        nothing is written and no note record is made."""
        self.unchanged(lambda text: capture.capture_to(self.cfg, self.prompt["id"], "reply", text, NOW, "text", "901"))
        self.unchanged(lambda text: capture.capture_note(self.cfg, text, NOW, "text", "902"))
        self.unchanged(lambda text: capture.capture(self.cfg, self.cfg.chat_id, "900", text, NOW, "text", "903"),
                       texts=[t for t in OFF if not t.lstrip().startswith("/")])  # (a leading "/" it skips itself)

    def test_a_face_that_skipped_actions_refuse_writes_nothing(self):
        self.unchanged(lambda text: capture.capture_web(self.cfg, "morning", text, NOW))
        self.unchanged(lambda text: capture.capture_web(self.cfg, None, text, NOW, via=capture.MCP_VIA))

    def test_a_block_into_an_entry_with_no_record_writes_nothing_and_keeps_no_recording(self):
        stem = "2026-10-14-j"
        entries.record_reply(self.cfg.root, {"stem": stem, "kind": "j", "local_date": "2026-10-14",
                                             "sent_at_local": "08:00 IST", "opener": ""},
                             "an older note", NOW, "text", names=NEUTRAL)
        upload = Path(self.tmp.name) / "upload.ogg"
        upload.write_bytes(b"OggS" + bytes(20))
        self.unchanged(lambda text: capture.add_web_block(self.cfg, stem, text, NOW))
        self.unchanged(lambda text: capture.add_web_block(self.cfg, stem, text, NOW, media=str(upload)))
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), [f"{stem}.md"])

    def test_write_words_refuses_and_the_custom_command_is_the_one_read(self):
        prompt = {"stem": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15",
                  "sent_at_local": "08:30 IST", "opener": ""}
        self.unchanged(lambda text: capture.write_words(self.cfg, prompt, text, NOW, "text"))
        self.cfg.conversations["opt_out_prefix"] = "/off"
        self.unchanged(lambda text: capture.write_words(self.cfg, prompt, text, NOW, "text"), texts=CUSTOM)

    def test_the_command_anywhere_but_a_line_start_is_just_words(self):
        for text in JUST_WORDS:
            with self.subTest(text=text), ledger.locked(self.cfg.ledger_path) as data:
                info = capture.record(self.cfg, ledger.find(data, self.prompt["id"]), "reply", text, NOW, data=data)
                self.assertIn(text, Path(info["path"]).read_text(encoding="utf-8"))

    def test_the_callers_refuse_first_and_say_why(self):
        """The floor is under the callers, not in place of them: a face still answers with its own refusal."""
        for text in OFF:
            with self.subTest(text=text), self.assertRaises(actions.Refused) as e:
                actions.answer(self.cfg, "morning", text, via="cli", now=NOW)
            self.assertEqual(e.exception.code, "off-the-record")
            self.assertNotIsInstance(e.exception, capture.OffRecord)


class ConversationFloorTest(FloorCase):
    def test_his_block_refuses(self):
        for text in OFF:
            with self.subTest(text=text), self.assertRaises(capture.OffRecord):
                conversations.his_block(self.cfg, NOW, text, key="m:1")
        self.cfg.conversations["opt_out_prefix"] = "/off"
        for text in CUSTOM:
            with self.subTest(text=text), self.assertRaises(capture.OffRecord):
                conversations.his_block(self.cfg, NOW, text)
        self.assertIn("/x is the old command", conversations.his_block(self.cfg, NOW, "/x is the old command"))


class EditFloorTest(FloorCase):
    def test_an_edit_refuses_with_the_routes_status(self):
        prompt = {"stem": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15",
                  "sent_at_local": "08:30 IST", "opener": ""}
        path = entries.record_reply(self.cfg.root, prompt, "words to edit", NOW, "text", names=NEUTRAL)
        body = path.read_text(encoding="utf-8")
        key = document.blocks_of(body, path.stem)[0]["key"]
        for text in OFF[:1] + OFF[3:]:
            with self.subTest(text=text), self.assertRaises(blocks.Refused) as e:
                blocks.edit(body, key, blocks.clean(text), cfg=self.cfg)
            self.assertEqual((e.exception.status, e.exception.slug), (422, "off-the-record"))
        self.assertIn("now edited", blocks.edit(body, key, "now edited", cfg=self.cfg))


class OneWriterTest(unittest.TestCase):
    """The floor holds only if nothing writes his words around it: ``entries.record_reply`` is called by
    ``capture.write_words`` alone, and ``blocks.edit`` cannot be called without the config it refuses by."""

    def callers(self, name: str) -> list[str]:
        found = []
        for path in sorted((ROOT / "src" / "journal").rglob("*.py")):
            tree_ = ast.parse(path.read_text(encoding="utf-8"))
            for fn in ast.walk(tree_):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(fn):
                    if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == name:
                        found.append(f"{path.relative_to(ROOT / 'src' / 'journal')}:{fn.name}")
        return sorted(set(found))

    def test_record_reply_has_one_caller(self):
        self.assertEqual(self.callers("record_reply"), ["capture.py:write_words"])

    def test_his_words_in_a_conversation_go_through_his_block(self):
        self.assertEqual(self.callers("append_conversation"), ["conversations.py:_append"])
        self.assertIn("conversations.py:his", self.callers("his_block"))

    def test_an_edit_needs_the_config(self):
        import inspect
        param = inspect.signature(blocks.edit).parameters["cfg"]
        self.assertEqual((param.kind, param.default), (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.empty))


if __name__ == "__main__":
    unittest.main()
