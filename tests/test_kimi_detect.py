"""Characterization tests: KimiMixin pure/detection helpers + translators.

Locks the per-model contract verified against platform.kimi.ai docs
2026-07-25: kimi-k3 has the sparse low/high/max reasoning_effort ladder
(always-reasoning); kimi-k2.6/k2.5 take an enabled/disabled thinking toggle;
kimi-k2.7-code (+ -highspeed) always thinks with no client knob and is
text-only. The load-bearing piece is the reasoning_content ROUND-TRIP policy:
required for k3 / k2.7-code / k2.6 (k2.6 with thinking.keep="all"), and
forbidden for k2.5 (no Preserved Thinking support) — _messages_to_kimi must
attach or strip reasoning_content accordingly, and _kimi_model_params must
never emit temperature for any Kimi model."""
import json
import unittest
from types import SimpleNamespace

from myagent.kimi_mixin import KimiMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin
from tests._util import stub


class _UIStub(UIMixin, KimiMixin):
    """_model_supports_thinking (UIMixin) consults _kimi_reasoning_values /
    _kimi_thinking_toggleable (KimiMixin) for provider Kimi — the stub needs
    both in its MRO, like App."""


class _CostStub(StreamingMixin, KimiMixin):
    """_kimi_usage_dict (KimiMixin) calls the static _get_pricing
    (StreamingMixin)."""


class TestKimiReasoningValues(unittest.TestCase):
    CASES = {
        "kimi-k3": ["low", "high", "max"],
        # Dated/preview ids inherit the family knob by prefix
        "kimi-k3-0801-preview": ["low", "high", "max"],
        # Toggle/always-on families have no effort knob
        "kimi-k2.6": None,
        "kimi-k2.5": None,
        "kimi-k2.7-code": None,
        "kimi-k2.7-code-highspeed": None,
    }

    def test_values(self):
        for mid, expected in self.CASES.items():
            with self.subTest(model=mid):
                obj = stub(KimiMixin, model=mid)
                self.assertEqual(obj._kimi_reasoning_values(), expected)


class TestKimiEffortCoercion(unittest.TestCase):
    """Stale saved efforts from other providers coerce onto the SPARSE
    low/high/max ladder (there is no medium); unmapped values land on max,
    the API's own default."""
    CASES = {
        "low": "low", "high": "high", "max": "max",
        "medium": "high",
        "xhigh": "max",
        "minimal": "low",
        "none": "low",
        "adaptive": "max",
        "off": "max",
    }

    def test_coercion(self):
        for saved, expected in self.CASES.items():
            with self.subTest(saved=saved):
                obj = stub(KimiMixin, model="kimi-k3", thinking_effort=saved)
                self.assertEqual(
                    obj._kimi_reasoning_effort(["low", "high", "max"]), expected)


class TestKimiDetection(unittest.TestCase):
    def test_thinking_toggleable(self):
        for mid, expected in {"kimi-k2.6": True, "kimi-k2.5": True,
                              "kimi-k3": False, "kimi-k2.7-code": False}.items():
            with self.subTest(model=mid):
                obj = stub(KimiMixin, model=mid)
                self.assertEqual(obj._kimi_thinking_toggleable(), expected)

    def test_thinking_active(self):
        # Always-on families think regardless of the checkbox
        for mid in ("kimi-k3", "kimi-k2.7-code", "kimi-k2.7-code-highspeed"):
            with self.subTest(model=mid):
                obj = stub(KimiMixin, model=mid, thinking_enabled=False)
                self.assertTrue(obj._kimi_thinking_active())
        # Toggle families follow the checkbox
        for enabled in (True, False):
            obj = stub(KimiMixin, model="kimi-k2.6", thinking_enabled=enabled)
            self.assertEqual(obj._kimi_thinking_active(), enabled)

    def test_roundtrip_policy(self):
        # k2.5 is the ONLY family that must not receive reasoning_content
        # back; unknown future models default to round-tripping.
        for mid, expected in {"kimi-k3": True, "kimi-k2.7-code": True,
                              "kimi-k2.6": True, "kimi-k2.5": False,
                              "kimi-k9-future": True}.items():
            with self.subTest(model=mid):
                obj = stub(KimiMixin, model=mid)
                self.assertEqual(obj._kimi_roundtrips_reasoning(), expected)

    def test_include_reasoning_honors_rejected_cache(self):
        obj = stub(KimiMixin, model="kimi-k2.6",
                   _kimi_reasoning_rejected={"kimi-k2.6"})
        self.assertFalse(obj._kimi_include_reasoning())
        obj = stub(KimiMixin, model="kimi-k2.6")  # no cache attr at all
        self.assertTrue(obj._kimi_include_reasoning())

    def test_vision(self):
        for mid, expected in {"kimi-k3": True, "kimi-k2.6": True,
                              "kimi-k2.5": True, "kimi-k2.7-code": False,
                              "kimi-k2.7-code-highspeed": False}.items():
            with self.subTest(model=mid):
                obj = stub(KimiMixin, model=mid)
                self.assertEqual(obj._is_kimi_vision_model(), expected)


