# Claude Python Testbed

A repo containing various Python scripts written using Claude Code. The main application is a full-featured Claude chatbot with a tkinter GUI.

## Contents

- **app.py** — Claude chatbot GUI application (see details below)
- **SelfBot.py** — Dual-instance self-chatting variant (see details below)
- **CLAUDE.md** — Project instructions and conventions for Claude Code sessions
- **system_prompts.json** — Saved system prompts (created at runtime)
- **saved_chats/** — Directory of saved chat conversations, one `.json` file per chat (created at runtime; migrated automatically from old `saved_chats.json` on first launch)
- **app_state.json** — Persistent app settings for app.py and SelfBot instance 1 (created at runtime)
- **app_state_2.json** — Persistent settings for SelfBot instance 2 (created at runtime)
- **skills.json** — Saved skills with content and mode (created at runtime)
- **selfbot.lock** — Lock file for SelfBot cleanup tracking (created/deleted at runtime)
- **selfbot_auto_msg.json** — Shared file for SelfBot cross-instance message injection (created/deleted at runtime)
- **LaunchSelfBot.bat** — One-click launcher that starts both SelfBot instances side by side (see below)
- **selfbot_position.ps1** — PowerShell helper used by the launcher to position and focus windows

## app.py — Claude Chatbot

A desktop chatbot application built with tkinter that connects to the Anthropic API. It supports streaming responses, tool use, image attachments, conversation management, model selection, customisable system prompts, and a skills system for injecting reusable knowledge into conversations.

### Features

#### Model Selection, Temperature & Extended Thinking
- A **Model** dropdown at the top of the window lists all available Claude models, fetched live from the Anthropic API on startup
- Models are shown by display name and the selected model is persisted across sessions via `app_state.json`
- Falls back to a hardcoded list (Sonnet 4.5, Opus 4.6, Haiku 4.5) if the API is unreachable
- Saved chats remember which model was used; loading a chat restores the model if still available
- A **Temp** spinbox sits to the right of the Model dropdown, controlling the API temperature parameter (0.0–1.0 in 0.1 steps)
- Temperature is persisted across sessions in `app_state.json` and saved/restored with each chat
- Lower values (e.g. 0.0) produce more deterministic responses; the default is 1.0

**Extended Thinking** — A **Thinking** checkbox and **Strength** combobox on the model toolbar let you enable Claude's step-by-step reasoning mode. When enabled, Claude shows its internal reasoning in amber/gold italic text before delivering the final answer in green.

| Model type | Thinking mode | Strength control |
|---|---|---|
| **Adaptive** (Opus 4.6, Sonnet 4.6) | `thinking: {type: "adaptive"}` | Effort level: low, medium, high (default), max |
| **Manual** (Sonnet 4.5, Haiku 4.5, etc.) | `thinking: {type: "enabled", budget_tokens: N}` | Token budget: 1K, 4K, 8K (default), 16K, 32K |

- When thinking is enabled, the **temperature controls are greyed out** (the API does not allow temperature with thinking)
- `max_tokens` is automatically raised from 8,192 to 32,768 when thinking is active
- The strength combobox automatically switches between effort levels and budget presets when you change models
- Switching to a model that doesn't support thinking disables the checkbox and re-enables temperature
- Thinking settings (`thinking_enabled`, `thinking_effort`, `thinking_budget`) are persisted in `app_state.json` and saved/restored with each chat
- Thinking and redacted_thinking blocks are preserved during tool-use loops (required by the API for reasoning continuity) but stripped when serializing chats for persistence

#### Chat Interface
- **Streaming responses** — Claude's replies are streamed token-by-token into the chat display for a real-time feel
- **Multi-turn conversation** — Full conversation history is maintained and sent with each request
- **Color-coded messages** — User messages appear in blue, assistant responses in green, errors in red, and tool activity in grey italics
- **Multi-line input** — The input field supports multiple lines; press **Enter** to send, **Shift+Enter** for a newline

#### Tool Use
The chatbot has twenty-eight built-in tools (plus a dynamic `get_skill` tool) that Claude can invoke autonomously during a conversation, organised into three categories:

**Core Tools (always available):**
- **web_search** — Searches the web via DuckDuckGo (`ddgs` library) and returns the top 5 results with titles, URLs, and snippets
- **fetch_webpage** — Fetches the full content of a URL using `httpx`, extracts readable text from HTML (stripping scripts, styles, and tags), and truncates to 20,000 characters
- **run_powershell** — Executes a PowerShell command on the local Windows PC and returns the output (stdout + stderr). Commands have a 30-second timeout and output is truncated at 20,000 characters
- **csv_search** — Searches a delimited text file (CSV, TSV, TXT, or any delimited format) for records matching a value. The file must have a header row. Supports searching a specific column or all columns, with three match modes: `contains` (default), `exact`, and `starts_with` — all case-insensitive. The delimiter is auto-detected from file content using `csv.Sniffer` (sampling the first 8KB), or can be explicitly specified (`,`, `\t`, `|`, `;`). Results are returned as labelled key-value rows, capped at 50 matches by default (configurable via `max_results`). Output is truncated at 20,000 characters

**Desktop Tools (enabled via Desktop checkbox):**
- **screenshot** — Captures the screen (or a specified region) and returns it as an image to Claude. The tool description is dynamically patched at startup with the actual screen resolution. Images wider than 1280px are scaled down for the API, and mouse coordinates are automatically mapped back to screen space so Claude can click using the positions it sees in the image
- **mouse_click** — Clicks at the given image coordinates with configurable button (left/right/middle) and click count (single/double). Coordinates from the screenshot are automatically scaled to the actual screen resolution
- **type_text** — Types text at the current cursor position. Uses `pyautogui.write()` for ASCII and clipboard paste via `pyperclip` for Unicode characters
- **press_key** — Presses a key or key combination (e.g., `enter`, `ctrl+c`, `alt+tab`). Supports common aliases like `windows` → `win`
- **mouse_scroll** — Scrolls the mouse wheel up or down, optionally at a specific screen position
- **open_application** — Opens an application by common name (e.g., `chrome`, `notepad`, `vscode`) using a built-in lookup table, or by full executable path
- **find_window** — Finds windows matching a title pattern using `pygetwindow`, returning titles, positions, and sizes. Can optionally activate (bring to foreground) the first match
- **clipboard_read** — Reads the current text contents of the Windows clipboard via tkinter's `clipboard_get()`. Returns an error message if the clipboard is empty or contains non-text data
- **clipboard_write** — Writes text to the Windows clipboard via tkinter's `clipboard_clear()` and `clipboard_append()`, replacing any current content
- **wait_for_window** — Polls `pygetwindow.getWindowsWithTitle()` every 0.5 seconds until a window matching the given title appears, or times out (default 10 seconds). Returns the window's title, position, and size once found
- **read_screen_text** — Captures a screen region and performs OCR using `winocr` (Windows-native OCR via `Windows.Media.Ocr`). Coordinates are scaled by `_screenshot_scale` to handle DPI differences. No Tesseract installation needed
- **find_image_on_screen** — Locates a reference image file on the screen using `pyautogui.locateOnScreen()` with confidence-based matching (requires `opencv-python`). Returns both screen coordinates and scaled image coordinates for clicking
- **mouse_drag** — Drags the mouse from one point to another using `pyautogui.moveTo()`, `mouseDown()`, `moveTo()`, `mouseUp()`. Coordinates are scaled by `_screenshot_scale`. Useful for drag-and-drop, resizing, sliders, and drawing

**Browser Tools (enabled via Browser checkbox):**
- **browser_open** — Connects to Microsoft Edge via Chrome DevTools Protocol (CDP) and navigates to a URL. Uses the user's real Edge profile with all cookies, logins, and extensions. Launches Edge automatically if it isn't running
- **browser_navigate** — Navigates the current browser page to a new URL
- **browser_click** — Clicks an element by CSS selector (e.g., `#submit-btn`, `button.login`) or by visible text
- **browser_fill** — Fills a form field instantly by CSS selector (clears existing value, no character-by-character typing)
- **browser_get_text** — Reads the text content of the page or a specific element without needing a screenshot. Output is truncated at 20,000 characters
- **browser_run_js** — Executes JavaScript on the page and returns the result. Supports `return` statements for extracting data
- **browser_screenshot** — Takes a visual screenshot of the browser page, resized to max 1280px wide
- **browser_close** — Disconnects the Playwright automation connection. Edge stays open
- **browser_wait_for** — Waits for an element matching a CSS selector to appear on the page using `page.wait_for_selector()`. Returns the element's text content once found, or times out (default 10,000ms)
- **browser_select** — Selects an option from a `<select>` dropdown element using `page.select_option()`. Options can be specified by `value` attribute or visible `label` text
- **browser_get_elements** — Gets information about elements matching a CSS selector via a single `page.evaluate()` JavaScript call. Returns tag name, text content (truncated to 200 chars), all HTML attributes, visibility status, and bounding rect for each match (default limit: 10 elements)

**Dynamic Tool:**
- **get_skill** — Automatically added when on-demand skills exist. Retrieves the full content of a named skill so Claude can access it mid-conversation. The tool's `enum` constraint is dynamically set to the list of available on-demand skill names

When Claude decides to use a tool, the app automatically executes it, feeds the result back, and lets Claude continue — this can loop multiple times in a single turn (e.g., search then fetch a result page, or open a browser then fill a form and click submit).

#### Skills System

Skills are reusable blocks of text (instructions, knowledge, personas, etc.) that can be injected into conversations. They are managed through a dedicated **Skills Manager** window and stored in `skills.json`.

Each skill has one of three modes, cycled via a **Cycle Mode** button:

| Mode | Indicator | Behaviour |
|---|---|---|
| **Disabled** | (no prefix) | Skill exists but is not used |
| **Enabled** | `[ON]` (green) | Skill content is appended to the system prompt on every API call |
| **On-Demand** | `[OD]` (blue) | Skill name is listed in the system prompt; Claude can retrieve its content via the `get_skill` tool when needed |

The **Skills** button in the button bar shows a count summary — e.g., `Skills (2+3)` means 2 enabled and 3 on-demand skills. The button auto-sizes to fit its label text. Click it to open the Skills Manager.

**Included skills:**
- **Example: Pirate Speak** — A simple enabled skill demonstrating persona injection
- **NIP Generation** — An on-demand skill for producing FSANZ-compliant Australian Nutrition Information Panels in structured JSON format, using web search to source official product data with AFCD/NUTTAB fallback

**Skills Manager** provides:
- **Skill Name** entry + **SAVE** / **DELETE** / **NEW** buttons for CRUD operations
- A scrollable **listbox** showing all skills with their mode indicators
- A **text editor** for viewing and editing skill content
- **Cycle Mode** button to toggle a selected skill through disabled → enabled → on-demand → disabled

**How skills are injected:**
- **Enabled** skills are appended as `## Skill: <name>` sections directly into the system prompt
- **On-demand** skills add a `get_skill` tool to the tool list, with the skill names as an enum constraint. The system prompt includes a note listing available on-demand skills and instructing Claude to call `get_skill` when needed
- This keeps the base token cost low for large skill libraries — only enabled skills consume prompt tokens; on-demand skills add only a brief mention plus a lightweight tool definition

#### Desktop Automation

The thirteen desktop tools (screenshot, mouse_click, type_text, press_key, mouse_scroll, open_application, find_window, clipboard_read, clipboard_write, wait_for_window, read_screen_text, find_image_on_screen, mouse_drag) are gated behind a **Desktop** checkbox. When disabled (the default), the desktop tool schemas are not sent to the API at all — Claude doesn't even know they exist, which saves tokens and prevents it from attempting to use unavailable tools.

**DPI-aware coordinate mapping** — The app sets `SetProcessDpiAwareness(2)` (Per-Monitor DPI Aware) at startup before any window creation, so `pyautogui.size()`, `.screenshot()`, and `.click()` all operate in the same physical-pixel coordinate space regardless of Windows display scaling (125%, 150%, etc.). Screenshots wider than 1280px are resized for the API, and the resize ratio is stored; `mouse_click` and `mouse_scroll` automatically scale image coordinates back to screen coordinates, so Claude can use pixel positions directly from the image it sees.

`pyautogui.FAILSAFE` is enabled — moving the mouse to the top-left corner `(0, 0)` immediately aborts any automation in progress. A 0.3-second pause between actions provides a safety buffer.

#### Browser Automation

The eleven browser tools are gated behind a **Browser** checkbox, independent of the Desktop toggle. When disabled (the default), any attempt by Claude to use browser tools returns an error message. Browser tool schemas are only sent to the API when the checkbox is enabled, saving tokens and preventing Claude from attempting to use unavailable tools.

**How it works** — Playwright connects to Microsoft Edge via the Chrome DevTools Protocol (CDP) on port 9222. Instead of launching a sterile automation browser, this approach uses the user's real Edge installation with their full profile (cookies, saved logins, extensions, and sessions).

**Edge connection scenarios:**

| Scenario | What happens |
|---|---|
| Edge not running | App launches Edge with `--remote-debugging-port=9222` |
| Edge running WITH debug port | App connects directly |
| Edge running WITHOUT debug port | Error message: close Edge and retry |
| Connection drops mid-session | Auto-detected and reconnected on next tool call |

**Lifecycle details:**
- `_ensure_browser()` handles the full connection lifecycle: probes port 9222, launches Edge if needed (checking three common install paths), waits up to 15 seconds for the debug port, connects Playwright via CDP, and reuses the first open tab as the active page
- If the connection dies between tool calls (e.g., Edge was closed), the next tool call auto-reconnects
- `browser_close` only disconnects Playwright — Edge stays open with all tabs intact
- Closing the app window automatically cleans up the Playwright connection via `WM_DELETE_WINDOW`

**No `playwright install` needed** — Since the app connects to the system-installed Edge via CDP, it does not use Playwright's bundled browser binaries. Only the `playwright` Python package is required.

#### PowerShell Safety Guardrails

The `run_powershell` tool uses a two-tier safety system to prevent accidental damage:

**Tier 1 — Hard Blocked** (rejected outright, never executed):
- Disk formatting (`Format-Volume`, `Format-Disk`, `diskpart`)
- Shutdown/restart (`Stop-Computer`, `Restart-Computer`)
- Security policy changes (`Set-ExecutionPolicy`, `bcdedit`)
- Registry mass-deletion (`reg delete`, `Remove-ItemProperty` on HKLM/HKCU)
- User account manipulation (`net user /add`, `Disable-LocalUser`, `Remove-LocalUser`)
- Event log clearing (`Clear-EventLog`)

**Tier 2 — Confirmation Required** (a Yes/No dialog appears, defaulting to No):
- File deletion/modification (`Remove-Item`, `rm`, `del`, `Move-Item`, `Set-Content`, `Out-File`)
- Process/service control (`Stop-Process`, `kill`, `Stop-Service`, `Remove-Service`)
- Package removal (`Uninstall-Package`)
- Code execution (`Invoke-Expression`, `iex`, `Start-Process`)
- Risky flags (`-Recurse`, `-Force`)

**Safe commands** (e.g., `Get-Process`, `Get-ChildItem`, `hostname`, `dir`) run freely without interruption.

#### Image Attachments
- Click **Attach Images** to select one or more image files (PNG, JPG, JPEG, GIF, WEBP)
- Attached images are shown as a purple indicator below the input field (click to clear)
- Images are sent to Claude as base64-encoded content blocks alongside your text message
- If you send images without text, the app defaults to asking "What's in this image?"

#### Chat Management (Toolbar)
Two toolbars at the top of the window provide model selection and conversation management:

| Control | Location | Description |
|---|---|---|
| **Model** dropdown | Model toolbar | Select from available Claude models |
| **Temp** spinbox | Model toolbar | Set API temperature (0.0–1.0) |
| **Thinking** checkbox | Model toolbar | Enable extended thinking mode |
| **Strength** combobox | Model toolbar | Set thinking effort (adaptive) or token budget (manual) |
| **DELETE** | Model toolbar | Deletes the selected or named chat from disk |
| **NEW CHAT** | Model toolbar | Clears the current conversation and display, but keeps the active system prompt |
| **Save Chat as** | Chat toolbar | Type a name and click **SAVE** (or press Enter) to save the current conversation |
| **Load Chat** dropdown | Chat toolbar | Select a previously saved chat — restores conversation, system prompt, and model |

Saved chats include:
- The full message history (serialised to JSON, with base64 image data stripped and replaced with `[Image was attached]` placeholders to keep file sizes small; thinking blocks are stripped during serialisation)
- The system prompt text that was active during the chat
- The system prompt name for easy identification
- The model that was in use
- Temperature and extended thinking settings (enabled, effort level, token budget)

Messages are sanitised on both save and load — extra fields from the Anthropic SDK (e.g. `parsed_output`) are stripped to prevent API rejection errors when continuing a reloaded conversation.

#### System Prompt Editor
Click **System Prompt** to open a dedicated editor window with:

- **Save** — Save the current prompt text under a name for reuse
- **Load** — Select from previously saved prompts via a dropdown
- **Delete** — Remove a saved prompt from disk
- **Clear** — Reset the editor fields
- **Apply to Chat** — Set the editor's prompt as the active system prompt and close the editor

When a named system prompt is applied, the window title updates to show it (e.g., `Claude Chatbot — My Prompt`).

#### App State Persistence
- The last-used system prompt name, selected model, temperature, thinking settings, and window geometry (size + position) are saved to `app_state.json`
- On startup, the app restores the last system prompt, model, temperature, thinking state, and window geometry automatically
- **Display safety check** — saved screen dimensions are compared against the current display on startup. If the resolution has changed or the saved position would place the window off-screen, geometry falls back to the default `1050x930` so the window is never lost
- If the "Default" system prompt is missing from `system_prompts.json` (e.g., on first run or after manual deletion), it is automatically recreated from the hardcoded default
- The app starts in a "new chat" state (empty conversation) with the last system prompt and model pre-loaded

#### Rate-Limit Retry

API calls automatically retry on rate-limit (HTTP 429) and overload (HTTP 529) errors with exponential backoff. Rate-limit retries wait 5s, 10s, 20s, 40s; overload retries wait 10s, 20s, 40s, 80s. Up to 5 attempts are made before raising the error. Retry status messages appear in the chat as grey italicised tool-info lines.

#### Debug Mode
- Toggle the **Debug** checkbox to show/hide the full API payload sent with each request
- When enabled, each API call displays:
  - A red **Call #N** counter badge
  - The complete JSON payload (model, system prompt, tools, messages) with base64 image data truncated for readability
  - Clear `--- PAYLOAD SENT TO API ---` / `--- END PAYLOAD ---` delimiters in orange
- When disabled, call counters still appear (in a subtler style) but payloads are hidden

#### Tool Call Display
- Toggle the **Tool Calls** checkbox independently of Debug to show/hide tool call details
- When enabled, each tool invocation displays the full JSON with tool name, call ID, and input arguments in teal-coloured `--- TOOL CALL ---` blocks
- This is separate from the Debug payload view, so you can see just tool calls without the full API payload, or vice versa

#### Activity Display
- Toggle the **Activity** checkbox to show/hide tool activity lines (e.g., "Searching: ...", "Fetching: ...", "Running: ...", "Taking screenshot...") that appear during tool execution
- When disabled, these status lines are suppressed for a cleaner, final-answer-only view
- The **Call #N** counter badges are hidden only when all three of Activity, Debug, and Tool Calls are unchecked — if either Debug or Tool Calls is enabled, the counter badges remain visible

### Requirements

- Python 3 with tkinter (included in standard library)
- An Anthropic API key set as the `ANTHROPIC_API_KEY` environment variable

#### Python Dependencies

```
anthropic
ddgs
httpx
opencv-python
Pillow
playwright
pyautogui
pygetwindow
pyperclip
winocr
```

> **Note:** `playwright install` is **not** required. The app connects to the system-installed Microsoft Edge via CDP, so no bundled browser binaries are needed.

### Running

```bash
# Activate the virtual environment
source .venv/Scripts/activate

# Run the application
python app.py
```

### Architecture

The application is a single-file tkinter app structured around the `App` class:

- **UI Layout** — Grid-based layout with 7 rows: model + temperature + thinking toolbar with DELETE/NEW CHAT buttons (row 0), chat save/load toolbar (row 1), chat display + scrollbar (row 2), input field (row 3), button bar with Attach Images, System Prompt, and Skills buttons (row 4), checkbox row with Debug/Tool Calls/Activity/Desktop/Browser toggles (row 5), and attachment indicator (row 6)
- **Threading** — API calls run in a background daemon thread (`stream_worker`) to keep the UI responsive. A `queue.Queue` passes events (text deltas, thinking deltas, labels, tool info, errors) back to the main thread. When thinking is enabled, the stream worker uses raw event iteration (`content_block_start`, `content_block_delta`, `content_block_stop`) instead of `text_stream` to handle both thinking and text blocks
- **Queue Polling** — The main thread polls the queue every 50ms via `root.after()` and updates the chat display accordingly
- **Persistence** — JSON-based storage handles different concerns: `system_prompts.json` for the prompt library, individual `.json` files in `saved_chats/` for conversation history (one file per chat), `app_state.json` for user preferences, and `skills.json` for the skills library
- **Skills System** — Skills are loaded from `skills.json` on startup. `_build_system_prompt()` assembles the final system prompt by appending enabled skill content and listing on-demand skill names. `_get_tools()` dynamically adds a `get_skill` tool when on-demand skills exist, with the skill names constrained via an `enum` in the input schema
- **Serialisation** — The `_serialize_messages()` method converts Anthropic SDK Pydantic objects (e.g., `ToolUseBlock`, `TextBlock`) to plain dicts via `model_dump()`, strips base64 image data, skips `thinking` and `redacted_thinking` blocks, and sanitises content blocks through `_clean_content_block()` to remove extra SDK fields (like `parsed_output`) that the API rejects on re-submission. `_clean_content_block()` preserves thinking/redacted_thinking blocks with their signatures for tool-use loop continuity
- **HTML Extraction** — The `HTMLTextExtractor` class (a `HTMLParser` subclass) strips HTML tags from fetched web pages, skipping `<script>`, `<style>`, and `<noscript>` blocks, and inserting newlines at block-level element boundaries
- **PowerShell Safety** — Two-tier regex-based guardrail system (`POWERSHELL_BLOCKED` and `POWERSHELL_CONFIRM` pattern lists) checks commands before execution. Confirmation dialogs are dispatched to the main tkinter thread via `root.after()` while the worker thread waits on a `threading.Event`
- **Desktop Automation** — Thirteen tools (`do_screenshot`, `do_mouse_click`, `do_type_text`, `do_press_key`, `do_mouse_scroll`, `do_open_application`, `do_find_window`, `do_clipboard_read`, `do_clipboard_write`, `do_wait_for_window`, `do_read_screen_text`, `do_find_image_on_screen`, `do_mouse_drag`) built on `pyautogui`, `pygetwindow`, `winocr`, and `opencv-python`. Defined in a separate `DESKTOP_TOOLS` list and conditionally included via `_get_tools()` only when the `desktop_enabled` checkbox is enabled. The `screenshot` tool description is dynamically patched with the current screen resolution. Process-level DPI awareness (`SetProcessDpiAwareness(2)`) is set before window creation, and screenshot-to-screen coordinate scaling is handled automatically via `_screenshot_scale`
- **Browser Automation** — Eleven tools (`do_browser_open`, `do_browser_navigate`, `do_browser_click`, `do_browser_fill`, `do_browser_get_text`, `do_browser_run_js`, `do_browser_screenshot`, `do_browser_close`, `do_browser_wait_for`, `do_browser_select`, `do_browser_get_elements`) built on Playwright's CDP connection to Microsoft Edge. Gated behind a `browser_enabled` `BooleanVar` toggle. Tool schemas are conditionally included via `_get_tools()` only when the checkbox is enabled. `_ensure_browser()` manages the full connection lifecycle with auto-reconnect on dead connections. `WM_DELETE_WINDOW` protocol handler ensures clean Playwright disconnection on app close
- **Rate-Limit Retry** — Exponential backoff loop in `stream_worker` handles HTTP 429 (rate limit) and 529 (overload) errors with up to 5 retries before propagating the exception

## SelfBot.py — Dual-Instance Self-Chatting Bot

A variant of app.py that enables two Claude instances to have an autonomous conversation with each other. Both instances share the same codebase but automatically configure themselves for their respective roles.

### How It Works

1. **Launch instance 1** — Run `python SelfBot.py`. It acquires a Windows named mutex and operates as the primary instance. When running solo, there is no send delay and auto-chat is disabled — it behaves like a normal chatbot
2. **Launch instance 2** — Run `python SelfBot.py` again. The mutex detects instance 1 is already running and configures this as the secondary instance
3. **Peer detection** — Instance 1 polls every 2 seconds for a peer SelfBot window. When instance 2 appears, auto-chat and the configurable send delay are automatically enabled; when instance 2 closes, they are disabled again
4. **Send a message in instance 1** — After the first response completes, the user's original message is injected into instance 2's output window (in assistant/green colour), and the reply body is written to a shared file for instance 2 to pick up
5. **Auto-conversation loop** — Each time either instance receives a reply, the response body is written to a shared JSON file (`selfbot_auto_msg.json`). The other instance polls for this file, reads the text into its own input field, and sends it internally — creating a continuous back-and-forth dialogue without any window switching or focus changes

### Instance Detection (Named Mutex)

Instance detection uses a Windows named mutex (`CreateMutexW`) instead of relying solely on a lock file. The OS automatically releases the mutex when a process exits — even on crash or `taskkill` — so stale state is impossible. A `selfbot.lock` file is still created containing instance 1's PID, used by the launcher (`selfbot_position.ps1`) to identify which window is instance 1 for correct positioning.

- If the mutex is not held → this is instance 1; the mutex is acquired and the lock file is created
- If the mutex is already held → this is instance 2

### Name Swapping & Read-Only Fields

The "Terminal user" and "Chatting with" name fields are automatically swapped for instance 2, so each side of the conversation sees the correct perspective. Instance 2 **always** reads names from instance 1's state file (`app_state.json`) and swaps them — not just on first bootstrap. The name fields on instance 2 are **read-only**; names can only be changed in instance 1.

If instance 2 starts before instance 1 has saved its state, the name fields retry loading every 2 seconds until they are populated. Instance 1 also saves state immediately on startup to minimise this race window.

### Separate Persistence

Each instance has its own state file so settings don't interfere:

| Instance | State file | Description |
|---|---|---|
| Instance 1 | `app_state.json` | Primary instance settings |
| Instance 2 | `app_state_2.json` | Secondary instance settings |

Both instances independently persist: model, temperature, thinking settings, send delay, and window geometry. Name fields are only editable and persisted by instance 1; instance 2 always derives its names from instance 1's state.

### Auto-Chat Toggle & Send Delay

When running solo (no peer detected), the **Auto: ON/OFF** button and **Delay(s)** spinbox are hidden. Enter sends messages immediately with no delay.

When a peer instance is detected, the controls appear on instance 1's names toolbar:
- **Auto: ON** (green) — Responses are automatically forwarded to the other instance
- **Auto: OFF** (red) — Auto-forwarding is paused; both instances operate independently
- **Delay(s)** spinbox (0–30 seconds) — Configurable delay before messages are sent, providing time to review or cancel. The delay value is persisted across sessions

Auto-chat is enabled automatically when a peer appears and disabled when it leaves. Manually toggling auto-chat off is respected — the peer poll will not re-enable it until the peer disconnects and reconnects.

These controls are hidden on instance 2 since the toggle controls the loop from instance 1's side.

### Cross-Instance Message Passing

The injection mechanism uses file-based message passing instead of GUI automation, making it reliable regardless of window focus or position:
- When a response completes, the sender writes the text and its PID to `selfbot_auto_msg.json`
- Both instances poll for this file every 500ms via `_poll_auto_msg()`
- The receiver (identified by PID mismatch) reads the text, inserts it into its own input field, and calls `send_message()` internally
- The configured send delay is respected — the text sits visibly in the input field for the delay duration before sending
- No window activation, coordinate clicking, or clipboard pasting is involved

**Thinking block transmission** — When Thinking mode is enabled, the sender's thinking text is included in the JSON payload alongside the response text. The receiving instance displays the styled "Thinking:" block in its output window before the response appears in its input field. This is purely visual — the thinking text is not added to the receiver's conversation history

### Pause & Resume (Pending Injection)

When Auto is toggled OFF mid-conversation, the current API response completes but the injection is deferred:
- A `_pending_injection` flag is set when a response completes while Auto is OFF
- When Auto is toggled back ON, any pending injection fires immediately, resuming the conversation loop
- This allows pausing the conversation to read responses without losing the thread

### Paired Shutdown

Closing either SelfBot window automatically closes the other instance. The `_on_close` handler finds the peer by window title (excluding its own PID) and terminates it before shutting down, so you never have to manually close both windows.

### Default Checkbox States

All checkboxes (Debug, Tool Calls, Activity, Desktop, Browser) default to **off** on startup.

### Running

**Quick launch (recommended):** Double-click `LaunchSelfBot.bat` (or the "Claude SelfBot Duo" desktop shortcut). This kills any existing instances, cleans up stale files, launches both instances with correct timing, positions them side by side filling the screen, and focuses instance 1's input field so you can start typing immediately.

**Manual launch:**
```bash
# Activate the virtual environment
source .venv/Scripts/activate

# Launch instance 1
python SelfBot.py

# In a second terminal, launch instance 2
python SelfBot.py
```
