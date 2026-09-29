"""The xAI (Grok) reasoning round trip (2026-09-30) — the OpenAI fix
(tests/test_openai_replay.py) carried to the other Responses-API provider.

xAI is called with store=False too, so between two calls of a tool loop its
server remembers nothing, and until this fix _stream_xai_events kept only the
text and the function calls of each response. Probed live the same day:
every served reasoning Grok accepts include=["reasoning.encrypted_content"]
and returns reasoning items with ciphertext; the verbatim replay is consumed
(input tokens up, re-reasoning output down 2-6x); grok-4.3's items are
consumed by 4.7 / 4.6 / build-0.1 / the pinned 4.20 reasoning variant (the
Model upgrade); web_search_call, the x_search custom_tool_call and
code_interpreter_call items replay; a corrupted ciphertext is a 400 whose
body — {"code": "invalid-argument", "error": "Could not decrypt the provided
encrypted_content. …"} — gives the SDK no code or param.

Pinned here, against the REAL code with only xai_client.responses.create
faked (raw events, as MyAgent iterates them; the result read from the
response.completed event, as it does):

  * capture   — the completed response's items become a responses_items
                block, x_search's custom_tool_call included; nothing when the
                stream never completed;
  * replay    — through the real stream_worker, the second request carries
                the first call's reasoning and server-tool items, in order;
  * include   — every request asks for the ciphertext; not once replay is
                off for xAI; not affected by OpenAI's flag;
  * the rung  — FIRST in xAI's 400 ladder: an xAI-worded refusal that also
                says "reasoning" switches replay off (warning once, legacy
                input, include dropped) and leaves the reasoning param alone,
                where the loose "reasoning" rung would have stripped it; an
                unrelated 400 still walks the old ladder with replay on;
  * display   — the Debug dump mirrors the include.
"""

import copy
import queue
import unittest
from types import SimpleNamespace

import httpx
import openai
from openai.types.responses import (ResponseCustomToolCall, ResponseFunctionToolCall,
                                    ResponseFunctionWebSearch, ResponseOutputMessage,
                                    ResponseOutputText, ResponseReasoningItem)
from openai.types.responses.response_function_web_search import ActionSearch
from openai.types.responses.response_reasoning_item import Summary

from myagent.streaming_mixin import StreamingMixin
from myagent.xai_mixin import XAIMixin

TOOL = {"name": "read_file", "description": "d",
        "input_schema": {"type": "object", "properties": {}}}
GO = {"role": "user", "content": "go"}
OUT1 = {"type": "function_call_output", "call_id": "call-1", "output": "ok"}
LEGACY_FC1 = {"type": "function_call", "call_id": "call-1", "name": "read_file",
              "arguments": '{"path": "x"}'}
INCLUDE = ["reasoning.encrypted_content"]
XAI_DECRYPT_ERROR = ("Could not decrypt the provided encrypted_content for the reasoning item. "
                     "Ensure the value is the unmodified encrypted_content from a previous "
                     "response.")


def reasoning(rid, enc):
    return ResponseReasoningItem(id=rid, type="reasoning", encrypted_content=enc,
                                 summary=[Summary(text="plan", type="summary_text")])


def function_call(fid, call_id):
    return ResponseFunctionToolCall(id=fid, type="function_call", call_id=call_id,
                                    name="read_file", arguments='{"path": "x"}',
                                    status="completed")


def web_search(wid):
    return ResponseFunctionWebSearch(id=wid, type="web_search_call", status="completed",
                                     action=ActionSearch(type="search", query="abc news"))


def x_search(cid):
    return ResponseCustomToolCall(id=cid, type="custom_tool_call", call_id="xs_" + cid,
                                  name="x_keyword_search", input='{"query": "abc"}')


def message(mid, text):
    return ResponseOutputMessage(id=mid, type="message", role="assistant", status="completed",
                                 content=[ResponseOutputText(type="output_text", text=text,
                                                             annotations=[])])


def dumped(item):
    return item.model_dump(mode="json", exclude_none=True)


