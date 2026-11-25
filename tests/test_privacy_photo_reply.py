"""#10: a captionless photo inside a bare-/x window, and her reply to his next public message.

A captionless photo inside a window is private, and her turn takes it in: her reply describing it is private. When
she wrote nothing after the photo, the pass kept that mark until her turn ended, so her reply to his next *public*
message, after the window had closed, was kept off the record too. The owner decided (2026-11-24, DECISIONS
X-PHOTO-REPLY) to save that reply, accepting that it may describe the photo, and nothing else: the tool guard still
refuses the journal's writes in that turn.

The photo rows are shaped as the hook and Hermes store them (``test_privacy_x_photo``): inside a window the hook
rewrites his message to its window marker and his text and records its id; Hermes puts its vision notes in front (text
image mode) or the picture lines after it (native image mode). ``DecidedTest`` is the decided case in every such
shape; ``StillPrivateTest`` is every input around it, each as private as on the pass before #10, the review's findings
(a caption after the marker, a /x marker, a document, a video, a gateway note, an id the hook recorded with no marker)
among them; ``GuardTest`` holds the tool guard to refusing.
"""
from datetime import datetime
from unittest import mock

from helpers import add_prompt
from test_privacy_coverage import Base, m
from test_privacy_x_photo import DOC, VIDEO, live_note, native_mode, old_note, text_mode

from journal import capture, conversations, notebook, privacy, tools

REPLY = "Her reply to the public message."


def captionless(cfg) -> dict:
    """A captionless picture sent inside a window, as stored: (stored text, whether the hook marked it)."""
    thread = capture.off_record_note(cfg, thread=True)
    return {
        "text mode, the hook's window marker": (text_mode([live_note("window-private picture")], thread), True),
        "text mode, the marker, stripped": (text_mode([live_note("window-private picture")], thread).rstrip(), True),
        "text mode, older note": (text_mode([old_note("window-private picture")], thread), True),
        "text mode, an album": (text_mode([live_note("window-private a", "/c/a.jpg"),
                                           live_note("window-private b", "/c/b.jpg")], thread), True),
        "text mode, no hook": (live_note("window-private picture"), False),
        "native, the hook's window marker": (native_mode(thread), True),
        "native, an album": (native_mode(thread, pictures=2), True),
        "native, no hook": (native_mode(""), False),
    }


def not_captionless(cfg) -> dict:
    """Pictures and attachments inside a window that are not a captionless picture: (stored text, hook-marked)."""
    thread = capture.off_record_note(cfg, thread=True)
    one = capture.off_record_note(cfg)
    return {
        "text mode, a caption after the window marker": (
            text_mode([live_note("a picture")], thread + "window-private caption"), True),
        "text mode, a caption, no hook": (text_mode([live_note("a picture")], "window-private caption"), False),
        "native, a caption after the window marker": (native_mode(thread + "window-private caption"), True),
        "native, a caption, no hook": (native_mode("window-private caption"), False),
        "text mode, the /x marker": (text_mode([live_note("a picture")], one), True),
        "native, the /x marker": (native_mode(one), True),
        "text mode, an album with /x on a later line": (
            text_mode([live_note("a"), live_note("b", "/c/b.jpg")], one + "first\n/x window-private line"), True),
        "a document": (text_mode([DOC], thread), True),
        "a document, no hook": (DOC, False),
        "a video": (text_mode([VIDEO], thread), True),
        "a picture and a document": (text_mode([live_note("a picture"), DOC], thread), True),
        "a gateway note": ("[System note: The previous turn was interrupted by a gateway restart; the gateway is now "
                           "back online.]", False),
        "an id the hook recorded, no marker": (live_note("window-private picture"), True),
        "a picture's turn with words after the marker": (
            text_mode([live_note("a picture")], thread + "\n\nwindow-private second paragraph"), True),
    }


