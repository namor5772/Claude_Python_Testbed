"""Characterization tests for the OpenAI / Gemini / xAI / Kimi usage
normalizers.

All four providers cache AUTOMATICALLY (no client opt-in, unlike Anthropic),
so the discount was always on the bill — it just wasn't reported, which
overstated every OpenAI/Google line in APICostLog.txt until 2026-07-31.

The load-bearing property is that the emitted buckets are DISJOINT. All four
report cached tokens as a SUBSET of their input total, while stream_worker
prices input at the full rate AND cache_read at the cached rate — so failing
to subtract would double-charge every cached token. xAI and Kimi dodged that
because both supply an authoritative per-call cost, so their gross
pass-through went unnoticed until the cost log started writing the buckets
themselves (2026-09-14): TOK-IN then counted every cached token twice and the
viewers' blended $/MTok came out low by the cache share (fixed 2026-09-15 —
the DisjointContractCase at the bottom pins all four).

Fixture values are the real ones observed live on 2026-07-31 (OpenAI, Gemini)
and the 2026-09-15 grok-4.6 cost-log row (xAI).
"""

import unittest
from types import SimpleNamespace

from myagent.gemini_mixin import GeminiMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.xai_mixin import XAIMixin
from tests._util import stub
from tests.test_kimi_detect import _CostStub


def _kimi(model="kimi-k3"):
    """_kimi_usage_dict reaches for self.model and the static _get_pricing."""
    return stub(_CostStub, provider="Moonshot", model=model)


class OpenAIUsageCase(unittest.TestCase):
    def test_cached_tokens_are_subtracted_from_input(self):
        # Live shape: input_tokens=2714 with cached_tokens=2711 INSIDE it.
        usage = SimpleNamespace(
            input_tokens=2714, output_tokens=16,
            input_tokens_details=SimpleNamespace(cached_tokens=2711,
                                                 cache_write_tokens=0))
        out = OpenAIMixin._openai_usage_dict(usage)
        self.assertEqual(out, {"input_tokens": 3, "output_tokens": 16,
                               "cache_read_input_tokens": 2711})
        # Buckets must sum back to the provider's reported total
        self.assertEqual(out["input_tokens"] + out["cache_read_input_tokens"], 2714)

    def test_cache_miss_reports_zero_cached(self):
        # Families before GPT-5.6 (5.5 / 5.4 / 5.2 / 5.1 / 4.1): a cache WRITE
        # is ordinary full-rate input (no write price on the pricing page), so
        # the written tokens stay in the input bucket and no cache_creation
        # key is emitted.
        usage = SimpleNamespace(
            input_tokens=2714, output_tokens=5,
            input_tokens_details=SimpleNamespace(cached_tokens=0,
                                                 cache_write_tokens=2711))
        out = OpenAIMixin._openai_usage_dict(usage)
        self.assertEqual(out["input_tokens"], 2714)
        self.assertEqual(out["cache_read_input_tokens"], 0)
        self.assertNotIn("cache_creation_input_tokens", out)

    def test_billed_cache_writes_leave_the_input_bucket(self):
        # GPT-5.6 and later bill writes at 1.25x (pricing page re-read
        # 2026-09-06). Live shape — identical on gpt-5.6-terra and
        # gpt-6-astra — on the first call of a 2423-token prompt:
        # cache_write_tokens=2420 INSIDE
        # input_tokens — with cache_write_billed the written tokens move to a
        # disjoint cache_creation bucket for stream_worker's cache_write rate.
        usage = SimpleNamespace(
            input_tokens=2423, output_tokens=5,
            input_tokens_details=SimpleNamespace(cached_tokens=0,
                                                 cache_write_tokens=2420))
        out = OpenAIMixin._openai_usage_dict(usage, cache_write_billed=True)
        self.assertEqual(out, {"input_tokens": 3, "output_tokens": 5,
                               "cache_read_input_tokens": 0,
                               "cache_creation_input_tokens": 2420})
        # ...and the repeat call reads it back: 2420 cached, 0 written
        usage2 = SimpleNamespace(
            input_tokens=2423, output_tokens=5,
            input_tokens_details=SimpleNamespace(cached_tokens=2420,
                                                 cache_write_tokens=0))
        out2 = OpenAIMixin._openai_usage_dict(usage2, cache_write_billed=True)
        self.assertEqual(out2, {"input_tokens": 3, "output_tokens": 5,
                                "cache_read_input_tokens": 2420,
                                "cache_creation_input_tokens": 0})
        # Buckets are disjoint and sum back to the provider's total
        self.assertEqual(sum(v for k, v in out.items() if k != "output_tokens"), 2423)

    def test_overreported_write_never_goes_negative(self):
        usage = SimpleNamespace(input_tokens=10, output_tokens=1,
                                input_tokens_details=SimpleNamespace(cached_tokens=8,
                                                                     cache_write_tokens=999))
        out = OpenAIMixin._openai_usage_dict(usage, cache_write_billed=True)
        self.assertEqual((out["input_tokens"], out["cache_read_input_tokens"],
                          out["cache_creation_input_tokens"]), (0, 8, 2))

    def test_missing_details_is_not_fatal(self):
        usage = SimpleNamespace(input_tokens=100, output_tokens=7)
        self.assertEqual(OpenAIMixin._openai_usage_dict(usage),
                         {"input_tokens": 100, "output_tokens": 7,
                          "cache_read_input_tokens": 0})

    def test_dict_details_shape(self):
        usage = SimpleNamespace(input_tokens=100, output_tokens=7,
                                input_tokens_details={"cached_tokens": 40})
        out = OpenAIMixin._openai_usage_dict(usage)
        self.assertEqual((out["input_tokens"], out["cache_read_input_tokens"]), (60, 40))

    def test_overreported_cache_never_goes_negative(self):
        usage = SimpleNamespace(input_tokens=10, output_tokens=1,
                                input_tokens_details=SimpleNamespace(cached_tokens=999))
        self.assertEqual(OpenAIMixin._openai_usage_dict(usage)["input_tokens"], 0)

    def test_no_usage_returns_none(self):
        self.assertIsNone(OpenAIMixin._openai_usage_dict(None))


