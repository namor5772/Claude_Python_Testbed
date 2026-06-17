"""Characterization test: ChatMixin._latex_to_unicode (static).

Locks the exact transform, including quirks: unknown commands lose both the
backslash AND grouping braces (\\unknowncmd{keep} -> unknowncmdkeep); bare
^2 / _y are left literal but braced ^{2} -> superscript; $$..$$ delimiters are
stripped but the inner ^2 stays bare. Values are ACTUAL captured outputs."""
import unittest

from myagent.chat_mixin import ChatMixin

CASES = [
    (r"The angle is $\theta$ and $\alpha + \beta$.", "The angle is θ and α + β."),
    (r"Fraction \frac{a}{b} and \frac{1}{2}.", "Fraction a/b and 1/2."),
    (r"\[ x = \sqrt{y} \]", " x = √y "),
    (r"$$E = mc^2$$", "E = mc^2"),
    (r"x^{2} + y_{i} = z", "x² + yᵢ = z"),
    (r"\sum_{i=1}^{n} \times \leq \infty", "Σᵢ₌₁ⁿ × ≤ ∞"),
    (r"Cost is $5 and $10 dollars.", "Cost is 5 and 10 dollars."),
    (r"\unknowncmd{keep} and \times", "unknowncmdkeep and ×"),
    (r"x^2 and _y stay literal", "x^2 and _y stay literal"),
    (r"\sin x + \cos y \to 0", "sin x + cos y → 0"),
]


class TestLatexToUnicode(unittest.TestCase):
    def test_cases(self):
        for src, expected in CASES:
            with self.subTest(src=src):
                self.assertEqual(ChatMixin._latex_to_unicode(src), expected)


if __name__ == "__main__":
    unittest.main()
