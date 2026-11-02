"""Claude Sonnet 5.5 (`claude-sonnet-5-5`, live 2026-09-28) on MyAgent's
Anthropic wire (the 2026-11-02 model audit).

The Sonnet that changed the off switch: the Models API tree says
thinking.types.disabled is UNSUPPORTED and between_tools SUPPORTED — the only
served model with that pair, read live that day — and the 15-request probe of
2026-10-14 (for SelfBot) gave the contract: an explicit disable is HTTP 400
('To turn thinking off on this model, send "thinking": {"type":
"between_tools"}'); `between_tools` is accepted at effort high or below only
and with NO other field inside `thinking` (the binding's beta HEADER alone
tolerated); adaptive thinking takes the preserved-thinking binding and the
summarized display; `fallbacks: "default"` is accepted beside either shape
(a model list refused); the budget form and any temperature but 1 are 400;
the 20260209 triple is accepted.

Until 2026-11-02 MyAgent sent Sonnet 5.5 the explicit disable under the
Sonnet >= 5 rules (SelfBot had the contract since 2026-10-14), so an
instruction pinned to it with Thinking Off would have failed on call 1 — none
of the four pinned that day was. This module drives the REAL
_stream_anthropic_call over tests/test_fast_mode.py's capturing fake client
and pins the request shape, with Sonnet 5 and Haiku 5.5 as the unchanged
controls; the detector by version; the picker's ladder; and a wiring scan of
the two builders (the live call and the Debug dump)."""

import pathlib
import unittest

from myagent.constants import (ANTHROPIC_SERVER_FALLBACK_BETA, ANTHROPIC_SERVER_FALLBACKS,
                               ANTHROPIC_THINKING_BINDING_BETA)
from tests._util import stub
from tests.test_fast_mode import _WireHost
from myagent.ui_mixin import UIMixin

ROOT = pathlib.Path(__file__).resolve().parents[1]
TRIPLE = [{"type": "web_search_20260209", "name": "web_search"},
          {"type": "web_fetch_20260209", "name": "web_fetch"},
          {"type": "code_execution_20260521", "name": "code_execution"}]
BINDING = {"prefix_mismatch_behavior": "drop_block"}


def _host(model, mode, enabled, temperature=0.7):
    host = _WireHost(fast=False)
    host.model = model
    host.thinking_enabled = enabled
    host.thinking_mode = mode
    host.temperature = temperature
    return host


def _call(host):
    stop, *_ = host._stream_anthropic_call([{"role": "user", "content": "hi"}], 3, False)
    assert stop == "end_turn"
    assert len(host.calls) == 1, host.calls   # no reactive 400 round trip
    return host.calls[0]