class PhotoReplyBase(Base):
    def private(self, at):
        privacy._CACHE.clear()
        rows = self.cfg.transcript().rows(since=m(-10))
        pv = privacy.view(self.cfg, at)
        return {r.text for r in rows if r.id in pv.private}

    def photo_row(self, at):
        privacy._CACHE.clear()
        rows = self.cfg.transcript().rows(since=m(-10))
        return next(r for r in rows if r.platform_message_id == "42")

    def window_with_photo(self, photo=None, marked=True):
        """A bare /x the gateway answered at minute 2, then a captionless picture at minute 3 (the hook records its
        id and rewrites it when ``marked``), and no row of hers after it. The window closes at minute 48
        (quiet_minutes, 45, after the photo)."""
        if photo is None:
            photo, marked = captionless(self.cfg)["text mode, the hook's window marker"]
        self.tick(m(-30))
        self.his("a question before it", m(0), "40")
        self.hers("Sure, go on.", m(1))
        conversations.note_off(self.cfg, None, m(2), thread_start=True)
        if marked:
            conversations.note_off(self.cfg, "42", m(3))
        self.his(photo, m(3), "42")


class DecidedTest(PhotoReplyBase):
    """The one case the owner changed: her reply to his next public message after the window is saved."""

    def test_her_reply_to_his_next_public_message_is_saved(self):
        for label, (photo, marked) in captionless(self.cfg).items():
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.window_with_photo(photo, marked)
                self.his("a public message after the window", m(60), "43")
                self.hers(REPLY, m(61))
                private = self.private(m(62))
                self.assertNotIn(REPLY, private)
                self.assertIn(self.photo_row(m(62)).id, privacy.view(self.cfg, m(62)).private)  # the photo stays
                self.tick(m(70))
                self.assertIn(REPLY, self.files())
                self.assertIn("a public message after the window", self.files())
                self.assertEqual(self.leaked(), [])

    def test_two_photos_in_the_window(self):
        self.window_with_photo()
        thread = capture.off_record_note(self.cfg, thread=True)
        conversations.note_off(self.cfg, "44", m(5))
        self.his(native_mode(thread), m(5), "44")
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assertNotIn(REPLY, self.private(m(62)))

    def test_a_prompt_send_closes_the_window_and_his_answer_is_the_next_public_message(self):
        """A prompt ends the window as a silence does: his answer to it is his next public message after it."""
        self.window_with_photo()
        add_prompt(self.cfg, kind="evening", local_date="2026-10-15", sent=m(10), message_id="700")
        self.his("my answer to the prompt", m(12), "43")
        self.hers(REPLY, m(13))
        self.assertNotIn(REPLY, self.private(m(14)))

    def test_compaction_copies(self):
        """After the decided case, a compaction copies the rows in place: the photo's copy is private, her saved
        reply's copy is not written twice, and a copy of her private answer in the window stays private."""
        photo, _ = captionless(self.cfg)["text mode, the hook's window marker"]
        self.window_with_photo(photo)
        self.hers("window-private: noted.", m(3.5))  # (her answer to the photo: that photo's turn has ended)
        conversations.note_off(self.cfg, "44", m(5))
        self.his(photo, m(5), "44")  # a second photo she did not answer
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.tick(m(70))
        self.assertEqual(self.files().count(REPLY), 1)
        self.db.sql("update messages set active = 0")
        self.hers("[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted", m(120))
        self.his(photo, m(120), "42")
        self.hers("window-private: noted.", m(120))
        self.his(photo, m(120), "44")
        self.his("a public message after the window", m(120), "43")
        self.hers(REPLY, m(120))
        log = self.tick(m(140))
        self.assertEqual(self.leaked(), [], log)
        self.assertEqual(self.files().count(REPLY), 1)
        privacy._CACHE.clear()
        pv = privacy.view(self.cfg, m(140))
        rows = self.cfg.transcript().rows(since=m(-10))
        for r in rows:
            if r.copy and (r.platform_message_id in ("42", "44") or "window-private" in r.text):
                self.assertIn(r.id, pv.private, r.text)