class GeminiUsageCase(unittest.TestCase):
    # Live shape: prompt=15009, candidates=1, thoughts=133, cached=8174.
    LIVE = SimpleNamespace(prompt_token_count=15009, candidates_token_count=1,
                           thoughts_token_count=133,
                           cached_content_token_count=8174)

    def test_cached_subtracted_and_thoughts_added(self):
        out = GeminiMixin._gemini_usage_dict(self.LIVE)
        self.assertEqual(out, {"input_tokens": 15009 - 8174,
                               "output_tokens": 1 + 133,
                               "cache_read_input_tokens": 8174})

    def test_thoughts_are_not_inside_candidates(self):
        # The whole reason output_tokens adds thoughts: the provider's own total
        # only reconciles when thoughts is counted as a separate bucket.
        out = GeminiMixin._gemini_usage_dict(self.LIVE)
        total = 15143  # observed total_token_count
        self.assertEqual(out["input_tokens"] + out["cache_read_input_tokens"]
                         + out["output_tokens"], total)

    def test_below_cache_floor_reports_zero(self):
        # Under the 4,096-token implicit-cache floor the field is None, which is
        # normal — not a broken probe.
        um = SimpleNamespace(prompt_token_count=2710, candidates_token_count=1,
                             thoughts_token_count=0,
                             cached_content_token_count=None)
        out = GeminiMixin._gemini_usage_dict(um)
        self.assertEqual(out, {"input_tokens": 2710, "output_tokens": 1,
                               "cache_read_input_tokens": 0})

    def test_no_usage_returns_none(self):
        self.assertIsNone(GeminiMixin._gemini_usage_dict(None))
        self.assertIsNone(GeminiMixin._gemini_usage_dict(
            SimpleNamespace(prompt_token_count=0, candidates_token_count=0)))


