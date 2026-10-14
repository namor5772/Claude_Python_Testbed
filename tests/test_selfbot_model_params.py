"""SelfBot's model toolbar shows each model's parameter controls and no
others (2026-10-14) — MyAgent's rule.

The user's report: in SelfBot some models still showed the Temp box, dimmed
and unusable, where MyAgent shows none. SelfBot DISABLED a control that could
not apply — Temp beside thinking, Temp on a model that removed sampling
params (Opus 4.7+, Sonnet 5+, Haiku 5+, Fable / Mythos), the budget box beside
an unticked Thinking box, the whole thinking pair on a model with no
thinking — where MyAgent hides it. Now `_layout_model_params` forgets every
parameter control and packs only what the current model and thinking setting
use, in one fixed order (thinking first, Temp after, as MyAgent's toolbar),
and `_sends_temperature` is the ONE rule behind the Temp box, the request
builder and the cost log's params string.

Found and fixed on the way, each a control showing one thing while the
request carried another:
- the budget models' Thinking box could show ticked with thinking off — the
  adaptive models' mode combobox never updated it — so Opus 4.6 at Off, then
  Haiku 4.5, showed thinking on and sent it off;
- the budget box kept any valid label it held, whatever budget was sent; a
  budget no preset names (manage_prompts takes any integer) now snaps to the
  nearest preset;
- a first launch, with no state file, never reached `_on_model_selected`, so
  the default model (adaptive) wore the budget models' controls;
- a temperature a 400 taught the session to drop still had its box.

The matrix is the live picker of 2026-10-14 — the Models API read that day,
zero tokens: every effort ladder and thinking type it lists agreed with
`_anthropic_mode_values` / `_model_supports_thinking`.

Real widgets on a withdrawn Tk root (the toolbar's pack_slaves() is what is
shown, in order); the worker's rung over the parity module's fake client.
"""
import gc
import inspect
import re
import tkinter as tk
import unittest
from types import SimpleNamespace

from tests.test_selfbot_anthropic_parity import (
    SelfBot, _app, _bad_request, _final, _needs_selfbot, _reply,
)

ALWAYS_ON = ("claude-fable-5-1", "claude-fable-5", "claude-opus-5-5")
NO_SAMPLING = ("claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
               "claude-sonnet-5-5", "claude-sonnet-5", "claude-haiku-5-5")
SAMPLING = ("claude-opus-4-6", "claude-sonnet-4-6")
BUDGET = ("claude-opus-4-5-20251101", "claude-haiku-4-5-20251001")
PICKER = ALWAYS_ON + NO_SAMPLING + SAMPLING + BUDGET
NO_THINKING = "claude-3-haiku-20240307"     # hidden by the picker; the code path stays
OPUS46, HAIKU45, OPUS5, FABLE = "claude-opus-4-6", "claude-haiku-4-5-20251001", "claude-opus-5", "claude-fable-5-1"

WIDGETS = {"_thinking_check": "check", "_thinking_strength_combo": "budget",
           "_thinking_mode_label": "mode_label", "_thinking_mode_combo": "mode",
           "_temp_label": "temp_label", "_temp_spin": "temp"}
MODE = ["mode_label", "mode"]
MODE_TEMP = ["mode_label", "mode", "temp_label", "temp"]
BOX_OFF = ["check", "temp_label", "temp"]
BOX_ON = ["check", "budget"]
TEMP = ["temp_label", "temp"]


class _Tk(unittest.TestCase):
    """A withdrawn root per test, collected before it goes (the real-widget
    modules' rule: Tk variables left in a cycle must not be finalised on
    another test's worker thread)."""

    def setUp(self):
        gc.collect()
        try:
            self.root = tk.Tk()
        except tk.TclError as exc:  # headless box, no display
            self.skipTest(f"Tk unavailable: {exc}")
        self.root.withdraw()
        self.apps = []

    def tearDown(self):
        for app in self.apps:
            app.toolbar.destroy()    # its widgets' callbacks hold the host
        self.apps = []
        gc.collect()                 # the hosts, on the main thread, before the root goes
        self.root.destroy()

    def host(self, model, mode="off", effort="high", budget=8192):
        """A bare App with the REAL parameter controls on a toolbar frame,
        its thinking state set as the restore paths set it, then laid out as
        setup_ui / _on_model_selected do."""
        app = SelfBot.App.__new__(SelfBot.App)
        app.root = self.root
        app.model = model
        app.temperature = 0.7
        app.thinking_enabled = mode != "off"
        app.thinking_mode, app.thinking_effort, app.thinking_budget = mode, effort, budget
        app._no_temperature, app._anthropic_unsupported = set(), set()
        app._anthropic_output_caps = {}
        app.available_models = list(PICKER) + [NO_THINKING]
        app._model_id_list = list(app.available_models)
        app._model_display_names = {}
        app.saves = []
        app._save_last_state = lambda: app.saves.append(1)
        app.toolbar = tk.Frame(self.root)
        app._model_var = tk.StringVar(master=self.root, value=model)
        app._build_model_param_widgets(app.toolbar)
        app._thinking_var.set(app.thinking_enabled)
        app._thinking_mode_var.set(mode.capitalize() if mode != "off" else "Off")
        app._apply_model_params()
        self.apps.append(app)
        return app


