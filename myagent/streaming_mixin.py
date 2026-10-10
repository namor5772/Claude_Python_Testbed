import json
import copy
import re
import concurrent.futures
import os
import tempfile
import time
import tkinter as tk
from datetime import datetime

from myagent.constants import (
    TOOLS, FILE_TOOLS, META_TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS, MCP_TOOLS,
    GOOGLE_TOOLS, PROTON_TOOLS, OUTLOOK_TOOLS, EXCEL_TOOLS, PHYSICAL_TOOLS,
    PARALLEL_SAFE_TOOLS,
    _HAS_DESKTOP, _HAS_MCP, _HAS_GOOGLE,
    _HAS_PROTONMAIL, _HAS_OUTLOOK, _HAS_EXCEL, _HAS_CAMERA, _HAS_MICROPHONE,
    _HAS_PHYSICAL,
    ANTHROPIC_PRICING, ANTHROPIC_FAST_PRICING, ANTHROPIC_WEB_SEARCH_FEE,
    ANTHROPIC_LONG_CONTEXT_PRICING,
    OPENAI_PRICING, GEMINI_PRICING, XAI_PRICING, OPENAI_RESPONSES_INCLUDE,
    RESPONSES_REASONING_INCLUDE, XAI_RESPONSES_INCLUDE,
    GENERIC_PRICING_PREFIXES,
    KIMI_PRICING, OLLAMA_PRICING, resolve_price,
    APICOST_LOG_FILE, APICOST_LOG_MAX_BYTES, CONVO_END_WORDS, CONVO_EXIT_WORD,
    AGENT_RUN_PREFIX,
)
from myagent.helpers import (_ToolBlock, camera_aware_hint, is_camera_result,
                             responses_replay_refused, rotate_log_if_needed)

if _HAS_DESKTOP:
    import pyautogui