def ev(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def completed(*items):
    return ev("response.completed",
              response=SimpleNamespace(status="completed", output=list(items), usage=None))


def call_events(idx, call_id):
    return [ev("response.output_item.added", output_index=idx,
               item=SimpleNamespace(type="function_call", call_id=call_id, name="read_file")),
            ev("response.function_call_arguments.done", output_index=idx,
               arguments='{"path": "x"}')]


def xai_bad_request(error_text):
    """What the SDK raises for an xAI 400: its body is {"code", "error"} with
    `error` a string, so the exception carries no code / param."""
    req = httpx.Request("POST", "https://api.x.ai/v1/responses")
    body = {"code": "invalid-argument", "error": error_text}
    return openai.BadRequestError(f"Error code: 400 - {body}",
                                  response=httpx.Response(400, request=req), body=error_text)


class _FakeStream:
    def __init__(self, events):
        self._events = events

    def __iter__(self):
        return iter(self._events)

    def close(self):
        pass


class _FakeResponses:
    """`responses.create(stream=True, **kw)` scripted per call: a list of
    events, or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def create(self, stream=False, **kw):
        self.sent.append(copy.deepcopy(kw))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _FakeStream(step)


class _Var:
    def __init__(self, v):
        self._v = v

    def get(self):
        return self._v


class _Host(StreamingMixin, XAIMixin):
    """The real stream_worker / _stream_xai_call / _stream_xai_events /
    _messages_to_responses on the xAI path; only the SDK is fake."""

    def __init__(self, script, model="grok-4.3"):
        self.provider = "xAI"
        self.model = model
        self.thinking_enabled = True
        self.thinking_effort = "low"
        self.thinking_mode = "low"
        self.thinking_budget = 16384
        self.temperature = 0.3
        self._temp_var = _Var(0.3)
        self.desktop_enabled = _Var(False)
        self._xai_caps = {}
        self._responses_replay_off = set()
        self.queue = queue.Queue()
        self.stop_requested = False
        self._headless = False
        self._result_file = None
        self.agent_instruction_name = "JustChat"
        self.xai_client = SimpleNamespace(responses=_FakeResponses(script))
        self.tool_calls = []

    @property
    def sent(self):
        return self.xai_client.responses.sent

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return [dict(TOOL)]

    def _weak_desktop_combo_warning(self):
        return None

    def _payload_for_display(self, messages):
        return ""

    def _get_model_param_summary(self):
        return "reasoning=Low"

    def _execute_tool(self, block):
        self.tool_calls.append(block.name)
        return "ok"

    def _log_api_cost(self, *args, **kwargs):
        pass

    def drain_all(self):
        out = []
        while True:
            try:
                out.append(self.queue.get_nowait())
            except queue.Empty:
                return out


def two_call_script(first_items):
    return [call_events(1, "call-1") + [completed(*first_items)],
            [ev("response.output_text.delta", delta="done"),
             completed(reasoning("rs_2", "ENC2"), message("msg_2", "done"))]]


class CaptureTests(unittest.TestCase):

    def test_completed_items_become_one_block_x_search_included(self):
        items = [reasoning("rs_1", "ENC1"), web_search("ws_1"), x_search("ctc_1"),
                 function_call("fc_1", "call-1")]
        h = _Host([call_events(3, "call-1") + [completed(*items)]])
        _t, stop, blocks, *_ = h._stream_xai_events({"model": h.model, "input": [GO]}, True)
        self.assertEqual(stop, "tool_use")
        self.assertEqual([b["type"] for b in blocks], ["tool_use", "responses_items"])
        self.assertEqual(blocks[1]["items"], [dumped(i) for i in items])

    def test_a_stream_that_never_completed_keeps_no_items(self):
        # STOP / a dropped connection: no response.completed event arrives
        h = _Host([call_events(1, "call-1")])
        _t, _s, blocks, *_ = h._stream_xai_events({"model": h.model, "input": [GO]}, True)
        self.assertEqual([b["type"] for b in blocks], ["tool_use"])


class ReplayThroughStreamWorkerTests(unittest.TestCase):

    def run_worker(self, host):
        host.stream_worker([dict(GO)])
        self.assertEqual([m for m in host.drain_all() if m.get("type") == "error"], [])
        return host.sent

    def test_second_call_carries_reasoning_and_server_tool_items(self):
        items = [reasoning("rs_1", "ENC1"), web_search("ws_1"), x_search("ctc_1"),
                 function_call("fc_1", "call-1")]
        h = _Host(two_call_script(items))
        sent = self.run_worker(h)
        self.assertEqual(len(sent), 2)
        self.assertEqual(h.tool_calls, ["read_file"])
        self.assertEqual(sent[1]["input"], [GO] + [dumped(i) for i in items] + [OUT1])
        self.assertNotIn(LEGACY_FC1, sent[1]["input"])

    def test_every_request_asks_for_the_ciphertext(self):
        h = _Host(two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call-1")]))
        for kw in self.run_worker(h):
            self.assertEqual(kw["include"], INCLUDE)
            self.assertIs(kw["store"], False)
            self.assertEqual(kw["reasoning"], {"effort": "low", "summary": "auto"})

    def test_replay_off_for_xai_sends_the_legacy_shape_and_no_include(self):
        h = _Host(two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call-1")]))
        h._responses_replay_off = {"xAI"}
        sent = self.run_worker(h)
        self.assertEqual(sent[1]["input"], [GO, LEGACY_FC1, OUT1])
        for kw in sent:
            self.assertNotIn("include", kw)

    def test_openais_refusal_leaves_xai_replaying(self):
        h = _Host(two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call-1")]))
        h._responses_replay_off = {"OpenAI"}
        sent = self.run_worker(h)
        self.assertIn(dumped(reasoning("rs_1", "ENC1")), sent[1]["input"])
        self.assertEqual(sent[1]["include"], INCLUDE)


class RefusalRungTests(unittest.TestCase):

    def test_refusal_is_caught_first_and_the_reasoning_param_survives(self):
        # The error text says "reasoning": in the old ladder order the loose
        # reasoning rung would have stripped the summary instead.
        script = two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call-1")])
        script.insert(1, xai_bad_request(XAI_DECRYPT_ERROR))
        h = _Host(script)
        h.stream_worker([dict(GO)])
        queued = h.drain_all()
        self.assertEqual([m for m in queued if m.get("type") == "error"], [])
        self.assertEqual(len(h.sent), 3)                 # call 1, refused, retry
        self.assertIn(dumped(reasoning("rs_1", "ENC1")), h.sent[1]["input"])
        retry = h.sent[2]
        self.assertEqual(retry["input"], [GO, LEGACY_FC1, OUT1])
        self.assertNotIn("include", retry)               # nothing left to ask for
        self.assertEqual(retry["reasoning"], {"effort": "low", "summary": "auto"})
        self.assertEqual([t["type"] for t in retry["tools"]],
                         ["function", "web_search", "x_search", "code_interpreter"])
        self.assertEqual(h._responses_replay_off, {"xAI"})
        self.assertEqual(len([m for m in queued if m.get("type") == "warning"
                              and "xAI refused" in m.get("content", "")]), 1)

    def test_unrelated_400_walks_the_old_ladder_with_replay_on(self):
        script = two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call-1")])
        script.insert(1, xai_bad_request("Argument not supported: temperature"))
        h = _Host(script)
        h.stream_worker([dict(GO)])
        self.assertEqual(len(h.sent), 3)
        self.assertNotIn("temperature", h.sent[2])       # the temperature rung ran
        self.assertIn(dumped(reasoning("rs_1", "ENC1")), h.sent[2]["input"])
        self.assertEqual(h._responses_replay_off, set())


class _DisplayHost(StreamingMixin, XAIMixin):
    def __init__(self):
        self.provider = "xAI"
        self.model = "grok-4.3"
        self.thinking_effort = "low"
        self.temperature = 0.3
        self.desktop_enabled = _Var(False)
        self._xai_caps = {}

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return [dict(TOOL)]


class DisplayTests(unittest.TestCase):

    def test_debug_dump_mirrors_the_include(self):
        h = _DisplayHost()
        self.assertIn('"reasoning.encrypted_content"', h._payload_for_display([GO]))
        h._responses_replay_off = {"xAI"}
        self.assertNotIn("reasoning.encrypted_content", h._payload_for_display([GO]))


if __name__ == "__main__":
    unittest.main()