def _shown(app):
    names = {str(getattr(app, attr)): name for attr, name in WIDGETS.items()}
    return [names[str(w)] for w in app.toolbar.pack_slaves()]


def _switch(app, model):
    app._model_var.set(model)
    app._on_model_selected()


def _pick_mode(app, value):
    app._thinking_mode_var.set(value)
    app._on_thinking_mode_changed()


def _tick(app, on):
    app._thinking_var.set(on)
    app._on_thinking_toggled()


def _cases():
    """(model, mode) for every picker model in every state it can be in."""
    for model in ALWAYS_ON:
        for mode in ("adaptive", "low", "high", "max"):
            yield model, mode
    for model in NO_SAMPLING + SAMPLING:
        for mode in ("off", "adaptive", "medium", "high"):
            yield model, mode
    for model in BUDGET:
        for mode in ("off", "high"):
            yield model, mode
    yield NO_THINKING, "off"


# ── 1. what each model shows ─────────────────────────────────────────────

@_needs_selfbot
class LayoutTests(_Tk):
    def test_always_on_models_show_the_mode_alone(self):
        for model in ALWAYS_ON:
            for mode in ("off", "adaptive", "high", "max"):     # a stale Off included
                with self.subTest(model=model, mode=mode):
                    app = self.host(model, mode)
                    self.assertEqual(_shown(app), MODE)
                    self.assertNotIn("Off", app._thinking_mode_combo["values"])
                    self.assertNotEqual(app._thinking_mode_var.get(), "Off")
                    self.assertTrue(app.thinking_enabled)

    def test_models_without_sampling_params_never_show_temp(self):
        for model in NO_SAMPLING:
            for mode in ("off", "adaptive", "high"):
                with self.subTest(model=model, mode=mode):
                    app = self.host(model, mode)
                    self.assertEqual(_shown(app), MODE)
                    self.assertIn("Off", app._thinking_mode_combo["values"])

    def test_opus_and_sonnet_4_6_show_temp_beside_off_only(self):
        for model in SAMPLING:
            with self.subTest(model=model):
                self.assertEqual(_shown(self.host(model, "off")), MODE_TEMP)
                for mode in ("adaptive", "low", "max"):
                    self.assertEqual(_shown(self.host(model, mode)), MODE, mode)

    def test_budget_models_show_the_box_then_the_budget_or_temp(self):
        for model in BUDGET:
            with self.subTest(model=model):
                self.assertEqual(_shown(self.host(model, "off")), BOX_OFF)
                self.assertEqual(_shown(self.host(model, "high")), BOX_ON)

    def test_a_model_with_no_thinking_control_shows_temp_alone(self):
        app = self.host(NO_THINKING, "high")
        self.assertEqual(_shown(app), TEMP)
        self.assertFalse(app.thinking_enabled)
        self.assertEqual(app.thinking_mode, "off")

    def test_nothing_is_ever_shown_dimmed(self):
        # The report itself: a shown control can always be used.
        for model, mode in _cases():
            with self.subTest(model=model, mode=mode):
                app = self.host(model, mode)
                for w in app.toolbar.pack_slaves():
                    self.assertNotEqual(str(w.cget("state")), "disabled", w)

    def test_the_mode_values_follow_the_ladders_the_models_api_lists(self):
        full = ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]
        expected = dict.fromkeys(NO_SAMPLING, full)
        expected.update(dict.fromkeys(ALWAYS_ON, full[1:]))
        expected.update(dict.fromkeys(SAMPLING, ["Off", "Adaptive", "Low", "Medium", "High", "Max"]))
        for model, values in expected.items():
            with self.subTest(model=model):
                self.assertEqual(list(self.host(model, "high")._thinking_mode_combo["values"]), values)


# ── 2. the handlers ──────────────────────────────────────────────────────

