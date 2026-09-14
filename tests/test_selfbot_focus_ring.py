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

    def test_the_auto_toggle_keeps_its_explicit_zero(self):
        # Its colour IS its state: no ring area, on any platform.
        src = (REPO / "SelfBot.py").read_text(encoding="utf-8")
        start = src.index("self._auto_chat_btn = tk.Button(")
        self.assertIn("highlightthickness=0", src[start:start + 400])


if __name__ == "__main__":
    unittest.main()
