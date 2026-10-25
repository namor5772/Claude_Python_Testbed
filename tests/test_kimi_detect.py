"""Characterization tests: KimiMixin pure/detection helpers + translators.

Locks the per-model contract verified against platform.kimi.ai docs
2026-07-25, re-probed live 2026-09-23 and re-audited 2026-10-26: the K3
LINE — kimi-k3.1-pro (+ -highspeed, new in 2026-10, the picker default) and
kimi-k3 — has the sparse none/low/high/max reasoning_effort ladder ("none"
is a real off switch — no reasoning tokens, found 2026-09-23 and re-measured
on all three ids 2026-10-26 although the docs and the listing name
low/high/max only; "medium" and even "banana" are accepted in silence with
no measurable effect, so they earned no rung) and sends the API's own
131,072 output default as max_completion_tokens; kimi-k2.6/k2.5 take an
enabled/disabled thinking toggle; kimi-k2.7-code (+ -highspeed) always
thinks with no client knob — and, per the listing's supports_image_in flag
and a live probe, takes images after all (the doc had called it text-only).
The whole K2 line is retiring (end of December 2026): hidden from the picker
and unpriced since 2026-10-26, yet SERVED, so a pinned instruction still
runs it with the param wiring below. A model none of the tables name takes
the reasoning_effort ladder the listing published for it. The load-bearing
piece is the reasoning_content ROUND-TRIP policy: required for the K3 line /
k2.7-code / k2.6 (k2.6 with thinking.keep="all"), and forbidden for k2.5 (no
Preserved Thinking support) — _messages_to_kimi must attach or strip
reasoning_content accordingly, and _kimi_model_params must never emit
temperature for any Kimi model (HTTP 400 "only 1 is allowed", probed
2026-10-26). The K3 line's cache WRITES (prompt_tokens_details.
cache_write_tokens) are their own disjoint bucket, billed at the 5m rate."""
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
        "kimi-k3": ["none", "low", "high", "max"],
        # The K3.1 Pro line shares k3's parameter constraints (the docs say
        # so; probed 2026-10-26) and rides the same prefix
        "kimi-k3.1-pro": ["none", "low", "high", "max"],
        "kimi-k3.1-pro-highspeed": ["none", "low", "high", "max"],
        # Dated/preview ids inherit the family knob by prefix
        "kimi-k3-0801-preview": ["none", "low", "high", "max"],
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

    def test_a_model_the_table_does_not_name_takes_the_listing_ladder(self):
        # The listing publishes the K3 line's ladder (reasoning_efforts.
        # valid_efforts, 2026-10): a future tier the static table does not
        # name gets its Reasoning combobox from it, in KIMI_EFFORT_LADDER order
        caps = {"kimi-k9": {"vision": True, "context": 1, "reasoning": True,
                            "efforts": ["max", "high", "low"], "thinking_type": "only"},
                "kimi-k9-plain": {"vision": True, "context": 1, "reasoning": True,
                                  "efforts": None, "thinking_type": None}}
        self.assertEqual(stub(KimiMixin, model="kimi-k9", _kimi_caps=caps)._kimi_reasoning_values(),
                         ["low", "high", "max"])
        # a rung the canonical ladder does not know keeps its place at the end
        caps["kimi-k9"]["efforts"] = ["ultra", "low"]
        self.assertEqual(stub(KimiMixin, model="kimi-k9", _kimi_caps=caps)._kimi_reasoning_values(),
                         ["low", "ultra"])
        # no ladder published, or no listing at all → no knob
        self.assertIsNone(stub(KimiMixin, model="kimi-k9-plain", _kimi_caps=caps)._kimi_reasoning_values())
        self.assertIsNone(stub(KimiMixin, model="kimi-k9")._kimi_reasoning_values())
        # ...and the hand-verified table wins over the listing: k3's listing
        # names low/high/max, the measured none rung stays
        caps["kimi-k3"] = {"vision": True, "context": 1048576, "reasoning": True,
                           "efforts": ["low", "high", "max"], "thinking_type": "only"}
        self.assertEqual(stub(KimiMixin, model="kimi-k3", _kimi_caps=caps)._kimi_reasoning_values(),
                         ["none", "low", "high", "max"])


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

    def test_k3_ladder_with_its_none_rung(self):
        # k3's real ladder since 2026-09-23: a saved none passes through, a
        # Claude off becomes none, the rest coerce as before
        for saved, expected in {"none": "none", "off": "none", "low": "low", "max": "max",
                                "medium": "high", "xhigh": "max", "minimal": "low",
                                "adaptive": "max", "": "max"}.items():
            with self.subTest(saved=saved):
                obj = stub(KimiMixin, model="kimi-k3", thinking_effort=saved)
                self.assertEqual(obj._kimi_reasoning_effort(["none", "low", "high", "max"]),
                                 expected)
        # A bare host without the attribute takes the default
        self.assertEqual(stub(KimiMixin, model="kimi-k3")._kimi_reasoning_effort(
            ["none", "low", "high", "max"]), "max")


