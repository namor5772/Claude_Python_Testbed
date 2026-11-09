"""The Model upgrade's own speed (2026-11-10): Model Setup's Fast box, and
what an upgraded run sends.

The user's request, the day Claude Fable 5.2 brought fast mode to the Fable
line: Model Setup (the upgrade model behind the Agent Request dialog's Upgrade
box) had no way to ask for fast mode on the upgrade model. The audit that came
with it found the opposite problem live: the switch left `fast_mode` as the
run's, and `WBC_LoginToWebsite` — on Haiku 5.5, with a Fast tick saved while
an Opus was picked and hidden since (the editor shows the box for fast-capable
models alone) — would have upgraded to Fable 5.2 at FAST rates without a word.

So `upgrade_target` gained `fast`, the upgrade's own speed: a bool, Anthropic
only, False for a target saved before the field existed; Model Setup shows a
Fast box only while the picked model serves fast mode and writes False for any
other model; `_upgrade_apply` sets `_upgrade_fast` (None while no upgrade is
active) and `_anthropic_fast_active` (ui_mixin — the ONE speed gate behind
the live request, the Debug dump, the title / cost-log summary and the
upgrade's pricing check) reads it AHEAD of the instruction's `fast_mode`,
which is never rewritten. Pinned here, on the bare hosts of
tests/test_model_upgrade.py and the REAL Model Setup dialog:

- the sanitiser and the target a run gets, with and without the field;
- the speed a target really gets (`_upgrade_fast_for`: the editor's own
  gate, so fast on 5.1 or a non-Anthropic model is standard, and LABELLED so);
- the switch: `_upgrade_fast` set and cleared, the ⬆ line, the box's label
  and the tool's reply naming "fast", the gate, the param summary, the
  pricing check on the FAST table — and the hidden-tick case running standard;
- Model Setup on real widgets: the Fast box present for Fable 5.2 and absent
  for Sonnet 5, hidden and cleared when the pick changes to a model without
  fast mode, Save writing the field through (True, and False for a model
  that cannot), Alt+F reaching it, the Tab order;
- the Provider line naming the instruction's own model while a run is
  upgraded;
- a wiring scan (the App's init field, the gate's read, the schema).
"""

import pathlib
import tkinter as tk
import unittest
from tkinter import ttk

from myagent.constants import META_TOOLS
from myagent.model_upgrade_mixin import ModelUpgradeMixin, UPGRADE_FAST_LABEL
from tests.test_agent_request_display import _walk
from tests.test_model_upgrade import (_Window, drained, free_tk_garbage_then_destroy,
                                      host)

REPO = pathlib.Path(__file__).resolve().parents[1]
FABLE = "claude-fable-5-2"


class TargetTests(unittest.TestCase):
    s = staticmethod(ModelUpgradeMixin._upgrade_sanitize_target)

    def test_fast_is_a_bool_for_an_anthropic_model_with_a_model(self):
        self.assertTrue(self.s({"model": FABLE, "level": "Max", "fast": True}, "Anthropic")["fast"])
        self.assertTrue(self.s({"model": FABLE, "fast": "yes"}, "Anthropic")["fast"])
        self.assertFalse(self.s({"model": FABLE, "level": "Max"}, "Anthropic")["fast"])   # pre-field
        self.assertFalse(self.s({"model": FABLE, "fast": 0}, "Anthropic")["fast"])
        self.assertFalse(self.s({"model": "", "fast": True}, "Anthropic")["fast"])        # no upgrade
        self.assertFalse(self.s({"model": "gpt-6-astra", "fast": True}, "OpenAI")["fast"])
        self.assertFalse(self.s({"provider": "Google", "model": "gemini-4-argon", "fast": True})["fast"])

    def test_the_run_gets_the_targets_speed(self):
        own = {"provider": "Anthropic", "model": FABLE, "level": "Max", "fast": True}
        self.assertEqual(host(upgrade_target=own)._upgrade_target(),
                         {"model": FABLE, "level": "Max", "fast": True})
        self.assertFalse(host()._upgrade_target()["fast"])                 # the default: standard
        # The draft Model Setup opens on carries it too.
        shown, source = host(upgrade_target=own)._upgrade_setup_draft()
        self.assertEqual((shown["fast"], source), (True, "own"))

    def test_the_speed_a_target_really_gets(self):
        h = host()
        self.assertTrue(h._upgrade_fast_for(FABLE, True))
        self.assertTrue(h._upgrade_fast_for("claude-opus-5-5", True))
        self.assertFalse(h._upgrade_fast_for(FABLE, False))
        self.assertFalse(h._upgrade_fast_for("claude-fable-5-1", True))   # no fast mode on 5.1
        self.assertFalse(h._upgrade_fast_for("claude-haiku-5-5", True))
        self.assertFalse(host(provider="OpenAI", model="gpt-5.6-terra")
                         ._upgrade_fast_for("gpt-6-astra", True))
        self.assertEqual(ModelUpgradeMixin._upgrade_level_text("Max", True), "Max, fast")
        self.assertEqual(ModelUpgradeMixin._upgrade_level_text("Max", False), "Max")
        self.assertEqual(ModelUpgradeMixin._upgrade_level_text("", True), "fast")
        self.assertEqual(ModelUpgradeMixin._upgrade_level_text("", False), "")


