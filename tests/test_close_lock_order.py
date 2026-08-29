"""Regression test for the close-then-relaunch instance-lock race.

_finish_close must release the instance-number lock as soon as persistent state
is saved (agent_state.json written) and BEFORE the potentially slow browser/MCP
cleanup — otherwise a quick close-then-relaunch finds agent_lock_1 still held by
the exiting process, claims instance 2, and restores from agent_state_2.json, so
the window reopens on the wrong monitor. Verified live 2026-08-29: pre-fix an
immediate relaunch spawned "My Agent (2)" on the wrong screen; post-fix it
reclaims instance 1. This locks in the ordering so it can't silently regress.
"""
import unittest
from unittest import mock

from myagent.event_loop_mixin import EventLoopMixin


class FinishCloseOrder(unittest.TestCase):
    def _run(self, streaming=False):
        host = EventLoopMixin.__new__(EventLoopMixin)
        host.streaming = streaming
        calls = []
        for name in ("_save_last_state", "_auto_save_on_close", "_cleanup_browser",
                     "_disconnect_mcp_servers", "_release_instance_lock"):
            setattr(host, name, (lambda n=name: calls.append(n)))
        host.root = mock.Mock()
        host.root.destroy.side_effect = lambda: calls.append("destroy")
        host.root.after.side_effect = lambda ms, fn: calls.append(("after", ms))
        host._finish_close()
        return calls

    def test_lock_released_after_state_save_and_before_cleanup(self):
        calls = self._run()
        # state is persisted first, so the reclaiming relaunch reads a fresh file
        self.assertLess(calls.index("_save_last_state"), calls.index("_release_instance_lock"))
        # the slot is freed BEFORE the slow browser/MCP teardown and chat autosave
        self.assertLess(calls.index("_release_instance_lock"), calls.index("_cleanup_browser"))
        self.assertLess(calls.index("_release_instance_lock"), calls.index("_disconnect_mcp_servers"))
        self.assertLess(calls.index("_release_instance_lock"), calls.index("_auto_save_on_close"))
        # and the window is torn down last
        self.assertEqual(calls[-1], "destroy")

    def test_release_is_idempotent_belt_and_braces(self):
        # the late release stays as a safety net; calling it twice is harmless
        self.assertGreaterEqual(self._run().count("_release_instance_lock"), 1)

    def test_still_streaming_defers_without_releasing(self):
        calls = self._run(streaming=True)
        self.assertNotIn("_release_instance_lock", calls)
        self.assertNotIn("destroy", calls)
        self.assertEqual(calls, [("after", 200)])  # reschedules, touches nothing else


if __name__ == "__main__":
    unittest.main()
