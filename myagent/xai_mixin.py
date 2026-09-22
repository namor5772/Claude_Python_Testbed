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
2. **Server-side tools, verified mixable** — live probes (2026-07-05) proved
   xAI's ``web_search`` / ``x_search`` / ``code_interpreter`` built-ins mix
   cleanly with client-side function tools in one request (the model routes
   correctly with both present), so xAI follows the OpenAI pattern: the local
   DuckDuckGo ``web_search`` / ``fetch_webpage`` tools are stripped in
   ``_get_tools()`` and the built-ins are appended here. Each server-tool
   invocation bills a flat $0.005 (folded into ``cost_in_usd_ticks``).
   ``code_interpreter`` is gated off while desktop tools are on, mirroring
   the OpenAI rationale (a CI-equipped model inspects screenshot bytes and
   pre-scales coordinates, colliding with our scaling). A 400 naming a
   server tool strips it and retries, so models that reject a built-in
   degrade gracefully.
3. **Reasoning knob per model, read from the listing** — ``/v1/language-models``
   (the listing ``_fetch_xai_models`` reads since 2026-09-23; its ``/v1/models``
   twin carries ids alone) publishes each model's ``capabilities.reasoning_effort``
   and ``input_modalities``, plus its aliases. The parsed records (``_xai_caps``:
   model id AND every alias → ladder + vision flag) answer
   ``_xai_reasoning_values`` / ``_is_xai_vision_model`` first; the static
   ``XAI_REASONING_EFFORT`` / ``XAI_NON_VISION_PREFIXES`` tables (longest
   prefix wins) answer only for a model the listing did not describe — a
   failed fetch, or a model listed without a capabilities block (the
   grok-4.20 variants). Live 2026-09-23: grok-4.3 none..xhigh, grok-4.5 /
   4.6 / 4.7 low..xhigh (always-reasoning — "none" is HTTP 400),
   grok-4.20-multi-agent low..xhigh from the table (the knob is agent
   collaboration count). ``_xai_effective_effort`` is the ONE coercion of a
   stale saved effort onto the model's ladder (nearest rung, OpenAI's rule),
   shared by the request builder, the Debug dump, the Reasoning combobox and
   the title. Every request carries ``reasoning.summary: "auto"`` — with an
   ``effort`` only for a knob model — because the knobless pinned
   ``-reasoning`` variant and grok-build-0.1 DO reason and stream their
   summaries when asked (probed 2026-09-23), while the ``-non-reasoning``
   variant simply streams none. Reasoning deltas arrive as
   ``response.reasoning_text.delta`` or ``response.reasoning_summary_text.delta``
   depending on model; both feed the Show Thinking pane.
4. **Temperature alongside reasoning** — xAI accepts both (Gemini-style).
   ``_xai_model_params`` is the ONE builder of the temperature + reasoning
   params behind both the live request and the Debug payload, so the dump
   cannot drift from the wire. A BadRequest mentioning temperature or
   reasoning downgrades the request (drop temperature → drop reasoning
   summary → drop reasoning) and retries, so a future API tightening
   degrades gracefully instead of hard-failing.