class SwitchTests(unittest.TestCase):
    FAST = {"model": FABLE, "level": "Max", "fast": True}

    def test_a_fast_upgrade_sets_the_speed_names_it_and_clears_it_at_the_end(self):
        h = host()                                   # Sonnet 5, fast_mode False
        self.assertIsNone(getattr(h, "_upgrade_fast", None))
        self.assertFalse(h._anthropic_fast_active())
        self.assertTrue(h._upgrade_apply(self.FAST))
        self.assertIs(h._upgrade_fast, True)
        self.assertFalse(h.fast_mode)                # the instruction's own, untouched
        self.assertTrue(h._anthropic_fast_active())  # the ONE gate follows the upgrade
        self.assertEqual(h._get_model_param_summary(),
                         "mode=Max speed=fast upgraded-from=claude-sonnet-5@call12")
        [line] = drained(h.queue)
        self.assertIn("claude-sonnet-5 → claude-fable-5-2 (Max, fast), from call #13 on",
                      line["content"])
        self.assertEqual(h._upgrade_box_state(self.FAST), (False, True,
                         "Upgraded to Claude Fable 5.2 (Max, fast) for the rest of this run"))
        h._upgrade_end_run()
        self.assertIsNone(h._upgrade_fast)
        self.assertFalse(h._anthropic_fast_active())
        self.assertEqual(h._get_model_param_summary(), "mode=Adaptive")

    def test_the_box_label_and_the_tool_name_the_speed(self):
        h = host()
        self.assertEqual(h._upgrade_box_state(self.FAST), (True, False,
                         "Upgrade to Claude Fable 5.2 (Max, fast) for the rest of this run"))
        # A target asking fast of a model without it is labelled standard.
        self.assertEqual(h._upgrade_box_state({"model": "claude-fable-5-1", "level": "Max",
                                               "fast": True})[2],
                         "Upgrade to Claude Fable 5.1 (Max) for the rest of this run")
        h = host(upgrade_target={"provider": "Anthropic", **self.FAST})
        self.assertTrue(h.do_upgrade_model().startswith(
            "Upgraded: claude-sonnet-5 → claude-fable-5-2 (Max, fast) for the rest"))
        self.assertIs(h._upgrade_fast, True)

    def test_the_hidden_tick_no_longer_rides_along(self):
        # The live case of 2026-11-10: Haiku 5.5 with fast_mode saved True
        # (hidden — Haiku has no Fast box) and an upgrade target without
        # `fast`. Before: Fable 5.2 at fast rates. Now: standard.
        h = host(model="claude-haiku-5-5", fast_mode=True,
                 upgrade_target={"provider": "Anthropic", "model": FABLE, "level": "Max"})
        self.assertFalse(h._anthropic_fast_active())          # Haiku: nothing sent anyway
        h.do_upgrade_model()
        self.assertEqual(h.model, FABLE)
        self.assertIs(h._upgrade_fast, False)
        self.assertFalse(h._anthropic_fast_active())          # 5.2 could, the target did not ask
        self.assertTrue(h.fast_mode)                          # still the instruction's own
        self.assertNotIn("speed", h._get_model_param_summary())
        self.assertIn("(Max), from call", drained(h.queue)[0]["content"])
        h._upgrade_end_run()
        self.assertIsNone(h._upgrade_fast)

    def test_a_fast_target_on_a_model_without_fast_mode_runs_standard(self):
        h = host()
        h._upgrade_apply({"model": "claude-fable-5-1", "level": "Max", "fast": True})
        self.assertIs(h._upgrade_fast, False)
        self.assertFalse(h._anthropic_fast_active())

    def test_the_pricing_check_reads_the_upgrades_speed(self):
        # The FAST table is what a fast upgrade needs — through the one gate,
        # not fast_mode (which is False here).
        from unittest import mock
        h = host()
        with mock.patch.dict("myagent.streaming_mixin.ANTHROPIC_FAST_PRICING", {}, clear=True):
            h._upgrade_apply(self.FAST)
        _upgraded, warning = drained(h.queue)
        self.assertIn("no row in the ANTHROPIC_FAST_PRICING", warning["content"])
        # With the row (the real table) a fast 5.2 upgrade is priced: the ⬆ line alone.
        h = host()
        h._upgrade_apply(self.FAST)
        self.assertEqual(len(drained(h.queue)), 1)


