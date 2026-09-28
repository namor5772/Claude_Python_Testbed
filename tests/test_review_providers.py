"""Regression tests for the 2026-09-28 review fixes in the provider callers.

- the "Waiting for model response" ticker thread stops on EVERY exit of the
  OpenAI / xAI / Kimi callers — a raise used to leave it posting to the pane;
- each attempt's 180 s first-content timeout starts with the attempt: six
  429 backoffs used to trip it on the seventh, healthy call;
- the OpenAI 400 rungs retry through the loop, so a second, different error
  on the retry reaches its own handler instead of ending the run;
- Gemini: only a thinking-related 400 steps the Thinking-off quiet ladder, and
  a 400 whose text merely contains "500" is not retried as a server error;
- kimi-k3: the title / cost-log summary names the effort actually sent, and a
  400 saying reasoning_content is MISSING does not teach the session to stop
  sending it;
- MCP: a tool call is STOP-aware and cancelled when given up;
- the Debug dump shows the thinking disable Opus 5 is sent, and leaves out the
  server tools a model rejected earlier in the session.

No network: fake clients only (after the reviewers' probe scripts).
"""

import asyncio
import queue
import threading
import time
import types
import unittest
from unittest import mock

import httpx
import openai

import myagent.gemini_mixin as gm
import myagent.mcp_mixin as mm
import myagent.openai_mixin as om
from myagent.gemini_mixin import GeminiMixin
from myagent.kimi_mixin import KimiMixin
from myagent.mcp_mixin import MCPMixin
from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin
from myagent.ui_mixin import UIMixin
from myagent.xai_mixin import XAIMixin
from tests.test_fast_mode import _WireHost
from tests.test_gpt6_params import _ReqHost

REQ = httpx.Request("POST", "https://api.example/v1/responses")


class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


def _not_found():
    return openai.NotFoundError("model not found",
                                response=httpx.Response(404, request=REQ), body=None)


def _bad_request(message):
    return openai.BadRequestError(message, response=httpx.Response(400, request=REQ),
                                  body=None)


def _rate_limited():
    return openai.RateLimitError("rate limited",
                                 response=httpx.Response(429, request=REQ), body=None)


class _RaisingResponses:
    def stream(self, **kw):
        raise _not_found()

    def create(self, **kw):
        raise _not_found()


class _Host(OpenAIMixin, XAIMixin, UIMixin, StreamingMixin):
    pass


def _host(provider, model, client):
    h = object.__new__(_Host)
    h.queue = queue.Queue()
    h.provider, h.model = provider, model
    h.thinking_enabled, h.thinking_effort, h.thinking_mode = True, "medium", "medium"
    h.thinking_budget, h.temperature, h.text_verbosity = 8192, 1.0, "medium"
    h.desktop_enabled = _Var(False)
    h.stop_requested = False
    h._openai_unsupported_tools = {}
    h.openai_client = h.xai_client = client
    h._build_system_prompt = lambda: "sys"
    h._get_tools = lambda: []
    return h


class _KimiHost(KimiMixin, UIMixin):
    def __init__(self, error=None, model="kimi-k2.6"):
        self.provider, self.model = "Moonshot", model
        self.thinking_enabled, self.thinking_effort, self.thinking_mode = True, "high", "high"
        self.temperature, self.text_verbosity = 1.0, "medium"
        self.stop_requested = False
        self.queue = queue.Queue()
        self.requests = 0
        host = self

        class _Completions:
            @staticmethod
            def create(**kw):
                host.requests += 1
                raise error

        self.kimi_client = types.SimpleNamespace(
            chat=types.SimpleNamespace(completions=_Completions()))

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return []


class TickerStopsOnEveryExit(unittest.TestCase):

    def test_openai(self):
        h = _host("OpenAI", "gpt-5.6-terra", types.SimpleNamespace(responses=_RaisingResponses()))
        with self.assertRaises(openai.NotFoundError):
            h._stream_responses_call([{"role": "user", "content": "hi"}], 10, False)
        self.assertTrue(h._oai_first_content.is_set())

    def test_xai(self):
        h = _host("xAI", "grok-4.7", types.SimpleNamespace(responses=_RaisingResponses()))
        with self.assertRaises(openai.NotFoundError):
            h._stream_xai_call([{"role": "user", "content": "hi"}], 10, False)
        self.assertTrue(h._xai_first_content.is_set())

    def test_kimi(self):
        h = _KimiHost(error=_bad_request("Error code: 400 - invalid request"))
        with self.assertRaises(openai.BadRequestError):
            h._stream_kimi_call([{"role": "user", "content": "hi"}], 3, False)
        self.assertTrue(h._kimi_first_content.is_set())


