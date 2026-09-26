"""Characterization tests for the model upgrade (myagent/model_upgrade_mixin.py,
2026-09-27): the Agent Request dialog's Upgrade box moves a run to a stronger
model of the same provider from that reply on; Model Setup, a button beside
Voice Setup, chooses the model + thinking level per provider.

Pinned here, without a key or the network:

* the settings: sanitising, the curated defaults for a machine with no file
  yet (and NOT for a file that names no target), the atomic save;
* the levels a model is offered — the instruction editor's own rungs for it,
  via a probe that never touches the live app — across every provider's
  kinds (mode combobox, checkbox + strength, token budgets, boolean), and how
  a saved level lands on a model that lacks it (the strongest offered);
* what a level means: the four live thinking fields, as the editor's three
  handlers would leave them;
* the switch on a bare host: the originals stashed BEFORE the live fields
  change, the pane line, the title refresh scheduled on the Tk thread, one
  upgrade per run, the restore at the loop's end, the title / cost-log part;
* the state file: while an upgrade is active `_save_last_state` writes the
  run's OWN model fields, never the upgrade's;
* the dialog's box on the REAL Agent Request dialog (inside a real mainloop
  with do_user_prompt on a worker thread, as a run calls it): its three
  states, and the switch applied only for a sent, non-empty reply with the
  box ticked;
* Model Setup on real widgets: the saved target shown, the level list
  following the model, "(none)" clearing a provider, Save writing the file;
* the wiring (a static scan): the row between the voice row and the image
  row, the mnemonic, the worker-side apply, the loop's two restore sites,
  the state file's stash read, the main window's button, the App's bases —
  and the module's independence from the instruction store.
"""

import json
import os
import pathlib
import queue
import tempfile
import threading
import tkinter as tk
import unittest
from tkinter import ttk
from unittest import mock

from tests._util import stub
from tests.test_agent_request_display import _Dictation, _walk
from tests.test_state_skill_modes import _Host as _StateHost
from myagent import model_upgrade_mixin as mu
from myagent.constants import UPGRADE_DEFAULT_TARGETS, UPGRADE_NO_MODEL_LABEL
from myagent.gemini_mixin import GeminiMixin
from myagent.kimi_mixin import KimiMixin
from myagent.model_upgrade_mixin import ModelUpgradeMixin, UPGRADE_FIELDS
from myagent.ollama_mixin import OllamaMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.safety_mixin import SafetyMixin
from myagent.ui_mixin import UIMixin
from myagent.xai_mixin import XAIMixin

REPO = pathlib.Path(__file__).resolve().parents[1]


class _Probe(ModelUpgradeMixin, UIMixin, OpenAIMixin, XAIMixin, KimiMixin,
             GeminiMixin, OllamaMixin):
    """The detection helpers the mixin's probe consults, as the App carries them."""


def probe(**attrs):
    attrs.setdefault("provider", "Anthropic")
    attrs.setdefault("model", "claude-sonnet-5")
    return stub(_Probe, **attrs)


class _Root:
    def __init__(self):
        self.scheduled = []

    def after(self, delay, fn):
        self.scheduled.append(fn)


def host(**attrs):
    """A run mid-way: Sonnet 5, adaptive, twelve calls in."""
    base = dict(provider="Anthropic", model="claude-sonnet-5", thinking_enabled=True,
                thinking_effort="high", thinking_budget=8192, thinking_mode="adaptive",
                temperature=1.0, fast_mode=False, _anthropic_unsupported=set(),
                queue=queue.Queue(), root=_Root(), _run_call_num=12, _upgrade_original=None,
                infos=[])
    base.update(attrs)
    h = probe(**base)
    h._tool_info = h.infos.append
    h._update_title = lambda: None
    h._get_display_name = lambda model_id: {"claude-fable-5-1": "Claude Fable 5.1"}.get(
        model_id, model_id)
    return h


