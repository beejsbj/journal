"""``journal serve --print-unit``: the systemd user unit, filled in from the configuration.

Everything host-specific comes from the loaded config (the ``journal`` executable, ``--config``, ``--hermes-home``,
the journal root, ``state_dir`` and ``practices_dir``), so the same code renders any deployment's unit and nothing
personal lives here. Memory limits are Decision 7's starting envelope (``MemoryHigh=384M``, ``MemoryMax=768M``), to
be revised from measured peaks.

No filesystem sandbox (2026-10-26; DECISIONS.md, SERVE-UNSANDBOXED). Under a user manager, ``PrivateTmp``,
``ProtectSystem``, ``ProtectHome``, ``ReadWritePaths`` and the other mount-namespace settings put the service in a
user namespace of its own, where root reads as the overflow uid 65534. tailscaled, the peer on ``serve.socket``, is
root, and Tailscale identity trusts only a root peer (``auth.tailscale_login``), so with them every browser fell back
to pairing (#49). What stays needs no namespace: ``NoNewPrivileges`` (a process flag; checked to leave the namespace
and the peer's uid 0 as they are), ``UMask``, the cgroup limits, the mount gates and the restart settings. The server
writes wherever the user it runs as can write; the Settings page's save needs only the config file's directory to be
writable by that user.

The bind address is not in the unit: ``serve.bind`` stays in the config, where the operator sets it at deploy
time (Decision 8), and the server retries a missing address for 60 s before exiting, so ``Restart=on-failure``
covers an interface that comes up late.

``KillMode=process``: the server spawns ``journal send`` and ``journal program …`` (``serve/writes.spawn_journal``),
which wait for the tick's lock and may be queued when the unit is restarted. systemd's default stops every process
of the unit's cgroup, and a child started with ``start_new_session`` is still in it, so a restart would kill the
waiting child and the send or step he asked for would be lost. With ``process`` only the server is stopped; the
children finish on their own.

``serve.socket`` (the Unix socket ``tailscale serve`` proxies to): a socket in a directory below the user's runtime
directory, at any depth (``/run/user/<uid>/journal/serve/serve.sock``), gets ``RuntimeDirectory=`` of that path
below it (``journal/serve``), mode 0700, which systemd creates at start and removes at stop. Anywhere else (the
runtime directory itself, the state directory) needs only its mount gated: the server makes a missing directory
0700 and the socket 0600. The server also refuses a directory that is not this user's own or that others can write
(``app._check_parent``), so a socket directly in ``/tmp`` does not start; with no private /tmp, a directory of its
own there would at least be the path tailscaled sees, but the runtime directory is the place for it.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Callable

from ..config import Config, serve_socket


MEMORY_HIGH = "384M"
MEMORY_MAX = "768M"


def mount_point(path: Path) -> Path:
    """The mount the path lives on (``/`` when it is the root filesystem)."""
    p = Path(path).absolute()
    while not os.path.ismount(p) and p != p.parent:
        p = p.parent
    return p


def executable() -> str:
    """The ``journal`` command this process was started as, else the one on PATH."""
    argv0 = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    if argv0 is not None and argv0.name == "journal" and argv0.exists():
        return str(argv0.absolute())
    return shutil.which("journal") or "journal"


def _quote(arg: str) -> str:
    """systemd's ``ExecStart`` quoting for an argument with spaces or quotes."""
    if arg and not any(c in arg for c in " \t\"'\\;$%"):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%").replace("$", "$$") + '"'


def _norm(path: Path) -> Path:
    return Path(os.path.normpath(Path(path).expanduser().absolute()))


def socket_dir(cfg: Config) -> Path | None:
    """The directory of ``serve.socket`` (normalised by ``serve_socket``: a ``..`` would make a
    ``RuntimeDirectory=`` systemd refuses, and hide a mount), or None without a socket."""
    sock = serve_socket(cfg)
    return None if sock is None else sock.parent


def required_paths(cfg: Config, runtime: Path) -> list[Path]:
    """What the server needs mounted to start: root, state_dir, practices_dir, the config file's directory when one
    is in use, and the socket's directory unless it is in the user's runtime directory (which the user manager
    itself lives on). Normalised, so a ``..`` does not hide a mount."""
    paths = [Path(cfg.root), Path(cfg.state_dir), Path(cfg.practices_dir)]
    if cfg.config_file:
        paths.append(Path(cfg.config_file).parent)
    sock = socket_dir(cfg)
    if sock is not None and sock != runtime and runtime not in sock.parents:
        paths.append(sock)
    return [_norm(p) for p in paths]


def user_runtime_dir() -> Path:
    """The user's runtime directory, which ``RuntimeDirectory=`` of a user unit lives in (systemd's ``%t``)."""
    return Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")


def runtime_directory(cfg: Config, runtime: Path) -> str | None:
    """The ``RuntimeDirectory=`` name for ``serve.socket``: its directory relative to the user's runtime directory
    when it is below it (``journal/serve`` for ``/run/user/<uid>/journal/serve/serve.sock``), else None (no socket,
    a socket directly in the runtime directory, or one elsewhere, whose missing directory the server makes 0700)."""
    sock = socket_dir(cfg)
    if sock is None or runtime not in sock.parents:
        return None
    return str(sock.relative_to(runtime))


def render(cfg: Config, *, exe: str | None = None, mount_of: Callable[[Path], Path] = mount_point,
           runtime_dir: Path | None = None) -> str:
    argv = [exe or executable()]
    if cfg.config_file:
        argv += ["--config", str(cfg.config_file)]
    if cfg.hermes_home:
        argv += ["--hermes-home", str(cfg.hermes_home)]
    argv.append("serve")
    runtime = runtime_dir or user_runtime_dir()
    mounts = []
    for p in required_paths(cfg, runtime):
        m = mount_of(p)
        if str(m) != "/" and m not in mounts:
            mounts.append(m)
    unit = ["[Unit]", "Description=journal serve (private web app and API)", "Wants=network-online.target",
            "After=network-online.target"]
    if mounts:
        unit.append("RequiresMountsFor=" + " ".join(_quote(str(m)) for m in mounts))
    unit += ["", "[Service]", "Type=simple"]
    unit += [f"ExecStartPre=/usr/bin/mountpoint -q {_quote(str(m))}" for m in mounts]
    if cfg.hermes_home:
        unit.append("Environment=HERMES_DISABLE_LAZY_INSTALLS=1")
    runtime_name = runtime_directory(cfg, runtime)
    unit += [
        "ExecStart=" + " ".join(_quote(a) for a in argv),
        "Restart=on-failure",
        "RestartSec=5s",
        "TimeoutStopSec=20s",
        "KillMode=process",
        "UMask=0077",
        "NoNewPrivileges=yes",
        *([f"RuntimeDirectory={runtime_name}", "RuntimeDirectoryMode=0700"] if runtime_name else []),
        f"MemoryHigh={MEMORY_HIGH}",
        f"MemoryMax={MEMORY_MAX}",
        "MemorySwapMax=0",
        "TasksMax=64",
        "CPUWeight=50",
        "",
        "[Install]",
        "WantedBy=default.target",
    ]
    return "\n".join(unit) + "\n"
