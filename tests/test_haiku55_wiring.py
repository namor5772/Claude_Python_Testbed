"""Claude Haiku 5.5 (`claude-haiku-5-5`, live 2026-10-07) on the Anthropic wire
(2026-10-08 audit).

The first Haiku with the Claude 5 request contract, every fact below probed
live that day against the Models API capability tree and the real endpoint:
adaptive thinking only (the budget form is HTTP 400), thinking ON when the
param is omitted, an explicit disable accepted at effort high or below (400 at
xhigh / max), effort low..max (default medium), no sampling params (any
temperature but 1 is '`temperature` is deprecated for this model'), the
current server-tool triple accepted (Haiku 4.5 still rejects it), the
preserved-thinking binding accepted beside adaptive thinking and refused
beside a disable, NO server-side fallback (a list is 400, "default" is a
no-op) and no fast mode.

Until that day every one of these keyed on Opus / Sonnet versions, so
claude-haiku-5-5 fell through to "no thinking control at all": a run would
have sent no thinking param (silent adaptive thinking, billed, pane dark), a
temperature (400 on the first call), the old web pair, and no pricing row
(no cost line). This module drives the REAL _stream_anthropic_call over
tests/test_fast_mode.py's capturing fake client and pins the request shape
for the two modes an instruction can run it in, with Haiku 4.5 as the
unchanged control."""

import pathlib
import tempfile
import unittest
from unittest import mock

import myagent.streaming_mixin as sm
from myagent.constants import (ANTHROPIC_FAST_MODE_BETA, ANTHROPIC_SERVER_FALLBACK_BETA,
                               ANTHROPIC_THINKING_BINDING_BETA)
from tests.test_costlog_run_fields import _Host as _LoopHost
from tests.test_fast_mode import _WireHost

TRIPLE = [{"type": "web_search_20260209", "name": "web_search"},
          {"type": "web_fetch_20260209", "name": "web_fetch"},
          {"type": "code_execution_20260521", "name": "code_execution"}]
OLD_PAIR = [{"type": "web_search_20250305", "name": "web_search"},
            {"type": "web_fetch_20250910", "name": "web_fetch"},
            {"type": "code_execution_20260521", "name": "code_execution"}]


def _host(model, mode, enabled, temperature=0.7, fast=False):
    host = _WireHost(fast=fast)
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


class Haiku55Wire(unittest.TestCase):
    def test_off_is_an_explicit_disable_with_nothing_else(self):
        betas, kw = _call(_host("claude-haiku-5-5", "off", enabled=False))
        self.assertEqual(kw["thinking"], {"type": "disabled"})   # thinks on omission
        self.assertNotIn("temperature", kw)                      # 400 on any value but 1
        self.assertNotIn("output_config", kw)                    # Off sends no effort
        self.assertNotIn("extra_body", kw)                       # no fallbacks
        self.assertNotIn("speed", kw)
        self.assertEqual(kw["max_tokens"], 128000)   # the model's own ceiling, Off too
        self.assertEqual(kw["tools"], TRIPLE)
        for beta in (ANTHROPIC_THINKING_BINDING_BETA, ANTHROPIC_SERVER_FALLBACK_BETA,
                     ANTHROPIC_FAST_MODE_BETA, "web-fetch-2025-09-10"):
            self.assertNotIn(beta, betas)

    def test_adaptive_low_carries_the_binding_and_the_effort(self):
        betas, kw = _call(_host("claude-haiku-5-5", "low", enabled=True))
        self.assertEqual(kw["thinking"], {"type": "adaptive", "display": "summarized",
                                          "block_binding": {"prefix_mismatch_behavior": "drop_block"}})
        self.assertEqual(kw["output_config"], {"effort": "low"})
        self.assertNotIn("temperature", kw)
        self.assertNotIn("extra_body", kw)
        self.assertEqual(kw["max_tokens"], 128000)
        self.assertEqual(kw["tools"], TRIPLE)
        self.assertIn(ANTHROPIC_THINKING_BINDING_BETA, betas)
        self.assertNotIn(ANTHROPIC_SERVER_FALLBACK_BETA, betas)

    def test_adaptive_and_the_top_rungs(self):
        _betas, kw = _call(_host("claude-haiku-5-5", "adaptive", enabled=True))
        self.assertNotIn("output_config", kw)        # the API default: medium
        for level in ("xhigh", "max"):
            _betas, kw = _call(_host("claude-haiku-5-5", level, enabled=True))
            self.assertEqual(kw["output_config"], {"effort": level})
            self.assertEqual(kw["thinking"]["type"], "adaptive")

    def test_a_dated_snapshot_rides_along(self):
        betas, kw = _call(_host("claude-haiku-5-5-20261007", "high", enabled=True))
        self.assertEqual(kw["thinking"]["type"], "adaptive")
        self.assertIn("block_binding", kw["thinking"])
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["tools"], TRIPLE)

    def test_haiku_4_5_is_unchanged(self):
        # The control: the manual budget, a temperature, the old web pair.
        betas, kw = _call(_host("claude-haiku-4-5-20251001", "high", enabled=True))
        self.assertEqual(kw["thinking"], {"type": "enabled", "budget_tokens": 8192})
        self.assertEqual(kw["max_tokens"], 64000)    # the dated 4.5 ids' ceiling (live 2026-10-11)
        self.assertNotIn("output_config", kw)
        self.assertNotIn("block_binding", kw["thinking"])
        self.assertEqual(kw["tools"], OLD_PAIR)
        self.assertIn("web-fetch-2025-09-10", betas)
        self.assertNotIn(ANTHROPIC_THINKING_BINDING_BETA, betas)
        betas, kw = _call(_host("claude-haiku-4-5-20251001", "off", enabled=False))
        self.assertNotIn("thinking", kw)             # thinking-off on omission
        self.assertEqual(kw["temperature"], 0.7)

    def test_learned_off_binding_is_dropped_for_haiku_too(self):
        host = _host("claude-haiku-5-5", "low", enabled=True)
        host._anthropic_unsupported.add("block_binding")
        betas, kw = _call(host)
        self.assertNotIn("block_binding", kw["thinking"])
        self.assertNotIn(ANTHROPIC_THINKING_BINDING_BETA, betas)


