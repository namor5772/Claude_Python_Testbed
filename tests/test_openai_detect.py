"""Characterization test: OpenAIMixin gpt-5 / o-series detection helpers.

Not refactored by the approved Phases 1-3, but a cheap broad regression net:
these helpers gate provider param logic and are easy to perturb. Every value is
an ACTUAL captured output from current code (note: _parse_gpt5_minor parses the
minor even for -chat models, though _is_gpt5_family excludes them)."""
import unittest

from tests._util import stub
from myagent.openai_mixin import OpenAIMixin

# model -> (minor, family, reasoning, none, xhigh, max, temp_at_none, chat, verbosity)
# `max` (reasoning.effort='max') is a GPT-5.6 addition: live-probed 2026-08-25,
# all three 5.6 tiers accept it and gpt-5.5 / 5.4 / 5.4-mini reject it.
EXPECTED = {
    "gpt-5":               (0, True,  True,  False, False, False, False, False, True),
    "gpt-5.0":             (0, True,  True,  False, False, False, False, False, True),
    "gpt-5.1":             (1, True,  True,  True,  False, False, False, False, True),
    "gpt-5.2":             (2, True,  True,  True,  True,  False, False, False, True),
    "gpt-5.4":             (4, True,  True,  True,  True,  False, True,  False, True),
    "gpt-5.5":             (5, True,  True,  True,  True,  False, True,  False, True),
    "gpt-5.5-pro":         (5, True,  True,  True,  True,  False, True,  False, True),
    "gpt-5.6-sol":         (6, True,  True,  True,  True,  True,  True,  False, True),
    "gpt-5.6-terra":       (6, True,  True,  True,  True,  True,  True,  False, True),
    "gpt-5.6-luna":        (6, True,  True,  True,  True,  True,  True,  False, True),
    "gpt-5.2-mini":        (2, True,  True,  True,  False, False, False, False, True),
    "gpt-5.1-nano":        (1, True,  True,  True,  False, False, False, False, True),
    "gpt-5.1-chat-latest": (1, False, False, False, False, False, False, True,  True),
    "gpt-5-chat":          (0, False, False, False, False, False, False, True,  True),
    "o1":                  (0, False, True,  False, False, False, False, False, False),
    "o3":                  (0, False, True,  False, False, False, False, False, False),
    "o4-mini":             (0, False, True,  False, False, False, False, False, False),
    "gpt-4o":              (0, False, False, False, False, False, False, False, False),
    "gpt-4.1":             (0, False, False, False, False, False, False, False, False),
}


# GPT-6 Astra (2026-09-03), probed live 2026-09-06: reasoning.effort accepts
# low/medium/high/xhigh/max ONLY ("none" / "minimal" are HTTP 400 — the model
# cannot stop reasoning), temperature is rejected unconditionally, and
# text.verbosity is accepted. It is NOT a gpt-5 family member (minor 0, family
# False — so the GPT-5.0 "minimal" / temp=1.0 paths must never fire), yet it IS
# a reasoning model with both xhigh and max. The columns are the same as above.
EXPECTED_GPT6 = {
    "gpt-6-astra":         (0, False, True,  False, True,  True,  False, False, True),
    "gpt-6.1-nova":        (0, False, True,  False, True,  True,  False, False, True),
}


class TestOpenAIDetect(unittest.TestCase):
    def setUp(self):
        self.p = stub(OpenAIMixin, provider="OpenAI", model="gpt-5.2")

    def _check(self, model, exp):
        minor, family, reasoning, none, xhigh, mx, temp_none, chat, verb = exp
        self.assertEqual(self.p._parse_gpt5_minor(model), minor)
        self.assertEqual(self.p._is_gpt5_family(model), family)
        self.assertEqual(self.p._is_openai_reasoning_model(model), reasoning)
        self.assertEqual(self.p._has_reasoning_none(model), none)
        self.assertEqual(self.p._has_reasoning_xhigh(model), xhigh)
        self.assertEqual(self.p._has_reasoning_max(model), mx)
        self.assertEqual(self.p._gpt5_supports_temp_at_none(model), temp_none)
        self.assertEqual(self.p._is_gpt5_chat_model(model), chat)
        self.assertEqual(self.p._has_openai_verbosity(model), verb)

    def test_matrix(self):
        for model, exp in EXPECTED.items():
            with self.subTest(model=model):
                self._check(model, exp)
                # No pre-GPT-6 id is always-reasoning
                self.assertFalse(self.p._is_gpt6_family(model))
                self.assertFalse(self.p._openai_always_reasoning(model))

    def test_gpt6_matrix(self):
        for model, exp in EXPECTED_GPT6.items():
            with self.subTest(model=model):
                self._check(model, exp)
                self.assertTrue(self.p._is_gpt6_family(model))
                self.assertTrue(self.p._openai_always_reasoning(model))

    def test_gpt6_family_is_exact(self):
        # A future "gpt-60" or a -chat Instant variant must not ride the
        # always-reasoning contract by accident.
        self.assertFalse(self.p._is_gpt6_family("gpt-60-foo"))
        self.assertFalse(self.p._is_gpt6_family("gpt-6-chat-latest"))
        self.assertTrue(self.p._is_gpt6_family("gpt-6"))

    def test_gpt6_effort_floor(self):
        # A "none"/"minimal" carried over from a GPT-5.x instruction (headless
        # runs never pass through the combobox coercion) sends the ladder
        # floor "low"; real rungs pass through untouched.
        p = stub(OpenAIMixin, provider="OpenAI", model="gpt-6-astra")
        for stale in ("none", "minimal", "off", "adaptive", "", None):
            p.thinking_effort = stale
            self.assertEqual(p._openai_effective_effort(), "low", stale)
        for rung in ("low", "medium", "high", "xhigh", "max"):
            p.thinking_effort = rung
            self.assertEqual(p._openai_effective_effort(), rung)

    def test_cache_write_billing_follows_the_pricing_row(self):
        # Only GPT-6 Astra carries a 4th (cache-write) pricing element.
        self.assertTrue(self.p._openai_bills_cache_writes("gpt-6-astra"))
        for mid in ("gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.5", "gpt-4.1",
                    "gpt-5.5-pro", "gpt-6-unpriced-tier", "o3"):
            self.assertFalse(self.p._openai_bills_cache_writes(mid), mid)


if __name__ == "__main__":
    unittest.main()
