"""Settings from every face: ``journal config``, ``hermes journal config`` and the web app's ``PATCH /settings``.

One allow-list (``config.WEB_WRITABLE``), one validation (``config.web_patch``), one write (``config.patch_values``,
or ``location.set_location`` for a place), and one function that runs them in that order: ``apply``. A face only
turns what the person typed into a mapping and what ``SettingsError`` says into its own kind of refusal (the web
app's status code, the command line's exit code), so a value one face accepts is a value every face accepts.

Where a write goes is the layer that owns the key (the config file, or Hermes settings). No face writes Hermes
settings: the companion never edits them, so a key Hermes owns is refused with the ``hermes config set`` line the
person can run, which is also what the web app has always done. A key from the environment is refused too.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
from typing import Any, Callable, Mapping

from . import config as jconfig
from .config import Config

# The codes are the web app's error codes (docs/API.md); a face maps them to its own status.
KEY_NOT_ALLOWED, BAD_REQUEST, INVALID, NOT_WRITABLE, UNAVAILABLE = (
    "key-not-allowed", "bad-request", "invalid", "not-writable", "engine-unavailable")


class SettingsError(Exception):
    """A change that was not made (and nothing was written). ``code`` says why in the API's words; ``keys`` names
    the refused keys, ``problems`` what is wrong with each value, ``commands`` the ``hermes config set`` lines when
    Hermes owns the keys (one per leaf; ``command`` is the same lines as one pasteable text)."""

    def __init__(self, code: str, message: str, *, keys=(), problems=(), commands=()):
        super().__init__(message)
        self.code, self.message = code, message
        self.keys, self.problems, self.commands = list(keys), list(problems), list(commands)

    @property
    def command(self) -> str | None:
        return "\n".join(self.commands) or None


def leaf_keys(tree: Mapping = jconfig.WEB_WRITABLE, prefix: str = "") -> list[str]:
    """Every key that can be set, dotted, in the order the allow-list lists them."""
    out: list[str] = []
    for name, below in tree.items():
        out += leaf_keys(below, f"{prefix}{name}.") if isinstance(below, dict) else [f"{prefix}{name}"]
    return out


def allowed(key: str) -> bool:
    """Whether ``key`` is on the allow-list, as a leaf or as the group above leaves (``windows``)."""
    node: Any = jconfig.WEB_WRITABLE
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def _allowed_part(value: Any, tree: Any) -> Any:
    """``value`` cut to the keys the allow-list names under ``tree``, so reading a group (``conversations``) shows
    what could be set there and not the rest of the block."""
    if not isinstance(tree, dict):
        return value
    if not isinstance(value, Mapping):
        return None
    return {k: _allowed_part(value[k], below) for k, below in tree.items() if k in value}


def current(cfg: Config, key: str) -> Any:
    """The value in force for an allowed ``key`` (defaults included), as the loaded configuration holds it. A group
    is cut to its allow-listed keys (``config get`` reads only what ``config set`` may write)."""
    top, *rest = key.split(".")
    node: Any = {"timezone": cfg.timezone, "location": cfg.location,
                 "today": jconfig.effective_today(cfg)}.get(top, cfg.raw.get(top))
    tree: Any = jconfig.WEB_WRITABLE.get(top)
    for part in rest:
        node = node.get(part) if isinstance(node, Mapping) else None
        tree = tree.get(part) if isinstance(tree, dict) else None
    return _allowed_part(node, tree)


def layer(cfg: Config, key: str) -> str:
    """Which layer owns ``key`` (by its top-level name, the unit a write replaces): file, hermes, env or default."""
    top = key.split(".")[0]
    origin = cfg.origins.get(top, "default")
    return "hermes" if origin == "default" and _no_file_under_hermes(cfg) else origin


def _no_file_under_hermes(cfg: Config) -> bool:
    """Under Hermes with no config file in use, nothing reads a file a write would make, so every key not set
    elsewhere is Hermes'."""
    return cfg.config_file is None and cfg.hermes_home is not None


def origins(cfg: Config) -> dict[str, str]:
    """``cfg.origins`` for the web app, with the keys a save cannot put in a file (Hermes with no config file) marked
    as Hermes', so the Settings page locks them and shows the line that sets them."""
    out = dict(cfg.origins)
    if _no_file_under_hermes(cfg):
        for top in jconfig.WEB_WRITABLE:
            if out.get(top, "default") == "default":
                out[top] = "hermes"
    return out