@_needs_selfbot
class HandlerTests(_Tk):
    def test_off_brings_temp_in_on_the_right_and_a_rung_takes_it_away(self):
        app = self.host(OPUS46, "high")
        _pick_mode(app, "Off")
        self.assertEqual(_shown(app), MODE_TEMP)
        self.assertFalse(app.thinking_enabled)
        _pick_mode(app, "Max")
        self.assertEqual(_shown(app), MODE)
        self.assertEqual((app.thinking_enabled, app.thinking_mode), (True, "max"))
        self.assertEqual(len(app.saves), 2)

    def test_the_box_swaps_the_budget_and_temp(self):
        app = self.host(HAIKU45, "off")
        _tick(app, True)
        self.assertEqual(_shown(app), BOX_ON)
        self.assertTrue(app.thinking_enabled)
        self.assertEqual(app.thinking_mode, app.thinking_effort)     # as the restore paths derive it
        _tick(app, False)
        self.assertEqual(_shown(app), BOX_OFF)
        self.assertEqual(app.thinking_mode, "off")

    def test_the_mode_combobox_keeps_the_box_in_step(self):
        app = self.host(OPUS46, "high")
        self.assertTrue(app._thinking_var.get())
        _pick_mode(app, "Off")
        self.assertFalse(app._thinking_var.get())    # it stayed True before the fix

    def test_from_off_to_a_budget_model_the_box_is_unticked_and_thinking_off_is_sent(self):
        app = self.host(OPUS46, "high")
        _pick_mode(app, "Off")
        _switch(app, HAIKU45)
        self.assertEqual(_shown(app), BOX_OFF)
        self.assertFalse(app._thinking_var.get())
        kw = app._apply_thinking_params({})
        self.assertNotIn("thinking", kw)
        self.assertEqual(kw["temperature"], 0.7)

    def test_from_a_thinking_rung_to_a_budget_model_the_budget_is_sent(self):
        app = self.host(OPUS5, "high")
        _switch(app, HAIKU45)
        self.assertEqual(_shown(app), BOX_ON)
        self.assertTrue(app._thinking_var.get())
        kw = app._apply_thinking_params({})
        self.assertEqual(kw["thinking"], {"type": "enabled", "budget_tokens": 8192})
        self.assertNotIn("temperature", kw)

    def test_from_a_budget_model_off_to_an_always_on_model(self):
        app = self.host(HAIKU45, "off")
        _switch(app, FABLE)
        self.assertEqual(_shown(app), MODE)
        self.assertEqual(app._thinking_mode_var.get(), "Adaptive")
        self.assertTrue(app.thinking_enabled)

    def test_back_to_a_model_that_takes_temp(self):
        app = self.host(OPUS5, "off")
        self.assertEqual(_shown(app), MODE)
        _switch(app, OPUS46)
        self.assertEqual(_shown(app), MODE_TEMP)
        _switch(app, NO_THINKING)
        self.assertEqual(_shown(app), TEMP)


# ── 3. what is shown is what is sent ─────────────────────────────────────

@_needs_selfbot
class AgreementTests(_Tk):
    def test_the_temp_box_the_request_and_the_cost_log_agree(self):
        for model, mode in _cases():
            with self.subTest(model=model, mode=mode):
                app = self.host(model, mode)
                shown = "temp" in _shown(app)
                kw = app._apply_thinking_params({})
                self.assertEqual("temperature" in kw, shown)
                self.assertEqual("temp=0.7" in app._get_model_param_summary(), shown)
                self.assertEqual(app._sends_temperature(), shown)

    def test_a_budget_models_box_and_budget_are_what_is_sent(self):
        for model in BUDGET:
            for mode in ("off", "high"):
                with self.subTest(model=model, mode=mode):
                    app = self.host(model, mode, budget=16384)
                    kw = app._apply_thinking_params({})
                    if app._thinking_var.get():
                        self.assertEqual(kw["thinking"], {"type": "enabled", "budget_tokens":
                                         SelfBot.BUDGET_PRESETS[app._thinking_strength_var.get()]})
                    else:
                        self.assertNotIn("thinking", kw)

    def test_the_mode_shown_is_the_effort_sent(self):
        for model in ALWAYS_ON + NO_SAMPLING + SAMPLING:
            app = self.host(model, "high")
            for value in app._thinking_mode_combo["values"]:
                with self.subTest(model=model, value=value):
                    _pick_mode(app, value)
                    kw = app._apply_thinking_params({})
                    effort = (kw.get("output_config") or {}).get("effort")
                    self.assertEqual(effort, value.lower() if value not in ("Off", "Adaptive") else None)
                    self.assertEqual(kw.get("thinking", {}).get("type") == "adaptive", value != "Off")


# ── 4. the budget box ────────────────────────────────────────────────────

