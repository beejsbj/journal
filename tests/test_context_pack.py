"""The context pack after a silence (CONTEXT-PACK): where things stand, counted by code, and a pack that holds.

Fixed fixtures under temporary roots, neutral names (Sam, his companion Ada): gaps of 0, 2, 5 and 20 days since
his last word on the record; sparse entries over two months; a program in progress; off-the-record (``/x``)
activity inside the gap, which must not move "last heard"; the size budget; pointers made only from the sources
that are connected. ``python3 tests/test_context_pack.py --sample`` prints the 5-day pack (the PR's sample).
"""
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import helpers  # noqa: F401  (src/ on the path; Telegram, the model and the mirror unreachable)
from helpers import FakeTranscript, _Snapshot

from journal import (agent, capture, config as jconfig, context, conversations, entries, hookstate, ledger, library,
                     privacy, prompts, sources, standing, tick, today, validate)
from journal.config import Config, Names
from journal.ports import Row

TZ = ZoneInfo("Europe/Lisbon")
NOW = datetime(2026, 10, 26, 7, 0, tzinfo=TZ)  # a Monday
TODAY = NOW.date()
NAMES = Names(user="Sam", companion="Ada", emoji="📓", pronouns="he/him/his", frontmatter_source="journal")
CHAT = "1001"
RUN = "2026-09-20-abc123"

# his entries before the gap: sparse, over two months
SPARSE = (("2026-08-27", "morning", "Woke before the alarm and walked to the bakery."),
          ("2026-09-10", "evening", "The tomatoes finally ripened; gave half to the neighbours."),
          ("2026-09-24", "evening", "Long call with my brother about the move."),
          ("2026-10-05", "morning", "Rain on the skylight, slow start, good coffee."))


def at(day: date | str, hh: int, mm: int = 0) -> datetime:
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return datetime(d.year, d.month, d.day, hh, mm, tzinfo=TZ)


def his(rid, when, text, mid, **meta):
    return Row(rid, when.astimezone(timezone.utc), "user", "his", text, mid, stored=text,
               meta={"stored_id": mid, "loose": text, "uid": None, "active": True, **meta})


def hers(rid, when, text):
    return Row(rid, when.astimezone(timezone.utc), "assistant", "hers", text, None, stored=text,
               meta={"uid": f"u{rid}", "active": True, "ends_turn": True, "reply": True})


class Scenario:
    """A journal ``gap`` days after his last practice reply (the evening of that day at 21:40; with no gap, this
    morning's at 06:40), every morning (06:30) and evening (21:30) prompt since then unanswered."""

    def __init__(self, tmp: Path, gap: int, *, program: bool = True, chat: bool = False):
        self.tmp, self.gap = tmp, gap
        self.cfg = Config(root=tmp / "journal", state_dir=tmp / "state", chat_id=CHAT, names=NAMES,
                          timezone="Europe/Lisbon", location="Riverside")
        self.cfg.root.mkdir(parents=True)
        self.cfg.sources = {"weather": {"enabled": False}, "telegram_history": {"enabled": chat, "days": 7, "limit": 25},
                            "not_connected": ""}
        self.rows: list[Row] = []
        self.mid = 100
        for day, kind, words in SPARSE:
            self.answer(kind, day, words)
        last_day = TODAY - timedelta(days=gap)
        if gap == 0:
            self.answer("morning", last_day, "Up early, the light on the water.", hh=6, mm=30, reply=(6, 40))
        else:
            self.answer("evening", last_day, "Tired but the walk helped.", hh=21, mm=30, reply=(21, 40))
        d = last_day + timedelta(days=1) if gap else None
        while d is not None and d <= TODAY:
            self.send("morning", d, 6, 30, f"Morning, Sam. A {d:%A} question about sleep and light.")
            if d < TODAY:
                self.send("evening", d, 21, 30, f"Evening, Sam. What stayed with you on {d:%A}?")
            d += timedelta(days=1)
        if program:
            self.program()
        week = self.cfg.root / "2026-09-06-week.md"
        week.write_text("---\ntype: journal\nkind: week\nauthor: Ada\n---\n# Week review\n\n**What stood out** — "
                        "the garden, and the call you kept putting off.\n", encoding="utf-8")
        (self.cfg.state_dir / "notebook-archive.jsonl").write_text(json.dumps(
            {"id": "a1", "text": "ask about the move", "created_at": "2026-09-01T10:00:00+00:00",
             "status": "resolved"}) + "\n", encoding="utf-8")
        self.chat = FakeTranscript(source="memory", load=lambda: _Snapshot(sorted(self.rows, key=lambda r: r.id)))

    def send(self, kind, day, hh, mm, opener, mid=None):
        self.mid += 1
        sent = at(day, hh, mm)
        rec = {"id": ledger.prompt_id(kind, str(day)), "kind": kind, "local_date": str(day), "chat_id": CHAT,
               "message_id": str(mid or self.mid), "sent_at": sent.astimezone(timezone.utc).isoformat(),
               "opener": opener, "opener_source": "ada"}
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append(rec)
        return rec

    def answer(self, kind, day, words, hh=None, mm=0, reply=None):
        hh = hh if hh is not None else (6 if kind == "morning" else 21)
        rec = self.send(kind, day, hh, mm, f"Opener for {kind} on {day}.")
        rh, rm = reply or (hh, mm + 10)
        capture.capture(self.cfg, CHAT, rec["message_id"], words, at(day, rh, rm), "text", f"r{rec['message_id']}")

    def program(self):
        """fix-life at session 3 of 10, phase 2, last answered on 1 Oct (before every gap here)."""
        run = {"program": "fix-life", "id": RUN, "status": "active", "started_at": "2026-09-20T18:00:00+00:00",
               "last_activity_at": "2026-10-01T19:00:00+00:00", "current": {"session": 3, "phase": 2},
               "sessions": {"1": {"status": "complete"}, "2": {"status": "complete"},
                            "3": {"status": "in_progress", "phases": {"2": {"prompt_id": f"{RUN}-s03-p2"}}}},
               "scheduled": []}
        path = self.cfg.state_dir / "runs" / "fix-life" / f"{RUN}.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(run), encoding="utf-8")
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({"id": f"{RUN}-s03-p1", "kind": "fix-life", "program": "fix-life", "run": RUN,
                                    "local_date": "2026-10-01", "chat_id": CHAT, "message_id": "900",
                                    "sent_at": "2026-10-01T18:30:00+00:00",
                                    "captures": [{"at": "2026-10-01T18:50:00+00:00", "rule": "reply"}]})

    def transcript(self):
        """Patch the chat port to this scenario's rows (and keep sqlite out of it)."""
        privacy._CACHE.clear()
        return mock.patch.object(Config, "transcript", lambda cfg, db=None: self.chat)


