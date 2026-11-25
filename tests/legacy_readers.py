"""The tuple-shaped readers the engine's modules used to carry (``sweep.parse/rows/read``, ``conversations.rows``).

Nothing in ``src/`` calls them any more: the engine reads ``cfg.transcript()`` and ``journal.hermes.transcript``. The
suites that pin the row semantics (tests/golden/hermes_rows.json and the sweep, conversations, mirror and privacy
tests) still ask in these shapes, so they live here, over the adapter, instead of as shims in the engine (issue #50;
DECISIONS.md, AUDIT-LEFTOVERS)."""
from pathlib import Path

from helpers import ROOT  # noqa: F401  (puts src/ on the path)

from journal.config import Config
from journal.hermes.transcript import raw_rows, read  # noqa: F401  (``read``: one stored message, classified)


def sweep_parse(content: str, platform_message_id: str | None) -> dict | None:
    """One stored user message → {message_id, quote, text, source, audio_path} (None: not a candidate)."""
    msg = read(content, platform_message_id)
    if msg["kind"] != "his":
        return None
    return {k: msg[k] for k in ("message_id", "quote", "text", "source", "audio_path")}


def sweep_rows(cfg: Config, db: Path, after_id: int, since: float) -> list[tuple]:
    """(id, ts, platform id, content, display kind) of his rows after ``after_id`` and newer than ``since``."""
    return [(r["id"], r["ts"], r["pmid"], r["content"], r["display_kind"])
            for r in raw_rows(db, cfg.chat_id, after_id, since, platform=cfg.platform, users=cfg.owner_ids)
            if r["role"] == "user" and (r["ts"] is None or float(r["ts"]) > since)]  # (no time: kept, as stored)


def conversation_rows(cfg: Config, db: Path, after_id: int, since_ts: float = 0.0, upto_id: int | None = None) -> list[tuple]:
    """(id, ts, role, content, platform id, display kind, tool calls, uid, copy) for the chat, in stored order."""
    return [(r["id"], r["ts"], r["role"], r["content"], r["pmid"], r["display_kind"], r["tool_calls"], r["uid"],
             r["copy"]) for r in raw_rows(db, cfg.chat_id, after_id, since_ts, upto_id, platform=cfg.platform,
                                         users=cfg.owner_ids)]
