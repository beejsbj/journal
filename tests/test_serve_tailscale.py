"""``journal serve`` behind ``tailscale serve`` (TSAUTH): a Unix socket listener, and the Tailscale identity header
trusted on that socket only.

``tailscale serve --https=<port> unix:<path>`` proxies to the socket and sets ``Tailscale-User-Login`` (deleting
any copy the client sent); nothing else should reach the socket. So: on the socket a listed login is signed in
with no cookie or token; on TCP the header means nothing, whoever sends it and whatever ``X-Forwarded-*`` rides
along; writes still need the site's ``Origin``. Neutral names: Sam is the person, the site is journal.example.org.
"""
import errno
import http.client
import io
import json
import os
import socket
import stat
import tempfile
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from helpers import CleanEnv, make_cfg
from serve_helpers import NEUTRAL, ServeCase, build_journal

from journal import cli, status
from journal import config as jconfig
from journal.serve import app as serve_app
from journal.serve import auth, unit

SITE = "https://journal.example.org:8790"
SAM = "sam@example.org"


class UnixConnection(http.client.HTTPConnection):
    """An HTTP connection over a Unix socket, as tailscaled makes it (``Host: localhost``)."""

    def __init__(self, path, timeout=10):
        super().__init__("localhost", timeout=timeout)
        self.unix_path = str(path)

    def connect(self):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        s.connect(self.unix_path)
        self.sock = s


def proxied(login=SAM, **extra):
    """The headers ``tailscale serve`` adds for a tailnet user on https://journal.example.org:8790."""
    h = {"Host": "localhost", "X-Forwarded-Host": "journal.example.org:8790", "X-Forwarded-Proto": "https",
         "X-Forwarded-For": "100.64.0.7"}
    if login is not None:
        h["Tailscale-User-Login"] = login
        h["Tailscale-User-Name"] = "Sam"
    h.update(extra)
    return h


class SocketCase(ServeCase):
    """ServeCase's TCP server plus a socket listener on ``<tmp>/run/serve.sock``."""
    serve = {"host": "journal.example.org", "public_port": 8790, "tailscale_users": [SAM]}

    tailscaled_uids = (os.getuid(),)   # the suite plays tailscaled (which is root on a real host)

    def setUp(self):
        super().setUp()
        uids = mock.patch.object(auth, "TAILSCALED_UIDS", self.tailscaled_uids)
        uids.start()
        self.addCleanup(uids.stop)
        self.app.spawn = lambda cfg, args: str(cfg.state_dir / "manual-trigger.log")   # nothing is started for real
        self.sock_path = Path(self.tmp.name) / "run" / "serve.sock"
        self.cfg.serve["socket"] = str(self.sock_path)
        self.unix = serve_app.make_unix_server(self.app, self.sock_path, retry_seconds=0)
        self.unix_thread = threading.Thread(target=self.unix.serve_forever, kwargs={"poll_interval": 0.05},
                                            daemon=True)
        self.unix_thread.start()

    def tearDown(self):
        self.unix.shutdown()
        self.unix.server_close()
        self.unix_thread.join(5)
        super().tearDown()

    def via_socket(self, method, path, headers, body=None, extra=()):
        """(status, headers, body) over the socket; ``extra``: header lines added as they are (duplicates too)."""
        conn = UnixConnection(self.sock_path)
        try:
            conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
            data = json.dumps(body).encode() if body is not None else None
            for k, v in headers.items():
                conn.putheader(k, v)
            for k, v in extra:
                conn.putheader(k, v)
            if data is not None:
                conn.putheader("Content-Type", "application/json")
            conn.putheader("Content-Length", str(len(data or b"")))
            conn.endheaders(data)
            r = conn.getresponse()
            return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
        finally:
            conn.close()

    def tcp(self, path, headers, method="GET"):
        return self.request(method, path, headers=headers, token=False)[0]


