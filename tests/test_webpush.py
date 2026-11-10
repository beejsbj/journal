"""Web Push for ``journal serve`` (OPEN-PROMPTS *Push notifications*): the cryptography of ``serve/webpush.py``.

Checked against the RFCs' own vectors where they are deterministic: RFC 8291 Appendix A (``aes128gcm`` payload
encryption, with its fixed keys and salt) gives the exact body; RFC 8292 section 2.4 gives a VAPID token whose
signature the test's verifier must accept. An ES256 signature is not deterministic, so a header we make is checked by
verifying its signature with the key it names and reading its claims. ``send`` talks to a push service on 127.0.0.1.
Everything that needs ``cryptography`` (the extra ``journal[push]``) is skipped without it; what the module says
without the extra is tested either way."""
import ast
import base64
import importlib.util
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from helpers import ROOT

from journal.serve import webpush

HAS_CRYPTO = importlib.util.find_spec("cryptography") is not None


def b64d(s: str) -> bytes:
    s = "".join(s.split())
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# RFC 8291 section 5 and Appendix A (whitespace inside a value is the RFC's line wrapping)
RFC8291 = {
    "plaintext": b"When I grow up, I want to be a watermelon",
    "as_private": "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw",
    "as_public": "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8",
    "ua_public": "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
    "salt": "DGv6ra1nlYgDCS1FRnbzlw",
    "auth": "BTBZMqHH6r4Tts7J_aSIgg",
    "body": """DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml
               mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT
               pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN""",
}

# RFC 8292 section 2.4, Figure 1: a token and the key that signed it
RFC8292_TOKEN = """eyJ0eXAiOiJKV1QiLCJhbGciOiJFUzI1NiJ9.eyJhdWQiOiJodHRwczovL3
        B1c2guZXhhbXBsZS5uZXQiLCJleHAiOjE0NTM1MjM3NjgsInN1YiI6Im1ha
        Wx0bzpwdXNoQGV4YW1wbGUuY29tIn0.i3CYb7t4xfxCDquptFOepC9GAu_H
        LGkMlMuCGSK2rpiUfnK9ojFwDXb1JrErtmysazNjjvW2L9OkSSHzvoD1oA"""
RFC8292_KEY = """BA1Hxzyi1RUM1b5wjxsn7nGxAszw2u61m164i3MrAIxHF6YK5h4SDYic-dR
        uU_RCPCfA5aq9ojSwk5Y2EmClBPs"""


