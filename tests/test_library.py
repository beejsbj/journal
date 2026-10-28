"""Practice files: parse/render round trip, overrides, defaults, slots, pins, problems, reserved ids, fork."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from helpers import CleanEnv, make_cfg

from journal import ledger, library
from journal.config import Config, Names

GRATITUDE = """---
kind: practice
id: gratitude
title: Daily gratitude
schedule.time: 21:30
---
- Three things today, and why each one mattered.
- Who made one of them possible?
"""

NEUTRAL = Names(user="Sam", companion="Ada", emoji="🌿")


def _definition(sections):
    """The body the web editor sends for a practice (``web/src/editorModel.ts``): ids by place, pins as flagged."""
    return {"id": "zz", "kind": "practice", "title": "Z", "emoji": "", "enabled": True,
            "schedule": [{"time": "21:00", "weekday": None}], "collapse_hours": 6, "annotate": True, "closes": None,
            "opener": {"mode": "companion", "plain": "x"}, "companion": "", "sections": sections}


def _ids_after_a_save(definition):
    text, _ = library.write_text(library.from_dict(definition))
    return [(q.id, q.text) for _title, qs in library.parse(text).sections for q in qs]


class PinnedIdsTest(unittest.TestCase):
    """The editor keeps a question's id across a move by pinning it (``{#id}``); an unpinned id is renumbered by place.
    The shapes ``editorModel`` produces for a section move, a flat list gaining sections, and a removed question."""

    def test_a_section_move_keeps_the_ids_of_both_sections_when_both_are_pinned(self):
        q = lambda id, text, pinned: {"id": id, "text": text, "pinned": pinned}   # noqa: E731
        moved = _definition([{"title": "S2", "questions": [q("2.1", "C", True)]},
                             {"title": "S1", "questions": [q("1.1", "A", True), q("1.2", "B", True)]}])
        self.assertEqual(_ids_after_a_save(moved), [("2.1", "C"), ("1.1", "A"), ("1.2", "B")])
        # the neighbour left unpinned (the old behaviour) is renumbered by place: A and B lose their ids
        loose = _definition([{"title": "S2", "questions": [q("2.1", "C", True)]},
                             {"title": "S1", "questions": [q("1.1", "A", False), q("1.2", "B", False)]}])
        self.assertEqual(_ids_after_a_save(loose), [("2.1", "C"), ("2.2", "A"), ("2.3", "B")])

    def test_a_question_moved_inside_a_list_pins_only_itself_the_neighbour_keeps_its_id(self):
        """The server numbers an unpinned question by place, skipping the numbers that are pinned, so the one it
        displaced keeps its id without a pin of its own (``moveQuestion`` pins only the question pressed)."""
        q = lambda id, text, pinned: {"id": id, "text": text, "pinned": pinned}   # noqa: E731
        down = _definition([{"title": None, "questions": [q("2", "B", False), q("1", "A", True), q("3", "C", False)]}])
        self.assertEqual(_ids_after_a_save(down), [("2", "B"), ("1", "A"), ("3", "C")])
        up = _definition([{"title": None, "questions": [q("1", "A", False), q("3", "C", True), q("2", "B", False)]}])
        self.assertEqual(_ids_after_a_save(up), [("1", "A"), ("3", "C"), ("2", "B")])

    def test_a_flat_list_that_gains_sections_keeps_its_ids_when_pinned(self):
        q = lambda id, text, pinned: {"id": id, "text": text, "pinned": pinned}   # noqa: E731
        sectioned = _definition([{"title": "Section 1", "questions": [q("1", "A", True), q("2", "B", True)]},
                                 {"title": "Section 2", "questions": [q("", "New", False)]}])
        self.assertEqual(_ids_after_a_save(sectioned), [("1", "A"), ("2", "B"), ("2.1", "New")])


class ParseRenderTest(unittest.TestCase):
    def test_builtins_round_trip_and_are_canonical(self):
        for pid, p in library.builtins().items():
            with self.subTest(pid):
                text = library.render(p)
                self.assertEqual(library.parse(text, p.path, builtin=True), p)
                self.assertEqual(text, p.raw)  # the packaged file is already in canonical form
                self.assertTrue(p.enabled and p.annotate and p.collapse_hours == 6.0 and p.emoji == "")

    def test_the_four_rituals_and_their_ids(self):
        b = library.builtins()
        self.assertEqual(list(b), ["morning", "evening", "midweek", "weekly"])
        self.assertEqual(library.builtin_ids(), tuple(b))  # the one list of them: read from the files
        self.assertEqual(b["evening"].closes, "day")
        self.assertEqual(b["weekly"].schedule, (library.Slot("20:00", 6),))
        self.assertEqual(b["midweek"].opener["limit"], 900)

    def test_user_file_round_trip_keeps_meaning_not_formatting(self):
        loose = ("---\r\nkind: practice\r\nid: gratitude\r\ntitle: \"Daily gratitude\"\r\nschedule.time: 21:30\r\n"
                 "annotate: false\r\n---\r\n\r\nSome prose the parser ignores.\r\n\r\n## Three things\r\n"
                 "* Three things today, and why each one mattered.\r\n\r\n## Who\r\n- Who made one of them possible?   \r\n")
        p = library.parse(loose, Path("/tmp/x.md"))
        self.assertEqual(p.title, "Daily gratitude")
        self.assertFalse(p.annotate)
        self.assertEqual([q.id for q in p.questions], ["1.1", "2.1"])
        self.assertEqual(p.questions[1].text, "Who made one of them possible?")
        canonical = library.render(p)
        self.assertIn("## 1. Three things\n* Three".replace("* ", "- "), canonical)
        self.assertEqual(library.parse(canonical, p.path), p)
        self.assertEqual(library.render(library.parse(canonical)), canonical)

    def test_front_matter_edges_read_as_written(self):
        # A closing fence on the last line (no newline) is still a closing fence: only the body is missing.
        ended = "---\nkind: practice\nid: gratitude\ntitle: T\n---"
        self.assertEqual(library._parse(ended, None, False)[1],
                         ["no questions: the body needs at least one `- ` line"])
        # A byte-order mark (some editors write one) is not part of the first line.
        p = library.parse("﻿" + GRATITUDE)
        self.assertEqual((p.id, p.title, len(p.questions)), ("gratitude", "Daily gratitude", 2))

    def test_questions_above_the_first_heading_are_a_problem(self):
        # A sectioned bank has no untitled section: it would print as "1. " and come back titled "1.".
        mixed = GRATITUDE.replace("- Who made", "## Who\n- Who made")
        self.assertEqual(library._parse(mixed, None, False)[1],
                         ["1 question above the first `## ` heading: move it under a heading, "
                          "or remove the headings for a flat list"])
        flat_then_prose = GRATITUDE + "\nSome closing prose.\n"
        self.assertEqual(library._parse(flat_then_prose, None, False)[1], [])

    def test_three_field_practice_gets_every_default(self):
        p = library.parse(GRATITUDE)
        self.assertEqual((p.id, p.kind, p.title), ("gratitude", "practice", "Daily gratitude"))
        self.assertTrue(p.flat)
        self.assertEqual(p.items(), {"1": "Three things today, and why each one mattered.",
                                     "2": "Who made one of them possible?"})
        self.assertEqual(p.bank_lines(), ["1. Three things today, and why each one mattered.",
                                          "2. Who made one of them possible?"])
        self.assertEqual(p.schedule, (library.Slot("21:30", None),))
        self.assertEqual((p.emoji, p.collapse_hours, p.annotate, p.closes), ("", 6.0, True, None))
        self.assertEqual(p.opener, {"mode": "companion", "shape": "a short opener (max ~900 characters)",
                                    "limit": 1100, "plain": "{title}. A sentence or two is enough."})
        self.assertEqual((p.companion, p.group, p.enabled, p.builtin), ("", "", True, False))
        self.assertEqual(library.parse(library.render(p)), p)

    def test_several_slots(self):
        text = GRATITUDE.replace("schedule.time: 21:30\n",
                                 "schedule.1.time: 08:00\nschedule.2.time: 20:30\nschedule.2.weekday: 6\n")
        p = library.parse(text)
        self.assertEqual(p.schedule, (library.Slot("08:00"), library.Slot("20:30", 6)))
        self.assertIn("schedule.1.time: 08:00\nschedule.2.time: 20:30\nschedule.2.weekday: 6\n", library.render(p))
        self.assertEqual(library.parse(library.render(p)), p)
        both = text.replace("schedule.1.time", "schedule.time")
        self.assertIn("not both", "; ".join(library._parse(both, None, False)[1]))
        none = GRATITUDE.replace("schedule.time: 21:30\n", "")
        self.assertEqual(library.parse(none).schedule, ())

    def test_pin_survives_a_reorder(self):
        evening = library.builtins()["evening"]
        self.assertEqual(evening.items()["9.2"], "What simple joys touched you today?")
        text = evening.raw.replace(
            "## 9. Gratitude\n- What moments from today are you thankful for?\n- What simple joys touched you today?",
            "## 9. Gratitude\n- What simple joys touched you today? {#9.2}\n- What moments from today are you thankful for?")
        p = library.parse(text)
        self.assertEqual(p.items()["9.2"], "What simple joys touched you today?")
        self.assertEqual(p.items()["9.1"], "What moments from today are you thankful for?")
        self.assertEqual([q.id for q in p.sections[8][1]], ["9.2", "9.1"])
        self.assertEqual(p.items()["10.1"], "What brought you peace today?")  # later sections unaffected
        self.assertIn("- What simple joys touched you today? {#9.2}\n", library.render(p))
        self.assertEqual(library.parse(library.render(p)), p)
        self.assertIn("   [9.2] What simple joys touched you today?\n   [9.1] What moments", p.bank_with_ids())

    def test_a_number_in_a_section_title_is_kept(self):
        text = GRATITUDE.replace("- Three", "## 3.5 hours of sleep\n- Three").replace("- Who made", "## 2. Who\n- Who made")
        p = library.parse(text)
        self.assertEqual([t for t, _ in p.sections], ["3.5 hours of sleep", "Who"])  # "2. " is numbering, "3.5" is not
        self.assertIn("## 1. 3.5 hours of sleep\n", library.render(p))
        self.assertEqual(library.parse(library.render(p)), p)

    def test_values_in_quotes_survive_the_round_trip(self):
        # entries.front_matter strips one layer of matching quotes, so render adds one where a value has them.
        p = library.parse(GRATITUDE.replace("title: Daily gratitude", "title: \"\"Thanks\"\"")
                          .replace("schedule.time: 21:30", "schedule.time: 21:30\ncompanion: 'gentle'"))
        self.assertEqual((p.title, p.companion), ('"Thanks"', "gentle"))
        self.assertEqual(library.parse(library.render(p)), p)
        p = library.parse(GRATITUDE.replace("title: Daily gratitude", "title: 'Tis the season'"))
        self.assertEqual(p.title, "Tis the season")  # one layer, as every entry's front matter is read
        self.assertEqual(library.parse(library.render(p)), p)

    def test_plain_opener_placeholders(self):
        p = library.parse(GRATITUDE)
        self.assertEqual(p.plain_opener(NEUTRAL), "Daily gratitude. A sentence or two is enough.")
        p = library.parse(GRATITUDE.replace("schedule.time: 21:30",
                                            "emoji: 🙏\nopener.plain: {greet:Hello} {title} for {user} {emoji} {x y}"))
        self.assertEqual(p.plain_opener(NEUTRAL), "Hello, Sam 🙏 Daily gratitude for Sam 🙏 {x y}")  # its own emoji
        self.assertEqual(p.plain_opener(Names(user="", emoji="")), "Hello 🙏 Daily gratitude for  🙏 {x y}")
        plain = library.parse(GRATITUDE.replace("schedule.time: 21:30", "opener.plain: {greet:Hi} Go."))
        self.assertEqual(plain.plain_opener(Names(user="", emoji="")), "Hi. Go.")

    def test_pins_in_a_flat_list_and_free_form_ids(self):
        p = library.parse(GRATITUDE.replace("- Who made", "- Who {#who}\n- Who made"))
        self.assertEqual([q.id for q in p.questions], ["1", "who", "2"])
        p = library.parse(GRATITUDE.replace("- Three", "- New first {#1}\n- Three"))
        self.assertEqual([q.id for q in p.questions], ["1", "2", "3"])  # positional skips the pinned 1

    def test_problems_are_named(self):
        def problems(text):
            return library._parse(text, None, False)[1]
        self.assertEqual(problems("just text"), ["no front matter: the file must start with a `---` block holding kind, id and title"])
        msgs = problems("---\nkind: program\nid: Bad Id\nschedule.time: 25:00\nschedule.weekday: 9\nbogus: 1\n"
                        "annotate: maybe\ncollapse_hours: -2\nopener.limit: many\nopener.mode: shout\ncloses: year\n"
                        "opener.plain: Hi {greet} {nope}\n---\n")
        joined = "\n".join(msgs)
        for want in ("title is required", "kind must be practice", "id must be 2 to 31", "schedule.time must be HH:MM",
                     "unknown key 'bogus'", "annotate must be true or false", "collapse_hours must be a positive",
                     "opener.limit must be a positive whole number", "opener.mode must be one of",
                     "closes must be one of day, week", "unknown placeholder(s) {nope}", "no questions"):
            self.assertIn(want, joined)
        self.assertNotIn("{greet}", joined.split("unknown placeholder")[1].split(";")[0])
        self.assertIn("{greet} needs a word, like {greet:Evening}", joined)
        self.assertIn("weekday must be 0 (Mon) to 6 (Sun)", "\n".join(problems(
            GRATITUDE.replace("schedule.time: 21:30", "schedule.time: 21:30\nschedule.weekday: 7"))))
        self.assertIn("pinned more than once", "\n".join(problems(GRATITUDE.replace("mattered.", "mattered. {#a}")
                                                                  .replace("possible?", "possible? {#a}"))))
        with self.assertRaises(library.ParseError) as e:
            library.parse(GRATITUDE.replace("title: Daily gratitude\n", ""))
        self.assertEqual(str(e.exception), "title is required")

    def test_only_ascii_digits_are_numbers(self):
        # str.isdigit() is True for "²" (int() refuses it: a traceback) and "١" (int() reads it as 1), and `\d`
        # matches "٦": each would be a crash or a value the tick's HH:MM comparison never meets. A stray character
        # is a named problem instead, like any other bad value.
        def problems(extra, text=GRATITUDE):
            return library._parse(text.replace("schedule.time: 21:30\n", f"schedule.time: 21:30\n{extra}\n"),
                                  None, False)[1]
        for digit in ("²", "١"):
            with self.subTest(digit):
                self.assertEqual(problems(f"opener.limit: {digit}"),
                                 [f"opener.limit must be a positive whole number (got {digit!r})"])
                self.assertEqual(problems(f"schedule.weekday: {digit}"),
                                 [f"schedule.weekday must be 0 (Mon) to 6 (Sun) (got {digit!r})"])
                with self.assertRaises(library.ParseError):
                    library.parse(GRATITUDE.replace("21:30", f"21:30\nopener.limit: {digit}"))
        self.assertEqual(problems("", GRATITUDE.replace("21:30", "0٦:00")), ["schedule.time must be HH:MM (got '0٦:00')"])
        self.assertEqual(problems("opener.limit: " + "9" * 5000)[0][:44], "opener.limit must be a positive whole number")
        for value in ("1e12", "8761", "1e300"):   # a window timedelta cannot hold would stop every capture and fold
            with self.subTest(value):
                self.assertEqual(problems(f"collapse_hours: {value}"),
                                 [f"collapse_hours must be a positive number of at most {library.MAX_COLLAPSE_HOURS:g} "
                                  f"hours (got {value!r})"])
        self.assertEqual(problems("collapse_hours: 8760"), [])
        for value in ("inf", "nan", "٦", "1e400"):
            with self.subTest(value):  # Infinity would reach the PWA as JSON it cannot parse
                self.assertEqual(problems(f"collapse_hours: {value}"),
                                 [f"collapse_hours must be a positive number of at most "
                                  f"{library.MAX_COLLAPSE_HOURS:g} hours (got {value!r})"])
        slots = GRATITUDE.replace("schedule.time: 21:30\n", "schedule.١.time: 10:00\n")
        self.assertEqual(library._parse(slots, None, False)[1], ["key 'schedule.١.time' is not a valid name (lowercase, digits, _ and .)"])
        p = library.parse(GRATITUDE.replace("- Three", "## ٣. Things\n- Three").replace("- Who", "## Who\n- Who"))
        self.assertEqual(p.sections[0][0], "٣. Things")  # only an ASCII `N. ` is a heading number
        self.assertEqual(library.parse(library.render(p)), p)

    def test_slot_numbers_are_written_one_way(self):
        # schedule.1 and schedule.01 would merge into one slot (last value wins) without a word.
        two = "schedule.1.time: 10:00\nschedule.01.time: 11:00\n"
        msgs = library._parse(GRATITUDE.replace("schedule.time: 21:30\n", two), None, False)[1]
        self.assertEqual(msgs, ["schedule.01: a slot number has no leading zero and at most 3 digits "
                                "(like schedule.2.time)"])
        huge = f"schedule.{'9' * 5000}.time: 10:00\n"
        self.assertEqual(len(library._parse(GRATITUDE.replace("schedule.time: 21:30\n", huge), None, False)[1]), 1)
        ok = library.parse(GRATITUDE.replace("schedule.time: 21:30\n", "schedule.0.time: 10:00\nschedule.10.time: 11:00\n"))
        self.assertEqual(ok.schedule, (library.Slot("10:00"), library.Slot("11:00")))

    def test_a_repeated_front_matter_key_is_a_problem(self):
        # The header reader keeps the last value; two titles (or two times) mean the file says two things.
        msgs = library._parse(GRATITUDE.replace("schedule.time: 21:30\n", "schedule.time: 21:30\ntitle: Other\n"
                                                "schedule.time: 22:00\n"), None, False)[1]
        self.assertEqual(msgs, ["key 'title' is given more than once; keep one",
                                "key 'schedule.time' is given more than once; keep one"])

    def test_lines_that_look_like_questions_are_not_dropped(self):
        # Prose is ignored, but a line that starts like a list item and is not a `- ` question would shorten the
        # bank without a word, so it is named.
        def problems(body):
            return library._parse(GRATITUDE.split("---\n- ")[0] + "---\n" + body, None, False)[1]
        self.assertEqual(problems("- a\n  - b sub\n1. numbered\n+ plus\n-dash\n•dot\n2) paren\n"), [
            "line 8: '  - b sub' looks like a question but is not one: write it as `- question` at the line's start",
            "line 9: '1. numbered' looks like a question but is not one: write it as `- question` at the line's start",
            "line 10: '+ plus' looks like a question but is not one: write it as `- question` at the line's start",
            "line 11: '-dash' looks like a question but is not one: write it as `- question` at the line's start",
            "line 12: '•dot' looks like a question but is not one: write it as `- question` at the line's start",
            "line 13: '2) paren' looks like a question but is not one: write it as `- question` at the line's start"])
        # Prose that only resembles a list stays prose: a rule, emphasis, a quote, a number mid-sentence.
        self.assertEqual(problems("Some prose.\n---\n**Bold** words and *emphasis*.\n> - quoted\n"
                                  "   continued prose\n2026 was long.\n- a\n"), [])
        self.assertEqual(problems("- a\n- {#b}\n"), ["line 8: question {#b} has a pin but no text"])

    def test_a_front_matter_line_without_a_colon_is_a_problem(self):
        # The header reader skips such a line, so `enabled false` left a fork enabled and validate said ok.
        msgs = library._parse(GRATITUDE.replace("schedule.time: 21:30\n", "schedule.time: 21:30\nenabled false\n\n"
                                                "closes day\n"), None, False)[1]
        self.assertEqual(msgs, ["line 6: 'enabled false' is not a `key: value` line",
                                "line 8: 'closes day' is not a `key: value` line"])

    def test_values_with_edge_whitespace_round_trip(self):
        # `" Daily "` was rendered unquoted, and the reader strips the spaces it then saw.
        p = library.parse(GRATITUDE.replace("title: Daily gratitude", 'title: " Daily "')
                          .replace("schedule.time: 21:30", 'schedule.time: 21:30\ncompanion: "  "'))
        self.assertEqual((p.title, p.companion), (" Daily ", "  "))
        self.assertEqual(library.parse(library.render(p)), p)

    def test_a_loaded_practice_is_immutable_and_hashable(self):
        # Practices are shared by the library cache: a caller must not be able to change one for everyone.
        fresh = library.parse(library.builtins()["evening"].raw, builtin=True)  # not the cached object
        with self.assertRaises(TypeError):
            fresh.opener["limit"] = 2000
        self.assertEqual(fresh.opener["limit"], 1400)
        self.assertEqual(len({library.builtins()["evening"], fresh}), 1)
        lib = library.Library(practices={"evening": fresh}, builtins={}, practices_dir=Path("/x"))
        self.assertEqual(lib.to_dict(fresh)["opener"]["limit"], 1400)  # still plain JSON

    def test_a_practice_copies_pickles_and_serialises_like_a_dataclass(self):
        # A read-only opener must not cost what a frozen dataclass invites: copy, pickle, asdict, json.
        import copy
        import dataclasses
        import json
        import pickle
        p = library.builtins()["evening"]
        self.assertEqual(copy.deepcopy(p), p)
        self.assertEqual(pickle.loads(pickle.dumps(p)), p)
        self.assertEqual(dataclasses.asdict(p)["opener"]["limit"], 1400)
        self.assertEqual(json.loads(json.dumps(p.opener))["mode"], "companion")
        with self.assertRaises(TypeError):
            copy.deepcopy(p).opener.update(limit=1)  # a copy is as read-only as the original

    def test_a_section_with_no_questions_is_a_problem(self):
        body = GRATITUDE.split("---\n- ")[0] + "---\n## A\n- a\n## B\nonly prose here\n## C\n- c\n"
        self.assertEqual(library._parse(body, None, False)[1],
                         ["section 'B' has no questions: add `- ` lines under it or remove the heading"])


class LoadTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), NEUTRAL)
        self.dir = self.cfg.practices_dir
        self.dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def write(self, name: str, text: str) -> Path:
        p = self.dir / name
        p.write_text(text, encoding="utf-8")
        return p

    def test_default_dir_and_builtins_only(self):
        self.assertEqual(self.cfg.practices_dir, self.cfg.state_dir / "practices")
        lib = library.load(self.cfg)
        self.assertEqual(list(lib.practices), ["morning", "evening", "midweek", "weekly"])
        self.assertEqual(lib.problems, [])
        self.assertEqual([p.id for p in library.scheduled(self.cfg)], ["morning", "evening", "midweek", "weekly"])
        self.assertTrue(library.get(self.cfg, "evening").builtin)
        with self.assertRaises(library.UnknownDefinition):
            library.get(self.cfg, "lunch")

    def test_user_practice_is_added_and_ordered_by_its_slot(self):
        self.write("gratitude.md", GRATITUDE)
        lib = library.load(self.cfg)
        self.assertEqual(list(lib.practices), ["morning", "gratitude", "evening", "midweek", "weekly"])
        self.assertEqual(lib.source(lib.get("gratitude")), "user")
        self.assertEqual(lib.source(lib.get("evening")), "builtin")
        self.assertEqual(lib.user_files(), [self.dir / "gratitude.md"])
        self.write("someday.md", GRATITUDE.replace("id: gratitude", "id: someday").replace("schedule.time: 21:30\n", ""))
        self.assertEqual(list(library.load(self.cfg).practices)[-1], "someday")  # unscheduled last
        self.assertNotIn("someday", [p.id for p in library.scheduled(self.cfg)])

    def test_override_replaces_the_whole_built_in(self):
        self.write("evening.md", "---\nkind: practice\nid: evening\ntitle: Short evening\n---\n- One thing from today.\n")
        lib = library.load(self.cfg)
        p = lib.get("evening")
        self.assertFalse(p.builtin)
        self.assertEqual((p.title, p.items(), p.schedule, p.closes), ("Short evening", {"1": "One thing from today."}, (), None))
        self.assertNotIn("evening", [x.id for x in lib.scheduled()])  # no slot in the file: on demand only
        self.assertEqual(lib.problems, [])
        self.assertTrue(lib.builtins["evening"].builtin)  # the built-in itself is untouched
        self.assertEqual(library.builtins()["evening"].items()["9.2"], "What simple joys touched you today?")

    def test_enabled_false_hides_a_built_in(self):
        self.write("midweek.md", library.render(library.builtins()["midweek"]).replace("---\n## 1.", "enabled: false\n---\n## 1."))
        lib = library.load(self.cfg)
        self.assertFalse(lib.get("midweek").enabled)
        self.assertEqual([p.id for p in lib.scheduled()], ["morning", "evening", "weekly"])
        self.assertEqual(lib.problems, [])

    def test_broken_user_file_is_a_problem_and_the_built_in_is_used(self):
        self.write("evening.md", "---\nkind: practice\nid: evening\n---\nno title, no questions\n")
        lib = library.load(self.cfg)
        self.assertTrue(lib.get("evening").builtin)
        self.assertEqual(len(lib.problems), 1)
        self.assertIn(f"{self.dir / 'evening.md'}: title is required; no questions", lib.problems[0])
        self.assertTrue(lib.problems[0].endswith("(built-in 'evening' is used)"))
        self.write("broken.md", "not a practice\n")
        msgs = library.problems(self.cfg)
        self.assertEqual(len(msgs), 2)
        self.assertFalse(any("built-in" in m for m in msgs if "broken.md" in m))

    def test_a_file_the_parser_chokes_on_never_takes_the_library_down(self):
        # "²" once raised ValueError out of load(): the CLI, doctor and (from WP2) every send would have died.
        self.write("evening.md", GRATITUDE.replace("id: gratitude", "id: evening").replace("21:30", "21:30\nopener.limit: ²"))
        lib = library.load(self.cfg)
        self.assertTrue(lib.get("evening").builtin)
        self.assertEqual(lib.problems, [f"{self.dir / 'evening.md'}: opener.limit must be a positive whole number "
                                        "(got '²') (built-in 'evening' is used)"])
        # Whatever a parser fault raises in future is that file's problem, not the library's.
        real = library._parse
        from unittest import mock

        def faulty(text, path, builtin):
            if path is not None and path.name == "gratitude.md":
                raise IndexError("boom")
            return real(text, path, builtin)
        path = self.write("gratitude.md", GRATITUDE)
        with mock.patch.object(library, "_parse", faulty):
            lib = library.load(self.cfg)
            self.assertNotIn("gratitude", lib.practices)
            self.assertIn(f"{path}: cannot parse (IndexError: boom)", lib.problems)
            self.assertEqual(library.validate_file(path, self.cfg), ["cannot parse (IndexError: boom)"])
            with self.assertRaises(library.ParseError):
                library.parse(GRATITUDE, path)

    def test_reserved_ids_do_not_depend_on_what_the_process_has_imported(self):
        # The gateway has Hermes' live command registry loaded and the cron tick does not; both must resolve the
        # same library, so only the dated table in config counts.
        import sys
        import types
        from unittest import mock
        self.write("gratitude.md", GRATITUDE)
        self.cfg.conversations["opt_out_prefix"] = "/my_off"
        before = library.reserved_ids(self.cfg)
        self.assertIn("my-off", before)  # the configured opt-out name, as an id would spell it
        fake = types.ModuleType("hermes_cli.commands")
        fake.COMMAND_REGISTRY = [types.SimpleNamespace(name="gratitude", aliases=("my_off",))]
        with mock.patch.dict(sys.modules, {"hermes_cli.commands": fake}):
            self.assertEqual(library.reserved_ids(self.cfg), before)
            lib = library.load(self.cfg)
            self.assertIn("gratitude", lib.practices)
            self.assertEqual(lib.problems, [])

    def test_a_dangling_link_is_named_and_does_not_freeze_reloads(self):
        # One broken link made the whole reload stamp empty, so later edits were never seen by a running process.
        path = self.write("gratitude.md", GRATITUDE)
        (self.dir / "gone.md").symlink_to(self.dir / "nowhere.md")
        first = library.load(self.cfg)
        self.assertEqual(first.problems, [f"{self.dir / 'gone.md'}: a link to a file that does not exist"])
        path.write_text(GRATITUDE.replace("Daily gratitude", "Gratitude, changed"), encoding="utf-8")
        self.assertEqual(library.load(self.cfg).get("gratitude").title, "Gratitude, changed")

    def test_an_entry_that_cannot_be_stat_ed_is_a_problem_not_a_crash(self):
        # A directory he can list but not enter: Path.is_file() re-raises EACCES on Python 3.11-3.13 (load crashed)
        # and swallows it on 3.14 (the file vanished without a word).
        if os.geteuid() == 0:
            self.skipTest("root ignores the directory mode")
        self.write("gratitude.md", GRATITUDE)
        self.dir.chmod(0o600)
        try:
            lib = library.load(self.cfg)
        finally:
            self.dir.chmod(0o700)
        self.assertEqual(list(lib.practices), ["morning", "evening", "midweek", "weekly"])
        self.assertEqual(lib.problems, [f"{self.dir / 'gratitude.md'}: cannot read: PermissionError"])

    def test_duplicate_ids_across_files(self):
        self.write("a.md", GRATITUDE)
        self.write("b.md", GRATITUDE.replace("Daily gratitude", "Another"))
        lib = library.load(self.cfg)
        self.assertEqual(lib.get("gratitude").title, "Daily gratitude")  # first by file name
        self.assertEqual(len(lib.problems), 1)
        self.assertIn("b.md: id 'gratitude' is already defined by", lib.problems[0])
        self.assertIn("a.md; that one is used", lib.problems[0])

    def test_reserved_ids(self):
        reserved = library.reserved_ids(self.cfg)
        for pid in ("j", "journal", "note", "reflection", "week", "start", "next", "skip", "back", "pause",
                    "status", "x", "fork", "reload-mcp"):
            self.assertIn(pid, reserved, pid)
        self.assertNotIn("evening", reserved)
        self.cfg.conversations["opt_out_prefix"] = "/off"
        self.assertIn("off", library.reserved_ids(self.cfg))
        self.write("status.md", GRATITUDE.replace("id: gratitude", "id: status"))
        lib = library.load(self.cfg)
        self.assertNotIn("status", lib.practices)
        self.assertEqual(len(lib.problems), 1)
        self.assertIn("id 'status' is reserved", lib.problems[0])

    def test_a_suffix_clash_is_a_problem(self):
        self.write("level.md", GRATITUDE.replace("id: gratitude", "id: level"))
        self.write("level-10.md", GRATITUDE.replace("id: gratitude", "id: level-10"))
        msgs = library.problems(self.cfg)
        self.assertEqual(len(msgs), 1)
        self.assertIn("'level' and 'level-10' are both defined", msgs[0])

    def test_reload_on_change_and_cache_when_unchanged(self):
        path = self.write("gratitude.md", GRATITUDE)
        first = library.load(self.cfg)
        self.assertIs(library.load(self.cfg), first)
        path.write_text(GRATITUDE.replace("Daily gratitude", "Gratitude, longer title"), encoding="utf-8")
        second = library.load(self.cfg)
        self.assertIsNot(second, first)
        self.assertEqual(second.get("gratitude").title, "Gratitude, longer title")
        path.unlink()
        self.assertNotIn("gratitude", library.load(self.cfg).practices)
        (self.dir / ".evening.md.tmp123").write_text("garbage")  # an in-flight atomic write is never read
        self.assertEqual(library.problems(self.cfg), [])

    def test_missing_dir_is_fine(self):
        self.dir.rmdir()
        lib = library.load(self.cfg)
        self.assertEqual(lib.problems, [])
        self.assertEqual(len(lib.practices), 4)

    def test_validate_file(self):
        good = self.write("gratitude.md", GRATITUDE)
        self.assertEqual(library.validate_file(good, self.cfg), [])
        bad = self.write("bad.md", GRATITUDE.replace("schedule.time: 21:30", "schedule.time: 9pm"))
        self.assertEqual(library.validate_file(bad, self.cfg), ["schedule.time must be HH:MM (got '9pm')"])
        res = self.write("skip.md", GRATITUDE.replace("id: gratitude", "id: skip"))
        self.assertEqual(library.validate_file(res, self.cfg), ["id 'skip' is reserved (a command or an entry kind)"])
        short = self.write("x.md", GRATITUDE.replace("id: gratitude", "id: x"))
        self.assertEqual(library.validate_file(short, self.cfg),
                         ["id must be 2 to 31 lowercase letters, digits or hyphens, starting with a letter (got 'x')"])
        self.assertEqual(library.validate_file(self.dir / "missing.md", self.cfg)[0][:12], "cannot read:")

    def test_fork_copies_the_built_in_once(self):
        dest = library.fork(self.cfg, "evening")
        self.assertEqual(dest, self.dir / "evening.md")
        self.assertEqual(dest.read_text(encoding="utf-8"), library.builtins()["evening"].raw)
        self.assertEqual(oct(dest.stat().st_mode & 0o777), "0o644")
        lib = library.load(self.cfg)
        self.assertEqual(lib.source(lib.get("evening")), "fork")
        self.assertEqual(lib.get("evening").items(), library.builtins()["evening"].items())
        with self.assertRaises(FileExistsError):
            library.fork(self.cfg, "evening")
        with self.assertRaises(library.UnknownDefinition):
            library.fork(self.cfg, "gratitude")
        self.assertTrue((self.dir / library.LOCK_NAME).exists())
        # beside the copy, the directory's state file keeps the shipped text it began from (BUILTIN-EDIT)
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [".library", ".library.lock", "evening.md"])
        self.assertEqual(library.edit_status(self.cfg, "evening")["default_changed"], False)

    def test_fork_creates_the_directory(self):
        self.dir.rmdir()
        library.fork(self.cfg, "morning")
        self.assertTrue((self.dir / "morning.md").is_file())


class BuiltinEditTest(CleanEnv):
    """BUILTIN-EDIT (Theo, 2026-10-26): a built-in of any kind is edited in place. His version is a definition of
    the same id in ``practices_dir``; the packaged files are never written; the shipped text his edit began from is
    kept beside it, so a later release's change to the default is said and shown; reset puts the shipped one back."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), NEUTRAL)
        self.dir = self.cfg.practices_dir
        self.dir.mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    write = LoadTest.write

    def packaged(self):
        return {f: f.read_bytes() for d in (library.BUILTIN_DIR, library.BUILTIN_PROGRAMS, library.BUILTIN_SITUATIONAL)
                for f in sorted(d.rglob("*.md"))}

    def test_shipped_names_every_kind_and_nothing_else(self):
        self.assertEqual(library.shipped("evening").kind, "practice")
        self.assertEqual(list(library.shipped("evening").files), ["evening.md"])
        fix = library.shipped("fix-life")
        self.assertEqual(fix.kind, "program")
        self.assertIn("program.md", fix.files)
        self.assertEqual(len([n for n in fix.files if n.startswith("sessions/")]), 10)
        self.assertEqual(library.shipped("conversation-prep").kind, "situational")
        self.assertIsNone(library.shipped("gratitude"))
        self.assertIsNone(library.shipped("prep"))                      # an alias is not an id
        self.assertEqual(library.shipped("evening").sha, library.shipped("evening").sha)

    def test_fork_and_reset_of_every_kind_leave_the_package_alone(self):
        before = self.packaged()
        for pid, made in (("evening", self.dir / "evening.md"), ("fix-life", self.dir / "fix-life"),
                          ("conversation-prep", self.dir / "conversation-prep.md")):
            with self.subTest(pid):
                self.assertEqual(library.fork(self.cfg, pid), made)
                self.assertEqual(library.his_path(self.cfg, pid), made)
                self.assertEqual(library.edit_status(self.cfg, pid)["default_changed"], False)
                self.assertEqual(library.default_diff(self.cfg, pid), {"since": None, "yours": ""})
                with self.assertRaises(library.AlreadyForked):
                    library.fork(self.cfg, pid)
        lib = library.load(self.cfg)
        self.assertEqual(lib.source(lib.get("evening")), "fork")
        self.assertFalse(lib.programs["fix-life"].builtin)
        self.assertFalse(lib.program("prep").builtin)                   # same /start word
        self.assertEqual(oct((self.dir / "fix-life").stat().st_mode & 0o777), "0o755")
        self.assertEqual(oct((self.dir / "fix-life" / "program.md").stat().st_mode & 0o777), "0o644")
        for pid in ("evening", "fix-life", "conversation-prep"):
            with self.subTest(pid):
                self.assertIsNotNone(library.reset(self.cfg, pid))
                self.assertIsNone(library.edit_status(self.cfg, pid))
                self.assertIsNone(library.reset(self.cfg, pid))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), [".library", ".library.lock"])
        self.assertEqual(ledger.read_state(self.dir / ".library"), {})
        lib = library.load(self.cfg)
        self.assertTrue(lib.get("evening").builtin and lib.programs["fix-life"].builtin)
        self.assertEqual(self.packaged(), before)
        with self.assertRaises(library.UnknownDefinition):
            library.reset(self.cfg, "gratitude")
        with self.assertRaises(ValueError):
            library.fork(self.cfg, "fix-life", schedule=())

    def test_reset_keeps_what_else_he_put_in_a_program_folder(self):
        library.fork(self.cfg, "fix-life")
        (self.dir / "fix-life" / "notes.txt").write_text("mine", encoding="utf-8")
        library.reset(self.cfg, "fix-life")
        self.assertEqual(sorted(p.name for p in (self.dir / "fix-life").iterdir()), ["notes.txt"])
        self.assertTrue(library.load(self.cfg).programs["fix-life"].builtin)

    def test_a_save_over_a_built_in_remembers_the_default_and_a_new_practice_does_not(self):
        text, _ = library.write_text(replace(library.builtins()["weekly"], title="Sunday scan", builtin=False))
        library.write(self.cfg, "weekly", text, None)
        status = library.edit_status(self.cfg, "weekly")
        self.assertEqual(status["default_changed"], False)
        self.assertRegex(status["edited_at"], r"^\d{4}-\d\d-\d\dT")
        self.assertIn("+title: Sunday scan", library.default_diff(self.cfg, "weekly")["yours"])
        library.write(self.cfg, "gratitude", GRATITUDE, None)
        self.assertEqual(list(ledger.read_state(self.dir / ".library")["defaults"]), ["weekly"])
        library.remove(self.cfg, "weekly")
        self.assertNotIn("defaults", ledger.read_state(self.dir / ".library"))

    def test_a_release_that_changes_the_default_keeps_his_version_and_says_so(self):
        library.fork(self.cfg, "midweek")
        his = (self.dir / "midweek.md").read_text(encoding="utf-8")
        with library.held(self.dir) as state:          # as if the default he began from was an older one
            rec = state["defaults"]["midweek"]
            rec["files"]["midweek.md"] = rec["files"]["midweek.md"].replace("title: Midweek check-in", "title: Midweek")
            rec["sha"] = library._fingerprint(rec["files"])
        self.assertEqual(library.edit_status(self.cfg, "midweek")["default_changed"], True)
        diff = library.default_diff(self.cfg, "midweek")
        self.assertIn("--- default when you edited/midweek.md", diff["since"])
        self.assertIn("-title: Midweek\n+title: Midweek check-in", diff["since"])
        self.assertEqual((self.dir / "midweek.md").read_text(encoding="utf-8"), his)
        lib = library.load(self.cfg)
        self.assertEqual(lib.source(lib.get("midweek")), "fork")

    def test_a_record_that_is_missing_or_damaged_reads_as_not_known(self):
        self.write("evening.md", library.builtins()["evening"].raw)
        self.assertEqual(library.edit_status(self.cfg, "evening"), {"default_changed": None, "edited_at": None})
        self.assertIsNone(library.default_diff(self.cfg, "evening")["since"])
        (self.dir / ".library").write_text('{"defaults": {"evening": {"sha": 1, "files": []}}}', encoding="utf-8")
        self.assertEqual(library.edit_status(self.cfg, "evening")["default_changed"], None)
        (self.dir / ".library").write_text("not json", encoding="utf-8")
        self.assertEqual(library.edit_status(self.cfg, "evening")["default_changed"], None)
        library.reset(self.cfg, "evening")                            # a damaged state file does not stop a reset
        self.assertFalse((self.dir / "evening.md").exists())

    def test_a_file_at_a_built_ins_name_that_defines_another_id_is_not_an_edit_of_it(self):
        """Review of BUILTIN-EDIT: a fork renamed by hand (``evening.md`` now saying ``id: night-notes``) is a practice of
        his own; resetting the built-in must not delete it."""
        mine = self.write("evening.md", GRATITUDE.replace("id: gratitude", "id: night-notes"))
        folder = self.dir / "fix-life"
        library.fork(self.cfg, "fix-life")
        program = folder / "program.md"
        program.write_text(program.read_text(encoding="utf-8").replace("id: fix-life", "id: my-life"), encoding="utf-8")
        library.reset(self.cfg, "evening")            # (fork recorded fix-life; these resets must leave both alone)
        for pid in ("evening", "fix-life"):
            with self.subTest(pid):
                self.assertIsNone(library.his_path(self.cfg, pid))
                self.assertIsNone(library.edit_status(self.cfg, pid))
                self.assertIsNone(library.reset(self.cfg, pid))
        self.assertTrue(mine.is_file())
        self.assertTrue(program.is_file() and len(list((folder / "sessions").glob("*.md"))) == 10)
        lib = library.load(self.cfg)
        self.assertIn("night-notes", lib.practices)
        self.assertIn("my-life", lib.programs)
        # the same with a byte-order mark, which the parser skips (re-review of BUILTIN-EDIT)
        mine.write_bytes("\ufeff".encode("utf-8") + mine.read_bytes())
        self.assertIn("night-notes", library.load(self.cfg).practices)
        self.assertIsNone(library.his_path(self.cfg, "evening"))
        self.assertIsNone(library.reset(self.cfg, "evening"))
        self.assertTrue(mine.is_file())
        # and an edit of the built-in cannot be made over it: the answer names the other id, not "delete it"
        text, _ = library.write_text(replace(library.builtins()["evening"], builtin=False))
        with self.assertRaises(FileExistsError) as caught:
            library.write(self.cfg, "evening", text, None)
        self.assertIn("holds the definition 'night-notes'; rename it", str(caught.exception))
        with self.assertRaises(FileExistsError) as caught:
            library.save_draft(self.cfg, library.draft(self.cfg, "fix-life", "program.md",
                                                       library.shipped("fix-life").files["program.md"]), None)
        self.assertIn("holds the definition 'my-life'; rename it", str(caught.exception))

    def test_a_reset_keeps_a_session_he_is_drafting(self):
        library.fork(self.cfg, "fix-life")
        draft = self.dir / "fix-life" / "sessions" / "11-my-own.md"
        draft.write_text("---\nkind: session\n---\n", encoding="utf-8")
        library.reset(self.cfg, "fix-life")
        self.assertEqual([p.name for p in (self.dir / "fix-life" / "sessions").iterdir()], ["11-my-own.md"])
        self.assertFalse((self.dir / "fix-life" / "program.md").exists())

    def test_the_record_of_an_edit_deleted_by_hand_is_dropped_at_the_next_write(self):
        library.fork(self.cfg, "evening")
        with library.held(self.dir) as state:          # an old default, so a stale record would say "changed"
            state["defaults"]["evening"]["sha"] = "old"
        (self.dir / "evening.md").unlink()             # by hand
        library.write(self.cfg, "gratitude", GRATITUDE, None)   # any write from the app or the CLI
        self.write("evening.md", library.builtins()["evening"].raw)
        self.assertEqual(library.edit_status(self.cfg, "evening"), {"default_changed": None, "edited_at": None})

    def test_draft_problems_name_his_files_not_the_scratch_copy(self):
        program = library.shipped("fix-life").files["program.md"]
        bad = library.draft(self.cfg, "fix-life", "program.md", program.replace("10-one-day-reset", "10-missing"))
        self.assertTrue(bad.problems)
        self.assertFalse(any("journal-draft-" in p for p in bad.problems), bad.problems)
        self.assertIn("session 10 (10-missing): cannot read sessions/10-missing.md (FileNotFoundError)", bad.problems)

    def test_a_save_checks_the_other_files_again_under_the_lock(self):
        library.fork(self.cfg, "fix-life")
        name = "sessions/02-hidden-goals.md"
        d = library.draft(self.cfg, "fix-life", name, library.shipped("fix-life").files[name] + "\n")
        other = self.dir / "fix-life" / "sessions" / "03-identity-tracer.md"
        other.write_text(other.read_text(encoding="utf-8") + "\nA line of his.\n", encoding="utf-8")   # meanwhile
        target = self.dir / "fix-life" / name
        with self.assertRaises(library.Changed):
            library.save_draft(self.cfg, d, target.stat().st_mtime)
        self.assertEqual(target.read_text(encoding="utf-8"), library.shipped("fix-life").files[name])

    def test_a_broken_practice_file_is_not_taken_for_a_situational_definition(self):
        self.write("stretching.md", "---\nkind: practice\nid: stretching\n---\n")
        self.assertIsNone(library.program_kind(self.cfg, "stretching"))
        self.write("evening.md", "---\nkind: situational\nid: evening\ntitle: T\n---\n")
        self.assertIsNone(library.his_path(self.cfg, "evening"))       # not his evening practice
        self.write("conversation-prep.md", "---\nkind: situational\nid: conversation-prep\n---\n")
        self.assertEqual(library.his_path(self.cfg, "conversation-prep"), self.dir / "conversation-prep.md")

    def test_a_draft_names_its_problems_and_a_saved_one_is_his(self):
        ship = library.shipped("fix-life")
        name = "sessions/02-hidden-goals.md"
        bad = library.draft(self.cfg, "fix-life", name, "no front matter")
        self.assertTrue(bad.problems and bad.program is None and bad.builtin)
        with self.assertRaises(ValueError):
            library.save_draft(self.cfg, bad, None)
        renamed = library.draft(self.cfg, "fix-life", "program.md", ship.files["program.md"].replace("id: fix-life", "id: fix-it"))
        self.assertIn("an id does not change", " ".join(renamed.problems))
        alias = library.draft(self.cfg, "fix-life", "program.md",
                              ship.files["program.md"].replace("debrief:", "aliases: prep, morning\ndebrief:"))
        self.assertEqual(len(alias.problems), 2, alias.problems)
        with self.assertRaises(ValueError):
            library.draft(self.cfg, "fix-life", "../evening.md", "x")
        with self.assertRaises(library.UnknownDefinition):
            library.draft(self.cfg, "gratitude", None, "x")
        good = library.draft(self.cfg, "fix-life", name, ship.files[name].replace("Hidden", "Unseen"))
        self.assertEqual(good.problems, ())
        with self.assertRaises(library.Changed):                        # nothing of his was read: no mtime to hold
            library.save_draft(self.cfg, good, 123.0)
        path = library.save_draft(self.cfg, good, None)
        self.assertEqual(path, self.dir / "fix-life" / name)
        self.assertEqual([p.name for p in self.dir.iterdir() if p.name.startswith(".fix-life")], [])
        again = library.draft(self.cfg, "fix-life", name, good.text + "\n")
        self.assertFalse(again.builtin)
        with self.assertRaises(library.Changed):
            library.save_draft(self.cfg, again, path.stat().st_mtime - 10)
        library.save_draft(self.cfg, again, path.stat().st_mtime)
        self.assertEqual(path.read_text(encoding="utf-8"), good.text + "\n")

    def test_a_program_folder_is_made_whole_or_not_at_all(self):
        ship = library.shipped("fix-life")
        with mock.patch.object(library.os, "rename", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                library.fork(self.cfg, "fix-life")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir() if not p.name.startswith(".library")), [])
        self.assertNotIn("defaults", ledger.read_state(self.dir / ".library"))
        (self.dir / "fix-life").mkdir()                                  # something unusable where it would go
        with self.assertRaises(FileExistsError):
            library.save_draft(self.cfg, library.draft(self.cfg, "fix-life", "program.md", ship.files["program.md"]), None)

    def test_a_run_in_progress_keeps_the_structure_and_lets_words_change(self):
        prog = library.load(self.cfg).programs["fix-life"]
        runs = self.cfg.state_dir / "runs" / "fix-life"
        runs.mkdir(parents=True)
        (runs / "r1.json").write_text(json.dumps({"id": "r1", "program": "fix-life", "status": "paused",
                                                  "current": {"session": 3, "phase": 2}}), encoding="utf-8")
        reworded = replace(prog, title="Fix it")
        self.assertIsNone(library.run_conflict(self.cfg, "fix-life", reworded))
        shorter = replace(prog, sessions=prog.sessions[:-1])
        why = library.run_conflict(self.cfg, "fix-life", shorter)
        self.assertIn("session 3, phase 2 (on a break)", why)
        retyped = replace(prog, sessions=(replace(prog.sessions[0], phases=tuple(
            replace(ph, type="ask") for ph in prog.sessions[0].phases)),) + prog.sessions[1:])
        self.assertIsNotNone(library.run_conflict(self.cfg, "fix-life", retyped))
        library.fork(self.cfg, "fix-life")
        program = self.dir / "fix-life" / "program.md"
        program.write_text(program.read_text(encoding="utf-8").replace(", 10-one-day-reset", ""), encoding="utf-8")
        with self.assertRaises(library.RunInProgress) as caught:   # a reset to the default's ten sessions, under it
            library.reset(self.cfg, "fix-life")
        self.assertEqual(caught.exception.runs, ["r1"])
        self.assertTrue(program.is_file())
        program.write_text(library.shipped("fix-life").files["program.md"], encoding="utf-8")
        # a completed run whose session-10 interrupts are still to come is held too
        (runs / "r1.json").write_text(json.dumps({"id": "r1", "program": "fix-life", "status": "completed",
                                                  "scheduled": [{"id": "s10-i1", "status": "pending"}]}),
                                      encoding="utf-8")
        self.assertIn("interrupts are still to come", library.run_conflict(self.cfg, "fix-life", shorter))
        (runs / "r1.json").write_text(json.dumps({"id": "r1", "program": "fix-life", "status": "completed",
                                                  "scheduled": [{"id": "s10-i1", "status": "sent"}]}),
                                      encoding="utf-8")
        self.assertIsNone(library.run_conflict(self.cfg, "fix-life", shorter))
        # his own program, broken while a run of it is in progress: no structure to check a repair against
        mine = self.cfg.state_dir / "runs" / "my-plan"
        mine.mkdir(parents=True)
        (mine / "r2.json").write_text(json.dumps({"id": "r2", "program": "my-plan", "status": "active"}), encoding="utf-8")
        self.assertIn("my-plan does not load", library.run_conflict(self.cfg, "my-plan", replace(prog, id="my-plan")))
        (mine / "r2.json").write_text(json.dumps({"id": "r2", "program": "my-plan", "status": "abandoned"}),
                                      encoding="utf-8")
        self.assertIsNone(library.run_conflict(self.cfg, "my-plan", replace(prog, id="my-plan")))
        for odd in (3, True, "x", {"a": 1}):   # a run file written by hand is read, never a crash (CodeRabbit)
            (runs / "r1.json").write_text(json.dumps({"id": "r1", "status": "completed", "scheduled": odd}),
                                          encoding="utf-8")
            self.assertIsNone(library.run_conflict(self.cfg, "fix-life", shorter))


class DoctorTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), NEUTRAL)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def rows(self):
        from journal import status
        return [(n, st, d) for n, st, d in status.doctor(self.cfg, token=False) if n in ("practices dir", "library")]

    def test_fresh_install(self):
        self.assertEqual(self.rows(), [
            ("practices dir", "ok", f"{self.cfg.practices_dir} will be created (no user practices yet)"),
            ("library", "ok", "4 practices (4 built-in): morning, evening, midweek, weekly")])

    def test_user_files_and_problems(self):
        from journal import status
        d = self.cfg.practices_dir
        d.mkdir(parents=True)
        (d / "gratitude.md").write_text(GRATITUDE, encoding="utf-8")
        (d / "evening.md").write_text("---\nkind: practice\nid: evening\n---\n", encoding="utf-8")
        rows = self.rows()
        self.assertEqual(rows[0], ("practices dir", "ok", f"{d} (1 user practice)"))
        self.assertEqual(rows[1], ("library", "ok", "5 practices (4 built-in, 1 user): morning, gratitude (user), "
                                                    "evening, midweek, weekly"))
        self.assertEqual(rows[2][:2], ("library", "warn"))
        self.assertIn("evening.md: title is required; no questions", rows[2][2])
        self.assertTrue(rows[2][2].endswith("(built-in 'evening' is used)"))
        self.assertEqual(status.exit_code(rows), 0)  # a broken user file never fails doctor: the built-in is sent

    def test_overrides_and_disabled_are_named(self):
        d = self.cfg.practices_dir
        d.mkdir(parents=True)
        (d / "midweek.md").write_text(library.builtins()["midweek"].raw.replace("---\n## 1.", "enabled: false\n---\n## 1."),
                                      encoding="utf-8")
        self.assertEqual(self.rows()[1], ("library", "ok", "4 practices (3 built-in, 1 user): morning, evening, "
                                                           "midweek (user, overrides the built-in, disabled), weekly"))

    def test_a_file_the_parser_once_crashed_on_is_a_warning(self):
        from journal import status
        d = self.cfg.practices_dir
        d.mkdir(parents=True)
        (d / "gratitude.md").write_text(GRATITUDE.replace("21:30", "21:30\nschedule.weekday: ²"), encoding="utf-8")
        rows = self.rows()
        self.assertEqual([r[:2] for r in rows], [("practices dir", "ok"), ("library", "ok"), ("library", "warn")])
        self.assertIn("schedule.weekday must be 0 (Mon) to 6 (Sun) (got '²')", rows[2][2])
        self.assertEqual(status.exit_code(rows), 0)

    def test_broken_package_data_fails_without_raising(self):
        def broken():
            raise RuntimeError("built-in practices are unreadable: nothing in /x")
        from unittest import mock
        with mock.patch.object(library, "builtins", broken):
            rows = self.rows()
        self.assertEqual(rows[-1], ("library", "fail", "built-in practices are unreadable: nothing in /x"))

    def test_env_owned_dir_says_so(self):
        from journal import config as jconfig
        os.environ["JOURNAL_PRACTICES_DIR"] = f"{self.tmp.name}/env-practices"
        cfg = jconfig.from_dict({"root": self.tmp.name, "state_dir": f"{self.tmp.name}/state"},
                                origins={"root": "file"})
        from journal import status
        row = [r for r in status.doctor(cfg, token=False) if r[0] == "practices dir"][0]
        self.assertEqual(row, ("practices dir", "ok", f"{self.tmp.name}/env-practices will be created (no user "
                                                      "practices yet; from JOURNAL_PRACTICES_DIR)"))


