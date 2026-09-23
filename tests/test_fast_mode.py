"""Characterization tests for Anthropic fast mode (2026-09-24).

Fast mode (research preview) serves Opus 4.8 / 5 / 5.5 at up to 2.5x output
tokens/sec for exactly 2x the per-token price when the request carries
speed="fast" under ANTHROPIC_FAST_MODE_BETA. What this module locks down:

- `_anthropic_supports_fast_mode` — the Opus >= (4, 8) version gate (Opus 4.7
  rejects speed="fast"; 4.6 silently runs standard; a dated snapshot's date
  in the minor slot does not count);
- `_anthropic_fast_active` — the ONE gate the live request, the Debug dump
  and the title / cost-log param summary share (checkbox AND capable model
  AND not learned off this session);
- `ANTHROPIC_FAST_PRICING` — every row exactly 2x its standard row, and
  `_get_pricing(speed="fast")` pricing from it — with a MISS returning None
  (unpriced + warned) rather than the standard row at half the real rate;
- `_unpriced_model_warning(fast=True)` — the run-start ⚠ when a fast run's
  model has no fast row;
- `_stream_anthropic_call` — speed="fast" + the beta on the wire only when
  active, the learn-once 400 rung that retries at standard speed, and the
  usage dict's "speed" (the API's served speed, falling back to the request);
- stream_worker pricing a call by usage["speed"] (the fast table at "fast");
- the persistence sites (instruction entry / applied snapshot / state file /
  manage_instructions) carrying "fast_mode" — pinned by source scan, the
  cheap guarantee that a save path can't silently drop the key.
"""

import queue
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import anthropic

from tests._util import stub
from tests.test_costlog_run_fields import _Host as _CostLogHost
from myagent.anthropic_mixin import AnthropicMixin
from myagent.constants import (ANTHROPIC_FAST_MODE_BETA, ANTHROPIC_FAST_PRICING,
                               ANTHROPIC_PRICING)
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin

_REPO = Path(__file__).resolve().parent.parent


class _DetectHost(AnthropicMixin, UIMixin):
    pass


def _host(model, provider="Anthropic", fast=False, unsupported=()):
    return stub(_DetectHost, provider=provider, model=model, fast_mode=fast,
                _anthropic_unsupported=set(unsupported))


class SupportsFastMode(unittest.TestCase):
    CASES = {
        # Fast-capable: exactly the research preview's Opus tiers (>= 4.8).
        "claude-opus-5-5": True,
        "claude-opus-5": True,
        "claude-opus-4-8": True,
        "claude-opus-5-20260724": True,    # dated Opus 5 snapshot: date != minor
        # Below the gate / outside the Opus family.
        "claude-opus-4-7": False,          # rejects speed="fast" outright
        "claude-opus-4-6": False,          # silently standard — never offer it
        "claude-opus-4-20250514": False,   # retired 4.0 dated id, not 4.<date>
        "claude-sonnet-5": False,
        "claude-fable-5-1": False,
        "claude-haiku-4-5": False,
    }

    def test_matrix(self):
        for model, expected in self.CASES.items():
            with self.subTest(model=model):
                self.assertIs(_host(model)._anthropic_supports_fast_mode(), expected)

    def test_other_providers_never_fast(self):
        self.assertFalse(_host("claude-opus-5-5", provider="OpenAI")
                         ._anthropic_supports_fast_mode())


class FastActive(unittest.TestCase):
    def test_requires_checkbox_and_capable_model(self):
        self.assertTrue(_host("claude-opus-5-5", fast=True)._anthropic_fast_active())
        self.assertFalse(_host("claude-opus-5-5", fast=False)._anthropic_fast_active())
        self.assertFalse(_host("claude-sonnet-5", fast=True)._anthropic_fast_active())

    def test_learned_off_surface_wins(self):
        self.assertFalse(_host("claude-opus-5-5", fast=True,
                               unsupported={"speed"})._anthropic_fast_active())

    def test_missing_attrs_mean_off(self):
        # Bare hosts (stream_worker's test harnesses) carry neither fast_mode
        # nor _anthropic_unsupported — the gate must answer False, not raise.
        bare = stub(_DetectHost, provider="Anthropic", model="claude-opus-5-5")
        self.assertFalse(bare._anthropic_fast_active())


