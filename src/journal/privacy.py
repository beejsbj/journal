"""Off the record: one pass over the chat decides what is private, and every reader asks it.

Before this module, privacy was reconstructed in several places at different times (the gateway hook, the
sweep, the conversation writer, the context readers), and each new ordering produced a new gap. Now there is
one authoritative pass, ``compute``, over the chat's rows in the order Hermes stored them (row id), merged by
time with two inputs the database can't show:

* bare ``/x`` commands the gateway answered itself (no row is stored): the hook records their time
  (``conversations.json`` ``off_threads``);
* journal prompt sends (``send_times``): a prompt ends an off-the-record window, as it ends a thread.

For each row the pass decides whether it is private:

* **One message** (``/x <text>``): the hook's marker in the stored text (``capture.OFF_RECORD``), the opt-out
  command itself (a message steered in on the busy path keeps it), or an id the hook recorded.
* **A window** (bare ``/x``, by the hook or as a busy-path row): every row of the chat, both sides, from the
  ``/x`` while the activity keeps going: it closes at the first silence of ``quiet_minutes`` (at the last
  activity plus ``quiet_minutes``) or at the next prompt send, whichever comes first. Windows are half-open
  ``[start, end)``: a message exactly at the end is public. A bare ``/x`` inside an open window keeps it; ``/j``
  is never private (an explicit note). A cron delivery does not end a window. A message queued while she was
  busy is stored after rows with later times and carries the time he sent it: that time is activity of the
  window, and the row is private when it falls inside a window, the open one or one already closed. A bare ``/x``
  stored as a row opens its window at its own time (known before the walk, as the hook's are) and again where the
  walk reaches it, so a message he sent after it is inside the window whichever of the two was stored first.
* **A tainted turn**: once a private row of his is taken in (a message, or a captionless photo inside a window),
  her rows are private until his next message after her turn ended (a text reply, a failed turn, an
  interruption): a public message steered into the same turn does not clear it, since her answer covers both.
  One exception, decided by the owner (#10, DECISIONS X-PHOTO-REPLY): when all she took in is captionless photos
  inside a window and she wrote no row after them, the mark clears at his next public message sent after them and
  outside every window (not steered into a running turn), so her reply to that message is saved, though it may
  describe the photo. The photos themselves stay private.

``View`` holds the result: private row ids (both roles), the windows, and the end state (an open window, a
tainted turn), with ``row``, ``at``, ``open_at`` and ``tainted``. ``view(cfg)`` recomputes the last
``RECOMPUTE`` hours (from the start of a window still open then) on every call, cached for a few seconds while
none of its inputs changes (the chat, the hook's records, the prompt sends), and adds what ``update`` persisted for
older rows, so every reader, in the gateway or the tick, sees the same answer, also for rows the tick hasn't reached
yet. A persisted answer names a row by id and keeps its time: it is not used for a row read under that id with
another time (the database was replaced; its rows are answered by the recompute). An id the view does not read is
kept as it is: it is the only answer for a compaction copy whose original is older than the recompute. ``update``
(each tick, before the sweep) persists the result in one locked write. When the pass can't run, ``view`` returns a
``Closed`` view: everything is private (fail closed); readers then save, show and quote nothing until it can.

**Coverage is proven by rows read, not by a stamp.** A saved pass says when it ran, not what it read: with the
sessions database missing or pointed elsewhere, another chat id, or a clock that ran ahead, a pass is saved over
rows it never saw, and those rows have no saved answer, which reads as public. So a view records where it began
recomputing (``View.start``), and a reader that holds rows older than that asks ``covering`` instead of ``view``:
the same view, re-taken from ``RECOMPUTE`` before its oldest row (for a turn or window begun earlier) unless the
view already began that far back. Only the hook's end state (the last ``RECOMPUTE`` hours, nothing older) asks
``view`` itself; the sweep, the writer and the others ask ``covering``. A compaction copy of a row (the transcript
finds it in the same session, with a new time, or in a rotation's child session: ``Row.copy``) is private when its
original is; a copy of her reply with no original private is still private inside an open window or tainted turn.
A view that holds a copy whose original it did not read, and that no saved answer names private, answers that
original by a pass over a slice around it, from ``RECOMPUTE`` before it to ``RECOMPUTE`` after (saved answers age out,
and are a floor, never proof that a row is public): ``_originals``. What a slice finds private is added to the view,
never taken from it, and the cost is the slices', never a read back to the oldest original.

The pass reads the chat as ``ports.Row``s from ``cfg.transcript()``; what a row is (his words, a notice, a frame,
the end of her turn) the transcript decided, what is private is decided here and only here.
"""
from __future__ import annotations

