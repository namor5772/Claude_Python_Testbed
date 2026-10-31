"""Characterization tests for SelfBot's macOS button focus ring (2026-09-15).

Tk 9.0.3 on macOS 26 draws a focused tk.Button's own 1-px focus ring shifted
by the parent frame's offset: an L-shaped black line struck through the Skills
Manager's SAVE / DELETE / NEW (their frame sits at (10, 10)), no ring at all
on the main window's seven buttons. From 3 px Aqua draws the ring itself, in
the right place. SelfBot carries an in-file copy of keyboard.py's
`install_focus_ring_defaults` (its myagent import is the optional kind),
pinned to the original the way its other copies are, and calls it first in
setup_ui, before any button exists, because the option database only fills
options at creation; the Auto toggle's explicit highlightthickness=0 still
wins. tests/test_keyboard.py pins what the helper itself does per platform.

Since 2026-09-26 the same holds for `install_scrollbar_defaults`, the copy that
keeps SelfBot's six tk.Scrollbars out of the Tab order (Tk 9.0.3's Scrollbar
class binds Page Up / Page Down, which Tk's fallback focus rule counts as
focusable): byte-identical to keyboard.py's, called right after the focus-ring
one, before any scrollbar exists.

Since 2026-10-31 the same holds for `install_quit_handler` (and `is_aqua`, which
it calls): on the Mac, Tk's application-menu Quit calls ::tk::mac::Quit when
that command exists and plain exit when it does not — measured: Cmd+Q ended
SelfBot at once, WM_DELETE_WINDOW never ran, so the state file, the chat save,
the cost line and the peer's close were all skipped. The copies are
byte-identical to keyboard.py's and the call sits in __init__ right after the
WM_DELETE_WINDOW protocol, with the same handler. (SelfBot has no Alt+letter
mnemonics, so MyAgent's Command mnemonic layer of the same day does not apply.)

SelfBot is importable in-process (module import builds no Tk root); nothing
here needs a display.
"""

import inspect
import pathlib
import sys
import unittest

from myagent import keyboard

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

    def test_the_in_file_copy_is_byte_identical_to_keyboards(self):
        self.assertEqual(inspect.getsource(SelfBot.install_focus_ring_defaults),
                         inspect.getsource(keyboard.install_focus_ring_defaults))

    def test_it_is_installed_first_in_setup_ui_before_any_button_exists(self):
        src = (REPO / "SelfBot.py").read_text(encoding="utf-8")
        call = src.index("install_focus_ring_defaults(self.root)")
        self.assertLess(src.index("def setup_ui(self):"), call)
        self.assertLess(call, src.index("tk.Button("))
        self.assertEqual(src.count("install_focus_ring_defaults(self.root)"), 1)

    def test_the_scrollbar_copy_is_byte_identical_to_keyboards(self):
        self.assertEqual(inspect.getsource(SelfBot.install_scrollbar_defaults),
                         inspect.getsource(keyboard.install_scrollbar_defaults))

    def test_it_is_installed_next_in_setup_ui_before_any_scrollbar_exists(self):
        src = (REPO / "SelfBot.py").read_text(encoding="utf-8")
        ring = src.index("install_focus_ring_defaults(self.root)")
        call = src.index("install_scrollbar_defaults(self.root)")
        self.assertLess(ring, call)
        self.assertLess(call, src.index("tk.Scrollbar("))
        self.assertEqual(src.count("install_scrollbar_defaults(self.root)"), 1)

    def test_the_auto_toggle_keeps_its_explicit_zero(self):
        # Its colour IS its state: no ring area, on any platform.
        src = (REPO / "SelfBot.py").read_text(encoding="utf-8")
        start = src.index("self._auto_chat_btn = tk.Button(")
        self.assertIn("highlightthickness=0", src[start:start + 400])

    def test_the_quit_handler_copies_are_byte_identical_to_keyboards(self):
        self.assertEqual(inspect.getsource(SelfBot.install_quit_handler),
                         inspect.getsource(keyboard.install_quit_handler))
        self.assertEqual(inspect.getsource(SelfBot.is_aqua), inspect.getsource(keyboard.is_aqua))

    def test_cmd_q_is_installed_beside_the_close_protocol_with_the_same_handler(self):
        src = (REPO / "SelfBot.py").read_text(encoding="utf-8")
        protocol = src.index('self.root.protocol("WM_DELETE_WINDOW", self._on_close)')
        self.assertIn("install_quit_handler(self.root, self._on_close)", src[protocol:protocol + 400])
        self.assertEqual(src.count("install_quit_handler(self.root, self._on_close)"), 1)
        # No mnemonic layer to port: SelfBot binds no Alt+letter anywhere.
        self.assertNotIn("<Alt-", src)
        self.assertNotIn("bind_mnemonics", src)


if __name__ == "__main__":
    unittest.main()
