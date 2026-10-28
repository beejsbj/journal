"""Keep the four built-in practice files in the canonical form ``library.render`` writes.

The files were generated once (WP1) from what the engine sent before practices were files; the constants they
came from (``questions.py``) are gone (WP13), and ``tests/test_golden.py`` holds the proof that the files still
produce the old banks and messages byte for byte. What is left of the generator is its round trip: each built-in
is parsed and rendered again, and the file must say exactly that. To change a built-in on purpose, edit its file,
run this to tidy it, run the suite, and regenerate the goldens with the reason in the commit.

    python3 tools/gen_builtins.py            # rewrites src/journal/library/practices/*.md into canonical form
    python3 tools/gen_builtins.py --check    # exit 1 if any file would change
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from journal import library  # noqa: E402


def main(argv: list[str]) -> int:
    check = "--check" in argv
    changed = 0
    for pid, practice in library.builtins().items():
        text = library.render(practice)
        assert library.parse(text, builtin=True) == practice, pid  # the round trip the library promises
        dest = library.BUILTIN_DIR / f"{pid}.md"
        if dest.exists() and dest.read_text(encoding="utf-8") == text:
            continue
        changed += 1
        if check:
            print(f"would change {dest}")
        else:
            dest.write_text(text, encoding="utf-8")
            print(f"wrote {dest}")
    return 1 if (check and changed) else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
