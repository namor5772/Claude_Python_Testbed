"""Characterization tests for an instruction's Notes (2026-10-20): a free-text
field stored in the instruction's entry as "notes" — general information about
the instruction, shown in the Instruction Editor's three-row Notes box ABOVE
the instruction text, saved with the instruction, never part of the prompt.

Pins: the store round trip through manage_instructions (read / create /
update), the REAL editor paths that carry the field on real widgets (a page
selection loads it, SAVE stores it and makes it the applied instruction's),
the -l / relaunch restore, and a wiring scan — the box created before the
text (so the text stays the editor's last Tab stop), three rows high, Alt+O,
every persistence site naming the key, and no provider / prompt module ever
reading it.
"""

import os
import re
import tkinter as tk
import unittest

from tests._util import stub
from myagent.instructions_mixin import InstructionsMixin
from myagent.model_upgrade_mixin import ModelUpgradeMixin
from myagent.state_mixin import StateMixin
from myagent import constants

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class _ToolHost(InstructionsMixin, ModelUpgradeMixin):
    pass


def tool_host(store=None):
    h = stub(_ToolHost, provider="Anthropic", model="claude-sonnet-5", temperature=1.0,
             thinking_enabled=True, thinking_effort="low", thinking_budget=8192,
             thinking_mode="low", text_verbosity="medium", fast_mode=False, skills={},
             _disabled_confirm_patterns=set(), _blocked_tools=set(),
             agent_instruction_name="", upgrade_target=None, _upgrade_original=None)
    h.store = store if store is not None else {}
    h._load_saved_instructions = lambda: h.store
    h._save_instructions_to_disk = lambda data: setattr(h, "store", data)
    return h


class StoreTests(unittest.TestCase):

    def test_read_returns_the_notes_and_defaults_to_empty(self):
        import json
        h = tool_host({"A": {"text": "do a", "notes": "why A exists"}, "B": {"text": "do b"}})
        self.assertEqual(json.loads(h.do_manage_instructions({"action": "read", "name": "A"}))["notes"], "why A exists")
        self.assertEqual(json.loads(h.do_manage_instructions({"action": "read", "name": "B"}))["notes"], "")

    def test_create_stores_the_notes_and_defaults_to_empty(self):
        h = tool_host()
        h.do_manage_instructions({"action": "create", "name": "N", "text": "do it", "notes": "first run 20/10"})
        h.do_manage_instructions({"action": "create", "name": "M", "text": "do it"})
        self.assertEqual(h.store["N"]["notes"], "first run 20/10")
        self.assertEqual(h.store["M"]["notes"], "")

    def test_update_changes_the_notes_alone_and_an_empty_string_clears(self):
        h = tool_host({"A": {"text": "do a", "notes": "old", "desktop": True}})
        out = h.do_manage_instructions({"action": "update", "name": "A", "notes": "new"})
        self.assertIn("updated", out)
        self.assertEqual((h.store["A"]["notes"], h.store["A"]["text"], h.store["A"]["desktop"]), ("new", "do a", True))
        h.do_manage_instructions({"action": "update", "name": "A", "notes": ""})
        self.assertEqual(h.store["A"]["notes"], "")

    def test_update_with_only_notes_is_a_valid_update(self):
        h = tool_host({"A": {"text": "do a"}})
        self.assertNotIn("Error", h.do_manage_instructions({"action": "update", "name": "A", "notes": "n"}))
        self.assertIn("'notes'", h.do_manage_instructions({"action": "update", "name": "A"}))

    def test_schema_names_the_field(self):
        tool = next(t for t in constants.META_TOOLS if t["name"] == "manage_instructions")
        prop = tool["input_schema"]["properties"]["notes"]
        self.assertEqual(prop["type"], "string")
        self.assertIn("NEVER part of the prompt", prop["description"])


