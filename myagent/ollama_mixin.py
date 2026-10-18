import json
import time

from myagent.constants import (
    OLLAMA_FALLBACK_MODELS,
    OLLAMA_THINKING_PREFIXES,
    OLLAMA_VISION_PREFIXES,
    OLLAMA_UNSUITABLE_PREFIXES,
    OLLAMA_THINK_LEVEL_PREFIXES,
    OLLAMA_THINK_LEVELS,
    OLLAMA_NUM_CTX_CAP,
    OLLAMA_KEEP_ALIVE,
)
from myagent.helpers import camera_aware_hint, is_camera_result, note_served_models


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
        # Tool results are linked to their calls by tool_call_id alone (Ollama
        # never needs the tool's name on a tool message).
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
                        deferred_from_camera = []  # one bool per deferred image
                        deferred_text_hints = []
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tc_content = block.get("content", "")
                                call_id = block.get("tool_use_id", "")
                                if isinstance(tc_content, list):
                                    text_parts = []
                                    from_camera = is_camera_result(tc_content)
                                    for part in tc_content:
                                        if isinstance(part, dict) and part.get("type") == "image":
                                            src = part.get("source", {})
                                            deferred_images.append(src.get("data", ""))
                                            deferred_from_camera.append(from_camera)
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
                            # A camera photo is no click surface (helpers.py)
                            hint_text = camera_aware_hint(hint_text, deferred_from_camera)
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

    def _ollama_request_kwargs(self, messages, announce=True):
        """The ONE builder of an /api/chat request — what `_stream_ollama_call`
        sends and what the Debug dump shows (`_payload_for_display`, since
        2026-10-18; until then Ollama fell through to the Anthropic-shaped
        dump, whose `max_tokens` / `thinking` keys Ollama never sees), so the
        dump cannot drift from the wire — the 2026-09-06 OpenAI pattern.
        `announce=False` (the dump) skips the two side effects that belong to
        a live call alone: the small-ctx warm-trap guard and the once-per-
        model tools-less warning."""
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
            if announce:
                self._ollama_ctx_guard(options["num_ctx"])

        request_kwargs = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": True,
            "options": options,
        }

        # Keep the model warm between runs (env-driven; unset leaves the
        # server's 5m default). Warm model = no reload and a reusable KV
        # prefix cache for the next run's identical prompt head.
        if OLLAMA_KEEP_ALIVE:
            request_kwargs["keep_alive"] = OLLAMA_KEEP_ALIVE

        # Only send tools if the model's Ollama template supports them.
        # qwen2.5vl advertises only ["completion", "vision"] — passing tools
        # there would be silently dropped or confuse the template.
        # If capabilities came back empty (query failed), fall back to the
        # pre-existing behaviour of always sending tools.
        if ollama_tools:
            tools_supported = (not caps["capabilities"]) or ("tools" in caps["capabilities"])
            if tools_supported:
                request_kwargs["tools"] = ollama_tools
            elif announce:
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

        # Thinking via the `think` flag. We pass the flag explicitly — true OR
        # false — for any thinking-capable model, because OMITTING `think`
        # falls back to the model's training default (which is thinking-ON
        # for Qwen3). Only explicit false reliably suppresses it. Which models
        # are thinking-capable is the server's own word — /api/show's
        # `thinking` capability, read into the cache by the caps query above
        # — with the name prefixes behind it for a model it did not describe
        # (`_is_ollama_thinking_model`), so a non-reasoning model is never
        # sent the flag, which it would error on.
        if self._is_ollama_thinking_model():
            request_kwargs["think"] = self._ollama_think_value(announce=announce)
        return request_kwargs

    def _stream_ollama_call(self, messages, max_retries, label_emitted):
        """Execute one Ollama /api/chat streaming call with retry logic.

        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict)
        — same 6-tuple shape as the other provider callers so stream_worker's
        dispatch stays uniform.
        """
        request_kwargs = self._ollama_request_kwargs(messages)

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
                    # No num_predict is sent, so "length" means the context
                    # window filled while the model was still writing: the
                    # reply (or a tool call) is cut off — say so, as the
                    # cloud callers do on their max_tokens stop (2026-10-18).
                    if _attr(last_chunk, "done_reason", None) == "length":
                        self.queue.put({
                            "type": "warning",
                            "content": (
                                f"⚠ {self.model} filled its context window (num_ctx "
                                f"{request_kwargs['options'].get('num_ctx', 'server default')}) while "
                                "replying — the reply is cut off\n"
                            ),
                        })
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

    def _ollama_loaded_context(self, model_id=None):
        """Context length of the currently loaded instance of a model, or None.

        Reads /api/ps — the data behind `ollama ps`'s CONTEXT column. None
        means "not loaded, field unavailable, or query failed"; the guard
        treats all three as no-op.
        """
        mid = model_id or self.model
        if not getattr(self, "ollama_client", None):
            return None
        try:
            resp = self.ollama_client.ps()
            for m in _attr(resp, "models", None) or []:
                name = _attr(m, "model", None) or _attr(m, "name", None)
                if name == mid:
                    ctx = _attr(m, "context_length", None)
                    return int(ctx) if ctx else None
        except Exception:
            return None
        return None

    def _ollama_ctx_guard(self, num_ctx):
        """Drop a warm instance pinned at a smaller context than this run needs.

        Ollama 0.32.x serves requests on an already-loaded instance regardless
        of the requested num_ctx — it never reloads for a bigger window — so an
        instance warmed small (a CLI `ollama run` chat, a probe script, a
        failed caps query) silently context-shifts the ~9K system-prompt/tool
        prefix out of the window and roughly doubles run time (live incident
        2026-08-14: 2,663s vs a 802-1,561s baseline). A keep_alive=0 generate
        poke unloads the small instance; the chat call that follows reloads at
        the right size. A LARGER loaded context is left alone — requests fit
        inside it, and downgrading a deliberately big warm instance would be
        its own trap.
        """
        loaded = self._ollama_loaded_context()
        if not loaded or loaded >= num_ctx:
            return
        try:
            self.ollama_client.generate(model=self.model, prompt="", keep_alive=0)
            self.queue.put({
                "type": "warning",
                "content": (
                    f"⚠ {self.model} was warm at {loaded} ctx but this run needs "
                    f"{num_ctx} — dropped the small instance so it reloads at "
                    f"{num_ctx} (small-ctx warm trap; see README)\n"
                ),
            })
        except Exception:
            pass  # best-effort: a failed poke must never block the run

    def _ollama_known_capabilities(self, model_id=None):
        """The capability set /api/show reported for a model — `completion`,
        `tools`, `vision`, `thinking` — from the per-model cache
        (`_get_ollama_model_caps`, filled by the model fetch for every
        listed model and by each stream call), or None when nothing is known:
        a bare host, a model the server never described, a query that
        failed. Read-only on purpose: the detection helpers below run on the
        Tk thread (the editor's controls, the title) and must never wait on
        the server."""
        mid = model_id or self.model
        cached = (getattr(self, "_ollama_model_caps", None) or {}).get(mid)
        return (cached or {}).get("capabilities") or None

    def _is_ollama_thinking_model(self, model_id=None):
        """Thinking-capable, by the server's own word FIRST — /api/show's
        `thinking` capability — and by the OLLAMA_THINKING_PREFIXES name
        table only for a model it has not described (2026-10-18 audit:
        the tables, written in April, knew nothing of Gemma 4, the Qwen 3.x
        vision tiers or Nemotron, so such a model showed no Thinking
        checkbox and was never sent `think`)."""
        mid = model_id or self.model
        known = self._ollama_known_capabilities(mid)
        if known is not None:
            return "thinking" in known
        return any(mid.startswith(p) for p in OLLAMA_THINKING_PREFIXES)

    def _is_ollama_vision_model(self, model_id=None):
        """Takes image input, by /api/show's `vision` capability first and
        OLLAMA_VISION_PREFIXES for an undescribed model (same audit: a
        text-only warning from the name alone called Gemma 4 blind)."""
        mid = model_id or self.model
        known = self._ollama_known_capabilities(mid)
        if known is not None:
            return "vision" in known
        return any(mid.startswith(p) for p in OLLAMA_VISION_PREFIXES)

    def _ollama_think_levels(self, model_id=None):
        """The `think` levels a model takes (quietest first), or None for a
        model whose flag is boolean. Ollama's 0.40 line publishes them in
        /api/show as `thinking.values` — kept in the capability cache as
        `think_levels` where a server sends them — and the
        OLLAMA_THINK_LEVEL_PREFIXES names answer behind that (gpt-oss, whose
        levels a 0.32.9 server honours without publishing them)."""
        mid = model_id or self.model
        cached = (getattr(self, "_ollama_model_caps", None) or {}).get(mid) or {}
        levels = cached.get("think_levels")
        if levels:
            return list(levels)
        if any(mid.startswith(p) for p in OLLAMA_THINK_LEVEL_PREFIXES):
            return list(OLLAMA_THINK_LEVELS)
        return None

    @staticmethod
    def _ollama_nearest_level(effort, levels):
        """The rung of `levels` nearest a saved thinking effort — the
        OpenAI / xAI nearest-rung rule: a rung passes, `minimal` is the
        quietest, `max` / `xhigh` the top, anything else (a Claude
        `adaptive`, an empty value) the model's middle."""
        effort = (effort or "").lower()
        if effort in levels:
            return effort
        if effort == "minimal":
            return levels[0]
        if effort in ("max", "xhigh"):
            return levels[-1]
        return levels[len(levels) // 2]

    def _ollama_think_value(self, announce=False):
        """What the `think` field carries for a thinking-capable model. A
        boolean model: True / False from the checkbox (False IS the off
        switch). A level model (`_ollama_think_levels`): the saved effort's
        nearest rung with Thinking on, and the QUIETEST rung with it off —
        such a model cannot stop thinking, so `false` would leave it
        reasoning at its default, unshown; `announce` posts that once per
        model per session, as the Gemini quiet rung is announced."""
        levels = self._ollama_think_levels()
        if levels is None:
            return bool(self.thinking_enabled)
        if self.thinking_enabled:
            return self._ollama_nearest_level(self.thinking_effort, levels)
        if announce:
            noted = getattr(self, "_ollama_quiet_noted", None)
            if noted is None:
                noted = self._ollama_quiet_noted = set()
            if self.model not in noted:
                noted.add(self.model)
                self.queue.put({
                    "type": "tool_info",
                    "content": (f"Thinking is off: {self.model} is sent think={levels[0]} "
                                "(this model cannot stop thinking — its quietest level)\n"),
                })
        return levels[0]

    def _ollama_runs_agent_loop(self, model_id):
        """Whether a listed model can run MyAgent's loop: its capabilities
        name `tools` (MyAgent declares client-side tools on every call and a
        model without them can only talk), or are unknown — a failed query
        keeps the model listed, as before — and its name is not one the live
        audit found unfit despite the capability (OLLAMA_UNSUITABLE_PREFIXES,
        constants.py, with the reason)."""
        if any(model_id.startswith(p) for p in OLLAMA_UNSUITABLE_PREFIXES):
            return False
        caps = self._get_ollama_model_caps(model_id)["capabilities"]
        return (not caps) or ("tools" in caps)

    def _fetch_ollama_models(self):
        """List the local server's models (/api/tags) and offer the ones that
        can run the agent loop.

        Every listed id is recorded as served (`note_served_models`) and its
        /api/show capabilities read into the per-model cache — one local
        call each — which is what the thinking / vision detection and the
        request builder then read. A model whose capabilities lack `tools`
        leaves the picker (2026-10-18 audit): MyAgent declares client-side
        tools on every call, and such a model — the stock qwen2.5vl:32b and
        gemma3:27b — runs with no tools at all behind a warning, so it can
        answer but never act; a pinned instruction still runs it (served,
        hidden — the retirement rule, with `_restore_model_params`' hidden-
        model note), a model whose query failed stays listed as before, and
        if nothing passes the whole list is offered rather than ids that are
        not installed. The fallback list serves a failed fetch alone."""
        if not getattr(self, "ollama_client", None):
            note_served_models(self, "Ollama", None)
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
        except Exception:
            self._ollama_model_display_names = {}
            note_served_models(self, "Ollama", None)
            return list(OLLAMA_FALLBACK_MODELS)
        if not names:
            self._ollama_model_display_names = {}
            note_served_models(self, "Ollama", None)
            return list(OLLAMA_FALLBACK_MODELS)
        note_served_models(self, "Ollama", names)
        picker = [n for n in names if self._ollama_runs_agent_loop(n)] or names
        self._ollama_model_display_names = {n: n for n in picker}
        return picker

    def _get_ollama_model_caps(self, model_id=None):
        """Query and cache Ollama model capabilities + context length.

        Returns a dict: {"context_length": int | None, "capabilities": set[str]}.
        SUCCESSFUL lookups are cached per-model for the life of the session —
        `ollama show` is cheap locally but this avoids hitting the endpoint on
        every stream call. A failed lookup is served empty for that call only
        and re-queried next time.

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
                # The 0.40 line's `thinking` block: `values` lists the
                # `think` levels a model takes ("low" / "medium" / "high";
                # `[false]` for no thinking, booleans skipped here) and
                # `default` the one an omitted flag means. A 0.32.9 server
                # sends no block, so the name table decides there.
                think_meta = _attr(resp, "thinking", None)
                values = _attr(think_meta, "values", None) if think_meta is not None else None
                if isinstance(values, (list, tuple)):
                    caps["think_levels"] = [v for v in values if isinstance(v, str)]
                # Cache only a SUCCESSFUL query. Caching a transient failure
                # used to pin empty caps for the whole session: every later
                # call then omitted num_ctx, so the server-default small load
                # (pinned by keep_alive) became a session-long small-ctx trap.
                self._ollama_model_caps[mid] = caps
            except Exception:
                # Serve empty caps for THIS call only (default num_ctx, tools
                # sent unconditionally); the next call re-queries.
                pass
        return caps
