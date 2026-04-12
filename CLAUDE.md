# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment
- OS: Windows 11 or macOS (cross-platform)
- Python: Activate the `.venv` before running Python commands
  - Windows: `source .venv/Scripts/activate`
  - macOS: `source .venv/bin/activate`
- After activation, use `python` to run scripts (the venv maps it correctly)
- Shell: bash (Git Bash on Windows, zsh/bash on macOS)

## Commands
```bash
# Activate venv (use Scripts on Windows, bin on macOS)
source .venv/bin/activate   # macOS
source .venv/Scripts/activate  # Windows

# Run SelfBot / MyAgent / CSVEditor
python SelfBot.py
python MyAgent.py
python CSVEditor.py

# Run MyAgent with auto-launch instruction
python MyAgent.py -l "Instruction Name"

# Run MyAgent headless (no main window, auto-closes on completion)
python MyAgent.py -l "Instruction Name" --headless

# Run Account Activity extractor
python Account_Activity_WBC.py

# Kill any running instances before relaunching
# Windows:
taskkill //F //IM pythonw.exe 2>/dev/null; taskkill //F //IM python.exe 2>/dev/null
# macOS:
pkill -f "python.*MyAgent.py" 2>/dev/null; pkill -f "python.*SelfBot.py" 2>/dev/null
```
There are no tests, linter, or build steps — these are testbed apps.

## Project Structure
- `SelfBot.py` — Single-file tkinter GUI chatbot (~4100 lines); works as a solo chatbot or as a dual-instance self-chatting bot via file-based message passing
- `MyAgent.py` — Entry point (~170 lines) for the modular tkinter GUI autonomous agent; fire-and-forget task runner with an agentic tool-use loop, supports Anthropic, OpenAI, and Gemini providers, supports `-l` argument for command-line auto-launch of saved instructions
- `myagent/` — Package containing MyAgent's mixin modules (split from the original single-file architecture):
  - `constants.py` — Tool schemas (TOOLS, META_TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS), safety patterns, model constants, API pricing tables (ANTHROPIC_PRICING, OPENAI_PRICING, GEMINI_PRICING), file paths
  - `helpers.py` — HTMLTextExtractor, extract_text_from_html, _ToolBlock
  - `ui_mixin.py` — setup_ui(), model/provider/thinking widget handlers
  - `state_mixin.py` — Instance management, display geometry, state persistence
  - `instructions_mixin.py` — Instruction CRUD, editor Toplevel dialog
  - `skills_mixin.py` — Skills CRUD, editor dialog, system prompt building
  - `streaming_mixin.py` — stream_worker (agentic loop), _execute_tool, _get_tools, _get_pricing (cost lookup), message translation
  - `anthropic_mixin.py` — _stream_anthropic_call
  - `openai_mixin.py` — OpenAI helpers, _stream_responses, _stream_responses_call
  - `gemini_mixin.py` — Gemini helpers, _tools_to_gemini, _messages_to_gemini, _stream_gemini_call
  - `desktop_mixin.py` — Desktop automation tools (pyautogui): screenshot, mouse, keyboard, clipboard, OCR
  - `browser_mixin.py` — Browser automation tools (Playwright): open, navigate, click, fill, screenshot
  - `safety_mixin.py` — Command safety, confirmation dialog, user_prompt, run_powershell, agent control
  - `chat_mixin.py` — Chat save/serialize, image attachment, LaTeX processing
  - `event_loop_mixin.py` — check_queue, _on_close, _finish_close
- `Account_Activity_WBC.py` — Single-file tkinter GUI browser automation utility (~340 lines); connects to Edge via CDP, clicks "Display more" on the Westpac account activity page, and exports transactions as HTML + CSV
- `CSVEditor.py` — Single-file tkinter GUI CSV editor (~520 lines); open, edit, filter, and save CSV files with a spreadsheet-style treeview interface
- `skills.json` — User-defined skills with content and mode, shared by both apps (created at runtime)
- `system_prompts.json` — Saved system prompts for SelfBot (created at runtime)
- `agent_instructions.json` — Saved agent instructions for MyAgent, with embedded images (created at runtime)
- `saved_chats/` — Directory of saved chat conversations, one `.json` file per chat; a matching `.txt` export of the output window is always saved alongside each `.json` file
- `app_state.json` — Persistent settings for SelfBot instance 1 (created at runtime)
- `app_state_2.json` — Persistent settings for SelfBot instance 2 (created at runtime)
- `agent_state.json` — Persistent settings for MyAgent (created at runtime)
- `csv_editor_state.json` — Persistent settings for CSVEditor (created at runtime)
- `selfbot.lock` — Lock file for SelfBot instance detection (created/deleted at runtime)
- `selfbot_auto_msg.json` — Shared file for SelfBot cross-instance message injection (created/deleted at runtime)
- `Account_Activity_WBC.txt` — Raw transaction HTML extracted by Account_Activity_WBC.py (created at runtime, gitignored)
- `Account_Activity_WBC.csv` — Parsed transaction CSV exported by Account_Activity_WBC.py (created at runtime, gitignored)
- `LaunchSelfBot.bat` — Launcher that starts both SelfBot instances side by side with focus on instance 1
- `LaunchMyAgent.bat` — Launcher for MyAgent
- `selfbot_position.ps1` — PowerShell helper used by the SelfBot launcher to position and focus windows