class _FakeTime:
    def __init__(self):
        self.now = 1_000_000.0

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class _Stream:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        yield types.SimpleNamespace(type="response.created")
        yield types.SimpleNamespace(type="response.output_text.delta", delta="ok")

    def get_final_response(self):
        raise RuntimeError("no usage in the fake")


class _FlakyResponses:
    def __init__(self, failures):
        self.failures = list(failures)
        self.calls = 0

    def stream(self, **kw):
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return _Stream()


class OpenAIRetryLoop(unittest.TestCase):

    def test_backoff_sleeps_do_not_count_against_the_first_content_timeout(self):
        flaky = _FlakyResponses([_rate_limited() for _ in range(6)])
        h = _host("OpenAI", "gpt-5.6-terra", types.SimpleNamespace(responses=flaky))
        with mock.patch.object(om, "time", _FakeTime()):
            result = h._stream_responses_call([{"role": "user", "content": "hi"}], 10, False)
        self.assertEqual(flaky.calls, 7)            # 6 x 429, then the answer — no 8th
        self.assertEqual(result[2], "ok")
        notices = [m.get("content", "") for m in list(h.queue.queue)]
        self.assertFalse(any("Stream timeout" in n for n in notices), notices)

    def test_a_second_error_on_a_rung_retry_reaches_its_own_handler(self):
        # A 400 on temperature (a tier the tables don't know), then a 429 on
        # the retry: the 429 is backed off and retried — it used to escape the
        # except clause the retry was issued from and end the run.
        flaky = _FlakyResponses([_bad_request("Unsupported parameter: 'temperature'"),
                                 _rate_limited()])
        h = _host("OpenAI", "gpt-5.6-terra", types.SimpleNamespace(responses=flaky))
        h.thinking_effort = h.thinking_mode = "none"          # temperature is sent at none
        h.thinking_enabled = False
        with mock.patch.object(om, "rate_limit_backoff", lambda attempt: 0):
            result = h._stream_responses_call([{"role": "user", "content": "hi"}], 10, False)
        self.assertEqual(flaky.calls, 3)
        self.assertEqual(result[2], "ok")

    def test_a_ladder_that_exhausts_every_attempt_raises_the_last_400(self):
        class _AlwaysEffort400:
            calls = 0

            def stream(self, **kw):
                _AlwaysEffort400.calls += 1
                raise _bad_request("Unsupported value: 'reasoning.effort'. "
                                   "Supported values are: 'low', 'medium'.")

        h = _host("OpenAI", "gpt-5.6-terra", types.SimpleNamespace(responses=_AlwaysEffort400()))
        with self.assertRaises(openai.BadRequestError):
            h._stream_responses_call([{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(_AlwaysEffort400.calls, 3)


class _Bad400(Exception):
    code = 400


def _style_of(config):
    tc = getattr(config, "thinking_config", None)
    if tc is None:
        return "none"
    if tc.thinking_budget == 0:
        return "budget0"
    return str(tc.thinking_level).split(".")[-1].lower()


class _GeminiHost(GeminiMixin, UIMixin, StreamingMixin):
    def __init__(self, message):
        self.provider, self.model = "Google", "gemini-3.6-flash"   # seeded "minimal"
        self.thinking_enabled, self.thinking_effort = False, "high"
        self.temperature, self.stop_requested = 0.4, False
        self.queue = queue.Queue()
        self.sent = []
        host = self

        class _Models:
            @staticmethod
            def generate_content_stream(model, contents, config):
                host.sent.append(_style_of(config))
                raise _Bad400(message)

        self.gemini_client = types.SimpleNamespace(models=_Models())

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return []


OVERFLOW = ("400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': 'The input token "
            "count (1050034) exceeds the maximum number of tokens allowed (1048576).', "
            "'status': 'INVALID_ARGUMENT'}}")


class GeminiErrorClassification(unittest.TestCase):

    def run_call(self, host):
        sleeps = []
        with mock.patch.object(gm.time, "sleep", sleeps.append):
            with self.assertRaises(_Bad400):
                host._stream_gemini_call([{"role": "user", "content": "hi"}], 10, False)
        return sleeps

    def test_an_unrelated_400_leaves_the_quiet_ladder_alone(self):
        h = _GeminiHost(OVERFLOW)
        sleeps = self.run_call(h)
        self.assertEqual(h.sent, ["minimal"])        # one attempt, no rung stepped
        # The session remembers the seeded rung, never one walked down to.
        self.assertEqual(h._gemini_quiet_styles.get("gemini-3.6-flash", "minimal"), "minimal")
        # ...and "1050034" contains "500": it is still a 400, not a server error.
        self.assertEqual(sleeps, [])

    def test_a_thinking_400_still_steps_the_ladder(self):
        h = _GeminiHost("400 INVALID_ARGUMENT: thinking_level MINIMAL is not supported "
                        "for this model.")
        self.run_call(h)
        self.assertEqual(h.sent[:2], ["minimal", "budget0"])


class KimiEffort(unittest.TestCase):

    def test_the_summary_names_the_effort_actually_sent(self):
        for mode, effort, enabled, sent in (("medium", "medium", True, "high"),
                                            ("adaptive", "high", True, "high"),
                                            ("off", "off", False, "none"),
                                            ("xhigh", "xhigh", True, "max")):
            with self.subTest(mode=mode):
                h = _KimiHost(model="kimi-k3")
                h.thinking_mode, h.thinking_effort, h.thinking_enabled = mode, effort, enabled
                self.assertEqual(h._kimi_model_params().get("reasoning_effort"), sent)
                self.assertIn(f"reasoning={sent.capitalize()}", h._get_model_param_summary())

    def test_a_missing_reasoning_content_400_is_not_learned_as_a_rejection(self):
        h = _KimiHost(model="kimi-k3", error=_bad_request(
            "Error code: 400 - reasoning_content is missing in assistant tool call message"))
        with self.assertRaises(openai.BadRequestError):
            h._stream_kimi_call([{"role": "user", "content": "hi"}], 3, False)
        self.assertEqual(h.requests, 1)
        self.assertNotIn("kimi-k3", getattr(h, "_kimi_reasoning_rejected", set()))

    def test_a_refused_round_trip_is_still_learned(self):
        h = _KimiHost(model="kimi-k3", error=_bad_request(
            "Error code: 400 - reasoning_content is not allowed for this model"))
        with self.assertRaises(openai.BadRequestError):
            h._stream_kimi_call([{"role": "user", "content": "hi"}], 3, False)
        self.assertIn("kimi-k3", h._kimi_reasoning_rejected)


class _MCPHost(MCPMixin):
    def __init__(self, loop, session):
        self._mcp_loop = loop
        self._mcp_tools_by_name = {"srv__slow": ("srv", "slow")}
        self._mcp_sessions = {"srv": session}
        self.stop_requested = False


class _SlowSession:
    def __init__(self):
        self.cancelled = threading.Event()

    async def call_tool(self, name, arguments):
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _FailingSession:
    async def call_tool(self, name, arguments):
        raise TimeoutError("the server gave up")


class MCPCall(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (self.loop.call_soon_threadsafe(self.loop.stop),
                                 thread.join(5), self.loop.close()))
        patcher = mock.patch.object(mm, "_HAS_MCP", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_stop_ends_the_wait_and_cancels_the_call(self):
        session = _SlowSession()
        host = _MCPHost(self.loop, session)
        threading.Timer(0.3, lambda: setattr(host, "stop_requested", True)).start()
        started = time.monotonic()
        result = host.do_mcp_call("srv__slow", {})
        self.assertLess(time.monotonic() - started, 5)
        self.assertIn("STOP", result)
        self.assertTrue(session.cancelled.wait(5))

    def test_a_timeout_cancels_and_says_so(self):
        session = _SlowSession()
        host = _MCPHost(self.loop, session)
        with mock.patch.object(_MCPHost, "MCP_CALL_TIMEOUT", 0.5):
            result = host.do_mcp_call("srv__slow", {})
        self.assertIn("timed out after 0.5 s", result)
        self.assertTrue(session.cancelled.wait(5))

    def test_the_calls_own_failure_is_reported_with_its_type(self):
        result = _MCPHost(self.loop, _FailingSession()).do_mcp_call("srv__slow", {})
        self.assertIn("TimeoutError('the server gave up')", result)


class _AnthropicDumpHost(StreamingMixin, _WireHost):
    def _get_tools(self):          # StreamingMixin's real one would win the MRO
        return []


class DebugDump(unittest.TestCase):

    def test_opus_5_off_shows_the_thinking_disable_the_wire_carries(self):
        h = _AnthropicDumpHost(fast=False)
        h.model = "claude-opus-5"
        h.thinking_enabled, h.thinking_mode, h.thinking_effort = False, "off", "off"
        h.desktop_enabled = _Var(False)
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertIn('"thinking": {\n    "type": "disabled"', text.replace("\r", ""))
        h._stream_anthropic_call([{"role": "user", "content": "hi"}], 1, True)
        self.assertEqual(h.calls[-1][1]["thinking"], {"type": "disabled"})

    def test_openai_dump_leaves_out_a_rejected_server_tool(self):
        h = _ReqHost(model="gpt-5.6-terra")
        h._openai_unsupported_tools = {"gpt-5.6-terra": {"code_interpreter"}}
        text = h._payload_for_display([{"role": "user", "content": "hi"}])
        self.assertNotIn('"type": "code_interpreter"', text)   # ("include" still names its outputs)
        self.assertIn('"type": "web_search_preview"', text)
        h.call()
        self.assertNotIn("code_interpreter", [t["type"] for t in h.sent[-1]["tools"]])


if __name__ == "__main__":
    unittest.main()
