"""Who may talk to ``journal serve``: the token, pairing, the session cookie, and the Host/Origin/proxy checks.

The network is the first gate (the operator binds and firewalls; API.md Decision 5); this module is the second,
because on a shared host every container behind the proxy can reach the port. Every ``/api`` request carries
either ``Authorization: Bearer <token>`` or the session cookie that pairing sets.

* **The token** comes from ``serve.token_env``, else ``serve.token_command`` (the same resolver as the bot token),
  else, when neither is configured, a generated ``state_dir/serve-token`` (0600), so a standalone install works
  without a secret store. A configured command that fails is a failure, never a silent fallback to the file: two
  processes must not end up with different tokens.
* **The cookie** is not the token. It is an HMAC of the token under a cookie key kept in ``serve-auth.json``
  (0600), so a cookie lifted from a browser is not a bearer token, and ``journal serve token --rotate`` (a new
  cookie key, and a new token when the token is file-backed) signs every browser out at once.
* **Pairing** hands a phone the cookie without pasting the token: ``journal serve pair`` stores the sha256 of a
  one-time code with a ten-minute expiry; ``POST /api/v1/pair`` redeems it once, under the state lock.
* **Host and Origin.** The ``Host`` header must be ``serve.host`` when that is set; a write needs ``Origin`` equal to
  the site's own origin (or ``Sec-Fetch-Site: same-origin``); ``X-Forwarded-*`` is believed only from
  ``serve.trusted_proxies``, and on the Unix socket.
* **Tailscale identity.** On the Unix socket (``serve.socket``) only, a request whose single
  ``Tailscale-User-Login`` header names a login in ``serve.tailscale_users`` is signed in with no token or cookie.
  ``tailscale serve`` proxies to that socket and replaces any copy of the header a client sent. The socket is 0600
  and the service user's, but that user also runs other things (the companion's agent, with tools), so the header
  is believed only when the connecting process is tailscaled: its uid, read from the kernel (``SO_PEERCRED``), must
  be in ``TAILSCALED_UIDS`` (root). That is not enough: tailscaled also relays connections from this very host,
  on its own tailnet address, and signs them in as the node's owner, so a process of any user here would be him;
  the client tailscaled saw (``X-Forwarded-For``) must be another machine (``remote_client``). A request the
  browser marks cross-site or same-site (``Sec-Fetch-Site``) is not signed in by identity either, since an ambient
  sign-in would lose the SameSite=Lax protection a cookie has. Over TCP the header means nothing, whoever sends it, so a forged header or
  ``X-Forwarded-*`` never signs anyone in.

Nothing here logs or returns a secret.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
import struct
import tempfile
import time
from pathlib import Path

from .. import ledger
from ..config import Config, serve_proxies, serve_site, serve_tailscale_users
from ..telegram import resolve_secret

COOKIE = "journal_session"
UNIX = "unix"                     # the listener a request came in on: "tcp" or this
LOGIN_HEADER = "Tailscale-User-Login"
FUNNEL_HEADER = "Tailscale-Funnel-Request"
TAILSCALED_UIDS = (0,)            # the uids a socket peer may have for its identity header to count: tailscaled runs as root
HOST_COOKIE = "__Host-" + COOKIE  # Secure, Path=/, no Domain: a browser refuses it from a sibling domain or path
COOKIE_MAX_AGE = 400 * 24 * 3600  # browsers cap cookie lifetimes near 400 days
PAIR_TTL = 600                    # seconds a pairing code stays redeemable
TOKEN_FILE = "serve-token"
AUTH_FILE = "serve-auth.json"
PAIRINGS = "serve-pairings.json"


# ── the token ────────────────────────────────────────────────────────────────

def token_file(cfg: Config) -> Path:
    return cfg.state_dir / TOKEN_FILE


def file_backed(cfg: Config, environ: dict | None = None) -> bool:
    """Whether the token lives in ``state_dir/serve-token``: no environment variable holds one and no command is
    configured."""
    env = os.environ if environ is None else environ
    name = cfg.serve.get("token_env") or ""
    return not (name and env.get(name)) and not cfg.serve.get("token_command")


def _write_secret(path: Path, text: str) -> None:
    """Atomic 0600 write (created 0600 from the start, so the secret is never briefly world-readable)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def resolve_token(cfg: Config, *, create: bool = False, environ: dict | None = None) -> tuple[str | None, str]:
    """(token, where it came from). ``create``: make ``serve-token`` when the token is file-backed and absent."""
    if not file_backed(cfg, environ):
        return resolve_secret(cfg.serve, "JOURNAL_SERVE_TOKEN", "serve", environ)
    path = token_file(cfg)
    try:
        tok = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        tok = ""
    except OSError as e:
        return None, f"{path} cannot be read ({type(e).__name__})"
    if not tok and create:
        tok = secrets.token_urlsafe(32)
        _write_secret(path, tok + "\n")
    if tok:
        return tok, f"{path}"
    return None, (f"no token yet: {path} is created by `journal serve` or `journal serve pair` (or set "
                  f"{cfg.serve.get('token_env') or 'serve.token_env'} or serve.token_command)")


