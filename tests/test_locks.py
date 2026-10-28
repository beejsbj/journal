"""The web app's writes beside the hook and the tick (WP10): a hook capture, a tick round and web writes at the same
time, in-process, with a deadline on everything. No deadlock, and every write is there once afterwards."""
import json
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from serve_helpers import IST, NOW, ServeCase

from journal import capture, ledger, library, tick


class LocksTest(ServeCase):
    ROUNDS = 12

    def setUp(self):
        super().setUp()
        self.errors = []
        sent = datetime(2026, 10, 15, 6, 0, tzinfo=IST)
        with ledger.locked(self.cfg.ledger_path) as data:
            data["prompts"].append({
                "id": "2026-10-15-morning", "stem": "2026-10-15-morning", "kind": "morning", "local_date": "2026-10-15",
                "chat_id": self.cfg.chat_id, "message_id": "600", "sent_at": sent.isoformat(),
                "sent_at_local": "06:00 IST", "opener": "Morning", "opener_source": "ada", "asked": []})
        self.cfg.audio["transcribe"] = False

    def run_thread(self, fn):
        def body():
            try:
                fn()
            except BaseException as e:   # noqa: BLE001 - reported by the test, not lost in a thread
                self.errors.append(repr(e))
        t = threading.Thread(target=body, daemon=True)
        t.start()
        return t

    def test_hook_tick_and_web_writes_together(self):
        stem = "2026-10-14-evening"

        def hook():   # what the gateway hook does for each of his Telegram replies (the sweep and the hook share it)
            for i in range(self.ROUNDS):
                capture.capture_to(self.cfg, "2026-10-14-evening", "direct-reply", f"hook reply {i}",
                                   NOW + timedelta(seconds=i), "text", f"tg-{i}")

        def ticker():  # a tick round's writers: ledger (fold), the same lock for entry writes, the state lock, prune
            for i in range(self.ROUNDS):
                with mock.patch("journal.telegram.edit_html"):
                    tick.fold(self.cfg, NOW + timedelta(hours=7), [])
                tick.prune(self.cfg, NOW)
                with ledger.locked_state(self.cfg.state_dir / "tick-state.json") as st:
                    st["round"] = i
                with library.held(self.cfg.practices_dir):
                    pass

        def web_blocks():
            for i in range(self.ROUNDS):
                code, _h, raw = self.request("POST", f"/api/v1/entries/{stem}/blocks", {"text": f"web block {i}"},
                                             headers={"Origin": self.origin})
                if code != 201:
                    raise AssertionError(f"block {i}: {code} {raw[:200]!r}")

        def web_notes():
            for i in range(self.ROUNDS):
                code, _h, raw = self.request("POST", "/api/v1/entries", {"practice": "morning" if i == 0 else None,
                                                                         "text": f"web note {i}"},
                                             headers={"Origin": self.origin})
                if code != 201:
                    raise AssertionError(f"note {i}: {code} {raw[:200]!r}")

        def web_meta_and_library():
            for i in range(self.ROUNDS):
                code, _h, raw = self.request("PATCH", f"/api/v1/entries/{stem}/meta", {"starred": i % 2 == 0},
                                             headers={"Origin": self.origin})
                if code != 200:
                    raise AssertionError(f"meta {i}: {code} {raw[:200]!r}")
                code, _h, raw = self.request("POST", "/api/v1/library/evening/fork", headers={"Origin": self.origin})
                if code not in (201, 409):
                    raise AssertionError(f"fork {i}: {code} {raw[:200]!r}")
                self.request("DELETE", "/api/v1/library/evening", headers={"Origin": self.origin})

        threads = [self.run_thread(f) for f in (hook, ticker, web_blocks, web_notes, web_meta_and_library)]
        deadline = time.monotonic() + 60
        for t in threads:
            t.join(max(0.1, deadline - time.monotonic()))
        self.assertFalse([t for t in threads if t.is_alive()], "a writer is stuck: deadlock")
        self.assertEqual(self.errors, [])

        text = (self.cfg.root / f"{stem}.md").read_text(encoding="utf-8")
        for i in range(self.ROUNDS):
            self.assertEqual(text.count(f"hook reply {i}\n"), 1, i)
            self.assertEqual(text.count(f"web block {i}\n"), 1, i)
        notes = "".join(p.read_text(encoding="utf-8") for p in self.cfg.root.glob("2026-10-15-*.md"))
        for i in range(self.ROUNDS):
            self.assertEqual(notes.count(f"web note {i}\n"), 1, i)
        data = ledger.snapshot(self.cfg.ledger_path)   # still one parseable ledger holding every capture
        evening = ledger.find(data, stem)
        self.assertEqual(len(evening["captures"]), 1 + 2 * self.ROUNDS)
        json.dumps(data)
        self.assertTrue(text.startswith("---\n") and "## Sam" in text and "## Ada's annotations" in text)


if __name__ == "__main__":
    unittest.main()
