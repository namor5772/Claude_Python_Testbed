"""Characterization test: StreamingMixin._get_pricing (static).

Locks the longest-prefix match, the per-million -> per-token conversion, the
Anthropic 4-key vs OpenAI/Gemini 2-key dict shape, and None on miss. Values are
ACTUAL current outputs captured from the live code (including the float-repr
artifact in claude-haiku-4-5's cache_read)."""
import unittest
from datetime import date

from myagent.streaming_mixin import StreamingMixin

# Google's 3.6/3.7 Flash launch promo ("through December 31, 2026") and the
# sticker it reverts to ("starting January 1, 2027") — the DatedPrice pair.
GEMINI_FLASH_PROMO = {"input": 0.75 / 1_000_000, "output": 3.75 / 1_000_000,
                      "cache_read": 0.075 / 1_000_000}
GEMINI_FLASH_STICKER = {"input": 1.50 / 1_000_000, "output": 7.50 / 1_000_000,
                        "cache_read": 0.15 / 1_000_000}


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
        # Launched as intro pricing through 2026-08-31 ($2/$10); the pricing
        # page made it the permanent standard rate (checked 2026-08-25) —
        # there is no September flip to $3/$15.
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
        # OpenAI/Gemini gained a cache_read key on 2026-07-31 (their caching is
        # automatic, so the discount was always billed — it just wasn't
        # reported). Rates re-verified against the live Standard-tier tables
        # the same day; several base rates were Batch-tier numbers.
        # GPT-5.6 tiers (2026-07-09). sol re-trued 2026-08-25: it had been
        # entered at gpt-5.5's $5/$30 but bills $4/$20 (cached $0.40). There
        # is no bare "gpt-5.6" family row any more — an unknown 5.6 id is
        # unpriced rather than mispriced.
        ("OpenAI", "gpt-5.6-sol"): {"input": 4.00 / 1_000_000,
                                    "output": 20.00 / 1_000_000,
                                    "cache_read": 0.40 / 1_000_000},
        ("OpenAI", "gpt-5.6-unknown-tier"): None,
        # Arithmetic form (like the Kimi cases below) wherever the naive
        # literal would miss the live float-division repr — see the docstring.
        ("OpenAI", "gpt-5.6-terra"): {"input": 2.00 / 1_000_000,
                                      "output": 12.00 / 1_000_000,
                                      "cache_read": 0.20 / 1_000_000},
        ("OpenAI", "gpt-5.6-luna"): {"input": 0.20 / 1_000_000,
                                     "output": 1.20 / 1_000_000,
                                     "cache_read": 0.02 / 1_000_000},
        ("OpenAI", "gpt-5.2"): {"input": 1.75e-06, "output": 1.4e-05,
                                "cache_read": 1.75e-07},
        ("OpenAI", "gpt-5.1"): {"input": 1.25e-06, "output": 1e-05,
                                "cache_read": 1.25e-07},
        ("OpenAI", "gpt-5.5"): {"input": 5e-06, "output": 3e-05,
                                "cache_read": 5e-07},
        # GPT-4.1 discounts cached input by 1/4, not the GPT-5 families' 1/10
        ("OpenAI", "gpt-4.1"): {"input": 2e-06, "output": 8e-06,
                                "cache_read": 5e-07},
        ("OpenAI", "gpt-4.1-mini"): {"input": 0.40 / 1_000_000,
                                     "output": 1.60 / 1_000_000,
                                     "cache_read": 0.10 / 1_000_000},
        # The -pro tiers have NO cached tier — the key must be absent entirely,
        # not present as 0 (which would silently price cached tokens free)
        ("OpenAI", "gpt-5.5-pro"): {"input": 3e-05, "output": 0.00018},
        ("OpenAI", "gpt-5.2-pro"): {"input": 2.1e-05, "output": 0.000168},
        # gpt-5-pro survives (no announced retirement) and must not fall to
        # None now that the bare "gpt-5" fallback entry is gone
        ("OpenAI", "gpt-5-pro"): {"input": 1.5e-05, "output": 0.00012},
        # Retired / scheduled-retirement ids are unpriced (2026-07 audit):
        # gpt-4o retired 2026-02-16; the gpt-5.0 base tiers retire 2026-12-11;
        # the o-series retires 2026-10-23 / 2026-12-11 → gpt-5.6
        ("OpenAI", "gpt-4o"): None,
        ("OpenAI", "gpt-5"): None,
        ("OpenAI", "gpt-5-mini"): None,
        ("OpenAI", "o3"): None,
        ("OpenAI", "o4-mini"): None,
        # Gemini 2.5 (sunset ≥ 2026-10-16) unpriced under the same policy
        ("Google", "gemini-2.5-pro"): None,
        # Gemini's cached rate is exactly 1/10 of input across the family
        ("Google", "gemini-3-pro"): {"input": 2.00 / 1_000_000,
                                     "output": 12.00 / 1_000_000,
                                     "cache_read": 0.20 / 1_000_000},
        # gemini-3.5-flash must NOT prefix-match down to the cheaper
        # "gemini-3" fallback entry.
        ("Google", "gemini-3.5-flash"): {"input": 1.5e-06, "output": 9e-06,
                                         "cache_read": 1.5e-07},
        # ...and gemini-3.5-flash-lite must not match gemini-3.5-flash (longer
        # prefix wins). The floating gemini-flash-lite-latest alias resolved
        # to it on 2026-08-25 (it had been 3.1-flash-lite at $0.25/$1.50).
        ("Google", "gemini-3.5-flash-lite"): {"input": 3e-07, "output": 2.5e-06,
                                              "cache_read": 3e-08},
        ("Google", "gemini-flash-lite-latest"): {"input": 3e-07, "output": 2.5e-06,
                                                 "cache_read": 3e-08},
        # gemini-3.6-flash / 3.7-flash / gemini-flash-latest are DatedPrice
        # entries — see DATED_CASES below.
        ("xAI", "grok-4.3"): {"input": 1.25e-06, "output": 2.5e-06},
        ("xAI", "grok-4.5"): {"input": 2e-06, "output": 6e-06},
        ("xAI", "grok-4.6"): {"input": 2e-06, "output": 6e-06},
        ("xAI", "grok-4.20-multi-agent-0309"): {"input": 1.25e-06, "output": 2.5e-06},
        ("xAI", "grok-build-0.1"): {"input": 1e-06, "output": 2e-06},
        # Aliases (live catalog 2026-07-17, re-checked 2026-08-18):
        # grok-code-fast-1 folds into grok-build-0.1's price; grok-latest
        # floats — it served grok-4.3 until 2026-08 and grok-4.6 since, so
        # its row tracks the current target; the longer grok-build-latest
        # entry (re-aliased to grok-4.5) must win over the cheaper
        # grok-build prefix.
        ("xAI", "grok-code-fast-1"): {"input": 1e-06, "output": 2e-06},
        ("xAI", "grok-latest"): {"input": 2e-06, "output": 6e-06},
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
        # kimi-k2.5 sunsets 2026-08-31 (never served to this account) —
        # unpriced under the retiring-models policy since 2026-08-25
        ("Moonshot", "kimi-k2.5"): None,
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

    # (provider, model, today) -> expected. A DatedPrice bills the promo on
    # its LAST day (inclusive "through December 31") and the sticker from the
    # next day; the boundary is resolved per lookup, never at import.
    DATED_CASES = {
        ("Google", "gemini-3.7-flash", date(2026, 8, 25)): GEMINI_FLASH_PROMO,
        ("Google", "gemini-3.7-flash", date(2026, 12, 31)): GEMINI_FLASH_PROMO,
        ("Google", "gemini-3.7-flash", date(2027, 1, 1)): GEMINI_FLASH_STICKER,
        ("Google", "gemini-3.6-flash", date(2026, 12, 31)): GEMINI_FLASH_PROMO,
        ("Google", "gemini-3.6-flash", date(2027, 1, 1)): GEMINI_FLASH_STICKER,
        # The floating alias resolves to 3.7-flash (verified live 2026-08-25)
        # and must NOT prefix-match the cheaper bare "gemini-3" entry.
        ("Google", "gemini-flash-latest", date(2026, 12, 31)): GEMINI_FLASH_PROMO,
        ("Google", "gemini-flash-latest", date(2027, 1, 1)): GEMINI_FLASH_STICKER,
        # A plain-tuple entry ignores `today` entirely.
        ("Google", "gemini-3.5-flash", date(2030, 1, 1)): {
            "input": 1.5e-06, "output": 9e-06, "cache_read": 1.5e-07},
        ("Anthropic", "claude-sonnet-5", date(2030, 1, 1)): {
            "input": 2e-06, "output": 1e-05,
            "cache_write": 2.5e-06, "cache_read": 2.0000000000000002e-07},
    }

    def test_pricing(self):
        for (provider, model), expected in self.CASES.items():
            with self.subTest(provider=provider, model=model):
                self.assertEqual(
                    StreamingMixin._get_pricing(provider, model), expected)

    def test_dated_pricing(self):
        for (provider, model, today), expected in self.DATED_CASES.items():
            with self.subTest(provider=provider, model=model, today=today):
                self.assertEqual(
                    StreamingMixin._get_pricing(provider, model, today=today),
                    expected)

    def test_dated_entry_resolves_without_an_explicit_date(self):
        # The production call site passes no date: whichever side of the
        # boundary the real clock is on, the result is a concrete rate dict.
        got = StreamingMixin._get_pricing("Google", "gemini-3.7-flash")
        self.assertIn(got, (GEMINI_FLASH_PROMO, GEMINI_FLASH_STICKER))


if __name__ == "__main__":
    unittest.main()