## Architecture (SelfBot.py)

**Single class design** — The `App` class contains all UI, API, tool execution, and persistence logic. No separate modules.

**Tool system** — Three global tool lists define API tool schemas:
- `TOOLS` — Core tools always sent to the API (web_search, fetch_webpage, run_command, csv_search)
- `DESKTOP_TOOLS` — 13 pyautogui-based tools, conditionally included when Desktop checkbox is enabled
- `BROWSER_TOOLS` — 11 Playwright/CDP tools, conditionally included when Browser checkbox is enabled
- `_get_tools()` assembles the final tool list dynamically based on UI toggle state

**Adding a new tool** requires three changes:
1. Add schema dict to the appropriate tool list (`TOOLS`, `DESKTOP_TOOLS`, or `BROWSER_TOOLS`)
2. Add a `do_<tool_name>()` method to the `App` class
3. Wire it up in the `stream_worker()` method's tool dispatch block (`elif block.name == "..."`)

**Threading model** — API calls run in a background `stream_worker` thread. A `queue.Queue` passes events (text, thinking, tool info, errors) to the main thread, polled every 50ms via `root.after()`. `_ensure_newline()` guarantees each new output block starts on a fresh line; an `ensure_newline` queue event between loop iterations prevents text merging when Activity is off.

**Thinking accumulator lifecycle** — `_current_thinking_text` is reset at `thinking_start` (not at `label`), so the accumulated thinking text survives past the label event and is available when `complete` fires to inject into the peer instance.

**Show Thinking checkbox** — The `show_thinking` BooleanVar (defaults False) gates display of thinking blocks in `check_queue` and `_poll_auto_msg`. This is separate from `thinking_enabled` which controls whether the API generates thinking blocks. Both must be on for thinking to appear in the output.

**Save Thinking checkbox** — The `save_thinking` BooleanVar (defaults False) controls whether thinking and redacted_thinking blocks are preserved in saved chat JSON files. When enabled, Anthropic thinking blocks (including cryptographic signatures) are kept during `_serialize_messages()`, allowing loaded chats to continue with full reasoning context. When disabled, thinking blocks are stripped to reduce file size. Has no effect for OpenAI models, whose reasoning summaries are display-only and never stored in messages. Persisted in the app's state file.

**Dual geometry persistence** — State files store `geometry` (solo mode) and `duo_geometry` (duo mode via `--no-geometry` flag) independently. `_duo_mode` is set in `__init__` from `sys.argv` before any save can occur. On save, the app reads the existing state file to preserve the other mode's geometry key.

**Skills system** — Three modes: disabled, enabled (injected into system prompt), on-demand (retrieved via `get_skill` tool). Managed through `_build_system_prompt()` and `_get_tools()`.

**DPI handling (SelfBot)** — `SetProcessDpiAwareness(2)` is called before any window creation. On macOS, Quartz captures at physical pixel resolution; `_capture_single_display()` resizes to the display's logical dimensions (from `_get_display_rects()`) so coordinates align with pyautogui's logical coordinate space. On Windows with DPI awareness, all coordinates are physical — no DPI alignment needed. The subsequent resize to provider API limits is tracked by `_screenshot_scale`; all coordinate-using tools compute `round(img_coord * scale) + offset` with `int()` type conversion for Gemini proto compatibility. `_screenshot_dims` tracks the last image dimensions for bounds checking. `pyautogui.PAUSE` is set to 0.1s.

**Note on MyAgent DPI** — MyAgent uses the newer `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` (i.e. `-4`), falling back to v1 then to legacy `SetProcessDPIAware()`. V2 fixes broken multi-monitor behavior under v1 when monitors have different DPI scaling (e.g. 100% primary + 225% secondary): v1 reports the secondary's rect as a logically-scaled smaller size, causing `ImageGrab.grab` to return a low-res image and `pyautogui` clicks to land in the wrong place. V2 returns true physical pixels per monitor regardless of per-monitor DPI.

**Auto-save on close** — When closing (via [X] button or `taskkill`), all instances auto-save their chat as `.json` + `.txt` to `saved_chats/`. Uses the name from the Save Chat entry if provided, otherwise auto-generates from the first user message. Instance 2's files are suffixed with `_` via `_save_name()` to avoid collisions. A periodic auto-save every 5 seconds on all instances also protects against force-kill data loss.

**Graceful duo shutdown** — Pressing [X] on either instance stops auto-chat, waits for any active streaming to finish, saves both instances' chats, then closes both windows via `WM_CLOSE` messages.

