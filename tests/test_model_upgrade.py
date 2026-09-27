"""Characterization tests for the model upgrade (myagent/model_upgrade_mixin.py,
2026-09-27): the Agent Request dialog's Upgrade box moves a run to a stronger
model of the same provider from that reply on; WHICH model is a setting of the
instruction (`upgrade_target`, edited in Model Setup — a button beside Voice
Setup — and written through to the instruction's store entry, the Auto-send
way; per-user at first, per instruction by the user's decision the same day).

Pinned here, without a key or the network:

* the instruction's setting: sanitising, the target a run gets (its own, an
  explicit "no upgrade", the provider default — also for a target saved for
  another provider), which instruction Model Setup edits (the editor's page
  while the editor is open, else the applied one), and the write-through;
* every per-instruction site carries `upgrade_target` (a static scan, the
  Auto-send list) and manage_instructions reads / creates / updates it;
* the levels a model is offered — the instruction editor's own rungs for it,
  via a probe that never touches the live app — across every provider's
  kinds, and how a saved level lands on a model that lacks it;
* what a level means: the four live thinking fields, as the editor's three
  handlers would leave them;
* the switch on a bare host: the originals stashed BEFORE the live fields
  change, the pane line, the title refresh scheduled on the Tk thread, one
  upgrade per run, the restore at the loop's end, the title / cost-log part;
* the state file: while an upgrade is active `_save_last_state` writes the
  run's OWN model fields, never the upgrade's; the applied snapshot carries
  the instruction's upgrade_target;
* the dialog's box on the REAL Agent Request dialog (inside a real mainloop
  with do_user_prompt on a worker thread, as a run calls it): its states,
  and the switch applied — to the target the label named — only for a sent,
  non-empty reply with the box ticked;
* Model Setup on real widgets: the default shown for an instruction without
  its own, levels following the model, Save written through to the right
  instruction, "(none)" an explicit no-upgrade, Cancel keeping nothing;
* the wiring (a static scan): the row between the voice row and the image
  row, the mnemonic, the worker-side apply, the loop's two restore sites,
  the state file's stash read, the main window's button, the App's bases —
  and a switch that never persists anything.
"""

import gc
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
from myagent.constants import META_TOOLS, UPGRADE_DEFAULT_TARGETS, UPGRADE_NO_MODEL_LABEL
from myagent.gemini_mixin import GeminiMixin
from myagent.instructions_mixin import InstructionsMixin
from myagent.kimi_mixin import KimiMixin
from myagent.model_upgrade_mixin import ModelUpgradeMixin, UPGRADE_FIELDS
from myagent.ollama_mixin import OllamaMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.safety_mixin import SafetyMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin
from myagent.xai_mixin import XAIMixin

REPO = pathlib.Path(__file__).resolve().parents[1]


class _Probe(ModelUpgradeMixin, UIMixin, OpenAIMixin, XAIMixin, KimiMixin,
             GeminiMixin, OllamaMixin, StreamingMixin):
    """The detection helpers the mixin's probe consults, and the pricing
    checks the switch runs, as the App carries them."""


def probe(**attrs):
    attrs.setdefault("provider", "Anthropic")
    attrs.setdefault("model", "claude-sonnet-5")
    return stub(_Probe, **attrs)


class _Root:
    def __init__(self):
        self.scheduled = []

    def after(self, delay, fn):
        self.scheduled.append(fn)


class _Window:
    """An open (or closed) Instruction Editor, as far as winfo_exists goes."""

    def __init__(self, open_=True):
        self.open = open_

    def winfo_exists(self):
        return self.open


