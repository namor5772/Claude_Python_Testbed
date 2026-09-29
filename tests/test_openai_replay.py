"""The OpenAI reasoning round trip (2026-09-30, saved chat "BAD _REPEAT").

MyAgent calls the Responses API with store=False, so between two calls of a
tool loop the server remembers nothing: a reasoning model's chain of thought,
its web searches and each message's `phase` exist only in the response's
output list. Until this fix `_stream_responses` kept the text and the function
calls and dropped the rest, and `_messages_to_responses` rebuilt each
assistant turn from those two alone — so gpt-6-luna at Xhigh met every tool
result without the reasoning that asked for it, re-derived the situation from
scratch and, asked "Tell me what time is it now?", spoke the time five times
in eight calls without ever handing the conversation back.

Pinned here, against the REAL code with only the SDK's
`responses.stream` faked (events + a final response built from real SDK item
types):

  * capture   — a completed response's output items become one
                `responses_items` block beside the text / tool_use blocks;
                nothing for an incomplete response or a STOPped stream;
  * replay    — through the real stream_worker, the SECOND request of a tool
                loop carries the first call's reasoning item (encrypted
                content and all), web_search_call and commentary `phase`
                included, in the API's order and never duplicated;
  * include   — every request asks for reasoning.encrypted_content;
  * fallback  — a 400 about the replayed items (invalid_encrypted_content,
                or a param pointing at a replayed item) switches replay off
                for the session, warns once and resends the legacy shape; an
                unrelated 400 does not;
  * display   — the Debug dump mirrors the include and stubs the ciphertext;
  * saving    — a saved chat keeps the items without ciphertext, and drops
                the reasoning items when Save Thinking is off.
"""

import copy
import queue
import unittest
from types import SimpleNamespace

import httpx
import openai
from openai.types.responses import (ResponseFunctionToolCall, ResponseFunctionWebSearch,
                                    ResponseOutputMessage, ResponseOutputText,
                                    ResponseReasoningItem)
from openai.types.responses.response_function_web_search import ActionSearch
from openai.types.responses.response_reasoning_item import Summary

from myagent.chat_mixin import ChatMixin
from myagent.constants import OPENAI_RESPONSES_INCLUDE
from myagent.openai_mixin import OpenAIMixin
from myagent.streaming_mixin import StreamingMixin

TOOL = {"name": "read_file", "description": "d",
        "input_schema": {"type": "object", "properties": {}}}


# --- the SDK's output items, as real typed objects ---------------------------

def reasoning(rid, enc, text="plan"):
    return ResponseReasoningItem(id=rid, type="reasoning", encrypted_content=enc,
                                 summary=[Summary(text=text, type="summary_text")])


def function_call(fid, call_id, name="read_file", args='{"path": "x"}'):
    return ResponseFunctionToolCall(id=fid, type="function_call", call_id=call_id,
                                    name=name, arguments=args, status="completed")


def web_search(wid, query="time now"):
    return ResponseFunctionWebSearch(id=wid, type="web_search_call", status="completed",
                                     action=ActionSearch(type="search", query=query))


def message(mid, text, phase=None):
    return ResponseOutputMessage(id=mid, type="message", role="assistant", status="completed",
                                 content=[ResponseOutputText(type="output_text", text=text,
                                                             annotations=[])],
                                 phase=phase)


def dumped(item):
    return item.model_dump(mode="json", exclude_none=True)


# --- the stream events _stream_responses reads -------------------------------

def ev(type_, **kw):
    return SimpleNamespace(type=type_, **kw)


def call_events(idx, call_id, name="read_file", args='{"path": "x"}'):
    return [ev("response.output_item.added", output_index=idx,
               item=SimpleNamespace(type="function_call", call_id=call_id, name=name)),
            ev("response.function_call_arguments.done", output_index=idx, arguments=args)]


def text_events(text):
    return [ev("response.output_text.delta", delta=text)]


def final(*items, status="completed"):
    return SimpleNamespace(status=status, output=list(items), usage=None)


def bad_request(message, code=None, param=None):
    req = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return openai.BadRequestError(
        message, response=httpx.Response(400, request=req),
        body={"message": message, "type": "invalid_request_error",
              "code": code, "param": param})


class _FakeStream:
    def __init__(self, events, final_response):
        self._events, self._final = events, final_response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_response(self):
        if isinstance(self._final, Exception):
            raise self._final
        return self._final


