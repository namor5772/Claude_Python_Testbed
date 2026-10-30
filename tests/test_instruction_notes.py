"""Characterization tests for an instruction's Notes (2026-10-20): a free-text
field stored in the instruction's entry as "notes" — general information about
the instruction, saved with it, never part of the prompt — and, since
2026-10-30, the Notes DIALOG that shows and edits it: the Notes button right
of Safety on the Instruction Editor's button row replaced the three-row Notes
box that stood over the instruction text (the user's request).

Pins: the store round trip through manage_instructions (read / create /
update); the editor's notes DRAFT — `_notes_draft` / `_set_notes_draft`: the
string `_instr_notes_text` while the dialog is closed, the dialog's text box
while it is open — through the REAL editor paths on real widgets (a page
selection loads it, SAVE stores it and makes it the applied instruction's,
Apply sets the live value without a store write); the REAL Notes dialog on a
mapped transparent root — opened on the draft with the cursor at the end and
titled after the instruction, its text the draft the editor's SAVE reads
while it is open, a page selection replacing what it shows (title included),
Close and [X] taking the text into the draft, remembering the geometry under
the "notes" kind and saving the state, a second press raising the one open,
the editor's close taking it along with its text kept and its geometry
remembered; the -l / relaunch restore; and a wiring scan — the button
created after Safety and before the bottom row (Tab order is creation order),
Alt+O on it, the dialog placed and remembered like the other persisted
windows (GEOMETRY_KINDS, _open_geometry_windows), no Escape-to-close (the
box is a draft), every persistence site naming the key, no provider / prompt
module ever reading it, and the old box gone.
"""

import gc
import os
import re
import time
import tkinter as tk
import unittest

from tests._util import stub
from myagent.instructions_mixin import InstructionsMixin
from myagent.model_upgrade_mixin import ModelUpgradeMixin
from myagent.state_mixin import StateMixin, GEOMETRY_KINDS
from myagent import constants

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


def _method(src, name):
    """The source of method `name` (to the next method)."""
    body = src[src.index(f"def {name}("):]
    nxt = body.find("\n    def ", 1)
    return body if nxt < 0 else body[:nxt]


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


class _EditorHost(InstructionsMixin, StateMixin):
    """The editor's data paths with the REAL geometry layer under the dialog."""


