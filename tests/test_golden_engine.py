"""What the engine does with the four built-in rituals must stay byte-identical when it is keyed by Practice (WP2).

``tests/golden/`` gains, captured on the commit before WP2 (``8249283``) with the string-keyed engine:

- ``<kind>.entry.md``: the entry file two replies to a sent prompt produce (front matter, title, the prompt
  section with its "woven in" line and folded bank, his words), through ``capture.capture``;
- ``<kind>.folded.html``: the edits a fold and a re-send make (header, the opener as sent, a status line), for two
  shapes of names;
- ``<kind>.opener_request.txt``: the opener request the companion is asked, with a missed run named;
- ``<kind>.send.json``: the ledger record, the ``sent.jsonl`` line and the mirror call of a manual send with no
  model (the plain opener), without the two send-time fields;
- ``schedule.due.txt``: every send a week of five-minute ticks makes (grace window included), and three days
  across a DST change.

A change here is a change to what he receives or to what the engine records about it, so it is never made by
accident. ``python3 tests/test_golden_engine.py --write`` rewrites the files from the code as it is: do that only
on purpose, with the reason in the commit."""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import helpers  # noqa: F401  (puts src/ on the path; keeps Telegram, the model and the mirror unreachable)

from journal import agent, capture, compose, context, ledger, library, mirror, prompts, schedule, telegram, tick
from journal.config import Config, Names

GOLDEN = Path(__file__).resolve().parent / "golden"
KINDS = ("morning", "evening", "midweek", "weekly")
IST = ZoneInfo("Asia/Kolkata")
FIXED = datetime(2026, 10, 14, 23, 0, tzinfo=IST)  # a Wednesday
NAMES = Names(user="Sam", companion="Ada", emoji="🌿", pronouns="he/him/his", frontmatter_source="journal")
OPENER = "## A heading the model wrote\nTea & <toast>? **Bold** and _soft_ — a line."
STATUSES = ("answered ✓", "missed — reply anytime", "sent again below")
VOLATILE = ("sent_at", "sent_at_local")  # the wall clock of the send, not the schedule


def cfg_in(tmp: Path, **kw) -> Config:
    cfg = Config(root=tmp / "journal", state_dir=tmp / "state", chat_id="1001", names=NAMES, timezone="Asia/Kolkata",
                 location="Lakeside", **kw)
    cfg.root.mkdir(parents=True, exist_ok=True)
    cfg.sources["weather"] = {"enabled": False}
    return cfg


def render_entry(kind: str) -> str:
    """Two replies to a sent prompt, through the capture path the hook uses."""
    with tempfile.TemporaryDirectory() as t:
        cfg = cfg_in(Path(t))
        items = library.builtins()[kind].items()
        first = next(iter(items))
        sent = FIXED.astimezone(timezone.utc)
        with ledger.locked(cfg.ledger_path) as data:
            data["prompts"].append({
                "id": f"2026-10-14-{kind}", "stem": f"2026-10-14-{kind}", "kind": kind, "local_date": "2026-10-14",
                "chat_id": cfg.chat_id, "message_id": "50", "sent_at": sent.isoformat(), "sent_at_local": "23:00 IST",
                "opener": OPENER, "opener_source": "ada",
                "asked": [{"id": first, "question": items[first], "as": "Asked in other words"}]})
        info = capture.capture(cfg, cfg.chat_id, "50", "First words.", sent + timedelta(minutes=12), "text", "51")
        capture.capture(cfg, cfg.chat_id, "50", "A later reply.", sent + timedelta(minutes=40), "text", "52")
        return Path(info["path"]).read_text(encoding="utf-8")


def render_folded(kind) -> str:
    """Each fold for two shapes of names, with the opener the prompt went out with and with none (a record without)."""
    return "\n".join(compose.telegram_html(kind, FIXED, o, s, n) for o in (OPENER, None)
                     for n in (Names(), NAMES) for s in STATUSES) + "\n"


def render_request(kind) -> str:
    cfg = Config(root=Path("/journal"), state_dir=Path("/state"), timezone="Asia/Kolkata", location="Lakeside",
                 names=NAMES)
    missed = [{"kind": "evening", "sent_at": "2026-10-13T17:31:00+00:00"},
              {"kind": "morning", "sent_at": "2026-10-14T00:31:00+00:00"}]
    return prompts.opener_request(cfg, kind, FIXED, "PACK", library.builtins()[kind].bank_with_ids(), missed) + "\n"


