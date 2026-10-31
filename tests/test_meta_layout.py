"""Filing by section through the Meta tools (2026-10-31).

`manage_instructions` and `manage_skills` take a `section` on create and
update: the page is filed as that section's last entry through the one pure
rule the editors' Section… prompt uses (`instruction_layout.file_page`), every
page renumbered; '' or the Unfiled label unfiles; omitted means unfiled on
create and unchanged on update. `read` reports the section ('' = unfiled) and
`list` tags each filed row `(section: X)` in its existing order. Until then
the two tools were the one writer that could not place a page, and a tool
change left an open Instruction Editor's list stale — `_post_instruction_ui_refresh`,
the skills tool's `_post_skill_ui_refresh` twin, now refreshes it on the Tk
thread.

Pinned here:

* the instruction store through the REAL `do_manage_instructions` over an
  in-memory store (the Notes tests' host): create filed / unfiled / into a new
  section, update moving / unfiling (both spellings) / leaving the layout
  alone / section alone accepted, read and list, a non-string section refused
  before anything is saved, the editor refresh on a fake root and its absence
  on a root-less host;
* the skills tree through the REAL `do_manage_skills`, `_save_skills` and the
  tree writer on a temp tree (the data dir redirected): the same cases, the
  `section:` / `order:` frontmatter lines on disk, the one-time renumbering of
  every skill's file;
* both tools through the REAL `_execute_tool` (the dispatch), and a schema /
  source scan.
"""

import copy
import json
import os
import pathlib
import queue
import tempfile
import unittest
from unittest import mock

from myagent import constants
from myagent import datapaths as dp
from myagent import skills_mixin
from myagent.helpers import _ToolBlock
from myagent.instruction_layout import rows, section_of
from myagent.instructions_mixin import InstructionsMixin
from myagent.model_upgrade_mixin import ModelUpgradeMixin
from myagent.skills_mixin import SkillsMixin
from myagent.streaming_mixin import StreamingMixin
from tests._util import stub
from tests.test_skills_list import _Button, _skills

REPO = pathlib.Path(__file__).resolve().parents[1]


def _read(*parts):
    return (REPO / pathlib.Path(*parts)).read_text(encoding="utf-8")


def _method(src, name):
    """The source of method `name` (to the next method)."""
    body = src[src.index(f"def {name}("):]
    nxt = body.find("\n    def ", 1)
    return body if nxt < 0 else body[:nxt]


class _Root:
    """A Tk root for the refresh hooks: `after` runs the callback at once."""

    def __init__(self):
        self.calls = 0

    def after(self, ms, fn=None, *args):
        self.calls += 1
        if fn is not None:
            fn(*args)


# ── Instructions ─────────────────────────────────────────────────────────

class _ToolHost(InstructionsMixin, ModelUpgradeMixin):
    pass


class _E2EInstrHost(StreamingMixin, InstructionsMixin, ModelUpgradeMixin):
    """The tool dispatch over the instructions tool."""


def _store():
    """A store as the editor lays it out: two sections and an unfiled page."""
    return {
        "Pay": {"text": "pay", "images": [], "section": "Banking", "order": 0},
        "Recon": {"text": "recon", "images": [], "section": "Banking", "order": 1},
        "Mail": {"text": "mail", "images": [], "section": "Office", "order": 2},
        "Loose": {"text": "loose", "images": []},
    }


def tool_host(cls=_ToolHost, store=None, **extra):
    h = stub(cls, provider="Anthropic", model="claude-sonnet-5", temperature=1.0,
             thinking_enabled=True, thinking_effort="low", thinking_budget=8192,
             thinking_mode="low", text_verbosity="medium", fast_mode=False, skills={},
             _disabled_confirm_patterns=set(), _blocked_tools=set(),
             agent_instruction_name="", upgrade_target=None, _upgrade_original=None,
             **extra)
    h.store = store if store is not None else _store()
    h.saves = 0
    h._load_saved_instructions = lambda: h.store

    def save(data):
        h.store = data
        h.saves += 1
    h._save_instructions_to_disk = save
    return h


def _layout(store):
    """[(section, name), ...] in display order — what the list shows."""
    return rows(store)