class IdentityOnTheSocketTest(SocketCase):
    def test_a_listed_login_reads_with_no_cookie_or_token(self):
        code, _h, body = self.via_socket("GET", "/api/v1/today", proxied())
        self.assertEqual(code, 200, body[:200])
        code, _h, body = self.via_socket("GET", "/api/v1/session", proxied())
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"via": "tailscale", "login": SAM})

    def test_an_unlisted_login_or_none_is_401(self):
        for login in ("someone@example.org", "SAM@example.org ", "", None, f"{SAM},{SAM}", "sam"):
            code, _h, _b = self.via_socket("GET", "/api/v1/today", proxied(login))
            self.assertEqual(code, 401, repr(login))

    def test_two_login_headers_are_no_login(self):
        h = proxied()
        code, _h, _b = self.via_socket("GET", "/api/v1/today", h, extra=[("Tailscale-User-Login", "other@example.org")])
        self.assertEqual(code, 401)
        code, _h, _b = self.via_socket("GET", "/api/v1/today", h, extra=[("Tailscale-User-Login", SAM)])
        self.assertEqual(code, 401)

    def test_a_funnel_request_is_never_signed_in(self):
        code, _h, _b = self.via_socket("GET", "/api/v1/today", proxied(**{"Tailscale-Funnel-Request": "?1"}))
        self.assertEqual(code, 401)

    def test_no_users_listed_means_the_header_signs_no_one_in(self):
        self.cfg.serve["tailscale_users"] = []
        self.assertEqual(self.via_socket("GET", "/api/v1/today", proxied())[0], 401)
        self.cfg.serve["tailscale_users"] = SAM   # a string, not a list: unusable, so no one
        self.assertEqual(self.via_socket("GET", "/api/v1/today", proxied())[0], 401)

    def test_the_token_and_cookie_still_work_on_the_socket(self):
        h = proxied(None, Authorization=f"Bearer {self.token}")
        code, _h, body = self.via_socket("GET", "/api/v1/session", h)
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"via": "token", "login": None})
        cookie = auth.session_value(self.app.cookie_key(self.cfg), self.token)
        h = proxied("stranger@example.org", Cookie=f"{auth.HOST_COOKIE}={cookie}")
        code, _h, body = self.via_socket("GET", "/api/v1/session", h)
        self.assertEqual(json.loads(body), {"via": "cookie", "login": None})

    def test_the_host_check_reads_the_forwarded_host_on_the_socket(self):
        self.assertEqual(self.via_socket("GET", "/api/v1/today", proxied(**{"X-Forwarded-Host": "evil.example"}))[0],
                         403)
        h = proxied()
        del h["X-Forwarded-Host"]   # tailscaled always sends it; without it the Host is "localhost": refused
        self.assertEqual(self.via_socket("GET", "/api/v1/today", h)[0], 403)
        self.assertEqual(self.via_socket("GET", "/healthz", proxied(None))[0], 200)

    def test_a_write_needs_the_sites_origin(self):
        for origin in (None, "https://evil.example", "https://journal.example.org", "http://journal.example.org:8790",
                       "null"):
            h = proxied() if origin is None else proxied(Origin=origin)
            code, _h, body = self.via_socket("POST", "/api/v1/logout", h)
            self.assertEqual(code, 403, origin)
            self.assertEqual(json.loads(body)["error"], "origin")
        self.assertEqual(self.via_socket("POST", "/api/v1/logout", proxied(Origin=SITE))[0], 204)
        self.assertEqual(self.via_socket("POST", "/api/v1/logout", proxied(**{"Sec-Fetch-Site": "same-origin"}))[0], 204)

    def test_a_journal_write_by_identity(self):
        code, _h, body = self.via_socket("POST", "/api/v1/entries", proxied(Origin=SITE), body={"text": "by the sea"})
        self.assertEqual(code, 201, body[:300])