class TestModelSupportsThinkingKimi(unittest.TestCase):
    def test_extended_for_k3(self):
        obj = stub(_UIStub, provider="Moonshot", model="kimi-k3")
        self.assertEqual(obj._model_supports_thinking(), "extended")

    def test_manual_for_toggle_families(self):
        for mid in ("kimi-k2.6", "kimi-k2.5"):
            with self.subTest(model=mid):
                obj = stub(_UIStub, provider="Moonshot", model=mid)
                self.assertEqual(obj._model_supports_thinking(), "manual")

    def test_none_for_knobless_always_on(self):
        for mid in ("kimi-k2.7-code", "kimi-k2.7-code-highspeed"):
            with self.subTest(model=mid):
                obj = stub(_UIStub, provider="Moonshot", model=mid)
                self.assertIsNone(obj._model_supports_thinking())


class TestKimiModelParams(unittest.TestCase):
    """The flat wire params — never temperature, correct thinking shapes."""

    def params(self, model, enabled=True, effort="high"):
        obj = stub(KimiMixin, model=model, thinking_enabled=enabled,
                   thinking_effort=effort)
        return obj._kimi_model_params()

    def test_k3(self):
        p = self.params("kimi-k3", effort="high")
        self.assertEqual(p, {"max_completion_tokens": 32768,
                             "reasoning_effort": "high"})

    def test_k26_thinking_on_gets_preserved_thinking(self):
        p = self.params("kimi-k2.6", enabled=True)
        self.assertEqual(p, {"max_completion_tokens": 32768,
                             "thinking": {"type": "enabled", "keep": "all"}})

    def test_k26_thinking_off(self):
        p = self.params("kimi-k2.6", enabled=False)
        self.assertEqual(p, {"max_completion_tokens": 8192,
                             "thinking": {"type": "disabled"}})

    def test_k25_never_gets_keep(self):
        # k2.5 has no Preserved Thinking — keep must be absent even when on
        p = self.params("kimi-k2.5", enabled=True)
        self.assertEqual(p, {"max_completion_tokens": 32768,
                             "thinking": {"type": "enabled"}})

    def test_k27_code_no_knobs(self):
        p = self.params("kimi-k2.7-code", enabled=False)
        self.assertEqual(p, {"max_completion_tokens": 32768})

    def test_never_temperature(self):
        for mid in ("kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5"):
            for enabled in (True, False):
                with self.subTest(model=mid, enabled=enabled):
                    self.assertNotIn("temperature",
                                     self.params(mid, enabled=enabled))


class TestToolsToKimi(unittest.TestCase):
    def test_nested_function_shape(self):
        tools = [{"name": "web_search", "description": "Search.",
                  "input_schema": {"type": "object",
                                   "properties": {"query": {"type": "string"}},
                                   "required": ["query"]}}]
        out = KimiMixin._tools_to_kimi(tools)
        self.assertEqual(out, [{
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "Search.",
                "parameters": {"type": "object",
                               "properties": {"query": {"type": "string"}},
                               "required": ["query"]},
            },
        }])


class TestMessagesToKimi(unittest.TestCase):
    ASSISTANT_TURN = {
        "role": "assistant",
        "content": [
            {"type": "thinking", "thinking": "I should check the weather."},
            {"type": "text", "text": "Checking now."},
            {"type": "tool_use", "id": "call_1", "name": "web_search",
             "input": {"query": "weather"}},
        ],
    }

    def test_reasoning_content_round_trip_on(self):
        obj = stub(KimiMixin)
        out = obj._messages_to_kimi([self.ASSISTANT_TURN], include_reasoning=True)
        self.assertEqual(len(out), 1)
        msg = out[0]
        self.assertEqual(msg["role"], "assistant")
        self.assertEqual(msg["reasoning_content"], "I should check the weather.")
        self.assertEqual(msg["content"], "Checking now.")
        self.assertEqual(msg["tool_calls"], [{
            "id": "call_1", "type": "function",
            "function": {"name": "web_search",
                         "arguments": json.dumps({"query": "weather"})},
        }])

    def test_reasoning_content_stripped_when_off(self):
        # The k2.5 policy: thinking blocks stay in the internal history but
        # must NOT reach the wire.
        obj = stub(KimiMixin)
        out = obj._messages_to_kimi([self.ASSISTANT_TURN], include_reasoning=False)
        self.assertNotIn("reasoning_content", out[0])
        self.assertEqual(out[0]["content"], "Checking now.")

    def test_tool_result_becomes_tool_role(self):
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1",
             "content": "Sunny, 22C"},
        ]}]
        out = stub(KimiMixin)._messages_to_kimi(msgs)
        self.assertEqual(out, [{"role": "tool", "tool_call_id": "call_1",
                                "content": "Sunny, 22C"}])

    def test_tool_result_image_deferred_to_user_message(self):
        msgs = [{"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_2", "content": [
                {"type": "text", "text": "Screenshot captured (800x600)"},
                {"type": "image", "source": {"type": "base64",
                                             "media_type": "image/png",
                                             "data": "AAAA"}},
            ]},
        ]}]
        out = stub(KimiMixin)._messages_to_kimi(msgs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {"role": "tool", "tool_call_id": "call_2",
                                  "content": "Screenshot captured (800x600)"})
        follow_up = out[1]
        self.assertEqual(follow_up["role"], "user")
        self.assertEqual(follow_up["content"][0]["type"], "text")
        self.assertIn("(800x600 pixels)", follow_up["content"][0]["text"])
        self.assertEqual(follow_up["content"][1], {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,AAAA"},
        })

    def test_user_text_and_image_parts(self):
        msgs = [{"role": "user", "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image", "source": {"type": "base64",
                                         "media_type": "image/jpeg",
                                         "data": "BBBB"}},
        ]}]
        out = stub(KimiMixin)._messages_to_kimi(msgs)
        self.assertEqual(out, [{"role": "user", "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url",
             "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
        ]}])

    def test_plain_strings_pass_through(self):
        msgs = [{"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi"}]
        out = stub(KimiMixin)._messages_to_kimi(msgs)
        self.assertEqual(out, [{"role": "user", "content": "hello"},
                               {"role": "assistant", "content": "hi"}])


