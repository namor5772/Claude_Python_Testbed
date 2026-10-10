"""The Anthropic output ceiling (2026-10-11), and the dropped-thinking notice.

Every Anthropic call used to send max_tokens = MAX_TOKENS_THINKING (32,768)
with thinking on and MAX_TOKENS (8,192) with it off — figures from the
budget_tokens era, when thinking had a budget of its own under max_tokens.
Adaptive thinking has no budget: max_tokens is the only bound on reasoning,
and the chat Act_on_unread_emails_2026-10-11_063721 (claude-haiku-5-5,
upgraded to claude-fable-5-1 at Max from call 9) reasoned past 32,768 on
call 14 without writing a word — stop_reason=max_tokens, 32,768 output
tokens billed (US$1.69) and discarded, the run ended. Every current model
takes 128,000 (the Models API's max_tokens field, read live that day; the
three dated 4.5 ids 64,000).

Now a call sends the model's OWN ceiling: the startup listing's max_tokens
per id (_anthropic_output_caps), else the family table
(ANTHROPIC_MAX_OUTPUT_TOKENS), and a 400 naming a lower ceiling — the exact
wording probed live — learns it and retries. The same run printed "API
dropped 7 replayed thinking block(s) (model_binding_mismatch) — earlier
history changed" on every call after the upgrade: Haiku's blocks, which
Fable cannot read, dropped unbilled — no fault, misworded, and repeated.
The notice is now worded by reason and said once per run per shape.
"""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import myagent.anthropic_mixin as am
import myagent.kimi_mixin as km
import myagent.streaming_mixin as sm
from myagent.anthropic_mixin import AnthropicMixin
from myagent.constants import (ANTHROPIC_MAX_OUTPUT_DEFAULT, ANTHROPIC_MAX_OUTPUT_TOKENS,
                               FALLBACK_MODELS, MAX_TOKENS, MAX_TOKENS_THINKING)
from myagent.helpers import _ToolBlock, parse_output_cap, parse_overflow_counts
from myagent.ui_mixin import UIMixin
from tests.test_fast_mode import _FakeStream, _WireHost, _bad_request
from tests.test_review_core import _ScriptHost, _drain

FABLE = "claude-fable-5-1"
HAIKU45 = "claude-haiku-4-5-20251001"
# The API's wording for an over-cap max_tokens, verbatim (probed 2026-10-11).
OVER_CAP_400 = ("max_tokens: 128000 > 64000, which is the maximum allowed number of "
                "output tokens for claude-zeta-9")
DROP = {"type": "thinking_dropped", "path": "messages.1.content.0",
        "reason": "model_binding_mismatch"}


def _wire(model, mode="max", enabled=True, caps=None, fail_first=None):
    host = _WireHost(fast=False, fail_first=fail_first)
    host.model = model
    host.thinking_enabled = enabled
    host.thinking_mode = mode
    if caps is not None:
        host._anthropic_output_caps = caps
    return host


def _call(host):
    return host._stream_anthropic_call([{"role": "user", "content": "hi"}], 3, False)


def _infos(host):
    return [m["content"] for m in _drain(host.queue) if m["type"] == "tool_info"]


def _drop_lines(host):
    return [i for i in _infos(host) if "dropped" in i]


def _message(transformations):
    usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
                            cache_read_input_tokens=0, iterations=None)
    return SimpleNamespace(content=[], stop_reason="end_turn", model=FABLE, usage=usage,
                           input_transformations=transformations)


def _host_dropping(transformations):
    """A wire host whose every response lists `transformations`; the message
    is settable per call through host._message."""
    host = _wire(FABLE)
    host._message = _message(transformations)

    class _Messages:
        @staticmethod
        def stream(betas, **api_kwargs):
            host.calls.append((list(betas), dict(api_kwargs)))
            return _FakeStream(host._message)

    host.client = SimpleNamespace(beta=SimpleNamespace(messages=_Messages))
    return host


class TableTests(unittest.TestCase):
    def test_every_current_tier_takes_128k_and_the_dated_4_5_ids_64k(self):
        cap = AnthropicMixin._anthropic_output_cap_for
        for mid in (FABLE, "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5",
                    "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-sonnet-5",
                    "claude-haiku-5-5", "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                    "claude-sonnet-4-6"):
            self.assertEqual(cap(mid), 128000, mid)
        for mid in (HAIKU45, "claude-opus-4-5-20251101", "claude-sonnet-4-5-20250929"):
            self.assertEqual(cap(mid), 64000, mid)

    def test_a_dated_snapshot_rides_with_its_family(self):
        cap = AnthropicMixin._anthropic_output_cap_for
        self.assertEqual(cap("claude-fable-5-1-20260828"), 128000)
        self.assertEqual(cap("claude-haiku-5-5-20261007"), 128000)

    def test_the_longest_prefix_wins_whatever_the_table_order(self):
        with mock.patch.object(am, "ANTHROPIC_MAX_OUTPUT_TOKENS",
                               (("claude-x", 1000), ("claude-x-9", 2000))):
            self.assertEqual(AnthropicMixin._anthropic_output_cap_for("claude-x-9-2027"), 2000)
            self.assertEqual(AnthropicMixin._anthropic_output_cap_for("claude-x-1"), 1000)
        with mock.patch.object(am, "ANTHROPIC_MAX_OUTPUT_TOKENS",
                               (("claude-x-9", 2000), ("claude-x", 1000))):
            self.assertEqual(AnthropicMixin._anthropic_output_cap_for("claude-x-9-2027"), 2000)

    def test_an_id_no_row_names_gets_the_default(self):
        cap = AnthropicMixin._anthropic_output_cap_for
        for mid in ("claude-zeta-9", "", None):
            self.assertEqual(cap(mid), ANTHROPIC_MAX_OUTPUT_DEFAULT, mid)

    def test_every_row_is_a_claude_prefix_with_a_positive_ceiling(self):
        for prefix, cap in ANTHROPIC_MAX_OUTPUT_TOKENS:
            self.assertTrue(prefix.startswith("claude-"), prefix)
            self.assertIsInstance(cap, int)
            self.assertGreater(cap, 0)


class LookupTests(unittest.TestCase):
    def test_the_listing_value_wins_over_the_table(self):
        host = _wire(FABLE, caps={FABLE: 100000})
        self.assertEqual(host._anthropic_output_cap(), 100000)
        self.assertEqual(host._anthropic_output_cap("claude-opus-5-5"), 128000)   # unlisted → table

    def test_a_bare_host_or_a_useless_value_falls_to_the_table(self):
        host = _wire(HAIKU45)                          # no _anthropic_output_caps at all
        self.assertEqual(host._anthropic_output_cap(), 64000)
        for bad in (0, -1, None, True, "128000"):
            host = _wire(HAIKU45, caps={HAIKU45: bad})
            self.assertEqual(host._anthropic_output_cap(), 64000, bad)


class WireTests(unittest.TestCase):
    def test_fable_at_max_sends_its_whole_window(self):
        host = _wire(FABLE, "max")
        stop, *_ = _call(host)
        self.assertEqual(stop, "end_turn")
        self.assertEqual(len(host.calls), 1)
        _betas, kw = host.calls[0]
        self.assertEqual(kw["max_tokens"], 128000)
        self.assertEqual(kw["output_config"], {"effort": "max"})
        self.assertEqual(kw["thinking"]["type"], "adaptive")

    def test_thinking_off_sends_the_same_ceiling(self):
        host = _wire("claude-opus-5", "off", enabled=False)
        _call(host)
        self.assertEqual(host.calls[0][1]["thinking"], {"type": "disabled"})
        self.assertEqual(host.calls[0][1]["max_tokens"], 128000)
        host = _wire(HAIKU45, "off", enabled=False)
        _call(host)
        self.assertNotIn("thinking", host.calls[0][1])
        self.assertEqual(host.calls[0][1]["max_tokens"], 64000)

    def test_the_largest_manual_budget_now_sits_under_the_ceiling(self):
        # The 32K preset equalled the old thinking cap, and the API wants
        # budget_tokens < max_tokens.
        host = _wire(HAIKU45, "high", enabled=True)
        host.thinking_budget = 32768
        _call(host)
        kw = host.calls[0][1]
        self.assertEqual(kw["thinking"], {"type": "enabled", "budget_tokens": 32768})
        self.assertEqual(kw["max_tokens"], 64000)
        self.assertLess(kw["thinking"]["budget_tokens"], kw["max_tokens"])

    def test_the_listing_value_reaches_the_wire(self):
        host = _wire(FABLE, caps={FABLE: 100000})
        _call(host)
        self.assertEqual(host.calls[0][1]["max_tokens"], 100000)

    def test_moonshot_keeps_the_two_old_constants_and_the_anthropic_paths_do_not(self):
        self.assertEqual((MAX_TOKENS, MAX_TOKENS_THINKING), (8192, 32768))
        self.assertTrue(hasattr(km, "MAX_TOKENS_THINKING"))
        for module in (am, sm):
            for name in ("MAX_TOKENS", "MAX_TOKENS_THINKING", "MODEL_MAX_OUTPUT_TOKENS"):
                self.assertFalse(hasattr(module, name), (module.__name__, name))

    def test_the_live_call_and_the_debug_dump_read_one_helper(self):
        self.assertIn('api_kwargs["max_tokens"] = self._anthropic_output_cap()',
                      inspect.getsource(AnthropicMixin._stream_anthropic_call))
        self.assertIn('payload["max_tokens"] = self._anthropic_output_cap()',
                      inspect.getsource(sm.StreamingMixin._payload_for_display))


class CeilingRungTests(unittest.TestCase):
    def test_the_parser_reads_the_apis_wording_and_nothing_else(self):
        self.assertEqual(parse_output_cap(OVER_CAP_400), 64000)
        self.assertEqual(parse_output_cap(
            "max_tokens: 200,000 > 128,000, which is the maximum allowed number of "
            "output tokens for claude-fable-5-1"), 128000)
        for other in ("prompt is too long: 1,200 tokens > 1,000 maximum", "", None,
                      "max_tokens: 0 > 0, which is"):
            self.assertIsNone(parse_output_cap(other), other)
        # and the overflow parser never mistakes the cap message for an overflow
        self.assertEqual(parse_overflow_counts(OVER_CAP_400), (None, None))

    def test_a_refused_ceiling_is_learned_and_the_call_retried_at_it(self):
        host = _wire("claude-zeta-9", fail_first=_bad_request(OVER_CAP_400))
        stop, *_ = _call(host)
        self.assertEqual(stop, "end_turn")
        self.assertEqual([kw["max_tokens"] for _b, kw in host.calls],
                         [ANTHROPIC_MAX_OUTPUT_DEFAULT, 64000])
        self.assertEqual(host._anthropic_output_caps, {"claude-zeta-9": 64000})
        self.assertTrue(any("64,000" in i and "ceiling" in i for i in _infos(host)))
        host.calls.clear()
        _call(host)                                    # the next call sends it up front
        self.assertEqual([kw["max_tokens"] for _b, kw in host.calls], [64000])

    def test_a_learned_ceiling_lands_in_an_existing_listing_dict(self):
        host = _wire("claude-zeta-9", caps={FABLE: 128000},
                     fail_first=_bad_request(OVER_CAP_400))
        _call(host)
        self.assertEqual(host._anthropic_output_caps, {FABLE: 128000, "claude-zeta-9": 64000})

    def test_an_overflow_400_still_walks_the_overflow_rung(self):
        host = _wire(FABLE, fail_first=_bad_request(
            "prompt is too long: 1,200 tokens > 1,000 maximum"))
        with self.assertRaises(RuntimeError):           # one round, nothing to trim
            _call(host)
        self.assertNotIn(FABLE, getattr(host, "_anthropic_output_caps", {}))
        self.assertEqual(len(host.calls), 1)


class _ListingHost(AnthropicMixin, UIMixin):
    pass


class ListingTests(unittest.TestCase):
    def _host(self, data, fail=False):
        host = _ListingHost.__new__(_ListingHost)

        class _Models:
            @staticmethod
            def list(limit):
                if fail:
                    raise RuntimeError("offline")
                return SimpleNamespace(data=data)

        host.client = SimpleNamespace(models=_Models)
        return host

    def test_the_listing_fills_every_served_ids_ceiling(self):
        data = [SimpleNamespace(id=FABLE, display_name="Claude Fable 5.1", max_tokens=128000),
                SimpleNamespace(id=HAIKU45, display_name="Claude Haiku 4.5", max_tokens=64000),
                # hidden from the picker, still served — a pinned instruction may run it
                SimpleNamespace(id="claude-3-opus-20240229", display_name="Claude 3 Opus",
                                max_tokens=4096),
                # an older SDK object without the field: the table answers for it
                SimpleNamespace(id="claude-sonnet-5", display_name="Claude Sonnet 5")]
        host = self._host(data)
        ids = host._fetch_available_models()
        self.assertEqual(ids, [FABLE, HAIKU45, "claude-sonnet-5"])
        self.assertEqual(host._anthropic_output_caps,
                         {FABLE: 128000, HAIKU45: 64000, "claude-3-opus-20240229": 4096})
        host.model = "claude-sonnet-5"
        self.assertEqual(host._anthropic_output_cap(), 128000)
        host.model = "claude-3-opus-20240229"
        self.assertEqual(host._anthropic_output_cap(), 4096)

    def test_a_failed_listing_keeps_what_a_400_taught(self):
        host = self._host([], fail=True)
        host._anthropic_output_caps = {"claude-zeta-9": 64000}
        self.assertEqual(host._fetch_available_models(), list(FALLBACK_MODELS))
        self.assertEqual(host._anthropic_output_caps, {"claude-zeta-9": 64000})

    def test_the_app_starts_with_an_empty_dict(self):
        src = Path(__file__).resolve().parents[1].joinpath("MyAgent.py").read_text(encoding="utf-8")
        self.assertIn("self._anthropic_output_caps = {}", src)


class DroppedNoticeTests(unittest.TestCase):
    def test_another_models_blocks_are_explained_once_per_run(self):
        host = _host_dropping([dict(DROP)] * 7)
        _call(host)
        lines = _drop_lines(host)
        self.assertEqual(len(lines), 1, lines)
        self.assertIn("7 replayed thinking block(s)", lines[0])
        self.assertIn("written by another model", lines[0])
        self.assertIn("before the upgrade", lines[0])
        self.assertIn("not billed", lines[0])
        self.assertNotIn("earlier history changed", lines[0])
        _call(host)                                    # the same 7 on the next call
        self.assertEqual(_drop_lines(host), [])
        host._message = _message([dict(DROP)] * 8)     # a new count: said again
        _call(host)
        self.assertEqual(len(_drop_lines(host)), 1)

    def test_a_history_edit_is_worded_as_one(self):
        host = _host_dropping([{"reason": "prefix_binding_mismatch"}] * 2)
        _call(host)
        line = _drop_lines(host)[0]
        self.assertIn("2 replayed", line)
        self.assertIn("earlier history changed", line)
        self.assertNotIn("another model", line)

    def test_the_wording_per_reason(self):
        why = AnthropicMixin._thinking_drop_why
        self.assertEqual(why(["prefix_binding_mismatch"]),
                         "earlier history changed since they were written")
        both = why(sorted(["prefix_binding_mismatch", "model_binding_mismatch"]))
        self.assertIn("another model", both)
        self.assertIn("earlier history changed", both)
        self.assertEqual(why(["odd"]), "reason: odd")

    def test_sdk_objects_count_too(self):
        host = _host_dropping([SimpleNamespace(type="thinking_dropped",
                                               reason="model_binding_mismatch")] * 3)
        _call(host)
        self.assertIn("3 replayed", _drop_lines(host)[0])

    def test_no_transformations_say_nothing(self):
        host = _host_dropping(None)
        _call(host)
        self.assertEqual(_drop_lines(host), [])
        host._message = _message([])
        _call(host)
        self.assertEqual(_drop_lines(host), [])

    def test_a_run_starts_with_nothing_said(self):
        self.assertIn("self._thinking_drops_noted = None",
                      inspect.getsource(sm.StreamingMixin.stream_worker))
        host = _ScriptHost([("end_turn", [], "done")])
        host._thinking_drops_noted = (7, ("model_binding_mismatch",))
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(sm, "APICOST_LOG_FILE", str(Path(d) / "cost.txt")):
            host._result_file = str(Path(d) / "r.json")
            host.stream_worker([{"role": "user", "content": "go"}])
        self.assertIsNone(host._thinking_drops_noted)


class CutOffWarningTests(unittest.TestCase):
    def _run(self, host):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(sm, "APICOST_LOG_FILE", str(Path(d) / "cost.txt")):
            host._result_file = str(Path(d) / "r.json")
            host.stream_worker([{"role": "user", "content": "go"}])
            with open(host._result_file, encoding="utf-8") as f:
                return json.load(f)

    @staticmethod
    def _cut_off_line(host):
        return [m["content"] for m in _drain(host.queue)
                if m["type"] == "warning" and "cut off" in m["content"]][0]

    def test_the_warning_names_the_models_ceiling(self):
        class _CapHost(_ScriptHost):
            def _anthropic_output_cap(self, model=None):
                return 128000

        host = _CapHost([("max_tokens", [], "")])      # thinking alone: no tool call, no text
        host.model = FABLE
        result = self._run(host)
        line = self._cut_off_line(host)
        self.assertIn("128,000-token output window claude-fable-5-1 allows", line)
        self.assertIn("thinking included", line)
        self.assertIn("ask for smaller steps", line)
        self.assertNotIn("Thinking on raises it", line)
        self.assertNotIn("was not run", line)
        self.assertEqual(result["status"], "error")
        self.assertIn("stop_reason=max_tokens", result["error"])

    def test_a_host_without_the_helper_keeps_the_generic_wording(self):
        cut = [_ToolBlock("write_file", "t1", {"path": "big.txt"})]
        host = _ScriptHost([("max_tokens", cut, "")])
        self._run(host)
        line = self._cut_off_line(host)
        self.assertIn("was not run", line)
        self.assertIn("output window the call allows", line)
        self.assertNotIn("-token", line)
        self.assertEqual(host.tool_calls, [])


if __name__ == "__main__":
    unittest.main()
