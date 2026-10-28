"""``journal serve``: a stdlib HTTP server (``ThreadingHTTPServer``) in front of ``api``, ``sse`` and ``static``.

One user, a private network, tens of requests a minute: no framework, and ``dependencies = []`` stays true
(API.md Decision 1). The order of checks on every request is fixed and is the security model:

1. ``Host`` must be ``serve.host`` when one is set (``/healthz`` is exempt, so the proxy can probe the bind
   address directly); on the Unix socket the host is ``X-Forwarded-Host``, since ``tailscale serve`` sends
   ``Host: localhost`` there;
2. a write (anything but GET/HEAD) needs a same-site ``Origin`` (or ``Sec-Fetch-Site: same-origin``), on every
   listener and however the request is signed in;
3. every ``/api`` path but health and pairing needs the token (bearer), the session cookie or, on the Unix socket
   only, a listed Tailscale login (``auth.tailscale_login``), *before* routing, so an unauthenticated client learns
   nothing about which paths exist;
4. then the route. Unknown → 404; a known path with another method → 405; a route that changes the journal
   (``api.WRITE``) answers 405 ``read-only`` unless ``serve.allow_write`` is true.

The server listens on TCP (``serve.bind``/``serve.port``) and, when ``serve.socket`` is set, on that Unix socket
as well: TCP keeps the token, cookie and pairing for anything that is not ``tailscale serve``, and the socket is
where ``tailscale serve`` proxies with its identity header. Both run in one process over the same ``App``.

Every response carries the CSP, ``nosniff`` and ``Referrer-Policy: no-referrer``; ``/api`` adds ``no-store``.
Bodies are never logged; ``--access-log`` records method, path (no query), status and bytes. The server reads
state and, for the routes in ``writes``, appends to the journal through the engine's own writers under the ledger
lock; it never takes ``tick.lock`` (sends and program steps are spawned), never calls a model and never reads the
chat transcript, so it keeps working with no model reachable and cannot serve what was said off the record.
"""
from __future__ import annotations

import errno
import hmac
import json
import os
import re
import signal
import socket
import socketserver
import stat
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qsl, unquote

from .. import config as jconfig
from .. import version
from ..config import Config
from . import api, auth, sse, static, writes

# worker-src and manifest-src name what default-src already implies, so the PWA's service worker and manifest are
# allowed (and only from this origin) on purpose, not by the fallback chain a browser may or may not follow.
CSP = ("default-src 'self'; img-src 'self' data:; media-src 'self' blob:; worker-src 'self'; manifest-src 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")
MAX_STREAMS = 8      # concurrent /events streams (each holds a thread; the unit allows 64 tasks)
BIND_RETRY = 60.0    # seconds to wait for the bind address to exist (a Tailscale IP at boot)


