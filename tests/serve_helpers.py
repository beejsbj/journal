"""A temporary journal with one of everything ``journal serve`` reads, and a live server on an ephemeral loopback
port to ask it. Neutral names throughout (Sam writes, Ada is the companion)."""
import http.client
import json
import tempfile
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from helpers import CleanEnv, make_cfg
from test_sweep import FakeSessions

from journal import ledger, reflect
from journal.config import Names
from journal.serve import app as serve_app
from journal.serve import auth

NEUTRAL = Names(user="Sam", companion="Ada", emoji="🌿", pronouns="they/them/their", frontmatter_source="journal")
IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 15, 9, 12, tzinfo=IST).astimezone(timezone.utc)
HERS = "SECRETHERS"  # a word only the companion said: no search over his words may find it

EVENING = """---
date: 2026-10-14
kind: evening
practice: evening
type: journal
source: journal
---

# Evening journal — Wed 14 Oct 2026

## Prompt

_Sent 23:00 IST · opener: ada_

Evening 🌿

> [!question]- Questions
> **1. Brain Dump & Letting Go**
> - What's spinning in your mind?

## Sam

### 23:12 IST · text

First words, about the lighthouse.

### 23:40 IST · voice transcript · 2:31

![[2026-10-14-evening.ogg]]

A spoken thought.

## Conversation

%% journal:conversation %%

%% journal his:813 %%
**23:44** — Later I thought of the harbour.

%% journal her:4815 %%
> [!quote]- Ada · 23:45
> The harbour sounds like a SECRETHERS word.
> Second line.

%% journal his:816 %%
> [!info]- ↩ replying to
> The harbour sounds like

**23:50** — yes, and the gulls.

## Ada's annotations

- a theme.

## Ada's notes

### 08:02 IST

A note of hers.
"""

SESSION = """---
date: 2026-10-15
kind: fix-life
practice: fix-life
program: fix-life
session: 1
run: 2026-10-15-a1f3
type: journal
source: journal
---

# Fix your life · Session 1 — Thu 15 Oct 2026

## Prompt

> [!question]- Phase 1: Anti-vision

## Sam

%% journal phase:1 %%
### 06:14 IST · text

The life I do not want.

%% journal phase:2 %%
### 06:20 IST · text

The life I want.

## Summary

%% journal:summary %%

Two phases done.

## Ada's annotations

_Pending._
"""

NOTE = """---
date: 2026-10-15
kind: j
type: journal
source: journal
capture: /j
---

# Journal note — Thu 15 Oct 2026

## Prompt

_Freeform — sent with /j._

## Sam

### 08:30 IST · text

call the harbour master
"""

GRATITUDE = """---
kind: practice
id: gratitude
title: Daily gratitude
schedule.time: 21:30
---
- Three things today, and why each one mattered.
- Who made one of them possible?
"""


