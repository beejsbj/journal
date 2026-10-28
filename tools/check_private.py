"""Fail if a tracked file names anything private to a deployment.

The repo is public and holds nothing specific to the person it serves or to the host it runs on: overlays,
deploy receipts and host notes live outside it. This guards that. The terms are regular expressions, one per line,
in a file kept OUTSIDE the repo (so the list itself is never published): ``$JOURNAL_PRIVATE_TERMS``, else
``~/.config/journal/private/terms.txt``. Blank lines and lines starting with ``#`` are ignored; a pattern may carry
its own inline flags, e.g. ``(?i)`` for case-insensitive.

    python3 tools/check_private.py                  # exit 1 on any hit, listing file:line and the pattern
    python3 tools/check_private.py --quiet          # print hits only
    python3 tools/check_private.py --commits A..B   # also the commits in a range (default: @{upstream}..HEAD)

Every file ``git ls-files`` lists is read (bytes decoded as UTF-8, invalid bytes replaced), so a term in a doc, a
fixture or the committed web build counts; a tracked symlink's target is checked as its text. A push publishes
commits, not just the tree, so the commits not yet on the upstream branch are checked too: each one's message,
author and committer, the names of the paths it touches (deleted ones included) and the whole of every blob it adds
or changes, binary or not; a merge by its change against its first parent (``--commits`` names another range; with
no upstream, only the tree is checked). With no terms file at the default path it prints a note and exits 0: CI has
no list, and a missing list is not a leak. A list named by ``$JOURNAL_PRIVATE_TERMS`` that is missing, or a list
with no patterns, exits 2 instead, so a typo cannot pass as clean. Run it before pushing.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Path("~/.config/journal/private/terms.txt")


def terms_path() -> Path:
    return Path(os.environ.get("JOURNAL_PRIVATE_TERMS") or DEFAULT).expanduser()


def load_terms(path: Path) -> list[re.Pattern[str]]:
    out = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            out.append(re.compile(line))
        except re.error as e:
            raise SystemExit(f"{path}:{n}: bad pattern ({e})") from None
    return out


def tracked(root: Path) -> list[str]:
    done = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True)
    return [p for p in done.stdout.decode("utf-8", "replace").split("\0") if p]


def scan(root: Path, patterns: list[re.Pattern[str]]) -> list[tuple[str, int, str, str]]:
    """(path, line number, pattern, the line) for every hit; a path itself is checked as line 0."""
    hits = []
    for rel in tracked(root):
        for pat in patterns:
            if pat.search(rel):
                hits.append((rel, 0, pat.pattern, rel))
        f = root / rel
        if f.is_symlink():
            text = os.readlink(f)
        elif f.is_file():
            text = f.read_bytes().decode("utf-8", "replace")
        else:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for pat in patterns:
                if pat.search(line):
                    hits.append((rel, n, pat.pattern, line.strip()[:160]))
    return hits


def upstream_range(root: Path) -> str | None:
    done = subprocess.run(["git", "-C", str(root), "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{upstream}"],
                          capture_output=True)
    return "@{upstream}..HEAD" if done.returncode == 0 else None


def _git(root: Path, *args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], input=stdin, capture_output=True, check=True).stdout


def _match(where: str, text: str, patterns: list[re.Pattern[str]], hits: list) -> None:
    for n, line in enumerate(text.splitlines() or [""], 1):
        for pat in patterns:
            if pat.search(line):
                hits.append((where, n, pat.pattern, line.strip()[:160]))


def scan_commits(root: Path, rev_range: str, patterns: list[re.Pattern[str]]) -> list[tuple[str, int, str, str]]:
    """Hits in every commit of ``rev_range``: its message (``<sha>:message``), its author's and committer's names
    and emails (``<sha>:identity``), the name of every path it adds,
    changes or deletes, and the whole content of every blob it adds or changes (``<sha>:<path>``), binary or not.
    A merge counts by its change against its first parent. Paths come NUL-separated, so no quoting gets in the way."""
    hits: list = []
    blobs: dict[str, str] = {}   # blob sha -> where it was first seen
    for sha in _git(root, "rev-list", rev_range).decode().split():
        short = sha[:12]
        def log(fmt: str) -> str:
            return _git(root, "log", "-1", "--encoding=UTF-8", f"--format={fmt}", sha).decode("utf-8", "replace")
        _match(f"{short}:message", log("%B"), patterns, hits)
        _match(f"{short}:identity", log("%an%n%ae%n%cn%n%ce"),
               patterns, hits)   # author and committer, a name or an email per line
        out = _git(root, "diff-tree", "-r", "--root", "--no-commit-id", "--no-renames", "--diff-merges=first-parent",
                   "-z", sha).split(b"\0")
        for meta, raw_path in zip(out[0::2], out[1::2]):
            if not meta.startswith(b":"):
                continue
            path = raw_path.decode("utf-8", "replace")
            _match(f"{short}:{path}", path, patterns, hits)
            _old_mode, new_mode, _old, new, status = meta[1:].decode().split(" ")
            if status != "D" and new_mode != "160000" and set(new) != {"0"}:   # not deleted, not a submodule
                blobs.setdefault(new, f"{short}:{path}")
    if blobs:   # one cat-file for every blob: "<sha> blob <size>\n<content>\n" each
        data, at = _git(root, "cat-file", "--batch", stdin="".join(f"{b}\n" for b in blobs).encode()), 0
        for blob, where in blobs.items():
            end = data.index(b"\n", at)
            size = int(data[at:end].split()[2])
            _match(where, data[end + 1:end + 1 + size].decode("utf-8", "replace"), patterns, hits)
            at = end + 1 + size + 1
    return hits


def main(argv: list[str]) -> int:
    path = terms_path()
    if not path.is_file():
        if os.environ.get("JOURNAL_PRIVATE_TERMS"):
            print(f"check_private: $JOURNAL_PRIVATE_TERMS names {path}, which is not a file", file=sys.stderr)
            return 2
        print(f"check_private: no terms file at {path}; nothing to check (set $JOURNAL_PRIVATE_TERMS to point at one)")
        return 0
    patterns = load_terms(path)
    if not patterns:
        print(f"check_private: {path} holds no patterns", file=sys.stderr)
        return 2
    hits = scan(ROOT, patterns)
    rev_range = argv[argv.index("--commits") + 1] if "--commits" in argv[:-1] else upstream_range(ROOT)
    if rev_range:
        hits += scan_commits(ROOT, rev_range, patterns)
    for rel, n, pat, line in hits:
        print(f"{rel}:{n}: [{pat}] {line}")
    if "--quiet" not in argv:
        files = len({h[0] for h in hits})
        print(f"check_private: {len(hits)} hit(s) in {files} file(s) for {len(patterns)} term(s)" if hits
              else f"check_private: clean ({len(patterns)} term(s))")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
