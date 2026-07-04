"""Characterization test: OpenAIMixin gpt-5 / o-series detection helpers.

Not refactored by the approved Phases 1-3, but a cheap broad regression net:
these helpers gate provider param logic and are easy to perturb. Every value is
an ACTUAL captured output from current code (note: _parse_gpt5_minor parses the
minor even for -chat models, though _is_gpt5_family excludes them)."""
import unittest

from tests._util import stub
from myagent.openai_mixin import OpenAIMixin

# model -> (minor, family, reasoning, none, xhigh, temp_at_none, chat, verbosity)
EXPECTED = {
    "gpt-5":               (0, True,  True,  False, False, False, False, True),
    "gpt-5.0":             (0, True,  True,  False, False, False, False, True),
    "gpt-5.1":             (1, True,  True,  True,  False, False, False, True),
    "gpt-5.2":             (2, True,  True,  True,  True,  False, False, True),
    "gpt-5.4":             (4, True,  True,  True,  True,  True,  False, True),
    "gpt-5.5":             (5, True,  True,  True,  True,  True,  False, True),
    "gpt-5.5-pro":         (5, True,  True,  True,  True,  True,  False, True),
    "gpt-5.2-mini":        (2, True,  True,  True,  False, False, False, True),
    "gpt-5.1-nano":        (1, True,  True,  True,  False, False, False, True),
    "gpt-5.1-chat-latest": (1, False, False, False, False, False, True,  True),
    "gpt-5-chat":          (0, False, False, False, False, False, True,  True),
    "o1":                  (0, False, True,  False, False, False, False, False),
    "o3":                  (0, False, True,  False, False, False, False, False),
    "o4-mini":             (0, False, True,  False, False, False, False, False),
    "gpt-4o":              (0, False, False, False, False, False, False, False),
    "gpt-4.1":             (0, False, False, False, False, False, False, False),
}


class TestOpenAIDetect(unittest.TestCase):
    def setUp(self):
        self.p = stub(OpenAIMixin, provider="OpenAI", model="gpt-5.2")

    def test_matrix(self):
        for model, exp in EXPECTED.items():
            minor, family, reasoning, none, xhigh, temp_none, chat, verb = exp
            with self.subTest(model=model):
                self.assertEqual(self.p._parse_gpt5_minor(model), minor)
                self.assertEqual(self.p._is_gpt5_family(model), family)
                self.assertEqual(self.p._is_openai_reasoning_model(model), reasoning)
                self.assertEqual(self.p._has_reasoning_none(model), none)
                self.assertEqual(self.p._has_reasoning_xhigh(model), xhigh)
                self.assertEqual(self.p._gpt5_supports_temp_at_none(model), temp_none)
                self.assertEqual(self.p._is_gpt5_chat_model(model), chat)
                self.assertEqual(self.p._has_openai_verbosity(model), verb)


if __name__ == "__main__":
    unittest.main()
