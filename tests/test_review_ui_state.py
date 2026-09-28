"""Regression tests for the 2026-09-28 review fixes in the UI / state layer.

- instance locks: the liveness check reads a process's command line itself
  (wmic is gone from current Windows 11 builds, and its failure accepted ANY
  python.exe reusing a stale lock's pid), and a lock naming our own pid is a
  crashed predecessor's, not a live sibling's;
- agent_state.json is written atomically, and never by a --headless run;
- an unattended -l launch that cannot start writes an error result and
  closes instead of blocking on a messagebox nobody will answer;
- the Thinking strength combo's correction reaches thinking_effort;
- a thinking-off value from another provider stays off on Anthropic;
- a pinned model the picker hides but the provider still serves runs;
- the editor refuses to switch or clear pages under a live run.
"""

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from myagent import instructions_mixin, state_mixin
from myagent.constants import IS_WINDOWS
from myagent.gemini_mixin import GeminiMixin
from myagent.instructions_mixin import InstructionsMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.state_mixin import StateMixin
from myagent.ui_mixin import UIMixin
from tests._util import stub
from tests.test_state_skill_modes import _Host as _StateHost, _StateFileCase
from tests.test_xai_params import _UIHost


@unittest.skipUnless(IS_WINDOWS, "the command-line read is the Windows branch")
class ProcessCommandLineTests(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def spawn(self, *args):
        proc = subprocess.Popen([sys.executable, *args])
        self.addCleanup(lambda: (proc.kill(), proc.wait(10)))
        return proc

    def test_our_own_command_line_is_readable(self):
        cmdline = StateMixin._win_process_cmdline(os.getpid())
        self.assertIsNotNone(cmdline)
        self.assertIn("python", cmdline.lower())

    def test_only_a_myagent_py_process_holds_a_slot(self):
        script = os.path.join(self.dir, "MyAgent.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write("import time; time.sleep(30)\n")
        other = self.spawn("-c", "import time; time.sleep(30)")
        agent = self.spawn(script)
        time.sleep(1.5)
        self.assertFalse(StateMixin._is_pid_alive(other.pid))   # was True without wmic
        self.assertTrue(StateMixin._is_pid_alive(agent.pid))

    def test_a_dead_pid_is_dead(self):
        self.assertFalse(StateMixin._is_pid_alive(999_999))


class OwnPidLockTests(unittest.TestCase):

    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, True)
        self.prefix = os.path.join(d, "agent_lock_")
        with open(self.prefix + "1.lock", "w") as f:
            f.write(str(os.getpid()))

    def claim(self):
        # Liveness as in production for OUR pid: we are running.
        with mock.patch.object(state_mixin, "AGENT_LOCK_PREFIX", self.prefix), \
                mock.patch.object(StateMixin, "_is_pid_alive",
                                  lambda self, pid: pid == os.getpid()):
            return stub(StateMixin)._claim_instance_number()

    def test_a_predecessors_lock_naming_our_own_pid_is_reclaimed(self):
        old = time.time() - 3600                    # written before this process began
        os.utime(self.prefix + "1.lock", (old, old))
        self.assertEqual(self.claim(), 1)

    def test_a_lock_written_since_we_started_is_a_live_claimants(self):
        self.assertEqual(self.claim(), 2)           # slot 1 stays with its claimant


class StateFileTests(_StateFileCase):

    def test_a_write_that_dies_half_way_leaves_the_previous_file_intact(self):
        host = _StateHost({}, self.state_file)
        host._save_last_state()
        with open(self.state_file, "rb") as f:
            before = f.read()

        def dies_half_way(obj, f, **kwargs):
            f.write('{"truncat')
            raise OSError("killed mid-write")

        with mock.patch.object(state_mixin.json, "dump", dies_half_way):
            with self.assertRaises(OSError):
                host._save_last_state()
        with open(self.state_file, "rb") as f:
            self.assertEqual(f.read(), before)
        folder, base = os.path.split(self.state_file)
        self.assertEqual([n for n in os.listdir(folder)
                          if n.startswith(base + ".") and n.endswith(".tmp")], [])

    def test_a_headless_run_never_writes_the_state_file(self):
        host = _StateHost({}, self.state_file)
        host._headless = True
        host._save_last_state()
        self.assertFalse(os.path.exists(self.state_file))


class _LaunchHost(StateMixin):
    def __init__(self, name, instructions, headless=False, result_file=None):
        self._launch_instruction = name
        self._instructions = instructions
        self._headless = headless
        self._result_file = result_file
        self._extra_text = ""
        self.queue = queue.Queue()
        self.scheduled = []
        self.results = []
        self.root = SimpleNamespace(after=lambda ms, fn: self.scheduled.append(fn))

    def _load_saved_instructions(self):
        return self._instructions

    def _apply_instruction_entry(self, name, entry):
        self.agent_instruction = entry["text"]

    def _write_result_file(self, status, messages, error=""):
        self.results.append((status, error))

    def _on_close(self):
        pass


class AutoLaunchTests(unittest.TestCase):

    def test_an_unattended_launch_of_an_unknown_name_reports_and_closes(self):
        for kwargs in ({"headless": True}, {"result_file": "r.json"}):
            with self.subTest(**kwargs):
                host = _LaunchHost("Missing", {"Other": {"text": "t"}}, **kwargs)
                with mock.patch.object(state_mixin.messagebox, "showerror") as dialog:
                    host._auto_launch()
                dialog.assert_not_called()           # nobody there to answer it
                self.assertEqual(len(host.results), 1)
                self.assertEqual(host.results[0][0], "error")
                self.assertIn("Missing", host.results[0][1])
                self.assertEqual(host.scheduled, [host._on_close])

    def test_an_unattended_launch_of_an_empty_instruction_reports_and_closes(self):
        host = _LaunchHost("Blank", {"Blank": {"text": "   "}}, headless=True)
        with mock.patch.object(state_mixin.messagebox, "showerror") as dialog:
            host._auto_launch()
        dialog.assert_not_called()
        self.assertEqual(host.results[0][0], "error")
        self.assertIn("no text", host.results[0][1])

    def test_a_gui_launch_still_gets_the_dialog(self):
        host = _LaunchHost("Missing", {"Other": {"text": "t"}})
        with mock.patch.object(state_mixin.messagebox, "showerror") as dialog:
            host._auto_launch()
        dialog.assert_called_once()
        self.assertEqual(host.results, [])


class _Var:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _StrengthHost(UIMixin, OpenAIMixin, GeminiMixin):
    pass


class ThinkingValueTests(unittest.TestCase):

    def test_the_strength_correction_reaches_thinking_effort(self):
        h = object.__new__(_StrengthHost)
        h.provider, h.model = "Google", "gemini-3.8-flash"
        h.thinking_enabled, h.thinking_effort = True, "xhigh"   # an Anthropic instruction's
        h._model_combo = object()                               # the editor is open
        h._thinking_strength_combo = {}
        h._thinking_strength_var = _Var("xhigh")
        h._update_thinking_strength_options()
        self.assertEqual(h._thinking_strength_var.get(), "high")
        self.assertEqual(h.thinking_effort, "high")             # what is sent and saved

    def test_thinking_off_from_another_provider_stays_off_on_anthropic(self):
        coerce = UIMixin._anthropic_mode_coerce
        ladder = ["Off", "Adaptive", "Low", "Medium", "High", "Max"]
        always_on = ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]
        self.assertEqual(coerce("None", ladder), "Off")
        self.assertEqual(coerce("None", always_on), "Adaptive")
        self.assertEqual(coerce("Off", always_on), "Adaptive")
        self.assertEqual(coerce("Minimal", ladder), "Low")
        self.assertEqual(coerce("Xhigh", ladder), "High")       # above this ladder
        self.assertEqual(coerce("Medium", ladder), "Medium")     # present: unchanged


