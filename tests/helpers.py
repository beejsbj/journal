"""Shared test helpers. Importing this module puts ``src/`` on the path and makes any real Telegram
call fail loudly, so no test can reach the network."""
import json
import os
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from journal import channel, ledger, telegram  # noqa: E402
from journal.config import Config, Names  # noqa: E402

ENV_KEYS = ("HERMES_HOME", "JOURNAL_CONFIG", "JOURNAL_STATE_DIR", "JOURNAL_PRACTICES_DIR", "JOURNAL_HERMES_SETTINGS",
            "JOURNAL_TELEGRAM_TOKEN", "JOURNAL_SERVE_TOKEN", "JOURNAL_SERVE_BIND", "XDG_CONFIG_HOME", "XDG_STATE_HOME")


import logging  # noqa: E402

_quiet = logging.getLogger("journal")
_quiet.addHandler(logging.NullHandler())
_quiet.propagate = False  # the hooks log expected failures (fail-open paths); keep test output clean


def _no_network(*a, **k):
    raise AssertionError("a test tried to call the real Telegram API")


def _no_agent(*a, **k):
    raise AssertionError("a test tried to run a real agent (hermes chat)")


telegram._call = _no_network
from journal import agent  # noqa: E402

real_ask = agent.ask  # for the tests of agent.ask itself, which stub only the subprocess
agent.ask = _no_agent  # tests that need a model patch journal.agent.ask themselves


class RealMirror(BaseException):
    """BaseException, so mirror.to_chat's best-effort ``except Exception`` cannot swallow it."""


def _no_mirror(*a, **k):
    raise RealMirror("a test tried to write into a real Hermes session (gateway.mirror)")


from journal import mirror  # noqa: E402

mirror._mirror_to_session = _no_mirror  # tests that mirror patch journal.mirror._mirror_to_session themselves

PERSON = Names(user="Theo", companion="Iris", emoji="🌻", pronouns="he/him/his", frontmatter_source="iris-journal")
CHAT = "100200300"

NEUTRAL = Names(user="Sam", companion="Ada", emoji="🌿", pronouns="he/him/his", frontmatter_source="ada-journal")


def guard_cfg(home: Path) -> Config:
    """A config for the guard's tests, laid out the way a Hermes install is but entirely under a temporary ``home``:
    a neutral profile ``ada``, a journal folder, a config file and a guard that protects them all."""
    hermes = home / ".hermes"
    profile = hermes / "profiles" / "ada"
    return Config(
        root=home / "notes" / "Journal", names=NEUTRAL, chat_id=CHAT,
        state_dir=profile / "plugin-data" / "journal", hermes_home=profile,
        config_file=home / ".config" / "journal" / "ada.json",
        guard={
            "enabled": True,
            "protected_paths": [str(hermes), str(home / ".config" / "ada"), str(home / ".config" / "journal"),
                                str(home / ".local" / "state" / "ada-journal"), str(home / "skills"),
                                str(home / ".config" / "systemd")],
            "allowed_write_paths": [str(profile / "cache" / "scratch")],
            "protected_tokens": [".hermes", "config/ada", "config/journal", "ada-journal"],
            "protect_journal_root": True,
            "block_execute_code": True,
            "restrict_hermes_cli": True,
            "block_system_commands": True,
            "escalate_to": "the operator",
        })


def make_cfg(tmp: Path, names: Names = PERSON, **kw) -> Config:
    cfg = Config(root=tmp / "Journal", state_dir=tmp / "state", chat_id=CHAT, names=names,
                 timezone=kw.pop("timezone", "Asia/Kolkata"), **kw)
    cfg.root.mkdir(parents=True, exist_ok=True)
    return cfg


def add_prompt(cfg, kind="morning", local_date="2026-10-14", sent=None, message_id="100"):
    sent = sent or datetime(2026, 10, 14, 0, 30, tzinfo=timezone.utc)
    p = {"id": ledger.prompt_id(kind, local_date), "kind": kind, "local_date": local_date,
         "chat_id": cfg.chat_id, "message_id": message_id, "sent_at": sent.isoformat(),
         "opener": "Morning 🌻", "opener_source": "iris", "plain_text": "Morning journal\n\n1. How did you sleep?"}
    channel.stamp(cfg, p)  # (a prompt sent on a platform other than Telegram names it, as tick.send_prompt does)
    with ledger.locked(cfg.ledger_path) as data:
        data["prompts"].append(p)
    return p


