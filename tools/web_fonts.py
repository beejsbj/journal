"""Remake the two web fonts that are cut down from their Fontsource originals (``web/public/fonts``).

The web app serves its faces from its own origin (SIL OFL 1.1; the licences sit beside the files as ``OFL-*.txt``). Four
of the six files are Fontsource's latin subsets as published; two are made smaller here, since the service worker
precaches every face:

- ``fraunces-digits-normal.woff2``: Fraunces holds only the figures (the week strip's days, the counts). The ten digits
  are kept, with the optical-size and SOFT axes; WONK is pinned at 0 (it only bends letters) and the weight axis is
  narrowed to 300-700. 121 KB becomes 9 KB.
- ``newsreader-latin-italic-400.woff2``: the italic is set at weight 400 only (the wordmark, section heads, the states
  of a prompt), so the weight axis is pinned there; optical size stays variable. 147 KB becomes 64 KB.

The inputs are Fontsource's files (jsDelivr, ``https://cdn.jsdelivr.net/fontsource/fonts/<path>``), checked by hash:

    fraunces:vf@latest/latin-full-normal.woff2      7e744849028e2219…
    newsreader:vf@latest/latin-opsz-italic.woff2    5dfcd10d24af8c82…

Run off the journal's host or on it (no Node, no network: download the two files first and pass their paths):

    uv run --no-project --with fonttools==4.67.0 --with brotli==1.2.1 python tools/web_fonts.py FRAUNCES.woff2 NEWSREADER_ITALIC.woff2

The same inputs and these versions make the same bytes (the committed files); another fontTools or brotli may not.

The outputs are written to ``web/public/fonts``. Commit them with a rebuilt ``static/`` (the web workflow's artifact).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont
from fontTools.varLib import instancer

OUT = Path(__file__).resolve().parents[1] / "web" / "public" / "fonts"
FRAUNCES_SHA = "7e744849028e2219"
NEWSREADER_ITALIC_SHA = "5dfcd10d24af8c82"


def check(path: Path, prefix: str) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if not digest.startswith(prefix):
        raise SystemExit(f"{path}: sha256 {digest[:16]}… is not the Fontsource file this was made from ({prefix}…)")


def save(font: TTFont, out: Path) -> None:
    font.flavor = "woff2"
    font.recalcTimestamp = False   # the same inputs make the same bytes
    font.save(out)


def figures(src: Path, out: Path) -> None:
    font = instancer.instantiateVariableFont(TTFont(src), {"WONK": 0, "wght": (300, 700)})
    opts = subset.Options()
    opts.layout_features = ["*"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    cut = subset.Subsetter(opts)
    cut.populate(unicodes=list(range(0x30, 0x3A)))   # 0-9
    cut.subset(font)
    save(font, out)


def italic(src: Path, out: Path) -> None:
    font = instancer.instantiateVariableFont(TTFont(src), {"wght": 400})
    save(font, out)


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    fraunces, newsreader = Path(argv[0]), Path(argv[1])
    check(fraunces, FRAUNCES_SHA)
    check(newsreader, NEWSREADER_ITALIC_SHA)
    figures(fraunces, OUT / "fraunces-digits-normal.woff2")
    italic(newsreader, OUT / "newsreader-latin-italic-400.woff2")
    print("written to", OUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