class TestKimiDetection(unittest.TestCase):
    def test_thinking_toggleable(self):
        for mid, expected in {"kimi-k2.6": True, "kimi-k2.5": True,
                              "kimi-k3": False, "kimi-k2.7-code": False}.items():
            with self.subTest(model=mid):
                obj = stub(KimiMixin, model=mid)
                self.assertEqual(obj._kimi_thinking_toggleable(), expected)

    def test_thinking_active(self):
        # Always-on families think regardless of the checkbox
        for mid in ("kimi-k3", "kimi-k3.1-pro", "kimi-k3.1-pro-highspeed",
                    "kimi-k2.7-code", "kimi-k2.7-code-highspeed"):
            with self.subTest(model=mid):
                obj = stub(KimiMixin, model=mid, thinking_enabled=False)
                self.assertTrue(obj._kimi_thinking_active())
        # ...except the K3 line on its none rung, which really stops
        # (2026-09-23 on k3; 2026-10-26 on all three ids)
        for mid in ("kimi-k3", "kimi-k3.1-pro", "kimi-k3.1-pro-highspeed"):
            with self.subTest(model=mid):
                self.assertFalse(stub(KimiMixin, model=mid, thinking_effort="none")
                                 ._kimi_thinking_active())
                self.assertTrue(stub(KimiMixin, model=mid, thinking_effort="low")
                                ._kimi_thinking_active())
        # Toggle families follow the checkbox
        for enabled in (True, False):
            obj = stub(KimiMixin, model="kimi-k2.6", thinking_enabled=enabled)
            self.assertEqual(obj._kimi_thinking_active(), enabled)
        # A model the tables do not name, whose listing says it cannot stop
        # (supports_thinking_type "only", the K3 line's value), thinks too
        caps = {"kimi-k9": {"vision": True, "context": 1, "reasoning": True,
                            "efforts": None, "thinking_type": "only"}}
        self.assertTrue(stub(KimiMixin, model="kimi-k9", _kimi_caps=caps,
                             thinking_enabled=False)._kimi_thinking_active())
        self.assertFalse(stub(KimiMixin, model="kimi-k9", thinking_enabled=False)
                         ._kimi_thinking_active())

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
        # Static fallback (no listing): every served model takes images
        # since the 2026-09-23 audit — the k2.7-code line included — and the
        # K3.1 Pro line lists text, image and video input too (2026-10-26)
        for mid, expected in {"kimi-k3": True, "kimi-k3.1-pro": True,
                              "kimi-k3.1-pro-highspeed": True, "kimi-k2.6": True,
                              "kimi-k2.5": True, "kimi-k2.7-code": True,
                              "kimi-k2.7-code-highspeed": True}.items():
            with self.subTest(model=mid):
                obj = stub(KimiMixin, model=mid)
                self.assertEqual(obj._is_kimi_vision_model(), expected)

    def test_vision_follows_the_listing_flag(self):
        caps = {"kimi-k9-text": {"vision": False, "context": 1, "reasoning": True},
                "kimi-k9-blank": {"vision": None, "context": 1, "reasoning": True}}
        self.assertFalse(stub(KimiMixin, model="kimi-k9-text", _kimi_caps=caps)._is_kimi_vision_model())
        # A listed model whose flag is missing falls back to the static rule
        self.assertTrue(stub(KimiMixin, model="kimi-k9-blank", _kimi_caps=caps)._is_kimi_vision_model())
        self.assertTrue(stub(KimiMixin, model="kimi-k2.7-code", _kimi_caps=caps)._is_kimi_vision_model())
        self.assertEqual(stub(KimiMixin, model="kimi-k3", _kimi_caps=None)._kimi_model_caps(), {})


