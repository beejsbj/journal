"""#10: a captionless photo inside a bare-/x window, and her reply to his next public message.

A captionless photo inside a window is private, and her turn takes it in: her reply describing it is private. When
she wrote nothing after the photo, the pass kept that mark until her turn ended, so her reply to his next *public*
message, after the window had closed, was kept off the record too.
"""
from test_images import note as image_note
from test_privacy_coverage import Base, m

from journal import conversations, privacy


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


class TodayTest(PhotoReplyBase):
    def test_her_reply_to_his_next_public_message_is_kept_off_the_record(self):
        for photo in ("", image_note("window-private picture of a letter")):
            with self.subTest(native=not photo):
                self.tearDown()
                self.setUp()
                self.window_with_photo(photo)
                self.his("a public message after the window", m(60), "43")
                self.hers("Her reply to the public message.", m(61))
                self.assertIn("Her reply to the public message.", self.private(m(62)))
