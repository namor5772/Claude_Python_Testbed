"""Characterization tests for the fixed title band over MyAgent's lists
(2026-09-12): `ui_mixin.list_title_band`, the uppercase INSTRUCTIONS band over
the Instruction Editor's list and the SKILLS band over the Skills Manager's —
a plain Label on the blue a clicked ttk heading shows, never a ttk heading
(which lit up on hover and click and cannot hold one look).

The helper is built on a withdrawn Tk root (skips without a display); the
Skills Manager's use is a static scan, because its builder is one closure-
heavy method that needs the App (the Instruction Editor's use is pinned by
tests/test_instruction_list.py).
"""

import pathlib
import tkinter as tk
import unittest

from myagent.constants import LIST_TITLE_BG
from myagent.ui_mixin import list_title_band

REPO = pathlib.Path(__file__).resolve().parents[1]


class BandTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_a_band_is_an_uppercase_label_on_the_clicked_heading_blue_that_reacts_to_nothing(self):
        band = list_title_band(tk.Frame(self.root), "Skills")
        self.assertIsInstance(band, tk.Label)
        self.assertEqual(band.cget("text"), "SKILLS")
        self.assertEqual(LIST_TITLE_BG, "#bcdcf4")       # the PRESSED header fill, sampled 2026-09-12
        self.assertEqual(band.cget("background"), LIST_TITLE_BG)
        self.assertEqual(str(band.cget("anchor")), "w")
        self.assertEqual(band.bind(), ())                    # nothing bound on it…
        self.assertEqual(self.root.bind_class("Label"), ())  # …and nothing on its class
        self.assertFalse(band.grid_info())                   # ungridded: the caller places it


class WiringTests(unittest.TestCase):
    """Both lists wear one (a static scan, no Tk needed): the band in the
    list's column only — never spanning the scrollbar — with the scrollbar
    running the full height beside band and list."""

    def test_the_skills_manager_list_wears_its_band_over_the_list_proper(self):
        src = (REPO / "myagent" / "skills_mixin.py").read_text(encoding="utf-8")
        self.assertIn('list_title_band(left, "SKILLS")', src)
        self.assertIn('skills_band.grid(row=1, column=0, sticky="ew")', src)
        self.assertIn('skill_listbox.grid(row=2, column=0, sticky="nsew")', src)
        self.assertIn('list_scrollbar.grid(row=1, column=1, rowspan=2, sticky="ns")', src)
        self.assertIn("left.grid_rowconfigure(2, weight=1)", src)

    def test_the_instruction_editor_list_uses_the_same_helper(self):
        src = (REPO / "myagent" / "instructions_mixin.py").read_text(encoding="utf-8")
        self.assertIn('list_title_band(pane, "INSTRUCTIONS")', src)
        self.assertNotIn("tree.heading(", src)


if __name__ == "__main__":
    unittest.main()
