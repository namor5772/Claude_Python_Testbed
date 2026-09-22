"""Characterization tests: Gemini function-call argument normalisation and
the tool-crash guard.

Live 2026-09-22, gemini-3.8-flash, the Process_Dockets instruction: 47 calls
in, the model asked for type_text(text="12345") — a docket number, sent as
the string the schema declares. `_normalize_gemini_args` (written for the
protobuf float64 / string-encoded-coordinate problem) parsed EVERY
numeric-looking string into a number, so `text` arrived as the int 12345,
`len(text)` in the dispatch's label preview raised, and the run ended with
"Error: object of type 'int' has no len()". Two changes, both pinned here:

* the normaliser now reads the tool's declared parameter types — a declared
  string is never parsed, a number sent for a declared string is
  stringified (integral floats as integers: protobuf's 12345.0 → "12345"),
  declared numerics and undeclared parameters keep the old heuristic;
* `_run_tool` wraps `_execute_tool` so an exception from a dispatch line
  becomes an error tool_result the model can recover from, not the end of
  the run.
"""
import queue
import unittest

from tests._util import stub
from myagent.constants import TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS, META_TOOLS
from myagent.gemini_mixin import GeminiMixin
from myagent.streaming_mixin import StreamingMixin

CATALOG = TOOLS + DESKTOP_TOOLS + BROWSER_TOOLS + META_TOOLS


def props(tool_name):
    tool = next(t for t in CATALOG if t["name"] == tool_name)
    return tool["input_schema"]["properties"]


normalize = GeminiMixin._normalize_gemini_args


class DeclaredStringTests(unittest.TestCase):
    def test_numeric_string_for_declared_string_stays_a_string(self):
        # The live failure: a docket number typed into a form
        out = normalize({"text": "12345"}, props("type_text"))
        self.assertEqual(out["text"], "12345")
        self.assertIsInstance(out["text"], str)

    def test_decimal_and_negative_strings_stay_strings(self):
        p = props("type_text")
        self.assertEqual(normalize({"text": "3.14"}, p)["text"], "3.14")
        self.assertEqual(normalize({"text": "-7"}, p)["text"], "-7")
        self.assertEqual(normalize({"text": "007"}, p)["text"], "007")

    def test_number_sent_for_declared_string_is_stringified(self):
        p = props("type_text")
        self.assertEqual(normalize({"text": 12345}, p)["text"], "12345")
        # protobuf Struct hands numbers back as float64
        self.assertEqual(normalize({"text": 12345.0}, p)["text"], "12345")
        self.assertEqual(normalize({"text": 2.5}, p)["text"], "2.5")

    def test_bool_for_declared_string_passes_through(self):
        # bool is an int subclass; it must not become "1"
        self.assertIs(normalize({"text": True}, props("type_text"))["text"], True)

    def test_union_type_with_null_counts_as_string(self):
        p = {"note": {"type": ["string", "null"]}}
        self.assertEqual(normalize({"note": "42"}, p)["note"], "42")
        self.assertEqual(normalize({"note": 42}, p)["note"], "42")


class DeclaredNumericTests(unittest.TestCase):
    def test_string_encoded_coordinate_for_declared_integer_is_parsed(self):
        # The original reason the normaliser exists
        p = props("mouse_click")
        out = normalize({"x": "500.0", "y": "250"}, p)
        self.assertEqual((out["x"], out["y"]), (500, 250))
        self.assertIsInstance(out["x"], int)

    def test_declared_number_keeps_fraction(self):
        out = normalize({"interval": "0.05"}, props("type_text"))
        self.assertEqual(out["interval"], 0.05)

    def test_non_numeric_string_for_declared_integer_is_left_alone(self):
        out = normalize({"x": "abc"}, props("mouse_click"))
        self.assertEqual(out["x"], "abc")


class UndeclaredTests(unittest.TestCase):
    def test_undeclared_parameter_keeps_the_legacy_heuristic(self):
        out = normalize({"mystery": "500.0", "word": "left"}, props("type_text"))
        self.assertEqual(out["mystery"], 500)
        self.assertEqual(out["word"], "left")

    def test_no_properties_at_all_is_the_legacy_behaviour(self):
        out = normalize({"a": "1", "b": "x", "c": 2.0})
        self.assertEqual(out, {"a": 1, "b": "x", "c": 2.0})
        out = normalize({"a": "1"}, None)
        self.assertEqual(out, {"a": 1})

    def test_whole_catalog_string_params_survive_numeric_strings(self):
        # Every declared-string parameter in the native catalog keeps a
        # numeric-looking value verbatim
        for tool in CATALOG:
            for name, schema in tool["input_schema"].get("properties", {}).items():
                if schema.get("type") == "string":
                    out = normalize({name: "2026"}, tool["input_schema"]["properties"])
                    self.assertEqual(out[name], "2026", f"{tool['name']}.{name}")


class _Block:
    def __init__(self, name, input):
        self.name = name
        self.input = input
        self.id = "toolu_1"


class RunToolGuardTests(unittest.TestCase):
    def make_host(self, execute):
        host = stub(StreamingMixin, queue=queue.Queue())
        host._execute_tool = execute
        return host

    def test_result_passes_through_unchanged(self):
        host = self.make_host(lambda block: "ok")
        self.assertEqual(host._run_tool(_Block("type_text", {})), "ok")
        self.assertTrue(host.queue.empty())

    def test_exception_becomes_an_error_result_and_a_warning(self):
        def boom(block):
            len(12345)
        host = self.make_host(boom)
        result = host._run_tool(_Block("type_text", {"text": 12345}))
        self.assertIn("Error executing type_text", result)
        self.assertIn("TypeError", result)
        self.assertIn("has no len()", result)
        self.assertIn("retry", result)
        msg = host.queue.get_nowait()
        self.assertEqual(msg["type"], "warning")
        self.assertIn("type_text raised TypeError", msg["content"])


if __name__ == "__main__":
    unittest.main()
