"""Characterization tests for the toolbar's "last pressed" highlight (2026-09-08).

The Instruction / START / STOP buttons on MyAgent's top row are one group: the
button pressed most recently wears a light-blue background and a bold face,
and the other two are put back to their resting look — the background they
were created with and their own font at regular weight. Nothing is repainted
at startup, so the buttons first appear exactly as created: all three in the
same regular Arial 10, STOP disabled.

These are the only tests in the suite that need a Tk root, because the
highlight is nothing but widget configuration. They build the three buttons
the way setup_ui does on a withdrawn root and skip cleanly where no display
is available.
"""

import tkinter as tk
import unittest

from tests._util import stub
from myagent.constants import TOOLBAR_ACTIVE_BG
from myagent.ui_mixin import UIMixin

TOOLBAR_FONT = ("Arial", 10)   # the one font setup_ui gives all three buttons


def _actual(button, option):
    """The rendered font attribute of a button, whatever its font spec is."""
    return button.tk.call("font", "actual", button.cget("font"), option)


class ToolbarHighlightTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.pressed = []
        self.app = stub(UIMixin)
        self.instruction = tk.Button(self.root, text="Instruction", font=TOOLBAR_FONT)
        self.start = tk.Button(self.root, text="START", width=8, font=TOOLBAR_FONT)
        self.stop = tk.Button(
            self.root, text="STOP", width=8, font=TOOLBAR_FONT, state="disabled",
        )
        self.buttons = (self.instruction, self.start, self.stop)
        self.created_look = {
            b: (b.cget("bg"), b.cget("font"), str(b.cget("state"))) for b in self.buttons
        }
        self.app._track_toolbar_presses(
            (self.instruction, lambda: self.pressed.append("instruction")),
            (self.start, lambda: self.pressed.append("start")),
            (self.stop, lambda: self.pressed.append("stop")),
        )
        self.default_bg = tk.Button(self.root).cget("bg")

    def tearDown(self):
        self.root.destroy()

    def test_startup_look_is_untouched_and_identical(self):
        # Tracking the group repaints nothing: bg, font spec and state are
        # exactly what the buttons were created with — and the three were
        # created alike, so at startup they share one regular-weight font.
        for button in self.buttons:
            self.assertEqual(
                (button.cget("bg"), button.cget("font"), str(button.cget("state"))),
                self.created_look[button],
            )
            self.assertEqual(_actual(button, "-weight"), "normal")
            self.assertEqual(_actual(button, "-family"), "Arial")
            self.assertEqual(int(_actual(button, "-size")), 10)
        self.assertEqual(len({str(b.cget("font")) for b in self.buttons}), 1)
        self.assertEqual(self.pressed, [])

    def test_press_paints_light_blue_bold_and_runs_the_command(self):
        self.start.invoke()
        self.assertEqual(self.pressed, ["start"])
        self.assertEqual(self.start.cget("bg"), TOOLBAR_ACTIVE_BG)
        self.assertEqual(self.start.cget("activebackground"), TOOLBAR_ACTIVE_BG)
        self.assertEqual(_actual(self.start, "-weight"), "bold")
        # Only the weight changes — family and size are the button's own.
        self.assertEqual(_actual(self.start, "-family"), "Arial")
        self.assertEqual(int(_actual(self.start, "-size")), 10)

    def test_other_buttons_go_back_to_resting_look(self):
        self.start.invoke()
        self.instruction.invoke()
        self.assertEqual(self.pressed, ["start", "instruction"])
        self.assertEqual(self.instruction.cget("bg"), TOOLBAR_ACTIVE_BG)
        self.assertEqual(_actual(self.instruction, "-weight"), "bold")
        for resting in (self.start, self.stop):
            self.assertEqual(resting.cget("bg"), self.default_bg)
            self.assertEqual(resting.cget("activebackground"), self.default_bg)
            self.assertEqual(_actual(resting, "-weight"), "normal")
            self.assertEqual(_actual(resting, "-family"), "Arial")
            self.assertEqual(int(_actual(resting, "-size")), 10)

    def test_bold_twin_is_derived_from_the_buttons_own_font(self):
        # The helper must never hardcode a face: a button created without an
        # explicit font (TkDefaultFont — Segoe UI 9 on Windows) gets a bold
        # twin with THAT family/size, so a per-button font choice is honoured.
        default_family = self.root.tk.call("font", "actual", "TkDefaultFont", "-family")
        default_size = self.root.tk.call("font", "actual", "TkDefaultFont", "-size")
        plain = tk.Button(self.root, text="plain")
        stub(UIMixin)._track_toolbar_presses((plain, lambda: None))
        plain.invoke()
        self.assertEqual(_actual(plain, "-family"), default_family)
        self.assertEqual(_actual(plain, "-size"), default_size)
        self.assertEqual(_actual(plain, "-weight"), "bold")

    def test_disabled_button_cannot_be_pressed(self):
        self.stop.invoke()  # disabled at startup, like the real STOP
        self.assertEqual(self.pressed, [])
        self.assertEqual(self.stop.cget("bg"), self.created_look[self.stop][0])
        self.stop.config(state="normal")
        self.stop.invoke()
        self.assertEqual(self.pressed, ["stop"])
        self.assertEqual(self.stop.cget("bg"), TOOLBAR_ACTIVE_BG)
        self.assertEqual(_actual(self.stop, "-weight"), "bold")
        self.assertEqual(self.start.cget("bg"), self.default_bg)
        self.assertEqual(self.instruction.cget("bg"), self.default_bg)

    def test_highlight_survives_state_toggles(self):
        # _start_agent disables START while running and the run's end
        # re-enables it; neither touches the paint, so START stays the
        # last-pressed button until another one is pressed.
        self.start.invoke()
        self.start.config(state="disabled")
        self.stop.config(state="normal")
        self.start.config(state="normal")
        self.stop.config(state="disabled")
        self.assertEqual(self.start.cget("bg"), TOOLBAR_ACTIVE_BG)
        self.assertEqual(_actual(self.start, "-weight"), "bold")

    def test_repeated_press_of_the_same_button_is_stable(self):
        self.start.invoke()
        self.start.invoke()
        self.assertEqual(self.pressed, ["start", "start"])
        self.assertEqual(self.start.cget("bg"), TOOLBAR_ACTIVE_BG)
        self.assertEqual(_actual(self.start, "-weight"), "bold")
        self.assertEqual(self.instruction.cget("bg"), self.default_bg)


if __name__ == "__main__":
    unittest.main()
