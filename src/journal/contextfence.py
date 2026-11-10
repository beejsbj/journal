"""The fence around what the engine adds to the companion's turn.

``turncontext.build`` puts its blocks between ``OPEN`` and ``CLOSE``; the platform adds them to his message for the
model and may store them with it (Hermes keeps a picture's turn as one row: ``hermes/transcript.py``). The fence is how
a reader of the stored chat tells what the engine added from what he wrote. ``HEADS`` are the blocks' own first lines,
for a row stored without the fence. A module of its own, so the chat reader does not load the turn's builders.
"""
from __future__ import annotations

OPEN, CLOSE = "<journal-context>", "</journal-context>"
# The first line of each block (notebook.render, context.pack_for_turn, programview.block, today.header_of)
HEADS = ("Your private notebook — open threads", "## Journal: the context your", "## Journal: program session",
         "## Journal: today — ")


def wrap(text: str) -> str:
    """The engine's context for a turn, between the marks a transcript reader takes off."""
    return f"{OPEN}\n{text}\n{CLOSE}"
