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
        ("Anthropic", "totally-unknown-xyz"): None,
        ("OpenAI", "gpt-5.2"): {"input": 8.75e-07, "output": 7e-06},
        ("OpenAI", "gpt-4o"): {"input": 2.5e-06, "output": 1e-05},
        # gpt-5.5 must NOT prefix-match down to the cheap bare "gpt-5" entry
        ("OpenAI", "gpt-5.5"): {"input": 5e-06, "output": 3e-05},
        ("OpenAI", "gpt-5.5-pro"): {"input": 3e-05, "output": 0.00018},
        ("Gemini", "gemini-2.5-pro"): {"input": 1.25e-06, "output": 1e-05},
        ("Gemini", "gemini-3-pro"): {"input": 2e-06, "output": 1.2e-05},
        # gemini-3.5-flash must NOT prefix-match down to the cheaper
        # "gemini-3" fallback entry; the -latest alias resolves to it too.
        ("Gemini", "gemini-3.5-flash"): {"input": 1.5e-06, "output": 9e-06},
        ("Gemini", "gemini-flash-latest"): {"input": 1.5e-06, "output": 9e-06},
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
