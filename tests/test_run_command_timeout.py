"""Characterization tests for run_powershell's timeout handling: the timeout
must kill the WHOLE process tree and return promptly even when a nested shell
(grandchild) inherits the stdout/stderr pipes — the 2026-07-23 build_cpp.ps1
hang, where killing only the direct child left communicate() blocked forever."""

import queue
import time
import unittest

from myagent.constants import IS_WINDOWS
from myagent.safety_mixin import SafetyMixin


class _Host(SafetyMixin):
    def __init__(self):
        self.queue = queue.Queue()

    def _check_command_safety(self, command):
        # Bypass pattern matching / Tk confirm dialogs — execution only.
        return "safe", None


class TimeoutCase(unittest.TestCase):
    def test_simple_timeout_returns_error(self):
        host = _Host()
        cmd = "Start-Sleep -Seconds 30" if IS_WINDOWS else "sleep 30"
        start = time.monotonic()
        result = host.run_powershell(cmd, timeout=2)
        elapsed = time.monotonic() - start
        self.assertIn("timed out after 2 seconds", result)
        self.assertLess(elapsed, 20)

    def test_grandchild_holding_pipes_does_not_block_return(self):
        # The nested shell inherits stdout/stderr; without the tree-kill the
        # post-timeout pipe read blocks until the grandchild's 30s sleep ends.
        host = _Host()
        if IS_WINDOWS:
            cmd = 'powershell -NoProfile -Command "Start-Sleep -Seconds 30"'
        else:
            cmd = "/bin/bash -c 'sleep 30'"
        start = time.monotonic()
        result = host.run_powershell(cmd, timeout=2)
        elapsed = time.monotonic() - start
        self.assertIn("timed out", result)
        self.assertLess(elapsed, 20)

    def test_fast_command_unaffected(self):
        host = _Host()
        cmd = "Write-Output hello" if IS_WINDOWS else "echo hello"
        result = host.run_powershell(cmd, timeout=30)
        self.assertIn("hello", result)


if __name__ == "__main__":
    unittest.main()
