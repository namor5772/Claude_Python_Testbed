"""Regression tests for the 2026-09-28 review fixes in the authored stores.

- an instruction store that exists but cannot be read is served as a
  session-only Default that NO save may write over the real file (every
  caller used to save the stand-in, replacing the whole library);
- a SKILL.md that is not UTF-8 no longer crashes the tree load (and MyAgent's
  and SelfBot's startup with it): UTF-16 and cp1252 files are read, anything
  else is skipped like an unreadable file;
- the skills-tree healer never loses a preserved conflict variant, gives a
  duplicate folder a name of its own, and keeps a copy whose bundled files
  differ;
- the SKILL.md-family guard is case-insensitive and a drive colon anywhere in
  a resource path is refused; a failed resource write leaves no temp file;
- manage_instructions carries the IMAP (`proton`) toggle, names every field
  its update accepts, and stores thinking_mode lower-cased;
- the editor's SAVE asks before replacing a DIFFERENT existing instruction.
"""

import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from myagent import datapaths as dp
from myagent import instructions_mixin, skills_mixin
from myagent.instructions_mixin import InstructionsMixin, StoreUnreadableError
from myagent.model_upgrade_mixin import ModelUpgradeMixin
from myagent.skills_mixin import SkillsMixin
from tests._util import stub
from tests.test_skills_tree import SkillsTreeCase


class _StoreHost(InstructionsMixin, ModelUpgradeMixin):
    pass


class UnreadableStoreTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        # A distinctive basename: load_store's one-shot repo-root migration
        # looks for a repo copy of the SAME name, and there is none of this.
        self.path = os.path.join(self.dir, "review_test_store.json")
        for patcher in (mock.patch.object(instructions_mixin, "INSTRUCTIONS_FILE", self.path),
                        mock.patch.object(instructions_mixin.messagebox, "showerror")):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.host = stub(_StoreHost, provider="Anthropic", model="claude-sonnet-5",
                         temperature=1.0, thinking_enabled=True, thinking_effort="low",
                         thinking_budget=8192, thinking_mode="low", text_verbosity="medium",
                         fast_mode=False, skills={}, _disabled_confirm_patterns=set(),
                         _blocked_tools=set(), agent_instruction_name="",
                         upgrade_target=None, _upgrade_original=None)

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def read(self):
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def test_no_save_replaces_an_unparseable_store(self):
        broken = '{"Balance Westpac": {"text": "a"}, "Pay bills": {"text": "b"},}'
        self.write(broken)                       # a hand edit's trailing comma
        for params in ({"action": "create", "name": "New", "text": "t"},
                       {"action": "delete", "name": "Default"}):
            with self.subTest(action=params["action"]):
                with self.assertRaises(StoreUnreadableError):
                    self.host.do_manage_instructions(dict(params))
                self.assertEqual(self.read(), broken)

    def test_a_non_utf8_store_is_unreadable_too(self):
        with open(self.path, "wb") as f:
            f.write(b'{"Caf\xe9": {"text": "a"}}')      # cp1252 bytes
        with self.assertRaises(StoreUnreadableError):
            self.host.do_manage_instructions({"action": "create", "name": "N", "text": "t"})

    def test_a_valid_empty_store_is_still_saved(self):
        self.write("{}")                         # emptied on purpose, not unreadable
        self.host.do_manage_instructions({"action": "create", "name": "New", "text": "t"})
        self.assertIn("New", json.loads(self.read()))


class ReadTextTests(SkillsTreeCase):

    def raw(self, sub, data):
        d = self.tree / sub
        d.mkdir(parents=True, exist_ok=True)
        (d / dp.SKILL_BASENAME).write_bytes(data)

    def test_cp1252_and_utf16_skill_files_load(self):
        self.raw("ansi", b"---\r\nname: ansi\r\nmode: enabled\r\n---\r\n\r\nStep 1 \x96 go\r\n")
        self.raw("wide", "---\nname: wide\nmode: enabled\n---\n\nwide – text\n"
                 .encode("utf-16"))
        skills = dp._scan_skills_tree(str(self.tree))
        self.assertEqual(skills["ansi"]["content"], "Step 1 – go")   # newlines translated
        self.assertEqual(skills["wide"]["content"], "wide – text")

    def test_an_undecodable_file_is_skipped_not_fatal(self):
        self.raw("junk", b"---\nname: junk\n---\n\n\x81\x8d\x8f\n")    # not cp1252 either
        self.write_md("good", "---\nname: good\nmode: enabled\n---\n\nfine\n")
        skills = dp._scan_skills_tree(str(self.tree))                  # used to raise
        self.assertIn("good", skills)
        self.assertNotIn("junk", skills)