class _LongPromptHost(_LoopHost):
    """The real loop on claude-haiku-5-5: call 1's prompt is 121K tokens
    (over the second-card line), call 2's 51K (under it)."""

    def __init__(self):
        super().__init__(second_call="end", instruction="Long run")
        self.model = "claude-haiku-5-5"

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        stop, blocks, text, thinking, label, usage = super()._stream_anthropic_call(
            messages, max_retries, label_emitted)
        usage["cache_creation_input_tokens"] = 0
        usage["cache_read_input_tokens"] = 120_000 if self._calls_made == 1 else 50_000
        return stop, blocks, text, thinking, label, usage


class LongContextAccounting(unittest.TestCase):
    """The announcement's second rate card ($0.50 / $2.50 above 100K prompt
    tokens) is applied per call by the prompt the API counted — input plus
    both cache buckets — with one ⚠ per run when a call first crosses it."""

    def test_each_call_is_priced_on_the_card_its_prompt_selects(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "APICostLog_test.txt"
            with mock.patch.object(sm, "APICOST_LOG_FILE", str(log)):
                host = _LongPromptHost()
                host.stream_worker([{"role": "user", "content": "go"}])
                line = log.read_text(encoding="utf-8").splitlines()[0].split(";")
        # call 1 on the long card: 1,000 in + 100 out + 120,000 cache reads;
        # call 2 on the base card: 1,000 in + 100 out + 50,000 cache reads.
        call1 = 1000 * 0.50e-6 + 100 * 2.50e-6 + 120_000 * 0.05e-6
        call2 = 1000 * 0.10e-6 + 100 * 0.50e-6 + 50_000 * 0.01e-6
        self.assertEqual(line[1:3], ["Anthropic", "claude-haiku-5-5"])
        self.assertEqual(line[3], f"{call1 + call2:.4f}")
        self.assertEqual(line[3], "0.0074")
        self.assertEqual(line[8:12], ["2000", "200", "0", "170000"])
        warns = []
        while not host.queue.empty():
            msg = host.queue.get_nowait()
            if msg.get("type") == "warning":
                warns.append(msg["content"])
        notes = [w for w in warns if "second rate card" in w]
        self.assertEqual(len(notes), 1)        # once per run, not per call
        self.assertIn("121,000", notes[0])
        self.assertIn("100,000", notes[0])
        self.assertIn("$0.50 / $2.50", notes[0])

    def test_a_run_under_the_line_is_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = pathlib.Path(tmp) / "APICostLog_test.txt"
            with mock.patch.object(sm, "APICOST_LOG_FILE", str(log)):
                host = _LoopHost(second_call="end")
                host.model = "claude-haiku-5-5"
                host.stream_worker([{"role": "user", "content": "go"}])
                line = log.read_text(encoding="utf-8").splitlines()[0].split(";")
        self.assertEqual(line[3], f"{2 * (1000 * 0.10e-6 + 100 * 0.50e-6):.4f}")
        warns = []
        while not host.queue.empty():
            msg = host.queue.get_nowait()
            if msg.get("type") == "warning":
                warns.append(msg["content"])
        self.assertFalse([w for w in warns if "second rate card" in w])


if __name__ == "__main__":
    unittest.main()
