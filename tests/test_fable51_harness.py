"""Characterization tests for the Claude Fable 5.1 harness surface (2026-09-02).

Fable 5.1 (`claude-fable-5-1`) rides the same prefix rules as Fable 5 for
thinking / temperature / effort (tests/test_anthropic_detect.py) and gets its
own pricing row (tests/test_pricing.py). What is NEW to the harness, and what
this module locks down:

- `AnthropicMixin._anthropic_fable_features` — the two beta surfaces sent for
  the always-on Fable/Mythos class (preserved-thinking `block_binding` under
  thinking-binding-controls, server-side refusal `fallbacks` under
  server-side-fallback) and the per-session learn-off cache behind them;
- `AnthropicMixin._refusal_note` — the ⚠ line for stop_reason="refusal", fed
  by a dict (anthropic 0.84.0 leaves stop_details untyped), an object, or None;
- `helpers.strip_thinking_blocks` — the no-beta recovery for a signature-bound
  400 (history edited since the blocks were produced);
- `helpers.strip_pre_fallback_blocks` — the API's echo rule after a mid-output
  refusal fallback (only text + paired server-tool blocks survive from the
  declined partial; the `fallback` markers themselves are dropped);
- stream_worker pricing a call by the model that actually served it
  (`usage["model"]`), so a fallback-served call bills at Opus rates.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

from tests._util import stub
from tests.test_costlog_run_fields import _Host as _CostLogHost
from myagent.anthropic_mixin import AnthropicMixin
from myagent.constants import (ANTHROPIC_SERVER_FALLBACK_BETA,
                               ANTHROPIC_THINKING_BINDING_BETA)
from myagent.helpers import strip_pre_fallback_blocks, strip_thinking_blocks
from myagent.ui_mixin import UIMixin


class _Host(AnthropicMixin, UIMixin):
    """_anthropic_fable_features needs UIMixin's model-detection helpers."""


def _host(model, unsupported=(), provider="Anthropic"):
    return stub(_Host, provider=provider, model=model,
                _anthropic_unsupported=set(unsupported))


class FableFeatures(unittest.TestCase):
    def test_full_surface_on_fable_5_1(self):
        f = _host("claude-fable-5-1")._anthropic_fable_features()
        self.assertEqual(f["betas"], [ANTHROPIC_THINKING_BINDING_BETA,
                                      ANTHROPIC_SERVER_FALLBACK_BETA])
        self.assertEqual(f["block_binding"], {"prefix_mismatch_behavior": "drop_block"})
        self.assertEqual(f["fallbacks"], "default")

    def test_whole_always_on_class(self):
        # Fable 5 / Mythos 5 accept both surfaces (and never act on the
        # binding check), so the class is gated by prefix, not by minor.
        for model in ("claude-fable-5", "claude-mythos-5", "claude-mythos-5-1"):
            with self.subTest(model=model):
                self.assertIsNotNone(_host(model)._anthropic_fable_features())

    def test_none_outside_the_class(self):
        for model in ("claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5",
                      "claude-opus-4-8"):
            with self.subTest(model=model):
                self.assertIsNone(_host(model)._anthropic_fable_features())

    def test_none_for_other_providers(self):
        self.assertIsNone(_host("claude-fable-5-1", provider="OpenAI")
                          ._anthropic_fable_features())

    def test_learned_off_surfaces_are_dropped(self):
        f = _host("claude-fable-5-1", unsupported={"fallbacks"})._anthropic_fable_features()
        self.assertEqual(f["betas"], [ANTHROPIC_THINKING_BINDING_BETA])
        self.assertIsNone(f["fallbacks"])
        self.assertIsNotNone(f["block_binding"])
        f = _host("claude-fable-5-1",
                  unsupported={"block_binding", "fallbacks"})._anthropic_fable_features()
        self.assertEqual(f, {"betas": [], "block_binding": None, "fallbacks": None})

    def test_returns_a_fresh_dict_each_call(self):
        # The BadRequest rungs mutate the returned dict; the constant must not.
        h = _host("claude-fable-5-1")
        h._anthropic_fable_features()["block_binding"]["prefix_mismatch_behavior"] = "error"
        self.assertEqual(
            h._anthropic_fable_features()["block_binding"]["prefix_mismatch_behavior"],
            "drop_block")


