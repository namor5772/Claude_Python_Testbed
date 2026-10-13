"""The Anthropic caller retries a connection error (2026-10-13).

Its retry ladder caught a 429 (RateLimitError) and a 529 (APIStatusError)
and nothing else: an anthropic.APIConnectionError — no HTTP status at all,
the request never reached the API or the stream broke under it, raised once
the SDK's own two quick retries had failed — ended the run outright. The
chat JustChat_2026-10-13_172727 (claude-opus-5-5, 29 calls, $0.90) died on
call #30 with a bare "Connection error." and never got to tell the full-
screen viewer it had put up to quit. Now the error has a rung of its own:
wait on the connection schedule (5 s doubling, capped at 60 s), retry the
same request, up to the loop's attempt count; a break mid-stream restarts
the call like a 429 does.

Pinned here: the REAL _stream_anthropic_call over the capturing fake client
of tests/test_fast_mode.py — a connection error before the stream, a
timeout (a subclass), a break while the events are being read, the sleep
schedule, the last attempt raising, a 5xx status error still raising at once
(the rung must not broaden), and a source scan of the wiring.
"""

import inspect
import unittest
from types import SimpleNamespace
from unittest import mock

import anthropic

from myagent.anthropic_mixin import AnthropicMixin
from myagent.retry_util import connection_error_backoff
from tests.test_fast_mode import _FakeStream, _WireHost, _final_message
from tests.test_review_core import _drain

MSG = [{"role": "user", "content": "hi"}]


def _connection_error(message="Connection error.", cls=anthropic.APIConnectionError):
    e = cls.__new__(cls)
    e.message = message
    return e


def _server_error(status):
    e = anthropic.APIStatusError.__new__(anthropic.APIStatusError)
    e.message = f"HTTP {status}"
    e.status_code = status
    return e


def _infos(host):
    return [m["content"] for m in _drain(host.queue) if m["type"] == "tool_info"]


class _BreakingStream(_FakeStream):
    """A stream that opens, then raises the connection error as its events
    are read — the shape of a connection dropped mid-response."""

    def __init__(self, message, exc):
        super().__init__(message)
        self._exc = exc

    def __iter__(self):
        raise self._exc


def _scripted(host, outcomes):
    """Replace the host's client with one whose stream() takes the next entry
    of `outcomes` per attempt: an exception to raise, a stream to return, or
    None for a normal reply. The last entry repeats."""
    message = _final_message(SimpleNamespace(
        input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
        cache_read_input_tokens=0, iterations=None))
    script = list(outcomes)

    class _Messages:
        @staticmethod
        def stream(betas, **api_kwargs):
            host.calls.append((list(betas), dict(api_kwargs)))
            outcome = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome if outcome is not None else _FakeStream(message)

    host.client = SimpleNamespace(beta=SimpleNamespace(messages=_Messages))
    return message


class ConnectionRetryTests(unittest.TestCase):
    def _call(self, host, max_retries=3):
        with mock.patch("myagent.anthropic_mixin.time.sleep") as slept:
            result = host._stream_anthropic_call(MSG, max_retries, False)
        return result, [c.args[0] for c in slept.call_args_list]

    def test_a_connection_error_before_the_stream_is_retried(self):
        host = _WireHost(fast=False, fail_first=_connection_error())
        (stop, _b, _t, _k, _l, usage), waits = self._call(host)
        self.assertEqual(stop, "end_turn")
        self.assertIsNotNone(usage)
        self.assertEqual(len(host.calls), 2)
        self.assertEqual(waits, [5])
        # the same request again — nothing stripped, nothing learned off
        self.assertEqual(host.calls[0][1], host.calls[1][1])
        lines = [i for i in _infos(host) if "retrying" in i]
        self.assertEqual(len(lines), 1)
        self.assertIn("Connection error.", lines[0])
        self.assertIn("retrying in 5s (attempt 1/3)", lines[0])

    def test_a_timeout_rides_the_same_rung(self):
        host = _WireHost(fast=False, fail_first=_connection_error(
            "Request timed out.", anthropic.APITimeoutError))
        (stop, *_), waits = self._call(host)
        self.assertEqual(stop, "end_turn")
        self.assertEqual(len(host.calls), 2)
        self.assertEqual(waits, [5])
        self.assertTrue(any("Request timed out." in i for i in _infos(host)))

    def test_a_break_mid_stream_restarts_the_call(self):
        host = _WireHost(fast=False)
        message = _final_message(SimpleNamespace(
            input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
            cache_read_input_tokens=0, iterations=None))
        _scripted(host, [_BreakingStream(message, _connection_error("Connection error.")), None])
        (stop, _b, text, *_), waits = self._call(host)
        self.assertEqual(stop, "end_turn")
        self.assertEqual(len(host.calls), 2)
        self.assertEqual(waits, [5])
        self.assertEqual(text, "")               # the broken attempt's text is not kept

    def test_the_waits_follow_the_connection_schedule_and_the_last_attempt_raises(self):
        host = _WireHost(fast=False)
        _scripted(host, [_connection_error()])   # every attempt fails
        with self.assertRaises(anthropic.APIConnectionError):
            self._call(host, max_retries=4)
        self.assertEqual(len(host.calls), 4)
        with mock.patch("myagent.anthropic_mixin.time.sleep") as slept:
            host.calls.clear()
            with self.assertRaises(anthropic.APIConnectionError):
                host._stream_anthropic_call(MSG, 4, False)
        self.assertEqual([c.args[0] for c in slept.call_args_list],
                         [connection_error_backoff(a) for a in range(3)])
        self.assertEqual([connection_error_backoff(a) for a in range(8)],
                         [5, 10, 20, 40, 60, 60, 60, 60])

    def test_a_bare_error_without_text_is_still_announced(self):
        host = _WireHost(fast=False, fail_first=_connection_error(""))
        self._call(host)
        self.assertTrue(any(i.startswith("Connection error — retrying") for i in _infos(host)))

    def test_a_5xx_status_error_is_not_this_rung(self):
        # APIStatusError and APIConnectionError are siblings under APIError:
        # a 500 still raises at once, as before (only a 529 backs off).
        host = _WireHost(fast=False, fail_first=_server_error(500))
        with mock.patch("myagent.anthropic_mixin.time.sleep") as slept:
            with self.assertRaises(anthropic.APIStatusError):
                host._stream_anthropic_call(MSG, 3, False)
        self.assertEqual(len(host.calls), 1)
        slept.assert_not_called()

    def test_wiring(self):
        src = inspect.getsource(AnthropicMixin._stream_anthropic_call)
        self.assertIn("except anthropic.APIConnectionError as e:", src)
        self.assertIn("wait = connection_error_backoff(attempt)", src)
        # after the status rung, so a 529 keeps its own schedule
        self.assertLess(src.index("except anthropic.APIStatusError as e:"),
                        src.index("except anthropic.APIConnectionError as e:"))


if __name__ == "__main__":
    unittest.main()
