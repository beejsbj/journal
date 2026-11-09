"""Web Push from ``journal serve``: RFC 8291 payload encryption (``aes128gcm``) and RFC 8292 VAPID, and one send.

Serve owns push (OPEN-PROMPTS *Push notifications*): the tick runs in the companion's Python with the standard library
only, and Web Push needs elliptic curves and AES-GCM, which the standard library does not have. They come from the
optional extra ``journal[push]`` (``cryptography``), imported lazily, so the core stays stdlib and this module imports
without it; a call that needs it raises :class:`Unavailable` saying how to install it.

Keys travel as base64url strings, ready for a JSON state file and for the browser: a P-256 private key as its 32-byte
scalar, a public key as the 65-byte uncompressed point (the PWA's ``applicationServerKey``, a subscription's
``p256dh``). The HKDF steps are HMAC-SHA-256 from the standard library; sending is ``urllib``.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from typing import NamedTuple
from urllib.parse import urlsplit

RECORD_SIZE = 4096
_HEADER = 16 + 4 + 1 + 65  # salt, record size, key id length, the sender's public key
_TAG = 16


class Unavailable(RuntimeError):
    """``cryptography`` is not installed: push needs the extra ``journal[push]``."""


def _crypto():
    try:
        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:
        raise Unavailable("Web Push needs the optional extra journal[push] (the cryptography package); "
                          "install journal with it: uv tool install --force 'journal[push]'") from e
    return ec, AESGCM


def available() -> bool:
    """Whether push can work here: the extra ``journal[push]`` is installed."""
    try:
        _crypto()
    except Unavailable:
        return False
    return True


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def unb64u(text: str) -> bytes:
    text = "".join(text.split())
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _private_key(private: str):
    ec, _ = _crypto()
    try:
        raw = unb64u(private)
    except ValueError as e:
        raise ValueError("a P-256 private key is 32 bytes, base64url") from e
    if len(raw) != 32:
        raise ValueError("a P-256 private key is 32 bytes")
    return ec.derive_private_key(int.from_bytes(raw, "big"), ec.SECP256R1())


def _point(key) -> bytes:
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    return key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, hashlib.sha256).digest()


def encrypt(payload: bytes, p256dh: str, auth: str, *, sender_private: str | None = None,
            salt: bytes | None = None, pad_to: int = 0) -> bytes:
    """The ``aes128gcm`` body (RFC 8291, RFC 8188) of ``payload`` for a subscription's ``p256dh`` and ``auth`` keys:
    one record, padded with zeros after the delimiter to ``pad_to`` bytes of payload (none by default), so bodies of
    one pad are one size whatever they say. The sender key and salt are fresh for every message unless given (a test
    vector)."""
    ec, AESGCM = _crypto()
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    if len(ua_public) != 65 or ua_public[0] != 4:
        raise ValueError("p256dh must be an uncompressed P-256 point (65 bytes)")
    if len(auth_secret) != 16:
        raise ValueError("auth must be 16 bytes")
    padding = max(0, pad_to - len(payload))
    if len(payload) + padding > RECORD_SIZE - _HEADER - _TAG - 1:
        raise ValueError(f"a push payload is at most {RECORD_SIZE - _HEADER - _TAG - 1} bytes, padding included")
    salt = os.urandom(16) if salt is None else salt
    if len(salt) != 16:
        raise ValueError("the salt is 16 bytes")
    sender = _private_key(sender_private) if sender_private else ec.generate_private_key(ec.SECP256R1())
    as_public = _point(sender.public_key())
    ecdh_secret = sender.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    # RFC 8291 section 3.4: combine the ECDH secret with the auth secret, then RFC 8188's key and nonce
    prk_key = _hmac(auth_secret, ecdh_secret)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02" + bytes(padding), None)
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + ciphertext


def generate_keys() -> dict[str, str]:
    """A new VAPID key pair: ``{"private": <32-byte scalar>, "public": <65-byte point>}``, both base64url."""
    ec, _ = _crypto()
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_numbers().private_value.to_bytes(32, "big")
    return {"private": b64u(private), "public": b64u(_point(key.public_key()))}


def public_key(private: str) -> str:
    """The public key (base64url, uncompressed point) of a base64url private key."""
    return b64u(_point(_private_key(private).public_key()))


def _origin(endpoint: str) -> str:
    parts = urlsplit(endpoint)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ValueError("a push endpoint is an http(s) URL")
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    default = {"https": 443, "http": 80}[parts.scheme]
    port = "" if parts.port in (None, default) else f":{parts.port}"
    return f"{parts.scheme}://{host}{port}"


def vapid_authorization(endpoint: str, private: str, subject: str, *, now: float | None = None,
                        ttl: int = 12 * 3600) -> str:
    """The ``Authorization`` header (RFC 8292) for a push to ``endpoint``: an ES256 JWT for the endpoint's origin,
    expiring ``ttl`` seconds from ``now`` (at most a day), with ``subject`` (``mailto:`` or ``https:``) to contact."""
    ec, _ = _crypto()
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
    if not 0 < ttl <= 24 * 3600:
        raise ValueError("a VAPID token expires within 24 hours")
    if not subject.startswith(("mailto:", "https:")):
        raise ValueError("the VAPID subject is a mailto: or https: URI")
    key = _private_key(private)
    claims = {"aud": _origin(endpoint), "exp": int(time.time() if now is None else now) + ttl, "sub": subject}

    def part(obj) -> str:
        return b64u(json.dumps(obj, separators=(",", ":")).encode("utf-8"))

    signing_input = f"{part({'typ': 'JWT', 'alg': 'ES256'})}.{part(claims)}"
    r, s = decode_dss_signature(key.sign(signing_input.encode("ascii"), ec.ECDSA(hashes.SHA256())))
    token = f"{signing_input}.{b64u(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"
    return f"vapid t={token}, k={b64u(_point(key.public_key()))}"


class Result(NamedTuple):
    """What a push service said: ``sent`` (2xx), ``gone`` (404 or 410: drop the subscription) or ``failed``, with
    the HTTP status when there was one and a short reason when it failed."""
    status: str
    code: int | None = None
    detail: str = ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # a push service does not redirect a push; a 3xx is a failure


def send(subscription: dict, payload: bytes, *, vapid_private: str, subject: str, ttl: int = 3600,
         timeout: float = 10, urgency: str = "normal", pad_to: int = 0) -> Result:
    """POST one push (RFC 8030) to a browser subscription (``PushSubscription.toJSON()``: ``endpoint`` and
    ``keys.p256dh``, ``keys.auth``): ``payload`` encrypted for it (padded to ``pad_to``), signed with the VAPID key,
    kept by the push service for at most ``ttl`` seconds while the device is away, with ``urgency`` (RFC 8030 5.3:
    ``high`` is delivered at once to a device saving power)."""
    endpoint = subscription["endpoint"]
    keys = subscription["keys"]
    body = encrypt(payload, keys["p256dh"], keys["auth"], pad_to=pad_to)
    headers = {"Authorization": vapid_authorization(endpoint, vapid_private, subject),
               "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(int(ttl)),
               "Urgency": urgency}
    request = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            return Result("sent", response.status)
    except urllib.error.HTTPError as e:
        e.close()
        if e.code in (404, 410):
            return Result("gone", e.code)
        return Result("failed", e.code, str(e.reason))
    except (urllib.error.URLError, OSError) as e:
        return Result("failed", None, str(getattr(e, "reason", e)))