class Base(unittest.TestCase):
    gap = 5
    program = True
    chat = False

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.s = Scenario(Path(self._tmp.name), self.gap, program=self.program, chat=self.chat)
        self.cfg = self.s.cfg

    def tearDown(self):
        self._tmp.cleanup()


class GapTest(unittest.TestCase):
    """Gaps of 0, 2, 5 and 20 days: what is counted, and when the returning stance is asked for."""

    def scenario(self, gap):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return Scenario(Path(tmp.name), gap)

    def test_the_gaps(self):
        expect = {  # gap: (unanswered total, per practice, returning)
            0: (0, {}, False),
            2: (3, {"morning": 2, "evening": 1}, False),
            5: (9, {"morning": 5, "evening": 4}, True),
            20: (28, {"evening": 14, "morning": 14}, True),
        }
        for gap, (total, counts, returning) in expect.items():
            with self.subTest(gap=gap):
                s = self.scenario(gap)
                st = standing.collect(s.cfg, NOW)
                self.assertEqual(st["gap_days"], gap)
                self.assertEqual(st["heard"]["date"], str(TODAY - timedelta(days=gap)))
                self.assertEqual(st["missed"]["total"], total)
                self.assertEqual(st["missed"]["counts"], counts)
                self.assertEqual(st["missed"]["capped"], gap == 20)  # 14 days back at most, none answered in them
                self.assertEqual(st["returning"], returning)
                pack = context.build(s.cfg, NOW, st)
                q = prompts.opener_request(s.cfg, "morning", NOW, pack, library.builtins()["morning"].bank_with_ids(),
                                           context.unanswered(s.cfg, NOW), st)
                self.assertEqual("is coming back after" in q, returning)
                if returning:
                    self.assertIn(f"Sam is coming back after {gap} days without a word on the record", q)
                    self.assertIn("no recap, no list of what he missed", q)
                    self.assertIn("Offer one light door in", q)
                    self.assertIn(f"past the 3-day mark: a return after time away", pack)
                elif gap:
                    self.assertIn("under the 3-day mark", pack)

    def test_the_twenty_day_gap_is_named_and_counted_but_not_listed(self):
        s = self.scenario(20)
        st = standing.collect(s.cfg, NOW)
        pack = context.build(s.cfg, NOW, st)
        self.assertIn("- Last heard from on the record: Tue 6 Oct 21:40, 20 days ago (a reply to the Evening "
                      "journal prompt).", pack)
        self.assertIn("- Prompts unanswered in the last 14 days, none of them answered: 28 — evening 14, morning 14.",
                      pack)
        self.assertIn("- Gap: 20 days since the last word from him on the record", pack)
        missed = context.unanswered(s.cfg, NOW)
        note = prompts.missed_note(s.cfg, missed, NOW)
        self.assertEqual(note, "His last 28 prompts got no reply, since Mon 12 Oct: evening 14, morning 14. ")
        short = prompts.missed_note(s.cfg, missed[:2], NOW)  # a short run is still named prompt by prompt
        self.assertEqual(short, "His last 2 prompts got no reply: morning (Mon 06:30), evening (Sun 21:30). ")

    def test_the_last_two_openers_word_for_word_and_never_evidence(self):
        s = self.scenario(5)
        pack = context.build(s.cfg, NOW)
        head = "## Your openers that got no reply (your own words, word for word, newest first)"
        self.assertIn(head, pack)
        section = pack.split(head)[1].split("\n## ")[0]
        self.assertIn("- [you] Mon 26 Oct 06:30 · Morning journal prompt: Morning, Sam. A Monday question about "
                      "sleep and light.", section)
        self.assertIn("- [you] Sun 25 Oct 21:30 · Evening journal prompt: Evening, Sam. What stayed with you on "
                      "Sunday?", section)
        self.assertEqual(section.count("- [you] "), 2)
        self.assertNotIn("sleep and light", validate.citable(pack).split("## Recent journal entries")[0])

    def test_no_gap_no_missed_section(self):
        s = self.scenario(0)
        pack = context.build(s.cfg, NOW)
        self.assertNotIn("Your openers that got no reply", pack)
        self.assertIn("- Last heard from on the record: Mon 26 Oct 06:40, today (a reply to the Morning journal "
                      "prompt).", pack)
        self.assertIn("- No prompt is waiting: the newest one was answered (Morning journal, sent Mon 26 Oct 06:30, "
                      "today).", pack)
        self.assertIn("- Gap: less than a day since the last word from him on the record; under the 3-day mark.",
                      pack)


