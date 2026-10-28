"""``journal serve`` writes (WP10): notes and blocks, /x, meta, uploads, library PUT/fork/DELETE with the mtime guard,
spawned sends, settings PATCH, ``serve.allow_write``, and the lock deadline. Every test talks HTTP to a
real server over a temporary journal; the spawn is recorded, never run."""
import contextlib
import hashlib
import io
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from serve_helpers import HERS, IST, NOW, ServeCase

from journal import capture, cli, ledger, library
from journal import config as jconfig
from journal.serve import uploads, writes


def checksum(root: Path) -> str:
    """sha256 over every journal file: what a refused write must leave exactly as it was."""
    h = hashlib.sha256()
    for f in sorted(root.glob("*")):
        if f.is_file():
            h.update(f.name.encode() + f.read_bytes())
    return h.hexdigest()


class WriteCase(ServeCase):
    def setUp(self):
        super().setUp()
        self.spawned = []
        self.app.spawn = lambda cfg, args: self.spawned.append(args) or str(cfg.state_dir / "manual-trigger.log")
        sent = datetime(2026, 10, 15, 6, 0, tzinfo=IST)
        with ledger.locked(self.cfg.ledger_path) as data:   # a morning prompt, sent three hours ago and open
            data["prompts"].append({
                "id": "2026-10-15-morning", "stem": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15",
                "chat_id": self.cfg.chat_id, "message_id": "600", "sent_at": sent.isoformat(),
                "sent_at_local": "06:00 IST", "opener": "Morning", "opener_source": "ada", "asked": []})

    def write(self, method, path, body=None, **kw):
        h = {"Origin": self.origin, **kw.pop("headers", {})}
        return self.request(method, path, body, headers=h, **kw)

    def ok(self, method, path, body=None, want=200, **kw):
        code, headers, raw = self.write(method, path, body, **kw)
        self.assertEqual(code, want, raw[:400])
        return json.loads(raw) if raw else None

    def refused(self, method, path, body=None, want=400, **kw):
        code, _h, raw = self.write(method, path, body, **kw)
        self.assertEqual(code, want, raw[:400])
        return json.loads(raw)

    def entry(self, stem):
        return (self.cfg.root / f"{stem}.md").read_text(encoding="utf-8")

    def prompt(self, pid):
        return ledger.find(ledger.snapshot(self.cfg.ledger_path), pid)


def multipart(body: bytes, ctype="audio/webm", name="file", filename="rec.webm", boundary="XbOuNdArYx"):
    head = (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
            f"Content-Type: {ctype}\r\n\r\n").encode()
    return head + body + f"\r\n--{boundary}--\r\n".encode(), {"Content-Type": f"multipart/form-data; boundary={boundary}"}