class _FakeResponses:
    """`responses.stream(**kw)` scripted per call: each entry is
    (events, final response) or an exception to raise."""

    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def stream(self, **kw):
        self.sent.append(copy.deepcopy(kw))
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return _FakeStream(*step)


class _Var:
    def __init__(self, v):
        self._v = v

    def get(self):
        return self._v


class _Host(StreamingMixin, OpenAIMixin):
    """The real stream_worker / _stream_responses_call / _stream_responses /
    _messages_to_responses on the OpenAI path; only the SDK is fake."""

    def __init__(self, script, model="gpt-6-luna"):
        self.provider = "OpenAI"
        self.model = model
        self.thinking_enabled = True
        self.thinking_effort = "xhigh"
        self.thinking_mode = "xhigh"
        self.thinking_budget = 16384
        self.temperature = 0.0
        self._temp_var = _Var(0.0)
        self.text_verbosity = "medium"
        self.desktop_enabled = _Var(False)
        self._openai_unsupported_tools = {}
        self._openai_replay_off = False
        self.queue = queue.Queue()
        self.stop_requested = False
        self._headless = False
        self._result_file = None
        self.agent_instruction_name = "JustChat"
        self.openai_client = SimpleNamespace(responses=_FakeResponses(script))
        self.tool_calls = []

    @property
    def sent(self):
        return self.openai_client.responses.sent

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return [dict(TOOL)]

    def _weak_desktop_combo_warning(self):
        return None

    def _payload_for_display(self, messages):
        return ""

    def _get_model_param_summary(self):
        return "reasoning=Xhigh"

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

    def drain(self, kind):
        """The queued contents of one kind — the queue is emptied."""
        return [m.get("content") for m in self.drain_all() if m.get("type") == kind]


GO = {"role": "user", "content": "go"}
OUT1 = {"type": "function_call_output", "call_id": "call_1", "output": "ok"}
LEGACY_FC1 = {"type": "function_call", "call_id": "call_1", "name": "read_file",
              "arguments": '{"path": "x"}'}


def two_call_script(first_items, first_events=None):
    """Call 1 asks for read_file with the given output items; call 2 answers."""
    return [
        (first_events or call_events(1, "call_1"), final(*first_items)),
        (text_events("done"), final(reasoning("rs_2", "ENC2"),
                                    message("msg_2", "done", phase="final_answer"))),
    ]


class CaptureTests(unittest.TestCase):

    def test_output_items_become_one_block_beside_text_and_tool_use(self):
        h = _Host([(text_events("Checking.") + call_events(2, "call_1"),
                    final(reasoning("rs_1", "ENC1"), message("msg_1", "Checking.", "commentary"),
                          function_call("fc_1", "call_1")))])
        _t, stop, blocks, _th, _lbl, _u = h._stream_responses(
            {"model": h.model, "input": [GO]}, True)
        self.assertEqual(stop, "tool_use")
        self.assertEqual([b["type"] for b in blocks], ["text", "tool_use", "responses_items"])
        self.assertEqual(blocks[1]["id"], "call_1")
        self.assertEqual(blocks[2]["items"], [
            dumped(reasoning("rs_1", "ENC1")),
            dumped(message("msg_1", "Checking.", "commentary")),
            dumped(function_call("fc_1", "call_1"))])

    def test_items_are_plain_json_dicts_without_none_fields(self):
        items = OpenAIMixin._openai_output_items(
            final(reasoning("rs_1", "ENC1"), message("msg_1", "hi")))
        self.assertEqual(items[0], {"id": "rs_1", "type": "reasoning", "encrypted_content": "ENC1",
                                    "summary": [{"text": "plan", "type": "summary_text"}]})
        self.assertNotIn("content", items[0])      # None on the SDK object
        self.assertNotIn("phase", items[1])        # None — never sent as null

    def test_incomplete_or_failed_response_keeps_no_items(self):
        for status in ("incomplete", "failed", "cancelled"):
            with self.subTest(status=status):
                self.assertEqual(OpenAIMixin._openai_output_items(
                    final(reasoning("rs_1", "E"), status=status)), [])

    def test_an_item_that_will_not_dump_drops_the_whole_turn(self):
        class Broken:
            def model_dump(self, **kw):
                raise ValueError("no")
        self.assertEqual(OpenAIMixin._openai_output_items(
            final(reasoning("rs_1", "E"), Broken())), [])
        self.assertEqual(OpenAIMixin._openai_output_items(
            final(reasoning("rs_1", "E"), {"no": "type"})), [])

    def test_stopped_stream_keeps_no_items(self):
        # STOP breaks the event loop; the SDK then has no final response.
        h = _Host([(call_events(1, "call_1"), RuntimeError("stream closed"))])
        _t, _s, blocks, *_ = h._stream_responses({"model": h.model, "input": [GO]}, True)
        self.assertEqual([b["type"] for b in blocks], ["tool_use"])