class EntriesAndAnalysisTest(Base):
    def test_sparse_entries_over_two_months_carry_their_dates_and_ages(self):
        pack = context.build(self.cfg, NOW)
        self.assertIn("## Recent journal entries (newest first, by count whatever their age)", pack)
        heads = [line for line in pack.splitlines() if line.startswith("### journal:")]
        self.assertEqual(heads, [
            "### journal:2026-10-21-evening (Wed 21 Oct, 5 days ago)",
            "### journal:2026-10-05-morning (Mon 5 Oct, 21 days ago)",
            "### journal:2026-09-24-evening (Thu 24 Sep, 32 days ago)",
            "### journal:2026-09-10-evening (Thu 10 Sep, 46 days ago)",
            "### journal:2026-08-27-morning (Thu 27 Aug, 60 days ago)",
        ])
        self.assertIn("- Last entry: journal:2026-10-21-evening (Wed 21 Oct, 5 days ago).", pack)

    def test_the_count_is_configurable(self):
        self.cfg.pack["entries"] = 2
        heads = [l for l in context.build(self.cfg, NOW).splitlines() if l.startswith("### journal:")]
        self.assertEqual(len(heads), 2)

    def test_her_latest_week_review_whatever_its_age_labelled_as_hers(self):
        pack = context.build(self.cfg, NOW)
        self.assertIn("## Your earlier analysis (your own reflections, not Sam's words)\n"
                      "### week:2026-09-06-week (your week review, for the week ending Sun 6 Sep, 50 days ago)\n"
                      "**What stood out**", pack)

    def test_a_program_in_progress(self):
        pack = context.build(self.cfg, NOW)
        self.assertIn("- Program in progress: Fix your entire life in 1 day: session 3 of 10 (The Identity "
                      "Construction Tracer), phase 2 of ", pack)
        line = next(l for l in pack.splitlines() if l.startswith("- Program in progress"))
        self.assertIn("; movement: diagnosis (sessions 1–4); last answered Thu 1 Oct, 25 days ago; its stated pace: "
                      "1 session every 2–3 days.", line)

    def test_a_paused_program_between_sessions(self):
        path = self.cfg.state_dir / "runs" / "fix-life" / f"{RUN}.json"
        run = json.loads(path.read_text())
        run.update(status="paused", current={"session": 4, "phase": 1})
        run["sessions"]["3"]["status"] = "complete"
        path.write_text(json.dumps(run))
        line = next(l for l in context.build(self.cfg, NOW).splitlines() if l.startswith("- Program in progress"))
        self.assertIn("session 3 done, session 4 of 10 (", line)
        self.assertIn("not begun yet, on a break; movement: diagnosis (sessions 1–4)", line)

    def test_movement_parsing(self):
        line = "diagnosis=1-4, understanding=5-7, transformation=8-9, integration=10"
        self.assertEqual(standing.movement(line, 6), "understanding (sessions 5–7)")
        self.assertEqual(standing.movement(line, 10), "integration (session 10)")
        self.assertEqual(standing.movement("nonsense", 2), "")


class OffTheRecordTest(Base):
    """``/x`` activity inside the gap never moves "last heard", nor leaks a time."""
    chat = True

    def setUp(self):
        super().setUp()
        r = self.s.rows
        r.append(his(1, at("2026-10-19", 12), "an older public message", "c1"))
        r.append(hers(2, at("2026-10-19", 12, 1), "Noted."))
        r.append(his(3, at("2026-10-24", 22), "/x the thing I keep to myself", "c3"))  # one message off the record
        r.append(hers(4, at("2026-10-24", 22, 1), "I hear you."))
        conversations.note_off(self.cfg, "c5", at("2026-10-25", 13), thread_start=True)  # a bare /x the gateway answered
        r.append(his(6, at("2026-10-25", 13, 5), "window words that stay private", "c6"))
        r.append(hers(7, at("2026-10-25", 13, 6), "Of course."))
        hookstate.stamp(self.cfg, last_his=at("2026-10-25", 13, 5).timestamp())  # moved by every message, /x too

    def test_x_messages_and_windows_do_not_move_last_heard(self):
        with self.s.transcript():
            last, state = standing.heard_in_chat(self.cfg, NOW)
            self.assertEqual((last, state), (at("2026-10-19", 12).astimezone(timezone.utc), "read"))
            st = standing.collect(self.cfg, NOW)
            pack = context.build(self.cfg, NOW, st)
        self.assertIn("— an older public message", pack)  # the chat section did read the fixture (not a vacuous pass)
        self.assertEqual(st["heard"]["date"], "2026-10-21")  # his last reply, not the /x on the 24th or 25th
        self.assertEqual(st["gap_days"], 5)
        self.assertNotIn("Sat 24 Oct", pack.split("## Recent journal entries")[0])
        self.assertNotIn("Sun 25 Oct 13", pack)
        for secret in ("keep to myself", "window words", "I hear you", "Of course"):
            self.assertNotIn(secret, pack)

    def test_telegram_quotes_verify_against_his_rows_only(self):
        """Re-review 2: a cron delivery, a compaction summary and a picture's vision description are not his."""
        from journal.hermes.transcript import to_row

        def raw(rid, when, content, pmid=None):
            return to_row({"id": rid, "ts": when.timestamp(), "role": "user", "content": content, "pmid": pmid,
                           "display_kind": "", "tool_calls": "", "uid": f"u{rid}", "copy": None,
                           "display_metadata": "", "active": 1})

        self.s.rows += [
            raw(20, at("2026-10-22", 7), "[Cron delivery: morning-brief] Remember the dentist at noon."),
            raw(21, at("2026-10-22", 8), "[CONTEXT COMPACTION] Summary so far: he is thinking of leaving the band."),
            raw(22, at("2026-10-22", 9), "[The user sent an image~ Here's what I can see:\nA tabby cat asleep on a red "
                                         "sofa.]\n[If you need a closer look, use vision_analyze with image_url: "
                                         "/tmp/a.jpg ~]\n\nthe cat won again", pmid="c22")]
        with self.s.transcript():
            verify = context.make_verifier(self.cfg)
            for quote in ("remember the dentist at noon", "thinking of leaving the band", "a tabby cat asleep",
                          "keep to myself", "window words"):
                with self.subTest(quote=quote):
                    self.assertFalse(verify("telegram:x", validate.norm(quote)))
            self.assertTrue(verify("telegram:x", validate.norm("an older public message")))

    def test_a_public_message_in_the_gap_does_move_it(self):
        self.s.rows.append(his(8, at("2026-10-23", 9), "back from the coast", "c8"))
        with self.s.transcript():
            st = standing.collect(self.cfg, NOW)
        self.assertEqual(st["heard"], {"date": "2026-10-23", "at": "2026-10-23T09:00+01:00", "via": "a chat message"})
        self.assertEqual(st["gap_days"], 3)

    def test_the_privacy_pass_failing_counts_nothing_from_the_chat(self):
        self.s.rows.append(his(8, at("2026-10-23", 9), "back from the coast", "c8"))
        with self.s.transcript(), mock.patch.object(privacy, "covering", lambda *a, **k: privacy.Closed("down")):
            st = standing.collect(self.cfg, NOW)
            pack = context.build(self.cfg, NOW, st)
        self.assertEqual(st["chat"], "closed")
        self.assertEqual(st["heard"]["date"], "2026-10-21")
        self.assertIn("His chat messages are not counted: the privacy pass could not run", pack)

    def test_the_today_block_reads_the_tick_s_cached_time_never_the_transcript(self):
        with self.s.transcript():
            self.assertTrue(today.refresh(self.cfg, NOW, [], force=True))
        self.assertEqual(today.read_cache(self.cfg)["heard"]["last"], at("2026-10-19", 12).astimezone(timezone.utc)
                         .isoformat())

        def no_chat(*a, **k):
            raise AssertionError("the today block read the transcript")

        with mock.patch.object(Config, "transcript", no_chat):
            block = today.render(self.cfg, NOW)
        self.assertIn("- last heard on the record: Wed 21 Oct, 5 days ago, a reply to the Evening journal prompt; "
                      "last entry 2026-10-21-evening (coming back after time away)", block)
        self.assertIn("- prompts unanswered since the newest one answered (Evening journal, Wed 21 Oct): 9 — "
                      "morning 5, evening 4", block)
        self.assertNotIn("Sun 25 Oct", block)


