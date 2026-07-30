"""Characterization tests for the Anthropic prompt-cache breakpoints:
_anthropic_cache_system (static tools+system prefix) and
_anthropic_cache_messages (rolling turn-boundary markers). The load-bearing
property is that history is NEVER mutated — stream_worker keeps appending to
the same list and the overflow handler trims it in place, so the wire copy has
to stay a copy."""

import unittest
from types import SimpleNamespace

from myagent.anthropic_mixin import AnthropicMixin

_CC = {"type": "ephemeral"}


class SystemCase(unittest.TestCase):
    def test_wraps_prompt_in_a_cached_text_block(self):
        out = AnthropicMixin._anthropic_cache_system("You are an agent.")
        self.assertEqual(out, [{"type": "text", "text": "You are an agent.",
                                "cache_control": _CC}])

    def test_empty_prompt_stays_a_bare_string(self):
        # A text block with "" is an HTTP 400 — never build one.
        self.assertEqual(AnthropicMixin._anthropic_cache_system(""), "")
        self.assertIsNone(AnthropicMixin._anthropic_cache_system(None))


class MessagesCase(unittest.TestCase):
    def _wire(self, messages, **kw):
        return AnthropicMixin._anthropic_cache_messages(messages, **kw)

    def test_marks_the_two_newest_eligible_turns(self):
        messages = [
            {"role": "user", "content": "do the task"},
            {"role": "user", "content": [{"type": "tool_result", "content": "a"}]},
            {"role": "user", "content": [{"type": "tool_result", "content": "b"}]},
        ]
        wire = self._wire(messages)
        self.assertNotIn("cache_control", wire[0]["content"][0]
                         if isinstance(wire[0]["content"], list) else {})
        self.assertEqual(wire[1]["content"][-1]["cache_control"], _CC)
        self.assertEqual(wire[2]["content"][-1]["cache_control"], _CC)

    def test_never_mutates_history(self):
        original = [{"role": "user", "content": [{"type": "tool_result", "content": "a"}]}]
        snapshot = [{"role": "user", "content": [{"type": "tool_result", "content": "a"}]}]
        wire = self._wire(original)
        self.assertEqual(original, snapshot)
        self.assertIsNot(wire, original)
        self.assertIsNot(wire[0]["content"], original[0]["content"])

    def test_string_content_becomes_a_cached_text_block(self):
        wire = self._wire([{"role": "user", "content": "do the task"}])
        self.assertEqual(wire[0]["content"],
                         [{"type": "text", "text": "do the task", "cache_control": _CC}])

    def test_skips_sdk_object_assistant_turns(self):
        # streaming_mixin appends final_message.content verbatim — Pydantic
        # blocks, not dicts. They must be passed through untouched, and the
        # breakpoint budget spent on the user turns instead.
        sdk_blocks = [SimpleNamespace(type="text", text="hi")]
        messages = [
            {"role": "user", "content": [{"type": "tool_result", "content": "a"}]},
            {"role": "assistant", "content": sdk_blocks},
        ]
        wire = self._wire(messages)
        self.assertIs(wire[1]["content"], sdk_blocks)
        self.assertEqual(wire[0]["content"][-1]["cache_control"], _CC)

    def test_respects_the_breakpoint_budget(self):
        messages = [{"role": "user", "content": [{"type": "tool_result", "content": str(i)}]}
                    for i in range(6)]
        wire = self._wire(messages)
        marked = [i for i, m in enumerate(wire)
                  if isinstance(m["content"], list) and "cache_control" in m["content"][-1]]
        self.assertEqual(marked, [4, 5])

    def test_empty_history_is_fine(self):
        self.assertEqual(self._wire([]), [])


if __name__ == "__main__":
    unittest.main()
