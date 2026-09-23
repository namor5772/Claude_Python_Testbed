"""Wiring tests for the cost log's INSTRUCTION / CALLS fields (2026-08-16):
stream_worker must hand _log_api_cost the instruction name it snapshotted at
run start and the final "Call #N" counter — on BOTH loop-end paths (normal
completion and the exception path), because call_num is hoisted above the
try exactly so a failed run still records how far it got.

Since 2026-09-14 the same applies to the four token accumulators (fields
9-12: input / output / cache-write / cache-read). They were already summed
for the output window's running totals but discarded at log time; they are
now hoisted beside total_cost for the same reason. Reading them in the
`except` while they were still declared inside the `try` makes an EARLY
failure (anything raising before the loop) abort the handler with
UnboundLocalError: the error message itself still reaches the queue, because
it is queued first, but _write_result_file and the headless auto-close after
it never run — so a waited parent sits out its whole timeout and an
unattended run is left a zombie. That is what
test_early_failure_completes_the_exception_handler pins; it fails if the
initialisers move back inside the try.

Since 2026-09-15 the accumulators run for EVERY call that reported usage,
not only priced ones: OLLAMA_PRICING is deliberately empty, so an Ollama run
is unpriced yet still logs a 0.0000 line (the 2026-08-12 exception) — and
that line read 0;0;0;0 while the model had reported real counts, because the
sums sat inside the pricing gate. _OllamaHost pins the fix; the paired
unpriced-paid-model test pins that the zero-cost gate itself is unchanged.

The provider call is stubbed at the dispatch seam (_stream_anthropic_call):
call 1 returns a tool_use block (so the loop goes round again), call 2 ends
the turn (or raises, for the exception-path case). Everything else the loop
touches is the thinnest stand-in that lets it run headless in a test."""

import queue
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import myagent.streaming_mixin as sm
from myagent.helpers import _ToolBlock


class _Var:
    def __init__(self, v):
        self._v = v

    def get(self):
        return self._v


class _Host(sm.StreamingMixin):
    """Minimal stream_worker host: Anthropic provider, two-call script."""

    def __init__(self, second_call="end", instruction="Balance Westpac"):
        self.provider = "Anthropic"
        self.model = "claude-sonnet-5"
        self.temperature = 1.0
        self._temp_var = _Var(1.0)
        self.queue = queue.Queue()
        self.thinking_enabled = False
        self.stop_requested = False
        self._headless = False
        self._result_file = None
        self.agent_instruction_name = instruction
        self._second_call = second_call
        self._calls_made = 0
        self.tool_calls = []

    # --- collaborators stream_worker reaches for ---
    def _weak_desktop_combo_warning(self):
        return None

    def _payload_for_display(self, messages):
        return ""

    def _tool_info(self, msg):
        pass

    def _get_model_param_summary(self):
        return "mode=Adaptive"

    def _execute_tool(self, block):
        self.tool_calls.append(block.name)
        return "ok"

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        self._calls_made += 1
        # Deliberately input/output only: tests/test_fable51_harness.py
        # subclasses this host and asserts exact per-call costs, so adding
        # cache buckets here would silently change ITS arithmetic. The cache
        # accumulators are exercised by _CacheHost below instead.
        usage = {"input_tokens": 1000, "output_tokens": 100}
        if self._calls_made == 1:
            block = _ToolBlock("read_file", "toolu_1", {"path": "x"})
            return "tool_use", [block], "", False, True, usage
        if self._second_call == "raise":
            raise RuntimeError("boom on call 2")
        return "end_turn", [], "done", False, True, usage


class _CacheHost(_Host):
    """_Host, but its usage also carries the two cache buckets — so all four
    accumulators are exercised. Separate from _Host because
    tests/test_fable51_harness.py subclasses that one and asserts exact
    per-call costs, which cache tokens would change."""

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        stop, blocks, text, thinking, label, usage = super()._stream_anthropic_call(
            messages, max_retries, label_emitted)
        # input_tokens stays the NON-cached count: the buckets are disjoint,
        # which is what makes in+write+read the run's billed input volume.
        usage["cache_creation_input_tokens"] = 40
        usage["cache_read_input_tokens"] = 7
        return stop, blocks, text, thinking, label, usage


class _OllamaHost(_Host):
    """_Host on the Ollama provider: usage is reported (prompt_eval_count /
    eval_count) but OLLAMA_PRICING is empty, so _get_pricing returns None and
    the run is unpriced — the one provider that logs at zero cost."""

    def __init__(self, second_call="end", instruction="Local run"):
        super().__init__(second_call, instruction)
        self.provider = "Ollama"
        self.model = "qwen3:32b"

    def _stream_ollama_call(self, messages, max_retries, label_emitted):
        return self._stream_anthropic_call(messages, max_retries, label_emitted)


class CostLogRunFieldsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = Path(tmp.name) / "APICostLog_test.txt"
        patcher = mock.patch.object(sm, "APICOST_LOG_FILE", str(self.log))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fields(self):
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        return lines[0].split(";")

    def test_completed_run_logs_instruction_and_call_count(self):
        host = _Host(second_call="end")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(len(f), 12)
        self.assertEqual(f[6], "Balance Westpac")
        # Two API round-trips: the tool_use turn plus the final answer.
        self.assertEqual(f[7], "2")
        self.assertEqual(host.tool_calls, ["read_file"])

    def test_failed_run_still_logs_calls_reached(self):
        # The exception path logs whatever the run got through: the failing
        # call was Call #2 (what the output window showed when it died).
        host = _Host(second_call="raise")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(f[6], "Balance Westpac")
        self.assertEqual(f[7], "2")

    def test_instruction_snapshotted_at_run_start(self):
        # An instruction applied while the run is in flight must not relabel
        # the entry: the name is captured before the loop starts.
        host = _Host(second_call="end")
        real_call = host._stream_anthropic_call

        def relabel_then_call(*a, **k):
            host.agent_instruction_name = "Something Else"
            return real_call(*a, **k)

        host._stream_anthropic_call = relabel_then_call
        host.stream_worker([{"role": "user", "content": "go"}])
        self.assertEqual(self._fields()[6], "Balance Westpac")

    def test_ad_hoc_run_logs_blank_instruction(self):
        host = _Host(second_call="end", instruction="")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(f[6], "")
        self.assertEqual(f[7], "2")

    def test_completed_run_logs_summed_token_buckets(self):
        # Two API round-trips, each reporting the same usage -> doubled sums.
        host = _Host(second_call="end")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(len(f), 12)
        self.assertEqual(f[8:], ["2000", "200", "0", "0"])

    def test_failed_run_still_logs_tokens_accumulated_so_far(self):
        # The accumulators are hoisted above the try precisely for this: the
        # run died on call 2, so only call 1's usage was ever counted, and
        # the except path must still be able to READ them.
        host = _Host(second_call="raise")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(len(f), 12)
        self.assertEqual(f[8:], ["1000", "100", "0", "0"])
        # The real error surfaced rather than a NameError from the handler.
        errs = [m for m in self._drain_queue(host) if m.get("type") == "error"]
        self.assertTrue(any("boom on call 2" in str(m.get("content"))
                            for m in errs), errs)

    @staticmethod
    def _drain_queue(host):
        out = []
        while not host.queue.empty():
            out.append(host.queue.get_nowait())
        return out

    def test_early_failure_completes_the_exception_handler(self):
        # Regression pin for the hoisting of the four token accumulators.
        # A collaborator that raises BEFORE the agentic loop used to leave
        # them unbound; the except path reads them to build the TOKENS
        # fields, so it died with UnboundLocalError *inside the handler*.
        # The error message still reached the queue (it is queued first), but
        # _write_result_file never ran -- so a waited parent sat out its full
        # timeout and a headless run was left a zombie. What this asserts is
        # therefore the handler COMPLETING, not just the message appearing.
        host = _Host(second_call="end")
        host._result_file = "sentinel"
        written = []
        host._write_result_file = lambda *a, **k: written.append(a)
        # A result file means this run is a waited subagent, so the handler's
        # tail schedules the auto-close — record it instead of needing Tk.
        closes = []
        host.root = type("_Root", (), {
            "after": lambda _self, ms, fn: closes.append(ms)})()
        host._on_close = lambda: None

        def boom():
            raise RuntimeError("EARLY BOOM")

        host._weak_desktop_combo_warning = boom
        # Must not propagate: stream_worker owns its exception path.
        host.stream_worker([{"role": "user", "content": "go"}])

        errs = [str(m.get("content")) for m in self._drain_queue(host)
                if m.get("type") == "error"]
        self.assertTrue(any("EARLY BOOM" in e for e in errs), errs)
        self.assertFalse(any("not associated" in e or "not defined" in e
                             for e in errs), errs)
        # The handler ran to completion.
        self.assertTrue(written, "exception path never wrote a result file")
        self.assertEqual(written[0][0], "error")
        self.assertEqual(closes, [500], "auto-close never scheduled")
        # Nothing was logged: no call completed, so the zero-cost gate holds.
        self.assertFalse(self.log.exists())

    def test_ollama_run_logs_its_token_counts_at_zero_cost(self):
        # Unpriced (empty table) but logged (Ollama exception) — and since
        # 2026-09-15 with the REAL counts: two calls x (1000 in, 100 out).
        # Before the hoist the line said 0;0;0;0, which the viewers render
        # as "no tokens billed" — false; 2,200 had been reported.
        host = _OllamaHost(second_call="end")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(len(f), 12)
        self.assertEqual((f[1], f[2], f[3]), ("Ollama", "qwen3:32b", "0.0000"))
        self.assertEqual(f[7], "2")
        self.assertEqual(f[8:], ["2000", "200", "0", "0"])

    def test_unpriced_paid_model_still_logs_nothing(self):
        # The zero-cost gate is untouched by the hoist: a PAID provider whose
        # model has no pricing row now accumulates tokens too, but still
        # writes no line — a $0.0000 row would claim the run was free when
        # the truth is the price is unknown.
        host = _Host(second_call="end")
        host.model = "claude-unknown-99"
        host.stream_worker([{"role": "user", "content": "go"}])
        self.assertFalse(self.log.exists())
        self.assertEqual(host.tool_calls, ["read_file"])  # the run itself ran

    def test_unpriced_paid_model_is_loud_and_shows_its_tokens(self):
        # 2026-09-23: a gpt-6-sol run (no OPENAI_PRICING row yet) showed no
        # cost line and reached no log, in silence. Now the run says so at
        # start and end, and every call's tokens still reach the window as
        # a cost_update with call_cost None.
        host = _Host(second_call="end")
        host.model = "claude-unknown-99"
        host.stream_worker([{"role": "user", "content": "go"}])
        msgs = []
        while not host.queue.empty():
            msgs.append(host.queue.get_nowait())
        warnings = [m["content"] for m in msgs if m["type"] == "warning"]
        self.assertEqual(len(warnings), 2)
        self.assertIn("claude-unknown-99 has no row in the Anthropic pricing table", warnings[0])
        self.assertTrue(warnings[1].startswith("⚠ Run NOT written to the API cost log"))
        costs = [m for m in msgs if m["type"] == "cost_update"]
        self.assertEqual(len(costs), 2)                      # one per call
        self.assertEqual([m["call_cost"] for m in costs], [None, None])
        self.assertEqual(costs[1]["input_tokens"], 1000)
        self.assertEqual(costs[1]["total_input_tokens"], 2000)
        self.assertFalse(self.log.exists())

    def test_priced_run_gets_no_such_warning(self):
        host = _Host(second_call="end")
        host.stream_worker([{"role": "user", "content": "go"}])
        msgs = []
        while not host.queue.empty():
            msgs.append(host.queue.get_nowait())
        self.assertEqual([m for m in msgs if m["type"] == "warning"], [])
        costs = [m for m in msgs if m["type"] == "cost_update"]
        self.assertEqual(len(costs), 2)
        self.assertTrue(all(m["call_cost"] > 0 for m in costs))


    def test_cache_buckets_accumulate_into_their_own_fields(self):
        # Two round-trips x (40 written, 7 read) -> 80 / 14, kept in the two
        # trailing fields rather than folded into TOK-IN.
        host = _CacheHost(second_call="end")
        host.stream_worker([{"role": "user", "content": "go"}])
        f = self._fields()
        self.assertEqual(len(f), 12)
        self.assertEqual(f[8:], ["2000", "200", "80", "14"])