**API retry logic** — `stream_worker` retries up to 10 times on transient API errors. Rate-limit errors (429) use exponential backoff capped at 60s (~6.5 min total). Overload errors (529) use exponential backoff capped at 90s (~10 min total). This makes the app resilient to prolonged Anthropic API outages without absurdly long individual waits.

## Architecture (MyAgent.py)

**Mixin-based modular design** — The `App` class in `MyAgent.py` inherits from 14 mixin classes in the `myagent/` package. Each mixin groups related methods by concern (UI, streaming, tools, persistence, etc.). Constants and tool schemas live in `myagent/constants.py`; helper classes in `myagent/helpers.py`. The `__init__` method and entry point remain in `MyAgent.py`. All mixins share state through `self.*` — no inter-mixin imports needed.

**Multi-provider support** — A Provider combobox in the instruction editor switches between Anthropic, OpenAI, and Gemini. The internal message format stays Anthropic-style; translation to/from other formats happens at the API boundary only. OpenAI uses `_messages_to_responses()`, `_tools_to_responses()`, `_stream_responses()`; Gemini uses `_messages_to_gemini()`, `_tools_to_gemini()`, `_stream_gemini_call()`. The `_ToolBlock` wrapper class gives OpenAI/Gemini dict-based tool responses the same `.name`/`.id`/`.input` attribute interface as Anthropic's Pydantic objects, so `_execute_tool()` works identically for all providers. Gemini tool call IDs are synthesized as `gemini_{name}_{index}` since the API doesn't use IDs. Provider selection is saved per-instruction and in `agent_state.json`. The Instruction button is disabled while the agent is running. Gemini requires `GEMINI_API_KEY` or `GOOGLE_API_KEY` env var.

**OpenAI model filtering** — `_fetch_openai_models()` filters the API model list to Responses API compatible families only via `OPENAI_RESPONSES_PREFIXES`: `gpt-4o`, `gpt-4.1`, `gpt-4.5`, `gpt-5`, `o1`, `o3`, `o4`. Non-chat model types (embedding, audio, search, realtime, preview, transcribe, tts) are skipped first. Legacy models (gpt-3.5-turbo, base gpt-4, gpt-4-turbo) are excluded as they don't support the Responses API. `OPENAI_REASONING_PREFIXES` (`o1`, `o3`, `o4`, `gpt-5`) determines which models get `reasoning` params instead of `temperature`. `gpt-5.x-chat-*` "Instant" variants are detected by `_is_gpt5_chat_model()` — they are non-reasoning models that don't support temperature (API rejects it) but do support `text.verbosity`. The OpenAI client uses `httpx.Timeout(600.0, connect=10.0, read=120.0)` to prevent indefinite hangs on unresponsive models.

**Gemini model filtering** — `_fetch_gemini_models()` calls `gemini_client.models.list()`, strips the `"models/"` prefix from IDs, and filters out non-generative models (embedding, imagen, etc.) and deprecated models (`gemini-2.0-*`, `gemini-1.*`), falling back to `GEMINI_FALLBACK_MODELS`. `GEMINI_THINKING_PREFIXES` (`gemini-2.5`, `gemini-3`) determines which models support thinking via `ThinkingConfig`; models with `"lite"` in the name are excluded from thinking support. Thinking budget is mapped from effort: low=1024, medium=8192, high=24576. The SDK is `google-genai` (unified Google AI SDK), initialized with `GEMINI_API_KEY` or `GOOGLE_API_KEY`.

