"""The Skills Manager's Description dialog (2026-10-31): a skill's description
— the what + when trigger signal listed in the system prompt's on-demand
skills index, stored in its frontmatter as `description` — is edited in its
own dialog behind a **Description** button right of Cycle Mode, the
Instruction Editor's Notes button and dialog applied to the skill (the user's
request; until then a three-row box between the name row and the panes).

Pinned here, on the REAL `open_skills_editor` over a stub host (a withdrawn
root; every disk-touching call stubbed):

* the Description button sits right of Cycle Mode in one frame above the
  list, Cycle Mode created first (Tab order), and the old box is gone;
* the DRAFT — `_desc_draft` / `_set_desc_draft`: the string `_skill_desc_text`
  while the dialog is closed, the dialog's text box while it is open — set by
  a skill selection, cleared by NEW and DELETE, read by SAVE either way and
  whitespace-collapsed (descriptions are single-line), an empty draft
  dropping the key;
* the REAL dialog: opened on the draft with the cursor at the end and titled
  after the skill, a second press raising the one open, a skill selection
  replacing what it shows (title included), Close and [X] taking the text
  into the draft, remembering the geometry under the "skill_desc" kind and
  saving the state, the Skills Manager's close taking it along (text kept,
  geometry remembered, the manager's own save);
* a wiring scan: GEOMETRY_KINDS / _open_geometry_windows, the dialog placed
  like the other persisted windows, no Escape-to-close (a draft), Alt+E on
  the button and in the dialog, MyAgent's init defaults, nothing of the old
  box left.
"""

import copy
import pathlib
import tkinter as tk
import unittest
from unittest import mock

from myagent import skills_mixin
from myagent.skills_mixin import SkillsMixin
from myagent.state_mixin import GEOMETRY_KINDS
from tests._util import stub
from tests.test_skills_list import _Button, _find, _skills, _walk

REPO = pathlib.Path(__file__).resolve().parents[1]


def _read(*parts):
    return (REPO / pathlib.Path(*parts)).read_text(encoding="utf-8")


def _method(src, name):
    """The source of method `name` (to the next method)."""
    body = src[src.index(f"def {name}("):]
    nxt = body.find("\n    def ", 1)
    return body if nxt < 0 else body[:nxt]


