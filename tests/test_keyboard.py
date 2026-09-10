"""Characterization tests for keyboard operation (myagent/keyboard.py, 2026-09-10).

MyAgent must be usable without a mouse. Tk already makes every control a Tab
stop; this module pins what keyboard.py adds on top:

* Escape inside a text box / entry / spinbox / combobox moves the focus to the
  next control and changes nothing — the safe way out of a Text widget, where
  Tab types a tab (Ctrl+Tab, Tk's own binding, still works too).
* Return presses the focused button, like Space; a disabled button ignores it.
* Alt+<letter> mnemonics press a button / toggle a checkbutton / focus a
  field, without drawing any underline cue on the widgets.
* Checkbuttons embedded in a scrolled Text (the Safety dialog) are walked with
  Tab / Shift+Tab / Down / Up, each scrolled into view first.

The widget tests need a real Tk root that HOLDS the keyboard focus, because a
synthetic key event is delivered to the focus window: the root is a fully
transparent window with the focus forced onto it, and the tests skip where the
display will not grant it (or where there is no display at all). The wiring
scan at the end needs no Tk.
"""

import pathlib
import time
import tkinter as tk
from tkinter import ttk
import unittest

from myagent import keyboard

REPO = pathlib.Path(__file__).resolve().parents[1]