class InstructionFilingTests(unittest.TestCase):

    def test_create_with_a_section_files_it_as_that_sections_last_page(self):
        h = tool_host()
        out = h.do_manage_instructions({"action": "create", "name": "New", "text": "t",
                                        "section": "Banking"})
        self.assertEqual(out, "Instruction 'New' created successfully. Filed under 'Banking'.")
        self.assertEqual(_layout(h.store), [("Banking", "Pay"), ("Banking", "Recon"),
                                            ("Banking", "New"), ("Office", "Mail"),
                                            ("", "Loose")])
        # Every page renumbered 0..n-1 in display order (the list's rule).
        self.assertEqual([h.store[n]["order"] for _, n in _layout(h.store)], [0, 1, 2, 3, 4])
        self.assertEqual(h.saves, 1)

    def test_create_without_a_section_lands_unfiled_and_says_nothing_of_filing(self):
        h = tool_host()
        out = h.do_manage_instructions({"action": "create", "name": "New", "text": "t"})
        self.assertEqual(out, "Instruction 'New' created successfully.")
        self.assertNotIn("section", h.store["New"])
        self.assertNotIn("order", h.store["New"])          # nothing was renumbered
        self.assertEqual(_layout(h.store)[-2:], [("", "Loose"), ("", "New")])

    def test_create_into_a_name_no_section_has_creates_it_after_the_last(self):
        h = tool_host()
        h.do_manage_instructions({"action": "create", "name": "Weekly", "text": "t",
                                  "section": "Reports"})
        self.assertEqual(_layout(h.store), [("Banking", "Pay"), ("Banking", "Recon"),
                                            ("Office", "Mail"), ("Reports", "Weekly"),
                                            ("", "Loose")])

    def test_update_moves_a_page_to_the_end_of_another_section(self):
        h = tool_host()
        out = h.do_manage_instructions({"action": "update", "name": "Pay", "section": "Office"})
        self.assertEqual(out, "Instruction 'Pay' updated successfully. Filed under 'Office'.")
        self.assertEqual(_layout(h.store), [("Banking", "Recon"), ("Office", "Mail"),
                                            ("Office", "Pay"), ("", "Loose")])

    def test_update_with_blank_or_the_unfiled_label_unfiles(self):
        for spelling in ("", "Unfiled", "unfiled", "  UNFILED "):
            with self.subTest(spelling=spelling):
                h = tool_host()
                out = h.do_manage_instructions({"action": "update", "name": "Pay",
                                                "section": spelling})
                self.assertEqual(out, "Instruction 'Pay' updated successfully. Unfiled.")
                self.assertEqual(section_of(h.store["Pay"]), "")
                self.assertNotIn("section", h.store["Pay"])
                self.assertEqual(_layout(h.store), [("Banking", "Recon"), ("Office", "Mail"),
                                                    ("", "Loose"), ("", "Pay")])

    def test_update_without_a_section_leaves_the_layout_alone(self):
        h = tool_host()
        before = copy.deepcopy(h.store)
        out = h.do_manage_instructions({"action": "update", "name": "Pay", "text": "new text"})
        self.assertEqual(out, "Instruction 'Pay' updated successfully.")
        self.assertEqual(h.store["Pay"]["text"], "new text")
        for name in before:
            self.assertEqual(h.store[name].get("section"), before[name].get("section"))
            self.assertEqual(h.store[name].get("order"), before[name].get("order"))

    def test_update_with_the_section_alone_is_accepted(self):
        h = tool_host()
        out = h.do_manage_instructions({"action": "update", "name": "Loose", "section": "Office"})
        self.assertNotIn("Error", out)
        self.assertEqual(section_of(h.store["Loose"]), "Office")
        # ...and the nothing-given error names it among the accepted fields.
        self.assertIn("'section'", h.do_manage_instructions({"action": "update", "name": "Loose"}))

    def test_the_applied_instructions_update_keeps_its_wording_plus_the_note(self):
        h = tool_host()
        h.agent_instruction_name = "Pay"
        out = h.do_manage_instructions({"action": "update", "name": "Pay", "section": "Office"})
        self.assertEqual(out, "Instruction 'Pay' updated on disk. Changes will take effect "
                              "next time it is loaded. Filed under 'Office'.")

    def test_read_reports_the_section_and_list_tags_the_filed_rows(self):
        h = tool_host()
        self.assertEqual(json.loads(h.do_manage_instructions(
            {"action": "read", "name": "Pay"}))["section"], "Banking")
        self.assertEqual(json.loads(h.do_manage_instructions(
            {"action": "read", "name": "Loose"}))["section"], "")
        listing = h.do_manage_instructions({"action": "list"}).split("\n")
        self.assertIn("• Pay  [Anthropic/]  (section: Banking)", listing)
        self.assertIn("• Mail  [Anthropic/]  (section: Office)", listing)
        self.assertIn("• Loose  [Anthropic/]", listing)
        self.assertFalse(any("Loose" in line and "(section:" in line for line in listing))

    def test_a_non_string_section_is_refused_before_anything_is_saved(self):
        h = tool_host()
        for params in ({"action": "create", "name": "New", "text": "t", "section": 5},
                       {"action": "update", "name": "Pay", "section": ["Office"]}):
            with self.subTest(action=params["action"]):
                out = h.do_manage_instructions(params)
                self.assertTrue(out.startswith("Error: 'section' must be a string"), out)
        self.assertEqual(h.saves, 0)
        self.assertNotIn("New", h.store)
        self.assertEqual(section_of(h.store["Pay"]), "Banking")

    def test_a_tool_change_refreshes_an_open_editors_list_on_the_tk_thread(self):
        root = _Root()
        h = tool_host(root=root)
        seen = []
        h._refresh_instruction_list = lambda instructions=None, select=None: seen.append(instructions)
        h.do_manage_instructions({"action": "create", "name": "New", "text": "t", "section": "Office"})
        h.do_manage_instructions({"action": "update", "name": "Pay", "text": "x"})
        h.do_manage_instructions({"action": "delete", "name": "Mail"})
        self.assertEqual(root.calls, 3)                     # one after(0, …) per change
        self.assertEqual(len(seen), 3)
        self.assertTrue(all(s is h.store for s in seen))    # the store just saved

    def test_a_root_less_host_skips_the_refresh(self):
        h = tool_host()                                     # no root attribute at all
        out = h.do_manage_instructions({"action": "create", "name": "New", "text": "t"})
        self.assertIn("created", out)

    def test_the_real_refresh_does_nothing_without_an_open_editor(self):
        h = tool_host(root=_Root())                         # the REAL _refresh_instruction_list
        out = h.do_manage_instructions({"action": "update", "name": "Pay", "section": "Office"})
        self.assertIn("Filed under 'Office'", out)

    def test_through_the_dispatch(self):
        h = tool_host(_E2EInstrHost, queue=queue.Queue())
        out = h._execute_tool(_ToolBlock("manage_instructions", "toolu_1",
                                         {"action": "update", "name": "Loose", "section": "Banking"}))
        self.assertEqual(out, "Instruction 'Loose' updated successfully. Filed under 'Banking'.")
        self.assertEqual(_layout(h.store)[:3], [("Banking", "Pay"), ("Banking", "Recon"),
                                                ("Banking", "Loose")])