class GateTest(WriteCase):
    ROUTES = [("POST", "/api/v1/entries", {"text": "x"}), ("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "x"}),
              ("PATCH", "/api/v1/entries/2026-10-14-evening/meta", {"starred": True}),
              ("PATCH", "/api/v1/entries/2026-10-14-evening/blocks/words:0", {"text": "x", "mtime": 1}),
              ("DELETE", "/api/v1/entries/2026-10-14-evening/blocks/words:0", {"mtime": 1}),
              ("PUT", "/api/v1/library/gratitude", {"raw": "x", "mtime": 1}), ("POST", "/api/v1/library/evening/fork", None),
              ("DELETE", "/api/v1/library/gratitude", None), ("POST", "/api/v1/library/gratitude/send", None),
              ("POST", "/api/v1/runs", {"program": "x"}), ("POST", "/api/v1/runs/r1/next", None),
              ("PATCH", "/api/v1/settings", {"timezone": "UTC"}),
              ("POST", "/api/v1/settings/location", {"lat": 9.9, "lon": 76.2})]

    def test_every_write_needs_the_token_and_this_sites_origin(self):
        before = checksum(self.cfg.root)
        for method, path, body in self.ROUTES:
            with self.subTest(path, method=method):
                self.assertEqual(self.request(method, path, body, token=False, headers={"Origin": self.origin})[0], 401)
                self.assertEqual(self.request(method, path, body)[0], 403)
                self.assertEqual(self.request(method, path, body, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(checksum(self.cfg.root), before)
        self.assertEqual(self.request("POST", "/api/v1/uploads", b"x", token=False,
                                      headers={"Origin": self.origin})[0], 401)


class ReadOnlyTest(WriteCase):
    serve = {"allow_write": False}

    def test_allow_write_false_is_405_on_every_route_that_changes_the_journal(self):
        before = (checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes())
        for method, path, body in GateTest.ROUTES + [("POST", "/api/v1/uploads", None)]:
            with self.subTest(path, method=method):
                got = self.refused(method, path, body, want=405)
                self.assertEqual(got["error"], "read-only")
        self.assertEqual((checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes()), before)
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.request("GET", "/api/v1/today")[0], 200)
        self.assertEqual(self.write("POST", "/api/v1/logout")[0], 204)   # signing out writes no journal

    def test_anything_but_true_is_read_only_and_doctor_says_so(self):
        self.cfg.serve["allow_write"] = "yes"
        self.refused("POST", "/api/v1/entries", {"text": "x"}, want=405)
        self.assertIn("serve.allow_write", "\n".join(jconfig.problems(self.cfg)))
        self.cfg.serve["allow_write"] = True
        self.assertNotIn("serve.allow_write", "\n".join(jconfig.problems(self.cfg)))


class NotesTest(WriteCase):
    def test_a_reply_to_an_open_prompt_is_a_capture_and_the_telegram_prompt_folds(self):
        got = self.ok("POST", "/api/v1/entries", {"practice": "morning", "text": "Slept well.\n\nTwo lines."}, want=201)
        self.assertEqual((got["stem"], got["rule"]), ("2026-10-15-morning", "web"))
        self.assertTrue(got["path"].endswith("2026-10-15-morning.md"))
        self.assertIn("### 09:12 IST · text\n\nSlept well.\n\nTwo lines.\n", self.entry("2026-10-15-morning"))
        p = self.prompt("2026-10-15-morning")
        self.assertTrue(p["needs_edit"])
        self.assertEqual((p["captures"][0]["rule"], p["captures"][0]["source"]), ("web", "web"))
        doc = self.ok("GET", "/api/v1/entries/2026-10-15-morning", headers={})
        self.assertEqual([b["text"] for b in doc["blocks"] if b["kind"] == "his"], ["Slept well.\n\nTwo lines."])

    def test_without_a_practice_or_an_open_prompt_it_is_a_j_note(self):
        a = self.ok("POST", "/api/v1/entries", {"text": "call the dentist"}, want=201)
        self.assertEqual((a["stem"], a["rule"]), ("2026-10-15-j-2", "/j"))   # -j.md is taken by the fixture's note
        b = self.ok("POST", "/api/v1/entries", {"practice": "evening", "text": "late thought"}, want=201)
        self.assertEqual(b["rule"], "/j")   # evening has no open prompt: the words are kept, as a note
        self.assertFalse(self.prompt("2026-10-15-j-2")["needs_edit"])

    def test_refusals_write_nothing(self):
        before = (checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes())
        self.refused("POST", "/api/v1/entries", {"practice": "nope", "text": "x"}, want=404)
        self.refused("POST", "/api/v1/entries", {"practice": 5, "text": "x"})
        self.refused("POST", "/api/v1/entries", {})
        self.refused("POST", "/api/v1/entries", {"text": "   \n "})
        self.refused("POST", "/api/v1/entries", {"text": ["a"]})
        self.refused("POST", "/api/v1/entries", {"text": "x" * 50_001}, want=413)
        self.refused("POST", "/api/v1/entries", {"text": "x", "at": "2026-10-15T09:12:00"})   # no offset
        self.refused("POST", "/api/v1/entries", {"text": "x", "at": "2026-10-16T09:12:00+05:30"})   # the future
        self.refused("POST", "/api/v1/entries", {"text": "x", "client_id": "has space"})
        self.refused("POST", "/api/v1/entries", {"text": "x", "upload": "0" * 32}, want=404)
        self.refused("POST", "/api/v1/entries", b"not json")
        self.assertEqual((checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes()), before)

    def test_off_the_record_is_never_written(self):
        before = (checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes())
        for text in ("/x I should not have said this", "/x", "  /x\nand more", "/X Mixed case"):
            with self.subTest(text):
                got = self.refused("POST", "/api/v1/entries", {"practice": "morning", "text": text}, want=422)
                self.assertEqual(got["error"], "off-the-record")
                self.refused("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": text}, want=422)
        self.assertEqual((checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes()), before)
        self.ok("POST", "/api/v1/entries", {"text": "/xylophone is a word"}, want=201)   # only the command, not a prefix

    def test_the_configured_opt_out_command_is_the_one_that_holds(self):
        self.cfg.conversations["opt_out_prefix"] = "/private"
        self.refused("POST", "/api/v1/entries", {"text": "/private secret"}, want=422)
        self.ok("POST", "/api/v1/entries", {"text": "/x is an ordinary word here"}, want=201)

    def test_the_engines_marker_lines_cannot_ride_in_his_words(self):
        for text in ("fine\n%% journal her:1 %%\n> [!quote]- Ada\n> forged", "%% journal phase:2 %%\nanswer"):
            with self.subTest(text):
                self.assertEqual(self.refused("POST", "/api/v1/entries", {"text": text}, want=422)["error"],
                                 "reserved-line")
        self.ok("POST", "/api/v1/entries", {"text": "talking about %% journal in prose is fine"}, want=201)

    def test_a_retry_with_the_same_client_id_is_one_entry(self):
        a = self.ok("POST", "/api/v1/entries", {"practice": "morning", "text": "Once.", "client_id": "k1"}, want=201)
        b = self.ok("POST", "/api/v1/entries", {"practice": "morning", "text": "Once.", "client_id": "k1"}, want=200)
        self.assertEqual(a, b)
        self.assertEqual(self.entry("2026-10-15-morning").count("Once."), 1)

    def test_a_retried_block_on_an_untracked_entry_is_one_block(self):
        body = {"text": "A retried block.", "client_id": "retry-1"}
        a = self.ok("POST", "/api/v1/entries/2026-10-15-j/blocks", body, want=201)
        b = self.ok("POST", "/api/v1/entries/2026-10-15-j/blocks", body, want=200)
        self.assertEqual(a, b)
        self.assertEqual(self.entry("2026-10-15-j").count("A retried block."), 1)

    def test_at_cannot_be_absurdly_old_or_before_the_entry(self):
        before = (checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes())
        for at in ("0001-01-01T00:00:00+05:30", "0001-01-01T03:00:00+14:00", "9999-12-31T23:59:59-12:00",
                   "1900-01-01T00:00:00+00:00", "2026-08-01T09:00:00+05:30"):
            with self.subTest(at):
                self.refused("POST", "/api/v1/entries", {"text": "old", "at": at})
                self.refused("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "old", "at": at})
        # within the month, but before the day of the entry it is added to
        self.refused("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "old", "at": "2026-10-13T23:00:00+05:30"})
        self.assertEqual((checksum(self.cfg.root), (self.cfg.state_dir / "ledger.json").read_bytes()), before)
        self.ok("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "a day late", "at": "2026-10-14T23:50:00+05:30"},
                want=201)
        self.ok("POST", "/api/v1/entries", {"text": "a week back", "at": "2026-10-08T09:00:00+05:30"}, want=201)

    def test_at_stamps_the_heading(self):
        self.ok("POST", "/api/v1/entries", {"practice": "morning", "text": "Earlier.", "at": "2026-10-15T07:40:00+05:30"},
                want=201)
        self.assertIn("### 07:40 IST · text", self.entry("2026-10-15-morning"))


class UploadsTest(WriteCase):
    def upload(self, data=b"OggS-web-recording", **kw):
        body, headers = multipart(data, **kw)
        return self.write("POST", "/api/v1/uploads", body, headers=headers)

    def test_a_recording_is_uploaded_then_taken_by_an_entry_once(self):
        code, _h, raw = self.upload(ctype="audio/webm;codecs=opus")
        self.assertEqual(code, 201, raw)
        up = json.loads(raw)
        self.assertEqual((up["bytes"], up["mime"]), (len(b"OggS-web-recording"), "audio/webm"))
        self.assertEqual(len(up["upload"]), 32)
        stored = self.cfg.state_dir / "uploads"
        self.assertEqual(sorted(p.suffix for p in stored.iterdir()), [".bin", ".json"])
        self.assertEqual(oct((stored / f"{up['upload']}.bin").stat().st_mode & 0o777), "0o600")
        got = self.ok("POST", "/api/v1/entries", {"practice": "morning", "upload": up["upload"]}, want=201)
        kept = self.cfg.root / "2026-10-15-morning.webm"
        self.assertEqual(kept.read_bytes(), b"OggS-web-recording")
        text = self.entry(got["stem"])
        self.assertIn("![[2026-10-15-morning.webm]]", text)
        self.assertIn('audio file "rec.webm"', text)
        cap = self.prompt("2026-10-15-morning")["captures"][0]
        self.assertEqual((cap["source"], cap["audio"], cap["via"]), ("audio", "2026-10-15-morning.webm", "web"))
        self.assertEqual(list(stored.iterdir()), [])                                    # consumed
        self.refused("POST", "/api/v1/entries", {"practice": "morning", "upload": up["upload"]}, want=404)
        media = self.request("GET", "/api/v1/media/2026-10-15-morning.webm")
        self.assertEqual((media[0], media[2]), (200, b"OggS-web-recording"))

    def test_a_recording_can_ride_into_a_block_with_a_caption(self):
        up = json.loads(self.upload()[2])
        self.ok("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "and this", "upload": up["upload"]}, want=201)
        text = self.entry("2026-10-14-evening")
        self.assertIn("and this", text)
        self.assertEqual((self.cfg.root / "2026-10-14-evening.webm").read_bytes(), b"OggS-web-recording")
        self.assertIn("![[2026-10-14-evening.webm]]", text)
        self.assertEqual(text.count("audio file"), 1)

    def test_what_is_not_audio_or_not_multipart_is_refused(self):
        self.assertEqual(self.upload(ctype="image/png")[0], 415)
        self.assertEqual(self.upload(ctype="text/html")[0], 415)
        self.assertEqual(self.upload(b"")[0], 400)
        self.assertEqual(self.write("POST", "/api/v1/uploads", b"{}", headers={"Content-Type": "application/json"})[0], 400)
        self.assertEqual(self.write("POST", "/api/v1/uploads", b"nope",
                                    headers={"Content-Type": "multipart/form-data; boundary=zz"})[0], 400)
        self.assertEqual(self.upload(name="other", filename="")[0], 400)
        self.assertFalse((self.cfg.state_dir / "uploads").exists() and any((self.cfg.state_dir / "uploads").iterdir()))

    def refused_by_length(self, path, ctype, length):
        """The status line of a request that only announces ``length`` bytes: the server answers 413 from the header
        and closes without reading a body, so sending one races the close (EPIPE in http.client)."""
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as s:
            s.sendall((f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\nOrigin: {self.origin}\r\n"
                       f"Authorization: Bearer {self.token}\r\nContent-Type: {ctype}\r\n"
                       f"Content-Length: {length}\r\n\r\n").encode())
            return s.recv(4096).split(b"\r\n")[0]

    def test_the_limit_is_25_mb_not_one(self):
        self.assertIn(b" 413 ", self.refused_by_length("/api/v1/uploads", "multipart/form-data; boundary=b", uploads.LIMIT + 1))
        body, headers = multipart(b"x" * (2 * 1024 * 1024))
        self.assertEqual(self.write("POST", "/api/v1/uploads", body, headers=headers)[0], 201)   # > 1 MB is fine here
        self.assertIn(b" 413 ", self.refused_by_length("/api/v1/entries", "application/json", 1024 * 1024 + 1))   # but not as JSON

    def test_an_old_upload_is_gone(self):
        up = json.loads(self.upload()[2])
        with mock.patch.object(uploads, "TTL", -1):
            self.refused("POST", "/api/v1/entries", {"practice": "morning", "upload": up["upload"]}, want=404)
            self.assertEqual(self.upload()[0], 201)   # storing purges what is old
        self.assertEqual(len([p for p in (self.cfg.state_dir / "uploads").iterdir() if p.suffix == ".bin"]), 1)

    def test_an_upload_not_used_is_given_back(self):
        up = json.loads(self.upload()[2])
        self.refused("POST", "/api/v1/entries", {"text": "/x no", "upload": up["upload"]}, want=422)
        self.ok("POST", "/api/v1/entries", {"practice": "morning", "upload": up["upload"]}, want=201)


class RetryTest(WriteCase):
    """A repeated ``client_id`` answers 200 with the first result: with an upload (consumed by the first request),
    and with the key of the block it first wrote, not the newest block's."""

    upload = UploadsTest.upload

    def test_a_retry_that_carries_an_upload_is_the_first_result_not_a_404(self):
        up = json.loads(self.upload()[2])["upload"]
        body = {"practice": "morning", "upload": up, "client_id": "rec-1"}
        a = self.ok("POST", "/api/v1/entries", body, want=201)
        b = self.ok("POST", "/api/v1/entries", body, want=200)
        self.assertEqual(a, b)
        self.assertEqual(self.entry("2026-10-15-morning").count("![[2026-10-15-morning.webm]]"), 1)
        up = json.loads(self.upload()[2])["upload"]
        body = {"text": "a caption", "upload": up, "client_id": "rec-2"}
        a = self.ok("POST", "/api/v1/entries/2026-10-14-evening/blocks", body, want=201)
        b = self.ok("POST", "/api/v1/entries/2026-10-14-evening/blocks", body, want=200)
        self.assertEqual(a, b)
        self.assertEqual(self.entry("2026-10-14-evening").count("a caption"), 1)
        up = json.loads(self.upload()[2])["upload"]
        body = {"text": "on a note", "upload": up, "client_id": "rec-3"}
        a = self.ok("POST", "/api/v1/entries/2026-10-15-j/blocks", body, want=201)   # no ledger prompt: web_blocks
        b = self.ok("POST", "/api/v1/entries/2026-10-15-j/blocks", body, want=200)
        self.assertEqual(a, b)
        self.assertEqual(self.entry("2026-10-15-j").count("on a note"), 1)

    def test_a_forgotten_untracked_retry_is_404_not_a_new_block_without_its_audio(self):
        up = json.loads(self.upload()[2])["upload"]
        body = {"text": "on a note", "upload": up, "client_id": "rec-old"}
        self.ok("POST", "/api/v1/entries/2026-10-15-j/blocks", body, want=201)
        with ledger.locked(self.cfg.ledger_path) as data:   # its idempotency record has expired
            data["web_blocks"]["rec-old"]["at"] = (datetime.now(timezone.utc) - capture.WEB_BLOCK_KEEP * 2).isoformat()
        before = checksum(self.cfg.root)
        self.refused("POST", "/api/v1/entries/2026-10-15-j/blocks", body, want=404)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_block_record_does_not_excuse_a_missing_upload_on_a_new_entry(self):
        up = json.loads(self.upload()[2])["upload"]
        self.ok("POST", "/api/v1/entries/2026-10-15-j/blocks", {"text": "x", "upload": up, "client_id": "shared"}, want=201)
        before = checksum(self.cfg.root)    # POST /entries dedupes only against captures: it must not write anew
        self.refused("POST", "/api/v1/entries", {"practice": "morning", "upload": up, "client_id": "shared"}, want=404)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_retry_that_overlaps_the_original_waits_for_it(self):
        up = json.loads(self.upload()[2])["upload"]
        uploads.claim(self.cfg, up)          # the original request has claimed the upload ...
        held, done = threading.Event(), threading.Event()

        def original():                      # ... and is writing, holding the ledger lock
            with ledger.locked(self.cfg.ledger_path) as data:
                held.set()
                time.sleep(0.6)
                data["prompts"][0].setdefault("captures", []).append(
                    {"at": NOW.isoformat(), "rule": "web", "source": "audio", "client_id": "race-1"})
            done.set()
        t = threading.Thread(target=original)
        t.start()
        self.assertTrue(held.wait(5))
        got = self.ok("POST", "/api/v1/entries", {"practice": "morning", "upload": up, "client_id": "race-1"}, want=200)
        self.assertTrue(done.is_set())      # it answered after the original finished, not before
        self.assertEqual(got["rule"], "web")
        t.join()

    def test_an_unknown_upload_without_a_recorded_client_id_is_still_404(self):
        self.refused("POST", "/api/v1/entries", {"practice": "morning", "upload": "0" * 32, "client_id": "never"}, want=404)

    def test_a_retried_block_returns_the_key_it_first_got(self):
        for stem in ("2026-10-14-evening", "2026-10-15-j"):   # one with a ledger prompt, one without
            with self.subTest(stem):
                a = self.ok("POST", f"/api/v1/entries/{stem}/blocks", {"text": "A.", "client_id": f"c-a-{stem}"}, want=201)
                b = self.ok("POST", f"/api/v1/entries/{stem}/blocks", {"text": "B.", "client_id": f"c-b-{stem}"}, want=201)
                self.assertNotEqual(a["key"], b["key"])
                again = self.ok("POST", f"/api/v1/entries/{stem}/blocks", {"text": "A.", "client_id": f"c-a-{stem}"}, want=200)
                self.assertEqual(again, a)


class HisFileTest(WriteCase):
    """The web app writes only to files the engine wrote; a page of his that happens to carry an entry's name is
    readable and left alone."""

    def mine(self, name, text):
        (self.cfg.root / name).write_text(text, encoding="utf-8")
        return checksum(self.cfg.root)

    def test_a_block_is_refused_into_his_hand_written_ritual_named_file(self):
        before = self.mine("2026-10-13-evening.md", "My own page about today.\n")
        self.assertEqual(self.refused("POST", "/api/v1/entries/2026-10-13-evening/blocks", {"text": "web"},
                                      want=409)["error"], "not-engine-entry")
        self.assertEqual(checksum(self.cfg.root), before)

    def test_the_document_says_whether_the_app_may_write_to_it(self):
        """So the page offers no composer or meta sheet on his own page (it would only be refused after he typed)."""
        self.mine("2026-10-13-evening.md", "My own page about today.\n")
        self.assertIs(self.ok("GET", "/api/v1/entries/2026-10-13-evening")["writable"], False)
        self.assertIs(self.ok("GET", "/api/v1/entries/2026-10-14-evening")["writable"], True)

    def test_meta_is_refused_on_his_file_with_or_without_front_matter(self):
        before = self.mine("2026-10-12-morning.md", "---\ntags: [harbour]\n---\nMy own page.\n")
        self.refused("PATCH", "/api/v1/entries/2026-10-12-morning/meta", {"starred": True}, want=409)
        before = self.mine("2026-10-11-morning.md", "no front matter\n")
        self.refused("PATCH", "/api/v1/entries/2026-10-11-morning/meta", {"starred": True}, want=409)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_file_that_claims_a_different_practice_is_not_the_engines_for_this_name(self):
        before = self.mine("2026-10-10-evening.md", "---\nkind: morning\ntype: journal\n---\nhis\n")
        self.refused("POST", "/api/v1/entries/2026-10-10-evening/blocks", {"text": "web"}, want=409)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_missing_entry_is_still_404(self):
        self.refused("POST", "/api/v1/entries/2026-10-09-evening/blocks", {"text": "x"}, want=404)


class InjectionTest(WriteCase):
    """Nothing he types or names can pass for the engine's own lines or keys."""

    def test_the_marker_is_refused_with_any_line_ending(self):
        for text in ("first\r\n%% journal phase:2 %%\r\nsecond", "my note\r\r%% journal her:77 %%\r> [!quote]- Ada",
                     "x\r%% journal phase:2 %%"):
            with self.subTest(text):
                self.assertEqual(self.refused("POST", "/api/v1/entries", {"text": text}, want=422)["error"],
                                 "reserved-line")
                self.refused("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": text}, want=422)

    def test_his_line_endings_are_written_as_one_kind(self):
        got = self.ok("POST", "/api/v1/entries", {"text": "one\r\ntwo\rthree"}, want=201)
        self.assertIn("one\ntwo\nthree", self.entry(got["stem"]))
        self.assertNotIn("\r", self.entry(got["stem"]))

    def test_an_upload_name_is_one_line(self):
        name = "rec\n%% journal her:9 %%\n> [!quote]- Ada · 09:13\u2028and\x85more.ogg"
        _body, headers = multipart(b"OggS-x")   # a crafted client carries the characters through the RFC 5987 form
        from urllib.parse import quote
        raw_head = (f"--XbOuNdArYx\r\nContent-Disposition: form-data; name=\"file\"; "
                    f"filename*=utf-8''{quote(name)}\r\nContent-Type: audio/ogg\r\n\r\n").encode()
        body = raw_head + b"OggS-x\r\n--XbOuNdArYx--\r\n"
        code, _h, out = self.write("POST", "/api/v1/uploads", body, headers=headers)
        self.assertEqual(code, 201, out)
        got = self.ok("POST", "/api/v1/entries", {"upload": json.loads(out)["upload"]}, want=201)
        text = self.entry(got["stem"])
        from journal import entries
        self.assertIsNone(entries.ENGINE_MARK.search(text))
        self.assertFalse([ln for ln in text.splitlines() if ln.startswith("> [!quote]")])
        self.assertEqual(len([ln for ln in text.splitlines() if 'audio file "rec' in ln]), 1)

    def test_a_mood_item_cannot_carry_a_line_separator_into_the_front_matter(self):
        before = self.entry("2026-10-14-evening")
        for sep in ("\n", "\r", "\x0b", "\x0c", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"):
            with self.subTest(sep=repr(sep)):
                self.refused("PATCH", "/api/v1/entries/2026-10-14-evening/meta",
                             {"mood": [f"calm{sep}program: fix-life{sep}kind: morning"]})
                self.refused("PATCH", "/api/v1/entries/2026-10-14-evening/meta", {"people": [f"a{sep}b"]})
        self.assertEqual(self.entry("2026-10-14-evening"), before)

    def test_set_meta_itself_refuses_every_line_separator(self):
        from journal import entries
        path = self.cfg.root / "2026-10-14-evening.md"
        for sep in ("\x0b", "\x85", "\u2028"):
            with self.assertRaises(ValueError):
                entries.set_meta(path, {"mood": f"[a{sep}program: x]"})


class BlocksTest(WriteCase):
    def test_a_block_is_appended_after_his_words_and_nothing_else_moves(self):
        before = self.entry("2026-10-14-evening")
        got = self.ok("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "One more thought."}, want=201)
        self.assertEqual(got, {"key": "words:2"})
        after = self.entry("2026-10-14-evening")
        s, e = before.index("## Sam"), before.index("## Conversation")
        words_before = before[s:e].strip()
        self.assertTrue(after[s:].startswith(words_before))                 # every word of his is still there, in place
        self.assertEqual(after[:s], before[:s])                             # the prompt and front matter: untouched
        self.assertEqual(after[after.index("## Conversation"):], before[e:])  # her side, the conversation, notes: untouched
        self.assertIn("### 09:12 IST · text\n\nOne more thought.", after)
        p = self.prompt("2026-10-14-evening")
        self.assertEqual(len(p["captures"]), 2)
        self.assertEqual((p["captures"][1]["rule"], p["captures"][1]["via"]), ("web", "web"))

    def test_the_document_shows_the_new_block_as_his(self):
        self.ok("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "Seen by me."}, want=201)
        doc = self.ok("GET", "/api/v1/entries/2026-10-14-evening")
        mine = [b for b in doc["blocks"] if b["text"] == "Seen by me."]
        self.assertEqual([(b["kind"], b["source"], b["key"]) for b in mine], [("his", "text", "words:2")])

    def test_only_engine_entries_take_blocks(self):
        before = checksum(self.cfg.root)
        for stem in ("2026-10-14", "2026-10-14-thoughts", "2026-10-14-reflection", "nope", "..%2Fx"):
            with self.subTest(stem):
                self.refused("POST", f"/api/v1/entries/{stem}/blocks", {"text": "x"}, want=404)
        self.assertEqual(checksum(self.cfg.root), before)

    def test_an_entry_of_a_run_that_is_gone_has_no_open_phase(self):
        before = checksum(self.cfg.root)
        got = self.refused("POST", "/api/v1/entries/2026-10-15-fix-life-s01/blocks", {"text": "x", "phase": 3}, want=409)
        self.assertEqual(got["error"], "phase-not-open")
        self.refused("POST", "/api/v1/entries/2026-10-15-fix-life-s01/blocks", {"text": "x"}, want=409)
        self.refused("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "x", "phase": 1})
        self.assertEqual(checksum(self.cfg.root), before)

    def test_a_block_is_changed_only_through_its_own_route(self):
        """The entry and the block collection take no PUT/PATCH/DELETE; one block of his takes PATCH and DELETE
        (``tests/test_serve_blocks.py``), nothing takes PUT."""
        before = checksum(self.cfg.root)
        for method in ("PUT", "PATCH", "DELETE"):
            for path in ("/api/v1/entries/2026-10-14-evening/blocks", "/api/v1/entries/2026-10-14-evening", "/api/v1/entries"):
                with self.subTest(method=method, path=path):
                    self.assertIn(self.write(method, path, {"text": "rewritten"})[0], (404, 405))
        self.assertEqual(self.write("PUT", "/api/v1/entries/2026-10-14-evening/blocks/words:0", {"text": "rewritten"})[0], 405)
        self.assertEqual(checksum(self.cfg.root), before)
        self.assertNotIn("rewritten", self.entry("2026-10-14-evening"))


class MetaTest(WriteCase):
    def test_meta_is_set_in_the_front_matter_and_his_words_stay_byte_for_byte(self):
        stem = "2026-10-14-evening"
        before = self.entry(stem)
        mtime = self.ok("GET", f"/api/v1/entries/{stem}")["mtime"]
        got = self.ok("PATCH", f"/api/v1/entries/{stem}/meta",
                      {"mood": ["calm", "a bit tired"], "people": ["Ada"], "starred": True, "mtime": mtime})
        self.assertEqual(got["meta"], {"mood": ["calm", "a bit tired"], "people": ["Ada"], "starred": True})
        after = self.entry(stem)
        self.assertEqual(after[after.index("\n---\n\n# Evening"):], before[before.index("\n---\n\n# Evening"):])
        self.assertIn("mood: [calm, a bit tired]", after)
        doc = self.ok("GET", f"/api/v1/entries/{stem}")
        self.assertEqual(doc["meta"], got["meta"])
        self.assertEqual(doc["mtime"], got["mtime"])
        listed = [i for i in self.ok("GET", "/api/v1/entries")["items"] if i["stem"] == stem][0]
        self.assertEqual(listed["meta"]["mood"], ["calm", "a bit tired"])
        again = self.ok("PATCH", f"/api/v1/entries/{stem}/meta", {"mood": [], "starred": False, "mtime": got["mtime"]})
        self.assertEqual(again["meta"], {"people": ["Ada"]})
        self.assertNotIn("mood:", self.entry(stem))

    def test_block_style_lists_from_obsidian_are_read_and_a_star_alone_leaves_them(self):
        """Obsidian's Properties editor writes ``mood:`` then ``  - calm`` lines. The sheet must open with them, and a
        save that changes only the star must not touch them (it used to read them as empty and delete them)."""
        stem = "2026-10-14-evening"
        path = self.cfg.root / f"{stem}.md"
        text = path.read_text(encoding="utf-8")
        head, rest = text.split("\n---\n", 1)
        path.write_text(head + '\nmood:\n  - calm\n  - "tired: a bit"\n  - \'it\'\'s fine\'\npeople:\n- Sam\n---\n' + rest,
                        encoding="utf-8")
        doc = self.ok("GET", f"/api/v1/entries/{stem}")
        self.assertEqual(doc["meta"]["mood"], ["calm", "tired: a bit", "it's fine"])
        self.assertEqual(doc["meta"]["people"], ["Sam"])
        listed = [i for i in self.ok("GET", "/api/v1/entries")["items"] if i["stem"] == stem][0]
        self.assertEqual(listed["meta"]["people"], ["Sam"])
        got = self.ok("PATCH", f"/api/v1/entries/{stem}/meta", {"starred": True, "mtime": doc["mtime"]})
        self.assertEqual(got["meta"]["mood"], ["calm", "tired: a bit", "it's fine"])
        self.assertEqual(got["meta"]["people"], ["Sam"])
        self.assertIn("  - calm\n", path.read_text(encoding="utf-8"))
        self.assertIn("\n- Sam\n", path.read_text(encoding="utf-8"))

    def test_mood_and_people_stay_valid_yaml_whatever_the_item_starts_with(self):
        import yaml
        stem = "2026-10-14-evening"
        items = ["#tired", "*Sam", '"quoted', "@home", "%x", "!tag", "a: b", "{x}", "- dash", "12:30", "true", "100",
                 "2026-10-14", "0b101", "0x1F", "e5", "back\\slash", "it's fine", "plain", "a bit tired", "ünï", "O'Neil (Sam)", "'single'"]
        got = self.ok("PATCH", f"/api/v1/entries/{stem}/meta", {"mood": items, "people": items[:3]})
        self.assertEqual(got["meta"]["mood"], items)                  # read back as he typed them
        self.assertEqual(got["meta"]["people"], items[:3])
        text = self.entry(stem)
        fm = yaml.safe_load(text.split("---\n")[1])
        self.assertEqual((fm["mood"], fm["people"]), (items, items[:3]))      # and by a real YAML reader
        self.assertEqual((fm["kind"], fm["type"]), ("evening", "journal"))   # the rest of the properties survive
        self.assertIn("mood: [#tired, ", text.replace('"#tired"', "#tired"))   # (shape only: quoted where needed)
        self.assertIn("a bit tired", text.split("mood:")[1].splitlines()[0])   # plain items stay plain
        self.assertNotIn('"a bit tired"', text)
        self.assertEqual(self.ok("GET", f"/api/v1/entries/{stem}")["meta"]["mood"], items)

    def test_what_the_engine_owns_cannot_be_changed_and_drift_is_a_conflict(self):
        stem = "2026-10-14-evening"
        before = checksum(self.cfg.root)
        for key in ("kind", "date", "practice", "program", "session", "run", "type", "stem", "title"):
            with self.subTest(key):
                self.assertEqual(self.refused("PATCH", f"/api/v1/entries/{stem}/meta", {key: "x"})["error"], "bad-request")
        for body in ({}, {"mood": "calm"}, {"mood": ["a,b"]}, {"mood": ["x" * 61]}, {"mood": [1]}, {"starred": "yes"},
                     {"people": ["line\nbreak"]}, {"mood": ["ok"], "mtime": "now"}):
            with self.subTest(body):
                self.refused("PATCH", f"/api/v1/entries/{stem}/meta", body)
        self.refused("PATCH", f"/api/v1/entries/{stem}/meta", {"starred": True, "mtime": 1.0}, want=409)
        self.refused("PATCH", "/api/v1/entries/2026-10-14/meta", {"starred": True}, want=404)
        self.assertEqual(checksum(self.cfg.root), before)


class LibraryWriteTest(WriteCase):
    NEW = ("---\nkind: practice\nid: stretching\ntitle: Stretching\nschedule.time: 07:30\n---\n"
           "## Body\n- Which part is tight?\n- How long today?\n")

    def path(self, pid):
        return self.cfg.practices_dir / f"{pid}.md"

    def test_a_new_practice_is_saved_canonically_and_is_due_from_the_next_tick(self):
        got = self.ok("PUT", "/api/v1/library/stretching", {"raw": self.NEW})
        self.assertEqual(got["definition"]["source"], "user")
        self.assertEqual(got["problems"], [])
        text = self.path("stretching").read_text(encoding="utf-8")
        self.assertIn("schedule.time: 07:30", text)
        self.assertIn("opener.mode: companion", text)          # canonical: the defaults are written out
        self.assertEqual(got["raw"], text)
        self.assertIsInstance(got["mtime"], float)
        rows = {r["id"]: r for r in self.ok("GET", "/api/v1/library")}
        self.assertEqual(rows["stretching"]["source"], "user")
        from journal import library
        self.assertIn("stretching", [p.id for p in library.scheduled(self.cfg)])

    def test_a_definition_in_json_is_saved_the_same_way(self):
        d = {"id": "walking", "title": "Walking", "schedule": [{"time": "18:00", "weekday": 2}],
             "sections": [{"title": None, "questions": [{"text": "Where to?"}]}]}
        got = self.ok("PUT", "/api/v1/library/walking", {"definition": d})
        self.assertEqual(got["definition"]["schedule"], [{"time": "18:00", "weekday": 2}])
        self.assertIn("schedule.weekday: 2", self.path("walking").read_text(encoding="utf-8"))

    def test_a_definition_that_does_not_parse_writes_nothing(self):
        before = sorted(p.name for p in self.cfg.practices_dir.iterdir())
        gratitude = self.path("gratitude").read_bytes()
        mtime = self.ok("GET", "/api/v1/library/gratitude")["mtime"]
        bad = [{"raw": "no front matter at all"}, {"raw": self.NEW.replace("07:30", "25:00")},
               {"raw": self.NEW.replace("id: stretching", "id: other")},
               {"raw": self.NEW.replace("stretching", "level-10")},
               {"raw": self.NEW.replace("stretching", "start")},
               {"definition": {"id": "stretching", "title": "x", "sections": []}},
               {"definition": {"id": "stretching", "title": ["x"], "sections": "no"}}]
        for body in bad:
            with self.subTest(body):
                got = self.refused("PUT", "/api/v1/library/stretching", {**body, "mtime": None}, want=422)
                self.assertTrue(got["problems"])
        got = self.refused("PUT", "/api/v1/library/gratitude", {"raw": "---\nkind: practice\nid: gratitude\n---\n",
                                                                "mtime": mtime}, want=422)
        self.assertIn("title is required", " ".join(got["problems"]))
        self.assertEqual(self.path("gratitude").read_bytes(), gratitude)
        self.assertEqual(sorted(p.name for p in self.cfg.practices_dir.iterdir()), before)
        self.assertEqual(sorted(p.name for p in self.cfg.practices_dir.glob(".*") if p.name != ".library.lock"), [])

    def test_a_window_a_timedelta_cannot_hold_is_422_and_one_bad_prompt_cannot_stop_capture(self):
        raw = self.NEW.replace("schedule.time: 07:30", "schedule.time: 07:30\ncollapse_hours: 1e12")
        got = self.refused("PUT", "/api/v1/library/stretching", {"raw": raw}, want=422)
        self.assertTrue(any("collapse_hours" in p for p in got["problems"]), got)
        d = {"id": "walking", "title": "Walking", "collapse_hours": 1e12,
             "sections": [{"title": None, "questions": [{"text": "Where to?"}]}]}
        self.assertTrue(any("collapse_hours" in p for p in self.refused(
            "PUT", "/api/v1/library/walking", {"definition": d}, want=422)["problems"]))
        self.assertFalse(self.path("stretching").exists() or self.path("walking").exists())
        # a prompt that already carries such a window (a hand-edited file, before the bound) must not raise in
        # the hook's capture, the web's, or the fold: it is held to the longest window there is
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({
                "id": "2026-10-15-stretching", "stem": "2026-10-15-stretching", "kind": "stretching",
                "local_date": "2026-10-15", "chat_id": self.cfg.chat_id, "message_id": "650",
                "sent_at": datetime(2026, 10, 15, 7, 30, tzinfo=IST).isoformat(), "opener": "Stretch",
                "collapse_hours": 1e12})
            self.assertLessEqual(capture.window(self.cfg, data["prompts"][-1]), timedelta(days=366))
        self.assertIsNotNone(capture.capture(self.cfg, self.cfg.chat_id, None, "words", NOW, "text", "777"))
        self.ok("POST", "/api/v1/entries", {"practice": "morning", "text": "web words"}, want=201)

    def test_a_built_in_is_edited_in_place_and_reset_to_the_default(self):
        """BUILTIN-EDIT (Theo, 2026-10-26): a save edits the built-in itself, under its own id; the packaged file is
        never touched; Reset (DELETE) puts the shipped one back."""
        read = self.ok("GET", "/api/v1/library/evening")
        packaged = Path(read["definition"]["path"])
        shipped = packaged.read_bytes()
        self.assertEqual((read["definition"]["source"], read["override"]), ("builtin", None))
        edited = read["raw"].replace("annotate: true", "annotate: false")
        got = self.ok("PUT", "/api/v1/library/evening", {"raw": edited, "mtime": read["mtime"]})   # the package's mtime: ignored
        self.assertEqual(got["definition"]["source"], "fork")
        self.assertFalse(got["definition"]["annotate"])
        self.assertEqual(got["override"]["default_changed"], False)
        self.assertTrue(got["override"]["in_force"])
        self.assertEqual(packaged.read_bytes(), shipped)                     # the packaged file is untouched
        self.assertTrue(self.path("evening").is_file())                     # his file, same id, same name
        lib = library.load(self.cfg)
        self.assertEqual([p.id for p in lib.practices.values()].count("evening"), 1)   # no duplicate
        self.assertEqual(lib.get("evening").schedule, lib.builtins["evening"].schedule)
        row = {r["id"]: r for r in self.ok("GET", "/api/v1/library")}["evening"]
        self.assertEqual((row["source"], row["default_changed"]), ("fork", False))
        # his file is saved against its own mtime from now on
        self.assertEqual(self.refused("PUT", "/api/v1/library/evening", {"raw": edited, "mtime": read["mtime"]},
                                      want=409)["error"], "mtime")
        again = self.ok("PUT", "/api/v1/library/evening", {"raw": edited.replace("Evening journal", "Night"),
                                                          "mtime": got["mtime"]})
        self.assertEqual(again["definition"]["title"], "Night")
        self.ok("DELETE", "/api/v1/library/evening", want=204)
        back = self.ok("GET", "/api/v1/library/evening")
        self.assertEqual((back["definition"]["source"], back["override"]), ("builtin", None))
        self.assertTrue(back["definition"]["annotate"])
        self.assertFalse(self.path("evening").exists())
        self.assertEqual(self.refused("DELETE", "/api/v1/library/evening", want=409)["error"], "builtin")

    def test_turning_a_built_in_off_is_one_save(self):
        read = self.ok("GET", "/api/v1/library/morning")
        d = {**read["definition"], "enabled": False}
        got = self.ok("PUT", "/api/v1/library/morning", {"definition": d, "mtime": None})
        self.assertEqual((got["definition"]["source"], got["definition"]["enabled"]), ("fork", False))
        self.assertNotIn("morning", [p.id for p in library.scheduled(self.cfg)])

    def test_a_built_in_edit_keeps_the_window_it_was_folding_on(self):
        """A built-in's prompt folds on ``windows.collapse_hours``; the definition the editor starts from says so,
        so saving it unchanged does not move the window to the file's 6."""
        self.cfg.collapse_hours = 9.0
        read = self.ok("GET", "/api/v1/library/evening")
        self.assertEqual(read["definition"]["collapse_hours"], 9.0)
        got = self.ok("PUT", "/api/v1/library/evening", {"definition": read["definition"]})
        self.assertEqual(got["definition"]["collapse_hours"], 9.0)
        self.assertIn("collapse_hours: 9", self.path("evening").read_text(encoding="utf-8"))

    def test_the_default_changed_since_he_edited_is_said_and_shown(self):
        read = self.ok("GET", "/api/v1/library/evening")
        self.ok("PUT", "/api/v1/library/evening", {"raw": read["raw"].replace("annotate: true", "annotate: false")})
        view = self.ok("GET", "/api/v1/library/evening/default")
        self.assertEqual((view["definition"]["source"], view["diff"]["since"]), ("builtin", None))
        self.assertIn("-annotate: true\n+annotate: false", view["diff"]["yours"])
        self.assertTrue(view["definition"]["annotate"])
        # a later release changes the shipped file: the record of the default he began from says so
        with library.held(self.cfg.practices_dir) as state:
            rec = state["defaults"]["evening"]
            rec["files"]["evening.md"] = rec["files"]["evening.md"].replace("title: Evening journal", "title: Evening")
            rec["sha"] = library._fingerprint(rec["files"])
        got = self.ok("GET", "/api/v1/library/evening")
        self.assertEqual(got["override"]["default_changed"], True)
        self.assertFalse(got["definition"]["annotate"])                     # his version is kept
        self.assertTrue({r["id"]: r for r in self.ok("GET", "/api/v1/library")}["evening"]["default_changed"])
        since = self.ok("GET", "/api/v1/library/evening/default")["diff"]["since"]
        self.assertIn("-title: Evening\n+title: Evening journal", since)
        self.refused("GET", "/api/v1/library/gratitude/default", want=404)
        self.refused("GET", "/api/v1/library/nope/default", want=404)

    def test_an_edit_made_before_the_record_is_kept_reads_as_not_known(self):
        self.path("evening").write_text(library.builtins()["evening"].raw, encoding="utf-8")
        got = self.ok("GET", "/api/v1/library/evening")
        self.assertEqual((got["definition"]["source"], got["override"]["default_changed"]), ("fork", None))
        self.assertIsNone(self.ok("GET", "/api/v1/library/evening/default")["diff"]["since"])

    def test_a_file_of_his_that_does_not_load_is_shown_and_can_be_reset(self):
        self.path("evening").write_text("---\nkind: practice\nid: evening\n---\n", encoding="utf-8")
        got = self.ok("GET", "/api/v1/library/evening")
        self.assertEqual(got["definition"]["source"], "builtin")           # the built-in is used meanwhile
        self.assertFalse(got["override"]["in_force"])
        self.assertTrue(any("title is required" in p for p in got["problems"]), got["problems"])
        self.ok("DELETE", "/api/v1/library/evening", want=204)
        self.assertFalse(self.path("evening").exists())

    def test_fork_still_copies_a_built_in(self):
        forked = self.ok("POST", "/api/v1/library/evening/fork", want=201)
        self.assertEqual(forked["definition"]["source"], "fork")
        self.assertEqual(self.refused("POST", "/api/v1/library/evening/fork", want=409)["error"], "already-forked")
        self.refused("POST", "/api/v1/library/gratitude/fork", want=409)          # his own, not a built-in
        self.refused("POST", "/api/v1/library/nope/fork", want=404)
        self.assertEqual(self.ok("POST", "/api/v1/library/conversation-prep/fork", want=201)["definition"]["source"],
                         "fork")
        self.assertTrue((self.cfg.practices_dir / "conversation-prep.md").is_file())

    def test_mtime_drift_is_409_and_the_other_edit_survives(self):
        read = self.ok("GET", "/api/v1/library/gratitude")
        self.path("gratitude").write_text(read["raw"].replace("Three things", "Four things"), encoding="utf-8")
        os.utime(self.path("gratitude"), (read["mtime"] + 50, read["mtime"] + 50))
        got = self.refused("PUT", "/api/v1/library/gratitude", {"raw": read["raw"].replace("Daily", "Nightly"),
                                                                "mtime": read["mtime"]}, want=409)
        self.assertEqual(got["error"], "mtime")
        self.assertIn("Four things", self.path("gratitude").read_text(encoding="utf-8"))
        self.assertNotIn("Nightly", self.path("gratitude").read_text(encoding="utf-8"))
        self.refused("PUT", "/api/v1/library/gratitude", {"raw": read["raw"]})                    # no mtime at all
        self.refused("PUT", "/api/v1/library/gratitude", {"raw": read["raw"], "definition": {}, "mtime": 1})
        self.refused("PUT", "/api/v1/library/gratitude", {"mtime": 1})

    def test_validate_is_a_dry_run(self):
        names = sorted(p.name for p in self.cfg.practices_dir.iterdir())
        got = self.ok("PUT", "/api/v1/library/stretching?validate=1", {"raw": self.NEW})
        self.assertTrue(got["valid"])
        self.assertIn("opener.plain:", got["raw"])
        bad = self.ok("PUT", "/api/v1/library/stretching?validate=1", {"raw": "---\nkind: practice\nid: stretching\n---\n"})
        self.assertEqual((bad["valid"], bad["raw"]), (False, None))
        self.assertTrue(bad["problems"])
        mixed = self.ok("PUT", "/api/v1/library/stretching?validate=1",
                        {"definition": {"id": "stretching", "title": "S", "sections": [{"questions": [{"text": "Q?"}]}]}})
        self.assertTrue(mixed["valid"])
        self.assertEqual(sorted(p.name for p in self.cfg.practices_dir.iterdir()), names)

    def test_delete_reverts_a_fork_and_removes_a_practice_of_his_own(self):
        self.ok("POST", "/api/v1/library/evening/fork", want=201)
        self.ok("DELETE", "/api/v1/library/evening", want=204)
        self.assertEqual(self.ok("GET", "/api/v1/library/evening")["definition"]["source"], "builtin")
        self.ok("DELETE", "/api/v1/library/gratitude", want=204)
        self.refused("GET", "/api/v1/library/gratitude", want=404)
        self.refused("DELETE", "/api/v1/library/gratitude", want=404)
        self.assertEqual(self.refused("DELETE", "/api/v1/library/morning", want=409)["error"], "builtin")
        self.refused("DELETE", "/api/v1/library/..", want=404)

    # (DELETE's guard for a program's run is exercised against a real run in test_serve_programs.DeleteGuardTest: a
    # practice has no runs, so a fake run under a practice's id would test nothing a user can reach.)

    def test_a_practice_is_sent_by_a_spawned_journal_send(self):
        got = self.ok("POST", "/api/v1/library/gratitude/send", want=202)
        self.assertEqual(got, {"spawned": True, "log": str(self.cfg.state_dir / "manual-trigger.log")})
        self.assertEqual(self.spawned, [["send", "gratitude"]])
        self.refused("POST", "/api/v1/library/nope/send", want=404)
        self.refused("POST", "/api/v1/library/..%2Fx/send", want=404)
        off = self.NEW.replace("---\n## Body", "enabled: false\n---\n## Body")
        self.ok("PUT", "/api/v1/library/stretching", {"raw": off})
        self.assertEqual(self.refused("POST", "/api/v1/library/stretching/send", want=409)["error"], "disabled")
        self.assertEqual(self.spawned, [["send", "gratitude"]])

    def test_a_spawn_that_cannot_start_is_503(self):
        def boom(cfg, args):
            raise OSError("no fork")
        self.app.spawn = boom
        self.assertEqual(self.refused("POST", "/api/v1/library/gratitude/send", want=503)["error"], "engine-unavailable")


class SpawnTest(WriteCase):
    def test_the_spawn_runs_this_server_s_journal_with_its_config_and_logs_to_the_state_dir(self):
        self.cfg.config_file = Path(self.tmp.name) / "cfg.json"
        self.cfg.hermes_home = Path(self.tmp.name) / "home"
        with mock.patch.object(writes.subprocess, "Popen") as popen:
            log = writes.spawn_journal(self.cfg, ["send", "evening"])
        argv = popen.call_args.args[0]
        self.assertEqual(argv[1:3], ["-m", "journal"])
        self.assertEqual(argv[3:], ["--config", str(self.cfg.config_file), "--hermes-home", str(self.cfg.hermes_home),
                                    "send", "evening"])
        kw = popen.call_args.kwargs
        self.assertTrue(kw["start_new_session"])
        self.assertEqual(Path(log), self.cfg.state_dir / "manual-trigger.log")
        self.assertIn(str(Path(writes.__file__).resolve().parents[2]), kw["env"]["PYTHONPATH"])
        # the child's mirror into the chat session refuses unless HERMES_HOME is the journal's profile home
        self.assertEqual(kw["env"]["HERMES_HOME"], str(self.cfg.hermes_home))


class SettingsWriteTest(WriteCase):
    def setUp(self):
        super().setUp()
        self.file = Path(self.tmp.name) / "config.json"
        self.file.write_text(json.dumps({"root": str(self.cfg.root), "windows": {"grace_hours": 3}}), encoding="utf-8")
        self.cfg.config_file = self.file
        self.cfg.origins = {"root": "file", "windows": "file"}
        self.cfg.hermes_home = None
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(Path(self.tmp.name) / "xdg")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def data(self):
        return json.loads(self.file.read_text(encoding="utf-8"))

    def test_allowed_settings_are_written_to_the_file_that_owns_them(self):
        got = self.ok("PATCH", "/api/v1/settings", {"windows": {"collapse_hours": 5}, "conversations.quiet_minutes": 30,
                                                    "reflections": {"day": {"enabled": False}},
                                                    "programs.settle_minutes": 3, "timezone": "Europe/London"})
        self.assertEqual(got["written"], {"windows.collapse_hours": "file", "conversations.quiet_minutes": "file",
                                          "reflections.day.enabled": "file", "programs.settle_minutes": "file",
                                          "timezone": "file"})
        d = self.data()
        self.assertEqual(d["windows"], {"grace_hours": 3, "collapse_hours": 5})
        self.assertEqual((d["conversations"], d["programs"], d["timezone"], d["root"]),
                         ({"quiet_minutes": 30}, {"settle_minutes": 3}, "Europe/London", str(self.cfg.root)))
        self.assertEqual(d["reflections"], {"day": {"enabled": False}})

    def test_keys_the_web_never_writes_are_400_and_nothing_is_written(self):
        before = self.file.read_bytes()
        for body in ({"telegram": {"chat_id": "1"}}, {"guard": {"enabled": False}}, {"hermes": {}}, {"agent": {}},
                     {"serve": {"allow_write": True}}, {"root": "/elsewhere"}, {"state_dir": "/x"},
                     {"practices_dir": "/x"}, {"serve.token_command": "x"}, {"windows": {"collapse_hours": 5}, "root": "/x"},
                     {"conversations": {"opt_out_prefix": "/q"}}, {"windows": {"nope": 1}}):
            with self.subTest(body):
                self.assertEqual(self.refused("PATCH", "/api/v1/settings", body)["error"], "key-not-allowed")
        self.assertEqual(self.file.read_bytes(), before)

    def test_bad_values_are_422_and_empty_is_400(self):
        before = self.file.read_bytes()
        self.assertTrue(self.refused("PATCH", "/api/v1/settings", {"timezone": "Not/AZone"}, want=422)["problems"])
        self.refused("PATCH", "/api/v1/settings", {"windows": {"collapse_hours": -2}}, want=422)
        self.refused("PATCH", "/api/v1/settings", {})
        self.assertEqual(self.file.read_bytes(), before)

    def test_values_that_stop_the_rituals_or_overflow_are_refused(self):
        before = self.file.read_bytes()
        for key, value in (("windows.grace_hours", 0), ("windows.grace_hours", 0.1), ("windows.grace_hours", 25),
                           ("windows.collapse_hours", 1e300), ("windows.collapse_hours", 1e-9),
                           ("windows.collapse_hours", 73), ("windows.annotate_after_minutes", 1e300),
                           ("windows.opener_budget_seconds", 901), ("windows.annotate_after_minutes", 15.5), ("conversations.quiet_minutes", 1e300),
                           ("programs.settle_minutes", 100000), ("today.cache_minutes", 1e300),
                           ("today.max_chars", 10**9)):
            with self.subTest(key, value=value):
                self.refused("PATCH", "/api/v1/settings", {key: value}, want=422)
        self.assertEqual(self.file.read_bytes(), before)
        self.ok("PATCH", "/api/v1/settings", {"windows.grace_hours": 0.25, "windows.collapse_hours": 72})

    def test_a_change_to_one_today_key_keeps_the_block_off_when_the_file_says_false(self):
        self.file.write_text(json.dumps({"root": str(self.cfg.root), "today": False}), encoding="utf-8")
        self.cfg.origins["today"] = "file"
        self.ok("PATCH", "/api/v1/settings", {"today.max_chars": 900})
        self.assertEqual(self.data()["today"], {"enabled": False, "max_chars": 900})
        self.assertFalse(jconfig.load(self.file).today["enabled"])

    def test_a_hermes_owned_key_is_409_with_the_cli_hint(self):
        self.cfg.origins["windows"] = "hermes"
        before = self.file.read_bytes()
        got = self.refused("PATCH", "/api/v1/settings", {"timezone": "UTC", "windows": {"collapse_hours": 4}}, want=409)
        self.assertEqual(got["error"], "not-writable")
        self.assertIn("hermes config set plugins.entries.journal.settings.windows.collapse_hours", got["detail"])
        self.assertEqual(self.file.read_bytes(), before)   # the timezone beside it was not written either

    def test_the_settings_say_today_is_off_when_the_file_says_false(self):
        from journal.serve import api
        self.cfg.raw["today"] = False
        self.cfg.today = {"enabled": False}
        got = api.redacted(self.cfg)["today"]
        self.assertEqual((got["enabled"], got["max_chars"]), (False, 1200))

    def test_the_settings_name_the_hermes_profile_so_the_page_can_print_a_line_that_changes_it(self):
        self.assertNotIn("profile", self.get_json("/api/v1/settings"))
        self.cfg.hermes_home = Path("/h/.hermes/profiles/iris")
        self.assertEqual(self.get_json("/api/v1/settings")["profile"], "iris")

    def test_a_hermes_owned_group_is_409_with_a_line_for_each_key(self):
        self.cfg.origins["windows"] = "hermes"
        got = self.refused("PATCH", "/api/v1/settings", {"windows": {"collapse_hours": 4, "grace_hours": 1}}, want=409)
        self.assertEqual([c.rsplit(".", 1)[1] for c in got["commands"]], ["collapse_hours 4", "grace_hours 1"])
        self.assertEqual(got["command"], "\n".join(got["commands"]))

    def test_an_env_owned_key_is_409(self):
        self.cfg.origins["today"] = "env"
        got = self.refused("PATCH", "/api/v1/settings", {"today": {"enabled": False}}, want=409)
        self.assertIn("environment", got["detail"])

    def test_my_location_saves_the_place_and_time_zone_there_and_answers_without_the_position(self):
        hit = {"name": "Kochi", "admin1": "Kerala", "country": "India", "latitude": 9.98, "longitude": 76.28,
               "timezone": "Asia/Kolkata"}
        with mock.patch("journal.location.reverse_geocode", return_value=hit) as rev:
            got = self.ok("POST", "/api/v1/settings/location", {"lat": 9.98, "lon": 76.28, "accuracy": 30})
        self.assertEqual(rev.call_args.args[:2], (9.98, 76.28))
        self.assertEqual(got["written"], {"location": "file", "timezone": "file", "coordinates": "file"})
        self.assertEqual((got["location"], got["timezone"]), ("Kochi, Kerala, India", "Asia/Kolkata"))
        self.assertRegex(got["local_time_now"], r"^\w{3} \d{2}:\d{2}$")
        self.assertNotIn("9.98", json.dumps(got))
        self.assertEqual((self.data()["location"], self.data()["timezone"]), ("Kochi, Kerala, India", "Asia/Kolkata"))

    def test_my_location_refuses_a_missing_or_bad_position_and_an_unreachable_lookup(self):
        before = self.file.read_bytes()
        with mock.patch("journal.location.reverse_geocode") as rev:
            for body in ({}, {"lat": 9.9}, {"lat": "9.9", "lon": "76.2"}, {"lat": 200, "lon": 0}):
                with self.subTest(body):
                    self.assertEqual(self.refused("POST", "/api/v1/settings/location", body, want=422)["error"], "invalid")
            rev.assert_not_called()
        with mock.patch("journal.location.reverse_geocode", side_effect=OSError("curl")):
            self.assertEqual(self.refused("POST", "/api/v1/settings/location", {"lat": 9.9, "lon": 76.2},
                                          want=503)["error"], "engine-unavailable")
        self.assertEqual(self.file.read_bytes(), before)

    def test_a_config_directory_the_user_cannot_write_is_503_and_says_how_to_fix_it(self):
        before = self.file.read_bytes()
        with mock.patch("journal.settings.os.access", return_value=False):
            got = self.refused("PATCH", "/api/v1/settings", {"windows": {"collapse_hours": 5}}, want=503)
        self.assertEqual(got["error"], "engine-unavailable")
        self.assertIn("writable for the user the journal runs as", got["detail"])
        self.assertNotIn("ReadWritePaths", got["detail"])   # no sandbox to widen (SERVE-UNSANDBOXED)
        self.assertIn("--print-unit", got["detail"])   # for a host still on an older, sandboxed unit
        self.assertEqual(self.file.read_bytes(), before)

    def test_every_face_judges_a_change_the_same_way(self):
        """The web app and ``journal config set`` run ``settings.apply``: what one refuses the other refuses, with
        the status of its own kind (web 400/422/409, command line exit 2/2/1)."""
        self.cfg.origins["today"] = "hermes"
        cases = [({"windows.collapse_hours": 5}, 200, 0), ({"root": "/x"}, 400, 2), ({"serve.port": 1}, 400, 2),
                 ({"windows.collapse_hours": 500}, 422, 2), ({"timezone": "Not/AZone"}, 422, 2),
                 ({"today.enabled": False}, 409, 1)]
        for body, status, exit_code in cases:
            with self.subTest(body):
                (key, value), = body.items()
                code, _h, _raw = self.write("PATCH", "/api/v1/settings", body)
                args = cli.build_parser().parse_args(["config", "set", key, json.dumps(value)])
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    cli_code = cli.run(args, cfg_loader=lambda: self.cfg)
                self.assertEqual((code, cli_code), (status, exit_code))

    def test_a_place_goes_through_the_location_module(self):
        hit = {"name": "Lisbon", "admin1": "Lisbon", "country": "Portugal", "latitude": 38.7, "longitude": -9.1,
               "timezone": "Europe/Lisbon"}
        with mock.patch("journal.location.geocode", return_value=hit) as geocode:
            got = self.ok("PATCH", "/api/v1/settings", {"location": "Lisbon"})
        geocode.assert_called_once_with("Lisbon")
        self.assertEqual(got["written"], {"location": "file", "timezone": "file", "coordinates": "file"})
        d = self.data()
        self.assertEqual((d["location"], d["timezone"], d["coordinates"]["lat"]), ("Lisbon, Lisbon, Portugal",
                                                                                  "Europe/Lisbon", 38.7))
        self.assertTrue((self.cfg.state_dir / "location-history.jsonl").exists())
        with mock.patch("journal.location.geocode", side_effect=ValueError("couldn't find a place called 'Zzz'")):
            self.assertTrue(self.refused("PATCH", "/api/v1/settings", {"location": "Zzz"}, want=422)["problems"])
        with mock.patch("journal.location.geocode", side_effect=OSError("curl")):
            self.refused("PATCH", "/api/v1/settings", {"location": "Faro"}, want=503)
        self.refused("PATCH", "/api/v1/settings", {"location": "Faro", "coordinates": {"lat": 1, "lon": 2}})
        self.assertEqual(self.data()["timezone"], "Europe/Lisbon")

    def test_a_hermes_owned_timezone_stops_a_place_before_it_is_looked_up(self):
        self.cfg.origins["timezone"] = "hermes"
        with mock.patch("journal.location.geocode") as geocode:
            self.refused("PATCH", "/api/v1/settings", {"location": "Lisbon"}, want=409)
        geocode.assert_not_called()


class LockTest(WriteCase):
    def test_a_busy_ledger_is_423_and_the_journal_is_as_it_was(self):
        before = checksum(self.cfg.root)
        held, done = threading.Event(), threading.Event()

        def holder():
            with ledger.locked(self.cfg.ledger_path):
                held.set()
                done.wait(10)
        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        try:
            with mock.patch.object(writes, "LOCK_WAIT", 0.2):
                started = time.monotonic()
                self.assertEqual(self.refused("POST", "/api/v1/entries", {"text": "x"}, want=423)["error"], "busy")
                self.refused("POST", "/api/v1/entries/2026-10-14-evening/blocks", {"text": "x"}, want=423)
                self.refused("PATCH", "/api/v1/entries/2026-10-14-evening/meta", {"starred": True}, want=423)
                self.assertLess(time.monotonic() - started, 5)
        finally:
            done.set()
            t.join(5)
        self.assertEqual(checksum(self.cfg.root), before)
        self.ok("POST", "/api/v1/entries", {"text": "after"}, want=201)

    def test_a_busy_library_is_423(self):
        from journal import library
        held, done = threading.Event(), threading.Event()

        def holder():
            with library.held(self.cfg.practices_dir):
                held.set()
                done.wait(10)
        t = threading.Thread(target=holder)
        t.start()
        held.wait(5)
        try:
            with mock.patch.object(writes, "LOCK_WAIT", 0.2):
                self.refused("DELETE", "/api/v1/library/gratitude", want=423)
        finally:
            done.set()
            t.join(5)
        self.assertTrue((self.cfg.practices_dir / "gratitude.md").exists())


if __name__ == "__main__":
    unittest.main()
