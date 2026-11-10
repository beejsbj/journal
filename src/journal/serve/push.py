"""Web Push from ``journal serve`` to the installed PWA (OPEN-PROMPTS *Push notifications*, rule 12).

A push is a nudge, not a delivery: it tells him a practice is due and writes no prompt record, nothing in
``sent.jsonl`` or ``send-times.json`` and no thread stamp, so it is never a chat boundary and never ends a ``/x``
window. That is why push is not a ``Channel``. What it keeps is its own:

* ``serve-push.json`` (0600, beside ``serve-auth.json``): the VAPID key pair, made at the first start, and the
  browsers' subscriptions, one per device (``POST``/``DELETE /api/v1/push/subscriptions``). A push service answering
  404 or 410 removes that subscription.
* ``push-sent.json``: the slot ids already pushed, so a slot is pushed once, across restarts too; pruned by age.

A background thread (``start``) makes a pass once a minute (``run_once``). A slot is pushed when its scheduled time has
come, it is still within ``windows.grace_hours``, it is not answered, and it was not pushed before: the schedule and
the marker decide, never the slot's *due* state, which a tick delivering it a moment earlier would hide. ``push.when``
says which journals push: ``no-chat`` (the default) only one with ``channel.platform: none``; ``always`` every one,
slots the chat has delivered included.

A push says the practice's title and "due now", with the practice's id for the tap to open (``/today?write=<id>``),
and nothing else: never his words, never an opener, nothing read from an entry. The payload is encrypted end to end
(``webpush``), but the browser vendor's push service still sees the endpoint, the time and the size, and the VAPID
contact (``push.subject``).

The thread reads the ledger, the library and the config, which is what serve already reads; it reads no chat, calls
no model and never takes ``tick.lock``. Its outbound calls go to the push services only: a subscription's endpoint
must be one of theirs (``SERVICES``). The cryptography needs the extra ``journal[push]``; without it nothing is pushed
and Settings says why.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta
from typing import Callable
from urllib.parse import urlsplit

from .. import ledger, library, slots
from ..config import PUSH_WHEN, Config
from . import api, webpush
from .api import HTTPError, Reply, Request

STATE = "serve-push.json"
SENT = "push-sent.json"
INTERVAL = 60.0               # seconds between passes
KEEP_DAYS = 3                 # how long a pushed slot is remembered (a pass looks at today and yesterday only)
MAX_SUBSCRIPTIONS = 10        # devices; a new one past this replaces the oldest
MAX_ENDPOINT = 2000           # characters of an endpoint URL
LOCK_WAIT = 5.0
SUBJECT = "https://github.com/beejsbj/journal"   # the default VAPID contact: the software's, never his site's name
BODY = "due now"
PAD_TO = 256                  # bytes every payload is padded to: its size does not tell one practice's title from another
URGENCY = "high"              # a nudge is timely: a phone saving power gets it at once (RFC 8030 5.3)
# The push services the browsers use (Chrome and its kin, Firefox, Safari, Edge): an exact host, or ".suffix" for any
# host under it. A subscription elsewhere is refused, so serve never posts to an address a request named.
SERVICES = ("fcm.googleapis.com", "android.googleapis.com", "updates.push.services.mozilla.com", ".push.apple.com",
            ".notify.windows.com")
UNAVAILABLE = ("Web Push needs the optional extra journal[push] (the cryptography package) where journal serve runs: "
               "uv tool install --force 'journal[push]', then restart journal serve")


class Invalid(ValueError):
    """A subscription the server will not keep (its endpoint or keys)."""


def when(cfg: Config) -> str:
    value = (getattr(cfg, "push", None) or {}).get("when")
    return value if value in PUSH_WHEN else "no-chat"


def active(cfg: Config) -> bool:
    """Whether this journal pushes at all: always, or with ``no-chat`` when it has no chat."""
    return when(cfg) == "always" or not cfg.has_channel


def subject(cfg: Config) -> str:
    value = (getattr(cfg, "push", None) or {}).get("subject")
    return value if isinstance(value, str) and value.startswith(("mailto:", "https:")) else SUBJECT


# ── serve-push.json ──────────────────────────────────────────────────────────

def _path(cfg: Config):
    return cfg.state_dir / STATE


def _vapid(state: dict) -> dict | None:
    v = state.get("vapid")
    if isinstance(v, dict) and isinstance(v.get("private"), str) and v["private"] \
            and isinstance(v.get("public"), str) and v["public"]:
        return {"private": v["private"], "public": v["public"]}
    return None


def _subs(state: dict) -> list[dict]:
    return [s for s in state.get("subscriptions") or [] if isinstance(s, dict) and isinstance(s.get("endpoint"), str)
            and isinstance(s.get("keys"), dict)]


def keys(cfg: Config, *, create: bool = True) -> dict | None:
    """The VAPID key pair (``private``, ``public``: base64url), made and kept on first use when ``create`` and the
    extra is installed; None otherwise."""
    found = _vapid(ledger.read_state(_path(cfg)))
    if found or not create or not webpush.available():
        return found
    with ledger.locked_state(_path(cfg)) as state:
        found = _vapid(state)
        if found is None:
            made = webpush.generate_keys()
            state["vapid"] = {**made, "at": datetime.now().astimezone().isoformat(timespec="seconds")}
            found = made
    return found


def subscriptions(cfg: Config) -> list[dict]:
    return _subs(ledger.read_state(_path(cfg)))


def _service(host: str) -> bool:
    return any(host.endswith(s) if s.startswith(".") else host == s for s in SERVICES)


def _checked(body) -> dict:
    """The subscription as kept (``endpoint`` and ``keys.p256dh``, ``keys.auth``), or ``Invalid`` saying why."""
    if not isinstance(body, dict):
        raise Invalid("a subscription is an object with endpoint and keys")
    endpoint, given = body.get("endpoint"), body.get("keys")
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > MAX_ENDPOINT:
        raise Invalid(f"endpoint must be a URL of at most {MAX_ENDPOINT} characters")
    try:
        parts = urlsplit(endpoint)
        port = parts.port
    except ValueError:
        raise Invalid("endpoint is not a URL") from None
    host = (parts.hostname or "").lower()
    if any(not 0x21 <= ord(c) <= 0x7e for c in endpoint):
        raise Invalid("endpoint must be a URL of printable ASCII, with no spaces")
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443) or not _service(host):
        raise Invalid("endpoint must be an https URL of a browser's push service (Google, Mozilla, Apple, Microsoft)")
    if not isinstance(given, dict):
        raise Invalid("keys must hold p256dh and auth")
    p256dh, auth = given.get("p256dh"), given.get("auth")
    try:
        point = webpush.unb64u(p256dh) if isinstance(p256dh, str) else b""
        secret = webpush.unb64u(auth) if isinstance(auth, str) else b""
    except ValueError:
        raise Invalid("keys must be base64url") from None
    if len(point) != 65 or point[0] != 4:
        raise Invalid("keys.p256dh must be a P-256 public key (65 bytes, base64url)")
    if len(secret) != 16:
        raise Invalid("keys.auth must be 16 bytes, base64url")
    return {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}}


def subscribe(cfg: Config, body, now: datetime) -> bool:
    """Keep a browser's subscription (``PushSubscription.toJSON()``); True when it is new, False when the device was
    already subscribed (its keys are refreshed). Past ``MAX_SUBSCRIPTIONS`` the oldest goes. ``Invalid`` when it is
    not one to keep."""
    sub = _checked(body)
    with ledger.locked_state(_path(cfg)) as state:
        subs = _subs(state)
        for s in subs:
            if s["endpoint"] == sub["endpoint"]:
                s["keys"] = sub["keys"]
                state["subscriptions"] = subs
                return False
        subs.append({**sub, "added": now.isoformat(timespec="seconds")})
        state["subscriptions"] = subs[-MAX_SUBSCRIPTIONS:]
    return True


def unsubscribe(cfg: Config, endpoint: str) -> bool:
    """Forget a subscription; True when there was one."""
    return bool(_drop(cfg, {endpoint}))


def _drop(cfg: Config, endpoints: set[str]) -> int:
    with ledger.locked_state(_path(cfg)) as state:
        subs = _subs(state)
        kept = [s for s in subs if s["endpoint"] not in endpoints]
        if len(kept) != len(subs):
            state["subscriptions"] = kept
    return len(subs) - len(kept)


def info(cfg: Config) -> dict:
    """What Settings shows: whether push works on this server (and why not), the public key a browser subscribes
    with (made here when the first start could not), ``push.when``, whether this journal pushes, and how many devices
    are subscribed."""
    available = webpush.available()
    found = keys(cfg) if available else None
    return {"available": available, "detail": "" if available else UNAVAILABLE,
            "public_key": found["public"] if found else None, "when": when(cfg), "active": active(cfg),
            "subscriptions": len(subscriptions(cfg))}


# ── the pass ─────────────────────────────────────────────────────────────────

def _pushed(cfg: Config) -> dict:
    found = ledger.read_state(cfg.state_dir / SENT).get("slots")
    return found if isinstance(found, dict) else {}


def due(cfg: Config, now: datetime, data: dict | None = None) -> list[slots.Slot]:
    """The scheduled slots to push now: today's and yesterday's (a late evening's grace runs past midnight) whose
    time has come, still within ``grace_hours``, not answered, and not pushed before."""
    data = data if data is not None else ledger.snapshot(cfg.ledger_path)
    done = _pushed(cfg)
    grace = timedelta(hours=cfg.grace_hours)
    today = now.astimezone(cfg.tz).date()
    out = []
    for d in (today - timedelta(days=1), today):
        for s in slots.day(cfg, d.isoformat(), now, data=data):
            if s.n is None or s.at is None or not (s.at <= now < s.at + grace):
                continue
            if (s.record or {}).get("captures") or s.id in done:
                continue
            out.append(s)
    return out


def payload(cfg: Config, slot: slots.Slot) -> bytes:
    """The practice's title, "due now", and its id for the tap: nothing else."""
    try:
        title = library.resolve(slot.practice, cfg).title
    except Exception:  # noqa: BLE001 - a practice the library lost meanwhile is still named by its id
        title = slot.practice
    return json.dumps({"title": title, "body": BODY, "practice": slot.practice}, ensure_ascii=False).encode("utf-8")


def _retriable(result: webpush.Result) -> bool:
    """A failure that may pass by itself (the network, a 429, a 5xx), as against a refusal (another 4xx: a key or a
    payload the push service will not take), which retrying every minute would only repeat."""
    return result.code is None or result.code == 429 or result.code >= 500


def _claim(cfg: Config, ids: list[str], now: datetime) -> list[str]:
    """Mark ``ids`` pushed in ``push-sent.json`` before anything is sent, and return those no other pass had: so a
    marker that cannot be written sends nothing (``OSError`` propagates), and two passes at once send once. The
    marker's old entries are pruned here."""
    cutoff = now - timedelta(days=KEEP_DAYS)
    with ledger.locked_state(cfg.state_dir / SENT) as marker:
        kept = {}
        for sid, at in (marker.get("slots") or {}).items():
            try:
                if datetime.fromisoformat(at) >= cutoff:
                    kept[sid] = at
            except (TypeError, ValueError):
                continue
        mine = [sid for sid in ids if sid not in kept]
        for sid in mine:
            kept[sid] = now.isoformat(timespec="seconds")
        marker["slots"] = kept
    return mine


def _release(cfg: Config, ids: list[str]) -> None:
    with ledger.locked_state(cfg.state_dir / SENT) as marker:
        slots_ = marker.get("slots") or {}
        for sid in ids:
            slots_.pop(sid, None)
        marker["slots"] = slots_


def run_once(cfg: Config, now: datetime, *, send: Callable | None = None,
             log: Callable[[str], None] | None = None) -> list[str]:
    """One pass: push each slot ``due`` to every subscription; returns the slot ids pushed. Each slot is claimed in
    the marker before it is sent (``_claim``). A slot counts as pushed when at least one device took it; when no
    device took it and one failed in a way that may pass (``_retriable``), the claim is released and the next pass
    tries again (within its grace); a refusal is not retried. A subscription a push service says is gone is removed.
    ``send`` is ``webpush.send`` (tests record)."""
    log = log or (lambda line: None)
    if not active(cfg):
        return []
    if send is None:
        if not webpush.available():
            return []
        send = webpush.send
    state = ledger.read_state(_path(cfg))
    vapid, subs = _vapid(state), _subs(state)
    if vapid is None or not subs:
        return []
    found = due(cfg, now)
    if not found:
        return []
    mine = set(_claim(cfg, [s.id for s in found], now))
    pushed, again, gone = [], [], set()
    for slot in found:
        if slot.id not in mine:
            continue
        body = payload(cfg, slot)
        ttl = max(60, int((slot.at + timedelta(hours=cfg.grace_hours) - now).total_seconds()))
        took = retry = False
        for sub in subs:
            if sub["endpoint"] in gone:
                continue
            try:
                result = send(sub, body, vapid_private=vapid["private"], subject=subject(cfg), ttl=ttl,
                              urgency=URGENCY, pad_to=PAD_TO)
            except Exception as e:  # noqa: BLE001 - a bad key or a damaged subscription fails this device only
                result = webpush.Result("failed", None, type(e).__name__)
            if result.status == "sent":
                took = True
            elif result.status == "gone":
                gone.add(sub["endpoint"])
            else:
                retry = retry or _retriable(result)
                log(f"journal serve: push of {slot.id} failed for one device "
                    f"({result.code or ''} {result.detail}".rstrip() + ")"
                    + ("; tried again next minute" if _retriable(result) else "; not tried again"))
        if took:
            pushed.append(slot.id)
        elif retry:
            again.append(slot.id)
    if again:
        _release(cfg, again)
    if gone:
        _drop(cfg, gone)
        log(f"journal serve: {len(gone)} push subscription(s) gone; removed")
    return pushed


def start(app, *, interval: float = INTERVAL, send: Callable | None = None,
          after_pass: Callable[[], None] | None = None) -> threading.Thread:
    """The push thread: makes the VAPID keys if there are none yet, then a pass at once and every ``interval``
    seconds until the server stops (``app.stopping``). A pass that fails is logged; the next one runs."""
    def loop():
        try:
            keys(app.config())
        except Exception as e:  # noqa: BLE001 - push is a nudge: it never stops the server
            app.log(f"journal serve: push keys could not be made: {type(e).__name__}: {e}")
        while not app.stopping.is_set():
            try:
                run_once(app.config(), app.clock(), send=send, log=app.log)
            except Exception as e:  # noqa: BLE001
                app.log(f"journal serve: push pass failed: {type(e).__name__}: {e}")
            if after_pass is not None:
                after_pass()
            if app.stopping.wait(interval):
                break
    thread = threading.Thread(target=loop, daemon=True, name="journal-serve-push")
    thread.start()
    return thread


# ── the routes ───────────────────────────────────────────────────────────────

def _bounded(fn: Callable[[], object]):
    try:
        with ledger.deadline(LOCK_WAIT):
            return fn()
    except ledger.LockBusy as e:
        raise HTTPError(423, "busy", str(e)) from None
    except OSError as e:
        raise HTTPError(503, "engine-unavailable", f"cannot write: {type(e).__name__}") from None


def push_info(req: Request):
    return _bounded(lambda: info(req.cfg))


def subscription_add(req: Request):
    body = req.json()
    try:
        new = _bounded(lambda: subscribe(req.cfg, body, req.now))
    except Invalid as e:
        raise HTTPError(422, "bad-subscription", str(e)) from None
    return Reply(201 if new else 200, {"subscriptions": len(subscriptions(req.cfg))})


def subscription_delete(req: Request):
    endpoint = req.json().get("endpoint")
    if not isinstance(endpoint, str) or not endpoint:
        raise HTTPError(400, "bad-request", "endpoint is the subscription's endpoint URL")
    _bounded(lambda: unsubscribe(req.cfg, endpoint))
    return Reply(204)


# Subscriptions change no entry, ledger or definition: they are signed-in routes (``api.TOKEN``), not ``api.WRITE``,
# so a read-only server (``serve.allow_write: false``) still nudges. A POST or DELETE still needs the site's Origin.
ROUTES: list[tuple[str, str, Callable, str]] = [
    ("GET", r"/api/v1/push", push_info, api.TOKEN),
    ("POST", r"/api/v1/push/subscriptions", subscription_add, api.TOKEN),
    ("DELETE", r"/api/v1/push/subscriptions", subscription_delete, api.TOKEN),
]
