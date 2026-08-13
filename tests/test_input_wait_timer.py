"""Characterization tests for helpers.input_wait_timer — the accumulator
behind the cost log's TIME(sec) user-input exclusion (2026-08-13): every
dialog that parks the streaming thread on the user (do_user_prompt,
_request_confirmation, mail_common.confirm_action) wraps its blocking wait
in this context manager, and stream_worker zeroes app._input_wait_secs at
run start then subtracts it from the wall clock before _log_api_cost — so
TIME(sec) measures the agent working, not the user's response latency.

The contract: seconds accumulate ACROSS waits within one run; the attribute
initializes from 0.0 when absent (a dialog outside a run must not crash);
and the finally path accumulates even when the wrapped wait raises."""

import unittest
from unittest import mock

from myagent import helpers


class _App:
    """Bare stand-in for the App: the timer only touches _input_wait_secs."""


class InputWaitTimerTests(unittest.TestCase):
    def test_initializes_attribute_and_records_elapsed(self):
        app = _App()
        with mock.patch.object(helpers.time, "monotonic",
                               side_effect=[100.0, 107.5]):
            with helpers.input_wait_timer(app):
                pass
        self.assertAlmostEqual(app._input_wait_secs, 7.5)

    def test_accumulates_across_multiple_waits(self):
        app = _App()
        app._input_wait_secs = 2.0
        with mock.patch.object(helpers.time, "monotonic",
                               side_effect=[10.0, 13.0]):
            with helpers.input_wait_timer(app):
                pass
        self.assertAlmostEqual(app._input_wait_secs, 5.0)

    def test_accumulates_even_when_wait_raises(self):
        app = _App()
        with mock.patch.object(helpers.time, "monotonic",
                               side_effect=[50.0, 51.0]):
            with self.assertRaises(RuntimeError):
                with helpers.input_wait_timer(app):
                    raise RuntimeError("dialog blew up")
        self.assertAlmostEqual(app._input_wait_secs, 1.0)


if __name__ == "__main__":
    unittest.main()
