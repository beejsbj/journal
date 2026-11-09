"""``journal serve`` (WP9): auth, Host/Origin, pairing, media confinement, the two-voice document, search over his
words only, /today = today.collect, headers, static files, SSE, bind retry, the unit, the CLI and doctor rows.
Every test talks HTTP to a real server on an ephemeral loopback port over a temporary journal."""
import http.client
import io
import json
import os
import re
import socket
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from helpers import ROOT, CleanEnv, make_cfg, write_config
from serve_helpers import EVENING_NOW, HERS, IST, NEUTRAL, NOW, SESSION, ServeCase, add_day_of_prompts, build_journal

from journal import cli, entries, ledger, status, today, version
from journal import config as jconfig
from journal.serve import api, auth, document, sse, unit
from journal.serve import app as serve_app


class AuthTest(ServeCase):
    def test_no_token_is_401_everywhere_under_api_even_for_unknown_paths(self):
        for path in ("/api/v1/today", "/api/v1/entries", "/api/v1/entries/2026-10-14-evening", "/api/v1/nope"):
            code, headers, body = self.request("GET", path, token=False)
            self.assertEqual(code, 401, path)
            self.assertEqual(json.loads(body)["error"], "token")

    def test_a_wrong_token_is_401_and_the_right_one_200(self):
        code, _h, _b = self.request("GET", "/api/v1/today", token=False, headers={"Authorization": "Bearer nope"})
        self.assertEqual(code, 401)
        self.assertEqual(self.request("GET", "/api/v1/today")[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/nope")[0], 404)  # routing only after auth

    def test_health_is_public_and_says_which_code(self):
        data = self.get_json("/healthz", token=False)
        self.assertEqual((data["ok"], data["version"], data["code"]), (True, version.stamp()["version"],
                                                                      version.code_id()))
        self.assertIn("tick_last_at", data)

    def test_the_token_file_is_private(self):
        mode = auth.token_file(self.cfg).stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)
        self.assertEqual(auth.auth_file(self.cfg).stat().st_mode & 0o777, 0o600)


class OriginTest(ServeCase):
    def test_a_write_needs_this_sites_origin(self):
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/v1/logout")[0], 403)   # no Origin at all
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={"Origin": self.origin})[0], 204)
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={"Sec-Fetch-Site": "same-origin"})[0], 204)

    def test_a_write_to_a_page_or_a_get_route_is_refused(self):
        self.assertEqual(self.request("POST", "/", headers={"Origin": self.origin})[0], 405)
        self.assertEqual(self.request("POST", "/api/v1/today", headers={"Origin": self.origin})[0], 405)


class TrustedProxyTest(ServeCase):
    serve = {"trusted_proxies": ["127.0.0.0/8"]}

    def test_forwarded_proto_is_believed_from_a_trusted_proxy(self):
        https_origin = f"https://127.0.0.1:{self.port}"
        code = self.request("POST", "/api/v1/logout", headers={"Origin": https_origin,
                                                               "X-Forwarded-Proto": "https"})[0]
        self.assertEqual(code, 204)


class UntrustedProxyTest(ServeCase):
    def test_forwarded_proto_is_ignored_from_anyone_else(self):
        https_origin = f"https://127.0.0.1:{self.port}"
        code = self.request("POST", "/api/v1/logout", headers={"Origin": https_origin, "X-Forwarded-Proto": "https"})[0]
        self.assertEqual(code, 403)


class HostTest(ServeCase):
    serve = {"host": "journal.example.org"}

    def test_a_wrong_host_is_403_and_health_is_exempt(self):
        code, _h, body = self.request("GET", "/api/v1/today")
        self.assertEqual(code, 403)
        self.assertEqual(json.loads(body)["error"], "host")
        self.assertEqual(self.request("GET", "/", token=False)[0], 403)
        self.assertEqual(self.request("GET", "/api/v1/today", headers={"Host": "journal.example.org"})[0], 200)
        self.assertEqual(self.request("GET", "/api/v1/today", headers={"Host": "JOURNAL.example.org:443"})[0], 200)
        self.assertEqual(self.request("GET", "/healthz", token=False)[0], 200)   # the proxy probes the bind address

    def test_the_origin_is_the_configured_https_site(self):
        h = {"Host": "journal.example.org"}
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={**h, "Origin": "https://journal.example.org"})[0],
                         204)
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={**h, "Origin": "http://journal.example.org"})[0],
                         403)

    def test_the_pairing_link_names_the_host(self):
        self.assertTrue(auth.pair_url(self.cfg, "abc").startswith("https://journal.example.org/pair#abc"))


class PublicPortTest(ServeCase):
    """``serve.public_port``: the app published on an HTTPS port other than 443 (tailscale serve on :8790)."""
    serve = {"host": "journal.example.org", "public_port": 8790}
    h = {"Host": "journal.example.org:8790"}

    def post(self, origin):
        return self.request("POST", "/api/v1/logout", headers={**self.h, "Origin": origin})[0]

    def test_the_site_and_the_pairing_link_carry_the_port(self):
        self.assertEqual(auth.site(self.cfg), "https://journal.example.org:8790")
        self.assertEqual(auth.pair_url(self.cfg, "abc"), "https://journal.example.org:8790/pair#abc")

    def test_the_origin_is_exactly_the_site(self):
        self.assertEqual(self.post("https://journal.example.org:8790"), 204)
        self.assertEqual(self.post("HTTPS://Journal.Example.org:8790/"), 204)

    def test_nothing_but_the_site_is_an_origin(self):
        for origin in ("https://journal.example.org",             # no port, though public_port is set
                       "https://journal.example.org:8791",        # another port
                       "https://journal.example.org:443",
                       "http://journal.example.org:8790",         # not https
                       "http://journal.example.org",
                       "https://evil.example:8790",
                       "https://journal.example.org:8790:8790"):
            self.assertEqual(self.post(origin), 403, origin)

    def test_the_host_headers_port_grants_nothing(self):
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={
            "Host": "journal.example.org:9999", "Origin": "https://journal.example.org:9999"})[0], 403)


class NoPublicPortTest(ServeCase):
    serve = {"host": "journal.example.org"}

    def test_without_it_the_site_has_no_port(self):
        self.assertEqual(auth.site(self.cfg), "https://journal.example.org")
        self.assertEqual(auth.pair_url(self.cfg, "abc"), "https://journal.example.org/pair#abc")

    def test_the_origin_is_the_bare_site_and_a_port_is_refused(self):
        h = {"Host": "journal.example.org:8790"}
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={**h, "Origin": "https://journal.example.org"})[0],
                         204)
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={
            **h, "Origin": "https://journal.example.org:8790"})[0], 403)


class Port443Test(ServeCase):
    serve = {"host": "journal.example.org", "public_port": 443}

    def test_443_is_the_default_and_adds_nothing(self):
        self.assertEqual(auth.site(self.cfg), "https://journal.example.org")
        self.assertEqual(auth.pair_url(self.cfg, "abc"), "https://journal.example.org/pair#abc")
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={
            "Host": "journal.example.org", "Origin": "https://journal.example.org"})[0], 204)
        self.assertEqual(self.request("POST", "/api/v1/logout", headers={
            "Host": "journal.example.org", "Origin": "https://journal.example.org:443"})[0], 403)


class BadPublicPortTest(ServeCase):
    serve = {"host": "journal.example.org", "public_port": "8790"}   # a string: not a port number

    def test_a_bad_value_is_reported_and_treated_as_absent(self):
        self.assertIsNone(jconfig.serve_public_port(self.cfg))
        self.assertIn("serve.public_port", "\n".join(jconfig.serve_problems(self.cfg)))
        self.assertEqual(auth.site(self.cfg), "https://journal.example.org")
        for bad in (0, 65536, -1, True, 80.5, "x", []):
            self.cfg.serve["public_port"] = bad
            self.assertIsNone(jconfig.serve_public_port(self.cfg), bad)
            self.assertIn("serve.public_port", "\n".join(jconfig.serve_problems(self.cfg)), bad)
            self.assertEqual(auth.site(self.cfg), "https://journal.example.org", bad)
        for good in (None, 1, 443, 8790, 65535):
            self.cfg.serve["public_port"] = good
            self.assertNotIn("serve.public_port", "\n".join(jconfig.serve_problems(self.cfg)), good)