class _WidgetCase(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def editor_host(self, store):
        """A bare InstructionsMixin carrying the editor widgets the two data
        paths touch, with every environment-level hook a no-op."""
        flags = {n: tk.BooleanVar(self.root, value=False) for n in
                 ("desktop", "browser", "excel", "physical", "meta", "mcp", "google", "proton", "outlook", "conversational")}
        h = stub(InstructionsMixin,
                 _instr_text=tk.Text(self.root), _instr_notes=tk.Text(self.root, height=3),
                 _instr_name_entry=tk.Entry(self.root), instruction_editor_window=self.root,
                 _instr_shown_name="", _instr_tree=None, _editor_images=[], pending_images=[],
                 dictation_auto_send=tk.BooleanVar(self.root, value=False), upgrade_target=None,
                 provider="Anthropic", model="claude-sonnet-5", thinking_enabled=True, thinking_effort="low",
                 thinking_budget=8192, thinking_mode="low", temperature=1.0, text_verbosity="medium",
                 fast_mode=False, skills={}, _disabled_confirm_patterns=set(), _blocked_tools=set(),
                 agent_instruction="", agent_instruction_name="", agent_instruction_notes="")
        for name, var in flags.items():
            setattr(h, f"_editor_{name}", var)
            setattr(h, f"{name}_enabled", var)
        h.store = store
        h._load_saved_instructions = lambda: h.store
        h._save_instructions_to_disk = lambda data: setattr(h, "store", data)
        for name in ("_editor_run_guard",):
            setattr(h, name, lambda: False)
        for name in ("_commit_editor_state", "_update_title", "_save_last_state", "_editor_mark_env",
                     "_update_ps_safety_button", "_refresh_image_listbox"):
            setattr(h, name, lambda: None)
        h._restore_model_params = lambda entry, **kw: None
        h._restore_skill_modes = lambda entry: None
        h._refresh_instruction_list = lambda *a, **kw: None
        h._section_for_new_page = lambda instructions: ""
        return h


class EditorPathTests(_WidgetCase):

    def test_selecting_a_page_loads_its_notes_and_an_old_entry_shows_none(self):
        h = self.editor_host({"Old": {"text": "old text"}, "New": {"text": "new text", "notes": "line 1\nline 2"}})
        h._instr_notes.insert("1.0", "stale draft")
        h._selected_instruction_name = lambda: "New"
        h._on_instruction_selected()
        self.assertEqual(h._instr_notes.get("1.0", "end-1c"), "line 1\nline 2")
        self.assertEqual(h._instr_text.get("1.0", "end-1c"), "new text")
        h._selected_instruction_name = lambda: "Old"
        h._on_instruction_selected()
        self.assertEqual(h._instr_notes.get("1.0", "end-1c"), "")

    def test_save_stores_the_notes_and_makes_them_the_applied_instructions(self):
        h = self.editor_host({"Page": {"text": "t", "notes": "before", "section": "Banking", "order": 3}})
        h._instr_shown_name = "Page"
        h._instr_name_entry.insert(0, "Page")
        h._instr_text.insert("1.0", "the instruction")
        h._instr_notes.insert("1.0", "  after  \n")
        h._save_instruction()
        entry = h.store["Page"]
        self.assertEqual(entry["notes"], "after")                 # stripped, like the text
        self.assertEqual(entry["text"], "the instruction")
        self.assertEqual((entry["section"], entry["order"]), ("Banking", 3))   # the layout keys still carried over
        self.assertEqual(h.agent_instruction_notes, "after")
        self.assertEqual(h.agent_instruction, "the instruction")

    def test_save_of_an_empty_box_stores_an_empty_string(self):
        h = self.editor_host({})
        h._instr_name_entry.insert(0, "Fresh")
        h._instr_text.insert("1.0", "x")
        h._save_instruction()
        self.assertEqual(h.store["Fresh"]["notes"], "")

    def test_apply_makes_the_draft_notes_the_applied_instructions_without_a_store_write(self):
        h = self.editor_host({"Page": {"text": "t", "notes": "stored"}})
        h._instr_name_entry.insert(0, "Page")
        h._instr_text.insert("1.0", "the instruction")
        h._instr_notes.insert("1.0", "edited, not saved")
        h._close_editor = lambda: None
        h._apply_instruction()
        self.assertEqual(h.agent_instruction_notes, "edited, not saved")
        self.assertEqual(h.store["Page"]["notes"], "stored")


class RestoreTests(_WidgetCase):

    def test_apply_instruction_entry_restores_the_notes(self):
        flags = {n: tk.BooleanVar(self.root, value=False) for n in
                 ("desktop", "browser", "excel", "physical", "meta", "mcp", "google", "proton", "outlook", "conversational")}
        h = stub(StateMixin, dictation_auto_send=tk.BooleanVar(self.root, value=False),
                 _restore_model_params=lambda e, **kw: None, _restore_skill_modes=lambda e: None,
                 _update_title=lambda: None)
        for name, var in flags.items():
            setattr(h, f"{name}_enabled", var)
        h._apply_instruction_entry("A", {"text": "t", "notes": "kept across a relaunch"})
        self.assertEqual(h.agent_instruction_notes, "kept across a relaunch")
        h._apply_instruction_entry("B", {"text": "t"})
        self.assertEqual(h.agent_instruction_notes, "")


class WiringTests(unittest.TestCase):

    def test_editor_builds_the_notes_box_before_the_text_three_rows_high(self):
        src = _read("myagent", "instructions_mixin.py")
        body = src[src.index("def open_instruction_editor"):src.index("def _editor_env_snapshot")]
        notes = body.index("self._instr_notes = tk.Text(")
        text = body.index("self._instr_text = tk.Text(")
        self.assertLess(notes, text, "the text must stay the editor's last Tab stop")
        self.assertRegex(body, r"self\._instr_notes = tk\.Text\(notes_frame, height=3, wrap=tk\.WORD, font=\(MONO_FONT, 10\)\)")
        self.assertRegex(body, r"self\._instr_text = tk\.Text\(text_pane, wrap=tk\.WORD, font=\(MONO_FONT, 10\)\)")   # the same font
        self.assertIn("notes_frame.grid(row=0,", body)
        self.assertIn("notes_scrollbar = tk.Scrollbar(notes_frame, command=self._instr_notes.yview)", body)
        self.assertIn("self._instr_notes.config(yscrollcommand=notes_scrollbar.set)", body)
        self.assertIn("self._instr_text.grid(row=1,", body)
        self.assertIn("text_pane.grid_rowconfigure(1, weight=1)", body)
        self.assertIn('"o": self._instr_notes', body)
        self.assertIn('self._instr_notes.insert("1.0", getattr(self, "agent_instruction_notes", "")', body)

    def test_every_persistence_site_carries_the_key(self):
        im = _read("myagent", "instructions_mixin.py")
        for func, needle in (
                ("_save_instruction", '"notes": notes'),
                ("_clear_instruction_editor", 'self._instr_notes.delete("1.0", tk.END)'),
                ("_on_instruction_selected", 'entry.get("notes", "")'),
                ("_apply_instruction", 'self.agent_instruction_notes = self._instr_notes.get('),
                ("do_manage_instructions", '"notes": entry.get("notes", "")'),
                ("do_manage_instructions", '"notes": params.get("notes") or ""'),
        ):
            body = im[im.index(f"def {func}("):]
            nxt = body.find("\n    def ", 1)
            body = body if nxt < 0 else body[:nxt]
            self.assertIn(needle, body, func)
        updatable = re.search(r"updatable = \((.*?)\)", im, re.S).group(1)
        self.assertIn('"notes"', updatable)
        sm = _read("myagent", "state_mixin.py")
        self.assertIn('"notes": getattr(self, "agent_instruction_notes", "") or ""', sm)
        self.assertIn('self.agent_instruction_notes = entry.get("notes", "") or ""', sm)
        self.assertIn('self.agent_instruction_notes = ""', _read("MyAgent.py"))

    def test_the_notes_never_reach_a_prompt_or_a_provider(self):
        for module in ("skills_mixin.py", "streaming_mixin.py", "anthropic_mixin.py", "openai_mixin.py",
                       "gemini_mixin.py", "xai_mixin.py", "kimi_mixin.py", "ollama_mixin.py", "chat_mixin.py"):
            src = _read("myagent", module)
            self.assertNotIn("agent_instruction_notes", src, module)
            self.assertNotIn('"notes"', src, module)
        self.assertNotIn("notes", _read("Heartbeat.py").lower().replace("# notes", ""))


if __name__ == "__main__":
    unittest.main()
