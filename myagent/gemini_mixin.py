import base64
import copy
import json
import re
import time

from google.genai import types as genai_types

from myagent.constants import GEMINI_FALLBACK_MODELS


class GeminiMixin:

    # Gemini uses pixel coordinates from the screenshot image (same convention
    # as Anthropic and OpenAI). The previous [0, 1000] normalised system forced
    # the model to mentally convert pixel-space visual perception into [0, 1000]
    # — which empirically introduced arithmetic errors, especially at higher
    # thinking budgets where the reasoning had more chances to drift. Direct
    # pixel coordinates let the model click where it visually sees the target.
    _GEMINI_COORD_HINTS = {
        "screenshot": (
            "\n\nAlways take a fresh screenshot before interacting with the screen."
        ),
    }

    # JSON-Schema fields the google-genai SDK's strict pydantic Schema validator
    # rejects with `extra_forbidden`. Native MyAgent tools never include them,
    # but MCP servers commonly do — `@modelcontextprotocol/server-filesystem`
    # emits `$schema: "http://json-schema.org/draft-07/schema#"` on every tool,
    # which crashes FunctionDeclaration construction without stripping. Mix of
    # JSON-Schema dialect markers ($schema/$id/$ref/$defs/definitions) and
    # pure-metadata fields (title/examples/default) plus the original
    # additionalProperties (kept here for one consolidated drop list).
    _GEMINI_SCHEMA_DROPS = frozenset({
        "$schema", "$id", "$ref", "$defs", "definitions",
        "title", "examples", "default",
        "additionalProperties",
    })

    def _tools_to_gemini(self, tools):
        """Convert Anthropic tool schemas to Gemini FunctionDeclaration objects.

        Per-tool conversion is wrapped in try/except so one tool with an exotic
        JSON-Schema field we haven't taught the stripper about yet won't kill
        the entire request — the bad tool is skipped with a warning, the rest
        of the catalog still ships. Important for MCP, where third-party server
        authors decide what JSON-Schema features their tool inputs use.
        """
        declarations = []
        for tool in tools:
            schema = copy.deepcopy(tool.get("input_schema", {"type": "object", "properties": {}}))
            self._clean_schema_for_gemini(schema)
            desc = tool.get("description", "")
            desc += self._GEMINI_COORD_HINTS.get(tool["name"], "")
            try:
                declarations.append(genai_types.FunctionDeclaration(
                    name=tool["name"],
                    description=desc,
                    parameters=schema,
                ))
            except Exception as e:
                queue = getattr(self, "queue", None)
                if queue is not None:
                    queue.put({
                        "type": "tool_info",
                        "content": (
                            f"⚠ Gemini: skipping tool '{tool.get('name', '?')}' — "
                            f"schema conversion failed ({type(e).__name__}: {e})\n"
                        ),
                    })
        return declarations

    def _clean_schema_for_gemini(self, schema):
        """Recursively drop JSON-Schema fields the google-genai Schema
        validator rejects, and normalize enum constraints.

        Two-part cleanup:
        1. Drop every key in ``_GEMINI_SCHEMA_DROPS`` — dialect markers and
           pure-metadata fields that Gemini's pydantic Schema doesn't accept.
        2. For ``enum``, keep only string-typed enums (stringifying the values
           so the constraint survives the round trip); non-string enums are
           removed entirely to preserve the original type, because converting
           e.g. integer params to string confuses Gemini's coordinate reasoning
           in desktop tools. Blank ("" / whitespace-only) values are dropped —
           Gemini rejects them with "enum[i]: cannot be empty" — and an enum
           left empty afterwards is removed so the param degrades to a plain
           string instead of an impossible zero-choice constraint.
        """
        if isinstance(schema, dict):
            for k in self._GEMINI_SCHEMA_DROPS:
                schema.pop(k, None)
            if "enum" in schema and isinstance(schema["enum"], list):
                if schema.get("type") == "string":
                    # Gemini's Schema validator rejects blank enum values
                    # ("enum[i]: cannot be empty") AND empty enum lists.
                    # Stringify, then drop blank/whitespace-only entries; if
                    # nothing survives, remove the enum constraint entirely so
                    # the param degrades to a plain string. Two cases covered:
                    # proton_create_label's `parent` carries a legit "" (the
                    # top-level option, which Anthropic/OpenAI accept), and an
                    # unconfigured account enum patches in at runtime as [].
                    cleaned = [str(v) for v in schema["enum"] if str(v).strip()]
                    if cleaned:
                        schema["enum"] = cleaned
                    else:
                        schema.pop("enum")
                else:
                    schema.pop("enum")
            for v in schema.values():
                self._clean_schema_for_gemini(v)
        elif isinstance(schema, list):
            for item in schema:
                self._clean_schema_for_gemini(item)

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
                                m = re.search(r"\((\d+)x(\d+)(?:\s+pixels)?\)", tp)
                                if m:
                                    dims_w, dims_h = m.group(1), m.group(2)
                                    dims_hint = f" ({dims_w}x{dims_h} pixels)"
                                    break
                            hint = genai_types.Part.from_text(
                                text=(
                                    f"Below is the screenshot image{dims_hint} returned by "
                                    "the screenshot tool above. Use pixel (x, y) coordinates "
                                    "directly from this image when calling mouse_click — "
                                    "top-left is (0, 0), X increases rightward, Y increases "
                                    "downward. Coordinates are automatically mapped to the "
                                    "actual screen."
                                )
                            )
                            contents.append(genai_types.Content(
                                role="user", parts=[hint, *image_parts],
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
        usage_dict = None

        for attempt in range(max_retries):
            try:
                in_thinking = False
                tool_index = 0
                last_chunk = None
                response_stream = self.gemini_client.models.generate_content_stream(
                    model=self.model,
                    contents=gemini_contents,
                    config=config,
                )
                for chunk in response_stream:
                    last_chunk = chunk
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
                # Extract usage from last streaming chunk
                # Try last chunk first, then fall back to iterating all chunks
                for source in ([last_chunk] if last_chunk else []):
                    um = getattr(source, "usage_metadata", None)
                    if um:
                        inp = (getattr(um, "prompt_token_count", None)
                               or getattr(um, "input_tokens", None)
                               or (um.get("prompt_token_count") if isinstance(um, dict) else None)
                               or 0)
                        out = (getattr(um, "candidates_token_count", None)
                               or getattr(um, "output_tokens", None)
                               or (um.get("candidates_token_count") if isinstance(um, dict) else None)
                               or 0)
                        if inp or out:
                            usage_dict = {"input_tokens": inp, "output_tokens": out}
                            break
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

        return stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict

    def do_gemini_find_element(self, description, display=None):
        """Locate a UI element on a screenshot using Gemini's native spatial
        pointing. When display=N is specified, searches the cached image of
        that specific display — critical for multi-display setups where the
        target may be on a different display than whatever was captured last.
        Falls back to the most recent screenshot when display is omitted."""
        if not self.gemini_client:
            return "find_element error: Gemini client not available."
        # Resolve which image to search and which dims to use for the [0, 1000] → pixel conversion
        if display is not None and display in getattr(self, "_display_images", {}):
            image_bytes = self._display_images[display]
            _scale, _offset, dims_tuple = self._display_states[display]
            iw, ih = dims_tuple
            search_label = f"display {display}"
        else:
            image_bytes = getattr(self, "_last_screenshot_bytes", None)
            iw, ih = self._screenshot_dims
            search_label = f"display {display} (NOT FOUND, falling back to most recent capture)" if display is not None else "most recent screenshot"
        if not image_bytes:
            return ("Take a screenshot first — find_element needs a recent screenshot "
                    "to search.")
        if not (iw and ih):
            return "Take a screenshot first — no screenshot dimensions available."
        try:
            image_part = genai_types.Part.from_bytes(
                data=image_bytes,
                mime_type="image/png",
            )
            # Official Gemini pointing prompt — phrasing matches the Google docs
            # exactly so the trained pointing capability activates. Asking for a
            # custom JSON shape (single object) bypasses the trained behavior and
            # makes the model hallucinate plausible-looking but wrong coordinates.
            prompt = (
                f"Point to the {description}. "
                "The answer should follow the json format: "
                '[{"point": <point>, "label": <label1>}, ...]. '
                "The points are in [y, x] format normalized to 0-1000."
            )
            config = genai_types.GenerateContentConfig(
                temperature=0.0,
            )
            response = self.gemini_client.models.generate_content(
                model=self.model,
                contents=[image_part, prompt],
                config=config,
            )
            text = (response.text or "").strip()
            # Strip code fences defensively
            if text.startswith("```"):
                text = text.strip("`")
                if text.lower().startswith("json"):
                    text = text[4:].lstrip()
                if text.endswith("```"):
                    text = text[:-3].rstrip()
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                # Fallback: regex-extract the first [y, x] pair from the raw text
                m = re.search(r"\[\s*(\d+)\s*,\s*(\d+)\s*\]", text)
                if not m:
                    return (f"find_element: could not parse Gemini's response. "
                            f"Raw output: {text[:300]}")
                data = [{"point": [int(m.group(1)), int(m.group(2))], "label": description}]
            # Gemini's official pointing format is an array of {point, label} objects.
            # Accept both array-of-dicts (official) and single dict (legacy/custom).
            if isinstance(data, list):
                if not data:
                    return (f"find_element: '{description}' was not found in the most "
                            "recent screenshot. Try a different description or "
                            "take a fresh screenshot if the UI has changed.")
                first = data[0] if isinstance(data[0], dict) else {}
            elif isinstance(data, dict):
                first = data
            else:
                return f"find_element: unexpected response shape: {type(data).__name__}"
            point = first.get("point")
            label = first.get("label", description)
            if not point or not isinstance(point, (list, tuple)) or len(point) < 2:
                return (f"find_element: '{description}' was not found in the most "
                        "recent screenshot. Try a different description, take a "
                        "region screenshot zoomed into the suspected area, or take "
                        "a fresh screenshot if the UI has changed.")
            ny, nx = int(point[0]), int(point[1])
            # Gemini's pointing API returns [0, 1000] normalised coordinates.
            # We convert to pixels so the model can pass them directly to
            # mouse_click, which expects pixel coordinates (same convention
            # as Anthropic/OpenAI).
            ny = max(0, min(ny, 1000))
            nx = max(0, min(nx, 1000))
            px = round(nx / 1000 * iw)
            py = round(ny / 1000 * ih)
            click_hint = (
                f"mouse_click(x={px}, y={py}, display={display})"
                if display is not None
                else f"mouse_click(x={px}, y={py})"
            )
            return (
                f"Found '{label}' at pixel ({px}, {py}) in the {iw}x{ih} {search_label}. "
                f"Call {click_hint} to click on it."
            )
        except Exception as e:
            return f"find_element error: {e}"

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
                if mid.startswith(("gemini-2.0-", "gemini-1.")):
                    continue
                model_ids.append(mid)
            model_ids.sort()
            self._gemini_model_display_names = {mid: mid for mid in model_ids}
            return model_ids if model_ids else list(GEMINI_FALLBACK_MODELS)
        except Exception:
            self._gemini_model_display_names = {}
            return list(GEMINI_FALLBACK_MODELS)