class TestKimiUsageDict(unittest.TestCase):
    def test_exact_cost_with_cache_hit_discount(self):
        obj = stub(_CostStub, provider="Moonshot", model="kimi-k2.6")
        usage = SimpleNamespace(
            prompt_tokens=1000, completion_tokens=100,
            prompt_tokens_details=SimpleNamespace(cached_tokens=600))
        d = obj._kimi_usage_dict(usage)
        self.assertEqual(d["input_tokens"], 1000)
        self.assertEqual(d["output_tokens"], 100)
        self.assertEqual(d["cache_read_input_tokens"], 600)
        # (1000-600)*0.95 + 600*0.16 + 100*4.00 per MTok = $0.000876
        self.assertAlmostEqual(d["cost_usd"], 0.000876, delta=1e-12)

    def test_flat_cached_tokens_field_fallback(self):
        obj = stub(_CostStub, provider="Moonshot", model="kimi-k2.5")
        usage = SimpleNamespace(prompt_tokens=500, completion_tokens=10,
                                cached_tokens=200)
        d = obj._kimi_usage_dict(usage)
        self.assertEqual(d["cache_read_input_tokens"], 200)
        # (500-200)*0.60 + 200*0.10 + 10*3.00 per MTok
        self.assertAlmostEqual(d["cost_usd"], (300 * 0.60 + 200 * 0.10 + 10 * 3.00) / 1e6,
                               delta=1e-12)

    def test_unknown_model_no_cost(self):
        obj = stub(_CostStub, provider="Moonshot", model="kimi-k9-future")
        usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
        d = obj._kimi_usage_dict(usage)
        self.assertEqual(d, {"input_tokens": 100, "output_tokens": 10})


class TestFetchKimiModels(unittest.TestCase):
    @staticmethod
    def _client(ids):
        data = [SimpleNamespace(id=i) for i in ids]
        return SimpleNamespace(models=SimpleNamespace(
            list=lambda: SimpleNamespace(data=data)))

    def test_filters_legacy_and_non_k_series(self):
        obj = stub(KimiMixin, kimi_client=self._client([
            "kimi-k2.6", "kimi-k3", "kimi-k2.7-code-highspeed",
            "kimi-k2-thinking",        # DASH family — discontinued 2026-05-25
            "kimi-k2-0905-preview",    # DASH family
            "kimi-latest",             # discontinued alias
            "kimi-thinking-preview",   # discontinued
            "moonshot-v1-8k",          # EOL 2026-08-31
            "moonshot-v1-128k-vision-preview",
        ]))
        self.assertEqual(obj._fetch_kimi_models(),
                         ["kimi-k2.6", "kimi-k2.7-code-highspeed", "kimi-k3"])
        self.assertEqual(set(obj._kimi_model_display_names),
                         {"kimi-k2.6", "kimi-k2.7-code-highspeed", "kimi-k3"})

    def test_fallback_without_client(self):
        obj = stub(KimiMixin, kimi_client=None)
        from myagent.constants import KIMI_FALLBACK_MODELS
        self.assertEqual(obj._fetch_kimi_models(), list(KIMI_FALLBACK_MODELS))

    def test_fallback_when_all_filtered(self):
        obj = stub(KimiMixin, kimi_client=self._client(["moonshot-v1-8k"]))
        from myagent.constants import KIMI_FALLBACK_MODELS
        self.assertEqual(obj._fetch_kimi_models(), list(KIMI_FALLBACK_MODELS))


if __name__ == "__main__":
    unittest.main()