def build_journal(tmp: Path):
    """The journal: an evening entry (two words blocks, a voice note, a conversation with her keyed reply, notes,
    annotations), a program session entry with phases and a summary, a /j note, the day reflection, a user
    practice, his own hand-written files, an unreferenced audio file and a pack the server must never serve."""
    cfg = make_cfg(tmp, names=NEUTRAL)
    cfg.sources["weather"] = {"enabled": False}
    root = cfg.root
    (root / "2026-10-14-evening.md").write_text(EVENING, encoding="utf-8")
    (root / "2026-10-14-evening.ogg").write_bytes(b"OggS" + bytes(range(60)))
    (root / "2026-10-14-stray.ogg").write_bytes(b"OggS-unreferenced")
    (root / "2026-10-15-fix-life-s01.md").write_text(SESSION, encoding="utf-8")
    (root / "2026-10-15-j.md").write_text(NOTE, encoding="utf-8")
    (root / "2026-10-14.md").write_text("# my own page\n\nprivate harbour thoughts\n", encoding="utf-8")
    (root / "2026-10-14-thoughts.md").write_text("---\nkind: thoughts\n---\n\n## Sam\n\nharbour\n", encoding="utf-8")
    reflection = reflect.render(cfg, "reflection", date(2026, 10, 14),
                                "**What stood out** — the harbour.\n\n**To carry forward** — What does the sea ask?",
                                [root / "2026-10-14-evening.md"], datetime(2026, 10, 15, 4, 0, tzinfo=IST))
    (root / "2026-10-14-reflection.md").write_text(reflection, encoding="utf-8")
    packs = cfg.state_dir / "packs"
    packs.mkdir(parents=True)
    (packs / "2026-10-14-evening.ogg").write_bytes(b"a pack, never served")
    cfg.practices_dir.mkdir(parents=True, exist_ok=True)
    (cfg.practices_dir / "gratitude.md").write_text(GRATITUDE, encoding="utf-8")
    with ledger.locked(cfg.ledger_path) as data:
        data["prompts"].append({
            "id": "2026-10-14-evening", "stem": "2026-10-14-evening", "kind": "evening", "local_date": "2026-10-14",
            "chat_id": cfg.chat_id, "message_id": "500", "sent_at": datetime(2026, 10, 14, 23, 0, tzinfo=IST).isoformat(),
            "sent_at_local": "23:00 IST", "opener": "Evening 🌿", "opener_source": "ada",
            "asked": [{"id": "1.1", "question": "What's spinning in your mind?"}],
            "entry_path": str(root / "2026-10-14-evening.md"),
            "captures": [{"message_id": "501", "at": datetime(2026, 10, 14, 23, 12, tzinfo=IST).isoformat()}]})
    # The chat transcript, which the server must never open: a real sessions database holding a thread (his words
    # and hers) beside the journal, and a persisted privacy pass, so a route that reached either would show.
    db = FakeSessions(tmp / "state.db")
    db.add("a thought I had in the chat", NOW - timedelta(hours=3), "700")
    db.add(f"{HERS} said in the chat", NOW - timedelta(hours=3, minutes=-1), role="assistant")
    cfg.hermes["state_db"] = str(db.path)
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "conversations.json").write_text(json.dumps(
        {"db": str(db.path), "privacy": {"private": {"2": NOW.isoformat()}, "windows": [], "at": NOW.isoformat()}}),
        encoding="utf-8")
    return cfg


EVENING_NOW = datetime(2026, 10, 15, 21, 40, tzinfo=IST).astimezone(timezone.utc)

GRATITUDE_ENTRY = """---
date: 2026-10-15
kind: gratitude
practice: gratitude
type: journal
source: journal
---

# Daily gratitude — Thu 15 Oct 2026

## Prompt

_Sent 21:30 IST · opener: ada_

Three small things tonight, Sam. The walk to the harbour counts.

## Sam

### 21:38 IST · text

The ferry was late and I did not mind. Bread from the corner bakery, still warm.
And the call with my brother, which went on longer than either of us planned.

### 21:39 IST · text

He made the call possible, really. I keep waiting for the right moment and he just rang.
"""


