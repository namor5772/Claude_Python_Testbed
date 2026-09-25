"""manage_skills' bundled-resource-file actions (2026-09-25): list_files /
read_file / write_file / delete_file, so a model with Meta access can author
a COMPLETE Agent-Skills-style skill (SKILL.md + references/ + scripts/ …)
through the one tool, without the native file tools.

Layers pinned here:

- `datapaths.skill_resource_path` — the validation gate every path-taking
  action goes through: RELATIVE only, inside the skill's folder (no '..',
  no absolute path or drive letter), never the SKILL.md family;
- `datapaths.list_skill_resources` — the lister, sharing ONE resource
  definition (`_iter_skill_resources`) with the save-as copy;
- the `do_manage_skills` action branches, round-tripped over a temp tree;
- SelfBot parity: with the myagent package present its helpers ARE the
  datapaths functions, and both apps' manage_skills schemas offer the same
  actions.
"""

import os
import unittest
from unittest import mock

import SelfBot
from tests._util import stub
from tests.test_skills_tree import SkillsTreeCase
from myagent import datapaths as dp
from myagent import skills_mixin
from myagent.constants import META_TOOLS
from myagent.skills_mixin import SkillsMixin


class ResourcePathValidation(SkillsTreeCase):
    def setUp(self):
        super().setUp()
        self.write_md("alpha", "---\nname: alpha\nmode: disabled\n---\n\nbody\n")

    def path(self, rel):
        return dp.skill_resource_path(str(self.tree), "alpha", rel)

    def test_valid_paths_resolve_inside_the_folder(self):
        for rel, parts in (("notes.txt", ("notes.txt",)),
                           ("references/palette.md", ("references", "palette.md")),
                           ("scripts\\run.py", ("scripts", "run.py")),   # backslashes ok
                           ("./refs/a.txt", ("refs", "a.txt"))):
            with self.subTest(rel=rel):
                path, err = self.path(rel)
                self.assertIsNone(err)
                self.assertEqual(path, os.path.join(str(self.tree), "alpha", *parts))

    def test_escapes_and_non_files_are_refused(self):
        for rel in ("../other/x.txt", "refs/../../x.txt", "..", "/abs/x.txt",
                    "C:/evil.txt", "C:\\evil.txt", "", "   ", "refs/", "."):
            with self.subTest(rel=rel):
                path, err = self.path(rel)
                self.assertIsNone(path)
                self.assertIsNotNone(err)

    def test_the_skill_md_family_is_refused_at_the_root_only(self):
        for rel in ("SKILL.md", "SKILL-Laptop.md", "SKILL.md.abc.tmp"):
            with self.subTest(rel=rel):
                self.assertIsNone(self.path(rel)[0])
        # ...but the same names INSIDE a subdirectory are ordinary resources
        path, err = self.path("references/SKILL.md")
        self.assertIsNone(err)
        self.assertTrue(path.endswith(os.path.join("references", "SKILL.md")))

    def test_missing_skill_folder_names_the_problem(self):
        path, err = dp.skill_resource_path(str(self.tree), "ghost", "a.txt")
        self.assertIsNone(path)
        self.assertIn("no folder on disk", err)


class ListResources(SkillsTreeCase):
    def test_lists_nested_sorted_and_skips_the_family(self):
        self.write_md("alpha", "---\nname: alpha\nmode: disabled\n---\n\nbody\n")
        self.write_md("alpha", "fork", basename="SKILL-Laptop.md")
        for rel in ("zz.txt", "references/b.md", "references/a.md"):
            p = self.tree / "alpha" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x", encoding="utf-8")
        self.assertEqual(dp.list_skill_resources(str(self.tree), "alpha"),
                         ["zz.txt",
                          os.path.join("references", "a.md"),
                          os.path.join("references", "b.md")])

    def test_missing_folder_is_none_and_empty_is_empty(self):
        self.assertIsNone(dp.list_skill_resources(str(self.tree), "ghost"))
        self.write_md("alpha", "---\nname: alpha\nmode: disabled\n---\n\nbody\n")
        self.assertEqual(dp.list_skill_resources(str(self.tree), "alpha"), [])


