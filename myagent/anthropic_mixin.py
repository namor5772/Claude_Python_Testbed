import time

import anthropic

from myagent.constants import MAX_TOKENS, MAX_TOKENS_THINKING, MODEL_MAX_OUTPUT_TOKENS, ADAPTIVE_THINKING_MODELS


class AnthropicMixin:

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        """Execute one Anthropic API call with streaming and retry logic.
        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted)."""
        full_text = ""
        had_thinking = False

        tools = self._get_tools()
        # Add Anthropic server-side tools
        tools.append({"type": "web_search_20250305", "name": "web_search"})
        tools.append({"type": "code_execution_20250825", "name": "code_execution"})
        api_kwargs = {
            "model": self.model,
            "system": self._build_system_prompt(),
            "messages": messages,
            "tools": tools,
        }
        model_cap = MODEL_MAX_OUTPUT_TOKENS.get(self.model)
        if self.thinking_enabled:
            support = self._model_supports_thinking()
            api_kwargs["max_tokens"] = min(MAX_TOKENS_THINKING, model_cap) if model_cap else MAX_TOKENS_THINKING
            if support == "adaptive":
                api_kwargs["thinking"] = {"type": "adaptive"}
                if self.thinking_mode not in ("off", "adaptive"):
                    api_kwargs["output_config"] = {"effort": self.thinking_mode}
            elif support == "manual":
                api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        else:
            api_kwargs["max_tokens"] = min(MAX_TOKENS, model_cap) if model_cap else MAX_TOKENS
            api_kwargs["temperature"] = self.temperature

        for attempt in range(max_retries):
            try:
                with self.client.beta.messages.stream(
                        betas=["web-search-2025-03-05", "code-execution-2025-08-25", "files-api-2025-04-14"],
                        **api_kwargs) as stream:
                    in_thinking = False
                    for event in stream:
                        if self.stop_requested:
                            break
                        if event.type == "content_block_start":
                            block = event.content_block
                            if hasattr(block, "type") and block.type == "thinking":
                                in_thinking = True
                                had_thinking = True
                                self.queue.put({"type": "thinking_start"})
                            elif hasattr(block, "type") and block.type == "text":
                                if had_thinking and in_thinking:
                                    self.queue.put({"type": "thinking_end"})
                                    in_thinking = False
                                if not label_emitted:
                                    self.queue.put({"type": "label"})
                                    label_emitted = True
                            elif hasattr(block, "type") and block.type == "server_tool_use":
                                tool_name = getattr(block, "name", "")
                                if tool_name == "web_search":
                                    self.queue.put({"type": "tool_info", "content": "Searching the web...\n"})
                                elif tool_name == "code_execution":
                                    self.queue.put({"type": "tool_info", "content": "Running code execution...\n"})
                            elif hasattr(block, "type") and block.type in (
                                    "code_execution_tool_result", "bash_code_execution_tool_result",
                                    "web_search_tool_result"):
                                pass  # Results extracted from final_message post-stream
                        elif event.type == "content_block_delta":
                            delta = event.delta
                            if hasattr(delta, "type") and delta.type == "thinking_delta":
                                self.queue.put({"type": "thinking_delta", "content": delta.thinking})
                            elif hasattr(delta, "type") and delta.type == "text_delta":
                                full_text += delta.text
                                self.queue.put({"type": "text_delta", "content": delta.text})
                        elif event.type == "content_block_stop":
                            if in_thinking:
                                self.queue.put({"type": "thinking_end"})
                                in_thinking = False
                    if self.stop_requested:
                        # Stream interrupted by user — synthesize a stop result
                        return "end_turn", [{"type": "text", "text": full_text}], full_text, had_thinking, label_emitted
                    try:
                        final_message = stream.get_final_message()
                    except Exception:
                        # Stream may be incomplete — synthesize a stop result
                        return "end_turn", [{"type": "text", "text": full_text}], full_text, had_thinking, label_emitted
                # Extract code execution images from final message
                # (file IDs are only available after streaming completes)
                for block in final_message.content:
                    if getattr(block, "type", None) in ("code_execution_tool_result",
                                                          "bash_code_execution_tool_result"):
                        content = getattr(block, "content", None)
                        items = content if isinstance(content, list) else [content] if content else []
                        for item in items:
                            itype = getattr(item, "type", None)
                            if itype in ("code_execution_result", "bash_code_execution_result"):
                                stdout = getattr(item, "stdout", "")
                                if stdout:
                                    self.queue.put({"type": "tool_info", "content": stdout + "\n"})
                                for sub in getattr(item, "content", []) or []:
                                    sub_type = getattr(sub, "type", None) or ""
                                    fid = getattr(sub, "file_id", "")
                                    if fid and ("output" in sub_type or sub_type == "file"):
                                        self.queue.put({"type": "ci_image", "url": "", "file_id": fid})
                            elif itype and ("output" in itype or itype == "file"):
                                fid = getattr(item, "file_id", "")
                                if fid:
                                    self.queue.put({"type": "ci_image", "url": "", "file_id": fid})
                break  # success
            except anthropic.RateLimitError as e:
                if attempt < max_retries - 1:
                    wait = min(2 ** attempt * 5, 60)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                else:
                    raise
            except anthropic.APIStatusError as e:
                if e.status_code == 529 and attempt < max_retries - 1:
                    wait = min(2 ** attempt * 10, 90)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"API overloaded — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                else:
                    raise

        return final_message.stop_reason, final_message.content, full_text, had_thinking, label_emitted