class _ManagerCase(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        skills = _skills()
        self.saves, self.remembered, self.state_saves = [], [], []
        host = self.host = stub(
            SkillsMixin, root=self.root, skills=skills,
            _skill_global_modes={n: e["mode"] for n, e in skills.items()},
            skills_editor_window=None, instruction_editor_window=None,
            _skills_refresh_list=None, _skills_list_width=None,
            _skills_collapsed_sections=set(), skills_button=_Button(),
            _desc_dialog=None, _desc_text=None, _skill_desc_text="stale", _skills_name_entry=None)
        host._remember_geometry = lambda kind, win: self.remembered.append(kind)
        host._save_last_state = lambda: self.state_saves.append(1)
        host._place_window = lambda *a, **k: None
        host._save_skills = lambda: self.saves.append(copy.deepcopy(host.skills))
        host.open_skills_editor()
        self.win = host.skills_editor_window
        self.lst = host._skills_list
        self.name_entry = _find(self.win, "Entry")
        self.text = _find(self.win, "Text")
        self.buttons = {t: _find(self.win, "Button", text=t)
                        for t in ("SAVE", "DELETE", "NEW", "Cycle Mode", "Description")}

    def tearDown(self):
        self.host._close_desc_dialog()
        self.root.destroy()

    def pump(self):
        self.root.update()

    def select(self, name):
        self.lst.select(("page", name))
        self.pump()

    def open(self):
        self.buttons["Description"].invoke()
        self.pump()
        return self.host._desc_dialog

    @staticmethod
    def close_button(dlg):
        return next(w for w in dlg.winfo_children()
                    if w.winfo_class() == "Button" and w.cget("text") == "Close")


class LayoutTests(_ManagerCase):

    def test_the_description_button_sits_right_of_cycle_mode_and_the_box_is_gone(self):
        cycle, desc = self.buttons["Cycle Mode"], self.buttons["Description"]
        self.assertIs(desc.master, cycle.master)
        self.assertEqual(cycle.master.pack_slaves(), [cycle, desc])     # created in that order: Tab order
        self.assertIs(cycle.master.master, self.host._skills_list_pane)  # above the list, in its pane
        self.assertEqual(int(cycle.master.grid_info()["row"]), 0)
        self.assertEqual(int(self.lst.tree.master.grid_info()["row"]), 1)
        texts = [w for w in _walk(self.win) if w.winfo_class() == "Text"]
        self.assertEqual(texts, [self.text])                              # the skill text alone
        labels = [str(w.cget("text")) for w in _walk(self.win) if w.winfo_class() == "Label"]
        self.assertNotIn("Description", labels)                           # the box's label is gone
        self.assertEqual(self.host._skill_desc_text, "")                  # a fresh manager: an empty draft
        self.assertIs(self.host._skills_name_entry, self.name_entry)


class DraftTests(_ManagerCase):

    def test_a_selection_sets_the_draft_and_save_reads_it_open_or_closed(self):
        self.select("bank-b")
        self.assertEqual(self.host._desc_draft(), "Second bank skill.")
        self.assertEqual(self.host._skill_desc_text, "Second bank skill.")
        self.assertIsNone(self.host._desc_dialog)
        dlg = self.open()
        self.host._desc_text.delete("1.0", tk.END)
        self.host._desc_text.insert("1.0", "  two\n  lines  ")
        self.assertEqual(self.host._desc_draft(), "  two\n  lines  ")      # the box IS the draft while open
        self.buttons["SAVE"].invoke()
        self.assertEqual(self.host.skills["bank-b"]["description"], "two lines")   # single-line in the store
        self.assertTrue(dlg.winfo_exists())                                 # SAVE leaves the dialog open
        self.assertEqual(self.host._desc_text.get("1.0", "end-1c"), "  two\n  lines  ")
        self.close_button(dlg).invoke()
        self.assertEqual(self.host._skill_desc_text, "  two\n  lines  ")    # raw in the draft …
        self.buttons["SAVE"].invoke()
        self.assertEqual(self.host.skills["bank-b"]["description"], "two lines")   # … collapsed again
        self.host._set_desc_draft("   ")
        self.buttons["SAVE"].invoke()
        self.assertNotIn("description", self.host.skills["bank-b"])       # a blank draft drops the key
        self.assertEqual(len(self.saves), 3)

    def test_new_and_delete_clear_the_draft_and_what_an_open_dialog_shows(self):
        self.select("bank-b")
        dlg = self.open()
        self.assertEqual(self.host._desc_text.get("1.0", "end-1c"), "Second bank skill.")
        self.buttons["NEW"].invoke()
        self.assertEqual(self.host._desc_draft(), "")
        self.assertEqual(self.host._desc_text.get("1.0", "end-1c"), "")
        self.assertEqual(dlg.title(), "Description")                      # no name any more
        self.select("bank-b")
        self.assertEqual(self.host._desc_text.get("1.0", "end-1c"), "Second bank skill.")
        with mock.patch.object(skills_mixin.messagebox, "askyesno", return_value=True), \
                mock.patch.object(skills_mixin, "delete_skill_tree_entry"):
            self.buttons["DELETE"].invoke()
        self.assertNotIn("bank-b", self.host.skills)
        self.assertEqual(self.host._desc_draft(), "")
        self.assertEqual(self.host._desc_text.get("1.0", "end-1c"), "")

    def test_the_draft_helpers_without_a_dialog(self):
        h = stub(SkillsMixin)
        self.assertEqual(h._desc_draft(), "")
        self.assertIsNone(h._desc_box())
        self.assertEqual(h._desc_title(), "Description")
        h._set_desc_draft("set while closed")
        self.assertEqual((h._desc_draft(), h._skill_desc_text), ("set while closed",) * 2)
        h._set_desc_draft(None)
        self.assertEqual(h._desc_draft(), "")
        h._close_desc_dialog()                                            # nothing open: a no-op
        self.assertIsNone(h._desc_dialog)


class DialogTests(_ManagerCase):

    def test_opens_on_the_draft_cursor_at_the_end_titled_after_the_skill_and_once(self):
        self.select("bank-b")
        dlg = self.open()
        self.assertEqual(dlg.winfo_class(), "Toplevel")
        self.assertIs(dlg.master, self.win)                                 # a child of the Skills Manager
        self.assertEqual(dlg.title(), "Description — bank-b")
        box = self.host._desc_text
        self.assertEqual(box.get("1.0", "end-1c"), "Second bank skill.")
        self.assertEqual(box.index("insert"), box.index("end-1c"))
        # (the initial focus — the text — is pinned by the wiring scan: an
        # unmapped dialog on a withdrawn root records no focus to read back)
        self.assertEqual(self.close_button(dlg).cget("text"), "Close")
        self.assertTrue(any(w.winfo_class() == "Scrollbar" for w in dlg.winfo_children()))
        self.buttons["Description"].invoke()                                # a second press
        self.assertIs(self.host._desc_dialog, dlg)
        self.assertEqual([w for w in self.win.winfo_children() if w.winfo_class() == "Toplevel"], [dlg])
        self.assertEqual(self.remembered, [])                               # nothing closed yet

    def test_a_skill_selection_replaces_what_the_dialog_shows_title_included(self):
        self.select("bank-b")
        dlg = self.open()
        self.select("mail-a")                                               # no description
        self.assertEqual(self.host._desc_text.get("1.0", "end-1c"), "")
        self.assertEqual(dlg.title(), "Description — mail-a")
        self.select("bank-b")
        self.assertEqual(self.host._desc_text.get("1.0", "end-1c"), "Second bank skill.")
        self.assertEqual(dlg.title(), "Description — bank-b")
        self.lst.select(("section", "Bank"))                                # a header loads nothing
        self.pump()
        self.assertEqual(dlg.title(), "Description — bank-b")

    def test_close_and_x_take_the_text_into_the_draft_remember_the_geometry_and_save_the_state(self):
        dlg = self.open()
        self.host._desc_text.insert("1.0", "kept on close")
        self.close_button(dlg).invoke()
        self.assertIsNone(self.host._desc_dialog)
        self.assertIsNone(self.host._desc_text)
        self.assertFalse(dlg.winfo_exists())
        self.assertEqual(self.host._skill_desc_text, "kept on close")
        self.assertEqual(self.host._desc_draft(), "kept on close")
        self.assertEqual(self.remembered, ["skill_desc"])
        self.assertEqual(self.state_saves, [1])
        again = self.open()
        self.assertIsNot(again, dlg)
        self.assertEqual(self.host._desc_text.get("1.0", "end-1c"), "kept on close")
        self.host._desc_text.insert("end", ", then [X]")
        again.tk.call(again.protocol("WM_DELETE_WINDOW"))
        self.assertFalse(again.winfo_exists())
        self.assertEqual(self.host._skill_desc_text, "kept on close, then [X]")
        self.assertEqual((self.remembered, self.state_saves), (["skill_desc"] * 2, [1, 1]))

    def test_the_skills_managers_close_takes_the_dialog_along(self):
        self.select("bank-b")
        dlg = self.open()
        self.host._desc_text.insert("end", " — typed before the close")
        self.root.tk.call(self.win.protocol("WM_DELETE_WINDOW"))
        self.assertFalse(dlg.winfo_exists())
        self.assertFalse(self.win.winfo_exists())
        self.assertIsNone(self.host._desc_dialog)
        self.assertIsNone(self.host._skills_name_entry)
        self.assertEqual(self.host._skill_desc_text, "Second bank skill. — typed before the close")
        self.assertEqual(self.remembered, ["skill_desc", "skills"])        # the dialog first, then the window
        self.assertEqual(self.state_saves, [1])                             # the manager's own save, once
        self.assertEqual(self.host._desc_title(), "Description")           # no name box any more


class WiringTests(unittest.TestCase):

    def test_the_dialog_is_placed_and_remembered_like_the_other_persisted_windows(self):
        self.assertEqual(GEOMETRY_KINDS["skill_desc"], "skill_desc_dialog_geometry")
        sm = _read("myagent", "state_mixin.py")
        self.assertIn('("skill_desc", getattr(self, "_desc_dialog", None))',
                      _method(sm, "_open_geometry_windows"))
        src = _read("myagent", "skills_mixin.py")
        opened = _method(src, "_open_desc_dialog")
        self.assertIn('self._place_window(dlg, "skill_desc", (560, 280), parent=parent, min_size=(300, 120))',
                      opened)
        self.assertIn("dlg.resizable(True, True)", opened)
        self.assertIn("dlg.transient(parent)", opened)
        self.assertIn('dlg.protocol("WM_DELETE_WINDOW", close)', opened)
        self.assertIn('bind_mnemonics(dlg, {"e": self._desc_text, "c": close_btn})', opened)
        self.assertNotIn("<Escape>", opened)                    # a draft: no Escape-to-close (keyboard.py's rule)
        self.assertIn("self._close_desc_dialog(save_state=True)", opened)
        self.assertRegex(opened, r'self\._desc_text = tk\.Text\(dlg, wrap=tk\.WORD, font=\("Arial", 10\), undo=True\)')
        # The initial focus: the text, cursor at the end — the dialog's last two lines
        self.assertTrue(opened.rstrip().endswith('self._desc_text.mark_set("insert", "end-1c")\n'
                                                 '        self._desc_text.focus_set()'))
        closed = _method(src, "_close_desc_dialog")
        self.assertIn('self._remember_geometry("skill_desc", dlg)', closed)
        self.assertIn('self._skill_desc_text = box.get("1.0", "end-1c")', closed)
        self.assertLess(closed.index('box.get("1.0", "end-1c")'), closed.index("dlg.destroy()"))

    def test_the_skills_manager_is_wired_to_the_dialog_and_the_box_is_gone(self):
        src = _read("myagent", "skills_mixin.py")
        body = src[src.index("def open_skills_editor"):]
        for gone in ("desc_entry", "desc_row", "height=3", 'text="Description", font=("Arial", 10)'):
            self.assertNotIn(gone, body, gone)
        close = body[body.index("def _on_skills_close():"):body.index("win.protocol(")]
        self.assertLess(close.index("self._close_desc_dialog()"), close.index("win.destroy()"))
        self.assertIn("self._skills_name_entry = None", close)
        self.assertIn("self._skills_name_entry = name_entry", body)
        self.assertIn('self._set_desc_draft("")', body)
        # Cycle Mode then Description, in one frame above the list
        self.assertLess(body.index('toggle_btn = tk.Button(mode_row, text="Cycle Mode"'),
                        body.index('desc_btn = tk.Button(mode_row, text="Description"'))
        self.assertIn("command=self._open_desc_dialog)", body)
        self.assertIn('mode_row.grid(row=0, column=0, sticky="w", pady=(0, 5))', body)
        # The draft is what SAVE reads and what a selection sets
        self.assertIn('desc = " ".join(self._desc_draft().split())', body)
        self.assertIn('self._set_desc_draft(self.skills[name].get("description", ""))', body)
        self.assertEqual(body.count('self._set_desc_draft("")'), 3)       # open, NEW, DELETE
        # The panes moved up into the box's row
        self.assertIn('paned.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=(0, 10))', body)
        self.assertIn("win.grid_rowconfigure(1, weight=1)", body)
        self.assertNotIn("win.grid_rowconfigure(2", body)
        self.assertIn('"k": name_entry, "e": desc_btn,', body)              # Alt+E, the box's letter
        app = _read("MyAgent.py")
        for line in ("self._desc_dialog = None", "self._desc_text = None",
                     'self._skill_desc_text = ""', "self._skills_name_entry = None"):
            self.assertIn(line, app, line)


if __name__ == "__main__":
    unittest.main()