class SetupDialogTests(unittest.TestCase):
    """The REAL Model Setup dialog, the pattern of tests/test_model_upgrade.py's."""

    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.geometry("240x240+0+0")
        self.root.attributes("-alpha", 0.0)

    def tearDown(self):
        free_tk_garbage_then_destroy(self)

    def open(self, act, **attrs):
        base = dict(root=self.root,
                    available_models=["claude-opus-5-5", "claude-opus-5", FABLE,
                                      "claude-fable-5-1", "claude-sonnet-5", "claude-haiku-5-5"],
                    _upgrade_setup_dialog=None)
        base.update(attrs)
        h = host(**base)
        h._place_window = lambda win, kind, size, parent=None: "+0+0"
        h._parse_geometry = lambda geo: None
        h._set_geometry = lambda win, geo: win.geometry(geo)
        seen = {}

        def when_up():
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
        boxes = [w for w in _walk(dlg) if isinstance(w, tk.Checkbutton)]
        labels = [w.cget("text") for w in _walk(dlg) if isinstance(w, tk.Label)]
        return combos, buttons, boxes, labels

    @staticmethod
    def pick(combo, value):
        combo.set(value)
        combo.event_generate("<<ComboboxSelected>>")

    @staticmethod
    def shown(box):
        box.update_idletasks()
        return bool(box.winfo_manager())

    def test_the_box_follows_the_pick_and_save_writes_it(self):
        def act(dlg, seen):
            (model, level), buttons, [fast], labels = self.widgets(dlg)
            seen["label"] = fast.cget("text")
            seen["on_default"] = self.shown(fast)              # Fable 5.2 Max: capable
            seen["ticked_at_open"] = bool(fast.getvar(fast.cget("variable")))
            self.pick(model, "claude-sonnet-5")
            seen["on_sonnet"] = self.shown(fast)
            self.pick(model, FABLE)
            seen["back_on_fable"] = self.shown(fast)
            seen["still_unticked"] = bool(fast.getvar(fast.cget("variable")))
            fast.invoke()                                      # tick it
            # Tab order: Upgrade model → Thinking → Fast → Save → Cancel.
            seen["tab"] = [model.tk_focusNext(), level.tk_focusNext(), fast.tk_focusNext()]
            seen["expected_tab"] = [level, fast, buttons["Save"]]
            buttons["Save"].invoke()

        h, seen = self.open(act)
        self.assertEqual(seen["label"], UPGRADE_FAST_LABEL)
        self.assertTrue(seen["on_default"])
        self.assertFalse(seen["ticked_at_open"])               # the default is standard
        self.assertFalse(seen["on_sonnet"])
        self.assertTrue(seen["back_on_fable"])
        self.assertFalse(seen["still_unticked"])
        self.assertEqual(seen["tab"], seen["expected_tab"])
        value = {"provider": "Anthropic", "model": FABLE, "level": "Max", "fast": True}
        self.assertEqual(h.upgrade_target, value)
        self.assertEqual(h.store["Balance"]["upgrade_target"], value)
        self.assertEqual(h._upgrade_target(), {"model": FABLE, "level": "Max", "fast": True})

    def test_a_tick_is_dropped_when_the_pick_loses_fast_mode(self):
        def act(dlg, seen):
            (model, _level), buttons, [fast], _labels = self.widgets(dlg)
            seen["ticked_at_open"] = bool(fast.getvar(fast.cget("variable")))
            self.pick(model, "claude-haiku-5-5")               # no fast mode
            seen["on_haiku"] = self.shown(fast)
            buttons["Save"].invoke()

        own = {"provider": "Anthropic", "model": FABLE, "level": "Max", "fast": True}
        h, seen = self.open(act, upgrade_target=own)
        self.assertTrue(seen["ticked_at_open"])                # the saved True shown
        self.assertFalse(seen["on_haiku"])
        self.assertEqual(h.store["Balance"]["upgrade_target"],
                         {"provider": "Anthropic", "model": "claude-haiku-5-5", "level": "Max",
                          "fast": False})

    def test_no_box_for_a_provider_without_fast_mode(self):
        def act(dlg, seen):
            _combos, buttons, boxes, _labels = self.widgets(dlg)
            seen["shown"] = [self.shown(b) for b in boxes]
            buttons["Cancel"].invoke()

        h, seen = self.open(act, provider="OpenAI", model="gpt-5.6-terra",
                            available_models=["gpt-6-astra", "gpt-5.6-terra"])
        self.assertEqual(seen["shown"], [False])
        self.assertIsNone(h.upgrade_target)

    def test_the_provider_line_names_the_instructions_own_model_mid_upgrade(self):
        def act(dlg, seen):
            _combos, buttons, _boxes, labels = self.widgets(dlg)
            seen["provider_line"] = next(t for t in labels if t.startswith("Anthropic — "))
            buttons["Cancel"].invoke()

        stash = {"model": "claude-sonnet-5", "thinking_enabled": True, "thinking_effort": "high",
                 "thinking_budget": 8192, "thinking_mode": "adaptive", "call": 3}
        _h, seen = self.open(act, model=FABLE, _upgrade_original=stash, _upgrade_fast=True,
                             instruction_editor_window=_Window(False))
        self.assertEqual(seen["provider_line"], "Anthropic — this instruction runs on claude-sonnet-5")