class ConfigTest(CleanEnv):
    def test_practices_dir_layers(self):
        from journal import config as jconfig
        with tempfile.TemporaryDirectory() as t:
            cfg = jconfig.from_dict({"root": t, "state_dir": f"{t}/state"})
            self.assertEqual(cfg.practices_dir, Path(t) / "state" / "practices")
            self.assertEqual(cfg.origins.get("practices_dir", "default"), "default")
            cfg = jconfig.from_dict({"root": t, "practices_dir": "~/my-practices"})
            self.assertEqual(cfg.practices_dir, Path.home() / "my-practices")
            os.environ["JOURNAL_PRACTICES_DIR"] = f"{t}/env-practices"
            cfg = jconfig.from_dict({"root": t, "practices_dir": "~/my-practices"}, origins={"practices_dir": "file"})
            self.assertEqual(cfg.practices_dir, Path(t) / "env-practices")
            self.assertEqual(cfg.origins["practices_dir"], "env")
            with self.assertRaises(jconfig.ConfigError) as e:
                jconfig.write_values(cfg, {"practices_dir": "/elsewhere"})
            self.assertIn("JOURNAL_PRACTICES_DIR", str(e.exception))
            hand = Config(root=Path(t), state_dir=Path(t) / "s")
            self.assertEqual(hand.practices_dir, Path(t) / "s" / "practices")

    def test_a_path_that_is_not_a_string_is_a_config_error(self):
        # Path(5) raised TypeError, which nothing above from_dict catches: the tick died with a traceback.
        from journal import config as jconfig
        with tempfile.TemporaryDirectory() as t:
            for key in ("practices_dir", "state_dir"):
                for bad in (5, True, False, ["a"], {"a": 1}):
                    with self.subTest(key=key, value=bad), self.assertRaises(jconfig.ConfigError) as e:
                        jconfig.from_dict({"root": t, "state_dir": f"{t}/s", key: bad}, environ={})
                    self.assertEqual(str(e.exception), f"`{key}` must be a path (got {bad!r})")
            cfg = jconfig.from_dict({"root": t, "state_dir": f"{t}/s", "practices_dir": ""}, environ={})
            self.assertEqual(cfg.practices_dir, Path(t) / "s" / "practices")  # empty still means the default

    def test_a_relative_path_is_a_warning(self):
        # Cron, the gateway and a shell run from different directories: a relative path is three directories.
        from journal import config as jconfig
        with tempfile.TemporaryDirectory() as t:
            cfg = jconfig.from_dict({"root": t, "state_dir": f"{t}/s", "practices_dir": "rel/dir",
                                     "telegram": {"chat_id": "1"}}, environ={})
            self.assertEqual(jconfig.problems(cfg), [
                "practices_dir is relative (rel/dir): each process would resolve it against its own working "
                "directory; use an absolute path"])
            cfg = jconfig.from_dict({"root": "Journal", "state_dir": "state", "telegram": {"chat_id": "1"}}, environ={})
            self.assertEqual([p.split(" is relative")[0] for p in jconfig.problems(cfg)], ["root", "state_dir"])
            cfg = jconfig.from_dict({"root": t, "state_dir": f"{t}/s", "telegram": {"chat_id": "1"}}, environ={})
            self.assertEqual(jconfig.problems(cfg), [])


if __name__ == "__main__":
    unittest.main()