class TodayBlockTest(Base):
    def test_the_block_opens_with_where_things_stand_and_the_program(self):
        block = today.render(self.cfg, NOW)
        lines = block.splitlines()
        self.assertTrue(lines[2].startswith("- last heard on the record: Wed 21 Oct, 5 days ago"))
        prog = [l for l in lines if l.startswith("- program:")]
        self.assertEqual(len(prog), 1)  # the richer line replaces the block's own program line
        self.assertIn("session 3 of 10", prog[0])
        self.assertIn("its stated pace: 1 session every 2–3 days", prog[0])
        self.assertLessEqual(len(block), jconfig.today_max_chars(self.cfg))

    def test_the_api_shape_is_unchanged(self):
        self.assertNotIn("standing", today.collect(self.cfg, NOW))
        self.assertNotIn("standing", today.collect(self.cfg, NOW, stems=True))

    def test_it_can_be_switched_off(self):
        self.cfg.today["sources"] = ["practices", "programs"]
        block = today.render(self.cfg, NOW)
        self.assertNotIn("last heard", block)
        self.assertIn("- program: Fix your entire life", block)  # the block's own line again

    def test_no_gap_no_heard_line(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        s = Scenario(Path(tmp.name), 0, program=False)
        self.assertNotIn("last heard", today.render(s.cfg, NOW))


class BudgetTest(Base):
    def bulk(self):
        """Long entries and a long chat: far more than the budget."""
        for i in range(12):
            day = TODAY - timedelta(days=30 + i * 3)
            self.s.answer("midweek", day, f"entry {i} " + "many words about the river " * 80, hh=12)

    def test_the_budget_holds_and_the_far_end_goes_first(self):
        self.bulk()
        self.cfg.pack.update(max_chars=9000, entries=10)
        pack = context.build(self.cfg, NOW)
        self.assertLessEqual(len(pack), 9000)
        heads = [l for l in pack.splitlines() if l.startswith("### journal:")]
        self.assertEqual(heads[0], "### journal:2026-10-21-evening (Wed 21 Oct, 5 days ago)")  # the newest stays
        self.assertNotIn("2026-08-27", "\n".join(heads))  # the oldest went
        for kept in ("## Where things stand", "## Your openers that got no reply", "## Where to look further",
                     "- Older journal entries:"):
            self.assertIn(kept, pack)
        self.assertIn("left out for length", pack)
        self.assertRegex(pack, r"_This pack is held to 9000 characters: \d+ entries \(Recent journal entries\)")

    def test_the_week_review_goes_before_newer_entries(self):
        self.bulk()
        self.cfg.pack.update(max_chars=12000, entries=10)
        pack = context.build(self.cfg, NOW)
        self.assertNotIn("### week:2026-09-06-week", pack)  # 6 Sep: older than what was kept
        self.assertIn("### journal:2026-10-21-evening", pack)

    def test_under_the_floor_the_floor_is_used(self):
        self.cfg.pack["max_chars"] = 100
        self.assertEqual(jconfig.pack_max_chars(self.cfg), jconfig.PACK_MIN_CHARS)
        self.assertIn("pack.max_chars 100 is out of range; 8000 is used", jconfig.problems(self.cfg))
        self.bulk()
        self.assertLessEqual(len(context.build(self.cfg, NOW)), jconfig.PACK_MIN_CHARS)

    def test_the_default_leaves_an_ordinary_pack_whole(self):
        pack = context.build(self.cfg, NOW)
        self.assertLess(len(pack), jconfig.pack_max_chars(self.cfg))
        self.assertNotIn("left out for length", pack)


class PointersTest(Base):
    def test_only_connected_sources_and_what_is_on_disk(self):
        tududi = Path(self._tmp.name) / "tududi-mcp.py"
        tududi.write_text("")
        self.cfg.coordinates = (38.7, -9.1)
        self.cfg.raw = {"coordinates": {"lat": 38.7, "lon": -9.1, "place": "Riverside"}}
        self.cfg.sources.update(
            weather={"enabled": True},
            tududi={"enabled": True, "command": ["python3", str(tududi)]},
            listenbrainz={"enabled": False, "user": "sam"},
            storyteller={"enabled": True, "db": str(Path(self._tmp.name) / "missing.db")},  # enabled, not reachable
        )
        self.cfg.pack["pointers"] = ["Older photos: the Photos folder, read-only.", 7]
        lines = context.pointers(self.cfg, NOW)
        text = "\n".join(lines)
        root = self.cfg.root
        self.assertIn(f"- Older journal entries: one Markdown file per entry in {root} (YYYY-MM-DD-<practice>.md); "
                      "5 entries of Sam's back to Thu 27 Aug; the pack has the newest 5.", text)
        self.assertIn("- Your earlier analysis (1 week review back to Sun 6 Sep)", text)
        self.assertIn("- Programs: each session he works through is an entry", text)
        self.assertIn("- Your notebook's archive: 1 thread resolved or gone stale, back to Tue 1 Sep, not in this "
                      "pack.", text)
        self.assertNotIn(str(self.cfg.state_dir), text)  # the engine's state directory is never named
        self.assertIn("- Weather: the Open-Meteo forecast for Riverside", text)
        self.assertIn(f"- Tasks and habits (tududi): the pack lists open and due tasks", text)
        self.assertIn(f"`python3 {tududi}`", text)
        self.assertNotIn("ListenBrainz", text)  # disabled
        self.assertNotIn("Reading", text)  # enabled, but its database isn't there
        self.assertNotIn("chat with you", text)  # no chat transcript here
        self.assertEqual(lines[-1], "- Older photos: the Photos folder, read-only.")
        self.assertIn("pack.pointers must be a list of lines", " ".join(jconfig.problems(self.cfg)))

    def test_nothing_connected_nothing_listed(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = Config(root=Path(tmp.name) / "j", state_dir=Path(tmp.name) / "s", names=NAMES)
        cfg.root.mkdir()
        cfg.sources = {"weather": {"enabled": False}}
        self.assertEqual(context.pointers(cfg, NOW), [])
        self.assertIn("## Where to look further", context.build(cfg, NOW))

    def test_no_pointer_into_his_chat_history(self):
        """It holds what he said off the record; held back until the WEB-DESIGN open point is closed."""
        self.cfg.sources["telegram_history"]["enabled"] = True
        with self.s.transcript():
            self.assertNotIn("chat with you", "\n".join(context.pointers(self.cfg, NOW)))


class VoicesTest(Base):
    """M1 (review of CONTEXT-PACK): her words never verify as his. A journal: or telegram: quote is checked against his words
    in the source it cites, not against the pack's text (which holds her review, her notes and code's prose)."""

    def setUp(self):
        super().setUp()
        self.entry = self.cfg.root / "2026-10-21-evening.md"
        entries.write_annotations(self.entry, "- He sounds worn down by the commute and wants a quieter month.",
                                  self.cfg.names)
        capture.add_companion_note(self.cfg, "Her aside: he might be avoiding the call with his sister.", NOW,
                                   local_date="2026-10-21", kind="evening")
        self.pack = context.build(self.cfg, NOW)
        self.verify = context.make_verifier(self.cfg)

    def check(self, source, quote, opener="Morning."):
        return validate.check("morning", {"opener": opener, "citations": [{"source": source, "quote": quote}]},
                              self.pack, self.verify)

    def test_his_words_verify_in_their_own_file(self):
        self.assertEqual(self.check("journal:2026-10-21-evening", "Tired but the walk helped."), (True, "ok"))
        self.assertEqual(self.check("journal:2026-10-21-evening.md", "Tired but the walk helped."), (True, "ok"))
        self.assertEqual(self.check("journal:2026-10-21-evening (Wed 21 Oct, 5 days ago)", "Tired but the walk helped."),
                         (True, "ok"))  # the heading copied whole
        self.assertEqual(self.check("journal:2026-10-21-evening", "“Tired but the walk helped”"), (True, "ok"))
        self.assertFalse(self.check("journal:../state/ledger", "not anything he wrote")[0])

    def test_her_words_and_code_s_prose_do_not(self):
        for source, quote in (
                ("journal:2026-10-21-evening", "worn down by the commute and wants a quieter month"),  # her annotation
                ("journal:2026-10-21-evening", "he might be avoiding the call with his sister"),  # her note in his entry
                ("journal:2026-10-21-evening", "the call you kept putting off"),  # her week review, in the pack
                ("journal:2026-10-21-evening", "a return after time away"),  # where things stand (code)
                ("journal:2026-10-21-evening", "A Monday question about sleep and light"),  # her missed opener
                ("telegram:2026-10-26T06:30+00:00", "What stayed with you on Sunday?"),
                ("journal:x", "Don't repeat them, their lines or their questions")):
            with self.subTest(quote=quote):
                self.assertIn(quote, self.pack + self.entry.read_text())  # all of it is in front of her ...
                self.assertFalse(self.check(source, quote)[0])  # ... none of it is his

    def test_a_file_of_his_own_verifies_whole_and_her_analyses_cite_as_hers(self):
        (self.cfg.root / "2026-10-10-thoughts.md").write_text("---\ntitle: x\n---\nWrote this by hand at the lake.\n")
        self.assertTrue(self.check("journal:2026-10-10-thoughts", "Wrote this by hand at the lake.")[0])
        self.assertTrue(self.check("week:2026-09-06-week", "the call you kept putting off")[0])
        self.assertFalse(self.check("journal:2026-09-06-week", "the call you kept putting off")[0])

    def test_source_names_are_read_loosely_and_fail_closed(self):
        """Re-review 1: a capital, a space or a brain: path into the journal folder is the same check."""
        his, hers = "Tired but the walk helped.", "worn down by the commute"
        for source in ("Journal:2026-10-21-evening", " journal:2026-10-21-evening",
                       "brain:journal/2026-10-21-evening.md", f"brain:{self.cfg.root}/2026-10-21-evening.md",
                       "brain:2026-10-21-evening"):
            with self.subTest(source=source):
                self.assertEqual(self.check(source, his), (True, "ok"))
                self.assertFalse(self.check(source, hers)[0])
        self.assertEqual(self.check("journals:2026-10-21-evening", his),
                         (False, "unrecognised source 'journals:2026-10-21-evening'"))
        self.assertEqual(self.check("telegram-chat:x", his), (False, "unrecognised source 'telegram-chat:x'"))
        self.assertEqual(self.check("brain:Projects/notes.md", "anything at all"), (True, "ok"))  # outside: unchecked

    def test_his_words_under_another_name_pass_once_and_say_so(self):
        """Re-review 3 (Theo's delegate): his quote in the wrong file is still his, if exactly one file has it."""
        verify = context.make_verifier(self.cfg)
        self.assertTrue(verify("journal:2026-10-21-evening", validate.norm("Rain on the skylight, slow start")))
        self.assertEqual(verify.notes, ["cited-elsewhere: journal:2026-10-21-evening is in journal:2026-10-05-morning"])
        self.assertFalse(verify("journal:2026-10-05-morning", validate.norm("worn down by the commute")))  # hers
        (self.cfg.root / "2026-10-13-thoughts.md").write_text("Rain on the skylight, slow start.\n")
        verify = context.make_verifier(self.cfg)
        self.assertFalse(verify("journal:2026-10-21-evening", validate.norm("Rain on the skylight, slow start")))
        self.assertEqual(verify.notes, [])  # two files have it: not his for sure, so not passed

    def test_a_claim_needs_a_citation_that_grounds_it(self):
        """Re-review 4: a scrap of the standing section or her own analysis doesn't ground "you said"."""
        claim = "Evening, Sam. You told me you are quitting your job."
        for cites, expect in (
                ([{"source": "standing", "quote": "days"}], (False, "claim without citation")),
                ([{"source": "week:2026-09-06-week", "quote": "the call you kept putting off"}],
                 (False, "claim without citation")),
                ([{"source": "standing", "quote": "5 days since the last word"}], (True, "ok")),
                ([{"source": "journal:2026-10-21-evening", "quote": "the walk helped"}], (True, "ok")),
                ([{"source": "book:Moby-Dick", "quote": "19% through"}], (True, "ok"))):  # as the request says
            with self.subTest(cites=cites):
                self.assertEqual(validate.check("morning", {"opener": claim, "citations": cites}, self.pack,
                                                self.verify), expect)

    def test_the_tick_logs_a_quote_cited_elsewhere(self):
        answer = json.dumps({"opener": "Morning, Sam. Rain on the skylight again?", "asked": [],
                             "citations": [{"source": "journal:2026-10-21-evening",
                                            "quote": "Rain on the skylight, slow start"}]})
        log = []
        with mock.patch.object(agent, "ask", return_value=answer):
            _o, source, _c, _a = tick._opener(self.cfg, "morning", NOW, "p1", log)
        self.assertEqual(source, "ada")
        self.assertIn("p1: opener: cited-elsewhere: journal:2026-10-21-evening is in journal:2026-10-05-morning", log)
        self.assertNotIn("skylight", " ".join(log))

    def test_a_name_two_nested_files_share_verifies_nothing(self):
        """CodeRabbit on CONTEXT-PACK: with no file at the root, two nested files of one name must not vouch for each other."""
        for sub, words in (("a", "words of the first file"), ("b", "words of the second file")):
            (self.cfg.root / sub).mkdir()
            (self.cfg.root / sub / "2026-10-11-thoughts.md").write_text(f"{words}\n", encoding="utf-8")
        (self.cfg.root / "c").mkdir()
        (self.cfg.root / "c" / "2026-10-12-alone.md").write_text("only here\n", encoding="utf-8")
        verify = context.make_verifier(self.cfg)
        self.assertFalse(verify("journal:2026-10-11-thoughts", validate.norm("words of the")))  # in both
        self.assertTrue(verify("journal:2026-10-11-thoughts", validate.norm("words of the first file")))
        self.assertEqual(verify.notes, ["cited-elsewhere: journal:2026-10-11-thoughts is in journal:2026-10-11-thoughts"])
        self.assertTrue(verify("journal:2026-10-12-alone", validate.norm("only here")))

    def test_the_returning_facts_cite_as_standing(self):
        """L3: "you were away" is a claim; it passes with the Gap line cited as standing, and only that section."""
        away = "Welcome back, Sam. You were away a few days; how was the coast?"
        self.assertEqual(validate.check("morning", {"opener": away, "citations": []}, self.pack, self.verify),
                         (False, "claim without citation"))
        self.assertEqual(self.check("standing", "5 days since the last word from him on the record", away),
                         (True, "ok"))
        self.assertFalse(self.check("standing", "Tired but the walk helped.", away)[0])  # not a standing fact
        self.assertTrue(validate.check("morning", {"opener": "Welcome back, Sam.", "citations": []}, self.pack,
                                       self.verify)[0])
        q = prompts.opener_request(self.cfg, "morning", NOW, self.pack, library.builtins()["morning"].bank_with_ids(),
                                   context.unanswered(self.cfg, NOW), standing.collect(self.cfg, NOW))
        self.assertIn('{"source": "standing", "quote": "<words copied from the pack\'s Gap line>"}', q)


class ReviewFindingsTest(Base):
    """The rest of the review of CONTEXT-PACK, each from its probe."""

    def test_a_phase_send_time_is_not_shown(self):
        """M2: a /start or /next typed in a bare /x window re-sends the phase and moves its sent_at."""
        conversations.note_off(self.cfg, "w", at("2026-10-24", 22), thread_start=True)
        path = self.cfg.state_dir / "runs" / "fix-life" / f"{RUN}.json"
        run = json.loads(path.read_text())
        run["sessions"]["3"]["phases"]["2"]["sent_at"] = at("2026-10-24", 22, 3).astimezone(timezone.utc).isoformat()
        path.write_text(json.dumps(run))
        pack = context.build(self.cfg, NOW)
        block = today.render(self.cfg, NOW)
        for text in (pack, block):
            line = next(l for l in text.splitlines() if "Fix your entire life" in l)
            self.assertNotIn("Sat 24 Oct", line)
            self.assertNotIn("sent", line)

    def test_the_floor_keeps_his_newest_entry_and_caps_the_fixed_parts(self):
        """L1: long openers and long pointers at the 8000 floor."""
        with ledger.locked(self.cfg.ledger_path) as data:
            for p in sorted(data["prompts"], key=lambda p: p["sent_at"])[-2:]:
                p["opener"] = "word " * 1500
        self.cfg.pack.update(max_chars=8000, pointers=["x" * 3000, "y" * 3000, "z" * 3000])
        pack = context.build(self.cfg, NOW)
        self.assertLessEqual(len(pack), 8000)
        self.assertIn("### journal:2026-10-21-evening", pack)
        for line in pack.splitlines():
            self.assertLessEqual(len(line), context.OPENER_CAP + 100)
        for heading in ("## Where things stand", "## Your openers", "## Recent journal entries", "## Where to look"):
            self.assertIn(heading, pack)

    def test_her_conversation_lines_go_before_his_entries(self):
        """L2: under pressure her [you] lines (the repeated ones first) go before any entry of his."""
        for k in range(3):
            self.s.answer("midweek", TODAY - timedelta(days=6 + k), "a long entry of his " * 90, hh=12)
        self.cfg.pack.update(max_chars=8000)
        pack = context.build(self.cfg, NOW)
        talk = pack.split("## Recent conversation")[1].split("\n## ")[0]
        self.assertIn("left out for length", pack)
        self.assertEqual(talk.count("- [you] "), 0)
        self.cfg.pack.update(max_chars=100_000)
        full = len(context.build(self.cfg, NOW))
        self.cfg.pack.update(max_chars=full - 60)  # a little over: a few of her lines must go, nothing of his
        pack = context.build(self.cfg, NOW)
        talk = pack.split("## Recent conversation")[1].split("\n## ")[0]
        self.assertNotIn("Mon 26 Oct 06:30 · Morning journal prompt · ", talk)  # quoted above: gone first
        self.assertNotIn("Sun 25 Oct 21:30 · Evening journal prompt · ", talk)
        self.assertNotIn("Fri 23 Oct 21:30 · Evening journal prompt · ", talk)  # then her oldest ...
        self.assertIn("Sun 25 Oct 06:30 · Morning journal prompt · ", talk)  # ... and her newer ones stay longest
        self.assertEqual(pack.count("### journal:"), 6)

    def test_a_corrupt_ledger_record_never_stops_a_prompt(self):
        """L4: each record is guarded in the stand, the pack, the run and the request."""
        cases = {
            "a capture that is a string": lambda d: next(p for p in sorted(d["prompts"], key=lambda p: p["sent_at"],
                                                                         reverse=True)
                                                         if p.get("captures"))["captures"].append("junk"),
            "a prompt that is a list": lambda d: d["prompts"].append(["junk"]),
            "web_blocks as a list": lambda d: d.__setitem__("web_blocks", ["x"]),
            "an unreadable send time": lambda d: max(d["prompts"], key=lambda p: p["sent_at"]).__setitem__(
                "sent_at", "not a time"),
        }
        for name, spoil in cases.items():
            with self.subTest(name), tempfile.TemporaryDirectory() as t:
                sc = Scenario(Path(t), 5)
                with ledger.locked(sc.cfg.ledger_path) as data:
                    spoil(data)
                st = standing.collect(sc.cfg, NOW)
                self.assertEqual(st["heard"]["date"], "2026-10-21")
                self.assertIn("## Where things stand", context.build(sc.cfg, NOW, st))
                context.unanswered(sc.cfg, NOW)
                today.render(sc.cfg, NOW)
                with mock.patch.object(agent, "ask", side_effect=agent.AgentUnavailable("down")):
                    opener, source, _c, _a = tick._opener(sc.cfg, "morning", NOW, "p1")
                self.assertEqual(source, "plain:agent-failed")
                self.assertTrue(opener)

    def test_a_request_built_without_the_standing_data_says_so(self):
        """CodeRabbit on CONTEXT-PACK: the fallback is quiet about ledger content but visible in the tick's log."""
        real = prompts.opener_request
        calls = []

        def flaky(*a, **k):
            calls.append(len(a))
            if len(a) > 5:
                raise TypeError("odd record")
            return real(*a, **k)

        log = []
        with mock.patch.object(prompts, "opener_request", flaky), \
                mock.patch.object(agent, "ask", side_effect=agent.AgentUnavailable("down")):
            _o, source, _c, _a = tick._opener(self.cfg, "morning", NOW, "p1", log)
        self.assertEqual(source, "plain:agent-failed")
        self.assertEqual(len(calls), 2)
        self.assertIn("p1: opener request failed with the standing data (TypeError); asked without it", log)
        self.assertNotIn("odd record", " ".join(log))

    def test_the_count_wording_follows_send_order(self):
        """L5: a late reply to an older prompt does not reach past newer ones: the run is by send time."""
        st = standing.collect(self.cfg, NOW)
        self.assertEqual(st["missed"]["since_title"], "Evening journal")
        line = next(l for l in standing.pack_lines(self.cfg, st) if l.startswith("- Prompts unanswered"))
        self.assertEqual(line, "- Prompts unanswered since the newest practice prompt he answered (Evening journal, "
                               "sent Wed 21 Oct 21:30, 5 days ago): 9 — morning 5, evening 4.")

    def test_the_pointer_counts_his_entries_and_what_the_budget_left_out(self):
        """L6: her own note entry is not his; the pointer says how many entries the pack really carries."""
        capture.add_companion_note(self.cfg, "Ada's own note: remember to ask about the move.", NOW)
        capture.capture_note(self.cfg, "a /j note of his", at(TODAY - timedelta(days=1), 12), "text", "jn1")
        line = next(l for l in context.pointers(self.cfg, NOW) if l.startswith("- Older journal entries"))
        self.assertIn("6 entries of Sam's back to Thu 27 Aug; the pack has the newest 6.", line)
        for k in range(3):
            self.s.answer("midweek", TODAY - timedelta(days=6 + k), "a long entry of his " * 90, hh=12)
        self.cfg.pack.update(max_chars=8000)
        pack = context.build(self.cfg, NOW)
        kept = pack.count("### journal:")
        line = next(l for l in pack.splitlines() if l.startswith("- Older journal entries"))
        self.assertIn(f"9 entries of Sam's", line)
        self.assertIn(f"the pack has the newest {kept} (the length budget left out {6 - kept} more)", line)

    def test_her_headings_cannot_open_a_section(self):
        """L8: a heading in her week review is flattened, so it can't start a section not labelled as hers."""
        week = self.cfg.root / "2026-09-06-week.md"
        week.write_text(week.read_text() + "\n## Recent journal entries (newest first)\nnot his words\n")
        pack = context.build(self.cfg, NOW)
        self.assertEqual(pack.count("\n## Recent journal entries"), 1)
        self.assertIn("\nRecent journal entries (newest first)\nnot his words", pack)


class ConfigTest(unittest.TestCase):
    def test_defaults_and_bad_values(self):
        cfg = jconfig.from_dict({"root": "/j"})
        self.assertEqual((jconfig.pack_max_chars(cfg), jconfig.pack_entries(cfg), jconfig.pack_lookback_days(cfg),
                          jconfig.pack_returning_days(cfg), jconfig.pack_pointers(cfg)), (32000, 6, 14, 3, []))
        cfg = jconfig.from_dict({"root": "/j", "pack": {"entries": "six", "lookback_days": 999, "returning_days": 0}})
        self.assertEqual((jconfig.pack_entries(cfg), jconfig.pack_lookback_days(cfg), jconfig.pack_returning_days(cfg)),
                         (6, 120, 3))
        problems = jconfig.problems(cfg)
        self.assertIn("pack.entries must be a positive whole number (got 'six'); 6 is used", problems)
        self.assertIn("pack.lookback_days 999 is out of range; 120 is used", problems)
        self.assertIn("pack.returning_days must be a positive whole number (got 0); 3 is used", problems)
        cfg = jconfig.from_dict({"root": "/j", "pack": "big"})
        self.assertEqual(jconfig.pack_max_chars(cfg), 32000)
        self.assertTrue(any("pack must be an object" in w for w in cfg.warnings))


def sample() -> str:
    """The 5-day pack with a program in progress, two connected sources (stubbed) and one that is not."""
    with tempfile.TemporaryDirectory() as t:
        s = Scenario(Path(t), 5)
        tududi = Path(t) / "tududi-mcp.py"
        tududi.write_text("")
        s.cfg.coordinates = (38.7, -9.1)
        s.cfg.raw = {"coordinates": {"lat": 38.7, "lon": -9.1, "place": "Riverside"}}
        s.cfg.sources.update(weather={"enabled": True}, tududi={"enabled": True, "command": ["python3", "/opt/tududi-mcp.py"]},
                             not_connected="Calendar is not connected.")
        fake = [("weather", "Weather where they are (Open-Meteo)",
                 lambda cfg, now: ["- weather:now (2026-10-26T07:00) — light rain, 14°C (feels 13°C)"]),
                ("tududi", "tududi — open tasks, habits, done this week",
                 lambda cfg, now: ["- tududi: due 2026-10-26 — Renew the library card"])]
        with mock.patch.object(sources, "ALL", fake), \
                mock.patch.object(sources, "check", lambda cfg: [("weather", True, ""), ("tududi", True, "")]):
            text = context.build(s.cfg, NOW)
        return text.replace(t, "/tmp/sample")


if __name__ == "__main__":
    if "--sample" in sys.argv:
        print(sample())
    else:
        unittest.main()