class _Host(_Probe):
    """The stand-ins are METHODS, not lambdas closing over the instance: a
    closure over its own host is a reference cycle, freed only by the cyclic
    collector — on whatever thread next allocates — and a host holding a Tk
    root or variable must never be finalised off the main thread (it killed
    test_physical_mixin's capture thread in the full suite: "main thread is
    not in main loop", "Tcl_AsyncDelete: async handler deleted by the wrong
    thread")."""

    def _tool_info(self, text):
        self.infos.append(text)

    def _update_title(self):
        pass

    def _get_display_name(self, model_id):
        return {"claude-fable-5-1": "Claude Fable 5.1"}.get(model_id, model_id)

    def _load_saved_instructions(self):
        return json.loads(json.dumps(self.store))

    def _save_instructions_to_disk(self, data):
        self.writes.append(data)
        self.store = data

    def _save_last_state(self):
        self.state_saves.append(True)


def host(**attrs):
    """A run mid-way: Sonnet 5, adaptive, twelve calls in, applied instruction
    "Balance" with a store of its own."""
    base = dict(provider="Anthropic", model="claude-sonnet-5", thinking_enabled=True,
                thinking_effort="high", thinking_budget=8192, thinking_mode="adaptive",
                temperature=1.0, fast_mode=False, _anthropic_unsupported=set(),
                queue=queue.Queue(), root=_Root(), _run_call_num=12, _upgrade_original=None,
                upgrade_target=None, agent_instruction_name="Balance",
                instruction_editor_window=None, _instr_shown_name="",
                infos=[], store={"Balance": {"text": "t", "provider": "Anthropic"},
                                 "Other": {"text": "o", "provider": "Anthropic"}},
                writes=[], state_saves=[])
    base.update(attrs)
    return stub(_Host, **base)


def free_tk_garbage_then_destroy(test):
    """tearDown for the real-widget tests: drop the host, collect its cycles
    HERE, on the main thread with the interpreter alive — the dialog's
    closures reach the host and its Tk variables — and only then destroy
    the root."""
    test.host = None
    gc.collect()
    test.root.destroy()


