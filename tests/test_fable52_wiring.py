"""Claude Fable 5.2 (`claude-fable-5-2`, live 2026-11-09) in MyAgent and
SelfBot (wired 2026-11-10).

The successor to Fable 5.1 in the same always-on tier at a LOWER price: $8 /
$40 per MTok (5.1: $10 / $50), 5-minute cache writes 1.25x ($10), cache reads
0.025x ($0.20), the same 1M context / 128K output, the same Models API
capability tree (thinking adaptive only, effort low..max — read live
2026-11-10), and — new for the Fable line — FAST MODE, GA at launch with no
beta header: $16 / $80, up to 2.5x output tokens/sec.

Because the always-on class is prefix-matched, 5.2 needed nothing for
thinking / temperature / effort / the picker. What it needed, and what this
module pins:

- the pricing rows (standard and fast) in both tables, out-ranking the
  `claude-fable-5` prefix, and the fast row exactly 2x the standard one;
- the fast-mode gate admitting Fable / Mythos >= (5, 2) and nothing older in
  the family, so the editor's Fast checkbox appears for 5.2 alone;
- the REAL _stream_anthropic_call over tests/test_fast_mode.py's capturing
  fake client: the full Fable surface (adaptive + summarized + the binding,
  fallbacks "default", the model's 128K ceiling, no temperature, an effort
  at Max) with speed="fast" and its beta header when the box is ticked, and
  neither when it is not — the exact shapes probed live on 2026-11-10 (both
  served usage.speed="fast"; the standard call with the server-tool triple
  was served too);
- the Model upgrade's Anthropic default moved to claude-fable-5-2 Max;
- the fallback and adaptive lists of both apps naming the id;
- SelfBot's own detectors on the id (always-on, adaptive, no Off, no
  temperature, the Fable surface), without a fast mode of its own.
"""

import pathlib
import unittest

import SelfBot
from myagent.constants import (ADAPTIVE_THINKING_MODELS, ANTHROPIC_FAST_MODE_BETA,
                               ANTHROPIC_FAST_PRICING, ANTHROPIC_PRICING,
                               ANTHROPIC_SERVER_FALLBACK_BETA, ANTHROPIC_SERVER_FALLBACKS,
                               ANTHROPIC_THINKING_BINDING_BETA, FALLBACK_MODELS,
                               UPGRADE_DEFAULT_TARGETS)
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin
from tests._util import stub
from tests.test_fast_mode import _WireHost

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODEL = "claude-fable-5-2"
BINDING = {"prefix_mismatch_behavior": "drop_block"}


def _ui(model=MODEL, **extra):
    return stub(UIMixin, provider="Anthropic", model=model,
                _anthropic_unsupported=set(), **extra)


class PricingTests(unittest.TestCase):
    def test_standard_rows(self):
        for mid in (MODEL, "claude-mythos-5-2"):
            self.assertEqual(ANTHROPIC_PRICING[mid], (8.00, 40.00, 10.00, 0.20), mid)
        # Cheaper than 5.1 in every bucket, and the lookup finds the 5.2 row,
        # not the family prefix's.
        self.assertEqual(ANTHROPIC_PRICING["claude-fable-5-1"], (10.00, 50.00, 12.50, 0.25))
        p = StreamingMixin._get_pricing("Anthropic", MODEL)
        self.assertAlmostEqual(p["input"], 8e-06, places=12)
        self.assertAlmostEqual(p["cache_read"], 0.20e-06, places=12)
        self.assertIsNone(StreamingMixin._unpriced_model_warning("Anthropic", MODEL))

    def test_fast_rows_are_double(self):
        for mid in (MODEL, "claude-mythos-5-2"):
            self.assertEqual(ANTHROPIC_FAST_PRICING[mid], (16.00, 80.00, 20.00, 0.40), mid)
            self.assertEqual(ANTHROPIC_FAST_PRICING[mid],
                             tuple(2 * p for p in ANTHROPIC_PRICING[mid]))
        self.assertIsNone(StreamingMixin._unpriced_model_warning("Anthropic", MODEL, fast=True))
        # 5.1 has no fast mode and must stay OUT of the fast table: a fast
        # call there would be the gate's mistake, and it is unpriced + warned.
        self.assertNotIn("claude-fable-5-1", ANTHROPIC_FAST_PRICING)


