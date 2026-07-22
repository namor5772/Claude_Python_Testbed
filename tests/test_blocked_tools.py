"""Characterization tests for the per-instruction hard tool blocklist:
_filter_blocked_tools stripping (tools never offered) and the _execute_tool
first-gate refusal (tools never executed) — the deterministic guarantee for
unattended runs, independent of model compliance."""

import queue
import unittest
from types import SimpleNamespace

from myagent.streaming_mixin import StreamingMixin


class _Host(StreamingMixin):
    def __init__(self, blocked):
        self.queue = queue.Queue()
        self._blocked_tools = blocked


class FilterCase(unittest.TestCase):
    TOOLS = [{"name": "run_command"}, {"name": "gmail_trash"}, {"name": "read_file"}]

    def test_empty_or_none_blocklist_is_noop(self):
        self.assertIs(StreamingMixin._filter_blocked_tools(self.TOOLS, None), self.TOOLS)
        self.assertIs(StreamingMixin._filter_blocked_tools(self.TOOLS, set()), self.TOOLS)

    def test_strips_only_named_tools(self):
        out = StreamingMixin._filter_blocked_tools(self.TOOLS, {"gmail_trash"})
        self.assertEqual([t["name"] for t in out], ["run_command", "read_file"])

    def test_covers_namespaced_names(self):
        tools = [{"name": "filesystem__delete"}, {"name": "filesystem__read"}]
        out = StreamingMixin._filter_blocked_tools(tools, {"filesystem__delete"})
        self.assertEqual([t["name"] for t in out], ["filesystem__read"])


class DispatchGateCase(unittest.TestCase):
    def test_blocked_tool_is_refused_not_executed(self):
        host = _Host({"gmail_trash"})
        block = SimpleNamespace(name="gmail_trash", input={"account": "x", "ids": ["1"]})
        result = host._execute_tool(block)
        self.assertIn("HARD-BLOCKED", result)
        self.assertIn("gmail_trash", result)
        warn = host.queue.get_nowait()
        self.assertEqual(warn["type"], "warning")
        self.assertIn("gmail_trash", warn["content"])

    def test_gate_runs_before_all_routing(self):
        # A blocked MCP-namespaced name is refused even though no MCP state,
        # mail flags, or tool checkboxes exist on this bare host — proof the
        # gate is the first check, ahead of every router that would otherwise
        # need those attributes and raise AttributeError.
        host = _Host({"filesystem__delete"})
        block = SimpleNamespace(name="filesystem__delete", input={})
        result = host._execute_tool(block)
        self.assertIn("HARD-BLOCKED", result)


if __name__ == "__main__":
    unittest.main()
