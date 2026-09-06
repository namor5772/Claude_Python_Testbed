"""Characterization tests for the GPT-5.6 family's parameter surface — terra
(2026-09-06 audit), then sol and luna the same day — the same layers as
tests/test_gpt6_params.py, whose hosts are reused. Against the contract probed
live with the real key on ALL THREE tiers:

  * reasoning.effort accepts none/low/medium/high/xhigh/max — "minimal" is
    HTTP 400 ("Supported values are: 'none', 'low', 'medium', 'high',
    'xhigh', and 'max'"), a stale "off" is HTTP 400 "Invalid value";
  * temperature is accepted ONLY at effort=none (400 at any other rung and
    when the reasoning param is omitted); top_p likewise (never sent);
  * text.verbosity low/medium/high, reasoning.summary detailed, the
    web_search_preview + code_interpreter server tools are all accepted;
  * cache_write_tokens / cached_tokens are subsets of input_tokens, and the
    pricing page lists a BILLED cache-write rate (1.25x input: terra $2.50,
    sol $5.00, luna $0.25 per M) — so the rows are 4-tuples and written
    tokens leave the input bucket.

Also pins the nearest-rung coercion shared by the request builder, the
Reasoning combobox and the reactive 400 rung (_openai_nearest_effort).
"""

import unittest

from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin
from tests.test_gpt6_params import _ReqHost, _UIHost

TERRA = "gpt-5.6-terra"
# tier -> (input, output, cached_input, cache_write) per M, pricing page 2026-09-06
TIERS = {
    "gpt-5.6-terra": (2.00, 12.00, 0.20, 2.50),
    "gpt-5.6-sol": (4.00, 20.00, 0.40, 5.00),
    "gpt-5.6-luna": (0.20, 1.20, 0.02, 0.25),
}
RUNGS = ["none", "low", "medium", "high", "xhigh", "max"]


class RequestParams(unittest.TestCase):

    def test_every_rung_is_sent_verbatim_temperature_only_at_none(self):
        for tier in TIERS:
            for rung in RUNGS:
                with self.subTest(tier=tier, rung=rung):
                    h = _ReqHost(model=tier, effort=rung, enabled=(rung != "none"))
                    h.call()
                    (kw,) = h.sent
                    self.assertEqual(kw["model"], tier)
                    self.assertEqual(kw["reasoning"], {"effort": rung, "summary": "auto"})
                    if rung == "none":
                        self.assertEqual(kw["temperature"], 0.7)   # 5.4+: the user's value
                    else:
                        self.assertNotIn("temperature", kw)
                    self.assertEqual(kw["text"], {"verbosity": "medium"})
                    self.assertIs(kw["store"], False)
                    self.assertEqual([t["type"] for t in kw["tools"]],
                                     ["function", "web_search_preview", "code_interpreter"])
                    self.assertEqual(h.infos(), [])
                    self.assertEqual((h.thinking_effort, h.thinking_mode), (rung, rung))

    def test_stale_minimal_becomes_low(self):
        # "minimal" is GPT-5.0 only — every 5.6 tier rejects it; the builder
        # sends the nearest rung, writes it back, and notices once per run.
        for tier in TIERS:
            with self.subTest(tier=tier):
                h = _ReqHost(model=tier, effort="minimal", enabled=True)
                h.call()
                self.assertEqual(h.sent[0]["reasoning"]["effort"], "low")
                self.assertNotIn("temperature", h.sent[0])
                self.assertEqual((h.thinking_effort, h.thinking_mode, h.thinking_enabled),
                                 ("low", "low", True))
                notices = h.infos()
                self.assertEqual(len(notices), 1)
                self.assertIn("no reasoning='minimal' rung", notices[0])
                h.call()
                self.assertEqual(h.infos(), [])

    def test_stale_claude_off_becomes_none_with_temperature(self):
        for tier in TIERS:
            with self.subTest(tier=tier):
                h = _ReqHost(model=tier, effort="off", enabled=False)
                h.call()
                self.assertEqual(h.sent[0]["reasoning"]["effort"], "none")
                self.assertEqual(h.sent[0]["temperature"], 0.7)
                self.assertEqual((h.thinking_effort, h.thinking_mode, h.thinking_enabled),
                                 ("none", "none", False))
                self.assertEqual(len(h.infos()), 1)

    def test_max_saved_on_a_56_steps_down_on_a_55(self):
        h = _ReqHost(model="gpt-5.5", effort="max", enabled=True)
        h.call()
        self.assertEqual(h.sent[0]["reasoning"]["effort"], "xhigh")
        self.assertEqual(h.thinking_effort, "xhigh")
        self.assertIn("no reasoning='max' rung", h.infos()[0])

    def test_none_on_a_pro_tier_steps_up_to_medium(self):
        h = _ReqHost(model="gpt-5.5-pro", effort="none", enabled=False)
        h.call()
        self.assertEqual(h.sent[0]["reasoning"]["effort"], "medium")
        self.assertNotIn("temperature", h.sent[0])
        self.assertEqual((h.thinking_effort, h.thinking_enabled), ("medium", True))


