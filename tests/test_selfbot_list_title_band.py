"""Characterization tests for SelfBot's Skills Manager list band (2026-09-12).

SelfBot's Skills Manager list wears the same fixed uppercase SKILLS band as
MyAgent's two lists (tests/test_list_title_band.py pins the band itself): a
plain Label on the blue a clicked ttk heading shows, never a ttk heading.
SelfBot carries an in-file copy of ui_mixin's `list_title_band` (its myagent
import is the optional kind), so the first test pins the copy to the
original; `LIST_TITLE_BG` is try-imported from myagent.constants with a
literal fallback in the stub block, and both are pinned to the constant.

SelfBot is importable in-process (module import builds no Tk root); the Tk
test builds the band on a withdrawn root and skips where no display exists;
the wiring is a static scan, because the builder is one closure-heavy method.
"""

import inspect
import pathlib
import sys
import tkinter as tk
import unittest

from myagent import ui_mixin
from myagent.constants import LIST_TITLE_BG

_saved_argv = sys.argv
sys.argv = ["SelfBot.py"]
try:
    import SelfBot
    _IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - a box without the GUI deps
    SelfBot = None
    _IMPORT_ERROR = exc
finally:
    sys.argv = _saved_argv

_needs_selfbot = unittest.skipIf(SelfBot is None, f"SelfBot not importable: {_IMPORT_ERROR}")
REPO = pathlib.Path(__file__).resolve().parents[1]


@_needs_selfbot
class CopyTests(unittest.TestCase):

    def test_the_in_file_copy_is_byte_identical_to_ui_mixins(self):
        self.assertEqual(inspect.getsource(SelfBot.list_title_band),
                         inspect.getsource(ui_mixin.list_title_band))

    def test_the_colour_is_the_shared_constant_and_the_stub_fallback_agrees(self):
        self.assertEqual(SelfBot.LIST_TITLE_BG, LIST_TITLE_BG)
        # A box without the myagent package paints the same blue
        src = (REPO / "SelfBot.py").read_text(encoding="utf-8")
        self.assertIn(f'    LIST_TITLE_BG = "{LIST_TITLE_BG}"', src)

    def test_the_skills_manager_list_wears_the_band_over_the_list_proper(self):
        src = (REPO / "SelfBot.py").read_text(encoding="utf-8")
        self.assertIn('list_title_band(left, "SKILLS")', src)
        self.assertIn('skills_band.grid(row=1, column=0, sticky="ew")', src)
        self.assertIn('skill_listbox.grid(row=2, column=0, sticky="nsew")', src)
        self.assertIn('list_scrollbar.grid(row=1, column=1, rowspan=2, sticky="ns")', src)
        self.assertIn("left.grid_rowconfigure(2, weight=1)", src)


@_needs_selfbot
class BandTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_the_copy_builds_the_same_band(self):
        band = SelfBot.list_title_band(tk.Frame(self.root), "Skills")
        self.assertEqual(band.cget("text"), "SKILLS")
        self.assertEqual(band.cget("background"), LIST_TITLE_BG)
        self.assertEqual(band.bind(), ())


if __name__ == "__main__":
    unittest.main()
