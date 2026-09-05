import json
import re
import time
import threading

import openai

from myagent.constants import (
    OPENAI_DEPRECATED_MODEL_IDS,
    OPENAI_DEPRECATED_MODEL_PREFIXES,
    OPENAI_PRICING,
    OPENAI_REASONING_PREFIXES,
    OPENAI_RESPONSES_PREFIXES,
    OPENAI_FALLBACK_MODELS,
    _HAS_DESKTOP,
    resolve_price,
)
from myagent.retry_util import rate_limit_backoff, server_error_backoff


class OpenAIMixin:

    @staticmethod
    def _openai_usage_dict(usage, cache_write_billed=False):
        """Normalize a Responses-API usage object into stream_worker's buckets.

        OpenAI caches AUTOMATICALLY above ~1024 tokens — no client opt-in, so
        unlike Anthropic (see anthropic_mixin's cache_control block) there was
        never a discount to switch on, only one to report.

        The subtraction is the load-bearing part: OpenAI reports the hit under
        input_tokens_details.cached_tokens as a SUBSET of input_tokens, whereas
        Anthropic's buckets are disjoint. stream_worker prices input at the full
        rate AND cache_read at the cached rate, so handing it the raw
        overlapping totals would double-charge every cached token. Verified live
        2026-07-31: input_tokens=2714 with cached_tokens=2711 inside it.

        `cache_write_tokens` is a subset of input_tokens too (verified live on
        gpt-6-astra 2026-09-06: 2420 of 2423 written on a first call, read back
        on the repeat). Whether it leaves the input bucket depends on the model:
        the GPT-5 / 4.1 families do not bill writes (no column on the pricing
        page — a written token is ordinary full-rate input), so it stays put
        and no cache_creation key is emitted. GPT-6 Astra bills writes at 1.25x
        ($12.50/M), so with ``cache_write_billed`` (the caller decides from the
        pricing row — _openai_bills_cache_writes) the written tokens move to a
        disjoint ``cache_creation_input_tokens`` bucket for stream_worker's
        cache_write rate, exactly like Anthropic's.

        Returns None when there is no usage to report. Dict fallbacks cover
        older/looser SDK shapes.
        """
        if not usage:
            return None
        total_in = getattr(usage, "input_tokens", 0) or 0
        details = getattr(usage, "input_tokens_details", None)

        def _detail(name):
            if details is None:
                return 0
            return (getattr(details, name, None)
                    or (details.get(name) if isinstance(details, dict) else None)
                    or 0)

        cached = min(_detail("cached_tokens"), total_in)  # never let a bad report go negative
        out = {
            "input_tokens": total_in - cached,
            "output_tokens": getattr(usage, "output_tokens", 0) or 0,
            "cache_read_input_tokens": cached,
        }
        if cache_write_billed:
            written = min(_detail("cache_write_tokens"), total_in - cached)
            out["input_tokens"] -= written
            out["cache_creation_input_tokens"] = written
        return out

    def _openai_bills_cache_writes(self, model_id=None):
        """True when the model's OPENAI_PRICING row carries a cache-write rate
        (a 4th element — GPT-6 Astra). Decided from the pricing table itself so
        the usage normalizer and the cost accumulator can never disagree about
        whether written tokens leave the input bucket."""
        mid = model_id or self.model or ""
        best, best_len = None, 0
        for prefix, prices in OPENAI_PRICING.items():
            if mid.startswith(prefix) and len(prefix) > best_len:
                best, best_len = prices, len(prefix)
        if best is None:
            return False
        entry = resolve_price(best)
        return len(entry) > 3 and entry[3] is not None

    def _stream_responses(self, api_kwargs, label_emitted):
        """Stream an OpenAI Responses API call, accumulating text and tool calls.
        Returns (full_text, stop_reason, content_blocks, had_thinking, label_emitted)."""
        full_text = ""
        had_thinking = False
        tool_calls_acc = {}  # output_index -> {call_id, name, arguments}
        ci_code_acc = ""     # accumulate code interpreter code deltas
        in_thinking = False

        # Waiting ticker & timeout are managed by _stream_responses_call via _oai_first_content
        timed_out = False
        first_content_timeout = getattr(self, '_oai_first_content_timeout', 0)
        with self.openai_client.responses.stream(**api_kwargs) as stream:
            for event in stream:
                # Check stop request — break out of stream immediately
                if self.stop_requested:
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    break
                # Check first-content timeout
                if (first_content_timeout and hasattr(self, '_oai_first_content')
                        and not self._oai_first_content.is_set()
                        and time.time() - self._oai_stream_start >= first_content_timeout):
                    self._oai_first_content.set()
                    timed_out = True
                    break
                # Reasoning summary deltas (thinking)
                if event.type == "response.reasoning_summary_text.delta":
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    if not in_thinking:
                        in_thinking = True
                        had_thinking = True
                        self.queue.put({"type": "thinking_start"})
                    self.queue.put({"type": "thinking_delta", "content": event.delta})

                elif event.type == "response.reasoning_summary_part.done":
                    if in_thinking:
                        self.queue.put({"type": "thinking_end"})
                        in_thinking = False

                # Regular text content
                elif event.type == "response.output_text.delta":
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    if in_thinking:
                        self.queue.put({"type": "thinking_end"})
                        in_thinking = False
                    if not label_emitted:
                        self.queue.put({"type": "label"})
                        label_emitted = True
                    full_text += event.delta
                    self.queue.put({"type": "text_delta", "content": event.delta})

                # New output item — capture function call name and call_id
                elif event.type == "response.output_item.added":
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", None) == "function_call":
                        tool_calls_acc[event.output_index] = {
                            "call_id": item.call_id,
                            "name": item.name,
                            "arguments": "",
                        }
                    elif item and getattr(item, "type", None) == "web_search_call":
                        self._tool_info("Searching the web...\n")
                    elif item and getattr(item, "type", None) == "code_interpreter_call":
                        self._tool_info("Running code interpreter...\n")

                # Function call argument chunks
                elif event.type == "response.function_call_arguments.delta":
                    idx = event.output_index
                    if idx in tool_calls_acc:
                        tool_calls_acc[idx]["arguments"] += event.delta

                # Function call arguments complete
                elif event.type == "response.function_call_arguments.done":
                    idx = event.output_index
                    if idx in tool_calls_acc:
                        tool_calls_acc[idx]["arguments"] = event.arguments

                # Code interpreter — accumulate code deltas
                elif event.type == "response.code_interpreter_call_code.delta":
                    if hasattr(self, '_oai_first_content'):
                        self._oai_first_content.set()
                    ci_code_acc += event.delta

                # Code interpreter — code complete, display full code block
                elif event.type == "response.code_interpreter_call_code.done":
                    if ci_code_acc.strip():
                        self.queue.put({"type": "ci_code", "content": ci_code_acc})
                    ci_code_acc = ""

                # Code interpreter — completed, extract logs and images from outputs
                elif event.type == "response.output_item.done":
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", None) == "code_interpreter_call":
                        outputs = getattr(item, "outputs", []) or []
                        for r in outputs:
                            rtype = getattr(r, "type", None) or (r.get("type") if isinstance(r, dict) else None)
                            if rtype == "logs":
                                logs = getattr(r, "logs", "") or (r.get("logs", "") if isinstance(r, dict) else "")
                                if logs:
                                    self._tool_info(logs + "\n")
                            elif rtype == "image":
                                # Image URL can be directly on the result or nested under .image
                                url = getattr(r, "url", "") or (r.get("url", "") if isinstance(r, dict) else "")
                                if not url:
                                    img_obj = getattr(r, "image", None) or (r.get("image") if isinstance(r, dict) else None)
                                    if img_obj:
                                        url = getattr(img_obj, "url", "") or (img_obj.get("url", "") if isinstance(img_obj, dict) else "")
                                if url:
                                    self.queue.put({"type": "ci_image", "url": url, "file_id": ""})

        if timed_out:
            raise openai.APITimeoutError(request=None)  # type: ignore[arg-type]

        # End any open thinking block
        if in_thinking:
            self.queue.put({"type": "thinking_end"})

        # Determine stop reason
        stop_reason = "end_turn"
        if tool_calls_acc:
            stop_reason = "tool_use"

        # Build content blocks in Anthropic-like format
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

        # Extract usage for cost tracking
        usage_dict = None
        try:
            final_resp = stream.get_final_response()
            usage_dict = self._openai_usage_dict(
                getattr(final_resp, "usage", None),
                cache_write_billed=self._openai_bills_cache_writes())
        except Exception:
            pass

        return full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict

    def _fetch_openai_models(self):
        """Fetch available OpenAI chat models suitable for agentic tool use."""
        if not self.openai_client:
            return list(OPENAI_FALLBACK_MODELS)
        try:
            response = self.openai_client.models.list()
            model_ids = []
            for m in response.data:
                mid = m.id
                # Skip non-chat model types
                if any(skip in mid for skip in ("embedding", "audio", "search",
                                                "realtime", "preview",
                                                "transcribe", "tts")):
                    continue
                # Include only Responses API compatible models
                if not mid.startswith(OPENAI_RESPONSES_PREFIXES):
                    continue
                # Drop scheduled-retirement ids that still pass the family
                # prefixes (GPT-5.0 base tiers, -chat/-codex ids — 2026-07
                # audit: retiring models are removed ahead of shutdown)
                if (mid.startswith(OPENAI_DEPRECATED_MODEL_PREFIXES)
                        or mid in OPENAI_DEPRECATED_MODEL_IDS):
                    continue
                model_ids.append(mid)
            model_ids.sort()
            self._openai_model_display_names = {mid: mid for mid in model_ids}
            return model_ids if model_ids else list(OPENAI_FALLBACK_MODELS)
        except Exception:
            self._openai_model_display_names = {}
            return list(OPENAI_FALLBACK_MODELS)

    def _fetch_models_for_provider(self):
        """Fetch models for the current provider."""
        if self.provider == "OpenAI":
            return self._fetch_openai_models()
        if self.provider == "Google":
            return self._fetch_gemini_models()
        if self.provider == "xAI":
            return self._fetch_xai_models()
        if self.provider == "Moonshot":
            return self._fetch_kimi_models()
        if self.provider == "Ollama":
            return self._fetch_ollama_models()
        return self._fetch_available_models()

    def _is_openai_reasoning_model(self, model_id=None):
        """Check if the model is an OpenAI reasoning model (o-series or gpt-5+)."""
        mid = model_id or self.model
        # gpt-5.x-chat-* variants are non-reasoning "instant" models
        if "-chat" in mid:
            return False
        return any(mid.startswith(p) for p in OPENAI_REASONING_PREFIXES)

    def _parse_gpt5_minor(self, model_id=None):
        """Parse minor version from gpt-5.x model IDs. Returns 0 for 'gpt-5' base."""
        mid = model_id or self.model
        if mid.startswith("gpt-5."):
            try:
                return int(mid[6:].split('-')[0].split('.')[0])
            except (IndexError, ValueError):
                return 0
        return 0

    def _is_gpt5_family(self, model_id=None):
        """Check if model is in the gpt-5 family (not -chat variants)."""
        mid = model_id or self.model
        return mid.startswith("gpt-5") and "-chat" not in mid

    def _has_reasoning_none(self, model_id=None):
        """Check if model supports reasoning.effort='none' (gpt-5.1+)."""
        mid = model_id or self.model
        return self._is_gpt5_family(mid) and self._parse_gpt5_minor(mid) >= 1

    def _is_gpt6_family(self, model_id=None):
        """GPT-6 family — gpt-6-astra (2026-09-03) and any later gpt-6.x tier.
        Anchored so a hypothetical "gpt-60" can't match, and a -chat Instant
        variant is excluded like _is_gpt5_family."""
        mid = model_id or self.model or ""
        return bool(re.match(r"gpt-6(?:[.-]|$)", mid)) and "-chat" not in mid

    def _openai_always_reasoning(self, model_id=None):
        """Models whose reasoning cannot be switched off: reasoning.effort is
        low/medium/high/xhigh/max ONLY ("none" and "minimal" are HTTP 400) and
        temperature is rejected unconditionally — the GPT-6 family, probed live
        on gpt-6-astra 2026-09-06. They get the Reasoning combobox without a
        None rung, and _stream_responses_call always sends `reasoning` and
        never `temperature`."""
        return self._is_gpt6_family(model_id)

    def _openai_effective_effort(self):
        """The reasoning.effort to send for an always-reasoning model: the
        current effort, or the ladder floor "low" when the saved value is a
        rung the model lacks (a "none" / "minimal" carried over from a GPT-5.x
        instruction — headless runs never pass through the combobox coercion
        in ui_mixin). The reactive 400 rung in _stream_responses_call remains
        the backstop for anything else."""
        effort = (getattr(self, "thinking_effort", "") or "").lower()
        if effort in ("none", "minimal", "off", "adaptive", ""):
            return "low"
        return effort

    def _openai_model_params(self, commit=False):
        """The reasoning / temperature / text.verbosity params for the current
        OpenAI model — the ONE builder behind both the live request
        (_stream_responses_call, commit=True) and the Debug payload
        (_payload_for_display, commit=False), so the dump can never drift
        from the wire (it had: the dump never showed text.verbosity, and the
        gpt-5.1+ none/temperature detail was approximated — caught by
        tests/test_gpt6_params.py, 2026-09-06).

        Per family: the always-reasoning GPT-6 tiers send `reasoning` only,
        never temperature, with a stale rung floor-coerced to "low"
        (_openai_effective_effort); GPT-5.1+ always send `reasoning` (even
        "none") and, at none, temperature (the user's on 5.4+, fixed 1.0
        before); GPT-5.0 sends reasoning when enabled and temperature 1.0;
        the o-series reasoning when enabled; non-reasoning ids temperature
        except the -chat Instant variants; every gpt-5 / gpt-6 id adds
        text.verbosity.

        Returns (params, notice): `notice` is the one-shot Activity line for a
        coerced GPT-6 effort. With commit=True the coerced value is written
        back to thinking_effort / thinking_mode / thinking_enabled so the
        title and cost-log params report what was sent (and the notice does
        not repeat on the run's later calls); the display path never
        mutates state."""
        params = {}
        notice = None
        if self._openai_always_reasoning():
            effort = self._openai_effective_effort()
            if effort != (self.thinking_effort or "").lower():
                notice = (f"{self.model} has no reasoning='{self.thinking_effort}' "
                          f"rung — sending '{effort}' instead.\n")
                if commit:
                    self.thinking_effort = effort
                    self.thinking_mode = effort
                    self.thinking_enabled = True
            params["reasoning"] = {"effort": effort, "summary": "auto"}
        elif self._has_reasoning_none():
            # GPT-5.1+: always send reasoning param, even with effort="none"
            params["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
            if self.thinking_effort == "none":
                # gpt-5.4+ supports user temperature at effort=none; older models fixed at 1.0
                params["temperature"] = (self.temperature if self._gpt5_supports_temp_at_none()
                                         else 1.0)
        elif self._is_gpt5_family():
            # GPT-5.0: always reasoning, temp fixed at 1.0
            if self.thinking_enabled:
                params["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
            params["temperature"] = 1.0
        elif self._is_openai_reasoning_model():
            if self.thinking_enabled:
                params["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
        elif not self._is_gpt5_chat_model():
            # gpt-5.x-chat Instant models don't support temperature
            params["temperature"] = self.temperature
        # Verbosity for all gpt-5 / gpt-6 models (including -chat Instant variants)
        if self._has_openai_verbosity():
            params["text"] = {"verbosity": self.text_verbosity}
        return params, notice

    def _openai_reasoning_values(self, model_id=None):
        """Reasoning-combobox rungs (display labels) for a model that gets the
        extended combobox, mirroring what _stream_responses_call will accept:
        the -pro tiers Medium/High only ('none' and 'low' are HTTP 400); the
        GPT-6 family Low..Max with NO None (always-reasoning — 'none' and
        'minimal' are HTTP 400, probed live 2026-09-06); GPT-5.1+ None/Low/
        Medium/High, plus Xhigh (5.2+ and codex-max, not mini/nano) and Max
        (5.6+). Pure, so the UI gate is unit-testable without Tk."""
        mid = model_id or self.model
        if "-pro" in mid and self._is_gpt5_family(mid):
            values = ["Medium", "High"]
        elif self._openai_always_reasoning(mid):
            values = ["Low", "Medium", "High"]
        else:
            values = ["None", "Low", "Medium", "High"]
        if self._has_reasoning_xhigh(mid):
            values.append("Xhigh")
        if self._has_reasoning_max(mid):
            values.append("Max")
        return values

    def _has_reasoning_xhigh(self, model_id=None):
        """Check if model supports reasoning.effort='xhigh'."""
        mid = model_id or self.model
        if self._is_gpt6_family(mid):
            return True  # gpt-6-astra: low..xhigh..max, probed live 2026-09-06
        if not self._is_gpt5_family(mid):
            return False
        if "codex-max" in mid:
            return True
        # mini/nano variants cap at 'high' — no xhigh
        if "-mini" in mid or "-nano" in mid:
            return False
        return self._parse_gpt5_minor(mid) >= 2

    def _has_reasoning_max(self, model_id=None):
        """Check if model supports reasoning.effort='max' — the GPT-5.6 tiers
        (sol/terra/luna all accept it; gpt-5.5, gpt-5.4 and 5.4-mini reject it
        with "Unsupported value: 'max' is not supported with the ... model",
        probed live 2026-08-25). Version-gated at minor >= 6 so a future 5.7
        keeps it; the reactive 400 handler in _stream_responses_call is the
        backstop if a later tier drops it. The GPT-6 family accepts it too."""
        mid = model_id or self.model
        if self._is_gpt6_family(mid):
            return True
        return self._is_gpt5_family(mid) and self._parse_gpt5_minor(mid) >= 6

    def _gpt5_supports_temp_at_none(self, model_id=None):
        """Check if model supports temperature when reasoning.effort='none' (gpt-5.4+)."""
        mid = model_id or self.model
        return self._is_gpt5_family(mid) and self._parse_gpt5_minor(mid) >= 4

    def _is_gpt5_chat_model(self, model_id=None):
        """Check if model is a gpt-5.x-chat-* Instant variant."""
        mid = model_id or self.model
        return mid.startswith("gpt-5") and "-chat" in mid

    def _has_openai_verbosity(self, model_id=None):
        """Check if model supports text.verbosity (all gpt-5 family including
        -chat, and gpt-6 — accepted by gpt-6-astra, probed live 2026-09-06)."""
        mid = model_id or self.model
        return mid.startswith(("gpt-5", "gpt-6"))

    def _stream_responses_call(self, messages, max_retries, label_emitted):
        """Execute one OpenAI Responses API call with streaming and retry logic.
        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage)."""
        usage_dict = None
        system_prompt = self._build_system_prompt()
        tools = self._get_tools()
        responses_tools = self._tools_to_responses(tools) if tools else []
        responses_tools.append({"type": "web_search_preview"})
        # Code interpreter is actively harmful for desktop click tasks: gpt-5.2
        # uses it to load screenshot bytes via PIL, sees the API-resized image
        # dimensions, and pre-scales coordinates instead of just visually
        # identifying the click target. Strip it whenever Desktop tools are on.
        # For non-desktop tasks (data analysis, math, file processing) it stays.
        if not (self.desktop_enabled.get() and _HAS_DESKTOP):
            responses_tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
        # Strip any server-side tools this model is known to reject (learned
        # from prior 400 errors in this session). Avoids re-hitting the same
        # error on every call.
        unsupported = self._openai_unsupported_tools.get(self.model, set())
        if unsupported:
            responses_tools = [t for t in responses_tools if t.get("type") not in unsupported]
        responses_input = self._messages_to_responses(messages)

        api_kwargs = {
            "model": self.model,
            "input": responses_input,
            "instructions": system_prompt,
            "tools": responses_tools,
            "store": False,
            "include": ["code_interpreter_call.outputs"],
        }
        # reasoning / temperature / text.verbosity — the shared per-family
        # builder (also feeds the Debug payload); commit=True writes a
        # floor-coerced GPT-6 effort back to the live state and hands back
        # the one-shot Activity notice for it.
        params, notice = self._openai_model_params(commit=True)
        if notice:
            self._tool_info(notice)
        api_kwargs.update(params)

        FIRST_CONTENT_TIMEOUT = 180
        WAITING_MSG_INTERVAL = 15

        self._oai_first_content = threading.Event()
        self._oai_stream_start = time.time()
        self._oai_first_content_timeout = FIRST_CONTENT_TIMEOUT

        # Background thread posts elapsed-time messages every 15s until content arrives
        def _waiting_ticker():
            while not self._oai_first_content.wait(timeout=WAITING_MSG_INTERVAL):
                elapsed = int(time.time() - self._oai_stream_start)
                self.queue.put({
                    "type": "tool_info",
                    "content": f"Waiting for model response... ({elapsed}s elapsed)\n",
                })
        ticker = threading.Thread(target=_waiting_ticker, daemon=True)
        ticker.start()

        for attempt in range(max_retries):
            try:
                full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict = \
                    self._stream_responses(api_kwargs, label_emitted)
                break  # success
            except openai.BadRequestError as e:
                err_str = str(e)
                # Some models reject temperature — retry without it
                if "temperature" in err_str and "temperature" in api_kwargs:
                    del api_kwargs["temperature"]
                    self.queue.put({
                        "type": "tool_info",
                        "content": "Model does not support temperature — retrying without it...\n",
                    })
                    full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict = \
                        self._stream_responses(api_kwargs, label_emitted)
                    break  # success
                # Some models reject specific server-side tools (e.g. gpt-5.2-pro
                # rejects code_interpreter). Parse the rejected tool name out of the
                # error, strip it from this request and cache it for future calls.
                import re as _re
                tool_match = _re.search(r"[Tt]ool\s+'([^']+)'\s+is\s+not\s+supported", err_str)
                if tool_match:
                    bad_tool = tool_match.group(1)
                    # Cache rejection for this model
                    self._openai_unsupported_tools.setdefault(self.model, set()).add(bad_tool)
                    # Strip from current request and retry
                    before_count = len(api_kwargs.get("tools", []))
                    api_kwargs["tools"] = [
                        t for t in api_kwargs.get("tools", [])
                        if t.get("type") != bad_tool
                    ]
                    after_count = len(api_kwargs["tools"])
                    if after_count < before_count:
                        self.queue.put({
                            "type": "tool_info",
                            "content": f"Model '{self.model}' does not support tool '{bad_tool}' — retrying without it (cached for this session)...\n",
                        })
                        full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict = \
                            self._stream_responses(api_kwargs, label_emitted)
                        break  # success
                # Some models reject specific reasoning.effort values (e.g. -pro
                # variants reject 'none' and 'low'). Parse the supported values
                # out of the error and retry with the lowest one.
                if "reasoning.effort" in err_str and "reasoning" in api_kwargs:
                    sup_match = _re.search(r"[Ss]upported values are:\s*([^.]+)", err_str)
                    if sup_match:
                        supported = _re.findall(r"'([^']+)'", sup_match.group(1))
                        if supported:
                            old_effort = api_kwargs["reasoning"].get("effort", "?")
                            new_effort = supported[0]
                            api_kwargs["reasoning"]["effort"] = new_effort
                            self.queue.put({
                                "type": "tool_info",
                                "content": f"Model '{self.model}' rejected reasoning.effort='{old_effort}' — retrying with '{new_effort}' (supported: {', '.join(supported)})...\n",
                            })
                            full_text, stop_reason, content_blocks, had_thinking, label_emitted, usage_dict = \
                                self._stream_responses(api_kwargs, label_emitted)
                            break  # success
                # Unrecognised BadRequestError — propagate
                raise
            except openai.APITimeoutError:
                # Reset timer for next attempt
                self._oai_first_content = threading.Event()
                self._oai_stream_start = time.time()
                ticker = threading.Thread(target=_waiting_ticker, daemon=True)
                ticker.start()
                if attempt < max_retries - 1:
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Stream timeout (no content from model within 180s) — retrying (attempt {attempt + 1}/{max_retries})...\n",
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
                if attempt < max_retries - 1 and getattr(e, 'status_code', 0) >= 500:
                    wait = server_error_backoff(attempt)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"API error — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                else:
                    raise

        # Stop the ticker thread
        self._oai_first_content.set()
        return stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict
