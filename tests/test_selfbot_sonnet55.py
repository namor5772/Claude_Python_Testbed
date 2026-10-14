"""SelfBot's Anthropic model audit (2026-10-14): Claude Sonnet 5.5 wired, Claude
Sonnet 4.5 retired from the picker, the pricing rows.

The user asked for an audit of the Anthropic models SelfBot uses — retired
models out, settings exposed correctly, costs collected correctly,
persistence confirmed — with no unnecessary code change. The live Models API
(14 served ids) and the deprecations and pricing pages gave three findings:

1. `claude-sonnet-5-5` (live 2026-09-28) was wired by the Sonnet >= 5 rules,
   so "Off" sent `thinking: {"type": "disabled"}` — HTTP 400 on that model
   (probed: 'To turn thinking off on this model, send "thinking": {"type":
   "between_tools"}'). `_is_sonnet_55` now makes Off a bare `between_tools`,
   gives the model the preserved-thinking binding beside adaptive thinking
   and `fallbacks: "default"` (both accepted live; a model list refused),
   and keeps the binding's beta header off the between_tools call.
2. Its cache reads are 0.05x input ($0.10/MTok, the pricing page's footnote
   2), not the $0.20 the `claude-sonnet-5` prefix charged: its own row.
3. `claude-sonnet-4-5-20250929` was deprecated 2026-09-30 (retires
   2026-11-30 → Sonnet 5.5) while the Models API still called it active: the
   retirement policy hides it from both pickers and unprices it, with its
   thinking / output-cap wiring kept for a pinned instruction.

Everything here runs on the parity module's bare App over its fake client —
no key, no network.
"""
import inspect
import unittest
from types import SimpleNamespace

from myagent import constants as c
from myagent.streaming_mixin import StreamingMixin
from tests.test_selfbot_anthropic_parity import (
    HAIKU55, SelfBot, _app, _final, _kinds, _needs_selfbot, _reply, _usage,
)

SONNET55 = "claude-sonnet-5-5"
SONNET5 = "claude-sonnet-5"
SONNET45 = "claude-sonnet-4-5-20250929"
BINDING = {"prefix_mismatch_behavior": "drop_block"}


class _Var:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


def _beta_names():
    return SelfBot.ANTHROPIC_THINKING_BINDING_BETA, SelfBot.ANTHROPIC_SERVER_FALLBACK_BETA


# ── detection ───────────────────────────────────────────────────────────

