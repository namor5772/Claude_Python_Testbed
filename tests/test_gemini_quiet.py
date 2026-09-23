"""Characterization tests for Gemini's "thinking off" wire contract
(2026-09-23 audit).

With MyAgent's Thinking checkbox OFF the Gemini call used to carry NO
thinking_config — and a Gemini 3.x tier sent nothing thinks at its DEFAULT
(probed live that day: 168 thoughts on 3.8-flash, 295 on 3.1-pro, on one
short question), billed at the output rate and shown nowhere. The quietest
setting a tier accepts differs per tier (GEMINI_QUIET_STYLES), so it is a
ladder: pre-seeded for the served tiers, learned per model per session from
the API's 400s, never able to end in a failure the old code did not have
(the last rung sends nothing). The same decision (_gemini_thinking_kwargs)
feeds the live call and the Debug payload, which until then showed Google an
Anthropic-shaped request."""
import queue
import types as pytypes
import unittest

from google.genai import types as genai_types

from myagent.constants import GEMINI_QUIET_STYLES
from myagent.gemini_mixin import GeminiMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin

TOOL = {"name": "read_file", "description": "d",
        "input_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}


class _Bad400(Exception):
    code = 400


def _chunk(text="12:15"):
    part = pytypes.SimpleNamespace(thought=False, text=text, function_call=None)
    content = pytypes.SimpleNamespace(parts=[part])
    usage = pytypes.SimpleNamespace(prompt_token_count=34, candidates_token_count=5,
                                    thoughts_token_count=0, cached_content_token_count=0)
    return pytypes.SimpleNamespace(candidates=[pytypes.SimpleNamespace(content=content)],
                                   usage_metadata=usage)


def _style_of(config):
    tc = getattr(config, "thinking_config", None)
    if tc is None:
        return "none"
    if tc.thinking_budget == 0:
        return "budget0"
    if tc.thinking_budget:
        return "budget"
    level = str(tc.thinking_level)
    level = level.split(".")[-1].lower() if "." in level else level.lower()
    return {"minimal": "minimal", "low": "low"}.get(level, "level:" + level)


class _FakeModels:
    def __init__(self, reject):
        self.reject = set(reject)
        self.sent = []

    def generate_content_stream(self, model, contents, config):
        style = _style_of(config)
        self.sent.append(style)
        if style in self.reject:
            raise _Bad400(f"400 INVALID_ARGUMENT. Thinking {style} is not supported for this model.")
        return iter([_chunk()])


class _Host(GeminiMixin, UIMixin, StreamingMixin):
    """The live-call host: GeminiMixin's caller plus UIMixin's two Gemini
    detection helpers, as in App. Widget-free."""

    def __init__(self, model="gemini-3.8-flash", enabled=False, effort="high", reject=()):
        self.provider = "Google"
        self.model = model
        self.thinking_enabled = enabled
        self.thinking_effort = effort
        self.temperature = 0.4
        self.stop_requested = False
        self.queue = queue.Queue()
        self.gemini_client = pytypes.SimpleNamespace(models=_FakeModels(reject))

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return [dict(TOOL)]

    def call(self):
        return self._stream_gemini_call([{"role": "user", "content": "hi"}], 5, False)

    def infos(self):
        out = []
        while not self.queue.empty():
            m = self.queue.get_nowait()
            if m["type"] == "tool_info":
                out.append(m["content"])
        return out


class QuietStyle(unittest.TestCase):

    def test_seeds(self):
        for mid, expected in (("gemini-3.8-flash", "budget0"), ("gemini-3.7-flash", "budget0"),
                              ("gemini-flash-latest", "budget0"), ("gemini-3.1-pro-preview", "low"),
                              ("gemini-3.1-pro-preview-customtools", "low"), ("gemini-pro-latest", "low"),
                              ("gemini-3.6-flash", "minimal"), ("gemini-3.5-flash", "minimal"),
                              ("gemini-3.5-flash-lite", "minimal"), ("gemini-3.1-flash-lite", "minimal"),
                              ("gemini-3-flash-preview", "minimal"), ("gemini-flash-lite-latest", "minimal"),
                              ("gemini-3.9-flash", "minimal"),          # unlisted: starts at the top
                              ("gemini-2.5-pro", "none"), ("gemini-2.5-flash", "budget0")):
            with self.subTest(model=mid):
                self.assertEqual(_Host(model=mid)._gemini_quiet_style(), expected)

    def test_configs_per_rung(self):
        c = GeminiMixin._gemini_quiet_config("minimal")
        self.assertEqual((str(c.thinking_level).lower().endswith("minimal"), c.include_thoughts), (True, None))
        self.assertEqual(GeminiMixin._gemini_quiet_config("budget0").thinking_budget, 0)
        self.assertTrue(str(GeminiMixin._gemini_quiet_config("low").thinking_level).lower().endswith("low"))
        self.assertIsNone(GeminiMixin._gemini_quiet_config("none"))
        self.assertEqual(GEMINI_QUIET_STYLES, ("minimal", "budget0", "low", "none"))

    def test_thinking_kwargs_is_the_one_decision(self):
        cfg, style = _Host(enabled=True, effort="high")._gemini_thinking_kwargs()
        self.assertTrue(str(cfg.thinking_level).lower().endswith("high"))
        self.assertTrue(cfg.include_thoughts)
        self.assertIsNone(style)
        cfg, style = _Host(enabled=False)._gemini_thinking_kwargs()
        self.assertEqual((cfg.thinking_budget, style), (0, "budget0"))
        self.assertEqual(_Host(model="gemini-2.0-flash")._gemini_thinking_kwargs(), (None, None))
        # 3.1-pro accepts medium now (the retired 3-pro preview's low|high
        # rule is gone): no coercion
        cfg, _ = _Host(model="gemini-3.1-pro-preview", enabled=True, effort="medium")._gemini_thinking_kwargs()
        self.assertTrue(str(cfg.thinking_level).lower().endswith("medium"))


class LiveCallLadder(unittest.TestCase):

    def test_seeded_tier_sends_its_rung_at_once(self):
        h = _Host(model="gemini-3.8-flash")
        stop, blocks, text, thinking, _label, usage = h.call()
        self.assertEqual(h.gemini_client.models.sent, ["budget0"])
        self.assertEqual((stop, text), ("end_turn", "12:15"))
        self.assertEqual(h.infos(), ["Thinking is off: gemini-3.8-flash is sent thinking_budget=0 "
                                     "(this tier's floor — a few thinking tokens remain).\n"])
        # Announced once per model per session
        h.call()
        self.assertEqual(h.infos(), [])

    def test_unlisted_tier_learns_from_the_400(self):
        h = _Host(model="gemini-3.9-flash", reject={"minimal"})
        h.call()
        self.assertEqual(h.gemini_client.models.sent, ["minimal", "budget0"])
        self.assertEqual(h._gemini_quiet_styles["gemini-3.9-flash"], "budget0")
        infos = h.infos()
        self.assertEqual(len(infos), 2)
        self.assertIn("thinking_level=minimal", infos[0])
        self.assertTrue(infos[1].startswith("⚠ gemini-3.9-flash rejected the thinking-off setting — retrying with thinking_budget=0"))
        # The next call goes straight to the learned rung
        h.call()
        self.assertEqual(h.gemini_client.models.sent[-1], "budget0")

    def test_a_tier_that_cannot_stop_lands_on_low(self):
        h = _Host(model="gemini-3.9-pro", reject={"minimal", "budget0"})
        h.call()
        self.assertEqual(h.gemini_client.models.sent, ["minimal", "budget0", "low"])
        self.assertEqual(h._gemini_quiet_styles["gemini-3.9-pro"], "low")

    def test_the_last_rung_sends_nothing_and_succeeds(self):
        h = _Host(model="gemini-3.9-odd", reject={"minimal", "budget0", "low"})
        stop, _b, text, _t, _l, _u = h.call()
        self.assertEqual(h.gemini_client.models.sent, ["minimal", "budget0", "low", "none"])
        self.assertEqual(text, "12:15")
        self.assertEqual(h._gemini_quiet_styles["gemini-3.9-odd"], "none")

    def test_thinking_on_is_untouched_and_keeps_its_own_swap(self):
        h = _Host(model="gemini-3.8-flash", enabled=True, effort="low")
        h.call()
        self.assertEqual(h.gemini_client.models.sent, ["low"])
        self.assertEqual(h.infos(), [])                         # no quiet announcement
        # A 400 on the level style still swaps to the legacy budget (the
        # 2026-07 fallback), never onto the quiet ladder
        h = _Host(model="gemini-3.8-flash", enabled=True, effort="low", reject={"low"})
        h.call()
        self.assertEqual(h.gemini_client.models.sent, ["low", "budget"])
        self.assertNotIn("gemini-3.8-flash", getattr(h, "_gemini_quiet_styles", {}))


class DebugPayload(unittest.TestCase):

    def test_google_dump_mirrors_the_wire(self):
        h = _Host(model="gemini-3.8-flash")
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertIn('"system_instruction": "SYS"', text)
        self.assertIn('"temperature": 0.4', text)
        self.assertIn('"thinking_budget": 0', text)
        self.assertIn('"thinking_off": "thinking_budget=0', text)
        self.assertIn('"function_declarations"', text)
        self.assertIn('"read_file"', text)
        self.assertNotIn('"max_tokens"', text)                  # no longer the Anthropic shape
        self.assertNotIn('"system":', text)
        h = _Host(model="gemini-3.5-flash", enabled=True, effort="medium")
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertIn('"thinking_level": "MEDIUM"', text)      # the SDK's enum value
        self.assertIn('"include_thoughts": true', text)
        self.assertNotIn("thinking_off", text)


if __name__ == "__main__":
    unittest.main()
