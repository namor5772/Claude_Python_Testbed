"""Wiring tests for the cost log's INSTRUCTION / CALLS fields (2026-08-16):
stream_worker must hand _log_api_cost the instruction name it snapshotted at
run start and the final "Call #N" counter — on BOTH loop-end paths (normal
completion and the exception path), because call_num is hoisted above the
try exactly so a failed run still records how far it got.

The provider call is stubbed at the dispatch seam (_stream_anthropic_call):
call 1 returns a tool_use block (so the loop goes round again), call 2 ends
the turn (or raises, for the exception-path case). Everything else the loop
touches is the thinnest stand-in that lets it run headless in a test."""

import queue
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.streaming_mixin as sm
from myagent.helpers import _ToolBlock


class _Var:
    def __init__(self, v):
        self._v = v

    def get(self):
        return self._v


class _Host(sm.StreamingMixin):
    """Minimal stream_worker host: Anthropic provider, two-call script."""

    def __init__(self, second_call="end", instruction="Balance Westpac"):
        self.provider = "Anthropic"
        self.model = "claude-sonnet-5"
        self.temperature = 1.0
        self._temp_var = _Var(1.0)
        self.queue = queue.Queue()
        self.thinking_enabled = False
        self.stop_requested = False
        self._headless = False
        self._result_file = None
        self.agent_instruction_name = instruction
        self._second_call = second_call
        self._calls_made = 0
        self.tool_calls = []

    # --- collaborators stream_worker reaches for ---
    def _weak_desktop_combo_warning(self):
        return None

    def _payload_for_display(self, messages):
        return ""

    def _tool_info(self, msg):
        pass

    def _get_model_param_summary(self):
        return "mode=Adaptive"

    def _execute_tool(self, block):
        self.tool_calls.append(block.name)
        return "ok"

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        self._calls_made += 1
        usage = {"input_tokens": 1000, "output_tokens": 100}
        if self._calls_made == 1:
            block = _ToolBlock("read_file", "toolu_1", {"path": "x"})
            return "tool_use", [block], "", False, True, usage
        if self._second_call == "raise":
            raise RuntimeError("boom on call 2")
        return "end_turn", [], "done", False, True, usage


class CostLogRunFieldsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "APICostLog_test.txt"
        patcher = mock.patch.object(sm, "APICOST_LOG_FILE", str(self.log))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fields(self):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        return lines[0].split(";")

    def test_completed_run_logs_instruction_and_call_count(self):
        host = _Host(second_call="end")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(len(f), 8)
        self.assertEqual(f[6], "Balance Westpac")
        # Two API round-trips: the tool_use turn plus the final answer.
        self.assertEqual(f[7], "2")
        self.assertEqual(host.tool_calls, ["read_file"])

    def test_failed_run_still_logs_calls_reached(self):
        # The exception path logs whatever the run got through: the failing
        # call was Call #2 (what the output window showed when it died).
        host = _Host(second_call="raise")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(f[6], "Balance Westpac")
        self.assertEqual(f[7], "2")

    def test_instruction_snapshotted_at_run_start(self):
        # An instruction applied while the run is in flight must not relabel
        # the entry: the name is captured before the loop starts.
        host = _Host(second_call="end")
        real_call = host._stream_anthropic_call

        def relabel_then_call(*a, **k):
            host.agent_instruction_name = "Something Else"
            return real_call(*a, **k)

        host._stream_anthropic_call = relabel_then_call
        host.stream_worker([{"role": "user", "content": "go"}])
        self.assertEqual(self._fields()[6], "Balance Westpac")

    def test_ad_hoc_run_logs_blank_instruction(self):
        host = _Host(second_call="end", instruction="")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(f[6], "")
        self.assertEqual(f[7], "2")


if __name__ == "__main__":
    unittest.main()