@_needs_selfbot
class DetectTests(unittest.TestCase):
    def test_sonnet_5_5_and_later_by_version(self):
        app = _app()
        for mid, expected in (
                (SONNET55, True), ("claude-sonnet-5-5-20260928", True), ("claude-sonnet-6", True),
                (SONNET5, False), ("claude-sonnet-5-20260629", False),   # a date, not a minor
                ("claude-sonnet-4-6", False), (SONNET45, False),
                ("claude-opus-5-5", False), (HAIKU55, False), ("claude-fable-5-1", False)):
            self.assertEqual(app._is_sonnet_55(mid), expected, mid)

    def test_the_picker_keeps_off_and_the_whole_ladder(self):
        app = _app(SONNET55)
        self.assertEqual(app._anthropic_mode_values(),
                         ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(app._model_supports_thinking(), "adaptive")
        self.assertTrue(app._thinking_on_by_default())
        self.assertTrue(app._rejects_temperature())
        self.assertFalse(app._is_always_on_thinking())

    def test_the_fable_surface_per_model(self):
        binding_beta, fallback_beta = _beta_names()
        app = _app(SONNET55)
        f = app._fable_features()
        self.assertEqual(f["block_binding"], BINDING)
        self.assertEqual(f["fallbacks"], "default")
        self.assertEqual(f["betas"], [binding_beta, fallback_beta])
        self.assertIsNone(_app(SONNET5)._fable_features())              # Sonnet 5: none of it
        h = _app(HAIKU55)._fable_features()
        self.assertEqual((h["block_binding"], h["fallbacks"]), (BINDING, None))   # Haiku: binding alone
        app._anthropic_unsupported.add("fallbacks")
        self.assertIsNone(app._fable_features()["fallbacks"])


# ── the wire ────────────────────────────────────────────────────────────

@_needs_selfbot
class WireTests(unittest.TestCase):
    def test_off_is_a_bare_between_tools(self):
        binding_beta, fallback_beta = _beta_names()
        app = _app(SONNET55, "off", enabled=False)
        msgs = _reply(app)
        self.assertIn("complete", _kinds(msgs))
        betas, kw = app.calls[0]
        self.assertEqual(kw["thinking"], {"type": "between_tools"})     # no display, no binding
        self.assertNotIn("output_config", kw)                           # Off sends no effort
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["extra_body"], {"fallbacks": "default"})    # accepted beside it
        self.assertEqual(kw["max_tokens"], 128000)
        self.assertIn(fallback_beta, betas)
        self.assertNotIn(binding_beta, betas)
        self.assertEqual(app._get_model_param_summary(), "mode=Off")

    def test_adaptive_carries_the_binding_the_display_and_the_fallbacks(self):
        binding_beta, fallback_beta = _beta_names()
        app = _app(SONNET55, "adaptive")
        _reply(app)
        betas, kw = app.calls[0]
        self.assertEqual(kw["thinking"], {"type": "adaptive", "display": "summarized",
                                          "block_binding": BINDING})
        self.assertNotIn("output_config", kw)
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["extra_body"], {"fallbacks": "default"})
        self.assertIn(binding_beta, betas)
        self.assertIn(fallback_beta, betas)

    def test_max_sends_the_effort(self):
        app = _app(SONNET55, "max")
        _reply(app)
        _betas, kw = app.calls[0]
        self.assertEqual(kw["output_config"], {"effort": "max"})
        self.assertEqual(kw["thinking"]["type"], "adaptive")

    def test_sonnet_5_and_haiku_5_5_are_unchanged_controls(self):
        binding_beta, fallback_beta = _beta_names()
        app = _app(SONNET5, "off", enabled=False)
        _reply(app)
        betas, kw = app.calls[0]
        self.assertEqual(kw["thinking"], {"type": "disabled"})
        self.assertNotIn("extra_body", kw)
        self.assertNotIn(binding_beta, betas)
        self.assertNotIn(fallback_beta, betas)
        app = _app(HAIKU55, "off", enabled=False)
        _reply(app)
        betas, kw = app.calls[0]
        self.assertEqual(kw["thinking"], {"type": "disabled"})
        self.assertNotIn("extra_body", kw)

    def test_learned_off_fallbacks_leave_the_body(self):
        app = _app(SONNET55, "off", enabled=False)
        app._anthropic_unsupported.add("fallbacks")
        _reply(app)
        betas, kw = app.calls[0]
        self.assertNotIn("extra_body", kw)
        self.assertNotIn(_beta_names()[1], betas)


# ── pricing ─────────────────────────────────────────────────────────────

@_needs_selfbot
class PricingTests(unittest.TestCase):
    def test_sonnet_5_5_has_its_own_row_with_the_cheaper_cache_reads(self):
        for lookup in (lambda m: SelfBot.App._get_pricing(m),
                       lambda m: StreamingMixin._get_pricing("Anthropic", m)):
            p = lookup(SONNET55)
            self.assertEqual(p["input"], 2.00 / 1_000_000)
            self.assertEqual(p["output"], 10.00 / 1_000_000)
            self.assertEqual(p["cache_write"], 2.50 / 1_000_000)
            self.assertEqual(p["cache_read"], 0.10 / 1_000_000)
            self.assertEqual(lookup(SONNET5)["cache_read"], 0.20 / 1_000_000)   # Sonnet 5 unchanged
            self.assertIsNone(lookup(SONNET45))                                 # unpriced, by policy
        self.assertNotIn("long_context", SelfBot.App._get_pricing(SONNET55, 500_000))  # no second card

    def test_a_call_is_priced_on_the_new_row(self):
        # The fake client's default final message is served by Opus 5.5; a
        # Sonnet 5.5 reply must name itself, as the real API does.
        app = _app(SONNET55, "adaptive", steps=[_final(model=SONNET55, usage=_usage(10, 5, 0, 40))])
        _reply(app)
        self.assertAlmostEqual(app._session_cost, 10 * 2e-6 + 5 * 10e-6 + 40 * 0.10e-6)
        self.assertEqual(list(app._session_per_model), [SONNET55])


# ── retirement ──────────────────────────────────────────────────────────