class ReplayThroughStreamWorkerTests(unittest.TestCase):

    def run_worker(self, host):
        host.stream_worker([dict(GO)])
        self.assertEqual(host.drain("error"), [])
        return host.sent

    def test_second_call_carries_the_first_calls_reasoning(self):
        # THE regression: the reasoning that asked for the tool reaches the
        # call that reads its result.
        h = _Host(two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call_1")]))
        sent = self.run_worker(h)
        self.assertEqual(len(sent), 2)
        self.assertEqual(h.tool_calls, ["read_file"])
        self.assertEqual(sent[1]["input"], [
            GO,
            dumped(reasoning("rs_1", "ENC1")),
            dumped(function_call("fc_1", "call_1")),
            OUT1])

    def test_every_request_asks_for_the_encrypted_reasoning(self):
        h = _Host(two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call_1")]))
        for kw in self.run_worker(h):
            self.assertEqual(kw["include"], list(OPENAI_RESPONSES_INCLUDE))
            self.assertIn("reasoning.encrypted_content", kw["include"])
            self.assertIs(kw["store"], False)

    def test_web_search_and_commentary_phase_survive_in_order_once(self):
        items = [reasoning("rs_1", "ENC1"), web_search("ws_1"),
                 message("msg_1", "Checking the clock.", "commentary"),
                 function_call("fc_1", "call_1")]
        h = _Host(two_call_script(items, text_events("Checking the clock.")
                                  + call_events(3, "call_1")))
        sent = self.run_worker(h)
        self.assertEqual(sent[1]["input"], [GO] + [dumped(i) for i in items] + [OUT1])
        # the legacy rebuild of the same turn (a phase-less assistant message +
        # an id-less function_call) is not ALSO sent
        self.assertNotIn(LEGACY_FC1, sent[1]["input"])
        self.assertEqual(sum(1 for i in sent[1]["input"] if i.get("role") == "assistant"
                             or i.get("type") == "message"), 1)

    def test_replay_off_sends_the_legacy_shape(self):
        h = _Host(two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call_1")]))
        h._openai_replay_off = True
        sent = self.run_worker(h)
        self.assertEqual(sent[1]["input"], [GO, LEGACY_FC1, OUT1])

    def test_incomplete_first_response_falls_back_to_the_legacy_shape(self):
        h = _Host([(call_events(1, "call_1"),
                    final(reasoning("rs_1", "E"), function_call("fc_1", "call_1"),
                          status="incomplete")),
                   (text_events("done"), final(message("msg_2", "done")))])
        sent = self.run_worker(h)
        self.assertEqual(sent[1]["input"], [GO, LEGACY_FC1, OUT1])

    def test_wire_copies_never_alias_the_history(self):
        items = [reasoning("rs_1", "ENC1"), function_call("fc_1", "call_1")]
        history = [GO, {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "x"}},
            {"type": "responses_items", "items": [dumped(i) for i in items]}]}]
        h = _Host([])
        wire = h._messages_to_responses(history)
        wire[1]["encrypted_content"] = "CHANGED"
        self.assertEqual(history[1]["content"][1]["items"][0]["encrypted_content"], "ENC1")