class GuardTest(PhotoReplyBase):
    """The owner decided to save her reply, not to loosen the guard: in her turn on the message that cleared the
    photo's mark, the journal's writes are still refused (``View.held``), until that turn ends and he writes again."""

    def guard(self, at):
        class Now(datetime):
            @classmethod
            def now(cls, tz=None):
                return at
        privacy._CACHE.clear()
        with mock.patch.object(tools, "datetime", Now):
            return tools._off_record_turn(self.cfg, None)

    def test_the_guard_still_refuses_in_that_turn(self):
        for label, (photo, marked) in captionless(self.cfg).items():
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.window_with_photo(photo, marked)
                self.assertTrue(self.guard(m(55)), "before his message: her next turn takes the photo in")
                self.his("a public message after the window", m(60), "43")
                self.assertTrue(self.guard(m(60.5)), "her turn on the message: the guard refuses")
                self.db.add("let me write that down", m(60.7), role="assistant")  # a tool step of that turn
                self.db.sql("update messages set tool_calls = ? where id = (select max(id) from messages)",
                            '[{"id": "1"}]')
                self.assertTrue(self.guard(m(60.8)))
                self.hers(REPLY, m(61))
                self.assertTrue(self.guard(m(61.5)), "her turn ended, his next message not yet read")
                self.assertNotIn(REPLY, self.private(m(62)))  # … while her reply is saved
                self.his("another public message", m(70), "45")
                self.assertFalse(self.guard(m(70.5)), "a new turn of his: nothing private in it")

    def test_her_rows_after_that_turn_ended_stay_private(self):
        """Only her reply to that message is saved: a row of hers after that turn ended (a failed turn, then a tool
        step of hers with no new message of his) stays private until he writes again, as before #10."""
        self.window_with_photo()
        self.his("a public message after the window", m(60), "43")
        self.db.add("x", m(61), role="assistant", display_kind="failed_turn")
        self.db.add("window-private: about the photo", m(62), role="assistant")
        self.assertIn("window-private: about the photo", self.private(m(63)))
        self.his("another public message", m(70), "45")
        self.hers("a public answer", m(71))
        self.assertNotIn("a public answer", self.private(m(72)))

    def test_a_steered_message_keeps_the_guard_too(self):
        self.window_with_photo()
        self.his("a public message after the window", m(60), "43")
        self.steer("and one more thing", m(60.5), "44")
        self.assertTrue(self.guard(m(60.6)))