# ── the cookie ───────────────────────────────────────────────────────────────

def auth_file(cfg: Config) -> Path:
    return cfg.state_dir / AUTH_FILE


def cookie_key(cfg: Config, *, create: bool = True) -> bytes | None:
    """The key cookies are signed with (``serve-auth.json``), made on first use."""
    path = auth_file(cfg)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        key = bytes.fromhex(data["cookie_key"])
        if len(key) >= 16:
            return key
    except FileNotFoundError:
        pass
    except (OSError, ValueError, KeyError, TypeError):
        pass  # damaged: a new key (every browser pairs again), never a crash
    if not create:
        return None
    key = secrets.token_bytes(32)
    _write_secret(path, json.dumps({"cookie_key": key.hex(), "at": int(time.time())}) + "\n")
    return key


def session_value(key: bytes, token: str) -> str:
    """The cookie for this key and token: changing either signs every browser out."""
    return hmac.new(key, b"journal-serve-session:" + hashlib.sha256(token.encode()).digest(), hashlib.sha256).hexdigest()


def cookie_name(secure: bool = True) -> str:
    """``__Host-journal_session`` whenever the cookie is Secure (a browser then accepts it only if it was set by
    this host, with no Domain, so an app on a parent or sibling domain cannot plant or shadow it); the plain name
    for plain-HTTP ``--dev``, where a browser drops a Secure cookie."""
    return HOST_COOKIE if secure else COOKIE