class RefusalNote(unittest.TestCase):
    def test_dict_details(self):
        note = AnthropicMixin._refusal_note({"category": "cyber", "explanation": "nope"})
        self.assertIn("category=cyber", note)
        self.assertIn(": nope", note)
        self.assertNotIn("fallback", note)
        self.assertIn("claude-opus-5", note)

    def test_object_details_and_recommended_model(self):
        details = SimpleNamespace(category="bio", explanation=None,
                                  recommended_model="claude-opus-4-8")
        note = AnthropicMixin._refusal_note(details, fallbacks_requested=True)
        self.assertIn("category=bio", note)
        self.assertIn("no fallback model served it", note)
        self.assertIn("retrying directly on claude-opus-4-8", note)

    def test_none_details(self):
        note = AnthropicMixin._refusal_note(None, fallbacks_requested=True)
        self.assertTrue(note.startswith(
            "⚠ The model declined this request (stop_reason=refusal)"))
        self.assertIn("no fallback model served it.", note)


def _thinking(sig="sig"):
    return {"type": "thinking", "thinking": "…", "signature": sig}


class StripThinking(unittest.TestCase):
    def test_strips_dict_and_object_blocks_in_place(self):
        obj_thinking = SimpleNamespace(type="thinking", thinking="x", signature="y")
        obj_text = SimpleNamespace(type="text", text="hi")
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": [_thinking(), {"type": "text", "text": "a"}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t",
                                          "content": "r"}]},
            {"role": "assistant", "content": [obj_thinking,
                                              {"type": "redacted_thinking", "data": "z"},
                                              obj_text]},
            {"role": "assistant", "content": "plain string turn"},
        ]
        self.assertEqual(strip_thinking_blocks(msgs), 3)
        self.assertEqual(msgs[1]["content"], [{"type": "text", "text": "a"}])
        self.assertEqual(msgs[3]["content"], [obj_text])
        self.assertEqual(msgs[2]["content"][0]["type"], "tool_result")  # user turn untouched
        self.assertEqual(msgs[4]["content"], "plain string turn")

    def test_nothing_to_strip(self):
        msgs = [{"role": "assistant", "content": [{"type": "text", "text": "a"}]}]
        self.assertEqual(strip_thinking_blocks(msgs), 0)

    def test_never_empties_a_turn(self):
        # An empty assistant content list is itself a 400 — leave it be.
        msgs = [{"role": "assistant", "content": [_thinking()]}]
        self.assertEqual(strip_thinking_blocks(msgs), 0)
        self.assertEqual(len(msgs[0]["content"]), 1)


def _fb(frm="claude-fable-5-1", to="claude-opus-5"):
    return {"type": "fallback", "from": {"model": frm}, "to": {"model": to}}