class PairingTest(ServeCase):
    def pair(self, code):
        return self.request("POST", "/api/v1/pair", {"token": code}, token=False, headers={"Origin": self.origin})

    def test_a_code_pairs_once_and_the_cookie_reads(self):
        code = auth.new_pairing(self.cfg)
        status_, headers, _b = self.pair(code)
        self.assertEqual(status_, 204)
        cookie = headers["set-cookie"]
        for part in ("HttpOnly", "SameSite=Lax", "Secure", "Path=/"):
            self.assertIn(part, cookie)
        self.assertNotIn("Domain", cookie)          # host-only
        self.assertNotIn(self.token, cookie)        # the cookie is not the bearer token
        value = cookie.split(";")[0]
        self.assertEqual(self.request("GET", "/api/v1/today", token=False, headers={"Cookie": value})[0], 200)
        self.assertEqual(self.pair(code)[0], 401)   # single use
        stored = (self.cfg.state_dir / auth.PAIRINGS).read_text(encoding="utf-8")
        self.assertNotIn(code, stored)              # only its hash is kept

    def test_an_expired_or_unknown_code_is_refused(self):
        old = auth.new_pairing(self.cfg, now=time.time() - auth.PAIR_TTL - 1)
        self.assertEqual(self.pair(old)[0], 401)
        self.assertEqual(self.pair("made-up")[0], 401)
        self.assertEqual(self.request("POST", "/api/v1/pair", b"not json", token=False,
                                      headers={"Origin": self.origin})[0], 400)

    def test_pairing_is_a_write_and_needs_the_origin(self):
        code = auth.new_pairing(self.cfg)
        self.assertEqual(self.request("POST", "/api/v1/pair", {"token": code}, token=False)[0], 403)

    def test_rotation_signs_every_browser_out(self):
        _s, headers, _b = self.pair(auth.new_pairing(self.cfg))
        value = headers["set-cookie"].split(";")[0]
        auth.rotate(self.cfg)
        self.assertEqual(self.request("GET", "/api/v1/today", token=False, headers={"Cookie": value})[0], 401)
        self.assertEqual(self.request("GET", "/api/v1/today")[0], 401)  # file-backed: a new token too
        self.token = auth.resolve_token(self.cfg)[0]
        self.assertEqual(self.request("GET", "/api/v1/today")[0], 200)


class CookieTest(ServeCase):
    def paired(self):
        _s, headers, _b = self.request("POST", "/api/v1/pair", {"token": auth.new_pairing(self.cfg)}, token=False,
                                       headers={"Origin": self.origin})
        return headers["set-cookie"]

    def test_the_cookie_is_a_host_cookie(self):
        cookie = self.paired()
        self.assertTrue(cookie.startswith("__Host-journal_session="), cookie)
        for part in ("Secure", "Path=/", "HttpOnly"):
            self.assertIn(part, cookie)
        self.assertNotIn("Domain", cookie)

    def test_another_apps_cookie_neither_hides_nor_replaces_the_session(self):
        value = self.paired().split(";")[0]
        mine = value.split("=", 1)[1]
        for header in (f'prefs={{"a":1}}; {value}', f"foo=bar baz; {value}", f'x="unterminated; {value}',
                       f"journal_session=evil; {value}", f"{value}; __Host-journal_session=evil"):
            with self.subTest(header=header):
                self.assertEqual(auth.cookie_from(header), mine)
                self.assertEqual(self.request("GET", "/api/v1/today", token=False, headers={"Cookie": header})[0], 200)
        for header in ("journal_session=" + mine, "", "__Host-journal_session", "other=1"):
            with self.subTest(header=header):
                self.assertIsNone(auth.cookie_from(header))  # the plain name is not the session over https

    def test_dev_over_plain_http_uses_the_plain_name(self):
        self.assertEqual(auth.cookie_header("v", secure=False).split(";")[0], "journal_session=v")
        self.assertEqual(auth.cookie_from("journal_session=v", secure=False), "v")
        self.assertNotIn("Secure", auth.cookie_header("v", secure=False))

    def test_rotation_voids_an_outstanding_pairing_code(self):
        code = auth.new_pairing(self.cfg)
        auth.rotate(self.cfg)
        self.assertFalse(auth.redeem(self.cfg, code))
        self.assertTrue(auth.redeem(self.cfg, auth.new_pairing(self.cfg)))  # a code made after it works


