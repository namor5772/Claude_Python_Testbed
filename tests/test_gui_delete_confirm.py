"""Characterization tests for the GUI-only delete confirmation (2026-09-02).

MyAgent's two GUI DELETE buttons — the Instruction editor's (`_delete_instruction`)
and the Skills Manager's (a closure inside `open_skills_editor`, needs Tk, so it
is verified live rather than here) — ask an askyesno (default No) before
mutating the OneDrive-shared stores. The tool-driven deletes — the
`manage_instructions` / `manage_skills` `delete` actions the model calls — must
NOT prompt: an unattended run has nobody to answer a dialog, and a tool delete
is the model acting on an explicit instruction. These tests pin both halves:
the button path prompts and honours "No"; the tool paths never touch the
messagebox at all.
"""

import unittest
from unittest import mock

from tests._util import stub
from myagent import instructions_mixin, skills_mixin
from myagent.instructions_mixin import InstructionsMixin
from myagent.skills_mixin import SkillsMixin


class _Var:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Entry:
    def __init__(self, text=""):
        self.text = text

    def get(self):
        return self.text

    def delete(self, *_):
        self.text = ""


def _editor_host(selected="Weather", store=None):
    host = stub(InstructionsMixin,
                _instr_combo_var=_Var(selected),
                _instr_name_entry=_Entry(),
                instruction_editor_window=object(),
                saved=[])
    store = dict(store or {"Weather": {"text": "w"}, "Other": {"text": "o"}})
    host._load_saved_instructions = lambda: dict(store)
    host._save_instructions_to_disk = lambda data: host.saved.append(dict(data))
    host._refresh_instruction_list = lambda: None
    return host


class InstructionEditorDelete(unittest.TestCase):
    def test_no_leaves_the_store_untouched(self):
        host = _editor_host()
        with mock.patch.object(instructions_mixin.messagebox, "askyesno",
                               return_value=False) as ask:
            host._delete_instruction()
        ask.assert_called_once()
        self.assertEqual(ask.call_args.kwargs.get("default"), "no")
        self.assertIn("Weather", ask.call_args.args[1])
        self.assertEqual(host.saved, [])
        # the selection is left alone too — nothing happened
        self.assertEqual(host._instr_combo_var.get(), "Weather")

    def test_yes_deletes_exactly_that_instruction(self):
        host = _editor_host()
        with mock.patch.object(instructions_mixin.messagebox, "askyesno",
                               return_value=True):
            host._delete_instruction()
        self.assertEqual(host.saved, [{"Other": {"text": "o"}}])
        self.assertEqual(host._instr_combo_var.get(), "")

    def test_missing_name_warns_without_prompting(self):
        # The existence checks still run first: a name that isn't in the store
        # gets the "Not found" warning and never reaches the confirmation.
        host = _editor_host(selected="Ghost")
        with mock.patch.object(instructions_mixin.messagebox, "askyesno") as ask, \
                mock.patch.object(instructions_mixin.messagebox, "showwarning") as warn:
            host._delete_instruction()
        ask.assert_not_called()
        warn.assert_called_once()
        self.assertEqual(host.saved, [])


class ToolDeletesNeverPrompt(unittest.TestCase):
    def test_manage_instructions_delete_is_unprompted(self):
        host = _editor_host()
        host.agent_instruction_name = "Weather"
        with mock.patch.object(instructions_mixin.messagebox, "askyesno") as ask:
            result = host.do_manage_instructions({"action": "delete", "name": "Weather"})
        ask.assert_not_called()
        self.assertEqual(result, "Instruction 'Weather' deleted.")
        self.assertEqual(host.saved, [{"Other": {"text": "o"}}])
        self.assertEqual(host.agent_instruction_name, "")

    def test_manage_skills_delete_is_unprompted(self):
        host = stub(SkillsMixin, skills={"tidy-up": {"content": "x", "mode": "disabled"}},
                    saved=0)
        host._save_skills = lambda: setattr(host, "saved", host.saved + 1)
        host._post_skill_ui_refresh = lambda: None
        with mock.patch.object(skills_mixin.messagebox, "askyesno") as ask, \
                mock.patch.object(skills_mixin, "delete_skill_tree_entry") as remove:
            result = host.do_manage_skills({"action": "delete", "name": "tidy-up"})
        ask.assert_not_called()
        remove.assert_called_once()
        self.assertEqual(result, "Skill 'tidy-up' deleted.")
        self.assertEqual(host.skills, {})
        self.assertEqual(host.saved, 1)


if __name__ == "__main__":
    unittest.main()
