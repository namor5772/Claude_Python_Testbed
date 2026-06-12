import time

import anthropic

from myagent.constants import MAX_TOKENS, MAX_TOKENS_THINKING, MODEL_MAX_OUTPUT_TOKENS


class AnthropicMixin:

    def _stream_anthropic_call(self, messages, max_retries, label_emitted):
        """Execute one Anthropic API call with streaming and retry logic.
        Returns (stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage)."""
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
        # Fable/Mythos 5 thinking is always on (an explicit disable is HTTP 400),
        # so a stale thinking_mode of "off" — possible via headless state restore,
        # which skips the UI coercion — still takes the thinking branch and is
        # sent as plain adaptive.
        always_on = self._is_anthropic_always_on_thinking()
        if self.thinking_enabled or always_on:
            support = self._model_supports_thinking()
            api_kwargs["max_tokens"] = min(MAX_TOKENS_THINKING, model_cap) if model_cap else MAX_TOKENS_THINKING
            if support == "adaptive":
                # display="summarized" restores readable thinking text on Fable 5 /
                # Opus 4.7+ (their default became "omitted" — empty thinking deltas,
                # which would leave the Show Thinking pane blank). Accepted as a
                # no-op on Opus/Sonnet 4.6, which already summarize by default.
                api_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
                if self.thinking_mode not in ("off", "adaptive"):
                    api_kwargs["output_config"] = {"effort": self.thinking_mode}
            elif support == "manual":
                api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        else:
            api_kwargs["max_tokens"] = min(MAX_TOKENS, model_cap) if model_cap else MAX_TOKENS
            # Opus 4.7+ and Fable/Mythos 5 removed temperature/top_p/top_k — sending
            # temperature returns a 400. Skip it for those models (parsed by version)
            # and for any model that rejected it earlier this session (reactive cache
            # below).
            if not self._anthropic_rejects_temperature() and self.model not in self._anthropic_no_temperature:
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
                                    self._tool_info("Searching the web...\n")
                                elif tool_name == "code_execution":
                                    self._tool_info("Running code execution...\n")
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
                        return "end_turn", [{"type": "text", "text": full_text}], full_text, had_thinking, label_emitted, None
                    try:
                        final_message = stream.get_final_message()
                    except Exception:
                        # Stream may be incomplete — synthesize a stop result
                        return "end_turn", [{"type": "text", "text": full_text}], full_text, had_thinking, label_emitted, None
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
                                    self._tool_info(stdout + "\n")
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
            except anthropic.RateLimitError:
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
            except anthropic.BadRequestError as e:
                # Opus 4.7+ reject temperature/top_p/top_k with HTTP 400. Strip
                # temperature, cache the model so later calls skip it upfront, and
                # retry. Mirrors the OpenAI temperature-rejection fallback. (Caught
                # before APIStatusError since BadRequestError is a subclass of it.)
                msg = (getattr(e, "message", "") or str(e)).lower()
                if "temperature" in msg and "temperature" in api_kwargs:
                    del api_kwargs["temperature"]
                    self._anthropic_no_temperature.add(self.model)
                    self.queue.put({
                        "type": "tool_info",
                        "content": "Model does not support temperature — retrying without it...\n",
                    })
                    full_text = ""
                    continue
                # Input exceeds the model's context window ("prompt is too long:
                # N tokens > M maximum"). Resending the same history can't fix it,
                # so end the turn gracefully with an actionable message instead of
                # letting the raw 400 crash the agentic loop. 200K-window models
                # (Haiku 4.5, Sonnet 4.5) can't run very long tasks; a 1M-context
                # model (Sonnet 4.6, Opus 4.6/4.7/4.8) is the only real fix.
                if "prompt is too long" in msg or ("too long" in msg and "maximum" in msg):
                    detail = getattr(e, "message", "") or str(e)
                    self.queue.put({"type": "error", "content":
                        f"⚠ Context window exceeded on {self.model}:\n  {detail}\n"
                        "  The conversation is now larger than this model can hold, so resending "
                        "it cannot succeed. 200K-window models (Haiku 4.5, Sonnet 4.5) cannot run "
                        "very long tasks — switch this instruction to a 1M-context model "
                        "(claude-sonnet-4-6 or claude-opus-4-8/4.7/4.6), or split the task into "
                        "smaller parts.\n"})
                    return ("end_turn", [{"type": "text", "text": full_text}],
                            full_text, had_thinking, label_emitted, None)
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

        # Fable 5 safety classifiers can decline a request with HTTP 200 and
        # stop_reason="refusal" — pre-output refusals carry an EMPTY content
        # array (nothing streamed, nothing billed), so without this notice the
        # agent run would just end silently. The loop in stream_worker treats
        # any non-tool_use stop_reason as end of turn, which is the right exit.
        if final_message.stop_reason == "refusal":
            details = getattr(final_message, "stop_details", None)
            category = getattr(details, "category", None) if details else None
            explanation = getattr(details, "explanation", None) if details else None
            note = "⚠ The model declined this request (stop_reason=refusal"
            if category:
                note += f", category={category}"
            note += ")"
            if explanation:
                note += f": {explanation}"
            note += ("\n  Any partial output above is incomplete. Rephrase the task "
                     "or switch this instruction to claude-opus-4-8.\n")
            self.queue.put({"type": "warning", "content": note})

        # Extract usage for cost tracking
        usage = getattr(final_message, "usage", None)
        usage_dict = None
        if usage:
            usage_dict = {
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
            }

        return final_message.stop_reason, final_message.content, full_text, had_thinking, label_emitted, usage_dict