class ConnectionTest(ServeCase):
    def raw(self, data: bytes, wait: float = 0.6) -> bytes:
        s = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        try:
            s.sendall(data)
            time.sleep(wait)
            s.settimeout(0.5)
            buf = b""
            try:
                while chunk := s.recv(65536):
                    buf += chunk
            except socket.timeout:
                pass
            return buf
        finally:
            s.close()

    def test_an_unread_request_body_is_not_parsed_as_the_next_request(self):
        host = f"Host: 127.0.0.1:{self.port}\r\n"
        inner = f"GET /api/v1/healthz HTTP/1.1\r\n{host}\r\n".encode()
        for verb in ("GET", "POST"):
            with self.subTest(verb=verb):
                out = self.raw(f"{verb} /api/v1/today HTTP/1.1\r\n{host}Origin: {self.origin}\r\n"
                               f"Content-Length: {len(inner)}\r\n\r\n".encode() + inner)
                self.assertEqual(out.count(b"HTTP/1.1 "), 1, out)
                self.assertIn(b"401", out.split(b"\r\n")[0])
                self.assertIn(b"Connection: close", out)

    def test_a_chunked_body_closes_the_connection(self):
        """A successful write (logout answers 204 without reading its body) with a chunked body left behind."""
        host = f"Host: 127.0.0.1:{self.port}\r\n"
        chunked = b"5\r\nhello\r\n0\r\n\r\n"
        out = self.raw(f"POST /api/v1/logout HTTP/1.1\r\n{host}Origin: {self.origin}\r\n"
                       f"Authorization: Bearer {self.token}\r\nTransfer-Encoding: chunked\r\n\r\n".encode()
                       + chunked + b"GET /healthz HTTP/1.1\r\n" + host.encode() + b"\r\n")
        self.assertEqual(out.count(b"HTTP/1.1 "), 1, out)
        self.assertIn(b"204", out.split(b"\r\n")[0])
        self.assertIn(b"Connection: close", out)

    def test_a_request_whose_body_was_read_keeps_the_connection(self):
        host = f"Host: 127.0.0.1:{self.port}\r\n"
        body = json.dumps({"token": auth.new_pairing(self.cfg)}).encode()
        req = (f"POST /api/v1/pair HTTP/1.1\r\n{host}Origin: {self.origin}\r\nContent-Type: application/json\r\n"
               f"Content-Length: {len(body)}\r\n\r\n").encode() + body
        out = self.raw(req + f"GET /healthz HTTP/1.1\r\n{host}\r\n".encode())
        self.assertEqual(out.count(b"HTTP/1.1 "), 2, out)

    def test_head_on_the_event_stream_answers_the_headers_and_frees_the_slot(self):
        slots = self.app._streams._value
        out = self.raw(f"HEAD /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                       f"Authorization: Bearer {self.token}\r\n\r\n".encode())
        self.assertIn(b"text/event-stream", out)
        self.assertTrue(out.endswith(b"\r\n\r\n"), out)  # no event followed the headers
        self.assertEqual(self.app._streams._value, slots)

    def test_the_code_fingerprint_is_pinned_at_start(self):
        version.code_id.cache_clear()
        try:
            ok, _why = serve_app.App(lambda: self.cfg, log=lambda line: None).prime()
            self.assertTrue(ok)
            self.assertEqual(version.code_id.cache_info().currsize, 1)  # computed by prime, not by the first /healthz
        finally:
            version.code_id.cache_clear()



class TodayPromptsTest(ServeCase):
    """Today shows every prompt sent today in full, whatever its state: the opener and the question set, as the entry's
    document shows them. Only Telegram folds a finished one."""

    def setUp(self):
        super().setUp()
        add_day_of_prompts(self.cfg)
        self.app.clock = lambda: EVENING_NOW

    def rows(self):
        return {r["id"]: r for r in self.get_json("/api/v1/today")["practices"]["rows"]}

    def test_each_sent_prompt_carries_its_opener_and_questions_whatever_its_state(self):
        rows = self.rows()
        self.assertEqual({k: r["state"] for k, r in rows.items()},
                         {"morning": "missed", "gratitude": "answered", "weekly": "open", "evening": "upcoming"})
        morning = rows["morning"]["prompt"]
        self.assertTrue(morning["opener"].startswith("Morning, Sam."))
        self.assertEqual(morning["sent_at_local"], "06:00 IST")
        self.assertEqual(morning["asked"], [{"id": "1", "question": "How did you sleep?"}])
        self.assertEqual(len(morning["questions"]), 11)
        self.assertEqual(morning["questions"][0]["text"], "How did you sleep?")
        self.assertEqual(rows["gratitude"]["prompt"]["questions"][1]["text"], "Who made one of them possible?")
        self.assertIsNone(rows["weekly"]["prompt"]["opener"])           # sent with the plain opener: none recorded
        self.assertGreater(len(rows["weekly"]["prompt"]["questions"]), 0)
        self.assertNotIn("prompt", rows["evening"])                     # not sent yet: nothing to show

    def test_the_prompt_is_the_one_the_entry_shows(self):
        doc = self.get_json("/api/v1/entries/2026-10-15-gratitude")
        self.assertEqual(self.rows()["gratitude"]["prompt"], doc["prompt"])

    def test_an_opener_with_an_off_the_record_line_is_not_shown(self):
        with ledger.locked(self.cfg.ledger_path) as data:
            for p in data["prompts"]:
                if p["id"] == "2026-10-15-morning":
                    p["opener"] = "Morning.\n/x something kept out"
        rows = self.rows()
        self.assertIsNone(rows["morning"]["prompt"]["opener"])
        self.assertNotIn("kept out", json.dumps(rows))
        self.assertEqual(len(rows["morning"]["prompt"]["questions"]), 11)   # the questions are the practice's: shown

    def test_one_bad_record_costs_its_own_prompt_not_the_route(self):
        real = document.prompt_of

        def flaky(cfg, lib, pid, rec, *a, **k):
            if pid == "morning":
                raise KeyError("a record the engine would not have written")
            return real(cfg, lib, pid, rec, *a, **k)
        with mock.patch.object(document, "prompt_of", side_effect=flaky), mock.patch("journal.serve.api.log"):
            rows = self.rows()
        self.assertNotIn("prompt", rows["morning"])
        self.assertEqual(rows["morning"]["state"], "missed")
        self.assertIn("prompt", rows["gratitude"])
        self.assertIn("prompt", rows["weekly"])
        with mock.patch.object(document, "prompt_records", side_effect=OSError("ledger")), \
                mock.patch("journal.serve.api.log"):
            rows = self.rows()
        self.assertEqual(len(rows), 4)
        self.assertFalse(any("prompt" in r for r in rows.values()))

    def test_the_place_carries_its_credit_while_openstreetmap_named_it(self):
        self.cfg.location = "Kochi, Kerala, India"
        self.cfg.raw["coordinates"] = {"lat": 9.97, "lon": 76.25, "place": "Kochi, Kerala, India", "source": "openstreetmap"}
        self.assertEqual(self.get_json("/api/v1/today")["location_source"], "openstreetmap")
        self.cfg.location = "Typed by hand"   # the place changed without the lookup: no credit
        self.assertIsNone(self.get_json("/api/v1/today")["location_source"])

    def test_the_block_dict_is_unchanged(self):
        # the companion's thread-start block reads today.collect, which carries no prompt text
        self.assertNotIn("prompt", json.dumps(today.collect(self.cfg, EVENING_NOW, stems=True)["practices"]))

class MediaTest(ServeCase):
    def test_a_referenced_audio_file_is_served_with_ranges(self):
        code, headers, body = self.request("GET", "/api/v1/media/2026-10-14-evening.ogg")
        self.assertEqual(code, 200)
        self.assertEqual(headers["content-type"], "audio/ogg")
        self.assertEqual(headers["accept-ranges"], "bytes")
        self.assertEqual(body, (self.cfg.root / "2026-10-14-evening.ogg").read_bytes())
        code, headers, body = self.request("GET", "/api/v1/media/2026-10-14-evening.ogg", headers={"Range": "bytes=0-3"})
        self.assertEqual((code, body, headers["content-range"]), (206, b"OggS", "bytes 0-3/64"))

    def test_confinement(self):
        for name in ("2026-10-14-stray.ogg",                     # on disk, but no entry embeds it
                     "..%2Fstate%2Fpacks%2F2026-10-14-evening.ogg",  # traversal into packs/
                     "%2E%2E%2Fstate%2Fpacks%2F2026-10-14-evening.ogg",
                     "..", "2026-10-14-evening.md",              # not audio
                     ".2026-10-14-evening.ogg", "missing.ogg"):
            self.assertEqual(self.request("GET", f"/api/v1/media/{name}")[0], 404, name)


class DocumentTest(ServeCase):
    def test_the_evening_document(self):
        doc = self.get_json("/api/v1/entries/2026-10-14-evening")
        self.assertEqual((doc["stem"], doc["date"], doc["kind"]), ("2026-10-14-evening", "2026-10-14", "evening"))
        self.assertEqual(doc["practice"], {"id": "evening", "title": "Evening journal", "emoji": ""})
        self.assertEqual(doc["title"], "Evening journal — Wed 14 Oct 2026")
        self.assertEqual(doc["voices"], {"his": "Sam", "hers": "Ada"})   # the labels, never guessed by the client
        self.assertEqual(doc["prompt"]["sent_at_local"], "23:00 IST")
        self.assertEqual(doc["prompt"]["asked"], [{"id": "1.1", "question": "What's spinning in your mind?"}])
        self.assertEqual(doc["prompt"]["questions"][0], {"id": "1.1", "text": "What's spinning in your mind?",
                                                         "section": "Brain Dump & Letting Go"})
        blocks = doc["blocks"]
        self.assertEqual([(b["kind"], b["key"]) for b in blocks],
                         [("his", "words:0"), ("his", "words:1"), ("his", "his:813"), ("hers", "her:4815"),
                          ("his", "his:816")])
        self.assertEqual((blocks[0]["at"], blocks[0]["source"], blocks[0]["text"]),
                         ("23:12 IST", "text", "First words, about the lighthouse."))
        self.assertEqual((blocks[1]["source"], blocks[1]["audio"], blocks[1]["meta"], blocks[1]["text"]),
                         ("voice", "2026-10-14-evening.ogg", {"duration": 151}, "A spoken thought."))
        self.assertEqual((blocks[2]["at"], blocks[2]["text"]), ("23:44", "Later I thought of the harbour."))
        self.assertEqual((blocks[3]["at"], blocks[3]["text"]),
                         ("23:45", f"The harbour sounds like a {HERS} word.\nSecond line."))
        self.assertEqual((blocks[4]["text"], blocks[4]["reply_to"]), ("yes, and the gulls.", "The harbour sounds like"))
        for b in blocks:
            if b["kind"] == "his":
                self.assertNotIn(HERS, b["text"])
            self.assertNotIn("%% journal", b["text"])
        self.assertEqual(doc["analysis"], {"annotations": "- a theme.", "notes": [{"at": "08:02 IST",
                                                                                   "text": "A note of hers."}],
                                           "reflection": "2026-10-14-reflection"})
        self.assertEqual(doc["flags"], {"annotated": True, "has_conversation": True, "transcript_pending": False,
                                       "audio": True})
        self.assertIsNone(doc["summary"])

    def test_a_program_session_document(self):
        doc = self.get_json("/api/v1/entries/2026-10-15-fix-life-s01")
        self.assertEqual((doc["kind"], doc["program"], doc["session"], doc["run"]),
                         ("fix-life", "fix-life", 1, "2026-10-15-a1f3"))
        self.assertEqual([(b["phase"], b["text"]) for b in doc["blocks"]],
                         [(1, "The life I do not want."), (2, "The life I want.")])
        self.assertEqual(doc["summary"], "Two phases done.")
        self.assertFalse(doc["flags"]["annotated"])

    def test_an_interrupt_answer_carries_its_interrupt_and_no_phase(self):
        """Session 10's one-off questions are answered under ``%% journal interrupt:N %%``: the block says which
        interrupt it answers, so the client labels it instead of reading it as the tail of the phase before."""
        body = SESSION.replace("session: 1\n", "session: 10\n").split("%% journal phase:2 %%")[0] + (
            "%% journal interrupt:3 %%\n### 11:00 IST · text\n\nWhat am I avoiding? The tax form.\n")
        (self.cfg.root / "2026-10-17-fix-life-s10.md").write_text(body, encoding="utf-8")
        doc = self.get_json("/api/v1/entries/2026-10-17-fix-life-s10")
        first, second = doc["blocks"]
        self.assertEqual((first["phase"], first.get("interrupt")), (1, None))
        self.assertEqual((second["phase"], second["interrupt"], second["text"]), (None, 3, "What am I avoiding? The tax form."))

    def test_a_session_number_outside_the_program_finds_no_phases(self):
        """``session: 0`` (hand-edited) indexed from the end and gave session 10's questions to a session 1 entry;
        the prompt follows the number the document reports (the name's), and a number outside the program is none."""
        path = self.cfg.root / "2026-10-15-fix-life-s01.md"
        for front, expected in (("session: 0", 1), ("session: -1", 1)):
            path.write_text(SESSION.replace("session: 1", front), encoding="utf-8")
            doc = self.get_json("/api/v1/entries/2026-10-15-fix-life-s01")
            self.assertEqual(doc["session"], 1)
            ours = self.get_json("/api/v1/library/fix-life")["sessions"][0]["phases"]
            self.assertEqual([p["number"] for p in doc["prompt"]["phases"]], [p["number"] for p in ours])
        path.write_text(SESSION.replace("session: 1", "session: 0"), encoding="utf-8")
        (self.cfg.root / "2026-10-16-fix-life.md").write_text(SESSION.replace("session: 1", "session: 0"), encoding="utf-8")
        doc = self.get_json("/api/v1/entries/2026-10-16-fix-life")
        self.assertEqual(doc["prompt"]["phases"], [])

    def test_an_audio_embed_is_any_recording_the_media_route_serves(self):
        """``hooks.original_extension`` keeps the sender's suffix, so a kept recording can be ``.aif`` or ``.oga``:
        it is audio exactly when ``/media`` would serve it, not only for the extensions the engine itself writes."""
        path = self.cfg.root / "2026-10-14-evening.md"
        path.write_text(path.read_text(encoding="utf-8").replace("![[2026-10-14-evening.ogg]]",
                                                                   "![[2026-10-14-evening.aif]]"), encoding="utf-8")
        (self.cfg.root / "2026-10-14-evening.aif").write_bytes(b"FORM")
        doc = self.get_json("/api/v1/entries/2026-10-14-evening")
        voice = next(b for b in doc["blocks"] if b.get("source") == "voice")
        self.assertEqual((voice["audio"], voice["text"]), ("2026-10-14-evening.aif", "A spoken thought."))
        self.assertTrue(doc["flags"]["audio"])
        self.assertEqual(self.request("GET", "/api/v1/media/2026-10-14-evening.aif")[0], 200)

    def test_his_own_summary_line_is_his_not_the_engines(self):
        """A ``## Summary`` he typed in a session entry (the engine has written none yet) is a line of his words:
        the document keeps both of his phases and shows no summary, and search finds what follows his heading."""
        own = SESSION.split("## Summary")[0].replace("The life I do not want.",
                                                     "The life I do not want.\n\n## Summary\n\nMYOWNSUMMARY I typed this.")
        (self.cfg.root / "2026-10-16-fix-life-s02.md").write_text(
            own.replace("session: 1", "session: 2") + "## Ada's annotations\n\n_Pending._\n", encoding="utf-8")
        doc = self.get_json("/api/v1/entries/2026-10-16-fix-life-s02")
        self.assertIsNone(doc["summary"])
        self.assertEqual([b["phase"] for b in doc["blocks"]], [1, 2])
        self.assertIn("MYOWNSUMMARY", doc["blocks"][0]["text"])
        self.assertEqual(self.get_json("/api/v1/entries?q=myownsummary")["items"][0]["stem"], "2026-10-16-fix-life-s02")
        self.assertEqual(self.get_json("/api/v1/entries?q=the%20life%20i%20want")["items"][0]["stem"],
                         "2026-10-16-fix-life-s02")
        # and the engine's marked summary, written after his words, is still the summary and not his
        sess = self.cfg.root / "2026-10-16-fix-life-s02.md"
        entries.write_summary(sess, "Two phases done.")
        doc = self.get_json("/api/v1/entries/2026-10-16-fix-life-s02")
        self.assertEqual(doc["summary"], "Two phases done.")
        self.assertEqual([b["phase"] for b in doc["blocks"]], [1, 2])
        self.assertEqual(self.get_json("/api/v1/entries?q=two%20phases%20done")["items"], [])

    def test_only_engine_entries_are_served(self):
        for stem in ("2026-10-14", "2026-10-14-thoughts", "2026-10-14-reflection", "..%2F2026-10-14-evening",
                     "2026-10-14-EVENING", "nope"):
            self.assertEqual(self.request("GET", f"/api/v1/entries/{stem}")[0], 404, stem)


class EntriesTest(ServeCase):
    def stems(self, query=""):
        return [i["stem"] for i in self.get_json(f"/api/v1/entries{query}")["items"]]

    def test_the_list_holds_engine_entries_newest_first(self):
        self.assertEqual(self.stems(), ["2026-10-15-j", "2026-10-15-fix-life-s01", "2026-10-14-evening"])
        item = self.get_json("/api/v1/entries?kind=evening")["items"][0]
        self.assertEqual({k: item[k] for k in ("stem", "date", "kind", "practice", "time", "first_line")},
                         {"stem": "2026-10-14-evening", "date": "2026-10-14", "kind": "evening", "practice": "evening",
                          "time": "23:12", "first_line": "First words, about the lighthouse."})
        self.assertEqual(item["flags"]["has_conversation"], True)
        self.assertEqual(self.stems("?program=fix-life"), ["2026-10-15-fix-life-s01"])
        self.assertEqual(self.stems("?from=2026-10-15&to=2026-10-15"), ["2026-10-15-j", "2026-10-15-fix-life-s01"])

    def test_q_searches_his_words_only(self):
        self.assertEqual(self.stems(f"?q={HERS}"), [])
        self.assertEqual(self.stems(f"?q={HERS.lower()}"), [])
        self.assertEqual(self.stems("?q=GULLS"), ["2026-10-14-evening"])       # his conversation line
        self.assertEqual(self.stems("?q=harbour"), ["2026-10-15-j", "2026-10-14-evening"])  # not his own page

    def test_engine_markup_is_not_his_words(self):
        """Block headings (``### 23:12 IST · text``), clock markers and audio embeds are the engine's: searching
        for them finds nothing, and the week's words are the ones he wrote (18 in the evening entry)."""
        for q in ("IST", "voice%20transcript", "evening.ogg", "2%3A31", "journal%20his"):
            self.assertEqual(self.stems(f"?q={q}"), [], q)
        path = self.cfg.root / "2026-10-14-evening.md"
        self.assertEqual(document.words_count(path), 18)
        weeks = self.get_json("/api/v1/insights/weeks?from=2026-10-12&to=2026-10-18")
        mine = sum(document.words_count(p) for p, _k in document.all_entries(self.cfg)
                   if "2026-10-12" <= p.stem[:10] <= "2026-10-18")
        self.assertEqual(weeks[0]["words"], mine)

    def test_pages_and_bad_input(self):
        first = self.get_json("/api/v1/entries?limit=2")
        self.assertEqual(len(first["items"]), 2)
        rest = self.get_json(f"/api/v1/entries?limit=2&cursor={first['next']}")
        self.assertEqual(([i["stem"] for i in rest["items"]], rest["next"]), (["2026-10-14-evening"], None))
        self.assertEqual(self.request("GET", "/api/v1/entries?from=yesterday")[0], 400)
        self.assertEqual(self.request("GET", "/api/v1/entries?limit=many")[0], 400)

    def test_days_group_entries_and_reflections(self):
        data = self.get_json("/api/v1/days?from=2026-10-13&to=2026-10-15")
        self.assertEqual([(d["date"], [e["stem"] for e in d["entries"]], d["reflection"]) for d in data],
                         [("2026-10-15", ["2026-10-15-j", "2026-10-15-fix-life-s01"], None),
                          ("2026-10-14", ["2026-10-14-evening"], "2026-10-14-reflection")])


class ReadRoutesTest(ServeCase):
    def test_today_is_today_collect_plus_the_week_strip(self):
        got = self.get_json("/api/v1/today")
        week = got.pop("week")
        self.assertEqual(got.pop("voices"), {"his": "Sam", "hers": "Ada"})   # the two voices, named as the document names them
        self.assertIsNone(got.pop("location_source"))   # no place named by OpenStreetMap: no credit to show
        self.assertEqual(got, json.loads(json.dumps(today.collect(self.cfg, NOW, stems=True))))   # the block's dict
        for row in got["practices"]["rows"]:      # plus the stems the web app links by, and nothing else
            row.pop("stem", None)
        (got["reflection"] or {}).pop("stem", None)
        self.assertEqual(got, json.loads(json.dumps(today.collect(self.cfg, NOW))))
        self.assertEqual([d["date"] for d in week], [f"2026-10-{n:02d}" for n in range(9, 16)])  # oldest first
        self.assertEqual(week[-1], {"date": "2026-10-15", "weekday": "Thu", "answered": 0, "missed": 0, "state": "none"})

    def test_the_week_strip_counts_the_ledger(self):
        from journal import ledger
        sent = lambda stem, day, hour, captures: {   # noqa: E731
            "id": stem, "stem": stem, "kind": stem.split("-")[-1], "local_date": day, "message_id": stem,
            "sent_at": datetime(2026, 10, int(day[-2:]), hour, 0, tzinfo=IST).isoformat(), "captures": captures}
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"] = [
                sent("2026-10-12-morning", "2026-10-12", 6, [{"message_id": "1", "at": NOW.isoformat()}]),
                sent("2026-10-12-evening", "2026-10-12", 23, []),
                sent("2026-10-13-evening", "2026-10-13", 23, []),
                sent("2026-10-14-evening", "2026-10-14", 23, [{"message_id": "2", "at": NOW.isoformat()}]),
                {**sent("2026-10-15-morning", "2026-10-15", 6, []), "sent_at": (NOW - timedelta(minutes=5)).isoformat()},
                {**sent("2026-10-15-note", "2026-10-15", 6, []), "kind": "j"},          # a /j note is no prompt
            ]
        states = {d["date"]: (d["state"], d["answered"], d["missed"]) for d in self.get_json("/api/v1/today")["week"]}
        self.assertEqual(states["2026-10-12"], ("partial", 1, 1))
        self.assertEqual(states["2026-10-13"], ("missed", 0, 1))
        self.assertEqual(states["2026-10-14"], ("answered", 1, 0))
        self.assertEqual(states["2026-10-15"], ("none", 0, 0))      # sent five minutes ago: still open
        with ledger.locked(self.cfg.ledger_path) as data:           # an answered prompt and one still waiting
            data["prompts"].append({**sent("2026-10-15-evening", "2026-10-15", 6, [{"message_id": "9", "at": NOW.isoformat()}])})
            data["prompts"].append({**sent("2026-10-15-gratitude", "2026-10-15", 6, []),
                                    "sent_at": (NOW - timedelta(minutes=1)).isoformat()})
        states = {d["date"]: (d["state"], d["answered"], d["missed"]) for d in self.get_json("/api/v1/today")["week"]}
        self.assertEqual(states["2026-10-15"][0], "partial")
        self.assertEqual(states["2026-10-11"], ("none", 0, 0))

    def test_program_phases_are_no_prompts_in_the_strip_or_the_weekly_counts(self):
        from journal import ledger
        sent = lambda stem, kind, **more: {   # noqa: E731
            "id": stem, "stem": stem, "kind": kind, "local_date": "2026-10-14", "message_id": stem,
            "sent_at": datetime(2026, 10, 14, 7, 0, tzinfo=IST).isoformat(), "captures": [], **more}
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"] = [
                sent("2026-10-14-evening", "evening", captures=[{"message_id": "2", "at": NOW.isoformat()}]),
                sent("2026-10-14-fix-life-s01", "fix-life", program="fix-life",
                     captures=[{"message_id": "3", "at": NOW.isoformat()}]),
                sent("2026-10-14-fix-life-s02", "fix-life", program="fix-life"),    # /skip leaves no capture
            ]
        states = {d["date"]: (d["state"], d["answered"], d["missed"]) for d in self.get_json("/api/v1/today")["week"]}
        self.assertEqual(states["2026-10-14"], ("answered", 1, 0))                    # as Today's rows see it
        weeks = self.get_json("/api/v1/insights/weeks")
        week = next(w for w in weeks if w["week_end"] >= "2026-10-14" > (date.fromisoformat(w["week_end"]) - timedelta(days=7)).isoformat())
        self.assertEqual((week["answered"], week["missed"]), (1, 0))

    def test_a_program_session_entry_shows_its_phases_and_the_program_title(self):
        doc = self.get_json("/api/v1/entries/2026-10-15-fix-life-s01")
        self.assertNotEqual(doc["practice"]["title"], "fix-life")
        qs = doc["prompt"]["questions"]
        self.assertTrue(qs and all(q["section"].startswith("Phase ") for q in qs))
        self.assertEqual(doc["prompt"]["phases"][0]["number"], 1)
        self.assertTrue(doc["prompt"]["phases"][0]["title"])

    def test_the_library_shows_the_schedule_of_the_file(self):
        rows = {r["id"]: r for r in self.get_json("/api/v1/library")}
        self.assertEqual([s["time"] for s in rows["gratitude"]["schedule"]], ["21:30"])
        self.assertEqual([s["time"] for s in self.get_json("/api/v1/library/gratitude")["definition"]["schedule"]], ["21:30"])

    def test_a_collision_renamed_entry_is_the_rows_stem(self):
        from journal import ledger
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"] = [{"id": "2026-10-15-morning", "stem": "2026-10-15-morning-2", "kind": "morning",
                                "local_date": "2026-10-15", "message_id": "1", "captures": [],
                                "entry_path": str(self.cfg.root / "2026-10-15-morning-2.md"),
                                "sent_at": datetime(2026, 10, 15, 6, 0, tzinfo=IST).isoformat()}]
        rows = [r for r in self.get_json("/api/v1/today")["practices"]["rows"] if r["id"] == "morning"]
        self.assertEqual(rows[0]["stem"], "2026-10-15-morning-2")

    def test_a_picture_embed_is_no_audio(self):
        from journal.serve import document
        from serve_helpers import NOTE
        path = self.cfg.root / "2026-10-15-j.md"
        path.write_text(NOTE + "\n![[diagram.png]]\n", encoding="utf-8")
        info = document.parsed(path)
        self.assertFalse(info["flags"]["audio"])
        self.assertEqual([m.group(1) for m in document._audio_embeds("![[2026-10-14-evening-2100.M4A]]\n![[a.png]]")],
                         ["2026-10-14-evening-2100.M4A"])

    def test_audio_filter_and_flag(self):
        got = self.get_json("/api/v1/entries?audio=1")
        self.assertEqual([i["stem"] for i in got["items"]], ["2026-10-14-evening"])
        self.assertTrue(got["items"][0]["flags"]["audio"])
        everything = {i["stem"]: i["flags"]["audio"] for i in self.get_json("/api/v1/entries")["items"]}
        self.assertEqual(everything, {"2026-10-15-j": False, "2026-10-15-fix-life-s01": False,
                                      "2026-10-14-evening": True})
        days = self.get_json("/api/v1/days?from=2026-10-01&to=2026-10-15&audio=1")
        self.assertEqual([e["stem"] for d in days for e in d["entries"]], ["2026-10-14-evening"])

    def test_library(self):
        rows = {r["id"]: r for r in self.get_json("/api/v1/library")}
        practices = {"morning", "evening", "midweek", "weekly", "gratitude"}
        self.assertEqual(set(rows) & practices, practices)
        # the programs and situational definitions are listed beside them (API.md), with their size
        self.assertEqual((rows["fix-life"]["kind"], rows["fix-life"]["source"], rows["fix-life"]["sessions"]),
                         ("program", "builtin", 10))
        self.assertGreater(rows["fix-life"]["phases_total"], 10)
        self.assertEqual((rows["fix-life"]["schedule"], rows["fix-life"]["active_run"]), ([], None))
        self.assertTrue(any(r["kind"] == "situational" for r in rows.values()))
        prog = self.get_json("/api/v1/library/fix-life")
        self.assertEqual((prog["definition"]["title"], prog["problems"], len(prog["sessions"])),
                         (rows["fix-life"]["title"], [], 10))
        self.assertIn("kind: program", prog["raw"])
        self.assertTrue(prog["definition"]["sections"] and prog["sessions"][0]["phases"][0]["questions"] is not None)
        self.assertEqual((rows["gratitude"]["source"], rows["evening"]["source"]), ("user", "builtin"))
        self.assertIsInstance(rows["gratitude"]["mtime"], float)
        one = self.get_json("/api/v1/library/gratitude")
        self.assertEqual((one["definition"]["title"], one["problems"]), ("Daily gratitude", []))
        self.assertIn("Three things today", one["raw"])
        self.assertEqual(self.request("GET", "/api/v1/library/nope")[0], 404)

    def test_no_run_is_an_empty_list_and_a_404(self):
        self.assertEqual(self.get_json("/api/v1/runs"), [])
        self.assertEqual(self.request("GET", "/api/v1/runs/2026-10-15-a1f3")[0], 404)
        self.assertEqual(self.request("GET", "/api/v1/runs/2026-10-15-a1f3/insights")[0], 404)

    def test_reflections_and_weeks(self):
        refs = self.get_json("/api/v1/reflections")
        self.assertEqual([(r["stem"], r["kind"], r["sources"]) for r in refs],
                         [("2026-10-14-reflection", "reflection", ["2026-10-14-evening"])])
        self.assertIn("What does the sea ask?", refs[0]["body"])
        self.assertEqual(self.request("GET", "/api/v1/reflections?kind=poem")[0], 400)
        weeks = self.get_json("/api/v1/insights/weeks?from=2026-10-12&to=2026-10-18")
        self.assertEqual(len(weeks), 1)
        w = weeks[0]
        self.assertEqual((w["week_end"], w["entries"], w["answered"], w["missed"], w["reflections"]),
                         ("2026-10-18", 3, 1, 0, ["2026-10-14-reflection"]))
        self.assertGreater(w["words"], 10)

    def test_program_prompts_are_not_practice_prompts(self):
        """Phase and interrupt prompts carry ``program``; they are answered through the run, not missed practices."""
        before = self.get_json("/api/v1/insights/weeks?from=2026-10-12&to=2026-10-18")[0]
        with ledger.locked(self.cfg.ledger_path) as data:
            for n, extra in enumerate(({"phase": 1}, {"phase": 2}, {"interrupt": 1})):
                data["prompts"].append({
                    "id": f"2026-10-14-a1f3-s01-p{n}", "stem": "2026-10-15-fix-life-s01", "kind": "fix-life",
                    "practice": "fix-life", "program": "fix-life", "run": "2026-10-15-a1f3", "local_date": "2026-10-14",
                    "chat_id": self.cfg.chat_id, "message_id": f"6{n}0", "captures": [],
                    "sent_at": datetime(2026, 10, 14, 6, 0, tzinfo=timezone.utc).isoformat(), **extra})
        after = self.get_json("/api/v1/insights/weeks?from=2026-10-12&to=2026-10-18")[0]
        self.assertEqual((after["answered"], after["missed"]), (before["answered"], before["missed"]))

    def test_status_doctor_and_settings_leave_the_chat_side_out(self):
        s = self.get_json("/api/v1/status")
        for key in ("conversations", "mirror_queued", "notebook_open", "hook_state"):
            self.assertNotIn(key, s)
        self.assertIn("today", s)
        checks = [r["check"] for r in self.get_json("/api/v1/doctor")]
        self.assertNotIn("hook state", checks)
        self.assertNotIn("telegram token", checks)
        self.assertNotIn("serve token", checks)
        self.assertIn("same version as tick", checks)

    def test_settings_hide_secrets(self):
        self.cfg.raw = jconfig.deep_merge(jconfig.DEFAULTS, {
            "root": str(self.cfg.root), "telegram": {"token_command": ["secret-helper", "x"]},
            "serve": {"token_command": "helper"}, "sources": {"tududi": {"enabled": True, "command": ["t", "--key"]}}})
        data = self.get_json("/api/v1/settings")["settings"]
        self.assertEqual((data["telegram"]["token_command"], data["serve"]["token_command"],
                          data["sources"]["tududi"]["command"]), ("<set>", "<set>", "<set>"))
        self.assertEqual(data["telegram"]["token_env"], "<set>")
        self.assertEqual(data["root"], str(self.cfg.root))