def verified_claims(token: str, public_key: str) -> dict:
    """The claims of an ES256 JWT once its signature verifies against ``public_key`` (raises if it does not)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    token = "".join(token.split())
    head, body, sig = token.split(".")
    raw = b64d(sig)
    assert len(raw) == 64, len(raw)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(public_key))
    key.verify(der, f"{head}.{body}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
    assert json.loads(b64d(head)) == {"typ": "JWT", "alg": "ES256"}
    return json.loads(b64d(body))


@unittest.skipUnless(HAS_CRYPTO, "cryptography (journal[push]) is not installed")
class EncryptTest(unittest.TestCase):
    def test_rfc8291_appendix_a(self):
        body = webpush.encrypt(RFC8291["plaintext"], RFC8291["ua_public"], RFC8291["auth"],
                               sender_private=RFC8291["as_private"], salt=b64d(RFC8291["salt"]))
        self.assertEqual(body, b64d(RFC8291["body"]))

    def test_the_sender_key_and_salt_are_fresh_for_every_message(self):
        a = webpush.encrypt(b"due now", RFC8291["ua_public"], RFC8291["auth"])
        b = webpush.encrypt(b"due now", RFC8291["ua_public"], RFC8291["auth"])
        self.assertEqual(len(a), 86 + len(b"due now") + 1 + 16)  # header, payload, delimiter, tag
        self.assertEqual(a[16:21], b"\x00\x00\x10\x00\x41")  # record size 4096, a 65-byte key id
        self.assertNotEqual(a[:16], b[:16])
        self.assertNotEqual(a[21:86], b[21:86])

    def test_a_payload_must_fit_one_record_and_keys_their_sizes(self):
        with self.assertRaises(ValueError):
            webpush.encrypt(b"x" * 3994, RFC8291["ua_public"], RFC8291["auth"])
        with self.assertRaises(ValueError):
            webpush.encrypt(b"x", RFC8291["ua_public"][:20], RFC8291["auth"])
        with self.assertRaises(ValueError):
            webpush.encrypt(b"x", RFC8291["ua_public"], RFC8291["auth"][:8])


@unittest.skipUnless(HAS_CRYPTO, "cryptography (journal[push]) is not installed")
class KeysTest(unittest.TestCase):
    def test_the_public_key_of_rfc8291s_sender(self):
        self.assertEqual(webpush.public_key(RFC8291["as_private"]), RFC8291["as_public"])

    def test_a_new_pair_round_trips_through_its_strings(self):
        keys = webpush.generate_keys()
        self.assertEqual(set(keys), {"private", "public"})
        self.assertEqual(len(b64d(keys["private"])), 32)
        self.assertEqual(len(b64d(keys["public"])), 65)
        self.assertEqual(webpush.public_key(keys["private"]), keys["public"])
        self.assertNotEqual(webpush.generate_keys()["private"], keys["private"])

    def test_a_bad_private_key_is_refused(self):
        with self.assertRaises(ValueError):
            webpush.public_key("AAAA")


@unittest.skipUnless(HAS_CRYPTO, "cryptography (journal[push]) is not installed")
class VapidTest(unittest.TestCase):
    ENDPOINT = "https://push.example.net/p/JzLQ3raZJfFBR0aqvOMsLrt54w4rJUsV"

    @staticmethod
    def parse(header: str) -> dict:
        scheme, _, params = header.partition(" ")
        assert scheme == "vapid", header
        return dict(p.strip().split("=", 1) for p in params.split(","))

    def test_the_verifier_accepts_rfc8292s_example(self):
        self.assertEqual(verified_claims(RFC8292_TOKEN, RFC8292_KEY),
                         {"aud": "https://push.example.net", "exp": 1453523768, "sub": "mailto:push@example.com"})

    def test_the_header_is_a_signed_token_and_the_key_that_signed_it(self):
        keys = webpush.generate_keys()
        header = webpush.vapid_authorization(self.ENDPOINT, keys["private"], "mailto:push@example.com",
                                             now=1453480568)
        params = self.parse(header)
        self.assertEqual(params["k"], keys["public"])
        self.assertEqual(verified_claims(params["t"], params["k"]),
                         {"aud": "https://push.example.net", "exp": 1453480568 + 12 * 3600,
                          "sub": "mailto:push@example.com"})

    def test_the_audience_is_the_endpoints_origin(self):
        keys = webpush.generate_keys()
        for endpoint, aud in (("https://Push.Example.net:8443/p/x?y=1", "https://push.example.net:8443"),
                              ("https://push.example.net:443/p/x", "https://push.example.net")):
            with self.subTest(endpoint):
                t = self.parse(webpush.vapid_authorization(endpoint, keys["private"], "https://example.com/"))["t"]
                self.assertEqual(verified_claims(t, keys["public"])["aud"], aud)

    def test_expiry_is_at_most_a_day_and_the_contact_a_uri(self):
        keys = webpush.generate_keys()
        with self.assertRaises(ValueError):
            webpush.vapid_authorization(self.ENDPOINT, keys["private"], "mailto:push@example.com", ttl=24 * 3600 + 1)
        with self.assertRaises(ValueError):
            webpush.vapid_authorization(self.ENDPOINT, keys["private"], "push@example.com")



class PushService:
    """A push service on 127.0.0.1 answering every request with ``status``, keeping what it was sent."""

    def __init__(self, status: int):
        received = self.received = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                received.append((self.path, self.headers, body))  # header names case-insensitive
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"   # RFC 8291 Appendix A, the receiver's private key


def decrypt(body: bytes, ua_private: str = UA_PRIVATE, auth: str = RFC8291["auth"]) -> bytes:
    """What the browser does with an ``aes128gcm`` body (RFC 8291, RFC 8188): the plaintext with its padding."""
    import hashlib
    import hmac
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    salt, idlen = body[:16], body[20]
    as_public, ciphertext = body[21:21 + idlen], body[21 + idlen:]
    key = ec.derive_private_key(int.from_bytes(b64d(ua_private), "big"), ec.SECP256R1())
    secret = key.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public))
    h = lambda k, d: hmac.new(k, d, hashlib.sha256).digest()  # noqa: E731
    ua_public = b64d(RFC8291["ua_public"])
    ikm = h(h(b64d(auth), secret), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = h(salt, ikm)
    cek, nonce = h(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16], h(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    return AESGCM(cek).decrypt(nonce, ciphertext, None)


@unittest.skipUnless(HAS_CRYPTO, "cryptography (journal[push]) is not installed")
class PaddingTest(unittest.TestCase):
    """Review round 1, finding 8: padded to a fixed size, two titles of different lengths make bodies of one size."""

    def test_padded_bodies_are_one_size_and_decrypt_to_the_payload(self):
        short = webpush.encrypt(b'{"title":"Evening"}', RFC8291["ua_public"], RFC8291["auth"], pad_to=256)
        longer = webpush.encrypt(b'{"title":"Morning journal, longer"}', RFC8291["ua_public"], RFC8291["auth"],
                                 pad_to=256)
        self.assertEqual(len(short), len(longer))
        self.assertEqual(len(short), 86 + 256 + 1 + 16)
        plain = decrypt(short)
        self.assertEqual(plain[:20], b'{"title":"Evening"}\x02')
        self.assertEqual(set(plain[20:]), {0})

    def test_without_padding_the_rfc_body_is_unchanged(self):
        body = webpush.encrypt(RFC8291["plaintext"], RFC8291["ua_public"], RFC8291["auth"],
                               sender_private=RFC8291["as_private"], salt=b64d(RFC8291["salt"]))
        self.assertEqual(body, b64d(RFC8291["body"]))
        self.assertEqual(decrypt(body), RFC8291["plaintext"] + b"\x02")

    def test_a_payload_longer_than_the_pad_is_not_cut(self):
        payload = b"x" * 300
        self.assertEqual(decrypt(webpush.encrypt(payload, RFC8291["ua_public"], RFC8291["auth"], pad_to=256)),
                         payload + b"\x02")


@unittest.skipUnless(HAS_CRYPTO, "cryptography (journal[push]) is not installed")
class SendTest(unittest.TestCase):
    def setUp(self):
        self.vapid = webpush.generate_keys()
        env = {k: v for k, v in os.environ.items() if not k.lower().endswith("_proxy")}
        patcher = mock.patch.dict(os.environ, env, clear=True)  # the push service is local, never through a proxy
        patcher.start()
        self.addCleanup(patcher.stop)

    def send(self, endpoint: str, payload: bytes = b'{"title":"Water","body":"due now"}'):
        sub = {"endpoint": endpoint, "keys": {"p256dh": RFC8291["ua_public"], "auth": RFC8291["auth"]}}
        return webpush.send(sub, payload, vapid_private=self.vapid["private"], subject="mailto:push@example.com",
                            ttl=600, timeout=5)

    def test_a_push_is_posted_encrypted_and_signed(self):
        payload = b'{"title":"Water","body":"due now"}'
        with PushService(201) as service:
            result = self.send(service.url + "/p/abc", payload)
        self.assertEqual((result.status, result.code), ("sent", 201))
        [(path, headers, body)] = service.received
        self.assertEqual(path, "/p/abc")
        self.assertEqual(headers["Content-Encoding"], "aes128gcm")
        self.assertEqual(headers["TTL"], "600")
        self.assertEqual(headers["Urgency"], "normal")
        self.assertEqual(len(body), 86 + len(payload) + 1 + 16)
        self.assertNotIn(b"Water", body)
        params = VapidTest.parse(headers["Authorization"])
        self.assertEqual(params["k"], self.vapid["public"])
        self.assertEqual(verified_claims(params["t"], params["k"])["aud"], service.url)

    def test_urgency_and_padding_are_passed_on(self):
        sub = {"endpoint": "", "keys": {"p256dh": RFC8291["ua_public"], "auth": RFC8291["auth"]}}
        with PushService(201) as service:
            sub["endpoint"] = service.url + "/p/abc"
            webpush.send(sub, b"{}", vapid_private=self.vapid["private"], subject="mailto:push@example.com",
                         urgency="high", pad_to=256, timeout=5)
        [(_path, headers, body)] = service.received
        self.assertEqual(headers["Urgency"], "high")
        self.assertEqual(len(body), 86 + 256 + 1 + 16)

    def test_404_and_410_mean_the_subscription_is_gone(self):
        for code in (404, 410):
            with self.subTest(code), PushService(code) as service:
                result = self.send(service.url + "/p/abc")
                self.assertEqual((result.status, result.code), ("gone", code))

    def test_any_other_answer_is_a_failure_with_its_code(self):
        for code in (400, 403, 413, 429, 500, 503):
            with self.subTest(code), PushService(code) as service:
                result = self.send(service.url + "/p/abc")
                self.assertEqual((result.status, result.code), ("failed", code))

    def test_an_unreachable_service_is_a_failure_without_a_code(self):
        with PushService(201) as service:
            url = service.url
        result = self.send(url + "/p/abc")  # the port is closed now
        self.assertEqual((result.status, result.code), ("failed", None))
        self.assertTrue(result.detail)


class WithoutTheExtraTest(unittest.TestCase):
    """Without ``cryptography`` the module still imports, and every call that needs it says how to install it."""

    def test_each_call_names_the_extra(self):
        hidden = {name: None for name in sys.modules if name.split(".")[0] == "cryptography"}
        with mock.patch.dict("sys.modules", {**hidden, "cryptography": None}):
            self.assertFalse(webpush.available())
            calls = (webpush.generate_keys,
                     lambda: webpush.public_key(RFC8291["as_private"]),
                     lambda: webpush.encrypt(b"x", RFC8291["ua_public"], RFC8291["auth"]),
                     lambda: webpush.vapid_authorization("https://push.example.net/p", RFC8291["as_private"],
                                                         "mailto:push@example.com"))
            for call in calls:
                with self.assertRaises(webpush.Unavailable) as caught:
                    call()
                self.assertIn("journal[push]", str(caught.exception))

    @unittest.skipUnless(HAS_CRYPTO, "cryptography (journal[push]) is not installed")
    def test_available_with_it(self):
        self.assertTrue(webpush.available())



class ExtraFenceTest(unittest.TestCase):
    """The core stays stdlib: ``cryptography`` is imported by ``serve/webpush.py`` alone, and only inside functions,
    so importing serve (or the module) never needs the extra."""

    def test_only_webpush_imports_cryptography_and_lazily(self):
        src = ROOT / "src" / "journal"
        importers, top_level = set(), set()
        for path in sorted(src.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            rel = path.relative_to(src).as_posix()
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
                if any(n.split(".")[0] == "cryptography" for n in names):
                    importers.add(rel)
                    if node in tree.body:
                        top_level.add(rel)
        self.assertEqual(importers, {"serve/webpush.py"})
        self.assertEqual(top_level, set())


if __name__ == "__main__":
    unittest.main()
