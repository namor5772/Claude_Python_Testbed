"""Characterization tests for the environment run_command's children get
(2026-10-06): the venv's bin folder first on PATH and VIRTUAL_ENV set when
MyAgent runs from a venv, nothing changed otherwise. The launchers start the
venv interpreter without activating it, so the model's bare `python3` had run
the system interpreter — on the Mac the Xcode Command Line Tools' 3.9, with
none of the venv's packages (seen in that day's TCC log, where the processes
raising the "access data from other apps" dialog were that Python)."""

import os
import queue
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from myagent.constants import IS_WINDOWS
from myagent.safety_mixin import SafetyMixin


class _Host(SafetyMixin):
    def __init__(self):
        self.queue = queue.Queue()

    def _check_command_safety(self, command):
        # Bypass pattern matching / Tk confirm dialogs — execution only.
        return "safe", None


def _fake_venv(root, with_cfg=True):
    """A venv-shaped tree — <root>/bin/python (Scripts/python.exe on Windows) —
    with or without the pyvenv.cfg that makes it one. Nothing in it is run."""
    bin_dir = root / ("Scripts" if IS_WINDOWS else "bin")
    bin_dir.mkdir(parents=True)
    exe = bin_dir / ("python.exe" if IS_WINDOWS else "python")
    exe.write_text("", encoding="utf-8")
    if with_cfg:
        (root / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    return exe, bin_dir


class EnvBuilderCase(unittest.TestCase):
    """_run_command_env, pure: a mapping and an interpreter in, a mapping out."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.sys_path = os.pathsep.join([str(self.dir / "usr" / "bin"), str(self.dir / "bin")])

    def test_venv_bin_goes_first_and_virtual_env_is_set(self):
        exe, bin_dir = _fake_venv(self.dir / "venv")
        env = SafetyMixin._run_command_env({"PATH": self.sys_path, "HOME": "x"}, str(exe))
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], str(bin_dir))
        self.assertEqual(parts[1:], self.sys_path.split(os.pathsep))
        self.assertEqual(env["VIRTUAL_ENV"], str(self.dir / "venv"))
        self.assertEqual(env["HOME"], "x")                 # everything else rides along

    def test_a_bin_folder_already_on_path_moves_to_the_front_once(self):
        exe, bin_dir = _fake_venv(self.dir / "venv")
        path = os.pathsep.join([str(self.dir / "usr" / "bin"), str(bin_dir), str(self.dir / "bin")])
        env = SafetyMixin._run_command_env({"PATH": path}, str(exe))
        parts = env["PATH"].split(os.pathsep)
        self.assertEqual(parts[0], str(bin_dir))
        self.assertEqual(parts.count(str(bin_dir)), 1)
        self.assertEqual(len(parts), 3)
        # Applying it again changes nothing: a fixed point.
        self.assertEqual(SafetyMixin._run_command_env(env, str(exe)), env)

    def test_without_pyvenv_cfg_the_environment_is_untouched(self):
        exe, _ = _fake_venv(self.dir / "system", with_cfg=False)
        src = {"PATH": self.sys_path}
        env = SafetyMixin._run_command_env(src, str(exe))
        self.assertEqual(env, src)
        self.assertIsNot(env, src)                          # a copy, never the caller's mapping
        self.assertNotIn("VIRTUAL_ENV", env)

    def test_an_empty_path_still_gets_the_bin_folder(self):
        exe, bin_dir = _fake_venv(self.dir / "venv")
        self.assertEqual(SafetyMixin._run_command_env({}, str(exe))["PATH"], str(bin_dir))

    def test_no_interpreter_means_no_change(self):
        src = {"PATH": self.sys_path}
        self.assertEqual(SafetyMixin._run_command_env(src, ""), src)

    def test_defaults_are_the_process_environment_and_interpreter(self):
        exe, bin_dir = _fake_venv(self.dir / "venv")
        with mock.patch.dict(os.environ, {"PATH": self.sys_path}), \
                mock.patch.object(sys, "executable", str(exe)):
            env = SafetyMixin._run_command_env()
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(bin_dir))
        self.assertEqual(env["VIRTUAL_ENV"], str(self.dir / "venv"))


class ChildProcessCase(unittest.TestCase):
    """The REAL run_powershell: its child inherits what the builder made."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def test_child_sees_the_venv_first_on_path(self):
        exe, bin_dir = _fake_venv(self.dir / "venv")
        if IS_WINDOWS:
            cmd = 'Write-Output (($env:PATH -split ";")[0] + "|" + $env:VIRTUAL_ENV)'
        else:
            cmd = 'echo "${PATH%%:*}|$VIRTUAL_ENV"'
        with mock.patch.object(sys, "executable", str(exe)):
            out = _Host().run_powershell(cmd, timeout=60)
        self.assertIn(f"{bin_dir}|{self.dir / 'venv'}", out)

    @unittest.skipUnless(
        os.path.isfile(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(sys.executable))), "pyvenv.cfg")),
        "the test runner is not a venv interpreter")
    def test_a_bare_python_in_a_command_is_this_venvs_interpreter(self):
        out = _Host().run_powershell('python -c "import sys; print(sys.executable)"', timeout=60)
        found = Path(out.strip().splitlines()[0].strip()).resolve()
        self.assertEqual(found, Path(sys.executable).resolve())


if __name__ == "__main__":
    unittest.main()
