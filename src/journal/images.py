"""Pictures he sends, kept beside entries the way audio is kept.

A photo or an image file (jpg, jpeg, png, webp, heic, gif) that answers a prompt, a ``/j`` note or a program phase,
or that he sends in a conversation the journal is writing, is copied into the journal root: flat, named after the
entry it belongs to exactly as audio is (``2026-10-13-evening.jpg``, then ``-2``, ``-3`` …; ``audio.keep`` claims
the name), and embedded in his block as an Obsidian ``![[name]]`` above his caption. The caption is his words; the
picture is kept, never altered. Off the record (``/x``) nothing is kept: the check comes before any of this
(``inbound``).

What a file name means as an image is decided here once: ``/media`` serves exactly the names ``media_type``
accepts, and the entry document treats exactly those embeds as pictures.

``images.describe`` (off by default) lets the tick ask the companion for a one-line description of each kept picture
(``tick.process_images``); it is written under her notes, marked as hers, and validated here (``valid_description``).
"""
from __future__ import annotations

import os
import re

from . import audio
from .config import Config, _positive

IMAGE_TYPES = {  # extension -> MIME type; what is kept and what ``/media`` serves
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
    ".heic": "image/heic", ".gif": "image/gif",
}
# What the companion's image option takes (Hermes' ``--image`` knows these, not HEIC): the rest is kept and shown,
# never described.
DESCRIBABLE = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
_EXT_OF_MIME = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/pjpeg": ".jpg", "image/png": ".png",
                "image/webp": ".webp", "image/heic": ".heic", "image/heif": ".heic", "image/gif": ".gif"}
DEFAULT_MAX_MB = 20.0
DESCRIPTION_MAX = 200
_QUOTES = '"“”„‟«»'


class ImageTooLarge(Exception):
    """The picture is over ``images.max_mb``; it is not kept (its caption still is)."""


def media_type(name: str) -> str | None:
    """The image type of a file name, or None when it is not one of the kept kinds."""
    return IMAGE_TYPES.get(os.path.splitext(name)[1].lower())


def extension(path: str | None, file_name: str | None, mime: str, *, photo: bool = False) -> str | None:
    """The suffix to keep a picture under, or None when it is not an image we keep: the sender's file name, else
    the cached file's own suffix, else what the MIME type says. A Telegram photo with no hint at all is a JPEG
    (``photo``: Telegram re-encodes photos as JPEG)."""
    for name in (file_name, path):
        ext = os.path.splitext(name or "")[1].lower()
        if ext in IMAGE_TYPES:
            return ext
    base = (mime or "").split(";")[0].strip().lower()
    if base in _EXT_OF_MIME:
        return _EXT_OF_MIME[base]
    return ".jpg" if photo and (not base or base.startswith("image/")) else None


def from_event(event) -> tuple[tuple[str, str], ...]:
    """(local path, mime) of each picture a platform message event carries (a Hermes ``MessageEvent``: ``media_urls``,
    ``media_types``, ``message_type``): a photo's attachments, or a document whose type or suffix says it is one of
    the kept image kinds. A voice or audio message carries none."""
    urls = [str(u) for u in (getattr(event, "media_urls", None) or [])]
    types = [str(t or "") for t in (getattr(event, "media_types", None) or [])]
    kind = getattr(event, "message_type", "")
    kind = str(getattr(kind, "name", kind)).lower()
    if kind in ("voice", "audio"):
        return ()
    out = []
    for i, url in enumerate(urls):
        mime = types[i].lower() if i < len(types) else ""
        if not os.path.isfile(url) or mime.startswith("audio/"):
            continue
        if mime.startswith("image/") or media_type(url) or (not mime and kind == "photo"):
            out.append((url, mime))
    return tuple(out)


def max_bytes(cfg: Config) -> int:
    """``images.max_mb`` as a size in bytes; the default 20 MB when it is missing or not a positive number (doctor
    reports a bad value)."""
    mb = _positive(cfg.images.get("max_mb", DEFAULT_MAX_MB)) or DEFAULT_MAX_MB
    return int(mb * 1024 * 1024)


def keep(cfg: Config, media: str, entry_path: str, ext: str) -> str:
    """Copy the picture next to its entry, same name as audio is kept under: 2026-10-13-evening.jpg (then -2, -3 …).
    Raises ``ImageTooLarge`` over the size cap, ``OSError`` when the copy fails; nothing is left behind either way."""
    ext = ext.lower()
    if ext not in IMAGE_TYPES:
        raise ValueError(f"not a kept image type: {ext!r}")
    if os.path.getsize(media) > max_bytes(cfg):
        raise ImageTooLarge(f"{os.path.getsize(media)} bytes is over images.max_mb")
    return audio.keep(cfg, media, entry_path, ext)


def describable(name: str) -> bool:
    """Whether the companion can be handed this picture to describe."""
    return os.path.splitext(name)[1].lower() in DESCRIBABLE


def describing(cfg: Config) -> bool:
    """Whether kept pictures are described by the companion (``images.describe``, off unless exactly true)."""
    return cfg.images.get("describe") is True


def describe_request(name: str) -> str:
    """The question for the companion: the picture is attached; the answer is one plain line."""
    return (f"A picture is attached ({name}). Describe what it shows in one plain sentence of at most "
            f"{DESCRIPTION_MAX // 2} characters: what is in it, nothing you cannot see. No quotation marks, no "
            "preamble, no markdown, no guessing about the person who sent it.")


def valid_description(text: str | None) -> str | None:
    """The description as one clean line, or None when it is not usable: empty, longer than one short line, several
    lines, or holding a quotation mark (a description must never read as his words, nor quote anything)."""
    line = (text or "").strip()
    if not line or "\n" in line or len(line) > DESCRIPTION_MAX or any(q in line for q in _QUOTES):
        return None
    from . import agent  # (imported here: agent needs config, and config is all this module needs at load)
    if re.search(r"[`#]|!\[\[|\]\]|^\s*[-*>|]", line) or agent.is_failure_text(line):
        return None  # markup that would turn into an embed or a heading in the entry, or Hermes' failure copy
    return line


def note_text(name: str, description: str) -> str:
    """Her note for a picture: marked as hers by where it goes (under her notes) and by its words."""
    return f"_Photo {name}:_ {description}"