class TestModelSupportsThinkingKimi(unittest.TestCase):
    def test_extended_for_the_k3_line(self):
        for mid in ("kimi-k3", "kimi-k3.1-pro", "kimi-k3.1-pro-highspeed"):
            with self.subTest(model=mid):
                obj = stub(_UIStub, provider="Moonshot", model=mid)
                self.assertEqual(obj._model_supports_thinking(), "extended")

    def test_extended_for_a_listing_described_future_tier(self):
        # The combobox appears the day a new knob model is listed, with no
        # table edit — the xAI rule since 2026-09-23, Kimi's since 2026-10-26
        caps = {"kimi-k9": {"vision": True, "context": 1, "reasoning": True,
                            "efforts": ["low", "high", "max"], "thinking_type": "only"}}
        obj = stub(_UIStub, provider="Moonshot", model="kimi-k9", _kimi_caps=caps)
        self.assertEqual(obj._model_supports_thinking(), "extended")
        self.assertEqual(obj._kimi_reasoning_values(), ["low", "high", "max"])
        # ...and an unlisted unknown id has no control at all
        self.assertIsNone(stub(_UIStub, provider="Moonshot", model="kimi-k9")
                          ._model_supports_thinking())

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
        # The K3 line's cap is the API's own documented default, 131,072
        # (2026-10-26) — the K2 rule's 32,768 had LOWERED it
        p = self.params("kimi-k3", effort="high")
        self.assertEqual(p, {"max_completion_tokens": 131072,
                             "reasoning_effort": "high"})

    def test_k3_none_rung_sends_none_and_keeps_the_k3_cap(self):
        p = self.params("kimi-k3", enabled=False, effort="none")
        self.assertEqual(p, {"max_completion_tokens": 131072,
                             "reasoning_effort": "none"})
        # A Claude "off" lands on the none rung too
        p = self.params("kimi-k3", enabled=False, effort="off")
        self.assertEqual(p["reasoning_effort"], "none")

    def test_k31_pro_line_is_wired_like_k3(self):
        # The exact request shapes the 2026-10-26 live probes sent and the API
        # accepted on kimi-k3.1-pro and -highspeed: a top-level
        # reasoning_effort, no thinking param, the K3 cap, never temperature
        for mid in ("kimi-k3.1-pro", "kimi-k3.1-pro-highspeed"):
            with self.subTest(model=mid):
                self.assertEqual(self.params(mid, effort="low"),
                                 {"max_completion_tokens": 131072, "reasoning_effort": "low"})
                self.assertEqual(self.params(mid, enabled=False, effort="none"),
                                 {"max_completion_tokens": 131072, "reasoning_effort": "none"})
                # a bare host (no saved effort) sends the API default, max
                self.assertEqual(self.params(mid, effort="")["reasoning_effort"], "max")
                self.assertNotIn("thinking", self.params(mid))

    def test_the_k2_line_keeps_its_caps(self):
        # 32,768 thinking / 8,192 not — the K2 docs' ">= 16000 for tool
        # loops" rule, unchanged by the K3 cap
        self.assertEqual(self.params("kimi-k2.6", enabled=True)["max_completion_tokens"], 32768)
        self.assertEqual(self.params("kimi-k2.6", enabled=False)["max_completion_tokens"], 8192)
        self.assertEqual(self.params("kimi-k2.7-code")["max_completion_tokens"], 32768)

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
        for mid in ("kimi-k3", "kimi-k3.1-pro", "kimi-k3.1-pro-highspeed",
                    "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5"):
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
    """kimi-k3.1-pro rates since 2026-10-26: miss $2.00 / hit $0.20 / 5m
    write $2.00 / out $10.00 (the cases ran on kimi-k2.6's rates until the
    K2 line was unpriced that day)."""

    def test_exact_cost_with_cache_hit_discount(self):
        obj = stub(_CostStub, provider="Moonshot", model="kimi-k3.1-pro")
        usage = SimpleNamespace(
            prompt_tokens=1000, completion_tokens=100,
            prompt_tokens_details=SimpleNamespace(cached_tokens=600))
        d = obj._kimi_usage_dict(usage)
        # prompt_tokens is the GROSS prompt with the 600 cache hits INSIDE
        # it; the emitted buckets are disjoint (miss 400 + hit 600 = 1000)
        # since 2026-09-15 — the gross pass-through had counted every cached
        # token twice in the cost log's TOK-IN field.
        self.assertEqual(d["input_tokens"], 400)
        self.assertEqual(d["output_tokens"], 100)
        self.assertEqual(d["cache_read_input_tokens"], 600)
        self.assertNotIn("cache_creation_input_tokens", d)
        self.assertEqual(d["input_tokens"] + d["cache_read_input_tokens"], 1000)
        # (1000-600)*2.00 + 600*0.20 + 100*10.00 per MTok = $0.00192
        self.assertAlmostEqual(d["cost_usd"], 0.00192, delta=1e-12)

    def test_cache_writes_are_their_own_bucket(self):
        # The K3 line's prompt_tokens_details.cache_write_tokens (2026-10):
        # cached, written and the uncached rest are disjoint and sum to
        # prompt_tokens (the docs' rule) — the written prefix is billed at the
        # 5-minute-TTL write rate, which equals the miss rate today
        obj = stub(_CostStub, provider="Moonshot", model="kimi-k3.1-pro")
        usage = SimpleNamespace(
            prompt_tokens=1000, completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=600, cache_write_tokens=100))
        d = obj._kimi_usage_dict(usage)
        self.assertEqual((d["input_tokens"], d["cache_read_input_tokens"],
                          d["cache_creation_input_tokens"]), (300, 600, 100))
        self.assertEqual(d["input_tokens"] + d["cache_read_input_tokens"]
                         + d["cache_creation_input_tokens"], 1000)
        # 300*2.00 + 600*0.20 + 100*2.00 + 50*10.00 per MTok
        self.assertAlmostEqual(d["cost_usd"], (600 + 120 + 200 + 500) / 1e6, delta=1e-12)

    def test_the_live_k31_shape_write_without_hits(self):
        # The exact usage the 2026-10-26 round trip's second call reported
        # (the SDK's details object carries cached_tokens=None beside the
        # extra cache_write_tokens field): 335 prompt = 79 uncached + 256
        # written, no cache_read bucket
        obj = stub(_CostStub, provider="Moonshot", model="kimi-k3.1-pro")
        usage = SimpleNamespace(
            prompt_tokens=335, completion_tokens=61,
            prompt_tokens_details=SimpleNamespace(cached_tokens=None, cache_write_tokens=256))
        d = obj._kimi_usage_dict(usage)
        self.assertEqual(d["input_tokens"], 79)
        self.assertEqual(d["cache_creation_input_tokens"], 256)
        self.assertNotIn("cache_read_input_tokens", d)
        self.assertAlmostEqual(d["cost_usd"], (79 * 2.00 + 256 * 2.00 + 61 * 10.00) / 1e6,
                               delta=1e-12)

    def test_highspeed_rates_do_not_prefix_match_down(self):
        # kimi-k3.1-pro-highspeed: miss $4 / hit $0.20 / write $4 / out $20 —
        # its own rows outrank the kimi-k3.1-pro and kimi-k3 prefixes
        obj = stub(_CostStub, provider="Moonshot", model="kimi-k3.1-pro-highspeed")
        usage = SimpleNamespace(
            prompt_tokens=400, completion_tokens=10,
            prompt_tokens_details=SimpleNamespace(cached_tokens=200, cache_write_tokens=100))
        d = obj._kimi_usage_dict(usage)
        self.assertAlmostEqual(d["cost_usd"], (100 * 4.00 + 200 * 0.20 + 100 * 4.00 + 10 * 20.00) / 1e6,
                               delta=1e-12)

    def test_flat_cached_tokens_field_fallback(self):
        # kimi-k3 rates (miss $3.00 / hit $0.30 / out $15.00) and Moonshot's
        # flat cached_tokens name
        obj = stub(_CostStub, provider="Moonshot", model="kimi-k3")
        usage = SimpleNamespace(prompt_tokens=500, completion_tokens=10,
                                cached_tokens=200)
        d = obj._kimi_usage_dict(usage)
        self.assertEqual(d["cache_read_input_tokens"], 200)
        self.assertEqual(d["input_tokens"], 300)   # disjoint: 500 gross - 200 hit
        # (500-200)*3.00 + 200*0.30 + 10*15.00 per MTok
        self.assertAlmostEqual(d["cost_usd"], (300 * 3.00 + 200 * 0.30 + 10 * 15.00) / 1e6,
                               delta=1e-12)

    def test_unpriced_models_report_tokens_and_no_cost(self):
        # An unknown future id, and the retiring K2 line (unpriced since
        # 2026-10-26 under the retirement policy): the buckets still come
        # through, cost_usd does not, and stream_worker's unpriced warning
        # says why nothing is logged
        for mid in ("kimi-k9-future", "kimi-k2.6", "kimi-k2.7-code-highspeed"):
            with self.subTest(model=mid):
                obj = stub(_CostStub, provider="Moonshot", model=mid)
                usage = SimpleNamespace(prompt_tokens=100, completion_tokens=10)
                self.assertEqual(obj._kimi_usage_dict(usage),
                                 {"input_tokens": 100, "output_tokens": 10})


