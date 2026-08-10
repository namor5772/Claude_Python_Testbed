"""Characterization tests for the cost-log line format written by
StreamingMixin._log_api_cost — since 2026-08-10 a 5th ;params field (the
comma-joined _get_model_param_summary() string) records the thinking /
temperature settings alongside the cost. The viewers (CostLog_Win.ps1 /
view_costlog.command) render it as a trailing PARAMETERS column and must
keep accepting the old 4-field lines, so the writer contract here is:
exactly five ;-separated fields, params last, possibly empty."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.streaming_mixin as sm


class _Host(sm.StreamingMixin):
    """Bare host exposing only what _log_api_cost touches."""

    def __init__(self, summary):
        self.provider = "xAI"
        self.model = "grok-4.5"
        self._summary = summary
        self.infos = []

    def _get_model_param_summary(self):
        return self._summary

    def _tool_info(self, msg):
        self.infos.append(msg)


class CostLogParamsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "APICostLog_test.txt"
        patcher = mock.patch.object(sm, "APICOST_LOG_FILE", str(self.log))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_line_has_params_fifth_field_comma_joined(self):
        host = _Host("reasoning=Medium temp=1")
        host._log_api_cost(0.3742)
        line = self.log.read_text(encoding="utf-8").strip()
        fields = line.split(";")
        self.assertEqual(len(fields), 5)
        self.assertEqual(fields[1], "xAI")
        self.assertEqual(fields[2], "grok-4.5")
        self.assertEqual(fields[3], "0.3742")
        # The space-joined title-bar summary is comma-joined in the log
        self.assertEqual(fields[4], "reasoning=Medium, temp=1")

    def test_empty_summary_leaves_params_field_blank(self):
        host = _Host("")
        host._log_api_cost(0.01)
        line = self.log.read_text(encoding="utf-8").strip()
        fields = line.split(";")
        self.assertEqual(len(fields), 5)
        self.assertEqual(fields[4], "")

    def test_zero_cost_skips_write(self):
        host = _Host("mode=Adaptive")
        host._log_api_cost(0)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