class StillPrivateTest(PhotoReplyBase):
    """Every input around the decided case: each stays as private as before #10."""

    def assert_reply_private(self, at=m(62)):
        self.assertIn(REPLY, self.private(at))

    def test_anything_but_a_captionless_picture(self):
        """The review's findings 1 and 2: only a picture row holding nothing beyond Hermes' notes and the hook's
        window marker counts. A caption, the /x marker, a document, a video, a gateway note, words, or an id the hook
        recorded with no marker: her reply to his next public message stays private."""
        for label, (stored, marked) in not_captionless(self.cfg).items():
            with self.subTest(label):
                self.tearDown()
                self.setUp()
                self.window_with_photo(stored, marked)
                self.his("a public message after the window", m(60), "43")
                self.hers(REPLY, m(61))
                self.assert_reply_private()
                self.assertIn(self.photo_row(m(62)).id, privacy.view(self.cfg, m(62)).private)
                self.tick(m(70))
                self.assertNotIn(REPLY, self.files())
                self.assertNotIn("window-private", self.files())

    def test_a_photo_she_answered_before_his_next_message(self):
        """Her answer to the photo stays private; her reply to the next message was saved before #10 too."""
        self.window_with_photo()
        self.hers("window-private: I see the picture.", m(4))
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        private = self.private(m(62))
        self.assertIn("window-private: I see the picture.", private)
        self.assertNotIn(REPLY, private)

    def test_her_turn_began_on_the_photo_without_ending(self):
        """A row of hers after the photo (a tool step, her turn still running): her turn took it in, and her reply
        to the next message may be the same turn."""
        self.window_with_photo()
        self.db.add("window-private: let me look", m(4), role="assistant")
        self.db.sql("update messages set tool_calls = ? where id = (select max(id) from messages)", '[{"id": "1"}]')
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_private_message_waits_with_the_photo(self):
        """A private message in the window before the photo, with no reply of hers yet: her next turn answers it."""
        thread = capture.off_record_note(self.cfg, thread=True)
        self.tick(m(-30))
        conversations.note_off(self.cfg, None, m(2), thread_start=True)
        conversations.note_off(self.cfg, "41", m(2.5))
        self.his(thread + "window-private words", m(2.5), "41")
        conversations.note_off(self.cfg, "42", m(3))
        self.his(text_mode([live_note("a picture")], thread), m(3), "42")
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_private_message_after_the_photo(self):
        self.window_with_photo()
        thread = capture.off_record_note(self.cfg, thread=True)
        conversations.note_off(self.cfg, "41", m(4))
        self.his(thread + "window-private words after it", m(4), "41")
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_second_x_message(self):
        self.window_with_photo()
        conversations.note_off(self.cfg, "43", m(60))
        self.his(capture.off_record_note(self.cfg) + "my secret after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_second_bare_x_window(self):
        self.window_with_photo()
        conversations.note_off(self.cfg, None, m(59), thread_start=True)
        self.his("window-private words in the second window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_j_note_inside_the_window_does_not_clear_it(self):
        """A /j note is public, but it is inside the window: not his next message after it. (Her reply here comes
        after the window closed, a long turn, so only the mark keeps it private.)"""
        self.window_with_photo()
        self.his("/j a public note in the window", m(10), "43")
        self.hers(REPLY, m(60))
        self.assert_reply_private()

    def test_an_opt_out_cut_off_after_a_photo_is_his_private_words(self):
        """A picture's row with no words in front and an opt-out in what was cut off after its placeholders (it
        may be his own words, ``privacy._in_cut``) is no captionless photo: it never clears at his next message."""
        notebook.add(self.cfg, "ask how the garden plan went")
        notes = notebook.render(self.cfg, "2026-10-15")  # her notebook, as the turn carried it: an engine block
        self.window_with_photo(f"[Image attached at: /c/x.jpg]\n[screenshot]\n{notes}\n/x keep this out", False)
        self.assertIn("keep this out", self.photo_row(m(4)).meta.get("cut") or "")
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_message_steered_into_her_running_turn(self):
        """The busy path: his public message came while her turn was running; that turn may be on the photo."""
        self.window_with_photo()
        self.steer("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_queued_message_he_sent_before_the_photo(self):
        """The busy path: a public message stored after the photo but sent before the /x is no message after it."""
        self.window_with_photo()
        self.his("a public message sent before the window", m(1.5), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_queued_photo_and_her_reply_to_it(self):
        """The busy path: a photo sent inside the window, stored after his public message: her reply takes it in."""
        thread = capture.off_record_note(self.cfg, thread=True)
        self.tick(m(-30))
        conversations.note_off(self.cfg, None, m(2), thread_start=True)
        conversations.note_off(self.cfg, "41", m(3))
        self.his(thread + "window-private words", m(3), "41")
        self.hers("window-private reply", m(4))
        self.his("a public message after the window", m(100), "43")
        conversations.note_off(self.cfg, "42", m(30))
        self.his(text_mode([live_note("a picture")], thread), m(30), "42")  # sent at minute 30, stored now
        self.hers(REPLY, m(101))
        self.assert_reply_private(m(102))
