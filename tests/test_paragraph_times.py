"""The time each paragraph of a typed block was begun (issue #40; DECISIONS.md, PARAGRAPH-TIMES).

The web app's write room notes when each paragraph (a line that is not blank) was begun and sends those times with the
words; the block keeps them on one hidden line under its heading, after the block's own key (MARGIN-FORMAT:
``%% journal block:<id> %%``, then ``%% journal times:23:12 23:15 %%``) and the entry document gives them back, one per
paragraph. What these pin:

* the format: the line, where it goes (after the key, in the same write), his words byte for byte beside it, and a
  block without times exactly as MARGIN-FORMAT writes it;
* the readers take the key and the times in either order, either alone, with a formatter's blank lines between them;
* every reader of his words leaves the line out (it is an engine line: ``ENGINE_MARK``), so an older engine does too;
* the server's check: malformed, miscounted, future or far-past times are dropped whole and the words are saved anyway;
* ``/x`` is still refused on this path, times or not;
* an edit of the block keeps the line, and the times show only while the paragraphs still number the same; a delete
  takes the line with the block;
* a recording's block, and a program's answer, keep no times.
"""
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import unkeyed
from serve_helpers import IST, NOW, SESSION
from test_serve_blocks import EVENING_STEM, URL, EditCase, block
from test_serve_writes import WriteCase, checksum

from journal import actions, entries
from journal.serve import blocks, document

WORDS = "The sea was loud.\n\nThen it went quiet.\nA gull, late."
KEY = r"%% journal block:b[0-9a-f]{8}(?:-[0-9]+)? %%\n"   # (the block's own key, MARGIN-FORMAT)


def keyed(head: str, rest: str) -> str:
    """A pattern: ``head`` (a heading line, with its newline), the block's key line, then ``rest``, all literal."""
    return re.escape(head) + KEY + re.escape(rest)


def iso(minutes_before_now: float) -> str:
    return (NOW - timedelta(minutes=minutes_before_now)).isoformat()


def times_for(text: str, *minutes_before: float) -> list[str]:
    assert len(minutes_before) == entries.paragraph_count(text)
    return [iso(m) for m in minutes_before]