import bisect
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import capture
from .config import Config
from .ports import Row

RECOMPUTE = timedelta(hours=48)
KEEP_DAYS = 400
SENDS = "send-times.json"  # every prompt send, never pruned (prompt boundaries for any backfill horizon)
_CACHE: dict = {}
CACHE_SECONDS = 5


# ── prompt sends, kept for good ──────────────────────────────────────────────

def remember_sends(cfg: Config, times) -> None:
    """Add prompt send times to the never-pruned list (the ledger and sent.jsonl are pruned at 120 days)."""
    path = cfg.state_dir / SENDS
    known = set(_read_sends(cfg))
    new = {t for t in times if t} - known
    if not new:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(sorted(known | new)), encoding="utf-8")
    tmp.replace(path)


def _read_sends(cfg: Config) -> list[str]:
    try:
        data = json.loads((cfg.state_dir / SENDS).read_text(encoding="utf-8"))
        return [str(x) for x in data] if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def send_times(cfg: Config) -> list[datetime]:
    """Every journal prompt send known: the ledger, sent.jsonl and the never-pruned list."""
    from . import conversations
    return sorted({datetime.fromisoformat(t) for t in [*conversations._send_times(cfg), *_read_sends(cfg)]})


# ── the pass ─────────────────────────────────────────────────────────────────

def _said(row: Row) -> str | None:
    """What of the row is his typed words: the text of his message, or the caption after an image or document
    placeholder (Hermes stores those as a ``system`` row, the placeholder being its own); None for any other row.
    A row withheld from his words (``Row.meta["withheld"]``) is read whole here, so an opt-out in it still counts;
    not a delivery or a platform frame withheld so, which is no message of his, unless it carries a picture he sent
    (``Row.meta["native"]``)."""
    if row.kind == "his" or (row.kind == "system" and row.meta.get("withheld")
                             and (row.meta.get("native") or not (row.meta.get("delivery") or row.meta.get("frame")))):
        return row.text
    return (row.meta.get("caption") or None) if row.kind == "system" else None


def _in_cut(cfg: Config, row: Row) -> bool:
    """Whether a line of what the transcript cut off the row after a picture's placeholders (``Row.meta["cut"]``)
    opens with the opt-out command. That part is read as what the turn added, never as his words; but a row is taken
    for a picture's turn by its shape alone, and a message of his shaped like one would hold his words there. So an
    opt-out in it counts for the whole row, as it did when the row was read whole: when in doubt, private."""
    return any(capture.opt_out(cfg, line) is not None for line in str(row.meta.get("cut") or "").splitlines())


def row_marked(cfg: Config, row: Row, off_ids: set[str]) -> bool:
    """One of his rows off the record by itself: the hook's marker in the stored text, the opt-out command (any
    text after it; on a later line of his words, or of what was cut off after a picture's placeholders: ``_in_cut``),
    or an id the hook recorded."""
    if capture.OFF_RECORD.search(row.stored):
        return True
    text = _said(row)
    if text is not None and (capture.opt_out(cfg, text) is not None or capture.opt_out_inside(cfg, text)):
        return True
    if _in_cut(cfg, row):
        return True
    mid = row.platform_message_id
    return bool(mid) and str(mid) in off_ids