class CostLineTests(unittest.TestCase):
    """event_loop_mixin._cost_line: the blue per-call line, pure."""

    @staticmethod
    def _msg(call_cost, total_cost, cw=0, cr=0):
        return {"call_cost": call_cost, "total_cost": total_cost, "input_tokens": 12345,
                "output_tokens": 678, "cache_write_tokens": cw, "cache_read_tokens": cr}

    def test_priced_lines(self):
        from myagent.event_loop_mixin import EventLoopMixin
        self.assertEqual(EventLoopMixin._cost_line(self._msg(0.0012, 0.0034)),
                         "  $0.0012 this call  |  $0.0034 total  (in:12,345  out:678)\n")
        self.assertEqual(EventLoopMixin._cost_line(self._msg(0.0012, 1.2345, cw=40, cr=7)),
                         "  $0.0012 this call  |  $1.23 total  (in:12,345  out:678  cache_write:40  cache_read:7)\n")

    def test_unpriced_line_shows_the_tokens(self):
        from myagent.event_loop_mixin import EventLoopMixin
        self.assertEqual(EventLoopMixin._cost_line(self._msg(None, None, cr=7)),
                         "  unpriced this call  |  no pricing row  (in:12,345  out:678  cache_read:7)\n")


if __name__ == "__main__":
    unittest.main()
