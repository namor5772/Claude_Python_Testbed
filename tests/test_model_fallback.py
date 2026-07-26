"""Characterization tests for the provider-default model fallback.

_restore_model_params falls back to _default_model_for_provider() when a
saved model isn't in the current provider's list. It must be the curated
default, never available_models[0]: fetched lists are sorted, and xAI's
sorts to grok-4.20-0309-non-reasoning — which is what a Heartbeat-launched
FNE run (2026-07-25) silently ended up on when MOONSHOT_API_KEY was absent.
"""
import unittest

from myagent.ui_mixin import UIMixin
from myagent.constants import (
    DEFAULT_MODEL, OPENAI_DEFAULT_MODEL, GEMINI_DEFAULT_MODEL,
    XAI_DEFAULT_MODEL, KIMI_DEFAULT_MODEL, OLLAMA_DEFAULT_MODEL,
    XAI_FALLBACK_MODELS,
)


class TestDefaultModelForProvider(unittest.TestCase):
    def test_each_provider_maps_to_its_curated_default(self):
        cases = {
            "Anthropic": DEFAULT_MODEL,
            "OpenAI": OPENAI_DEFAULT_MODEL,
            "Google": GEMINI_DEFAULT_MODEL,
            "xAI": XAI_DEFAULT_MODEL,
            "Moonshot": KIMI_DEFAULT_MODEL,
            "Ollama": OLLAMA_DEFAULT_MODEL,
        }
        for provider, expected in cases.items():
            self.assertEqual(
                UIMixin._default_model_for_provider(provider), expected)

    def test_unknown_provider_falls_back_to_anthropic_default(self):
        self.assertEqual(
            UIMixin._default_model_for_provider("NoSuchProvider"),
            DEFAULT_MODEL)

    def test_xai_default_is_not_the_lexicographic_first(self):
        # The regression this guards: a sorted xAI catalog puts the pinned
        # grok-4.20 variants ahead of grok-4.3, so [0] is a terrible fallback.
        sorted_catalog = sorted(XAI_FALLBACK_MODELS)
        self.assertEqual(XAI_DEFAULT_MODEL, "grok-4.3")
        self.assertNotEqual(XAI_DEFAULT_MODEL, sorted_catalog[0])


if __name__ == "__main__":
    unittest.main()