def cookie_header(value: str, *, secure: bool = True, clear: bool = False) -> str:
    """``Set-Cookie``: host-only (no Domain), HttpOnly, SameSite=Lax, Secure unless serving plain-HTTP dev."""
    parts = [f"{cookie_name(secure)}={'' if clear else value}", "Path=/", "HttpOnly", "SameSite=Lax",
             f"Max-Age={0 if clear else COOKIE_MAX_AGE}"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def cookie_from(header: str | None, secure: bool = True) -> str | None:
    """The session cookie's value, or None. The header is split by hand, not parsed with ``SimpleCookie``: that
    parser gives up on the whole header at one malformed or JSON-valued cookie from another app, and lets a later
    cookie of the same name override an earlier one. The first cookie of the name wins."""
    if not header:
        return None
    name = cookie_name(secure)
    for part in header.split(";"):
        key, sep, value = part.partition("=")
        if sep and key.strip() == name:
            return value.strip()
    return None


def bearer_from(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return value.strip() or None


def rotate(cfg: Config, environ: dict | None = None) -> list[str]:
    """``journal serve token --rotate``: a new cookie key (every paired browser must pair again) and, when the
    token is file-backed, a new token. A token from the environment or a command is changed where it lives."""
    path = auth_file(cfg)
    key = secrets.token_bytes(32)
    _write_secret(path, json.dumps({"cookie_key": key.hex(), "at": int(time.time())}) + "\n")
    with ledger.locked_state(cfg.state_dir / PAIRINGS) as state:
        state["pairings"] = []  # a code printed before the rotation must not pair a browser under the new key
    lines = [f"new cookie key in {path}: every paired browser has to pair again"]
    if file_backed(cfg, environ):
        _write_secret(token_file(cfg), secrets.token_urlsafe(32) + "\n")
        lines.append(f"new token in {token_file(cfg)}; a running `journal serve` picks both up at its next request")
    else:
        _tok, where = resolve_token(cfg, environ=environ)
        lines.append(f"the bearer token comes from {where}: change it there, then restart `journal serve`")
    return lines


# ── pairing ──────────────────────────────────────────────────────────────────

def _digest(code: str) -> str:
    return hashlib.sha256(code.encode()).hexdigest()


def new_pairing(cfg: Config, now: float | None = None) -> str:
    """A one-time pairing code, valid ``PAIR_TTL`` seconds; only its hash is stored."""
    now = time.time() if now is None else now
    code = secrets.token_urlsafe(32)
    with ledger.locked_state(cfg.state_dir / PAIRINGS) as state:
        live = [p for p in state.get("pairings") or [] if isinstance(p, dict) and _num(p.get("expires")) > now]
        live.append({"hash": _digest(code), "expires": now + PAIR_TTL})
        state["pairings"] = live
    with contextlib.suppress(OSError):
        os.chmod(cfg.state_dir / PAIRINGS, 0o600)
    return code


def redeem(cfg: Config, code: str, now: float | None = None) -> bool:
    """Spend a pairing code: True once for a code that exists and has not expired; it is gone afterwards either
    way, and expired codes are dropped."""
    now = time.time() if now is None else now
    if not isinstance(code, str) or not code or len(code) > 200:
        return False
    want = _digest(code)
    with ledger.locked_state(cfg.state_dir / PAIRINGS) as state:
        found = False
        live = []
        for p in state.get("pairings") or []:
            if not isinstance(p, dict) or _num(p.get("expires")) <= now:
                continue
            if hmac.compare_digest(str(p.get("hash", "")), want):
                found = True
                continue
            live.append(p)
        state["pairings"] = live
    return found


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0.0


def site(cfg: Config) -> str:
    """The site's base URL as a browser reaches it (``config.serve_site``, which the chat's ``/web`` reads too)."""
    return serve_site(cfg)


def pair_url(cfg: Config, code: str) -> str:
    """The URL to open on the phone. The code rides in the fragment, which browsers never send to a server, so
    it stays out of every proxy's and server's request line; the page posts it."""
    return f"{site(cfg)}/pair#{code}"


# ── the request's origin ─────────────────────────────────────────────────────

def trusted_peer(cfg: Config, peer: str) -> bool:
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(ip in net for net in serve_proxies(cfg))


def host_ok(cfg: Config, host_header: str | None) -> bool:
    """``Host`` equals ``serve.host`` (case-insensitive, any port) when one is set; anything goes otherwise."""
    want = (cfg.serve.get("host") or "").strip().lower()
    if not want:
        return True
    got = (host_header or "").strip().lower()
    if got.startswith("["):
        got = got.split("]")[0] + "]"
    else:
        got = got.rsplit(":", 1)[0] if got.count(":") == 1 else got
    return got == want


def origin_ok(cfg: Config, headers, *, scheme: str, dev: bool = False, host: str | None = None) -> bool:
    """A write comes from the site itself: ``Origin`` is the site's origin (``site(cfg)``: ``https://<serve.host>``,
    with ``serve.public_port`` when set; or the scheme and host the request arrived with when no host is
    configured: ``host``, else the ``Host`` header), or the browser says
    ``Sec-Fetch-Site: same-origin``. ``--dev`` also admits the Vite dev server."""
    if (headers.get("Sec-Fetch-Site") or "").strip().lower() == "same-origin":
        return True
    origin = (headers.get("Origin") or "").strip().lower().rstrip("/")
    if not origin or origin == "null":
        return False
    want = (cfg.serve.get("host") or "").strip().lower()
    arrived = (headers.get("Host") if host is None else host) or ""
    allowed = {site(cfg).lower()} if want else {f"{scheme}://{arrived.strip().lower()}"}
    if dev:
        allowed |= set(DEV_ORIGINS)
    return origin in allowed


DEV_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def peer_uid(conn) -> int | None:
    """The uid of the process at the other end of a Unix socket connection, from the kernel (Linux
    ``SO_PEERCRED``), or None where it cannot be read: then no identity header counts (fail closed)."""
    opt = getattr(socket, "SO_PEERCRED", None)
    if opt is None:
        return None
    try:
        _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, opt, struct.calcsize("3i")))
    except (OSError, struct.error):
        return None
    return uid