def add_day_of_prompts(cfg) -> None:
    """The evening of 2026-10-15 (``EVENING_NOW``): one prompt in each state, with openers and the practices' own
    questions: the morning prompt missed, a weekly review sent on demand and still open (with no opener: the plain
    one is the practice's), the gratitude prompt answered (with its entry), and the evening one still to come; and
    the day's weather in the tick's cache. ``tools/serve_fixture.py --demo`` shows it."""
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    (cfg.state_dir / "today-cache.json").write_text(json.dumps({"at": EVENING_NOW.isoformat(), "weather": {
        "text": "27°C, light rain; today 24–30°C, rain chance 60%", "at": EVENING_NOW.isoformat()}}), encoding="utf-8")
    root = cfg.root
    (root / "2026-10-15-gratitude.md").write_text(GRATITUDE_ENTRY, encoding="utf-8")

    def sent(hh: int, mm: int) -> str:
        return datetime(2026, 10, 15, hh, mm, tzinfo=IST).isoformat()

    with ledger.locked(cfg.ledger_path) as data:
        data["prompts"] += [
            {"id": "2026-10-15-morning", "stem": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15",
             "chat_id": cfg.chat_id, "message_id": "601", "sent_at": sent(6, 0), "sent_at_local": "06:00 IST",
             "collapse_hours": 6, "opener_source": "ada", "asked": [{"id": "1", "question": "How did you sleep?"}],
             "opener": "Morning, Sam. Yesterday ended on the harbour and the gulls; you said the lighthouse kept "
                       "coming back. Before the day takes over, how did you sleep, and what is sitting on your chest?"},
            {"id": "2026-10-15-weekly", "stem": "2026-10-15-weekly", "kind": "weekly", "local_date": "2026-10-15",
             "chat_id": cfg.chat_id, "message_id": "602", "sent_at": sent(20, 5), "sent_at_local": "20:05 IST",
             "collapse_hours": 24, "opener_source": "plain", "asked": [], "opener": None},
            {"id": "2026-10-15-gratitude", "stem": "2026-10-15-gratitude", "kind": "gratitude",
             "local_date": "2026-10-15", "chat_id": cfg.chat_id, "message_id": "603", "sent_at": sent(21, 30),
             "sent_at_local": "21:30 IST", "opener_source": "ada", "asked": [],
             "opener": "Three small things tonight, Sam. The walk to the harbour counts.",
             "entry_path": str(root / "2026-10-15-gratitude.md"),
             "captures": [{"message_id": "604", "at": sent(21, 38)}]},
        ]


EDITED_AT = "2026-10-12T18:30:00+00:00"


def add_edited_builtin(cfg) -> None:
    """His edit of the midweek built-in (BUILTIN-EDIT): its first question in his words, saved in place, begun from an
    older default whose drift question was worded otherwise, so the page says the default has changed since. The
    design screenshots (``tools/serve_fixture.py --demo``) and the web fixtures show it."""
    from journal import library
    shipped = library.shipped("midweek").files["midweek.md"]
    mine = shipped.replace("- How's your energy been this week?", "- Where has your energy gone this week?")
    library.write(cfg, "midweek", mine, None)
    with library.held(cfg.practices_dir) as state:
        rec = state["defaults"]["midweek"]
        rec["files"]["midweek.md"] = shipped.replace("aligned or drifted?", "on track?")
        rec["sha"] = library._fingerprint(rec["files"])
        rec["at"] = EDITED_AT


class ServeCase(CleanEnv):
    """A server on 127.0.0.1:<ephemeral> over ``build_journal``; ``serve`` overrides go into ``cfg.serve``."""
    serve = {}

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = build_journal(Path(self.tmp.name))
        self.cfg.serve.update(self.serve)
        self.app = serve_app.App(lambda: self.cfg, clock=lambda: NOW, poll_seconds=0.05, ping_seconds=60,
                                 log=lambda line: None)
        ok, why = self.app.prime()
        self.assertTrue(ok, why)
        self.server = serve_app.make_server(self.app, "127.0.0.1", 0, retry_seconds=0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.thread.start()
        self.token = auth.resolve_token(self.cfg)[0]

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
        self.tmp.cleanup()
        super().tearDown()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def request(self, method, path, body=None, headers=None, token=True):
        """(status, headers as a dict with lower-case names, body bytes)."""
        h = {"Host": f"127.0.0.1:{self.port}"}
        if token:
            h["Authorization"] = f"Bearer {self.token}"
        h.update(headers or {})
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        if data is not None:
            h.setdefault("Content-Type", "application/json")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=data, headers=h)
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()

    def get_json(self, path, status=200, **kw):
        code, _h, body = self.request("GET", path, **kw)
        self.assertEqual(code, status, body[:300])
        return json.loads(body) if body else None
