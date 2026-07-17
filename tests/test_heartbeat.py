"""Characterization tests for Heartbeat.py's one-slot log rotation.

rotate_log_if_needed reads the module-global LOG_FILE (not self), so these
tests repoint Heartbeat.LOG_FILE at a temp directory instead of using
_util.stub. log() resolves the same global at call time, so the marker line
lands in the temp log too.
"""

import tempfile
import unittest
from pathlib import Path

import Heartbeat


class RotateLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "heartbeat.log"
        self.old = self.log.with_name("heartbeat.log.old")
        orig = Heartbeat.LOG_FILE
        Heartbeat.LOG_FILE = self.log
        self.addCleanup(setattr, Heartbeat, "LOG_FILE", orig)

    def test_missing_log_is_a_noop(self):
        Heartbeat.rotate_log_if_needed()
        self.assertFalse(self.log.exists())
        self.assertFalse(self.old.exists())

    def test_log_at_cap_is_untouched(self):
        content = "x" * Heartbeat.LOG_MAX_BYTES
        self.log.write_text(content, encoding="utf-8")
        Heartbeat.rotate_log_if_needed()
        self.assertEqual(self.log.read_text(encoding="utf-8"), content)
        self.assertFalse(self.old.exists())

    def test_over_cap_archives_and_seeds_marker(self):
        content = "x" * (Heartbeat.LOG_MAX_BYTES + 1)
        self.log.write_text(content, encoding="utf-8")
        Heartbeat.rotate_log_if_needed()
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)
        fresh = self.log.read_text(encoding="utf-8")
        self.assertIn("log restarted", fresh)
        self.assertIn("heartbeat.log.old", fresh)
        self.assertEqual(len(fresh.splitlines()), 1)

    def test_rotation_replaces_previous_archive(self):
        self.old.write_text("ancient history", encoding="utf-8")
        content = "y" * (Heartbeat.LOG_MAX_BYTES + 1)
        self.log.write_text(content, encoding="utf-8")
        Heartbeat.rotate_log_if_needed()
        self.assertEqual(self.old.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main()