class HeadersAndStaticTest(ServeCase):
    def test_a_percent_encoded_api_path_is_no_store_too(self):
        for path in ("/%61pi/v1/today", "/api/v1/entries/2026-10-14-%65vening", "/%68ealthz"):
            code, h, _b = self.request("GET", path)
            self.assertEqual(code, 200, path)
            self.assertEqual(h["cache-control"], "no-store", path)
        self.assertEqual(self.request("GET", "/%61pi/v1/today", token=False)[1]["cache-control"], "no-store")  # a 401

    def test_csp_nosniff_and_no_store(self):
        for path, token in (("/api/v1/today", True), ("/api/v1/today", False), ("/", False), ("/healthz", False)):
            _c, h, _b = self.request("GET", path, token=token)
            self.assertIn("default-src 'self'", h["content-security-policy"], path)
            # blob: lets the composer play back a clip he just recorded before sending it (a blob: URL is no
            # network origin: it only names bytes the page itself holds)
            self.assertIn("media-src 'self' blob:;", h["content-security-policy"])
            # the service worker and the manifest are this origin's files, nothing else's
            self.assertIn("worker-src 'self';", h["content-security-policy"])
            self.assertIn("manifest-src 'self';", h["content-security-policy"])
            self.assertEqual(h["x-content-type-options"], "nosniff")
            if path.startswith("/api") or path == "/healthz":
                self.assertEqual(h["cache-control"], "no-store", path)
        self.assertNotIn("access-control-allow-origin", self.request("GET", "/api/v1/today",
                                                                     headers={"Origin": "http://localhost:5173"})[1])

    def test_the_placeholder_page_and_confinement(self):
        empty = Path(self.tmp.name) / "no-build"
        empty.mkdir()
        self.cfg.serve["assets_dir"] = str(empty)    # an install with no web app build: the placeholder stands in
        code, h, body = self.request("GET", "/", token=False)
        self.assertEqual((code, h["content-type"]), (200, "text/html; charset=utf-8"))
        self.assertIn(b"/fallback.js", body)
        self.assertEqual(self.request("GET", "/pair", token=False)[2], body)     # an app route
        self.assertEqual(self.request("GET", "/fallback.js", token=False)[0], 200)
        for path in ("/../app.py", "/%2E%2E/app.py", "/.hidden", "/nope.js"):
            self.assertEqual(self.request("GET", path, token=False)[0], 404, path)

    def test_the_packaged_web_app_is_served_by_default(self):
        code, h, body = self.request("GET", "/", token=False)
        self.assertEqual((code, h["content-type"], h["cache-control"]), (200, "text/html; charset=utf-8", "no-cache"))
        self.assertIn(b'<div id="app">', body)
        self.assertNotIn(b"fallback.js", body)
        for route in ("/pair", "/entries", "/entries/2026-10-14-evening", "/library/gratitude", "/insights", "/settings"):
            self.assertEqual(self.request("GET", route, token=False)[2], body, route)   # the client routes them
        assets = re.findall(rb'(?:src|href)="(/assets/[^"]+)"', body)
        self.assertTrue(assets)
        for asset in assets:
            c, ah, _b = self.request("GET", asset.decode(), token=False)
            self.assertEqual(c, 200, asset)
            self.assertTrue(ah["content-type"].startswith(("text/javascript", "text/css")), asset)
        self.assertIn(b'rel="manifest"', body)
        for path, ctype in (("/sw.js", "text/javascript; charset=utf-8"),
                            ("/manifest.webmanifest", "application/manifest+json")):
            c, ph, _b = self.request("GET", path, token=False)
            self.assertEqual((c, ph["content-type"]), (200, ctype), path)
            self.assertEqual(ph["cache-control"], "no-cache", path)   # a new build is picked up on the next load

    def test_the_service_worker_and_manifest_are_never_cached_by_the_browser(self):
        # a stale sw.js is a stale app: the worker script and the manifest are revalidated every time
        build = Path(self.tmp.name) / "pwa-build"
        build.mkdir()
        (build / "index.html").write_text("<!doctype html>", encoding="utf-8")
        (build / "sw.js").write_text("self.skipWaiting()", encoding="utf-8")
        (build / "manifest.webmanifest").write_text("{}", encoding="utf-8")
        self.cfg.serve["assets_dir"] = str(build)
        for path in ("/sw.js", "/manifest.webmanifest", "/"):
            c, h, _b = self.request("GET", path, token=False)
            self.assertEqual((c, h["cache-control"]), (200, "no-cache"), path)
        self.assertEqual(self.request("GET", "/sw.js", token=False)[1]["x-content-type-options"], "nosniff")
        # a missing file with an extension is a 404, never the shell served as a script
        self.assertEqual(self.request("GET", "/other-sw.js", token=False)[0], 404)

    def test_a_built_app_is_served_instead(self):
        build = Path(self.tmp.name) / "build"
        (build / "assets").mkdir(parents=True)
        (build / "index.html").write_text("<!doctype html><title>built</title>", encoding="utf-8")
        (build / "assets" / "a-1.js").write_text("1", encoding="utf-8")
        self.cfg.serve["assets_dir"] = str(build)
        self.assertIn(b"built", self.request("GET", "/entries/2026-10-14-evening", token=False)[2])
        self.assertEqual(self.request("GET", "/assets/a-1.js", token=False)[1]["content-type"],
                         "text/javascript; charset=utf-8")


