"""xAI (Grok) provider mixin.

xAI's API is OpenAI-compatible, so the provider rides on the already-installed
``openai`` SDK with ``base_url="https://api.x.ai/v1"`` — no new dependency.
The primary surface is the Responses endpoint (same ``input`` items, flat
``type: "function"`` tool declarations and ``function_call_output`` results as
OpenAI's), which means the existing StreamingMixin translators
(``_messages_to_responses`` / ``_tools_to_responses``) are reused verbatim.

Deliberate differences from the OpenAI mixin:

1. **Raw event iteration, not the SDK stream wrapper** — the loop consumes
   ``client.responses.create(stream=True)`` events directly instead of
   ``responses.stream()``. The wrapper maintains a client-side snapshot state
   machine tuned to OpenAI's exact event sequences; raw events keep xAI's
   compatibility surface as small as possible. Usage is read from the
   ``response.completed`` event rather than ``get_final_response()``.
2. **No server-side tools** — xAI offers ``web_search`` / ``x_search`` /
   ``code_interpreter`` server tools, but whether they can be MIXED with
   client-side function tools is not clearly documented (Gemini has exactly
   this restriction). MyAgent's agentic loop is custom-function-first, so xAI
   keeps the local DuckDuckGo ``web_search`` / ``fetch_webpage`` tools instead
   (the Gemini pattern) and sends no built-ins.
3. **Reasoning knob per family** — ``XAI_REASONING_EFFORT`` maps model
   families to their accepted ``reasoning.effort`` values (grok-4.3:
   none/low/medium/high; grok-4.20-multi-agent: low..xhigh, where the knob is
   agent collaboration count; legacy grok-3-mini: low/high). Families not in
   the table get no reasoning param at all — the pinned ``-reasoning`` /
   ``-non-reasoning`` variants bake the behaviour into the model id.
   Reasoning deltas stream back as ``response.reasoning_text.delta`` or
   ``response.reasoning_summary_text.delta`` depending on model; both feed the
   Show Thinking pane.
4. **Temperature alongside reasoning** — xAI accepts both (Gemini-style).
   A BadRequest mentioning temperature or reasoning downgrades the request
   (drop temperature → drop reasoning summary → drop reasoning) and retries,
   so a future API tightening degrades gracefully instead of hard-failing.
"""
import json
import time
import threading

import openai

from myagent.constants import (
    XAI_FALLBACK_MODELS,
    XAI_NON_AGENTIC_SUBSTRINGS,
    XAI_NON_VISION_PREFIXES,
    XAI_REASONING_EFFORT,
)
from myagent.retry_util import rate_limit_backoff, server_error_backoff


