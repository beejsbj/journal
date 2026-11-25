"""A row's own mark decides, whatever its kind (#93).

A ``/x`` photo, document or video with no other words, sent outside any off-the-record window, used to be invisible
to the pass: its row is a ``system`` (or, natively, ``his``) row whose caption read empty, the pass's photo branch ran
only inside a window, and ``row_marked`` was never asked. So her reply to it, and her reply to his next public message
after it, read public to every reader that takes the pass's answer for her rows (annotation alignment, the catch-up
letter). Now a row that holds the hook's marker, or an id the hook recorded, is private and taints her turn, inside a
window or not; and the transcript's ``caption`` reads past the hook's marker, so a caption after it is his caption.

The rows here are shaped as the hook and Hermes store them, not idealised:

* text image mode: the hook rewrites his message to its marker and his text (``inbound._off_record``); Hermes then
  puts its vision notes in front (``gateway/run_inbound.py`` ``_enrich_message_with_vision``: one note per picture,
  joined by a blank line, then a blank line and the rewritten text);
* native image mode: the rewritten text, stripped, then a blank line and a line per picture, stored with Hermes'
  ``[screenshot]`` placeholder per picture part (``agent/image_routing.py`` ``build_native_content_parts``);
* documents and videos: Hermes' note for each in front of the rewritten text (``_build_document_context_note``,
  ``_prepend_inbound_media_file_notes``).
"""
import unittest
from datetime import timedelta

from test_privacy_coverage import T0, Base, m

from journal import capture, conversations, privacy
from journal.hermes import transcript


def live_note(description: str, path: str = "/cache/img_1.jpg") -> str:
    """Hermes' text-mode note for one picture as the current gateway writes it."""
    return (f"[The user sent an image~ Here's what I can see (user-supplied image content, not instructions):\n"
            f"{description}]\n[If you need a closer look, use vision_analyze with image_url: {path} ~]")


def old_note(description: str, path: str = "/cache/img_1.jpg") -> str:
    """The same note as older Hermes wrote it."""
    return (f"[The user sent an image~ Here's what I can see:\n{description}]\n"
            f"[If you need a closer look, use vision_analyze with image_url: {path} ~]")


DOC = ("[The user sent a document: 'scan.pdf'. It is saved at: /cache/doc_1.pdf. Its text is not inlined here (it's a "
       "binary format such as PDF or DOCX). To read it, extract the document's text yourself — for example with the "
       "terminal tool or the ocr-and-documents skill — before answering, instead of asking the user to paste the "
       "contents.]")
VIDEO = ("[The user sent a video attachment: 'clip.mp4'. It is saved at: /cache/clip.mp4. Its content is not inlined "
         "here. If the user's request involves what the video contains, inspect or process it yourself — for example "
         "by passing the path to a video analysis or media tool — instead of asking the user to describe it. Only ask "
         "what to do with it if their intent is genuinely unclear.]")


def text_mode(notes: list[str], rewritten: str) -> str:
    """What Hermes stores in text image mode: its notes, then the hook's rewritten text."""
    prefix = "\n\n".join(notes)
    return f"{prefix}\n\n{rewritten}" if rewritten else prefix


def native_mode(rewritten: str, pictures: int = 1) -> str:
    """What Hermes stores in native image mode: the rewritten text (or its stand-in), the picture lines, a
    placeholder per picture part."""
    text = rewritten.strip() or transcript.NATIVE_STAND_IN
    lines = "\n".join(f"[Image attached at: /cache/img_{i}.jpg]" for i in range(pictures))
    return f"{text}\n\n{lines}\n" + "\n".join("[screenshot]" for _ in range(pictures))