class EventsTest(ServeCase):
    def test_touching_an_entry_emits_entries_changed(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=10)
        try:
            sock.sendall((f"GET /api/v1/events HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                          f"Authorization: Bearer {self.token}\r\n\r\n").encode())
            buf = b""
            while b": connected" not in buf:
                buf += sock.recv(4096)
            self.assertIn(b"text/event-stream", buf)
            self.assertIn(b"no-transform", buf)
            future = time.time() + 5
            os.utime(self.cfg.root / "2026-10-14-evening.md", (future, future))
            deadline = time.time() + 5
            while b"entries-changed" not in buf and time.time() < deadline:
                buf += sock.recv(4096)
            self.assertIn(b'event: entries-changed\ndata: {"stem": "2026-10-14-evening"}', buf)
        finally:
            sock.close()

    def test_a_reflection_is_not_an_entry_for_the_stream_either(self):
        """The stream seeds what it knows from the snapshot with the same rule ``changes`` uses for a new file; a
        reflection seeded as known would report ``entries-changed`` for a stem the document route answers 404."""
        before = sse.snapshot(self.cfg)
        known = sse.known_stems(self.cfg, before)
        self.assertIn("2026-10-14-evening", known)
        self.assertNotIn("2026-10-14-reflection", known)
        future = time.time() + 100
        os.utime(self.cfg.root / "2026-10-14-reflection.md", (future, future))
        self.assertEqual(sse.changes(self.cfg, before, sse.snapshot(self.cfg), known), [])

    def test_edits_to_a_user_program_are_reported(self):
        """A user program lives at ``practices_dir/<name>/program.md`` (with its session files beside it)."""
        prog = Path(self.cfg.practices_dir) / "my-program"
        prog.mkdir()
        (prog / "program.md").write_text("---\nkind: program\n---\n", encoding="utf-8")
        before = sse.snapshot(self.cfg)
        future = time.time() + 5
        os.utime(prog / "program.md", (future, future))
        self.assertEqual(sse.changes(self.cfg, before, sse.snapshot(self.cfg), set()),
                         [("library-changed", {"id": "my-program"})])
        before = sse.snapshot(self.cfg)
        (prog / "session-01.md").write_text("one", encoding="utf-8")
        self.assertEqual(sse.changes(self.cfg, before, sse.snapshot(self.cfg), set()),
                         [("library-changed", {"id": "my-program"})])
        before = sse.snapshot(self.cfg)
        (prog / "session-01.md").unlink()
        self.assertEqual(sse.changes(self.cfg, before, sse.snapshot(self.cfg), set()),
                         [("library-changed", {"id": "my-program"})])

    def test_editing_a_program_file_reports_library_changed_for_the_program(self):
        folder = self.cfg.practices_dir / "my-program" / "sessions"
        folder.mkdir(parents=True)
        (self.cfg.practices_dir / "my-program" / "program.md").write_text("x", encoding="utf-8")
        (folder / "01-a.md").write_text("x", encoding="utf-8")
        before = sse.snapshot(self.cfg)
        future = time.time() + 50
        os.utime(folder / "01-a.md", (future, future))
        events = sse.changes(self.cfg, before, sse.snapshot(self.cfg), set())
        self.assertEqual(events, [("library-changed", {"id": "my-program"})])

    def test_his_own_files_are_never_reported(self):
        before = sse.snapshot(self.cfg)
        known = {"2026-10-14-evening"}
        future = time.time() + 5
        for name in ("2026-10-14-thoughts.md", "2026-10-14-evening.md"):
            os.utime(self.cfg.root / name, (future, future))
        (self.cfg.state_dir / "last-tick.json").write_text('{"at": "2026-10-15T03:00:00+00:00"}', encoding="utf-8")
        events = sse.changes(self.cfg, before, sse.snapshot(self.cfg), known)
        self.assertEqual(events, [("entries-changed", {"stem": "2026-10-14-evening"}),
                                  ("tick", {"at": "2026-10-15T03:00:00+00:00"})])

    def test_streams_are_capped(self):
        self.app._streams = serve_app.threading.BoundedSemaphore(0)
        self.assertEqual(self.request("GET", "/api/v1/events")[0], 503)


class BindTest(CleanEnv):
    def test_an_address_that_never_appears_exits_1_after_the_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), names=NEUTRAL)
            lines = []
            application = serve_app.App(lambda: cfg, log=lines.append)
            started = time.monotonic()
            code = serve_app.run(application, "192.0.2.1", 0, retry_seconds=0.6)  # TEST-NET-1: never local
            self.assertEqual(code, 1)
            self.assertGreaterEqual(time.monotonic() - started, 0.5)
            self.assertTrue(any("retrying" in l for l in lines), lines)
            self.assertTrue(any("cannot listen" in l for l in lines), lines)