class ThisHostIsNotHimTest(SocketCase):
    """tailscaled answers connections from the machine itself on its tailnet address, and signs them in as the node's
    owner. Without this check any process on the host (the companion's tools, any container) would be him, with no
    token. ``X-Forwarded-For`` is what tailscaled saw as the client: this host, or nothing usable, is no one."""

    def status_for(self, forwarded):
        h = proxied()
        if forwarded is None:
            del h["X-Forwarded-For"]
        else:
            h["X-Forwarded-For"] = forwarded
        return self.via_socket("GET", "/api/v1/today", h)[0]

    def test_the_client_being_this_host_is_no_one(self):
        for ip in ("127.0.0.1", "127.0.0.2", "::1", "::ffff:127.0.0.1", "0.0.0.0", "::"):
            self.assertEqual(self.status_for(ip), 401, ip)

    def test_an_address_this_host_can_bind_is_no_one(self):
        with mock.patch.object(auth, "_bindable", lambda ip: ip == "100.64.0.9"):
            self.assertEqual(self.status_for("100.64.0.9"), 401)
            self.assertEqual(self.status_for("100.64.0.7"), 200)

    def test_a_real_local_address_is_no_one(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            try:
                probe.connect(("192.0.2.1", 9))   # no packet is sent; it only picks the outgoing address
                mine = probe.getsockname()[0]
            except OSError:
                self.skipTest("no outgoing route to read an own address from")
        self.assertEqual(self.status_for(mine), 401, mine)

    def test_no_usable_client_address_is_no_one(self):
        for forwarded in (None, "", "not an ip", "100.64.0.7, 127.0.0.1", "100.64.0.7:4040", "[::1]",
                          "100.64.0.7;for=1"):
            self.assertEqual(self.status_for(forwarded), 401, repr(forwarded))
        h = proxied()
        code, _h, _b = self.via_socket("GET", "/api/v1/today", h, extra=[("X-Forwarded-For", "127.0.0.1")])
        self.assertEqual(code, 401)   # two copies: not what tailscaled sets

    def test_another_device_on_the_tailnet_is_him(self):
        for ip in ("100.64.0.7", "100.101.102.103", "fd7a:115c:a1e0::1"):
            self.assertEqual(self.status_for(ip), 200, ip)

    def test_the_token_still_works_from_this_host(self):
        h = proxied(None, Authorization=f"Bearer {self.token}", **{"X-Forwarded-For": "127.0.0.1"})
        self.assertEqual(self.via_socket("GET", "/api/v1/session", h)[0], 200)


class CrossSiteIsNotHimTest(SocketCase):
    """A tailnet user's browser sends no cookie on a cross-site subresource request (SameSite=Lax); an identity
    sign-in is ambient, so it takes over that protection: a request the browser marks cross-site or same-site
    (another port of the same host is same-site) falls back to the token and cookie, which are not there."""

    def test_cross_site_and_same_site_requests_are_not_signed_in_by_identity(self):
        for site in ("cross-site", "same-site", "Cross-Site", " same-site "):
            for path in ("/api/v1/today", "/api/v1/entries?q=harbour", "/api/v1/media/2026-10-14-evening.ogg"):
                code, _h, _b = self.via_socket("GET", path, proxied(**{"Sec-Fetch-Site": site,
                                                                       "Sec-Fetch-Mode": "no-cors"}))
                self.assertEqual(code, 401, (site, path))

    def test_same_origin_none_and_no_header_are_signed_in(self):
        for site in ("same-origin", "none", None):
            extra = {} if site is None else {"Sec-Fetch-Site": site}
            self.assertEqual(self.via_socket("GET", "/api/v1/today", proxied(**extra))[0], 200, site)

    def test_the_token_is_not_affected(self):
        h = proxied(None, Authorization=f"Bearer {self.token}", **{"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(self.via_socket("GET", "/api/v1/today", h)[0], 200)


class IdentityOverTcpTest(SocketCase):
    """The header is believed on the socket only: over TCP it never authenticates."""

    def test_the_header_over_tcp_is_ignored(self):
        h = {"Host": "journal.example.org:8790", "Tailscale-User-Login": SAM}
        self.assertEqual(self.tcp("/api/v1/today", h), 401)
        self.assertEqual(self.tcp("/api/v1/session", h), 401)

    def test_forged_forwarded_and_tailscale_headers_over_tcp_never_authenticate(self):
        h = proxied(Host="journal.example.org:8790")
        h.update({"X-Forwarded-For": "100.64.0.7", "Forwarded": "for=100.64.0.7;proto=https",
                  "X-Real-IP": "100.64.0.7", "Tailscale-Headers-Info": "https://tailscale.com/s/serve-headers"})
        self.assertEqual(self.tcp("/api/v1/today", h), 401)
        self.cfg.serve["trusted_proxies"] = ["127.0.0.0/8"]   # even from a proxy whose X-Forwarded-* is believed
        self.assertEqual(self.tcp("/api/v1/today", h), 401)
        self.assertEqual(self.request("POST", "/api/v1/entries", {"text": "x"}, token=False,
                                      headers={**h, "Origin": SITE})[0], 401)

    def test_tcp_still_takes_the_token(self):
        self.assertEqual(self.request("GET", "/api/v1/today", headers={"Host": "journal.example.org"})[0], 200)


class NotTailscaledTest(SocketCase):
    """Another process of the service user (the companion's tools, say) can open the 0600 socket; its header
    counts for nothing, since only tailscaled's uid (root) is believed."""
    tailscaled_uids = (0,) if os.getuid() != 0 else (65534,)

    def test_a_peer_that_is_not_tailscaled_is_not_signed_in_by_the_header(self):
        self.assertEqual(self.via_socket("GET", "/api/v1/today", proxied())[0], 401)
        code, _h, body = self.via_socket("GET", "/api/v1/session", proxied(None, Authorization=f"Bearer {self.token}"))
        self.assertEqual(json.loads(body), {"via": "token", "login": None})   # its token still works

    def test_the_peer_uid_is_read_from_the_kernel(self):
        a, b = socket.socketpair(socket.AF_UNIX)
        with a, b:
            self.assertEqual(auth.peer_uid(a), os.getuid())
        with mock.patch.object(auth.socket, "SO_PEERCRED", None, create=True):
            self.assertIsNone(auth.peer_uid(a))   # cannot tell: no identity (fail closed)


class SocketFileTest(SocketCase):
    def test_the_socket_is_0600_owned_by_this_user(self):
        st = os.lstat(self.sock_path)
        self.assertTrue(stat.S_ISSOCK(st.st_mode))
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(st.st_uid, os.getuid())

    def test_the_socket_is_removed_at_stop(self):
        self.unix.shutdown()
        self.unix.server_close()
        self.assertFalse(self.sock_path.exists())
        self.unix = serve_app.make_unix_server(self.app, self.sock_path, retry_seconds=0)   # tearDown closes it
        self.unix_thread = threading.Thread(target=self.unix.serve_forever, daemon=True)
        self.unix_thread.start()

    def test_a_live_socket_is_not_taken_over(self):
        with self.assertRaises(OSError):
            serve_app.make_unix_server(self.app, self.sock_path, retry_seconds=0)
        self.assertEqual(self.via_socket("GET", "/api/v1/today", proxied())[0], 200)


class SocketPathTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = make_cfg(Path(self.tmp.name), names=NEUTRAL)
        self.app = serve_app.App(lambda: self.cfg, log=lambda line: None)

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def test_a_stale_socket_is_replaced(self):
        path = Path(self.tmp.name) / "s.sock"
        old = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old.bind(str(path))
        old.close()   # the file stays, nobody listens: a server that died without cleaning up
        server = serve_app.make_unix_server(self.app, path, retry_seconds=0)
        try:
            self.assertEqual(stat.S_IMODE(os.lstat(path).st_mode), 0o600)
        finally:
            server.server_close()
        self.assertFalse(path.exists())

    def test_a_file_that_is_not_a_socket_is_left_alone(self):
        path = Path(self.tmp.name) / "notes.txt"
        path.write_text("his", encoding="utf-8")
        with self.assertRaises(OSError):
            serve_app.make_unix_server(self.app, path, retry_seconds=0)
        self.assertEqual(path.read_text(encoding="utf-8"), "his")

    def test_the_parent_directory_is_made_private(self):
        path = Path(self.tmp.name) / "new" / "dir" / "s.sock"
        server = serve_app.make_unix_server(self.app, path, retry_seconds=0)
        try:
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)
        finally:
            server.server_close()

    def test_run_listens_on_both_and_removes_the_socket_when_it_stops(self):
        path = Path(self.tmp.name) / "run" / "s.sock"
        servers, lines = [], []
        self.app.log = lines.append
        t = threading.Thread(target=serve_app.run, args=(self.app, "127.0.0.1", 0),
                             kwargs={"socket_path": path, "retry_seconds": 0, "ready": servers.append}, daemon=True)
        t.start()
        deadline = time.monotonic() + 5
        while not servers and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(servers, lines)
        self.assertTrue(stat.S_ISSOCK(os.lstat(path).st_mode))
        self.assertTrue(any(f"unix:{path}" in line for line in lines), lines)
        servers[0].shutdown()
        t.join(5)
        self.assertFalse(t.is_alive())
        self.assertFalse(path.exists())

    def test_a_directory_others_can_write_is_refused(self):
        """Another local user who can write the directory could swap the socket for a listener of their own."""
        for mode in (0o777, 0o1777, 0o770, 0o720):
            with self.subTest(mode=oct(mode)):
                d = Path(self.tmp.name) / f"open-{mode:o}"
                d.mkdir()
                d.chmod(mode)
                with self.assertRaises(OSError) as cm:
                    serve_app.make_unix_server(self.app, d / "s.sock", retry_seconds=0)
                self.assertIn("writable by group or others", str(cm.exception))
                self.assertFalse((d / "s.sock").exists())

    def test_a_directory_of_another_user_is_refused(self):
        d = Path(self.tmp.name) / "theirs"
        d.mkdir(mode=0o700)
        with mock.patch("os.getuid", return_value=os.getuid() + 1), self.assertRaises(OSError) as cm:
            serve_app.make_unix_server(self.app, d / "s.sock", retry_seconds=0)
        self.assertIn(f"belongs to uid {os.getuid()}", str(cm.exception))
        self.assertFalse((d / "s.sock").exists())

    def test_a_symlinked_directory_is_refused(self):
        real = Path(self.tmp.name) / "real"
        real.mkdir(mode=0o700)
        link = Path(self.tmp.name) / "link"
        link.symlink_to(real)
        with self.assertRaises(OSError) as cm:
            serve_app.make_unix_server(self.app, link / "s.sock", retry_seconds=0)
        self.assertIn("not a directory", str(cm.exception))
        self.assertFalse((real / "s.sock").exists())

    def test_a_socket_of_another_user_is_neither_removed_nor_reused(self):
        path = Path(self.tmp.name) / "s.sock"
        old = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        old.bind(str(path))
        old.close()   # stale: ours would be replaced; another user's is left alone
        real_lstat = os.lstat
        theirs = lambda p, *a, **k: (os.stat_result((*real_lstat(p)[:4], os.getuid() + 1, *real_lstat(p)[5:]))   # noqa: E731
                                     if str(p) == str(path) else real_lstat(p, *a, **k))
        with mock.patch.object(serve_app.os, "lstat", side_effect=theirs), self.assertRaises(OSError) as cm:
            serve_app.make_unix_server(self.app, path, retry_seconds=0)
        self.assertEqual(cm.exception.errno, errno.EPERM)
        self.assertIn("not this user's", str(cm.exception))
        self.assertTrue(stat.S_ISSOCK(os.lstat(path).st_mode))   # still there, untouched

    def test_run_refuses_a_shared_directory_at_once_and_says_why(self):
        d = Path(self.tmp.name) / "shared"
        d.mkdir()
        d.chmod(0o1777)
        lines = []
        self.app.log = lines.append
        started = time.monotonic()
        self.assertEqual(serve_app.run(self.app, "127.0.0.1", 0, socket_path=d / "s.sock", retry_seconds=30), 1)
        self.assertLess(time.monotonic() - started, 5)   # not retried like a busy socket
        self.assertTrue(any("cannot listen on unix:" in l and "writable by group or others" in l for l in lines), lines)

    def test_a_dotdot_in_the_path_makes_no_stray_directory(self):
        base = Path(self.tmp.name)
        self.cfg.serve["socket"] = str(base / "stray" / ".." / "sock" / "s.sock")
        sock = jconfig.serve_socket(self.cfg)
        self.assertEqual(sock, base / "sock" / "s.sock")
        server = serve_app.make_unix_server(self.app, sock, retry_seconds=0)
        try:
            self.assertTrue(stat.S_ISSOCK(os.lstat(base / "sock" / "s.sock").st_mode))
        finally:
            server.server_close()
        self.assertFalse((base / "stray").exists())

    def test_run_exits_1_when_the_socket_cannot_be_made(self):
        path = Path(self.tmp.name) / "taken"
        path.write_text("x", encoding="utf-8")
        lines = []
        self.app.log = lines.append
        self.assertEqual(serve_app.run(self.app, "127.0.0.1", 0, socket_path=path, retry_seconds=0), 1)
        self.assertTrue(any("cannot listen on unix:" in line for line in lines), lines)


class SettingsTest(CleanEnv):
    def cfg(self, **serve):
        tmp = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        cfg = make_cfg(Path(tmp), names=NEUTRAL)
        cfg.serve.update(serve)
        return cfg

    def test_defaults(self):
        cfg = self.cfg()
        self.assertIsNone(cfg.serve["socket"])
        self.assertEqual(cfg.serve["tailscale_users"], [])
        self.assertIsNone(jconfig.serve_socket(cfg))
        self.assertEqual(jconfig.serve_tailscale_users(cfg), frozenset())
        text = "\n".join(jconfig.serve_problems(cfg))
        self.assertNotIn("serve.socket", text)
        self.assertNotIn("serve.tailscale_users", text)

    def test_the_socket_must_be_an_absolute_short_path(self):
        for bad in ("relative.sock", "~/x.sock", 5, True, ["/a"], "/" + "a" * 120, "/tmp/a\0b"):
            cfg = self.cfg(socket=bad)
            self.assertIsNone(jconfig.serve_socket(cfg), bad)
            self.assertIn("serve.socket", "\n".join(jconfig.serve_problems(cfg)), bad)
        cfg = self.cfg(socket="/run/user/1000/journal-serve/serve.sock")
        self.assertEqual(jconfig.serve_socket(cfg), Path("/run/user/1000/journal-serve/serve.sock"))
        cfg = self.cfg(socket="/run/user/1000/journal/../journal-serve//serve.sock")
        self.assertEqual(jconfig.serve_socket(cfg), Path("/run/user/1000/journal-serve/serve.sock"))
        self.assertNotIn("serve.socket", "\n".join(jconfig.serve_problems(cfg)))

    def test_users_must_be_a_list_of_logins(self):
        cfg = self.cfg(tailscale_users=[SAM, " pat@example.org "])
        self.assertEqual(jconfig.serve_tailscale_users(cfg), frozenset({SAM, "pat@example.org"}))
        for bad in (SAM, {"a": 1}, 5):
            cfg = self.cfg(tailscale_users=bad)
            self.assertEqual(jconfig.serve_tailscale_users(cfg), frozenset(), bad)
            self.assertIn("serve.tailscale_users", "\n".join(jconfig.serve_problems(cfg)), bad)
        cfg = self.cfg(tailscale_users=[SAM, "", 5, "two words@x", "a,b@x"])
        self.assertEqual(jconfig.serve_tailscale_users(cfg), frozenset({SAM}))
        self.assertIn("serve.tailscale_users", "\n".join(jconfig.serve_problems(cfg)))

    def test_doctor_rows(self):
        cfg = self.cfg()
        names = [n for n, _s, _d in status.serve_rows(cfg, token=False)]
        self.assertNotIn("serve socket", names)
        self.assertNotIn("serve identity", names)
        cfg = self.cfg(tailscale_users=[SAM])
        rows = {n: (s, d) for n, s, d in status.serve_rows(cfg, token=False)}
        self.assertEqual(rows["serve identity"][0], "warn")
        self.assertIn("serve.socket", rows["serve identity"][1])
        sock = cfg.state_dir / "serve.sock"
        cfg.serve["socket"] = str(sock)
        rows = {n: (s, d) for n, s, d in status.serve_rows(cfg, token=False)}
        self.assertEqual(rows["serve socket"][0], "ok", rows)
        self.assertEqual(rows["serve identity"], ("ok", f"Tailscale logins signed in on the socket: {SAM}"))
        cfg.serve["tailscale_users"] = []
        rows = {n: (s, d) for n, s, d in status.serve_rows(cfg, token=False)}
        self.assertEqual(rows["serve identity"][0], "ok")
        self.assertIn("token or a paired cookie", rows["serve identity"][1])
        sock.parent.mkdir(parents=True, exist_ok=True)
        sock.write_text("x", encoding="utf-8")
        rows = {n: (s, d) for n, s, d in status.serve_rows(cfg, token=False)}
        self.assertEqual(rows["serve socket"][0], "fail", rows)
        cfg.serve["socket"] = "relative"
        rows = {n: (s, d) for n, s, d in status.serve_rows(cfg, token=False)}
        self.assertEqual(rows["serve socket"][0], "warn")


class UnitTest(CleanEnv):
    def cfg(self, **serve):
        cfg = jconfig.from_dict({"root": "/srv/journal/Journal", "timezone": "UTC", "serve": serve},
                                hermes_home=Path("/home/you/.hermes/profiles/companion"),
                                config_file=Path("/home/you/.config/journal/companion.json"))
        return cfg

    def render(self, cfg):
        return unit.render(cfg, exe="/home/you/.local/bin/journal", mount_of=lambda p: Path("/"),
                           runtime_dir=Path("/run/user/1000"))

    def test_no_socket_no_change(self):
        text = self.render(self.cfg())
        self.assertNotIn("RuntimeDirectory", text)

    def test_a_socket_in_the_runtime_directory_gets_a_runtime_directory(self):
        text = self.render(self.cfg(socket="/run/user/1000/journal-serve/serve.sock"))
        self.assertIn("RuntimeDirectory=journal-serve\n", text)
        self.assertIn("RuntimeDirectoryMode=0700\n", text)

    def test_a_nested_runtime_directory_is_made_whole(self):
        text = self.render(self.cfg(socket="/run/user/1000/journal/serve/serve.sock"))
        self.assertIn("RuntimeDirectory=journal/serve\n", text)

    def test_a_socket_anywhere_else_needs_nothing_in_the_unit(self):
        """The server makes a missing directory 0700 itself; with no private /tmp, a socket there is the path
        tailscaled sees (DECISIONS.md, SERVE-UNSANDBOXED)."""
        plain = self.render(self.cfg())
        for sock in ("/run/user/1000/serve.sock", "/home/you/.hermes/profiles/companion/plugin-data/journal/serve.sock",
                     "/srv/sockets/journal.sock", "/tmp/journal-serve/serve.sock", "/var/tmp/serve.sock"):
            with self.subTest(sock=sock):
                self.assertEqual(self.render(self.cfg(socket=sock)), plain)

    def test_a_dotdot_in_the_socket_path_is_normalised(self):
        text = self.render(self.cfg(socket="/run/user/1000/journal/../serve/serve.sock"))
        self.assertIn("RuntimeDirectory=serve\n", text)
        self.assertNotIn("..", text.split("RuntimeDirectory=", 1)[1].splitlines()[0])

    def test_a_socket_on_its_own_mount_is_required_to_be_there(self):
        mounts = {"/data": Path("/data"), "/run/user/1000": Path("/run/user/1000")}
        mount_of = lambda p: next((m for k, m in mounts.items() if str(p).startswith(k)), Path("/"))   # noqa: E731
        render = lambda sock: unit.render(self.cfg(socket=sock), exe="/home/you/.local/bin/journal",   # noqa: E731
                                          mount_of=mount_of, runtime_dir=Path("/run/user/1000"))
        # unnormalised, /run/user/1000 would be among its parents: RuntimeDirectory=../../../data and no gate
        text = render("/run/user/1000/../../../data/s.sock")
        self.assertIn("RequiresMountsFor=/data\n", text)
        self.assertNotIn("RuntimeDirectory", text)
        self.assertIn("ExecStartPre=/usr/bin/mountpoint -q /data\n", text)
        for sock in ("/run/user/1000/journal-serve/serve.sock", "/run/user/1000/serve.sock"):   # the manager's own
            with self.subTest(sock=sock):
                self.assertNotIn("RequiresMountsFor", render(sock))

    def test_print_unit_with_a_socket_under_tmp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        cfg = build_journal(Path(tmp.name))
        cfg.serve["socket"] = "/tmp/journal-serve/serve.sock"
        args = cli.build_parser().parse_args(["serve", "--print-unit"])
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run(args, cfg_loader=lambda: cfg)
        self.assertEqual((code, err.getvalue()), (0, ""))
        self.assertIn("ExecStart=", out.getvalue())
        self.assertNotIn("PrivateTmp", out.getvalue())


class CliTest(CleanEnv):
    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.cfg = build_journal(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()
        super().tearDown()

    def run_cli(self, *argv):
        args = cli.build_parser().parse_args(list(argv))
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.run(args, cfg_loader=lambda: self.cfg)
        return code, out.getvalue(), err.getvalue()

    def test_an_unusable_socket_stops_the_server_at_start(self):
        self.cfg.serve["socket"] = "relative.sock"
        code, _out, err = self.run_cli("serve", "--port", "1")
        self.assertEqual(code, 2)   # a configuration error, as an unusable serve.port is
        self.assertIn("serve.socket", err)

    def test_check_names_the_socket(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.cfg.serve.update({"port": port, "socket": str(Path(self.tmp.name) / "s.sock"),
                               "tailscale_users": [SAM]})
        code, out, _ = self.run_cli("serve", "check")
        self.assertEqual(code, 0, out)
        self.assertIn("serve socket:", out)
        self.assertIn(f"serve identity: Tailscale logins signed in on the socket: {SAM}", out)
