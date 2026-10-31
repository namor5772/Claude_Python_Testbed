"""Characterization tests for keyboard operation (myagent/keyboard.py, 2026-09-10).

MyAgent must be usable without a mouse. Tk already makes every control a Tab
stop; this module pins what keyboard.py adds on top:

* Escape inside a text box / entry / spinbox / combobox / tree list moves the
  focus to the next control and changes nothing — the safe way out of a Text
  widget, where Tab types a tab (Ctrl+Tab, Tk's own binding, still works too).
* Return presses the focused button, like Space; a disabled button ignores it.
* Alt+<letter> mnemonics press a button / toggle a checkbutton / focus a
  field, without drawing any underline cue on the widgets.
* Checkbuttons embedded in a scrolled Text (the Safety dialog) are walked with
  Tab / Shift+Tab / Down / Up, each scrolled into view first.
* On macOS every tk.Button created after `install_focus_ring_defaults` gets a
  3-px focus ring, the width from which Aqua draws it (Tk's own 1-px one is a
  stray black line on Tk 9.0.3 / macOS 26); other platforms are untouched.
* No tk.Scrollbar created after `install_scrollbar_defaults` is a Tab stop, on
  any platform (Tk 9.0.3 counted them as stops: on the Mac, Tab from the
  output pane landed on its scrollbar instead of Voice Setup).

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
        self.tree = ttk.Treeview(self.root, height=2)   # the Instructions list
        self.tree.pack()
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

    def test_escape_leaves_an_entry_a_spinbox_a_combobox_and_a_tree_too(self):
        for field, following in ((self.entry, self.spin), (self.spin, self.combo),
                                 (self.combo, self.tree), (self.tree, self.after)):
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

    def mn(self, letter):
        """The sequence that is this letter's mnemonic on THIS platform:
        Alt+letter, or on the Mac Cmd+letter — Cmd+Shift+letter for a letter
        macOS owns (the test's "c" and "h" are two of them)."""
        if keyboard.is_aqua(self.root):
            shift = "Shift-" if letter.lower() in keyboard.MAC_COMMAND_RESERVED else ""
            return f"<Command-{shift}{letter}>"
        return f"<Alt-{letter}>"

    def test_no_underline_cue_is_drawn(self):
        # The user prefers plain buttons: the letters live in the README, not
        # on the widgets. Tk's "no underline" reads back as -1 on Tk 8.6 and
        # as an empty string on Tk 9 (the macOS venv since 2026-09).
        for widget in (self.button, self.check):
            self.assertIn(str(widget.cget("underline")), ("-1", ""))

    def test_alt_letter_presses_toggles_or_focuses_from_anywhere_in_the_window(self):
        self.focus(self.entry)
        self.key(self.entry, self.mn("i"), seconds=0.25)
        self.assertEqual(self.pressed, ["instruction"])
        self.key(self.entry, self.mn("h"))
        self.assertTrue(self.flag.get())
        self.focus(self.text)
        self.key(self.text, self.mn("c"))
        self.assertIs(self.root.focus_get(), self.entry)
        self.key(self.entry, self.mn("E"))  # case-insensitive
        self.assertIs(self.root.focus_get(), self.text)

    def test_a_combobox_target_is_focused(self):
        self.focus(self.entry)
        self.key(self.entry, self.mn("p"))
        self.assertIs(self.root.focus_get(), self.combo)

    def test_a_disabled_button_and_an_unbound_letter_do_nothing(self):
        self.button.config(state="disabled")
        self.focus(self.entry)
        self.key(self.entry, self.mn("i"), seconds=0.25)
        self.key(self.entry, self.mn("y"))
        self.assertEqual(self.pressed, [])
        self.assertIs(self.root.focus_get(), self.entry)

    def test_duplicate_letters_are_rejected(self):
        with self.assertRaises(ValueError):
            keyboard.bind_mnemonics(self.root, {"a": self.button, "A": self.entry})

    def test_the_binding_is_command_on_the_mac_and_alt_elsewhere(self):
        seq = keyboard.mnemonic_sequence(self.root)
        self.assertEqual(seq, "<Command-KeyPress>" if keyboard.is_aqua(self.root) else "<Alt-KeyPress>")
        self.assertTrue(self.root.bind(seq))
        other = "<Alt-KeyPress>" if seq.startswith("<Command") else "<Command-KeyPress>"
        self.assertFalse(self.root.bind(other))

    def test_on_the_mac_a_reserved_letter_without_shift_is_left_to_the_system(self):
        # Cmd+C in a text box must still copy (and Cmd+M minimise): the plain
        # combination reaches Tk's bindings too, so the mnemonic steps aside
        # and Cmd+Shift+C is the window's "c".
        if not keyboard.is_aqua(self.root):
            self.skipTest("the reserved letters are a macOS rule")
        self.focus(self.text)
        self.key(self.text, "<Command-c>")
        self.assertIs(self.root.focus_get(), self.text)
        self.key(self.text, "<Command-Shift-C>")
        self.assertIs(self.root.focus_get(), self.entry)
        self.key(self.entry, "<Command-e>")                     # not reserved: no Shift needed
        self.assertIs(self.root.focus_get(), self.text)


class MnemonicLetterTests(unittest.TestCase):
    """The pure rule behind the handler, no Tk: which letter an event asks for."""

    def test_windows_takes_every_letter_shift_or_not(self):
        for keysym, state in (("c", 0), ("C", keyboard.SHIFT_MASK), ("m", 0), ("s", 0)):
            self.assertEqual(keyboard.mnemonic_letter(keysym, state, aqua=False), keysym.lower())

    def test_the_mac_leaves_a_reserved_letter_to_the_system_unless_shifted(self):
        for letter in sorted(keyboard.MAC_COMMAND_RESERVED):
            self.assertIsNone(keyboard.mnemonic_letter(letter, 0, aqua=True), letter)
            self.assertIsNone(keyboard.mnemonic_letter(letter, 0x8, aqua=True), letter)   # Command alone
            self.assertEqual(keyboard.mnemonic_letter(letter.upper(), 0x9, aqua=True), letter)
        for letter in "sdeiokl":                                  # the rest: Shift optional
            self.assertEqual(keyboard.mnemonic_letter(letter, 0x8, aqua=True), letter)
            self.assertEqual(keyboard.mnemonic_letter(letter.upper(), 0x9, aqua=True), letter)

    def test_the_reserved_set_is_what_macos_and_tk_own(self):
        # Edit menu (a c v x z), Close (w), Quit (q), Minimize (m), Hide (h) —
        # and it covers every letter MyAgent's windows use among them.
        self.assertEqual(keyboard.MAC_COMMAND_RESERVED, frozenset("acvxzwqmh"))
        self.assertEqual(keyboard.SHIFT_MASK, 1)


class QuitHandlerTests(unittest.TestCase):
    """Cmd+Q runs the close handler on the Mac; nothing is installed elsewhere."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()

    def tearDown(self):
        self.root.destroy()

    def test_the_mac_gets_tk_mac_quit_bound_to_the_handler(self):
        calls = []
        installed = keyboard.install_quit_handler(self.root, lambda: calls.append("close"))
        exists = self.root.tk.splitlist(self.root.tk.call("info", "commands", "::tk::mac::Quit"))
        if keyboard.is_aqua(self.root):
            self.assertTrue(installed)
            self.assertEqual(exists, ("::tk::mac::Quit",))
            self.root.tk.call("::tk::mac::Quit")        # what Tk's Quit menu item calls
            self.assertEqual(calls, ["close"])
        else:
            self.assertFalse(installed)
            self.assertEqual(exists, ())
            self.assertEqual(calls, [])

    def test_the_list_moves_follow_the_platform(self):
        seqs = keyboard.list_move_sequences(self.root)
        if keyboard.is_aqua(self.root):
            self.assertEqual(seqs, ("<Command-Up>", "<Command-Down>"))
        else:
            self.assertEqual(seqs, ("<Alt-Up>", "<Alt-Down>"))


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


class FocusRingDefaultTests(unittest.TestCase):
    """On macOS a later tk.Button gets a ring Aqua draws; elsewhere nothing moves.

    The option database only fills options at creation, so the fix must run
    before the first button exists (the wiring scan pins that), an existing
    widget keeps its value, and an explicit thickness on a widget still wins.
    A withdrawn root is enough here: nothing needs the keyboard focus.
    """

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.aqua = self.root.tk.call("tk", "windowingsystem") == "aqua"
        self.before = tk.Button(self.root)  # exists before the call
        self.defaults = {
            cls: int(cls(self.root).cget("highlightthickness"))
            for cls in (tk.Button, tk.Checkbutton, tk.Text)
        }
        keyboard.install_focus_ring_defaults(self.root)

    def tearDown(self):
        self.root.destroy()

    def test_a_later_button_gets_3px_on_aqua_and_the_default_elsewhere(self):
        later = tk.Button(self.root)
        expected = 3 if self.aqua else self.defaults[tk.Button]
        self.assertEqual(int(later.cget("highlightthickness")), expected)

    def test_an_existing_button_and_an_explicit_thickness_are_left_alone(self):
        self.assertEqual(int(self.before.cget("highlightthickness")),
                         self.defaults[tk.Button])
        explicit = tk.Button(self.root, highlightthickness=1)
        self.assertEqual(int(explicit.cget("highlightthickness")), 1)

    def test_no_other_class_is_touched(self):
        for cls in (tk.Checkbutton, tk.Text):
            self.assertEqual(int(cls(self.root).cget("highlightthickness")),
                             self.defaults[cls], cls.__name__)


class ScrollbarDefaultTests(unittest.TestCase):
    """A later tk.Scrollbar is no Tab stop: Tab goes from a Text to the button after it.

    Tk's fallback rule (`tk::FocusOK`) makes a scrollbar a stop wherever its
    class carries a Key binding, as Tk 9.0.3's does (Page Up / Page Down):
    the main window's Tab-order test in test_voice.py had failed on the Mac
    since it was written (2026-09-20). The root must be MAPPED (transparent
    here) because the rule also asks whether a widget is viewable, and a
    withdrawn window's widgets never are; nothing needs the keyboard focus.
    """

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.geometry("240x240+0+0")
        self.root.attributes("-alpha", 0.0)
        self.before = tk.Scrollbar(self.root)   # exists before the call
        keyboard.install_scrollbar_defaults(self.root)

    def tearDown(self):
        self.root.destroy()

    def test_a_later_scrollbar_takes_no_focus_and_tab_skips_it(self):
        # Small and in a row, so all three fit the window: the rule also asks
        # whether a widget is viewable, and one pushed outside is not.
        text = tk.Text(self.root, width=10, height=2)
        bar = tk.Scrollbar(self.root, command=text.yview)
        button = tk.Button(self.root)
        for widget in (text, bar, button):
            widget.pack(side="left")
        self.root.update()
        self.assertEqual(str(bar.cget("takefocus")), "0")
        self.assertIs(text.tk_focusNext(), button)

    def test_an_existing_scrollbar_and_an_explicit_value_are_left_alone(self):
        self.assertEqual(str(self.before.cget("takefocus")), "")
        explicit = tk.Scrollbar(self.root, takefocus=1)
        self.assertEqual(str(explicit.cget("takefocus")), "1")


class WiringTests(unittest.TestCase):
    """Every window builder uses the helpers (a static scan, no Tk needed)."""

    def test_every_window_builder_is_wired(self):
        src = {
            name: (REPO / "myagent" / name).read_text(encoding="utf-8")
            for name in ("ui_mixin.py", "instructions_mixin.py",
                         "skills_mixin.py", "safety_mixin.py", "voice_mixin.py")
        }
        self.assertIn("install_class_bindings(self.root)", src["ui_mixin.py"])
        # The macOS focus-ring default is installed before the first button
        # exists (the option database only fills options at creation).
        self.assertLess(src["ui_mixin.py"].index("install_focus_ring_defaults(self.root)"),
                        src["ui_mixin.py"].index("tk.Button("))
        # ... and no scrollbar is a Tab stop: that default too goes in before
        # the first one exists.
        self.assertLess(src["ui_mixin.py"].index("install_scrollbar_defaults(self.root)"),
                        src["ui_mixin.py"].index("tk.Scrollbar("))
        for name, text in src.items():
            self.assertIn("bind_mnemonics(", text, name)
        self.assertIn("link_embedded_checkbuttons(text_widget, cbs)", src["safety_mixin.py"])
        # The Mac has no Alt modifier: every mnemonic goes through
        # bind_mnemonics (which picks Command there) and the lists' moves
        # through list_move_sequences — no literal <Alt-…> binding anywhere.
        package = {p.name: p.read_text(encoding="utf-8") for p in (REPO / "myagent").glob("*.py")}
        for name, text in package.items():
            if name != "keyboard.py":
                self.assertNotIn("<Alt-", text, name)
        self.assertIn("list_move_sequences(tree)", package["sectioned_list.py"])
        # Cmd+Q is the same close as [X]: the Quit hook sits beside the
        # WM_DELETE_WINDOW protocol, with the same handler.
        entry = (REPO / "MyAgent.py").read_text(encoding="utf-8")
        protocol = entry.index('protocol("WM_DELETE_WINDOW", self._on_close)')
        self.assertIn("install_quit_handler(self.root, self._on_close)", entry[protocol:protocol + 400])
        # The read-only panes stay Tab stops (a disabled widget is skipped otherwise).
        self.assertEqual(src["ui_mixin.py"].count("takefocus=1,"), 1)
        self.assertEqual(src["safety_mixin.py"].count("takefocus=1,"), 2)
        self.assertEqual(src["voice_mixin.py"].count("takefocus=1,"), 1)   # Voice Setup's test transcript


if __name__ == "__main__":
    unittest.main()