def refusal(cfg: Config, key: str) -> tuple[str, str] | None:
    """Why ``key`` cannot be written, as ``(message, command)``, or None when it can (``command`` is the
    ``hermes config set`` line, else ``""``). The check ``apply`` makes, so a listing never promises what a write
    would refuse."""
    try:
        layers = jconfig.check_writable(cfg, jconfig.nest({key: None}))
    except jconfig.HermesOwned as e:
        return str(e), e.hint
    except jconfig.ConfigError as e:
        return str(e), ""
    if "file" in layers.values() and (why := _unwritable(cfg)):
        return why, ""
    return None


def rows(cfg: Config) -> list[dict]:
    """Every setting with its value, the layer that owns it, and whether (and how) it can be changed."""
    out = []
    for key in leaf_keys():
        why = refusal(cfg, key)
        out.append({"key": key, "value": current(cfg, key), "layer": layer(cfg, key), "writable": why is None,
                    **({"why": why[0], **({"command": why[1]} if why[1] else {})} if why else {})})
    return out


def _unwritable(cfg: Config) -> str | None:
    """Why the config file cannot be replaced, or None. A replace writes a new file beside it and renames it over
    the old, so it is the directory that must be writable, by the user the journal runs as (``journal serve``'s unit
    has no sandbox, so no drop-in is needed: the file permissions decide)."""
    directory = jconfig.file_target(cfg).parent
    while not directory.exists() and directory != directory.parent:
        directory = directory.parent
    if os.access(directory, os.W_OK):
        return None
    return (f"cannot write in {directory}, where the config file lives (a save writes a new file beside it and "
            "renames it over the old one): make the directory writable for the user the journal runs as (under an "
            "older, sandboxed journal serve unit, re-render the unit with `journal serve --print-unit`)")


def _line(name: str, hint: str, value: Any) -> str:
    """One ``hermes config set`` line for dotted ``name`` (``hint`` is the line without its value). A ``<...>``
    value is a placeholder for what only the lookup knows, and stays as written (not shell-quoted)."""
    if value in (None, "", {}):
        return f"{hint} <value>"
    if isinstance(value, str) and value.startswith("<") and value.endswith(">"):
        return f"{hint} {value}"
    return f"{hint} {shlex.quote(value if isinstance(value, str) else json.dumps(value))}"


def _commands(cfg: Config, owned: Mapping[str, Any], place: str | None, tz: str | None) -> list[str]:
    """Every ``hermes config set`` line that makes the change, for each top-level key of ``owned`` that Hermes owns:
    one per leaf (``windows`` with two values is two lines), and for a place all three of location, timezone and
    coordinates (the lookup sets them together, so changing the label alone would leave the schedule where it was)."""
    out: list[str] = []
    place_advised = False
    for top, part in owned.items():
        try:
            jconfig.check_writable(cfg, {top: part})
        except jconfig.HermesOwned:
            pass
        except jconfig.ConfigError:
            continue
        else:
            continue
        profile = jconfig.profile_from_home(cfg.hermes_home)
        hint = lambda name: jconfig.hermes_set_line(name, profile)   # noqa: E731
        if place and top in jconfig.WEB_WHOLE:
            if not place_advised:   # whichever of the three Hermes owns, the change needs all three
                out += [_line("location", hint("location"), place),
                        _line("timezone", hint("timezone"), tz or "<timezone of the place>"),
                        _line("coordinates", hint("coordinates"), "<lat and lon of the place, as JSON>")]
                place_advised = True
            continue
        for leaf in leaves({top: part}):
            value: Any = {top: part}
            for name in leaf.split("."):
                value = value.get(name) if isinstance(value, Mapping) else None
            out.append(_line(leaf, hint(leaf), value))
    return out


