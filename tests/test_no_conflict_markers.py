"""No tracked file carries a merge's conflict markers: a merge committed with them passes every other test when the
files are docs."""
import subprocess
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class ConflictMarkerTest(unittest.TestCase):
    def test_no_tracked_file_has_conflict_markers(self):
        try:
            out = subprocess.run(["git", "grep", "-n", "-I", "-E", "^(<{7} |>{7} )", "--", "."], cwd=REPO,
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            self.skipTest("git is not available")
        if out.returncode not in (0, 1):
            self.skipTest("not a git checkout")
        self.assertEqual(out.stdout.strip(), "", "conflict markers left in tracked files")


if __name__ == "__main__":
    unittest.main()