class StreamingMixin:

    def _tool_info(self, message):
        """Post a tool_info activity line to the GUI queue (shared by all mixins)."""
        self.queue.put({"type": "tool_info", "content": message})

    def _tools_to_responses(self, tools):
        """Convert Anthropic tool schemas to OpenAI Responses API format."""
        return [
            {
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                "strict": False,
            }
            for tool in tools
        ]

    def _messages_to_responses(self, messages):
        """Convert internal Anthropic-format messages to Responses API input format.

        Key differences from Chat Completions:
        - No system message (system prompt moves to 'instructions' parameter)
        - User images use input_text/input_image content types
        - Assistant tool calls become top-level function_call items
        - Tool results become top-level function_call_output items
        - An assistant turn carrying a `responses_items` block (an OpenAI turn,
          since 2026-09-30) is replayed from it verbatim instead — reasoning
          included; see _responses_replay_items
        """
        result = []

        for msg in messages:
            role = msg["role"]
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    result.append({"role": "user", "content": content})
                elif isinstance(content, list):
                    # Check if this is a tool_result list
                    has_tool_result = any(
                        (isinstance(b, dict) and b.get("type") == "tool_result") for b in content
                    )
                    if has_tool_result:
                        # Collect any images from tool results to send as a
                        # separate user message after all function_call_output
                        # items.  GPT models process images more reliably from
                        # user messages than from function_call_output content.
                        deferred_images = []
                        deferred_from_camera = []  # one bool per deferred image
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tc_content = block.get("content", "")
                                call_id = block.get("tool_use_id", "")
                                # Handle content that is a list (e.g. with image blocks)
                                if isinstance(tc_content, list):
                                    text_parts = []
                                    from_camera = is_camera_result(tc_content)
                                    for part in tc_content:
                                        if isinstance(part, dict) and part.get("type") == "image":
                                            src = part.get("source", {})
                                            data_url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                                            deferred_images.append({
                                                "type": "input_image",
                                                "image_url": data_url,
                                            })
                                            deferred_from_camera.append(from_camera)
                                        elif isinstance(part, dict) and part.get("type") == "text":
                                            text_parts.append(part.get("text", ""))
                                        else:
                                            text_parts.append(str(part))
                                    result.append({
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": "\n".join(text_parts),
                                    })
                                else:
                                    result.append({
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": str(tc_content) if tc_content else "",
                                    })
                        # Send deferred images as a user message so the model
                        # processes them through its normal vision pipeline
                        if deferred_images:
                            # Extract dimensions from the tool output text
                            dims_hint = ""
                            for item in reversed(result):
                                out = item.get("output", "")
                                if isinstance(out, str):
                                    m = re.search(r"\((\d+)x(\d+)(?:\s+pixels)?\)", out)
                                    if m:
                                        w, h = m.group(1), m.group(2)
                                        dims_hint = f" ({w}x{h} pixels)"
                                        break
                            hint_text = (
                                f"Below is the screenshot image{dims_hint} returned by the "
                                "screenshot tool above. COORDINATE SYSTEM: the top-left pixel "
                                "is (0, 0), X increases rightward, Y increases downward. "
                                "When calling mouse_click, use the pixel (x, y) coordinates "
                                "as they appear in THIS image — they are automatically "
                                "scaled to actual screen coordinates."
                            )
                            # A camera photo is no click surface (helpers.py)
                            hint_text = camera_aware_hint(hint_text, deferred_from_camera)
                            result.append({
                                "role": "user",
                                "content": [
                                    {"type": "input_text", "text": hint_text},
                                    *deferred_images,
                                ],
                            })
                    else:
                        # User message with text + images
                        parts = []
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    parts.append({"type": "input_text", "text": block.get("text", "")})
                                elif block.get("type") == "image":
                                    src = block.get("source", {})
                                    data_url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                                    parts.append({
                                        "type": "input_image",
                                        "image_url": data_url,
                                    })
                            elif isinstance(block, str):
                                parts.append({"type": "input_text", "text": block})
                        result.append({"role": "user", "content": parts})

            elif role == "assistant":
                if isinstance(content, str):
                    result.append({"role": "assistant", "content": [{"type": "output_text", "text": content}]})
                elif isinstance(content, list):
                    replay = self._responses_replay_items(content)
                    if replay:
                        # The turn exactly as the API returned it: encrypted
                        # reasoning, web_search_call records, each message's
                        # phase, function_calls — see _openai_output_items.
                        result.extend(replay)
                        continue
                    # Collect text and tool_use blocks separately
                    text_parts = []
                    func_calls = []
                    for block in content:
                        # Handle both Pydantic objects and dicts
                        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                        if btype == "text":
                            t = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else "")
                            if t:
                                text_parts.append(t)
                        elif btype == "tool_use":
                            bid = getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else "")
                            bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else "")
                            binput = getattr(block, "input", None) or (block.get("input") if isinstance(block, dict) else {})
                            func_calls.append({
                                "type": "function_call",
                                "call_id": bid,
                                "name": bname,
                                "arguments": json.dumps(binput),
                            })
                        # Skip thinking/redacted_thinking blocks
                    combined_text = "\n".join(text_parts)
                    if combined_text:
                        result.append({"role": "assistant", "content": [{"type": "output_text", "text": combined_text}]})
                    # Function calls are top-level items in Responses API
                    result.extend(func_calls)

        return result

    def _responses_replay_items(self, content):
        """The output items an assistant turn's `responses_items` block holds
        (written by OpenAIMixin._stream_responses and XAIMixin.
        _stream_xai_events), as fresh top-level copies — the wire list never
        aliases the history — or None to rebuild the turn from its text /
        tool_use blocks: a turn without the block (a STOPped or incomplete
        stream), or replay switched off for this provider for the session
        after its API refused it (`_responses_replay_off`, set by
        _responses_drop_replay).

        Sending the items to a DIFFERENT model of the same provider is safe —
        the Model upgrade does it. Probed live 2026-09-30: gpt-6-luna's
        reasoning replayed to gpt-6-astra and gpt-6-sol was consumed (input
        tokens rose by its size), to gpt-5.6-terra and gpt-4.1 silently
        dropped; grok-4.3's to grok-4.7, grok-4.6, grok-build-0.1 and the
        pinned grok-4.20 reasoning variant consumed; every call 200."""
        if self.provider in getattr(self, "_responses_replay_off", ()):
            return None
        for block in content:
            if isinstance(block, dict) and block.get("type") == "responses_items":
                items = [dict(item) for item in block.get("items") or ()
                         if isinstance(item, dict)]
                return items or None
        return None

    def _responses_include(self, base):
        """The `include` list a Responses request carries — the provider's
        tuple (OPENAI_RESPONSES_INCLUDE / XAI_RESPONSES_INCLUDE) minus the
        encrypted reasoning once replay is off for this provider, since
        nothing would replay it. The live calls and the Debug dump both call
        this, so the dump shows what the wire carries."""
        if self.provider in getattr(self, "_responses_replay_off", ()):
            return [i for i in base if i != RESPONSES_REASONING_INCLUDE]
        return list(base)

    def _responses_drop_replay(self, error, api_kwargs, messages):
        """The 400 rung both Responses callers run FIRST (_stream_responses_call,
        _stream_xai_call): when `error` is about the replayed or requested
        encrypted reasoning, or about a replayed input item
        (helpers.responses_replay_refused), switch replay off for this
        provider for the rest of the session, rebuild the request's input
        the pre-2026-09-30 way, stop asking for the ciphertext, post an
        always-shown ⚠ and return True — the caller retries. False (nothing
        touched) for any other error, and once replay is already off.

        First because the rungs after it match loosely: xAI's drops the
        reasoning parameter on any error that says "reasoning" and strips a
        server tool on any that names one — a refused replayed reasoning item
        or web_search_call would have tripped them."""
        off = getattr(self, "_responses_replay_off", None)
        if off is None:
            off = self._responses_replay_off = set()
        if self.provider in off or not responses_replay_refused(error, api_kwargs.get("input")):
            return False
        off.add(self.provider)
        api_kwargs["input"] = self._messages_to_responses(messages)
        if "include" in api_kwargs:
            kept = [i for i in api_kwargs["include"] if i != RESPONSES_REASONING_INCLUDE]
            if kept:
                api_kwargs["include"] = kept
            else:
                del api_kwargs["include"]
        self.queue.put({
            "type": "warning",
            "content": f"⚠ {self.provider} refused the replayed reasoning / output items "
                       "— resending without them. For the rest of this session the "
                       "model's reasoning is not carried between calls.\n",
        })
        return True

    def _make_serializable(self, obj):
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        return str(obj)

    @staticmethod
    def _debug_render(obj):
        """JSON-shaped rendering for the Debug payload dump, except strings
        containing newlines become indented triple-quoted blocks with REAL
        line breaks. The system prompt (with its ## Skill blocks) is the main
        thing a human opens this dump for, and as a JSON string it was one
        endless \\n-escaped line that hid the very content being verified
        (live confusion 2026-08-07). Deliberately NOT valid JSON — display
        only; the wire payload is unaffected."""
        def render(o, indent):
            pad = "  " * indent
            inner = "  " * (indent + 1)
            if isinstance(o, dict):
                if not o:
                    return "{}"
                parts = [f"{inner}{json.dumps(str(k), ensure_ascii=False)}: "
                         f"{render(v, indent + 1)}" for k, v in o.items()]
                return "{\n" + ",\n".join(parts) + "\n" + pad + "}"
            if isinstance(o, (list, tuple)):
                if not o:
                    return "[]"
                parts = [f"{inner}{render(v, indent + 1)}" for v in o]
                return "[\n" + ",\n".join(parts) + "\n" + pad + "]"
            if isinstance(o, str) and "\n" in o:
                block = "\n".join(inner + "  " + line for line in o.split("\n"))
                return '"""\n' + block + "\n" + inner + '"""'
            try:
                return json.dumps(o, ensure_ascii=False)
            except TypeError:
                return repr(o)  # display must never crash on a stray object
        return render(obj, 0)

    def _payload_for_display(self, messages):
        display_msgs = []
        for msg in messages:
            content = msg.get("content")
            if isinstance(content, list):
                content = [
                    self._make_serializable(block) if not isinstance(block, dict) else block
                    for block in content
                ]
            display_msgs.append({"role": msg["role"], "content": content})
        display_msgs = copy.deepcopy(display_msgs)

        def _truncate_images(blocks):
            for block in blocks:
                if isinstance(block, dict):
                    if block.get("type") == "image":
                        src = block.get("source", {})
                        if src.get("data"):
                            src["data"] = src["data"][:40] + "...[truncated]"
                    if block.get("type") == "tool_result" and isinstance(block.get("content"), list):
                        _truncate_images(block["content"])

        for msg in display_msgs:
            content = msg.get("content")
            if isinstance(content, list):
                _truncate_images(content)

        if self.provider in ("OpenAI", "xAI"):
            # Both speak the Responses API format and both append server-side
            # built-ins below (xAI: web_search + x_search, code_interpreter
            # gated off while Desktop is on — see xai_mixin).
            system_prompt = self._build_system_prompt()
            tools = self._get_tools()
            responses_tools = self._tools_to_responses(tools) if tools else []
            if self.provider == "OpenAI":
                responses_tools.append({"type": "web_search_preview"})
                # Code interpreter is gated off when desktop tools are enabled —
                # see _stream_responses_call for the rationale.
                if not (self.desktop_enabled.get() and _HAS_DESKTOP):
                    responses_tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
                # ...minus the built-ins this model rejected earlier in the
                # session, as _stream_responses_call strips them from the wire.
                unsupported = getattr(self, "_openai_unsupported_tools", {}).get(self.model, set())
                if unsupported:
                    responses_tools = [t for t in responses_tools
                                       if t.get("type") not in unsupported]
            else:
                # xAI — mirror _stream_xai_call's server-side built-ins
                responses_tools.append({"type": "web_search"})
                responses_tools.append({"type": "x_search"})
                if not (self.desktop_enabled.get() and _HAS_DESKTOP):
                    responses_tools.append({"type": "code_interpreter"})
            responses_input = self._messages_to_responses(display_msgs)
            # Truncate input_image data in Responses API input
            for item in responses_input:
                c = item.get("content")
                if isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and part.get("type") == "input_image":
                            url = part.get("image_url", "")
                            if isinstance(url, str) and url.startswith("data:"):
                                part["image_url"] = url[:60] + "...[truncated]"
                # A replayed reasoning item's encrypted_content is kilobytes
                # of opaque base64 — a stub, like the image data
                enc = item.get("encrypted_content")
                if isinstance(enc, str) and len(enc) > 60:
                    item["encrypted_content"] = enc[:40] + f"...[{len(enc)} chars]"
                # Also truncate images in function_call_output (legacy format)
                if item.get("type") == "function_call_output" and isinstance(item.get("output"), list):
                    for part in item["output"]:
                        if isinstance(part, dict) and part.get("type") == "input_image":
                            url = part.get("image_url", "")
                            if isinstance(url, str) and url.startswith("data:"):
                                part["image_url"] = url[:60] + "...[truncated]"
            payload = {
                "model": self.model,
                "input": responses_input,
                "instructions": system_prompt,
                "tools": responses_tools,
                "store": False,
            }
            if self.provider == "OpenAI":
                payload["include"] = self._responses_include(OPENAI_RESPONSES_INCLUDE)
                # The same per-family builder _stream_responses_call uses
                # (reasoning / temperature / text.verbosity), read-only here —
                # the dump shows exactly what the wire will carry.
                params, _notice = self._openai_model_params()
                payload.update(params)
            else:
                # xAI — the same builder _stream_xai_call uses (temperature
                # always; reasoning.summary always, with an effort only for
                # a knob model), read-only here — the dump shows exactly
                # what the wire will carry.
                payload.update(self._xai_model_params())
                include = self._responses_include(XAI_RESPONSES_INCLUDE)
                if include:
                    payload["include"] = include
        elif self.provider == "Moonshot":
            # Chat Completions format (Kimi has no Responses endpoint) — mirror
            # _stream_kimi_call: system message first, tool results as role:
            # "tool" messages, reasoning_content round-tripped per policy, and
            # the flat wire params (never temperature). Local web_search /
            # fetch_webpage stay in (no Kimi server-side tools).
            tools = self._get_tools()
            kimi_messages = ([{"role": "system", "content": self._build_system_prompt()}]
                             + self._messages_to_kimi(
                                 display_msgs,
                                 include_reasoning=self._kimi_include_reasoning()))
            # Truncate image data URLs in user-message parts
            for m in kimi_messages:
                c = m.get("content")
                if isinstance(c, list):
                    for part in c:
                        if isinstance(part, dict) and part.get("type") == "image_url":
                            url = part.get("image_url", {}).get("url", "")
                            if isinstance(url, str) and url.startswith("data:"):
                                part["image_url"]["url"] = url[:60] + "...[truncated]"
            payload = {
                "model": self.model,
                "messages": kimi_messages,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if tools:
                payload["tools"] = self._tools_to_kimi(tools)
            payload.update(self._kimi_model_params())
        elif self.provider == "Google":
            # Gemini — mirror _stream_gemini_call (2026-09-23; until then
            # Google fell through to the Anthropic-shaped dump below, which
            # named thinking / max_tokens keys Gemini never sees):
            # system_instruction + temperature always, thinking_config from
            # the ONE decision the live call makes (_gemini_thinking_kwargs:
            # the level with the checkbox on, the tier's quietest setting
            # with it off), the custom function declarations (Gemini cannot
            # mix its built-in tools with them, so there are none), and the
            # internal messages — the Content translation is not rendered.
            tools = self._get_tools()
            payload = {
                "model": self.model,
                "stream": True,
                "system_instruction": self._build_system_prompt(),
                "temperature": self.temperature,
            }
            thinking_config, quiet_style = self._gemini_thinking_kwargs()
            if thinking_config is not None:
                payload["thinking_config"] = thinking_config.model_dump(
                    mode="json", exclude_none=True)
            if quiet_style is not None:
                payload["thinking_off"] = self._gemini_quiet_describe(quiet_style)
            payload["tools"] = [{"function_declarations": [
                d.model_dump(mode="json", exclude_none=True)
                for d in self._tools_to_gemini(tools)]}] if tools else []
            payload["contents"] = display_msgs
        else:
            tools = self._get_tools()
            if self.provider == "Anthropic":
                # The same per-model triple / fallback decision the live call
                # makes (_anthropic_server_tools), so the dump shows what is
                # really declared.
                tools.extend(self._anthropic_server_tools()[0])
            payload = {
                "model": self.model,
                "stream": True,
                "system": self._build_system_prompt(),
                "tools": tools,
                "messages": display_msgs,
            }
            # Mirror _stream_anthropic_call: the model's own output ceiling
            # (the same helper the live call reads), Fable/Mythos always on
            # the thinking branch (thinking can't be disabled on them) and
            # carrying the 5.1 block-binding + fallbacks surface.
            payload["max_tokens"] = self._anthropic_output_cap()
            always_on = (self.provider == "Anthropic"
                         and self._is_anthropic_always_on_thinking())
            fable = self._anthropic_fable_features() if self.provider == "Anthropic" else None
            if self.thinking_enabled or always_on:
                support = self._model_supports_thinking()
                if support == "adaptive":
                    payload["thinking"] = {"type": "adaptive"}
                    if self.provider == "Anthropic":
                        payload["thinking"]["display"] = "summarized"
                        if fable and fable["block_binding"]:
                            payload["thinking"]["block_binding"] = fable["block_binding"]
                    if self.thinking_mode not in ("off", "adaptive"):
                        payload["output_config"] = {"effort": self.thinking_mode}
                elif support == "manual":
                    payload["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
            else:
                # Opus 5+ / Sonnet 5+ think when the param is omitted, so the
                # real call sends an explicit disable for "Off" — show it.
                if (self.provider == "Anthropic"
                        and self._anthropic_thinking_on_by_default()):
                    payload["thinking"] = {"type": "disabled"}
                # Opus 4.7+ / Fable 5 reject temperature — mirror the real call's skip
                if (self.provider != "Anthropic"
                        or (not self._anthropic_rejects_temperature()
                            and self.model not in self._anthropic_no_temperature)):
                    payload["temperature"] = self.temperature
            if fable and fable["fallbacks"]:
                payload["fallbacks"] = fable["fallbacks"]
            # Fast mode — the same gate the live request uses
            # (_anthropic_fast_active), so the dump shows what is really sent.
            if self.provider == "Anthropic" and self._anthropic_fast_active():
                payload["speed"] = "fast"
        return self._debug_render(payload)

    def _get_tools(self):
        tools = copy.deepcopy(TOOLS)
        # OpenAI/Anthropic/xAI use native server-side web search; exclude custom
        # web tools (xAI mixing custom functions with built-ins verified live
        # 2026-07-05). Gemini can't combine built-in tools with function
        # calling, so it keeps local tools.
        if self.provider in ("OpenAI", "Anthropic", "xAI"):
            tools = [t for t in tools if t["name"] not in ("web_search", "fetch_webpage")]
        # Native file tools ride along unconditionally, like read_document —
        # no checkbox; they are the reliable-editing surface for coding tasks.
        tools.extend(copy.deepcopy(FILE_TOOLS))
        if self.desktop_enabled.get() and _HAS_DESKTOP:
            desktop = copy.deepcopy(DESKTOP_TOOLS)
            # Build display info for tool description (API order: 0=primary)
            rects = self._get_display_rects()
            num_displays = len(rects) if rects else 1
            if rects:
                disp_info = ", ".join(
                    f"display {i}: {r[2]-r[0]}x{r[3]-r[1]}" for i, r in enumerate(rects)
                )
            else:
                sw, sh = pyautogui.size()
                disp_info = f"display 0: {sw}x{sh}"
            for tool in desktop:
                if tool["name"] == "screenshot":
                    if num_displays > 1:
                        tool["description"] = (
                            f"Take a screenshot. {num_displays} displays available: {disp_info}. "
                            "By default (no 'display' parameter), captures ALL displays as separate images "
                            "so you can see everything. To click on something, call screenshot again with "
                            "the specific 'display' number where the target is, then use coordinates from "
                            "THAT screenshot for mouse_click. Always take a screenshot BEFORE clicking. "
                            "TIP: For precise clicking on small targets (close buttons, icons), take a REGION "
                            "screenshot (x, y, width, height) zoomed into just that area."
                        )
                    else:
                        tool["description"] = (
                            f"Take a screenshot of the screen (resolution {disp_info.split(': ')[1]}). "
                            "Always use this FIRST to see what is on the screen before clicking or typing. "
                            "The image may be resized. For mouse_click, use the pixel coordinates as you see "
                            "them in the image — they are automatically scaled to screen coordinates. "
                            "Optionally capture only a region by specifying x, y, width, height. "
                            "TIP: For precise clicking on small targets (close buttons, icons), first take a "
                            "full screenshot to locate the target, then take a REGION screenshot zoomed into "
                            "just that area for pixel-accurate coordinates."
                        )
                    break
            # find_element is Gemini-only — strip it out for other providers so they don't see a tool they can't use
            if self.provider != "Google":
                desktop = [t for t in desktop if t["name"] != "find_element"]
            tools.extend(desktop)
        if self.browser_enabled.get():
            tools.extend(copy.deepcopy(BROWSER_TOOLS))
        # Excel live-workbook tools (xlwings) — checkbox-gated like browser;
        # _HAS_EXCEL is False when xlwings isn't installed.
        if (_HAS_EXCEL and getattr(self, "excel_enabled", None)
                and self.excel_enabled.get()):
            tools.extend(copy.deepcopy(EXCEL_TOOLS))
        # Physical tools (camera_capture via OpenCV, microphone_listen via
        # sounddevice) — their own checkbox, never part of Desktop: see the
        # PHYSICAL_TOOLS comment in constants.py. Each is offered only when
        # its own package is installed.
        if (_HAS_PHYSICAL and getattr(self, "physical_enabled", None)
                and self.physical_enabled.get()):
            installed = {"camera_capture": _HAS_CAMERA, "microphone_listen": _HAS_MICROPHONE}
            tools.extend(copy.deepcopy(t) for t in PHYSICAL_TOOLS
                         if installed.get(t["name"], True))
        if self.meta_enabled.get():
            tools.extend(copy.deepcopy(META_TOOLS))
        # MCP tools are populated by MCPMixin._list_tools_for_server at connect-time.
        # Empty when no servers configured or _HAS_MCP is False, so this is a
        # no-op for users who haven't set up MCP.
        if _HAS_MCP and getattr(self, "mcp_enabled", None) and self.mcp_enabled.get() and MCP_TOOLS:
            tools.extend(copy.deepcopy(MCP_TOOLS))
        # Google (Gmail) native tools — patch the `account` enum on each tool
        # at runtime so the model only sees actually-configured accounts.
        # If no accounts are configured, the tools are still exposed but with
        # an empty enum, which surfaces a clearer error at tool-call time than
        # silently dropping the tools would.
        if (_HAS_GOOGLE and getattr(self, "google_enabled", None)
                and self.google_enabled.get()):
            account_names = self._get_google_account_names()
            google_tools = copy.deepcopy(GOOGLE_TOOLS)
            for t in google_tools:
                props = t.get("input_schema", {}).get("properties", {})
                if "account" in props:
                    props["account"]["enum"] = account_names
            tools.extend(google_tools)
        # Proton Mail native tools — same runtime account-enum patching as Gmail.
        # Empty enum when no accounts.json present is fine; the tool call will
        # then return a clear "Unknown Proton account" error to the agent
        # rather than silently dropping the tool from the surface.
        if (_HAS_PROTONMAIL and getattr(self, "proton_enabled", None)
                and self.proton_enabled.get()):
            account_names = self._get_proton_account_names()
            proton_tools = copy.deepcopy(PROTON_TOOLS)
            for t in proton_tools:
                props = t.get("input_schema", {}).get("properties", {})
                if "account" in props:
                    props["account"]["enum"] = account_names
            tools.extend(proton_tools)
        # Outlook / Microsoft 365 native tools (Microsoft Graph) — same runtime
        # account-enum patching as Gmail/Proton. Empty enum when no accounts.json
        # is present yields a clear "Unknown Outlook account" error at call time.
        if (_HAS_OUTLOOK and getattr(self, "outlook_enabled", None)
                and self.outlook_enabled.get()):
            account_names = self._get_outlook_account_names()
            outlook_tools = copy.deepcopy(OUTLOOK_TOOLS)
            for t in outlook_tools:
                props = t.get("input_schema", {}).get("properties", {})
                if "account" in props:
                    props["account"]["enum"] = account_names
            tools.extend(outlook_tools)
        # Per-instruction hard blocklist: blocked tools are not even OFFERED to
        # the model (the dispatch gate in _execute_tool is the second, load-
        # bearing layer for anything that slips through). Applied to the fully
        # assembled list so MCP/mail tools are coverable by name too.
        tools = self._filter_blocked_tools(tools, getattr(self, "_blocked_tools", None))
        od_names = [n for n, s in self.skills.items() if s.get("mode") == "on_demand"]
        if od_names:
            tools.append({
                "name": "get_skill",
                "description": ("Retrieve the full content of an on-demand skill by name. "
                                "Each skill's purpose is listed under '## On-Demand Skills' "
                                "in the system prompt."),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "description": "Name of the skill to retrieve.",
                            "enum": od_names,
                        }
                    },
                    "required": ["skill_name"],
                },
            })
        return tools

    def _run_tool(self, block):
        """_execute_tool with a crash INSIDE a tool turned into an error
        result for the model, instead of the end of the run.

        Every do_* method catches its own exceptions and returns an error
        string, so what reaches here is a bug in the dispatch itself — an
        argument of an unexpected type, typically (2026-09-22: Gemini's
        type_text(text=12345) met `len(text)` in the label preview and 47
        calls of a docket run died on "object of type 'int' has no len()").
        The model sees a tool_result naming the exception and retries with
        the corrected argument; a warning line records it in the pane.
        """
        try:
            return self._execute_tool(block)
        except Exception as e:
            self.queue.put({"type": "warning", "content":
                f"⚠ {block.name} raised {type(e).__name__}: {e}\n"})
            return (f"Error executing {block.name}: {type(e).__name__}: {e}. "
                    "Check the argument types against the tool's schema and retry.")

    def _execute_tool(self, block):
        """Execute a single tool_use block and return the result.

        Thread-safe for parallel-safe tools (PARALLEL_SAFE_TOOLS: web_search,
        fetch_webpage, csv_search, get_skill, read_document, read_file,
        glob_files, grep_files, run_instruction). Sequential tools (desktop,
        browser, run_powershell, write_file/edit_file, user_prompt) must only
        be called from one thread.
        """
        # Per-instruction hard blocklist — FIRST gate, before any routing, so
        # it covers native, MCP, and mail tools alike. This is the deterministic
        # guarantee for unattended runs: unlike a prompt directive or a confirm
        # dialog (unanswerable when nobody is watching), a blocked tool call is
        # refused regardless of model compliance. The refusal is firm so the
        # model moves on instead of retry-looping.
        if block.name in getattr(self, "_blocked_tools", ()):
            self.queue.put({"type": "warning", "content":
                f"⚠ Blocked tool call refused: {block.name} "
                f"(per-instruction blocked_tools)\n"})
            return (f"Tool '{block.name}' is HARD-BLOCKED for this instruction "
                    "(blocked_tools). This action is not permitted under any "
                    "circumstances — do not attempt it again; continue the task "
                    "without it.")
        # MCP tools are namespaced "<server>__<tool>" — route them to the
        # MCP mixin before the static tool dispatch chain. The lookup table
        # keyed by full name is the authoritative test (substring on "__"
        # would have false positives like a future native tool with two
        # underscores in its name).
        if _HAS_MCP and block.name in getattr(self, "_mcp_tools_by_name", {}):
            self._tool_info(f"MCP: {block.name}\n")
            return self.do_mcp_call(block.name, block.input or {})
        # Gmail (Google) native tools — dispatch any block.name beginning with
        # `gmail_` to the matching do_<name> method on the GmailMixin. Cheaper
        # than enumerating every tool name; the GmailMixin owns the namespace.
        if _HAS_GOOGLE and block.name.startswith("gmail_"):
            if not getattr(self, "google_enabled", None) or not self.google_enabled.get():
                return f"Gmail is disabled. Enable the Gmail checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Gmail tool: {block.name}"
            self._tool_info(f"Gmail: {block.name}\n")
            return method(block.input or {})
        # Proton Mail native tools — same namespaced dispatch pattern.
        if _HAS_PROTONMAIL and block.name.startswith("proton_"):
            if not getattr(self, "proton_enabled", None) or not self.proton_enabled.get():
                return f"IMAP mail tools are disabled. Enable the IMAP checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Proton tool: {block.name}"
            self._tool_info(f"Proton: {block.name}\n")
            return method(block.input or {})
        # Outlook / Microsoft 365 native tools — same namespaced dispatch pattern.
        if _HAS_OUTLOOK and block.name.startswith("outlook_"):
            if not getattr(self, "outlook_enabled", None) or not self.outlook_enabled.get():
                return f"Outlook is disabled. Enable the Outlook checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Outlook tool: {block.name}"
            self._tool_info(f"Outlook: {block.name}\n")
            return method(block.input or {})
        # Excel live-workbook tools (xlwings) — same namespaced dispatch
        # pattern. The _HAS_EXCEL check is inside so a missing xlwings gives
        # a clear install hint instead of falling through to "Unknown tool".
        if block.name.startswith("excel_"):
            if not _HAS_EXCEL:
                return ("Excel tools unavailable: xlwings is not installed. "
                        "Install with: pip install xlwings (desktop Excel must "
                        "also be installed).")
            if not getattr(self, "excel_enabled", None) or not self.excel_enabled.get():
                return f"Excel tools are disabled. Enable the Excel checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Excel tool: {block.name}"
            self._tool_info(f"Excel: {block.name}\n")
            return method(block.input or {})
        # Physical tools (camera_capture) — same namespaced dispatch pattern,
        # the missing-OpenCV hint inside for the same reason as Excel's.
        if block.name.startswith("camera_"):
            if not _HAS_CAMERA:
                return ("Camera tools unavailable: OpenCV is not installed. "
                        "Install with: pip install opencv-python")
            if not getattr(self, "physical_enabled", None) or not self.physical_enabled.get():
                return f"Physical tools are disabled. Enable the Physical checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Physical tool: {block.name}"
            cam_inp = block.input or {}
            cam_wait = f"waiting {cam_inp['delay_seconds']} s, then " if cam_inp.get("delay_seconds") else ""
            self._tool_info(f"Camera: {cam_wait}taking a photo (camera {cam_inp.get('camera') or 0})...\n")
            return method(cam_inp)
        if block.name.startswith("microphone_"):
            if not _HAS_MICROPHONE:
                return ("Microphone tools unavailable: the 'sounddevice' package is not "
                        "installed. Install with: pip install sounddevice")
            if not getattr(self, "physical_enabled", None) or not self.physical_enabled.get():
                return f"Physical tools are disabled. Enable the Physical checkbox to use '{block.name}'."
            method = getattr(self, f"do_{block.name}", None)
            if method is None:
                return f"Unknown Physical tool: {block.name}"
            mic_inp = block.input or {}
            asked = mic_inp.get("seconds")
            shown = asked if asked not in (None, "") else getattr(self, "MIC_DEFAULT_SECONDS", 5)
            shown = f"{shown:g}" if isinstance(shown, (int, float)) else shown
            self._tool_info(f"Microphone: listening for {shown} s...\n")
            return method(mic_inp)
        if block.name == "web_search":
            query = block.input.get("query", "")
            self._tool_info(f"Searching: {query}\n")
            return self.search_web(query)
        if block.name == "fetch_webpage":
            url = block.input.get("url", "")
            self._tool_info(f"Fetching: {url}\n")
            return self.fetch_url(url)
        if block.name == "run_command":
            cmd = block.input.get("command", "")
            try:
                cmd_timeout = int(block.input.get("timeout") or 30)
            except (TypeError, ValueError):
                cmd_timeout = 30
            cmd_timeout = max(5, min(600, cmd_timeout))
            self._tool_info(f"Running: {cmd}\n")
            return self.run_powershell(cmd, timeout=cmd_timeout)
        if block.name == "csv_search":
            inp = block.input
            fp = inp.get("file_path", "")
            sv = inp.get("search_value", "")
            self._tool_info(f"Searching CSV: {os.path.basename(fp)} for '{sv}'\n")
            return self.do_csv_search(
                fp, sv,
                column=inp.get("column"),
                match_mode=inp.get("match_mode", "contains"),
                max_results=inp.get("max_results", 50),
                delimiter=inp.get("delimiter"),
            )
        if block.name == "read_document":
            inp = block.input or {}
            fp = inp.get("path", "")
            self._tool_info(f"Reading document: {os.path.basename(fp)}\n")
            return self.do_read_document(inp)
        if block.name in ("read_file", "write_file", "edit_file",
                          "glob_files", "grep_files"):
            # Native file tools (FileMixin) — dynamic dispatch like the mail
            # mixins; the label shows the path or pattern being operated on.
            inp = block.input or {}
            label = inp.get("path") or inp.get("pattern") or ""
            self._tool_info(f"{block.name}: {label}\n")
            return getattr(self, f"do_{block.name}")(inp)
        if block.name == "user_prompt":
            prompt_msg = block.input.get("message", "")
            self._tool_info("Requesting user input...\n")
            response = self.do_user_prompt(prompt_msg)
            images = self._take_prompt_images()
            if not response.strip():
                self.stop_requested = True
                return "[User submitted empty response — stopping agent]"
            if response.strip().lower() == CONVO_EXIT_WORD:
                # "exit" ends the run like an empty reply AND closes MyAgent
                # (constants.CONVO_EXIT_WORD): the flag is read where the
                # loop ends, beside the headless auto-close (stream_worker).
                self.stop_requested = True
                self._close_after_run = True
                return "[User typed 'exit' — stopping the agent and closing MyAgent]"
            if images:
                # Ship attached images inside the tool_result — the same
                # list-of-blocks shape screenshots use, so every provider
                # translator already handles it.
                return ([{"type": "text", "text": response}]
                        + self._prompt_image_blocks(images))
            return response
        if block.name in ("screenshot", "mouse_click", "type_text",
                             "press_key", "mouse_scroll", "open_application",
                             "find_window", "clipboard_read", "clipboard_write",
                             "wait_for_window", "read_screen_text",
                             "find_image_on_screen", "mouse_drag", "find_element"):
            if not self.desktop_enabled.get():
                return "Desktop control is disabled. Enable the Desktop checkbox to use this tool."
            inp = block.input
            click_display = inp.get("display")
            if click_display is not None:
                click_display = int(click_display)
            if block.name == "screenshot":
                display = click_display  # alias for clarity in screenshot path
                disp_label = f"display {display}" if display is not None else "all displays"
                self._tool_info(f"Taking screenshot ({disp_label})...\n")
                region = None
                if all(k in inp for k in ("x", "y", "width", "height")):
                    region = (inp["x"], inp["y"], inp["width"], inp["height"])
                return self.do_screenshot(region, display=display, grid=bool(inp.get("grid", False)))
            if block.name == "mouse_click":
                cx, cy = inp.get("x"), inp.get("y")
                if cx is None or cy is None:
                    coord = inp.get("coordinate")
                    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                        cx, cy = coord[0], coord[1]
                    else:
                        return f"mouse_click error: missing x/y coordinates. Got: {inp}"
                self._tool_info(f"Clicking at ({cx}, {cy})...\n")
                return self.do_mouse_click(
                    cx, cy,
                    button=inp.get("button", "left"),
                    clicks=int(inp.get("clicks", 1)),
                    display=click_display,
                )
            if block.name == "type_text":
                text = inp.get("text", "")
                preview = text[:50] + "..." if len(text) > 50 else text
                self._tool_info(f"Typing: {preview}\n")
                return self.do_type_text(text, interval=inp.get("interval", 0.02))
            if block.name == "press_key":
                keys = inp.get("keys", "")
                self._tool_info(f"Pressing: {keys}\n")
                return self.do_press_key(keys)
            if block.name == "mouse_scroll":
                clicks_val = int(inp.get("clicks", 0))
                sx, sy = inp.get("x"), inp.get("y")
                self._tool_info(f"Scrolling {clicks_val} clicks...\n")
                return self.do_mouse_scroll(clicks_val, x=sx, y=sy, display=click_display)
            if block.name == "open_application":
                app_name = inp.get("name", "")
                app_args = inp.get("args")
                self._tool_info(f"Opening: {app_name}{f' {app_args}' if app_args else ''}\n")
                return self.do_open_application(app_name, args=app_args)
            if block.name == "find_window":
                title = inp.get("title", "")
                self._tool_info(f"Finding windows: {title}\n")
                return self.do_find_window(title, activate=inp.get("activate", False))
            if block.name == "clipboard_read":
                self._tool_info("Reading clipboard...\n")
                return self.do_clipboard_read()
            if block.name == "clipboard_write":
                text = inp.get("text", "")
                preview = text[:50] + "..." if len(text) > 50 else text
                self._tool_info(f"Writing to clipboard: {preview}\n")
                return self.do_clipboard_write(text)
            if block.name == "wait_for_window":
                title = inp.get("title", "")
                timeout = inp.get("timeout", 10)
                self._tool_info(f"Waiting for window: {title}\n")
                return self.do_wait_for_window(title, timeout=timeout)
            if block.name == "read_screen_text":
                rx, ry, rw, rh = inp.get("x"), inp.get("y"), inp.get("width"), inp.get("height")
                if None in (rx, ry, rw, rh):
                    return f"read_screen_text error: missing region parameters. Got: {inp}"
                self._tool_info(f"OCR region ({rx},{ry} {rw}x{rh})...\n")
                return self.do_read_screen_text(rx, ry, rw, rh, display=click_display)
            if block.name == "find_image_on_screen":
                path = inp.get("image_path", "")
                self._tool_info(f"Finding image: {os.path.basename(path)}\n")
                return self.do_find_image_on_screen(path, confidence=inp.get("confidence", 0.8))
            if block.name == "mouse_drag":
                sx, sy = inp.get("start_x"), inp.get("start_y")
                ex, ey = inp.get("end_x"), inp.get("end_y")
                if None in (sx, sy, ex, ey):
                    return f"mouse_drag error: missing coordinates. Got: {inp}"
                self._tool_info(f"Dragging ({sx},{sy}) to ({ex},{ey})...\n")
                return self.do_mouse_drag(
                    sx, sy, ex, ey,
                    duration=inp.get("duration", 0.5),
                    button=inp.get("button", "left"),
                    display=click_display,
                )
            if block.name == "find_element":
                if self.provider != "Google":
                    return "find_element is only available for the Google provider (Gemini models). Use a region screenshot or grid=true overlay instead."
                description = inp.get("description", "")
                if not description:
                    return "find_element error: missing 'description' parameter."
                disp_str = f" (display {click_display})" if click_display is not None else ""
                self._tool_info(f"Locating: {description}{disp_str}\n")
                return self.do_gemini_find_element(description, display=click_display)
        elif block.name in ("browser_open", "browser_navigate",
                              "browser_click", "browser_download",
                              "browser_fill",
                              "browser_get_text", "browser_run_js",
                              "browser_screenshot", "browser_close",
                              "browser_wait_for", "browser_select",
                              "browser_get_elements"):
            if not self.browser_enabled.get():
                return "Browser tools are disabled. Enable the Browser checkbox to use this tool."
            inp = block.input
            if block.name == "browser_open":
                url = inp.get("url", "")
                self._tool_info(f"Browser: opening {url}\n")
                return self.do_browser_open(url)
            if block.name == "browser_navigate":
                url = inp.get("url", "")
                self._tool_info(f"Browser: navigating to {url}\n")
                return self.do_browser_navigate(url)
            if block.name == "browser_click":
                sel = inp.get("selector", "")
                txt = inp.get("text", "")
                target = sel or f"text='{txt}'"
                self._tool_info(f"Browser: clicking {target}\n")
                return self.do_browser_click(selector=sel or None, text=txt or None)
            if block.name == "browser_download":
                sel = inp.get("selector", "")
                txt = inp.get("text", "")
                target = sel or f"text='{txt}'"
                path = inp.get("save_path", "")
                self._tool_info(f"Browser: downloading via {target} -> {path}\n")
                return self.do_browser_download(
                    save_path=path, selector=sel or None, text=txt or None,
                    timeout_s=inp.get("timeout_s", 60))
            if block.name == "browser_fill":
                sel = inp.get("selector", "")
                val = inp.get("value", "")
                self._tool_info(f"Browser: filling {sel}\n")
                return self.do_browser_fill(sel, val)
            if block.name == "browser_get_text":
                sel = inp.get("selector", "")
                self._tool_info(f"Browser: reading text{' from ' + sel if sel else ''}...\n")
                return self.do_browser_get_text(selector=sel or None)
            if block.name == "browser_run_js":
                code = inp.get("code", "")
                preview = code[:80] + "..." if len(code) > 80 else code
                self._tool_info(f"Browser: running JS: {preview}\n")
                return self.do_browser_run_js(code)
            if block.name == "browser_screenshot":
                self._tool_info("Browser: taking screenshot...\n")
                return self.do_browser_screenshot()
            if block.name == "browser_close":
                self._tool_info("Browser: closing connection...\n")
                return self.do_browser_close()
            if block.name == "browser_wait_for":
                sel = inp.get("selector", "")
                timeout = inp.get("timeout", 10000)
                self._tool_info(f"Browser: waiting for {sel}...\n")
                return self.do_browser_wait_for(sel, timeout=timeout)
            if block.name == "browser_select":
                sel = inp.get("selector", "")
                self._tool_info(f"Browser: selecting in {sel}...\n")
                return self.do_browser_select(sel, value=inp.get("value"), label=inp.get("label"))
            if block.name == "browser_get_elements":
                sel = inp.get("selector", "")
                limit = inp.get("limit", 10)
                self._tool_info(f"Browser: getting elements {sel}...\n")
                return self.do_browser_get_elements(sel, limit=limit)
        elif block.name == "get_skill":
            skill_name = block.input.get("skill_name", "")
            self._tool_info(f"Loading skill: {skill_name}\n")
            if skill_name in self.skills and self.skills[skill_name].get("mode") == "on_demand":
                return self.skills[skill_name]["content"]
            return f"Skill not found or not on-demand: {skill_name}"
        elif block.name == "manage_instructions":
            action = block.input.get("action", "")
            self._tool_info(f"manage_instructions: {action}\n")
            return self.do_manage_instructions(block.input)
        elif block.name == "manage_skills":
            action = block.input.get("action", "")
            self._tool_info(f"manage_skills: {action}\n")
            return self.do_manage_skills(block.input)
        elif block.name == "run_instruction":
            name = block.input.get("name", "")
            headless = block.input.get("headless", True)
            mode = "headless" if headless else "GUI"
            self._tool_info(f"run_instruction: {name} ({mode})\n")
            return self.do_run_instruction(block.input)
        # Catch-all: an unknown tool name, or a family branch above (desktop/
        # browser) that matched the group test but no specific handler.
        return f"Unknown tool: {block.name}"

    def _weak_desktop_combo_warning(self):
        """Returns a warning string when the active provider/model has known
        weak spatial precision for desktop click tasks, or None if no warning
        applies. Surfaced once at agent start so the user knows why a desktop
        task may iterate or miss small targets."""
        if not (self.desktop_enabled.get() and _HAS_DESKTOP):
            return None
        if self.provider == "OpenAI":
            # gpt-5 family with reasoning effort 'none' / 'minimal' struggles
            # on precise spatial targets — verified empirically on Notepad++
            # close-button tests where the same model with effort=low or higher
            # converges quickly.
            if self._is_gpt5_family() and self.thinking_effort in ("none", "minimal"):
                return (
                    f"gpt-5 family with reasoning='{self.thinking_effort}' has limited "
                    "spatial precision for small UI targets like close buttons. "
                    "Consider switching to reasoning='low' or higher for desktop work."
                )
            if self._is_gpt5_chat_model():
                return (
                    "gpt-5 'Instant' chat variants have no reasoning capacity and tend "
                    "to miss small UI targets. Consider a non-chat gpt-5 model with "
                    "reasoning enabled for desktop work."
                )
        elif self.provider == "Google":
            # Gemini 2.x has noticeably weaker UI spatial reasoning than 3.x —
            # verified empirically on Notepad++ close-button tests where 2.5 Pro
            # consistently misidentified targets while 3.1 Pro Preview hit them.
            if self.model.startswith("gemini-2."):
                return (
                    f"{self.model} has limited spatial precision for small UI targets "
                    "like close buttons. Consider switching to gemini-3.1-pro-preview "
                    "(or any gemini-3.x) for desktop work."
                )
        elif self.provider == "xAI":
            # No Grok language model the API serves is text-only any more
            # (the live listing's input_modalities decides — grok-build-0.1
            # included since the 2026-09-23 audit); the branch stays for the
            # next one that is.
            if not self._is_xai_vision_model():
                return (
                    f"{self.model} is a text-only model — it cannot see screenshots. "
                    "Desktop/browser tools will not work with this model. "
                    "Switch to grok-4.3 (or any grok-4.x chat tier) for desktop work."
                )
        elif self.provider == "Moonshot":
            # No served Kimi model is text-only any more (the listing's
            # supports_image_in decides — the k2.7-code line included since
            # the 2026-09-23 audit); the branch stays for the next one that is.
            if not self._is_kimi_vision_model():
                return (
                    f"{self.model} is a text-only model — it cannot see screenshots. "
                    "Desktop/browser tools will not work with this model. "
                    "Switch to kimi-k2.6 or kimi-k3 (both vision-capable) "
                    "for desktop work."
                )
        elif self.provider == "Ollama":
            # Text-only local models cannot see screenshots — Ollama accepts
            # image parts without error but non-vision models drop them.
            if not self._is_ollama_vision_model():
                return (
                    f"{self.model} is a text-only model — it cannot see screenshots. "
                    "Desktop/browser tools will not work with this model. "
                    "Pull a vision model (e.g. `ollama pull muse-glimmer:30b-mlx` "
                    "or `ollama pull qwen2.5vl:32b`) for desktop work."
                )
        return None

    def _blind_camera_warning(self):
        """A warning string when the Physical tools are on but the active
        model takes no image input — camera_capture would hand it photos it
        cannot see — else None. The twin of the text-only branches above for a
        run with Physical on and Desktop off; with Desktop on, that warning has
        already said the model is blind."""
        if not (_HAS_CAMERA and getattr(self, "physical_enabled", None)
                and self.physical_enabled.get()):
            return None
        if self.desktop_enabled.get() and _HAS_DESKTOP:
            return None
        blind = ((self.provider == "xAI" and not self._is_xai_vision_model())
                 or (self.provider == "Moonshot" and not self._is_kimi_vision_model())
                 or (self.provider == "Ollama" and not self._is_ollama_vision_model()))
        if not blind:
            return None
        return (f"{self.model} is a text-only model — it cannot see photos. "
                "camera_capture will not work with this model.")

    @staticmethod
    def _pricing_match(provider, model_name, table=None):
        """The (prefix, table entry) a model prices by — the LONGEST table
        prefix the id starts with — or (None, None) when the provider has no
        table or nothing matches. The entry is raw (possibly a DatedPrice):
        _get_pricing resolves and converts it; _generic_pricing_warning only
        needs the prefix, to tell a model's own row from a family catch-all.
        An explicit ``table`` overrides the provider's standard one — how the
        fast-mode lookup (ANTHROPIC_FAST_PRICING) reuses this one
        longest-prefix implementation."""
        if table is None:
            table = {"Anthropic": ANTHROPIC_PRICING,
                     "OpenAI": OPENAI_PRICING,
                     "Google": GEMINI_PRICING,
                     "xAI": XAI_PRICING,
                     "Moonshot": KIMI_PRICING,
                     "Ollama": OLLAMA_PRICING}.get(provider)
        if not table:
            return None, None
        # Match longest prefix first for specificity
        best_prefix, best_match, best_len = None, None, 0
        for prefix, prices in table.items():
            if model_name.startswith(prefix) and len(prefix) > best_len:
                best_prefix, best_match, best_len = prefix, prices, len(prefix)
        return best_prefix, best_match

    @staticmethod
    def _generic_pricing_warning(provider, model_name):
        """A ⚠ line for stream_worker when the active model is priced by a
        family catch-all row (GENERIC_PRICING_PREFIXES) instead of one of its
        own — how every new Gemini Flash tier since 3.6 was silently
        under-priced for weeks until someone added its row (2026-09-16). None
        when the model has its own row, is unpriced, or the provider keeps no
        catch-all rows."""
        prefix, _entry = StreamingMixin._pricing_match(provider, model_name)
        if prefix is None or prefix not in GENERIC_PRICING_PREFIXES.get(provider, ()):
            return None
        return (f"{model_name} has no pricing entry of its own — it is priced by the "
                f"generic '{prefix}' fallback row, so its cost lines and cost-log "
                f"rows may be wrong (every new Gemini Flash tier since 3.6 was "
                f"under-priced this way until its row was added). Add a "
                f"{model_name} entry to the {provider} pricing table in "
                f"myagent/constants.py.")

    @staticmethod
    def _unpriced_model_warning(provider, model_name, fast=False):
        """A ⚠ line for stream_worker when a PAID provider's model has no row
        in its pricing table at all — the 2026-09-23 report: a gpt-6-sol run
        on the Mac showed no cost line and never reached the cost log,
        because the tier (created 2026-09-14) had no OPENAI_PRICING row, and
        those two symptoms are exactly what an unpriced paid model gets (no
        per-call price, and the zero-cost gate in _log_api_cost — a $0.0000
        line would claim the run was free). None when the model has a row —
        its own or a family catch-all, which _generic_pricing_warning covers
        — for Ollama (free by design, logged at $0.0000) and for xAI (its
        API reports the billed cost per call, so a missing row costs
        nothing). With ``fast`` (this run will request Anthropic fast mode),
        the FAST table is what must hold a row — fast-served calls never
        price from the standard table, so a model missing there is unpriced
        for the run however ordinary its standard row is."""
        if provider in ("Ollama", "xAI"):
            return None
        if fast and provider == "Anthropic":
            prefix, _entry = StreamingMixin._pricing_match(
                provider, model_name, table=ANTHROPIC_FAST_PRICING)
            if prefix is not None:
                return None
            return (f"{model_name} will run in FAST mode but has no row in "
                    f"ANTHROPIC_FAST_PRICING (myagent/constants.py), so its calls "
                    f"cannot be priced: no cost will be shown for this run and the "
                    f"run will NOT be written to the API cost log (a $0.0000 line "
                    f"would claim it was free — and the standard row would bill it "
                    f"at half the fast rate). Token counts are still shown per "
                    f"call. Add the model's fast row.")
        prefix, _entry = StreamingMixin._pricing_match(provider, model_name)
        if prefix is not None:
            return None
        return (f"{model_name} has no row in the {provider} pricing table "
                f"(myagent/constants.py), so its calls cannot be priced: no cost "
                f"will be shown for this run and the run will NOT be written to "
                f"the API cost log (a $0.0000 line would claim it was free). "
                f"Token counts are still shown per call. Add the model's row.")

    @staticmethod
    def _get_pricing(provider, model_name, today=None, speed=None,
                     prompt_tokens=None):
        """Look up per-token pricing for a model.
        Returns a dict with per-token prices, or None if no match.
        ``speed`` is what the provider says served the call (Anthropic's
        usage.speed): "fast" prices from ANTHROPIC_FAST_PRICING — 2x the
        standard row in every bucket — and a fast-served model MISSING there
        returns None (unpriced, warned) rather than the standard row at half
        the real rate. Any other speed, and every other provider, ignores it.
        Anthropic: {input, output, cache_write, cache_read}
        OpenAI/Gemini: {input, output, cache_read} — cache_read omitted for the
        few models with no cached tier (the OpenAI -pro ids, priced None);
        plus cache_write for the OpenAI rows that bill writes (a 4th tuple
        element — the GPT-5.6 tiers and GPT-6 Astra, 1.25x input; 2026-09-06)
        xAI/Moonshot: {input, output} — both providers supply an authoritative
        per-call cost that already nets out their cached-input discount, so a
        table rate would never be consulted.
        A table entry may be a DatedPrice (a launch promo that reverts to the
        sticker rate on a known date — Gemini 3.6/3.7/3.8 Flash through
        2026-12-31); it is resolved against ``today`` (default: the real date,
        so a long-running agent flips at the boundary; tests pin it).
        The longest-prefix step lives in _pricing_match, shared with
        _generic_pricing_warning.
        ``prompt_tokens`` (2026-10-08) is the call's whole prompt — the
        uncached input plus both cache buckets — and selects a model's
        SECOND rate card where it has one (ANTHROPIC_LONG_CONTEXT_PRICING:
        Haiku 5.5 above 100K tokens, $0.50 / $2.50): the returned dict then
        carries that card's four rates plus ``long_context`` = the
        threshold crossed. None, or a prompt at or under the line, is the
        base card; the fast lookup and every other provider ignore it."""
        if provider == "Anthropic" and speed == "fast":
            _prefix, fast_match = StreamingMixin._pricing_match(
                provider, model_name, table=ANTHROPIC_FAST_PRICING)
            if fast_match is None:
                return None
            per_token = tuple(p / 1_000_000 for p in fast_match)
            return {"input": per_token[0], "output": per_token[1],
                    "cache_write": per_token[2], "cache_read": per_token[3]}
        _prefix, best_match = StreamingMixin._pricing_match(provider, model_name)
        if best_match is None:
            return None
        best_match = resolve_price(best_match, today)
        # Convert from per-million to per-token. A None slot (an OpenAI -pro
        # tier with no cached-input rate) passes through untouched — dividing
        # it would raise.
        per_token = tuple(None if p is None else p / 1_000_000 for p in best_match)
        if provider == "Anthropic":
            priced = {"input": per_token[0], "output": per_token[1],
                      "cache_write": per_token[2], "cache_read": per_token[3]}
            if prompt_tokens is not None:
                _lc_prefix, card = StreamingMixin._pricing_match(
                    provider, model_name, table=ANTHROPIC_LONG_CONTEXT_PRICING)
                if card is not None and prompt_tokens > card[0]:
                    long_rates = tuple(p / 1_000_000 for p in card[1])
                    priced = {"input": long_rates[0], "output": long_rates[1],
                              "cache_write": long_rates[2],
                              "cache_read": long_rates[3],
                              "long_context": card[0]}
            return priced
        priced = {"input": per_token[0], "output": per_token[1]}
        # OpenAI/Gemini carry a 3rd cached-input element; a None entry means the
        # model has no cached tier, so the key is left out entirely and the
        # accumulator's pricing.get("cache_read", 0) falls back to unpriced.
        if len(per_token) > 2 and best_match[2] is not None:
            priced["cache_read"] = per_token[2]
        # A 4th element is a BILLED cache-write rate (OpenAI from GPT-5.6 on:
        # 1.25x input — terra $2.50/M, gpt-6-astra $12.50/M; added
        # 2026-09-06). Exposed only when present:
        # _openai_usage_dict moves written tokens into cache_creation_input_tokens
        # only for these rows (see _openai_bills_cache_writes), so a 3-tuple
        # family never sees a cache_write key and its writes stay full-rate input.
        if len(per_token) > 3 and best_match[3] is not None:
            priced["cache_write"] = per_token[3]
        return priced

    @staticmethod
    def _cost_split_field(split, logged_model=None):
        """The cost log's 13th field (2026-09-27): a run's cost and token
        buckets per model that served its calls — "model,cost,in,out,
        cache_write,cache_read" per model, first-used first, joined by "|" —
        written when MORE than one model served the run (a Model upgrade
        from an Agent Request reply on, or an Anthropic refusal fallback
        served by an Opus tier), or when the ONE model that served it is not
        the row's MODEL field (`logged_model`: a run upgraded on its last
        reply, whose new model never served a priced call, is logged under the
        new model — without this field the viewers credited it the original
        model's whole cost). The viewers' By-model summaries split such a run
        between its models; every other block, and a line without the field,
        reads as before. "" when there is nothing to split, and then the
        field is not written at all. Cost at six places, so the parts sum to
        the row's four-place total; a model id's field separators, which no
        provider uses, are neutralised."""
        if not split:
            return ""
        if len(split) < 2 and (logged_model is None or logged_model in split):
            return ""
        parts = []
        for model, (cost, tok_in, tok_out, tok_cw, tok_cr) in split.items():
            name = str(model).replace(",", "_").replace("|", "_").replace(";", "_")
            parts.append(f"{name},{cost:.6f},{int(tok_in)},{int(tok_out)},"
                         f"{int(tok_cw)},{int(tok_cr)}")
        return "|".join(parts)

    def _log_api_cost(self, total_cost, had_usage=False, duration_secs=None,
                      instruction="", calls=None, tokens=None, split=None,
                      chat=None, provider=None, model=None, params=None,
                      timestamp=None):
        """Append the run's final cumulative cost to this machine's cost log.

        Called once when stream_worker's agentic loop ends (GUI and headless).
        Line format:
        {timestamp};{provider};{model};{cost};{params};{secs};{instruction};{calls}
        ;{in};{out};{cache_write};{cache_read}[;{split}[;{chat}]]
        — split (13th field, 2026-09-27, `_cost_split_field`) is present
        only for a run served by more than one model, or by one model other
        than the MODEL field, which is the model the run ENDED on;
        — chat (14th field, 2026-10-07) is the stem of the chat files the
        run's transcript is saved under — <chat>.json + .txt in CHATS_DIR,
        the saved_chats folder of the OneDrive share since 2026-10-08 (every
        machine's runs in one folder, so the file this names opens anywhere),
        the "Save Chat as" name, which every MyAgent run has since the same
        day (chat_mixin._name_run_chat) — whitespace-collapsed with any ';'
        turned into ',' like the instruction. When it is written the 13th
        field is ALWAYS present, blank for a one-model run, so a chat name
        can never be read as a split; a blank / None chat (SelfBot's caller,
        a bare host) keeps the 12-/13-field shapes exactly → the viewers'
        CHAT column, rightmost after INSTRUCTION;
        — params is the compact _get_model_param_summary() string
        (comma-joined), so the log records the thinking/temperature settings
        the run used alongside its cost; secs (6th field, 2026-08-12) is the
        run's wall-clock duration in whole seconds — minus any time the run
        sat waiting on user input (user_prompt / confirmation dialogs, since
        2026-08-13), so it measures the agent working, not the user's
        response latency — blank when duration_secs is None; instruction
        (7th field, 2026-08-16) is the name of the saved Agent Instruction
        the run was launched from (agent_instruction_name, snapshotted at
        run start — blank for an ad-hoc run), whitespace-collapsed and with
        any ';' turned into ',' so a name can never split the line; calls
        (8th field, 2026-08-16) is the run's API-call count — the "Call #N"
        counter in the output window, one per round-trip of the agentic loop,
        so every call after the first is a tool-use round-trip — blank when
        None. tokens (9th-12th fields, 2026-09-14) is the run's cumulative
        (input, output, cache_write, cache_read) token counts — the same four
        buckets _get_pricing rates, summed over every call of the agentic
        loop; they were already accumulated for the output window's running
        totals and merely discarded at log time, which left the log unable to
        answer "what is my blended $/MTok for this model?" (cost alone can't
        separate a 5x rate from a 40x cache-read discount). Written as plain
        integers, blank when tokens is None (SelfBot's caller, and any run
        whose provider returned no usage). input_tokens is the NON-cached
        count and the cache buckets are disjoint for EVERY provider — each
        normalizer subtracts a subset-style cache hit before emitting
        (OpenAI/Gemini since 2026-07-31, xAI/Kimi since 2026-09-15, when the
        gross pass-through was caught double-counting here), and Ollama's
        counts are summed although it is unpriced — so in+cache_write+cache_read
        is the run's true billed input volume — do not add input to a cache
        total expecting a subtotal. Older 4-/5-/6-/8-field lines stay valid
        and render blank PARAMETERS / TIME(sec) / INSTRUCTION / CALLS /
        TOKENS columns in the viewers.
        total_cost is the last cost displayed in the output window.
        Ollama runs are deliberately free but still logged (cost 0.0000) when
        at least one call returned usage (had_usage) — local activity shows in
        the viewers without inflating any spend total. Other zero-cost runs
        (a paid provider's unmatched model prefix, or a STOP before the first
        API result) stay skipped: a false $0.0000 line for a PAID provider
        would claim the run was free when the truth is the price is unknown.
        The log
        (APICOST_LOG_FILE via datapaths.resolve_costlog) is
        APICostLog_<machine>.txt in the OneDrive share — per-machine files
        never conflict-fork, yet every machine's spend syncs everywhere for
        the viewers to aggregate — falling back to repo-root APICostLog.txt
        on solo machines. Best-effort: any I/O failure is reported but never
        interrupts the run. Returns True when a line was written.
        provider / model / params / timestamp (2026-10-08) override the
        live fields for a line written on behalf of ANOTHER run — the
        in-progress record a killed instance left behind
        (_run_progress_fold_in): the record's own provider and model, its
        raw parameter summary (joined here like the live one) and the time
        of its last completed call, never this process's. None = the live
        value, so every existing caller writes exactly what it wrote."""
        provider = provider or self.provider
        model = model or self.model
        if not total_cost or total_cost <= 0:
            if not (provider == "Ollama" and had_usage):
                q = getattr(self, "queue", None)
                if had_usage and q is not None:
                    # A paid provider's run that made calls but could price
                    # none of them (no pricing row — the 2026-09-23 gpt-6-sol
                    # report): say so where the user looks for the line,
                    # rather than skipping in silence.
                    q.put({"type": "warning", "content": (
                        f"⚠ Run NOT written to the API cost log: {model} has no "
                        f"row in the {provider} pricing table, so its cost is "
                        f"unknown (a $0.0000 line would claim it was free). Add the "
                        f"row in myagent/constants.py.\n")})
                return False
            total_cost = 0.0
        try:
            timestamp = timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # 5th field: the title-bar parameter summary (e.g. "mode=Adaptive",
            # "reasoning=Medium temp=1"), comma-joined for log readability —
            # parts never contain ';' or ',' so the field can't split.
            summary = self._get_model_param_summary() if params is None else params
            params = ", ".join(str(summary).split())
            secs = "" if duration_secs is None else f"{duration_secs:.0f}"
            # 7th field: the instruction name is user-typed free text —
            # collapse whitespace (a stray newline would end the record
            # early) and neutralise the one character that could split it.
            instr = " ".join(str(instruction or "").split()).replace(";", ",")
            calls_s = "" if calls is None else f"{int(calls)}"
            # 9th-12th fields: the run's cumulative token buckets. All four
            # are blank together when tokens is None — a partially-filled
            # TOKENS group would read as "zero tokens billed" in the viewers
            # rather than "not recorded".
            tok_s = (";;;" if not tokens
                     else ";".join(f"{int(t)}" for t in tokens))
            # 13th field: the per-model split, only for a run more than one
            # model served (or one model other than the MODEL field) — a line
            # its MODEL field describes keeps its 12 fields exactly.
            split_s = self._cost_split_field(split, model)
            # 14th field (2026-10-07): the chat file stem, sanitised like the
            # instruction. It forces the 13th out, blank or not, so a reader
            # finds the split at 13 and the chat at 14 whatever the run did.
            chat_s = " ".join(str(chat or "").split()).replace(";", ",")
            if chat_s:
                tail = f";{split_s};{chat_s}"
            else:
                tail = f";{split_s}" if split_s else ""
            # ';' delimiter (not ',') so a comma inside a model name, the
            # params field or an instruction name can't be misread as a
            # field separator.
            line = (f"{timestamp};{provider};{model};"
                    f"{total_cost:.4f};{params};{secs};{instr};{calls_s};"
                    f"{tok_s}{tail}\n")
            rotate_log_if_needed(APICOST_LOG_FILE, APICOST_LOG_MAX_BYTES)
            # newline="\n": the per-machine logs are read cross-platform via
            # OneDrive; Windows text-mode CRLF shows as ^M in the macOS viewer.
            with open(APICOST_LOG_FILE, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
            self._tool_info(f"Logged API cost to {APICOST_LOG_FILE}: {line}")
            return True
        except Exception as e:
            self.queue.put({"type": "warning",
                            "content": f"⚠ Could not write the API cost log "
                                       f"({APICOST_LOG_FILE}): {e}\n"})
            return False

    # ── In-progress cost record (2026-10-08) ─────────────────────────────
    def _run_progress_path(self):
        """agent_run_<N>.json for this instance — None on a host without an
        instance number (the bare test hosts), which therefore never writes
        one: the real App always has _instance_num, and a test harness run
        beside a live instance must not touch that instance's record."""
        num = getattr(self, "_instance_num", None)
        if not num:
            return None
        return f"{AGENT_RUN_PREFIX}{num}.json"

    def _run_progress_write(self, total_cost, had_usage, duration_secs,
                            instruction="", calls=None, tokens=None, split=None,
                            chat=None):
        """Rewrite this instance's in-progress cost record: the exact
        arguments _log_run would hand _log_api_cost if the run ended now,
        plus the live provider / model / parameter summary and the time, so
        the line can be written later by a process that knows nothing else
        about the run. stream_worker calls it after every call's cost
        accounting (on the worker — file IO only, no Tk), and _end_run
        removes the record after the real line on both tails, so a record
        found at launch means its instance died with a run in flight: a
        reboot while parked at an Agent Request (the 2026-10-08 case — a
        US$7.77 run with a complete transcript and no line), a taskkill, a
        power cut. Atomic (mkstemp + os.replace) so a kill mid-write leaves
        the previous record, never a torn one. Best-effort: a failure is
        reported once per run and never interrupts it."""
        path = self._run_progress_path()
        if path is None:
            return
        try:
            record = {
                "version": 1,
                "written": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "provider": self.provider,
                "model": self.model,
                "params": self._get_model_param_summary(),
                "total_cost": total_cost,
                "had_usage": bool(had_usage),
                "duration_secs": duration_secs,
                "instruction": instruction or "",
                "calls": calls,
                "tokens": list(tokens) if tokens else None,
                "split": split or {},
                "chat": chat or "",
            }
            fd, tmp = tempfile.mkstemp(prefix=os.path.basename(path) + ".",
                                       suffix=".tmp", dir=os.path.dirname(path))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False)
                os.replace(tmp, path)
            except BaseException:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
        except Exception as e:
            if not getattr(self, "_run_progress_warned", False):
                self._run_progress_warned = True
                self.queue.put({"type": "warning", "content": (
                    f"⚠ Could not write the in-progress cost record ({path}): {e}\n")})

    def _run_progress_clear(self):
        """Remove this instance's in-progress record: the run ended and
        _log_run has written (or deliberately skipped) its line. Called by
        stream_worker's _end_run on both tails, AFTER _log_run."""
        self._run_progress_warned = False
        path = self._run_progress_path()
        if path is None:
            return
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as e:
            self.queue.put({"type": "warning", "content": (
                f"⚠ Could not remove the in-progress cost record ({path}): {e}\n")})

    def _run_progress_fold_in(self):
        """At launch, once this process owns its instance slot (MyAgent.py
        __init__): a record the slot's previous owner left behind means that
        instance died with a run in flight — killed from outside, since both
        loop-end tails remove the record after the line — so its cost line is
        written NOW from the record: under the record's own provider / model
        / parameters, stamped with the time of its last completed call, with
        `unfinished@call<N>` appended to PARAMETERS (the viewers show it in
        that column; the cost counts, it was spent). The zero-cost gate
        applies as it would have at the run's end. The record is removed
        whatever happens (an unreadable one is reported, not retried at every
        launch). Returns True when a line was written."""
        path = self._run_progress_path()
        if path is None or not os.path.exists(path):
            return False
        written = False
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if not isinstance(rec, dict):
                raise ValueError("not a JSON object")
            calls = rec.get("calls")
            marker = f"unfinished@call{int(calls)}" if calls else "unfinished"
            summary = str(rec.get("params") or "")
            tokens = rec.get("tokens")
            written = self._log_api_cost(
                float(rec.get("total_cost") or 0.0), bool(rec.get("had_usage")),
                rec.get("duration_secs"),
                instruction=rec.get("instruction") or "", calls=calls,
                tokens=tuple(tokens) if tokens else None,
                split=rec.get("split") or None, chat=rec.get("chat") or "",
                provider=rec.get("provider") or None,
                model=rec.get("model") or None,
                params=f"{summary} {marker}".strip(),
                timestamp=rec.get("written") or None)
            if written:
                self.queue.put({"type": "warning", "content": (
                    f"⚠ The previous run of this instance ended without its cost "
                    f"line (MyAgent was killed while it ran — chat "
                    f"{rec.get('chat') or '?'}): logged now as {marker}.\n")})
        except Exception as e:
            self.queue.put({"type": "warning", "content": (
                f"⚠ Could not fold in the in-progress cost record ({path}): {e}\n")})
        self._run_progress_clear()
        return written

    @staticmethod
    def _filter_blocked_tools(tools, blocked):
        """Strip per-instruction blocked tools from an assembled tool list.
        Pure — unit-tested directly; None/empty blocklist is a no-op."""
        if not blocked:
            return tools
        return [t for t in tools if t.get("name") not in blocked]

    @staticmethod
    def _final_assistant_text(messages):
        """The last assistant message's text — the run's 'final report'.

        Content may be a plain string, a list of dicts, or Anthropic SDK block
        objects (with .type/.text attributes); text blocks are joined."""
        for m in reversed(messages):
            if m.get("role") != "assistant":
                continue
            c = m.get("content")
            if isinstance(c, str):
                if c.strip():
                    return c
                continue
            if isinstance(c, list):
                parts = []
                for b in c:
                    if isinstance(b, dict):
                        if b.get("type") == "text" and b.get("text"):
                            parts.append(b["text"])
                    elif getattr(b, "type", None) == "text" and getattr(b, "text", ""):
                        parts.append(b.text)
                if parts:
                    return "\n".join(parts)
        return ""

    def _write_result_file(self, status, messages, error=""):
        """Persist the run outcome for a waiting parent (run_instruction wait=true).

        No-op unless launched with --result-file. Written on BOTH loop-end paths
        before the headless close is scheduled; the parent's synchronization
        point is child-process exit, so a plain write is race-free. Best-effort:
        a write failure must never break the run itself."""
        path = getattr(self, "_result_file", None)
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "instruction": getattr(self, "agent_instruction_name", ""),
                    "status": status,
                    "error": error,
                    "final_text": self._final_assistant_text(messages),
                }, f, ensure_ascii=False, indent=1)
        except Exception as e:
            self.queue.put({"type": "warning",
                            "content": f"⚠ Could not write result file: {e}\n"})

    def stream_worker(self, messages):
        # Initialized before the try so the except path can log whatever cost
        # accumulated before the failure. had_usage distinguishes a run that
        # made at least one completed call from one stopped before any result
        # — free (Ollama) runs log only in the former case. run_started feeds
        # the cost log's TIME(sec) field (wall-clock run duration; monotonic
        # so a system clock change can't produce a negative duration), minus
        # _input_wait_secs — the seconds the run sat parked on user input
        # (user_prompt / confirmation dialogs, accumulated by
        # helpers.input_wait_timer) — so TIME(sec) measures the agent
        # working, not the user's response latency. call_num — the "Call #N"
        # counter, one per API round-trip — and the instruction name feed the
        # cost log's CALLS / INSTRUCTION fields; both hoisted for the same
        # reason (the exception path logs whatever the run got through), and
        # the name is snapshotted at run start so an instruction applied
        # mid-run can't relabel the entry. The four token accumulators (the
        # 2026-09-14 TOKENS fields) are hoisted for the same reason: the
        # except path reads them, so an EARLY failure — anything raising
        # before the loop — would otherwise abort the handler with
        # UnboundLocalError and skip _write_result_file / the headless close
        # (pinned by test_early_failure_completes_the_exception_handler).
        total_cost = 0.0
        had_usage = False
        call_num = 0
        total_input_tokens = 0
        total_output_tokens = 0
        total_cache_write_tokens = 0
        total_cache_read_tokens = 0
        # The same five figures per model that served a call — model →
        # [cost, in, out, cache_write, cache_read], in first-use order — for
        # the cost log's 13th field (2026-09-27): a run served by more than one
        # model (a Model upgrade from an Agent Request reply on, or an
        # Anthropic refusal fallback) is split between them by the viewers'
        # By-model summaries. Hoisted with the totals for the same reason.
        per_model = {}
        # One ⚠ per run the first time a call's prompt crosses a model's
        # second-rate-card line (ANTHROPIC_LONG_CONTEXT_PRICING).
        long_context_noted = False
        instr_name = getattr(self, "agent_instruction_name", "")
        # The chat file stem _start_agent recorded (chat_mixin._name_run_chat),
        # snapshotted like the instruction: the box can be edited mid-run,
        # and the worker must not touch a Tk widget anyway.
        chat_name = getattr(self, "_run_chat_name", "")
        run_started = time.monotonic()
        self._input_wait_secs = 0.0
        # Set by an "exit" reply to an Agent Request (CONVO_EXIT_WORD — the
        # Convo branch below, or the user_prompt tool in _execute_tool): the
        # run ends as on "quit", then _close_if_due closes MyAgent as the
        # main window's [X] would. Per run, so a GUI session's next START
        # starts clean.
        self._close_after_run = False
        # The dropped-thinking-blocks notice is said once per run per shape
        # (_stream_anthropic_call); a new run starts with nothing said.
        self._thinking_drops_noted = None

        def _log_run():
            # The one cost-log call for both loop-end paths, so the fields
            # can't drift between the success and exception tails.
            self._log_api_cost(total_cost, had_usage,
                               max(0.0, time.monotonic() - run_started
                                   - self._input_wait_secs),
                               instruction=instr_name, calls=call_num,
                               tokens=(total_input_tokens, total_output_tokens,
                                       total_cache_write_tokens,
                                       total_cache_read_tokens),
                               split=per_model, chat=chat_name)

        def _end_run():
            # The log line first — it records the run under the model it
            # ENDED on, with an upgraded-from part — then an upgraded run
            # (model_upgrade_mixin: a stronger model from an Agent Request
            # reply on) goes back to the instruction's model for the next
            # START, and the system prompt frozen for the run is let go
            # (skills_mixin). The getattr guards keep bare test hosts without
            # the mixins working.
            _log_run()
            # The in-progress record is for a run that never gets here:
            # the line is written, so the record goes (both tails).
            self._run_progress_clear()
            end_upgrade = getattr(self, "_upgrade_end_run", None)
            if end_upgrade is not None:
                end_upgrade()
            thaw_prompt = getattr(self, "_thaw_system_prompt", None)
            if thaw_prompt is not None:
                thaw_prompt()

        def _close_if_due():
            # The one close decision for both loop-end paths. A result file
            # means this run IS a subagent (run_instruction spawn): auto-close
            # even with a GUI (headless=false watch mode), so the waiting
            # parent gets the report at loop end instead of only after the
            # user closes the child window (or the wait times out). And an
            # "exit" typed into an Agent Request dialog (CONVO_EXIT_WORD)
            # closes MyAgent the way the main window's [X] does — the same
            # _on_close, which waits for `streaming` to clear (check_queue's
            # `complete`) before _finish_close saves the state file and the
            # chat, tears down browser / MCP and releases the instance lock.
            if (self._headless or self._result_file
                    or getattr(self, "_close_after_run", False)):
                self.root.after(500, self._on_close)

        try:
            # Sync temperature from spinbox
            try:
                self.temperature = max(0.0, min(1.0, self._temp_var.get()))
            except (tk.TclError, ValueError):
                pass

            # Surface a warning at agent start when the active provider/model is
            # known to be weak at small-target click work. The user can switch
            # models without restarting if they want better accuracy.
            weak_warning = (self._weak_desktop_combo_warning()
                            or self._blind_camera_warning())
            if weak_warning:
                self.queue.put({
                    "type": "tool_info",
                    "content": f"⚠ {weak_warning}\n",
                })

            # ...and when the model is priced by a family catch-all row rather
            # than one of its own (a new Gemini Flash tier before its row is
            # added — 3.6, 3.7 and 3.8 each ran that way for weeks): every
            # cost line of the run may be wrong, so this is a "warning"
            # (always shown, Activity or not), not Activity-gated tool_info.
            generic_pricing = self._generic_pricing_warning(self.provider, self.model)
            if generic_pricing:
                self.queue.put({"type": "warning",
                                "content": f"⚠ {generic_pricing}\n"})
            # ...and when a paid provider's model has NO row at all (a new
            # tier before its row is added — gpt-6-sol ran nine days that
            # way): no cost line and no cost-log line would follow, in
            # silence, so this too is an always-shown warning. A fast-mode
            # run needs its row in the FAST table instead (the getattr guard
            # keeps bare test hosts without the UI mixin working).
            fast_run = (self.provider == "Anthropic"
                        and getattr(self, "fast_mode", False)
                        and self._anthropic_fast_active())
            unpriced = self._unpriced_model_warning(self.provider, self.model,
                                                    fast=fast_run)
            if unpriced:
                self.queue.put({"type": "warning", "content": f"⚠ {unpriced}\n"})

            # Re-post provider/model drift warnings from the last instruction
            # restore: _start_agent wipes the output window, so anything queued
            # during an -l auto-launch restore has already been erased by now.
            # Without this, an unattended run that silently substituted its
            # model leaves no trace in the window or the saved transcript.
            for drift in getattr(self, "_model_drift_warnings", []):
                self.queue.put({"type": "warning", "content": drift})

            # MCP servers connect in the background (mcp_mixin): a run that
            # offers their tools waits here, on the worker, until they are up —
            # the getattr guard keeps bare test hosts without the mixin working.
            wait_mcp = getattr(self, "_mcp_wait_ready", None)
            if wait_mcp is not None:
                wait_mcp()

            # Every call of the run sends the system prompt the first one does
            # (skills_mixin._build_system_prompt says why): frozen here, let go
            # by _end_run.
            freeze_prompt = getattr(self, "_freeze_system_prompt", None)
            if freeze_prompt is not None:
                freeze_prompt()

            label_emitted = False
            if not self.thinking_enabled:
                self.queue.put({"type": "label"})
                label_emitted = True

            user_prompt_count = 0
            user_prompt_nudges = 0
            # Set when a reply was cut off (stop_reason max_tokens / context
            # window): the result file then says why the run ended early.
            truncated_note = ""
            while True:
                # Check stop request between API calls
                if self.stop_requested:
                    self._tool_info("Agent stopped by user.\n")
                    break

                call_num += 1
                # Mirrored on the instance for the model upgrade's "left the
                # first model at call #N" line (model_upgrade_mixin).
                self._run_call_num = call_num
                if call_num > 1:
                    self.queue.put({"type": "ensure_newline"})
                payload_text = self._payload_for_display(messages)
                self.queue.put({"type": "call_counter", "content": call_num})
                self.queue.put({"type": "debug", "content": payload_text})

                max_retries = 10

                # Dispatch to provider-specific streaming
                if self.provider == "OpenAI":
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_responses_call(messages, max_retries, label_emitted)
                elif self.provider == "Google":
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_gemini_call(messages, max_retries, label_emitted)
                elif self.provider == "xAI":
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_xai_call(messages, max_retries, label_emitted)
                elif self.provider == "Moonshot":
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_kimi_call(messages, max_retries, label_emitted)
                elif self.provider == "Ollama":
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_ollama_call(messages, max_retries, label_emitted)
                else:
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_anthropic_call(messages, max_retries, label_emitted)

                # Accumulate tokens, then cost
                if usage:
                    had_usage = True
                    # Summed for EVERY call that reported usage, priced or not,
                    # so a free Ollama run's 0.0000 line still carries its
                    # token counts (the zero-cost gate in _log_api_cost still
                    # drops an unpriced PAID model's line).
                    call_input = usage.get("input_tokens", 0)
                    call_output = usage.get("output_tokens", 0)
                    call_cache_write = usage.get("cache_creation_input_tokens", 0)
                    call_cache_read = usage.get("cache_read_input_tokens", 0)
                    total_input_tokens += call_input
                    total_output_tokens += call_output
                    total_cache_write_tokens += call_cache_write
                    total_cache_read_tokens += call_cache_read
                    # ...and under the model that served this call (the one
                    # it is priced by, below), for the cost log's per-model
                    # split. A dated snapshot the API echoes for an alias is
                    # the same model, kept under the name the MODEL field
                    # shows. The call's cost joins part[0] once it is priced.
                    served = usage.get("model") or self.model
                    if re.fullmatch(re.escape(self.model) + r"-\d{8}", served):
                        served = self.model
                    part = per_model.setdefault(served, [0.0, 0, 0, 0, 0])
                    part[1] += call_input
                    part[2] += call_output
                    part[3] += call_cache_write
                    part[4] += call_cache_read
                    # Price by the model that actually produced the message when
                    # the provider reports one (Anthropic: a server-side refusal
                    # fallback serves the call on an Opus-tier model at ITS
                    # rates), falling back to the configured model's row — and by
                    # the SPEED that served it (usage["speed"]="fast" prices from
                    # the fast table at 2x; absent everywhere but Anthropic).
                    call_speed = usage.get("speed")
                    # The whole prompt the API counted for this call — the
                    # uncached input plus both cache buckets — selects the
                    # rate card where a model has two (Haiku 5.5 above 100K
                    # tokens: ANTHROPIC_LONG_CONTEXT_PRICING, 2026-10-08).
                    call_prompt = call_input + call_cache_write + call_cache_read
                    pricing = (self._get_pricing(self.provider, usage.get("model") or self.model,
                                                 speed=call_speed, prompt_tokens=call_prompt)
                               or self._get_pricing(self.provider, self.model,
                                                    speed=call_speed, prompt_tokens=call_prompt))
                    # xAI reports the authoritative billed cost per call
                    # (cost_in_usd_ticks → cost_usd, set in _xai_usage_dict).
                    # Prefer it over the table estimate: it already includes
                    # the cached-input discount ($0.20/M vs full input rate)
                    # and the flat $0.005 per server-side tool invocation,
                    # neither of which the 2-tuple estimate can see.
                    authoritative_cost = usage.get("cost_usd")
                    if pricing or authoritative_cost is not None:
                        if authoritative_cost is not None:
                            call_cost = authoritative_cost
                        else:
                            call_cost = (call_input * pricing["input"]
                                         + call_output * pricing["output"]
                                         + call_cache_write * pricing.get("cache_write", 0)
                                         + call_cache_read * pricing.get("cache_read", 0))
                            # Anthropic's server-side web_search bills a flat
                            # $10/1,000 per EXECUTED search on top of tokens —
                            # usage carries the count (2026-09-25; only the
                            # Anthropic usage dict has the key, and xAI's
                            # authoritative cost above already folds its own
                            # tool fees in).
                            call_cost += (usage.get("web_search_requests", 0)
                                          * ANTHROPIC_WEB_SEARCH_FEE)
                        total_cost += call_cost
                        part[0] += call_cost
                        if (pricing and pricing.get("long_context")
                                and not long_context_noted):
                            long_context_noted = True
                            self.queue.put({"type": "warning", "content": (
                                f"⚠ Prompt of {call_prompt:,} tokens (input plus cache) — over "
                                f"the {pricing['long_context']:,}-token line where {served} "
                                f"moves to its second rate card: this call, and the rest of "
                                f"the run while prompts stay this long, are priced at "
                                f"${pricing['input'] * 1e6:.2f} / ${pricing['output'] * 1e6:.2f} "
                                f"per MTok instead of the base rates.\n")})
                        self.queue.put({
                            "type": "cost_update",
                            "call_cost": call_cost,
                            "total_cost": total_cost,
                            "input_tokens": call_input,
                            "output_tokens": call_output,
                            "cache_write_tokens": call_cache_write,
                            "cache_read_tokens": call_cache_read,
                            "total_input_tokens": total_input_tokens,
                            "total_output_tokens": total_output_tokens,
                        })
                    else:
                        # No table row and no authoritative cost: the call's
                        # tokens still go to the window (call_cost None —
                        # check_queue's _cost_line prints them behind
                        # "unpriced"), so API usage is never silent; the
                        # run-start ⚠ has said why there is no price. Ollama
                        # has no row because it is FREE, not unknown: "free"
                        # makes the line say so.
                        self.queue.put({
                            "type": "cost_update",
                            "call_cost": None,
                            "total_cost": None,
                            "free": self.provider == "Ollama",
                            "input_tokens": call_input,
                            "output_tokens": call_output,
                            "cache_write_tokens": call_cache_write,
                            "cache_read_tokens": call_cache_read,
                            "total_input_tokens": total_input_tokens,
                            "total_output_tokens": total_output_tokens,
                        })
                    # The in-progress record (agent_run_<N>.json): the
                    # line _log_run would write if the run ended now,
                    # rewritten after every call, so a run killed from
                    # outside — a reboot while parked at an Agent Request
                    # — still gets its line at the next launch
                    # (_run_progress_fold_in). Its TIME is the working
                    # time up to this call, which excludes a wait that
                    # then begins: input_wait_timer adds its seconds only
                    # once the wait ends.
                    self._run_progress_write(
                        total_cost, had_usage,
                        max(0.0, time.monotonic() - run_started
                            - self._input_wait_secs),
                        instruction=instr_name, calls=call_num,
                        tokens=(total_input_tokens, total_output_tokens,
                                total_cache_write_tokens,
                                total_cache_read_tokens),
                        split=per_model, chat=chat_name)

                # Post-process LaTeX in the just-completed text segment
                if full_text:
                    self.queue.put({"type": "post_process_latex"})

                if self.stop_requested:
                    self._tool_info("Agent stopped by user.\n")
                    break

                if stop_reason == "pause_turn":
                    # Anthropic paused a long server-tool turn (the 20260209
                    # web search / fetch run code execution underneath, and the
                    # server's iteration limit ends the sampling loop): the
                    # partial turn goes back UNCHANGED and the next request
                    # resumes it. It used to end the run with a partial answer.
                    messages.append({"role": "assistant", "content": content_blocks})
                    full_text = ""
                    self._tool_info("Server-side tools paused the turn — resuming...\n")
                    continue
                truncated_note = ""   # describes the LAST call (Convo mode goes on)
                if stop_reason in ("max_tokens", "model_context_window_exceeded"):
                    unfinished_tool = any(
                        (b.get("type") if isinstance(b, dict) else getattr(b, "type", None))
                        == "tool_use" for b in content_blocks or ())
                    truncated_note = (
                        f"the model's reply was cut off (stop_reason={stop_reason})"
                        + ("; its unfinished tool call was not run" if unfinished_tool else ""))
                    # The ceiling named where it is the model's own (Anthropic
                    # since 2026-10-11: _anthropic_output_cap — thinking
                    # included, so a reply of nothing but thinking filled it
                    # all); a window overflow is the context, not the cap.
                    cap_fn = getattr(self, "_anthropic_output_cap", None)
                    cap = (cap_fn() if cap_fn and stop_reason == "max_tokens"
                           and self.provider == "Anthropic" else None)
                    why = (f"it filled the whole {cap:,}-token output window {self.model} "
                           "allows per call (thinking included)" if cap
                           else "it filled the whole output window the call allows")
                    self.queue.put({"type": "warning", "content":
                                    f"⚠ {truncated_note[0].upper()}{truncated_note[1:]}: "
                                    f"{why}. The turn ends here — ask for smaller steps.\n"})

                if stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": content_blocks})
                    full_text = ""   # it is inside content_blocks — never append it twice

                    # Wrap dict-based blocks (OpenAI/Gemini/xAI/Kimi/Ollama) in _ToolBlock for uniform attribute access
                    if self.provider in ("OpenAI", "Google", "xAI", "Moonshot", "Ollama"):
                        tool_blocks = [
                            _ToolBlock(b["name"], b["id"], b["input"])
                            for b in content_blocks if isinstance(b, dict) and b.get("type") == "tool_use"
                        ]
                    else:
                        tool_blocks = [b for b in content_blocks if b.type == "tool_use"]

                    # Log all tool calls up front
                    for block in tool_blocks:
                        tool_call_detail = json.dumps(
                            {"tool": block.name, "id": block.id, "input": block.input},
                            indent=2,
                        )
                        self.queue.put({"type": "tool_call_debug", "content": tool_call_detail})

                    # Partition into parallel-safe vs sequential, preserving original index
                    parallel_items = []   # [(index, block), ...]
                    sequential_items = [] # [(index, block), ...]
                    for idx, block in enumerate(tool_blocks):
                        if block.name in PARALLEL_SAFE_TOOLS:
                            parallel_items.append((idx, block))
                        else:
                            sequential_items.append((idx, block))

                    # Pre-allocate results list to preserve original order
                    tool_results_ordered = [None] * len(tool_blocks)

                    # Execute parallel-safe tools concurrently
                    if parallel_items:
                        if len(parallel_items) > 1:
                            self._tool_info(f"Running {len(parallel_items)} tools in parallel...\n")
                        with concurrent.futures.ThreadPoolExecutor(max_workers=len(parallel_items)) as executor:
                            future_map = {}
                            for idx, block in parallel_items:
                                future = executor.submit(self._run_tool, block)
                                future_map[future] = (idx, block)
                            for future in concurrent.futures.as_completed(future_map):
                                idx, block = future_map[future]
                                result = future.result()
                                tool_results_ordered[idx] = {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                }

                    # Execute sequential tools one at a time, in order
                    had_user_prompt = False
                    for idx, block in sequential_items:
                        if self.stop_requested:
                            # STOP mid-turn: the model's remaining actions (a
                            # click, a keystroke, a command) must not run —
                            # each still gets a result, so every tool_use
                            # keeps its tool_result.
                            result = "[Not run: the user pressed STOP]"
                        else:
                            result = self._run_tool(block)
                        tool_results_ordered[idx] = {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        }
                        if block.name == "user_prompt":
                            had_user_prompt = True

                    # After user_prompt, reset label so next response gets a fresh "Agent:" heading
                    if had_user_prompt:
                        label_emitted = False
                        user_prompt_count += 1
                        user_prompt_nudges = 0

                    messages.append({"role": "user", "content": tool_results_ordered})
                else:
                    # Conversational mode: when the per-instruction toggle is on,
                    # MyAgent enforces a chatbot loop by invoking do_user_prompt
                    # directly whenever the model ends a turn without calling
                    # user_prompt itself. This is the strong fallback for smaller
                    # open-weights models (Qwen3, Llama, etc.) that don't reliably
                    # follow "always call user_prompt" meta-rules — the model's
                    # behaviour no longer matters because MyAgent itself prompts
                    # the user and feeds the response back as a user message.
                    convo_mode = (getattr(self, "conversational_enabled", None)
                                  and self.conversational_enabled.get())
                    if convo_mode and full_text:
                        messages.append({"role": "assistant", "content": full_text})
                        next_msg = self.do_user_prompt(
                            "Reply, or type empty / 'quit' / 'stop' to end, "
                            "or 'exit' to end and close MyAgent.",
                            convo=True,   # an end word here ends the run: no upgrade
                        )
                        prompt_images = self._take_prompt_images()
                        if (not next_msg
                                or next_msg.strip().lower() in CONVO_END_WORDS):
                            if next_msg.strip().lower() == CONVO_EXIT_WORD:
                                # Ends like "quit", then closes MyAgent (the
                                # flag is read by _close_if_due at loop end).
                                self._close_after_run = True
                                self._tool_info("Conversation ended — 'exit' closes MyAgent.\n")
                            else:
                                self._tool_info("Conversation ended.\n")
                            full_text = ""  # already appended above; don't double-add
                            break
                        # do_user_prompt already emits user_prompt_echo from
                        # safety_mixin — no second echo needed here.
                        if prompt_images:
                            # Same list-of-blocks user message shape as the
                            # instruction images sent at agent start.
                            messages.append({
                                "role": "user",
                                "content": ([{"type": "text", "text": next_msg}]
                                            + self._prompt_image_blocks(prompt_images)),
                            })
                        else:
                            messages.append({"role": "user", "content": next_msg})
                        full_text = ""
                        label_emitted = False
                        continue
                    # If the model has called user_prompt 2+ times (established chatbot
                    # loop pattern) but ended this turn without calling it, nudge it.
                    # A single user_prompt call could be a one-off info request, so
                    # we only nudge when a repeating pattern has been established.
                    if user_prompt_count >= 2 and full_text and user_prompt_nudges < 3:
                        user_prompt_nudges += 1
                        messages.append({"role": "assistant", "content": full_text})
                        full_text = ""   # appended — a STOP next must not add it again
                        messages.append({
                            "role": "user",
                            "content": "[System: You ended your turn without calling user_prompt. "
                                       "You must call user_prompt now to get the user's next message.]"
                        })
                        self._tool_info("Model forgot user_prompt — nudging...\n")
                        label_emitted = False
                        continue
                    break

            if full_text:
                messages.append({"role": "assistant", "content": full_text})
            _end_run()
            if self.stop_requested:
                self._write_result_file("stopped", messages)
            elif truncated_note:
                self._write_result_file("error", messages, error=truncated_note)
            else:
                self._write_result_file("completed", messages)
            self.queue.put({"type": "complete"})
            _close_if_due()

        except Exception as e:
            self.queue.put({"type": "error", "content": str(e)})
            # Mirror the success path: persist whatever cost accrued before the
            # failure, and never leave a headless run as a zombie process — an
            # unattended error should exit (the auto-saved transcript records it).
            _end_run()
            self._write_result_file("error", messages, error=str(e))
            _close_if_due()
