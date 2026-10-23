"""The spoken "Agent Request" cue (2026-10-23).

Until that day the Agent Request dialog, opened with the Auto-send box ticked,
rang the display bell as the cue to start speaking; the user asked for MyAgent
to say "Agent Request" aloud instead. `myagent/speech.py` is the one speaker —
`speak(text)`: SAPI on a daemon thread (pywin32) else a hidden PowerShell
System.Speech child on Windows, `say` on macOS, spd-say / espeak elsewhere,
False where nothing can speak — and `SafetyMixin._announce_request(dlg)` says
the words or, when it cannot, rings the bell as before.

Pinned here: the words and the voice settings; every backend's exact launch
over fakes (no sound in the suite, nothing reaches a real voice); the
fallbacks and refusals; the announce helper's two branches; and a wiring scan
of the dialog — the cue after the focus, under the Auto-send gate, no bare
bell left on the dialog path. The real dialog is driven in
tests/test_agent_request_display.py with speech patched out.
"""

import pathlib
import re
import subprocess
import sys
import threading
import types
import unittest
from unittest import mock

from myagent import speech
from myagent.constants import _SUBPROCESS_NOWND
from myagent.safety_mixin import SafetyMixin

ROOT = pathlib.Path(__file__).resolve().parents[1]


class WordsTests(unittest.TestCase):
    def test_the_announcement_and_the_voice_settings(self):
        self.assertEqual(speech.AGENT_REQUEST_ANNOUNCEMENT, "Agent Request")
        self.assertEqual(speech.SPEECH_VOLUME, 100)          # as loud as the voice goes
        self.assertTrue(-10 <= speech.SPEECH_RATE <= 0)      # not faster than the default

    def test_nothing_is_said_for_an_empty_text(self):
        with mock.patch.object(speech, "_speak_sapi") as sapi, \
                mock.patch.object(speech, "_speak_powershell") as ps, \
                mock.patch.object(speech, "_speak_command") as cmd:
            self.assertFalse(speech.speak(""))
            self.assertFalse(speech.speak("   "))
            self.assertFalse(speech.speak(None))
        for backend in (sapi, ps, cmd):
            backend.assert_not_called()


class _FakeVoice:
    """A stand-in SAPI.SpVoice recording what the speaker sets and says."""
    def __init__(self, log):
        self.log = log
        self.Volume = 50
        self.Rate = 0

    def Speak(self, text):
        self.log.append(("speak", text, self.Volume, self.Rate, threading.current_thread().name))


class SapiTests(unittest.TestCase):
    def _fake_pywin32(self, log):
        pythoncom = types.ModuleType("pythoncom")
        pythoncom.CoInitialize = lambda: log.append(("coinit", threading.current_thread().name))
        pythoncom.CoUninitialize = lambda: log.append(("couninit",))
        win32com = types.ModuleType("win32com")
        client = types.ModuleType("win32com.client")
        client.Dispatch = lambda progid: (log.append(("dispatch", progid)), _FakeVoice(log))[1]
        win32com.client = client
        return {"pythoncom": pythoncom, "win32com": win32com, "win32com.client": client}

    def _run_threads(self):
        for t in threading.enumerate():
            if t.name == "speak":
                t.join(timeout=5)

    def test_sapi_speaks_on_its_own_thread_in_its_own_apartment_at_full_volume(self):
        log = []
        with mock.patch.dict(sys.modules, self._fake_pywin32(log)):
            self.assertTrue(speech._speak_sapi("Agent Request"))
            self._run_threads()
        kinds = [entry[0] for entry in log]
        self.assertEqual(kinds, ["coinit", "dispatch", "speak", "couninit"])
        self.assertEqual(log[1], ("dispatch", "SAPI.SpVoice"))
        _k, text, volume, rate, thread = log[2]
        self.assertEqual((text, volume, rate), ("Agent Request", 100, speech.SPEECH_RATE))
        # Spoken on the daemon thread, never the caller's (the Tk thread).
        self.assertEqual(thread, "speak")
        self.assertEqual(log[0][1], "speak")

    def test_a_voice_that_fails_is_swallowed_and_the_apartment_still_closed(self):
        log = []
        modules = self._fake_pywin32(log)

        def boom(progid):
            log.append(("dispatch", progid))
            raise OSError("no voice installed")

        modules["win32com.client"].Dispatch = boom
        with mock.patch.dict(sys.modules, modules):
            self.assertTrue(speech._speak_sapi("Agent Request"))   # handed off; the failure is later
            self._run_threads()
        self.assertEqual([e[0] for e in log], ["coinit", "dispatch", "couninit"])

    def test_without_pywin32_sapi_declines(self):
        with mock.patch.dict(sys.modules, {"pythoncom": None, "win32com": None,
                                           "win32com.client": None}):
            self.assertFalse(speech._speak_sapi("Agent Request"))


