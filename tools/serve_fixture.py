"""A throwaway ``journal serve`` over a temporary journal, for the browser smoke test (``web/e2e``, CI only).

It builds the same neutral journal the Python suite uses (``tests/serve_helpers.build_journal``: an evening entry with
a voice note and a conversation, a program session, a /j note, a reflection, a user practice) under a temporary
directory, serves it on loopback with a fixed clock (2026-10-15 09:12 IST, so Today reads the same every run) in
``--dev`` mode (plain HTTP, so the session cookie is not ``Secure``), and writes three one-time pairing codes to
``--pair-file`` (one per line). Nothing outside the temporary directory is read or written.

``--demo`` is for looking at the app (the design screenshots, ``web/shots``), never for the smoke test: the clock moves
to the evening (21:40 IST) and today gets one prompt in each state, with an opener and the practice's questions: the
morning prompt missed, the gratitude prompt answered (with its entry), a weekly review sent on demand and still open,
and the evening one still to come; and the midweek built-in edited in place, its default changed since.

    python tools/serve_fixture.py --port 8791 --pair-file /tmp/journal-pair-codes
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from serve_helpers import EVENING_NOW, NOW, add_day_of_prompts, add_edited_builtin, build_journal  # noqa: E402

from journal.serve import app as serve_app  # noqa: E402
from journal.serve import auth  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8791)
    ap.add_argument("--pair-file", required=True, help="where to write the one-time pairing codes")
    ap.add_argument("--demo", action="store_true", help="evening clock and a prompt in every state (screenshots only)")
    args = ap.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="journal-fixture-") as tmp:
        cfg = build_journal(Path(tmp))
        if args.demo:
            add_day_of_prompts(cfg)
            add_edited_builtin(cfg)
            cfg.location = "Kochi, Kerala, India"
        now = EVENING_NOW if args.demo else NOW
        app = serve_app.App(lambda: cfg, clock=lambda: now, dev=True, log=lambda line: None)
        ok, why = app.prime()
        if not ok:
            print(why, file=sys.stderr)
            return 1
        server = serve_app.make_server(app, "127.0.0.1", args.port, retry_seconds=0)
        Path(args.pair_file).write_text("\n".join(auth.new_pairing(cfg) for _ in range(3)) + "\n", encoding="utf-8")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