class DetectTests(unittest.TestCase):
    def test_sonnet_5_5_and_later_by_version(self):
        h = stub(UIMixin, provider="Anthropic", model="claude-sonnet-5-5")
        for mid in ("claude-sonnet-5-5", "claude-sonnet-5-5-20260928", "claude-sonnet-5-6",
                    "claude-sonnet-6", "claude-sonnet-6-0"):
            self.assertTrue(h._is_anthropic_sonnet_55(mid), mid)
        for mid in ("claude-sonnet-5", "claude-sonnet-5-20260601",   # a date, not a minor
                    "claude-sonnet-4-6", "claude-sonnet-4-5-20250929",
                    "claude-opus-5-5", "claude-haiku-5-5", "claude-fable-5-1"):
            self.assertFalse(h._is_anthropic_sonnet_55(mid), mid)
        self.assertTrue(h._is_anthropic_sonnet_55())            # self.model
        self.assertFalse(stub(UIMixin, provider="OpenAI", model="claude-sonnet-5-5")
                         ._is_anthropic_sonnet_55())

    def test_the_picker_keeps_off_and_the_whole_ladder(self):
        # Off stays: the model CAN stop extended thinking — through
        # between_tools, which the builder sends in its place.
        h = stub(UIMixin, provider="Anthropic", model="claude-sonnet-5-5",
                 _anthropic_unsupported=set())
        self.assertEqual(h._anthropic_mode_values(),
                         ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"])
        self.assertTrue(h._anthropic_thinking_on_by_default())
        self.assertTrue(h._anthropic_rejects_temperature())
        self.assertFalse(h._is_anthropic_always_on_thinking())


class WireTests(unittest.TestCase):
    def test_off_is_a_bare_between_tools(self):
        betas, kw = _call(_host("claude-sonnet-5-5", "off", enabled=False))
        self.assertEqual(kw["thinking"], {"type": "between_tools"})   # nothing else inside
        self.assertNotIn("temperature", kw)                           # 400 on any value but 1
        self.assertNotIn("output_config", kw)                         # Off sends no effort
        self.assertEqual(kw["extra_body"], {"fallbacks": ANTHROPIC_SERVER_FALLBACKS})
        self.assertEqual(kw["max_tokens"], 128000)
        self.assertEqual(kw["tools"], TRIPLE)
        self.assertIn(ANTHROPIC_SERVER_FALLBACK_BETA, betas)
        self.assertNotIn(ANTHROPIC_THINKING_BINDING_BETA, betas)      # the header goes with the field

    def test_adaptive_low_carries_the_binding_the_display_and_the_fallbacks(self):
        betas, kw = _call(_host("claude-sonnet-5-5", "low", enabled=True))
        self.assertEqual(kw["thinking"], {"type": "adaptive", "display": "summarized",
                                          "block_binding": BINDING})
        self.assertEqual(kw["output_config"], {"effort": "low"})
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["extra_body"], {"fallbacks": ANTHROPIC_SERVER_FALLBACKS})
        self.assertEqual(kw["tools"], TRIPLE)
        self.assertIn(ANTHROPIC_THINKING_BINDING_BETA, betas)
        self.assertIn(ANTHROPIC_SERVER_FALLBACK_BETA, betas)

    def test_adaptive_and_the_top_rungs(self):
        _betas, kw = _call(_host("claude-sonnet-5-5", "adaptive", enabled=True))
        self.assertNotIn("output_config", kw)        # the API default: high
        for level in ("xhigh", "max"):
            _betas, kw = _call(_host("claude-sonnet-5-5", level, enabled=True))
            self.assertEqual(kw["output_config"], {"effort": level})
            self.assertEqual(kw["thinking"]["type"], "adaptive")

    def test_a_dated_snapshot_rides_along(self):
        _betas, kw = _call(_host("claude-sonnet-5-5-20260928", "off", enabled=False))
        self.assertEqual(kw["thinking"], {"type": "between_tools"})

    def test_sonnet_5_and_haiku_5_5_are_unchanged_controls(self):
        betas, kw = _call(_host("claude-sonnet-5", "off", enabled=False))
        self.assertEqual(kw["thinking"], {"type": "disabled"})
        self.assertNotIn("extra_body", kw)
        self.assertNotIn(ANTHROPIC_SERVER_FALLBACK_BETA, betas)
        betas, kw = _call(_host("claude-haiku-5-5", "off", enabled=False))
        self.assertEqual(kw["thinking"], {"type": "disabled"})
        self.assertNotIn("extra_body", kw)

    def test_learned_off_surfaces_leave_the_body(self):
        host = _host("claude-sonnet-5-5", "low", enabled=True)
        host._anthropic_unsupported.update({"block_binding", "fallbacks"})
        betas, kw = _call(host)
        self.assertNotIn("block_binding", kw["thinking"])
        self.assertNotIn("extra_body", kw)
        self.assertNotIn(ANTHROPIC_THINKING_BINDING_BETA, betas)
        self.assertNotIn(ANTHROPIC_SERVER_FALLBACK_BETA, betas)


class WiringTests(unittest.TestCase):
    def test_both_builders_choose_the_off_shape_by_the_detector(self):
        # The live call (anthropic_mixin) and the Debug dump (streaming_mixin)
        # pick between_tools / disabled through the ONE detector, so the dump
        # cannot drift from the wire.
        live = (ROOT / "myagent" / "anthropic_mixin.py").read_text(encoding="utf-8")
        dump = (ROOT / "myagent" / "streaming_mixin.py").read_text(encoding="utf-8")
        for src, name in ((live, "anthropic_mixin"), (dump, "streaming_mixin")):
            self.assertIn('{"type": "between_tools"}', src, name)
            self.assertIn("if self._is_anthropic_sonnet_55()", src, name)
        features = live[live.index("def _anthropic_fable_features("):live.index("def _refusal_note(")]
        self.assertIn("sonnet55 = self._is_anthropic_sonnet_55()", features)
        self.assertIn("if not (always_on or sonnet55 or self._is_anthropic_haiku_5()):", features)
        self.assertIn('if (always_on or sonnet55) and "fallbacks" not in unsupported:', features)


if __name__ == "__main__":
    unittest.main()