@dataclass
class View:
    private: dict = field(default_factory=dict)    # row id → time (both roles)
    windows: list = field(default_factory=list)    # [start, end) as datetimes; end None while open
    window: dict | None = None                     # the open window at the end: {"since", "last"}
    taint: bool = False                            # his last message taken in was private …
    answered: bool = False                         # … and her turn on it has ended
    bare: datetime | None = None                   # … it is only captionless photos in a window, no row of hers
                                                   #   after them: the newest one's time (#10)
    quiet: timedelta = timedelta(minutes=45)
    sends: list = field(default_factory=list)
    closed: bool = False                           # the pass couldn't run: everything is private
    start: datetime | None = None                  # rows from here were recomputed; older ones: saved results only
    copies: dict = field(default_factory=dict)     # a compaction copy's row id → its original's
    retake: dict | None = None                     # originals answered from older slices (#4): counts, for the log

    def row(self, row_id) -> bool:
        return self.closed or int(row_id) in self.private

    def at(self, t: datetime) -> bool:
        """Whether a moment falls inside an off-the-record window."""
        if self.closed:
            return True
        for start, end in self.windows:
            if start <= t and (t < end if end is not None else self.open_at(t)):
                return True
        return False

    def open_at(self, now: datetime) -> bool:
        """Whether the open window still holds at ``now`` (no silence of quiet_minutes since its last
        activity, no prompt sent since it began)."""
        if self.closed:
            return True
        w = self.window
        if not w or now < w["since"]:
            return False
        if now - w["last"] >= self.quiet:
            return False
        return not any(w["since"] < s <= now for s in self.sends)

    def tainted(self) -> bool:
        """Her current turn took in something private (or the pass couldn't tell)."""
        return self.closed or self.taint


class Closed(View):
    def __init__(self, why: str = ""):
        super().__init__(closed=True)
        self.why = why


def compute(cfg: Config, rows: list[Row], hook_starts: list[datetime], sends: list[datetime],
            off_ids: set[str]) -> View:
    """The pass. ``rows`` as ``cfg.transcript().rows()`` returns them (stored order); ``hook_starts`` the bare /x
    commands the hook recorded; ``sends`` prompt send times. A copy (``Row.copy``: its original's id) is no
    activity of its own: it is private when its original is (and, with no Telegram id to prove it a copy, also
    inside an off-the-record window or turn)."""
    from . import conversations
    v = View(quiet=conversations.quiet(cfg), sends=sorted(sends))
    # a bare /x stored as a row opens its window at its own time too: one queued behind a message he sent after it
    # is stored later, and that message is his private words (#5); the walk still opens one at the stored-order clock
    pending = sorted([*hook_starts, *_busy_starts(cfg, rows)])
    seen = None  # the latest time handled: a queued row can carry an older one
    late = _late(rows)  # the own times of his queued rows, stored after rows with later times

    def close(end: datetime) -> None:
        v.windows.append((v.window["since"], end))
        v.window = None

    def lapse(t: datetime) -> None:
        """Close the open window if it ended before ``t``: a prompt send, or a quiet gap. A gap is a silence of
        ``quiet_minutes`` in his activity and hers by every row's time, a queued row's included (``late``): a
        message he sent inside the window but that was stored later keeps it going, or a row stored between would
        be read as outside it."""
        w = v.window
        if not w:
            return
        sent = [s for s in v.sends if w["since"] < s <= t]
        end = w["last"] + v.quiet
        i = bisect.bisect_left(late, w["since"])
        while i < len(late) and late[i] < end:
            end = max(end, late[i] + v.quiet)
            i += 1
        if sent and sent[0] <= end:
            close(sent[0])
        elif t >= end:
            close(end)

    def closed_at(t: datetime) -> bool:
        """Whether ``t`` falls in a window already closed (a row stored after the window ended can carry a time
        inside it). The windows close in time order and do not overlap, so an in-order row costs one look."""
        for s, e in reversed(v.windows):
            if t >= e:
                return False
            if t >= s:
                return True
        return False

    def inside(t: datetime) -> bool:
        """Whether a row's own time falls in a window: the open one (from its start), or a closed one."""
        return bool(v.window) and t >= v.window["since"] or closed_at(t)

    def start(t: datetime) -> None:
        lapse(t)
        if v.window:
            v.window["last"] = max(v.window["last"], t)
        else:
            v.window = {"since": t, "last": t}

    def private(rid: int, t: datetime) -> None:
        v.private[int(rid)] = t

    for row in rows:
        rid, t = row.id, row.at
        while pending and pending[0] <= t:
            start(pending.pop(0))
        if row.copy:
            v.copies[rid] = row.copy
            if row.copy in v.private:
                private(rid, t)
            elif not row.meta.get("stored_id") and ((row.role == "assistant" and v.taint)
                                                    or (v.window and v.open_at(t)) or closed_at(t)):
                # no Telegram id to prove it the same message (hers, or an old row of his): a repeat of
                # earlier words in an off-the-record turn or window is not told from a copy, so it is private
                private(rid, t)
            continue
        now_t = t if seen is None or t > seen else seen
        seen = now_t
        lapse(now_t)
        if row.role == "assistant":
            v.bare = None  # her turn took the photos in: her reply may describe them
            if v.window:
                v.window["last"] = max(v.window["last"], now_t)  # her activity keeps the window going
            if v.taint or inside(t):
                private(rid, t)
            if row.meta.get("ends_turn") and v.taint:
                v.answered = True
            continue
        if row.meta.get("notice"):
            continue  # notifications, process completions: not his words, no activity of his
        said = _said(row)
        if row.kind in ("mirror", "empty", "system", "boundary") and said is None:
            cut = row.kind in ("empty", "system") and _in_cut(cfg, row)
            if row.kind in ("empty", "system") and (inside(t) or cut):
                # a captionless photo (Hermes' image placeholder, or an empty row in native image mode) inside a
                # window: no words to mark, but the picture, its stash and the vision note are his private
                # message, and it keeps the window going as any message of his does: else her reply to it, or his next
                # message, more than ``quiet_minutes`` after the activity before it, would read as outside the window
                # and be saved. (A gateway row of these kinds extends it too: that errs toward more privacy.) Her
                # turn took it in, as any private message of his: her reply describing it is private, also when the
                # photo was queued and stored after the window ended. (The same for a row with no words in front of
                # a picture's placeholders and an opt-out in what was cut off after them: ``_in_cut``.)
                private(rid, t)
                # only a captionless photo in a window, with nothing else private waiting for her turn, may clear at
                # his next public message (#10); an opt-out cut off after a picture is his private words
                if not inside(t) or cut:
                    v.bare = None
                elif not v.taint or v.answered:
                    v.bare = t  # the first private thing her next turn takes in
                elif v.bare is not None:
                    v.bare = max(v.bare, t)  # another photo of the same kind
                v.taint, v.answered = True, False
                if v.window:
                    v.window["last"] = max(v.window["last"], now_t)
            continue
        rest = capture.opt_out(cfg, said) if said is not None else None
        explicit = said is not None and capture.strip_command(said) is not None  # /j: never private
        if rest is not None and not rest.strip() and row.kind == "his":
            start(now_t)  # a bare /x that came in on the busy path (its own time is in ``pending``: _busy_starts)
            hidden = True
        else:
            # in a window by its own time: a queued message he sent before the /x stays public, and one he sent
            # inside a window that had already closed when it was stored is private
            hidden = row_marked(cfg, row, off_ids) or (inside(t) and not explicit)
        if v.window:
            v.window["last"] = max(v.window["last"], now_t)
        if hidden:
            private(rid, t)
            v.taint, v.answered, v.bare = True, False, None
        elif v.taint and v.answered:
            v.taint, v.answered, v.bare = False, False, None  # a new turn of his: her private one is over
        elif v.taint and v.bare is not None and t > v.bare and not inside(t) and not row.meta.get("steer"):
            # the owner's decision (#10): captionless photos in a window she wrote nothing after are no turn of hers
            # yet; his next public message after the window starts one, and her reply to it is saved
            v.taint, v.answered, v.bare = False, False, None
    while pending:
        start(pending.pop(0))
    if v.window:
        v.windows.append((v.window["since"], None))
    return v


