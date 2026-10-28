"""Images: a photo (or an image file) is kept beside its entry like audio, flat and named after the entry, and embedded
in his block above his caption. Off the record nothing is kept. ``/media`` serves the kept pictures, the document
shows them, the tick can describe them (``images.describe``) in her words, never his."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from helpers import CHAT, CleanEnv, add_prompt, make_cfg, real_ask
from serve_helpers import ServeCase
from test_conversations import Base as ConversationBase
from test_conversations import conv, m
from test_inbound import NOW, Spawns, msg
from test_programs import Base as ProgramBase

from journal import agent, capture, conversations, entries, hookstate, images, inbound, ledger, privacy, tick
from journal.hermes import hooks, transcript
from journal.serve import document

PNG = b"\x89PNG\r\n\x1a\n-pixels"


def note(description, path="/cache/img_1.jpg"):
    """Hermes' text-mode note for one picture, as ``_enrich_message_with_vision`` writes it."""
    return (f"[The user sent an image~ Here's what I can see:\n{description}]\n"
            f"[If you need a closer look, use vision_analyze with image_url: {path} ~]")


NO_SIGHT = ("[The user sent an image but I couldn't quite see it this time (>_<) You can try looking at it yourself "
            "with vision_analyze using image_url: /cache/img_2.jpg]")
FAILED = ("[The user sent an image but something went wrong when I tried to look at it~ You can try examining it "
          "yourself with vision_analyze using image_url: /cache/img_3.jpg]")


class UnitTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))
        self.src = Path(self.tmp.name) / "cache" / "img.jpg"
        self.src.parent.mkdir()
        self.src.write_bytes(b"\xff\xd8jpeg")

    def test_the_kinds_kept_and_their_types(self):
        for name, mime in (("a.jpg", "image/jpeg"), ("a.JPEG", "image/jpeg"), ("a.png", "image/png"),
                           ("a.webp", "image/webp"), ("a.heic", "image/heic"), ("a.gif", "image/gif")):
            self.assertEqual(images.media_type(name), mime, name)
        for name in ("a.svg", "a.ogg", "a.md", "a", "a.jpg.exe"):
            self.assertIsNone(images.media_type(name), name)

    def test_the_suffix_follows_the_name_then_the_file_then_the_mime(self):
        self.assertEqual(images.extension("/c/x.bin", "Holiday.HEIC", "image/jpeg"), ".heic")
        self.assertEqual(images.extension("/c/x.PNG", None, "image/jpeg"), ".png")
        self.assertEqual(images.extension("/c/blob", None, "image/webp"), ".webp")
        self.assertEqual(images.extension("/c/blob", None, "", photo=True), ".jpg")  # a Telegram photo: JPEG
        self.assertIsNone(images.extension("/c/blob", None, "image/svg+xml"))
        self.assertIsNone(images.extension("/c/blob", None, ""))  # a document of unknown type is not an image

    def test_names_follow_the_entry_like_audio_with_a_counter_on_collision(self):
        entry = str(self.cfg.root / "2026-10-14-evening.md")
        got = [images.keep(self.cfg, str(self.src), entry, ".jpg") for _ in range(3)]
        self.assertEqual(got, ["2026-10-14-evening.jpg", "2026-10-14-evening-2.jpg", "2026-10-14-evening-3.jpg"])
        self.assertEqual(images.keep(self.cfg, str(self.src), entry, ".png"), "2026-10-14-evening.png")
        self.assertEqual((self.cfg.root / got[0]).read_bytes(), b"\xff\xd8jpeg")
        self.assertEqual((self.cfg.root / got[0]).stat().st_mode & 0o777, 0o644)

    def test_the_size_cap_refuses_and_leaves_nothing(self):
        self.cfg.images["max_mb"] = 0.000001  # ~1 byte
        with self.assertRaises(images.ImageTooLarge):
            images.keep(self.cfg, str(self.src), str(self.cfg.root / "2026-10-14-evening.md"), ".jpg")
        self.assertEqual(list(self.cfg.root.iterdir()), [])

    def test_the_cap_defaults_to_twenty_megabytes_and_a_bad_value_does_not_break_it(self):
        self.assertEqual(images.max_bytes(self.cfg), 20 * 1024 * 1024)
        for bad in (0, -1, "big", None, True):
            self.cfg.images["max_mb"] = bad
            self.assertEqual(images.max_bytes(self.cfg), 20 * 1024 * 1024, bad)

    def test_an_unkept_suffix_is_refused(self):
        with self.assertRaises(ValueError):
            images.keep(self.cfg, str(self.src), str(self.cfg.root / "2026-10-14-evening.md"), ".svg")

    def test_a_description_is_one_short_line_without_quotation_marks(self):
        ok = "A grey cat asleep on a red sofa."
        self.assertEqual(images.valid_description(f"  {ok}\n"), ok)
        self.assertEqual(images.valid_description("It's a dog's bowl."), "It's a dog's bowl.")
        for bad in (None, "", "   ", "two\nlines", "x" * 201, 'A sign that says "open"', "A sign that says “open”",
                    "![[x.jpg]]", "# A heading", "- a list", "`code`",
                    "⚠️ Something went wrong and I couldn't finish this reply (`hermes logs`)."):
            self.assertIsNone(images.valid_description(bad), bad)

    def test_config_says_what_is_wrong(self):
        from journal.config import problems
        self.cfg.images.update(max_mb="lots", describe="yes")
        found = problems(self.cfg)
        self.assertTrue(any("images.max_mb" in p for p in found), found)
        self.assertTrue(any("images.describe" in p for p in found), found)
        self.cfg.images.update(max_mb=5, describe=True)
        self.assertFalse([p for p in problems(self.cfg) if "images." in p])


def stashed(cfg):
    """What the hook has stashed for the conversation writer: the copies on disk and the records."""
    d = cfg.state_dir / "audio-stash"
    return [*(sorted(p.name for p in d.iterdir()) if d.is_dir() else []),
            *sorted((conversations.snapshot(cfg).get("images") or {}))]


class ReceiveBase(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))
        self.spawn = Spawns()
        self.prompt = add_prompt(self.cfg, kind="evening", local_date="2026-10-14", sent=NOW - timedelta(minutes=20),
                                 message_id="700")
        self.cache = Path(self.tmp.name) / "cache"
        self.cache.mkdir()

    def photo(self, name="photo_1.jpg", data=b"\xff\xd8jpeg"):
        path = self.cache / name
        path.write_bytes(data)
        return path

    def recv(self, text="", pictures=None, **kw):
        kw.setdefault("kind", "photo")
        pics = tuple((str(p), "image/jpeg") for p in (pictures if pictures is not None else [self.photo()]))
        return inbound.receive(self.cfg, msg(text, images=pics, has_media=True, **kw), spawn=self.spawn)

    def entry(self, stem="2026-10-14-evening"):
        return (self.cfg.root / f"{stem}.md").read_text(encoding="utf-8")

    def kept(self):
        return sorted(p.name for p in self.cfg.root.iterdir() if p.suffix != ".md")


class ReplyTest(ReceiveBase):
    def test_a_photo_answering_a_prompt_is_kept_named_after_the_entry_and_embedded_above_the_caption(self):
        out = self.recv("the lake at six", reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertTrue(out.text.startswith("[Journal reply") and out.text.endswith("the lake at six"))
        self.assertEqual(self.kept(), ["2026-10-14-evening.jpg"])
        self.assertEqual((self.cfg.root / "2026-10-14-evening.jpg").read_bytes(), b"\xff\xd8jpeg")
        body = self.entry()
        self.assertIn("### 14:30 IST · photo\n\n![[2026-10-14-evening.jpg]]\n\nthe lake at six\n", body)
        self.assertIn("the lake at six", entries.his_words(body))
        cap = ledger.find(ledger.snapshot(self.cfg.ledger_path), self.prompt["id"])["captures"][0]
        self.assertEqual((cap["source"], cap["images"]), ("photo", ["2026-10-14-evening.jpg"]))
        self.assertNotIn("describe", cap)

    def test_the_caption_is_saved_verbatim(self):
        caption = "  The  lake — “half frozen”,\n\n  and *nobody* there.  "
        self.recv(caption, reply_to="700")
        self.assertIn(caption.rstrip(), self.entry())

    def test_a_photo_with_no_caption_is_still_an_answer(self):
        out = self.recv("", reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertIn("### 14:30 IST · photo\n\n![[2026-10-14-evening.jpg]]\n", self.entry())

    def test_more_photos_get_the_next_names_and_an_album_embeds_each(self):
        self.recv("one", reply_to="700")
        self.recv("two", message_id="901", reply_to="700", pictures=[self.photo("a.jpg"), self.photo("b.jpg")])
        self.assertEqual(self.kept(), ["2026-10-14-evening-2.jpg", "2026-10-14-evening-3.jpg",
                                       "2026-10-14-evening.jpg"])
        self.assertIn("![[2026-10-14-evening-2.jpg]]\n\n![[2026-10-14-evening-3.jpg]]\n\ntwo", self.entry())

    def test_an_image_file_keeps_the_senders_suffix(self):
        heic = self.cache / "blob"
        heic.write_bytes(b"heic")
        out = self.recv("from the camera roll", pictures=[heic], kind="document", file_name="IMG_1.HEIC",
                        reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertEqual(self.kept(), ["2026-10-14-evening.heic"])

    def test_the_next_message_after_a_prompt_takes_a_photo_too(self):
        self.recv("no reply pointer", message_id="905")
        self.assertEqual(self.kept(), ["2026-10-14-evening.jpg"])

    def test_a_j_photo_is_a_note_with_its_picture(self):
        out = self.recv("/j the view from the train")
        self.assertTrue(out.text.startswith("[Journal note") and out.text.endswith("the view from the train"))
        self.assertEqual(self.kept(), ["2026-10-14-j.jpg"])
        self.assertIn("### 14:30 IST · photo\n\n![[2026-10-14-j.jpg]]\n\nthe view from the train\n",
                      self.entry("2026-10-14-j"))

    def test_a_redelivered_j_photo_is_the_same_note_not_a_pass_and_not_a_new_one(self):
        first = self.recv("/j the harbour", message_id="901")
        again = self.recv("/j the harbour", message_id="901")
        self.assertEqual((again.action, again.text), (first.action, first.text))
        self.assertTrue(again.text.startswith("[Journal note") and again.text.endswith("the harbour"))
        self.assertEqual([p["id"] for p in ledger.snapshot(self.cfg.ledger_path)["prompts"] if p["kind"] == "j"],
                         ["2026-10-14-j"])
        self.assertEqual(self.kept(), ["2026-10-14-j.jpg"])

    def test_a_bare_j_photo_that_cannot_be_kept_leaves_no_empty_note_behind(self):
        self.cfg.images["max_mb"] = 0.000001
        with self.assertLogs("journal.inbound", "WARNING"):
            out = self.recv("/j")
        self.assertEqual(out, inbound.PASS)
        self.assertEqual([p["id"] for p in ledger.snapshot(self.cfg.ledger_path)["prompts"] if p["kind"] == "j"], [])

    def test_a_bare_j_with_a_photo_is_a_note_of_just_the_picture(self):
        out = self.recv("/j")
        self.assertEqual(out.action, "rewrite")
        self.assertIn("![[2026-10-14-j.jpg]]", self.entry("2026-10-14-j"))

    def test_a_photo_that_answers_nothing_passes_and_is_not_kept(self):
        self.assertEqual(self.recv("hello", reply_to="31337", at=NOW + timedelta(days=3)), inbound.PASS)
        self.assertEqual(self.kept(), [])

    def test_a_picture_too_large_is_not_kept_but_the_caption_is(self):
        self.cfg.images["max_mb"] = 0.000001
        with self.assertLogs("journal.inbound", "WARNING"):
            out = self.recv("too big to keep", reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertEqual(self.kept(), [])
        self.assertIn("### 14:30 IST · text\n\ntoo big to keep", self.entry())

    def test_a_redelivered_photo_is_kept_once(self):
        self.recv("once", reply_to="700")
        self.recv("once", reply_to="700")
        self.assertEqual(self.kept(), ["2026-10-14-evening.jpg"])
        self.assertEqual(self.entry().count("once"), 1)

    def test_a_missing_cache_file_falls_back_to_the_text_path(self):
        out = inbound.receive(self.cfg, msg("caption only", images=(("/nonexistent/x.jpg", "image/jpeg"),),
                                            has_media=True, kind="photo", reply_to="700"), spawn=self.spawn)
        self.assertEqual(out.action, "rewrite")
        self.assertIn("caption only", self.entry())
        self.assertEqual(self.kept(), [])

    def test_describe_on_marks_the_capture_for_the_tick(self):
        self.cfg.images["describe"] = True
        self.recv("x", reply_to="700")
        cap = ledger.find(ledger.snapshot(self.cfg.ledger_path), self.prompt["id"])["captures"][0]
        self.assertEqual(cap["describe"], "pending")


class OffRecordTest(ReceiveBase):
    def test_x_with_a_photo_keeps_nothing(self):
        out = self.recv("/x something private", reply_to="700")
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertEqual(self.kept(), [])
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), [])

    def test_a_x_on_a_later_item_of_a_merged_album_keeps_nothing(self):
        # Hermes joins the items' captions with blank lines into one event
        out = self.recv("the lake\n\n/x the other one", reply_to="700",
                        pictures=[self.photo("a.jpg"), self.photo("b.jpg")])
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertEqual(self.kept(), [])
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), [])

    def test_a_x_on_a_later_line_of_merged_text_is_off_the_record_too(self):
        out = inbound.receive(self.cfg, msg("one thing\n/x another", reply_to="700"), spawn=self.spawn)
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), [])

    def test_a_bare_x_photo_is_one_off_record_message(self):
        out = self.recv("/x", reply_to="700")
        self.assertEqual(out.action, "rewrite")
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), [])

    def test_a_photo_inside_a_bare_x_window_is_not_kept_not_even_as_a_j_less_reply(self):
        self.assertEqual(inbound.receive(self.cfg, msg("/x"), spawn=self.spawn), inbound.PASS)
        later = NOW + timedelta(minutes=2)
        out = self.recv("", message_id="901", reply_to="700", at=later)
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertEqual(sorted(p.name for p in self.cfg.root.iterdir()), [])
        self.assertEqual(stashed(self.cfg), [])

    def test_a_photo_in_the_window_is_not_stashed_for_the_conversation(self):
        conversations.note_off(self.cfg, None, NOW, thread_start=True)
        self.recv("", message_id="902", at=NOW + timedelta(minutes=1))
        self.assertEqual(stashed(self.cfg), [])

    def test_a_j_photo_in_the_window_is_the_exception_like_any_j(self):
        conversations.note_off(self.cfg, None, NOW, thread_start=True)
        out = self.recv("/j kept on purpose", message_id="903", at=NOW + timedelta(minutes=1))
        self.assertTrue(out.text.startswith("[Journal note"))
        self.assertEqual(self.kept(), ["2026-10-14-j.jpg"])

    def test_a_failing_off_record_record_still_keeps_nothing(self):
        with mock.patch.object(conversations, "note_off", side_effect=OSError("read-only")):
            out = self.recv("/x private", reply_to="700")
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertEqual(self.kept(), [])


class ProgramTest(ProgramBase):
    def test_a_photo_in_a_program_phase_is_kept_in_the_session_entry_and_settles(self):
        self.start()
        rid = self.the_run()["id"]
        cache = Path(self.tmp.name) / "p.jpg"
        cache.write_bytes(b"\xff\xd8phase")
        spawns = Spawns()
        out = inbound.receive(self.cfg, msg("what I see", images=((str(cache), "image/jpeg"),), has_media=True,
                                            kind="photo", message_id="88", at=self.now), spawn=spawns)
        self.assertTrue(out.text.startswith("[Journal reply — program reply"))
        self.assertEqual(spawns.calls, [("program", "settle", rid)])
        entry = self.entry()
        stem = entry.stem
        body = entry.read_text(encoding="utf-8")
        self.assertIn(f"![[{stem}.jpg]]\n\nwhat I see", body)
        self.assertTrue((self.cfg.root / f"{stem}.jpg").is_file())
        self.assertEqual(entries.phase_blocks(body)[1], ["what I see"])  # his words: the embed is the engine's line
        self.assertEqual(hookstate.summary(self.cfg)["pending_programs"], 1)

    def test_a_captionless_photo_in_a_phase_is_a_block_with_no_words(self):
        self.start()
        cache = Path(self.tmp.name) / "p.jpg"
        cache.write_bytes(b"x")
        spawns = Spawns()
        inbound.receive(self.cfg, msg("", images=((str(cache), "image/jpeg"),), has_media=True, kind="photo",
                                      message_id="89", at=self.now), spawn=spawns)
        body = self.entry().read_text(encoding="utf-8")
        self.assertEqual(entries.phase_blocks(body, keep_empty=True)[1], [""])
        self.assertEqual(entries.phase_blocks(body), {})
        self.assertEqual(len(spawns.calls), 1)

    def test_an_off_record_photo_in_a_phase_keeps_nothing_and_leaves_it_open(self):
        self.start()
        cache = Path(self.tmp.name) / "p.jpg"
        cache.write_bytes(b"x")
        spawns = Spawns()
        out = inbound.receive(self.cfg, msg("/x private", images=((str(cache), "image/jpeg"),), has_media=True,
                                            kind="photo", message_id="90", at=self.now), spawn=spawns)
        self.assertTrue(out.text.startswith("[Off the record"))
        self.assertEqual(spawns.calls, [])
        self.assertEqual([p.name for p in self.cfg.root.iterdir() if p.suffix == ".jpg"], [])


