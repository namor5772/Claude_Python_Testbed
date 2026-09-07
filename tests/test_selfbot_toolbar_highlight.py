"""Characterization tests for SelfBot's toolbar "last pressed" highlight (2026-09-08).

SelfBot's main window has seven plain buttons — DELETE / NEW CHAT (row 1),
SAVE (row 2), Attach Images / System Prompt / Skills / Safety (the button
bar) — that form one "last pressed" group with one shared Arial 10 font,
exactly like MyAgent's Instruction / START / STOP: the button pressed most
recently is light blue + bold, the other six wear their creation-time look
at regular weight, and nothing is repainted at startup. The red/green Auto
toggle is deliberately NOT in the group — its colour is its state. SelfBot
carries an in-file copy of ui_mixin's helper (its myagent import is the
optional kind), so the first test pins the copy to the original.

SelfBot is importable in-process (module import builds no Tk root); the Tk
tests build the buttons on a withdrawn root and skip where no display exists.
"""

import inspect
import sys
import tkinter as tk
import unittest

from tests._util import stub
from myagent.constants import TOOLBAR_ACTIVE_BG
from myagent.ui_mixin import UIMixin

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

TOOLBAR_FONT = ("Arial", 10)   # the one font setup_ui gives all seven buttons
BUTTONS = (  # text, creation options — as in SelfBot.setup_ui
    ("DELETE", dict(width=8)),
    ("NEW CHAT", dict(width=10)),
    ("SAVE", dict(width=6)),
    ("Attach Images", dict(width=14)),
    ("System Prompt", dict(width=14)),
    ("Skills (2)", dict(padx=10)),
    ("Safety", dict(padx=10)),
)


def _actual(button, option):
    """The rendered font attribute of a button, whatever its font spec is."""
    return button.tk.call("font", "actual", button.cget("font"), option)


@_needs_selfbot
class InFileCopyTests(unittest.TestCase):

    def test_helper_copies_are_identical_to_ui_mixins(self):
        # SelfBot's convention is an in-file copy, not an import. Pinning the
        # two copies byte-for-byte means a fix in one can't silently miss the
        # other.
        for name in ("_track_toolbar_presses", "_press_toolbar_button"):
            self.assertEqual(
                inspect.getsource(getattr(SelfBot.App, name)),
                inspect.getsource(getattr(UIMixin, name)),
                name,
            )

    def test_active_colour_matches_myagents(self):
        self.assertEqual(SelfBot.TOOLBAR_ACTIVE_BG, TOOLBAR_ACTIVE_BG)


@_needs_selfbot
class SelfBotToolbarHighlightTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.pressed = []
        self.app = stub(SelfBot.App)
        self.buttons = {}
        pairs = []
        for text, opts in BUTTONS:
            button = tk.Button(self.root, text=text, font=TOOLBAR_FONT, **opts)
            self.buttons[text] = button
            pairs.append((button, lambda t=text: self.pressed.append(t)))
        self.created_look = {
            b: (b.cget("bg"), b.cget("font"), str(b.cget("state")))
            for b in self.buttons.values()
        }
        self.app._track_toolbar_presses(*pairs)
        self.default_bg = tk.Button(self.root).cget("bg")

    def tearDown(self):
        self.root.destroy()

    def _highlighted(self):
        return [t for t, b in self.buttons.items() if b.cget("bg") == TOOLBAR_ACTIVE_BG]

    def _bold(self):
        return [t for t, b in self.buttons.items() if _actual(b, "-weight") == "bold"]

    def test_startup_look_is_untouched_identical_and_all_enabled(self):
        for button in self.buttons.values():
            self.assertEqual(
                (button.cget("bg"), button.cget("font"), str(button.cget("state"))),
                self.created_look[button],
            )
            # None of SelfBot's toolbar buttons is ever disabled.
            self.assertEqual(str(button.cget("state")), "normal")
            self.assertEqual(_actual(button, "-weight"), "normal")
            self.assertEqual(_actual(button, "-family"), "Arial")
            self.assertEqual(int(_actual(button, "-size")), 10)
        self.assertEqual(len({str(b.cget("font")) for b in self.buttons.values()}), 1)
        self.assertEqual(self._highlighted(), [])
        self.assertEqual(self.pressed, [])

    def test_exactly_one_button_is_highlighted_after_each_press(self):
        sequence = ["Attach Images", "SAVE", "NEW CHAT", "Safety", "DELETE"]
        for text in sequence:
            self.buttons[text].invoke()
            self.assertEqual(self._highlighted(), [text])
            self.assertEqual(self._bold(), [text])
            for other, button in self.buttons.items():
                if other != text:
                    self.assertEqual(button.cget("bg"), self.default_bg)
                    self.assertEqual(button.cget("activebackground"), self.default_bg)
        self.assertEqual(self.pressed, sequence)

    def test_relabels_leave_the_paint_alone(self):
        # Skills / Safety relabel themselves through config(text=...) — the
        # skill counts and the "(N bypassed)" suffix — which must not disturb
        # the highlight in either direction.
        skills, safety = self.buttons["Skills (2)"], self.buttons["Safety"]
        skills.invoke()
        skills.config(text="Skills (1+3)")
        safety.config(text="Safety (2 bypassed)")
        self.assertEqual(self._highlighted(), ["Skills (2)"])
        self.assertEqual(_actual(skills, "-weight"), "bold")
        self.assertEqual(safety.cget("bg"), self.default_bg)
        self.assertEqual(_actual(safety, "-weight"), "normal")


if __name__ == "__main__":
    unittest.main()