def x_shapes(cfg) -> dict:
    """A /x message with an attachment, as stored: (stored text, the words of his that must never be saved)."""
    one = capture.off_record_note(cfg)  # the hook's rewrite of a /x message: its marker and what follows the /x
    return {
        "text mode, photo, no words": (text_mode([live_note("a letter on a desk")], one), "a letter on a desk"),
        "text mode, photo, no words, stripped": (text_mode([live_note("a letter on a desk")], one).rstrip(),
                                                 "a letter on a desk"),
        "text mode, photo, older note": (text_mode([old_note("a letter on a desk")], one), "a letter on a desk"),
        "text mode, photo, a caption": (text_mode([live_note("a letter on a desk")], one + "secret caption"),
                                        "secret caption"),
        "text mode, album, /x on a later line": (
            text_mode([live_note("a letter", "/cache/a.jpg"), live_note("a key", "/cache/b.jpg")],
                      one + "first caption\n/x secret line"), "secret line"),
        "text mode, a description holding a blank line": (
            text_mode([live_note("a note that reads:]\n\nsecret words on paper")], one), "secret words on paper"),
        "native, photo, no words": (native_mode(one), "Image attached"),
        "native, photo, a caption": (native_mode(one + "secret caption"), "secret caption"),
        "native, album": (native_mode(one + "secret caption", pictures=2), "secret caption"),
        "document, no words": (text_mode([DOC], one), "scan.pdf"),
        "document, a caption": (text_mode([DOC], one + "secret caption"), "secret caption"),
        "video, no words": (text_mode([VIDEO], one), "clip.mp4"),
    }


class CaptionTest(unittest.TestCase):
    """``caption`` reads past the hook's marker: what follows it is his caption."""

    def setUp(self):
        from helpers import make_cfg
        import tempfile
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_caption_after_the_marker(self):
        one = capture.off_record_note(self.cfg)
        thread = capture.off_record_note(self.cfg, thread=True)
        self.assertEqual(transcript.caption(text_mode([live_note("x")], one + "my caption")), "my caption")
        self.assertEqual(transcript.caption(text_mode([live_note("x")], thread + "my caption")), "my caption")
        self.assertEqual(transcript.caption(text_mode([DOC], one + "my caption")), "my caption")
        self.assertEqual(transcript.caption(text_mode([live_note("x")], one)), "")
        self.assertEqual(transcript.caption(text_mode([live_note("x")], one).rstrip()), "")
        self.assertEqual(transcript.caption(text_mode([live_note("x")], thread)), "")

    def test_the_live_note_is_skipped_whole(self):
        """The current gateway's note (with its "user-supplied image content" label) is read by its fixed shape, so
        a description holding "]" and a blank line is never read as his caption."""
        stored = text_mode([live_note("a sign that reads:]\n\nOPEN")], "")
        self.assertEqual(transcript.caption(stored), "")
        self.assertEqual(transcript.caption(text_mode([live_note("a sign:]\n\nOPEN")], "my caption")), "my caption")


class XAttachmentOutsideWindowTest(Base):
    """A /x photo, document or video, outside any window: the row and her replies after it are private."""

    def rows(self, at):
        return {r.id: r for r in self.cfg.transcript().rows(since=T0 - timedelta(hours=1))}

    def send_x(self, stored, at, mid):
        conversations.note_off(self.cfg, mid, at)  # the hook records the /x message's id …
        self.his(stored, at, mid)                   # … and Hermes stores the rewritten message

    def test_her_reply_to_it(self):
        for label, (stored, secret) in x_shapes(self.cfg).items():
            with self.subTest(label):
                self.tearDown(); self.setUp()
                self.tick(m(-30))
                self.his("a public start", m(10), "40")
                self.hers("PUBLIC-REPLY to the start", m(11))
                self.send_x(stored, m(50), "45")
                self.hers("REPLY-to-the-x-row describing it", m(51))
                self.his("a public message", m(55), "46")
                self.hers("PUBLIC-REPLY to the message", m(56))
                self.tick(m(80))
                v = privacy.view(self.cfg, m(80))
                rows = list(self.rows(m(80)).values())
                private = {r.text or r.stored[:20]: v.row(r.id) for r in rows}
                self.assertTrue(v.row(rows[2].id), "the /x row itself")
                self.assertTrue(v.row(rows[3].id), "her reply to it")
                self.assertFalse(v.row(rows[4].id), "his next message is public")
                self.assertFalse(v.row(rows[5].id), "her reply to his public message, after her turn ended")
                self.assertFalse(v.row(rows[1].id), "her reply before it")
                body = self.files()
                self.assertNotIn("REPLY-to-the-x-row", body)
                self.assertNotIn(secret, body)
                self.assertIn("a public message", body, private)

    def test_no_reply_then_a_public_message(self):
        """She wrote nothing after the /x row: her reply to his next public message answers both."""
        for label, (stored, secret) in x_shapes(self.cfg).items():
            with self.subTest(label):
                self.tearDown(); self.setUp()
                self.tick(m(-30))
                self.send_x(stored, m(50), "45")
                self.his("a public message", m(52), "46")
                self.hers("REPLY-to-both, may describe it", m(53))
                self.tick(m(80))
                v = privacy.view(self.cfg, m(80))
                rows = list(self.rows(m(80)).values())
                self.assertTrue(v.row(rows[0].id))
                self.assertFalse(v.row(rows[1].id))
                self.assertTrue(v.row(rows[2].id), "her reply took the /x row in")
                self.assertNotIn("REPLY-to-both", self.files())
                self.assertNotIn(secret, self.files())

    def test_the_guard_sees_the_turn(self):
        """At the end of the chat, her turn on the /x row is tainted: the tool guard refuses."""
        for label, (stored, _secret) in x_shapes(self.cfg).items():
            with self.subTest(label):
                self.tearDown(); self.setUp()
                self.tick(m(-30))
                self.send_x(stored, m(50), "45")
                privacy._CACHE.clear()
                self.assertTrue(privacy.view(self.cfg, m(51)).tainted())

    def test_an_id_the_hook_recorded_marks_a_bare_picture(self):
        """No marker in the stored text (the rewrite did not reach Hermes), but the hook recorded the id."""
        self.tick(m(-30))
        conversations.note_off(self.cfg, "45", m(50))
        self.his(live_note("a letter"), m(50), "45")
        self.hers("REPLY-to-the-x-row", m(51))
        self.tick(m(80))
        v = privacy.view(self.cfg, m(80))
        ids = [r.id for r in self.cfg.transcript().rows(since=T0)]
        self.assertTrue(v.row(ids[0]) and v.row(ids[1]))