class TestFetchKimiModels(unittest.TestCase):
    @staticmethod
    def _client(ids, flags=None):
        data = [SimpleNamespace(id=i, **(flags or {}).get(i, {})) for i in ids]
        return SimpleNamespace(models=SimpleNamespace(
            list=lambda: SimpleNamespace(data=data)))

    # The six ids the live catalog served on 2026-10-26, with the capability
    # fields the K3 line's entries carry (the K2 entries have no ladder)
    LIVE_IDS = ["kimi-k2.6", "kimi-k2.7-code", "kimi-k2.7-code-highspeed",
                "kimi-k3", "kimi-k3.1-pro", "kimi-k3.1-pro-highspeed"]
    K3_FLAGS = {"supports_image_in": True, "supports_video_in": True,
                "supports_reasoning": True, "context_length": 1048576,
                "supports_thinking_type": "only",
                "reasoning_efforts": {"default_effort": "max", "support": True,
                                      "valid_efforts": ["low", "high", "max"]},
                "reasoning": {"effort": {"default": "max", "valid": ["low", "high", "max"]},
                              "type": "only"}}
    K2_FLAGS = {"supports_image_in": True, "supports_video_in": True,
                "supports_reasoning": True, "context_length": 262144}

    def test_capability_flags_are_kept(self):
        # The listing's per-model flags (found 2026-09-23) drive
        # _is_kimi_vision_model; the K3 line's ladder and thinking type
        # (2026-10-26) the knob helpers; a model without them gets None slots
        flags = {"kimi-k3": self.K3_FLAGS,
                 "kimi-k9-text": {"supports_image_in": False, "context_length": 262144}}
        obj = stub(KimiMixin, kimi_client=self._client(["kimi-k3", "kimi-k9-text", "kimi-k2.6"], flags))
        # the retiring k2.6 is served but not offered
        self.assertEqual(obj._fetch_kimi_models(), ["kimi-k3", "kimi-k9-text"])
        self.assertEqual(obj._served_model_ids["Moonshot"], {"kimi-k3", "kimi-k9-text", "kimi-k2.6"})
        self.assertEqual(obj._kimi_caps["kimi-k3"],
                         {"vision": True, "context": 1048576, "reasoning": True,
                          "efforts": ["low", "high", "max"], "thinking_type": "only"})
        self.assertEqual(obj._kimi_caps["kimi-k2.6"],
                         {"vision": None, "context": None, "reasoning": None,
                          "efforts": None, "thinking_type": None})
        self.assertFalse(obj._is_kimi_vision_model("kimi-k9-text"))
        self.assertTrue(obj._is_kimi_vision_model("kimi-k2.6"))     # None flag → static rule
        # A failed fetch clears the records and the served set
        boom = SimpleNamespace(models=SimpleNamespace(list=lambda: (_ for _ in ()).throw(RuntimeError("down"))))
        obj.kimi_client = boom
        from myagent.constants import KIMI_FALLBACK_MODELS
        self.assertEqual(obj._fetch_kimi_models(), list(KIMI_FALLBACK_MODELS))
        self.assertEqual(obj._kimi_caps, {})
        self.assertIsNone(obj._served_model_ids["Moonshot"])

    def test_the_listing_ladder_is_read_from_either_field(self):
        # reasoning_efforts.valid_efforts first, the reasoning.effort.valid
        # twin as the fallback, None when neither is there
        both = SimpleNamespace(id="x", **self.K3_FLAGS)
        self.assertEqual(KimiMixin._kimi_listed_efforts(both), ["low", "high", "max"])
        twin_only = SimpleNamespace(id="x", reasoning={"effort": {"valid": ["low", "max"]}})
        self.assertEqual(KimiMixin._kimi_listed_efforts(twin_only), ["low", "max"])
        self.assertIsNone(KimiMixin._kimi_listed_efforts(SimpleNamespace(id="x", **self.K2_FLAGS)))
        self.assertIsNone(KimiMixin._kimi_listed_efforts(SimpleNamespace(id="x", reasoning="yes")))

    def test_the_live_catalog_offers_the_k3_line_and_serves_all_six(self):
        flags = {mid: (self.K3_FLAGS if mid.startswith("kimi-k3") else self.K2_FLAGS)
                 for mid in self.LIVE_IDS}
        obj = stub(KimiMixin, kimi_client=self._client(self.LIVE_IDS, flags))
        from myagent.constants import KIMI_DEFAULT_MODEL, KIMI_FALLBACK_MODELS
        picker = obj._fetch_kimi_models()
        self.assertEqual(picker, ["kimi-k3", "kimi-k3.1-pro", "kimi-k3.1-pro-highspeed"])
        # the fallback list mirrors the picker, and the default is offered
        self.assertEqual(set(picker), set(KIMI_FALLBACK_MODELS))
        self.assertIn(KIMI_DEFAULT_MODEL, picker)
        self.assertEqual(KIMI_DEFAULT_MODEL, "kimi-k3.1-pro")
        self.assertEqual(obj._served_model_ids["Moonshot"], set(self.LIVE_IDS))
        self.assertEqual(set(obj._kimi_model_display_names), set(picker))
        # every served id still has its vision / knob answers
        for mid in self.LIVE_IDS:
            self.assertTrue(obj._is_kimi_vision_model(mid), mid)
        self.assertEqual(obj._kimi_reasoning_values("kimi-k3.1-pro-highspeed"),
                         ["none", "low", "high", "max"])
        self.assertIsNone(obj._kimi_reasoning_values("kimi-k2.7-code"))

    def test_filters_legacy_and_non_k_series(self):
        obj = stub(KimiMixin, kimi_client=self._client([
            "kimi-k2.6", "kimi-k3", "kimi-k2.7-code-highspeed", "kimi-k3.1-pro",
            "kimi-k2-thinking",        # DASH family — discontinued 2026-05-25
            "kimi-k2-0905-preview",    # DASH family
            "kimi-latest",             # discontinued alias
            "kimi-thinking-preview",   # discontinued
            "moonshot-v1-8k",          # EOL 2026-08-31
            "moonshot-v1-128k-vision-preview",
        ]))
        self.assertEqual(obj._fetch_kimi_models(), ["kimi-k3", "kimi-k3.1-pro"])
        self.assertEqual(set(obj._kimi_model_display_names), {"kimi-k3", "kimi-k3.1-pro"})
        # the K2 line is served (hidden by the retirement filter); the DASH
        # family and the non-K ids are not served at all
        self.assertEqual(obj._served_model_ids["Moonshot"],
                         {"kimi-k2.6", "kimi-k3", "kimi-k2.7-code-highspeed", "kimi-k3.1-pro"})

    def test_fallback_without_client(self):
        obj = stub(KimiMixin, kimi_client=None)
        from myagent.constants import KIMI_FALLBACK_MODELS
        self.assertEqual(obj._fetch_kimi_models(), list(KIMI_FALLBACK_MODELS))

    def test_fallback_when_all_filtered(self):
        obj = stub(KimiMixin, kimi_client=self._client(["moonshot-v1-8k"]))
        from myagent.constants import KIMI_FALLBACK_MODELS
        self.assertEqual(obj._fetch_kimi_models(), list(KIMI_FALLBACK_MODELS))
        self.assertEqual(obj._served_model_ids["Moonshot"], set())
        # A catalog of retiring ids alone offers the fallback list too, and
        # still serves what it listed
        obj = stub(KimiMixin, kimi_client=self._client(["kimi-k2.6", "kimi-k2.7-code"]))
        self.assertEqual(obj._fetch_kimi_models(), list(KIMI_FALLBACK_MODELS))
        self.assertEqual(obj._served_model_ids["Moonshot"], {"kimi-k2.6", "kimi-k2.7-code"})


if __name__ == "__main__":
    unittest.main()