class FastPricing(unittest.TestCase):
    def test_every_fast_row_is_exactly_double_its_standard_row(self):
        # The pricing page defines fast as a 2x multiplier with the caching
        # multipliers applied on top — so each bucket is exactly 2x.
        for model, fast_row in ANTHROPIC_FAST_PRICING.items():
            with self.subTest(model=model):
                std_row = ANTHROPIC_PRICING[model]
                self.assertEqual(fast_row, tuple(2 * p for p in std_row))

    def test_fast_lookup(self):
        p = StreamingMixin._get_pricing("Anthropic", "claude-opus-5-5", speed="fast")
        expected = {"input": 8e-06, "output": 40e-06,
                    "cache_write": 10e-06, "cache_read": 0.40e-06}
        self.assertEqual(set(p), set(expected))
        for key, value in expected.items():
            self.assertAlmostEqual(p[key], value, places=12, msg=key)

    def test_standard_speed_and_none_use_the_standard_table(self):
        std = StreamingMixin._get_pricing("Anthropic", "claude-opus-5-5")
        for speed in (None, "standard"):
            with self.subTest(speed=speed):
                self.assertEqual(StreamingMixin._get_pricing(
                    "Anthropic", "claude-opus-5-5", speed=speed), std)

    def test_fast_miss_is_unpriced_never_the_standard_row(self):
        # Pricing a fast call from the standard table would under-report 2x —
        # the same bug class as Opus 5.5's first two days on the opus-5 row.
        for model in ("claude-sonnet-5", "claude-opus-6"):
            with self.subTest(model=model):
                self.assertIsNone(StreamingMixin._get_pricing(
                    "Anthropic", model, speed="fast"))

    def test_speed_is_ignored_for_other_providers(self):
        self.assertEqual(
            StreamingMixin._get_pricing("OpenAI", "gpt-5.6-terra", speed="fast"),
            StreamingMixin._get_pricing("OpenAI", "gpt-5.6-terra"))


class UnpricedFastWarning(unittest.TestCase):
    def test_fast_capable_model_with_row_is_silent(self):
        self.assertIsNone(StreamingMixin._unpriced_model_warning(
            "Anthropic", "claude-opus-5-5", fast=True))

    def test_fast_run_without_a_fast_row_warns(self):
        note = StreamingMixin._unpriced_model_warning(
            "Anthropic", "claude-opus-6", fast=True)
        self.assertIn("FAST mode", note)
        self.assertIn("ANTHROPIC_FAST_PRICING", note)

    def test_standard_behaviour_unchanged(self):
        self.assertIsNone(StreamingMixin._unpriced_model_warning(
            "Anthropic", "claude-opus-5-5"))
        self.assertIsNotNone(StreamingMixin._unpriced_model_warning(
            "Anthropic", "claude-unknown-tier"))


class ParamSummary(unittest.TestCase):
    def _summary_host(self, fast, model="claude-opus-5-5", unsupported=()):
        return stub(_DetectHost, provider="Anthropic", model=model,
                    fast_mode=fast, _anthropic_unsupported=set(unsupported),
                    thinking_mode="adaptive", temperature=1.0)

    def test_speed_fast_is_reported(self):
        self.assertEqual(
            UIMixin._get_model_param_summary(self._summary_host(True)),
            "mode=Adaptive speed=fast")

    def test_absent_when_off_or_incapable_or_learned_off(self):
        for host in (self._summary_host(False),
                     self._summary_host(True, model="claude-sonnet-5"),
                     self._summary_host(True, unsupported={"speed"})):
            with self.subTest(host=host.model):
                self.assertNotIn("speed", UIMixin._get_model_param_summary(host))


# --- wire-level: the real _stream_anthropic_call over a fake client ---------

class _FakeStream:
    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(())

    def get_final_message(self):
        return self._message


def _bad_request(message):
    e = anthropic.BadRequestError.__new__(anthropic.BadRequestError)
    e.message = message
    return e


def _rate_limit(message):
    e = anthropic.RateLimitError.__new__(anthropic.RateLimitError)
    e.message = message
    return e


def _final_message(usage):
    return SimpleNamespace(content=[], stop_reason="end_turn",
                           model="claude-opus-5-5", usage=usage,
                           input_transformations=None)