def drained(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


# ── The instruction's setting ──────────────────────────────────────────

class TargetTests(unittest.TestCase):

    def test_sanitising(self):
        s = ModelUpgradeMixin._upgrade_sanitize_target
        self.assertIsNone(s(None))
        self.assertIsNone(s("claude-fable-5-1"))
        self.assertIsNone(s({"provider": "Bedrock", "model": "x"}))       # unknown provider
        self.assertIsNone(s({"model": "x"}))                               # no provider at all
        self.assertEqual(s({"model": " claude-opus-5-5 ", "level": " High "}, "Anthropic"),
                         {"provider": "Anthropic", "model": "claude-opus-5-5", "level": "High"})
        # An explicit "no upgrade": the model is "", and so is its level.
        self.assertEqual(s({"provider": "OpenAI", "model": "", "level": "Max"}),
                         {"provider": "OpenAI", "model": "", "level": ""})
        # A value's own provider wins over the fallback.
        self.assertEqual(s({"provider": "OpenAI", "model": "gpt-6-astra"}, "Anthropic")["provider"],
                         "OpenAI")

    def test_an_instruction_without_its_own_gets_the_provider_default(self):
        self.assertEqual(host()._upgrade_target(), UPGRADE_DEFAULT_TARGETS["Anthropic"])
        self.assertEqual(host(provider="OpenAI", model="gpt-5.6-terra")._upgrade_target(),
                         {"model": "gpt-6-astra", "level": "Max"})
        self.assertIsNone(host(provider="Google", model="gemini-3.8-flash")._upgrade_target())

    def test_its_own_wins_and_an_explicit_none_means_no_upgrade(self):
        own = {"provider": "Anthropic", "model": "claude-opus-5-5", "level": "High"}
        self.assertEqual(host(upgrade_target=own)._upgrade_target(),
                         {"model": "claude-opus-5-5", "level": "High"})
        none = {"provider": "Anthropic", "model": "", "level": ""}
        self.assertIsNone(host(upgrade_target=none)._upgrade_target())

    def test_a_target_saved_for_another_provider_does_not_apply(self):
        # The instruction's provider was changed after its upgrade model was set.
        h = host(upgrade_target={"provider": "OpenAI", "model": "gpt-6-astra", "level": "Max"})
        self.assertIsNone(h._upgrade_own_target())
        self.assertEqual(h._upgrade_target(), UPGRADE_DEFAULT_TARGETS["Anthropic"])

    def test_model_setup_edits_the_instruction_whose_settings_are_live(self):
        self.assertEqual(host()._upgrade_instruction_name(), "Balance")          # applied
        self.assertEqual(host(instruction_editor_window=_Window(False))._upgrade_instruction_name(),
                         "Balance")                                               # editor closed
        editing = host(instruction_editor_window=_Window(), _instr_shown_name="Other")
        self.assertEqual(editing._upgrade_instruction_name(), "Other")           # the editor's page
        unsaved = host(instruction_editor_window=_Window(), _instr_shown_name="")
        self.assertEqual(unsaved._upgrade_instruction_name(), "")
        self.assertEqual(host(agent_instruction_name="")._upgrade_instruction_name(), "")

    def test_save_writes_through_one_key_of_one_entry(self):
        h = host()
        value = {"provider": "Anthropic", "model": "claude-opus-5-5", "level": "High"}
        h._upgrade_set_target(value)
        self.assertEqual(h.upgrade_target, value)
        self.assertEqual(len(h.writes), 1)
        self.assertEqual(h.store["Balance"], {"text": "t", "provider": "Anthropic",
                                              "upgrade_target": value})
        self.assertEqual(h.store["Other"], {"text": "o", "provider": "Anthropic"})
        self.assertEqual(len(h.state_saves), 1)                  # the applied snapshot too
        h._upgrade_set_target(dict(value))                       # already held: no rewrite
        self.assertEqual(len(h.writes), 1)
        self.assertEqual(len(h.state_saves), 2)

    def test_an_unsaved_instruction_keeps_it_for_the_session_and_the_snapshot(self):
        h = host(agent_instruction_name="")
        value = {"provider": "Anthropic", "model": "", "level": ""}
        h._upgrade_set_target(value)
        self.assertEqual((h.upgrade_target, h.writes, len(h.state_saves)), (value, [], 1))

    def test_with_the_editor_open_its_page_is_written(self):
        h = host(instruction_editor_window=_Window(), _instr_shown_name="Other")
        value = {"provider": "Anthropic", "model": "claude-fable-5-1", "level": "Xhigh"}
        h._upgrade_set_target(value)
        self.assertEqual(h.store["Other"]["upgrade_target"], value)
        self.assertNotIn("upgrade_target", h.store["Balance"])

    def test_what_model_setup_opens_on(self):
        self.assertEqual(host()._upgrade_setup_draft(),
                         ({"provider": "Anthropic", "model": "claude-fable-5-1", "level": "Max"},
                          "default"))
        own = {"provider": "Anthropic", "model": "claude-opus-5-5", "level": "High"}
        self.assertEqual(host(upgrade_target=own)._upgrade_setup_draft(), (own, "own"))
        none = {"provider": "Anthropic", "model": "", "level": ""}
        self.assertEqual(host(upgrade_target=none)._upgrade_setup_draft(), (none, "none"))
        self.assertEqual(host(provider="Google")._upgrade_setup_draft(),
                         ({"provider": "Google", "model": "", "level": ""}, "none"))


class ManageInstructionsTests(unittest.TestCase):
    """The meta tool reads, creates and updates the setting like the other
    per-instruction keys (it never touches the live session)."""

    class _Host(InstructionsMixin, ModelUpgradeMixin):
        pass

    def tool_host(self, store):
        h = stub(self._Host, provider="Anthropic", model="claude-sonnet-5", temperature=1.0,
                 thinking_enabled=True, thinking_effort="low", thinking_budget=8192,
                 thinking_mode="low", text_verbosity="medium", fast_mode=False, skills={},
                 _disabled_confirm_patterns=set(), _blocked_tools=set(),
                 agent_instruction_name="", upgrade_target=None)
        h.store = store
        h._load_saved_instructions = lambda: h.store
        h._save_instructions_to_disk = lambda data: setattr(h, "store", data)
        return h

    def test_create_read_update(self):
        h = self.tool_host({})
        h.do_manage_instructions({"action": "create", "name": "New", "text": "do it",
                                  "upgrade_target": {"model": "claude-opus-5-5", "level": "High"}})
        self.assertEqual(h.store["New"]["upgrade_target"],
                         {"provider": "Anthropic", "model": "claude-opus-5-5", "level": "High"})
        h.do_manage_instructions({"action": "create", "name": "Plain", "text": "do it"})
        self.assertIsNone(h.store["Plain"]["upgrade_target"])            # the provider default
        read = json.loads(h.do_manage_instructions({"action": "read", "name": "New"}))
        self.assertEqual(read["upgrade_target"]["model"], "claude-opus-5-5")
        # An update that also moves the instruction to OpenAI: the target is
        # the new provider's.
        h.do_manage_instructions({"action": "update", "name": "New", "provider": "OpenAI",
                                  "model": "gpt-5.6-terra",
                                  "upgrade_target": {"model": "gpt-6-astra", "level": "Xhigh"}})
        self.assertEqual(h.store["New"]["upgrade_target"],
                         {"provider": "OpenAI", "model": "gpt-6-astra", "level": "Xhigh"})
        self.assertIsNone(h.upgrade_target)                              # the session untouched
        # upgrade_target alone is enough for an update.
        result = h.do_manage_instructions({"action": "update", "name": "Plain",
                                           "upgrade_target": {"model": ""}})
        self.assertIn("updated", result)
        self.assertEqual(h.store["Plain"]["upgrade_target"],
                         {"provider": "Anthropic", "model": "", "level": ""})

    def test_the_schema_offers_it(self):
        tool = next(t for t in META_TOOLS if t["name"] == "manage_instructions")
        prop = tool["input_schema"]["properties"]["upgrade_target"]
        self.assertEqual(prop["type"], "object")
        self.assertEqual(set(prop["properties"]), {"model", "level"})


class PersistenceScanTests(unittest.TestCase):
    """`upgrade_target` at every site the Auto-send tick passes through."""

    @staticmethod
    def between(src, start, end):
        return src[src.index(start):src.index(end)]

    def test_every_per_instruction_site(self):
        instr = (REPO / "myagent" / "instructions_mixin.py").read_text(encoding="utf-8")
        state = (REPO / "myagent" / "state_mixin.py").read_text(encoding="utf-8")
        self.assertIn('"upgrade_target": getattr(self, "upgrade_target", None),',
                      self.between(instr, "def _save_instruction(", "def _delete_instruction("))
        self.assertIn("self.upgrade_target = None",
                      self.between(instr, "def _clear_instruction_editor(", "def _on_instruction_selected("))
        self.assertIn('self.upgrade_target = entry.get("upgrade_target")',
                      self.between(instr, "def _on_instruction_selected(", "def _apply_instruction("))
        manage = self.between(instr, "def do_manage_instructions(", "def open_instruction_editor(")
        self.assertIn('"upgrade_target": entry.get("upgrade_target"),', manage)          # read
        self.assertIn('params.get("upgrade_target"), self.provider)', manage)            # create
        self.assertIn('"dictation_auto_send", "upgrade_target",', manage)               # update
        self.assertIn('"upgrade_target": getattr(self, "upgrade_target", None),',
                      self.between(state, "def _save_last_state(", "def _load_last_state("))
        self.assertIn('self.upgrade_target = entry.get("upgrade_target")',
                      self.between(state, "def _apply_instruction_entry(", "def _merge_extra_text("))
        init = (REPO / "MyAgent.py").read_text(encoding="utf-8")
        self.assertIn("self.upgrade_target = None", init[init.index("def __init__"):])


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
        return host()._upgrade_params_for(provider, model, level)

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
        # The switch is the RUN's: the instruction's setting and the store are untouched.
        self.assertIsNone(h.upgrade_target)
        self.assertEqual((h.writes, h.state_saves), ([], []))

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

    def test_an_unpriced_upgrade_model_is_announced_at_the_switch(self):
        # The run-start check only saw the model the run began on; the switch
        # repeats it for the new one — with the mid-run consequence, not the
        # run-start text ("will NOT be written to the cost log" is untrue now).
        h = host()
        h._upgrade_apply({"model": "claude-nova-9", "level": ""})
        upgraded, warning = drained(h.queue)
        self.assertTrue(upgraded["content"].startswith("⬆ Model upgraded"))
        self.assertEqual(warning["type"], "warning")
        self.assertIn("claude-nova-9 has no row in the Anthropic pricing table", warning["content"])
        self.assertIn("count only the calls before the switch", warning["content"])
        self.assertNotIn("NOT be written", warning["content"])

    def test_a_model_priced_by_a_family_catch_all_row_says_so(self):
        h = host(provider="Google", model="gemini-3.8-flash")
        h._upgrade_apply({"model": "gemini-3.9-flash", "level": "High"})
        _upgraded, warning = drained(h.queue)
        self.assertIn("generic 'gemini-3' fallback row", warning["content"])

    def test_the_fast_table_is_what_a_fast_run_needs(self):
        # Fast stays on across a switch; a fast-capable model with no FAST row
        # would be unpriced for this run however ordinary its standard row.
        h = host(model="claude-opus-4-8", fast_mode=True)
        with mock.patch.dict("myagent.streaming_mixin.ANTHROPIC_FAST_PRICING", {}, clear=True):
            h._upgrade_apply({"model": "claude-opus-5-5", "level": "High"})
        _upgraded, warning = drained(h.queue)
        self.assertIn("no row in the ANTHROPIC_FAST_PRICING", warning["content"])

    def test_a_priced_upgrade_model_gets_the_switch_line_alone(self):
        for provider, model, target in (("Anthropic", "claude-sonnet-5", "claude-fable-5-1"),
                                        ("OpenAI", "gpt-5.6-terra", "gpt-6-astra"),
                                        ("xAI", "grok-4.3", "grok-no-row-needed")):
            with self.subTest(target):
                h = host(provider=provider, model=model)
                h._upgrade_apply({"model": target, "level": ""})
                self.assertEqual(len(drained(h.queue)), 1)   # xAI reports its own cost

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
                         "Upgrade: no upgrade model set for this instruction "
                         "(Model Setup, on the main window)"))
        self.assertEqual(h._upgrade_box_state({"model": "claude-sonnet-5", "level": "Max"}),
                         (False, False, "Upgrade: this run is already on claude-sonnet-5"))
        h._upgrade_apply(self.TARGET)
        self.assertEqual(h._upgrade_box_state(self.TARGET), (False, True,
                         "Upgraded to Claude Fable 5.1 (Max) for the rest of this run"))


