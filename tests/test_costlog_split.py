"""The cost log's per-model split (13th field, 2026-09-27).

A run that more than one model served — a Model upgrade from an Agent Request
reply on (model_upgrade_mixin), or an Anthropic refusal fallback served by an
Opus tier — is logged as ONE row under the model it ended on, whose cost and
token fields are the run's totals, plus a 13th field holding each model's own
share: "model,cost,in,out,cache_w,cache_r" per model, joined by "|". The
viewers' By-model summaries split such a run between its models
(tests/test_costlog_viewer_days.py runs the real viewer on one); a one-model
run keeps its 12 fields exactly.

Pinned here: the field's format and when it is written; stream_worker keying
each call by the model that served it — the configured model at pricing time
(the switch happens between calls), or the model the provider reports (a
refusal fallback), a dated snapshot of the configured model folded into it —
with each call's cost at that model's rates.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.streaming_mixin as sm
from tests.test_costlog_run_fields import _Host


class SplitFieldTests(unittest.TestCase):

    def test_nothing_to_split_writes_nothing(self):
        f = sm.StreamingMixin._cost_split_field
        self.assertEqual(f(None), "")
        self.assertEqual(f({}), "")
        self.assertEqual(f({"claude-sonnet-5": [0.5, 1, 2, 3, 4]}), "")

    def test_each_model_its_cost_and_four_buckets_first_used_first(self):
        split = {"claude-sonnet-5": [0.0264, 2, 60, 10324, 0],
                 "claude-fable-5-1": [0.14097, 2, 226, 10372, 0]}
        self.assertEqual(sm.StreamingMixin._cost_split_field(split),
                         "claude-sonnet-5,0.026400,2,60,10324,0"
                         "|claude-fable-5-1,0.140970,2,226,10372,0")

    def test_a_separator_in_a_model_id_cannot_split_the_field(self):
        split = {"odd,id|x;y": [0.1, 1, 1, 1, 1], "b": [0.2, 1, 1, 1, 1]}
        self.assertTrue(sm.StreamingMixin._cost_split_field(split)
                        .startswith("odd_id_x_y,0.100000,"))


class _LogHost(sm.StreamingMixin):
    def __init__(self):
        self.provider, self.model = "Anthropic", "claude-fable-5-1"
        self.infos = []

    def _get_model_param_summary(self):
        return "mode=Max upgraded-from=claude-sonnet-5@call1"

    def _tool_info(self, msg):
        self.infos.append(msg)


class _Isolated(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "APICostLog_test.txt"
        patcher = mock.patch.object(sm, "APICOST_LOG_FILE", str(self.log))
        patcher.start()
        self.addCleanup(patcher.stop)

    def fields(self):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        return lines[0].split(";")


class LogLineTests(_Isolated):

    def test_a_two_model_run_gets_the_13th_field(self):
        split = {"claude-sonnet-5": [0.0264, 2, 60, 10324, 0],
                 "claude-fable-5-1": [0.141, 2, 226, 10372, 0]}
        _LogHost()._log_api_cost(0.1674, had_usage=True, duration_secs=7,
                                 instruction="ZZ", calls=2,
                                 tokens=(4, 286, 20696, 0), split=split)
        f = self.fields()
        self.assertEqual(len(f), 13)
        self.assertEqual(f[2:4], ["claude-fable-5-1", "0.1674"])
        self.assertEqual(f[8:12], ["4", "286", "20696", "0"])
        self.assertEqual(f[12], "claude-sonnet-5,0.026400,2,60,10324,0"
                                "|claude-fable-5-1,0.141000,2,226,10372,0")

    def test_a_one_model_run_keeps_its_12_fields(self):
        _LogHost()._log_api_cost(0.5, had_usage=True, tokens=(1, 2, 3, 4),
                                 split={"claude-fable-5-1": [0.5, 1, 2, 3, 4]})
        self.assertEqual(len(self.fields()), 12)


class _SwitchingHost(_Host):
    """_Host (call 1 a tool round, call 2 the answer; 1000 in / 100 out each)
    whose tool round moves the run to Fable 5.1 — as the Upgrade box does,
    between calls."""

    def _execute_tool(self, block):
        self.model = "claude-fable-5-1"
        return super()._execute_tool(block)


class _ReportingHost(_Host):
    """_Host whose calls report the model that served them."""

    def __init__(self, served):
        super().__init__()
        self._served = list(served)

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        result = super()._stream_anthropic_call(messages, max_retries, label_emitted)
        result[5]["model"] = self._served.pop(0)
        return result


class StreamWorkerSplitTests(_Isolated):

    def run_host(self, host):
        host.stream_worker([{"role": "user", "content": "go"}])
        return self.fields()

    def test_a_run_moved_to_another_model_is_split_at_each_models_rates(self):
        f = self.run_host(_SwitchingHost())
        # Sonnet 5 $2 / $10 per MTok, Fable 5.1 $10 / $50: 0.003 + 0.015.
        self.assertEqual(f[2:4], ["claude-fable-5-1", "0.0180"])
        self.assertEqual(f[8:12], ["2000", "200", "0", "0"])
        self.assertEqual(f[12], "claude-sonnet-5,0.003000,1000,100,0,0"
                                "|claude-fable-5-1,0.015000,1000,100,0,0")

    def test_a_refusal_fallback_served_by_opus_is_split_too(self):
        f = self.run_host(_ReportingHost(["claude-sonnet-5", "claude-opus-5"]))
        # Opus 5 $5 / $25: 0.005 + 0.0025 for the fallback-served call.
        self.assertEqual(f[3], "0.0105")
        self.assertEqual(f[12], "claude-sonnet-5,0.003000,1000,100,0,0"
                                "|claude-opus-5,0.007500,1000,100,0,0")

    def test_a_dated_snapshot_of_the_configured_model_is_the_same_model(self):
        f = self.run_host(_ReportingHost(["claude-sonnet-5-20260801"] * 2))
        self.assertEqual(len(f), 12)
        self.assertEqual(f[3], "0.0060")

    def test_a_one_model_run_has_no_split(self):
        self.assertEqual(len(self.run_host(_Host())), 12)


if __name__ == "__main__":
    unittest.main()
