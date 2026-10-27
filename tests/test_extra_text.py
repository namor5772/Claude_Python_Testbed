"""Characterization tests for run_instruction's per-spawn extra_text: the
_merge_extra_text composition on the child side, and the parent-side temp-file
transport (--extra-file on the command line, cleanup semantics) with
subprocess.Popen mocked out."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from myagent.skills_mixin import SkillsMixin
from myagent.state_mixin import StateMixin


class MergeCase(unittest.TestCase):
    def test_empty_extra_is_identity(self):
        self.assertEqual(StateMixin._merge_extra_text("base task", ""), "base task")
        self.assertEqual(StateMixin._merge_extra_text("base task", None), "base task")

    def test_appends_labeled_block(self):
        out = StateMixin._merge_extra_text("base task", "the specific question")
        self.assertTrue(out.startswith("base task\n\n"))
        self.assertIn("ADDITIONAL TASK CONTEXT", out)
        self.assertTrue(out.endswith("the specific question"))


class PrependCase(unittest.TestCase):
    """--prepend-file (2026-10-28): the Telegram prompt for an instruction
    without a ***** block, put in FRONT of the saved text for one run."""

    def test_empty_prepend_is_identity(self):
        self.assertEqual(StateMixin._merge_prepend_text("base task", ""), "base task")
        self.assertEqual(StateMixin._merge_prepend_text("base task", None), "base task")

    def test_the_text_comes_first_plain_with_a_blank_line_between(self):
        self.assertEqual(StateMixin._merge_prepend_text("base task", "Do this today"),
                         "Do this today\n\nbase task")

    def test_the_launch_prepends_before_it_appends_and_the_flag_is_wired(self):
        import inspect
        from pathlib import Path
        launch = inspect.getsource(StateMixin._auto_launch)
        self.assertLess(launch.index("_merge_prepend_text("), launch.index("_merge_extra_text("))
        self.assertIn('getattr(self, "_prepend_text", "")', launch)
        entry = (Path(__file__).resolve().parents[1] / "MyAgent.py").read_text(encoding="utf-8")
        self.assertIn('"--prepend-file"', entry)
        self.assertIn("prepend_file=args.prepend_file", entry)
        self.assertIn("self._prepend_text = f.read()", entry)


class _Host(SkillsMixin):
    def __init__(self):
        self.stop_requested = False

    def _load_saved_instructions(self):
        return {"Child": {"text": "t"}}


class TransportCase(unittest.TestCase):
    def test_fire_and_forget_passes_extra_file_with_content(self):
        host = _Host()
        fake = SimpleNamespace(pid=42)
        with mock.patch("myagent.skills_mixin.subprocess.Popen",
                        return_value=fake) as popen:
            out = host.do_run_instruction(
                {"name": "Child", "extra_text": "per-run question?"})
        self.assertIn("extra task context", out)
        cmd = popen.call_args[0][0]
        self.assertIn("--extra-file", cmd)
        path = cmd[cmd.index("--extra-file") + 1]
        try:
            with open(path, encoding="utf-8") as f:
                self.assertEqual(f.read(), "per-run question?")
        finally:
            os.remove(path)  # fire-and-forget leaves the file for the child

    def test_no_extra_text_means_no_flag(self):
        host = _Host()
        with mock.patch("myagent.skills_mixin.subprocess.Popen",
                        return_value=SimpleNamespace(pid=42)) as popen:
            host.do_run_instruction({"name": "Child"})
        self.assertNotIn("--extra-file", popen.call_args[0][0])

    def test_waited_run_cleans_up_both_temp_files(self):
        host = _Host()
        fake = SimpleNamespace(pid=42, returncode=0, poll=lambda: 0,
                               terminate=lambda: None)
        with mock.patch("myagent.skills_mixin.subprocess.Popen",
                        return_value=fake) as popen:
            out = host.do_run_instruction(
                {"name": "Child", "wait": True, "extra_text": "q"})
        # Child "exited" without writing a result — surfaced as a clear error...
        self.assertIn("no readable result", out)
        # ...and the finally block removed BOTH temp files.
        cmd = popen.call_args[0][0]
        result_path = cmd[cmd.index("--result-file") + 1]
        extra_path = cmd[cmd.index("--extra-file") + 1]
        self.assertFalse(os.path.exists(result_path))
        self.assertFalse(os.path.exists(extra_path))


if __name__ == "__main__":
    unittest.main()
