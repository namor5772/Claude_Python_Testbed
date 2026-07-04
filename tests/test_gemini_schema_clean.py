"""Characterization test: _clean_schema_for_gemini (GeminiMixin).

Locks the is_property_map guard — a property NAMED like a JSON-Schema
metadata keyword (find_window/wait_for_window's required "title", an MCP
tool's "default") must survive the cleanup, otherwise Gemini rejects the
whole request with 400 "required[0]: property is not defined" (hit live
2026-07 on gemini-3.5-flash with desktop tools). Also locks the original
behaviors: schema-level metadata drops, string-enum normalization, and
blank/empty-enum removal."""
import unittest

from tests._util import stub
from myagent.gemini_mixin import GeminiMixin


def clean(schema):
    stub(GeminiMixin)._clean_schema_for_gemini(schema)
    return schema


class TestSchemaClean(unittest.TestCase):
    def test_property_named_title_survives(self):
        # find_window's real shape — the July 2026 Gemini 400 regression
        schema = {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Window title"},
                "activate": {"type": "boolean"},
            },
            "required": ["title"],
        }
        clean(schema)
        self.assertIn("title", schema["properties"])
        self.assertEqual(schema["required"], ["title"])

    def test_property_named_default_survives_but_keyword_dropped(self):
        schema = {
            "type": "object",
            "properties": {
                "default": {"type": "string", "default": "x", "title": "meta"},
            },
            "required": ["default"],
        }
        clean(schema)
        # property NAME kept...
        self.assertIn("default", schema["properties"])
        # ...but metadata KEYWORDS inside its sub-schema still dropped
        self.assertNotIn("default", schema["properties"]["default"])
        self.assertNotIn("title", schema["properties"]["default"])

    def test_nested_object_properties_protected(self):
        schema = {
            "type": "object",
            "properties": {
                "config": {
                    "type": "object",
                    "properties": {"title": {"type": "string"}},
                    "required": ["title"],
                },
            },
        }
        clean(schema)
        self.assertIn("title", schema["properties"]["config"]["properties"])

    def test_schema_level_drops_and_enum_rules_unchanged(self):
        schema = {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "mode": {"type": "string", "enum": ["a", "", "b"]},
                "count": {"type": "integer", "enum": [1, 2]},
                "empty": {"type": "string", "enum": [""]},
            },
        }
        clean(schema)
        self.assertNotIn("$schema", schema)
        self.assertNotIn("additionalProperties", schema)
        self.assertEqual(schema["properties"]["mode"]["enum"], ["a", "b"])
        self.assertNotIn("enum", schema["properties"]["count"])
        self.assertNotIn("enum", schema["properties"]["empty"])


if __name__ == "__main__":
    unittest.main()