@_needs_selfbot
class BudgetTests(_Tk):
    def test_each_preset_shows_its_label(self):
        for label, budget in SelfBot.BUDGET_PRESETS.items():
            app = self.host(HAIKU45, "high", budget=budget)
            self.assertEqual(app._thinking_strength_var.get(), label)
            self.assertEqual(app.thinking_budget, budget)

    def test_an_unlisted_budget_snaps_to_the_nearest_preset(self):
        for budget, label in ((10000, "8K"), (12288, "8K"),       # a tie goes to the smaller
                              (100000, "32K"), (500, "1K"), ("junk", "8K"), (None, "8K")):
            with self.subTest(budget=budget):
                app = self.host(HAIKU45, "high", budget=budget)
                self.assertEqual(app._thinking_strength_var.get(), label)
                self.assertEqual(app._apply_thinking_params({})["thinking"]["budget_tokens"],
                                 SelfBot.BUDGET_PRESETS[label])

    def test_a_stale_label_follows_the_budget(self):
        app = self.host(OPUS46, "high")
        app._thinking_strength_var.set("4K")      # left from an earlier budget model
        app.thinking_budget = 16384               # what a prompt bundle applied meanwhile
        _switch(app, HAIKU45)
        self.assertEqual(app._thinking_strength_var.get(), "16K")
        self.assertEqual(app._apply_thinking_params({})["thinking"]["budget_tokens"], 16384)

    def test_picking_a_preset_sets_the_budget(self):
        app = self.host(HAIKU45, "high")
        app._thinking_strength_var.set("32K")
        app._on_thinking_strength_changed()
        self.assertEqual(app.thinking_budget, 32768)
        self.assertEqual(app.saves, [1])


# ── 5. a temperature a 400 refused ───────────────────────────────────────

@_needs_selfbot
class LearnedOffTests(_Tk):
    def test_the_box_goes_with_it(self):
        app = self.host(OPUS46, "off")
        self.assertEqual(_shown(app), MODE_TEMP)
        app._no_temperature.add(OPUS46)
        app._layout_model_params()
        self.assertEqual(_shown(app), MODE)
        self.assertNotIn("temperature", app._apply_thinking_params({}))
        self.assertEqual(app._get_model_param_summary(), "mode=Off")


@_needs_selfbot
class WorkerRungTests(unittest.TestCase):
    def test_the_worker_drops_temperature_and_hands_the_relayout_to_the_tk_thread(self):
        app = _app(OPUS46, "off", enabled=False,
                   steps=[_bad_request("temperature: this model does not support it"), _final()])
        scheduled = []
        app.root = SimpleNamespace(after=lambda ms, fn: scheduled.append((ms, fn)))
        _reply(app)
        self.assertIn("temperature", app.calls[0][1])
        self.assertNotIn("temperature", app.calls[1][1])
        self.assertEqual(app._no_temperature, {OPUS46})
        self.assertEqual(scheduled, [(0, app._layout_model_params)])

    def test_a_bare_host_without_a_root_still_retries(self):
        app = _app(OPUS46, "off", enabled=False,
                   steps=[_bad_request("temperature: this model does not support it"), _final()])
        _reply(app)
        self.assertEqual(len(app.calls), 2)
        self.assertNotIn("temperature", app.calls[1][1])


# ── 6. wiring ────────────────────────────────────────────────────────────

PARAM_ATTRS = "|".join(WIDGETS)


@_needs_selfbot
class WiringTests(_Tk):
    def test_creation_order_is_display_order(self):
        # Creation order is Tab order: thinking controls first, Temp after.
        app = self.host(OPUS46, "off")
        made = [str(w) for w in app.toolbar.winfo_children()]
        order = [made.index(str(getattr(app, attr))) for attr in WIDGETS]
        self.assertEqual(order, sorted(order))

    def test_only_the_layout_packs_them_and_nothing_dims_them(self):
        src = inspect.getsource(SelfBot)
        layout = inspect.getsource(SelfBot.App._layout_model_params)
        pack = re.compile(rf"self\.(?:{PARAM_ATTRS})\.pack\(")
        self.assertEqual(len(pack.findall(layout)), 6)
        self.assertEqual(len(pack.findall(src)), 6)
        dim = re.compile(rf"self\.(?:{PARAM_ATTRS})\.config(?:ure)?\(\s*state")
        self.assertEqual(dim.findall(src), [])
        self.assertFalse(hasattr(SelfBot.App, "_set_temp_state"))

    def test_setup_ui_lays_them_out_once_the_buttons_are_in(self):
        src = inspect.getsource(SelfBot.App.setup_ui)
        built = src.index("self._build_model_param_widgets(model_toolbar)")
        buttons = src.index("self.delete_chat_button.pack(")
        self.assertLess(built, buttons)
        self.assertLess(buttons, src.index("self._apply_model_params()"))

    def test_one_temperature_rule(self):
        for method in (SelfBot.App._apply_thinking_params, SelfBot.App._layout_model_params,
                       SelfBot.App._get_model_param_summary):
            body = inspect.getsource(method)
            self.assertIn("self._sends_temperature()", body, method.__name__)
            self.assertNotIn("self._rejects_temperature()", body, method.__name__)


if __name__ == "__main__":
    unittest.main()
