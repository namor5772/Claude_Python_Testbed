"""Wiring test for the catch-all pricing-row warning (2026-09-16).

stream_worker posts an always-shown ⚠ at run start when the active model is
priced by a GENERIC_PRICING_PREFIXES row — the bare "gemini-3" fallback that
silently under-priced 3.6, 3.7 and 3.8 Flash in turn, each for the weeks
between its GA and its own pricing row — and stays silent for a model with a
row of its own. It is a "warning" (shown whether or not Activity is ticked),
unlike the Activity-gated weak-desktop "tool_info" note, because every cost
line of the run may be wrong. Drives the real loop on the _Host harness from
tests/test_costlog_run_fields.py, re-pointed at the Google provider."""

import queue
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.streaming_mixin as sm
from tests.test_costlog_run_fields import _Host


class _GoogleHost(_Host):
    """_Host on the Google provider: the loop dispatches to
    _stream_gemini_call, which replays the two-call Anthropic script."""

    def __init__(self, model):
        super().__init__(second_call="end", instruction="Gemini run")
        self.provider = "Google"
        self.model = model

    def _stream_gemini_call(self, messages, max_retries, label_emitted):
        return self._stream_anthropic_call(messages, max_retries, label_emitted)


class GenericPricingWarningWiringTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patcher = mock.patch.object(
            sm, "APICOST_LOG_FILE", str(Path(tmp.name) / "APICostLog_test.txt"))
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _warnings(host):
        out = []
        while True:
            try:
                msg = host.queue.get_nowait()
            except queue.Empty:
                return out
            if msg.get("type") == "warning":
                out.append(str(msg.get("content")))

    def test_catch_all_priced_model_gets_a_run_start_warning(self):
        host = _GoogleHost("gemini-3.9-flash")   # no row of its own (yet)
        host.stream_worker([{"role": "user", "content": "go"}])
        notes = [w for w in self._warnings(host)
                 if "generic 'gemini-3' fallback row" in w]
        self.assertEqual(len(notes), 1, notes)
        self.assertTrue(notes[0].startswith("⚠ gemini-3.9-flash"), notes[0])
        # The run itself still prices on the catch-all row and completes —
        # the warning is additive, not a refusal.
        self.assertEqual(host._calls_made, 2)

    def test_model_with_its_own_row_is_silent(self):
        host = _GoogleHost("gemini-3.8-flash")
        host.stream_worker([{"role": "user", "content": "go"}])
        self.assertEqual(
            [w for w in self._warnings(host) if "fallback row" in w], [])
        self.assertEqual(host._calls_made, 2)


if __name__ == "__main__":
    unittest.main()