def apply(cfg: Config, raw: Mapping[str, Any], *, reload: Callable[[], Config] | None = None) -> dict:
    """Change settings. ``raw`` maps keys (dotted, or nested) to values; every key must be on the allow-list and every
    value valid, or ``SettingsError`` and nothing is written. Returns ``{"written": {key: layer}, "location":
    <what location.set_location answered, or None>}``.

    ``location`` is looked up first (``location.set_location`` writes location, timezone and coordinates together
    and logs the change; a ``timezone`` beside it overrides the lookup's), after the ownership of all three is
    checked, so a Hermes-owned timezone stops it before the geocoder is called.
    """
    from . import location
    patch, refused, problems = jconfig.web_patch(raw)
    if refused:
        raise SettingsError(KEY_NOT_ALLOWED, f"cannot be changed here: {', '.join(refused)}", keys=refused)
    if problems:
        raise SettingsError(INVALID, "a value is not allowed; nothing was saved", problems=problems)
    if not patch:
        raise SettingsError(BAD_REQUEST, "nothing to change")
    place = patch.pop("location", None)
    if place and "coordinates" in patch:
        raise SettingsError(BAD_REQUEST, "coordinates come from the place: send location or coordinates, not both")
    tz = patch.pop("timezone", None) if place else None   # with a place, a timezone overrides the lookup's
    owned = {**patch, **({"location": place, "timezone": tz or "", "coordinates": {}} if place else {})}
    layers = _writable(cfg, owned, place, tz)   # before anything is looked up or written
    found = None
    if place:
        found = _looked_up(lambda: location.set_location(cfg, place, tz_override=tz, reload=reload))
    if patch:
        try:
            jconfig.patch_values(cfg, patch)
        except jconfig.ConfigError as e:
            raise SettingsError(NOT_WRITABLE, str(e)) from None
        except OSError as e:
            raise SettingsError(UNAVAILABLE, f"cannot write the config file: {e.strerror or type(e).__name__}") from None
    written = {leaf: layers[leaf.split(".")[0]] for leaf in leaves(patch)}
    if place:
        written.update({k: layers[k] for k in ("location", "timezone", "coordinates")})
    return {"written": written, "location": found}


def _writable(cfg: Config, owned: Mapping[str, Any], place: str | None, tz: str | None) -> dict[str, str]:
    """The layer each top-level key of ``owned`` is written to, or ``SettingsError`` when one cannot be written."""
    try:
        layers = jconfig.check_writable(cfg, owned)
    except jconfig.HermesOwned as e:
        raise SettingsError(NOT_WRITABLE, str(e), commands=_commands(cfg, owned, place, tz) or [e.hint + " <value>"]) from None
    except jconfig.ConfigError as e:
        raise SettingsError(NOT_WRITABLE, str(e)) from None
    if "file" in layers.values() and (why := _unwritable(cfg)):
        raise SettingsError(UNAVAILABLE, why)
    return layers


def _looked_up(move: Callable[[], dict]) -> dict:
    """Run ``location.set_location`` with its failures as refusals: nothing usable or unreachable is 503, a place or
    position the lookup cannot name is ``invalid``."""
    try:
        return move()
    except json.JSONDecodeError:
        raise SettingsError(UNAVAILABLE, "the place lookup answered nothing usable") from None
    except ValueError as e:
        raise SettingsError(INVALID, str(e), problems=[str(e)]) from None
    except (OSError, subprocess.SubprocessError):
        raise SettingsError(UNAVAILABLE, "the place lookup could not be reached") from None


def apply_near(cfg: Config, lat: Any, lon: Any, *, reload: Callable[[], Config] | None = None) -> dict:
    """Move the journal to where the device is (the web app's "Use my location"): the same three values a place
    sets (location, timezone, coordinates), through the same write (``location.set_location`` with ``near``), after
    the same ownership checks, made before the position is sent anywhere. Returns what ``apply`` returns. The
    position is never part of a refusal's text."""
    from . import location
    try:
        near = location.near_point(lat, lon)
    except ValueError as e:
        raise SettingsError(INVALID, str(e), problems=[str(e)]) from None
    layers = _writable(cfg, {"location": "<the place there>", "timezone": "", "coordinates": {}}, "<the place there>", None)
    found = _looked_up(lambda: location.set_location(cfg, "", reload=reload, near=near))
    return {"written": {k: layers[k] for k in ("location", "timezone", "coordinates")}, "location": found}


def leaves(patch: Mapping[str, Any], prefix: str = "") -> list[str]:
    """The dotted keys a ``web_patch`` changes (a whole-valued key such as ``coordinates`` is one leaf)."""
    out: list[str] = []
    for key, value in patch.items():
        if isinstance(value, dict) and not (not prefix and key in jconfig.WEB_WHOLE):
            out += leaves(value, f"{prefix}{key}.")
        else:
            out.append(f"{prefix}{key}")
    return out
