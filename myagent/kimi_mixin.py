"""Moonshot AI (Kimi) provider mixin.

Kimi's API is OpenAI-compatible, so the provider rides on the already-installed
``openai`` SDK with ``base_url="https://api.moonshot.ai/v1"`` — no new
dependency. Unlike xAI, the compatibility surface is **Chat Completions only**
(no Responses endpoint), so the Responses translators cannot be reused: this
mixin carries its own ``_messages_to_kimi`` / ``_tools_to_kimi``.

The load-bearing difference from every other OpenAI-compatible provider is
**reasoning_content round-tripping**. Kimi's thinking models return their
reasoning in a non-standard ``reasoning_content`` field on the assistant
message (streamed as ``delta.reasoning_content``, always before ``content``),
and the models are TRAINED to reason across interleaved tool calls: during a
tool-call loop the accumulated reasoning must be sent back unmodified on the
assistant messages, or quality degrades (naively pointing a Grok/OpenAI-style
harness at Kimi "works" but silently underperforms — several frameworks have
shipped exactly that bug). The per-model contract, verified against
platform.kimi.ai docs 2026-07-25:

- ``kimi-k3`` — always reasons; Preserved Thinking always on; top-level
  ``reasoning_effort`` (low/high/max, default max). ROUND-TRIP REQUIRED.
- ``kimi-k2.7-code`` (+ ``-highspeed``) — always thinks; the ``thinking``
  param is rejected (behaviour baked in); keep is fixed "all".
  ROUND-TRIP REQUIRED.
- ``kimi-k2.6`` — thinking toggleable via ``thinking {"type": enabled|
  disabled}``; round-trip required within a tool loop, and ``keep: "all"``
  (Preserved Thinking) extends it across turns — requested here whenever
  thinking is on, matching the keep-everything translator.
- ``kimi-k2.5`` — thinking toggleable, but Preserved Thinking is UNSUPPORTED:
  reasoning_content must NOT be sent back at all.

Mechanically: ``_stream_kimi_events`` accumulates the streamed reasoning into
a ``{"type": "thinking", "thinking": ...}`` block stored FIRST in the
assistant content_blocks (the internal history stays Anthropic-style), and
``_messages_to_kimi`` re-emits it as ``reasoning_content`` on the wire —
included or stripped per the policy above. A 400 naming reasoning_content
flips a per-model learn-once switch and retries without it, so a future model
whose support the static table mispredicts degrades instead of hard-failing.
Reasoning tokens bill as output when generated and again as input when
re-sent; Kimi's automatic context caching (cache-hit input at ~1/6 the miss
rate) keeps the re-send cheap, and ``_kimi_usage_dict`` folds the reported
cached tokens into an exact ``cost_usd`` that stream_worker prefers over the
flat 2-tuple estimate.

Other deliberate choices:

1. **No temperature, ever** — every current Kimi model fixes sampling
   server-side (temperature/top_p non-modifiable per the parameter
   reference); the thinking-model guide says outright "do not set it".
2. **No server-side tools** — Kimi keeps the local DuckDuckGo ``web_search``
   / ``fetch_webpage`` (the Gemini pattern); ``_get_tools()`` does not strip
   them for this provider.
3. **max_completion_tokens** (the currently documented name; a 400 ladder
   rung swaps to the legacy ``max_tokens``) — MAX_TOKENS_THINKING (32768)
   when thinking is active, comfortably above the docs' ">= 16000 for
   tool-calling loops" floor.
4. **Model fetch filter** — keep ids starting ``kimi-k`` and drop the DASH
   family ``kimi-k2-*`` (kimi-k2-thinking etc., discontinued 2026-05-25;
   the current K2 generation uses DOTS: kimi-k2.5/k2.6/k2.7-code). The
   non-K rest (moonshot-v1* — EOL 2026-08-31 — kimi-latest,
   kimi-thinking-preview) falls out of the prefix rule.
"""
import json
import re
import time
import threading

