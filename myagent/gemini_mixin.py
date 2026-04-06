import base64
import copy
import re
import time

from google.genai import types as genai_types

from myagent.constants import (
    IS_WINDOWS,
    GEMINI_FALLBACK_MODELS,
    GEMINI_DEFAULT_MODEL,
    GEMINI_THINKING_PREFIXES,
    DESKTOP_TOOLS,
)


class GeminiMixin:

    # Gemini needs much more explicit coordinate guidance than Anthropic/OpenAI.
    # These suffixes are appended to desktop tool descriptions in _tools_to_gemini.
    _GEMINI_COORD_HINTS = {
        "screenshot": (
            "\n\nCRITICAL COORDINATE RULES: The returned image has a specific pixel "
            "resolution (stated in the response text, e.g. 1920x1080). All coordinate-"
            "based tools (mouse_click, mouse_scroll, mouse_drag, read_screen_text) "
            "use pixel positions within THIS image. Origin (0,0) is the TOP-LEFT "
            "corner. X increases rightward, Y increases downward. The bottom-right "
            "pixel is (width-1, height-1). Always take a fresh screenshot before "
            "interacting with the screen."
        ),
        "mouse_click": (
            "\n\nCRITICAL: x and y MUST be pixel coordinates taken directly from the "
            "most recent screenshot image. (0,0) is the top-left corner of the image. "
            "X is the horizontal pixel offset from the left edge, Y is the vertical "
            "pixel offset from the top edge. Do NOT use screen resolution coordinates "
            "— use the coordinates as they appear in the screenshot image."
        ),
        "mouse_scroll": (
            "\n\nIf specifying x/y position, use pixel coordinates from the most "
            "recent screenshot image (origin top-left)."
        ),
        "mouse_drag": (
            "\n\nAll coordinates (start_x, start_y, end_x, end_y) MUST be pixel "
            "positions from the most recent screenshot image. Origin (0,0) is the "
            "top-left corner."
        ),
        "read_screen_text": (
            "\n\nAll coordinates (x, y, width, height) use pixel positions from the "
            "most recent screenshot image. Origin (0,0) is the top-left corner."
        ),
    }

    def _tools_to_gemini(self, tools):
        """Convert Anthropic tool schemas to Gemini FunctionDeclaration objects."""
        declarations = []
        for tool in tools:
            schema = copy.deepcopy(tool.get("input_schema", {"type": "object", "properties": {}}))
            # Strip additionalProperties which some Gemini models reject
            self._strip_additional_properties(schema)
            desc = tool.get("description", "")
            # Append Gemini-specific coordinate guidance for desktop tools
            desc += self._GEMINI_COORD_HINTS.get(tool["name"], "")
            declarations.append(genai_types.FunctionDeclaration(
                name=tool["name"],
                description=desc,
                parameters=schema,
            ))
        return declarations

    def _strip_additional_properties(self, schema):
        """Recursively strip additionalProperties and handle enum values for Gemini."""
        if isinstance(schema, dict):
            schema.pop("additionalProperties", None)
            # Gemini only allows enum on STRING type properties.
            # For non-string enums (e.g. integer), remove the enum constraint
            # to keep the original type — converting integer params to string
            # type confuses Gemini's coordinate reasoning in desktop tools.
            if "enum" in schema and isinstance(schema["enum"], list):
                if schema.get("type") == "string":
                    schema["enum"] = [str(v) for v in schema["enum"]]
                else:
                    schema.pop("enum")
            for v in schema.values():
                self._strip_additional_properties(v)
        elif isinstance(schema, list):
            for item in schema:
                self._strip_additional_properties(item)

    @staticmethod
    def _normalize_gemini_args(args_dict):
        """Normalize Gemini protobuf args to plain Python types.

        Protobuf Struct returns all numbers as float64.  Edge cases
        (especially when schema enum types are mismatched) can produce
        string-encoded numbers like "500.0" that break downstream
        int(x) calls in shared tool methods.  Convert string numbers
        back to int/float; leave real strings (e.g. "left") untouched.
        """
        result = {}
        for k, v in args_dict.items():
            if isinstance(v, str):
                try:
                    fv = float(v)
                    result[k] = int(fv) if fv == int(fv) else fv
                except (ValueError, OverflowError):
                    result[k] = v
            else:
                result[k] = v
        return result

    def _messages_to_gemini(self, messages):
        """Convert Anthropic-format messages to Gemini Content objects."""
        contents = []
        # Build tool_use_id → tool_name lookup for function responses
        id_to_name = {}
        for msg in messages:
            if msg["role"] == "assistant" and isinstance(msg.get("content"), list):
                for block in msg["content"]:
                    btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                    if btype == "tool_use":
                        bid = getattr(block, "id", None) or (block.get("id") if isinstance(block, dict) else "")
                        bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else "")
                        if bid and bname:
                            id_to_name[bid] = bname

        for msg in messages:
            role = msg["role"]
            content = msg.get("content")

            if role == "user":
                if isinstance(content, str):
                    contents.append(genai_types.Content(
                        role="user",
                        parts=[genai_types.Part.from_text(text=content)],
                    ))
                elif isinstance(content, list):
                    # Check if this is a tool_result list
                    has_tool_result = any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in content
                    )
                    if has_tool_result:
                        parts = []
                        image_parts = []  # Collect images separately
                        all_text_parts = []  # Accumulate for dimension extraction
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "tool_result":
                                tool_id = block.get("tool_use_id", "")
                                tool_name = id_to_name.get(tool_id, "unknown")
                                tc_content = block.get("content", "")
                                # Extract text from content (may be string or list)
                                if isinstance(tc_content, list):
                                    text_parts = []
                                    for part in tc_content:
                                        if isinstance(part, dict) and part.get("type") == "text":
                                            text_parts.append(part.get("text", ""))
                                        elif isinstance(part, dict) and part.get("type") == "image":
                                            # Collect images to send in a separate Content
                                            # after function responses — Gemini doesn't
                                            # handle mixed image + function_response Parts
                                            # in the same Content reliably.
                                            src = part.get("source", {})
                                            img_data = base64.b64decode(src.get("data", ""))
                                            media_type = src.get("media_type", "image/png")
                                            image_parts.append(genai_types.Part.from_bytes(
                                                data=img_data, mime_type=media_type,
                                            ))
                                        else:
                                            text_parts.append(str(part))
                                    response_text = "\n".join(text_parts) if text_parts else ""
                                    all_text_parts.extend(text_parts)
                                else:
                                    response_text = str(tc_content) if tc_content else ""
                                parts.append(genai_types.Part.from_function_response(
                                    name=tool_name,
                                    response={"result": response_text},
                                ))
                        if parts:
                            contents.append(genai_types.Content(role="user", parts=parts))
                        # Send images in a separate user Content so the model
                        # actually sees them (function_response Content can't
                        # reliably carry inline image Parts in Gemini).
                        if image_parts:
                            # Prefix with a text hint so Gemini associates the
                            # image with the preceding screenshot tool call and
                            # knows to use its pixel coordinates for mouse_click.
                            # Include image dimensions so the model has the
                            # coordinate space right next to the image.
                            dims_hint = ""
                            dims_w, dims_h = "", ""
                            for tp in all_text_parts:
                                m = re.search(r"\((\d+)x(\d+)\)", tp)
                                if m:
                                    dims_w, dims_h = m.group(1), m.group(2)
                                    dims_hint = f" ({dims_w}x{dims_h} pixels)"
                                    break
                            hint = genai_types.Part.from_text(
                                text=(
                                    f"Below is the screenshot image{dims_hint} returned by the "
                                    "screenshot tool above. COORDINATE SYSTEM: the top-left pixel "
                                    "is (0, 0), X increases rightward, Y increases downward"
                                    + (f", bottom-right is ({int(dims_w)-1}, {int(dims_h)-1})" if dims_w else "")
                                    + ". When calling mouse_click, use pixel coordinates "
                                    "as they appear in THIS image — they are automatically "
                                    "scaled to actual screen coordinates."
                                )
                            )
                            contents.append(genai_types.Content(
                                role="user", parts=[hint] + image_parts,
                            ))
                    else:
                        # User message with text + images
                        parts = []
                        for block in content:
                            if isinstance(block, dict):
                                if block.get("type") == "text":
                                    parts.append(genai_types.Part.from_text(text=block.get("text", "")))
                                elif block.get("type") == "image":
                                    src = block.get("source", {})
                                    img_data = base64.b64decode(src.get("data", ""))
                                    media_type = src.get("media_type", "image/png")
                                    parts.append(genai_types.Part.from_bytes(
                                        data=img_data, mime_type=media_type,
                                    ))
                            elif isinstance(block, str):
                                parts.append(genai_types.Part.from_text(text=block))
                        if parts:
                            contents.append(genai_types.Content(role="user", parts=parts))

            elif role == "assistant":
                if isinstance(content, str):
                    contents.append(genai_types.Content(
                        role="model",
                        parts=[genai_types.Part.from_text(text=content)],
                    ))
                elif isinstance(content, list):
                    parts = []
                    for block in content:
                        btype = getattr(block, "type", None) or (block.get("type") if isinstance(block, dict) else None)
                        if btype == "gemini_thinking":
                            # Reconstruct Gemini thinking part with thought=True
                            t = block.get("text") if isinstance(block, dict) else ""
                            if t:
                                parts.append(genai_types.Part(text=t, thought=True))
                        elif btype == "text":
                            t = getattr(block, "text", None) or (block.get("text") if isinstance(block, dict) else "")
                            if t:
                                parts.append(genai_types.Part.from_text(text=t))
                        elif btype == "tool_use":
                            bname = getattr(block, "name", None) or (block.get("name") if isinstance(block, dict) else "")
                            binput = getattr(block, "input", None) or (block.get("input") if isinstance(block, dict) else {})
                            # Include thought_signature if present (required by Gemini thinking models)
                            ts = block.get("thought_signature") if isinstance(block, dict) else getattr(block, "thought_signature", None)
                            if ts:
                                # Ensure bytes — may be base64 string if loaded from saved chat
                                if isinstance(ts, str):
                                    ts = base64.b64decode(ts)
                                parts.append(genai_types.Part(
                                    function_call=genai_types.FunctionCall(name=bname, args=binput),
                                    thought_signature=ts,
                                ))
                            else:
                                parts.append(genai_types.Part.from_function_call(
                                    name=bname, args=binput,
                                ))
                        # Skip thinking/redacted_thinking blocks
                    if parts:
                        contents.append(genai_types.Content(role="model", parts=parts))

        return contents

    def _stream_gemini_call(self, messages, max_retries, label_emitted):
        """Execute one Gemini API call with streaming and retry logic.
        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted)."""
        system_prompt = self._build_system_prompt()
        tools = self._get_tools()
        gemini_tools = [genai_types.Tool(function_declarations=self._tools_to_gemini(tools))] if tools else None
        gemini_contents = self._messages_to_gemini(messages)

        # Build config
        config_kwargs = {
            "system_instruction": system_prompt,
            "temperature": self.temperature,
        }
        if self.thinking_enabled and self._is_gemini_thinking_model():
            budget_map = {"minimal": 1024, "low": 1024, "medium": 8192, "high": 24576, "max": 32768}
            budget = budget_map.get(self.thinking_effort, 8192)
            config_kwargs["thinking_config"] = genai_types.ThinkingConfig(
                thinking_budget=budget,
            )
        if gemini_tools:
            config_kwargs["tools"] = gemini_tools

        config = genai_types.GenerateContentConfig(**config_kwargs)

        full_text = ""
        had_thinking = False
        thinking_text = ""  # accumulate thinking for message history
        tool_calls = []  # list of {name, id, input}

        for attempt in range(max_retries):
            try:
                in_thinking = False
                tool_index = 0
                response_stream = self.gemini_client.models.generate_content_stream(
                    model=self.model,
                    contents=gemini_contents,
                    config=config,
                )
                for chunk in response_stream:
                    if self.stop_requested:
                        break
                    if not chunk.candidates:
                        continue
                    candidate = chunk.candidates[0]
                    if not candidate.content or not candidate.content.parts:
                        continue
                    for part in candidate.content.parts:
                        if getattr(part, "thought", False):
                            # Thinking part
                            if not in_thinking:
                                in_thinking = True
                                had_thinking = True
                                self.queue.put({"type": "thinking_start"})
                            thinking_text += part.text or ""
                            self.queue.put({"type": "thinking_delta", "content": part.text})
                        elif part.text is not None:
                            # Regular text
                            if in_thinking:
                                self.queue.put({"type": "thinking_end"})
                                in_thinking = False
                            if not label_emitted:
                                self.queue.put({"type": "label"})
                                label_emitted = True
                            full_text += part.text
                            self.queue.put({"type": "text_delta", "content": part.text})
                        elif part.function_call is not None:
                            # Tool call
                            if in_thinking:
                                self.queue.put({"type": "thinking_end"})
                                in_thinking = False
                            fc = part.function_call
                            tool_id = f"gemini_{fc.name}_{tool_index}"
                            tool_index += 1
                            tc_entry = {
                                "name": fc.name,
                                "id": tool_id,
                                "input": self._normalize_gemini_args(dict(fc.args)) if fc.args else {},
                            }
                            # Preserve thought_signature for thinking models (required by Gemini API)
                            ts = getattr(part, "thought_signature", None)
                            if ts is not None:
                                # Keep as bytes — the SDK expects bytes when reconstructing Parts
                                tc_entry["thought_signature"] = ts if isinstance(ts, bytes) else ts.encode("utf-8") if isinstance(ts, str) else ts
                            tool_calls.append(tc_entry)
                if in_thinking:
                    self.queue.put({"type": "thinking_end"})
                break  # success
            except Exception as e:
                err_str = str(e)
                status = getattr(e, "code", 0) or getattr(e, "status_code", 0)
                is_timeout = "timed out" in err_str.lower() or "timeout" in err_str.lower() or isinstance(e, (TimeoutError, ConnectionError))
                is_rate_limit = status == 429 or "429" in err_str or "RESOURCE_EXHAUSTED" in err_str
                is_server_error = (isinstance(status, int) and status >= 500) or "500" in err_str or "503" in err_str
                if is_timeout and attempt < max_retries - 1:
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Gemini stream timed out — retrying (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    full_text = ""
                    thinking_text = ""
                    tool_calls = []
                elif is_rate_limit and attempt < max_retries - 1:
                    wait = min(2 ** attempt * 5, 60)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                    thinking_text = ""
                    tool_calls = []
                elif is_server_error and attempt < max_retries - 1:
                    wait = min(2 ** attempt * 10, 90)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"API error — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                    thinking_text = ""
                    tool_calls = []
                else:
                    raise

        # Determine stop reason
        stop_reason = "tool_use" if tool_calls else "end_turn"

        # Build content blocks in Anthropic-like dict format
        content_blocks = []
        # Preserve Gemini thinking parts for message history (required for thought_signature validation)
        if thinking_text:
            content_blocks.append({"type": "gemini_thinking", "text": thinking_text})
        if full_text:
            content_blocks.append({"type": "text", "text": full_text})
        for tc in tool_calls:
            block = {
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["name"],
                "input": tc["input"],
            }
            if "thought_signature" in tc:
                ts = tc["thought_signature"]
                # Store as base64 string for JSON serialization safety
                block["thought_signature"] = base64.b64encode(ts).decode("ascii") if isinstance(ts, bytes) else ts
            content_blocks.append(block)

        return stop_reason, content_blocks, full_text, had_thinking, label_emitted

    def _fetch_gemini_models(self):
        """Fetch available Gemini generative models."""
        if not self.gemini_client:
            return list(GEMINI_FALLBACK_MODELS)
        try:
            model_ids = []
            for m in self.gemini_client.models.list():
                mid = m.name  # e.g. "models/gemini-2.5-flash"
                # Strip "models/" prefix
                if mid.startswith("models/"):
                    mid = mid[len("models/"):]
                # Skip non-generative models
                if any(skip in mid for skip in ("embedding", "imagen", "aqa",
                                                "bisheng", "text-")):
                    continue
                # Skip deprecated models
                if mid.startswith("gemini-2.0-") or mid.startswith("gemini-1."):
                    continue
                model_ids.append(mid)
            model_ids.sort()
            self._gemini_model_display_names = {mid: mid for mid in model_ids}
            return model_ids if model_ids else list(GEMINI_FALLBACK_MODELS)
        except Exception:
            self._gemini_model_display_names = {}
            return list(GEMINI_FALLBACK_MODELS)
