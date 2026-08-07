"""_debug_render — the Debug payload dump's JSON-shaped renderer that shows
multi-line strings (above all the system prompt with its ## Skill blocks) as
indented triple-quoted blocks with real line breaks, instead of one endless
\\n-escaped JSON string line. SelfBot.py keeps an in-file copy (it can't be
imported under test), so these tests are the contract both apps implement."""
import json
import unittest

from myagent.streaming_mixin import StreamingMixin

R = StreamingMixin._debug_render


class TestDebugRender(unittest.TestCase):

    def test_multiline_string_becomes_real_block(self):
        out = R({"system": "You are an agent.\n\n## Skill: Test-skill\nDoes nothing!"})
        self.assertIn('"system": """', out)
        # the skill block sits on its own real line, no \n escapes anywhere
        self.assertTrue(any("## Skill: Test-skill" in ln for ln in out.splitlines()))
        self.assertNotIn("\\n", out)

    def test_single_line_strings_and_scalars_stay_json(self):
        out = R({"model": "kimi-k3", "stream": True, "n": 3, "none": None})
        self.assertIn('"model": "kimi-k3"', out)
        self.assertIn('"stream": true', out)
        self.assertIn('"n": 3', out)
        self.assertIn('"none": null', out)

    def test_nested_messages_render(self):
        out = R({"messages": [{"role": "user",
                               "content": [{"type": "text", "text": "line1\nline2"}]}]})
        self.assertIn('"role": "user"', out)
        self.assertIn('"""', out)
        self.assertIn("line1", out)

    def test_empty_containers(self):
        self.assertEqual(R({}), "{}")
        self.assertEqual(R([]), "[]")

    def test_unicode_stays_readable(self):
        out = R({"s": "• bullet — dash"})
        self.assertIn("• bullet — dash", out)
        self.assertNotIn("\\u2022", out)

    def test_stray_object_falls_back_to_repr_not_crash(self):
        class Stray:
            pass
        out = R({"x": Stray()})
        self.assertIn("Stray object", out)

    def test_single_line_output_still_parses_as_json(self):
        # payloads without any multi-line string remain valid JSON
        payload = {"model": "m", "tools": [{"name": "t", "n": 1}], "on": False}
        self.assertEqual(json.loads(R(payload)), payload)


if __name__ == "__main__":
    unittest.main()