# ── Skills ───────────────────────────────────────────────────────────────

class _E2ESkillsHost(StreamingMixin, SkillsMixin):
    """The tool dispatch over the skills tool."""


class SkillFilingTests(unittest.TestCase):
    """The REAL `do_manage_skills`, `_save_skills` and tree writer on a temp
    tree — the data dir redirected, so the shared tree is never touched —
    with a fake root whose `after` runs at once (the refresh hook) and no
    Skills Manager open."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        os.environ[dp.DATA_DIR_ENV] = tmp.name
        self.addCleanup(os.environ.pop, dp.DATA_DIR_ENV, None)
        self.tree = os.path.join(tmp.name, "skills")
        dp.save_skills_tree(self.tree, _skills())              # the seed, as a launch finds it
        patcher = mock.patch.object(skills_mixin, "SKILLS_DIR", self.tree)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.root = _Root()
        host = self.host = stub(_E2ESkillsHost, root=self.root, queue=queue.Queue(),
                                skills_editor_window=None, _skills_refresh_list=None,
                                skills_button=_Button(), _blocked_tools=set())
        host.skills = host._load_skills()                      # REAL: the temp tree, global modes noted

    def reload(self):
        return dp.load_skills_tree(self.tree)

    def frontmatter(self, name):
        with open(os.path.join(self.tree, name, dp.SKILL_BASENAME), encoding="utf-8") as fh:
            text = fh.read()
        return text.split("\n---\n", 1)[0].split("\n")[1:]

    def test_create_with_a_section_files_it_last_in_that_section_on_disk(self):
        out = self.host.do_manage_skills({"action": "create", "name": "mail-b", "content": "body",
                                          "section": "Mail"})
        self.assertEqual(out, "Skill 'mail-b' created successfully. Filed under 'Mail'.")
        tree = self.reload()
        self.assertEqual(rows(tree), [("Bank", "bank-a"), ("Bank", "bank-b"), ("Mail", "mail-a"),
                                      ("Mail", "mail-b"), ("", "loose")])
        self.assertEqual(self.frontmatter("mail-b"),
                         ["name: mail-b", "mode: disabled", "section: Mail", "order: 3"])
        # The renumbering reached every skill's file once (the unfiled one too).
        self.assertEqual([tree[n]["order"] for _, n in rows(tree)], [0, 1, 2, 3, 4])
        self.assertEqual(self.frontmatter("loose"), ["name: loose", "mode: disabled", "order: 4"])
        self.assertEqual(self.root.calls, 1)                   # the UI refresh hook ran

    def test_create_without_a_section_lands_unfiled_with_no_layout_lines(self):
        out = self.host.do_manage_skills({"action": "create", "name": "new-one", "content": "body"})
        self.assertEqual(out, "Skill 'new-one' created successfully.")
        self.assertEqual(self.frontmatter("new-one"), ["name: new-one", "mode: disabled"])
        self.assertEqual(rows(self.reload())[-2:], [("", "loose"), ("", "new-one")])

    def test_update_moves_a_skill_to_the_end_of_another_section(self):
        out = self.host.do_manage_skills({"action": "update", "name": "mail-a", "section": "Bank"})
        self.assertEqual(out, "Skill 'mail-a' updated successfully. Filed under 'Bank'.")
        tree = self.reload()
        self.assertEqual(rows(tree), [("Bank", "bank-a"), ("Bank", "bank-b"), ("Bank", "mail-a"),
                                      ("", "loose")])
        self.assertEqual(self.frontmatter("mail-a"),
                         ["name: mail-a", "mode: enabled", "section: Bank", "order: 2"])

    def test_update_with_blank_or_the_unfiled_label_unfiles(self):
        out = self.host.do_manage_skills({"action": "update", "name": "bank-a", "section": "Unfiled"})
        self.assertEqual(out, "Skill 'bank-a' updated successfully. Unfiled.")
        tree = self.reload()
        self.assertNotIn("section", tree["bank-a"])
        # Unfiling puts the page at the very end, after the pages already unfiled.
        self.assertEqual(rows(tree), [("Bank", "bank-b"), ("Mail", "mail-a"),
                                      ("", "loose"), ("", "bank-a")])
        self.assertNotIn("section: Bank", self.frontmatter("bank-a"))

    def test_update_without_a_section_leaves_the_layout_alone(self):
        before = self.reload()
        out = self.host.do_manage_skills({"action": "update", "name": "bank-a", "content": "new body"})
        self.assertEqual(out, "Skill 'bank-a' updated successfully.")
        after = self.reload()
        self.assertEqual(after["bank-a"]["content"], "new body")
        for name in before:
            self.assertEqual(after[name].get("section"), before[name].get("section"))
            self.assertEqual(after[name].get("order"), before[name].get("order"))

    def test_update_with_the_section_alone_is_accepted_and_nothing_given_names_it(self):
        self.assertNotIn("Error", self.host.do_manage_skills(
            {"action": "update", "name": "loose", "section": "Mail"}))
        self.assertEqual(section_of(self.reload()["loose"]), "Mail")
        self.assertIn("'section'", self.host.do_manage_skills({"action": "update", "name": "loose"}))

    def test_read_reports_the_section_and_list_tags_the_filed_rows(self):
        self.assertEqual(json.loads(self.host.do_manage_skills(
            {"action": "read", "name": "bank-b"}))["section"], "Bank")
        self.assertEqual(json.loads(self.host.do_manage_skills(
            {"action": "read", "name": "loose"}))["section"], "")
        listing = self.host.do_manage_skills({"action": "list"}).split("\n")
        self.assertIn("• bank-b  [on_demand]  (section: Bank)", listing)
        self.assertIn("• mail-a  [enabled]  (section: Mail)", listing)
        self.assertIn("• loose  [disabled]", listing)
        self.assertFalse(any("loose" in line and "(section:" in line for line in listing))

    def test_a_non_string_section_is_refused_before_anything_is_written(self):
        before = self.reload()
        for params in ({"action": "create", "name": "new-one", "content": "b", "section": 5},
                       {"action": "update", "name": "bank-a", "section": ["Mail"]}):
            with self.subTest(action=params["action"]):
                out = self.host.do_manage_skills(params)
                self.assertTrue(out.startswith("Error: 'section' must be a string"), out)
        self.assertEqual(self.reload(), before)
        self.assertNotIn("new-one", self.host.skills)

    def test_the_mode_and_description_travel_with_the_filing(self):
        out = self.host.do_manage_skills({"action": "create", "name": "mail-b", "content": "body",
                                          "mode": "on_demand", "description": "What and when.",
                                          "section": "Mail"})
        self.assertIn("Filed under 'Mail'", out)
        self.assertEqual(self.frontmatter("mail-b"),
                         ["name: mail-b", "description: What and when.", "mode: on_demand",
                          "section: Mail", "order: 3"])

    def test_through_the_dispatch(self):
        out = self.host._execute_tool(_ToolBlock("manage_skills", "toolu_1",
                                                 {"action": "update", "name": "loose", "section": "Bank"}))
        self.assertEqual(out, "Skill 'loose' updated successfully. Filed under 'Bank'.")
        self.assertEqual(rows(self.reload())[:3], [("Bank", "bank-a"), ("Bank", "bank-b"),
                                                   ("Bank", "loose")])


# ── Schema and wiring ────────────────────────────────────────────────────

class SchemaTests(unittest.TestCase):

    def _prop(self, tool):
        return next(t for t in constants.META_TOOLS if t["name"] == tool)["input_schema"]["properties"]

    def test_both_schemas_name_section_as_a_string_with_the_unfiled_rule(self):
        for tool in ("manage_instructions", "manage_skills"):
            with self.subTest(tool=tool):
                prop = self._prop(tool)["section"]
                self.assertEqual(prop["type"], "string")
                for word in ("last", "Unfiled", "unchanged on update"):
                    self.assertIn(word, prop["description"])

    def test_the_tools_file_through_the_one_pure_rule(self):
        instr = _read("myagent", "instructions_mixin.py")
        skills = _read("myagent", "skills_mixin.py")
        self.assertIn("file_page(instructions, name, section)", _method(instr, "do_manage_instructions"))
        self.assertEqual(_method(instr, "do_manage_instructions").count("file_page("), 2)  # create + update
        self.assertIn("file_page(self.skills, name, section)", _method(skills, "do_manage_skills"))
        self.assertEqual(_method(skills, "do_manage_skills").count("file_page("), 2)
        # The update's accepted-field list names it (so a section-only update is not refused).
        self.assertIn('"fast_mode", "blocked_tools", "section")', _method(instr, "do_manage_instructions"))
        # Every store change the tool makes reaches an open editor's list.
        self.assertEqual(_method(instr, "do_manage_instructions").count("self._post_instruction_ui_refresh(instructions)"), 3)
        self.assertIn("root.after(0, lambda: self._refresh_instruction_list(instructions))",
                      _method(instr, "_post_instruction_ui_refresh"))


if __name__ == "__main__":
    unittest.main()