class StripPreFallback(unittest.TestCase):
    def test_no_fallback_returns_same_list(self):
        blocks = [{"type": "text", "text": "a"},
                  {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]
        self.assertIs(strip_pre_fallback_blocks(blocks), blocks)

    def test_pre_output_decline_drops_only_the_marker(self):
        # Seamless fallback: the marker arrives first, the served turn follows.
        tail = [{"type": "thinking", "thinking": "", "signature": "s"},
                {"type": "tool_use", "id": "t1", "name": "x", "input": {}}]
        self.assertEqual(strip_pre_fallback_blocks([_fb()] + tail), tail)

    def test_mid_output_decline_keeps_text_and_paired_server_tools_only(self):
        pre = [
            {"type": "thinking", "thinking": "plan", "signature": "s1"},
            {"type": "text", "text": "Partial answer"},
            {"type": "server_tool_use", "id": "srv1", "name": "web_search", "input": {}},
            {"type": "web_search_tool_result", "tool_use_id": "srv1", "content": []},
            {"type": "server_tool_use", "id": "srv2", "name": "web_search", "input": {}},
            {"type": "tool_use", "id": "t1", "name": "run_command", "input": {}},
        ]
        post = [{"type": "thinking", "thinking": "", "signature": "s2"},
                {"type": "tool_use", "id": "t2", "name": "run_command", "input": {}}]
        kept = strip_pre_fallback_blocks(pre + [_fb()] + post)
        # text + the PAIRED srv1 use/result survive; thinking, the unpaired
        # srv2 use and the declined model's tool_use t1 do not.
        self.assertEqual(kept, [pre[1], pre[2], pre[3]] + post)

    def test_last_fallback_block_is_the_boundary(self):
        blocks = [{"type": "text", "text": "one"}, _fb(),
                  {"type": "tool_use", "id": "a", "name": "x", "input": {}},
                  _fb("claude-opus-5", "claude-opus-4-8"),
                  {"type": "text", "text": "final"}]
        self.assertEqual(strip_pre_fallback_blocks(blocks),
                         [{"type": "text", "text": "one"}, {"type": "text", "text": "final"}])

    def test_sdk_object_blocks(self):
        marker = SimpleNamespace(type="fallback")
        pre_use = SimpleNamespace(type="tool_use", id="t1", name="x", input={})
        post_text = SimpleNamespace(type="text", text="served")
        self.assertEqual(strip_pre_fallback_blocks([pre_use, marker, post_text]), [post_text])


class _FallbackServedHost(_CostLogHost):
    """A Fable 5.1 run whose first call was served by claude-opus-5 via
    server-side fallback (usage["model"] names the serving model) and whose
    second call came back from Fable itself."""

    def __init__(self):
        super().__init__(second_call="end")
        self.model = "claude-fable-5-1"

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        stop, blocks, text, thinking, label, usage = super()._stream_anthropic_call(
            messages, max_retries, label_emitted)
        usage["model"] = "claude-opus-5" if self._calls_made == 1 else "claude-fable-5-1"
        return stop, blocks, text, thinking, label, usage


class ServedModelPricing(unittest.TestCase):
    def test_call_is_priced_by_the_model_that_served_it(self):
        host = _FallbackServedHost()
        with mock.patch.object(host, "_log_api_cost"):
            host.stream_worker([{"role": "user", "content": "go"}])
        costs = [m["call_cost"] for m in _drain(host.queue) if m["type"] == "cost_update"]
        self.assertEqual(len(costs), 2)
        # 1000 in / 100 out at Opus 5's $5/$25 vs Fable 5.1's $10/$50.
        self.assertAlmostEqual(costs[0], 1000 * 5e-06 + 100 * 25e-06)
        self.assertAlmostEqual(costs[1], 1000 * 10e-06 + 100 * 50e-06)

    def test_unpriced_served_model_falls_back_to_the_configured_row(self):
        host = _FallbackServedHost()
        real = host._stream_anthropic_call

        def unknown_server(*a, **k):
            r = real(*a, **k)
            r[5]["model"] = "claude-unknown-tier"
            return r

        host._stream_anthropic_call = unknown_server
        with mock.patch.object(host, "_log_api_cost"):
            host.stream_worker([{"role": "user", "content": "go"}])
        costs = [m["call_cost"] for m in _drain(host.queue) if m["type"] == "cost_update"]
        self.assertEqual(len(costs), 2)
        for cost in costs:
            self.assertAlmostEqual(cost, 1000 * 10e-06 + 100 * 50e-06)


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


if __name__ == "__main__":
    unittest.main()