def _bindable(ip: str) -> bool:
    """Whether this host can bind the address, i.e. it is one of its own (an interface address, tailnet one
    included). A host with ``ip_nonlocal_bind`` says yes to everything: then nobody is signed in (fail closed)."""
    family = socket.AF_INET6 if ":" in ip else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_DGRAM) as s:
            s.bind((ip, 0))
    except OSError:
        return False
    return True


def remote_client(headers) -> bool:
    """Whether ``X-Forwarded-For`` names a client that is not this host. tailscaled also answers connections from
    the machine itself, on its own tailnet address, and signs them in as the node's owner; so every process on the
    host (the companion's tools, any container) would be him with no token. tailscaled sets exactly one address
    there (what it saw as the peer), so: not exactly one parsable address, loopback, unspecified, or an address
    this host owns is no one."""
    values = headers.get_all("X-Forwarded-For") or []
    if len(values) != 1 or not isinstance(values[0], str):
        return False
    try:
        ip = ipaddress.ip_address(values[0].strip())
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback or ip.is_unspecified:
        return False
    return not _bindable(str(ip))


def tailscale_login(cfg: Config, headers, *, via: str, peer_uid: int | None = None) -> str | None:
    """The Tailscale login this request is signed in as, or None. Only on the Unix socket (``via == UNIX``), only
    from tailscaled (``peer_uid`` in ``TAILSCALED_UIDS``: another process of the service user can open the socket
    too), only when the client tailscaled saw is another machine (``remote_client``: tailscaled also relays this
    host's own processes, signed in as the node's owner), never on a request the browser marks cross-site or
    same-site (``Sec-Fetch-Site``: an ambient sign-in must not do what SameSite=Lax withholds from a cookie;
    the token and cookie still decide those), only with exactly one ``Tailscale-User-Login`` header, never on a
    Funnel request (Tailscale sends no login there, and the public internet is not his tailnet), and only for a
    login listed in ``serve.tailscale_users``, compared exactly. A login Tailscale had to RFC 2047-encode
    (non-ASCII) matches nothing: fail closed."""
    if via != UNIX or peer_uid is None or peer_uid not in TAILSCALED_UIDS:
        return None
    users = serve_tailscale_users(cfg)
    if not users or headers.get(FUNNEL_HEADER) is not None:
        return None
    if (headers.get("Sec-Fetch-Site") or "").strip().lower() in ("cross-site", "same-site"):
        return None
    if not remote_client(headers):
        return None
    values = headers.get_all(LOGIN_HEADER) or []   # http.client's message: every copy, not only the first
    if len(values) != 1 or not isinstance(values[0], str):
        return None
    login = values[0].strip()   # optional whitespace around a field value is not part of it (RFC 9110 5.5)
    return login if login in users else None