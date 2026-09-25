"""Anthropic server-side tools + the web-search fee (2026-09-25).

Two changes of the same day, both apps:

- `_anthropic_server_tools` — the per-model server-tool declaration: the
  CURRENT triple (web_search_20260209 + web_fetch_20260209 +
  code_execution_20260521 — which also makes code execution free) for every
  served model except Haiku 4.5, which rejects the 20260209 web tools ("does
  not support programmatic tool calling", probed live) and keeps the older
  web pair (web_fetch_20250910 under its beta) with the new code-execution
  type; a session that learned the triple off ("server_tools_20260209" in
  `_anthropic_unsupported`) falls back too. SelfBot carries a byte-identical
  in-file copy.

- the web-search fee — Anthropic bills a flat $10/1,000 per EXECUTED search
  on top of tokens; the usage dict now carries
  `usage.server_tool_use.web_search_requests` and stream_worker adds
  `count × ANTHROPIC_WEB_SEARCH_FEE` to the call's cost (until now a
  documented bias: every search-heavy run's cost line was under).
"""

import inspect
import unittest
from types import SimpleNamespace
from unittest import mock

import SelfBot
from tests._util import stub
from tests.test_costlog_run_fields import _Host as _CostLogHost
from tests.test_fast_mode import _WireHost, _drain
from myagent.anthropic_mixin import AnthropicMixin
from myagent.constants import ANTHROPIC_WEB_SEARCH_FEE
from myagent.ui_mixin import UIMixin

NEW_TRIPLE = [
    {"type": "web_search_20260209", "name": "web_search"},
    {"type": "web_fetch_20260209", "name": "web_fetch"},
    {"type": "code_execution_20260521", "name": "code_execution"},
]
FALLBACK_SET = [
    {"type": "web_search_20250305", "name": "web_search"},
    {"type": "web_fetch_20250910", "name": "web_fetch"},
    {"type": "code_execution_20260521", "name": "code_execution"},
]


class _Detect(AnthropicMixin, UIMixin):
    pass


def _host(model, unsupported=()):
    return stub(_Detect, provider="Anthropic", model=model,
                _anthropic_unsupported=set(unsupported))


class ServerToolSelection(unittest.TestCase):
    def test_current_models_get_the_new_triple_without_betas(self):
        for model in ("claude-sonnet-5", "claude-opus-5-5", "claude-opus-5",
                      "claude-fable-5-1", "claude-opus-4-5"):
            with self.subTest(model=model):
                tools, betas = _host(model)._anthropic_server_tools()
                self.assertEqual(tools, NEW_TRIPLE)
                self.assertEqual(betas, [])

    def test_haiku_gets_the_fallback_set_with_the_web_fetch_beta(self):
        for model in ("claude-haiku-4-5", "claude-haiku-4-5-20251001"):
            with self.subTest(model=model):
                tools, betas = _host(model)._anthropic_server_tools()
                self.assertEqual(tools, FALLBACK_SET)
                self.assertEqual(betas, ["web-fetch-2025-09-10"])

    def test_learned_off_session_falls_back(self):
        tools, betas = _host("claude-sonnet-5",
                             unsupported={"server_tools_20260209"})._anthropic_server_tools()
        self.assertEqual(tools, FALLBACK_SET)
        self.assertEqual(betas, ["web-fetch-2025-09-10"])

    def test_bare_host_without_the_learn_off_set_gets_the_triple(self):
        bare = stub(_Detect, provider="Anthropic", model="claude-sonnet-5")
        self.assertEqual(bare._anthropic_server_tools()[0], NEW_TRIPLE)

    def test_selfbot_copy_is_byte_identical(self):
        self.assertEqual(
            inspect.getsource(SelfBot.App._anthropic_server_tools),
            inspect.getsource(AnthropicMixin._anthropic_server_tools))

    def test_debug_dump_uses_the_same_helper(self):
        # The dump must show what is really declared — pinned as a source scan
        # (the full _payload_for_display needs a built system prompt).
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "myagent" / "streaming_mixin.py").read_text(encoding="utf-8")
        self.assertIn("tools.extend(self._anthropic_server_tools()[0])", src)


class UsageCarriesSearchCount(unittest.TestCase):
    def test_count_from_server_tool_use(self):
        served = SimpleNamespace(input_tokens=10, output_tokens=5,
                                 cache_creation_input_tokens=0,
                                 cache_read_input_tokens=0, iterations=None,
                                 server_tool_use=SimpleNamespace(web_search_requests=3))
        host = _WireHost(fast=False, usage=served)
        *_, usage = host._stream_anthropic_call(
            [{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(usage["web_search_requests"], 3)

    def test_absent_field_reads_zero(self):
        host = _WireHost(fast=False)   # its usage has no server_tool_use
        *_, usage = host._stream_anthropic_call(
            [{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(usage["web_search_requests"], 0)


class _SearchingHost(_CostLogHost):
    """_CostLogHost whose calls each report 2 executed web searches."""

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        stop, blocks, text, think, label, usage = super()._stream_anthropic_call(
            messages, max_retries, label_emitted)
        usage["web_search_requests"] = 2
        return stop, blocks, text, think, label, usage


class WebSearchFee(unittest.TestCase):
    def test_fee_added_to_each_priced_call(self):
        host = _SearchingHost(second_call="end")
        with mock.patch.object(host, "_log_api_cost"):
            host.stream_worker([{"role": "user", "content": "go"}])
        costs = [m["call_cost"] for m in _drain(host.queue)
                 if m["type"] == "cost_update"]
        self.assertEqual(len(costs), 2)
        # 1000 in / 100 out at Sonnet 5's $2/$10, plus 2 searches at $0.01.
        expected = 1000 * 2e-06 + 100 * 10e-06 + 2 * ANTHROPIC_WEB_SEARCH_FEE
        for cost in costs:
            self.assertAlmostEqual(cost, expected)

    def test_fee_constant_is_ten_dollars_per_thousand(self):
        self.assertAlmostEqual(ANTHROPIC_WEB_SEARCH_FEE, 0.01)

    def test_selfbot_cost_block_folds_the_fee_in(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parent.parent
               / "SelfBot.py").read_text(encoding="utf-8")
        self.assertIn('"web_search_requests", 0) or 0) * ANTHROPIC_WEB_SEARCH_FEE', src)


if __name__ == "__main__":
    unittest.main()
