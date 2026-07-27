"""Characterization test: StreamingMixin._get_pricing (static).

Locks the longest-prefix match, the per-million -> per-token conversion, the
Anthropic 4-key vs OpenAI/Gemini 2-key dict shape, and None on miss. Values are
ACTUAL current outputs captured from the live code (including the float-repr
artifact in claude-haiku-4-5's cache_read)."""
import unittest

from myagent.streaming_mixin import StreamingMixin


class TestGetPricing(unittest.TestCase):
    CASES = {
        ("Anthropic", "claude-opus-4-8"): {
            "input": 5e-06, "output": 2.5e-05,
            "cache_write": 6.25e-06, "cache_read": 5e-07},
        ("Anthropic", "claude-sonnet-4-6"): {
            "input": 3e-06, "output": 1.5e-05,
            "cache_write": 3.75e-06, "cache_read": 3e-07},
        ("Anthropic", "claude-haiku-4-5"): {
            "input": 1e-06, "output": 5e-06,
            "cache_write": 1.25e-06, "cache_read": 1.0000000000000001e-07},
        ("Anthropic", "claude-fable-5"): {
            "input": 1e-05, "output": 5e-05,
            "cache_write": 1.25e-05, "cache_read": 1e-06},
        # Intro pricing through 2026-08-31 ($2/$10) — update alongside the
        # constants.py entry when it flips to the $3/$15 sticker.
        ("Anthropic", "claude-sonnet-5"): {
            "input": 2e-06, "output": 1e-05,
            "cache_write": 2.5e-06, "cache_read": 2.0000000000000002e-07},
        # Opus 5 — drop-in successor to Opus 4.8 at the same rates
        ("Anthropic", "claude-opus-5"): {
            "input": 5e-06, "output": 2.5e-05,
            "cache_write": 6.25e-06, "cache_read": 5e-07},
        # Retired generations were dropped from the table (2026-07 audit)
        ("Anthropic", "claude-3-haiku-20240307"): None,
        ("Anthropic", "claude-opus-4-1"): None,
        ("Anthropic", "totally-unknown-xyz"): None,
        # GPT-5.6 tiers (2026-07-09); the tier ids must NOT prefix-match down
        # to the bare "gpt-5.6" fallback entry
        ("OpenAI", "gpt-5.6-sol"): {"input": 5e-06, "output": 3e-05},
        ("OpenAI", "gpt-5.6-terra"): {"input": 2.5e-06, "output": 1.5e-05},
        ("OpenAI", "gpt-5.6-luna"): {"input": 1e-06, "output": 6e-06},
        ("OpenAI", "gpt-5.2"): {"input": 8.75e-07, "output": 7e-06},
        # gpt-4o retired from the API 2026-02-16 — unpriced since the audit
        ("OpenAI", "gpt-4o"): None,
        # gpt-5.5 must NOT prefix-match down to the cheap bare "gpt-5" entry
        ("OpenAI", "gpt-5.5"): {"input": 5e-06, "output": 3e-05},
        ("OpenAI", "gpt-5.5-pro"): {"input": 3e-05, "output": 0.00018},
        ("Google", "gemini-2.5-pro"): {"input": 1.25e-06, "output": 1e-05},
        ("Google", "gemini-3-pro"): {"input": 2e-06, "output": 1.2e-05},
        # gemini-3.5-flash must NOT prefix-match down to the cheaper
        # "gemini-3" fallback entry; the -latest alias resolves to it too.
        ("Google", "gemini-3.5-flash"): {"input": 1.5e-06, "output": 9e-06},
        ("Google", "gemini-flash-latest"): {"input": 1.5e-06, "output": 9e-06},
        ("xAI", "grok-4.3"): {"input": 1.25e-06, "output": 2.5e-06},
        ("xAI", "grok-4.5"): {"input": 2e-06, "output": 6e-06},
        ("xAI", "grok-4.20-multi-agent-0309"): {"input": 1.25e-06, "output": 2.5e-06},
        ("xAI", "grok-build-0.1"): {"input": 1e-06, "output": 2e-06},
        # Aliases (live catalog 2026-07-17): grok-code-fast-1 folds into
        # grok-build-0.1's price; grok-latest resolves to grok-4.3; the
        # longer grok-build-latest entry (re-aliased to grok-4.5) must win
        # over the cheaper grok-build prefix.
        ("xAI", "grok-code-fast-1"): {"input": 1e-06, "output": 2e-06},
        ("xAI", "grok-latest"): {"input": 1.25e-06, "output": 2.5e-06},
        ("xAI", "grok-build-latest"): {"input": 2e-06, "output": 6e-06},
        # Retired families are no longer priced (the API rejects their ids)
        ("xAI", "grok-3-mini"): None,
        ("xAI", "totally-unknown-xyz"): None,
        # Kimi (Moonshot) — cache-MISS input rates; the mixin's cost_usd
        # applies the cache-hit discount separately. Expressed as arithmetic
        # (rate / 1e6) to match the live float conversion exactly.
        ("Moonshot", "kimi-k3"): {"input": 3.00 / 1_000_000,
                              "output": 15.00 / 1_000_000},
        ("Moonshot", "kimi-k2.6"): {"input": 0.95 / 1_000_000,
                                "output": 4.00 / 1_000_000},
        ("Moonshot", "kimi-k2.5"): {"input": 0.60 / 1_000_000,
                                "output": 3.00 / 1_000_000},
        # -highspeed (2x rates) must NOT prefix-match down to kimi-k2.7-code
        ("Moonshot", "kimi-k2.7-code-highspeed"): {"input": 1.90 / 1_000_000,
                                               "output": 8.00 / 1_000_000},
        ("Moonshot", "kimi-k2.7-code"): {"input": 0.95 / 1_000_000,
                                     "output": 4.00 / 1_000_000},
        # The discontinued DASH family (kimi-k2-thinking etc.) is filtered out
        # of the picker and deliberately unpriced
        ("Moonshot", "kimi-k2-thinking"): None,
        ("Moonshot", "totally-unknown-xyz"): None,
        ("Ollama", "qwen3"): None,
        ("Bogus", "x"): None,
    }

    def test_pricing(self):
        for (provider, model), expected in self.CASES.items():
            with self.subTest(provider=provider, model=model):
                self.assertEqual(
                    StreamingMixin._get_pricing(provider, model), expected)


if __name__ == "__main__":
    unittest.main()