class App:
    """The server's state: the configuration (reloaded when its files change), the token and cookie key
    (reloaded when their files change), and the routes. ``clock`` gives "now" (tests pin it)."""

    def __init__(self, load_config: Callable[[], Config], *, clock: Callable[[], datetime] | None = None,
                 dev: bool = False, access_log: bool = False, poll_seconds: float = 2.0, ping_seconds: float = 25.0,
                 max_streams: int = MAX_STREAMS, log=None, spawn: Callable | None = None):
        self._load = load_config
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.dev, self.access_log = dev, access_log
        self.poll_seconds, self.ping_seconds = poll_seconds, ping_seconds
        self.stopping = threading.Event()
        self._streams = threading.BoundedSemaphore(max_streams)
        self._lock = threading.Lock()
        self._cfg: Config | None = None
        self._cfg_sig = None
        self._token: tuple | None = None   # (signature, token)
        self._key: tuple | None = None     # (signature, key)
        self.log = log or (lambda line: print(line, file=sys.stderr, flush=True))
        self.spawn = spawn or writes.spawn_journal   # (cfg, argv tail) -> log path; tests substitute a recorder
        self._routes = [(m, re.compile(p + r"\Z"), fn, a) for m, p, fn, a in [*api.ROUTES, *writes.ROUTES]]
        self._routes.append(("GET", re.compile(r"/api/v1/events\Z"), self._events, api.TOKEN))

    # ── configuration, token, cookie key ─────────────────────────────────────

    @staticmethod
    def _sig(paths) -> tuple:
        out = []
        for p in paths:
            try:
                st = Path(p).stat()
                out.append((str(p), st.st_mtime_ns, st.st_size))
            except (OSError, TypeError):
                out.append((str(p), None, None))
        return tuple(out)

    def _config_files(self, cfg: Config) -> list:
        files = [cfg.config_file] if cfg.config_file else []
        if cfg.hermes_home:
            files.append(Path(cfg.hermes_home) / "config.yaml")
        return files

    def config(self) -> Config:
        """The current configuration; reloaded when the config file or the Hermes settings change (a travel
        update moves the timezone). Raises ``ConfigError`` when it has become unusable."""
        with self._lock:
            if self._cfg is not None and self._sig(self._config_files(self._cfg)) == self._cfg_sig:
                return self._cfg
            cfg = self._load()
            self._cfg, self._cfg_sig = cfg, self._sig(self._config_files(cfg))
            return cfg

    def token(self, cfg: Config, *, create: bool = False) -> str:
        """The bearer token. File-backed: re-read when the file changes (``serve token --rotate``); from the
        environment or a command: resolved once per process."""
        with self._lock:
            backed = auth.file_backed(cfg)
            sig = ("file", self._sig([auth.token_file(cfg)])) if backed else ("fixed",)
            if self._token is None or self._token[0] != sig:
                tok, where = auth.resolve_token(cfg, create=create)
                if not tok:
                    raise api.HTTPError(503, "engine-unavailable", f"no serve token ({where})")
                sig = ("file", self._sig([auth.token_file(cfg)])) if backed else ("fixed",)
                self._token = (sig, tok)
            return self._token[1]

    def cookie_key(self, cfg: Config) -> bytes:
        with self._lock:
            sig = self._sig([auth.auth_file(cfg)])
            if self._key is None or self._key[0] != sig:
                key = auth.cookie_key(cfg)
                self._key = (self._sig([auth.auth_file(cfg)]), key)
            return self._key[1]

    def prime(self) -> tuple[bool, str]:
        """Resolve the configuration, the token (creating a file-backed one) and the cookie key before the first
        request, so a token command that fails stops the server at start, not on every request."""
        cfg = None
        try:
            cfg = self.config()
            self.token(cfg, create=True)
            self.cookie_key(cfg)
            version.code_id()  # pin the fingerprint to the code this process loaded, not to the disk at the first /healthz
        except jconfig.ConfigError as e:
            return False, f"configuration problem: {e}"
        except api.HTTPError as e:
            return False, e.detail
        except OSError as e:
            return False, f"cannot write the serve state in {cfg.state_dir if cfg else 'the state dir'}: {e}"
        return True, ""

    def secure_cookie(self, req: api.Request) -> bool:
        """``Secure`` always, except plain-HTTP ``--dev`` (a browser drops a Secure cookie over http)."""
        return not (self.dev and req.scheme == "http")

    def signed_in(self, cfg: Config, headers, secure: bool = True, via: str = "tcp",
                  peer_uid: int | None = None) -> tuple[str, str | None] | None:
        """How the request is signed in: ``("tailscale", login)`` (the Unix socket only, from tailscaled),
        ``("token", None)``, ``("cookie", None)``, or None. A listed login needs nothing else; otherwise a bearer
        token, when sent, decides alone (a wrong one is not rescued by a cookie)."""
        login = auth.tailscale_login(cfg, headers, via=via, peer_uid=peer_uid)
        if login is not None:
            return ("tailscale", login)
        token = self.token(cfg)
        given = auth.bearer_from(headers.get("Authorization"))
        if given is not None:
            return ("token", None) if hmac.compare_digest(given.encode(), token.encode()) else None
        cookie = auth.cookie_from(headers.get("Cookie"), secure)
        if cookie is None:
            return None
        ok = hmac.compare_digest(cookie.encode(), auth.session_value(self.cookie_key(cfg), token).encode())
        return ("cookie", None) if ok else None

    # ── /events ──────────────────────────────────────────────────────────────

    def _events(self, req: api.Request):
        if not self._streams.acquire(blocking=False):
            raise api.HTTPError(503, "busy", "too many event streams open")

        def run(write, stop):
            sse.stream(self.config, write, stop, poll_seconds=self.poll_seconds, ping_seconds=self.ping_seconds)
        return api.StreamReply(run, release=self._streams.release)

    # ── one request ──────────────────────────────────────────────────────────

    @staticmethod
    def _proxied(cfg: Config | None, peer: str, via: str) -> bool:
        """Whether ``X-Forwarded-*`` is believed: from ``serve.trusted_proxies``, and on the Unix socket, which only
        the proxy (``tailscale serve``, which sets those headers itself) can reach."""
        return via == auth.UNIX or (cfg is not None and auth.trusted_peer(cfg, peer))

    def scheme(self, cfg: Config | None, headers, peer: str, via: str = "tcp") -> str:
        if self._proxied(cfg, peer, via):
            proto = (headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
            if proto in ("http", "https"):
                return proto
        return "http"

    def client(self, cfg: Config | None, headers, peer: str, via: str = "tcp") -> str:
        if self._proxied(cfg, peer, via):
            fwd = (headers.get("X-Forwarded-For") or "").split(",")[0].strip()
            if fwd:
                return fwd
        return peer

    @staticmethod
    def host(headers, via: str = "tcp") -> str | None:
        """The host the browser asked for: the ``Host`` header, except on the Unix socket, where ``tailscale serve``
        sends ``Host: localhost`` and the browser's host in ``X-Forwarded-Host`` (missing: ``Host``, which then
        fails a configured ``serve.host``)."""
        if via == auth.UNIX:
            return headers.get("X-Forwarded-Host") or headers.get("Host")
        return headers.get("Host")

    def handle(self, method: str, target: str, headers, peer: str, read_body: Callable[[int], bytes],
               via: str = "tcp", peer_uid: int | None = None):
        """Route one request; returns a reply object (``api.Reply``/``FileReply``/``StreamReply``). ``via``: the
        listener it came in on, ``"tcp"`` or ``auth.UNIX``; ``peer_uid``: on the socket, the connecting process's
        uid (``auth.peer_uid``)."""
        raw_path, _, qs = target.partition("?")
        path = unquote(raw_path) or "/"
        try:
            cfg = self.config()
        except jconfig.ConfigError as e:
            cfg, cfg_error = None, str(e)
        else:
            cfg_error = None
        health = path in ("/healthz", "/api/v1/healthz")
        host = self.host(headers, via)
        if cfg is not None and not health and not auth.host_ok(cfg, host):
            return api.Reply(403, {"error": "host", "detail": "this server answers for its configured host only"})
        scheme = self.scheme(cfg, headers, peer, via)
        if method == "OPTIONS":
            if self.dev:
                return api.Reply(204, None, [("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE"),
                                             ("Access-Control-Allow-Headers", "Content-Type, Authorization")])
            return api.Reply(405, {"error": "method", "detail": "OPTIONS is answered only with --dev"})
        if method not in ("GET", "HEAD") and cfg is not None \
                and not auth.origin_ok(cfg, headers, scheme=scheme, dev=self.dev, host=host):
            return api.Reply(403, {"error": "origin", "detail": "writes must come from this site"})
        if not (path.startswith("/api/") or health):
            if method not in ("GET", "HEAD"):
                return api.Reply(405, {"error": "method", "detail": "only GET here"})
            if cfg is None:
                return api.Reply(503, {"error": "engine-unavailable", "detail": cfg_error})
            found = static.lookup(cfg, path)
            return found or api.Reply(404, {"error": "not-found", "detail": "no such page"})
        if cfg is None:
            return api.Reply(503, {"error": "engine-unavailable", "detail": f"configuration problem: {cfg_error}"})
        want = "GET" if method == "HEAD" else method
        matched = [(m, fn, a, rx.match(path)) for m, rx, fn, a in self._routes if rx.match(path)]
        public = any(a == api.PUBLIC for _m, _fn, a, _x in matched)
        try:
            who = None if public else self.signed_in(cfg, headers, not (self.dev and scheme == "http"), via, peer_uid)
            if not public and who is None:
                return api.Reply(401, {"error": "token", "detail": "pair this browser (journal serve pair) or send "
                                                                   "Authorization: Bearer <token>"})
            route = next(((fn, a, mt) for m, fn, a, mt in matched if m == want), None)
            if route is None:
                if matched:
                    return api.Reply(405, {"error": "method", "detail": f"{method} is not allowed here"})
                return api.Reply(404, {"error": "not-found", "detail": "no such route"})
            fn, kind, mt = route
            if kind == api.WRITE and cfg.serve.get("allow_write") is not True:  # anything but a plain true is read-only
                return api.Reply(405, {"error": "read-only", "detail": "this server was started with "
                                                                       "serve.allow_write off"})
            length = _length(headers)
            limit = getattr(fn, "body_limit", api.JSON_LIMIT)
            if length > limit:
                return api.Reply(413, {"error": "too-large", "detail": f"this body is at most {limit // 2**20} MB"})
            body = read_body(length) if length else b""
            req = api.Request(method=want, path=path, query=dict(parse_qsl(qs, keep_blank_values=True)),
                              params=mt.groupdict(), headers=headers, body=body, cfg=cfg, now=self.clock(),
                              app=self, scheme=scheme, credential=who[0] if who else "",
                              login=who[1] if who else None)
            out = fn(req)
        except api.HTTPError as e:
            return api.Reply(e.status, e.payload())
        if isinstance(out, (api.Reply, api.FileReply, api.StreamReply)):
            return out
        return api.Reply(200, out)


def _length(headers) -> int:
    try:
        n = int(headers.get("Content-Length") or 0)
    except ValueError:
        return 0
    return max(n, 0)


class Handler(BaseHTTPRequestHandler):
    server_version = "journal"
    sys_version = ""
    protocol_version = "HTTP/1.1"
    timeout = 60  # an idle keep-alive connection gives its thread back

    def do_GET(self):
        self._dispatch()

    do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_GET

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler's name
        pass  # the default would log the request line, query string included

    def _dispatch(self):
        app: App = self.server.app
        status, sent = 500, 0
        self._read = 0

        def read_body(n: int) -> bytes:
            data = self.rfile.read(n)
            self._read += len(data)
            return data
        via = getattr(self.server, "via", "tcp")
        peer = self.client_address[0] if isinstance(self.client_address, tuple) and self.client_address else via
        uid = auth.peer_uid(self.connection) if via == auth.UNIX else None
        try:
            reply = app.handle(self.command, self.path, self.headers, peer, read_body, via=via, peer_uid=uid)
            status, sent = self._send(app, reply)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:  # noqa: BLE001 - one request fails, the server stays up
            app.log("journal serve: " + traceback.format_exc(limit=8).replace("\n", " | "))
            try:
                status, sent = self._send(app, api.Reply(500, {"error": "internal", "detail": "see the server log"}))
            except OSError:
                self.close_connection = True
        if app.access_log:
            try:
                cfg = app.config()
            except jconfig.ConfigError:
                cfg = None
            who = app.client(cfg, self.headers, peer, via)
            app.log(f"{who} {self.command} {self.path.split('?')[0]} {status} {sent}")

    def _body_left(self) -> bool:
        """Whether the request carries a body the handler did not read (a refused request, a GET with a body, or
        any chunked one): left on the connection it would be parsed as the next request, so the reply closes."""
        if self.headers.get("Transfer-Encoding"):
            return True
        try:
            return int(self.headers.get("Content-Length") or 0) > self._read
        except ValueError:
            return True

    def _common(self, app: App, api_path: bool):
        if self._body_left():
            self.send_header("Connection", "close")
            self.close_connection = True
        self.send_header("Content-Security-Policy", CSP)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if api_path:
            self.send_header("Cache-Control", "no-store")
        origin = self.headers.get("Origin") or ""
        if app.dev and origin.lower().rstrip("/") in auth.DEV_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Credentials", "true")
            self.send_header("Vary", "Origin")

    def _send(self, app: App, reply) -> tuple[int, int]:
        # the same decoded path ``App.handle`` routed on: "/%61pi/v1/today" is an /api response and gets no-store
        decoded = unquote(self.path.split("?")[0])
        api_path = decoded.startswith("/api/") or decoded == "/healthz"
        head = self.command == "HEAD"
        if isinstance(reply, api.StreamReply):
            return self._stream(app, reply, head)
        if isinstance(reply, api.FileReply):
            return self._file(app, reply, api_path, head)
        body = b"" if reply.payload is None else json.dumps(reply.payload, ensure_ascii=False).encode()
        self.send_response(reply.status)
        self._common(app, api_path)
        for k, v in reply.headers:
            self.send_header(k, v)
        if reply.payload is not None:
            self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head and body:
            self.wfile.write(body)
        return reply.status, len(body)

    def _file(self, app: App, reply: api.FileReply, api_path: bool, head: bool) -> tuple[int, int]:
        size = reply.path.stat().st_size
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range") if reply.ranges else None
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", (rng or "").strip())
        if m and (m.group(1) or m.group(2)):
            if m.group(1):
                start = int(m.group(1))
                end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
            else:
                start = max(size - int(m.group(2)), 0)
            if start > end or start >= size:
                self.send_response(416)
                self._common(app, api_path)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return 416, 0
            status = 206
        length = end - start + 1 if size else 0
        self.send_response(status)
        self._common(app, api_path)
        if not api_path:
            self.send_header("Cache-Control", reply.cache)
        self.send_header("Content-Type", reply.content_type)
        self.send_header("Content-Length", str(length))
        if reply.ranges:
            self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if head or not length:
            return status, 0
        with open(reply.path, "rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(65536, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)
        return status, length - left

    def _stream(self, app: App, reply: api.StreamReply, head: bool = False) -> tuple[int, int]:
        sent = 0
        try:
            self.send_response(200)
            self._common(app, True)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store, no-transform")
            self.send_header("X-Accel-Buffering", "no")
            if not self.close_connection:  # (_common already said so when a request body is left unread)
                self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            if head:
                return 200, 0  # the headers only: no stream, and the slot goes back (the finally)

            def write(data: bytes):
                nonlocal sent
                self.wfile.write(data)
                self.wfile.flush()
                sent += len(data)
            reply.run(write, app.stopping)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            reply.release()
        return 200, sent


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    via = "tcp"

    def __init__(self, address, app: App):
        self.app = app
        if ":" in address[0]:
            self.address_family = socket.AF_INET6
        super().__init__(address, Handler)

    def server_close(self):
        self.app.stopping.set()
        super().server_close()


def make_server(app: App, bind: str, port: int, *, retry_seconds: float = BIND_RETRY,
                sleep: Callable[[float], None] = time.sleep, note=None) -> Server:
    """Bind, retrying for ``retry_seconds`` while the address does not exist yet (or the port is still held by
    the previous process); then the last ``OSError`` propagates."""
    deadline = time.monotonic() + retry_seconds
    warned = False
    while True:
        try:
            return Server((bind, port), app)
        except OSError as e:
            if e.errno not in (errno.EADDRNOTAVAIL, errno.EADDRINUSE) or time.monotonic() >= deadline:
                raise
            if note and not warned:
                note(f"journal serve: {bind}:{port} is not available yet ({e.strerror}); retrying for "
                     f"{retry_seconds:g} s")
                warned = True
            sleep(min(1.0, max(0.0, deadline - time.monotonic())) or 0.05)


class UnixServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    """The Unix socket listener (``serve.socket``) that ``tailscale serve`` proxies to. The socket is created 0600
    (the umask is narrowed around ``bind``, then the mode is set outright), so only the service user, and root, can
    connect; its parent directory, when missing, is made 0700. The directory must be this user's own and writable
    by no one else (``_check_parent``): another local user who could write there could replace the socket with a
    listener of their own, and tailscaled would hand them his browser's requests, cookie included. ``server_close``
    removes the file, if it is still the one this server made."""
    daemon_threads = True
    via = auth.UNIX

    def __init__(self, path: Path, app: App):
        self.app = app
        self.path = Path(path)
        self._made = None   # (st_dev, st_ino) of the socket file this server created
        super().__init__(str(path), Handler)

    def server_bind(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _check_parent(self.path.parent)
        _clear_stale(self.path)
        old = os.umask(0o177)
        try:
            self.socket.bind(str(self.path))
        finally:
            os.umask(old)
        os.chmod(self.path, 0o600)
        st = os.lstat(self.path)
        self._made = (st.st_dev, st.st_ino)
        self.server_address = str(self.path)

    def server_close(self):
        super().server_close()
        if self._made is None:
            return   # never bound (``TCPServer.__init__`` closes a server whose bind failed): nothing to stop
        self.app.stopping.set()
        try:
            st = os.lstat(self.path)
            if stat.S_ISSOCK(st.st_mode) and (st.st_dev, st.st_ino) == self._made:
                os.unlink(self.path)
        except OSError:
            pass
        self._made = None


def _check_parent(directory: Path) -> None:
    """Refuse (``EPERM``, not retried) a socket directory that is a symlink or not a directory, that belongs to
    another user, or that group or others can write: a socket directly in ``/tmp`` is refused, and so is a
    directory under it that another user made first."""
    st = os.lstat(directory)
    where = "put serve.socket in a directory of this user's own (/run/user/<uid>/journal-serve/serve.sock)"
    if not stat.S_ISDIR(st.st_mode):
        raise OSError(errno.EPERM, f"{directory} is not a directory (a symlink is not followed): {where}")
    if st.st_uid != os.getuid():
        raise OSError(errno.EPERM, f"{directory} belongs to uid {st.st_uid}, not this user ({os.getuid()}), who could "
                                   f"replace the socket: {where}")
    if st.st_mode & 0o022:
        raise OSError(errno.EPERM, f"{directory} is writable by group or others (mode {stat.S_IMODE(st.st_mode):o}), "
                                   f"who could replace the socket: {where}")


def _clear_stale(path: Path) -> None:
    """Make room for the socket: nothing there is fine; a socket nobody answers on is a server that died without
    cleaning up, so it is removed; a socket that answers is another server (``EADDRINUSE``, retried); a socket of
    another user's (``EPERM``) and anything else (a file, a directory; ``EEXIST``) are never removed or reused."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if not stat.S_ISSOCK(st.st_mode):
        raise OSError(errno.EEXIST, f"{path} exists and is not a socket; it is left alone")
    if st.st_uid != os.getuid():
        raise OSError(errno.EPERM, f"{path} is a socket of uid {st.st_uid}, not this user's; it is left alone")
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(1.0)
    try:
        probe.connect(str(path))
    except (ConnectionRefusedError, FileNotFoundError):
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    finally:
        probe.close()
    raise OSError(errno.EADDRINUSE, f"another server is listening on {path}")


def make_unix_server(app: App, path: Path, *, retry_seconds: float = BIND_RETRY,
                     sleep: Callable[[float], None] = time.sleep, note=None) -> UnixServer:
    """Listen on the Unix socket, retrying for ``retry_seconds`` while another server still holds it (the previous
    process stopping); then the last ``OSError`` propagates."""
    deadline = time.monotonic() + retry_seconds
    warned = False
    while True:
        try:
            return UnixServer(Path(path), app)
        except OSError as e:
            if e.errno != errno.EADDRINUSE or time.monotonic() >= deadline:
                raise
            if note and not warned:
                note(f"journal serve: unix:{path} is in use; retrying for {retry_seconds:g} s")
                warned = True
            sleep(min(1.0, max(0.0, deadline - time.monotonic())) or 0.05)


def _interrupt(_signum, _frame):
    raise KeyboardInterrupt


def run(app: App, bind: str, port: int, *, socket_path: Path | None = None, retry_seconds: float = BIND_RETRY,
        ready=None) -> int:
    """Serve until interrupted. 1 when the address (or the socket) cannot be bound within ``retry_seconds``
    (systemd restarts). With ``socket_path`` the Unix socket is served too, on its own thread, and a SIGTERM stops
    the server the way Ctrl-C does, so the socket file is removed at stop."""
    unix = None
    if socket_path is not None:
        try:
            unix = make_unix_server(app, socket_path, retry_seconds=retry_seconds, note=app.log)
        except OSError as e:
            app.log(f"journal serve: cannot listen on unix:{socket_path}: {e.strerror or e}")
            return 1
    try:
        server = make_server(app, bind, port, retry_seconds=retry_seconds, note=app.log)
    except OSError as e:
        app.log(f"journal serve: cannot listen on {bind}:{port}: {e.strerror or e}")
        if unix is not None:
            unix.server_close()
        return 1
    host, real_port = server.server_address[:2]
    app.log(f"journal serve: listening on {host}:{real_port}" + (" (dev)" if app.dev else ""))
    previous = None
    if unix is not None:
        app.log(f"journal serve: listening on unix:{unix.path}")
        threading.Thread(target=unix.serve_forever, kwargs={"poll_interval": 0.5}, daemon=True,
                         name="journal-serve-unix").start()
        if threading.current_thread() is threading.main_thread():
            previous = signal.signal(signal.SIGTERM, _interrupt)
    if ready is not None:
        ready(server)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
        if unix is not None:
            unix.shutdown()
            unix.server_close()
        server.server_close()
    return 0
