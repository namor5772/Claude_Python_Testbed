"""Characterization tests for SelfBot's OpenAI (GPT-6) provider path (2026-09-06).

SelfBot has no Provider combobox: a gpt-* model id routes the round trip to
MyAgent's inherited OpenAIMixin (`_stream_responses_call`), everything else to
the Anthropic path. These pin the pure helpers that decide the route and what
the toolbar / cost layer do with it; the Tk-bound `_on_model_selected` branch
and the live round trip were verified by hand (see CLAUDE_SELFBOT.md).

SelfBot IS importable in-process (module import builds no Tk root); the bare
`App.__new__` stub from tests/_util.py serves its methods exactly as it serves
the myagent mixins. Import guarded like tests/test_selfbot_delete_confirm.py.
"""

import queue
import sys
import unittest
from types import SimpleNamespace

from tests._util import stub

_saved_argv = sys.argv
sys.argv = ["SelfBot.py"]
try:
    import SelfBot
    _IMPORT_ERROR = None
except Exception as e:  # pragma: no cover - environment-dependent
    SelfBot = None
    _IMPORT_ERROR = e
finally:
    sys.argv = _saved_argv


@unittest.skipIf(SelfBot is None, f"SelfBot not importable here: {_IMPORT_ERROR}")
class SelfBotOpenAIRoute(unittest.TestCase):

    def test_inherits_the_openai_provider(self):
        # The whole provider comes from the one mixin: caller + translators +
        # detection helpers. A missing method here means SelfBot's OpenAI
        # branch would AttributeError at the first gpt-* message.
        for name in ("_stream_responses_call", "_stream_responses",
                     "_messages_to_responses", "_tools_to_responses",
                     "_openai_always_reasoning", "_openai_effective_effort",
                     "_has_reasoning_xhigh", "_has_reasoning_max"):
            self.assertTrue(hasattr(SelfBot.App, name), name)

    def test_routing_is_by_model_id(self):
        app = stub(SelfBot.App, model="gpt-6-astra")
        self.assertTrue(app._is_openai_model())
        self.assertEqual(app._model_provider(), "OpenAI")
        self.assertEqual(app._model_supports_thinking(), "extended")
        for claude in ("claude-opus-5", "claude-fable-5-1", "claude-haiku-4-5-20251001"):
            self.assertFalse(app._is_openai_model(claude), claude)
            self.assertEqual(app._model_provider(claude), "Anthropic")
            self.assertNotEqual(app._model_supports_thinking(claude), "extended", claude)

    def test_reasoning_rungs_have_no_off(self):
        app = stub(SelfBot.App, model="gpt-6-astra")
        self.assertEqual(app._openai_mode_values(), ["Low", "Medium", "High", "Xhigh", "Max"])

    def test_pricing_is_provider_aware(self):
        # gpt-* → OPENAI_PRICING (incl. the billed cache-write rate on the
        # gpt-6-astra 4-tuple); Claude → the unchanged ANTHROPIC_PRICING path.
        self.assertEqual(SelfBot.App._get_pricing("gpt-6-astra"),
                         {"input": 10.00 / 1_000_000, "output": 50.00 / 1_000_000,
                          "cache_read": 1.00 / 1_000_000, "cache_write": 12.50 / 1_000_000})
        # A 3-tuple row carries no cache_write key (writes stay full-rate input)
        terra = SelfBot.App._get_pricing("gpt-5.6-terra")
        self.assertEqual(set(terra), {"input", "output", "cache_read"})
        self.assertIsNone(SelfBot.App._get_pricing("gpt-6-unknown-tier"))
        opus = SelfBot.App._get_pricing("claude-opus-5")
        self.assertEqual(set(opus), {"input", "output", "cache_write", "cache_read"})

    def test_param_summary_reports_the_floor_coerced_effort(self):
        # A stale Claude "off" carried onto a GPT-6 model is what
        # _stream_responses_call sends as "low" — the summary must say so.
        app = stub(SelfBot.App, model="gpt-6-astra", thinking_mode="off",
                   thinking_effort="off", thinking_enabled=False, temperature=1.0)
        self.assertEqual(app._get_model_param_summary(), "reasoning=Low")
        app.thinking_mode = app.thinking_effort = "xhigh"
        self.assertEqual(app._get_model_param_summary(), "reasoning=Xhigh")

    def test_track_openai_cost_prices_all_four_buckets(self):
        # The live SelfBot smoke's first call (2026-09-06): 68 in, 24 out,
        # 6,006 cache-write tokens (the server-tool definitions) → $0.076955.
        app = stub(SelfBot.App, model="gpt-6-astra", _session_cost=0.0, queue=queue.Queue())
        app._track_openai_cost({"input_tokens": 68, "output_tokens": 24,
                                "cache_creation_input_tokens": 6006,
                                "cache_read_input_tokens": 0})
        self.assertAlmostEqual(app._session_cost, 0.076955, places=9)
        msg = app.queue.get_nowait()
        self.assertEqual(msg["type"], "cost_update")
        self.assertEqual((msg["input_tokens"], msg["output_tokens"],
                          msg["cache_write_tokens"], msg["cache_read_tokens"]), (68, 24, 6006, 0))
        # ...and the repeat call reads the same tokens back at $1/M
        app._track_openai_cost({"input_tokens": 68, "output_tokens": 16,
                                "cache_creation_input_tokens": 41,
                                "cache_read_input_tokens": 6006})
        self.assertAlmostEqual(app._session_cost, 0.076955 + 0.0079985, places=9)
        # No usage / unpriced model → nothing happens
        app._track_openai_cost(None)
        self.assertAlmostEqual(app._session_cost, 0.076955 + 0.0079985, places=9)

    def test_openai_model_listing(self):
        app = stub(SelfBot.App, openai_client=None, _model_display_names={})
        self.assertEqual(app._fetch_selfbot_openai_models(), [])
        listing = SimpleNamespace(data=[SimpleNamespace(id=i) for i in (
            "gpt-5.6-terra", "gpt-6-astra", "gpt-6-chat-latest", "gpt-6-nova", "o3")])
        app.openai_client = SimpleNamespace(models=SimpleNamespace(list=lambda: listing))
        # Only the gpt-6 family, -chat excluded, sorted, display name = id
        self.assertEqual(app._fetch_selfbot_openai_models(), ["gpt-6-astra", "gpt-6-nova"])
        self.assertEqual(app._model_display_names["gpt-6-astra"], "gpt-6-astra")
        # A listing failure serves the fallback rather than an empty picker
        app.openai_client = SimpleNamespace(models=SimpleNamespace(
            list=lambda: (_ for _ in ()).throw(RuntimeError("down"))))
        self.assertEqual(app._fetch_selfbot_openai_models(), ["gpt-6-astra"])


if __name__ == "__main__":
    unittest.main()
