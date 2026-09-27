"""Regression tests for the 2026-09-28 review fixes in the safety layer.

- do_user_prompt / _request_confirmation never park the worker when the dialog
  cannot be built (a null `user_prompt` message raised inside the builder,
  leaving event.wait() blocked for good and an invisible grab on the main
  window, STOP included);
- _check_command_safety: unticking a pattern bypasses THAT pattern only;
- the Windows block list's registry / reg.exe / shutdown patterns match the
  forms PowerShell really uses, and the alias patterns fire only at command
  position;
- run_powershell decodes PowerShell's output as UTF-8 (a "ü" used to empty the
  whole stdout: "[No output]");
- camera_capture never overwrites a file created while the photo was taken.
"""

import gc
import os
import queue
import shutil
import tempfile
import threading
import tkinter as tk
import unittest
from unittest import mock

from myagent import safety_mixin
from myagent.constants import IS_WINDOWS
from myagent.physical_mixin import PhysicalMixin
from myagent.safety_mixin import SafetyMixin
from tests._util import stub
from tests.test_agent_request_display import DISMISSED, _DialogHost, _walk


class ConfirmPatternTests(unittest.TestCase):
    """The rule on its own, with a pattern list of our choosing (any OS)."""

    PATTERNS = [r"\bRemove-Item\b", r"(?<!\S)-Recurse\b", r"(?<!\S)-Force\b"]

    def check(self, command, disabled=()):
        host = stub(SafetyMixin, _disabled_confirm_patterns=set(disabled))
        with mock.patch.object(safety_mixin, "COMMAND_BLOCKED", []), \
                mock.patch.object(safety_mixin, "COMMAND_CONFIRM", self.PATTERNS):
            return host._check_command_safety(command)

    def test_an_unticked_pattern_does_not_bypass_a_ticked_one(self):
        self.assertEqual(self.check(r"Remove-Item C:\x -Recurse", {r"\bRemove-Item\b"}),
                         ("confirm", r"(?<!\S)-Recurse\b"))

    def test_skipped_only_when_every_matching_pattern_is_unticked(self):
        self.assertEqual(self.check(r"Remove-Item C:\x -Recurse",
                                    {r"\bRemove-Item\b", r"(?<!\S)-Recurse\b"}),
                         ("skipped", r"\bRemove-Item\b"))

    def test_all_ticked_confirms_on_the_first_match(self):
        self.assertEqual(self.check(r"Remove-Item C:\x -Force"),
                         ("confirm", r"\bRemove-Item\b"))

    def test_no_match_is_safe(self):
        self.assertEqual(self.check("Get-ChildItem"), ("safe", ""))


@unittest.skipUnless(IS_WINDOWS, "the PowerShell pattern lists are Windows-only")
class WindowsPatternTests(unittest.TestCase):

    @staticmethod
    def classify(command):
        host = stub(SafetyMixin, _disabled_confirm_patterns=set())
        return host._check_command_safety(command)[0]

    def test_registry_deletes_reg_exe_and_shutdown_are_blocked(self):
        for cmd in (r'Remove-ItemProperty -Path "HKLM:\SOFTWARE\X" -Name Y',
                    r"Remove-Item HKCU:\Software\X -Recurse",
                    r"Remove-Item -Path Registry::HKEY_LOCAL_MACHINE\SOFTWARE\X",
                    r"reg.exe delete HKCU\Software\X /f",
                    r"reg delete HKCU\Software\X /f",
                    "shutdown /s /t 0",
                    "Get-Date; shutdown.exe -r -t 5"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.classify(cmd), "blocked")

    def test_what_the_block_patterns_leave_alone(self):
        self.assertEqual(self.classify("shutdown /a"), "safe")
        self.assertEqual(self.classify(
            "Get-WinEvent -LogName System | Where-Object { $_.Message -match 'shutdown' }"),
            "safe")
        # A file NAMED after a hive is only a Remove-Item (confirm), not a block.
        self.assertEqual(self.classify(r"Remove-Item C:\temp\HKLM_notes.txt"), "confirm")

    def test_aliases_confirm_at_command_position(self):
        for cmd in (r"ri C:\data -r -fo", "erase x.txt", "mv a.txt b.txt",
                    "Get-Date; move a b", "ren a.txt b.txt", "sc notes.txt hi",
                    "clc log.txt", "spps -Name notepad", "saps notepad",
                    "iwr https://example.com/y -OutFile y.zip",
                    "iex(Get-Content a.ps1 -Raw)",
                    r"Remove-ItemProperty -Path HKCR:\x -Name y",
                    "Get-ChildItem *.tmp | ri",
                    "Get-Date\nmi a.txt b.txt"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.classify(cmd), "confirm")

    def test_aliases_do_not_fire_inside_strings_or_paths(self):
        for cmd in ('Write-Output "move along, nothing to see"',
                    "sc.exe query spooler",
                    r"Get-Content C:\notes\mv\readme.txt",
                    "Start-Sleep 1"):
            with self.subTest(cmd=cmd):
                self.assertEqual(self.classify(cmd), "safe")


@unittest.skipUnless(IS_WINDOWS, "PowerShell's OEM output code page is a Windows matter")
class PowerShellEncodingTests(unittest.TestCase):

    @staticmethod
    def run_ps(command):
        host = stub(SafetyMixin, _disabled_confirm_patterns=set(), queue=queue.Queue())
        return host.run_powershell(command, timeout=60)

    def test_non_ascii_output_arrives_intact(self):
        out = self.run_ps("Write-Output 'line one'; "
                          "Write-Output ('Gr' + [char]0xFC + 'n'); "      # ü killed stdout
                          "Write-Output ('caf' + [char]0xE9); "           # é came back as ‚
                          "Write-Output ([char]0x4E2D)")                  # outside the OEM page
        self.assertEqual(out.splitlines(), ["line one", "Grün", "café", "中"])

    def test_an_error_still_reaches_the_model(self):
        out = self.run_ps("Get-Item C:\\no\\such\\path\\anywhere")
        self.assertIn("STDERR", out)
        self.assertIn("[Exit code: 1]", out)