def _busy_bare(cfg: Config, row: Row) -> bool:
    """A bare /x that came in on the busy path: a stored row of his words that is the opt-out command alone."""
    if row.copy or row.role == "assistant" or row.kind != "his" or row.meta.get("notice"):
        return False
    rest = capture.opt_out(cfg, row.text)
    return rest is not None and not rest.strip()


def _busy_starts(cfg: Config, rows: list[Row]) -> list[datetime]:
    """The own times of the bare /x rows (``_busy_bare``): a start known before the walk, as the hook's are, so a
    message he sent after the /x is inside its window whatever order the two were stored in."""
    return [row.at for row in rows if _busy_bare(cfg, row)]


def _late(rows: list[Row]) -> list[datetime]:
    """The own times, sorted, of his rows stored after a row with a later time (a message queued while she was busy
    carries the time he sent it) that count as his activity in ``compute``: not a copy, not a platform notice, not
    a mirror or delivery with no words of his."""
    out, top = [], None
    for row in rows:
        if row.copy:
            continue
        if top is not None and row.at < top and row.role == "user" and not row.meta.get("notice") \
                and not (row.kind in ("mirror", "boundary") and _said(row) is None):
            out.append(row.at)
        top = row.at if top is None or row.at > top else top
    return sorted(out)


# ── the view every reader uses ───────────────────────────────────────────────

def _inputs(cfg: Config, state: dict) -> tuple[list[datetime], set[str]]:
    starts = []
    for t in (state.get("off_threads") or {}).values():
        try:
            starts.append(datetime.fromisoformat(t))
        except (TypeError, ValueError):
            continue
    return starts, set((state.get("off") or {}).keys())


def view(cfg: Config, now: datetime | None = None, since: datetime | None = None, db: Path | None = None) -> View:
    """The current answer: persisted results for older rows, recomputed for the last ``RECOMPUTE`` hours (or
    from ``RECOMPUTE`` before ``since``, so a window or a tainted turn begun just before it holds). Fails closed."""
    from . import conversations
    now = now or datetime.now(timezone.utc)
    try:
        chat = cfg.transcript(db)
        state = conversations.snapshot(cfg)
        frozen = state.get("privacy") or {}
        start = (since or now) - RECOMPUTE
        if since is None:
            start = min(start, _unpersisted_from(cfg, state, chat.source, now))
        for s, e in frozen.get("windows") or []:  # begin at a window that was still open then
            if e is None or datetime.fromisoformat(e) > start:
                start = min(start, datetime.fromisoformat(s))
        # (keyed by start too: ``covering`` asks for two views in a row; and by every file the pass reads, the prompt
        # sends among them: a send from another process ends a window, so a view cached before it must not answer;
        # the sessions database's own stamp too, as a database replaced at the same path can keep the newest id; and
        # by the channel, whose chat is the one read: CHANNELS)
        key = (str(chat.source), cfg.platform, str(cfg.chat_id), cfg.owner_ids, str(cfg.state_dir),
               start.timestamp() // 60,
               *(_stamp(f) for f in (conversations._path(cfg), cfg.ledger_path, cfg.state_dir / "sent.jsonl",
                                     cfg.state_dir / SENDS)),
               _stamp(Path(str(chat.source))) if chat.source is not None else (),
               _max(chat) if chat.source is not None else None)
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
        starts, off_ids = _inputs(cfg, state)
        sends = send_times(cfg)
        saved = {}
        for rid, t in (frozen.get("private") or {}).items():
            saved[int(rid)] = datetime.fromisoformat(t)
        readable = chat.source is not None and chat.available()
        found = chat.rows(since=start) if readable else []
        v = compute(cfg, found, [h for h in starts if h >= start], sends, off_ids)
        read = {r.id: r.at for r in found}
        # a compaction copy whose original this view did not read (it is older than ``start``) is answered by the
        # original, and a saved answer is only a floor that ages out (#4): answer those originals from a slice each
        missing = sorted({o for o in v.copies.values() if o not in read and o not in saved})
        uncertain: set[int] = set()
        if missing:
            v.retake = _originals(cfg, chat, missing, start, starts, sends, off_ids, frozen, v.private, uncertain)
        for rid, t in saved.items():
            if rid in read and read[rid] != t:
                continue  # another row under this id (a replaced database): the saved answer was not about it
            v.private.setdefault(rid, t)
        for s, e in frozen.get("windows") or []:
            w = (datetime.fromisoformat(s), datetime.fromisoformat(e) if e else None)
            if w[1] is not None and w[1] <= start:
                v.windows.append(w)
        for c, o in v.copies.items():  # (an original private by a saved result only)
            if o in v.private:
                v.private.setdefault(c, read.get(c, v.private[o]))
            elif o in uncertain:  # a window begun before its slice may cover it: the copy, never the original
                v.private.setdefault(c, read[c])
        v.start = start
        for old, (at, _v) in list(_CACHE.items()):  # (a copy: the gateway asks from several threads)
            if time.time() - at >= CACHE_SECONDS:
                _CACHE.pop(old, None)
        _CACHE[key] = (time.time(), v)
        return v
    except Exception as e:  # noqa: BLE001 - privacy fails closed
        return Closed(f"{type(e).__name__}: {e}"[:200])


SAME_LOOKUP = 64  # two originals this few ids apart are looked up in one read (a compaction carries a run of rows)


def _originals(cfg: Config, chat, missing: list[int], start: datetime, starts: list[datetime],
               sends: list[datetime], off_ids: set[str], frozen: dict, into: dict, uncertain: set) -> dict:
    """Answer the originals of compaction copies the view did not read (#4), adding every private row found to
    ``into`` (a re-take only adds). Each original's time comes from a read of its id (originals a few ids apart
    share one read); then the pass runs over a slice from ``RECOMPUTE`` before it (a window or turn begun in
    those hours) to ``RECOMPUTE`` after it (a row he sent before it but queued is stored after it: ``_late``,
    ``_busy_starts``), originals whose slices meet sharing one, a saved window still open at a slice's start
    widening it back to that window's start. So the cost is bounded by the slices, never a read back to the oldest
    original.

    A slice cannot see a window begun before it and kept open into it (his messages less than ``quiet_minutes``
    apart, no prompt send): so the pass runs over the slice a second time with a window opened at its start, the
    widest one that could be open there. An original private in that run but public in the real one could be
    covered by an earlier window, and goes into ``uncertain``: the view makes its copies private, never the
    original's id (``update`` would save that old row as private). Raises (the view fails closed) when an
    original can't be read, is read as newer than ``start`` (the view's own read should then have held it: two
    reads that disagree answer nothing), or is missing from its slice."""
    want, when, i = set(missing), {}, 0
    while i < len(missing):
        j = i
        while j + 1 < len(missing) and missing[j + 1] - missing[j] <= SAME_LOOKUP:
            j += 1
        when.update((r.id, r.at) for r in chat.rows(after_id=missing[i] - 1, upto_id=missing[j]) if r.id in want)
        i = j + 1
    if len(when) < len(want):  # an original gone between two reads: its copies can't be answered
        raise LookupError("a compaction copy's original could not be read")
    if any(t >= start for t in when.values()):
        raise LookupError("a compaction copy's original is newer than the view's start, but the view did not read it")
    spans: list[list] = []  # [lo, hi, the originals in it]
    for oid, t in sorted(when.items(), key=lambda kv: kv[1]):
        lo, hi = t - RECOMPUTE, t + RECOMPUTE
        for s, e in frozen.get("windows") or []:  # begin at a saved window still open then, as ``view`` does
            if datetime.fromisoformat(s) < lo and (e is None or datetime.fromisoformat(e) > lo):
                lo = datetime.fromisoformat(s)
        if spans and lo <= spans[-1][1]:
            # (a no-op as the starts come in order: a saved window that takes a later original's start back before an
            # earlier one's covers that one too; kept so a merged span can never start later than a member's)
            spans[-1][0] = min(spans[-1][0], lo)
            spans[-1][1] = max(spans[-1][1], hi)
            spans[-1][2].append(oid)
        else:
            spans.append([lo, hi, [oid]])
    total = 0
    for lo, hi, oids in spans:
        first, past = chat.first_id(lo), chat.first_id(hi)
        after = min(oids) - 1 if first is None else min(first - 1, min(oids) - 1)
        upto = None if past is None else max(past - 1, max(oids))
        rows = chat.rows(after_id=after, since=lo, upto_id=upto)
        if not set(oids) <= {r.id for r in rows}:
            raise LookupError("a compaction copy's original was not in its slice")
        total += len(rows)
        hooks = [h for h in starts if lo <= h < hi]
        got = compute(cfg, rows, hooks, sends, off_ids).private
        for rid, t in got.items():
            into.setdefault(rid, t)
        wide = compute(cfg, rows, [lo, *hooks], sends, off_ids).private
        uncertain.update(o for o in oids if o in wide and o not in got)
    return {"originals": len(want), "slices": len(spans), "rows": total, "uncertain": len(uncertain)}


def covering(cfg: Config, oldest: datetime | None, now: datetime | None = None, db: Path | None = None) -> View:
    """The view for a reader holding rows from ``oldest`` on: ``view``, re-taken from ``oldest`` (and ``RECOMPUTE``
    before it) unless ``view`` already began recomputing that far back. A saved result is trusted for no row the
    reader holds, since the stamp that bounds ``view`` proves only that a pass ran (``_unpersisted_from``); and
    a window or turn begun before ``oldest`` is found only by recomputing before it, so a reader whose oldest row
    sits at ``view``'s start is re-taken too (the sweep's default lookback is exactly there). One more pass over
    ``RECOMPUTE`` more hours, cached with the other."""
    v = view(cfg, now, db=db)
    if oldest is not None and oldest.tzinfo is None:
        oldest = oldest.astimezone(timezone.utc)  # a naive time is local time; comparing it as is would raise
    if v.closed or oldest is None or v.start is None or oldest - RECOMPUTE >= v.start:
        return v
    return view(cfg, now, since=oldest, db=db)


def _unpersisted_from(cfg: Config, state: dict, source: str | None, now: datetime) -> datetime:
    """Where the recompute must begin so no row is left without an answer. Older rows carry the result the last
    ``update`` saved, and each pass saves everything up to its own time, so only rows after it have none; a row
    with no answer would read as public. When the tick was away (or the pass stayed closed) for longer than
    ``RECOMPUTE``, begin ``RECOMPUTE`` before that pass, the same lookback a tick has on its own, so a turn that
    straddled it keeps its taint. With no pass saved yet, the conversation writer's cursor stands in for it (what
    lies before the cursor was decided when it was written), but only while the writer is on and reads this
    database: a stale cursor must not make the hook recompute weeks of transcript on each turn. An unreadable
    stamp means the pass cannot say what it covered: recompute as far back as results are kept. A stamp says a
    pass ran, not that it read these rows, so this bounds only what ``view`` recomputes by default; a reader that
    holds rows older than that asks ``covering``."""
    from . import conversations
    stamp = (state.get("privacy") or {}).get("at")
    if not stamp and conversations.enabled(cfg) and state.get("db") == str(source):
        stamp = (state.get("cursor") or {}).get("last_at")
    if not stamp:
        return now
    try:
        return datetime.fromisoformat(stamp) - RECOMPUTE
    except (TypeError, ValueError):
        return now - timedelta(days=KEEP_DAYS)


def _stamp(path: Path) -> tuple:
    """What tells a file's versions apart: two writes within the clock's step can share a time, not size and inode."""
    try:
        st = path.stat()
    except OSError:
        return ()
    return st.st_mtime_ns, st.st_size, st.st_ino


def _max(t):
    try:
        return t.max_id()
    except Exception:  # noqa: BLE001
        return None


def update(cfg: Config, now: datetime, log: list[str] | None = None) -> View:
    """Each tick, before the sweep: persist the pass's result (private ids, windows) in one locked write. Not
    while the configured sessions database is missing: a pass over no rows would be saved as if it had read
    them, and the stamp would move the next views' start past rows nobody classified."""
    from . import conversations
    _CACHE.clear()
    v = view(cfg, now)
    if v.closed:
        if log is not None:
            log.append(f"privacy pass could not run ({getattr(v, 'why', '')}); nothing is saved until it can")
        return v
    chat = cfg.transcript()
    if chat.source is not None and not chat.available():
        if log is not None:
            log.append(f"privacy pass not saved: no sessions database at {chat.source}")
        return v
    if v.retake and log is not None:  # (counts only: never a row's words)
        log.append("privacy: answered {originals} compaction-copy original(s) older than the view from {slices} "
                   "slice(s) of {rows} rows".format(**v.retake)
                   + ("; the copies of {uncertain} read private (a window begun before the slice may cover them)"
                      .format(**v.retake) if v.retake.get("uncertain") else ""))
    keep = now - timedelta(days=KEEP_DAYS)
    with conversations.locked(cfg) as st:
        st["privacy"] = {
            "private": {str(k): t.isoformat() for k, t in v.private.items() if t >= keep},
            "windows": [[s.isoformat(), e.isoformat() if e else None] for s, e in v.windows
                        if e is None or e >= keep],
            "at": now.isoformat(),
        }
    _CACHE.clear()
    return v


def window_open(cfg: Config, now: datetime) -> bool:
    """For the hook: is a bare /x holding the current thread off the record right now? If the pass can't run,
    only a /x the hook recorded within quiet_minutes counts (so a broken database doesn't silence the journal).
    Never raises: if that fallback can't run either, the answer is yes (fail closed), since the hook's failure path
    for this check is the one that does not consider the window."""
    from . import conversations
    v = view(cfg, now)
    if not v.closed:
        return v.open_at(now)
    try:
        starts, _ids = _inputs(cfg, conversations.snapshot(cfg))
        return any(timedelta(0) <= now - t < conversations.quiet(cfg) for t in starts)
    except Exception:  # noqa: BLE001 - privacy fails closed
        return True
