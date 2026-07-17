"""Characterization tests for myagent.helpers.rotate_log_if_needed — the
one-slot size-cap rotation shared by heartbeat.log (Heartbeat.py wraps it,
see test_heartbeat.py) and APICostLog.txt (MyAgent's and SelfBot's
_log_api_cost call it before each append)."""

import tempfile
import unittest
from pathlib import Path

from myagent.helpers import rotate_log_if_needed


class RotateLogHelperTests(unittest.TestCase):
    CAP = 500

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "APICostLog.txt"
        self.old = self.log.with_name("APICostLog.txt.old")

    def test_missing_log_returns_false(self):
        self.assertFalse(rotate_log_if_needed(self.log, self.CAP))
        self.assertFalse(self.log.exists())
        self.assertFalse(self.old.exists())

    def test_log_at_cap_returns_false_untouched(self):
        content = "x" * self.CAP
        self.log.write_text(content, encoding="utf-8")
        self.assertFalse(rotate_log_if_needed(self.log, self.CAP))
        self.assertEqual(self.log.read_text(encoding="utf-8"), content)
        self.assertFalse(self.old.exists())

    def test_over_cap_archives_and_seeds_marker(self):
        content = "x" * (self.CAP + 1)
        self.log.write_text(content, encoding="utf-8")
        self.assertTrue(rotate_log_if_needed(self.log, self.CAP))
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)
        fresh = self.log.read_text(encoding="utf-8")
        self.assertIn("log restarted", fresh)
        self.assertIn("APICostLog.txt.old", fresh)
        self.assertEqual(len(fresh.splitlines()), 1)

    def test_rotation_replaces_previous_archive(self):
        self.old.write_text("ancient history", encoding="utf-8")
        content = "y" * (self.CAP + 1)
        self.log.write_text(content, encoding="utf-8")
        self.assertTrue(rotate_log_if_needed(self.log, self.CAP))
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)

    def test_accepts_str_path(self):
        content = "z" * (self.CAP + 1)
        self.log.write_text(content, encoding="utf-8")
        self.assertTrue(rotate_log_if_needed(str(self.log), self.CAP))
        self.assertTrue(self.old.exists())


if __name__ == "__main__":
    unittest.main()