"""
import json
import time
import threading

import httpx
import openai

from myagent.constants import (
    XAI_EFFORT_LADDER,
    XAI_FALLBACK_MODELS,
    XAI_NON_AGENTIC_SUBSTRINGS,
    XAI_NON_VISION_PREFIXES,
    XAI_REASONING_EFFORT,
    _HAS_DESKTOP,
)
from myagent.helpers import responses_usage_dict
from myagent.openai_mixin import OpenAIMixin
from myagent.retry_util import rate_limit_backoff, server_error_backoff


class XAIMixin:

    def _xai_model_caps(self):
        """The per-model records the last successful /v1/language-models
        fetch parsed (_fetch_xai_models): the model id AND each of its
        aliases → {"reasoning_effort": ladder | None, "vision": bool}. Empty
        until a fetch succeeds, and after one fails — the static tables
        answer then."""
        return getattr(self, "_xai_caps", None) or {}

    @staticmethod
    def _parse_xai_language_models(payload):
        """(model_ids, caps) from a /v1/language-models body. Keeps the grok*
        ids that can serve the agentic loop (XAI_NON_AGENTIC_SUBSTRINGS
        dropped), sorted; caps maps each kept id and every alias it lists to
        its record — a ladder ordered by XAI_EFFORT_LADDER when the entry
        carries capabilities.reasoning_effort, else None (unadvertised: the
        static table decides, see _xai_reasoning_values), and whether
        input_modalities includes image."""
        model_ids, caps = [], {}
        for entry in (payload or {}).get("models") or []:
            mid = entry.get("id") or ""
            if not mid.startswith("grok"):
                continue
            if any(skip in mid for skip in XAI_NON_AGENTIC_SUBSTRINGS):
                continue
            ladder = (entry.get("capabilities") or {}).get("reasoning_effort") or None
            if ladder:
                ladder = sorted((str(v).lower() for v in ladder),
                                key=lambda v: (XAI_EFFORT_LADDER.index(v)
                                               if v in XAI_EFFORT_LADDER
                                               else len(XAI_EFFORT_LADDER)))
            record = {"reasoning_effort": ladder,
                      "vision": "image" in (entry.get("input_modalities") or [])}
            model_ids.append(mid)
            caps[mid] = record
            for alias in entry.get("aliases") or []:
                caps.setdefault(alias, record)
        model_ids.sort()
        return model_ids, caps

    def _xai_reasoning_values(self, model_id=None):
        """Accepted reasoning.effort values for a Grok model, or None when the
        model has no client-side knob. The live listing's ladder when it
        published one for this id (or for the model this id is an alias
        of); else XAI_REASONING_EFFORT by longest prefix — so
        grok-4.20-multi-agent-0309 (listed without capabilities) keeps its
        table entry and does not fall through to a shorter one."""
        mid = model_id or self.model or ""
        live = self._xai_model_caps().get(mid)
        if live and live.get("reasoning_effort"):
            return list(live["reasoning_effort"])
        best = None
        best_len = 0
        for prefix, values in XAI_REASONING_EFFORT.items():
            if mid.startswith(prefix) and len(prefix) > best_len:
                best = values
                best_len = len(prefix)
        return best

    def _xai_nearest_effort(self, requested, model_id=None):
        """`requested` mapped onto the rungs the model accepts — OpenAI's
        nearest-rung rule (_openai_nearest_effort): a supported value passes
        through, a Claude Off / Adaptive becomes None where it exists
        (grok-4.3) else the floor, a Max steps down to Xhigh, a None on an
        always-reasoning tier steps up to Low, anything unknown takes the
        floor. None when the model has no knob."""
        values = self._xai_reasoning_values(model_id)
        if not values:
            return None
        return OpenAIMixin._openai_nearest_effort(requested, values)

    def _xai_effective_effort(self, model_id=None):
        """The reasoning.effort that goes on the wire for the current model —
        the saved effort when the model accepts it, else the nearest rung it
        does — or None for a knobless model (no effort is sent). The ONE
        coercion behind the request builder (_xai_model_params), the Debug
        dump, the Reasoning combobox and the title."""
        return self._xai_nearest_effort(getattr(self, "thinking_effort", ""), model_id)

    def _xai_model_params(self):
        """The temperature + reasoning params for the current Grok model —
        the ONE builder behind both the live request (_stream_xai_call) and
        the Debug payload (stream_worker's dump), so the dump cannot drift
        from the wire. Temperature always (xAI takes it alongside reasoning);
        reasoning.summary "auto" always — the knobless pinned -reasoning
        variant and grok-build reason too, and stream their summaries only
        when asked — with an effort for a knob model. The 400 ladder in
        _stream_xai_call drops each in turn if a model refuses."""
        reasoning = {}
        effort = self._xai_effective_effort()
        if effort:
            reasoning["effort"] = effort
        reasoning["summary"] = "auto"
        return {"temperature": self.temperature, "reasoning": reasoning}

    def _is_xai_vision_model(self, model_id=None):
        """Whether the model takes image input: the live listing's
        input_modalities when it described this id (or its alias target),
        else not a XAI_NON_VISION_PREFIXES family — a tuple empty since
        2026-09-23, every served Grok language model being vision-capable."""
        mid = model_id or self.model or ""
        live = self._xai_model_caps().get(mid)
        if live is not None:
            return bool(live.get("vision"))
        return not mid.startswith(XAI_NON_VISION_PREFIXES)

    def _fetch_xai_models(self):
        """List Grok language models from api.x.ai's /v1/language-models —
        the listing that publishes each model's reasoning knob, input
        modalities and aliases (its /v1/models twin carries ids alone) —
        dropping non-agentic entries by substring, and keep the parsed
        records on the instance (_xai_caps) for _xai_reasoning_values /
        _is_xai_vision_model. A failed or empty fetch clears them, so the
        static tables answer, and serves XAI_FALLBACK_MODELS."""
        self._xai_caps = {}
        self._xai_model_display_names = {}
        if not getattr(self, "xai_client", None):
            return list(XAI_FALLBACK_MODELS)
        try:
            # The SDK's own transport (key, base URL, timeouts, retries) on
            # an endpoint it has no method for: cast_to=httpx.Response hands
            # back the raw response, and a 4xx / 5xx raises as usual.
            response = self.xai_client.get("/language-models", cast_to=httpx.Response)
            model_ids, caps = self._parse_xai_language_models(response.json())
        except Exception:
            return list(XAI_FALLBACK_MODELS)
        if not model_ids:
            return list(XAI_FALLBACK_MODELS)
        self._xai_caps = caps
        self._xai_model_display_names = {mid: mid for mid in model_ids}
        return model_ids

    @staticmethod
    def _xai_usage_dict(usage):
        """Normalize a Responses-API usage object into stream_worker's buckets.

        xAI speaks OpenAI's Responses usage shape — cached_tokens a SUBSET of
        input_tokens — so the disjoint-bucket subtraction is the shared
        helpers.responses_usage_dict (the 2026-09-15 grok-4.6 row in
        tests/test_cached_usage.py is the proof it is a subset: the table
        rates reproduce its authoritative cost only under that reading).

        xAI extras (the SDK's pydantic models keep unknown fields, so plain
        getattr works): cost_in_usd_ticks is the AUTHORITATIVE billed cost
        (1 tick = $1e-10) including the cache discount and the flat $0.005
        per server-tool invocation — stream_worker prefers it over the table
        estimate; num_server_side_tools_used feeds the activity notice."""
        usage_dict = responses_usage_dict(usage)
        ticks = getattr(usage, "cost_in_usd_ticks", None)
        if isinstance(ticks, (int, float)) and ticks >= 0:
            usage_dict["cost_usd"] = ticks / 1e10
        server_calls = getattr(usage, "num_server_side_tools_used", 0) or 0
        if server_calls:
            usage_dict["server_tool_calls"] = server_calls
        return usage_dict

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
                    itype = getattr(item, "type", None) if item else None
                    if itype == "function_call":
                        idx = getattr(event, "output_index", len(tool_calls_acc))
                        tool_calls_acc[idx] = {
                            "call_id": item.call_id,
                            "name": item.name,
                            "arguments": "",
                        }
                    # Server-side tool activity — executed on xAI's servers,
                    # surfaced as an activity notice only. x_search arrives
                    # as a generic custom_tool_call item (observed live).
                    elif itype == "web_search_call":
                        self._tool_info("Searching the web (xAI server-side)...\n")
                    elif itype == "code_interpreter_call":
                        self._tool_info("Running code interpreter (xAI server-side)...\n")
                    elif itype == "custom_tool_call":
                        name = getattr(item, "name", "") or "x_search"
                        self._tool_info(f"Running xAI server-side tool ({name})...\n")

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
                        # Disjoint buckets + the xAI extras (cost_usd,
                        # server_tool_calls) — see _xai_usage_dict.
                        usage_dict = self._xai_usage_dict(usage)

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
        # xAI server-side built-ins — mixing with custom function tools
        # verified live 2026-07-05 (correct routing with both present).
        # Flat $0.005/invocation, already folded into cost_in_usd_ticks.
        # _get_tools() strips the local web_search/fetch_webpage for xAI so
        # search flows through these instead (the OpenAI pattern).
        responses_tools.append({"type": "web_search"})
        responses_tools.append({"type": "x_search"})
        # Code interpreter is gated off when desktop tools are on — same
        # rationale as OpenAI: a CI-equipped model loads screenshot bytes,
        # sees resized dimensions, and pre-scales click coordinates.
        if not (self.desktop_enabled.get() and _HAS_DESKTOP):
            responses_tools.append({"type": "code_interpreter"})

        api_kwargs = {
            "model": self.model,
            "input": self._messages_to_responses(messages),
            "instructions": system_prompt,
            "store": False,
        }
        # temperature + reasoning from the ONE builder the Debug dump also
        # uses (a stale saved effort → the nearest rung the model accepts;
        # the summary asked of every model); the BadRequest ladder below
        # drops each in turn if a model refuses.
        api_kwargs.update(self._xai_model_params())
        if responses_tools:
            api_kwargs["tools"] = responses_tools

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
                # A 400 naming a server-side built-in (e.g. a model that
                # doesn't support code_interpreter) strips just that tool.
                # Checked before the generic "reasoning" branch because tool
                # names are more specific than that substring.
                rejected = [t for t in ("web_search", "x_search", "code_interpreter")
                            if t in err_str and any(
                                d.get("type") == t for d in api_kwargs.get("tools", []))]
                if rejected:
                    api_kwargs["tools"] = [
                        d for d in api_kwargs["tools"] if d.get("type") not in rejected]
                    self.queue.put({
                        "type": "tool_info",
                        "content": (f"Model rejected server-side tool(s) "
                                    f"{', '.join(rejected)} — retrying without...\n"),
                    })
                    continue
                if "reasoning" in err_str.lower() and "reasoning" in api_kwargs:
                    r = api_kwargs["reasoning"]
                    if isinstance(r, dict) and "summary" in r:
                        # First downgrade: some models may reject the summary
                        # request while accepting the effort itself. On a
                        # knobless model the summary is all there was, so
                        # the whole parameter goes rather than an empty {}.
                        stripped = {k: v for k, v in r.items() if k != "summary"}
                        if stripped:
                            api_kwargs["reasoning"] = stripped
                            notice = "Model rejected reasoning summary — retrying with effort only...\n"
                        else:
                            del api_kwargs["reasoning"]
                            notice = "Model rejected the reasoning summary — retrying without it...\n"
                        self.queue.put({"type": "tool_info", "content": notice})
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
        if usage_dict and usage_dict.get("server_tool_calls"):
            n = usage_dict["server_tool_calls"]
            self._tool_info(
                f"xAI server-side tools used this call: {n} "
                f"(flat $0.005 each, included in the cost line)\n")
        return stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict
