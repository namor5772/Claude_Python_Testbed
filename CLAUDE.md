# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment
- OS: Windows 11
- Python: Activate the `.venv` before running Python commands: `source .venv/Scripts/activate`
- After activation, use `python` to run scripts (the venv maps it correctly)
- Shell: bash (Git Bash)

## Commands
```bash
# Activate venv and run SelfBot
source .venv/Scripts/activate && python SelfBot.py

# Activate venv and run MyAgent
source .venv/Scripts/activate && python MyAgent.py

# Activate venv and run MyAgent with auto-launch instruction
source .venv/Scripts/activate && python MyAgent.py -l "Instruction Name"

# Activate venv and run MyAgent headless (no main window, auto-closes on completion)
source .venv/Scripts/activate && python MyAgent.py -l "Instruction Name" --headless

# Activate venv and run Account Activity extractor
source .venv/Scripts/activate && python Account_Activity_WBC.py

# Kill any running instances before relaunching (Windows)
taskkill //F //IM pythonw.exe 2>/dev/null; taskkill //F //IM python.exe 2>/dev/null
```
There are no tests, linter, or build steps — these are single-file testbed apps.

## Project Structure
- `SelfBot.py` — Single-file tkinter GUI chatbot (~3300 lines); works as a solo chatbot or as a dual-instance self-chatting bot via file-based message passing
- `MyAgent.py` — Single-file tkinter GUI autonomous agent (~4000 lines); fire-and-forget task runner with an agentic tool-use loop, supports both Anthropic and OpenAI providers, supports `-l` argument for command-line auto-launch of saved instructions
- `Account_Activity_WBC.py` — Single-file tkinter GUI browser automation utility (~340 lines); connects to Edge via CDP, clicks "Display more" on the Westpac account activity page, and exports transactions as HTML + CSV
- `skills.json` — User-defined skills with content and mode, shared by both apps (created at runtime)
- `system_prompts.json` — Saved system prompts for SelfBot (created at runtime)
- `agent_instructions.json` — Saved agent instructions for MyAgent, with embedded images (created at runtime)
- `saved_chats/` — Directory of saved chat conversations, one `.json` file per chat; a matching `.txt` export of the output window is always saved alongside each `.json` file
- `app_state.json` — Persistent settings for SelfBot instance 1 (created at runtime)
- `app_state_2.json` — Persistent settings for SelfBot instance 2 (created at runtime)
- `agent_state.json` — Persistent settings for MyAgent (created at runtime)
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
- `TOOLS` — Core tools always sent to the API (web_search, fetch_webpage, run_powershell, csv_search)
- `DESKTOP_TOOLS` — 13 pyautogui-based tools, conditionally included when Desktop checkbox is enabled
- `BROWSER_TOOLS` — 11 Playwright/CDP tools, conditionally included when Browser checkbox is enabled
- `_get_tools()` assembles the final tool list dynamically based on UI toggle state

**Adding a new tool** requires three changes:
1. Add schema dict to the appropriate tool list (`TOOLS`, `DESKTOP_TOOLS`, or `BROWSER_TOOLS`)
2. Add a `do_<tool_name>()` method to the `App` class
3. Wire it up in the `stream_worker()` method's tool dispatch block (`elif block.name == "..."`)

**Threading model** — API calls run in a background `stream_worker` thread. A `queue.Queue` passes events (text, thinking, tool info, errors) to the main thread, polled every 50ms via `root.after()`.

**Thinking accumulator lifecycle** — `_current_thinking_text` is reset at `thinking_start` (not at `label`), so the accumulated thinking text survives past the label event and is available when `complete` fires to inject into the peer instance.

**Show Thinking checkbox** — The `show_thinking` BooleanVar (defaults False) gates display of thinking blocks in `check_queue` and `_poll_auto_msg`. This is separate from `thinking_enabled` which controls whether the API generates thinking blocks. Both must be on for thinking to appear in the output.

**Dual geometry persistence** — State files store `geometry` (solo mode) and `duo_geometry` (duo mode via `--no-geometry` flag) independently. `_duo_mode` is set in `__init__` from `sys.argv` before any save can occur. On save, the app reads the existing state file to preserve the other mode's geometry key.

**Skills system** — Three modes: disabled, enabled (injected into system prompt), on-demand (retrieved via `get_skill` tool). Managed through `_build_system_prompt()` and `_get_tools()`.

**DPI handling** — `SetProcessDpiAwareness(2)` is called before any window creation. Screenshot coordinates are scaled via `_screenshot_scale` for mouse click mapping.

**Auto-save on close** — When closing (via [X] button or `taskkill`), all instances auto-save their chat as `.json` + `.txt` to `saved_chats/`. Uses the name from the Save Chat entry if provided, otherwise auto-generates from the first user message. Instance 2's files are suffixed with `_` via `_save_name()` to avoid collisions. A periodic auto-save every 5 seconds on all instances also protects against force-kill data loss.

**Graceful duo shutdown** — Pressing [X] on either instance stops auto-chat, waits for any active streaming to finish, saves both instances' chats, then closes both windows via `WM_CLOSE` messages.

**API retry logic** — `stream_worker` retries up to 10 times on transient API errors. Rate-limit errors (429) use exponential backoff capped at 60s (~6.5 min total). Overload errors (529) use exponential backoff capped at 90s (~10 min total). This makes the app resilient to prolonged Anthropic API outages without absurdly long individual waits.

## Architecture (MyAgent.py)

**Single class design** — Same as SelfBot: the `App` class contains all UI, API, tool execution, and persistence logic.

**Dual-provider support** — A Provider combobox in the instruction editor switches between Anthropic and OpenAI. The internal message format stays Anthropic-style; translation to/from OpenAI format happens at the API boundary only via `_messages_to_responses()`, `_tools_to_responses()`, and `_stream_responses()`. OpenAI uses the Responses API (`client.responses.stream()`) with event-based streaming, flat tool schemas, and `function_call`/`function_call_output` items instead of Chat Completions. The `_ToolBlock` wrapper class gives OpenAI dict-based tool responses the same `.name`/`.id`/`.input` attribute interface as Anthropic's Pydantic objects, so `_execute_tool()` works identically for both providers. Provider selection is saved per-instruction and in `agent_state.json`. The Agent Instruction button is disabled while the agent is running (preventing model/provider changes).

**OpenAI model filtering** — `_fetch_openai_models()` filters the API model list to Responses API compatible families only via `OPENAI_RESPONSES_PREFIXES`: `gpt-4o`, `gpt-4.1`, `gpt-4.5`, `gpt-5`, `o1`, `o3`, `o4`. Non-chat model types (embedding, audio, search, realtime, preview, transcribe, tts) are skipped first. Legacy models (gpt-3.5-turbo, base gpt-4, gpt-4-turbo) are excluded as they don't support the Responses API. `OPENAI_REASONING_PREFIXES` (`o1`, `o3`, `o4`, `gpt-5`) determines which models get `reasoning` params instead of `temperature`. The OpenAI client uses `httpx.Timeout(600.0, connect=10.0, read=120.0)` to prevent indefinite hangs on unresponsive models.

**Temperature/thinking UI gating** — OpenAI reasoning models don't accept `temperature`, so the Temp spinner stays disabled for these models even when thinking is unchecked. This is enforced in `_on_thinking_toggled()`, `_on_model_selected()`, and `_restore_model_params()`. All these methods use `_has_model_widgets()` guards since model controls only exist while the editor is open. Anthropic models always allow temperature when thinking is off.

**Agentic loop** — `stream_worker()` runs a `while True:` loop: dispatches to `_stream_anthropic_call()` or `_stream_responses_call()` based on the provider, streams the response, executes any tool calls, appends results, and loops again. Exits on `end_turn` or when `stop_requested` is set via the STOP button. No fixed iteration limit.

**Tool system** — Four-list structure (`TOOLS`, `DESKTOP_TOOLS`, `BROWSER_TOOLS`, `META_TOOLS`) and `_get_tools()` assembler. `TOOLS` is always included; the others are conditionally added based on Desktop/Browser/Meta checkboxes. Tool dispatch is handled by the `_execute_tool()` helper method. Adding a new tool requires: (1) schema dict in the appropriate tool list, (2) `elif` branch in `_execute_tool()`, (3) `do_<name>()` implementation method, and optionally (4) adding the tool name to the `PARALLEL_SAFE` set if it is thread-safe and stateless.

**Parallel tool execution** — When Claude requests multiple tools in one turn, tool blocks are partitioned into parallel-safe (`web_search`, `fetch_webpage`, `csv_search`, `get_skill`) and sequential (everything else). Parallel-safe tools run concurrently via `concurrent.futures.ThreadPoolExecutor`; sequential tools run one at a time in order. Results are placed into a pre-allocated list indexed by original position, preserving the API-expected ordering.

**Agent Instructions** — Stored in `agent_instructions.json` as `{name: {text: str, images: [{data, media_type, filename}], desktop: bool, browser: bool, meta: bool, provider: str, model: str, temperature: float, thinking_enabled: bool, thinking_effort: str, thinking_budget: int, skill_modes: {skill_name: mode_string}}}`. Images are embedded as base64 and re-attached when loading an instruction. Desktop/Browser/Meta tool toggle states, model parameters (model, temperature, thinking settings), and skill modes are saved per-instruction and restored on load. Skills not present in the snapshot default to disabled; deleted skills are silently skipped.

**Editor draft/commit model** — The instruction editor works on temporary copies (`_editor_images`, `_editor_desktop`, `_editor_browser`). Changes are only committed to live state on SAVE (persists to disk) or Apply (session-only). Closing the editor with [X] discards uncommitted changes. Model params and skill modes are restored immediately on instruction selection (like live state), matching the pattern of being environment-level settings rather than draft state. Model/provider widgets (Provider combo, Model combo, Temp, Thinking) are created inside the editor dialog and only exist while it's open. Widget references are set to `None` on close/Apply so `_has_model_widgets()` guards skip widget updates. A read-only `_model_info_label` on the main toolbar shows the current provider and model at all times.

**Threading model** — Same as SelfBot: background daemon thread for API calls, `queue.Queue` for events, main thread polls every 50ms via `root.after()`.

**PowerShell Safety dialog** — The "PS Safety" button opens a dialog listing all `POWERSHELL_CONFIRM` patterns as checkboxes. Checked = confirmation required (default), unchecked = bypass confirmation and show a `⚠ Confirm bypassed` warning in the output window instead. Disabled patterns are persisted in `agent_state.json` as `disabled_confirm_patterns`. The bypass warning uses the `"warning"` queue message type which always displays regardless of the Activity checkbox.

**Command-line launch** — `python MyAgent.py -l "Name"` auto-loads a saved instruction and starts the agent. Uses `argparse` for `-l`/`--load`. The instruction is loaded via `_auto_launch()`, scheduled as `root.after(100)` to ensure UI is initialized, which then schedules `_start_agent` via `root.after(200)` to allow a full event loop cycle between state setup and agent start. Shows an error dialog listing available names if the instruction is not found. The `-l` flag also auto-populates the "Save Chat as" entry with `"{InstructionName}_{timestamp}"` so output is always captured.

**Headless mode** — `python MyAgent.py -l "Name" --headless` runs without a main window (`root.withdraw()`). Dialogs (`user_prompt`, PS confirmation) skip `transient()` and `grab_set()` so they float as standalone windows. The process auto-closes after the agent loop completes. Designed for orchestrator patterns where a parent MyAgent spawns child instances via `run_powershell`.

**Meta-agent tools** — Three tools in the `META_TOOLS` list, conditionally included when the Meta checkbox is enabled (same gating pattern as Desktop/Browser). Two CRUD tools (`manage_instructions`, `manage_skills`) use a single `action` parameter (`list`/`read`/`create`/`update`/`delete`) to minimize tool count. `manage_instructions` creates entries inheriting the current provider/model/thinking settings. `manage_skills` updates the in-memory skills dict and triggers thread-safe UI refresh via `_post_skill_ui_refresh()`. `run_instruction` launches a saved instruction as a separate MyAgent process (fire-and-forget) using `subprocess.Popen` with `sys.executable`; defaults to headless mode, with an optional `headless=false` parameter to show the GUI. None of these tools are in `PARALLEL_SAFE` since they modify shared state or spawn processes. The Meta toggle is saved per-instruction.

**State persistence** — `agent_state.json` stores provider, last instruction name, model, temperature, thinking settings, display checkbox states (debug, tool calls, activity, show thinking), window geometry, dialog geometries (editor, prompt dialog, confirm dialog, PS Safety dialog), and disabled confirm patterns. Periodic auto-save every 5 seconds. Dialog geometries are also flushed to disk immediately when the dialog closes (editor close, Apply, or prompt/confirm dismiss).

**Chat saving is opt-in** — Chats are only saved (on close or by the periodic auto-save) if the user has typed a name in the "Save Chat as" entry. If the field is blank, no chat file is created.

**No dual-instance support** — Strictly single-instance. No mutex, no lock file, no cross-instance message passing.

**API retry logic** — Same as SelfBot: up to 10 retries with exponential backoff capped at 60s (429) or 90s (529). OpenAI additionally catches `APITimeoutError` (from the 120s read timeout) and retries immediately without backoff.

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
- Single-file architecture: SelfBot changes go in `SelfBot.py`, agent changes go in `MyAgent.py`, bank extractor changes go in `Account_Activity_WBC.py`
