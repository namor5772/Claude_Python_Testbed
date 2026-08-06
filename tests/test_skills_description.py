"""Skill `description` field — the Agent-Skills-style what+when routing signal.

Covers the pure listing builder feeding _build_system_prompt and
do_manage_skills' handling of the new optional field, on a bare SkillsMixin
host (no Tk, no disk — _save_skills/_post_skill_ui_refresh are stubbed).
SelfBot.py keeps in-file copies of _format_on_demand_listing /
_desc_length_warning (it can't be imported under test: module-level Tk/DPI
code), so these tests are the contract both apps implement.
"""
import unittest

from myagent.skills_mixin import SkillsMixin
from tests._util import stub


def host(skills):
    h = stub(SkillsMixin, skills=skills, system_prompt="BASE")
    h.saves = []
    h._save_skills = lambda: h.saves.append(True)
    h._post_skill_ui_refresh = lambda: None
    return h


class TestOnDemandListing(unittest.TestCase):

    def test_empty_when_nothing_on_demand(self):
        self.assertEqual(SkillsMixin._format_on_demand_listing({}), "")
        self.assertEqual(SkillsMixin._format_on_demand_listing(
            {"A": {"content": "x", "mode": "enabled"},
             "B": {"content": "y", "mode": "disabled"}}), "")

    def test_described_and_bare_names_mix(self):
        skills = {
            "PDF Skill": {"content": "c", "mode": "on_demand",
                          "description": "Extract PDFs. Use for PDF tasks."},
            "Bare": {"content": "c", "mode": "on_demand"},
            "Hidden": {"content": "c", "mode": "enabled"},
        }
        block = SkillsMixin._format_on_demand_listing(skills)
        self.assertIn("## On-Demand Skills", block)
        self.assertIn("- PDF Skill — Extract PDFs. Use for PDF tasks.", block)
        self.assertIn("- Bare", block)
        self.assertNotIn("Hidden", block)
        self.assertIn("`get_skill`", block)

    def test_whitespace_description_treated_as_absent(self):
        skills = {"S": {"content": "c", "mode": "on_demand", "description": "   "}}
        block = SkillsMixin._format_on_demand_listing(skills)
        self.assertIn("- S", block)
        self.assertNotIn("- S —", block)

    def test_build_system_prompt_composition(self):
        h = host({
            "On": {"content": "ENABLED-BODY", "mode": "enabled"},
            "OD": {"content": "od-body", "mode": "on_demand",
                   "description": "Do X. Use when Y."},
        })
        sp = h._build_system_prompt()
        self.assertTrue(sp.startswith("BASE"))
        self.assertIn("## Skill: On\nENABLED-BODY", sp)
        self.assertIn("- OD — Do X. Use when Y.", sp)
        self.assertNotIn("od-body", sp)  # on_demand content is NOT injected

    def test_build_system_prompt_no_od_block_when_none(self):
        h = host({"On": {"content": "E", "mode": "enabled"}})
        self.assertNotIn("## On-Demand Skills", h._build_system_prompt())


class TestManageSkillsDescription(unittest.TestCase):

    def test_create_stores_stripped_and_read_returns(self):
        h = host({})
        out = h.do_manage_skills({"action": "create", "name": "S", "content": "c",
                                  "mode": "on_demand", "description": " What. When. "})
        self.assertIn("created successfully", out)
        self.assertEqual(h.skills["S"]["description"], "What. When.")
        read = h.do_manage_skills({"action": "read", "name": "S"})
        self.assertIn('"description": "What. When."', read)

    def test_create_without_description_omits_key(self):
        h = host({})
        h.do_manage_skills({"action": "create", "name": "S", "content": "c"})
        self.assertNotIn("description", h.skills["S"])

    def test_update_description_alone(self):
        h = host({"S": {"content": "c", "mode": "disabled"}})
        out = h.do_manage_skills({"action": "update", "name": "S", "description": "D"})
        self.assertIn("updated successfully", out)
        self.assertEqual(h.skills["S"]["description"], "D")
        self.assertEqual(h.skills["S"]["content"], "c")

    def test_update_empty_string_clears(self):
        h = host({"S": {"content": "c", "mode": "disabled", "description": "D"}})
        h.do_manage_skills({"action": "update", "name": "S", "description": ""})
        self.assertNotIn("description", h.skills["S"])

    def test_update_content_preserves_description(self):
        h = host({"S": {"content": "c", "mode": "disabled", "description": "D"}})
        h.do_manage_skills({"action": "update", "name": "S", "content": "c2"})
        self.assertEqual(h.skills["S"]["description"], "D")
        self.assertEqual(h.skills["S"]["content"], "c2")

    def test_update_nothing_provided_errors(self):
        h = host({"S": {"content": "c", "mode": "disabled"}})
        out = h.do_manage_skills({"action": "update", "name": "S"})
        self.assertIn("Error", out)
        self.assertIn("description", out)

    def test_long_description_warns_but_saves(self):
        h = host({})
        long_desc = "x" * 1500
        out = h.do_manage_skills({"action": "create", "name": "S", "content": "c",
                                  "description": long_desc})
        self.assertIn("created successfully", out)
        self.assertIn("Warning", out)
        self.assertIn("1500", out)
        self.assertEqual(h.skills["S"]["description"], long_desc)

    def test_list_prefers_description_over_preview(self):
        h = host({"A": {"content": "body-A", "mode": "on_demand", "description": "Desc-A"},
                  "B": {"content": "body-B", "mode": "enabled"}})
        out = h.do_manage_skills({"action": "list"})
        self.assertIn("Desc-A", out)
        self.assertNotIn("body-A", out)
        self.assertIn("body-B", out)  # preview fallback for undescribed skills


class TestNameConventionWarning(unittest.TestCase):
    """Agent-Skills kebab-case naming — soft warning, never a rejection."""

    def test_conforming_names_pass_silently(self):
        for name in ("westpac-login", "nip-generation", "a", "x1", "a-2-b",
                     "reliable-youtube-music-playback"):
            self.assertEqual(SkillsMixin._name_convention_warning(name), "", name)

    def test_nonconforming_names_warn(self):
        for name in ("Westpac Login", "S", "has space", "-leading", "trailing-",
                     "double--hyphen", "UPPER", "under_score", "a" * 65):
            self.assertIn("naming convention",
                          SkillsMixin._name_convention_warning(name), name)

    def test_create_warns_but_still_saves(self):
        h = host({})
        out = h.do_manage_skills({"action": "create", "name": "Title Case",
                                  "content": "c"})
        self.assertIn("created successfully", out)
        self.assertIn("naming convention", out)
        self.assertIn("Title Case", h.skills)

    def test_create_conforming_name_no_warning(self):
        h = host({})
        out = h.do_manage_skills({"action": "create", "name": "tidy-skill",
                                  "content": "c"})
        self.assertIn("created successfully", out)
        self.assertNotIn("Warning", out)


if __name__ == "__main__":
    unittest.main()