class XAIUsageCase(unittest.TestCase):
    # The real 2026-09-15 grok-4.6 cost-log row: input_tokens 246,964 with
    # cached_tokens 183,424 INSIDE it, output 2,226, authoritative cost
    # $0.232148 — which the table rates ($2 / $6, cached $0.50) reproduce
    # ONLY under the subset reading. The logged line had TOK-IN 246,964, so
    # the viewers' blended rate came out $0.54/MTok against a real $0.93.
    LIVE = SimpleNamespace(
        input_tokens=246964, output_tokens=2226,
        input_tokens_details=SimpleNamespace(cached_tokens=183424),
        cost_in_usd_ticks=2321480000, num_server_side_tools_used=0)

    def test_cached_tokens_are_subtracted_from_input(self):
        out = XAIMixin._xai_usage_dict(self.LIVE)
        self.assertEqual(out["input_tokens"], 246964 - 183424)
        self.assertEqual(out["cache_read_input_tokens"], 183424)
        self.assertEqual(out["output_tokens"], 2226)
        # Buckets must sum back to the provider's reported total
        self.assertEqual(out["input_tokens"] + out["cache_read_input_tokens"], 246964)

    def test_disjoint_buckets_reproduce_the_authoritative_cost(self):
        # The proof that cached_tokens is a subset: priced disjointly, the
        # buckets land on cost_in_usd_ticks to the cent; priced gross they
        # would say $0.599.
        out = XAIMixin._xai_usage_dict(self.LIVE)
        table = (out["input_tokens"] * 2.00 + out["cache_read_input_tokens"] * 0.50
                 + out["output_tokens"] * 6.00) / 1e6
        self.assertAlmostEqual(out["cost_usd"], 0.232148, places=9)
        self.assertAlmostEqual(table, out["cost_usd"], places=6)

    def test_no_cache_hit_reports_zero_cached(self):
        # The contract is the disjoint sum, not the key's presence:
        # stream_worker .get()s every bucket with a 0 default.
        usage = SimpleNamespace(input_tokens=100, output_tokens=7)
        out = XAIMixin._xai_usage_dict(usage)
        self.assertEqual((out["input_tokens"], out["output_tokens"],
                          out.get("cache_read_input_tokens", 0)), (100, 7, 0))
        self.assertNotIn("cost_usd", out)

    def test_overreported_cache_never_goes_negative(self):
        usage = SimpleNamespace(input_tokens=10, output_tokens=1,
                                input_tokens_details=SimpleNamespace(cached_tokens=999))
        out = XAIMixin._xai_usage_dict(usage)
        self.assertEqual((out["input_tokens"], out["cache_read_input_tokens"]), (0, 10))

    def test_extras_pass_through(self):
        usage = SimpleNamespace(input_tokens=100, output_tokens=7,
                                cost_in_usd_ticks=50_000_000,
                                num_server_side_tools_used=2)
        out = XAIMixin._xai_usage_dict(usage)
        self.assertAlmostEqual(out["cost_usd"], 0.005)
        self.assertEqual(out["server_tool_calls"], 2)


class DisjointContractCase(unittest.TestCase):
    """The one property the cost log's TOKENS fields rest on, pinned for every
    subset-style provider at once: input + cache_read (+ cache_creation) must
    equal the provider's GROSS input, so in+cache_write+cache_read is the
    billed input volume and TOK-IN never counts a cached token twice."""

    def test_every_normalizer_is_disjoint(self):
        gross, hit = 10_000, 8_000
        cases = {
            "OpenAI": OpenAIMixin._openai_usage_dict(SimpleNamespace(
                input_tokens=gross, output_tokens=1,
                input_tokens_details=SimpleNamespace(cached_tokens=hit))),
            "Google": GeminiMixin._gemini_usage_dict(SimpleNamespace(
                prompt_token_count=gross, candidates_token_count=1,
                thoughts_token_count=0, cached_content_token_count=hit)),
            "xAI": XAIMixin._xai_usage_dict(SimpleNamespace(
                input_tokens=gross, output_tokens=1,
                input_tokens_details=SimpleNamespace(cached_tokens=hit))),
            "Moonshot": _kimi()._kimi_usage_dict(SimpleNamespace(
                prompt_tokens=gross, completion_tokens=1,
                prompt_tokens_details=SimpleNamespace(cached_tokens=hit))),
        }
        for provider, out in cases.items():
            with self.subTest(provider=provider):
                self.assertEqual(out["input_tokens"], gross - hit)
                self.assertEqual(out["cache_read_input_tokens"], hit)
                self.assertEqual(out["input_tokens"]
                                 + out.get("cache_creation_input_tokens", 0)
                                 + out["cache_read_input_tokens"], gross)


class CostArithmeticCase(unittest.TestCase):
    """End-to-end: normalizer buckets priced by _get_pricing must beat the
    old all-at-full-rate number, and must not double-count."""

    def test_openai_cached_call_is_cheaper_than_uncached(self):
        pricing = StreamingMixin._get_pricing("OpenAI", "gpt-5.6-luna")
        usage = OpenAIMixin._openai_usage_dict(SimpleNamespace(
            input_tokens=2714, output_tokens=16,
            input_tokens_details=SimpleNamespace(cached_tokens=2711)))
        actual = (usage["input_tokens"] * pricing["input"]
                  + usage["output_tokens"] * pricing["output"]
                  + usage["cache_read_input_tokens"] * pricing["cache_read"])
        naive = 2714 * pricing["input"] + 16 * pricing["output"]
        self.assertLess(actual, naive)
        # 2711 cached at 1/10 rate, 3 fresh at full rate
        self.assertAlmostEqual(
            actual,
            3 * (0.20 / 1e6) + 16 * (1.20 / 1e6) + 2711 * (0.02 / 1e6))

    def test_pro_tier_without_cached_rate_omits_the_key(self):
        # A 0.0 rate would silently price cached tokens FREE; absence makes the
        # accumulator's .get("cache_read", 0) the explicit, auditable choice.
        self.assertNotIn("cache_read",
                         StreamingMixin._get_pricing("OpenAI", "gpt-5-pro"))


if __name__ == "__main__":
    unittest.main()
