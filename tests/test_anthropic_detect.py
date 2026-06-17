"""Characterization test: UIMixin Anthropic model-detection helpers.

These are the methods Phase 1 will refactor (the four version-parsers
_is_anthropic_adaptive_model / _anthropic_rejects_temperature /
_anthropic_supports_max_effort / _anthropic_supports_xhigh_effort, plus the
composed _anthropic_mode_values and _is_anthropic_always_on_thinking).
Every expected value is an ACTUAL captured output from current code."""
import unittest

from tests._util import stub
from myagent.ui_mixin import UIMixin

# model -> (always_on, rejects_temp, max_effort, xhigh_effort, adaptive, mode_values)
EXPECTED = {
    "claude-fable-5":             (True,  True,  True,  True,  True,
                                   ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-mythos-5":            (True,  True,  True,  True,  True,
                                   ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-opus-4-8":            (False, True,  True,  True,  True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-opus-4-7":            (False, True,  True,  True,  True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]),
    "claude-opus-4-6":            (False, False, True,  False, True,
                                   ["Off", "Adaptive", "Low", "Medium", "High", "Max"]),
    "claude-opus-4-5":            (False, False, False, False, False,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
    "claude-sonnet-4-6":          (False, False, False, False, True,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
    "claude-sonnet-4-6-20260101": (False, False, False, False, True,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
    "claude-sonnet-4-5":          (False, False, False, False, False,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
    "claude-haiku-4-5":           (False, False, False, False, False,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
    "claude-opus-3":              (False, False, False, False, False,
                                   ["Off", "Adaptive", "Low", "Medium", "High"]),
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

    def test_adaptive_requires_anthropic_provider(self):
        # _is_anthropic_adaptive_model returns False unless self.provider == "Anthropic"
        other = stub(UIMixin, provider="OpenAI", model="gpt-5.2")
        self.assertFalse(other._is_anthropic_adaptive_model("claude-opus-4-8"))


if __name__ == "__main__":
    unittest.main()
