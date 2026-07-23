import json
import copy
import re
import concurrent.futures
import os
import tkinter as tk
from datetime import datetime

from myagent.constants import (
    TOOLS, FILE_TOOLS, META_TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS, MCP_TOOLS,
    GOOGLE_TOOLS, PROTON_TOOLS, OUTLOOK_TOOLS, PARALLEL_SAFE_TOOLS, _HAS_DESKTOP, _HAS_MCP, _HAS_GOOGLE,
    _HAS_PROTONMAIL, _HAS_OUTLOOK,
    MAX_TOKENS, MAX_TOKENS_THINKING, MODEL_MAX_OUTPUT_TOKENS,
    ANTHROPIC_PRICING, OPENAI_PRICING, GEMINI_PRICING, XAI_PRICING,
    OLLAMA_PRICING, APICOST_LOG_FILE, APICOST_LOG_MAX_BYTES,
)
from myagent.helpers import _ToolBlock, rotate_log_if_needed

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
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tc_content = block.get("content", "")
                                call_id = block.get("tool_use_id", "")
                                # Handle content that is a list (e.g. with image blocks)
                                if isinstance(tc_content, list):
                                    text_parts = []
                                    for part in tc_content:
                                        if isinstance(part, dict) and part.get("type") == "image":
                                            src = part.get("source", {})
                                            data_url = f"data:{src.get('media_type', 'image/png')};base64,{src.get('data', '')}"
                                            deferred_images.append({
                                                "type": "input_image",
                                                "image_url": data_url,
                                            })
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

    def _make_serializable(self, obj):
        if hasattr(obj, "model_dump"):
            return obj.model_dump()
        return str(obj)

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
            # Both speak the Responses API format; xAI gets no server-side
            # tools (see xai_mixin) while OpenAI appends its built-ins.
            system_prompt = self._build_system_prompt()
            tools = self._get_tools()
            responses_tools = self._tools_to_responses(tools) if tools else []
            if self.provider == "OpenAI":
                responses_tools.append({"type": "web_search_preview"})
                # Code interpreter is gated off when desktop tools are enabled —
                # see _stream_responses_call for the rationale.
                if not (self.desktop_enabled.get() and _HAS_DESKTOP):
                    responses_tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
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
                payload["include"] = ["code_interpreter_call.outputs"]
                is_reasoning = self._is_openai_reasoning_model()
                if is_reasoning and self.thinking_enabled:
                    payload["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
                elif not is_reasoning:
                    payload["temperature"] = self.temperature
            else:
                # xAI — mirror _stream_xai_call: temperature always, reasoning
                # only for families with an effort knob.
                payload["temperature"] = self.temperature
                values = self._xai_reasoning_values()
                if values:
                    effort = (self.thinking_effort if self.thinking_effort in values
                              else ("low" if "low" in values else values[0]))
                    payload["reasoning"] = {"effort": effort, "summary": "auto"}
        else:
            tools = self._get_tools()
            if self.provider == "Anthropic":
                tools.append({"type": "web_search_20250305", "name": "web_search"})
                tools.append({"type": "code_execution_20250825", "name": "code_execution"})
            payload = {
                "model": self.model,
                "stream": True,
                "system": self._build_system_prompt(),
                "tools": tools,
                "messages": display_msgs,
            }
            model_cap = MODEL_MAX_OUTPUT_TOKENS.get(self.model)
            # Mirror _stream_anthropic_call: Fable/Mythos 5 always take the
            # thinking branch (thinking can't be disabled on them).
            always_on = (self.provider == "Anthropic"
                         and self._is_anthropic_always_on_thinking())
            if self.thinking_enabled or always_on:
                support = self._model_supports_thinking()
                payload["max_tokens"] = min(MAX_TOKENS_THINKING, model_cap) if model_cap else MAX_TOKENS_THINKING
                if support == "adaptive":
                    payload["thinking"] = {"type": "adaptive"}
                    if self.provider == "Anthropic":
                        payload["thinking"]["display"] = "summarized"
                    if self.thinking_mode not in ("off", "adaptive"):
                        payload["output_config"] = {"effort": self.thinking_mode}
                elif support == "manual":
                    payload["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
            else:
                payload["max_tokens"] = min(MAX_TOKENS, model_cap) if model_cap else MAX_TOKENS
                # Opus 4.7+ / Fable 5 reject temperature — mirror the real call's skip
                if (self.provider != "Anthropic"
                        or (not self._anthropic_rejects_temperature()
                            and self.model not in self._anthropic_no_temperature)):
                    payload["temperature"] = self.temperature
        return json.dumps(payload, indent=2)

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
            if self.provider != "Gemini":
                desktop = [t for t in desktop if t["name"] != "find_element"]
            tools.extend(desktop)
        if self.browser_enabled.get():
            tools.extend(copy.deepcopy(BROWSER_TOOLS))
        if self.meta_enabled.get():
            tools.extend(copy.deepcopy(META_TOOLS))
        # MCP tools are populated by MCPMixin._refresh_mcp_tools at connect-time.
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
                "description": "Retrieve the full content of an on-demand skill by name.",
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

    def _execute_tool(self, block):
        """Execute a single tool_use block and return the result.

        Thread-safe for parallel-safe tools (web_search, fetch_webpage,
        csv_search, get_skill, read_document, read_file, glob_files,
        grep_files). Sequential tools (desktop, browser, run_powershell,
        write_file/edit_file, user_prompt) must only be called from one thread.
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
                return f"Google/Gmail is disabled. Enable the Google checkbox to use '{block.name}'."
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
            self._tool_info(f"Running: {cmd}\n")
            return self.run_powershell(cmd)
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
            if not response.strip():
                self.stop_requested = True
                return "[User submitted empty response — stopping agent]"
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
                if self.provider != "Gemini":
                    return "find_element is only available for the Gemini provider. Use a region screenshot or grid=true overlay instead."
                description = inp.get("description", "")
                if not description:
                    return "find_element error: missing 'description' parameter."
                disp_str = f" (display {click_display})" if click_display is not None else ""
                self._tool_info(f"Locating: {description}{disp_str}\n")
                return self.do_gemini_find_element(description, display=click_display)
        elif block.name in ("browser_open", "browser_navigate",
                              "browser_click", "browser_fill",
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
        elif self.provider == "Gemini":
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
            # grok-build and the legacy grok-3 / grok-code families are
            # text-only — they cannot see screenshots at all.
            if not self._is_xai_vision_model():
                return (
                    f"{self.model} is a text-only model — it cannot see screenshots. "
                    "Desktop/browser tools will not work with this model. "
                    "Switch to grok-4.3 (or any grok-4.x chat tier) for desktop work."
                )
        elif self.provider == "Ollama":
            # Text-only local models cannot see screenshots — Ollama accepts
            # image parts without error but non-vision models drop them.
            if not self._is_ollama_vision_model():
                return (
                    f"{self.model} is a text-only model — it cannot see screenshots. "
                    "Desktop/browser tools will not work with this model. "
                    "Pull a vision variant (e.g. `ollama pull qwen2.5-vl:32b`) for desktop work."
                )
        return None

    @staticmethod
    def _get_pricing(provider, model_name):
        """Look up per-token pricing for a model.
        Returns a dict with per-token prices, or None if no match.
        Anthropic: {input, output, cache_write, cache_read}
        OpenAI/Gemini: {input, output}"""
        table = {"Anthropic": ANTHROPIC_PRICING,
                 "OpenAI": OPENAI_PRICING,
                 "Gemini": GEMINI_PRICING,
                 "xAI": XAI_PRICING,
                 "Ollama": OLLAMA_PRICING}.get(provider)
        if not table:
            return None
        # Match longest prefix first for specificity
        best_match = None
        best_len = 0
        for prefix, prices in table.items():
            if model_name.startswith(prefix) and len(prefix) > best_len:
                best_match = prices
                best_len = len(prefix)
        if best_match is None:
            return None
        # Convert from per-million to per-token
        per_token = tuple(p / 1_000_000 for p in best_match)
        if provider == "Anthropic":
            return {"input": per_token[0], "output": per_token[1],
                    "cache_write": per_token[2], "cache_read": per_token[3]}
        return {"input": per_token[0], "output": per_token[1]}

    def _log_api_cost(self, total_cost):
        """Append the run's final cumulative cost to APICostLog.txt in the repo root.

        Called once when stream_worker's agentic loop ends (GUI and headless).
        total_cost is the last cost displayed in the output window. Runs where
        no priced usage was recorded (total_cost == 0 — e.g. Ollama, an
        unmatched model prefix, or a STOP before the first API result) are
        skipped, matching the "only if relevant" display behaviour. The log
        lives in the project root (APICOST_LOG_FILE, derived from _BASE_DIR like
        agent_instructions.json/skills.json), so it works unchanged on any
        platform and from any working directory. Best-effort: any I/O failure is
        reported but never interrupts the run."""
        if not total_cost or total_cost <= 0:
            return
        try:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            # ';' delimiter (not ',') so a comma inside a model name can't be
            # misread as a field separator.
            line = f"{timestamp};{self.provider};{self.model};{total_cost:.4f}\n"
            rotate_log_if_needed(APICOST_LOG_FILE, APICOST_LOG_MAX_BYTES)
            with open(APICOST_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line)
            self._tool_info(f"Logged API cost to {APICOST_LOG_FILE}: {line}")
        except Exception as e:
            self.queue.put({"type": "warning",
                            "content": f"⚠ Could not write APICostLog.txt: {e}\n"})

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
        # accumulated before the failure.
        total_cost = 0.0
        try:
            # Sync temperature from spinbox
            try:
                self.temperature = max(0.0, min(1.0, self._temp_var.get()))
            except (tk.TclError, ValueError):
                pass

            # Surface a warning at agent start when the active provider/model is
            # known to be weak at small-target click work. The user can switch
            # models without restarting if they want better accuracy.
            weak_warning = self._weak_desktop_combo_warning()
            if weak_warning:
                self.queue.put({
                    "type": "tool_info",
                    "content": f"⚠ {weak_warning}\n",
                })

            label_emitted = False
            if not self.thinking_enabled:
                self.queue.put({"type": "label"})
                label_emitted = True

            call_num = 0
            user_prompt_count = 0
            user_prompt_nudges = 0
            # Cost tracking (total_cost itself is hoisted above the try)
            total_input_tokens = 0
            total_output_tokens = 0
            total_cache_write_tokens = 0
            total_cache_read_tokens = 0
            while True:
                # Check stop request between API calls
                if self.stop_requested:
                    self._tool_info("Agent stopped by user.\n")
                    break

                call_num += 1
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
                elif self.provider == "Gemini":
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_gemini_call(messages, max_retries, label_emitted)
                elif self.provider == "xAI":
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_xai_call(messages, max_retries, label_emitted)
                elif self.provider == "Ollama":
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_ollama_call(messages, max_retries, label_emitted)
                else:
                    stop_reason, content_blocks, full_text, _had_thinking, label_emitted, usage = \
                        self._stream_anthropic_call(messages, max_retries, label_emitted)

                # Accumulate cost
                if usage:
                    pricing = self._get_pricing(self.provider, self.model)
                    # xAI reports the authoritative billed cost per call
                    # (cost_in_usd_ticks → cost_usd, set in _stream_xai_events).
                    # Prefer it over the table estimate: it already includes
                    # the cached-input discount ($0.20/M vs full input rate)
                    # and the flat $0.005 per server-side tool invocation,
                    # neither of which the 2-tuple estimate can see.
                    authoritative_cost = usage.get("cost_usd")
                    if pricing or authoritative_cost is not None:
                        call_input = usage.get("input_tokens", 0)
                        call_output = usage.get("output_tokens", 0)
                        call_cache_write = usage.get("cache_creation_input_tokens", 0)
                        call_cache_read = usage.get("cache_read_input_tokens", 0)
                        total_input_tokens += call_input
                        total_output_tokens += call_output
                        total_cache_write_tokens += call_cache_write
                        total_cache_read_tokens += call_cache_read
                        if authoritative_cost is not None:
                            call_cost = authoritative_cost
                        else:
                            call_cost = (call_input * pricing["input"]
                                         + call_output * pricing["output"]
                                         + call_cache_write * pricing.get("cache_write", 0)
                                         + call_cache_read * pricing.get("cache_read", 0))
                        total_cost += call_cost
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

                # Post-process LaTeX in the just-completed text segment
                if full_text:
                    self.queue.put({"type": "post_process_latex"})

                if self.stop_requested:
                    self._tool_info("Agent stopped by user.\n")
                    break

                if stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": content_blocks})

                    # Wrap dict-based blocks (OpenAI/Gemini/xAI/Ollama) in _ToolBlock for uniform attribute access
                    if self.provider in ("OpenAI", "Gemini", "xAI", "Ollama"):
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
                                future = executor.submit(self._execute_tool, block)
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
                        result = self._execute_tool(block)
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
                            "Reply, or type empty / 'quit' / 'exit' / 'stop' to end."
                        )
                        if (not next_msg
                                or next_msg.strip().lower() in ("quit", "exit", "stop")):
                            self._tool_info("Conversation ended.\n")
                            full_text = ""  # already appended above; don't double-add
                            break
                        # do_user_prompt already emits user_prompt_echo from
                        # safety_mixin — no second echo needed here.
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
            self._log_api_cost(total_cost)
            self._write_result_file(
                "stopped" if self.stop_requested else "completed", messages)
            self.queue.put({"type": "complete"})
            # A result file means this run IS a subagent (run_instruction spawn):
            # auto-close even with a GUI (headless=false watch mode), so the
            # waiting parent gets the report at loop end instead of only after
            # the user closes the child window (or the wait times out).
            if self._headless or self._result_file:
                self.root.after(500, self._on_close)

        except Exception as e:
            self.queue.put({"type": "error", "content": str(e)})
            # Mirror the success path: persist whatever cost accrued before the
            # failure, and never leave a headless run as a zombie process — an
            # unattended error should exit (the auto-saved transcript records it).
            self._log_api_cost(total_cost)
            self._write_result_file("error", messages, error=str(e))
            if self._headless or self._result_file:
                self.root.after(500, self._on_close)