class CostRow(unittest.TestCase):

    def test_every_tier_bills_cache_writes(self):
        for tier, (inp, out, cached, write) in TIERS.items():
            with self.subTest(tier=tier):
                self.assertTrue(_ReqHost(model=tier)._openai_bills_cache_writes())
                self.assertEqual(StreamingMixin._get_pricing("OpenAI", tier),
                                 {"input": inp / 1_000_000, "output": out / 1_000_000,
                                  "cache_read": cached / 1_000_000,
                                  "cache_write": write / 1_000_000})
                # the write premium is exactly 1.25x input on every tier
                self.assertAlmostEqual(write, inp * 1.25, places=9)

    def test_terra_call_cost_with_writes_and_reads(self):
        # The SelfBot smoke's live shapes, priced at terra's rates: call 1
        # (68 in + 24 out + 6,006 written) and call 2 (the 6,006 read back).
        p = StreamingMixin._get_pricing("OpenAI", TERRA)

        def cost(u):
            return (u["input_tokens"] * p["input"] + u["output_tokens"] * p["output"]
                    + u.get("cache_creation_input_tokens", 0) * p.get("cache_write", 0)
                    + u.get("cache_read_input_tokens", 0) * p.get("cache_read", 0))

        c1 = cost({"input_tokens": 68, "output_tokens": 24,
                   "cache_creation_input_tokens": 6006, "cache_read_input_tokens": 0})
        c2 = cost({"input_tokens": 68, "output_tokens": 16,
                   "cache_creation_input_tokens": 41, "cache_read_input_tokens": 6006})
        self.assertAlmostEqual(c1, 0.000136 + 0.000288 + 0.015015, places=9)
        self.assertAlmostEqual(c2, 0.000136 + 0.000192 + 0.0001025 + 0.0012012, places=9)
        # Priced as a 3-tuple (the pre-audit table) call 1 would have been 25%
        # short on the written tokens: 6006 × $2.00 instead of × $2.50
        self.assertAlmostEqual(c1 - (68 * 2e-6 + 24 * 12e-6 + 6006 * 2e-6), 6006 * 0.5e-6, places=9)


class DebugPayload(unittest.TestCase):

    def test_payload_mirrors_the_request(self):
        for tier in TIERS:
            with self.subTest(tier=tier):
                h = _ReqHost(model=tier, effort="none", enabled=False, verbosity="low")
                text = h._payload_for_display([{"role": "user", "content": "hi"}])
                self.assertIn('"effort": "none"', text)
                self.assertIn('"temperature": 0.7', text)
                self.assertIn('"verbosity": "low"', text)
                h = _ReqHost(model=tier, effort="minimal", enabled=True)
                text = h._payload_for_display([{"role": "user", "content": "hi"}])
                self.assertIn('"effort": "low"', text)              # coerced like the request...
                self.assertEqual(h.thinking_effort, "minimal")      # ...but the dump never mutates state
                self.assertNotIn("temperature", text)


class Exposed(unittest.TestCase):

    def test_reasoning_combobox(self):
        for tier in TIERS:
            with self.subTest(tier=tier):
                h = _UIHost(model=tier)
                self.assertEqual(h._model_supports_thinking(), "extended")
                self.assertEqual(h._openai_reasoning_values(),
                                 ["None", "Low", "Medium", "High", "Xhigh", "Max"])

    def test_param_summary(self):
        self.assertEqual(_UIHost(model=TERRA, mode="none")._get_model_param_summary(),
                         "reasoning=None verbosity=medium temp=0.7")
        self.assertEqual(_UIHost(model="gpt-5.6-sol", mode="max", verbosity="high")._get_model_param_summary(),
                         "reasoning=Max verbosity=high")
        # stale values report what the wire will carry
        self.assertEqual(_UIHost(model=TERRA, mode="minimal")._get_model_param_summary(),
                         "reasoning=Low verbosity=medium")
        self.assertEqual(_UIHost(model="gpt-5.6-luna", mode="off")._get_model_param_summary(),
                         "reasoning=None verbosity=medium temp=0.7")
        self.assertEqual(_UIHost(model="gpt-5.5", mode="max")._get_model_param_summary(),
                         "reasoning=Xhigh verbosity=medium")