def drained(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


# ── Settings ────────────────────────────────────────────────────────────

class ConfigTests(unittest.TestCase):

    def test_anything_sanitises_to_well_formed_targets_for_known_providers(self):
        self.assertEqual(ModelUpgradeMixin._upgrade_sanitize_config(None), {"targets": {}})
        self.assertEqual(ModelUpgradeMixin._upgrade_sanitize_config([1]), {"targets": {}})
        cfg = ModelUpgradeMixin._upgrade_sanitize_config({"targets": {
            "Anthropic": {"model": " claude-fable-5-1 ", "level": " Max "},
            "OpenAI": {"model": "gpt-6-astra"},                 # level optional
            "Google": {"model": ""},                            # no model: no target
            "xAI": "grok-4.7",                                  # wrong shape
            "Bedrock": {"model": "x", "level": "y"},            # unknown provider
        }})
        self.assertEqual(cfg, {"targets": {
            "Anthropic": {"model": "claude-fable-5-1", "level": "Max"},
            "OpenAI": {"model": "gpt-6-astra", "level": ""}}})

    def test_no_file_yet_means_the_curated_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            cfg = ModelUpgradeMixin._upgrade_load_config(os.path.join(folder, "config.json"))
        self.assertEqual(cfg["targets"], UPGRADE_DEFAULT_TARGETS)
        self.assertEqual(set(cfg["targets"]), {"Anthropic", "OpenAI"})

    def test_a_saved_file_is_taken_as_it_is_with_no_defaults_added(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"targets": {"Google": {"model": "gemini-3.1-pro-preview",
                                                  "level": "High"}}}, f)
            cfg = ModelUpgradeMixin._upgrade_load_config(path)
        self.assertEqual(cfg["targets"], {"Google": {"model": "gemini-3.1-pro-preview",
                                                     "level": "High"}})

    def test_save_then_load_round_trips_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "sub", "config.json")
            targets = {"Anthropic": {"model": "claude-opus-5-5", "level": "High"}}
            ModelUpgradeMixin._upgrade_save_config({"targets": targets}, path)
            self.assertEqual(os.listdir(os.path.dirname(path)), ["config.json"])
            self.assertEqual(ModelUpgradeMixin._upgrade_load_config(path)["targets"], targets)

    def test_a_torn_file_loads_as_the_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "config.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write('{"targets": {"Anth')
            cfg = ModelUpgradeMixin._upgrade_load_config(path)
        self.assertEqual(cfg["targets"], UPGRADE_DEFAULT_TARGETS)

    def test_the_target_is_the_runs_provider_unless_asked_otherwise(self):
        h = host()
        with mock.patch.object(ModelUpgradeMixin, "_upgrade_load_config",
                               staticmethod(lambda path=None: {"targets": {
                                   "Anthropic": {"model": "a", "level": "Max"},
                                   "OpenAI": {"model": "o", "level": "Low"}}})):
            self.assertEqual(h._upgrade_target(), {"model": "a", "level": "Max"})
            self.assertEqual(h._upgrade_target("OpenAI"), {"model": "o", "level": "Low"})
            self.assertIsNone(h._upgrade_target("Google"))


# ── Levels ──────────────────────────────────────────────────────────────