class FormatTest(unittest.TestCase):
    """``entries.record_reply`` and the readers, on files in a temporary root."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.at = datetime(2026, 10, 14, 23, 40, tzinfo=IST)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, text, times=(), **kw):
        prompt = {"stem": "2026-10-14-j", "kind": "j", "local_date": "2026-10-14"}
        return entries.record_reply(self.root, prompt, text, self.at, "web", times=times, **kw).read_text("utf-8")

    def test_the_times_sit_on_one_hidden_line_under_the_heading_and_his_words_are_untouched(self):
        body = self.write(WORDS, ["23:12", "23:15", "23:31"])
        self.assertRegex(body, keyed("### 23:40 IST · text\n", "%% journal times:23:12 23:15 23:31 %%\n\n" + WORDS + "\n"))
        self.assertRegex("%% journal times:23:12 23:15 23:31 %%\n", entries.ENGINE_MARK)
        self.assertEqual(body.count(WORDS), 1)

    def test_without_times_the_block_is_byte_for_byte_what_it_was(self):
        self.assertRegex(self.write(WORDS), keyed("### 23:40 IST · text\n", "\n" + WORDS + "\n\n"))

    def test_a_count_that_does_not_match_the_paragraphs_writes_no_line(self):
        self.assertNotIn("journal times", self.write(WORDS, ["23:12", "23:15"]))
        self.assertNotIn("journal times", self.write("one line", ["9:12"]))   # (not HH:MM)

    def test_a_recording_or_a_picture_keeps_no_times(self):
        self.assertNotIn("journal times", self.write("a caption", ["23:30"], audio="2026-10-14-j.ogg"))
        self.assertNotIn("journal times", self.write("a caption", ["23:30"], images=["2026-10-14-j.jpg"]))

    def test_a_programs_answer_keeps_no_times(self):
        prompt = {"stem": "2026-10-15-fix-life-s01", "kind": "fix-life", "local_date": "2026-10-15",
                  "program": "fix-life", "run": "r1", "phase": 1}
        path = entries.record_reply(self.root, prompt, "the life", self.at, "web", times=["23:30"])
        self.assertNotIn("journal times", path.read_text("utf-8"))

    def test_every_reader_of_his_words_leaves_the_line_out(self):
        body = self.write(WORDS, ["23:12", "23:15", "23:31"])
        for reader in (entries.raw_entry, entries.his_words, entries.entry_words, entries.without_companion_replies):
            with self.subTest(reader.__name__):
                self.assertNotIn("journal times", reader(body))
                self.assertIn(WORDS, reader(body))
        self.assertNotIn("journal times", entries.his_text(self.root / "2026-10-14-j.md"))

    def test_the_document_gives_one_time_per_paragraph_and_his_text_as_written(self):
        body = self.write(WORDS, ["23:12", "23:15", "23:31"])
        (b,) = [b for b in document.blocks_of(body, "2026-10-14-j") if b["kind"] == "his"]
        self.assertEqual(b["text"], WORDS)
        self.assertEqual(b["at"], "23:40 IST")
        self.assertEqual(b["times"], ["23:12", "23:15", "23:31"])

    def test_an_entry_without_times_reads_as_before(self):
        body = self.write(WORDS)
        (b,) = [b for b in document.blocks_of(body, "2026-10-14-j") if b["kind"] == "his"]
        self.assertNotIn("times", b)
        self.assertEqual(b["text"], WORDS)

    def test_a_times_line_that_no_longer_pairs_with_the_paragraphs_is_not_shown(self):
        body = self.write(WORDS, ["23:12", "23:15", "23:31"]).replace("A gull, late.", "A gull, late.\nAnd another.")
        (b,) = [b for b in document.blocks_of(body, "2026-10-14-j") if b["kind"] == "his"]
        self.assertNotIn("times", b)
        self.assertEqual(b["text"], WORDS + "\nAnd another.")

    def test_a_line_shaped_like_it_inside_his_words_is_not_his_times(self):
        # (his words can never hold one: actions.refuse; a hand edit that puts one lower down is not the block's)
        body = self.write("one\ntwo").replace("one\ntwo", "one\n%% journal times:01:00 02:00 %%\ntwo")
        (b,) = [b for b in document.blocks_of(body, "2026-10-14-j") if b["kind"] == "his"]
        self.assertNotIn("times", b)


class KeyOrderTest(unittest.TestCase):
    """The times line beside MARGIN-FORMAT's block key: written after the key in the same write; read through
    ``entries.head_lines_end`` / ``entries.times_under`` in either order, either alone, and with a formatter's blank
    lines (empty or only spaces) between the heading, the key and the times; kept by an edit and taken by a delete."""

    STEM = "2026-10-14-j"
    TIMES = "%% journal times:23:12 23:15 23:31 %%"

    def setUp(self):
        import tempfile
        from helpers import make_cfg
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))
        self.at = datetime(2026, 10, 14, 23, 40, tzinfo=IST)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, times=("23:12", "23:15", "23:31")):
        prompt = {"stem": self.STEM, "kind": "j", "local_date": "2026-10-14"}
        written = {}
        path = entries.record_reply(self.cfg.root, prompt, WORDS, self.at, "web", written=written, times=times)
        return path.read_text("utf-8"), written["block_key"]

    def his(self, body):
        (b,) = [b for b in document.blocks_of(body, self.STEM) if b["kind"] == "his"]
        return b

    def head(self, body, key, lines):
        """``body`` with the lines under the heading replaced by ``lines`` (``{key}`` and ``{times}`` filled)."""
        written = f"### 23:40 IST · text\n%% journal block:{key} %%\n{self.TIMES}\n\n"
        assert written in body
        return body.replace(written, "### 23:40 IST · text\n" + lines.format(
            key=f"%% journal block:{key} %%", times=self.TIMES) + "\n\n")

    def test_a_block_with_both_lines_round_trips(self):
        body, key = self.write()
        self.assertIn(f"### 23:40 IST · text\n%% journal block:{key} %%\n{self.TIMES}\n\n{WORDS}\n", body)
        b = self.his(body)
        self.assertEqual((b["text"], b["times"], b["anchor"], b["at"]),
                         (WORDS, ["23:12", "23:15", "23:31"], f"block:{key}", "23:40 IST"))
        for reader in (entries.raw_entry, entries.his_words, entries.without_companion_replies):
            with self.subTest(reader.__name__):
                self.assertNotIn("journal block:", reader(body))
                self.assertNotIn("journal times:", reader(body))
                self.assertIn(WORDS, reader(body))

    def test_a_keyed_block_without_times_reads(self):
        body, key = self.write(times=())
        self.assertIn(f"### 23:40 IST · text\n%% journal block:{key} %%\n\n{WORDS}\n", body)
        b = self.his(body)
        self.assertEqual((b["text"], b["anchor"]), (WORDS, f"block:{key}"))
        self.assertNotIn("times", b)

    def test_an_old_block_with_times_and_no_key_reads(self):
        # (this package's first draft wrote the times line straight under the heading, before keys existed)
        body, key = self.write()
        body = body.replace(f"%% journal block:{key} %%\n", "")
        self.assertIn(f"### 23:40 IST · text\n{self.TIMES}\n\n{WORDS}\n", body)
        b = self.his(body)
        self.assertEqual((b["text"], b["times"], b["anchor"]), (WORDS, ["23:12", "23:15", "23:31"], None))

    def test_either_order_and_a_formatters_blank_lines_read_the_same(self):
        body, key = self.write()
        for name, lines in {
            "times above the key": "{times}\n{key}",
            "a blank line under the heading": "\n{key}\n{times}",
            "blank lines between them": "\n{key}\n\n{times}",
            "a line of spaces between them": "{key}\n   \n{times}",
            "times first, blank between": "\n{times}\n\n{key}",
        }.items():
            with self.subTest(name):
                b = self.his(self.head(body, key, lines))
                self.assertEqual((b["text"], b["times"], b["anchor"]),
                                 (WORDS, ["23:12", "23:15", "23:31"], f"block:{key}"))

    def test_an_edit_keeps_both_lines_and_a_delete_takes_both(self):
        body, key = self.write()
        for name, lines in {"as written": "{key}\n{times}", "times above the key": "{times}\n{key}",
                            "blank lines between them": "\n{key}\n\n{times}", "no key": "{times}"}.items():
            with self.subTest(name):
                was = self.head(body, key, lines)
                new = blocks.edit(was, "words:0", WORDS.replace("loud", "LOUD"), cfg=self.cfg, stem=self.STEM)
                self.assertEqual(new, was.replace("loud", "LOUD"))
                b = self.his(new)
                self.assertEqual(b["times"], ["23:12", "23:15", "23:31"])
                gone = blocks.remove(was, "words:0", stem=self.STEM)
                self.assertNotIn("%% journal block:", gone)
                self.assertNotIn("%% journal times:", gone)
                self.assertNotIn("The sea was loud.", gone)

    def test_an_edit_that_changes_the_count_keeps_the_lines_and_shows_one_time(self):
        body, key = self.write()
        new = blocks.edit(body, "words:0", WORDS + "\nOne more.", cfg=self.cfg, stem=self.STEM)
        self.assertIn(f"%% journal block:{key} %%\n{self.TIMES}\n\n{WORDS}\nOne more.\n", new)
        b = self.his(new)
        self.assertNotIn("times", b)
        self.assertEqual(b["anchor"], f"block:{key}")


class CheckTest(unittest.TestCase):
    """``actions.paragraph_times``: what a face sent, kept when it holds and dropped whole when not."""

    def setUp(self):
        from helpers import make_cfg
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))   # (Asia/Kolkata)
        self.at = NOW

    def tearDown(self):
        self.tmp.cleanup()

    def check(self, raw, text=WORDS, **kw):
        return actions.paragraph_times(self.cfg, raw, text, self.at, **kw)

    def test_good_times_become_local_hours(self):
        self.assertEqual(self.check(times_for(WORDS, 40, 30, 0)), ["08:32", "08:42", "09:12"])
        utc = [(NOW - timedelta(minutes=m)).astimezone(timezone.utc).isoformat() for m in (40, 30, 0)]
        self.assertEqual(self.check(utc), ["08:32", "08:42", "09:12"])

    def test_times_need_not_rise(self):
        # a paragraph he went back and added above the others was begun later, and says so
        self.assertEqual(self.check(times_for(WORDS, 40, 5, 30)), ["08:32", "09:07", "08:42"])

    def test_a_clock_a_little_ahead_is_the_blocks_own_time(self):
        self.assertEqual(self.check(times_for(WORDS, 40, 30, -3)), ["08:32", "08:42", "09:12"])

    def test_what_does_not_hold_is_dropped_whole(self):
        good = times_for(WORDS, 40, 30, 0)
        cases = {
            "not a list": "08:32 08:42 09:12",
            "a dict": {"0": good[0]},
            "empty": [],
            "too few": good[:2],
            "too many": good + [good[-1]],
            "not text": [1, 2, 3],
            "no offset": [t[:19] for t in good],
            "not a time": ["yesterday", good[1], good[2]],
            "in the future": [good[0], good[1], iso(-30)],
            "a day before": [iso(60 * 25), good[1], good[2]],
            "yesterday, under 24 hours back": [iso(60 * 14), good[1], good[2]],
            "far too long": [good[0], good[1], "9" * 100],
            "year one": ["0001-01-01T00:00:00+14:00", good[1], good[2]],
            "null": None,
        }
        for why, raw in cases.items():
            with self.subTest(why):
                self.assertEqual(self.check(raw), [])

    def test_a_recording_keeps_no_times(self):
        self.assertEqual(self.check(times_for("a caption", 1), "a caption", media="/tmp/upload.webm"), [])

    def test_a_time_from_before_the_journal_day_is_dropped_and_one_across_midnight_is_kept(self):
        # the journal day starts at windows.day_ends_at (04:00): a piece begun at 23:50 and saved at 00:10 is one
        # day's and keeps its times; one begun the evening before and saved after four would read as later today.
        two = "First.\nSecond."
        at = datetime(2026, 10, 15, 0, 10, tzinfo=IST)
        begun = [datetime(2026, 10, 14, 23, 50, tzinfo=IST).isoformat(), datetime(2026, 10, 15, 0, 5, tzinfo=IST).isoformat()]
        self.assertEqual(actions.paragraph_times(self.cfg, begun, two, at), ["23:50", "00:05"])
        at = datetime(2026, 10, 15, 3, 59, tzinfo=IST)   # (still last night's journal day)
        self.assertEqual(actions.paragraph_times(self.cfg, begun, two, at), ["23:50", "00:05"])
        at = datetime(2026, 10, 15, 4, 5, tzinfo=IST)    # (a tab left open: a new journal day began at 04:00)
        self.assertEqual(actions.paragraph_times(self.cfg, begun, two, at), [])
        morning = [datetime(2026, 10, 15, 3, 55, tzinfo=IST).isoformat(), datetime(2026, 10, 15, 4, 1, tzinfo=IST).isoformat()]
        self.assertEqual(actions.paragraph_times(self.cfg, morning, two, at), [])   # (one time of the night before)
        morning[0] = datetime(2026, 10, 15, 4, 0, tzinfo=IST).isoformat()
        self.assertEqual(actions.paragraph_times(self.cfg, morning, two, at), ["04:00", "04:01"])



class RouteTest(WriteCase):
    """The web app's two writes of his words, over HTTP."""

    def stem_of(self, got):
        return got["stem"]

    def test_a_note_keeps_its_times_and_the_document_gives_them_back(self):
        got = self.ok("POST", "/api/v1/entries", {"text": WORDS, "times": times_for(WORDS, 40, 30, 1)}, want=201)
        body = self.entry(got["stem"])
        self.assertRegex(body, keyed("### 09:12 IST · text\n", "%% journal times:08:32 08:42 09:11 %%\n\n" + WORDS + "\n"))
        doc = self.ok("GET", f"/api/v1/entries/{got['stem']}")
        (b,) = [b for b in doc["blocks"] if b["kind"] == "his"]
        self.assertEqual((b["text"], b["times"]), (WORDS, ["08:32", "08:42", "09:11"]))

    def test_times_from_the_night_before_cost_only_the_times(self):
        # a tab left open overnight: the piece was begun yesterday evening (11 hours back, within a day), so its hours
        # would read as later today. His words are saved with the block's one time.
        two = "First.\nSecond."
        last_night = [(NOW - timedelta(hours=11)).isoformat(), iso(1)]
        got = self.ok("POST", "/api/v1/entries", {"text": two, "times": last_night}, want=201)
        self.assertNotIn("journal times:", self.entry(got["stem"]))
        self.assertIn("First.\nSecond.", self.entry(got["stem"]))

    def test_an_answer_keeps_its_times(self):
        got = self.ok("POST", "/api/v1/entries", {"practice": "morning", "text": "Slept well.\n\nTwo lines.",
                                                  "times": times_for("a\nb", 20, 10)}, want=201)
        self.assertRegex(self.entry(got["stem"]),
                         KEY + re.escape("%% journal times:08:52 09:02 %%\n\nSlept well.\n\nTwo lines.\n"))

    def test_a_block_added_to_an_entry_keeps_its_times(self):
        self.ok("POST", f"/api/v1/entries/{EVENING_STEM}/blocks",
                {"text": "More, later.\nAnd more.", "times": times_for("a\nb", 20, 10)}, want=201)
        body = self.entry(EVENING_STEM)
        self.assertRegex(body, keyed("### 09:12 IST · text\n", "%% journal times:08:52 09:02 %%\n\nMore, later.\nAnd more.\n"))
        doc = self.ok("GET", f"/api/v1/entries/{EVENING_STEM}")
        self.assertEqual([b.get("times") for b in doc["blocks"] if b["kind"] == "his" and b["key"].startswith("words:")],
                         [None, None, ["08:52", "09:02"]])

    def test_malformed_times_never_cost_his_words(self):
        for i, raw in enumerate(([iso(5)], "nope", [iso(-60), iso(1), iso(0)], [{"t": 1}] * 3, list(range(5000)))):
            with self.subTest(raw=str(raw)[:40]):
                text = f"Words {i}.\nStill mine.\nAll of it."
                got = self.ok("POST", "/api/v1/entries", {"text": text, "times": raw}, want=201)
                body = self.entry(got["stem"])
                self.assertIn(f"### 09:12 IST · text\n\n{text}\n", unkeyed(body))
                self.assertNotIn("journal times", body)

    def test_off_the_record_is_still_refused_with_times(self):
        before = checksum(self.cfg.root)
        for text in ("/x not for the journal", "fine\n/x but not this"):
            with self.subTest(text):
                got = self.refused("POST", "/api/v1/entries", {"text": text, "times": times_for(text, *[1] * entries.paragraph_count(text))}, want=422)
                self.assertEqual(got["error"], "off-the-record")
                got = self.refused("POST", f"/api/v1/entries/{EVENING_STEM}/blocks",
                                   {"text": text, "times": times_for(text, *[1] * entries.paragraph_count(text))}, want=422)
                self.assertEqual(got["error"], "off-the-record")
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_line_of_the_engines_own_is_still_refused(self):
        got = self.refused("POST", "/api/v1/entries", {"text": "%% journal times:01:00 %%", "times": [iso(1)]}, want=422)
        self.assertEqual(got["error"], "reserved-line")

    def test_a_retry_with_the_same_client_id_is_one_block(self):
        body = {"text": WORDS, "times": times_for(WORDS, 3, 2, 1), "client_id": "w-times-1"}
        first = self.ok("POST", "/api/v1/entries", body, want=201)
        again = self.ok("POST", "/api/v1/entries", body, want=200)
        self.assertEqual(first["stem"], again["stem"])
        self.assertEqual(self.entry(first["stem"]).count("journal times"), 1)