class NearestEffort(unittest.TestCase):
    FULL = ["none", "low", "medium", "high", "xhigh", "max"]          # 5.6
    NO_MAX = ["none", "low", "medium", "high", "xhigh"]               # 5.5 / 5.4
    CAPPED = ["none", "low", "medium", "high"]                        # 5.1, mini / nano
    PRO = ["medium", "high", "xhigh"]                                 # 5.5-pro
    GPT6 = ["low", "medium", "high", "xhigh", "max"]

    def test_matrix(self):
        f = OpenAIMixin._openai_nearest_effort
        for req, sup, exp in [
            ("max", self.FULL, "max"), ("none", self.FULL, "none"),
            ("minimal", self.FULL, "low"), ("off", self.FULL, "none"),
            ("adaptive", self.FULL, "none"), ("", self.FULL, "none"), (None, self.FULL, "none"),
            ("max", self.NO_MAX, "xhigh"), ("xhigh", self.CAPPED, "high"),
            ("max", self.CAPPED, "high"), ("minimal", self.CAPPED, "low"),
            ("none", self.PRO, "medium"), ("low", self.PRO, "medium"), ("max", self.PRO, "xhigh"),
            ("off", self.PRO, "medium"), ("minimal", self.PRO, "medium"),
            ("none", self.GPT6, "low"), ("minimal", self.GPT6, "low"), ("off", self.GPT6, "low"),
            ("max", self.GPT6, "max"), ("Xhigh", self.GPT6, "xhigh"),   # case-insensitive
            ("bogus", self.FULL, "none"),
        ]:
            with self.subTest(req=req, sup=sup):
                self.assertEqual(f(req, sup), exp)

    def test_effective_effort_uses_the_models_own_rungs(self):
        h = _ReqHost(model="gpt-5.2-mini", effort="xhigh")
        self.assertEqual(h._openai_effective_effort(), "high")
        h = _ReqHost(model="gpt-5.5-pro", effort="low")
        self.assertEqual(h._openai_effective_effort(), "medium")


class SavedEntry(unittest.TestCase):
    ENTRY = {"provider": "OpenAI", "model": TERRA, "temperature": 0.7,
             "thinking_enabled": False, "thinking_effort": "none", "thinking_budget": 8192,
             "thinking_mode": "none", "text_verbosity": "medium"}

    def test_reasoning_none_entry_restores_headless(self):
        h = _UIHost(model="gpt-6-astra", mode="max")
        h._restore_model_params(dict(self.ENTRY))
        self.assertEqual(h.model, TERRA)
        self.assertEqual((h.thinking_mode, h.thinking_effort, h.thinking_enabled),
                         ("none", "none", False))
        self.assertEqual(h._thinking_mode_var.get(), "None")
        self.assertEqual(h.temperature, 0.7)
        self.assertEqual(h._get_model_param_summary(), "reasoning=None verbosity=medium temp=0.7")

    def test_max_entry_restores_headless(self):
        h = _UIHost(model=TERRA, mode="none")
        h._restore_model_params(dict(self.ENTRY, thinking_enabled=True, thinking_effort="max",
                                     thinking_mode="max", text_verbosity="low"))
        self.assertEqual((h.thinking_mode, h.thinking_effort, h.thinking_enabled),
                         ("max", "max", True))
        self.assertEqual(h.text_verbosity, "low")
        self.assertEqual(h._get_model_param_summary(), "reasoning=Max verbosity=low")

    def test_claude_off_entry_on_sol_restores_headless(self):
        # An instruction saved on a Claude model (mode "off") re-pointed at sol:
        # restore keeps the saved value (no combobox headless), but every
        # wire-facing reader already says None + temperature, and the model
        # list check accepts sol.
        h = _UIHost(model=TERRA, mode="max")
        h.available_models = ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"]
        h._restore_model_params(dict(self.ENTRY, model="gpt-5.6-sol", thinking_effort="off",
                                     thinking_mode="off", text_verbosity="low"))
        self.assertEqual(h.model, "gpt-5.6-sol")
        self.assertEqual((h.thinking_mode, h.thinking_enabled), ("off", False))
        self.assertEqual(h._openai_effective_effort(), "none")
        self.assertEqual(h._get_model_param_summary(), "reasoning=None verbosity=low temp=0.7")
        self.assertEqual(h._model_drift_warnings, [])


if __name__ == "__main__":
    unittest.main()
