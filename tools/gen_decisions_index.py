"""Keep the index at the top of docs/DECISIONS.md current (issue #49).

DECISIONS.md is long (hundreds of KB) and every package is asked to read it before a change. The index at its top
lists every decision once: its key, a one-line title, its status and a link to its entry, so a reader opens the index
and then only the entries a change touches.

What this script reads (it changes no decision's words):

* the table of numbered decisions at the top (rows ``| N | **…** | … |``), status *decided*, or *superseded* when the
  row's decision is struck through (``~~``);
* every top-level bullet ``- **KEY — title.** …`` in the section "Raised by packages (open, not decided)" (status
  *open*) and in "Taken by packages" (status *decided*, or *superseded* when its bold lead says so).

Each entry carries an anchor, ``<a id="…"></a>``, written at the start of the bullet (or of the row's first cell).
An existing anchor is kept as it is, so a link survives an edit of the title; a new entry gets one made from its
bold lead (and ``-2``, ``-3`` for a repeat). The index sits between ``<!-- decisions-index:start -->`` and
``<!-- decisions-index:end -->`` and is rewritten whole.

    python3 tools/gen_decisions_index.py            # adds missing anchors and rewrites the index
    python3 tools/gen_decisions_index.py --check    # exit 1 (and say what) if either would change

CI runs ``--check`` beside ``gen_builtins.py --check``. A merge conflict inside the index is resolved by running the
script again.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "DECISIONS.md"

START = "<!-- decisions-index:start -->"
END = "<!-- decisions-index:end -->"
OPEN_HEADING = "## Raised by packages"
TAKEN_HEADING = "## Taken by packages"
ANCHOR = re.compile(r'<a id="([a-z0-9-]+)"></a>')
BULLET = re.compile(r"^- (?:<a id=\"[a-z0-9-]+\"></a>)?\*\*")
ROW = re.compile(r"^\| (?:<a id=\"[a-z0-9-]+\"></a>)?(\d+) \| (.*?) \|")
MAX_TITLE = 150


def lead_of(lines: list[str], i: int) -> str:
    """The bold lead of the bullet that starts at ``lines[i]``: text from its opening ``**`` to the closing one (it may
    run over several lines), one line, markdown emphasis left in."""
    text = re.sub(r'^- (?:<a id="[a-z0-9-]+"></a>)?', "", lines[i])
    j = i
    while text.count("**") < 2 and j + 1 < len(lines) and lines[j + 1].strip():
        j += 1
        text += " " + lines[j].strip()
    m = re.match(r"\*\*(.*?)\*\*", text)
    return " ".join((m.group(1) if m else text).split())


def plain(text: str) -> str:
    return re.sub(r"[`*~]", "", text).strip().rstrip(".")


def shorten(text: str) -> str:
    if len(text) <= MAX_TITLE:
        return text
    cut = text[:MAX_TITLE].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return cut + "…"


def slug(text: str, taken: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", plain(text).lower()).strip("-")[:48].strip("-") or "decision"
    out, n = base, 1
    while out in taken:
        n += 1
        out = f"{base}-{n}"
    return out


def superseded(lead: str) -> bool:
    return lead.startswith("~~") or bool(re.search(r"\bsuperseded\b", lead, re.IGNORECASE))


def link_text(text: str) -> str:
    return plain(text).replace("[", "(").replace("]", ")")


def build(text: str) -> tuple[str, list[str]]:
    """(the document with anchors and the index in place, what was missing or stale as readable lines)."""
    problems: list[str] = []
    lines = text.split("\n")
    # drop the old index, keeping the anchors it does not own
    if START in lines and END in lines:
        a, b = lines.index(START), lines.index(END)
        body = lines[:a] + ["@@INDEX@@"] + lines[b + 1:]
    else:
        problems.append("the index markers are missing")
        at = next((n for n, l in enumerate(lines) if l.startswith("| # |")), None)
        if at is None:
            raise SystemExit("gen_decisions_index: no numbered-decisions table to put the index above")
        body = lines[:at] + ["@@INDEX@@", ""] + lines[at:]
    taken = {m.group(1) for l in body for m in ANCHOR.finditer(l)}
    rows: list[tuple[str, str, str, str]] = []   # (section, anchor, title, status)
    section = "table"
    for n, line in enumerate(body):
        if line.startswith("## "):
            section = "open" if line.startswith(OPEN_HEADING) else "taken" if line.startswith(TAKEN_HEADING) else "other"
            continue
        m = ROW.match(line) if section == "table" else None
        if m:
            number, rest = m.groups()
            anchor = f"decision-{number}"
            if f'<a id="{anchor}"></a>' not in line:
                problems.append(f"table row {number} has no anchor")
                body[n] = line.replace(f"| {number} |", f'| <a id="{anchor}"></a>{number} |', 1)
                taken.add(anchor)
            lead = rest.strip()
            first = re.match(r"(~~)?\*\*(.*?)\*\*", lead)
            title = f"{number}. {first.group(2)}" if first else f"{number}. {lead}"
            rows.append(("table", anchor, title, "superseded" if lead.startswith("~~") else "decided"))
            continue
        if section in ("open", "taken") and BULLET.match(line):
            lead = lead_of(body, n)
            have = ANCHOR.search(line)
            if have:
                anchor = have.group(1)
            else:
                anchor = slug(lead, taken)
                taken.add(anchor)
                problems.append(f"entry without an anchor: {plain(lead)[:70]}")
                body[n] = f'- <a id="{anchor}"></a>' + line[2:]
            status = "open" if section == "open" else "superseded" if superseded(lead) else "decided"
            rows.append((section, anchor, lead, status))
    ids = [r[1] for r in rows]
    problems += [f"anchor used twice: {a}" for a in sorted({a for a in ids if ids.count(a) > 1})]
    index = [START,
             "<!-- Generated by tools/gen_decisions_index.py (CI runs it with --check); do not edit between the markers. -->",
             "",
             "## Index",
             "",
             "Read this, then only the entries your change touches. *open*: raised and not yet decided; *decided*: taken "
             "(the number table first, then the packages' entries); *superseded*: kept for the record, replaced by a later one.",
             ""]
    heads = {"table": "Numbered decisions", "open": "Open points (raised by packages)", "taken": "Taken by packages"}
    for key in ("table", "open", "taken"):
        mine = [r for r in rows if r[0] == key]
        if not mine:
            continue
        index += [f"**{heads[key]}** ({len(mine)})", ""]
        index += [f"- [{shorten(link_text(title))}](#{anchor}) · {status}" for _, anchor, title, status in mine]
        index.append("")
    index.append(END)
    out = "\n".join(body).replace("@@INDEX@@", "\n".join(index))
    if out != text and not problems:
        problems.append("the index is out of date")
    return out, problems


def main(argv: list[str]) -> int:
    check = "--check" in argv
    text = DOC.read_text(encoding="utf-8")
    new, problems = build(text)
    if check:
        if new != text or problems:
            print("docs/DECISIONS.md: the index is not current (run `python3 tools/gen_decisions_index.py`):")
            for p in problems[:20]:
                print(f"  - {p}")
            if len(problems) > 20:
                print(f"  … and {len(problems) - 20} more")
            return 1
        print("decisions index: current")
        return 0
    if new != text:
        DOC.write_text(new, encoding="utf-8")
        print(f"rewrote {DOC.relative_to(ROOT)}: {len(problems)} change(s)")
    else:
        print("decisions index: already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
