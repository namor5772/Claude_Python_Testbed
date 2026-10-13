"""No console window flashes from the GUI apps (2026-10-14).

MyAgent and SelfBot run under pythonw, which owns no console, so a child
that is a console program (PowerShell, taskkill) would get a console of its
own — a terminal window that appears and disappears over the chat. Every
such launch on Windows therefore carries `CREATE_NO_WINDOW`
(`_SUBPROCESS_NOWND`: `myagent/constants.py`, SelfBot's in-file twin). The
user saw the flash while dictating into SelfBot on 2026-10-14 — the bot was
reading the clock through `run_command` — because SelfBot's `run_powershell`
copy had lost the flag MyAgent's carries. This module pins the convention:
the two command runners, and an inventory of every `subprocess` launch in
both apps' code — each either passes the flag, or starts a program that is
POSIX-only (`ps`, `pgrep`, `osascript`) or a GUI executable (Edge).
"""
import inspect
import pathlib
import re
import unittest

from myagent import safety_mixin
from myagent.constants import _SUBPROCESS_NOWND
from tests.test_selfbot_anthropic_parity import SelfBot, _needs_selfbot

ROOT = pathlib.Path(__file__).resolve().parents[1]
LAUNCH = re.compile(r"subprocess\.(?:run|Popen|call|check_output|check_call)\(")
# Launches that need no flag: a POSIX-only program (never reached on Windows),
# a GUI-subsystem executable (Edge), which Windows never gives a console, or
# run_instruction's child MyAgent — `sys.executable`, the parent's own
# interpreter: pythonw under the launchers (no console), and under a console
# python the child shares the parent's console rather than opening one.
EXEMPT_MARKERS = ('["ps",', '["pgrep",', '["osascript",', "self._edge_process = ",
                  "cwd=_BASE_DIR,")
FILES = ["SelfBot.py", "MyAgent.py", *sorted(p.relative_to(ROOT).as_posix()
                                            for p in (ROOT / "myagent").glob("*.py"))]


def _launches(text):
    """Each subprocess launch as (line number, the call and the 12 lines
    around it — enough to see `**_SUBPROCESS_NOWND` or the popen_kwargs
    built just above)."""
    lines = text.splitlines()
    found = []
    for i, line in enumerate(lines):
        if LAUNCH.search(line):
            window = "\n".join(lines[max(0, i - 4): i + 12])
            found.append((i + 1, window))
    return found


class ConventionTests(unittest.TestCase):
    def test_the_flag_is_create_no_window_on_windows_only(self):
        import subprocess
        import sys
        if sys.platform == "win32":
            self.assertEqual(_SUBPROCESS_NOWND, {"creationflags": subprocess.CREATE_NO_WINDOW})
        else:
            self.assertEqual(_SUBPROCESS_NOWND, {})

    def test_every_launch_in_both_apps_passes_the_flag_or_is_exempt(self):
        offenders = []
        for rel in FILES:
            text = (ROOT / rel).read_text(encoding="utf-8")
            for line_no, window in _launches(text):
                flagged = "_SUBPROCESS_NOWND" in window or "CREATE_NO_WINDOW" in window
                exempt = any(marker in window for marker in EXEMPT_MARKERS)
                if not (flagged or exempt):
                    offenders.append(f"{rel}:{line_no}")
        self.assertEqual(offenders, [], "subprocess launches without CREATE_NO_WINDOW")

    def test_myagents_command_runner_passes_the_flag(self):
        src = inspect.getsource(safety_mixin.SafetyMixin.run_powershell)
        self.assertIn("popen_kwargs = dict(_SUBPROCESS_NOWND)", src)
        self.assertIn("**popen_kwargs,", src)


@_needs_selfbot
class SelfBotTests(unittest.TestCase):
    def test_selfbots_command_runner_passes_the_flag_like_myagents(self):
        src = inspect.getsource(SelfBot.App.run_powershell)
        self.assertIn("popen_kwargs = dict(_SUBPROCESS_NOWND)", src)
        self.assertIn("**popen_kwargs,", src)
        self.assertNotIn("popen_kwargs = {} if IS_WINDOWS", src)
        self.assertEqual(SelfBot._SUBPROCESS_NOWND, _SUBPROCESS_NOWND)

    def test_selfbots_process_tree_kill_passes_the_flag(self):
        src = inspect.getsource(SelfBot.App._kill_process_tree)
        self.assertIn("**_SUBPROCESS_NOWND", src)


if __name__ == "__main__":
    unittest.main()
