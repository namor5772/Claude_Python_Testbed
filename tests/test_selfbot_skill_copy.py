"""SelfBot's Skills Manager save-as resource copy (2026-09-25) — the mirror
of MyAgent's fix of the same day (tests/test_skills_tree.py
ResourceCopyTests; CLAUDE_MYAGENT.md's Skills tree paragraph).

The dialog closures need Tk and are verified live (real listbox select →
rename → SAVE over a temp tree, both apps, 9/9 each); what this module pins
is the plumbing that a refactor could silently drop:

- with the myagent package present, SelfBot's `_copy_skill_resources` IS
  `myagent.datapaths.copy_skill_resources` (the try-import), so the two
  Skills Managers share one implementation over the shared tree;
- the standalone stub exists in the except block (a myagent-less SelfBot
  keeps the behaviour — exercised live with the import blocked);
- the save_skill wiring lines are present and BYTE-IDENTICAL between
  SelfBot.py and myagent/skills_mixin.py, the drift pin the other in-file
  copies get.
"""

import unittest
from pathlib import Path

import SelfBot
from myagent import datapaths as dp

_REPO = Path(__file__).resolve().parent.parent


class SharedImplementation(unittest.TestCase):
    def test_selfbot_uses_the_datapaths_copy_when_myagent_is_present(self):
        self.assertIs(SelfBot._copy_skill_resources, dp.copy_skill_resources)


class WiringScan(unittest.TestCase):
    WIRING_LINES = (
        'loaded = {"name": None}',
        'is_new = name not in self.skills',
        'if is_new and src and src != name and src in self.skills:',
        'loaded["name"] = name',
        'loaded["name"] = None   # a fresh skill has no save-as source',
    )

    def setUp(self):
        self.selfbot = (_REPO / "SelfBot.py").read_text(encoding="utf-8")
        self.mixin = (_REPO / "myagent" / "skills_mixin.py").read_text(encoding="utf-8")

    def test_save_as_wiring_present_in_both_apps(self):
        for line in self.WIRING_LINES:
            with self.subTest(line=line):
                self.assertIn(line, self.selfbot)
                self.assertIn(line, self.mixin)

    def test_each_app_calls_its_copy_with_the_same_arguments(self):
        self.assertIn("_copy_skill_resources(SKILLS_DIR, src, name)", self.selfbot)
        self.assertIn("copy_skill_resources(SKILLS_DIR, src, name)", self.mixin)

    def test_standalone_stub_exists_in_the_except_block(self):
        self.assertIn("def _copy_skill_resources(dirpath, src_name, dst_name):",
                      self.selfbot)


if __name__ == "__main__":
    unittest.main()