class LevelTests(unittest.TestCase):

    def levels(self, provider, model, **attrs):
        return probe(**attrs)._upgrade_level_values(provider, model)

    def test_anthropic_adaptive_models_get_the_editors_mode_rungs(self):
        self.assertEqual(self.levels("Anthropic", "claude-sonnet-5"),
                         ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        # Always-on thinking: no Off.
        self.assertEqual(self.levels("Anthropic", "claude-fable-5-1"),
                         ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(self.levels("Anthropic", "claude-opus-5-5")[0], "Adaptive")

    def test_anthropic_budget_models_get_off_and_the_presets(self):
        self.assertEqual(self.levels("Anthropic", "claude-haiku-4-5-20251001"),
                         ["Off", "1K", "4K", "8K", "16K", "32K"])

    def test_openai_families(self):
        self.assertEqual(self.levels("OpenAI", "gpt-6-astra"),
                         ["Low", "Medium", "High", "Xhigh", "Max"])       # always-reasoning
        self.assertEqual(self.levels("OpenAI", "gpt-5.6-terra"),
                         ["None", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(self.levels("OpenAI", "gpt-5.5-pro"), ["Medium", "High", "Xhigh"])
        self.assertEqual(self.levels("OpenAI", "gpt-5"),
                         ["Off", "Minimal", "Low", "Medium", "High"])   # the 5.0 strength combo
        self.assertEqual(self.levels("OpenAI", "o3"), ["Off", "Low", "Medium", "High"])
        self.assertEqual(self.levels("OpenAI", "gpt-5.4-chat-latest"), [])   # no reasoning

    def test_google_xai_moonshot_and_ollama(self):
        self.assertEqual(self.levels("Google", "gemini-3.8-flash"), ["Off", "Low", "Medium", "High"])
        self.assertEqual(self.levels("xAI", "grok-4.7"), ["Low", "Medium", "High", "Xhigh"])
        self.assertEqual(self.levels("xAI", "grok-4.3")[0], "None")
        self.assertEqual(self.levels("xAI", "grok-4.20-0309-non-reasoning"), [])   # no knob
        self.assertEqual(self.levels("Moonshot", "kimi-k3"), ["None", "Low", "High", "Max"])
        self.assertEqual(self.levels("Moonshot", "kimi-k2.6"), ["Off", "On"])
        self.assertEqual(self.levels("Moonshot", "kimi-k2.7-code"), [])   # always-on, no knob
        self.assertEqual(self.levels("Ollama", "qwen3:8b"), ["Off", "On"])
        self.assertEqual(self.levels("Ollama", "llama3.3"), [])

    def test_the_live_xai_ladder_reaches_the_probe(self):
        caps = {"grok-5": {"reasoning_effort": ["low", "max"], "vision": True}}
        self.assertEqual(self.levels("xAI", "grok-5", _xai_caps=caps), ["Low", "Max"])

    def test_the_probe_is_never_the_live_app(self):
        h = probe(provider="OpenAI", model="gpt-5.6-terra")
        h._upgrade_level_values("Anthropic", "claude-fable-5-1")
        self.assertEqual((h.provider, h.model), ("OpenAI", "gpt-5.6-terra"))

    def test_a_saved_level_lands_on_what_the_model_offers(self):
        h = probe()
        self.assertEqual(h._upgrade_level_for("Anthropic", "claude-sonnet-5", "max"), "Max")
        self.assertEqual(h._upgrade_level_for("OpenAI", "gpt-5.5-pro", "Max"), "Xhigh")
        self.assertEqual(h._upgrade_level_for("Anthropic", "claude-fable-5-1", "Off"), "Max")
        self.assertEqual(h._upgrade_level_for("Anthropic", "claude-fable-5-1", ""), "Max")
        self.assertEqual(h._upgrade_level_for("OpenAI", "gpt-5.4-chat-latest", "Max"), "")


class ParamTests(unittest.TestCase):

    def params(self, provider, model, level):
        h = host()
        return h._upgrade_params_for(provider, model, level)

    def test_a_mode_rung_is_the_mode_and_the_effort(self):
        self.assertEqual(self.params("Anthropic", "claude-fable-5-1", "Max"), {
            "thinking_enabled": True, "thinking_effort": "max", "thinking_budget": 8192,
            "thinking_mode": "max"})
        self.assertEqual(self.params("OpenAI", "gpt-6-astra", "Xhigh")["thinking_mode"], "xhigh")

    def test_adaptive_enables_at_the_internal_default_effort(self):
        p = self.params("Anthropic", "claude-sonnet-5", "Adaptive")
        self.assertEqual((p["thinking_enabled"], p["thinking_effort"], p["thinking_mode"]),
                         (True, "high", "adaptive"))

    def test_off_and_none_disable_as_the_combobox_would(self):
        self.assertEqual(self.params("Anthropic", "claude-sonnet-5", "Off"), {
            "thinking_enabled": False, "thinking_effort": "off", "thinking_budget": 8192,
            "thinking_mode": "off"})
        p = self.params("OpenAI", "gpt-5.6-terra", "None")
        self.assertEqual((p["thinking_enabled"], p["thinking_effort"], p["thinking_mode"]),
                         (False, "none", "none"))

    def test_a_strength_is_enabled_plus_effort(self):
        p = self.params("Google", "gemini-3.8-flash", "High")
        self.assertEqual((p["thinking_enabled"], p["thinking_effort"], p["thinking_mode"]),
                         (True, "high", "high"))
        self.assertFalse(self.params("Google", "gemini-3.8-flash", "Off")["thinking_enabled"])

    def test_a_budget_preset_sets_the_budget(self):
        p = self.params("Anthropic", "claude-haiku-4-5-20251001", "16K")
        self.assertEqual((p["thinking_enabled"], p["thinking_budget"]), (True, 16384))

    def test_the_boolean_kinds(self):
        self.assertTrue(self.params("Moonshot", "kimi-k2.6", "On")["thinking_enabled"])
        self.assertFalse(self.params("Moonshot", "kimi-k2.6", "Off")["thinking_enabled"])

    def test_a_model_with_no_thinking_control_runs_with_thinking_off(self):
        p = self.params("OpenAI", "gpt-5.4-chat-latest", "Max")
        self.assertEqual((p["thinking_enabled"], p["thinking_mode"]), (False, "off"))


# ── The switch ──────────────────────────────────────────────────────────

class SwitchTests(unittest.TestCase):

    TARGET = {"model": "claude-fable-5-1", "level": "Max"}

    def test_apply_stashes_the_originals_switches_the_fields_and_says_so(self):
        h = host()
        self.assertTrue(h._upgrade_apply(self.TARGET))
        self.assertEqual(h._upgrade_original, {
            "model": "claude-sonnet-5", "thinking_enabled": True, "thinking_effort": "high",
            "thinking_budget": 8192, "thinking_mode": "adaptive", "call": 12})
        self.assertEqual((h.model, h.thinking_enabled, h.thinking_effort, h.thinking_mode),
                         ("claude-fable-5-1", True, "max", "max"))
        self.assertEqual(h.provider, "Anthropic")
        [line] = drained(h.queue)
        self.assertEqual(line["type"], "warning")
        self.assertIn("claude-sonnet-5 → claude-fable-5-1 (Max), from call #13 on", line["content"])
        self.assertEqual(h.root.scheduled, [h._update_title])      # the Tk thread's job
        self.assertTrue(h._upgrade_active())
        self.assertEqual(h._upgrade_summary_part(), "upgraded-from=claude-sonnet-5@call12")

    def test_the_stash_goes_up_before_the_live_fields_change(self):
        # _save_last_state may run on the Tk thread between the two writes:
        # it must find the stash by the time the model has changed.
        h = host()
        seen = []

        class Watch(_Probe):
            def __setattr__(self, name, value):
                # Only the live instance's model write counts (the probes the
                # level lookups build are Watch instances too).
                if name == "model" and getattr(self, "watched", False):
                    seen.append(getattr(self, "_upgrade_original", None) is not None)
                object.__setattr__(self, name, value)

        w = stub(Watch, **{k: getattr(h, k) for k in (
            "provider", "model", "thinking_enabled", "thinking_effort", "thinking_budget",
            "thinking_mode", "queue", "root", "_run_call_num", "_upgrade_original")})
        w._tool_info = lambda text: None
        w._update_title = lambda: None
        w.watched = True
        w._upgrade_apply(self.TARGET)
        self.assertEqual(seen, [True])

    def test_a_run_upgrades_once(self):
        h = host()
        h._upgrade_apply(self.TARGET)
        drained(h.queue)
        self.assertFalse(h._upgrade_apply({"model": "claude-opus-5-5", "level": "High"}))
        self.assertEqual(h.model, "claude-fable-5-1")
        self.assertEqual(drained(h.queue), [])

    def test_no_target_is_no_switch(self):
        h = host()
        self.assertFalse(h._upgrade_apply(None))
        self.assertFalse(h._upgrade_apply({"model": "", "level": "Max"}))
        self.assertEqual(h.model, "claude-sonnet-5")
        self.assertIsNone(h._upgrade_original)

    def test_end_run_restores_the_originals_once(self):
        h = host()
        h._upgrade_apply(self.TARGET)
        h.root.scheduled.clear()
        h._upgrade_end_run()
        self.assertEqual((h.model, h.thinking_enabled, h.thinking_effort, h.thinking_budget,
                          h.thinking_mode),
                         ("claude-sonnet-5", True, "high", 8192, "adaptive"))
        self.assertIsNone(h._upgrade_original)
        self.assertEqual(h.infos, ["Model restored to claude-sonnet-5 for the next run.\n"])
        self.assertEqual(h.root.scheduled, [h._update_title])
        self.assertEqual(h._upgrade_summary_part(), "")
        h._upgrade_end_run()                    # a run that never upgraded: nothing
        self.assertEqual(len(h.infos), 1)

    def test_the_title_and_cost_log_summary_carries_the_part(self):
        h = host(model="claude-fable-5-1", thinking_mode="max", thinking_effort="max",
                 _upgrade_original={"model": "claude-sonnet-5", "thinking_enabled": True,
                                    "thinking_effort": "high", "thinking_budget": 8192,
                                    "thinking_mode": "adaptive", "call": 12})
        self.assertEqual(h._get_model_param_summary(),
                         "mode=Max upgraded-from=claude-sonnet-5@call12")
        h._upgrade_original = None
        self.assertEqual(h._get_model_param_summary(), "mode=Max")

    def test_the_box_states(self):
        h = host()
        self.assertEqual(h._upgrade_box_state(self.TARGET),
                         (True, False, "Upgrade to Claude Fable 5.1 (Max) for the rest of this run"))
        self.assertEqual(h._upgrade_box_state(None), (False, False,
                         "Upgrade: no upgrade model set for Anthropic (Model Setup, on the main window)"))
        self.assertEqual(h._upgrade_box_state({"model": "claude-sonnet-5", "level": "Max"}),
                         (False, False, "Upgrade: this run is already on claude-sonnet-5"))
        h._upgrade_apply(self.TARGET)
        self.assertEqual(h._upgrade_box_state(self.TARGET), (False, True,
                         "Upgraded to Claude Fable 5.1 (Max) for the rest of this run"))


class StateFileTests(unittest.TestCase):
    """While an upgrade is active the state file goes on describing the
    instruction's model — this method runs every five seconds."""

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state_file = os.path.join(self.folder.name, "agent_state.json")

    def tearDown(self):
        self.folder.cleanup()

    def saved(self, h):
        h._save_last_state()
        with open(self.state_file, encoding="utf-8") as f:
            return json.load(f)

    def test_the_originals_are_written_while_an_upgrade_is_active(self):
        h = _StateHost({}, self.state_file)               # terra, medium
        h.model, h.thinking_mode, h.thinking_effort = "gpt-6-astra", "max", "max"
        h._upgrade_original = {"model": "gpt-5.6-terra", "thinking_enabled": True,
                               "thinking_effort": "medium", "thinking_budget": 8192,
                               "thinking_mode": "medium", "call": 3}
        state = self.saved(h)
        self.assertEqual(state["last_model"], "gpt-5.6-terra")
        self.assertEqual((state["thinking_mode"], state["thinking_effort"]), ("medium", "medium"))
        applied = state["applied_instruction"]
        self.assertEqual((applied["model"], applied["thinking_mode"]), ("gpt-5.6-terra", "medium"))
        self.assertEqual(applied["provider"], "OpenAI")

    def test_without_an_upgrade_the_live_fields_are_written_as_before(self):
        h = _StateHost({}, self.state_file)
        h.model, h.thinking_mode = "gpt-6-astra", "max"
        state = self.saved(h)
        self.assertEqual((state["last_model"], state["thinking_mode"]), ("gpt-6-astra", "max"))
        self.assertEqual(state["applied_instruction"]["model"], "gpt-6-astra")


# ── The Agent Request dialog's box (the real dialog) ────────────────────

class _DialogHost(SafetyMixin, _Probe):
    """do_user_prompt's host: the dialog and the Upgrade row are the real
    ones (over the real detection helpers, which the row's probe needs); the
    voice row, geometry persistence and the settings file are stand-ins.
    `applied` records what the worker-side switch was handed."""

    _headless = True
    _prompt_dialog = None

    def __init__(self, root, target):
        self.root = root
        self.queue = queue.Queue()
        self.dictation_auto_send = tk.BooleanVar(master=root, value=False)
        self.send = None
        self.target = target
        self.applied = []
        self.provider, self.model = "Anthropic", "claude-sonnet-5"
        self.thinking_enabled, self.thinking_mode = True, "adaptive"
        self._upgrade_original = None

    def _get_display_name(self, model_id):
        return model_id

    def _upgrade_target(self, provider=None):
        return self.target

    def _upgrade_apply(self, target):
        self.applied.append(target)
        return True

    def _voice_build_row(self, dlg, resp_text, auto_send, send):
        row = tk.Frame(dlg)
        mike = tk.Button(row, text="Mike")
        mike.pack(side=tk.LEFT)
        auto = tk.Checkbutton(row, text="Auto-send", variable=auto_send)
        auto.pack(side=tk.LEFT)
        self.send = send
        return row, mike, auto, _Dictation()

    def _place_window(self, win, kind, default_size, **_kwargs):
        win.attributes("-alpha", 0.0)
        win.geometry("420x460+0+0")

    def _remember_geometry(self, kind, win):
        pass


class DialogTests(unittest.TestCase):

    TARGET = {"model": "claude-fable-5-1", "level": "Max"}

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.geometry("240x240+0+0")
        self.root.attributes("-alpha", 0.0)

    def tearDown(self):
        self.root.destroy()

    def run_dialog(self, target, act, upgraded=False):
        host = self.host = _DialogHost(self.root, target)
        if upgraded:
            host._upgrade_original = {"model": "claude-sonnet-5", "call": 4}
            host.model, host.thinking_mode = "claude-fable-5-1", "max"
        outcome = {}

        def work():
            try:
                outcome["reply"] = host.do_user_prompt("What next?")
            except Exception as exc:
                outcome["error"] = exc

        worker = threading.Thread(target=work, daemon=True)

        def wait_for_dialog():
            dialog = host._prompt_dialog
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
        self.root.after(20, wait_for_dialog)
        self.root.after(40, wait_for_worker)
        watchdog = self.root.after(20000, self.root.quit)
        self.root.mainloop()
        self.root.after_cancel(watchdog)
        self.assertNotIn("error", outcome)
        self.assertIn("reply", outcome, "the dialog never closed")
        return host, outcome

    @staticmethod
    def parts(dialog):
        boxes = [w for w in _walk(dialog) if isinstance(w, tk.Checkbutton)]
        upgrade = [b for b in boxes if b.cget("text").startswith("Upgrade")][0]
        reply = [w for w in _walk(dialog)
                 if isinstance(w, tk.Text) and str(w.cget("state")) == "normal"][0]
        return upgrade, reply

    def test_ticked_and_sent_the_reply_upgrades_the_run(self):
        state = {}

        def act(dialog):
            box, reply = self.parts(dialog)
            state["label"] = box.cget("text")
            state["state"] = str(box.cget("state"))
            # Between the voice row and the image row, as a Tab would find it.
            rows = {str(w.master.grid_info().get("row")): w.master for w in (box,)}
            state["row"] = list(rows)[0]
            state["attach_row"] = str([w for w in _walk(dialog) if isinstance(w, tk.Button)
                                       and w.cget("text") == "Attach Images"][0]
                                      .master.grid_info().get("row"))
            box.invoke()                                     # tick
            reply.insert("1.0", "tighten the skills you used")
            self.host.send()                                 # the dialog's own send path

        host, outcome = self.run_dialog(self.TARGET, act)
        self.assertEqual(outcome["reply"], "tighten the skills you used")
        self.assertEqual(state["label"], "Upgrade to claude-fable-5-1 (Max) for the rest of this run")
        self.assertEqual(state["state"], "normal")
        self.assertEqual((state["row"], state["attach_row"]), ("5", "6"))
        self.assertEqual(host.applied, [self.TARGET])
        self.assertEqual([m["type"] for m in drained(host.queue)],
                         ["user_prompt_request", "user_prompt_echo"])

    def test_unticked_the_run_stays_on_its_model(self):
        def act(dialog):
            box, reply = self.parts(dialog)
            reply.insert("1.0", "carry on")
            self.host.send()

        host, outcome = self.run_dialog(self.TARGET, act)
        self.assertEqual(outcome["reply"], "carry on")
        self.assertEqual(host.applied, [])

    def test_ticked_but_dismissed_or_empty_upgrades_nothing(self):
        def dismiss(dialog):
            box, reply = self.parts(dialog)
            box.invoke()
            dialog.tk.call(dialog.protocol("WM_DELETE_WINDOW"))

        host, outcome = self.run_dialog(self.TARGET, dismiss)
        self.assertEqual(host.applied, [])

        def empty(dialog):
            box, reply = self.parts(dialog)
            box.invoke()
            self.host.send()                # an empty reply stops the agent

        host, outcome = self.run_dialog(self.TARGET, empty)
        self.assertEqual(outcome["reply"], "")
        self.assertEqual(host.applied, [])

    def test_with_no_target_the_box_is_disabled_and_names_model_setup(self):
        state = {}

        def act(dialog):
            box, reply = self.parts(dialog)
            state["label"], state["state"] = box.cget("text"), str(box.cget("state"))
            reply.insert("1.0", "go on")
            self.host.send()

        host, outcome = self.run_dialog(None, act)
        self.assertEqual(state["state"], "disabled")
        self.assertIn("Model Setup", state["label"])
        self.assertEqual(host.applied, [])

    def test_an_upgraded_run_shows_the_box_ticked_and_disabled(self):
        state = {}

        def act(dialog):
            box, reply = self.parts(dialog)
            var_name = str(box.cget("variable"))
            state["ticked"] = bool(int(dialog.getvar(var_name)))
            state["label"], state["state"] = box.cget("text"), str(box.cget("state"))
            reply.insert("1.0", "and again")
            self.host.send()

        host, outcome = self.run_dialog(self.TARGET, act, upgraded=True)
        self.assertEqual((state["ticked"], state["state"]), (True, "disabled"))
        self.assertEqual(state["label"], "Upgraded to claude-fable-5-1 (Max) for the rest of this run")
        self.assertEqual(host.applied, [])


# ── Model Setup (real widgets) ──────────────────────────────────────────

class _SetupHost(_Probe):
    pass


class SetupDialogTests(unittest.TestCase):

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        # Mapped but invisible: the dialog is transient to the root, and a
        # transient of a withdrawn window never maps (wait_visibility would
        # block for ever).
        self.root.geometry("240x240+0+0")
        self.root.attributes("-alpha", 0.0)
        self.folder = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.folder.name, "config.json")
        self.patch = mock.patch.object(mu, "UPGRADE_CONFIG_FILE", self.path)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.folder.cleanup()
        self.root.destroy()

    def open(self, act, provider="Anthropic"):
        h = stub(_SetupHost, provider=provider, model="claude-sonnet-5", root=self.root,
                 _has_anthropic=True, _has_openai=True, _has_gemini=False, _has_xai=False,
                 _has_kimi=False, _has_ollama=False,
                 available_models=["claude-opus-5", "claude-fable-5-1", "claude-sonnet-5"],
                 _upgrade_models_fetched={"OpenAI": ["gpt-5.6-terra", "gpt-6-astra"]},
                 _upgrade_setup_dialog=None)
        h._place_window = lambda win, kind, size, parent=None: "+0+0"
        h._parse_geometry = lambda geo: None
        self.host = h

        def when_up():
            # Once it is on screen: acting on the withdrawn window would
            # destroy it under its own wait_visibility.
            dlg = h._upgrade_setup_dialog
            if dlg is not None and dlg.winfo_exists() and dlg.winfo_ismapped():
                act(dlg)
            else:
                self.root.after(20, when_up)

        self.root.after(20, when_up)
        watchdog = self.root.after(15000, lambda: h._upgrade_setup_dialog.destroy())
        h._open_upgrade_setup(self.root)
        self.root.after_cancel(watchdog)
        return h

    @staticmethod
    def widgets(dlg):
        combos = [w for w in _walk(dlg) if isinstance(w, ttk.Combobox)]
        buttons = {w.cget("text"): w for w in _walk(dlg) if isinstance(w, tk.Button)}
        return combos, buttons

    @staticmethod
    def pick(combo, value):
        combo.set(value)
        combo.event_generate("<<ComboboxSelected>>")

    def test_the_saved_target_is_shown_and_the_levels_follow_the_model(self):
        seen = {}

        def act(dlg):
            (provider, model, level), buttons = self.widgets(dlg)
            seen["provider"] = provider.get()
            seen["model"], seen["level"] = model.get(), level.get()
            seen["level_values"] = list(level.cget("values"))
            seen["model_values"] = list(model.cget("values"))[:2]
            self.pick(model, "claude-opus-5")
            seen["after"] = (level.get(), list(level.cget("values")))
            buttons["Cancel"].invoke()

        self.open(act)
        self.assertEqual(seen["provider"], "Anthropic")
        self.assertEqual((seen["model"], seen["level"]), ("claude-fable-5-1", "Max"))   # the default
        self.assertEqual(seen["level_values"], ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(seen["model_values"], [UPGRADE_NO_MODEL_LABEL, "claude-opus-5"])
        # A fresh pick lands on the model's strongest level, from its own rungs.
        self.assertEqual(seen["after"], ("Max", ["Off", "Adaptive", "Low", "Medium", "High",
                                                "Xhigh", "Max"]))
        self.assertFalse(os.path.exists(self.path))          # Cancel saved nothing

    def test_save_keeps_every_providers_draft_and_none_clears_one(self):
        def act(dlg):
            (provider, model, level), buttons = self.widgets(dlg)
            self.pick(model, "claude-opus-5-5")
            self.pick(level, "High")
            self.pick(provider, "OpenAI")
            self.assertEqual(model.get(), "gpt-6-astra")        # OpenAI's saved default
            self.pick(model, UPGRADE_NO_MODEL_LABEL)
            self.assertEqual(str(level.cget("state")), "disabled")
            buttons["Save"].invoke()

        self.open(act)
        self.assertEqual(ModelUpgradeMixin._upgrade_load_config(self.path)["targets"],
                         {"Anthropic": {"model": "claude-opus-5-5", "level": "High"}})


# ── Wiring ──────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):

    def src(self, *parts):
        return (REPO.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_dialog_builds_the_row_and_applies_the_switch_on_the_worker(self):
        src = self.src("myagent", "safety_mixin.py")
        # Between the voice row and the image row: Tab order is creation order.
        self.assertLess(src.index("self._voice_build_row("), src.index("self._upgrade_build_row(dlg)"))
        self.assertLess(src.index("self._upgrade_build_row(dlg)"), src.index("attach_btn = tk.Button("))
        self.assertIn('"u": upgrade_btn', src)
        # Read when the reply is sent; applied after the wait, on the worker,
        # only for a sent, non-empty reply.
        inject = src[src.index("def on_inject("):src.index("def on_close(")]
        self.assertIn("result_holder[2] = upgrade_target is not None and bool(upgrade_var.get())",
                      inject)
        tail = src[src.index("event.wait()"):src.index("def _take_prompt_images(")]
        self.assertIn("if result_holder[2] and response.strip():", tail)
        self.assertIn("self._upgrade_apply(self._upgrade_target())", tail)
        self.assertLess(tail.index('"user_prompt_echo"'), tail.index("self._upgrade_apply("))

    def test_the_loop_counts_calls_for_it_and_restores_at_both_ends(self):
        src = self.src("myagent", "streaming_mixin.py")
        worker = src[src.index("def stream_worker("):]
        self.assertIn("self._run_call_num = call_num", worker)
        # Both loop-end tails go through _end_run: the cost log line first
        # (the run under the model it ended on), then the restore, then the
        # result file.
        end_run = worker[worker.index("def _end_run("):worker.index("try:")]
        self.assertLess(end_run.index("_log_run()"), end_run.index("_upgrade_end_run"))
        self.assertEqual(worker.count("            _end_run()\n"), 2)
        for tail in worker.split("            _end_run()\n")[1:]:
            self.assertLess(tail.index("_write_result_file("), tail.index("_on_close"))
        # Never logged without the restore: _end_run is the only caller.
        self.assertEqual(worker.replace(end_run, "").count("_log_run()"), 1)   # its def alone

    def test_the_state_file_reads_the_stash(self):
        src = self.src("myagent", "state_mixin.py")
        save = src[src.index("def _save_last_state("):src.index("def _load_last_state(")]
        self.assertIn('kept = getattr(self, "_upgrade_original", None) or {}', save)
        self.assertEqual(save.count('"last_model": model_fields["model"]'), 1)
        self.assertEqual(save.count('"model": model_fields["model"]'), 1)
        self.assertNotIn('"last_model": self.model', save)

    def test_the_main_window_carries_the_button_and_the_summary_the_part(self):
        src = self.src("myagent", "ui_mixin.py")
        self.assertIn("command=self._upgrade_setup_from_main", src)
        self.assertIn('"m": self.model_setup_button,', src)
        self.assertLess(src.index("self.voice_setup_button = tk.Button("),
                        src.index("self.model_setup_button = tk.Button("))
        self.assertLess(src.index("self.model_setup_button = tk.Button("),
                        src.index("self.debug_toggle = tk.Checkbutton("))
        summary = src[src.index("def _get_model_param_summary("):src.index("def _update_title(")]
        self.assertIn("_upgrade_summary_part", summary)

    def test_the_app_inherits_the_mixin_and_starts_with_no_upgrade(self):
        src = self.src("MyAgent.py")
        self.assertIn("from myagent.model_upgrade_mixin import ModelUpgradeMixin", src)
        bases = src[src.index("class App("):src.index("def __init__")]
        self.assertIn("ModelUpgradeMixin", bases)
        init = src[src.index("def __init__"):]
        self.assertIn("self._upgrade_original = None", init)
        self.assertIn("self._run_call_num = 0", init)

    def test_the_upgrade_never_reaches_the_instruction_store(self):
        module = self.src("myagent", "model_upgrade_mixin.py")
        for forbidden in ("_save_instructions_to_disk(", "_load_saved_instructions(",
                          "save_store(", "agent_instructions", "self._save_last_state("):
            self.assertNotIn(forbidden, module)
        for name in ("instructions_mixin.py", "instruction_layout.py", "datapaths.py",
                     "skills_mixin.py"):
            self.assertNotIn("upgrade", self.src("myagent", name).lower(), name)
        self.assertNotIn("upgrade", self.src("Heartbeat.py").lower())
        self.assertEqual(UPGRADE_FIELDS, ("model", "thinking_enabled", "thinking_effort",
                                          "thinking_budget", "thinking_mode"))


if __name__ == "__main__":
    unittest.main()