class TreeHealingTests(SkillsTreeCase):

    MAIN = "---\nname: foo\nmode: enabled\n---\n\nmain body\n"

    def test_a_second_fork_with_the_same_label_never_replaces_the_first_variant(self):
        self.write_md("foo", self.MAIN)
        self.write_md("foo__LAPTOP", "---\nname: foo__LAPTOP\nmode: enabled\n---\n\nA\n")
        self.write_md("foo", "---\nname: foo\nmode: enabled\n---\n\nB\n",
                      basename="SKILL-LAPTOP.md")
        skills = dp._scan_skills_tree(str(self.tree))
        self.assertEqual(skills["foo__LAPTOP"]["content"], "A")        # preserved
        self.assertEqual(skills["foo__LAPTOP_2"]["content"], "B")      # the new one
        self.assertFalse((self.tree / "foo" / "SKILL-LAPTOP.md").exists())
        again = dp._scan_skills_tree(str(self.tree))                   # stable on disk
        self.assertEqual((again["foo__LAPTOP"]["content"], again["foo__LAPTOP_2"]["content"]),
                         ("A", "B"))

    def test_a_duplicate_folder_gets_a_name_of_its_own(self):
        self.write_md("foo", self.MAIN)
        self.write_md("foo - Copy", "---\nname: foo\nmode: enabled\n---\n\nhand copy\n")
        (self.tree / "foo - Copy" / "b.ps1").write_text("echo b", encoding="utf-8")
        skills = dp._scan_skills_tree(str(self.tree))
        self.assertEqual(skills["foo__foo - Copy"]["content"], "hand copy")
        folder = dp.skill_dir_for(str(self.tree), "foo__foo - Copy")
        self.assertEqual(os.path.basename(folder), "foo - Copy")       # the folder IS it
        dp.delete_skill_tree_entry(str(self.tree), "foo")              # removes ONE folder
        self.assertTrue((self.tree / "foo - Copy" / "b.ps1").exists())
        self.assertFalse((self.tree / "foo").exists())

    def test_an_identical_copy_with_edited_files_is_kept(self):
        self.write_md("bar", "---\nname: bar\nmode: enabled\n---\n\nsame\n")
        self.write_md("bar - Copy", "---\nname: bar\nmode: enabled\n---\n\nsame\n")
        (self.tree / "bar" / "notes.txt").write_text("v1", encoding="utf-8")
        (self.tree / "bar - Copy" / "notes.txt").write_text("v2 edited", encoding="utf-8")
        dp._scan_skills_tree(str(self.tree))
        self.assertEqual((self.tree / "bar - Copy" / "notes.txt").read_text(encoding="utf-8"),
                         "v2 edited")

    def test_an_identical_copy_with_nothing_else_is_still_junk(self):
        self.write_md("baz", "---\nname: baz\nmode: enabled\n---\n\nsame\n")
        self.write_md("baz_2", "---\nname: baz\nmode: enabled\n---\n\nsame\n")
        skills = dp._scan_skills_tree(str(self.tree))
        self.assertEqual(list(skills), ["baz"])
        self.assertFalse((self.tree / "baz_2").exists())


class ResourceGuardTests(SkillsTreeCase):

    def setUp(self):
        super().setUp()
        self.write_md("qux", "---\nname: qux\nmode: enabled\n---\n\nbody\n")
        self.host = stub(SkillsMixin, skills={"qux": {"content": "body", "mode": "enabled"}})
        patcher = mock.patch.object(skills_mixin, "SKILLS_DIR", str(self.tree))
        patcher.start()
        self.addCleanup(patcher.stop)

    def path(self, rel):
        return dp.skill_resource_path(str(self.tree), "qux", rel)

    def test_the_skill_md_family_is_refused_in_any_case(self):
        for rel in ("skill.md", "Skill.MD", "skill-notes.md", "skill.md.bak.tmp"):
            with self.subTest(rel=rel):
                self.assertIsNone(self.path(rel)[0])

    def test_a_drive_colon_anywhere_is_refused(self):
        for rel in ("sub/Z:evil.txt", "a/b/C:x", "notes:stream.txt"):
            with self.subTest(rel=rel):
                self.assertIsNone(self.path(rel)[0])

    def test_a_failed_write_leaves_no_temp_file(self):
        (self.tree / "qux" / "references").mkdir()
        out = self.host.do_manage_skills({"action": "write_file", "name": "qux",
                                          "file_path": "references", "file_content": "x"})
        self.assertIn("folder", out)
        out = self.host.do_manage_skills({"action": "write_file", "name": "qux",
                                          "file_path": "bad.txt",
                                          "file_content": "split emoji \ud83d"})
        self.assertIn("Error", out)
        out = self.host.do_manage_skills({"action": "write_file", "name": "qux",
                                          "file_path": "obj.json", "file_content": {"a": 1}})
        self.assertIn("must be a string", out)
        leftovers = [n for _r, _d, files in os.walk(self.tree / "qux")
                     for n in files if n.endswith(".tmp")]
        self.assertEqual(leftovers, [])