class XAIMixin:

    def _xai_reasoning_values(self, model_id=None):
        """Accepted reasoning.effort values for a Grok model, or None when the
        family has no client-side knob. Longest prefix wins so
        grok-4.20-multi-agent-0309 matches its own entry, not a shorter one."""
        mid = model_id or self.model or ""
        best = None
        best_len = 0
        for prefix, values in XAI_REASONING_EFFORT.items():
            if mid.startswith(prefix) and len(prefix) > best_len:
                best = values
                best_len = len(prefix)
        return best

    def _is_xai_vision_model(self, model_id=None):
        """False for text-only Grok families (grok-build, legacy grok-3 /
        grok-code); every current chat tier (grok-4.x) takes image input."""
        mid = model_id or self.model or ""
        return not mid.startswith(XAI_NON_VISION_PREFIXES)

    def _fetch_xai_models(self):
        """List Grok chat models from api.x.ai, dropping non-agentic entries
        (image/video generation, embeddings) by substring."""
        if not getattr(self, "xai_client", None):
            return list(XAI_FALLBACK_MODELS)
        try:
            response = self.xai_client.models.list()
            model_ids = []
            for m in response.data:
                mid = m.id
                if not mid.startswith("grok"):
                    continue
                if any(skip in mid for skip in XAI_NON_AGENTIC_SUBSTRINGS):
                    continue
                model_ids.append(mid)
            model_ids.sort()
            self._xai_model_display_names = {mid: mid for mid in model_ids}
            return model_ids if model_ids else list(XAI_FALLBACK_MODELS)
        except Exception:
            self._xai_model_display_names = {}
            return list(XAI_FALLBACK_MODELS)

    def _stream_xai_events(self, api_kwargs, label_emitted):
        """One streaming pass over the xAI Responses endpoint.
        Returns (full_text, stop_reason, content_blocks, had_thinking,
        label_emitted, usage_dict) — accumulator state is local so a retry
        starts clean."""
        full_text = ""
        had_thinking = False
        in_thinking = False
        tool_calls_acc = {}  # output_index -> {call_id, name, arguments}
        usage_dict = None
        timed_out = False
        first_content_timeout = getattr(self, "_xai_first_content_timeout", 0)

        stream = self.xai_client.responses.create(stream=True, **api_kwargs)
        try:
            for event in stream:
                if self.stop_requested:
                    if hasattr(self, "_xai_first_content"):
                        self._xai_first_content.set()
                    break
                if (first_content_timeout and hasattr(self, "_xai_first_content")
                        and not self._xai_first_content.is_set()
                        and time.time() - self._xai_stream_start >= first_content_timeout):
                    self._xai_first_content.set()
                    timed_out = True
                    break

                etype = getattr(event, "type", "")

                # Reasoning deltas — grok streams raw reasoning text and/or
                # summarized reasoning depending on model; treat both as thinking.
                if etype in ("response.reasoning_text.delta",
                             "response.reasoning_summary_text.delta"):
                    if hasattr(self, "_xai_first_content"):
                        self._xai_first_content.set()
                    if not in_thinking:
                        in_thinking = True
                        had_thinking = True
                        self.queue.put({"type": "thinking_start"})
                    self.queue.put({"type": "thinking_delta", "content": event.delta})

                elif etype in ("response.reasoning_text.done",
                               "response.reasoning_summary_part.done"):
                    if in_thinking:
                        self.queue.put({"type": "thinking_end"})
                        in_thinking = False

                elif etype == "response.output_text.delta":
                    if hasattr(self, "_xai_first_content"):
                        self._xai_first_content.set()
                    if in_thinking:
                        self.queue.put({"type": "thinking_end"})
                        in_thinking = False
                    if not label_emitted:
                        self.queue.put({"type": "label"})
                        label_emitted = True
                    full_text += event.delta
                    self.queue.put({"type": "text_delta", "content": event.delta})

                elif etype == "response.output_item.added":
                    if hasattr(self, "_xai_first_content"):
                        self._xai_first_content.set()
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", None) == "function_call":
                        idx = getattr(event, "output_index", len(tool_calls_acc))
                        tool_calls_acc[idx] = {
                            "call_id": item.call_id,
                            "name": item.name,
                            "arguments": "",
                        }

                elif etype == "response.function_call_arguments.delta":
                    idx = getattr(event, "output_index", None)
                    if idx in tool_calls_acc:
                        tool_calls_acc[idx]["arguments"] += event.delta

                elif etype == "response.function_call_arguments.done":
                    idx = getattr(event, "output_index", None)
                    if idx in tool_calls_acc:
                        tool_calls_acc[idx]["arguments"] = event.arguments

                elif etype == "response.completed":
                    usage = getattr(getattr(event, "response", None), "usage", None)
                    if usage:
                        usage_dict = {
                            "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                        }

                elif etype == "response.failed":
                    resp = getattr(event, "response", None)
                    err = getattr(resp, "error", None)
                    msg = getattr(err, "message", None) or str(err) or "response.failed"
                    raise RuntimeError(f"xAI response failed: {msg}")
        finally:
            try:
                stream.close()
            except Exception:
                pass

        if timed_out:
            raise openai.APITimeoutError(request=None)  # type: ignore[arg-type]

        if in_thinking:
            self.queue.put({"type": "thinking_end"})

        stop_reason = "tool_use" if tool_calls_acc else "end_turn"

        content_blocks = []
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            try:
                parsed_args = json.loads(tc["arguments"]) if tc["arguments"] else {}
            except json.JSONDecodeError:
                parsed_args = {"_raw": tc["arguments"]}
            content_blocks.append({
                "type": "tool_use",
                "id": tc["call_id"],
                "name": tc["name"],
                "input": parsed_args,
            })

        return full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict

    def _stream_xai_call(self, messages, max_retries, label_emitted):
        """Execute one xAI Responses API call with streaming and retry logic.
        Returns (stop_reason, content_blocks, full_text, had_thinking,
        label_emitted, usage) — same 6-tuple as the other provider callers."""
        usage_dict = None
        system_prompt = self._build_system_prompt()
        tools = self._get_tools()
        responses_tools = self._tools_to_responses(tools) if tools else []

        api_kwargs = {
            "model": self.model,
            "input": self._messages_to_responses(messages),
            "instructions": system_prompt,
            "store": False,
            # xAI accepts temperature alongside reasoning (Gemini-style);
            # the BadRequest ladder below drops it if a model refuses.
            "temperature": self.temperature,
        }
        if responses_tools:
            api_kwargs["tools"] = responses_tools
        values = self._xai_reasoning_values()
        if values:
            # A stale saved effort (e.g. "max" from an Anthropic instruction)
            # coerces to the family default rather than erroring.
            effort = (self.thinking_effort if self.thinking_effort in values
                      else ("low" if "low" in values else values[0]))
            api_kwargs["reasoning"] = {"effort": effort, "summary": "auto"}

        FIRST_CONTENT_TIMEOUT = 180
        WAITING_MSG_INTERVAL = 15

        self._xai_first_content = threading.Event()
        self._xai_stream_start = time.time()
        self._xai_first_content_timeout = FIRST_CONTENT_TIMEOUT

        def _waiting_ticker():
            while not self._xai_first_content.wait(timeout=WAITING_MSG_INTERVAL):
                elapsed = int(time.time() - self._xai_stream_start)
                self.queue.put({
                    "type": "tool_info",
                    "content": f"Waiting for model response... ({elapsed}s elapsed)\n",
                })
        ticker = threading.Thread(target=_waiting_ticker, daemon=True)
        ticker.start()

        # stop_reason doubles as the success sentinel: the downgrade ladder
        # below retries via `continue`, so the loop can theoretically exhaust
        # all attempts without a break or raise — guard against returning
        # unbound values in that corner.
        stop_reason = None
        full_text, content_blocks, had_thinking = "", [], False
        for attempt in range(max_retries):
            try:
                full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict = \
                    self._stream_xai_events(api_kwargs, label_emitted)
                break  # success
            except openai.AuthenticationError as e:
                raise RuntimeError(
                    "xAI rejected the API key — check the XAI_API_KEY "
                    f"environment variable. Original error: {e}"
                ) from e
            except openai.BadRequestError as e:
                # Parameter-downgrade ladder: each branch adjusts api_kwargs and
                # retries via `continue` (consuming an attempt), so successive
                # 400s — e.g. temperature AND reasoning both refused — still
                # converge on an accepted request instead of hard-failing.
                err_str = str(e)
                if "temperature" in err_str and "temperature" in api_kwargs:
                    del api_kwargs["temperature"]
                    self.queue.put({
                        "type": "tool_info",
                        "content": "Model does not support temperature — retrying without it...\n",
                    })
                    continue
                if "reasoning" in err_str.lower() and "reasoning" in api_kwargs:
                    r = api_kwargs["reasoning"]
                    if isinstance(r, dict) and "summary" in r:
                        # First downgrade: some models may reject the summary
                        # request while accepting the effort itself.
                        api_kwargs["reasoning"] = {k: v for k, v in r.items() if k != "summary"}
                        self.queue.put({
                            "type": "tool_info",
                            "content": "Model rejected reasoning summary — retrying with effort only...\n",
                        })
                        continue
                    del api_kwargs["reasoning"]
                    self.queue.put({
                        "type": "tool_info",
                        "content": "Model does not support the reasoning parameter — retrying without it...\n",
                    })
                    continue
                raise
            except openai.APITimeoutError:
                self._xai_first_content = threading.Event()
                self._xai_stream_start = time.time()
                ticker = threading.Thread(target=_waiting_ticker, daemon=True)
                ticker.start()
                if attempt < max_retries - 1:
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Stream timeout (no content from model within {FIRST_CONTENT_TIMEOUT}s) — retrying (attempt {attempt + 1}/{max_retries})...\n",
                    })
                else:
                    raise
            except openai.RateLimitError:
                if attempt < max_retries - 1:
                    wait = rate_limit_backoff(attempt)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                else:
                    raise
            except openai.APIError as e:
                if attempt < max_retries - 1 and getattr(e, "status_code", 0) >= 500:
                    wait = server_error_backoff(attempt)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"API error — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                else:
                    raise

        # Stop the ticker thread
        self._xai_first_content.set()
        if stop_reason is None:
            raise RuntimeError("xAI call failed: retries exhausted without a successful response")
        return stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict
