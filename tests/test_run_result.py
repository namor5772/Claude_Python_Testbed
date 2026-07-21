"""Characterization tests for the subagent result channel: streaming_mixin's
_final_assistant_text extraction and _write_result_file JSON round-trip —
the return path behind run_instruction(wait=true)."""

import json
import queue
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from myagent.streaming_mixin import StreamingMixin


class _Host(StreamingMixin):
    def __init__(self):
        self.queue = queue.Queue()
        self.agent_instruction_name = "TestInstr"


class FinalTextCase(unittest.TestCase):
    def test_plain_string_content(self):
        msgs = [{"role": "user", "content": "task"},
                {"role": "assistant", "content": "the report"}]
        self.assertEqual(StreamingMixin._final_assistant_text(msgs), "the report")

    def test_skips_trailing_user_messages(self):
        msgs = [{"role": "assistant", "content": "answer"},
                {"role": "user", "content": [{"type": "tool_result",
                                              "tool_use_id": "t", "content": "ok"}]}]
        self.assertEqual(StreamingMixin._final_assistant_text(msgs), "answer")

    def test_joins_text_blocks_and_ignores_tool_use(self):
        msgs = [{"role": "assistant", "content": [
            {"type": "text", "text": "part one"},
            {"type": "tool_use", "id": "t", "name": "x", "input": {}},
            {"type": "text", "text": "part two"},
        ]}]
        self.assertEqual(StreamingMixin._final_assistant_text(msgs),
                         "part one\npart two")

    def test_sdk_style_block_objects(self):
        blk = SimpleNamespace(type="text", text="sdk text")
        msgs = [{"role": "assistant", "content": [blk]}]
        self.assertEqual(StreamingMixin._final_assistant_text(msgs), "sdk text")

    def test_skips_empty_assistant_and_falls_back(self):
        msgs = [{"role": "assistant", "content": "earlier real answer"},
                {"role": "assistant", "content": "   "}]
        self.assertEqual(StreamingMixin._final_assistant_text(msgs),
                         "earlier real answer")

    def test_no_assistant_returns_empty(self):
        self.assertEqual(StreamingMixin._final_assistant_text(
            [{"role": "user", "content": "only"}]), "")


class ResultFileCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "result.json"
        self.host = _Host()

    def test_writes_status_error_and_final_text(self):
        self.host._result_file = str(self.path)
        msgs = [{"role": "assistant", "content": "final words"}]
        self.host._write_result_file("error", msgs, error="boom")
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "error")
        self.assertEqual(data["error"], "boom")
        self.assertEqual(data["final_text"], "final words")
        self.assertEqual(data["instruction"], "TestInstr")

    def test_noop_without_result_file(self):
        self.host._write_result_file("completed", [])  # no _result_file attr
        self.assertFalse(self.path.exists())

    def test_write_failure_warns_instead_of_raising(self):
        self.host._result_file = str(self.path / "no_such_dir" / "r.json")
        self.host._write_result_file("completed", [])
        kind = self.host.queue.get_nowait()
        self.assertEqual(kind["type"], "warning")


if __name__ == "__main__":
    unittest.main()
