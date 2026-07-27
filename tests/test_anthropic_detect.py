"""Characterization test: UIMixin Anthropic model-detection helpers.

Covers the version-parsers (_is_anthropic_adaptive_model /
_anthropic_rejects_temperature / _anthropic_supports_max_effort /
_anthropic_supports_xhigh_effort / _anthropic_thinking_on_by_default), plus
the composed _anthropic_mode_values and _is_anthropic_always_on_thinking.
Expected values reflect the LIVE Models API capability tree (verified
2026-07): Sonnet 5 is adaptive-only with xhigh+max effort, rejects
temperature, and runs adaptive thinking when the param is omitted; Sonnet
4.6 supports max (an older revision capped Sonnet at High — stale); minor-
less Claude 5 ids parse as (major, 0)."""
import unittest

from tests._util import stub
from myagent.ui_mixin import UIMixin

# model -> (always_on, rejects_temp, max_effort, xhigh_effort, adaptive, mode_values)
EXPECTED = {
    "claude-fable-5":             (True,  True,  True,  True,  True,
                                   ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-mythos-5":            (True,  True,  True,  True,  True,
                                   ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-opus-5":              (False, True,  True,  True,  True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-opus-4-8":            (False, True,  True,  True,  True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-opus-4-7":            (False, True,  True,  True,  True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-opus-4-6":            (False, False, True,  False, True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Max"]),
    "claude-opus-4-5":            (False, False, False, False, False,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
    "claude-sonnet-5":            (False, True,  True,  True,  True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-sonnet-5-20260601":   (False, True,  True,  True,  True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-sonnet-4-6":          (False, False, True,  False, True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Max"]),
    "claude-sonnet-4-6-20260101": (False, False, True,  False, True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Max"]),
    "claude-sonnet-4-5":          (False, False, False, False, False,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
    "claude-haiku-4-5":           (False, False, False, False, False,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
    "claude-opus-3":              (False, False, False, False, False,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
}

# model -> omitting the thinking param runs adaptive (True) vs thinking-off
THINKING_DEFAULT_ON = {
    "claude-fable-5": True,
    "claude-mythos-5": True,
    # Opus 5 runs adaptive thinking on omission (unlike Opus 4.8/4.7) — the
    # off branch must send an explicit disable (2026-07 audit fix).
    "claude-opus-5": True,
    "claude-sonnet-5": True,
    "claude-sonnet-5-20260601": True,
    "claude-sonnet-4-6": False,
    "claude-sonnet-4-5": False,
    "claude-opus-4-8": False,
    "claude-haiku-4-5": False,
}


class TestAnthropicDetect(unittest.TestCase):
    def setUp(self):
        self.u = stub(UIMixin, provider="Anthropic", model="claude-opus-4-8")

    def test_matrix(self):
        for model, exp in EXPECTED.items():
            always, rej, mx, xh, adp, modes = exp
            with self.subTest(model=model):
                self.assertEqual(self.u._is_anthropic_always_on_thinking(model), always)
                self.assertEqual(self.u._anthropic_rejects_temperature(model), rej)
                self.assertEqual(self.u._anthropic_supports_max_effort(model), mx)
                self.assertEqual(self.u._anthropic_supports_xhigh_effort(model), xh)
                self.assertEqual(self.u._is_anthropic_adaptive_model(model), adp)
                self.assertEqual(self.u._anthropic_mode_values(model), modes)

    def test_thinking_on_by_default(self):
        for model, expected in THINKING_DEFAULT_ON.items():
            with self.subTest(model=model):
                self.assertEqual(self.u._anthropic_thinking_on_by_default(model), expected)

    def test_adaptive_requires_anthropic_provider(self):
        # _is_anthropic_adaptive_model returns False unless self.provider == "Anthropic"
        other = stub(UIMixin, provider="OpenAI", model="gpt-5.2")
        self.assertFalse(other._is_anthropic_adaptive_model("claude-opus-4-8"))


if __name__ == "__main__":
    unittest.main()
