"""The companion's private notebook: threads it means to pick up with the user later.

Private to the companion by design: it lives in the engine's state dir, outside the journal, and
only the `journal_notebook` tool and the annotation pass write it. Open items are injected into
every companion turn and into openers. Cleanup is automatic: resolved items move to an archive
file at once, and open items untouched for STALE_DAYS are archived as stale (never deleted).
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import ledger
from .config import Config

STALE_DAYS = 21
MAX_OPEN = 40
INJECT = 15


def _paths(cfg: Config) -> tuple[Path, Path]:
    return cfg.state_dir / "notebook.json", cfg.state_dir / "notebook-archive.jsonl"


def _archive(cfg: Config, items: list[dict]) -> None:
    if not items:
        return
    _, arch = _paths(cfg)
    with open(arch, "a", encoding="utf-8") as fh:
        for it in items:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")


def add(cfg: Config, text: str, source: str = "", bring_up_after: str | None = None,
        now: datetime | None = None) -> dict:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty notebook item")
    if bring_up_after:
        datetime.strptime(bring_up_after, "%Y-%m-%d")
    now = now or datetime.now(timezone.utc)
    item = {"id": secrets.token_hex(3), "text": text[:500], "source": source, "created_at": now.isoformat(),
            "touched_at": now.isoformat(), "bring_up_after": bring_up_after, "status": "open"}
    path, _ = _paths(cfg)
    with ledger.locked(path) as data:
        data.setdefault("items", []).append(item)
        data.pop("prompts", None)
    return item


def resolve(cfg: Config, item_id: str, outcome: str = "", now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    path, _ = _paths(cfg)
    with ledger.locked(path) as data:
        data.pop("prompts", None)
        items = data.setdefault("items", [])
        it = next((i for i in items if i["id"] == item_id), None)
        if it is None:
            raise KeyError(f"no open item {item_id}")
        items.remove(it)
        it.update(status="resolved", resolved_at=now.isoformat(), outcome=(outcome or "").strip()[:500])
    _archive(cfg, [it])
    return it


def open_items(cfg: Config) -> list[dict]:
    path, _ = _paths(cfg)
    data = ledger.snapshot(path)
    return list(data.get("items", []))


def sweep(cfg: Config, now: datetime | None = None) -> int:
    """Archive stale items and keep the open list bounded. Returns how many were archived."""
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=STALE_DAYS)).isoformat()
    path, _ = _paths(cfg)
    gone: list[dict] = []
    with ledger.locked(path) as data:
        data.pop("prompts", None)
        items = data.setdefault("items", [])
        keep = []
        for it in items:
            due = it.get("bring_up_after")
            if it["touched_at"] < cutoff and not (due and due >= now.date().isoformat()):
                it.update(status="stale", archived_at=now.isoformat())
                gone.append(it)
            else:
                keep.append(it)
        if len(keep) > MAX_OPEN:
            keep.sort(key=lambda i: i["touched_at"])
            extra, keep = keep[:-MAX_OPEN], keep[-MAX_OPEN:]
            for it in extra:
                it.update(status="stale", archived_at=now.isoformat())
            gone += extra
        data["items"] = keep
    _archive(cfg, gone)
    return len(gone)


def render(cfg: Config, today: str, limit: int = INJECT) -> str:
    """Compact block of open items for the companion's context; due items first."""
    items = open_items(cfg)
    if not items:
        return ""
    def key(i):
        due = i.get("bring_up_after")
        return (0 if due and due <= today else 1, i["created_at"])
    lines = []
    for it in sorted(items, key=key)[:limit]:
        due = it.get("bring_up_after")
        tag = f" (bring up after {due})" if due else ""
        src = f" [from {it['source']}]" if it.get("source") else ""
        lines.append(f"- {it['id']}: {it['text']}{tag}{src} — noted {it['created_at'][:10]}")
    more = len(items) - len(lines)
    tail = f"\n(+{more} more)" if more > 0 else ""
    who = cfg.names.user or "them"
    return (f"Your private notebook — open threads you meant to pick up with {who}. Raise one when it fits "
            "naturally, never as a checklist; resolve it with journal_notebook once it has come up or no longer "
            "matters.\n" + "\n".join(lines) + tail)


def split_followups(annotation_text: str) -> tuple[str, list[str]]:
    """Pull `FOLLOW-UP: …` lines out of an annotation draft."""
    keep, follow = [], []
    for line in annotation_text.splitlines():
        stripped = line.strip().lstrip("-* ").strip()
        if stripped.upper().startswith("FOLLOW-UP:"):
            follow.append(stripped.split(":", 1)[1].strip())
        else:
            keep.append(line)
    return "\n".join(keep).strip(), [f for f in follow if f][:2]