class _WidgetCase(unittest.TestCase):
    mapped = False      # the dialog tests need a mapped root (a transient of a withdrawn one never maps)

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        if self.mapped:
            self.root.geometry("360x240+0+0")
            self.root.attributes("-alpha", 0.0)
        else:
            self.root.withdraw()
        self.h = None

    def tearDown(self):
        # Drop the host and collect its cycles HERE, on the main thread with
        # the interpreter alive — the dialog's close closure reaches the host
        # and its Tk variables — and only then destroy the root.
        if self.h is not None:
            try:
                self.h._close_notes_dialog()
            except Exception:
                pass
        self.h = None
        gc.collect()
        self.root.destroy()

    def editor_host(self, store):
        """A bare editor host carrying the widgets the data paths touch, with
        every environment-level hook a no-op and the notes draft empty."""
        flags = {n: tk.BooleanVar(self.root, value=False) for n in
                 ("desktop", "browser", "excel", "physical", "meta", "mcp", "google", "proton", "outlook", "conversational")}
        h = stub(_EditorHost, root=self.root,
                 _instr_text=tk.Text(self.root), _instr_name_entry=tk.Entry(self.root),
                 instruction_editor_window=self.root,
                 _instr_notes_text="", _notes_dialog=None, _notes_text=None,
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
        h._editor_run_guard = lambda: False
        for name in ("_commit_editor_state", "_update_title", "_editor_mark_env",
                     "_update_ps_safety_button", "_refresh_image_listbox"):
            setattr(h, name, lambda: None)
        h.saved = []
        h._save_last_state = lambda: h.saved.append(1)
        h._restore_model_params = lambda entry, **kw: None
        h._restore_skill_modes = lambda entry: None
        h._refresh_instruction_list = lambda *a, **kw: None
        h._section_for_new_page = lambda instructions: ""
        self.h = h
        return h


class EditorPathTests(_WidgetCase):
    """The data paths with the dialog CLOSED: the draft is the string."""

    def test_selecting_a_page_loads_its_notes_and_an_old_entry_shows_none(self):
        h = self.editor_host({"Old": {"text": "old text"}, "New": {"text": "new text", "notes": "line 1\nline 2"}})
        h._set_notes_draft("stale draft")
        h._selected_instruction_name = lambda: "New"
        h._on_instruction_selected()
        self.assertEqual(h._notes_draft(), "line 1\nline 2")
        self.assertEqual(h._instr_text.get("1.0", "end-1c"), "new text")
        h._selected_instruction_name = lambda: "Old"
        h._on_instruction_selected()
        self.assertEqual(h._notes_draft(), "")

    def test_save_stores_the_notes_and_makes_them_the_applied_instructions(self):
        h = self.editor_host({"Page": {"text": "t", "notes": "before", "section": "Banking", "order": 3}})
        h._instr_shown_name = "Page"
        h._instr_name_entry.insert(0, "Page")
        h._instr_text.insert("1.0", "the instruction")
        h._set_notes_draft("  after  \n")
        h._save_instruction()
        entry = h.store["Page"]
        self.assertEqual(entry["notes"], "after")                 # stripped, like the text
        self.assertEqual(entry["text"], "the instruction")
        self.assertEqual((entry["section"], entry["order"]), ("Banking", 3))   # the layout keys still carried over
        self.assertEqual(h.agent_instruction_notes, "after")
        self.assertEqual(h.agent_instruction, "the instruction")

    def test_save_of_an_empty_draft_stores_an_empty_string(self):
        h = self.editor_host({})
        h._instr_name_entry.insert(0, "Fresh")
        h._instr_text.insert("1.0", "x")
        h._save_instruction()
        self.assertEqual(h.store["Fresh"]["notes"], "")

    def test_apply_makes_the_draft_notes_the_applied_instructions_without_a_store_write(self):
        h = self.editor_host({"Page": {"text": "t", "notes": "stored"}})
        h._instr_name_entry.insert(0, "Page")
        h._instr_text.insert("1.0", "the instruction")
        h._set_notes_draft("edited, not saved")
        h._close_editor = lambda: None
        h._apply_instruction()
        self.assertEqual(h.agent_instruction_notes, "edited, not saved")
        self.assertEqual(h.store["Page"]["notes"], "stored")

    def test_the_draft_helpers_without_a_dialog(self):
        h = self.editor_host({})
        self.assertEqual(h._notes_draft(), "")
        h._set_notes_draft(None)
        self.assertEqual((h._instr_notes_text, h._notes_draft()), ("", ""))
        h._set_notes_draft("a\nb")
        self.assertEqual(h._notes_draft(), "a\nb")
        h._close_notes_dialog()                 # a no-op with no dialog open
        self.assertEqual(h._notes_draft(), "a\nb")
        self.assertEqual(h.saved, [])


class NotesDialogTests(_WidgetCase):
    """The REAL Notes dialog over the editor host."""
    mapped = True

    def shown(self, h):
        h._open_notes_dialog()
        dlg = h._notes_dialog
        end = time.monotonic() + 3.0
        while not dlg.winfo_ismapped() and time.monotonic() < end:
            self.root.update()
            time.sleep(0.01)
        if not dlg.winfo_ismapped():
            self.skipTest("the display never mapped the dialog")
        self.root.update()
        return dlg

    @staticmethod
    def close_button(dlg):
        return next(w for w in dlg.winfo_children()
                    if w.winfo_class() == "Button" and w.cget("text") == "Close")

    def test_opens_on_the_draft_cursor_at_the_end_titled_after_the_instruction(self):
        h = self.editor_host({})
        h._instr_name_entry.insert(0, "Page")
        h._set_notes_draft("line 1\nline 2")
        dlg = self.shown(h)
        self.assertEqual(h._notes_text.get("1.0", "end-1c"), "line 1\nline 2")
        self.assertEqual(h._notes_text.index("insert"), h._notes_text.index("end-1c"))
        self.assertEqual(dlg.title(), "Notes — Page")
        self.assertIs(dlg.focus_lastfor(), h._notes_text)       # the initial focus: the text
        self.assertEqual(str(dlg.cget("width")) if False else dlg.winfo_class(), "Toplevel")
        self.assertEqual(self.close_button(dlg).cget("text"), "Close")

    def test_without_a_name_the_title_is_notes_alone(self):
        h = self.editor_host({})
        self.assertEqual(self.shown(h).title(), "Notes")

    def test_the_dialogs_text_is_the_draft_the_editors_save_reads_while_it_is_open(self):
        h = self.editor_host({"Page": {"text": "t", "notes": "before"}})
        h._instr_shown_name = "Page"
        h._instr_name_entry.insert(0, "Page")
        h._instr_text.insert("1.0", "the instruction")
        h._set_notes_draft("before")
        self.shown(h)
        h._notes_text.delete("1.0", tk.END)
        h._notes_text.insert("1.0", "  typed in the dialog  \n")
        self.assertEqual(h._notes_draft(), "  typed in the dialog  \n")
        h._save_instruction()
        self.assertEqual(h.store["Page"]["notes"], "typed in the dialog")
        self.assertEqual(h.agent_instruction_notes, "typed in the dialog")
        self.assertTrue(h._notes_dialog.winfo_exists())        # SAVE leaves the dialog open
        h._close_editor = lambda: None
        h._apply_instruction()                                 # Apply reads it too
        self.assertEqual(h.agent_instruction_notes, "typed in the dialog")

    def test_a_page_selection_replaces_what_the_dialog_shows_title_included(self):
        h = self.editor_host({"Old": {"text": "old"}, "New": {"text": "new", "notes": "the new page's"}})
        dlg = self.shown(h)
        h._notes_text.insert("1.0", "stale draft")
        h._selected_instruction_name = lambda: "New"
        h._on_instruction_selected()
        self.assertEqual(h._notes_text.get("1.0", "end-1c"), "the new page's")
        self.assertEqual(dlg.title(), "Notes — New")
        h._selected_instruction_name = lambda: "Old"
        h._on_instruction_selected()
        self.assertEqual(h._notes_text.get("1.0", "end-1c"), "")
        self.assertEqual(dlg.title(), "Notes — Old")
        # CLEAR empties the draft after clearing the name (the wiring scan
        # pins that order), so the title falls back to the bare word.
        h._instr_name_entry.delete(0, tk.END)
        h._instr_shown_name = ""
        h._set_notes_draft("")
        self.assertEqual((h._notes_text.get("1.0", "end-1c"), dlg.title()), ("", "Notes"))

    def test_close_takes_the_text_into_the_draft_remembers_the_geometry_and_saves_the_state(self):
        h = self.editor_host({})
        dlg = self.shown(h)
        h._notes_text.insert("1.0", "kept on close")
        self.close_button(dlg).invoke()
        self.assertIsNone(h._notes_dialog)
        self.assertIsNone(h._notes_text)
        self.assertFalse(dlg.winfo_exists())
        self.assertEqual(h._instr_notes_text, "kept on close")
        self.assertEqual(h._notes_draft(), "kept on close")
        self.assertRegex(h._geo_cache().get("notes", ""), r"^\d+x\d+[+-]\d+[+-]\d+$")
        self.assertEqual(h.saved, [1])

    def test_x_goes_the_same_way_and_the_draft_reopens_in_a_new_dialog(self):
        h = self.editor_host({})
        dlg = self.shown(h)
        h._notes_text.insert("1.0", "typed, then [X]")
        dlg.tk.call(dlg.protocol("WM_DELETE_WINDOW"))
        self.assertFalse(dlg.winfo_exists())
        self.assertEqual((h._instr_notes_text, h.saved), ("typed, then [X]", [1]))
        again = self.shown(h)
        self.assertIsNot(again, dlg)
        self.assertEqual(h._notes_text.get("1.0", "end-1c"), "typed, then [X]")

    def test_a_second_press_raises_the_one_open(self):
        h = self.editor_host({})
        dlg = self.shown(h)
        h._open_notes_dialog()
        self.assertIs(h._notes_dialog, dlg)
        self.assertEqual([w for w in self.root.winfo_children() if w.winfo_class() == "Toplevel"], [dlg])

    def test_the_editors_close_takes_the_dialog_along_text_kept_geometry_remembered(self):
        h = self.editor_host({})
        editor = tk.Toplevel(self.root)
        editor.attributes("-alpha", 0.0)
        editor.geometry("320x200+0+0")
        h.instruction_editor_window = editor
        self.root.update()
        dlg = self.shown(h)
        self.assertEqual(dlg.master, editor)                    # a child of the editor, like the Safety dialog
        h._notes_text.insert("1.0", "typed before the editor closed")
        h._close_editor()
        self.assertFalse(dlg.winfo_exists())
        self.assertFalse(editor.winfo_exists())
        self.assertIsNone(h._notes_dialog)
        self.assertIsNone(h._notes_button)
        self.assertEqual(h._instr_notes_text, "typed before the editor closed")
        self.assertIn("notes", h._geo_cache())
        self.assertEqual(h.saved, [])                           # the editor's own close path saves


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

    def test_the_notes_button_follows_safety_and_the_box_is_gone(self):
        src = _read("myagent", "instructions_mixin.py")
        self.assertNotRegex(src, r"_instr_notes\b")             # the box of 2026-10-20 is gone
        body = src[src.index("def open_instruction_editor"):src.index("def _editor_env_snapshot")]
        safety = body.index("self.ps_safety_button = tk.Button(")
        notes = body.index("self._notes_button = tk.Button(")
        paned = body.index("paned = tk.PanedWindow(")
        self.assertLess(safety, notes)                          # right of Safety …
        self.assertLess(notes, paned)                           # … and before the bottom row: Tab order is creation order
        self.assertRegex(body, r'self\._notes_button = tk\.Button\(\s*buttons_frame, text="Notes", '
                               r'command=self\._open_notes_dialog, padx=10,\s*font=SMALL_BUTTON_FONT,')
        self.assertIn('self._notes_button.pack(side=tk.LEFT, padx=(5, 0))', body)
        self.assertIn('self.ps_safety_button.pack(side=tk.LEFT, padx=(5, 5))', body)
        self.assertIn('"o": self._notes_button', body)          # Alt+O, the box's letter
        self.assertNotIn('"Notes:"', body)
        self.assertNotIn("top_row", body)
        self.assertIn('self._instr_text.grid(row=0,', body)     # the text is the pane's only row again
        self.assertIn("text_pane.grid_rowconfigure(0, weight=1)", body)
        self.assertRegex(body, r"self\._instr_text = tk\.Text\(text_pane, wrap=tk\.WORD, font=\(MONO_FONT, 10\)\)")
        self.assertIn('self._set_notes_draft(getattr(self, "agent_instruction_notes", "") or "")', body)

    def test_the_dialog_is_placed_and_remembered_like_the_other_persisted_windows(self):
        self.assertEqual(GEOMETRY_KINDS["notes"], "notes_dialog_geometry")
        im = _read("myagent", "instructions_mixin.py")
        opened = _method(im, "_open_notes_dialog")
        self.assertIn('self._place_window(dlg, "notes", (560, 320), parent=parent, min_size=(300, 150))', opened)
        self.assertIn("dlg.resizable(True, True)", opened)
        self.assertIn("dlg.transient(parent)", opened)
        self.assertIn('dlg.protocol("WM_DELETE_WINDOW", close)', opened)
        self.assertIn('bind_mnemonics(dlg, {"n": self._notes_text, "c": close_btn})', opened)
        self.assertNotIn("<Escape>", opened)                    # a draft: no Escape-to-close (keyboard.py's rule)
        self.assertIn('self._close_notes_dialog(save_state=True)', opened)
        self.assertRegex(opened, r"self\._notes_text = tk\.Text\(dlg, wrap=tk\.WORD, font=\(MONO_FONT, 10\)\)")
        closed = _method(im, "_close_notes_dialog")
        self.assertIn('self._remember_geometry("notes", dlg)', closed)
        self.assertIn('self._instr_notes_text = box.get("1.0", "end-1c")', closed)
        self.assertLess(closed.index('box.get("1.0", "end-1c")'), closed.index("dlg.destroy()"))
        editor_close = _method(im, "_close_editor")
        self.assertLess(editor_close.index("self._close_notes_dialog()"),
                        editor_close.index("self.instruction_editor_window.destroy()"))
        self.assertIn("self._notes_button = None", _method(im, "_nullify_editor_widgets"))
        sm = _read("myagent", "state_mixin.py")
        self.assertIn('("notes", getattr(self, "_notes_dialog", None))', _method(sm, "_open_geometry_windows"))

    def test_every_persistence_site_carries_the_key(self):
        im = _read("myagent", "instructions_mixin.py")
        for func, needle in (
                ("_save_instruction", "notes = self._notes_draft().strip()"),
                ("_save_instruction", '"notes": notes'),
                ("_clear_instruction_editor", 'self._set_notes_draft("")'),
                ("_on_instruction_selected", 'self._set_notes_draft(entry.get("notes", "") or "")'),
                ("_apply_instruction", "self.agent_instruction_notes = self._notes_draft().strip()"),
                ("do_manage_instructions", '"notes": entry.get("notes", "")'),
                ("do_manage_instructions", '"notes": params.get("notes") or ""'),
        ):
            self.assertIn(needle, _method(im, func), func)
        # The draft is set AFTER the name on both paths that change the page:
        # the open dialog's title follows the name.
        clear = _method(im, "_clear_instruction_editor")
        self.assertLess(clear.index('self._instr_shown_name = ""'), clear.index('self._set_notes_draft("")'))
        select = _method(im, "_on_instruction_selected")
        self.assertLess(select.index("self._instr_name_entry.insert(0, name)"),
                        select.index('self._set_notes_draft(entry.get("notes", "") or "")'))
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
            self.assertNotIn("_notes_draft", src, module)
        self.assertNotIn("notes", _read("Heartbeat.py").lower().replace("# notes", ""))


if __name__ == "__main__":
    unittest.main()