class PinnedModelTests(unittest.TestCase):

    ENTRY = {"provider": "xAI", "temperature": 1.0, "thinking_enabled": True,
             "thinking_effort": "high", "thinking_budget": 8192,
             "thinking_mode": "high", "text_verbosity": "medium"}

    def test_a_hidden_but_served_model_runs_as_pinned(self):
        h = _UIHost(model="grok-4.3")
        h._served_model_ids = {"xAI": {"grok-4.3", "grok-4.7", "grok-build-0.1",
                                       "grok-latest"}}
        h._restore_model_params(dict(self.ENTRY, model="grok-latest"))
        self.assertEqual(h.model, "grok-latest")
        self.assertEqual(len(h._model_drift_warnings), 1)
        self.assertIn("hidden from the model picker", h._model_drift_warnings[0])

    def test_a_model_nobody_serves_still_falls_back(self):
        h = _UIHost(model="grok-4.7")
        h._served_model_ids = {"xAI": {"grok-4.3", "grok-4.7"}}
        h._restore_model_params(dict(self.ENTRY, model="claude-sonnet-5"))
        self.assertEqual(h.model, "grok-4.3")                   # the curated default
        self.assertIn("falling back", h._model_drift_warnings[0])

    def test_an_unknown_listing_falls_back_as_before(self):
        h = _UIHost(model="grok-4.7")              # the fetch failed: nothing noted
        h._restore_model_params(dict(self.ENTRY, model="grok-latest"))
        self.assertEqual(h.model, "grok-4.3")


class _EditorHost(InstructionsMixin):
    def __init__(self, streaming):
        self.streaming = streaming
        self.instruction_editor_window = None
        self._instr_shown_name = "Current"
        self._instr_text = mock.Mock()
        self.selected = []
        self.loads = 0

    def _selected_instruction_name(self):
        return "Other"

    def _select_instruction_row(self, row):
        self.selected.append(row)

    def _load_saved_instructions(self):
        self.loads += 1
        return {}


class EditorRunGuardTests(unittest.TestCase):

    def test_no_page_switch_under_a_live_run(self):
        h = _EditorHost(streaming=True)
        with mock.patch.object(instructions_mixin.messagebox, "showinfo") as notice:
            h._on_instruction_selected()
        notice.assert_called_once()
        self.assertEqual(h.loads, 0)                            # nothing restored
        self.assertEqual(h.selected, [("page", "Current")])     # the list goes back

    def test_no_clear_under_a_live_run(self):
        h = _EditorHost(streaming=True)
        with mock.patch.object(instructions_mixin.messagebox, "showinfo"):
            h._clear_instruction_editor()
        h._instr_text.delete.assert_not_called()

    def test_idle_the_page_loads_as_ever(self):
        h = _EditorHost(streaming=False)
        with mock.patch.object(instructions_mixin.messagebox, "showinfo") as notice:
            h._on_instruction_selected()
        notice.assert_not_called()
        self.assertEqual(h.loads, 1)


if __name__ == "__main__":
    unittest.main()