**Temperature/thinking UI visibility** — Model parameter controls are dynamically shown or hidden (not just disabled) based on the selected model's capabilities. `_forget_all_model_widgets()` resets all parameter widgets at the start of `_on_model_selected()` to guarantee correct pack order when switching between model types. Models without thinking support hide the thinking checkbox and strength combo entirely, showing only the temperature spinner (or just verbosity for `gpt-5.x-chat-*` Instant models which don't support temperature). OpenAI reasoning models hide temperature entirely (these models don't accept it). Anthropic adaptive models replace the checkbox+strength with a **Thinking mode combobox** (Off/Adaptive/Low/Medium/High/Max) and hide temperature when thinking is active. Gemini shows temperature alongside thinking controls since the API accepts both. "Max" effort is only available for Opus 4.6. GPT-5.1+ models use an "extended" reasoning mode with a **Reasoning combobox** (None/Low/Medium/High, plus Xhigh for gpt-5.2+/codex-max — but mini/nano variants cap at High, no Xhigh). GPT-5.4+ models show the temperature spinner when reasoning effort is set to "None" (the API accepts it in that mode); older gpt-5 models keep temperature hidden. GPT-5.0 base models add "minimal" to the strength options. All gpt-5 models (including `-chat` Instant variants) show a **Verbosity combobox** (Low/Medium/High) that maps to `text.verbosity` in the API. The Thinking/Reasoning label and its dropdown are always packed adjacently. `_on_thinking_toggled()` delegates to `_on_model_selected()` to maintain pack order. Detection helpers: `_parse_gpt5_minor()`, `_is_gpt5_family()`, `_is_gpt5_chat_model()`, `_has_reasoning_none()`, `_has_reasoning_xhigh()`, `_has_openai_verbosity()`, `_gpt5_supports_temp_at_none()`.

**Agentic loop** — `stream_worker()` runs a `while True:` loop: dispatches to `_stream_anthropic_call()`, `_stream_responses_call()`, or `_stream_gemini_call()` based on the provider, streams the response, executes any tool calls, appends results, and loops again. Exits on `end_turn` or when `stop_requested` is set via the STOP button. No fixed iteration limit.

**Tool system** — Four-list structure (`TOOLS`, `DESKTOP_TOOLS`, `BROWSER_TOOLS`, `META_TOOLS`) in `myagent/constants.py` and `_get_tools()` assembler in `myagent/streaming_mixin.py`. `TOOLS` is always included; the others are conditionally added based on Desktop/Browser/Meta checkboxes. Tool dispatch is handled by `_execute_tool()` in `streaming_mixin.py`. The `find_element` tool inside `DESKTOP_TOOLS` is provider-gated: `_get_tools()` strips it for non-Gemini providers since it leverages Gemini's native pointing API. Adding a new tool requires: (1) schema dict in the appropriate tool list in `constants.py`, (2) `elif` branch in `_execute_tool()` in `streaming_mixin.py`, (3) `do_<name>()` implementation method in the appropriate mixin (desktop_mixin, browser_mixin, safety_mixin, etc.), and optionally (4) adding the tool name to the `PARALLEL_SAFE_TOOLS` set if it is thread-safe and stateless.

**Click accuracy pipeline** — MyAgent's desktop click stack went through significant refinement for cross-provider accuracy. Key invariants:
- All providers receive **pixel coordinates** (no normalized [0, 1000] convention) — what the model sees in the image is what it passes to `mouse_click`. Earlier versions used Gemini's [0, 1000] convention, but switching to pixels removed the mental-arithmetic burden that caused systematic ~1-pixel drift on Gemini and broke entirely under multi-monitor mixed-DPI scenarios.
- `do_mouse_click`/`do_mouse_scroll`/`do_mouse_drag`/`do_read_screen_text` use `round(float(x))` (not `int(x)`) to avoid 1-pixel truncation bias.
- A pre-screenshot guard refuses click/scroll/drag/OCR with "Take a screenshot first" when `_screenshot_dims == (0, 0)`, preventing silent misclicks before any capture.
- A tiered out-of-bounds policy: ≤2px overflow silently clamps, > 2% of image dimension refuses with a "re-take a screenshot" message, in-between clamps with a `⚠ clamped` warning. Replaces the prior behaviour of always-clamp-and-click which masked perception errors.
- A 50ms `time.sleep` after `pyautogui.click` lets the post-click UI settle before the next screenshot.
- The `_capture_single_display` region branch snapshots `entry_scale`/`entry_offset` at function entry so chained region screenshots compute correctly (without this, region-inside-region drifts through stacked offsets).

**Per-display state tracking** — Multi-display coordinate state is tracked via two parallel dicts in MyAgent:
- `_display_states[N]` / `_display_images[N]` — the **most recent capture state** for display N (full or region). Updated on both full and region captures. Used by `mouse_click`, `find_element`, and any tool with a `display=N` parameter so the model can click using coordinates from whatever it most recently saw of that display.
- `_display_full_states[N]` / `_display_full_images[N]` — the **most recent FULL display capture state** for display N. Updated only on full captures, never overwritten by region captures. Used by region screenshots: when the model says `screenshot(display=1, x=..., y=..., width=..., height=...)`, those region coordinates are interpreted in display 1's full image space (not relative to whatever was captured most recently). Without this two-slot separation, chained region screenshots on the same display would drift through stacked offsets.

`_resolve_coord_state(display)` is the lookup helper used by all coordinate-consuming tools — returns `_display_states[display]` if a `display` parameter is supplied, else falls back to current `_screenshot_scale/offset/dims`. `mouse_click`, `mouse_scroll`, `mouse_drag`, `read_screen_text`, and `find_element` all accept an optional `display` parameter so the model can disambiguate which screen to act on without re-screenshotting.

**Image resolution caps per provider** — `_capture_single_display` resizes the captured image to a provider-specific cap based on each API's actual limits:
- **Anthropic**: `1568` long edge / `1.15 MP` — matches Anthropic's vision API hard limit (the API silently downscales to this anyway)
- **OpenAI**: `2048` long edge / `5 MP` — matches OpenAI's vision API hard limit. We previously tried 2560 but discovered via gpt-5.2's code_interpreter inspection (`PIL.Image.size == (2048, 1152)`) that the API silently downscales above 2048, breaking our scale calculations. The 2048 cap means our `_screenshot_scale` matches what the model actually sees.
- **Gemini**: `2048` long edge / `4 MP` — bumped above the Anthropic-matched 1568 because Gemini's tile system supports higher resolution (768x768 tiles, many per image), giving older models like Gemini 2.5 Pro more pixel density on small UI elements.

**find_element tool (Gemini-only)** — Uses Gemini's native spatial pointing API to locate UI elements by natural-language description. Implemented in `gemini_mixin.py:do_gemini_find_element` using Google's documented pointing prompt format (`"Point to the X. The answer should follow the json format: [{\"point\": <point>, \"label\": <label1>}, ...]. The points are in [y, x] format normalized to 0-1000."`) — NOT a custom prompt, because the trained pointing capability only activates when it sees the exact phrasing. Parses both the official array shape and a single-object fallback. Accepts an optional `display` parameter so the cached image lookup hits the right display (`_display_images[display]`); without this, find_element after a multi-display screenshot would always search whichever display was captured last in the loop. Returns pixel coordinates (converted from [0, 1000] internally) and tells the model exactly which mouse_click call to make. The tool is filtered out of `_get_tools()` for non-Gemini providers since it requires the Google API.

**Grid overlay** — `do_screenshot` accepts an optional `grid=true` parameter. When set, `_capture_single_display` calls `_draw_coord_grid(img)` after the API-limit resize but before PNG encoding, drawing 100-pixel-spaced magenta gridlines with `(x,y)` labels at intersections (skipping (0,0) to avoid clutter). Drawn after resize so labels match the dimensions the model actually sees. Opt-in default off so the grid doesn't obscure small UI text on normal screenshots; the tool description nudges the model to use it when targeting small/dense UI areas.

**Diag checkbox** — A new `Diag` checkbox in the main window (next to `Save Thinking`) gates `[DIAG capture]` and `[DIAG click]` lines that surface to the activity output, showing the full coordinate-mapping trail (display rects, physical/logical/sent_to_model dims, scale, offset, raw input, computed screen pixels). Persisted in `agent_state.json` as `diag_enabled`. Independent of the `Debug` checkbox (which controls API JSON payload display) — Debug stays focused on its original job. Defaults on for now since the click-accuracy debugging in this session benefited from always-on visibility; toggle off when not needed.

**Code interpreter gating (OpenAI)** — `_stream_responses_call` and `_payload_for_display` skip appending `code_interpreter` to the OpenAI tools list when desktop tools are enabled (`self.desktop_enabled.get() and _HAS_DESKTOP`). Empirically verified that gpt-5.2 with code_interpreter access loads screenshot bytes via PIL, sees the API-resized image dimensions, and pre-scales coordinates ITSELF before calling `mouse_click` — collides with our scale calculation and produces double-scaled clicks. Stripping CI when desktop is on forces the model to use direct visual perception. CI remains available for non-desktop OpenAI tasks (data analysis, file processing, etc.).

**OpenAI tool rejection cache** — Newer OpenAI models can reject specific server-side tools with HTTP 400 (e.g. `gpt-5.2-pro` rejects `code_interpreter`). `_stream_responses_call`'s `BadRequestError` handler regex-extracts the offending tool name from messages like `"Tool 'code_interpreter' is not supported with gpt-5.2-pro"`, adds it to `self._openai_unsupported_tools[model_id]` (a per-model set), strips it from the current request, retries once, and caches the rejection so subsequent calls in the same session skip the unsupported tool upfront. Avoids re-hitting the same error on every call. Same pattern as the existing temperature retry, applied to any tool name OpenAI rejects.

**Weak desktop combo warning** — `_weak_desktop_combo_warning()` in `streaming_mixin.py` returns a string when the active provider/model has empirically-weak spatial precision for desktop click tasks: gpt-5 family with reasoning effort = `none` or `minimal`, gpt-5 `-chat` Instant variants, and any `gemini-2.x` model. `stream_worker` calls this at agent start and posts the warning to the activity output as `⚠ ...`. Informational only — does not change behaviour or override the model. Anthropic, gpt-4 family, gpt-5 with reasoning ≥ low, and gemini-3.x all pass without a warning.

**Parallel tool execution** — When Claude requests multiple tools in one turn, tool blocks are partitioned into parallel-safe (`web_search`, `fetch_webpage`, `csv_search`, `get_skill`) and sequential (everything else). Parallel-safe tools run concurrently via `concurrent.futures.ThreadPoolExecutor`; sequential tools run one at a time in order. Results are placed into a pre-allocated list indexed by original position, preserving the API-expected ordering.

**Agent Instructions** — Stored in `agent_instructions.json` as `{name: {text: str, images: [{data, media_type, filename}], desktop: bool, browser: bool, meta: bool, provider: str, model: str, temperature: float, thinking_enabled: bool, thinking_effort: str, thinking_budget: int, thinking_mode: str, text_verbosity: str, skill_modes: {skill_name: mode_string}, disabled_confirm_patterns: [str]}}`. Images are embedded as base64 and re-attached when loading an instruction. Desktop/Browser/Meta tool toggle states, model parameters (model, temperature, thinking settings including thinking_mode for adaptive models, text_verbosity for gpt-5 family), skill modes, and disabled PS Safety confirm patterns are all saved per-instruction and restored on load. Skills not present in the snapshot default to disabled; deleted skills are silently skipped. Old instruction entries without `thinking_mode` are backward-compatible: the mode is inferred from `thinking_enabled` + `thinking_effort`. Entries without `text_verbosity` default to `"medium"`.

**Editor draft/commit model** — The instruction editor works on temporary copies (`_editor_images`, `_editor_desktop`, `_editor_browser`). Changes are only committed to live state on SAVE (persists to disk) or Apply (session-only). Closing the editor with [X] discards uncommitted changes. Model params and skill modes are restored immediately on instruction selection (like live state), matching the pattern of being environment-level settings rather than draft state. Model/provider widgets (Provider combo, Model combo, Temp, Thinking) are created inside the editor dialog and only exist while it's open. Widget references are set to `None` via `_nullify_editor_widgets()` on close/Apply so `_has_model_widgets()` guards skip widget updates. The current provider and model are shown in the title bar at all times via `_update_title()`.

**Threading model** — Same as SelfBot: background daemon thread for API calls, `queue.Queue` for events, main thread polls every 50ms via `root.after()`.

**PowerShell Safety dialog** — The "PS Safety" button (inside the instruction editor, next to the Skills button) opens a dialog listing all `COMMAND_CONFIRM` patterns as checkboxes. Checked = confirmation required (default), unchecked = bypass confirmation and show a `⚠ Confirm bypassed` warning in the output window instead. Disabled patterns are persisted per-instruction in `agent_instructions.json` as `disabled_confirm_patterns`. The bypass warning uses the `"warning"` queue message type which always displays regardless of the Activity checkbox. The PS Safety button label shows a count when patterns are bypassed (e.g., `PS Safety (3 bypassed)`).

**Command-line launch** — `python MyAgent.py -l "Name"` auto-loads a saved instruction and starts the agent. Uses `argparse` for `-l`/`--load`. The instruction is loaded via `_auto_launch()`, scheduled as `root.after(100)` to ensure UI is initialized, which then schedules `_start_agent` via `root.after(200)` to allow a full event loop cycle between state setup and agent start. Shows an error dialog listing available names if the instruction is not found. The `-l` flag also auto-populates the "Save Chat as" entry with `"{InstructionName}_{timestamp}"` so output is always captured.

**Headless mode** — `python MyAgent.py -l "Name" --headless` runs without a main window (`root.withdraw()`). Dialogs (`user_prompt`, PS confirmation) skip `transient()` and `grab_set()` so they float as standalone windows. The process auto-closes after the agent loop completes. Designed for orchestrator patterns where a parent MyAgent spawns child instances via `run_command`.

**Meta-agent tools** — Three tools in the `META_TOOLS` list, conditionally included when the Meta checkbox is enabled (same gating pattern as Desktop/Browser). Two CRUD tools (`manage_instructions`, `manage_skills`) use a single `action` parameter (`list`/`read`/`create`/`update`/`delete`) to minimize tool count. `manage_instructions` creates entries inheriting the current provider/model/thinking settings; the `read` action returns all model params (provider, model, temperature, thinking_enabled, thinking_effort, thinking_budget, thinking_mode); the `update` action accepts these same params as optional fields alongside text, desktop, browser, meta, and skill_modes. `manage_skills` updates the in-memory skills dict and triggers thread-safe UI refresh via `_post_skill_ui_refresh()`. `run_instruction` launches a saved instruction as a separate MyAgent process (fire-and-forget) using `subprocess.Popen` with `sys.executable`; defaults to headless mode, with an optional `headless=false` parameter to show the GUI. None of these tools are in `PARALLEL_SAFE` since they modify shared state or spawn processes. The Meta toggle is saved per-instruction.

**State persistence** — `agent_state.json` stores provider, last instruction name, model, temperature, thinking settings (including `thinking_mode`), `text_verbosity`, display checkbox states (debug, tool calls, activity, show thinking, save thinking), and a `geometries` dict keyed by monitor configuration. Each monitor config key (generated by `_get_monitor_config_key()` via Win32 `EnumDisplayMonitors` on Windows, CoreGraphics `CGGetActiveDisplayList` on macOS) maps to a set of window/dialog geometries (main window, editor, prompt dialog, confirm dialog, PS Safety dialog, Skills Manager dialog). This enables per-monitor-configuration persistence — switching between docked/undocked or different monitor arrangements restores the correct positions for each setup. Disabled confirm patterns are stored per-instruction in `agent_instructions.json`. Periodic auto-save every 5 seconds captures live geometry from all currently open dialogs (not just cached values from when they were last closed). Dialog geometries are also flushed to disk immediately when the dialog closes (editor close, Apply, or prompt/confirm dismiss). All persisted geometries are validated on restore via `_sanitize_geometry()` against the full virtual desktop bounds (all monitors via `_get_virtual_screen_bounds()` using Win32 `GetSystemMetrics` on Windows, CoreGraphics `CGDisplayBounds` on macOS) — windows that are too small (below 200x150) or positioned entirely off-screen are reset to defaults. Old state files with flat geometry fields are automatically migrated to the per-config format on first load.

**Dialog geometry pattern** — All dialogs (editor, PS Safety, Skills Manager, confirm, prompt) use a consistent withdraw/deiconify pattern to prevent `transient(parent)` from overriding saved positions: `withdraw()` → build content → `update_idletasks()` → set geometry → `deiconify()`. The PS Safety dialog additionally re-applies geometry via `after(100ms)` because its embedded checkbutton widgets request a large natural size that can override the saved dimensions on map. On macOS, `transient(parent)` is skipped entirely for all dialogs — macOS's window manager restricts transient windows to the parent's screen, preventing them from being dragged to secondary monitors. On Windows, `transient()` works correctly across screens and is kept. Dialog references (`_ps_safety_dialog`, `_confirm_dialog`, `_prompt_dialog`) are stored on `self` so the periodic auto-save can read live geometry from open dialogs; closing the editor also captures the PS Safety dialog geometry before the destroy cascade.

**LaTeX to Unicode conversion** — Assistant text containing LaTeX math notation is automatically converted to Unicode after each streaming segment completes via `_post_process_latex()`, which iterates over all `tag_ranges("assistant")` in the widget and applies `_latex_to_unicode()` only to assistant-tagged text (preventing filename/path mangling in tool_info messages). The static method handles all delimiter styles (`\( \)`, `$ $`, `\[ \]`, `$$ $$`), braced superscripts/subscripts only (`^{...}`, `_{...}` — bare `^x`/`_x` patterns are left alone to avoid false positives in filenames), Greek letters, operators, fractions (`\frac{a}{b}` → `a/b`), functions, arrows, set notation, and more. Unrecognised `\command` patterns have their backslash stripped as a fallback. A `post_process_latex` queue message is emitted from `stream_worker` after each provider call returns text, and also during the `complete` handler.

**Server-side tools** — OpenAI and Anthropic use server-side web search and code execution, replacing the local DuckDuckGo `web_search`/`fetch_webpage` custom tools. OpenAI appends `web_search_preview` and `code_interpreter` (with `container: auto` and `include: ["code_interpreter_call.outputs"]`) to the Responses API tools — but `code_interpreter` is gated off when desktop tools are enabled (see "Code interpreter gating" above). Anthropic appends `web_search_20250305` and `code_execution_20250825` and uses `client.beta.messages.stream()` with `betas=["web-search-2025-03-05", "code-execution-2025-08-25", "files-api-2025-04-14"]`; code execution file outputs are extracted from `final_message.content` post-stream (file IDs aren't available during streaming) and downloaded via `client.beta.files.download()`. Gemini cannot combine built-in tools (`google_search`, `code_execution`) with custom function declarations — an API restriction — so it keeps local DuckDuckGo web search. Code execution images from both OpenAI and Anthropic are displayed inline in the chat widget (scaled to max 600px) and saved to `saved_chats/` as PNG files.

**Multi-display desktop tools** — On macOS, `pyautogui.screenshot()` only captures the primary display. The `_macos_display_screenshot()` method uses Quartz `CGWindowListCreateImage` with a per-display `CGRect` to capture individual displays. Displays are indexed in CoreGraphics API order (display 0 = primary at origin 0,0). On Windows, per-display capture uses `ImageGrab.grab(bbox=..., all_screens=True)` via `_get_windows_display_rects()` (EnumDisplayMonitors); rects are sorted to put the primary monitor (origin 0,0) first as display 0. The `screenshot` tool accepts a `display` parameter: omit it to capture ALL displays as separate images (one per display), or specify a display number for a single capture. The schema description explicitly tells the model that omitting the parameter is the recommended first step (`"OMIT this parameter to capture ALL displays at once as separate images — that's the default and the recommended first step when you don't yet know which display has your target"`). For OpenAI and Gemini, screenshot images are delivered as separate user messages (not embedded in function_call_output/function_response) with explicit coordinate system hints. The region screenshot (x, y, width, height) interprets the region coordinates relative to display N's FULL image (via `_display_full_states[N]`), then captures, then stores the result in `_display_states[N]` for subsequent clicks. The screenshot result text now explicitly tells the model "Use pixel coordinates AS YOU READ THEM from this image for mouse_click. Do NOT scale or convert coordinates to a different resolution — the system handles scaling internally" to prevent the model from pre-converting coordinates after inspecting image dimensions via code_interpreter (see "Code interpreter gating" above for the gpt-5.2 history).

**Chat saving is opt-in** — Chats are only saved (on close or by the periodic auto-save) if the user has typed a name in the "Save Chat as" entry. If the field is blank, no chat file is created.

**Multi-instance support** — Multiple instances can run simultaneously. Each instance claims the lowest available instance number via lock files (`agent_lock_N.lock` containing the PID). Instance 1 uses `agent_state.json`, instance 2+ use `agent_state_N.json` for independent geometry and settings persistence. Stale locks (crashed processes) are detected via `ctypes.windll.kernel32.OpenProcess` with executable name verification (`psapi.GetModuleBaseNameW` confirms the PID belongs to `python.exe` or `pythonw.exe`, not a recycled PID from an unrelated process) and reclaimed automatically. The title bar shows `My Agent (N)` for instance 2+. Lock files are cleaned up on graceful close.

**API retry logic** — Same as SelfBot: up to 10 retries with exponential backoff capped at 60s (429) or 90s (529). OpenAI additionally catches `APITimeoutError` and retries without backoff. A 180-second first-content timeout cancels the stream if the model produces no visible output (thinking, text, or tool calls) within that window — a background ticker thread posts elapsed-time messages every 15 seconds while waiting. OpenAI models that reject `temperature` (e.g. `gpt-5.x-chat-*`) are automatically retried without it. OpenAI models that reject specific server-side tools (e.g. `gpt-5.2-pro` rejects `code_interpreter`) are caught via regex on the BadRequestError message — the offending tool is stripped from the request, the rejection is cached in `self._openai_unsupported_tools[model_id]` for the rest of the session, and the call is retried. All three providers break out of the retry loop immediately on success.


**STOP during streaming** — The STOP button sets `stop_requested`, which is checked inside the stream event loop for all three providers (not just between API calls), allowing mid-stream cancellation.

**API cost tracking** — `stream_worker()` tracks real-time API costs across all three providers. Each provider's streaming method returns a usage dict (6th tuple element) with token counts extracted from the API response: Anthropic via `final_message.usage` (includes `cache_creation_input_tokens`, `cache_read_input_tokens`), OpenAI via `stream.get_final_response().usage`, Gemini via the last streaming chunk's `usage_metadata`. The static `_get_pricing(provider, model)` method in `streaming_mixin.py` does a longest-prefix match against hardcoded pricing tables in `constants.py` (`ANTHROPIC_PRICING`, `OPENAI_PRICING`, `GEMINI_PRICING`), returning per-token prices. Costs accumulate across all API calls in a run and are emitted as `cost_update` queue messages, displayed in blue monospace (`cost_info` tag) and gated by the Activity checkbox. Models with no matching prefix silently skip cost display. The `MyAgent_Pricing.txt` file documents all pricing entries. Early returns (STOP, incomplete stream) return `None` for usage, skipping cost for that call.

**State restore fallback** — `_restore_model_params()` validates that the saved model exists in the saved provider's model list. If mismatched (e.g., Anthropic model saved with OpenAI provider due to a race in auto-save), falls back to the first available model for that provider.

## Architecture (Account_Activity_WBC.py)

**Single class design** — Same as the other apps: the `App` class contains all UI, browser automation, HTML parsing, and CSV export logic.

**Browser connection** — Connects to Edge via CDP on port 9222 using Playwright. Searches all open tabs for one containing the target button text. Does not auto-launch Edge — requires the user to start Edge with `--remote-debugging-port=9222` beforehand.

**Threading model** — The click-and-extract loop runs in a background daemon thread (`_click_worker`). A `queue.Queue` passes status messages (info, success, error, done) to the main thread, polled every 50ms via `root.after()`.

**HTML extraction** — After clicking, waits for the DOM row count to stabilise (polling every 1s, up to 30s), then reads the `<tbody data-bind="foreach: PastTransactions()">` element in 50-row JavaScript chunks to avoid Playwright string truncation.

**CSV conversion** — `_convert_html_to_csv()` uses regex to parse WBC's Knockout.js-bound HTML: date from `displayDateOnly` bindings, description from `text: Description` bindings, debit/credit from `IsDebit` conditional blocks, and balance from `account-activity-runningbalance` spans.

**No state persistence** — Unlike SelfBot and MyAgent, this app has no state file. All parameters are set in the UI each run.

## Portability
- No hardcoded paths — the project works when cloned to any directory on any Windows PC
- `LaunchSelfBot.bat` uses `%~dp0` (resolves to its own directory at runtime)
- Python files use relative paths for all runtime file I/O
- The `.venv` is gitignored and must be recreated on each machine (`python -m venv .venv` + `pip install` dependencies)

## Workflow
- After editing or changing a .py file, always re-run it automatically — close any currently running instance first if necessary

## Conventions
- Keep code simple and focused — this is a testbed for experimentation
- Use tkinter for GUI work
- SelfBot uses single-file architecture: all changes go in `SelfBot.py`
- MyAgent uses a mixin-based modular architecture: the `App` class in `MyAgent.py` inherits from 14 mixin classes in the `myagent/` package. Add new methods to the appropriate mixin module by concern. `MyAgent.py` itself contains only `__init__` and the entry point.
- Bank extractor changes go in `Account_Activity_WBC.py`
