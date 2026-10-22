"""The Skills Manager's list pane width (2026-10-23) — the SKILLS list and the
skill text share a draggable sash, the Instruction Editor's bottom row's twin,
and the list pane's width is remembered per instance in agent_state.json as
`skills_list_width`, like the editor's `instruction_list_width`:

* the dialog builds a tk.PanedWindow with the list pane first, added at the
  remembered width (None on a first opening, which tkinter drops, so the
  40-column list's natural width applies), the text pane the stretchy one;
* `_skills_list_width_live` reads the width off the open, laid-out pane and
  returns None for a pane not yet mapped (Tk reports 1), a destroyed one, or
  no dialog at all;
* `_save_last_state` takes the live value when there is one, else the last
  value kept, and writes it under the key; `_load_last_state` reads it back
  with the editor's own validation (a positive int, else None).
"""

import pathlib
import tkinter as tk
import unittest

from tests._util import stub
from myagent.skills_mixin import SkillsMixin

REPO = pathlib.Path(__file__).resolve().parents[1]


def _read(*parts):
    return (REPO / pathlib.Path(*parts)).read_text(encoding="utf-8")


class _Pane:
    def __init__(self, width, exists=True):
        self._width, self._exists = width, exists

    def winfo_exists(self):
        return self._exists

    def winfo_width(self):
        return self._width


class LiveWidthTests(unittest.TestCase):

    def test_a_laid_out_pane_reports_its_width(self):
        h = stub(SkillsMixin, _skills_list_pane=_Pane(312))
        self.assertEqual(h._skills_list_width_live(), 312)

    def test_an_unmapped_pane_a_destroyed_one_and_no_dialog_are_none(self):
        self.assertIsNone(stub(SkillsMixin, _skills_list_pane=_Pane(1))._skills_list_width_live())
        self.assertIsNone(stub(SkillsMixin, _skills_list_pane=_Pane(300, exists=False))
                          ._skills_list_width_live())
        self.assertIsNone(stub(SkillsMixin)._skills_list_width_live())
        self.assertIsNone(stub(SkillsMixin, _skills_list_pane=None)._skills_list_width_live())

    def test_a_real_pane_before_and_after_layout(self):
        try:
            root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        try:
            root.withdraw()
            paned = tk.PanedWindow(root, orient=tk.HORIZONTAL)
            paned.pack(fill=tk.BOTH, expand=True)
            left, right = tk.Frame(paned), tk.Frame(paned)
            paned.add(left, width=None, minsize=150)     # tkinter drops the None
            paned.add(right, minsize=200)
            h = stub(SkillsMixin, _skills_list_pane=left)
            self.assertIsNone(h._skills_list_width_live())   # withdrawn: never laid out
            root.deiconify()
            root.geometry("700x300+0+0")
            root.update()
            width = h._skills_list_width_live()
            if width is None:
                self.skipTest("the display did not lay the window out")
            self.assertGreaterEqual(width, 150)
            left.destroy()
            self.assertIsNone(h._skills_list_width_live())
        finally:
            root.destroy()


class WiringTests(unittest.TestCase):

    def test_the_dialog_splits_list_and_text_with_a_remembered_sash(self):
        src = _read("myagent", "skills_mixin.py")
        body = src[src.index("def open_skills_editor"):]
        self.assertIn("paned = tk.PanedWindow(win, orient=tk.HORIZONTAL, sashwidth=5,", body)
        self.assertIn("self._skills_list_pane = left = tk.Frame(paned)", body)
        self.assertIn("right = tk.Frame(paned)", body)
        self.assertIn('paned.add(left, width=getattr(self, "_skills_list_width", None), minsize=150)',
                      body)
        self.assertIn("paned.add(right, minsize=200)", body)
        self.assertLess(body.index("paned.add(left,"), body.index("paned.add(right,"))
        self.assertNotIn("left.grid(row=2", body)        # the panes are the sash's, not the grid's
        self.assertNotIn("right.grid(row=2", body)
        # The SKILLS band and list keep their rows inside the pane (pinned by
        # tests/test_list_title_band.py too)
        self.assertIn('skills_band.grid(row=1, column=0, sticky="ew")', body)
        self.assertIn("left.grid_rowconfigure(2, weight=1)", body)
        # The close saves the state BEFORE destroying the window, so the width
        # is read off a live pane, and lets the pane go after
        close = body[body.index("def _on_skills_close():"):body.index("win.protocol(")]
        self.assertLess(close.index("self._save_last_state()"), close.index("win.destroy()"))
        self.assertLess(close.index("win.destroy()"), close.index("self._skills_list_pane = None"))

    def test_the_state_file_has_one_writer_and_one_reader(self):
        src = _read("myagent", "state_mixin.py")
        save = src[src.index("def _save_last_state("):src.index("def _load_last_state(")]
        load = src[src.index("def _load_last_state("):src.index("def _periodic_save(")]
        self.assertEqual(src.count('"skills_list_width"'), 2)
        self.assertIn("self._skills_list_width_live()", save)
        self.assertIn('state["skills_list_width"] = self._skills_list_width', save)
        self.assertIn('width = state.get("skills_list_width")', load)
        self.assertIn("self._skills_list_width = width if isinstance(width, int) and width > 0 else None",
                      load)
        # Beside the editor's key, the pattern it copies
        self.assertLess(save.index('"instruction_list_width"'), save.index('"skills_list_width"'))
        self.assertLess(load.index('"instruction_list_width"'), load.index('"skills_list_width"'))


if __name__ == "__main__":
    unittest.main()
