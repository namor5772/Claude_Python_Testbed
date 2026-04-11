import json
import copy
import time
import re
import base64
import threading
import concurrent.futures
import os
import tkinter as tk

from myagent.constants import (
    IS_WINDOWS, TOOLS, META_TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS,
    PARALLEL_SAFE_TOOLS, _HAS_DESKTOP, _BASE_DIR, CHATS_DIR,
    MAX_TOKENS, MAX_TOKENS_THINKING, MODEL_MAX_OUTPUT_TOKENS,
)
from myagent.helpers import _ToolBlock

if _HAS_DESKTOP:
    import pyautogui


class StreamingMixin:

    def _tools_to_responses(self, tools):
        """Convert Anthropic tool schemas to OpenAI Responses API format."""
        result = []
        for tool in tools:
            result.append({
                "type": "function",
                "name": tool["name"],
                "description": tool.get("description", ""),
                "parameters": tool.get("input_schema", {"type": "object", "properties": {}}),
                "strict": False,
            })
        return result

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
                                ] + deferred_images,
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

        if self.provider == "OpenAI":
            system_prompt = self._build_system_prompt()
            tools = self._get_tools()
            responses_tools = self._tools_to_responses(tools) if tools else []
            responses_tools.append({"type": "web_search_preview"})
            # Code interpreter is gated off when desktop tools are enabled —
            # see _stream_responses_call for the rationale.
            if not (self.desktop_enabled.get() and _HAS_DESKTOP):
                responses_tools.append({"type": "code_interpreter", "container": {"type": "auto"}})
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
                "include": ["code_interpreter_call.outputs"],
            }
            is_reasoning = self._is_openai_reasoning_model()
            if is_reasoning and self.thinking_enabled:
                payload["reasoning"] = {"effort": self.thinking_effort, "summary": "auto"}
            elif not is_reasoning:
                payload["temperature"] = self.temperature
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
            if self.thinking_enabled:
                support = self._model_supports_thinking()
                payload["max_tokens"] = min(MAX_TOKENS_THINKING, model_cap) if model_cap else MAX_TOKENS_THINKING
                if support == "adaptive":
                    payload["thinking"] = {"type": "adaptive"}
                    if self.thinking_mode not in ("off", "adaptive"):
                        payload["output_config"] = {"effort": self.thinking_mode}
                elif support == "manual":
                    payload["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
            else:
                payload["max_tokens"] = min(MAX_TOKENS, model_cap) if model_cap else MAX_TOKENS
                payload["temperature"] = self.temperature
        return json.dumps(payload, indent=2)

    def _get_tools(self):
        tools = copy.deepcopy(TOOLS)
        # OpenAI/Anthropic use native server-side web search; exclude custom web tools
        # (Gemini can't combine built-in tools with function calling, so it keeps local tools)
        if self.provider in ("OpenAI", "Anthropic"):
            tools = [t for t in tools if t["name"] not in ("web_search", "fetch_webpage")]
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
        csv_search, get_skill). Sequential tools (desktop, browser,
        run_powershell, user_prompt) must only be called from one thread.
        """
        if block.name == "web_search":
            query = block.input.get("query", "")
            self.queue.put({"type": "tool_info", "content": f"Searching: {query}\n"})
            return self.search_web(query)
        elif block.name == "fetch_webpage":
            url = block.input.get("url", "")
            self.queue.put({"type": "tool_info", "content": f"Fetching: {url}\n"})
            return self.fetch_url(url)
        elif block.name == "run_command":
            cmd = block.input.get("command", "")
            self.queue.put({"type": "tool_info", "content": f"Running: {cmd}\n"})
            return self.run_powershell(cmd)
        elif block.name == "csv_search":
            inp = block.input
            fp = inp.get("file_path", "")
            sv = inp.get("search_value", "")
            self.queue.put({"type": "tool_info", "content": f"Searching CSV: {os.path.basename(fp)} for '{sv}'\n"})
            return self.do_csv_search(
                fp, sv,
                column=inp.get("column"),
                match_mode=inp.get("match_mode", "contains"),
                max_results=inp.get("max_results", 50),
                delimiter=inp.get("delimiter"),
            )
        elif block.name == "user_prompt":
            prompt_msg = block.input.get("message", "")
            self.queue.put({"type": "tool_info", "content": "Requesting user input...\n"})
            response = self.do_user_prompt(prompt_msg)
            if not response.strip():
                self.stop_requested = True
                return "[User submitted empty response — stopping agent]"
            return response
        elif block.name in ("screenshot", "mouse_click", "type_text",
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
                self.queue.put({"type": "tool_info", "content": f"Taking screenshot ({disp_label})...\n"})
                region = None
                if all(k in inp for k in ("x", "y", "width", "height")):
                    region = (inp["x"], inp["y"], inp["width"], inp["height"])
                return self.do_screenshot(region, display=display, grid=bool(inp.get("grid", False)))
            elif block.name == "mouse_click":
                cx, cy = inp.get("x"), inp.get("y")
                if cx is None or cy is None:
                    coord = inp.get("coordinate")
                    if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                        cx, cy = coord[0], coord[1]
                    else:
                        return f"mouse_click error: missing x/y coordinates. Got: {inp}"
                self.queue.put({"type": "tool_info", "content": f"Clicking at ({cx}, {cy})...\n"})
                return self.do_mouse_click(
                    cx, cy,
                    button=inp.get("button", "left"),
                    clicks=int(inp.get("clicks", 1)),
                    display=click_display,
                )
            elif block.name == "type_text":
                text = inp.get("text", "")
                preview = text[:50] + "..." if len(text) > 50 else text
                self.queue.put({"type": "tool_info", "content": f"Typing: {preview}\n"})
                return self.do_type_text(text, interval=inp.get("interval", 0.02))
            elif block.name == "press_key":
                keys = inp.get("keys", "")
                self.queue.put({"type": "tool_info", "content": f"Pressing: {keys}\n"})
                return self.do_press_key(keys)
            elif block.name == "mouse_scroll":
                clicks_val = int(inp.get("clicks", 0))
                sx, sy = inp.get("x"), inp.get("y")
                self.queue.put({"type": "tool_info", "content": f"Scrolling {clicks_val} clicks...\n"})
                return self.do_mouse_scroll(clicks_val, x=sx, y=sy, display=click_display)
            elif block.name == "open_application":
                app_name = inp.get("name", "")
                app_args = inp.get("args")
                self.queue.put({"type": "tool_info", "content": f"Opening: {app_name}{f' {app_args}' if app_args else ''}\n"})
                return self.do_open_application(app_name, args=app_args)
            elif block.name == "find_window":
                title = inp.get("title", "")
                self.queue.put({"type": "tool_info", "content": f"Finding windows: {title}\n"})
                return self.do_find_window(title, activate=inp.get("activate", False))
            elif block.name == "clipboard_read":
                self.queue.put({"type": "tool_info", "content": "Reading clipboard...\n"})
                return self.do_clipboard_read()
            elif block.name == "clipboard_write":
                text = inp.get("text", "")
                preview = text[:50] + "..." if len(text) > 50 else text
                self.queue.put({"type": "tool_info", "content": f"Writing to clipboard: {preview}\n"})
                return self.do_clipboard_write(text)
            elif block.name == "wait_for_window":
                title = inp.get("title", "")
                timeout = inp.get("timeout", 10)
                self.queue.put({"type": "tool_info", "content": f"Waiting for window: {title}\n"})
                return self.do_wait_for_window(title, timeout=timeout)
            elif block.name == "read_screen_text":
                rx, ry, rw, rh = inp.get("x"), inp.get("y"), inp.get("width"), inp.get("height")
                if None in (rx, ry, rw, rh):
                    return f"read_screen_text error: missing region parameters. Got: {inp}"
                self.queue.put({"type": "tool_info", "content": f"OCR region ({rx},{ry} {rw}x{rh})...\n"})
                return self.do_read_screen_text(rx, ry, rw, rh, display=click_display)
            elif block.name == "find_image_on_screen":
                path = inp.get("image_path", "")
                self.queue.put({"type": "tool_info", "content": f"Finding image: {os.path.basename(path)}\n"})
                return self.do_find_image_on_screen(path, confidence=inp.get("confidence", 0.8))
            elif block.name == "mouse_drag":
                sx, sy = inp.get("start_x"), inp.get("start_y")
                ex, ey = inp.get("end_x"), inp.get("end_y")
                if None in (sx, sy, ex, ey):
                    return f"mouse_drag error: missing coordinates. Got: {inp}"
                self.queue.put({"type": "tool_info", "content": f"Dragging ({sx},{sy}) to ({ex},{ey})...\n"})
                return self.do_mouse_drag(
                    sx, sy, ex, ey,
                    duration=inp.get("duration", 0.5),
                    button=inp.get("button", "left"),
                    display=click_display,
                )
            elif block.name == "find_element":
                if self.provider != "Gemini":
                    return "find_element is only available for the Gemini provider. Use a region screenshot or grid=true overlay instead."
                description = inp.get("description", "")
                if not description:
                    return "find_element error: missing 'description' parameter."
                disp_str = f" (display {click_display})" if click_display is not None else ""
                self.queue.put({"type": "tool_info", "content": f"Locating: {description}{disp_str}\n"})
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
                self.queue.put({"type": "tool_info", "content": f"Browser: opening {url}\n"})
                return self.do_browser_open(url)
            elif block.name == "browser_navigate":
                url = inp.get("url", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: navigating to {url}\n"})
                return self.do_browser_navigate(url)
            elif block.name == "browser_click":
                sel = inp.get("selector", "")
                txt = inp.get("text", "")
                target = sel or f"text='{txt}'"
                self.queue.put({"type": "tool_info", "content": f"Browser: clicking {target}\n"})
                return self.do_browser_click(selector=sel or None, text=txt or None)
            elif block.name == "browser_fill":
                sel = inp.get("selector", "")
                val = inp.get("value", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: filling {sel}\n"})
                return self.do_browser_fill(sel, val)
            elif block.name == "browser_get_text":
                sel = inp.get("selector", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: reading text{' from ' + sel if sel else ''}...\n"})
                return self.do_browser_get_text(selector=sel or None)
            elif block.name == "browser_run_js":
                code = inp.get("code", "")
                preview = code[:80] + "..." if len(code) > 80 else code
                self.queue.put({"type": "tool_info", "content": f"Browser: running JS: {preview}\n"})
                return self.do_browser_run_js(code)
            elif block.name == "browser_screenshot":
                self.queue.put({"type": "tool_info", "content": "Browser: taking screenshot...\n"})
                return self.do_browser_screenshot()
            elif block.name == "browser_close":
                self.queue.put({"type": "tool_info", "content": "Browser: closing connection...\n"})
                return self.do_browser_close()
            elif block.name == "browser_wait_for":
                sel = inp.get("selector", "")
                timeout = inp.get("timeout", 10000)
                self.queue.put({"type": "tool_info", "content": f"Browser: waiting for {sel}...\n"})
                return self.do_browser_wait_for(sel, timeout=timeout)
            elif block.name == "browser_select":
                sel = inp.get("selector", "")
                self.queue.put({"type": "tool_info", "content": f"Browser: selecting in {sel}...\n"})
                return self.do_browser_select(sel, value=inp.get("value"), label=inp.get("label"))
            elif block.name == "browser_get_elements":
                sel = inp.get("selector", "")
                limit = inp.get("limit", 10)
                self.queue.put({"type": "tool_info", "content": f"Browser: getting elements {sel}...\n"})
                return self.do_browser_get_elements(sel, limit=limit)
        elif block.name == "get_skill":
            skill_name = block.input.get("skill_name", "")
            self.queue.put({"type": "tool_info", "content": f"Loading skill: {skill_name}\n"})
            if skill_name in self.skills and self.skills[skill_name].get("mode") == "on_demand":
                return self.skills[skill_name]["content"]
            else:
                return f"Skill not found or not on-demand: {skill_name}"
        elif block.name == "manage_instructions":
            action = block.input.get("action", "")
            self.queue.put({"type": "tool_info", "content": f"manage_instructions: {action}\n"})
            return self.do_manage_instructions(block.input)
        elif block.name == "manage_skills":
            action = block.input.get("action", "")
            self.queue.put({"type": "tool_info", "content": f"manage_skills: {action}\n"})
            return self.do_manage_skills(block.input)
        elif block.name == "run_instruction":
            name = block.input.get("name", "")
            headless = block.input.get("headless", True)
            mode = "headless" if headless else "GUI"
            self.queue.put({"type": "tool_info", "content": f"run_instruction: {name} ({mode})\n"})
            return self.do_run_instruction(block.input)
        else:
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
        return None

    def stream_worker(self, messages):
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
            while True:
                # Check stop request between API calls
                if self.stop_requested:
                    self.queue.put({"type": "tool_info", "content": "Agent stopped by user.\n"})
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
                    stop_reason, content_blocks, full_text, had_thinking, label_emitted = \
                        self._stream_responses_call(messages, max_retries, label_emitted)
                elif self.provider == "Gemini":
                    stop_reason, content_blocks, full_text, had_thinking, label_emitted = \
                        self._stream_gemini_call(messages, max_retries, label_emitted)
                else:
                    stop_reason, content_blocks, full_text, had_thinking, label_emitted = \
                        self._stream_anthropic_call(messages, max_retries, label_emitted)

                # Post-process LaTeX in the just-completed text segment
                if full_text:
                    self.queue.put({"type": "post_process_latex"})

                if self.stop_requested:
                    self.queue.put({"type": "tool_info", "content": "Agent stopped by user.\n"})
                    break

                if stop_reason == "tool_use":
                    messages.append({"role": "assistant", "content": content_blocks})

                    # Wrap dict-based blocks (OpenAI/Gemini) in _ToolBlock for uniform attribute access
                    if self.provider in ("OpenAI", "Gemini"):
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
                            self.queue.put({"type": "tool_info", "content": f"Running {len(parallel_items)} tools in parallel...\n"})
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
                        self.queue.put({"type": "tool_info",
                                        "content": "Model forgot user_prompt — nudging...\n"})
                        label_emitted = False
                        continue
                    break

            if full_text:
                messages.append({"role": "assistant", "content": full_text})
            self.queue.put({"type": "complete"})
            if self._headless:
                self.root.after(500, self._on_close)

        except Exception as e:
            self.queue.put({"type": "error", "content": str(e)})