class _Bot:
    def __init__(self):
        self.calls, self.next_id = [], 700

    def __call__(self, cfg, method, payload):
        self.calls.append((method, payload))
        self.next_id += 1
        return {"message_id": self.next_id}


def render_send(kind) -> str:
    """A manual send with the model unreachable: what the ledger, ``sent.jsonl`` and the mirror get."""
    with tempfile.TemporaryDirectory() as t:
        cfg = cfg_in(Path(t))
        mirrored = []
        bot = _Bot()

        def unreachable(*a, **k):
            raise agent.AgentUnavailable("no model in this test")
        with mock.patch.object(telegram, "_call", bot), mock.patch.object(telegram, "token", lambda c: "T"), \
                mock.patch.object(agent, "ask", unreachable), mock.patch.object(context, "build", lambda c, n, *a: "PACK"), \
                mock.patch.object(mirror, "to_chat", lambda c, what, text, log, ref=None: mirrored.append(
                    {"what": what.rsplit(" at ", 1)[0], "text": text, "ref": list(ref or ())})):
            log = []
            tick.send_due(cfg, FIXED + timedelta(minutes=1), log, only=kind)
            tick.send_due(cfg, FIXED + timedelta(minutes=9), log, only=kind)  # a re-send: the first is refolded
        record = ledger.find(ledger.snapshot(cfg.ledger_path), f"2026-10-14-{kind}")
        lines = [json.loads(l) for l in (cfg.state_dir / "sent.jsonl").read_text(encoding="utf-8").splitlines()]
        out = {"record": {k: v for k, v in record.items() if k not in VOLATILE},
               "sent": [{k: v for k, v in l.items() if k not in VOLATILE} for l in lines],
               "telegram": [[m, p] for m, p in bot.calls], "mirror": mirrored,
               "log": [l for l in log]}
        return json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True) + "\n"


def render_due() -> str:
    """Every send five-minute ticks make: a week from Monday 07:00 IST (the morning still inside its grace
    window), then three days across the end of daylight saving in Chicago."""
    lines = []
    for tz, start, days in (("Asia/Kolkata", datetime(2026, 10, 12, 7, 0), 8),
                            ("America/Chicago", datetime(2026, 10, 31, 0, 0), 3)):
        cfg = Config(root=Path("/journal"), state_dir=Path("/state"), timezone=tz)
        sent: set[str] = set()
        t = start.replace(tzinfo=ZoneInfo(tz)).astimezone(timezone.utc)
        end = t + timedelta(days=days)
        lines.append(f"# {tz}")
        while t < end:
            now_local = t.astimezone(cfg.tz)
            for kind, at in schedule.due(cfg, now_local, sent):
                pid = f"{at.date().isoformat()}-{kind}"
                sent.add(pid)
                lines.append(f"{now_local.isoformat()} {pid} {at.isoformat()}")
            t += timedelta(minutes=5)
    return "\n".join(lines) + "\n"


def golden(name: str) -> str:
    return (GOLDEN / name).read_text(encoding="utf-8")


RENDERERS = {"entry.md": render_entry, "folded.html": render_folded, "opener_request.txt": render_request,
             "send.json": render_send}


class GoldenEngineTest(unittest.TestCase):
    def test_entry_files(self):
        for k in KINDS:
            with self.subTest(k):
                self.assertEqual(render_entry(k), golden(f"{k}.entry.md"))

    def test_folded_headers(self):
        for k in KINDS:
            with self.subTest(k):
                self.assertEqual(render_folded(k), golden(f"{k}.folded.html"))

    def test_opener_requests(self):
        for k in KINDS:
            with self.subTest(k):
                self.assertEqual(render_request(k), golden(f"{k}.opener_request.txt"))

    def test_manual_send_records(self):
        for k in KINDS:
            with self.subTest(k):
                self.assertEqual(render_send(k), golden(f"{k}.send.json"))

    def test_schedule(self):
        self.assertEqual(render_due(), golden("schedule.due.txt"))


def write() -> None:
    for k in KINDS:
        for suffix, fn in RENDERERS.items():
            (GOLDEN / f"{k}.{suffix}").write_text(fn(k), encoding="utf-8")
    (GOLDEN / "schedule.due.txt").write_text(render_due(), encoding="utf-8")


if __name__ == "__main__":
    if "--write" in sys.argv:
        write()
    else:
        unittest.main()
