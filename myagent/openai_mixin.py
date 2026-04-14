import json
import time
import threading

import openai

from myagent.constants import (
    OPENAI_REASONING_PREFIXES,
    OPENAI_RESPONSES_PREFIXES,
    OPENAI_FALLBACK_MODELS,
    OPENAI_DEFAULT_MODEL,
    _HAS_DESKTOP,
)


class OpenAIMixin:

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
                if first_content_timeout and hasattr(self, '_oai_first_content') and not self._oai_first_content.is_set():
                    if time.time() - self._oai_stream_start >= first_content_timeout:
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
                        self.queue.put({"type": "tool_info", "content": "Searching the web...\n"})
                    elif item and getattr(item, "type", None) == "code_interpreter_call":
                        self.queue.put({"type": "tool_info", "content": "Running code interpreter...\n"})

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
                                    self.queue.put({"type": "tool_info", "content": logs + "\n"})
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
            usage = getattr(final_resp, "usage", None)
            if usage:
                usage_dict = {
                    "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                    "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                }
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
                if mid.startswith(OPENAI_RESPONSES_PREFIXES):
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
        if self.provider == "Gemini":
            return self._fetch_gemini_models()
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

    def _has_reasoning_xhigh(self, model_id=None):
        """Check if model supports reasoning.effort='xhigh'."""
        mid = model_id or self.model
        if not self._is_gpt5_family(mid):
            return False
        if "codex-max" in mid:
            return True
        # mini/nano variants cap at 'high' — no xhigh
        if "-mini" in mid or "-nano" in mid:
            return False
        return self._parse_gpt5_minor(mid) >= 2

    def _gpt5_supports_temp_at_none(self, model_id=None):
        """Check if model supports temperature when reasoning.effort='none' (gpt-5.4+)."""
        mid = model_id or self.model
        return self._is_gpt5_family(mid) and self._parse_gpt5_minor(mid) >= 4

    def _is_gpt5_chat_model(self, model_id=None):
        """Check if model is a gpt-5.x-chat-* Instant variant."""
        mid = model_id or self.model
        return mid.startswith("gpt-5") and "-chat" in mid

    def _has_openai_verbosity(self, model_id=None):
        """Check if model supports text.verbosity (all gpt-5 family including -chat)."""
        mid = model_id or self.model
        return mid.startswith("gpt-5")

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
        is_reasoning = self._is_openai_reasoning_model()
        has_none = self._has_reasoning_none()

        api_kwargs = {
            "model": self.model,
            "input": responses_input,
            "instructions": system_prompt,
            "tools": responses_tools,
            "store": False,
            "include": ["code_interpreter_call.outputs"],
        }
        if has_none:
            # GPT-5.1+: always send reasoning param, even with effort="none"
            api_kwargs["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
            if self.thinking_effort == "none":
                # gpt-5.4+ supports user temperature at effort=none; older models fixed at 1.0
                if self._gpt5_supports_temp_at_none():
                    api_kwargs["temperature"] = self.temperature
                else:
                    api_kwargs["temperature"] = 1.0
        elif self._is_gpt5_family():
            # GPT-5.0: always reasoning, temp fixed at 1.0
            if self.thinking_enabled:
                api_kwargs["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
            api_kwargs["temperature"] = 1.0
        elif is_reasoning and self.thinking_enabled:
            api_kwargs["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
        elif not is_reasoning:
            # gpt-5.x-chat Instant models don't support temperature
            if not self._is_gpt5_chat_model():
                api_kwargs["temperature"] = self.temperature
        # Verbosity for all gpt-5 models (including -chat Instant variants)
        if self._has_openai_verbosity():
            api_kwargs["text"] = {"verbosity": self.text_verbosity}

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
                    wait = min(2 ** attempt * 5, 60)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                else:
                    raise
            except openai.APIError as e:
                if attempt < max_retries - 1 and getattr(e, 'status_code', 0) >= 500:
                    wait = min(2 ** attempt * 10, 90)
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