class _WireHost(AnthropicMixin, UIMixin):
    """The real _stream_anthropic_call over a fake client that captures each
    attempt's (betas, api_kwargs)."""

    def __init__(self, fast=True, usage=None, fail_first=None):
        self.provider = "Anthropic"
        self.model = "claude-opus-5-5"
        self.fast_mode = fast
        self.thinking_enabled = True
        self.thinking_mode = "adaptive"
        self.thinking_budget = 8192
        self.temperature = 1.0
        self.stop_requested = False
        self.queue = queue.Queue()
        self._anthropic_no_temperature = set()
        self._anthropic_unsupported = set()
        self.calls = []          # [(betas, api_kwargs-copy)] per attempt
        self._fail_first = fail_first
        usage = usage if usage is not None else SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
            cache_read_input_tokens=0, iterations=None)
        message = _final_message(usage)

        host = self

        class _Messages:
            @staticmethod
            def stream(betas, **api_kwargs):
                host.calls.append((list(betas), dict(api_kwargs)))
                if host._fail_first is not None:
                    exc, host._fail_first = host._fail_first, None
                    raise exc
                return _FakeStream(message)

        self.client = SimpleNamespace(beta=SimpleNamespace(messages=_Messages))

    def _get_tools(self):
        return []

    def _build_system_prompt(self):
        return ""

    def _tool_info(self, text):
        self.queue.put({"type": "tool_info", "content": text})


