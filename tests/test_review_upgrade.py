"""Regression tests for the 2026-09-28 review fixes around the Model upgrade.

- _upgrade_end_run restores the model fields BEFORE dropping the stash (a 5 s
  state save landing between the two steps wrote the upgrade model);
- a write to the instruction store made while a run is upgraded — the
  editor's SAVE, manage_instructions create — records the instruction's own
  model, never the upgrade's;
- a Convo-mode end word ends the run without applying a ticked Upgrade — as
  does "exit" from either caller, which also closes MyAgent (2026-10-01);
- a run whose upgrade model served no priced call is not credited to it in
  the cost log (the 13th field names the model that did serve).
"""

import gc
import inspect
import tempfile
import threading
import tkinter as tk
import unittest
from pathlib import Path
from unittest import mock

import myagent.streaming_mixin as sm
from myagent.instructions_mixin import InstructionsMixin
from myagent.model_upgrade_mixin import ModelUpgradeMixin
from tests._util import stub
from tests.test_costlog_run_fields import _Host as _CostHost
from tests.test_model_upgrade import _DialogHost, _Host as _UpgradeHost, _walk, host

ORIGINAL = {"model": "claude-sonnet-5", "thinking_enabled": True, "thinking_effort": "low",
            "thinking_budget": 8192, "thinking_mode": "low", "call": 3}


class _Watch(_UpgradeHost):
    """Records the model at the moment the stash is dropped."""

    def __setattr__(self, name, value):
        if name == "_upgrade_original" and value is None:
            self.__dict__.setdefault("seen", []).append(self.__dict__.get("model"))
        object.__setattr__(self, name, value)


class EndRunOrderTests(unittest.TestCase):

    def test_the_fields_are_back_before_the_stash_goes(self):
        h = host(model="claude-fable-5-1", thinking_mode="max", thinking_effort="max")
        h.__class__ = _Watch
        h._upgrade_original = dict(ORIGINAL)
        h._upgrade_end_run()
        self.assertEqual(h.seen, ["claude-sonnet-5"])
        self.assertEqual((h.model, h.thinking_mode), ("claude-sonnet-5", "low"))
        self.assertIsNone(h._upgrade_original)


class StoreWritesTests(unittest.TestCase):

    class _Host(InstructionsMixin, ModelUpgradeMixin):
        pass

    def tool_host(self, **live):
        h = stub(self._Host, provider="Anthropic", model="claude-sonnet-5", temperature=1.0,
                 thinking_enabled=True, thinking_effort="low", thinking_budget=8192,
                 thinking_mode="low", text_verbosity="medium", fast_mode=False, skills={},
                 _disabled_confirm_patterns=set(), _blocked_tools=set(),
                 agent_instruction_name="", upgrade_target=None, _upgrade_original=None)
        for key, value in live.items():
            setattr(h, key, value)
        h.store = {}
        h._load_saved_instructions = lambda: h.store
        h._save_instructions_to_disk = lambda data: setattr(h, "store", data)
        return h

    def test_create_during_an_upgraded_run_stores_the_instructions_own_model(self):
        h = self.tool_host(model="claude-fable-5-1", thinking_effort="max",
                           thinking_mode="max", _upgrade_original=dict(ORIGINAL))
        h.do_manage_instructions({"action": "create", "name": "New", "text": "do it"})
        entry = h.store["New"]
        self.assertEqual((entry["model"], entry["thinking_mode"], entry["thinking_effort"]),
                         ("claude-sonnet-5", "low", "low"))
        self.assertNotIn("call", entry)            # the stash's call number stays out

    def test_create_outside_an_upgrade_stores_the_live_model(self):
        h = self.tool_host(model="claude-opus-5", thinking_mode="high", thinking_effort="high")
        h.do_manage_instructions({"action": "create", "name": "New", "text": "do it"})
        self.assertEqual(h.store["New"]["model"], "claude-opus-5")

    def test_the_editors_save_goes_through_the_same_rule(self):
        # _save_instruction needs the whole editor; its entry must be built
        # from _store_model_fields exactly as the tool's is.
        source = inspect.getsource(InstructionsMixin._save_instruction)
        self.assertIn("**self._store_model_fields()", source)
        self.assertNotIn('"model": self.model', source)


