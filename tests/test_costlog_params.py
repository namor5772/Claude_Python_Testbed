"""Characterization tests for the cost-log line format written by
StreamingMixin._log_api_cost — since 2026-08-10 a ;params field (the
comma-joined _get_model_param_summary() string) records the thinking /
temperature settings alongside the cost, since 2026-08-12 a 6th ;secs
field records the run's wall-clock duration in whole seconds (blank when
duration_secs is None — older history), and since 2026-08-16 a 7th
;instruction field (the saved Agent Instruction the run was launched from,
whitespace-collapsed, ';' -> ',' — blank for an ad-hoc run) and an 8th
;calls field (the run's API-call count, the "Call #N" counter — blank when
None) complete the line. The viewers (CostLog_Win.ps1 / view_costlog.command)
render params as a trailing PARAMETERS column, secs as TIME(sec) right of
COST(USD), calls as CALLS right of that and instruction as INSTRUCTION after
MODEL, and must keep accepting 4-, 5- and 6-field lines, so the writer
contract here is: exactly eight ;-separated fields, calls last (possibly
empty), instruction before it, secs (possibly empty) 6th, params 5th.

Since 2026-08-12 the zero-cost gate has one exception: Ollama runs that made
at least one completed call (had_usage) log a 0.0000 line — local activity is
recorded without inflating spend — while zero-cost runs on paid providers
(unmatched pricing prefix, STOP before the first result) stay skipped."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.streaming_mixin as sm


class _Host(sm.StreamingMixin):
    """Bare host exposing only what _log_api_cost touches."""

    def __init__(self, summary, provider="xAI", model="grok-4.5"):
        self.provider = provider
        self.model = model
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
        line = self.log.read_text(encoding="utf-8").strip("\n")
        fields = line.split(";")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[1], "xAI")
        self.assertEqual(fields[2], "grok-4.5")
        self.assertEqual(fields[3], "0.3742")
        # The space-joined title-bar summary is comma-joined in the log
        self.assertEqual(fields[4], "reasoning=Medium, temp=1")
        # No duration passed -> blank TIME(sec) field
        self.assertEqual(fields[5], "")
        # No instruction / calls passed -> blank INSTRUCTION and CALLS fields
        self.assertEqual(fields[6], "")
        self.assertEqual(fields[7], "")

    def test_empty_summary_leaves_params_field_blank(self):
        host = _Host("")
        host._log_api_cost(0.01)
        line = self.log.read_text(encoding="utf-8").strip("\n")
        fields = line.split(";")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[4], "")

    def test_duration_written_as_whole_seconds_sixth_field(self):
        host = _Host("mode=Adaptive")
        host._log_api_cost(0.3742, had_usage=True, duration_secs=137.4)
        fields = self.log.read_text(encoding="utf-8").strip("\n").split(";")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[5], "137")

    def test_instruction_seventh_and_calls_eighth_fields(self):
        host = _Host("mode=Adaptive", provider="Anthropic",
                     model="claude-sonnet-5")
        host._log_api_cost(0.1234, had_usage=True, duration_secs=42.0,
                           instruction="Balance Westpac Mastercard account",
                           calls=7)
        fields = self.log.read_text(encoding="utf-8").strip("\n").split(";")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[5], "42")
        # Spaces inside a name are fine — only ';' is the delimiter
        self.assertEqual(fields[6], "Balance Westpac Mastercard account")
        # An integer, no decimals, no padding
        self.assertEqual(fields[7], "7")

    def test_instruction_name_is_sanitized_so_it_cannot_split_the_line(self):
        # A ';' in a user-typed name would add a phantom field; a newline
        # would end the record early. Both are neutralised, whitespace
        # collapsed — the field count stays exactly eight.
        host = _Host("mode=Adaptive")
        host._log_api_cost(0.5, instruction="  odd; name\nwith   breaks ",
                           calls=3)
        fields = self.log.read_text(encoding="utf-8").strip("\n").split(";")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[6], "odd, name with breaks")
        self.assertEqual(fields[7], "3")

    def test_none_instruction_and_calls_render_blank(self):
        # instruction=None (attribute missing) and calls=None both degrade to
        # blank fields rather than the strings "None".
        host = _Host("mode=Adaptive")
        host._log_api_cost(0.5, instruction=None, calls=None)
        fields = self.log.read_text(encoding="utf-8").strip("\n").split(";")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[6], "")
        self.assertEqual(fields[7], "")

    def test_zero_cost_skips_write(self):
        host = _Host("mode=Adaptive")
        host._log_api_cost(0)
        self.assertFalse(self.log.exists())

    def test_ollama_zero_cost_with_usage_logs_free_line(self):
        host = _Host("thinking=on temp=1", provider="Ollama",
                     model="muse-glimmer:30b-mlx")
        host._log_api_cost(0.0, had_usage=True, duration_secs=268.7,
                           instruction="GeneralChatAgent_MacOS_Ollama_gemma",
                           calls=12)
        fields = self.log.read_text(encoding="utf-8").strip("\n").split(";")
        self.assertEqual(len(fields), 8)
        self.assertEqual(fields[1], "Ollama")
        self.assertEqual(fields[2], "muse-glimmer:30b-mlx")
        self.assertEqual(fields[3], "0.0000")
        self.assertEqual(fields[4], "thinking=on, temp=1")
        self.assertEqual(fields[5], "269")
        self.assertEqual(fields[6], "GeneralChatAgent_MacOS_Ollama_gemma")
        self.assertEqual(fields[7], "12")

    def test_ollama_zero_cost_without_usage_skips(self):
        # STOP before the first result: nothing ran, nothing to record.
        host = _Host("thinking=on", provider="Ollama", model="qwen3:32b-q4_K_M")
        host._log_api_cost(0.0, had_usage=False)
        self.assertFalse(self.log.exists())

    def test_paid_provider_zero_cost_with_usage_still_skips(self):
        # An unmatched pricing prefix on a PAID provider must not fabricate
        # a $0.0000 line — the price is unknown, not free.
        host = _Host("mode=Adaptive", provider="Anthropic",
                     model="claude-future-99")
        host._log_api_cost(0.0, had_usage=True)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
