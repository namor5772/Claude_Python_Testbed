"""Characterization tests for the OpenAI / Gemini usage normalizers.

Both providers cache AUTOMATICALLY (no client opt-in, unlike Anthropic), so the
discount was always on the bill — it just wasn't reported, which overstated
every OpenAI/Google line in APICostLog.txt until 2026-07-31.

The load-bearing property is that the emitted buckets are DISJOINT. Both
providers report cached tokens as a SUBSET of their input total, while
stream_worker prices input at the full rate AND cache_read at the cached rate —
so failing to subtract would double-charge every cached token.

Fixture values are the real ones observed live on 2026-07-31.
"""

import unittest
from types import SimpleNamespace

from myagent.gemini_mixin import GeminiMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin


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