class ToolActions(SkillsTreeCase):
    """The do_manage_skills branches over a real temp tree."""

    def setUp(self):
        super().setUp()
        self.write_md("alpha", "---\nname: alpha\nmode: disabled\n---\n\nbody\n")
        self.host = stub(SkillsMixin, skills={"alpha": {"content": "body",
                                                        "mode": "disabled"}})
        patcher = mock.patch.object(skills_mixin, "SKILLS_DIR", str(self.tree))
        patcher.start()
        self.addCleanup(patcher.stop)

    def act(self, **params):
        return self.host.do_manage_skills(params)

    def test_write_list_read_delete_round_trip(self):
        out = self.act(action="write_file", name="alpha",
                       file_path="references/palette.md", file_content="colors")
        self.assertIn("Wrote 'references/palette.md'", out)
        self.assertIn("(6 bytes)", out)
        listing = self.act(action="list_files", name="alpha")
        self.assertIn("references/palette.md  (6 bytes)", listing)
        self.assertEqual(self.act(action="read_file", name="alpha",
                                  file_path="references/palette.md"), "colors")
        out = self.act(action="write_file", name="alpha",
                       file_path="references/palette.md", file_content="colors v2")
        self.assertIn("Replaced", out)
        out = self.act(action="delete_file", name="alpha",
                       file_path="references/palette.md")
        self.assertIn("Deleted", out)
        # The emptied subdirectory is pruned; the skill folder survives.
        self.assertFalse((self.tree / "alpha" / "references").exists())
        self.assertTrue((self.tree / "alpha" / dp.SKILL_BASENAME).is_file())
        self.assertIn("no bundled resource files",
                      self.act(action="list_files", name="alpha"))

    def test_traversal_and_skill_md_are_refused(self):
        for rel in ("../evil.txt", "SKILL.md", "C:\\evil.txt"):
            with self.subTest(rel=rel):
                out = self.act(action="write_file", name="alpha",
                               file_path=rel, file_content="x")
                self.assertTrue(out.startswith("Error:"), out)
        self.assertFalse((self.tree / "evil.txt").exists())

    def test_guards(self):
        self.assertIn("not found", self.act(action="list_files", name="ghost"))
        self.assertIn("'file_content' is required",
                      self.act(action="write_file", name="alpha", file_path="a.txt"))
        self.assertIn("does not exist",
                      self.act(action="read_file", name="alpha", file_path="a.txt"))
        self.assertIn("does not exist",
                      self.act(action="delete_file", name="alpha", file_path="a.txt"))

    def test_binary_read_reports_instead_of_dumping(self):
        (self.tree / "alpha" / "blob.bin").write_bytes(b"\x00\x01\x02")
        out = self.act(action="read_file", name="alpha", file_path="blob.bin")
        self.assertIn("binary file", out)


class SelfBotParity(unittest.TestCase):
    def test_selfbot_uses_the_datapaths_helpers_when_myagent_is_present(self):
        self.assertIs(SelfBot._list_skill_resources, dp.list_skill_resources)
        self.assertIs(SelfBot._skill_resource_path, dp.skill_resource_path)
        self.assertIs(SelfBot._skill_dir_for, dp.skill_dir_for)

    def test_both_schemas_offer_the_same_actions(self):
        def actions(tool_list):
            schema = next(t for t in tool_list if t["name"] == "manage_skills")
            return schema["input_schema"]["properties"]["action"]["enum"]
        self.assertEqual(actions(META_TOOLS), actions(SelfBot.SELFBOT_META_TOOLS))
        self.assertIn("write_file", actions(META_TOOLS))


if __name__ == "__main__":
    unittest.main()
