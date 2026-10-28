"""Travel: one call moves the journal's location, timezone and weather coordinates.

The companion calls this through the ``journal_set_location`` tool when the user says they have
moved. The place is geocoded with Open-Meteo (no key), which also returns its IANA timezone.
The web app's "Use my location" sends the device's position instead (``near``): the place is named by
OpenStreetMap's Nominatim reverse lookup, asked with the position cut to ``NEAR_DECIMALS`` (about a kilometre), and
what is kept is **the matched place's own coordinates** (its centre at zoom 10), as for a typed place: the device's
position is not written anywhere, so it never reaches the weather call's command line, the history or a receipt.
The place's time zone comes from Open-Meteo's forecast (``timezone=auto``), asked with the place's coordinates. Both
services are keyless and configurable (``lookup.reverse_url``, ``lookup.timezone_url``); the position is never
logged and never on a command line (curl reads the address from its stdin). A lookup that cannot be reached (curl
fails) is an ``OSError``, never "no such place". Prompts follow the new local time automatically. The values are
written back to the layer that owns them (the config file, or Hermes settings; see ``config.write_values``) and
every change is appended to ``location-history.jsonl`` in the state dir so it can be undone.
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.parse
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

from .config import Config, SettingsWriter, write_values

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
NEAR_DECIMALS = 2   # a device's position is cut to this before it leaves the host (~1 km); it is never written
# The two keyless services "Use my location" asks; `lookup.reverse_url` / `lookup.timezone_url` replace them (Nominatim's
# usage policy asks that an app can be pointed at another instance).
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
TIMEZONE_URL = "https://api.open-meteo.com/v1/forecast"
_URL = re.compile(r"^https?://[^\s\"\\]+$")   # no space, quote or backslash: the address goes into curl's config
# Nominatim's usage policy asks for an identifying User-Agent; this names the software, never the person.
USER_AGENT = "journal-engine (self-hosted journal; reverse lookup on the owner's request)"
# The place a position is in, from the most to the least specific of what Nominatim's address names at zoom 10.
_PLACE_KEYS = ("city", "town", "village", "municipality", "hamlet", "suburb", "county", "state_district")


# curl prints the answer's HTTP status after the body (`-w`), so a service that answers an error in JSON (a 429, a 5xx)
# is told apart from one that answers "nothing here": the first is unavailable (503), only the second is no such place.
_STATUS = "\n%{http_code}"


def _answer(out: subprocess.CompletedProcess) -> dict:
    """The JSON body of a curl run made with ``-w _STATUS``; ``OSError`` when curl failed (unreachable, timed out) or
    the service answered an HTTP error, so neither is ever read as "no such place". ``{}`` for an empty body."""
    if out.returncode != 0:
        raise OSError(f"the lookup failed (curl exit {out.returncode})")
    body, _, code = (out.stdout or "").rpartition("\n")
    if not code.strip().isdigit() or not 200 <= int(code) < 300:
        raise OSError(f"the lookup service answered HTTP {code.strip() or 'nothing'}")
    data = json.loads(body or "{}")
    return data if isinstance(data, dict) else {}


def geocode(place: str) -> dict:
    tries = [place] + ([place.split(",")[0].strip()] if "," in place else [])
    for q in tries:
        url = ("https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json&name="
               + urllib.parse.quote(q))
        out = subprocess.run(["curl", "-4", "-s", "--max-time", "15", "-w", _STATUS, url], capture_output=True,
                             text=True, timeout=20)
        results = _answer(out).get("results") or []
        if results:
            return results[0]
    raise ValueError(f"couldn't find a place called {place!r}")


def _fetch_quietly(url: str, headers: tuple[str, ...] = ()) -> dict:
    """GET ``url`` as JSON with curl, the address passed on stdin (``-K -``) so a position never shows in the process
    list another user of the host can read. ``{}`` for an empty answer; ``OSError`` when curl fails or the service
    answers an HTTP error (``_answer``)."""
    conf = "".join(f'header = "{h}"\n' for h in headers) + f'url = "{url}"\n'
    out = subprocess.run(["curl", "-4", "-s", "--max-time", "15", "-w", _STATUS, "-K", "-"], input=conf,
                         capture_output=True, text=True, timeout=20)
    return _answer(out)


def near_point(lat, lon) -> tuple[float, float]:
    """A position as numbers in range, cut to ``NEAR_DECIMALS``; ValueError otherwise (no position is echoed)."""
    if isinstance(lat, bool) or isinstance(lon, bool) or not isinstance(lat, (int, float)) \
            or not isinstance(lon, (int, float)):
        raise ValueError("a position is two numbers, lat and lon")
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):   # NaN fails both comparisons too
        raise ValueError("that position is not on the map")
    return round(float(lat), NEAR_DECIMALS), round(float(lon), NEAR_DECIMALS)


def service(cfg: Config | None, key: str, default: str) -> str:
    """The base address configured as ``lookup.<key>``, or ``default`` when it is unset or not a plain http(s) URL."""
    part = (cfg.raw.get("lookup") if cfg is not None else None) or {}
    url = part.get(key) if isinstance(part, dict) else None
    return url.rstrip("?") if isinstance(url, str) and _URL.match(url) else default


def _number(value) -> float | None:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n == n and abs(n) != float("inf") else None


def reverse_geocode(lat: float, lon: float, cfg: Config | None = None) -> dict:
    """The place and timezone at a position, in ``geocode``'s shape (name, admin1, country, latitude, longitude,
    timezone). The coordinates are **the matched place's** (Nominatim's answer at zoom 10: a city, town or village,
    its centre), never the device's; the time zone is asked for the place too, so the position goes to Nominatim
    only, cut to ``NEAR_DECIMALS``."""
    lat, lon = near_point(lat, lon)
    base = service(cfg, "reverse_url", REVERSE_URL)
    where = _fetch_quietly(f"{base}?format=jsonv2&zoom=10&addressdetails=1&accept-language=en&lat={lat}&lon={lon}",
                           (f"User-Agent: {USER_AGENT}",))
    address = where.get("address") if isinstance(where.get("address"), dict) else {}
    name = next((address[k] for k in _PLACE_KEYS if isinstance(address.get(k), str) and address[k].strip()), None)
    name = name or (where.get("name") if isinstance(where.get("name"), str) and where["name"].strip() else None)
    plat, plon = _number(where.get("lat")), _number(where.get("lon"))
    if not name or plat is None or plon is None or not (-90 <= plat <= 90 and -180 <= plon <= 180):
        raise ValueError("no named place is known at that position")
    plat, plon = round(plat, 4), round(plon, 4)
    zone = _fetch_quietly(f"{service(cfg, 'timezone_url', TIMEZONE_URL)}?latitude={plat}&longitude={plon}"
                          "&timezone=auto&forecast_days=1")
    tz = zone.get("timezone")
    try:
        ZoneInfo(tz)
    except Exception:   # absent, not text, or a name this host does not know (ZoneInfoNotFoundError is a KeyError)
        raise ValueError("the time zone at that position could not be found") from None
    state = address.get("state") if address.get("state") != name else None
    return {"name": name, "admin1": state, "country": address.get("country"), "latitude": plat, "longitude": plon,
            "timezone": tz}


OSM = "openstreetmap"   # `coordinates.source` of a place the reverse lookup named: its name is OpenStreetMap's data


def place_source(cfg: Config) -> str | None:
    """Where the stored place's name came from when it needs a credit: ``"openstreetmap"`` when the reverse lookup
    named it and it is still the place in force (a hand edit of ``location`` alone does not inherit the credit),
    else None."""
    coords = cfg.raw.get("coordinates")
    if isinstance(coords, dict) and coords.get("source") == OSM and cfg.location and coords.get("place") == cfg.location:
        return OSM
    return None


def set_location(cfg: Config, place: str, tz_override: str | None = None,
                 reload: Callable[[], Config] | None = None,
                 hermes_writer: SettingsWriter | None = None, *,
                 near: tuple[float, float] | None = None) -> dict:
    """Move the journal to ``place`` (looked up by name), or to the device position ``near`` (looked up by
    position: ``reverse_geocode``). Either way the same three values are written, the same way, and logged in the
    history; for a position the history's ``query`` says so rather than holding the position. A place named by the
    reverse lookup is OpenStreetMap's data, so its coordinates carry ``source: "openstreetmap"`` and every view that
    shows the place credits it (``place_source``); a typed place writes them without, which clears the credit."""
    if near is None and not place:
        raise ValueError("place is empty")
    hit = reverse_geocode(*near, cfg) if near is not None else geocode(place)
    tz = tz_override or hit.get("timezone")
    ZoneInfo(tz)  # raises on an invalid name
    label = ", ".join(x for x in (hit.get("name"), hit.get("admin1"), hit.get("country")) if x)
    before = {"location": cfg.location, "timezone": cfg.timezone,
              "coordinates": cfg.raw.get("coordinates")}
    after = {"location": label, "timezone": tz,
             "coordinates": {"lat": hit["latitude"], "lon": hit["longitude"], "place": label,
                             **({"source": OSM} if near is not None else {})}}
    written = write_values(cfg, after, hermes_writer)
    new = reload() if reload else cfg
    hist = cfg.state_dir / "location-history.jsonl"
    hist.parent.mkdir(parents=True, exist_ok=True)
    with open(hist, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": datetime.now(timezone.utc).isoformat(), "before": before,
                             "after": after, "query": place if near is None else "device position",
                             "written_to": written}) + "\n")
    now = datetime.now(ZoneInfo(tz))
    from . import library
    times = {p.id: ", ".join(s.time + (f" ({WEEKDAYS[s.weekday]})" if s.weekday is not None else "")
                             for s in p.schedule)
             for p in library.scheduled(new)}
    return {"location": label, "timezone": tz, "local_time_now": now.strftime("%a %H:%M"),
            "schedule_local": times, "previous": before}