class ManageInstructionsFieldTests(unittest.TestCase):

    def tool_host(self):
        h = stub(_StoreHost, provider="Anthropic", model="claude-sonnet-5", temperature=1.0,
                 thinking_enabled=True, thinking_effort="low", thinking_budget=8192,
                 thinking_mode="low", text_verbosity="medium", fast_mode=False, skills={},
                 _disabled_confirm_patterns=set(), _blocked_tools=set(),
                 agent_instruction_name="", upgrade_target=None, _upgrade_original=None)
        h.store = {}
        h._load_saved_instructions = lambda: h.store
        h._save_instructions_to_disk = lambda data: setattr(h, "store", data)
        return h

    def test_the_imap_toggle_round_trips(self):
        h = self.tool_host()
        h.do_manage_instructions({"action": "create", "name": "Mail", "text": "t",
                                  "proton": True})
        self.assertIs(h.store["Mail"]["proton"], True)
        self.assertIs(json.loads(h.do_manage_instructions(
            {"action": "read", "name": "Mail"}))["proton"], True)
        self.assertIn("proton", h.do_manage_instructions({"action": "list"}))
        h.do_manage_instructions({"action": "update", "name": "Mail", "proton": False})
        self.assertIs(h.store["Mail"]["proton"], False)

    def test_update_names_every_field_it_accepts(self):
        h = self.tool_host()
        h.do_manage_instructions({"action": "create", "name": "X", "text": "t"})
        out = h.do_manage_instructions({"action": "update", "name": "X"})
        for field in ("'proton'", "'blocked_tools'", "'fast_mode'", "'upgrade_target'",
                      "'outlook'", "'excel'"):
            self.assertIn(field, out)

    def test_thinking_mode_is_stored_lower_cased(self):
        h = self.tool_host()
        h.do_manage_instructions({"action": "create", "name": "X", "text": "t"})
        h.do_manage_instructions({"action": "update", "name": "X", "thinking_mode": "Max"})
        self.assertEqual(h.store["X"]["thinking_mode"], "max")

    def test_the_schema_offers_proton_and_text_verbosity(self):
        from myagent.constants import META_TOOLS
        props = next(t for t in META_TOOLS
                     if t["name"] == "manage_instructions")["input_schema"]["properties"]
        self.assertEqual(props["proton"]["type"], "boolean")
        self.assertEqual(props["text_verbosity"]["enum"], ["low", "medium", "high"])


class _Field:
    def __init__(self, value):
        self.value = value

    def get(self, *args):
        return self.value


class EditorSaveTests(unittest.TestCase):

    def host(self, typed, shown, exists=True):
        h = stub(InstructionsMixin, instruction_editor_window=None, _instr_shown_name=shown)
        h._instr_name_entry = _Field(typed)
        h._instr_text = _Field("the text")
        h._instr_tree = mock.Mock(exists=mock.Mock(return_value=exists))
        h._editor_images = []
        h._editor_desktop = _Field(False)
        h.desktop_enabled = mock.Mock()      # the first thing SAVE commits
        return h

    def test_saving_over_a_different_existing_instruction_asks(self):
        h = self.host("Other", shown="Current")
        with mock.patch.object(instructions_mixin.messagebox, "askyesno",
                               return_value=False) as ask:
            h._save_instruction()
        ask.assert_called_once()
        h.desktop_enabled.set.assert_not_called()      # declined: nothing committed

    def test_resaving_the_shown_page_does_not_ask(self):
        h = self.host("Current", shown="Current")
        with mock.patch.object(instructions_mixin.messagebox, "askyesno") as ask:
            with self.assertRaises(AttributeError):
                h._save_instruction()      # runs on past the check (the stub ends there)
        ask.assert_not_called()
        h.desktop_enabled.set.assert_called_once()


if __name__ == "__main__":
    unittest.main()
