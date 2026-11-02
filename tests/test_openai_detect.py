"""Characterization test: OpenAIMixin gpt-5 / o-series detection helpers.

Not refactored by the approved Phases 1-3, but a cheap broad regression net:
these helpers gate provider param logic and are easy to perturb. Every value is
an ACTUAL captured output from current code (note: _parse_gpt5_minor parses the
minor even for -chat models, though _is_gpt5_family excludes them)."""
import unittest
from types import SimpleNamespace

from tests._util import stub
from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin

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
# a reasoning model with both xhigh and max. GPT-6 Sol and Luna (2026-09-14),
# probed live 2026-09-23, are NOT always-reasoning: none/low/medium/high/
# xhigh/max ("minimal" is HTTP 400), temperature ONLY at effort=none — the
# GPT-5.6 rule — so the always-reasoning contract is a per-tier list
# (OPENAI_ALWAYS_REASONING_PREFIXES: astra alone), and an unknown future tier
# (gpt-6.1-nova) gets the None rung. GPT-6.1 Sol (2026-09-27), probed live
# 2026-09-30, IS always-reasoning like astra (low..max, no temperature), so it
# is listed. GPT-6.1 Terra (created 2026-10-13), probed live 2026-11-02, is
# NOT: none accepted, temperature at none only — sol / luna's contract.
# The columns are the same as above, plus a trailing always-reasoning flag.
EXPECTED_GPT6 = {
    "gpt-6-astra":         (0, False, True,  False, True,  True,  False, False, True,  True),
    "gpt-6-sol":           (0, False, True,  True,  True,  True,  True,  False, True,  False),
    "gpt-6-luna":          (0, False, True,  True,  True,  True,  True,  False, True,  False),
    "gpt-6.1-sol":         (0, False, True,  False, True,  True,  False, False, True,  True),
    "gpt-6.1-terra":       (0, False, True,  True,  True,  True,  True,  False, True,  False),
    "gpt-6.1-nova":        (0, False, True,  True,  True,  True,  True,  False, True,  False),
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
                self._check(model, exp[:9])
                self.assertTrue(self.p._is_gpt6_family(model))
                self.assertEqual(self.p._openai_always_reasoning(model), exp[9])

    def test_always_reasoning_is_a_tier_list_not_the_family(self):
        # A -chat Instant variant of the listed tier never rides the contract
        self.assertFalse(self.p._openai_always_reasoning("gpt-6-astra-chat-latest"))
        # ...and a dated astra snapshot would
        self.assertTrue(self.p._openai_always_reasoning("gpt-6-astra-2026-08-27"))

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
        # sol / luna keep a saved "none" (their real floor) and map "off" to
        # it; "minimal" still becomes "low" (it means reason a little).
        for mid in ("gpt-6-sol", "gpt-6-luna"):
            p = stub(OpenAIMixin, provider="OpenAI", model=mid)
            for stale, expected in (("none", "none"), ("off", "none"), ("adaptive", "none"),
                                    ("", "none"), ("minimal", "low"), ("max", "max")):
                p.thinking_effort = stale
                self.assertEqual(p._openai_effective_effort(), expected, (mid, stale))

    def test_deprecated_ids_leave_the_picker_and_stay_served(self):
        # The retirement policy over a fake listing, through the REAL
        # _fetch_openai_models (2026-11-02: every id the deprecations page
        # names leaves the picker and the price table; the served set keeps
        # it for a pinned instruction, and its param wiring still answers).
        listing = ["gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano", "gpt-5", "gpt-5-pro",
                   "gpt-5.1", "gpt-5.1-codex-max", "gpt-5.1-codex-mini", "gpt-5.1-chat-latest",
                   "gpt-5.2", "gpt-5.2-pro", "gpt-5.2-chat-latest", "gpt-5.2-codex",
                   "gpt-5.3-chat-latest", "gpt-5.3-codex", "gpt-5.4", "gpt-5.4-mini",
                   "gpt-5.4-nano", "gpt-5.4-pro", "gpt-5.5", "gpt-5.5-pro",
                   "gpt-5.6-terra", "gpt-6-astra", "gpt-6.1-terra", "gpt-5-search-api",
                   "o3", "gpt-4o", "text-embedding-3-small"]
        data = [SimpleNamespace(id=mid) for mid in listing]
        host = stub(OpenAIMixin, openai_client=SimpleNamespace(
            models=SimpleNamespace(list=lambda: SimpleNamespace(data=data))))
        picker = host._fetch_openai_models()
        self.assertEqual(picker, ["gpt-5.2", "gpt-5.2-pro", "gpt-5.4", "gpt-5.4-mini",
                                  "gpt-5.4-pro", "gpt-5.5", "gpt-5.5-pro", "gpt-5.6-terra",
                                  "gpt-6-astra", "gpt-6.1-terra"])
        self.assertEqual(host._served_model_ids["OpenAI"], set(listing))
        for mid in ("gpt-4.1", "gpt-4.1-mini", "gpt-5-pro", "gpt-5.1", "gpt-5.1-codex-max",
                    "gpt-5.2-chat-latest", "gpt-5.2-codex", "gpt-5.3-codex", "gpt-5.4-nano"):
            self.assertIsNone(StreamingMixin._get_pricing("OpenAI", mid), mid)
        self.assertEqual(host._openai_reasoning_values("gpt-5.1"), ["None", "Low", "Medium", "High"])
        self.assertTrue(host._is_gpt5_family("gpt-5.1"))
        # A retiring sub-tier whose family row survives is an explicit None
        # row — unpriced AND warned, never priced by the family's row (nano at
        # gpt-5.4's $2.50 would be 12x over) — while the family itself is fine.
        self.assertIsNotNone(StreamingMixin._unpriced_model_warning("OpenAI", "gpt-5.4-nano"))
        self.assertIsNotNone(StreamingMixin._unpriced_model_warning("OpenAI", "gpt-5.2-codex"))
        self.assertIsNone(StreamingMixin._unpriced_model_warning("OpenAI", "gpt-5.4"))
        self.assertIsNone(StreamingMixin._unpriced_model_warning("OpenAI", "gpt-5.4-mini"))
        self.assertFalse(host._openai_bills_cache_writes("gpt-5.4-nano"))

    def test_cache_write_billing_follows_the_pricing_row(self):
        # OpenAI bills cache writes from GPT-5.6 on (1.25x input): the 5.6
        # tiers and GPT-6 Astra carry a 4th pricing element; the older
        # families list no write price (pricing page re-read 2026-09-06).
        for mid in ("gpt-6-astra", "gpt-6.1-sol", "gpt-6.1-terra", "gpt-6-sol", "gpt-6-luna",
                    "gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna"):
            self.assertTrue(self.p._openai_bills_cache_writes(mid), mid)
        for mid in ("gpt-5.5", "gpt-5.4", "gpt-5.2", "gpt-5.1", "gpt-4.1",
                    "gpt-5.5-pro", "gpt-6-unpriced-tier", "o3"):
            self.assertFalse(self.p._openai_bills_cache_writes(mid), mid)


if __name__ == "__main__":
    unittest.main()