class ConvoEndWordTests(unittest.TestCase):

    TARGET = {"model": "claude-fable-5-1", "level": "Max"}

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.geometry("240x240+0+0")
        self.root.attributes("-alpha", 0.0)

    def tearDown(self):
        self.host = None
        gc.collect()          # on the main thread, before the root goes
        self.root.destroy()

    def reply_ticked(self, text, convo):
        """The real dialog: tick Upgrade, type `text`, send — on a worker
        thread with the main thread in mainloop, as stream_worker runs it."""
        host = self.host = _DialogHost(self.root, self.TARGET)
        outcome = {}
        worker = threading.Thread(
            target=lambda: outcome.setdefault("reply", host.do_user_prompt("Next?", convo=convo)),
            daemon=True)

        def act():
            dialog = host._prompt_dialog
            if dialog is None or not dialog.winfo_ismapped():
                if worker.is_alive():
                    self.root.after(20, act)
                return
            box = [w for w in _walk(dialog) if isinstance(w, tk.Checkbutton)
                   and w.cget("text").startswith("Upgrade")][0]
            reply = [w for w in _walk(dialog)
                     if isinstance(w, tk.Text) and str(w.cget("state")) == "normal"][0]
            box.invoke()
            reply.insert("1.0", text)
            host.send()

        def wait():
            if worker.is_alive():
                self.root.after(20, wait)
            else:
                self.root.quit()

        self.root.after(0, worker.start)
        self.root.after(20, act)
        self.root.after(40, wait)
        watchdog = self.root.after(20000, self.root.quit)
        self.root.mainloop()
        self.root.after_cancel(watchdog)
        self.assertEqual(outcome.get("reply"), text)
        return host

    def test_a_convo_end_word_ends_the_run_without_upgrading(self):
        self.assertEqual(self.reply_ticked("quit", convo=True).applied, [])

    def test_a_convo_reply_still_upgrades(self):
        self.assertEqual(self.reply_ticked("tighten the skills", convo=True).applied,
                         [self.TARGET])

    def test_outside_convo_quit_is_just_a_reply_and_upgrades(self):
        # The user_prompt tool: "quit" goes to the model as text; only an empty
        # reply — or "exit", below — stops that run.
        self.assertEqual(self.reply_ticked("quit", convo=False).applied, [self.TARGET])

    def test_exit_ends_the_run_without_upgrading_in_convo_mode(self):
        self.assertEqual(self.reply_ticked("exit", convo=True).applied, [])

    def test_exit_outside_convo_ends_the_run_too_and_upgrades_nothing(self):
        # Unlike "quit", "exit" (CONVO_EXIT_WORD, 2026-10-01) is an end word for
        # the user_prompt tool as well — it stops the run and closes MyAgent —
        # so there is no call left for an upgrade to serve.
        self.assertEqual(self.reply_ticked("exit", convo=False).applied, [])


class CostSplitTests(unittest.TestCase):

    SONNET = {"claude-sonnet-5": [0.0264, 1000, 100, 0, 0]}

    def test_one_served_model_other_than_the_logged_one_is_written(self):
        field = sm.StreamingMixin._cost_split_field(self.SONNET, "claude-fable-5-1")
        self.assertEqual(field, "claude-sonnet-5,0.026400,1000,100,0,0")

    def test_one_served_model_that_is_the_logged_one_is_not(self):
        self.assertEqual(sm.StreamingMixin._cost_split_field(self.SONNET, "claude-sonnet-5"), "")
        self.assertEqual(sm.StreamingMixin._cost_split_field(self.SONNET), "")

    def test_the_log_line_carries_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "cost.txt"
            with mock.patch.object(sm, "APICOST_LOG_FILE", str(log)):
                h = _CostHost()
                h.model = "claude-fable-5-1"       # upgraded on the last reply
                h._log_api_cost(0.0264, True, 5, instruction="Balance", calls=3,
                                tokens=(1000, 100, 0, 0), split=dict(self.SONNET))
            fields = log.read_text(encoding="utf-8").strip().split(";")
        self.assertEqual(len(fields), 13)
        self.assertEqual(fields[2], "claude-fable-5-1")
        self.assertTrue(fields[12].startswith("claude-sonnet-5,"))


if __name__ == "__main__":
    unittest.main()