class _TkCase(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.geometry("240x240+0+0")
        self.root.attributes("-alpha", 0.0)  # invisible, yet mapped and focusable
        self.root.update()
        self.root.focus_force()
        self.root.update()
        if self.root.focus_get() is None:
            self.root.destroy()
            self.skipTest("the display will not give this process the keyboard focus")

    def tearDown(self):
        self.root.destroy()

    def settle(self, seconds=0.0):
        self.root.update()
        if seconds:
            time.sleep(seconds)
            self.root.update()

    def focus(self, widget):
        widget.focus_set()
        self.settle()
        self.assertIs(self.root.focus_get(), widget)

    def key(self, widget, sequence, seconds=0.0):
        widget.event_generate(sequence)
        self.settle(seconds)


class EscapeLeavesFieldTests(_TkCase):

    def setUp(self):
        super().setUp()
        keyboard.install_class_bindings(self.root)
        self.before = tk.Entry(self.root)
        self.before.pack()
        self.text = tk.Text(self.root, height=2)
        self.text.pack()
        self.entry = tk.Entry(self.root)
        self.entry.pack()
        self.spin = tk.Spinbox(self.root, from_=0, to=9)
        self.spin.pack()
        self.combo = ttk.Combobox(self.root, values=["a", "b"], state="readonly")
        self.combo.pack()
        self.after = tk.Button(self.root, text="after")
        self.after.pack()
        self.window_escapes = []
        self.root.bind("<Escape>", lambda e: self.window_escapes.append(e))
        self.settle()

    def test_escape_in_a_text_box_moves_to_the_next_control_and_changes_nothing(self):
        self.text.insert("1.0", "line one\nline two")
        self.focus(self.text)
        self.key(self.text, "<Escape>")
        self.assertIs(self.root.focus_get(), self.entry)
        self.assertEqual(self.text.get("1.0", "end-1c"), "line one\nline two")
        # The window's own Escape handler never sees it — nothing can close.
        self.assertEqual(self.window_escapes, [])

    def test_tab_still_types_a_tab_and_control_tab_leaves(self):
        self.focus(self.text)
        self.key(self.text, "<Tab>")
        self.assertIs(self.root.focus_get(), self.text)
        self.assertEqual(self.text.get("1.0", "end-1c"), "\t")
        self.key(self.text, "<Control-Tab>")
        self.assertIs(self.root.focus_get(), self.entry)

    def test_escape_leaves_an_entry_a_spinbox_and_a_combobox_too(self):
        for field, following in ((self.entry, self.spin), (self.spin, self.combo),
                                 (self.combo, self.after)):
            self.focus(field)
            self.key(field, "<Escape>")
            self.assertIs(self.root.focus_get(), following, field.winfo_class())
        self.assertEqual(self.window_escapes, [])

    def test_escape_on_a_button_still_reaches_the_window(self):
        # Only FIELDS swallow Escape; a dialog that closes on Escape (Safety,
        # confirm-command) still gets it from its buttons and checkboxes.
        self.focus(self.after)
        self.key(self.after, "<Escape>")
        self.assertEqual(len(self.window_escapes), 1)


class ReturnPressesButtonTests(_TkCase):

    def setUp(self):
        super().setUp()
        keyboard.install_class_bindings(self.root)
        self.pressed = []
        self.button = tk.Button(self.root, text="Go", command=lambda: self.pressed.append("go"))
        self.button.pack()
        self.settle()

    def test_return_presses_the_focused_button(self):
        # (<KP_Enter> is bound alongside for macOS / X11; on Windows Tk 8.6 the
        # keypad Enter already arrives as Return, and a synthetic KP_Enter has
        # no keycode to match against, so it is not exercised here.)
        self.focus(self.button)
        self.key(self.button, "<Return>", seconds=0.25)  # tk::ButtonInvoke's 100-ms flash
        self.assertEqual(self.pressed, ["go"])

    def test_a_disabled_button_ignores_return(self):
        self.button.config(state="disabled")
        self.focus(self.button)
        self.key(self.button, "<Return>", seconds=0.25)
        self.assertEqual(self.pressed, [])


class MnemonicTests(_TkCase):

    def setUp(self):
        super().setUp()
        self.pressed = []
        self.button = tk.Button(
            self.root, text="Instruction", command=lambda: self.pressed.append("instruction"),
        )
        self.button.pack()
        self.flag = tk.BooleanVar(value=False)
        self.check = tk.Checkbutton(self.root, text="Show Thinking", variable=self.flag)
        self.check.pack()
        self.entry = tk.Entry(self.root)
        self.entry.pack()
        self.text = tk.Text(self.root, height=2)
        self.text.pack()
        self.combo = ttk.Combobox(self.root, values=["x", "y"], state="readonly")
        self.combo.pack()
        self.settle()
        keyboard.bind_mnemonics(self.root, {
            "i": self.button, "h": self.check, "c": self.entry, "e": self.text, "p": self.combo,
        })

    def test_no_underline_cue_is_drawn(self):
        # The user prefers plain buttons: the letters live in the README, not
        # on the widgets (-1 is Tk's "no underline").
        self.assertEqual(int(self.button.cget("underline")), -1)
        self.assertEqual(int(self.check.cget("underline")), -1)

    def test_alt_letter_presses_toggles_or_focuses_from_anywhere_in_the_window(self):
        self.focus(self.entry)
        self.key(self.entry, "<Alt-i>", seconds=0.25)
        self.assertEqual(self.pressed, ["instruction"])
        self.key(self.entry, "<Alt-h>")
        self.assertTrue(self.flag.get())
        self.focus(self.text)
        self.key(self.text, "<Alt-c>")
        self.assertIs(self.root.focus_get(), self.entry)
        self.key(self.entry, "<Alt-E>")  # case-insensitive
        self.assertIs(self.root.focus_get(), self.text)

    def test_a_combobox_target_is_focused(self):
        self.focus(self.entry)
        self.key(self.entry, "<Alt-p>")
        self.assertIs(self.root.focus_get(), self.combo)

    def test_a_disabled_button_and_an_unbound_letter_do_nothing(self):
        self.button.config(state="disabled")
        self.focus(self.entry)
        self.key(self.entry, "<Alt-i>", seconds=0.25)
        self.key(self.entry, "<Alt-z>")
        self.assertEqual(self.pressed, [])
        self.assertIs(self.root.focus_get(), self.entry)

    def test_duplicate_letters_are_rejected(self):
        with self.assertRaises(ValueError):
            keyboard.bind_mnemonics(self.root, {"a": self.button, "A": self.entry})


class EmbeddedCheckbuttonTests(_TkCase):

    def setUp(self):
        super().setUp()
        self.text = tk.Text(self.root, height=3, width=20)
        self.text.pack()
        self.cbs = []
        for i in range(12):
            cb = tk.Checkbutton(self.text, text=f"pattern {i}")
            self.text.window_create("end", window=cb)
            self.text.insert("end", "\n")
            self.cbs.append(cb)
        self.text.configure(state="disabled")
        keyboard.link_embedded_checkbuttons(self.text, self.cbs)
        self.settle()

    def test_tab_and_down_walk_forward_scrolling_each_into_view(self):
        self.focus(self.cbs[0])
        # Scrolled out of the 3-line window: Tk's own Tab traversal skips it.
        self.assertFalse(self.cbs[8].winfo_viewable())
        for i in range(8):
            self.key(self.root.focus_get(), "<Tab>" if i % 2 else "<Down>")
        self.assertIs(self.root.focus_get(), self.cbs[8])
        self.assertTrue(self.cbs[8].winfo_viewable())

    def test_shift_tab_and_up_walk_backward_and_both_ends_wrap(self):
        self.focus(self.cbs[0])
        self.key(self.cbs[0], "<Shift-Tab>")
        self.assertIs(self.root.focus_get(), self.cbs[-1])
        self.assertTrue(self.cbs[-1].winfo_viewable())
        self.key(self.cbs[-1], "<Up>")
        self.assertIs(self.root.focus_get(), self.cbs[-2])
        self.key(self.cbs[-2], "<Down>")
        self.key(self.cbs[-1], "<Tab>")
        self.assertIs(self.root.focus_get(), self.cbs[0])


class WiringTests(unittest.TestCase):
    """Every window builder uses the helpers (a static scan, no Tk needed)."""

    def test_every_window_builder_is_wired(self):
        src = {
            name: (REPO / "myagent" / name).read_text(encoding="utf-8")
            for name in ("ui_mixin.py", "instructions_mixin.py",
                         "skills_mixin.py", "safety_mixin.py")
        }
        self.assertIn("install_class_bindings(self.root)", src["ui_mixin.py"])
        for name, text in src.items():
            self.assertIn("bind_mnemonics(", text, name)
        self.assertIn("link_embedded_checkbuttons(text_widget, cbs)", src["safety_mixin.py"])
        # The read-only panes stay Tab stops (a disabled widget is skipped otherwise).
        self.assertEqual(src["ui_mixin.py"].count("takefocus=1,"), 1)
        self.assertEqual(src["safety_mixin.py"].count("takefocus=1,"), 2)


if __name__ == "__main__":
    unittest.main()