class WiringTests(unittest.TestCase):
    def src(self, *parts):
        return REPO.joinpath(*parts).read_text(encoding="utf-8")

    def test_the_app_starts_with_no_upgrade_speed(self):
        init = self.src("MyAgent.py")
        init = init[init.index("def __init__"):]
        self.assertIn("self._upgrade_fast = None", init)

    def test_the_one_gate_reads_the_upgrades_speed_first(self):
        src = self.src("myagent", "ui_mixin.py")
        gate = src[src.index("def _anthropic_fast_active("):src.index("def _anthropic_mode_values(")]
        self.assertLess(gate.index('getattr(self, "_upgrade_fast", None)'),
                        gate.index('getattr(self, "fast_mode", False)'))
        module = self.src("myagent", "model_upgrade_mixin.py")
        self.assertIn("self._upgrade_fast = fast", module[module.index("def _upgrade_apply("):])
        self.assertIn("self._upgrade_fast = None", module[module.index("def _upgrade_end_run("):])
        # The switch never rewrites the instruction's own Fast setting.
        apply = module[module.index("def _upgrade_apply("):module.index("def do_upgrade_model(")]
        self.assertNotIn("self.fast_mode", apply)
        # The pricing check asks the gate, not fast_mode.
        pricing = module[module.index("def _upgrade_pricing_warnings("):
                         module.index("def _upgrade_end_run(")]
        self.assertNotIn('getattr(self, "fast_mode"', pricing)
        self.assertNotIn("self.fast_mode", pricing)
        self.assertIn("self._anthropic_fast_active()", pricing)

    def test_the_schema_and_the_dialog(self):
        tool = next(t for t in META_TOOLS if t["name"] == "manage_instructions")
        self.assertEqual(tool["input_schema"]["properties"]["upgrade_target"]["properties"]["fast"],
                         {"type": "boolean"})
        module = self.src("myagent", "model_upgrade_mixin.py")
        dialog = module[module.index("def _open_upgrade_setup("):]
        self.assertIn('"f": fast_box', dialog)
        self.assertIn('"fast": bool(fast_shown["on"] and fast_var.get())', dialog)
        self.assertLess(dialog.index("fast_box = tk.Checkbutton("), dialog.index("btn_row = tk.Frame("))


if __name__ == "__main__":
    unittest.main()
