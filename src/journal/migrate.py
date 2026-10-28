"""Migrations from the hand-wired predecessor (a plugin of its own, with a flat ``journal.json``). Both are
read-only on the source.

``migrate_config``: the old flat ``journal.json`` becomes the new nested config. Values the old
code hardcoded (names, token helper, source paths, guard paths) were never in that file; supply
them with an overlay (``--merge``), e.g. a deployment's own overlay kept outside the repo
(``deploy/example-overlay.json`` shows the shape). A config in the new shape passes through too, which is how a
``schedule`` block left in one is retired: the key is not carried over (nothing reads it any more) and each entry
that changes a practice's times is written into a fork of that built-in in ``practices_dir`` (``move_schedule``),
with a note saying what was done.

``migrate_state``: copies (never moves) the old state directory — ledger, notebook, archive,
location history, context packs — into the new one, holding the old tick and ledger locks
(shared) so no old tick or capture writes mid-copy. Lock files and temp files are not copied.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import shutil
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from .config import ConfigError, deep_merge, from_dict, read_hermes_settings, read_json_file, write_json_file

WINDOW_KEYS = ("collapse_hours", "grace_hours", "opener_budget_seconds", "annotate_after_minutes")
DIRECT_KEYS = ("root", "timezone", "location", "coordinates")


def convert_config(old: dict) -> tuple[dict, list[str]]:
    """(new config, notes). An input already in the new shape passes through unchanged."""
    if isinstance(old.get("telegram"), dict) or isinstance(old.get("windows"), dict):
        return {k: v for k, v in old.items() if k != "schedule"}, ["input is already in the new format; copied as is"]
    new: dict = {"version": 3}
    notes: list[str] = []
    for k in DIRECT_KEYS:
        if k in old:
            new[k] = old[k]
    windows = {k: old[k] for k in WINDOW_KEYS if k in old}
    if windows:
        new["windows"] = windows
    if "telegram_chat_id" in old:
        new["telegram"] = {"chat_id": str(old["telegram_chat_id"])}
    if old.get("profile"):
        new["hermes"] = {"profile": old["profile"]}
    if "state_dir" in old:
        notes.append(f"state_dir {old['state_dir']!r} not carried over: the new default is the plugin data dir; "
                     "copy the old state with `journal migrate-state`")
    known = set(DIRECT_KEYS) | set(WINDOW_KEYS) | {"telegram_chat_id", "profile", "state_dir", "version", "schedule"}
    for k in sorted(set(old) - known):
        notes.append(f"unknown key {k!r} not carried over")
    return new, notes


def _override_slots(spec: Mapping, p) -> tuple | None:
    """The slots an old ``schedule.<id>`` entry gave practice ``p``, read the way the override was: its keys on top
    of the file's first slot (``{"time": "07:30"}`` keeps the weekly's Sunday), one slot as the result,
    ``enabled: false`` = not scheduled. None when the entry is not usable (a bad time or weekday)."""
    from . import library
    if spec.get("enabled") is False:
        return ()
    base = p.schedule[0] if p.schedule else None
    time = spec["time"] if "time" in spec else (base.time if base else None)
    weekday = spec["weekday"] if "weekday" in spec else (base.weekday if base else None)
    if not isinstance(time, str) or not library.TIME.match(time):
        return None
    if weekday is not None and (isinstance(weekday, bool) or not isinstance(weekday, int) or not 0 <= weekday <= 6):
        return None
    return (library.Slot(time, weekday),)


def _hermes_layer(hermes_home: Path | None) -> tuple[dict, list[str]]:
    """The plugin settings the engine lays over the config file (as ``config.load`` does), and what could not be read."""
    settings, warnings = read_hermes_settings(hermes_home)
    return ({k: v for k, v in settings.items()
             if k != "config_file" and not (k in ("root", "state_dir") and v in ("", None))}, warnings)


def _locate(new: dict, hermes_layer: Mapping, hermes_home: Path | None):
    """The Config the engine will read once ``new`` is written: file under the Hermes settings and the environment
    (``config.load`` order). The timezone plays no part in where files go, so a bad one is not a reason to fail
    here (doctor names it when the config is loaded for real)."""
    layer = deep_merge(new, hermes_layer)
    layer["timezone"] = "UTC"
    layer.setdefault("root", ".")  # (a place to resolve against; the forks never touch the journal)
    try:
        return from_dict(layer, hermes_home=hermes_home)
    except ConfigError as e:
        raise ValueError(f"cannot tell where the practice files go: {e}") from e


def _place(new: dict, hermes_layer: Mapping, practices_dir: Path | None, hermes_home: Path | None):
    """(config to write, Config the engine will read, note or None). ``practices_dir`` (``--practices-dir``) puts the
    forks somewhere; the engine only reads them there if the config says so, so when the config would point
    elsewhere it is written into the config. A Hermes setting or ``$JOURNAL_PRACTICES_DIR`` overrides the file,
    so there it cannot be recorded: refused, before anything is written."""
    cfg = _locate(new, hermes_layer, hermes_home)
    if not practices_dir:
        return new, cfg, None
    want = Path(practices_dir).expanduser()
    if cfg.practices_dir == want:
        return new, cfg, None
    placed = deep_merge(new, {"practices_dir": str(practices_dir)})
    now = _locate(placed, hermes_layer, hermes_home)
    if now.practices_dir != want:
        raise ValueError(f"the engine will read practices from {now.practices_dir} (a Hermes setting or "
                         f"$JOURNAL_PRACTICES_DIR overrides the config's practices_dir), not {practices_dir}; "
                         "drop --practices-dir or change that setting")
    return placed, now, f"practices_dir {practices_dir} recorded in the config (otherwise the engine reads {cfg.practices_dir})"


def move_schedule(schedule, new: dict, practices_dir: Path | None = None, hermes_home: Path | None = None,
                  write: bool = True, out: list[str] | None = None) -> list[str]:
    """Write the old ``schedule.<id>`` block ``schedule`` into the practice files of the config ``new`` would be:
    each entry that still changes something goes into a fork of that built-in in the directory the engine will
    read for that config (``practices_dir`` of ``new``, the Hermes settings or ``$JOURNAL_PRACTICES_DIR`` over it,
    else ``<state_dir>/practices``; ``practices_dir`` here stands for the file's value), with the window the
    built-in was folding on (``library.fork``). Never rewrites a file of his (that would drop his prose): a
    practice of his own gets the lines to change. ``write=False`` only says what would be done. Lines are appended
    to ``out`` as each step is done, so a fork that fails part way still leaves the account of the ones before it;
    an entry that cannot be used is named and skipped."""
    from . import library
    out = [] if out is None else out
    if not schedule:
        return out
    if not isinstance(schedule, Mapping):
        out.append(f"schedule is not an object ({schedule!r}); nothing to move")
        return out
    _new, cfg, note = _place(new, _hermes_layer(hermes_home)[0], practices_dir, hermes_home)
    if note:
        out.append(note)
    lib = library.load(cfg)
    for pid, spec in schedule.items():
        p = lib.practices.get(pid)
        if p is None:
            out.append(f"schedule.{pid}: no practice has this id; nothing to move")
            continue
        want = _override_slots(spec, p) if isinstance(spec, Mapping) else None
        if want is None:
            out.append(f"schedule.{pid}: not a usable schedule ({spec!r}); nothing to move, fix or drop it")
            continue
        said = library.describe_schedule(want)
        if want == p.schedule:
            out.append(f"schedule.{pid}: {'the built-in' if p.builtin else p.path} already says {said}; nothing to move")
        elif not p.builtin:
            lines = (["(no schedule lines: on demand only)"] if not want else
                     [f"schedule.time: {want[0].time}"] + ([f"schedule.weekday: {want[0].weekday}"]
                                                         if want[0].weekday is not None else []))
            out.append(f"schedule.{pid}: {p.path} is your own file, so it is not rewritten; give it "
                       f"{'; '.join(lines)} in place of its schedule lines ({said})")
        elif not write:
            out.append(f"schedule.{pid}: would fork {pid} into {cfg.practices_dir} with {said}"
                       f"{library.window_note(cfg, pid)}")
        else:
            try:
                dest = library.fork(cfg, pid, schedule=want)
            except OSError as e:  # (AlreadyForked is not one: p would have been a user file above)
                raise OSError(f"cannot fork {pid} into {cfg.practices_dir}: {e}; done so far: "
                              f"{'; '.join(out) or 'nothing'}") from e
            out.append(f"schedule.{pid}: forked {pid} to {dest} with {said}{library.window_note(cfg, pid)}")
    return out


def migrate_config(src: Path, dest: Path | None = None, merge: Path | None = None,
                   force: bool = False, practices_dir: Path | None = None,
                   hermes_home: Path | None = None) -> tuple[dict, list[str]]:
    old = read_json_file(src)
    new, notes = convert_config(old)
    schedule = old.get("schedule")
    if merge:
        overlay = read_json_file(merge)
        overlay.pop("version", None)
        over_schedule = overlay.pop("schedule", None)
        new = deep_merge(new, overlay)
        notes.append(f"merged {merge}")
        if isinstance(schedule, Mapping) and isinstance(over_schedule, Mapping):
            schedule = deep_merge(schedule, over_schedule)
        elif over_schedule is not None:
            schedule = over_schedule
    hermes_layer, hermes_warnings = _hermes_layer(hermes_home)
    notes.extend(hermes_warnings)
    if hermes_layer.get("schedule"):
        # The plugin settings were laid over the file, so they win per entry; they live in the profile's
        # config.yaml, which this command never edits: it says the one step that retires them.
        over_schedule = hermes_layer["schedule"]
        if isinstance(schedule, Mapping) and isinstance(over_schedule, Mapping):
            schedule = deep_merge(schedule, over_schedule)
        else:
            schedule = over_schedule
        notes.append("`schedule` also found in the Hermes settings of the profile (plugins.entries.journal.settings); "
                     "it is moved with the rest, and stays there until you remove it: "
                     "`hermes config unset plugins.entries.journal.settings.schedule`")
    if dest is not None:
        dest = Path(dest)
        if dest.exists() and not force:
            raise FileExistsError(f"{dest} exists; pass --force to overwrite it")
        if dest.resolve() == Path(src).resolve():
            raise ValueError("refusing to overwrite the source config")
    if schedule:
        notes.append("`schedule` is not carried over: nothing reads it any more, when a practice is sent is in its "
                     "file (`schedule.time`)")
        if practices_dir and isinstance(schedule, Mapping):
            new, _cfg, note = _place(new, hermes_layer, practices_dir, hermes_home)  # (refuses before anything is written)
            if note:
                notes.append(note)
        # forks are files in the practices directory: written only with --to, as the config is
        move_schedule(schedule, new, practices_dir, hermes_home, write=dest is not None, out=notes)
        if dest is None:
            notes.append("nothing was forked: pass --to to write the config and the forks")
    if dest is not None:
        write_json_file(dest, new)
        notes.append(f"wrote {dest}")
    return new, notes


SKIP_SUFFIXES = (".lock",)


def _skip(path: Path) -> bool:
    return path.suffix in SKIP_SUFFIXES or path.name.startswith(".ledger-") or path.name.startswith(".")


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@contextlib.contextmanager
def _shared_locks(src: Path, wait_note):
    """Hold the old tick and ledger locks (shared, opened read-only) while copying."""
    handles = []
    try:
        for name in ("tick.lock", "ledger.lock", "notebook.lock"):
            path = src / name
            if not path.is_file():
                continue
            fh = open(path, "r")
            try:
                fcntl.flock(fh, fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                wait_note(f"waiting for {path} (a tick or capture is running)…")
                fcntl.flock(fh, fcntl.LOCK_SH)
            handles.append(fh)
        yield
    finally:
        for fh in handles:
            fcntl.flock(fh, fcntl.LOCK_UN)
            fh.close()


def migrate_state(src: Path, dest: Path, force: bool = False, lock: bool = True,
                  note=lambda s: None) -> list[str]:
    src, dest = Path(src), Path(dest)
    if not src.is_dir():
        raise FileNotFoundError(f"no state directory at {src}")
    if src.resolve() == dest.resolve():
        raise ValueError("source and destination are the same directory")
    clash = [n for n in ("ledger.json", "notebook.json") if (dest / n).exists()]
    if clash and not force:
        raise FileExistsError(f"{dest} already has {', '.join(clash)}; pass --force to replace "
                              "(the existing files are kept as .bak-<time>)")
    lines = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    with (_shared_locks(src, note) if lock else contextlib.nullcontext()):
        files = [p for p in sorted(src.rglob("*")) if p.is_file() and not _skip(p)]
        def _blocked(rel: Path) -> bool:  # the target is not a file, or a parent on the way is not a directory
            out = dest / rel
            if out.exists() and not out.is_file():
                return True
            return any(parent.exists() and not parent.is_dir()
                       for parent in out.parents if parent == dest or dest in parent.parents)
        blocked = [str(p.relative_to(src)) for p in files if _blocked(p.relative_to(src))]
        if blocked:  # not something --force can replace: refuse before copying anything
            raise FileExistsError(f"{dest} has non-file paths where files would go: {', '.join(blocked)}; "
                                  "move them aside first")
        differing = [str(p.relative_to(src)) for p in files
                     if (dest / p.relative_to(src)).exists()
                     and not ((dest / p.relative_to(src)).is_file()
                              and _digest(dest / p.relative_to(src)) == _digest(p))]
        if differing and not force:  # --force is the permission to replace anything that differs
            raise FileExistsError(f"{dest} already has differing {', '.join(differing)}; pass --force to "
                                  "replace (the existing files are kept as .bak-<time>)")
        for path in files:
            rel = path.relative_to(src)
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                if out.is_file() and _digest(out) == _digest(path):
                    continue
                backup = out.with_name(f"{out.name}.bak-{stamp}")
                shutil.copy2(out, backup)
                lines.append(f"kept the existing {rel} as {backup.name}")
            shutil.copy2(path, out)
            if _digest(out) != _digest(path):
                raise OSError(f"copy of {rel} does not match its source")
        lines.append(f"copied {len(files)} file(s) from {src} to {dest} (source untouched)")
    ledger = dest / "ledger.json"
    if ledger.is_file():
        data = json.loads(ledger.read_text(encoding="utf-8"))
        prompts = data.get("prompts", [])
        lines.append(f"ledger: {len(prompts)} prompt(s), "
                     f"{sum(len(p.get('captures') or []) for p in prompts)} capture(s)")
    return lines