class StateFileTests(unittest.TestCase):
    """While an upgrade is active the state file goes on describing the
    instruction's model — this method runs every five seconds — and the
    applied snapshot carries the instruction's upgrade model."""

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

    def test_the_applied_snapshot_carries_the_instructions_upgrade_model(self):
        h = _StateHost({}, self.state_file)
        self.assertIsNone(self.saved(h)["applied_instruction"]["upgrade_target"])
        h.upgrade_target = {"provider": "OpenAI", "model": "gpt-6-astra", "level": "Xhigh"}
        self.assertEqual(self.saved(h)["applied_instruction"]["upgrade_target"], h.upgrade_target)


# ── The Agent Request dialog's box (the real dialog) ────────────────────

class _DialogHost(SafetyMixin, _Probe):
    """do_user_prompt's host: the dialog and the Upgrade row are the real
    ones (over the real detection helpers, which the row's probe needs); the
    voice row, geometry persistence and the target are stand-ins. `applied`
    records what the worker-side switch was handed."""

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

    def _upgrade_target(self):
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
        free_tk_garbage_then_destroy(self)

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

    def test_ticked_and_sent_the_reply_upgrades_the_run_to_the_named_target(self):
        state = {}

        def act(dialog):
            box, reply = self.parts(dialog)
            state["label"] = box.cget("text")
            state["state"] = str(box.cget("state"))
            # Between the voice row and the image row, as a Tab would find it.
            state["row"] = str(box.master.grid_info().get("row"))
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
        self.assertIn("this instruction", state["label"])
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

    def tearDown(self):
        free_tk_garbage_then_destroy(self)

    def open(self, act, **attrs):
        h = host(root=self.root,
                 available_models=["claude-opus-5-5", "claude-opus-5", "claude-fable-5-1",
                                   "claude-sonnet-5"],
                 _upgrade_setup_dialog=None, **attrs)
        h._place_window = lambda win, kind, size, parent=None: "+0+0"
        h._parse_geometry = lambda geo: None
        seen = {}

        def when_up():
            # Once it is on screen: acting on the withdrawn window would
            # destroy it under its own wait_visibility.
            dlg = h._upgrade_setup_dialog
            if dlg is not None and dlg.winfo_exists() and dlg.winfo_ismapped():
                seen["title"] = dlg.title()
                act(dlg, seen)
            else:
                self.root.after(20, when_up)

        self.root.after(20, when_up)
        watchdog = self.root.after(15000, lambda: h._upgrade_setup_dialog.destroy())
        h._open_upgrade_setup(self.root)
        self.root.after_cancel(watchdog)
        return h, seen

    @staticmethod
    def widgets(dlg):
        combos = [w for w in _walk(dlg) if isinstance(w, ttk.Combobox)]
        buttons = {w.cget("text"): w for w in _walk(dlg) if isinstance(w, tk.Button)}
        labels = [w.cget("text") for w in _walk(dlg) if isinstance(w, tk.Label)]
        return combos, buttons, labels

    @staticmethod
    def pick(combo, value):
        combo.set(value)
        combo.event_generate("<<ComboboxSelected>>")

    def test_an_instruction_without_its_own_opens_on_the_default_and_cancel_keeps_nothing(self):
        def act(dlg, seen):
            (model, level), buttons, labels = self.widgets(dlg)
            seen["model"], seen["level"] = model.get(), level.get()
            seen["levels"] = list(level.cget("values"))
            seen["first_values"] = list(model.cget("values"))[:2]
            seen["labels"] = labels
            self.pick(model, "claude-opus-5")
            seen["after"] = (level.get(), list(level.cget("values")))
            buttons["Cancel"].invoke()

        h, seen = self.open(act)
        self.assertEqual(seen["title"], "Model Setup — Balance")
        self.assertEqual((seen["model"], seen["level"]), ("claude-fable-5-1", "Max"))
        self.assertEqual(seen["levels"], ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(seen["first_values"], [UPGRADE_NO_MODEL_LABEL, "claude-opus-5-5"])
        self.assertTrue(any("no upgrade model of its own yet" in t for t in seen["labels"]))
        # A fresh pick lands on the model's strongest level, from its own rungs.
        self.assertEqual(seen["after"], ("Max", ["Off", "Adaptive", "Low", "Medium", "High",
                                                "Xhigh", "Max"]))
        self.assertIsNone(h.upgrade_target)
        self.assertEqual((h.writes, h.state_saves), ([], []))

    def test_save_writes_the_choice_through_to_the_instruction(self):
        def act(dlg, seen):
            (model, level), buttons, _labels = self.widgets(dlg)
            self.pick(model, "claude-opus-5-5")
            self.pick(level, "High")
            buttons["Save"].invoke()

        h, seen = self.open(act)
        value = {"provider": "Anthropic", "model": "claude-opus-5-5", "level": "High"}
        self.assertEqual(h.upgrade_target, value)
        self.assertEqual(h.store["Balance"]["upgrade_target"], value)
        self.assertEqual(len(h.state_saves), 1)
        self.assertEqual(h._upgrade_target(), {"model": "claude-opus-5-5", "level": "High"})

    def test_none_is_an_explicit_no_upgrade(self):
        def act(dlg, seen):
            (model, level), buttons, _labels = self.widgets(dlg)
            self.pick(model, UPGRADE_NO_MODEL_LABEL)
            seen["level_state"] = str(level.cget("state"))
            buttons["Save"].invoke()

        own = {"provider": "Anthropic", "model": "claude-opus-5-5", "level": "High"}
        h, seen = self.open(act, upgrade_target=own)
        self.assertEqual(seen["level_state"], "disabled")
        self.assertEqual(h.store["Balance"]["upgrade_target"],
                         {"provider": "Anthropic", "model": "", "level": ""})
        self.assertIsNone(h._upgrade_target())

    def test_with_the_editor_open_it_edits_the_editors_page(self):
        def act(dlg, seen):
            (model, level), buttons, _labels = self.widgets(dlg)
            self.pick(model, "claude-fable-5-1")
            buttons["Save"].invoke()

        h, seen = self.open(act, instruction_editor_window=_Window(), _instr_shown_name="Other")
        self.assertEqual(seen["title"], "Model Setup — Other")
        self.assertEqual(h.store["Other"]["upgrade_target"]["model"], "claude-fable-5-1")
        self.assertNotIn("upgrade_target", h.store["Balance"])


# ── Wiring ──────────────────────────────────────────────────────────────

class WiringTests(unittest.TestCase):

    def src(self, *parts):
        return (REPO.joinpath(*parts)).read_text(encoding="utf-8")

    def test_the_dialog_builds_the_row_and_applies_the_named_target_on_the_worker(self):
        src = self.src("myagent", "safety_mixin.py")
        # Between the voice row and the image row: Tab order is creation order.
        self.assertLess(src.index("self._voice_build_row("), src.index("self._upgrade_build_row(dlg)"))
        self.assertLess(src.index("self._upgrade_build_row(dlg)"), src.index("attach_btn = tk.Button("))
        self.assertIn('"u": upgrade_btn', src)
        # Read when the reply is sent; applied after the wait, on the worker,
        # only for a sent, non-empty reply — to the target the label named.
        inject = src[src.index("def on_inject("):src.index("def on_close(")]
        self.assertIn("result_holder[2] = upgrade_target if upgrade_var.get() else None", inject)
        tail = src[src.index("event.wait()"):src.index("def _take_prompt_images(")]
        self.assertIn("if result_holder[2] and response.strip():", tail)
        self.assertIn("self._upgrade_apply(result_holder[2])", tail)
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

    def test_the_switch_never_persists_and_the_setting_has_one_writer(self):
        module = self.src("myagent", "model_upgrade_mixin.py")

        def method(name):
            start = module.index(f"    def {name}(")
            end = module.find("\n    def ", start + 10)
            return module[start:end]

        for name in ("_upgrade_apply", "_upgrade_end_run"):
            body = method(name)
            for forbidden in ("_save_instructions_to_disk(", "_save_last_state(",
                              "upgrade_target"):
                self.assertNotIn(forbidden, body, name)
        # Model Setup's Save is the one writer: one targeted key of one entry.
        writer = method("_upgrade_set_target")
        self.assertIn('entry["upgrade_target"] = value', writer)
        self.assertEqual(module.count("_save_instructions_to_disk("), 1)
        # No per-user settings file any more.
        self.assertNotIn("~/.config", module)
        self.assertNotIn("expanduser", module)
        self.assertNotIn("upgrade", self.src("Heartbeat.py").lower())
        self.assertEqual(UPGRADE_FIELDS, ("model", "thinking_enabled", "thinking_effort",
                                          "thinking_budget", "thinking_mode"))


if __name__ == "__main__":
    unittest.main()