import openai

from myagent.constants import (
    KIMI_ALWAYS_THINKING_PREFIXES,
    KIMI_CACHE_HIT_PRICING,
    KIMI_FALLBACK_MODELS,
    KIMI_NO_REASONING_ROUNDTRIP_PREFIXES,
    KIMI_NON_VISION_PREFIXES,
    KIMI_REASONING_EFFORT,
    KIMI_THINKING_TOGGLE_PREFIXES,
    MAX_TOKENS,
    MAX_TOKENS_THINKING,
)
from myagent.retry_util import rate_limit_backoff, server_error_backoff

# Stale saved efforts from other providers' UIs coerced onto kimi-k3's SPARSE
# low/high/max ladder (there is no medium). Anything unmapped — "off",
# "adaptive", junk — lands on "max", the API's own default.
_KIMI_EFFORT_COERCE = {
    "minimal": "low",
    "none": "low",
    "medium": "high",
    "xhigh": "max",
}


class KimiMixin:

    def _kimi_reasoning_values(self, model_id=None):
        """Accepted reasoning_effort values for a Kimi model, or None when the
        family has no effort knob (only kimi-k3 has one today). Longest prefix
        wins, same convention as the xAI matrix."""
        mid = model_id or self.model or ""
        best = None
        best_len = 0
        for prefix, values in KIMI_REASONING_EFFORT.items():
            if mid.startswith(prefix) and len(prefix) > best_len:
                best = values
                best_len = len(prefix)
        return best

    def _kimi_reasoning_effort(self, values):
        """The reasoning_effort to send: the saved effort when the model
        accepts it, else coerced onto the sparse ladder (medium→high,
        xhigh→max, minimal/none→low, anything else→max, the API default)."""
        if self.thinking_effort in values:
            return self.thinking_effort
        coerced = _KIMI_EFFORT_COERCE.get(self.thinking_effort, "max")
        return coerced if coerced in values else values[-1]

    def _kimi_thinking_toggleable(self, model_id=None):
        """True for models whose thinking can be switched via the
        thinking {"type": enabled|disabled} parameter (k2.6 / k2.5)."""
        mid = model_id or self.model or ""
        return mid.startswith(KIMI_THINKING_TOGGLE_PREFIXES)

    def _kimi_thinking_active(self, model_id=None):
        """Whether this call will produce reasoning: always for k3 /
        k2.7-code, the Thinking checkbox for k2.6 / k2.5, else False."""
        mid = model_id or self.model or ""
        if mid.startswith(KIMI_ALWAYS_THINKING_PREFIXES):
            return True
        if self._kimi_thinking_toggleable(mid):
            return bool(self.thinking_enabled)
        return False

    def _kimi_roundtrips_reasoning(self, model_id=None):
        """True when historical reasoning_content must be sent back on
        assistant messages (k3 / k2.7-code / k2.6). False for k2.5, which
        does not support Preserved Thinking at all. Unknown future models
        default to True — the documented direction of travel — backstopped
        by the 400 ladder's learn-once strip."""
        mid = model_id or self.model or ""
        return not mid.startswith(KIMI_NO_REASONING_ROUNDTRIP_PREFIXES)

    def _kimi_include_reasoning(self, model_id=None):
        """The runtime round-trip decision: the static policy minus models
        that rejected reasoning_content with a 400 this session."""
        mid = model_id or self.model or ""
        if mid in getattr(self, "_kimi_reasoning_rejected", ()):
            return False
        return self._kimi_roundtrips_reasoning(mid)

    def _is_kimi_vision_model(self, model_id=None):
        """False for the text-only k2.7-code coding line (incl. -highspeed);
        k3 / k2.6 / k2.5 all take image input."""
        mid = model_id or self.model or ""
        return not mid.startswith(KIMI_NON_VISION_PREFIXES)

    def _fetch_kimi_models(self):
        """List Kimi chat models from api.moonshot.ai. Keeps the current
        K-series (``kimi-k`` prefix: kimi-k2.5/k2.6/k2.7-code/k3), drops the
        discontinued DASH family (``kimi-k2-*`` — kimi-k2-thinking etc., EOL
        2026-05-25). moonshot-v1* (EOL 2026-08-31), kimi-latest and
        kimi-thinking-preview fall out of the prefix rule."""
        if not getattr(self, "kimi_client", None):
            return list(KIMI_FALLBACK_MODELS)
        try:
            response = self.kimi_client.models.list()
            model_ids = []
            for m in response.data:
                mid = m.id
                if not mid.startswith("kimi-k"):
                    continue
                if mid.startswith("kimi-k2-"):
                    continue
                model_ids.append(mid)
            model_ids.sort()
            self._kimi_model_display_names = {mid: mid for mid in model_ids}
            return model_ids if model_ids else list(KIMI_FALLBACK_MODELS)
        except Exception:
            self._kimi_model_display_names = {}
            return list(KIMI_FALLBACK_MODELS)

    @staticmethod
    def _tools_to_kimi(tools):
        """Convert Anthropic tool schemas to Chat Completions function format
        (nested under "function" — unlike the FLAT Responses shape the
        OpenAI/xAI paths use)."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema",
                                           {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]

    def _kimi_model_params(self, model_id=None):
        """Flat wire-format params for a Kimi call — the single source of
        truth for _stream_kimi_call and the Debug payload display.

        - NEVER temperature/top_p (fixed server-side on every current model).
        - max_completion_tokens: 32768 when thinking is active (docs require
          >= 16000 so reasoning + answer fit), else the plain 8192.
        - kimi-k3: top-level reasoning_effort; no thinking param.
        - kimi-k2.6 / k2.5: thinking {"type": enabled|disabled}; k2.6 also
          gets keep:"all" (Preserved Thinking) when enabled, matching the
          keep-everything translator. k2.5 never gets keep (unsupported).
        - kimi-k2.7-code: no knobs at all — always thinks, params baked in.
        """
        mid = model_id or self.model or ""
        params = {"max_completion_tokens":
                  MAX_TOKENS_THINKING if self._kimi_thinking_active(mid)
                  else MAX_TOKENS}
        values = self._kimi_reasoning_values(mid)
        if values:
            params["reasoning_effort"] = self._kimi_reasoning_effort(values)
        elif self._kimi_thinking_toggleable(mid):
            if self.thinking_enabled:
                thinking = {"type": "enabled"}
                if self._kimi_roundtrips_reasoning(mid):
                    thinking["keep"] = "all"
                params["thinking"] = thinking
            else:
                params["thinking"] = {"type": "disabled"}
        return params

    def _messages_to_kimi(self, messages, include_reasoning=True):
        """Convert internal Anthropic-format messages to Chat Completions
        format.

        Key differences from the Responses translator:
        - Tool results become role:"tool" messages keyed by tool_call_id
          (not top-level function_call_output items).
        - Assistant tool calls ride on the assistant message's tool_calls
          array (not top-level items).
        - Images use the image_url part shape with a data: URL.
        - ``{"type": "thinking"}`` blocks (written by _stream_kimi_events)
          are re-emitted as the assistant message's ``reasoning_content`` —
          the round-trip Kimi's thinking models are trained to expect —
          unless ``include_reasoning`` is False (kimi-k2.5, or a model that
          rejected the field this session).

        Images inside tool results are deferred to a follow-up user message
        (same rationale as the Responses translator: vision pipelines handle
        user-message images far more reliably than tool-output ones).
        """
        result = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    result.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    has_tool_result = any(
                        (isinstance(b, dict) and b.get("type") == "tool_result")
                        for b in content
                    )
                    if has_tool_result:
                        deferred_images = []
                        for block in content:
                            if not (isinstance(block, dict)
                                    and block.get("type") == "tool_result"):
                                continue
                            tc_content = block.get("content", "")
                            call_id = block.get("tool_use_id", "")
                            if isinstance(tc_content, list):
                                text_parts = []
                                for part in tc_content:
                                    if isinstance(part, dict) and part.get("type") == "image":
                                        src = part.get("source", {})
                                        data_url = (f"data:{src.get('media_type', 'image/png')};"
                                                    f"base64,{src.get('data', '')}")
                                        deferred_images.append({
                                            "type": "image_url",
                                            "image_url": {"url": data_url},
                                        })
                                    elif isinstance(part, dict) and part.get("type") == "text":
                                        text_parts.append(part.get("text", ""))
                                    else:
                                        text_parts.append(str(part))
                                result.append({
                                    "role": "tool",
                                    "tool_call_id": call_id,
                                    "content": "\n".join(text_parts),
                                })
                            else:
                                result.append({
                                    "role": "tool",
                                    "tool_call_id": call_id,
                                    "content": str(tc_content) if tc_content else "",
                                })
                        if deferred_images:
                            # Extract dimensions from the tool output text
                            dims_hint = ""
                            for item in reversed(result):
                                if item.get("role") != "tool":
                                    continue
                                out = item.get("content", "")
                                if isinstance(out, str):
                                    m = re.search(r"\((\d+)x(\d+)(?:\s+pixels)?\)", out)
                                    if m:
                                        dims_hint = f" ({m.group(1)}x{m.group(2)} pixels)"
                                        break
                            hint_text = (
                                f"Below is the screenshot image{dims_hint} returned by the "
                                "screenshot tool above. COORDINATE SYSTEM: the top-left pixel "
                                "is (0, 0), X increases rightward, Y increases downward. "
                                "When calling mouse_click, use the pixel (x, y) coordinates "
                                "as they appear in THIS image — they are automatically "
                                "scaled to actual screen coordinates."
                            )
                            result.append({
                                "role": "user",
                                "content": [
                                    {"type": "text", "text": hint_text},
                                    *deferred_images,
                                ],
                            })
                    else:
                        # User message with text + images
                        parts = []
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    parts.append({"type": "text",
                                                  "text": block.get("text", "")})
                                elif block.get("type") == "image":
                                    src = block.get("source", {})
                                    data_url = (f"data:{src.get('media_type', 'image/png')};"
                                                f"base64,{src.get('data', '')}")
                                    parts.append({
                                        "type": "image_url",
                                        "image_url": {"url": data_url},
                                    })
                            elif isinstance(block, str):
                                parts.append({"type": "text", "text": block})
                        result.append({"role": "user", "content": parts})

            elif role == "assistant":
                if isinstance(content, str):
                    result.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    reasoning_parts = []
                    tool_calls = []
                    for block in content:
                        # Handle both Pydantic objects and dicts
                        btype = getattr(block, "type", None) or (
                            block.get("type") if isinstance(block, dict) else None)
                        if btype == "text":
                            t = getattr(block, "text", None) or (
                                block.get("text") if isinstance(block, dict) else "")
                            if t:
                                text_parts.append(t)
                        elif btype == "thinking":
                            t = getattr(block, "thinking", None) or (
                                block.get("thinking") if isinstance(block, dict) else "")
                            if t:
                                reasoning_parts.append(t)
                        elif btype == "tool_use":
                            bid = getattr(block, "id", None) or (
                                block.get("id") if isinstance(block, dict) else "")
                            bname = getattr(block, "name", None) or (
                                block.get("name") if isinstance(block, dict) else "")
                            binput = getattr(block, "input", None) or (
                                block.get("input") if isinstance(block, dict) else {})
                            tool_calls.append({
                                "id": bid,
                                "type": "function",
                                "function": {"name": bname,
                                             "arguments": json.dumps(binput)},
                            })
                        # redacted_thinking (Anthropic-only) is skipped
                    out = {"role": "assistant",
                           "content": "\n".join(text_parts) or None}
                    if include_reasoning and reasoning_parts:
                        out["reasoning_content"] = "\n".join(reasoning_parts)
                    if tool_calls:
                        out["tool_calls"] = tool_calls
                    if out["content"] is None and not tool_calls \
                            and "reasoning_content" not in out:
                        continue  # nothing to send for this message
                    result.append(out)

        return result

    def _kimi_usage_dict(self, usage):
        """Build stream_worker's usage dict from a Chat Completions usage
        object, folding Kimi's cache-hit discount into an exact ``cost_usd``
        (which stream_worker prefers over the flat 2-tuple estimate — the
        estimate can't see that cached input bills at ~1/6 the miss rate).
        The cached-token count is probed under the three names in the wild:
        OpenAI-style prompt_tokens_details.cached_tokens, Moonshot's flat
        cached_tokens, and the DeepSeek-style prompt_cache_hit_tokens."""
        input_tokens = getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "completion_tokens", 0) or 0
        usage_dict = {"input_tokens": input_tokens,
                      "output_tokens": output_tokens}
        details = getattr(usage, "prompt_tokens_details", None)
        cached = (getattr(details, "cached_tokens", 0) or 0) if details else 0
        if not cached:
            cached = getattr(usage, "cached_tokens", 0) or 0
        if not cached:
            cached = getattr(usage, "prompt_cache_hit_tokens", 0) or 0
        cached = min(cached, input_tokens)
        if cached:
            usage_dict["cache_read_input_tokens"] = cached
        pricing = self._get_pricing("Moonshot", self.model)
        if pricing:
            hit_rate = pricing["input"]  # fall back to the miss rate
            best_len = 0
            for prefix, rate in KIMI_CACHE_HIT_PRICING.items():
                if (self.model or "").startswith(prefix) and len(prefix) > best_len:
                    hit_rate = rate / 1_000_000
                    best_len = len(prefix)
            usage_dict["cost_usd"] = (
                (input_tokens - cached) * pricing["input"]
                + cached * hit_rate
                + output_tokens * pricing["output"])
        return usage_dict

    def _stream_kimi_events(self, api_kwargs, label_emitted):
        """One streaming pass over the Kimi Chat Completions endpoint.
        Returns (full_text, stop_reason, content_blocks, had_thinking,
        label_emitted, usage_dict) — accumulator state is local so a retry
        starts clean."""
        full_text = ""
        thinking_text = ""
        had_thinking = False
        in_thinking = False
        tool_calls_acc = {}  # tool_calls index -> {id, name, arguments}
        finish_reason = None
        usage_dict = None
        timed_out = False
        first_content_timeout = getattr(self, "_kimi_first_content_timeout", 0)

        stream = self.kimi_client.chat.completions.create(**api_kwargs)
        try:
            for chunk in stream:
                if self.stop_requested:
                    if hasattr(self, "_kimi_first_content"):
                        self._kimi_first_content.set()
                    break
                if (first_content_timeout and hasattr(self, "_kimi_first_content")
                        and not self._kimi_first_content.is_set()
                        and time.time() - self._kimi_stream_start >= first_content_timeout):
                    self._kimi_first_content.set()
                    timed_out = True
                    break

                # The final chunk (stream_options include_usage) carries usage
                # with an empty choices list — read it wherever it appears.
                usage = getattr(chunk, "usage", None)
                if usage:
                    usage_dict = self._kimi_usage_dict(usage)

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue
                choice = choices[0]
                fr = getattr(choice, "finish_reason", None)
                if fr:
                    finish_reason = fr
                delta = getattr(choice, "delta", None)
                if delta is None:
                    continue

                # reasoning_content is Moonshot's extra delta field — the SDK's
                # pydantic models keep unknown fields, so plain getattr works.
                # It always streams BEFORE content on thinking models.
                reasoning_piece = getattr(delta, "reasoning_content", None)
                if reasoning_piece:
                    if hasattr(self, "_kimi_first_content"):
                        self._kimi_first_content.set()
                    if not in_thinking:
                        in_thinking = True
                        had_thinking = True
                        self.queue.put({"type": "thinking_start"})
                    thinking_text += reasoning_piece
                    self.queue.put({"type": "thinking_delta",
                                    "content": reasoning_piece})

                text_piece = getattr(delta, "content", None)
                if text_piece:
                    if hasattr(self, "_kimi_first_content"):
                        self._kimi_first_content.set()
                    if in_thinking:
                        self.queue.put({"type": "thinking_end"})
                        in_thinking = False
                    if not label_emitted:
                        self.queue.put({"type": "label"})
                        label_emitted = True
                    full_text += text_piece
                    self.queue.put({"type": "text_delta", "content": text_piece})

                for tc in (getattr(delta, "tool_calls", None) or []):
                    if hasattr(self, "_kimi_first_content"):
                        self._kimi_first_content.set()
                    idx = getattr(tc, "index", None)
                    if idx is None:
                        idx = len(tool_calls_acc)
                    acc = tool_calls_acc.setdefault(
                        idx, {"id": "", "name": "", "arguments": ""})
                    if getattr(tc, "id", None):
                        acc["id"] = tc.id
                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        if getattr(fn, "name", None):
                            acc["name"] = fn.name
                        if getattr(fn, "arguments", None):
                            acc["arguments"] += fn.arguments
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
        if finish_reason == "length" and not tool_calls_acc:
            self.queue.put({"type": "warning", "content":
                            "⚠ Kimi hit the max_completion_tokens cap "
                            "(finish_reason=length) — the answer may be "
                            "truncated.\n"})

        content_blocks = []
        if thinking_text:
            # Stored FIRST in the internal history so _messages_to_kimi can
            # round-trip it as reasoning_content on the next call — required
            # for k3 / k2.7-code / k2.6 tool loops (and the dict shape
            # chat_mixin already knows how to serialize).
            content_blocks.append({"type": "thinking", "thinking": thinking_text})
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
                "id": tc["id"] or f"kimi_call_{idx}",
                "name": tc["name"],
                "input": parsed_args,
            })

        return full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict

    def _stream_kimi_call(self, messages, max_retries, label_emitted):
        """Execute one Kimi Chat Completions call with streaming and retry
        logic. Returns (stop_reason, content_blocks, full_text, had_thinking,
        label_emitted, usage) — same 6-tuple as the other provider callers."""
        usage_dict = None
        system_prompt = self._build_system_prompt()
        tools = self._get_tools()
        kimi_tools = self._tools_to_kimi(tools) if tools else []

        include_reasoning = self._kimi_include_reasoning()
        wire_params = self._kimi_model_params()

        def _build_messages(include):
            return ([{"role": "system", "content": system_prompt}]
                    + self._messages_to_kimi(messages, include_reasoning=include))

        api_kwargs = {
            "model": self.model,
            "messages": _build_messages(include_reasoning),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if kimi_tools:
            api_kwargs["tools"] = kimi_tools
        # Split the flat wire params into typed kwargs vs extra_body: the
        # openai SDK has no thinking / reasoning_effort params for
        # chat.completions, but extra_body merges verbatim into the JSON.
        extra_body = {}
        for key, value in wire_params.items():
            if key == "max_completion_tokens":
                api_kwargs["max_completion_tokens"] = value
            else:
                extra_body[key] = value
        if extra_body:
            api_kwargs["extra_body"] = extra_body

        FIRST_CONTENT_TIMEOUT = 180
        WAITING_MSG_INTERVAL = 15

        self._kimi_first_content = threading.Event()
        self._kimi_stream_start = time.time()
        self._kimi_first_content_timeout = FIRST_CONTENT_TIMEOUT

        def _waiting_ticker():
            while not self._kimi_first_content.wait(timeout=WAITING_MSG_INTERVAL):
                elapsed = int(time.time() - self._kimi_stream_start)
                self.queue.put({
                    "type": "tool_info",
                    "content": f"Waiting for model response... ({elapsed}s elapsed)\n",
                })
        ticker = threading.Thread(target=_waiting_ticker, daemon=True)
        ticker.start()

        # stop_reason doubles as the success sentinel (xAI pattern): the
        # downgrade ladder retries via `continue`, so the loop can exhaust all
        # attempts without a break or raise — guard the unbound corner.
        stop_reason = None
        full_text, content_blocks, had_thinking = "", [], False
        for attempt in range(max_retries):
            try:
                full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict = \
                    self._stream_kimi_events(api_kwargs, label_emitted)
                break  # success
            except openai.AuthenticationError as e:
                raise RuntimeError(
                    "Kimi (Moonshot) rejected the API key — check the "
                    "MOONSHOT_API_KEY (or KIMI_API_KEY) environment variable. "
                    f"Original error: {e}"
                ) from e
            except openai.BadRequestError as e:
                # Parameter-downgrade ladder: each branch adjusts api_kwargs
                # and retries via `continue` (consuming an attempt), so
                # successive 400s converge instead of hard-failing.
                err_str = str(e)
                eb = api_kwargs.get("extra_body", {})
                # reasoning_effort first — it contains the "reasoning"
                # substring and is only ever sent for kimi-k3.
                if "reasoning_effort" in err_str and "reasoning_effort" in eb:
                    del eb["reasoning_effort"]
                    self.queue.put({
                        "type": "tool_info",
                        "content": "Model rejected reasoning_effort — retrying without it...\n",
                    })
                    continue
                # reasoning_content round-trip refused: learn once per model,
                # strip reasoning from the history, retry. The backstop for a
                # model whose Preserved Thinking support the static table
                # mispredicts.
                if "reasoning_content" in err_str and include_reasoning:
                    include_reasoning = False
                    rejected = getattr(self, "_kimi_reasoning_rejected", None)
                    if rejected is None:
                        rejected = self._kimi_reasoning_rejected = set()
                    rejected.add(self.model)
                    api_kwargs["messages"] = _build_messages(False)
                    self.queue.put({
                        "type": "tool_info",
                        "content": ("Model rejected reasoning_content round-trip — "
                                    "retrying without sending reasoning back...\n"),
                    })
                    continue
                # thinking param refused: drop keep first (a model that takes
                # the toggle but not Preserved Thinking), then the whole param.
                if "thinking" in err_str and "thinking" in eb:
                    if isinstance(eb["thinking"], dict) and "keep" in eb["thinking"]:
                        eb["thinking"] = {k: v for k, v in eb["thinking"].items()
                                          if k != "keep"}
                        self.queue.put({
                            "type": "tool_info",
                            "content": ("Model rejected thinking.keep — retrying "
                                        "with the plain thinking toggle...\n"),
                        })
                    else:
                        del eb["thinking"]
                        self.queue.put({
                            "type": "tool_info",
                            "content": ("Model does not support the thinking "
                                        "parameter — retrying without it...\n"),
                        })
                    continue
                # Legacy param name fallback.
                if "max_completion_tokens" in err_str and "max_completion_tokens" in api_kwargs:
                    api_kwargs["max_tokens"] = api_kwargs.pop("max_completion_tokens")
                    self.queue.put({
                        "type": "tool_info",
                        "content": ("Model rejected max_completion_tokens — "
                                    "retrying with legacy max_tokens...\n"),
                    })
                    continue
                raise
            except openai.APITimeoutError:
                self._kimi_first_content = threading.Event()
                self._kimi_stream_start = time.time()
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
        self._kimi_first_content.set()
        if stop_reason is None:
            raise RuntimeError("Kimi call failed: retries exhausted without a successful response")
        return stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict
