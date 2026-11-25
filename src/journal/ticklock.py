"""``tick.lock``: who holds it, and how a running tick lets a waiting send or program step go first (issue #45).

The lock serialises everything that sends or moves a program on: the scheduled tick (``tick.run``), a deliberate send
(``journal send``: ``/start``, the web app's Send), every spawned ``journal program …`` (``/next``, a phase step) and the
two maintenance passes (``journal sweep``, ``journal backfill-conversations``). It also orders the chat: whoever holds
it sends, so a prompt's ``sent_at`` in the ledger and its place in the chat agree, and a reply is matched to the prompt
that really is the newest (``capture.superseded``).

A tick holds it for minutes when it calls a model (an opener, annotations, a reflection). So the tick **yields**: at
safe points (``pause``: between the steps ``tick.run`` names, and between the model calls of a step that makes several)
it asks whether anyone is waiting, and if so lets them go first, then takes the lock back and carries on where it was.
A waiter then waits for the rest of one step, or of one model call inside a step, not for the whole tick.

Three files under ``state_dir``, all ``flock``\\ ed (released by the kernel when a process dies, so no stale marker):

* ``tick.lock`` (``LOCK``): the lock itself. A waiter takes it blocking (``waited``); the scheduled tick takes it
  non-blocking and skips its round when it is busy (``scheduled``), as before.
* ``tick-wait.lock`` (``WAIT``): the waiting flag. A waiter that finds ``tick.lock`` busy holds this one shared while it
  blocks, and lets go of it once it has ``tick.lock``. The tick's ``pause`` tries it exclusive without blocking: busy
  means someone waits. Never a busy-poll: the tick asks only at its safe points, and a waiter sleeps in ``flock``.
* ``tick-run.lock`` (``RUN``): a scheduled tick holds it for its whole run, yields included. A tick that starts while
  another is yielded finds it busy and skips its round, as it would have found ``tick.lock`` busy; so two ticks never
  interleave, and a yielded tick is never overtaken by a new one in the gap between a waiter letting go of
  ``tick.lock`` and the yielded tick taking it back.

The handoff (``pause``): the tick lets go of ``tick.lock``, then waits for ``tick-wait.lock`` exclusive, which it gets
only once every waiter holds (or has held) ``tick.lock`` and so has let go of its share; then it waits for ``tick.lock``
itself, behind the last of them. ``flock`` is not fair, so without the second wait the tick could take ``tick.lock``
straight back before a woken waiter ran. A waiter that arrives while the tick is between the two waits takes its share
and goes ahead as well; the tick takes the lock back when no one is waiting any more.

After a yield the tick goes on with the same ``now`` (a tick has always run its later steps minutes after its ``now``
while it waited on a model; a yield adds the waiter's time to that) and every step reads the state again: see
``tick.run`` for where it yields and why those points are safe.
"""
from __future__ import annotations

import contextlib
import contextvars
import fcntl
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Iterator

from .config import Config

LOCK = "tick.lock"
WAIT = "tick-wait.lock"
RUN = "tick-run.lock"


@dataclass
class _Held:
    lock: IO
    wait: Path
    yields: int = 0


_HELD: contextvars.ContextVar[_Held | None] = contextvars.ContextVar("journal_tick_lock_held", default=None)


def _open(cfg: Config, name: str) -> IO:
    cfg.state_dir.mkdir(parents=True, exist_ok=True)
    return open(cfg.state_dir / name, "a+")


@contextlib.contextmanager
def waited(cfg: Config) -> Iterator[None]:
    """``tick.lock``, waited for: a deliberate send, a program transition, a sweep or a backfill. When a tick holds it,
    the waiting flag is raised for as long as this waits, so the tick yields at its next safe point."""
    with _open(cfg, LOCK) as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            with _open(cfg, WAIT) as flag:  # (closing it drops the share, once tick.lock is ours)
                fcntl.flock(flag, fcntl.LOCK_SH)
                fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


@contextlib.contextmanager
def scheduled(cfg: Config) -> Iterator[bool]:
    """The scheduled tick's hold: yields False (and holds nothing) when another tick is running, yielded or not, or
    someone else holds ``tick.lock``: the tick skips this round. Yields True with the lock held; ``pause`` may then
    hand it to a waiter and take it back."""
    with _open(cfg, RUN) as running, _open(cfg, LOCK) as lock:
        try:
            fcntl.flock(running, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        token = _HELD.set(_Held(lock, cfg.state_dir / WAIT))
        try:
            yield True
        finally:
            _HELD.reset(token)


def waiting(cfg: Config) -> bool:
    """Whether someone is waiting for ``tick.lock`` (holds the waiting flag)."""
    return _flag_busy(cfg.state_dir / WAIT)


def _flag_busy(path: Path) -> bool:
    try:
        flag = open(path, "a+")
    except OSError:
        return False
    with flag:
        try:
            fcntl.flock(flag, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        except OSError:  # (cannot ask: nobody is taken to be waiting, as when the file cannot be opened)
            return False
        fcntl.flock(flag, fcntl.LOCK_UN)
        return False


def pause() -> bool:
    """A safe point of the scheduled tick: when someone waits for ``tick.lock``, let them have it and take it back
    after them. True when it yielded. Outside a scheduled tick (a send, a test calling a step) it does nothing.

    The caller must hold no other lock here (the ledger lock, a state lock): a waiter may need it."""
    held = _HELD.get()
    if held is None or not _flag_busy(held.wait):
        return False
    fcntl.flock(held.lock, fcntl.LOCK_UN)
    try:
        with open(held.wait, "a+") as flag:  # every waiter has tick.lock, or has had it and let go
            fcntl.flock(flag, fcntl.LOCK_EX)
    except OSError:  # (no flag file to wait on: the tick goes on, after taking its lock back below)
        pass
    finally:
        fcntl.flock(held.lock, fcntl.LOCK_EX)  # behind the last of them
    held.yields += 1
    return True


def yields() -> int:
    """How many times the running scheduled tick has yielded (0 outside one)."""
    held = _HELD.get()
    return held.yields if held else 0
