"""Remake the one web font that is cut down from its Fontsource original (``web/public/fonts``).

The web app serves its faces from its own origin (SIL OFL 1.1; the licences sit beside the files as ``OFL-*.txt``). The
files are Fontsource's latin subsets as published, except one made smaller here, since the service worker precaches
every face:

- ``newsreader-latin-italic-400.woff2``: the italic is set at weight 400 only (the wordmark, book heads, the hours in
  the margin, hints), so the weight axis is pinned there; optical size stays variable. 147 KB becomes 64 KB.

The input is Fontsource's file (jsDelivr, ``https://cdn.jsdelivr.net/fontsource/fonts/<path>``), checked by hash:

    newsreader:vf@latest/latin-opsz-italic.woff2    5dfcd10d24af8c82…

Her face, IBM Plex Mono, is used as published (no cut): ``@fontsource/ibm-plex-mono@5.3.0``, ``files/`` on jsDelivr
(``https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-mono@5.3.0/files/ibm-plex-mono-latin-<w>-<style>.woff2``):

    ibm-plex-mono-latin-400-normal.woff2    08949f728dc52d52…
    ibm-plex-mono-latin-400-italic.woff2    0840095faae86403…
    ibm-plex-mono-latin-500-normal.woff2    01d285447409c8a5…

and its licence is IBM's (``https://github.com/IBM/plex``, ``LICENSE.txt``), as ``OFL-IBMPlexMono.txt``.

Run off the journal's host or on it (no Node, no network: download the file first and pass its path):

    uv run --no-project --with fonttools==4.67.0 --with brotli==1.2.1 python tools/web_fonts.py NEWSREADER_ITALIC.woff2

The same input and these versions make the same bytes (the committed file); another fontTools or brotli may not.

The output is written to ``web/public/fonts``. Commit it with a rebuilt ``static/`` (the web workflow's artifact).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = Path(__file__).resolve().parents[1] / "web" / "public" / "fonts"
NEWSREADER_ITALIC_SHA = "5dfcd10d24af8c82"


def check(path: Path, prefix: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not digest.startswith(prefix):
        raise SystemExit(f"{path}: sha256 {digest[:16]}… is not the Fontsource file this was made from ({prefix}…)")


def save(font: TTFont, out: Path) -> None:
    font.flavor = "woff2"
    font.recalcTimestamp = False   # the same inputs make the same bytes
    font.save(out)


def italic(src: Path, out: Path) -> None:
    font = instancer.instantiateVariableFont(TTFont(src), {"wght": 400})
    save(font, out)


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    newsreader = Path(argv[0])
    check(newsreader, NEWSREADER_ITALIC_SHA)
    italic(newsreader, OUT / "newsreader-latin-italic-400.woff2")
    print("written to", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