class PowerShellTests(unittest.TestCase):
    def test_a_hidden_system_speech_child_with_the_text_as_an_argument(self):
        with mock.patch.object(speech.shutil, "which", return_value=r"C:\ps\powershell.exe"), \
                mock.patch.object(speech.subprocess, "Popen") as popen:
            self.assertTrue(speech._speak_powershell("Agent Request"))
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        kwargs = popen.call_args.kwargs
        self.assertEqual(argv[0], r"C:\ps\powershell.exe")
        self.assertIn("-NonInteractive", argv)
        self.assertEqual(argv[-1], "Agent Request")              # the text is an ARGUMENT, never
        script = argv[argv.index("-Command") + 1]                # interpolated into the script
        self.assertIn("System.Speech.Synthesis.SpeechSynthesizer", script)
        self.assertIn("$s.Volume = 100", script)
        self.assertIn(f"$s.Rate = {speech.SPEECH_RATE}", script)
        self.assertIn("$s.Speak([string]$args[0])", script)
        self.assertNotIn("Agent Request", script)
        for key, value in _SUBPROCESS_NOWND.items():
            self.assertEqual(kwargs[key], value)                 # no console flash
        self.assertEqual(kwargs["stdin"], subprocess.DEVNULL)

    def test_no_powershell_or_a_failed_launch_declines(self):
        with mock.patch.object(speech.shutil, "which", return_value=None):
            self.assertFalse(speech._speak_powershell("Agent Request"))
        with mock.patch.object(speech.shutil, "which", return_value="powershell.exe"), \
                mock.patch.object(speech.subprocess, "Popen", side_effect=OSError("gone")):
            self.assertFalse(speech._speak_powershell("Agent Request"))


class PlatformTests(unittest.TestCase):
    def test_windows_tries_sapi_then_powershell(self):
        with mock.patch.object(speech, "IS_WINDOWS", True), \
                mock.patch.object(speech, "_speak_sapi", return_value=True) as sapi, \
                mock.patch.object(speech, "_speak_powershell", return_value=True) as ps:
            self.assertTrue(speech.speak("Agent Request"))
        sapi.assert_called_once_with("Agent Request")
        ps.assert_not_called()
        with mock.patch.object(speech, "IS_WINDOWS", True), \
                mock.patch.object(speech, "_speak_sapi", return_value=False), \
                mock.patch.object(speech, "_speak_powershell", return_value=False) as ps:
            self.assertFalse(speech.speak("Agent Request"))
        ps.assert_called_once_with("Agent Request")

    def test_macos_uses_say(self):
        with mock.patch.object(speech, "IS_WINDOWS", False), \
                mock.patch.object(speech.sys, "platform", "darwin"), \
                mock.patch.object(speech.subprocess, "Popen") as popen:
            self.assertTrue(speech.speak("Agent Request"))
        self.assertEqual(popen.call_args.args[0], ["say", "Agent Request"])
        self.assertEqual(popen.call_args.kwargs["stdout"], subprocess.DEVNULL)

    def test_elsewhere_uses_spd_say_or_espeak_or_declines(self):
        found = {"spd-say": None, "espeak": "/usr/bin/espeak"}
        with mock.patch.object(speech, "IS_WINDOWS", False), \
                mock.patch.object(speech.sys, "platform", "linux"), \
                mock.patch.object(speech.shutil, "which", side_effect=found.get), \
                mock.patch.object(speech.subprocess, "Popen") as popen:
            self.assertTrue(speech.speak("Agent Request"))
        self.assertEqual(popen.call_args.args[0], ["/usr/bin/espeak", "Agent Request"])
        with mock.patch.object(speech, "IS_WINDOWS", False), \
                mock.patch.object(speech.sys, "platform", "linux"), \
                mock.patch.object(speech.shutil, "which", return_value=None), \
                mock.patch.object(speech.subprocess, "Popen") as popen:
            self.assertFalse(speech.speak("Agent Request"))
        popen.assert_not_called()

    def test_a_launch_that_fails_declines(self):
        with mock.patch.object(speech, "IS_WINDOWS", False), \
                mock.patch.object(speech.sys, "platform", "darwin"), \
                mock.patch.object(speech.subprocess, "Popen", side_effect=OSError("no say")):
            self.assertFalse(speech.speak("Agent Request"))


class AnnounceTests(unittest.TestCase):
    def test_the_words_are_spoken_and_the_bell_stays_quiet(self):
        dlg = mock.Mock()
        with mock.patch("myagent.safety_mixin.speak", return_value=True) as say:
            SafetyMixin._announce_request(dlg)
        say.assert_called_once_with("Agent Request")
        dlg.bell.assert_not_called()

    def test_where_nothing_can_speak_the_bell_rings_as_before(self):
        dlg = mock.Mock()
        with mock.patch("myagent.safety_mixin.speak", return_value=False):
            SafetyMixin._announce_request(dlg)
        dlg.bell.assert_called_once_with()


class WiringTests(unittest.TestCase):
    def test_the_dialog_announces_after_the_focus_under_the_auto_send_gate(self):
        src = (ROOT / "myagent" / "safety_mixin.py").read_text(encoding="utf-8")
        self.assertIn("from myagent.speech import AGENT_REQUEST_ANNOUNCEMENT, speak", src)
        prompt = src[src.index("def do_user_prompt("):src.index("def _announce_request(")]
        focus = "(mike_btn if hands_free else resp_text).focus_set()"
        self.assertIn(focus, prompt)
        gate = re.search(r"if hands_free:\n\s+self\._announce_request\(dlg\)", prompt)
        self.assertIsNotNone(gate)
        self.assertLess(prompt.index(focus), gate.start())
        # No bare bell on the dialog path: the fallback lives in the helper.
        self.assertNotIn("dlg.bell()", prompt)
        helper = src[src.index("def _announce_request("):src.index("def _take_prompt_images(")]
        self.assertIn("if not speak(AGENT_REQUEST_ANNOUNCEMENT):", helper)
        self.assertIn("dlg.bell()", helper)

    def test_helpers_stays_stdlib_only(self):
        # speech.py is its own module because helpers.py is imported by the
        # zero-token jobs and must not grow a pywin32 or constants dependency.
        helpers = (ROOT / "myagent" / "helpers.py").read_text(encoding="utf-8")
        self.assertNotIn("speech", helpers)
        self.assertNotIn("win32com", helpers)


if __name__ == "__main__":
    unittest.main()
