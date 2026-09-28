"""Regression tests for the 2026-09-28 review fixes in the agent loop.

- stop_reason "pause_turn" (a long Anthropic server-tool turn the server
  paused) hands the partial turn back and the loop resumes it, instead of the
  run ending on a partial answer;
- a reply cut off at the output-token limit (stop_reason "max_tokens") warns,
  runs no half-written tool call, and a waited parent's result file says why;
- STOP pressed during one of a turn's sequential tools stops the rest of them
  (each still gets a tool_result, so the pairing holds);
- the turn's text is never appended to the history a second time after STOP;
- the context-overflow dead end ends the run through the error path;
- _finish_close always reaches root.destroy(), whatever a step raises;
- an Ollama call's cost line says the call is free, not "unpriced".

The provider call is stubbed at the dispatch seam, as in
tests/test_costlog_run_fields.py (whose _Host this reuses).
"""

import io
import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import myagent.streaming_mixin as sm
from myagent.event_loop_mixin import EventLoopMixin
from myagent.helpers import _ToolBlock
from tests.test_costlog_run_fields import _Host, _OllamaHost
from tests.test_fast_mode import _WireHost, _bad_request


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


class _ScriptHost(_Host):
    """_Host whose provider calls follow a script of (stop_reason,
    content_blocks, full_text); every call records the history it was sent,
    and a tool may run an effect on the host (e.g. press STOP)."""

    def __init__(self, script, effects=None):
        super().__init__()
        self.script = list(script)
        self.sent = []
        self.effects = effects or {}
        # A result file makes the run a subagent, which schedules its own close.
        self.root = SimpleNamespace(after=lambda *a: None)

    def _on_close(self):
        pass

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        self.sent.append([dict(m) for m in messages])
        stop, blocks, text = self.script.pop(0)
        return stop, blocks, text, False, True, {"input_tokens": 10, "output_tokens": 5}

    def _execute_tool(self, block):
        self.tool_calls.append(block.name)
        effect = self.effects.get(block.name)
        if effect:
            effect(self)
        return "ok"


class LoopTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        patcher = mock.patch.object(sm, "APICOST_LOG_FILE", str(self.dir / "cost.txt"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_host(self, host):
        host._result_file = str(self.dir / "result.json")
        messages = [{"role": "user", "content": "go"}]
        host.stream_worker(messages)
        with open(host._result_file, encoding="utf-8") as f:
            return messages, json.load(f)

    def test_pause_turn_hands_the_partial_turn_back_and_resumes(self):
        partial = [{"type": "server_tool_use", "id": "srvtoolu_1",
                    "name": "web_search", "input": {"query": "tides"}}]
        host = _ScriptHost([("pause_turn", partial, ""), ("end_turn", [], "High tide at 6.")])
        messages, result = self.run_host(host)
        self.assertEqual(len(host.sent), 2)            # it went round again
        self.assertEqual(host.sent[1][-1], {"role": "assistant", "content": partial})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["final_text"], "High tide at 6.")

    def test_a_reply_cut_off_at_max_tokens_warns_and_says_why(self):
        cut = [_ToolBlock("write_file", "toolu_1", {"path": "big.txt"})]
        host = _ScriptHost([("max_tokens", cut, "")])
        _messages, result = self.run_host(host)
        self.assertEqual(host.tool_calls, [])          # the half-written call never runs
        warnings = [m["content"] for m in _drain(host.queue) if m["type"] == "warning"]
        self.assertTrue(any("cut off" in w and "was not run" in w for w in warnings), warnings)
        self.assertEqual(result["status"], "error")
        self.assertIn("stop_reason=max_tokens", result["error"])

    def test_stop_skips_the_rest_of_the_turns_sequential_tools(self):
        blocks = [_ToolBlock("mouse_click", "t1", {"x": 1, "y": 2}),
                  _ToolBlock("press_key", "t2", {"key": "enter"})]
        host = _ScriptHost([("tool_use", blocks, "Clicking, then Enter.")],
                           effects={"mouse_click": lambda h: setattr(h, "stop_requested", True)})
        messages, result = self.run_host(host)
        self.assertEqual(host.tool_calls, ["mouse_click"])     # Enter was never pressed
        results = messages[-1]["content"]
        self.assertEqual([r["tool_use_id"] for r in results], ["t1", "t2"])
        self.assertEqual(results[1]["content"], "[Not run: the user pressed STOP]")
        self.assertEqual(result["status"], "stopped")

    def test_the_turns_text_is_not_appended_twice_after_a_stop(self):
        blocks = [_ToolBlock("mouse_click", "t1", {})]
        host = _ScriptHost([("tool_use", blocks, "Clicking.")],
                           effects={"mouse_click": lambda h: setattr(h, "stop_requested", True)})
        messages, _result = self.run_host(host)
        # user, the tool turn (its text is inside it), the tool result — and
        # no fourth, duplicate assistant message after the loop.
        self.assertEqual([m["role"] for m in messages], ["user", "assistant", "user"])

    def test_ollama_calls_are_marked_free(self):
        host = _OllamaHost()
        host.stream_worker([{"role": "user", "content": "go"}])
        costs = [m for m in _drain(host.queue) if m["type"] == "cost_update"]
        self.assertTrue(costs)
        self.assertTrue(all(m["free"] and m["call_cost"] is None for m in costs))


class CostLineTests(unittest.TestCase):

    MSG = {"input_tokens": 5, "output_tokens": 2, "cache_write_tokens": 0,
           "cache_read_tokens": 0, "call_cost": None, "total_cost": None}

    def test_a_free_call_says_so(self):
        self.assertIn("no charge", EventLoopMixin._cost_line(dict(self.MSG, free=True)))

    def test_an_unpriced_paid_call_is_still_unpriced(self):
        line = EventLoopMixin._cost_line(dict(self.MSG, free=False))
        self.assertIn("unpriced", line)
        self.assertIn("unpriced", EventLoopMixin._cost_line(self.MSG))   # no key at all


class ContextOverflowTests(unittest.TestCase):

    def test_nothing_left_to_trim_ends_the_run_through_the_error_path(self):
        host = _WireHost(fast=False, fail_first=_bad_request(
            "prompt is too long: 250000 tokens > 200000 maximum"))
        with self.assertRaises(RuntimeError) as ctx:
            host._stream_anthropic_call([{"role": "user", "content": "hi"}], 3, True)
        self.assertIn("nothing left to trim", str(ctx.exception))
        queued = _drain(host.queue)
        # The advice is a warning; an "error" here used to end the run in the
        # UI while the worker was still finishing.
        self.assertTrue(any(m["type"] == "warning" and "Context window exceeded"
                            in m["content"] for m in queued))
        self.assertNotIn("error", [m["type"] for m in queued])


class _CloseHost(EventLoopMixin):
    def __init__(self):
        self.streaming = False
        self.calls = []
        self.root = SimpleNamespace(destroy=lambda: self.calls.append("destroy"),
                                    after=lambda *a: None)

    def _save_last_state(self):
        self.calls.append("save")
        raise OSError("disk full")

    def _release_instance_lock(self):
        self.calls.append("release")

    def _auto_save_on_close(self):
        self.calls.append("chat")
        raise RuntimeError("chat folder vanished")

    def _cleanup_browser(self):
        self.calls.append("browser")

    def _disconnect_mcp_servers(self):
        self.calls.append("mcp")


class FinishCloseTests(unittest.TestCase):

    def test_every_step_runs_and_the_window_is_destroyed_whatever_raises(self):
        host = _CloseHost()
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            host._finish_close()
        self.assertEqual(host.calls,
                         ["save", "release", "chat", "browser", "mcp", "release", "destroy"])
        self.assertIn("_save_last_state", err.getvalue())
        self.assertIn("_auto_save_on_close", err.getvalue())

    def test_no_stderr_is_no_problem(self):          # pythonw: sys.stderr is None
        host = _CloseHost()
        with mock.patch("sys.stderr", None):
            host._finish_close()
        self.assertEqual(host.calls[-1], "destroy")


if __name__ == "__main__":
    unittest.main()
