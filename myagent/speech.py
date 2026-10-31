"""Spoken announcements — the Agent Request dialog's "Agent Request" cue.

One function, ``speak(text)``: says the text aloud through the platform's own
text-to-speech, at full voice volume, without holding the calling thread.
It returns True when speech was handed off and False when nothing on this
machine can speak — the caller then rings the bell, as the dialog did before
2026-10-23 — so a user who cannot hear speech still gets a cue. The words
are here too: the opening cue and, since 2026-10-31, the reminders the
dialog repeats every 15 s until it is answered (``reminder_text``).

Why it is its own module: ``helpers.py`` must stay stdlib-only (the zero-token
Heartbeat / UnreadSummary jobs import it), and the Windows path prefers the
optional ``pywin32`` package the Excel tools already pull in.

Backends, in order:

* Windows — SAPI (``SAPI.SpVoice``) in-process on a daemon thread with its own
  COM apartment: ~no start-up cost, the voice object lives exactly as long as
  the utterance. Without pywin32 a hidden Windows PowerShell child uses
  ``System.Speech`` (``CREATE_NO_WINDOW``, the repo's no-console-flash rule).
* macOS — the ``say`` command.
* Elsewhere — ``spd-say`` or ``espeak`` when one is on PATH.

Loudness: a voice is set to its maximum (100) and spoken a touch slower than
the default, so two words land clearly; nothing here can exceed the machine's
master volume, and nothing here changes it.
"""

import shutil
import subprocess
import sys
import threading

from myagent.constants import IS_WINDOWS, _SUBPROCESS_NOWND

# The words the Agent Request dialog says when it opens on Mike.
AGENT_REQUEST_ANNOUNCEMENT = "Agent Request"

# The reminders it repeats while nobody has answered (2026-10-31, the user's
# request): the first repeat, then every later one — see reminder_text and
# SafetyMixin._remind_request, which paces them at REQUEST_REMINDER_MS.
AGENT_REQUEST_REMINDERS = ("Please Respond", "Please Respond Now")


def reminder_text(repeat):
    """The words of the dialog's ``repeat``-th reminder (0 is the first).

    The first repeat is "Please Respond"; every one after it "Please Respond
    Now" — the last entry of AGENT_REQUEST_REMINDERS serves from there on.
    """
    repeat = max(int(repeat), 0)
    return AGENT_REQUEST_REMINDERS[min(repeat, len(AGENT_REQUEST_REMINDERS) - 1)]

# SAPI: Volume 0-100; Rate -10..10 (0 is the default, negative is slower).
SPEECH_VOLUME = 100
SPEECH_RATE = -1

# System.Speech's SpeechSynthesizer takes the same Volume / Rate ranges.
_PS_SPEAK = (
    "Add-Type -AssemblyName System.Speech; "
    "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
    "$s.Volume = {volume}; $s.Rate = {rate}; "
    "$s.Speak([string]$args[0])"
)


def _speak_sapi(text):
    """Windows: say ``text`` through SAPI on its own thread (synchronous there).

    Returns True when pywin32 is present and the thread was started; the
    thread's own failures are swallowed (a machine with no voice installed),
    since by then the caller has moved on.
    """
    try:
        import pythoncom  # noqa: F401  (pywin32 — optional)
        import win32com.client  # noqa: F401
    except ImportError:
        return False

    def run():
        try:
            pythoncom.CoInitialize()
            try:
                voice = win32com.client.Dispatch("SAPI.SpVoice")
                voice.Volume = SPEECH_VOLUME
                voice.Rate = SPEECH_RATE
                voice.Speak(text)
            finally:
                pythoncom.CoUninitialize()
        except Exception:
            pass

    threading.Thread(target=run, name="speak", daemon=True).start()
    return True


def _speak_powershell(text):
    """Windows without pywin32: a hidden PowerShell child with System.Speech."""
    exe = shutil.which("powershell") or shutil.which("powershell.exe")
    if not exe:
        return False
    script = _PS_SPEAK.format(volume=SPEECH_VOLUME, rate=SPEECH_RATE)
    try:
        subprocess.Popen(
            [exe, "-NoProfile", "-NonInteractive", "-Command", script, text],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, **_SUBPROCESS_NOWND)
    except OSError:
        return False
    return True


def _speak_command(argv):
    """POSIX: start ``argv`` detached from our pipes; True when it launched.

    `_SUBPROCESS_NOWND` is empty off Windows — passed so every launch in the
    package carries the no-console-window flag (tests/test_no_console_flash.py).
    """
    try:
        subprocess.Popen(argv, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         **_SUBPROCESS_NOWND)
    except OSError:
        return False
    return True


def speak(text):
    """Say ``text`` aloud without blocking. True if speech was handed off."""
    text = str(text or "").strip()
    if not text:
        return False
    if IS_WINDOWS:
        return _speak_sapi(text) or _speak_powershell(text)
    if sys.platform == "darwin":
        return _speak_command(["say", text])
    for program in ("spd-say", "espeak"):
        exe = shutil.which(program)
        if exe:
            return _speak_command([exe, text])
    return False