def write_config(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class CleanEnv(unittest.TestCase):
    """Runs each test with the journal's environment variables cleared (and restored afterwards)."""

    def setUp(self):
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        telegram._reset_token_cache()

    def tearDown(self):
        self._env.stop()
        telegram._reset_token_cache()


# ── a second transcript (WP7): the engine's chat readers over the port, with no SQL in any read ──────────────

import sqlite3  # noqa: E402

_REAL_TRANSCRIPT = Config.transcript


class FakeTranscript:
    """A second ``ports.Transcript``: rows held in memory and filtered in Python. Built from ``Row``s
    (``FakeTranscript(rows)``), or by ``over_sessions`` from a test's sessions fixture, read afresh on every call
    (a test changes its fixture between ticks) with the copies found in Python. Either way the engine's readers
    see only what the port promises; the turn lease and the mirror write, which are not row reading, are the
    Hermes adapter's (``live``)."""

    def __init__(self, rows=(), *, source: str = "fake", load=None, live=None, errors=(OSError,)):
        self.source, self.errors, self._live = source, tuple(errors), live
        self._load = load or (lambda: _Snapshot(sorted(rows, key=lambda r: r.id)))

    @classmethod
    def over_sessions(cls, cfg, db):
        from journal.hermes.transcript import HermesTranscript
        return cls(source=str(db), load=lambda: _read_sessions(cfg, db), live=HermesTranscript(cfg, db),
                   errors=(OSError, sqlite3.Error))

    def available(self) -> bool:
        return self._live.available() if self._live is not None else True

    def rows(self, after_id=0, since=None, upto_id=None):
        snap = self._load()
        snap.refuse(rid for rid, ts in snap.bad if rid > after_id and (upto_id is None or rid <= upto_id)
                    and (since is None or not isinstance(ts, (int, float)) or ts >= since.timestamp()))
        return [r for r in snap.rows if r.id > after_id and (since is None or r.at >= since)
                and (upto_id is None or r.id <= upto_id)]

    def tail(self, n):
        snap = self._load()
        newest = sorted([r.id for r in snap.rows] + [rid for rid, _ts in snap.bad])[-n:] if n > 0 else []
        snap.refuse(rid for rid, _ts in snap.bad if rid in newest)
        return [r for r in snap.rows if r.id in newest]

    def max_id(self):
        return self._load().max_id

    def anchor(self, row_id):
        if not row_id:
            return None
        uid, ts = self._load().anchors.get(int(row_id), (None, None))
        return {"id": row_id, "uid": uid, "ts": ts}

    def first_id(self, since):
        return min((i for i, at in self._load().chat if at is not None and at >= since), default=None)

    def busy(self):
        return self._live.busy() if self._live is not None else False

    def last_activity(self, before):
        return max((r.at for r in self._load().rows if r.at < before), default=None)

    def mirror(self, text, *, flushing=False):
        return self._live.mirror(text, flushing=flushing) if self._live is not None else False


class _Snapshot:
    def __init__(self, rows, max_id=None, anchors=None, chat=None, bad=()):
        self.rows = list(rows)
        self.bad = list(bad)  # (id, stored time) of the chat's rows with no readable time
        self.max_id = max_id if max_id is not None else max((r.id for r in self.rows), default=0)
        self.anchors = anchors if anchors is not None else {r.id: (r.meta.get("uid"), r.at.timestamp())
                                                            for r in self.rows}
        self.chat = chat if chat is not None else [(r.id, r.at) for r in self.rows]

    def refuse(self, ids) -> None:
        """As the adapter: a row with no readable time that a read would return makes the chat unreadable, never a
        skipped row (it could be a bare /x). Rows outside the read (by id, or timed before its ``since``) do not."""
        from journal.ports import ChatUnreadable
        hit = sorted(ids)
        if hit:
            raise ChatUnreadable(f"{len(hit)} row(s) with no readable time (first id {hit[0]})")


def _read_sessions(cfg, db) -> _Snapshot:
    """Every row of a sessions fixture, as Hermes stores them, normalised by the adapter's row reading
    (``to_row``); which rows belong to the chat, their order and the compaction copies are worked out here, in
    Python, not by the adapter's SQL."""
    from datetime import datetime as _dt, timezone as _tz
    from journal.hermes.transcript import to_row
    if not Path(db).is_file():
        raise FileNotFoundError(f"no sessions database at {db}")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        msgs = [dict(r) for r in con.execute("select rowid as rid, * from messages order by rowid")]
        sessions = {r["id"]: dict(r) for r in con.execute("select * from sessions")}
    finally:
        con.close()
    ours = {sid for sid, s in sessions.items() if s.get("source") == cfg.platform and s.get("chat_id") == str(cfg.chat_id)
            and (not cfg.owner_ids or str(s.get("user_id")) in cfg.owner_ids)}  # (the journal's chat: CHANNELS)
    in_chat = [m for m in msgs if m.get("session_id") in ours]
    talk = [m for m in in_chat if m.get("role") in ("user", "assistant")]
    has_pmid = bool(msgs) and "platform_message_id" in msgs[0]
    copies = {}
    for m in talk:  # in place: the same session, role, time, words (and Telegram id), earlier
        same = [c["rid"] for c in msgs if c.get("session_id") == m.get("session_id") and c["rid"] < m["rid"]
                and c.get("timestamp") is not None and c.get("timestamp") == m.get("timestamp")
                and c.get("role") == m.get("role") and (c.get("content") or "") == (m.get("content") or "")
                and (not has_pmid or (c.get("platform_message_id") or "") == (m.get("platform_message_id") or ""))]
        if same:
            copies[m["rid"]] = min(same)
    for m in talk:  # with words, anywhere in the chat: by its Telegram id, or an archived original of hers
        if m["rid"] in copies or not m.get("content") or m.get("timestamp") is None:
            continue
        mid = m.get("platform_message_id") or ""
        for c in talk:
            if c["rid"] >= m["rid"]:
                break
            if not c.get("content") or (c.get("role"), c.get("content"), c.get("platform_message_id") or "") != \
                    (m.get("role"), m.get("content"), mid):
                continue
            archived = c.get("active", 1) == 0 or sessions.get(c.get("session_id"), {}).get("end_reason") == "compression"
            if c.get("timestamp") is not None and c["timestamp"] <= m["timestamp"] and (mid or archived):
                copies[m["rid"]] = c["rid"]
                break
    rows, bad = [], []
    for m in talk:
        row = to_row({"id": m["rid"], "ts": m.get("timestamp"), "role": m.get("role"), "content": m.get("content"),
                      "pmid": m.get("platform_message_id"), "display_kind": m.get("display_kind") or "",
                      "tool_calls": m.get("tool_calls") or "", "uid": m.get("message_uid"),
                      "copy": copies.get(m["rid"]), "display_metadata": m.get("display_metadata") or "",
                      "active": m.get("active")})
        if row is None:  # kept aside: a read that would return it refuses the chat (``_Snapshot.refuse``)
            bad.append((m["rid"], m.get("timestamp")))
            continue
        rows.append(row)

    def at(m):
        try:
            return _dt.fromtimestamp(float(m.get("timestamp")), _tz.utc)
        except (TypeError, ValueError):
            return None
    return _Snapshot(rows, max((m["rid"] for m in msgs), default=0),
                     {m["rid"]: (m.get("message_uid"), m.get("timestamp")) for m in msgs},
                     [(m["rid"], at(m)) for m in in_chat], bad)


class FakeTranscriptMixin:
    """Runs a test with every ``cfg.transcript(db)`` answered by ``FakeTranscript.over_sessions`` on the same
    fixture: the suite runs a second time over a transcript that is not the Hermes adapter's SQL."""

    def setUp(self):
        def transcript(cfg, db=None):  # (a test may call setUp again: always wrap the real resolution)
            chat = _REAL_TRANSCRIPT(cfg, db)
            return chat if chat.source is None else FakeTranscript.over_sessions(cfg, chat.db)
        patch = mock.patch.object(Config, "transcript", transcript)
        patch.start()
        self.addCleanup(patch.stop)
        super().setUp()
