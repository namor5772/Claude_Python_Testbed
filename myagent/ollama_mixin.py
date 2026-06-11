import json
import time

from myagent.constants import (
    OLLAMA_FALLBACK_MODELS,
    OLLAMA_THINKING_PREFIXES,
    OLLAMA_VISION_PREFIXES,
    OLLAMA_NUM_CTX_CAP,
)


def _attr(obj, key, default=None):
    """Pull a field from either a dict or a Pydantic/SDK object.

    The ollama Python SDK returns ChatResponse/Message as Pydantic models, but
    older versions and the `list()` response shape mix dicts and objects — this
    helper lets every access site stay uniform.
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


class OllamaMixin:

    def _tools_to_ollama(self, tools):
        """Convert Anthropic tool schemas to Ollama's OpenAI-style function list.

        Ollama's /api/chat accepts `tools` in the same shape as OpenAI Chat
        Completions: a list of {"type": "function", "function": {name, description, parameters}}.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                },
            }
            for tool in tools
        ]

    def _messages_to_ollama(self, messages):
        """Translate Anthropic-style internal messages to Ollama chat messages.

        Ollama /api/chat expects a list of messages with roles system/user/assistant/tool.
        Image inputs attach as a base64 list on the user message. Tool results
        go to a dedicated "tool" role keyed by tool_call_id.

        Screenshot images carried inside tool_result blocks are hoisted into a
        separate follow-up user message (same strategy as the Gemini/OpenAI
        translators) so vision models can reliably process them — Ollama's
        `tool` role content is string-only.
        """
        out = []
        # Build tool_use_id → tool_name lookup for tool-result linkage.
        id_to_name = {}
        for msg in messages:
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    if _attr(block, "type") == "tool_use":
                        bid = _attr(block, "id")
                        bname = _attr(block, "name")
                        if bid and bname:
                            id_to_name[bid] = bname

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    out.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    has_tool_result = any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                    )
                    if has_tool_result:
                        deferred_images = []
                        deferred_text_hints = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tc_content = block.get("content", "")
                                call_id = block.get("tool_use_id", "")
                                if isinstance(tc_content, list):
                                    text_parts = []
                                    for part in tc_content:
                                        if isinstance(part, dict) and part.get("type") == "image":
                                            src = part.get("source", {})
                                            deferred_images.append(src.get("data", ""))
                                        elif isinstance(part, dict) and part.get("type") == "text":
                                            text_parts.append(part.get("text", ""))
                                        else:
                                            text_parts.append(str(part))
                                    out.append({
                                        "role": "tool",
                                        "tool_call_id": call_id,
                                        "content": "\n".join(text_parts) if text_parts else "",
                                    })
                                    if text_parts:
                                        deferred_text_hints.extend(text_parts)
                                else:
                                    out.append({
                                        "role": "tool",
                                        "tool_call_id": call_id,
                                        "content": str(tc_content) if tc_content else "",
                                    })
                        # Screenshot images hoisted to a user message — vision
                        # models process them reliably there. Non-vision models
                        # silently drop the images, which is fine: the weak-combo
                        # warning tells the user why desktop tools won't work.
                        if deferred_images:
                            dims_hint = ""
                            import re
                            for tp in deferred_text_hints:
                                m = re.search(r"\((\d+)x(\d+)(?:\s+pixels)?\)", tp)
                                if m:
                                    dims_hint = f" ({m.group(1)}x{m.group(2)} pixels)"
                                    break
                            hint_text = (
                                f"Below is the screenshot image{dims_hint} returned by the "
                                "screenshot tool above. COORDINATE SYSTEM: the top-left pixel "
                                "is (0, 0), X increases rightward, Y increases downward. "
                                "When calling mouse_click, use the pixel (x, y) coordinates "
                                "as they appear in THIS image — they are automatically scaled "
                                "to actual screen coordinates."
                            )
                            out.append({
                                "role": "user",
                                "content": hint_text,
                                "images": deferred_images,
                            })
                    else:
                        text_parts = []
                        image_parts = []
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    text_parts.append(block.get("text", ""))
                                elif block.get("type") == "image":
                                    src = block.get("source", {})
                                    image_parts.append(src.get("data", ""))
                            elif isinstance(block, str):
                                text_parts.append(block)
                        msg_out = {"role": "user", "content": "\n".join(text_parts) if text_parts else ""}
                        if image_parts:
                            msg_out["images"] = image_parts
                        out.append(msg_out)

            elif role == "assistant":
                if isinstance(content, str):
                    out.append({"role": "assistant", "content": content})
                elif isinstance(content, list):
                    text_parts = []
                    tool_calls = []
                    for block in content:
                        btype = _attr(block, "type")
                        if btype == "text":
                            t = _attr(block, "text", "")
                            if t:
                                text_parts.append(t)
                        elif btype == "tool_use":
                            tool_calls.append({
                                "id": _attr(block, "id", ""),
                                "type": "function",
                                "function": {
                                    "name": _attr(block, "name", ""),
                                    "arguments": _attr(block, "input", {}) or {},
                                },
                            })
                        # thinking / gemini_thinking / redacted_thinking blocks
                        # are intentionally skipped — Ollama doesn't round-trip
                        # thinking signatures and Qwen3 regenerates reasoning
                        # fresh on every turn.
                    msg_out = {"role": "assistant", "content": "\n".join(text_parts)}
                    if tool_calls:
                        msg_out["tool_calls"] = tool_calls
                    out.append(msg_out)

        return out

    def _stream_ollama_call(self, messages, max_retries, label_emitted):
        """Execute one Ollama /api/chat streaming call with retry logic.

        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict)
        — same 6-tuple shape as the other provider callers so stream_worker's
        dispatch stays uniform.
        """
        system_prompt = self._build_system_prompt()
        tools = self._get_tools()
        ollama_tools = self._tools_to_ollama(tools) if tools else None
        ollama_messages = [{"role": "system", "content": system_prompt},
                           *self._messages_to_ollama(messages)]

        # Per-model capability + context discovery. Ollama's default num_ctx
        # is just 4096 — far below what modern models support — so without
        # this we'd be wasting 90%+ of the model's trained context. Capabilities
        # also tell us which models can actually use the `tools` parameter.
        caps = self._get_ollama_model_caps()

        options = {"temperature": self.temperature}
        if caps["context_length"]:
            # Cap at OLLAMA_NUM_CTX_CAP to avoid memory pressure / swap on
            # unified-memory Macs. The model's full advertised max (40K-128K)
            # is almost never needed for agent loops and triples the KV cache
            # footprint. Override via env var if you have bigger hardware.
            options["num_ctx"] = min(caps["context_length"], OLLAMA_NUM_CTX_CAP)

        request_kwargs = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": True,
            "options": options,
        }

        # Only send tools if the model's Ollama template supports them.
        # qwen2.5vl advertises only ["completion", "vision"] — passing tools
        # there would be silently dropped or confuse the template.
        # If capabilities came back empty (query failed), fall back to the
        # pre-existing behaviour of always sending tools.
        if ollama_tools:
            tools_supported = (not caps["capabilities"]) or ("tools" in caps["capabilities"])
            if tools_supported:
                request_kwargs["tools"] = ollama_tools
            else:
                if not hasattr(self, "_ollama_caps_warned"):
                    self._ollama_caps_warned = set()
                if self.model not in self._ollama_caps_warned:
                    self._ollama_caps_warned.add(self.model)
                    self.queue.put({
                        "type": "tool_info",
                        "content": (
                            f"⚠ {self.model} does not advertise the 'tools' capability — "
                            "tool calls will be skipped. Only completion + vision (if any) "
                            "will work. Use qwen3 or another tool-capable model for agent tasks.\n"
                        ),
                    })

        # Qwen3 thinking mode via Ollama 0.5+ `think` flag. We pass the flag
        # explicitly — true OR false — for any thinking-capable model, because
        # OMITTING `think` falls back to the model's training default (which is
        # thinking-ON for Qwen3). Only explicit false reliably suppresses it.
        # Capability check protects against sending `think` to a non-reasoning
        # model that would error on it.
        if self._is_ollama_thinking_model() and (
            not caps["capabilities"] or "thinking" in caps["capabilities"]
        ):
            request_kwargs["think"] = bool(self.thinking_enabled)

        full_text = ""
        thinking_text = ""
        had_thinking = False
        tool_calls = []
        tool_index = 0
        usage_dict = None
        # Qwen3's tool-calling template wraps plain-text replies in
        # <message>...</message>. We strip these tags as they stream without
        # disabling the template itself (disabling it would break tool calls).
        OPEN_TAG = "<message>"
        CLOSE_TAG = "</message>"
        text_buffer = ""           # accumulates unemitted text
        open_tag_decided = False   # have we decided whether the stream starts with <message>?

        for attempt in range(max_retries):
            try:
                in_thinking = False
                last_chunk = None
                for chunk in self.ollama_client.chat(**request_kwargs):
                    last_chunk = chunk
                    if self.stop_requested:
                        break
                    msg = _attr(chunk, "message", None)
                    if msg is None:
                        continue

                    think_delta = _attr(msg, "thinking", None) or ""
                    if think_delta:
                        if not in_thinking:
                            in_thinking = True
                            had_thinking = True
                            self.queue.put({"type": "thinking_start"})
                        thinking_text += think_delta
                        self.queue.put({"type": "thinking_delta", "content": think_delta})

                    text_delta = _attr(msg, "content", None) or ""
                    if text_delta:
                        if in_thinking:
                            self.queue.put({"type": "thinking_end"})
                            in_thinking = False
                        text_buffer += text_delta
                        # Decide once whether the response opens with <message>.
                        # Wait until we have enough chars to confirm OR rule out.
                        if not open_tag_decided:
                            if text_buffer.startswith(OPEN_TAG):
                                text_buffer = text_buffer[len(OPEN_TAG):]
                                open_tag_decided = True
                            elif len(text_buffer) >= len(OPEN_TAG) or not OPEN_TAG.startswith(text_buffer):
                                open_tag_decided = True
                        if open_tag_decided and text_buffer:
                            # Hold back any trailing suffix that could be the
                            # start of </message> so we can strip it later.
                            hold_len = 0
                            for n in range(min(len(CLOSE_TAG), len(text_buffer)), 0, -1):
                                if CLOSE_TAG.startswith(text_buffer[-n:]):
                                    hold_len = n
                                    break
                            emit_len = len(text_buffer) - hold_len
                            if emit_len > 0:
                                emit = text_buffer[:emit_len]
                                text_buffer = text_buffer[emit_len:]
                                if not label_emitted:
                                    self.queue.put({"type": "label"})
                                    label_emitted = True
                                full_text += emit
                                self.queue.put({"type": "text_delta", "content": emit})

                    tcs = _attr(msg, "tool_calls", None) or []
                    for tc in tcs:
                        fn = _attr(tc, "function", {}) or {}
                        fname = _attr(fn, "name", "") or ""
                        fargs = _attr(fn, "arguments", {}) or {}
                        if isinstance(fargs, str):
                            try:
                                fargs = json.loads(fargs)
                            except Exception:
                                fargs = {}
                        tool_id = _attr(tc, "id", None)
                        if not tool_id:
                            tool_id = f"ollama_{fname}_{tool_index}"
                        tool_index += 1
                        tool_calls.append({
                            "name": fname,
                            "id": tool_id,
                            "input": fargs if isinstance(fargs, dict) else {},
                        })
                if in_thinking:
                    self.queue.put({"type": "thinking_end"})
                # Flush any text still held in the buffer — strip a trailing
                # </message> if the wrapper closed cleanly.
                if text_buffer:
                    if text_buffer.endswith(CLOSE_TAG):
                        text_buffer = text_buffer[:-len(CLOSE_TAG)]
                    if text_buffer:
                        if not label_emitted:
                            self.queue.put({"type": "label"})
                            label_emitted = True
                        full_text += text_buffer
                        self.queue.put({"type": "text_delta", "content": text_buffer})
                        text_buffer = ""
                # Usage lives only in the done=True final chunk.
                if last_chunk is not None:
                    pe = _attr(last_chunk, "prompt_eval_count", None)
                    ec = _attr(last_chunk, "eval_count", None)
                    if pe or ec:
                        usage_dict = {"input_tokens": int(pe or 0), "output_tokens": int(ec or 0)}
                break  # success
            except Exception as e:
                err_str = str(e).lower()
                is_timeout = "timeout" in err_str or "timed out" in err_str
                is_conn_refused = ("connection" in err_str and
                                   ("refused" in err_str or "reset" in err_str or
                                    "cannot connect" in err_str))
                if is_conn_refused:
                    # Ollama server is down — no point retrying. Surface a
                    # clear error so the user can restart it.
                    raise RuntimeError(
                        f"Ollama server unreachable at {getattr(self, '_ollama_base_url', 'localhost')}. "
                        f"Start the server (e.g. `ollama serve` or relaunch the Ollama app) and retry. "
                        f"Original error: {e}"
                    ) from e
                if is_timeout and attempt < max_retries - 1:
                    wait = min(2 ** attempt * 2, 30)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Ollama stream timed out — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                    thinking_text = ""
                    tool_calls = []
                    tool_index = 0
                    text_buffer = ""
                    open_tag_decided = False
                    continue
                raise

        stop_reason = "tool_use" if tool_calls else "end_turn"

        content_blocks = []
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        content_blocks.extend({
            "type": "tool_use",
            "id": tc["id"],
            "name": tc["name"],
            "input": tc["input"],
        } for tc in tool_calls)
        # Thinking text is display-only for Ollama — not preserved in message
        # history because Qwen3 regenerates reasoning on each call with no
        # signature round-trip needed.

        return stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict

    def _is_ollama_thinking_model(self, model_id=None):
        mid = model_id or self.model
        return any(mid.startswith(p) for p in OLLAMA_THINKING_PREFIXES)

    def _is_ollama_vision_model(self, model_id=None):
        mid = model_id or self.model
        return any(mid.startswith(p) for p in OLLAMA_VISION_PREFIXES)

    def _fetch_ollama_models(self):
        """List installed models from the local Ollama server via /api/tags."""
        if not getattr(self, "ollama_client", None):
            return list(OLLAMA_FALLBACK_MODELS)
        try:
            resp = self.ollama_client.list()
            models_list = _attr(resp, "models", []) or []
            names = []
            for m in models_list:
                # Ollama SDK's Model objects use `.model` as the canonical
                # identifier (verified live — `.name` is not set). Kept `.name`
                # as a secondary fallback for older SDK versions.
                name = _attr(m, "model", None) or _attr(m, "name", None)
                if name:
                    names.append(name)
            names.sort()
            self._ollama_model_display_names = {n: n for n in names}
            return names if names else list(OLLAMA_FALLBACK_MODELS)
        except Exception:
            self._ollama_model_display_names = {}
            return list(OLLAMA_FALLBACK_MODELS)

    def _get_ollama_model_caps(self, model_id=None):
        """Query and cache Ollama model capabilities + context length.

        Returns a dict: {"context_length": int | None, "capabilities": set[str]}.
        Cached per-model for the life of the session — `ollama show` is cheap
        locally but this avoids hitting the endpoint on every stream call.

        The `context_length` lives in `modelinfo["{arch}.context_length"]` where
        the arch prefix is model-specific (`qwen3`, `qwen25vl`, `llama`, etc.).
        We scan by suffix so we don't need to know the arch naming convention.
        """
        mid = model_id or self.model
        if not hasattr(self, "_ollama_model_caps"):
            self._ollama_model_caps = {}
        if mid in self._ollama_model_caps:
            return self._ollama_model_caps[mid]
        caps = {"context_length": None, "capabilities": set()}
        if getattr(self, "ollama_client", None):
            try:
                resp = self.ollama_client.show(mid)
                cap_list = _attr(resp, "capabilities", None) or []
                caps["capabilities"] = set(cap_list)
                mi = _attr(resp, "modelinfo", None) or {}
                if isinstance(mi, dict):
                    for k, v in mi.items():
                        if k.endswith(".context_length") and isinstance(v, int):
                            caps["context_length"] = v
                            break
            except Exception:
                # Leave caps empty → the caller falls back to pre-existing
                # behaviour (default num_ctx, tools passed unconditionally).
                pass
        self._ollama_model_caps[mid] = caps
        return caps