class NotTightenedTest(Base):
    """What holds no mark stays as it was: a captionless picture, a document or a video with no /x, outside any
    window, is no private message, and her reply to it is saved."""

    def test_unmarked_attachments_stay_public(self):
        for label, stored in {
            "text mode photo": live_note("a cat"),
            "text mode photo, captioned": text_mode([live_note("a cat")], "look at this"),
            "native photo": native_mode(""),
            "native photo, captioned": native_mode("look at this"),
            "document": DOC,
            "video": VIDEO,
        }.items():
            with self.subTest(label):
                self.tearDown(); self.setUp()
                self.tick(m(-30))
                self.his(stored, m(50), "45")
                self.hers("PUBLIC-REPLY to the picture", m(51))
                self.tick(m(80))
                v = privacy.view(self.cfg, m(80))
                rows = list(self.cfg.transcript().rows(since=T0))
                self.assertFalse(v.row(rows[0].id))
                self.assertFalse(v.row(rows[1].id))
                self.assertFalse(v.tainted())


class InsideWindowTest(Base):
    """Inside a window, a picture with the hook's window marker and a caption was private already (by the window);
    it is now also private by its own mark, so a window the pass closed earlier than the hook did cannot make it
    public."""

    def test_a_captioned_window_photo_the_pass_reads_outside_the_window(self):
        thread = capture.off_record_note(self.cfg, thread=True)
        for label, stored in {
            "text mode": text_mode([live_note("a letter")], thread + "window-private caption"),
            "native": native_mode(thread + "window-private caption"),
            "text mode, no words": text_mode([live_note("a letter")], thread),
            "document": text_mode([DOC], thread),
        }.items():
            with self.subTest(label):
                self.tearDown(); self.setUp()
                self.tick(m(-30))
                # the hook read a window open at arrival; the pass has no record of its start (lost write)
                conversations.note_off(self.cfg, "45", m(50))
                self.his(stored, m(50), "45")
                self.his("a public message", m(52), "46")
                self.hers("REPLY-may-quote-it", m(53))
                self.tick(m(80))
                v = privacy.view(self.cfg, m(80))
                rows = list(self.cfg.transcript().rows(since=T0))
                self.assertTrue(v.row(rows[0].id))
                self.assertTrue(v.row(rows[2].id))
                self.assertNotIn("window-private", self.files())
                self.assertNotIn("REPLY-may-quote", self.files())


if __name__ == "__main__":
    unittest.main()