class _BrokenUpgradeRowHost(_DialogHost):
    """A host whose dialog raises half-way through being built — after the
    grab (so the recovery must release it), before the dialog is shown."""
    _headless = False

    def _upgrade_build_row(self, dlg):
        raise RuntimeError("boom")


class _BrokenConfirmHost(_DialogHost):
    _headless = False
    _confirm_dialog = None

    def _place_window(self, win, kind, default_size, **_kwargs):
        raise RuntimeError("boom")


class DialogRecoveryTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.geometry("240x240+0+0")
        self.root.attributes("-alpha", 0.0)

    def tearDown(self):
        # Collect the dialogs' reference cycles here, on the main thread,
        # before the root goes (see tests/test_agent_request_display.py).
        self.host = None
        gc.collect()
        self.root.destroy()

    def on_worker(self, host, call, act=None):
        """Run call() on a worker thread while the main thread sits in a real
        mainloop, as stream_worker does; act(dialog) once a prompt dialog is
        on screen. Returns call()'s result — failing if the worker is still
        parked when the watchdog fires."""
        self.host = host
        outcome = {}
        worker = threading.Thread(target=lambda: outcome.setdefault("result", call()),
                                  daemon=True)

        def wait_for_dialog():
            dialog = getattr(host, "_prompt_dialog", None)
            if dialog is not None and dialog.winfo_ismapped():
                act(dialog)
            elif worker.is_alive():
                self.root.after(20, wait_for_dialog)

        def wait_for_worker():
            if worker.is_alive():
                self.root.after(20, wait_for_worker)
            else:
                self.root.quit()

        self.root.after(0, worker.start)
        if act is not None:
            self.root.after(20, wait_for_dialog)
        self.root.after(40, wait_for_worker)
        watchdog = self.root.after(15000, self.root.quit)
        self.root.mainloop()
        self.root.after_cancel(watchdog)
        self.assertFalse(worker.is_alive(), "the worker is still parked on the dialog")
        return outcome["result"]

    @staticmethod
    def queued(host):
        items = []
        while not host.queue.empty():
            items.append(host.queue.get_nowait())
        return items

    def test_a_null_message_opens_the_dialog_with_an_empty_message(self):
        shown = []

        def act(dialog):
            shown.append([w for w in _walk(dialog) if isinstance(w, tk.Text)][0]
                         .get("1.0", "end-1c"))
            dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))

        host = _DialogHost(self.root)
        reply = self.on_worker(host, lambda: host.do_user_prompt(None), act)
        self.assertEqual(reply, DISMISSED)
        self.assertEqual(shown, [""])
        self.assertEqual(self.queued(host),
                         [{"type": "user_prompt_request", "content": ""}])

    def test_a_dialog_that_fails_to_build_releases_the_worker_and_its_grab(self):
        host = _BrokenUpgradeRowHost(self.root)
        reply = self.on_worker(host, lambda: host.do_user_prompt("Which city?"))
        self.assertEqual(reply, "")          # stops the run on both callers
        self.assertIsNone(host._prompt_dialog)
        self.assertIsNone(self.root.grab_current())
        warnings = [m["content"] for m in self.queued(host) if m["type"] == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("could not be shown", warnings[0])
        self.assertIn("boom", warnings[0])

    def test_a_confirmation_dialog_that_fails_to_build_denies(self):
        host = _BrokenConfirmHost(self.root)
        allowed = self.on_worker(host, lambda: host._request_confirmation("Remove-Item x"))
        self.assertIs(allowed, False)
        self.assertIsNone(host._confirm_dialog)
        self.assertIsNone(self.root.grab_current())
        warnings = [m["content"] for m in self.queued(host) if m["type"] == "warning"]
        self.assertEqual(len(warnings), 1)
        self.assertIn("denied", warnings[0])


class _FakeCv2:
    IMWRITE_JPEG_QUALITY = 1

    class _Encoded:
        @staticmethod
        def tobytes():
            return b"PHOTO"

    @classmethod
    def imencode(cls, ext, frame, params):
        return True, cls._Encoded()


class _Frame:
    shape = (1080, 1920, 3)


class CameraSaveTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def test_a_file_created_before_the_write_is_never_replaced(self):
        path = os.path.join(self.dir, "photo.jpg")
        with open(path, "wb") as f:           # appeared during the self-timer
            f.write(b"USER DATA")
        note = stub(PhysicalMixin)._camera_save(_FakeCv2, _Frame(), path)
        self.assertIn("NOT saved", note)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"USER DATA")

    def test_a_free_path_is_written(self):
        path = os.path.join(self.dir, "photo.jpg")
        note = stub(PhysicalMixin)._camera_save(_FakeCv2, _Frame(), path)
        self.assertIn("Saved at full resolution", note)
        with open(path, "rb") as f:
            self.assertEqual(f.read(), b"PHOTO")

    @unittest.skipIf(os.name == "nt", "a drive-letter path is valid on Windows")
    def test_the_redirect_note_names_the_cameras_own_argument(self):
        _path, note, _error = PhysicalMixin._camera_save_target(
            r"C:\Users\x\photo.jpg", home=self.dir)
        self.assertIn("save_path", note)
        self.assertNotIn("save_to", note)


if __name__ == "__main__":
    unittest.main()