class HookTest(CleanEnv):
    def event(self, **kw):
        base = dict(source=SimpleNamespace(platform="telegram", chat_id=CHAT), message_id="5", text="cap",
                    reply_to_message_id=None, message_type="PHOTO", media_urls=[], media_types=[], raw_message=None)
        base.update(kw)
        return SimpleNamespace(**base)

    def test_a_photo_event_carries_its_pictures_into_the_inbound(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.jpg", Path(d) / "b"
            a.write_bytes(b"1")
            b.write_bytes(b"2")
            got = hooks.to_inbound(self.event(media_urls=[str(a), str(b)], media_types=["image/jpeg", ""]),
                                   datetime.now(timezone.utc))
        self.assertEqual((got.kind, got.images), ("photo", ((str(a), "image/jpeg"), (str(b), ""))))
        self.assertIsNone(got.media_path)

    def test_an_image_document_counts_and_a_pdf_or_audio_does_not(self):
        with tempfile.TemporaryDirectory() as d:
            heic, pdf, ogg = Path(d) / "x.heic", Path(d) / "x.pdf", Path(d) / "x.ogg"
            for f in (heic, pdf, ogg):
                f.write_bytes(b"1")
            doc = self.event(message_type="DOCUMENT", media_urls=[str(heic)], media_types=["application/octet-stream"])
            self.assertEqual(images.from_event(doc), ((str(heic), "application/octet-stream"),))
            self.assertEqual(images.from_event(self.event(message_type="DOCUMENT", media_urls=[str(pdf)],
                                                          media_types=["application/pdf"])), ())
            self.assertEqual(images.from_event(self.event(message_type="VOICE", media_urls=[str(ogg)],
                                                          media_types=["audio/ogg"])), ())

    def test_a_bug_reading_the_pictures_never_fails_the_inbound(self):
        with mock.patch.object(images, "from_event", side_effect=RuntimeError("boom")):
            got = hooks.to_inbound(self.event(text="/x private"), datetime.now(timezone.utc))
        self.assertEqual((got.images, got.text), ((), "/x private"))


class CaptionTest(unittest.TestCase):
    """His words after Hermes' picture notes, read structurally: each note ends at its own fixed trailer, never at the
    first blank line after a ``]`` (the vision model's description may hold both)."""

    def test_one_note_then_a_caption(self):
        self.assertEqual(transcript.caption(note("A lake.") + "\n\nthe walk"), "the walk")

    def test_a_note_alone_has_no_caption(self):
        self.assertEqual(transcript.caption(note("A lake.")), "")

    def test_an_album_of_notes_then_a_caption(self):
        text = note("A lake.", "/c/a.jpg") + "\n\n" + note("A dog.", "/c/b.jpg") + "\n\nour trip to the lake"
        self.assertEqual(transcript.caption(text), "our trip to the lake")

    def test_a_description_with_a_closing_bracket_and_a_blank_line_stays_inside_the_note(self):
        self.assertEqual(transcript.caption(note("A sign: [OPEN]\n\nBelow it, a menu.")), "")
        self.assertEqual(transcript.caption(note("A sign: [OPEN]\n\nBelow it, a menu.") + "\n\nthe diner"), "the diner")

    def test_the_two_failure_notes(self):
        self.assertEqual(transcript.caption(NO_SIGHT), "")
        self.assertEqual(transcript.caption(FAILED + "\n\nthe pier"), "the pier")
        self.assertEqual(transcript.caption(note("A lake.") + "\n\n" + NO_SIGHT + "\n\nboth"), "both")

    def test_a_caption_that_opens_with_a_bracket_is_his(self):
        self.assertEqual(transcript.caption(note("A lake.") + "\n\n[draft] the walk"), "[draft] the walk")

    def test_gateway_text_after_the_notes_is_not_his(self):
        self.assertEqual(transcript.caption(note("A lake.") + "\n\n[Note: model was just switched]"), "")

    def test_a_document_note_still_ends_at_its_blank_line(self):
        self.assertEqual(transcript.caption("[The user sent a document: 'a.pdf'. It is saved at: /c/a.pdf]\n\nthe form"),
                         "the form")


class BusyPathPrivacyTest(CleanEnv):
    """A picture's caption is his typed text even though Hermes stores the row as a placeholder (``system``): the pass
    must read ``/x`` in it, for a row the hook never saw (sent while the companion was mid-turn)."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = make_cfg(Path(self.tmp.name))

    def rows(self, caption):
        t0 = datetime(2026, 10, 15, 14, 0, tzinfo=timezone.utc).timestamp()
        raw = [{"id": 1, "ts": t0, "role": "user", "content": note("A lake.") + "\n\n" + caption, "pmid": "601"},
               {"id": 2, "ts": t0 + 5, "role": "assistant", "content": "Lovely.", "pmid": None}]
        return [transcript.to_row(r) for r in raw]

    def private(self, caption):
        return sorted(privacy.compute(self.cfg, self.rows(caption), [], [], set()).private)

    def test_an_x_caption_makes_the_row_and_her_reply_private(self):
        self.assertEqual(self.private("/x not for the journal"), [1, 2])

    def test_a_x_on_a_later_line_of_a_merged_caption_does_too(self):
        self.assertEqual(self.private("the lake\n\n/x the other one"), [1, 2])

    def test_a_bare_x_caption_is_one_message_not_a_window(self):
        v = privacy.compute(self.cfg, self.rows("/x"), [], [], set())
        self.assertEqual((sorted(v.private), v.window), ([1, 2], None))

    def test_an_ordinary_caption_stays_public(self):
        self.assertEqual(self.private("the lake"), [])


class ConversationTest(ConversationBase):
    """A photo in ordinary chat goes into the conversation's entry the same way."""

    def photo_row(self, caption, at, mid):
        """The row Hermes stores when it describes the picture itself (text mode; gateway/run_inbound.py)."""
        self.his(note("A lake at dusk.") + (f"\n\n{caption}" if caption else ""), at, mid)

    def stash(self, mid, at, data=b"\xff\xd8chat"):
        cache = Path(self.tmp.name) / f"cache_{mid}.jpg"
        cache.write_bytes(data)
        conversations.stash_images(self.cfg, mid, [(str(cache), ".jpg")], at)
        cache.unlink()  # Hermes' cache is gone by now; the stash kept a copy

    def test_a_photo_in_chat_is_kept_beside_the_entry_and_embedded_in_the_conversation(self):
        self.his("one thing", m(0), "570")
        self.stash("571", m(2))
        self.photo_row("the walk", m(2), "571")
        self.hers("Lovely.", m(3))
        self.tick(m(8))
        body = self.note().read_text()
        self.assertIn("**19:32** · photo\n\n![[2026-10-15-j.jpg]]\n\nthe walk", conv(body))
        self.assertEqual((self.cfg.root / "2026-10-15-j.jpg").read_bytes(), b"\xff\xd8chat")
        self.assertEqual(conversations.snapshot(self.cfg).get("images"), {})
        self.assertEqual(list((self.cfg.state_dir / "audio-stash").iterdir()), [])

    def test_a_captionless_photo_in_chat_is_written_from_its_stash(self):
        self.his("one thing", m(0), "570")
        self.stash("572", m(2))
        self.photo_row("", m(2), "572")
        self.tick(m(8))
        self.assertIn("**19:32** · photo\n\n![[2026-10-15-j.jpg]]", conv(self.note().read_text()))

    def test_a_captionless_photo_Hermes_attached_natively_is_written_from_its_stash(self):
        # native image input adds no placeholder: the stored row is empty (or the caption alone)
        self.his("one thing", m(0), "570")
        self.stash("577", m(2))
        self.his("", m(2), "577")
        self.tick(m(8))
        self.assertIn("**19:32** · photo\n\n![[2026-10-15-j.jpg]]", conv(self.note().read_text()))
        self.assertEqual(conversations.snapshot(self.cfg).get("images"), {})

    def test_a_native_photo_with_a_caption_keeps_the_caption_as_his_words(self):
        self.his("one thing", m(0), "570")
        self.stash("578", m(2))
        self.his("the walk", m(2), "578")
        self.tick(m(8))
        self.assertIn("**19:32** · photo\n\n![[2026-10-15-j.jpg]]\n\nthe walk", conv(self.note().read_text()))

    def test_an_empty_row_with_no_stash_is_still_nothing(self):
        self.his("one thing", m(0), "570")
        self.his("", m(2), "579")
        self.tick(m(8))
        self.assertNotIn("photo", conv(self.note().read_text()))

    def test_a_text_mode_album_keeps_his_caption_and_none_of_hermes_notes(self):
        self.his("one thing", m(0), "570")
        self.stash("580", m(2))
        self.his(note("A lake.", "/c/a.jpg") + "\n\n" + note("A dog.", "/c/b.jpg") + "\n\nour trip to the lake",
                 m(2), "580")
        self.tick(m(8))
        body = self.note().read_text()
        self.assertIn("![[2026-10-15-j.jpg]]\n\nour trip to the lake", conv(body))
        self.assertEqual(entries.his_words(body).count("vision_analyze"), 0)

    def test_what_the_vision_model_saw_is_never_his_words_even_with_a_bracket_in_it(self):
        self.his("one thing", m(0), "570")
        self.stash("581", m(2))
        self.his(note("A sign: [OPEN]\n\nBelow it, a menu."), m(2), "581")
        self.tick(m(8))
        body = self.note().read_text()
        for leak in ("menu", "vision_analyze", "closer look", "OPEN"):
            self.assertNotIn(leak, entries.his_words(body))
        self.assertIn("**19:32** · photo\n\n![[2026-10-15-j.jpg]]", conv(body))

    def test_a_photo_that_starts_the_thread_starts_the_note_with_its_picture(self):
        self.stash("573", m(0))
        self.photo_row("first thing today", m(0), "573")
        self.tick(m(8))
        body = self.note().read_text()
        self.assertIn("### 19:30 IST · photo\n\n![[2026-10-15-j.jpg]]\n\nfirst thing today", body)

    def test_a_replay_does_not_keep_the_picture_twice(self):
        self.his("one thing", m(0), "570")
        self.stash("574", m(2))
        self.photo_row("again", m(2), "574")
        self.tick(m(8))
        with conversations.locked(self.cfg) as st:
            st["written"] = {}
            st["cursor"] = {}
        self.tick(m(12))
        self.assertEqual(sorted(p.name for p in self.cfg.root.glob("*.jpg")), ["2026-10-15-j.jpg"])

    def test_a_replay_after_the_stash_is_gone_finds_the_names_the_pictures_were_kept_under(self):
        self.his("one thing", m(0), "570")
        self.stash("583", m(2))
        self.photo_row("again", m(2), "583")
        self.tick(m(8))
        state = conversations.snapshot(self.cfg)
        self.assertNotIn("583", state.get("images") or {})  # committed: the stash is gone
        writer = conversations._Writer(self.cfg, state, True, False, "conversation")
        ev = conversations.Event(m(2), 1, "his", key="583", mid="583", text="again", source="photo")
        self.assertEqual(writer._keep_images(ev, str(self.note())), ["2026-10-15-j.jpg"])

    def test_a_merged_album_with_a_later_x_caption_is_not_written_to_the_conversation(self):
        self.his("one thing", m(0), "570")
        self.stash("582", m(2))
        self.his(note("A lake.") + "\n\nthe lake\n\n/x the other one", m(2), "582")
        self.tick(m(8))
        self.assertNotIn("photo", conv(self.note().read_text()))
        self.assertEqual(list(self.cfg.root.glob("*.jpg")), [])
        self.assertNotIn("the other one", self.note().read_text())

    def test_a_photo_with_no_stash_and_no_caption_is_not_a_message(self):
        self.his("one thing", m(0), "570")
        self.photo_row("", m(2), "575")
        self.tick(m(8))
        self.assertNotIn("photo", conv(self.note().read_text()))

    def test_the_stash_ages_out(self):
        self.stash("576", m(0))
        conversations.run(self.cfg, m(0) + timedelta(days=conversations.STASH_DAYS + 1), [])
        self.assertEqual(conversations.snapshot(self.cfg).get("images"), {})
        self.assertEqual(list((self.cfg.state_dir / "audio-stash").iterdir()), [])

    def test_the_hook_stashes_a_chat_photo_only_when_something_writes_the_chat_and_it_is_not_off_record(self):
        cache = Path(self.tmp.name) / "c.jpg"
        cache.write_bytes(b"x")
        pics = ((str(cache), "image/jpeg"),)
        with mock.patch.object(type(self.cfg), "transcript", mock.Mock(return_value=SimpleNamespace(source="fake"))):
            out = inbound.receive(self.cfg, msg("just chatting", images=pics, has_media=True, kind="photo",
                                                message_id="580"))
            self.assertEqual(out, inbound.PASS)
            self.assertIn("580", conversations.snapshot(self.cfg)["images"])
            inbound.receive(self.cfg, msg("/x private", images=pics, has_media=True, kind="photo", message_id="581"))
            self.assertNotIn("581", conversations.snapshot(self.cfg)["images"])


class MediaTest(ServeCase):
    def setUp(self):
        super().setUp()
        root = self.cfg.root
        (root / "2026-10-15-j-2.md").write_text(
            "---\nkind: j\ntype: journal\n---\n\n# Note\n\n## Sam\n\n"
            "### 08:00 IST · photo\n\n![[2026-10-15-j-2.jpg]]\n\nthe harbour at dawn\n\n"
            "### 08:05 IST · photo\n\n![[2026-10-15-j-2.png]]\n![[2026-10-15-j-2-2.webp]]\n\n"
            "## Iris's annotations\n\n_Pending._\n", encoding="utf-8")
        (root / "2026-10-15-j-2.jpg").write_bytes(b"\xff\xd8\xff-jpeg-bytes")
        (root / "2026-10-15-j-2.png").write_bytes(PNG)
        (root / "2026-10-15-j-2-2.webp").write_bytes(b"RIFFxxxxWEBP")
        (root / "2026-10-15-j-2-stray.jpg").write_bytes(b"\xff\xd8unreferenced")
        (root / "2026-10-15-j-2.svg").write_bytes(b"<svg onload=alert(1)/>")
        (root / "notes.jpg").write_bytes(b"\xff\xd8no-date")

    def test_a_referenced_image_is_served_with_its_type(self):
        for name, mime, data in (("2026-10-15-j-2.jpg", "image/jpeg", b"\xff\xd8\xff-jpeg-bytes"),
                                 ("2026-10-15-j-2.png", "image/png", PNG),
                                 ("2026-10-15-j-2-2.webp", "image/webp", b"RIFFxxxxWEBP")):
            code, headers, body = self.request("GET", f"/api/v1/media/{name}")
            self.assertEqual((code, headers["content-type"], body), (200, mime, data), name)
            self.assertNotEqual(headers.get("accept-ranges"), "bytes")

    def test_an_unreferenced_image_or_a_type_not_kept_is_refused(self):
        for name in ("2026-10-15-j-2-stray.jpg",           # on disk, no entry embeds it
                     "notes.jpg",                          # not named after an entry's day
                     "2026-10-15-j-2.svg",                 # never served, even if an entry embedded it
                     "..%2F2026-10-15-j-2.jpg", "2026-10-15-j-2.JPG.exe", "missing.jpg"):
            self.assertEqual(self.request("GET", f"/api/v1/media/{name}")[0], 404, name)

    def test_an_image_needs_the_token_like_everything_else(self):
        self.assertEqual(self.request("GET", "/api/v1/media/2026-10-15-j-2.jpg", token=False)[0], 401)

    def test_the_document_lists_the_pictures_apart_from_his_words(self):
        doc = self.get_json("/api/v1/entries/2026-10-15-j-2")
        a, b = [x for x in doc["blocks"] if x["kind"] == "his"]
        self.assertEqual((a["source"], a["images"], a["text"], a["audio"]),
                         ("photo", ["2026-10-15-j-2.jpg"], "the harbour at dawn", None))
        self.assertEqual((b["images"], b["text"]), (["2026-10-15-j-2.png", "2026-10-15-j-2-2.webp"], ""))

    def test_a_block_without_pictures_has_no_images_field(self):
        doc = self.get_json("/api/v1/entries/2026-10-14-evening")
        self.assertTrue(all("images" not in x for x in doc["blocks"]))

    def test_a_picture_further_down_in_his_words_stays_his_text(self):
        text = "### 09:00 IST · text\n\nlook\n\n![[2026-10-15-j-2.jpg]]\n\nand more\n"
        block = document.words_blocks(text)[0]
        self.assertNotIn("images", block)
        self.assertIn("![[2026-10-15-j-2.jpg]]", block["text"])

    def test_a_text_block_that_opens_with_an_image_embed_shows_it_as_written(self):
        # only the engine's photo blocks put their pictures first; an embed he (or Obsidian) wrote is his text
        block = document.words_blocks("### 09:00 IST · text\n\n![[2026-10-15-j-2.jpg]]\n\nlook\n")[0]
        self.assertNotIn("images", block)
        self.assertIn("![[2026-10-15-j-2.jpg]]", block["text"])
        msg_block = document._his_message("**09:00** — ![[2026-10-15-j-2.jpg]]\nlook", "k")
        self.assertNotIn("images", msg_block)
        self.assertIn("![[2026-10-15-j-2.jpg]]", msg_block["text"])
        photo = document._his_message("**09:00** · photo\n\n![[2026-10-15-j-2.jpg]]\n\nlook", "k")
        self.assertEqual((photo["images"], photo["text"]), (["2026-10-15-j-2.jpg"], "look"))

    def test_a_caption_that_opens_with_an_embed_of_another_file_stays_his_caption(self):
        text = "### 09:00 IST · photo\n\n![[2026-10-15-j.jpg]]\n\n![[Pasted image 2026.png]]\nmy sketch\n"
        block = document.words_blocks(text, "2026-10-15-j")[0]
        self.assertEqual(block["images"], ["2026-10-15-j.jpg"])
        self.assertIn("![[Pasted image 2026.png]]", block["text"])
        album = "### 09:00 IST · photo\n\n![[2026-10-15-j.jpg]]\n\n![[2026-10-15-j-2.jpg]]\n\nboth\n"
        self.assertEqual(document.words_blocks(album, "2026-10-15-j")[0]["images"],
                         ["2026-10-15-j.jpg", "2026-10-15-j-2.jpg"])
        msg_block = document._his_message("**09:00** · photo\n\n![[2026-10-15-j.jpg]]\n\n![[Other.png]]", "k",
                                          stem="2026-10-15-j")
        self.assertEqual(msg_block["images"], ["2026-10-15-j.jpg"])


class DescribeTest(ReceiveBase):
    """The tick asks the companion (no tools, the picture attached) for one short line, written as her note."""

    def setUp(self):
        super().setUp()
        self.cfg.images["describe"] = True
        self.recv("the lake", reply_to="700")
        self.calls = []

    def ask(self, answer):
        def fake(cfg, query, budget, toolsets=None, image=None):
            self.calls.append((query, budget, toolsets, image))
            if isinstance(answer, Exception):
                raise answer
            return answer
        return mock.patch.object(agent, "ask", fake)

    def capture(self):
        return self.capture_at(0)

    def capture_at(self, i):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), self.prompt["id"])["captures"][i]

    def test_a_description_goes_under_her_notes_marked_as_hers_never_in_his_block(self):
        before = self.entry()
        with self.ask("A frozen lake under a pale sky."):
            log = []
            tick.process_images(self.cfg, NOW, log)
        query, budget, toolsets, image = self.calls[0]
        self.assertEqual((toolsets, image), ("none", str(self.cfg.root / "2026-10-14-evening.jpg")))
        body = self.entry()
        self.assertTrue(body.startswith(before.rstrip()[:200]))
        words = entries.his_words(body)
        self.assertNotIn("frozen lake", words)  # not among his words
        self.assertIn("### 14:30 IST · photo\n\n![[2026-10-14-evening.jpg]]\n\nthe lake\n", body)  # his block untouched
        notes = entries.notes_span(body)
        self.assertIn("_Photo 2026-10-14-evening.jpg:_ A frozen lake under a pale sky.", body[notes[0]:notes[1]])
        self.assertEqual(self.capture()["describe"], "done")
        self.assertEqual(log, [f"described 2026-10-14-evening.jpg for {self.prompt['id']}"])

    def test_a_type_the_companion_cannot_take_is_kept_but_never_asked_about_and_does_not_stall_the_rest(self):
        # Hermes' image options do not know HEIC: asking would fail, read as an outage, and defer every picture
        self.recv("two", reply_to="700", message_id="910", pictures=[self.photo("one.heic"), self.photo("two.jpg")])
        with self.ask("A harbour."):
            tick.process_images(self.cfg, NOW, [])
        asked = sorted(Path(c[3]).name for c in self.calls)
        self.assertTrue(all(not n.endswith(".heic") for n in asked), asked)
        self.assertEqual(len(asked), 2)  # the first reply's picture and the album's jpg
        caps = ledger.find(ledger.snapshot(self.cfg.ledger_path), self.prompt["id"])["captures"]
        self.assertEqual([c["describe"] for c in caps], ["done", "done"])
        self.assertTrue(any(p.suffix == ".heic" for p in self.cfg.root.iterdir()))  # kept all the same

    def test_an_entry_with_only_pictures_the_companion_cannot_take_is_settled_without_asking(self):
        self.recv("only", reply_to="700", message_id="911", pictures=[self.photo("solo.heic")])
        with self.ask("A harbour."):
            tick.process_images(self.cfg, NOW, [])
        self.assertEqual(len(self.calls), 1)  # only the first reply's jpg
        self.assertEqual(self.capture_at(1)["describe"], "none")

    def test_nothing_is_asked_when_the_setting_is_off(self):
        self.cfg.images["describe"] = False
        with self.ask("Whatever."):
            tick.process_images(self.cfg, NOW, [])
        self.assertEqual(self.calls, [])

    def test_a_description_with_quotation_marks_or_lines_is_dropped_and_two_tries_end_it(self):
        with self.ask('A sign saying "closed".'):
            tick.process_images(self.cfg, NOW, [])
            self.assertEqual(self.capture()["describe"], "pending")
            tick.process_images(self.cfg, NOW + timedelta(minutes=5), [])
        self.assertEqual(self.capture()["describe"], "none")
        self.assertNotIn("closed", self.entry())
        self.assertEqual(len(self.calls), 2)

    def test_an_unreachable_model_defers_without_using_an_attempt_or_raising(self):
        with self.ask(agent.AgentUnavailable("timed out")):
            log = []
            tick.process_images(self.cfg, NOW, log)
            self.assertEqual(len(self.calls), 1)
            tick.process_images(self.cfg, NOW + timedelta(minutes=5), log)  # inside the 30 minutes: not asked
            self.assertEqual(len(self.calls), 1)
        cap = self.capture()
        self.assertEqual(cap["describe"], "pending")
        self.assertNotIn("describe_attempts", cap)
        self.assertTrue(cap["deferred_since"])
        with self.ask("A lake."):
            tick.process_images(self.cfg, NOW + timedelta(minutes=40), [])
        self.assertEqual(self.capture()["describe"], "done")
        self.assertNotIn("deferred_since", self.capture())

    def test_an_outage_midway_through_an_album_keeps_what_was_described_and_asks_only_for_the_rest(self):
        self.recv("two", reply_to="700", message_id="912", pictures=[self.photo("a.jpg"), self.photo("b.jpg")])
        seen = []

        def flaky(cfg, query, budget, toolsets=None, image=None):
            seen.append(Path(image).name)
            if image.endswith("-3.jpg") and not getattr(flaky, "up", False):
                raise agent.AgentUnavailable("timed out")
            return "A harbour."
        with mock.patch.object(agent, "ask", flaky):
            tick.process_images(self.cfg, NOW, [])  # (the first reply's picture, the album's first, then down)
            flaky.up = True
            tick.process_images(self.cfg, NOW + timedelta(minutes=40), [])
        self.assertEqual(seen.count("2026-10-14-evening-2.jpg"), 1)  # described once, not again after the outage
        self.assertEqual(seen[-1], "2026-10-14-evening-3.jpg")
        self.assertEqual(self.capture_at(1)["describe"], "done")
        self.assertEqual(self.entry().count("_Photo 2026-10-14-evening-2.jpg:_"), 1)

    def test_asking_a_companion_that_cannot_take_a_picture_is_no_sign_the_model_is_reachable(self):
        self.cfg.agent["command"] = ["echo", "{query_file}"]  # no {image}
        agent._answered = False
        self.addCleanup(setattr, agent, "_answered", False)
        self.assertIsNone(real_ask(self.cfg, "describe", 10, "none", image="/x.jpg"))
        self.assertFalse(agent.answered_this_run())

    def test_a_companion_that_cannot_take_a_picture_describes_nothing(self):
        c = agent.CommandCompanion(self.cfg)  # no {image} in the command
        self.cfg.agent["command"] = ["echo", "{query_file}"]
        self.assertIsNone(c.ask("describe", 10, "none", image="/x.jpg"))
        self.cfg.agent["command"] = ["echo", "{query_file}", "--image", "{image}"]
        self.assertTrue(c.takes_image())
        self.assertIn("/x.jpg", c.argv(Path("q.md"), 10, "none", "/x.jpg"))

    def test_the_hermes_companion_attaches_the_picture(self):
        from journal.hermes.companion import HermesCompanion
        argv = HermesCompanion(self.cfg).argv(Path("q.md"), 10, "none", "/x.jpg")
        self.assertEqual(argv[-2:], ["--image", "/x.jpg"])
        self.assertNotIn("--image", HermesCompanion(self.cfg).argv(Path("q.md"), 10, "none"))


if __name__ == "__main__":
    unittest.main()