class ProgramTest(WriteCase):
    def test_a_programs_answer_from_the_web_keeps_no_times(self):
        # (a session's answers are read back line for line by the run: ``entries.phase_blocks``)
        from journal import capture, ledger
        stem = "2026-10-15-fix-life-s01"
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": "r1-s1-p3", "stem": stem, "kind": "fix-life", "local_date": "2026-10-15",
                                    "chat_id": self.cfg.chat_id, "message_id": "900", "program": "fix-life",
                                    "run": "2026-10-15-a1f3", "session": 1, "phase": 3, "phase_open": True,
                                    "sent_at": (NOW - timedelta(minutes=5)).isoformat(),
                                    "entry_path": str(self.cfg.root / f"{stem}.md")})
        capture.add_web_block(self.cfg, stem, "an answer", NOW, phase_prompt="r1-s1-p3", times=["09:10"])
        body = self.entry(stem)
        self.assertIn("an answer", body)
        self.assertNotIn("journal times", body)
        self.assertEqual(entries.phase_answers(body)[3], "an answer")


class EditTimesTest(EditCase):
    def add(self, text, *minutes):
        got = self.ok("POST", f"/api/v1/entries/{EVENING_STEM}/blocks",
                      {"text": text, "times": times_for(text, *minutes)}, want=201)
        return got["key"]

    def test_his_edit_keeps_the_line_and_the_times_while_the_paragraphs_number_the_same(self):
        key = self.add("One.\nTwo.", 20, 10)
        before = self.entry(EVENING_STEM)
        got = self.patch(key, "One, better.\nTwo.")
        self.assertEqual(got["block"]["times"], ["08:52", "09:02"])
        self.assertEqual(self.entry(EVENING_STEM), before.replace("One.\nTwo.", "One, better.\nTwo."))
        got = self.patch(key, "One, better.\nTwo.\nThree, new.")
        self.assertNotIn("times", got["block"])
        self.assertRegex(self.entry(EVENING_STEM),
                         KEY + re.escape("%% journal times:08:52 09:02 %%\n\nOne, better.\nTwo.\nThree, new.\n"))

    def test_a_delete_takes_the_line_with_the_block(self):
        keys_before = re.findall(KEY, self.entry(EVENING_STEM))
        key = self.add("Gone soon.", 2)
        self.delete(key)
        self.assertNotIn("journal times", self.entry(EVENING_STEM))
        self.assertEqual(re.findall(KEY, self.entry(EVENING_STEM)), keys_before)   # (its key went with it)
        self.assertNotIn("Gone soon.", self.entry(EVENING_STEM))

    def test_the_splice_alone_keeps_every_other_byte(self):
        key = self.add("A.\nB.", 3, 2)
        body = self.entry(EVENING_STEM)
        new = blocks.edit(body, key, "A!\nB.", cfg=self.cfg, stem=EVENING_STEM)
        self.assertEqual(new, body.replace("A.\nB.", "A!\nB."))
        self.assertEqual(block({"blocks": document.blocks_of(new, EVENING_STEM)}, key)["times"], ["09:09", "09:10"])


if __name__ == "__main__":
    unittest.main()
