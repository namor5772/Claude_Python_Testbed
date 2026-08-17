"""Characterization tests: XAIMixin pure/detection helpers.

Locks the reasoning-effort matrix (longest-prefix match), the text-only
vision detection, and the UIMixin thinking-support plumbing that hangs off
them for provider "xAI". The matrix mirrors the LIVE /v1/models catalog
(verified 2026-07-17, grok-4.6 added 2026-08-18): grok-4.3 takes
none/low/medium/high, grok-4.5 and grok-4.6 take low..xhigh
(always-reasoning — "none" is HTTP 400 on both), grok-4.20-multi-agent
takes low..xhigh (the knob is agent collaboration count), and everything
else — the pinned -reasoning/-non-reasoning variants, grok-build, and the
aliases (bare grok-4.20, grok-latest — which floats: grok-4.3 until 2026-08,
grok-4.6 since) — has no client-side knob."""
import unittest

from myagent.ui_mixin import UIMixin
from myagent.xai_mixin import XAIMixin
from tests._util import stub


class _UIStub(UIMixin, XAIMixin):
    """_model_supports_thinking (UIMixin) consults _xai_reasoning_values
    (XAIMixin) for provider xAI — the stub needs both in its MRO, like App."""


class TestXaiReasoningValues(unittest.TestCase):
    CASES = {
        "grok-4.3": ["none", "low", "medium", "high"],
        # Alias/dated ids inherit their family's knob by prefix
        "grok-4.3-latest": ["none", "low", "medium", "high"],
        # grok-4.5 is always-reasoning — no "none" (HTTP 400, live-verified)
        "grok-4.5": ["low", "medium", "high", "xhigh"],
        "grok-4.5-latest": ["low", "medium", "high", "xhigh"],
        # grok-4.6 (2026-08 flagship) — same always-reasoning ladder; "none"
        # is HTTP 400 "This model does not support `reasoning_effort` value
        # `none`" (live-verified 2026-08-18)
        "grok-4.6": ["low", "medium", "high", "xhigh"],
        # Longest prefix wins — multi-agent must NOT fall through to a
        # shorter family entry
        "grok-4.20-multi-agent-0309": ["low", "medium", "high", "xhigh"],
        "grok-4.20-multi-agent-latest": ["low", "medium", "high", "xhigh"],
        # Pinned variants bake reasoning into the id — no knob. The bare
        # "grok-4.20" alias resolves server-side to the pinned reasoning
        # variant, so it correctly gets no knob either.
        "grok-4.20-0309-reasoning": None,
        "grok-4.20-0309-non-reasoning": None,
        "grok-4.20": None,
        # Floating alias (grok-4.6 since 2026-08): deliberately knobless so a
        # server-side re-point can never strand a saved effort value
        "grok-latest": None,
        "grok-build-0.1": None,
    }

    def test_values(self):
        for mid, expected in self.CASES.items():
            with self.subTest(model=mid):
                obj = stub(XAIMixin, model=mid)
                self.assertEqual(obj._xai_reasoning_values(), expected)

    def test_explicit_model_id_overrides_self_model(self):
        obj = stub(XAIMixin, model="grok-build-0.1")
        self.assertEqual(obj._xai_reasoning_values("grok-4.3"),
                         ["none", "low", "medium", "high"])


class TestXaiVisionModel(unittest.TestCase):
    CASES = {
        "grok-4.3": True,
        "grok-4.5": True,
        "grok-4.6": True,
        "grok-4.20-0309-non-reasoning": True,
        "grok-latest": True,
        # Re-aliased to grok-4.5 (vision) in the 2026-07 catalog — must NOT
        # be caught by the grok-build-0 prefix
        "grok-build-latest": True,
        "grok-build-0.1": False,
        "grok-code-fast-1": False,  # alias of grok-build-0.1
    }

    def test_vision(self):
        for mid, expected in self.CASES.items():
            with self.subTest(model=mid):
                obj = stub(XAIMixin, model=mid)
                self.assertEqual(obj._is_xai_vision_model(), expected)


class TestModelSupportsThinkingXai(unittest.TestCase):
    def test_extended_for_knob_families(self):
        for mid in ("grok-4.3", "grok-4.5", "grok-4.6",
                    "grok-4.20-multi-agent-0309"):
            with self.subTest(model=mid):
                obj = stub(_UIStub, provider="xAI", model=mid)
                self.assertEqual(obj._model_supports_thinking(), "extended")

    def test_none_for_knobless_families(self):
        for mid in ("grok-4.20-0309-reasoning", "grok-4.20-0309-non-reasoning",
                    "grok-latest", "grok-build-0.1"):
            with self.subTest(model=mid):
                obj = stub(_UIStub, provider="xAI", model=mid)
                self.assertIsNone(obj._model_supports_thinking())


if __name__ == "__main__":
    unittest.main()
