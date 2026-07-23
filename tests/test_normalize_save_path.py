"""Characterization tests for normalize_save_path in myagent/helpers.py:
the guard against model-hallucinated wrong-OS save paths (a grok run wrote a
Gmail attachment to C:\\Users\\...\\Temp\\ on macOS on 2026-07-23, creating a
literal 'C:' directory tree in the repo). POSIX-only behaviour is tested —
the redirect branch is a no-op on Windows (os.name == 'nt')."""

import os
import unittest

from myagent.helpers import normalize_save_path

HOME = "/Users/roman"
POSIX = os.name != "nt"


class TestNormalizeSavePath(unittest.TestCase):
    def test_plain_posix_path_unchanged(self):
        path, note = normalize_save_path("/Users/roman/Temp/a.pdf", home=HOME)
        self.assertEqual(path, "/Users/roman/Temp/a.pdf")
        self.assertEqual(note, "")

    def test_relative_path_unchanged(self):
        path, note = normalize_save_path("saved_chats/a.pdf", home=HOME)
        self.assertEqual(path, "saved_chats/a.pdf")
        self.assertEqual(note, "")

    def test_tilde_expanded(self):
        path, note = normalize_save_path("~/Temp/a.pdf")
        self.assertEqual(path, os.path.expanduser("~/Temp/a.pdf"))
        self.assertEqual(note, "")

    @unittest.skipUnless(POSIX, "redirect branch is POSIX-only")
    def test_windows_backslash_path_redirected(self):
        path, note = normalize_save_path(
            r"C:\Users\roman\AppData\Local\Temp\INV08595169.pdf", home=HOME)
        self.assertEqual(path, "/Users/roman/Temp/INV08595169.pdf")
        self.assertIn("Windows path", note)
        self.assertIn(path, note)

    @unittest.skipUnless(POSIX, "redirect branch is POSIX-only")
    def test_windows_forwardslash_path_redirected(self):
        path, note = normalize_save_path(
            "C:/Users/roman/AppData/Local/Temp/INV08595169.pdf", home=HOME)
        self.assertEqual(path, "/Users/roman/Temp/INV08595169.pdf")
        self.assertNotEqual(note, "")

    @unittest.skipUnless(POSIX, "redirect branch is POSIX-only")
    def test_other_drive_letter_redirected(self):
        path, note = normalize_save_path(r"D:\stuff\report.docx", home=HOME)
        self.assertEqual(path, "/Users/roman/Temp/report.docx")
        self.assertNotEqual(note, "")

    @unittest.skipUnless(POSIX, "redirect branch is POSIX-only")
    def test_bare_drive_root_gets_fallback_name(self):
        path, note = normalize_save_path("C:/", home=HOME)
        self.assertEqual(path, "/Users/roman/Temp/attachment.bin")
        self.assertNotEqual(note, "")


if __name__ == "__main__":
    unittest.main()
