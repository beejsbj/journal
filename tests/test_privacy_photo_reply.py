"""#10: a captionless photo inside a bare-/x window, and her reply to his next public message.

A captionless photo inside a window is private, and her turn takes it in: her reply describing it is private. When
she wrote nothing after the photo, the pass kept that mark until her turn ended, so her reply to his next *public*
message, after the window had closed, was kept off the record too. The owner decided (2026-11-24, DECISIONS
X-PHOTO-REPLY) to save that reply, accepting that it may describe the photo. ``DecidedTest`` is that one case;
``StillPrivateTest`` is every input around it, each at least as private as before (each passes on the pass before #10
too).
"""
from helpers import add_prompt
from test_images import note as image_note
from test_privacy_coverage import Base, m

from journal import capture, conversations, notebook, privacy

REPLY = "Her reply to the public message."


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

    def window_with_photo(self, photo=""):
        """A bare /x the gateway answered at minute 2, then a photo with no caption at minute 3, and no row of hers
        after it. The window closes at minute 48 (quiet_minutes, 45, after the photo)."""
        self.tick(m(-30))
        self.his("a question before it", m(0), "40")
        self.hers("Sure, go on.", m(1))
        conversations.note_off(self.cfg, None, m(2), thread_start=True)
        self.his(photo, m(3), "42")


class DecidedTest(PhotoReplyBase):
    """The one case the owner changed: her reply to his next public message after the window is saved."""

    def test_her_reply_to_his_next_public_message_is_saved(self):
        for photo in ("", image_note("window-private picture of a letter")):
            with self.subTest(native=not photo):
                self.tearDown()
                self.setUp()
                self.window_with_photo(photo)
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
        self.his("", m(5), "44")
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

    def test_the_turn_is_no_longer_tainted_for_the_tool_guard(self):
        """The end state follows the rows: before his public message her next turn would take the photo in; after
        it, her turn is on that message (the journal's writing tools are no longer refused in it)."""
        self.window_with_photo()
        privacy._CACHE.clear()
        self.assertTrue(privacy.view(self.cfg, m(55)).tainted())
        self.his("a public message after the window", m(60), "43")
        privacy._CACHE.clear()
        self.assertFalse(privacy.view(self.cfg, m(60.5)).tainted())


    def test_compaction_copies(self):
        """After the decided case, a compaction copies the rows in place: the photo's copy is private, her saved
        reply's copy is not written twice, and a copy of her private answer in the window stays private."""
        self.window_with_photo()
        self.hers("window-private: noted.", m(3.5))  # (her answer to the photo: that photo's turn has ended)
        self.his("", m(5), "44")  # a second photo she did not answer
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.tick(m(70))
        self.assertEqual(self.files().count(REPLY), 1)
        self.db.sql("update messages set active = 0")
        self.hers("[CONTEXT COMPACTION — REFERENCE ONLY] Earlier turns were compacted", m(120))
        self.his("", m(120), "42")
        self.hers("window-private: noted.", m(120))
        self.his("", m(120), "44")
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


class StillPrivateTest(PhotoReplyBase):
    """Every input around the decided case: each stays at least as private as before #10."""

    def assert_reply_private(self, at=m(62)):
        self.assertIn(REPLY, self.private(at))

    def test_a_photo_with_a_caption_is_his_private_words(self):
        self.window_with_photo(image_note("a picture") + "\n\nwindow-private caption")
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()
        self.assertIn(self.photo_row(m(62)).id, privacy.view(self.cfg, m(62)).private)

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
        self.tick(m(-30))
        conversations.note_off(self.cfg, None, m(2), thread_start=True)
        self.his("window-private words", m(2.5), "41")
        self.his("", m(3), "42")
        self.his("a public message after the window", m(60), "43")
        self.hers(REPLY, m(61))
        self.assert_reply_private()

    def test_a_private_message_after_the_photo(self):
        self.window_with_photo()
        self.his("window-private words after it", m(4), "41")
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
        self.window_with_photo(f"[Image attached at: /c/x.jpg]\n[screenshot]\n{notes}\n/x keep this out")
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
        self.tick(m(-30))
        conversations.note_off(self.cfg, None, m(2), thread_start=True)
        self.his("window-private words", m(3), "41")
        self.hers("window-private reply", m(4))
        self.his("a public message after the window", m(100), "43")
        self.his("", m(30), "42")  # sent at minute 30, stored now
        self.hers(REPLY, m(101))
        self.assert_reply_private(m(102))