@_needs_selfbot
class RetirementTests(unittest.TestCase):
    def test_sonnet_4_5_is_hidden_from_both_pickers(self):
        self.assertIn("claude-sonnet-4-5", SelfBot.DEPRECATED_MODEL_PREFIXES)
        self.assertIn("claude-sonnet-4-5", c.ANTHROPIC_DEPRECATED_MODEL_PREFIXES)
        app = _app()
        served = [SimpleNamespace(id=m, display_name=m, max_tokens=64000) for m in (
            SONNET55, SONNET45, "claude-opus-4-5-20251101", "claude-haiku-4-5-20251001")]
        app.client = SimpleNamespace(models=SimpleNamespace(
            list=lambda limit: SimpleNamespace(data=served)))
        self.assertEqual(app._fetch_available_models(),
                         [SONNET55, "claude-opus-4-5-20251101", "claude-haiku-4-5-20251001"])
        self.assertEqual(app._anthropic_output_caps[SONNET45], 64000)   # the cap is kept for a pinned run

    def test_the_wiring_of_a_pinned_sonnet_4_5_stays(self):
        app = _app(SONNET45)
        self.assertEqual(app._model_supports_thinking(), "manual")
        self.assertEqual(SelfBot.App._anthropic_output_cap_for(SONNET45), 64000)
        self.assertIn("claude-sonnet-4-5", SelfBot.MANUAL_THINKING_PREFIXES)

    def test_the_lists_name_sonnet_5_5(self):
        for lst in (SelfBot.FALLBACK_MODELS, c.FALLBACK_MODELS):
            self.assertIn(SONNET55, lst)
            self.assertNotIn(SONNET45, lst)
        for st in (SelfBot.ADAPTIVE_THINKING_MODELS, c.ADAPTIVE_THINKING_MODELS):
            self.assertIn(SONNET55, st)


# ── persistence ─────────────────────────────────────────────────────────

@_needs_selfbot
class PersistenceTests(unittest.TestCase):
    def test_a_prompt_saved_off_on_sonnet_5_5_restores_and_sends_between_tools(self):
        app = _app(SONNET5, "adaptive")
        app.available_models = [SONNET5, SONNET55]
        app._model_display_names = {}
        app._model_var = _Var(SONNET5)
        app._thinking_var = _Var(True)
        app._thinking_mode_var = _Var("Adaptive")
        app._thinking_strength_var = _Var("high")
        app._is_second_instance = False
        app._on_model_selected = lambda: None
        app._apply_prompt_settings({"model": SONNET55, "thinking_enabled": False, "thinking_mode": "off",
                                    "thinking_effort": "adaptive", "thinking_budget": 8192})
        self.assertEqual(app.model, SONNET55)
        self.assertFalse(app.thinking_enabled)
        self.assertEqual(app.thinking_mode, "off")
        self.assertEqual(app._thinking_mode_var.get(), "Off")
        kw = app._apply_thinking_params({})
        self.assertEqual(kw["thinking"], {"type": "between_tools"})
        self.assertEqual(kw["fallbacks"], "default")         # the Debug dump's wire name
        self.assertNotIn("temperature", kw)

    def test_the_state_file_keys_are_the_ones_the_model_fields_use(self):
        save = inspect.getsource(SelfBot.App._save_last_state)
        for key in ("last_model", "thinking_enabled", "thinking_effort", "thinking_budget", "temperature"):
            self.assertIn(f'"{key}"', save, key)
        load = inspect.getsource(SelfBot.App._load_last_state)
        self.assertIn('state.get("last_model"', load)
        self.assertIn("self._on_model_selected()", load)     # re-coerces a stale saved mode


# ── wiring ──────────────────────────────────────────────────────────────

@_needs_selfbot
class WiringTests(unittest.TestCase):
    def test_the_off_shape_and_the_header_rule(self):
        builder = inspect.getsource(SelfBot.App._apply_thinking_params)
        self.assertIn('{"type": "between_tools"} if self._is_sonnet_55()', builder)
        worker = inspect.getsource(SelfBot.App.stream_worker)
        self.assertIn('in ("disabled", "between_tools")', worker)
        self.assertIn("sonnet55", inspect.getsource(SelfBot.App._fable_features))


if __name__ == "__main__":
    unittest.main()