class GateTests(unittest.TestCase):
    def test_fast_checkbox_for_5_2_and_later_only(self):
        h = _ui()
        for mid in (MODEL, "claude-mythos-5-2", "claude-fable-5-3", "claude-fable-6",
                    "claude-mythos-6-0"):
            self.assertTrue(h._anthropic_supports_fast_mode(mid), mid)
        for mid in ("claude-fable-5-1", "claude-fable-5", "claude-mythos-5-1",
                    "claude-mythos-5", "claude-sonnet-5-5", "claude-haiku-5-5"):
            self.assertFalse(h._anthropic_supports_fast_mode(mid), mid)
        # The Opus gate is unchanged.
        self.assertTrue(h._anthropic_supports_fast_mode("claude-opus-5-5"))
        self.assertTrue(h._anthropic_supports_fast_mode("claude-opus-4-8"))
        self.assertFalse(h._anthropic_supports_fast_mode("claude-opus-4-7"))
        # The active gate: box + capable model + not learned off.
        self.assertTrue(_ui(fast_mode=True)._anthropic_fast_active())
        self.assertFalse(_ui(fast_mode=False)._anthropic_fast_active())
        self.assertFalse(_ui("claude-fable-5-1", fast_mode=True)._anthropic_fast_active())
        self.assertFalse(stub(UIMixin, provider="Anthropic", model=MODEL, fast_mode=True,
                              _anthropic_unsupported={"speed"})._anthropic_fast_active())

    def test_the_rest_of_the_contract_is_5_1s(self):
        h = _ui()
        self.assertTrue(h._is_anthropic_always_on_thinking())
        self.assertTrue(h._is_anthropic_adaptive_model())
        self.assertTrue(h._anthropic_rejects_temperature())
        self.assertTrue(h._anthropic_thinking_on_by_default())
        self.assertEqual(h._anthropic_mode_values(),
                         ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(h._model_supports_thinking(), "adaptive")
        summary = _ui(fast_mode=True, thinking_mode="max", temperature=1.0)
        self.assertEqual(UIMixin._get_model_param_summary(summary), "mode=Max speed=fast")


class WireTests(unittest.TestCase):
    """The REAL _stream_anthropic_call on claude-fable-5-2."""

    def _host(self, fast, mode="max"):
        host = _WireHost(fast=fast)
        host.model = MODEL
        host.thinking_enabled = True
        host.thinking_mode = mode
        host.temperature = 0.7          # must never reach the wire
        return host

    def _call(self, host):
        stop, *_rest, usage = host._stream_anthropic_call(
            [{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(stop, "end_turn")
        self.assertEqual(len(host.calls), 1, host.calls)   # no reactive round trip
        return host.calls[0], usage

    def test_fast_sends_the_full_surface_plus_speed(self):
        (betas, kw), usage = self._call(self._host(fast=True))
        self.assertEqual(kw["model"], MODEL)
        self.assertEqual(kw["speed"], "fast")
        self.assertIn(ANTHROPIC_FAST_MODE_BETA, betas)          # tolerated on 5.2, probed live
        self.assertIn(ANTHROPIC_THINKING_BINDING_BETA, betas)
        self.assertIn(ANTHROPIC_SERVER_FALLBACK_BETA, betas)
        self.assertEqual(kw["thinking"], {"type": "adaptive", "display": "summarized",
                                          "block_binding": BINDING})
        self.assertEqual(kw["output_config"], {"effort": "max"})
        self.assertEqual(kw["extra_body"], {"fallbacks": ANTHROPIC_SERVER_FALLBACKS})
        self.assertEqual(kw["max_tokens"], 128000)
        self.assertNotIn("temperature", kw)
        self.assertEqual(usage["speed"], "fast")

    def test_unticked_sends_neither_speed_nor_its_beta(self):
        (betas, kw), usage = self._call(self._host(fast=False))
        self.assertNotIn("speed", kw)
        self.assertNotIn(ANTHROPIC_FAST_MODE_BETA, betas)
        self.assertEqual(kw["thinking"]["type"], "adaptive")    # the rest unchanged
        self.assertIsNone(usage["speed"])

    def test_adaptive_sends_no_effort(self):
        (_betas, kw), _usage = self._call(self._host(fast=True, mode="adaptive"))
        self.assertNotIn("output_config", kw)                   # the model's default, high
        self.assertEqual(kw["speed"], "fast")


class ListsAndDefaultsTests(unittest.TestCase):
    def test_upgrade_default_is_5_2_max(self):
        self.assertEqual(UPGRADE_DEFAULT_TARGETS["Anthropic"], {"model": MODEL, "level": "Max"})

    def test_both_apps_name_the_id(self):
        for lst, name in ((FALLBACK_MODELS, "MyAgent fallback"),
                          (SelfBot.FALLBACK_MODELS, "SelfBot fallback")):
            self.assertIn(MODEL, lst, name)
            self.assertLess(lst.index(MODEL), lst.index("claude-fable-5-1"), name)
        for s, name in ((ADAPTIVE_THINKING_MODELS, "MyAgent adaptive"),
                        (SelfBot.ADAPTIVE_THINKING_MODELS, "SelfBot adaptive")):
            self.assertIn(MODEL, s, name)
            self.assertIn("claude-mythos-5-2", s, name)

    def test_tool_schema_text_names_5_2(self):
        from myagent.constants import META_TOOLS
        props = next(t for t in META_TOOLS
                     if t["name"] == "manage_instructions")["input_schema"]["properties"]
        self.assertIn("Anthropic claude-fable-5-2 Max, OpenAI gpt-6-astra Max",
                      props["upgrade_target"]["description"])
        self.assertIn("Anthropic fast mode (Fable 5.2 / Opus 5.5 / Opus 5 / Opus 4.8 only)",
                      props["fast_mode"]["description"])


class SelfBotTests(unittest.TestCase):
    """SelfBot's in-file detectors on the id — the same contract by prefix,
    and no fast mode (SelfBot never sends speed)."""

    def setUp(self):
        self.app = SelfBot.App.__new__(SelfBot.App)
        self.app.model = MODEL
        self.app.thinking_mode = "max"
        self.app.thinking_enabled = True
        self.app._anthropic_unsupported = set()
        self.app._no_temperature = set()

    def test_detectors(self):
        a = self.app
        self.assertTrue(a._is_always_on_thinking())
        self.assertTrue(a._is_adaptive_model())
        self.assertTrue(a._rejects_temperature())
        self.assertTrue(a._thinking_on_by_default())
        self.assertEqual(a._anthropic_mode_values(),
                         ["Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertEqual(a._model_supports_thinking(), "adaptive")

    def test_fable_surface(self):
        f = self.app._fable_features()
        self.assertEqual(f["betas"], [ANTHROPIC_THINKING_BINDING_BETA,
                                      ANTHROPIC_SERVER_FALLBACK_BETA])
        self.assertEqual(f["block_binding"], BINDING)
        self.assertEqual(f["fallbacks"], "default")

    def test_pricing_through_selfbots_lookup(self):
        p = self.app._get_pricing(MODEL)
        self.assertAlmostEqual(p["input"], 8e-06, places=12)
        self.assertAlmostEqual(p["output"], 40e-06, places=12)
        self.assertAlmostEqual(p["cache_read"], 0.20e-06, places=12)

    def test_selfbot_has_no_fast_mode(self):
        src = (ROOT / "SelfBot.py").read_text(encoding="utf-8")
        self.assertNotIn('speed="fast"', src)
        self.assertNotIn("fast_mode", src)


if __name__ == "__main__":
    unittest.main()
