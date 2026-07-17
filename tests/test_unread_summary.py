"""Wiring tests for UnreadSummary.py's log rotation. The rotation mechanics
are the shared myagent.helpers.rotate_log_if_needed (pinned by
test_log_rotation.py); these pin the module-global wrapper — LOG_FILE and
LOG_MAX_BYTES resolved at call time — same shape as test_heartbeat.py."""

import tempfile
import unittest
from pathlib import Path

import UnreadSummary


class RotateLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "unread_summary.log"
        self.old = self.log.with_name("unread_summary.log.old")
        orig = UnreadSummary.LOG_FILE
        UnreadSummary.LOG_FILE = self.log
        self.addCleanup(setattr, UnreadSummary, "LOG_FILE", orig)

    def test_under_cap_is_untouched(self):
        self.log.write_text("one line\n", encoding="utf-8")
        UnreadSummary.rotate_log_if_needed()
        self.assertEqual(self.log.read_text(encoding="utf-8"), "one line\n")
        self.assertFalse(self.old.exists())

    def test_over_cap_archives_and_seeds_marker(self):
        content = "x" * (UnreadSummary.LOG_MAX_BYTES + 1)
        self.log.write_text(content, encoding="utf-8")
        UnreadSummary.rotate_log_if_needed()
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)
        fresh = self.log.read_text(encoding="utf-8")
        self.assertIn("log restarted", fresh)
        self.assertIn("unread_summary.log.old", fresh)


if __name__ == "__main__":
    unittest.main()
