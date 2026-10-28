"""Audio the browser records, held for a while until an entry or a block takes it (``POST /uploads``).

A recording is too large for a JSON body, so it is posted first (multipart, at most 25 MB) and then named by id in
``POST /entries`` or ``POST /entries/{stem}/blocks``. It waits in ``state_dir/uploads/`` for an hour; taking it
copies it beside the entry through ``audio.keep``, exactly as a Telegram audio file is kept, and the tick transcribes
it. Nothing here is his words: an upload is bytes in the state directory, never in the journal until an entry
takes it, and an unclaimed one is deleted when it is an hour old.

The multipart parser is deliberately small (one request, one file part): the stdlib's ``cgi`` is gone in 3.13 and
``email`` would hold three copies of a 25 MB body.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

from .. import audio
from ..config import Config

LIMIT = 25 * 1024 * 1024   # bytes of one upload (and of the request carrying it)
TTL = 3600                 # seconds an unclaimed upload is kept
DIR = "uploads"
ID = re.compile(r"^[0-9a-f]{32}$")


class BadUpload(ValueError):
    """The request is not a usable upload; ``status`` is the HTTP code to answer with."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass
class Upload:
    id: str
    path: Path       # the bytes
    mime: str
    ext: str         # the file extension the kept copy takes
    name: str | None  # the file name the browser gave, when it gave one


def directory(cfg: Config) -> Path:
    return Path(cfg.state_dir) / DIR


def parse_multipart(content_type: str, body: bytes) -> tuple[str | None, str, bytes]:
    """(file name, content type, bytes) of the upload's file part: the part named ``file``, else the first one that
    has a file name. ``BadUpload`` (400) when the request is not multipart form data or has no file part."""
    head = Message()
    head["Content-Type"] = content_type or ""
    boundary = head.get_boundary() if head.get_content_type() == "multipart/form-data" else None
    if not boundary:
        raise BadUpload(400, "send the recording as multipart/form-data")
    first = None
    for part in body.split(b"--" + boundary.encode("latin-1", "replace"))[1:]:
        if part.startswith(b"--"):
            break
        raw_head, sep, content = part.removeprefix(b"\r\n").partition(b"\r\n\r\n")
        if not sep:
            continue
        msg = Message()
        for line in raw_head.decode("utf-8", "replace").split("\r\n"):
            key, colon, value = line.partition(":")
            if colon:
                msg[key.strip()] = value.strip()
        if msg.get_content_disposition() != "form-data":
            continue
        found = (msg.get_filename(), msg.get_content_type() if "Content-Type" in msg else "",
                 content.removesuffix(b"\r\n"))
        if msg.get_param("name", header="content-disposition") == "file":
            return found
        if first is None and found[0]:
            first = found
    if first is None:
        raise BadUpload(400, "the request has no file part")
    return first


def one_line(name: str | None) -> str:
    """A file name as one plain line: control characters and line separators (``\\n``, ``\\r``, ``\\x85``,
    ``\\u2028``) dropped. The name goes into the heading of the block, which must stay one line of his words."""
    return "".join(c for c in name or "" if unicodedata.category(c)[0] not in "CZ" or c == " ").strip()


def store(cfg: Config, content_type: str, body: bytes, now: float | None = None) -> dict:
    """Keep the recording in ``body`` and say how to name it: ``{"upload", "bytes", "mime"}``."""
    name, mime, data = parse_multipart(content_type, body)
    base = mime.split(";")[0].strip().lower()
    if not base.startswith("audio/"):
        raise BadUpload(415, f"only audio can be uploaded (got {base or 'no content type'})")
    if not data:
        raise BadUpload(400, "the recording is empty")
    now = time.time() if now is None else now
    purge(cfg, now)
    folder = directory(cfg)
    folder.mkdir(parents=True, exist_ok=True)
    uid = secrets.token_hex(16)
    _write(folder / f"{uid}.bin", data)
    meta = {"mime": base, "bytes": len(data), "at": now, "name": one_line(name)[:200] or None}
    _write(folder / f"{uid}.json", json.dumps(meta).encode())
    return {"upload": uid, "bytes": len(data), "mime": base}


def _write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def claim(cfg: Config, uid: str, now: float | None = None) -> Upload:
    """Take an upload for one entry: a second request naming it gets ``KeyError`` (the claim renames its record
    atomically). ``release`` gives it back when the entry could not be written, ``finish`` deletes it."""
    now = time.time() if now is None else now
    if not isinstance(uid, str) or not ID.match(uid):
        raise KeyError(uid)
    folder = directory(cfg)
    record, claimed = folder / f"{uid}.json", folder / f"{uid}.claimed"
    try:
        os.rename(record, claimed)
        meta = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise KeyError(uid) from None
    if now - float(meta.get("at") or 0) > TTL:
        finish_files(folder, uid)
        raise KeyError(uid)
    return Upload(uid, folder / f"{uid}.bin", str(meta.get("mime") or ""), audio.extension_for(meta.get("mime") or ""),
                  meta.get("name"))


def release(cfg: Config, up: Upload) -> None:
    folder = directory(cfg)
    try:
        os.rename(folder / f"{up.id}.claimed", folder / f"{up.id}.json")
    except OSError:
        pass


def finish(cfg: Config, up: Upload) -> None:
    finish_files(directory(cfg), up.id)


def finish_files(folder: Path, uid: str) -> None:
    for suffix in (".bin", ".json", ".claimed"):
        (folder / f"{uid}{suffix}").unlink(missing_ok=True)


def purge(cfg: Config, now: float | None = None) -> int:
    """Delete uploads older than ``TTL`` (and stray temp files); how many ids went."""
    now = time.time() if now is None else now
    folder = directory(cfg)
    gone = 0
    try:
        files = list(folder.iterdir())
    except OSError:
        return 0
    for f in files:
        try:
            if now - f.stat().st_mtime > TTL:
                f.unlink(missing_ok=True)
                gone += f.suffix == ".json"
        except OSError:
            continue
    return gone
