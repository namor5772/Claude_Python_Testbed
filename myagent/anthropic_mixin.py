import time

import anthropic

from myagent.constants import (
    MAX_TOKENS, MAX_TOKENS_THINKING, MODEL_MAX_OUTPUT_TOKENS,
    ANTHROPIC_THINKING_BINDING_BETA, ANTHROPIC_THINKING_BLOCK_BINDING,
    ANTHROPIC_SERVER_FALLBACK_BETA, ANTHROPIC_SERVER_FALLBACKS,
)
from myagent.helpers import (parse_overflow_counts, strip_pre_fallback_blocks,
                             strip_thinking_blocks, trim_history_for_context)
from myagent.retry_util import rate_limit_backoff, server_error_backoff


_CACHE_CONTROL = {"type": "ephemeral"}
# Betas every Anthropic call carries (server-side web search + code execution,
# and the Files API for the code-execution file outputs). The Fable/Mythos
# class appends its own two — see _anthropic_fable_features.
_BASE_BETAS = ["web-search-2025-03-05", "code-execution-2025-08-25", "files-api-2025-04-14"]


class AnthropicMixin:

    @staticmethod
    def _anthropic_cache_system(system_text):
        """System prompt as a single cached text block.

        Anthropic builds the cache prefix in the order tools → system →
        messages, and a breakpoint caches EVERYTHING before it — so this one
        marker covers the whole static header: every tool schema (the full
        TOOLS/FILE/DESKTOP/BROWSER/MCP/mail catalog) plus the built system
        prompt. Written once at 1.25x, read at 0.10x on every later turn.

        An empty prompt stays a bare string: a text block with "" is a 400.
        """
        if not system_text:
            return system_text
        return [{"type": "text", "text": system_text, "cache_control": dict(_CACHE_CONTROL)}]

    @staticmethod
    def _anthropic_cache_messages(messages, max_breakpoints=2):
        """Copy `messages` with rolling cache breakpoints on the newest turns.

        Two rolling markers (plus the static system one) stay under Anthropic's
        4-breakpoint ceiling. Turn N writes a cache ending at its last message;
        turn N+1 matches that prefix and reads it at 0.10x, then writes its own.
        The second, older marker is the safety net — if the newest write hasn't
        landed or has aged past the 5-minute TTL, the previous turn's prefix is
        still a hit instead of a full-price re-read of the whole conversation.

        NEVER mutates history: only the messages that receive a breakpoint are
        shallow-copied. `messages` stays the plain list stream_worker appends to
        and that the context-overflow handler trims in place — and because the
        caller rebuilds this per attempt, a trim is reflected on the retry.

        Assistant turns hold SDK block objects (streaming_mixin appends
        `final_message.content` verbatim), not dicts, so they are skipped; the
        eligible messages are the user turns carrying tool_result dicts, which
        are the natural turn boundaries anyway.
        """
        wire = list(messages)
        placed = 0
        for i in range(len(wire) - 1, -1, -1):
            if placed >= max_breakpoints:
                break
            msg = wire[i]
            if not isinstance(msg, dict):
                continue
            content = msg.get("content")
            if isinstance(content, str):
                if not content:
                    continue
                blocks = [{"type": "text", "text": content,
                           "cache_control": dict(_CACHE_CONTROL)}]
            elif isinstance(content, list) and content and isinstance(content[-1], dict):
                blocks = list(content)
                blocks[-1] = {**blocks[-1], "cache_control": dict(_CACHE_CONTROL)}
            else:
                continue
            wire[i] = {**msg, "content": blocks}
            placed += 1
        return wire

    def _anthropic_fable_features(self):
        """The Fable/Mythos-only request surface for this call, or None for
        every other model: the beta headers to add plus the
        `thinking.block_binding` and `fallbacks` values to send (constants.py
        explains both), minus any surface this session has already seen the
        API reject — `_anthropic_unsupported`, filled by the BadRequest rungs
        in _stream_anthropic_call, so an org that isn't enrolled in a beta
        learns once and stops sending it. Also drives the Debug payload dump
        (_payload_for_display), so the dump shows what is really sent."""
        if not self._is_anthropic_always_on_thinking():
            return None
        unsupported = getattr(self, "_anthropic_unsupported", set())
        features = {"betas": [], "block_binding": None, "fallbacks": None}
        if "block_binding" not in unsupported:
            features["betas"].append(ANTHROPIC_THINKING_BINDING_BETA)
            features["block_binding"] = dict(ANTHROPIC_THINKING_BLOCK_BINDING)
        if "fallbacks" not in unsupported:
            features["betas"].append(ANTHROPIC_SERVER_FALLBACK_BETA)
            features["fallbacks"] = ANTHROPIC_SERVER_FALLBACKS
        return features

    @staticmethod
    def _refusal_note(stop_details, fallbacks_requested=False):
        """The ⚠ line for a stop_reason="refusal" response. `stop_details` is
        whatever rode on the final message_delta — a plain dict on anthropic
        0.84.0 (the field isn't typed there, so the SDK's snapshot never copies
        it and _stream_anthropic_call captures it off the event), an object on
        newer SDKs, or None (the API may omit it even on a refusal — branch on
        stop_reason, never on this)."""
        def field(name):
            if isinstance(stop_details, dict):
                return stop_details.get(name)
            return getattr(stop_details, name, None) if stop_details is not None else None
        category = field("category")
        explanation = field("explanation")
        recommended = field("recommended_model")
        note = "⚠ The model declined this request (stop_reason=refusal"
        if category:
            note += f", category={category}"
        note += ")"
        if explanation:
            note += f": {explanation}"
        note += "\n  Any partial output above is incomplete."
        if fallbacks_requested:
            note += " A server-side fallback was requested but no fallback model served it"
            if recommended:
                note += f" — the API suggests retrying directly on {recommended}"
            note += "."
        note += " Rephrase the task or switch this instruction to claude-opus-5.\n"
        return note

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
            "system": self._anthropic_cache_system(self._build_system_prompt()),
            "messages": messages,  # replaced per attempt with the breakpointed copy
            "tools": tools,
        }
        model_cap = MODEL_MAX_OUTPUT_TOKENS.get(self.model)
        # Fable/Mythos thinking is always on (an explicit disable is HTTP 400),
        # so a stale thinking_mode of "off" — possible via headless state restore,
        # which skips the UI coercion — still takes the thinking branch and is
        # sent as plain adaptive.
        always_on = self._is_anthropic_always_on_thinking()
        # Fable 5.1's preserved-thinking binding + server-side refusal fallbacks
        # (None outside the always-on class; constants.py explains both).
        fable = self._anthropic_fable_features()
        if self.thinking_enabled or always_on:
            support = self._model_supports_thinking()
            api_kwargs["max_tokens"] = min(MAX_TOKENS_THINKING, model_cap) if model_cap else MAX_TOKENS_THINKING
            if support == "adaptive":
                # display="summarized" restores readable thinking text on Fable /
                # Opus 4.7+ (their default became "omitted" — empty thinking deltas,
                # which would leave the Show Thinking pane blank). Accepted as a
                # no-op on Opus/Sonnet 4.6, which already summarize by default.
                # On Fable 5.1 "summarized" also carries its between-tool-call
                # progress notes (the `display: "updates"` subset), so long tool
                # chains keep narrating in the pane.
                api_kwargs["thinking"] = {"type": "adaptive", "display": "summarized"}
                if fable and fable["block_binding"]:
                    # A nested key the SDK doesn't type passes through its
                    # TypedDict transform untouched (probed on anthropic 0.84.0).
                    api_kwargs["thinking"]["block_binding"] = fable["block_binding"]
                if self.thinking_mode not in ("off", "adaptive"):
                    api_kwargs["output_config"] = {"effort": self.thinking_mode}
            elif support == "manual":
                api_kwargs["thinking"] = {"type": "enabled", "budget_tokens": self.thinking_budget}
        else:
            api_kwargs["max_tokens"] = min(MAX_TOKENS, model_cap) if model_cap else MAX_TOKENS
            # Opus 5+ and Sonnet 5+ run ADAPTIVE thinking when the param is
            # omitted (a silent change from 4.x-era models, which ran
            # thinking-off on omission) — "Off" must be an explicit disable
            # there, or the model silently thinks against the non-thinking
            # max_tokens cap with the Show Thinking pane dark. Explicit
            # disabled is accepted on the whole 4.6+/5 Sonnet-Opus range
            # (Opus 5 only at effort ≤ high — satisfied, as "Off" sends no
            # effort); the always-on Fable/Mythos models (where it is HTTP
            # 400) never reach this branch.
            if self._anthropic_thinking_on_by_default():
                api_kwargs["thinking"] = {"type": "disabled"}
            # Opus 4.7+, Sonnet 5+, and Fable/Mythos 5 removed temperature/top_p/
            # top_k — sending temperature returns a 400. Skip it for those models
            # (parsed by version) and for any model that rejected it earlier this
            # session (reactive cache below).
            if not self._anthropic_rejects_temperature() and self.model not in self._anthropic_no_temperature:
                api_kwargs["temperature"] = self.temperature
        if fable and fable["fallbacks"]:
            # `fallbacks` is not a typed parameter in anthropic 0.84.0 (a typed
            # kwarg would raise) — extra_body merges it into the JSON body as-is.
            api_kwargs["extra_body"] = {"fallbacks": fable["fallbacks"]}
        betas = _BASE_BETAS + (fable["betas"] if fable else [])
        stripped_thinking = False  # one-shot guard for the signature-400 rung below

        for attempt in range(max_retries):
            # Rebuilt every attempt, not once before the loop: the retry paths
            # below mutate `messages` in place (the context-overflow trim), so
            # the wire copy has to be re-derived or the retry would resend the
            # pre-trim history — and the rolling breakpoints need to sit on the
            # post-trim tail anyway.
            api_kwargs["messages"] = self._anthropic_cache_messages(messages)
            try:
                with self.client.beta.messages.stream(betas=betas, **api_kwargs) as stream:
                    in_thinking = False
                    stop_details = None
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
                        elif event.type == "message_delta":
                            # stop_details (the refusal category/explanation)
                            # rides on the delta; anthropic 0.84.0's snapshot
                            # copies only stop_reason/stop_sequence, so read
                            # it off the event (a raw dict there, untyped).
                            stop_details = getattr(event.delta, "stop_details", None) or stop_details
                    if self.stop_requested:
                        # Stream interrupted by user — synthesize a stop result
                        return "end_turn", [{"type": "text", "text": full_text}], full_text, had_thinking, label_emitted, None
                    try:
                        final_message = stream.get_final_message()
                    except Exception:
                        # Stream may be incomplete — synthesize a stop result
                        return "end_turn", [{"type": "text", "text": full_text}], full_text, had_thinking, label_emitted, None
                # Extract code execution file outputs from final message
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
                    wait = rate_limit_backoff(attempt)
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
                # Claude Fable 5.1's preserved-thinking check: a replayed thinking
                # block whose conversation prefix has changed (the overflow trim
                # below is one such edit) is a 400 on enforced accounts —
                # "Invalid `signature` in `thinking` block … bound to a different
                # conversation". The drop_block binding normally has the API drop
                # such blocks instead; this rung is the no-beta floor: strip every
                # thinking block from history and retry once, so the turn goes on
                # without that reasoning rather than dying. Checked before the
                # beta-surface rungs because this error text also NAMES
                # block_binding (as the remedy), which must not disable it.
                if "signature" in msg and "thinking" in msg and not stripped_thinking:
                    stripped_thinking = True
                    removed = strip_thinking_blocks(messages)
                    if removed:
                        self.queue.put({"type": "warning", "content":
                            f"⚠ The API rejected {removed} replayed thinking block(s) — earlier "
                            "history changed since they were produced. Retrying without them; "
                            "the model continues without that reasoning.\n"})
                        full_text = ""
                        continue
                # A Fable beta surface this API key isn't enrolled in (the 400
                # names it): learn once for the session, drop it, retry.
                if fable and fable["fallbacks"] and "fallback" in msg:
                    self._anthropic_unsupported.add("fallbacks")
                    fable["fallbacks"] = None
                    api_kwargs.pop("extra_body", None)
                    betas = [b for b in betas if b != ANTHROPIC_SERVER_FALLBACK_BETA]
                    self._tool_info("Server-side refusal fallbacks are not available on this "
                                    "API key — retrying without them...\n")
                    full_text = ""
                    continue
                if fable and fable["block_binding"] and ("block_binding" in msg or "thinking-binding" in msg):
                    self._anthropic_unsupported.add("block_binding")
                    fable["block_binding"] = None
                    api_kwargs.get("thinking", {}).pop("block_binding", None)
                    betas = [b for b in betas if b != ANTHROPIC_THINKING_BINDING_BETA]
                    self._tool_info("Thinking-binding controls are not available on this "
                                    "API key — retrying without them...\n")
                    full_text = ""
                    continue
                # Input exceeds the model's context window ("prompt is too long:
                # N tokens > M maximum"). Resending the same history can't fix it,
                # so compact instead: drop the oldest conversation rounds (never
                # orphaning a tool_use/tool_result pair) and retry with the same
                # in-place list — the trim hits `messages` itself, so it both
                # persists into stream_worker's ongoing loop and is picked up by
                # the next attempt's `_anthropic_cache_messages` rebuild at the
                # top of the retry loop (which is why that rebuild lives inside
                # the loop and not above it). Same sliding-
                # window recovery SelfBot uses for endless duo chats; here it lets
                # a long coding/agent run keep going instead of dying mid-task.
                if "prompt is too long" in msg or ("too long" in msg and "maximum" in msg):
                    rep_tok, rep_max = parse_overflow_counts(msg)
                    removed = trim_history_for_context(messages, rep_tok, rep_max)
                    if removed > 0:
                        self.queue.put({"type": "warning", "content":
                            f"⚠ Context exceeded the model's limit — dropped the {removed} "
                            f"oldest message(s) and retried; earlier history is no longer "
                            f"in context.\n"})
                        full_text = ""
                        continue
                    # Only the last two rounds remain and they still overflow —
                    # nothing left to trim, so end the turn gracefully with an
                    # actionable message instead of crashing the agentic loop.
                    detail = getattr(e, "message", "") or str(e)
                    self.queue.put({"type": "error", "content":
                        f"⚠ Context window exceeded on {self.model}:\n  {detail}\n"
                        "  Even the most recent exchanges alone are larger than this model "
                        "can hold. 200K-window models (Haiku 4.5, Opus 4.5) cannot run "
                        "very long tasks — switch this instruction to a 1M-context model "
                        "(claude-opus-5, claude-fable-5-1, claude-sonnet-5, claude-sonnet-4-6, or "
                        "claude-opus-4-8/4.7/4.6), or "
                        "split the task into smaller parts.\n"})
                    return ("end_turn", [{"type": "text", "text": full_text}],
                            full_text, had_thinking, label_emitted, None)
                raise
            except anthropic.APIStatusError as e:
                if e.status_code == 529 and attempt < max_retries - 1:
                    wait = server_error_backoff(attempt)
                    self.queue.put({
                        "type": "tool_info",
                        "content": f"API overloaded — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...\n",
                    })
                    time.sleep(wait)
                    full_text = ""
                else:
                    raise

        # Preserved-thinking bookkeeping (Fable 5.1+, thinking-binding beta): with
        # the header on, every response carries `input_transformations` — the
        # replayed thinking blocks the API dropped before the model saw them
        # (prefix_binding_mismatch: history changed, e.g. the overflow trim;
        # model_binding_mismatch: the blocks came from another model). Unbilled,
        # but the model re-plans without that reasoning, so say so.
        transformations = getattr(final_message, "input_transformations", None) or []
        if transformations:
            reasons = sorted({str((t.get("reason") if isinstance(t, dict)
                                   else getattr(t, "reason", None)) or "unknown")
                              for t in transformations})
            self._tool_info(f"API dropped {len(transformations)} replayed thinking block(s) "
                            f"({', '.join(reasons)}) — earlier history changed; the model "
                            "continues without that reasoning.\n")

        # Server-side refusal fallback: `message.model` names the model that
        # produced this message, and a `fallback_message` entry in
        # usage.iterations is the served-by signal (a mid-output switch also
        # leaves a `fallback` content block; a sticky follow-up turn does not,
        # so the iterations check is the one that covers both).
        served_model = getattr(final_message, "model", None) or self.model
        usage = getattr(final_message, "usage", None)
        iterations = getattr(usage, "iterations", None) or []
        fallback_ran = any(
            (it.get("type") if isinstance(it, dict) else getattr(it, "type", None)) == "fallback_message"
            for it in iterations)
        content_blocks = strip_pre_fallback_blocks(list(final_message.content))
        if fallback_ran and final_message.stop_reason != "refusal":
            self.queue.put({"type": "warning", "content":
                f"⚠ {self.model} declined this request (safety classifier) — served by "
                f"{served_model} via server-side fallback; this call bills at "
                f"{served_model} rates.\n"})

        # Fable safety classifiers can decline a request with HTTP 200 and
        # stop_reason="refusal" — pre-output refusals carry an EMPTY content
        # array (nothing streamed, nothing billed), so without this notice the
        # agent run would just end silently. The loop in stream_worker treats
        # any non-tool_use stop_reason as end of turn, which is the right exit.
        # With fallbacks on, a refusal here means the whole chain declined.
        if final_message.stop_reason == "refusal":
            self.queue.put({"type": "warning", "content": self._refusal_note(
                stop_details, fallbacks_requested=bool(fable and fable["fallbacks"]))})

        # Extract usage for cost tracking
        usage_dict = None
        if usage:
            usage_dict = {
                "input_tokens": getattr(usage, "input_tokens", 0) or 0,
                "output_tokens": getattr(usage, "output_tokens", 0) or 0,
                "cache_creation_input_tokens": getattr(usage, "cache_creation_input_tokens", 0) or 0,
                "cache_read_input_tokens": getattr(usage, "cache_read_input_tokens", 0) or 0,
                # The model that produced the message — differs from self.model
                # after a fallback (and on its sticky follow-up turns);
                # stream_worker prices the call by it, so a fallback-served
                # call bills at the serving model's rates, not Fable's.
                "model": served_model,
            }

        return final_message.stop_reason, content_blocks, full_text, had_thinking, label_emitted, usage_dict