class WireSpeed(unittest.TestCase):
    def test_fast_sends_speed_and_beta(self):
        host = _WireHost(fast=True)
        stop, _blocks, _text, _think, _label, usage = host._stream_anthropic_call(
            [{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(stop, "end_turn")
        betas, kwargs = host.calls[0]
        self.assertIn(ANTHROPIC_FAST_MODE_BETA, betas)
        self.assertEqual(kwargs["speed"], "fast")
        # usage.speed absent from the snapshot → the requested speed (a
        # successful speed="fast" request WAS served fast).
        self.assertEqual(usage["speed"], "fast")

    def test_off_sends_neither(self):
        host = _WireHost(fast=False)
        _stop, _b, _t, _k, _l, usage = host._stream_anthropic_call(
            [{"role": "user", "content": "hi"}], 3, False)
        betas, kwargs = host.calls[0]
        self.assertNotIn(ANTHROPIC_FAST_MODE_BETA, betas)
        self.assertNotIn("speed", kwargs)
        self.assertIsNone(usage["speed"])

    def test_served_speed_from_usage_wins(self):
        served = SimpleNamespace(input_tokens=10, output_tokens=5,
                                 cache_creation_input_tokens=0,
                                 cache_read_input_tokens=0, iterations=None,
                                 speed="standard")
        host = _WireHost(fast=True, usage=served)
        _stop, _b, _t, _k, _l, usage = host._stream_anthropic_call(
            [{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(usage["speed"], "standard")

    def test_speed_400_learns_off_and_retries_standard(self):
        host = _WireHost(fast=True, fail_first=_bad_request(
            '"speed": "fast" is not supported for this model.'))
        _stop, _b, _t, _k, _l, usage = host._stream_anthropic_call(
            [{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(len(host.calls), 2)
        betas2, kwargs2 = host.calls[1]
        self.assertNotIn("speed", kwargs2)
        self.assertNotIn(ANTHROPIC_FAST_MODE_BETA, betas2)
        self.assertIn("speed", host._anthropic_unsupported)
        self.assertIsNone(usage["speed"])   # the retry was NOT served fast
        warnings = [m for m in _drain(host.queue) if m["type"] == "warning"]
        self.assertTrue(any("standard speed" in m["content"] for m in warnings))
        # ...and the shared gate now answers False for the rest of the session.
        self.assertFalse(host._anthropic_fast_active())

    def test_fast_limit_of_zero_429_learns_off_without_backoff(self):
        # An org outside the research preview has a fast rate limit of ZERO —
        # a 429 (probed live 2026-09-24), not a 400, which no backoff ever
        # unblocks. The call must drop to standard speed at once and the
        # session must stop asking.
        host = _WireHost(fast=True, fail_first=_rate_limit(
            "This request would exceed your rate limit of 0 fast mode input "
            "tokens per minute (org: x, model: claude-opus-5-5)."))
        _stop, _b, _t, _k, _l, usage = host._stream_anthropic_call(
            [{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(len(host.calls), 2)
        _betas2, kwargs2 = host.calls[1]
        self.assertNotIn("speed", kwargs2)
        self.assertIn("speed", host._anthropic_unsupported)
        self.assertIsNone(usage["speed"])
        warnings = [m for m in _drain(host.queue) if m["type"] == "warning"]
        self.assertTrue(any("not enabled" in m["content"] for m in warnings))

    def test_transient_fast_429_drops_this_call_only(self):
        host = _WireHost(fast=True, fail_first=_rate_limit(
            "This request would exceed your rate limit of 40000 fast mode "
            "input tokens per minute."))
        host._stream_anthropic_call([{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(len(host.calls), 2)
        _betas2, kwargs2 = host.calls[1]
        self.assertNotIn("speed", kwargs2)
        # NOT learned off: the next call tries fast again.
        self.assertNotIn("speed", host._anthropic_unsupported)
        self.assertTrue(host._anthropic_fast_active())

    def test_non_fast_429_still_backs_off(self):
        # The ordinary rate-limit path is untouched: no speed in flight means
        # the fast rung never fires and the backoff handles it.
        host = _WireHost(fast=False, fail_first=_rate_limit(
            "Number of request tokens has exceeded your per-minute rate limit."))
        with mock.patch("myagent.anthropic_mixin.time.sleep") as slept:
            host._stream_anthropic_call([{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(len(host.calls), 2)
        slept.assert_called_once()


class _FastServedHost(_CostLogHost):
    """_CostLogHost whose calls report being served fast on claude-opus-5-5."""

    def __init__(self):
        super().__init__(second_call="end")
        self.model = "claude-opus-5-5"

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        stop, blocks, text, think, label, usage = super()._stream_anthropic_call(
            messages, max_retries, label_emitted)
        usage["model"] = "claude-opus-5-5"
        usage["speed"] = "fast"
        return stop, blocks, text, think, label, usage


class StreamWorkerFastPricing(unittest.TestCase):
    def test_fast_served_calls_bill_at_fast_rates(self):
        host = _FastServedHost()
        with mock.patch.object(host, "_log_api_cost"):
            host.stream_worker([{"role": "user", "content": "go"}])
        costs = [m["call_cost"] for m in _drain(host.queue)
                 if m["type"] == "cost_update"]
        self.assertEqual(len(costs), 2)
        # 1000 in / 100 out at Opus 5.5's FAST $8/$40 (standard is $4/$20).
        for cost in costs:
            self.assertAlmostEqual(cost, 1000 * 8e-06 + 100 * 40e-06)


class PersistenceScan(unittest.TestCase):
    """The cheap guarantee that no save/restore path silently drops the key —
    the pattern the Physical toggle's scan established."""

    def _src(self, name):
        return (_REPO / name).read_text(encoding="utf-8")

    def test_instruction_entry_sites(self):
        src = self._src("myagent/instructions_mixin.py")
        # _save_instruction + manage_instructions create both write the live value
        self.assertEqual(src.count('"fast_mode": self.fast_mode,'), 2)
        # manage_instructions read reports it
        self.assertIn('"fast_mode": entry.get("fast_mode", False),', src)
        # manage_instructions update: in the updatable gate AND the copy loop
        self.assertEqual(src.count('"fast_mode", "blocked_tools")'), 2)

    def test_state_and_snapshot_sites(self):
        src = self._src("myagent/state_mixin.py")
        # top-level state AND the applied_instruction snapshot (getattr form:
        # bare test hosts drive _save_last_state without the attribute)
        self.assertEqual(
            src.count('"fast_mode": getattr(self, "fast_mode", False),'), 2)

    def test_restore_site(self):
        self.assertIn('entry.get("fast_mode", False)',
                      self._src("myagent/ui_mixin.py"))

    def test_manage_instructions_schema(self):
        self.assertIn('"fast_mode": {', self._src("myagent/constants.py"))


def _drain(q):
    items = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


if __name__ == "__main__":
    unittest.main()