class ReplayRefusedTests(unittest.TestCase):

    def test_refusal_matrix(self):
        replayed = [GO, {"id": "rs_1", "type": "reasoning", "encrypted_content": "E"},
                    {"role": "user", "content": [{"type": "input_image", "image_url": "x"}]}]
        refused = OpenAIMixin._openai_replay_refused
        self.assertTrue(refused(bad_request("x", code="invalid_encrypted_content"), replayed))
        self.assertTrue(refused(bad_request(
            "The encrypted content for item rs_1 could not be verified."), replayed))
        self.assertTrue(refused(bad_request("bad", param="input[1].summary"), replayed))
        # an item without an id is ours (a user message, a rebuilt turn):
        # an error about it is not about the replay
        self.assertFalse(refused(bad_request("bad image", param="input[2].content[0]"), replayed))
        self.assertFalse(refused(bad_request("bad", param="input[0]"), replayed))
        self.assertFalse(refused(bad_request("bad", param="input[9]"), replayed))
        self.assertFalse(refused(bad_request("context too long", param=None), replayed))
        self.assertFalse(refused(bad_request("bad", param="tools[0]"), replayed))

    def test_refused_replay_warns_once_and_resends_the_legacy_shape(self):
        script = two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call_1")])
        script.insert(1, bad_request(
            "The encrypted content for item rs_1 could not be verified.",
            code="invalid_encrypted_content"))
        h = _Host(script)
        h.stream_worker([dict(GO)])
        queued = h.drain_all()
        self.assertEqual([m for m in queued if m.get("type") == "error"], [])
        self.assertEqual(len(h.sent), 3)                 # call 1, refused, retry
        self.assertIn(dumped(reasoning("rs_1", "ENC1")), h.sent[1]["input"])
        self.assertEqual(h.sent[2]["input"], [GO, LEGACY_FC1, OUT1])
        self.assertTrue(h._openai_replay_off)
        self.assertEqual(len([m for m in queued if m.get("type") == "warning"
                              and "refused" in m.get("content", "")]), 1)

    def test_unrelated_400_is_raised_and_keeps_replay_on(self):
        script = two_call_script([reasoning("rs_1", "ENC1"), function_call("fc_1", "call_1")])
        script[1] = bad_request("Invalid image", param="input[0].content")
        h = _Host(script)
        h.stream_worker([dict(GO)])
        errors = h.drain("error")
        self.assertEqual(len(errors), 1)
        self.assertIn("Invalid image", errors[0])
        self.assertFalse(h._openai_replay_off)
        self.assertEqual(len(h.sent), 2)


class _DisplayHost(StreamingMixin, OpenAIMixin):
    def __init__(self):
        self.provider = "OpenAI"
        self.model = "gpt-6-luna"
        self.thinking_enabled = True
        self.thinking_effort = "xhigh"
        self.thinking_mode = "xhigh"
        self.temperature = 0.0
        self.text_verbosity = "medium"
        self.desktop_enabled = _Var(False)
        self._openai_unsupported_tools = {}

    def _build_system_prompt(self):
        return "SYS"

    def _get_tools(self):
        return [dict(TOOL)]


class DisplayAndSaveTests(unittest.TestCase):
    ENC = "E" * 5000

    def history(self):
        return [GO, {"role": "assistant", "content": [
            {"type": "tool_use", "id": "call_1", "name": "read_file", "input": {"path": "x"}},
            {"type": "responses_items", "items": [
                dumped(reasoning("rs_1", self.ENC, "the plan")), dumped(web_search("ws_1")),
                dumped(function_call("fc_1", "call_1"))]}]},
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "call_1", "content": "ok"}]}]

    def test_debug_dump_mirrors_the_include_and_stubs_the_ciphertext(self):
        history = self.history()
        text = _DisplayHost()._payload_for_display(history)
        self.assertIn('"reasoning.encrypted_content"', text)
        self.assertIn('"rs_1"', text)
        self.assertIn("...[5000 chars]", text)
        self.assertNotIn(self.ENC, text)
        self.assertEqual(history[1]["content"][1]["items"][0]["encrypted_content"], self.ENC)

    def saved(self, save_thinking):
        host = ChatMixin.__new__(ChatMixin)
        host.messages = self.history()
        host.save_thinking = _Var(save_thinking)
        (block,) = [b for b in host._serialize_messages()[1]["content"]
                    if b["type"] == "responses_items"]
        return block["items"]

    def test_saved_chat_keeps_items_without_ciphertext(self):
        items = self.saved(save_thinking=True)
        self.assertEqual([i["type"] for i in items], ["reasoning", "web_search_call", "function_call"])
        self.assertEqual(items[0]["summary"][0]["text"], "the plan")
        self.assertEqual(items[0]["encrypted_content"], "[5000 chars, not saved]")

    def test_saved_chat_drops_reasoning_when_save_thinking_is_off(self):
        items = self.saved(save_thinking=False)
        self.assertEqual([i["type"] for i in items], ["web_search_call", "function_call"])


if __name__ == "__main__":
    unittest.main()
