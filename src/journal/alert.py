"""``Alert``: a bounded retry ran out. Raised by the tick's steps and by the chat face's sends (``chat.send``), caught
by ``tick.run``, which reports it once and exits 1 so the scheduler alerts. Its own module so the chat face raises it
without importing the tick."""
from __future__ import annotations


class Alert(RuntimeError):
    """A bounded retry ran out: reported once, and the run exits 1 so the scheduler alerts."""