EXAMPLE = ROOT / "deploy" / "journal-serve.service.example"
OVERLAY = ROOT / "deploy" / "example-overlay.json"  # the shape of a deployment overlay, placeholder values


class UnitTest(CleanEnv):
    def overlay_cfg(self):
        data = json.loads(OVERLAY.read_text(encoding="utf-8"))
        data.update({"root": "/srv/journal/Journal", "timezone": "UTC"})
        return jconfig.from_dict(data, hermes_home=Path("/home/you/.hermes/profiles/companion"),
                                 config_file=Path("/home/you/.config/journal/companion.json"))

    def render(self, cfg):
        return unit.render(cfg, exe="/home/you/.local/bin/journal",
                           mount_of=lambda p: Path("/srv") if str(p).startswith("/srv") else Path("/"))

    def test_the_example_is_the_rendering_of_the_overlay(self):
        """JOURNAL_WRITE_FIXTURES=1 rewrites the example after a deliberate change."""
        text = self.render(self.overlay_cfg())
        if os.environ.get("JOURNAL_WRITE_FIXTURES"):
            EXAMPLE.write_text(text, encoding="utf-8")
        self.assertEqual(text, EXAMPLE.read_text(encoding="utf-8"))
        for line in ("NoNewPrivileges=yes", "KillMode=process", "UMask=0077", "MemoryHigh=384M", "MemoryMax=768M",
                     "MemorySwapMax=0", "TasksMax=64", "CPUWeight=50",
                     "Environment=HERMES_DISABLE_LAZY_INSTALLS=1", "RequiresMountsFor=/srv",
                     "ExecStartPre=/usr/bin/mountpoint -q /srv",
                     "ExecStart=/home/you/.local/bin/journal --config /home/you/.config/journal/companion.json "
                     "--hermes-home /home/you/.hermes/profiles/companion serve"):
            self.assertIn(line + "\n", text)

    def test_no_setting_that_builds_a_namespace(self):
        """Under a user manager these put the service in a user namespace where tailscaled (root) reads as uid
        65534, and Tailscale identity fails (DECISIONS.md, SERVE-UNSANDBOXED)."""
        cfg = self.overlay_cfg()
        cfg.serve["socket"] = "/run/user/1000/journal-serve/serve.sock"
        for text in (self.render(self.overlay_cfg()), self.render(cfg)):
            for line in text.splitlines():
                self.assertFalse(line.startswith(("Private", "Protect", "ReadWritePaths", "ReadOnlyPaths",
                                                  "InaccessiblePaths", "TemporaryFileSystem", "BindPaths",
                                                  "BindReadOnlyPaths", "RestrictNamespaces")), line)

    def test_the_overlay_leaves_the_bind_to_the_operator(self):
        serve = json.loads(OVERLAY.read_text(encoding="utf-8"))["serve"]
        self.assertNotIn("bind", serve)   # Decision 8: loopback until the operator sets it at deploy time
        self.assertEqual(self.overlay_cfg().serve["bind"], "127.0.0.1")

    def test_a_separate_practices_dir_on_its_own_mount_is_required_too(self):
        cfg = self.overlay_cfg()
        cfg.practices_dir = Path("/data/practices")
        mounts = {"/srv": Path("/srv"), "/data": Path("/data")}
        text = unit.render(cfg, exe="/home/you/.local/bin/journal",
                           mount_of=lambda p: next((m for k, m in mounts.items() if str(p).startswith(k)), Path("/")))
        self.assertIn("RequiresMountsFor=/srv /data\n", text)
        self.assertIn("ExecStartPre=/usr/bin/mountpoint -q /data\n", text)


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

    def test_pair_prints_a_one_time_link(self):
        code, out, _err = self.run_cli("serve", "pair")
        self.assertEqual(code, 0)
        self.assertRegex(out.strip(), r"^http://127\.0\.0\.1:8790/pair#[\w-]{40,}$")
        self.assertTrue(auth.redeem(self.cfg, out.strip().split("#", 1)[1]))

    def test_token_rotate_and_print_unit(self):
        self.run_cli("serve", "pair")
        before = auth.token_file(self.cfg).read_text()
        code, out, _ = self.run_cli("serve", "token", "--rotate")
        self.assertEqual(code, 0)
        self.assertNotEqual(before, auth.token_file(self.cfg).read_text())
        self.assertNotIn(auth.resolve_token(self.cfg)[0], out)   # never printed
        code, out, _ = self.run_cli("serve", "--print-unit")
        self.assertEqual(code, 0)
        self.assertIn("NoNewPrivileges=yes\n", out)
        self.assertNotIn("ProtectHome", out)

    def test_check_on_a_free_port(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.cfg.serve["port"] = port
        code, out, _ = self.run_cli("serve", "check")
        self.assertEqual(code, 0, out)
        self.assertIn(f"serve port: 127.0.0.1:{port} is free", out)
        self.assertIn("same version as tick", out)

    def test_a_failing_token_command_stops_the_server_at_start(self):
        self.cfg.serve["token_command"] = ["/nonexistent/secret-helper"]
        code, _out, err = self.run_cli("serve", "--port", "1")
        self.assertEqual(code, 1)
        self.assertIn("no serve token", err)


class ConfigAndDoctorTest(CleanEnv):
    def test_defaults_env_and_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_config(Path(tmp) / "c.json", {"root": tmp})
            cfg = jconfig.load(path)
            self.assertEqual((cfg.serve["bind"], cfg.serve["port"], cfg.serve["host"], cfg.serve["allow_write"]),
                             ("127.0.0.1", 8790, "", True))
            cfg = jconfig.load(path, environ={"JOURNAL_SERVE_BIND": "10.9.8.7"})
            self.assertEqual(cfg.serve["bind"], "10.9.8.7")
            bad = write_config(Path(tmp) / "b.json", {"root": tmp, "serve": {"port": "x", "host": "https://a/",
                                                                             "trusted_proxies": ["nope"],
                                                                             "public_port": "x"}})
            text = "\n".join(jconfig.problems(jconfig.load(bad)))
            self.assertIsNone(cfg.serve["public_port"])
            for word in ("serve.port", "serve.host", "serve.trusted_proxies", "serve.public_port"):
                self.assertIn(word, text)

    def test_doctor_rows_and_the_version_check(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), names=NEUTRAL)
            rows = {n: (s, d) for n, s, d in status.doctor(cfg)}
            for name in ("serve token", "serve bind", "serve assets", "same version as tick"):
                self.assertIn(name, rows)
                self.assertNotEqual(rows[name][0], "fail", (name, rows[name]))
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            tick = cfg.state_dir / "last-tick.json"
            tick.write_text(json.dumps({"at": "x", **version.stamp()}), encoding="utf-8")
            self.assertEqual(status.version_row(cfg)[1], "ok")
            tick.write_text(json.dumps({"at": "x", "version": "0.1", "code": "000000000000"}), encoding="utf-8")
            self.assertEqual(status.version_row(cfg)[1], "fail")
            tick.write_text(json.dumps({"at": "x"}), encoding="utf-8")
            self.assertEqual(status.version_row(cfg)[1], "warn")

    def test_the_tick_records_its_version(self):
        from journal import tick
        with tempfile.TemporaryDirectory() as tmp:
            cfg = make_cfg(Path(tmp), names=NEUTRAL)
            cfg.state_dir.mkdir(parents=True, exist_ok=True)
            tick._record_run(cfg, datetime(2026, 10, 15, tzinfo=timezone.utc), "tick", [], [], 0)
            data = json.loads((cfg.state_dir / "last-tick.json").read_text(encoding="utf-8"))
            self.assertEqual((data["version"], data["code"]), (version.stamp()["version"], version.code_id()))


if __name__ == "__main__":
    unittest.main()
