# Claude Python Testbed

A repo containing various Python scripts written using Claude Code. The two main applications are a full-featured Claude chatbot with dual-instance self-chatting (SelfBot.py) and an autonomous task agent that loops until a job is done (MyAgent.py). There is also a standalone browser automation utility for extracting bank transaction data (Account_Activity_WBC.py).

## Contents

- **SelfBot.py** — Claude chatbot GUI application (see details below)
- **MyAgent.py** — Autonomous AI agent GUI application supporting Anthropic and OpenAI providers (see details below)
- **Account_Activity_WBC.py** — Browser automation utility for extracting Westpac bank transaction data (see details below)
- **CLAUDE.md** — Project instructions and conventions for Claude Code sessions
- **system_prompts.json** — Saved system prompts for SelfBot (created at runtime)
- **agent_instructions.json** — Saved agent instructions for MyAgent, with embedded images (created at runtime, gitignored)
- **saved_chats/** — Directory of saved chat conversations, one `.json` file per chat (created at runtime). A matching `.txt` export of the output window is always saved alongside each `.json` file
- **app_state.json** — Persistent app settings for SelfBot instance 1 (created at runtime)
- **app_state_2.json** — Persistent settings for SelfBot instance 2 (created at runtime)
- **agent_state.json** — Persistent app settings for MyAgent instance 1 (created at runtime)
- **agent_state_N.json** — Persistent settings for MyAgent instance N (created at runtime when multiple instances run)
- **skills.json** — Saved skills with content and mode, shared by both apps (created at runtime)
- **selfbot.lock** — Lock file for SelfBot cleanup tracking (created/deleted at runtime)
- **selfbot_auto_msg.json** — Shared file for SelfBot cross-instance message injection (created/deleted at runtime)
- **LaunchSelfBot.bat** — One-click launcher that starts both SelfBot instances side by side (see below)
- **LaunchMyAgent.bat** — One-click launcher for MyAgent
- **selfbot_position.ps1** — PowerShell helper used by the launcher to position and focus windows

## SelfBot.py — Claude Chatbot & Dual-Instance Self-Chatting Bot

A desktop chatbot application built with tkinter that connects to the Anthropic API. It supports streaming responses, tool use, image attachments, conversation management, model selection, customisable system prompts, and a skills system for injecting reusable knowledge into conversations. When a second instance is launched, it automatically enables dual-instance self-chatting where two Claude instances converse autonomously.

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
- **run_powershell** — Executes a PowerShell command on the local Windows PC and returns the output (stdout + stderr). Commands have a 30-second timeout and output is truncated at 20,000 characters. The tool description instructs Claude to use `Start-Process` when launching GUI applications to avoid blocking the tool loop
- **csv_search** — Searches a delimited text file (CSV, TSV, TXT, or any delimited format) for records matching a value. The file must have a header row. Supports searching a specific column or all columns, with three match modes: `contains` (default), `exact`, and `starts_with` — all case-insensitive. The delimiter is auto-detected from file content using `csv.Sniffer` (sampling the first 8KB), or can be explicitly specified (`,`, `\t`, `|`, `;`). Results are returned as labelled key-value rows, capped at 50 matches by default (configurable via `max_results`). Output is truncated at 20,000 characters

**Desktop Tools (enabled via Desktop checkbox):**
- **screenshot** — Captures the screen (or a specified region) and returns it as an image to Claude. The tool description is dynamically patched at startup with the actual screen resolution. Images wider than 1280px are scaled down for the API, and mouse coordinates are automatically mapped back to screen space so Claude can click using the positions it sees in the image
- **mouse_click** — Clicks at the given image coordinates with configurable button (left/right/middle) and click count (single/double). Coordinates from the screenshot are automatically scaled to the actual screen resolution
- **type_text** — Types text at the current cursor position. Uses `pyautogui.write()` for ASCII and clipboard paste via `pyperclip` for Unicode characters
- **press_key** — Presses a key or key combination (e.g., `enter`, `ctrl+c`, `alt+tab`). Supports common aliases like `windows` → `win`
- **mouse_scroll** — Scrolls the mouse wheel up or down, optionally at a specific screen position
- **open_application** — Opens an application by common name (e.g., `chrome`, `notepad++`, `vscode`) using a built-in lookup table, or by full executable path. Accepts an optional `args` parameter to pass arguments (e.g., a file path to open in the application). Uses `subprocess.Popen` so it returns immediately without blocking the tool loop
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

> **Note (MyAgent only):** The **PS Safety** button opens a dialog where individual Tier 2 patterns can be unchecked to bypass their confirmation dialog. Bypassed patterns still display a `⚠ Confirm bypassed (pattern: ...)` warning in the output window (always visible, regardless of the Activity checkbox). Disabled patterns are persisted across restarts in `agent_state.json`. See the MyAgent section below for details.

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
| **DELETE** | Model toolbar | Deletes the selected or named chat (and any associated `.txt` file) from disk |
| **NEW CHAT** | Model toolbar | Clears the current conversation and display, but keeps the active system prompt |
| **Save Chat as** | Chat toolbar | Type a name and click **SAVE** (or press Enter) to save the current conversation as `.json` + `.txt` |
| **Load Chat** dropdown | Chat toolbar | Select a previously saved chat — restores conversation, system prompt, and model |

Saved chats include:
- The full message history (serialised to JSON, with base64 image data stripped and replaced with `[Image was attached]` placeholders to keep file sizes small; thinking blocks are stripped during serialisation)
- The system prompt text that was active during the chat
- The system prompt name for easy identification
- The model that was in use
- Temperature and extended thinking settings (enabled, effort level, token budget)

Messages are sanitised on both save and load — extra fields from the Anthropic SDK (e.g. `parsed_output`) are stripped to prevent API rejection errors when continuing a reloaded conversation.

**Output .txt export** — Every save (manual or automatic) writes both the `.json` chat file and a matching `.txt` file to `saved_chats/`. The `.txt` captures the raw text content of the output window exactly as shown (including thinking blocks, labels, and formatting) as a plain text file. These `.txt` files are write-only — the app never loads them; they serve as human-readable archives. Deleting a chat via the **DELETE** button always removes both the `.json` and its associated `.txt` file.

**Auto-save on close** — When the app is closed (via [X] button or `taskkill`), all instances automatically save the current chat as both `.json` and `.txt` to `saved_chats/`. If a name is typed in the Save Chat entry, that name is used; otherwise a name is auto-generated from the first user message (or a timestamp fallback). A periodic auto-save runs every 5 seconds on all instances to protect against force-kill data loss. In dual-instance mode, instance 2's saved files are suffixed with `_` (e.g., `My Chat_.json`, `My Chat_.txt`) to avoid filename collisions with instance 1.

#### System Prompt Editor
Click **System Prompt** to open a dedicated editor window with:

- **Save** — Save the current prompt text under a name for reuse
- **Load** — Select from previously saved prompts via a dropdown
- **Delete** — Remove a saved prompt from disk
- **Clear** — Reset the editor fields
- **Apply to Chat** — Set the editor's prompt as the active system prompt and close the editor

When a named system prompt is applied, the window title updates to show it (e.g., `Claude SelfBot — My Prompt`).

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

#### Show Thinking Display
- Toggle the **Show Thinking** checkbox to show/hide the extended thinking blocks that appear when Thinking mode is enabled on the model toolbar
- When checked, thinking blocks are displayed in amber/gold italic text before the response
- When unchecked (the default), thinking blocks are suppressed from the display (the API still generates them, they are just hidden)
- This is independent of the model toolbar **Thinking** checkbox, which controls whether the API generates thinking blocks at all

### Dual-Instance Self-Chatting

When a second instance is launched, SelfBot automatically enables dual-instance mode where two Claude instances converse autonomously.

#### How It Works

1. **Launch instance 1** — Run `python SelfBot.py`. It acquires a Windows named mutex and operates as the primary instance. When running solo, there is no send delay and auto-chat is disabled — it behaves like a normal chatbot
2. **Launch instance 2** — Run `python SelfBot.py` again. The mutex detects instance 1 is already running and configures this as the secondary instance
3. **Peer detection** — Instance 1 polls every 2 seconds for a peer SelfBot window. When instance 2 appears, auto-chat and the configurable send delay are automatically enabled; when instance 2 closes, they are disabled again
4. **Send a message in instance 1** — After the first response completes, the user's original message is injected into instance 2's output window (in assistant/green colour), and the reply body is written to a shared file for instance 2 to pick up
5. **Auto-conversation loop** — Each time either instance receives a reply, the response body is written to a shared JSON file (`selfbot_auto_msg.json`). The other instance polls for this file, reads the text into its own input field, and sends it internally — creating a continuous back-and-forth dialogue without any window switching or focus changes

#### Instance Detection (Named Mutex)

Instance detection uses a Windows named mutex (`CreateMutexW`) instead of relying solely on a lock file. The OS automatically releases the mutex when a process exits — even on crash or `taskkill` — so stale state is impossible. A `selfbot.lock` file is still created containing instance 1's PID, used by the launcher (`selfbot_position.ps1`) to identify which window is instance 1 for correct positioning.

- If the mutex is not held → this is instance 1; the mutex is acquired and the lock file is created
- If the mutex is already held → this is instance 2

#### Name Swapping & Read-Only Fields

The "Terminal user" and "Chatting with" name fields are automatically swapped for instance 2, so each side of the conversation sees the correct perspective. Instance 2 **always** reads names from instance 1's state file (`app_state.json`) and swaps them — not just on first bootstrap. The name fields on instance 2 are **read-only**; names can only be changed in instance 1.

If instance 2 starts before instance 1 has saved its state, the name fields retry loading every 2 seconds until they are populated. Instance 1 also saves state immediately on startup to minimise this race window.

#### Separate Persistence

Each instance has its own state file so settings don't interfere:

| Instance | State file | Description |
|---|---|---|
| Instance 1 | `app_state.json` | Primary instance settings |
| Instance 2 | `app_state_2.json` | Secondary instance settings |

Both instances independently persist: model, temperature, thinking settings, send delay, and window geometry. Name fields are only editable and persisted by instance 1; instance 2 always derives its names from instance 1's state.

**Independent geometry for solo vs duo mode** — Each state file stores two separate geometry keys: `geometry` (used when SelfBot is launched manually as a single instance) and `duo_geometry` (used when launched via the shortcut/batch file). Resizing or repositioning in one mode does not affect the other. On first duo launch, windows default to side-by-side filling the screen; subsequent duo launches restore the saved duo geometry.

#### Auto-Chat Toggle & Send Delay

When running solo (no peer detected), the **Auto: ON/OFF** button and **Delay(s)** spinbox are hidden. Enter sends messages immediately with no delay.

When a peer instance is detected, the controls appear on instance 1's names toolbar:
- **Auto: ON** (green) — Responses are automatically forwarded to the other instance
- **Auto: OFF** (red) — Auto-forwarding is paused; both instances operate independently
- **Delay(s)** spinbox (0–30 seconds) — Configurable delay before messages are sent, providing time to review or cancel. The delay value is persisted across sessions

Auto-chat is enabled automatically when a peer appears and disabled when it leaves. Manually toggling auto-chat off is respected — the peer poll will not re-enable it until the peer disconnects and reconnects.

These controls are hidden on instance 2 since the toggle controls the loop from instance 1's side.

#### Cross-Instance Message Passing

The injection mechanism uses file-based message passing instead of GUI automation, making it reliable regardless of window focus or position:
- When a response completes, the sender writes the text and its PID to `selfbot_auto_msg.json`
- Both instances poll for this file every 500ms via `_poll_auto_msg()`
- The receiver (identified by PID mismatch) reads the text, inserts it into its own input field, and calls `send_message()` internally
- The configured send delay is respected — the text sits visibly in the input field for the delay duration before sending
- No window activation, coordinate clicking, or clipboard pasting is involved

**Thinking block transmission** — When Thinking mode is enabled, the sender's thinking text is included in the JSON payload alongside the response text. The receiving instance displays the styled "Thinking:" block in its output window before the response appears in its input field. This is purely visual — the thinking text is not added to the receiver's conversation history

#### Pause & Resume (Pending Injection)

When Auto is toggled OFF mid-conversation, the current API response completes but the injection is deferred:
- A `_pending_injection` flag is set when a response completes while Auto is OFF
- When Auto is toggled back ON, any pending injection fires immediately, resuming the conversation loop
- This allows pausing the conversation to read responses without losing the thread

#### Paired Shutdown

Closing either SelfBot window stops the auto-chat conversation, waits for any in-flight API streaming to finish, auto-saves both instances' chats (`.json` + `.txt`), and then shuts down both instances cleanly via `WM_CLOSE` messages. Instance 2's files are suffixed with `_` to avoid collisions. A periodic auto-save every 5 seconds on all instances also protects against force-kill (`taskkill /F`, `Stop-Process`) data loss.

#### Message Display Formatting

Both user and assistant messages display their content on the line below the label (e.g., "You:" on one line, message text on the next). This consistent below-label formatting improves readability during autonomous conversations.

#### Default Checkbox States

All checkboxes (Debug, Tool Calls, Activity, Show Thinking, Desktop, Browser) default to **off** on startup.

### Requirements

- Windows 10/11
- Python 3 with tkinter (included in standard library)
- At least one of `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` environment variables (MyAgent supports both; SelfBot requires Anthropic)

#### Python Dependencies

```
anthropic
openai
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

### Setup (New Machine)

The project is fully portable — no hardcoded paths. To set up on a new Windows PC:

```bash
# Clone the repository
git clone https://github.com/namor5772/Claude_Python_Testbed.git
cd Claude_Python_Testbed

# Create and activate the virtual environment
python -m venv .venv
source .venv/Scripts/activate   # Git Bash
# or: .venv\Scripts\activate    # CMD / PowerShell

# Install dependencies
pip install anthropic openai ddgs httpx opencv-python Pillow playwright pyautogui pygetwindow pyperclip winocr

# Set your API key(s) (or add to your environment permanently)
export ANTHROPIC_API_KEY="your-key-here"
export OPENAI_API_KEY="your-key-here"      # optional, for MyAgent OpenAI support
```

The `.venv` directory is gitignored and must be recreated on each machine. All runtime files (`app_state.json`, `skills.json`, `saved_chats/`, etc.) are created automatically on first run.

### Running

**Solo mode:**
```bash
# Activate the virtual environment
source .venv/Scripts/activate

# Run the application
python SelfBot.py
```

**Dual-instance mode (recommended):** Double-click `LaunchSelfBot.bat` (or the "Claude SelfBot Duo" desktop shortcut). This kills any existing instances, cleans up stale files, launches both instances with `--no-geometry` (so SelfBot positions itself using the saved duo geometry or side-by-side defaults), and focuses instance 1's input field so you can start typing immediately.

**Manual dual launch:**
```bash
# Activate the virtual environment
source .venv/Scripts/activate

# Launch instance 1
python SelfBot.py

# In a second terminal, launch instance 2
python SelfBot.py
```

### Architecture

The application is a single-file tkinter app structured around the `App` class:

- **UI Layout** — Grid-based layout with 7 rows: model + temperature + thinking toolbar with DELETE/NEW CHAT buttons (row 0), chat save/load toolbar with SAVE button (row 1), chat display + scrollbar (row 2), input field (row 3), button bar with Attach Images, System Prompt, and Skills buttons (row 4), checkbox row with Debug/Tool Calls/Activity/Show Thinking/Desktop/Browser toggles (row 5), and attachment indicator (row 6)
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
- **Auto-Save & Graceful Shutdown** — `_auto_save_on_close()` silently saves the chat (`.json` + `.txt`) using the entry field name or an auto-generated name; instance 2's filenames are suffixed with `_` via `_save_name()` to avoid collisions. `_periodic_save()` runs every 5 seconds on all instances and triggers auto-save when new messages are detected. `_on_close()` stops auto-chat, waits for streaming to finish via `_finish_close()` polling, saves the current instance's chat, sends `WM_CLOSE` to peer windows, and cleans up lock files and browser connections. Re-entrancy is guarded by a `_closing` flag, and `_poll_auto_msg`/`_auto_msg_delayed_send`/`_poll_for_peer` all bail immediately when closing

---

## MyAgent.py — Autonomous AI Task Agent

A fire-and-forget autonomous task runner built with tkinter that supports both **Anthropic** (Claude) and **OpenAI** (GPT-4.1, o4-mini, etc.) APIs. Unlike SelfBot (which is a conversational chatbot), MyAgent is designed for hands-off task execution: you configure an **Agent Instruction** (a task description, optionally with images), select a **Provider** and **Model**, press **START**, and the AI autonomously loops — calling tools, interpreting results, calling more tools — until the task is complete. The user is a passive observer. The window title is **"Claude Agent"** (with **"[OpenAI]"** appended when using the OpenAI provider).

### How the Agentic Loop Works

1. **Configure** — Write or load an Agent Instruction describing the task (e.g., "Search for today's top tech news and summarise it", "Check disk space and clean up temp files"). Optionally attach reference images.
2. **Press START** (or use `-l` from the command line) — The instruction is injected as the first user message and a background thread begins the agentic loop.
3. **Loop** — `stream_worker()` runs a `while True:` loop:
   - Sends the full message history to the selected API provider via streaming.
   - Streams the response token-by-token into the display.
   - If the API returns `stop_reason: "tool_use"`: executes all requested tools with **parallel execution** for network I/O tools (including `user_prompt`, which pauses the loop to show a dialog and wait for user input), appends the results to the conversation, and **loops again** (next API call with updated history).
   - If the API returns `stop_reason: "end_turn"`: the task is complete — the loop exits.
4. **Press STOP** (optional) — Halts the loop cleanly at the top of the next iteration or after the current API call finishes.

There is **no fixed iteration limit** — the agent runs until Claude decides it is done or the user hits STOP. Each iteration displays a **Call #N** counter badge so you can track how many API round-trips have occurred.

### Command-Line Launch

MyAgent supports a `-l` / `--load` argument to auto-load a saved instruction and immediately start the agent — useful for scripting and automation without manual GUI interaction:

```bash
# Normal launch (GUI only)
python MyAgent.py

# Auto-load an instruction and start the agent
python MyAgent.py -l "Weather_Agent3"

# Auto-load and run headless (no main window, auto-closes on completion)
python MyAgent.py -l "Weather_Agent3" --headless

# Show usage help
python MyAgent.py --help
```

When launched with `-l`, the app restores window geometry and display settings normally, then loads the named instruction (text, images, tool toggles, provider, model, skill modes) and calls START automatically. The "Save Chat as" entry is auto-populated with `"{InstructionName}_{timestamp}"` so output is always captured. If the instruction name is not found, an error dialog lists all available instruction names.

**Headless mode** — Adding `--headless` hides the main window (`root.withdraw()`). Dialogs (`user_prompt`, PS confirmation) still appear as standalone floating windows when needed. The process auto-closes after the agent loop completes. Designed for orchestrator patterns where a parent MyAgent spawns child instances via `run_instruction` (preferred) or `run_powershell`.

### Features

#### Agent Instructions

Agent Instructions are pre-configured task descriptions that serve as the first (and only) user message. They are managed through a dedicated **Agent Instruction** editor window and stored in `agent_instructions.json`.

| Control | Description |
|---|---|
| **Instruction Name** entry | Name for saving/loading instructions |
| **SAVE** button | Save the instruction (text, images, tool toggles, provider, model parameters, skill modes) to disk and make it the active instruction |
| **DELETE** button | Remove the named instruction from disk |
| **CLEAR** button | Reset the editor — clears text, images, and tool toggles |
| **Load Instruction** dropdown | Select a previously saved instruction — populates the editor fields for preview |
| **Text editor** | Multi-line area for writing the task description |
| **Attach Images** button | Select image files to attach to the instruction |
| **Remove Selected** button | Delete selected images from the image list |
| **Desktop** checkbox | Enable/disable the 13 desktop automation tools for this instruction |
| **Browser** checkbox | Enable/disable the 11 browser automation tools for this instruction |
| **Meta** checkbox | Enable/disable the 3 meta-agent tools (`manage_instructions`, `manage_skills`, `run_instruction`) for this instruction |
| **Skills** button | Open the Skills Manager to configure skills; the button label shows a count summary (e.g., `Skills (2+3)` = 2 enabled + 3 on-demand) |
| **Image list** | Scrollable listbox showing attached image filenames (purple text, multi-select) |
| **Apply** button | Make the instruction active for this session (no disk write) and close the editor |

**Draft/commit editing model** — The editor works on a temporary copy of all data (text, images, Desktop/Browser/Meta toggles). Loading an instruction or making edits only affects the editor's working copy. Changes are only committed when you explicitly press SAVE or Apply. Closing the editor with [X] discards all uncommitted changes.

| Action | Makes it active | Saves to disk | Closes editor |
|---|---|---|---|
| **Load Instruction** | No | No | No |
| **SAVE** | Yes | Yes | No |
| **Apply** | Yes | No | Yes |
| **Close [X]** | No | No | Yes |

**Images persist with instructions** — When you save a named instruction, any attached images are embedded as base64 data inside `agent_instructions.json`. Loading that instruction later automatically re-attaches those images. This means a task like "analyse this screenshot and do X" can be saved as a reusable instruction that always includes its reference image.

**Tool toggles persist with instructions** — Each saved instruction stores its Desktop, Browser, and Meta checkbox states. Loading an instruction restores these toggles in the editor; SAVE or Apply commits them to the main window.

**Provider and model parameters persist with instructions** — Each saved instruction stores the provider (Anthropic or OpenAI), model, temperature, and thinking settings. Loading an instruction from the dropdown immediately restores the provider, refreshes the model list, and sets the model and thinking parameters on the main toolbar.

**Skill modes persist with instructions** — Each saved instruction snapshots the current skill modes (disabled/enabled/on-demand for every skill). Loading an instruction restores these modes immediately, updating both `skills.json` and the Skills button label. Skills that didn't exist when the instruction was saved default to disabled. This effectively makes each instruction a self-contained task profile — text, images, tool categories, provider, model configuration, and skills environment — so different tasks can target different providers, models, settings, and skill sets.

When a named instruction is applied, the window title updates to show it (e.g., `Claude Agent — Daily News Brief`).

A "Default" instruction is automatically created on first run if missing. Old-format instruction files (plain string values) are auto-migrated to the new dict format that includes image data.

#### Provider Selection & Model Selection

A **Provider** combobox on the model toolbar switches between **Anthropic** and **OpenAI**. Only providers with valid API keys are shown (set `ANTHROPIC_API_KEY` and/or `OPENAI_API_KEY`). The provider combobox is **locked (disabled) while the agent is running** to prevent mid-run changes.

When switching providers, the **Model** dropdown refreshes with available models for that provider:
- **Anthropic** — Fetches models live from the Anthropic API (falls back to Claude Sonnet 4.5, Opus 4.6, Haiku 4.5)
- **OpenAI** — Fetches models from the OpenAI API, filtered to Responses API compatible families only: `gpt-4o`, `gpt-4.1`, `gpt-4.5`, `gpt-5`, `o1`, `o3`, `o4` (falls back to GPT-5, GPT-5-mini, GPT-4.1, GPT-4.1-mini, o4-mini). Legacy models (gpt-3.5-turbo, base gpt-4, gpt-4-turbo) are excluded as they don't support the Responses API

A **Temp** spinbox controls temperature (0.0–1.0), and a **Thinking** checkbox with **Strength** combobox enables extended thinking/reasoning.

| Provider | Model type | Thinking mode | Strength control |
|---|---|---|---|
| Anthropic | **Adaptive** (Opus 4.6, Sonnet 4.6) | `thinking: {type: "adaptive"}` | Effort level: low, medium, high (default), max |
| Anthropic | **Manual** (Sonnet 4.5, Haiku 4.5, etc.) | `thinking: {type: "enabled", budget_tokens: N}` | Token budget: 1K, 4K, 8K (default), 16K, 32K |
| OpenAI | **Reasoning** (o1, o3, o4, gpt-5 series) | `reasoning: {effort: ..., summary: "auto"}` | Effort level: low, medium, high |
| OpenAI | **Standard** (GPT-4o, GPT-4.1, etc.) | Not supported | N/A |

**Temperature and thinking controls are model-aware** — OpenAI reasoning models (o1/o3/o4/gpt-5) don't accept a `temperature` parameter, so the Temp spinner stays disabled for these models even when thinking is unchecked. Standard OpenAI models show the Temp spinner normally. This is enforced across all code paths: model selection, thinking toggle, and state restore.

Provider, model, temperature, and thinking settings are all persisted across sessions in `agent_state.json` and saved/restored per Agent Instruction.

#### Tool Use

MyAgent has thirty-one built-in tools and the dynamic `get_skill` tool, organised into four categories:

**Core Tools (always available):** `web_search`, `fetch_webpage`, `run_powershell`, `csv_search`, `user_prompt`

**Desktop Tools (enabled via Desktop checkbox):** `screenshot`, `mouse_click`, `type_text`, `press_key`, `mouse_scroll`, `open_application`, `find_window`, `clipboard_read`, `clipboard_write`, `wait_for_window`, `read_screen_text`, `find_image_on_screen`, `mouse_drag`

**Browser Tools (enabled via Browser checkbox):** `browser_open`, `browser_navigate`, `browser_click`, `browser_fill`, `browser_get_text`, `browser_run_js`, `browser_screenshot`, `browser_close`, `browser_wait_for`, `browser_select`, `browser_get_elements`

**Meta Tools (enabled via Meta checkbox):** `manage_instructions`, `manage_skills`, `run_instruction` — tools for the agent to manage its own instruction library, shared skills, and launch other agents. `manage_instructions` lets the agent list, read, create, update, or delete saved instructions (changes apply to future runs, not the current one); read/create/update actions include `skill_modes` (a map of skill names to disabled/enabled/on_demand modes), and update uses merge semantics so omitted skills keep their current mode. `manage_skills` lets the agent manage skills with mode control (disabled/enabled/on-demand). `run_instruction` launches a saved instruction as a separate MyAgent process (fire-and-forget via `subprocess.Popen`); defaults to headless mode, with an optional `headless=false` parameter to show the GUI window — the launched process runs independently and the PID is returned. None of these tools are parallel-safe since they modify shared state or spawn processes.

**User Interaction Tool:**
- **user_prompt** — Pauses the agentic loop and displays a modal dialog to the user with the agent's message, then waits for the user to type a response. This is the **only** way the agent can get user input mid-task (e.g., asking the user to log in, approve an action, or make a choice). The system prompt strongly instructs Claude to always use this tool rather than outputting a question as plain text (which would end the turn and exit the loop). The user types their response and presses **Enter** to submit (or **Ctrl+Enter** to insert a newline for multi-line responses), or dismisses the dialog (via [X]) to return a default "no response" message. The user's injected response is echoed in the chat display as "You: [text]" so the conversation flow is visible, and the agent's follow-up response gets a fresh "Agent:" heading

**Dynamic Tool:** `get_skill` — automatically added when on-demand skills exist

All tool behaviour (DPI-aware coordinate mapping, browser CDP connection to Edge, PowerShell safety guardrails, image compression) is identical to SelfBot. See the SelfBot.py tool sections above for full details.

#### Parallel Tool Execution

When Claude requests multiple tools in a single turn, MyAgent automatically classifies each tool as **parallel-safe** or **sequential** and executes them accordingly:

**Parallel-safe tools** (`web_search`, `fetch_webpage`, `csv_search`, `get_skill`) run concurrently via `ThreadPoolExecutor` — if Claude requests three web searches at once, they execute simultaneously rather than one after another. A status message ("Running N tools in parallel...") appears in the Activity output when multiple parallel tools fire.

**Sequential tools** (all desktop, browser, `run_powershell`, and `user_prompt` tools) run one at a time in their original order, since they interact with shared state (screen, browser session, filesystem, user attention).

Results are slotted back into their original API-requested order regardless of execution order, so the model always sees responses in the sequence it expects. Tool dispatch is handled by the `_execute_tool()` helper method, which is thread-safe for parallel-safe tools.

#### Skills System

Shared with SelfBot — both apps read from the same `skills.json` file. The three-mode system (disabled, enabled, on-demand) works identically. See the SelfBot.py Skills System section above for full details.

The **Skills** button is located in the **Agent Instruction Editor** (not on the main window), since skill modes are saved and restored per-instruction. Opening the Skills Manager from the editor makes it clear that the skills configuration is part of the instruction's environment.

#### Image Attachments

- Image management is integrated into the **Agent Instruction Editor** — click **Attach Images** to select files (PNG, JPG, JPEG, GIF, WEBP)
- Attached images appear in a scrollable listbox showing filenames in purple text
- Select one or more images and click **Remove Selected** to delete them (supports Ctrl+click and Shift+click for multi-select)
- Images are sent to Claude as base64-encoded content blocks alongside the Agent Instruction text when START is pressed
- Images exceeding 4.8 MB are automatically compressed — first trying JPEG at decreasing quality levels (90, 75, 60, 45, 30), then progressively halving dimensions if still too large

#### Chat Save

Chat saving is opt-in — there is no manual SAVE button, and **no chat is saved unless you type a name** in the **Save Chat as** entry field.

- The **Save Chat as** entry field on the chat toolbar sets the filename for saved chats. If left blank (the default), **no chat file is created** — neither on close nor by the periodic auto-save
- **Periodic auto-save** every 5 seconds writes `.json` + `.txt` to `saved_chats/` whenever new messages are detected, but only if a save name is provided
- **Auto-save on close** — closing the window (or `taskkill`) saves the current run, but only if a save name is provided
- Saved chats include the full message history, system prompt, agent instruction name, model, temperature, and thinking settings
- Base64 image data is stripped during serialisation and replaced with `[Screenshot]` or `[Image was attached]` placeholders

#### Display Toggles

Four checkboxes on the main window control what is shown in the output display (all default to **off** on first run, then **persist across sessions** via `agent_state.json`), plus a PS Safety button:

| Checkbox | What it controls |
|---|---|
| **Debug** | Full API payload JSON with each request |
| **Tool Calls** | Tool name, call ID, and input arguments in teal `--- TOOL CALL ---` blocks |
| **Activity** | Tool activity status lines (e.g., "Searching: ...", "Fetching: ...", "Taking screenshot...") |
| **Show Thinking** | Extended thinking blocks in amber/gold italic text |
| **PS Safety** button | Opens a dialog to selectively disable individual PowerShell confirmation patterns (see below) |

Desktop and Browser tool toggles are managed per-instruction inside the Agent Instruction Editor.

The **Call #N** counter badges are hidden only when all three of Activity, Debug, and Tool Calls are unchecked.

#### PS Safety — Deselectable Confirm Patterns

The **PS Safety** button (next to the Browser checkbox) opens a dialog listing all 24 `POWERSHELL_CONFIRM` patterns as checkboxes:

- **Checked** (default) — the pattern requires a confirmation dialog before execution, as normal
- **Unchecked** — the confirmation dialog is bypassed; the command runs immediately and a `⚠ Confirm bypassed (pattern: ...)` warning is displayed in the output window

The bypass warning always appears regardless of the Activity checkbox state. Disabled patterns and the dialog's position/size are persisted in `agent_state.json` across restarts.

#### App State Persistence

- **Multi-instance state** — Each instance claims the lowest available instance number via lock files (`agent_lock_N.lock`). Instance 1 saves to `agent_state.json`, instance 2+ to `agent_state_N.json`. All settings (provider, model, geometry, dialog positions, display checkboxes, disabled confirm patterns) are independent per instance. Stale locks from crashed processes are detected via Windows `OpenProcess` and reclaimed. The title bar shows `Claude Agent (N)` for instance 2+
- Provider, last-used instruction name, model, temperature, thinking settings, display checkbox states (Debug, Tool Calls, Activity, Show Thinking), main window geometry, and dialog geometries are saved per instance
- On startup, the app restores all settings and the last instruction (including its images, Desktop/Browser/Meta toggles, provider, and model parameters) automatically. If the saved model doesn't exist in the saved provider's model list (e.g., provider/model mismatch from a corrupted state file), it falls back to the first available model for that provider
- **Persistent dialog geometry** — The **Agent Instruction Editor**, **Agent Request** (user_prompt), and **PowerShell Confirm** dialog windows all remember their size and position across sessions. Resizing or moving any dialog persists to the instance's state file and is restored the next time that dialog is opened
- **Display safety check** — saved screen dimensions are compared against the current display; if the resolution has changed, geometry falls back to defaults so windows are never lost off-screen

#### Rate-Limit Retry

API calls automatically retry up to 10 times on transient errors with exponential backoff. Rate-limit errors (HTTP 429) use backoff capped at 60 seconds. Overload errors (HTTP 529) use backoff capped at 90 seconds. Retry status messages appear in the output as grey italicised lines.

**OpenAI stream timeout** — The OpenAI client is configured with a 120-second read timeout (`httpx.Timeout(600.0, connect=10.0, read=120.0)`). If no data arrives for 2 minutes during streaming, the connection is aborted and retried. This prevents the app from hanging indefinitely on unresponsive models. Timeout errors (`APITimeoutError`) are retried immediately (no backoff) since the issue is typically a dropped connection rather than server overload.

#### Graceful Shutdown

Closing the window stops the agentic loop, waits for any in-flight API streaming to finish (polling every 200ms), auto-saves the chat, cleans up any browser connection, then destroys the window. `SIGINT` (Ctrl+C) is suppressed — the only way to stop is via the STOP button or closing the window.

### UI Layout

The window is 1050x930 (default). Grid layout with 4 rows:

| Row | Contents |
|---|---|
| **Row 0** | Model toolbar: Provider dropdown, Model dropdown, Temp spinbox, Thinking checkbox, Strength combobox |
| **Row 1** | Chat toolbar: Agent Instruction button, Save Chat as entry, START button (green), STOP button (red) |
| **Row 2** | Chat display: read-only text area with scrollbar, colour-coded output |
| **Row 3** | Checkbox row: Debug, Tool Calls, Activity, Show Thinking, PS Safety button |

**Colour coding:** User/instruction text in blue, agent responses in green, errors in red, tool activity in grey italics, debug payloads in amber monospace, tool call details in teal monospace, call counters as white-on-red badges, thinking blocks in gold italic on pale yellow.

### Key Differences from SelfBot.py

| Aspect | SelfBot.py | MyAgent.py |
|---|---|---|
| **Paradigm** | Interactive chatbot — user sends messages, gets replies | Autonomous agent — configure a task, press START, observe |
| **User input** | Multi-line text input field for typing messages | No input field — task is defined via Agent Instruction editor; mid-task input via `user_prompt` tool dialog |
| **Controls** | Send button (Enter key) | START / STOP buttons |
| **Conversation** | Multi-turn back-and-forth with user | Single task instruction, then autonomous tool-use loop |
| **Multi-instance** | Yes — two instances can self-chat autonomously | Yes — unlimited instances with independent state via lock files |
| **System prompt editor** | Full editor with save/load/delete/apply | No user-facing editor — system prompt is built internally |
| **Task config** | System prompts (reusable prompt text) | Agent Instructions (reusable task descriptions with embedded images) |
| **State file** | `app_state.json` / `app_state_2.json` | `agent_state.json` / `agent_state_N.json` (per instance) |
| **Instruction file** | `system_prompts.json` | `agent_instructions.json` |
| **Chat loading** | Save and load chats | Save only (no load-back into UI) |
| **API providers** | Anthropic only | Anthropic + OpenAI (switchable via Provider combobox) |
| **Window title** | "Claude SelfBot" | "Claude Agent" (+ "[OpenAI]" when using OpenAI) |

### Running

```bash
# Activate the virtual environment
source .venv/Scripts/activate

# Run the application
python MyAgent.py
```

Or double-click `LaunchMyAgent.bat` (or the "MyAgent" desktop shortcut).

### Architecture

The application is a single-file (~3,700 lines) tkinter app structured around the `App` class, sharing the same single-class design philosophy as SelfBot.py:

- **UI Layout** — Grid-based layout with 4 rows: provider + model + temperature + thinking toolbar (row 0), chat toolbar with Agent Instruction button, save-chat entry, and START/STOP buttons (row 1), chat display + scrollbar (row 2), checkbox row with Debug/Tool Calls/Activity/Show Thinking toggles and PS Safety button (row 3). Image attachments, Desktop/Browser tool toggles, and the Skills button are managed inside the Agent Instruction editor window
- **Threading** — API calls run in a background daemon thread (`stream_worker`) to keep the UI responsive. A `queue.Queue` passes events (text deltas, thinking deltas, call counters, tool info, errors, completion) back to the main thread, polled every 50ms via `root.after()`
- **Dual-Provider Support** — A Provider combobox switches between Anthropic and OpenAI. The internal message format stays Anthropic-style; translation to/from OpenAI format happens at the API boundary via `_messages_to_responses()`, `_tools_to_responses()`, and `_stream_responses()`. OpenAI uses the Responses API (`client.responses.stream()`) with event-based streaming, flat tool schemas, and top-level `function_call`/`function_call_output` items. The `_ToolBlock` wrapper class gives OpenAI dict-based tool responses the same `.name`/`.id`/`.input` attribute interface as Anthropic's Pydantic objects, so `_execute_tool()` works identically for both providers
- **Agentic Loop** — The `stream_worker` contains a `while True:` loop that dispatches to `_stream_anthropic_call()` or `_stream_responses_call()` based on the provider, processes the response, executes any requested tools (including `user_prompt` which pauses to collect user input via a modal dialog), appends results, and loops again. The loop exits on `end_turn` or when `stop_requested` is set via the STOP button. An **auto-prompt safety net** keeps interactive instructions alive: if the instruction text mentions `user_prompt` but the model ends its turn without calling it, the agent automatically injects a `user_prompt` dialog asking the user what to do next (submitting an empty response exits the loop)
- **Persistence** — JSON-based storage: `agent_instructions.json` for the instruction library (with embedded images, Desktop/Browser/Meta toggle state, provider, model parameters, and skill modes), individual `.json` + `.txt` files in `saved_chats/` for completed runs, `agent_state.json` (instance 1) or `agent_state_N.json` (instance N) for user preferences, dialog geometries (editor, prompt dialog, confirm dialog, PS Safety dialog), and disabled confirm patterns, and `skills.json` (shared with SelfBot) for the skills library
- **Tool System** — Four global tool lists (`TOOLS`, `DESKTOP_TOOLS`, `BROWSER_TOOLS`, `META_TOOLS`) define API tool schemas, assembled dynamically by `_get_tools()` based on checkbox state. Tool dispatch is handled by the `_execute_tool()` helper method, which routes each tool call to its implementation and returns the result. Adding a new tool requires: (1) schema dict in the appropriate tool list, (2) `elif` branch in `_execute_tool()`, (3) `do_<name>()` implementation method, and optionally (4) adding the tool name to the `PARALLEL_SAFE` set if it is thread-safe and stateless
- **Parallel Tool Execution** — When Claude requests multiple tools in one turn, tool blocks are partitioned into parallel-safe (`web_search`, `fetch_webpage`, `csv_search`, `get_skill`) and sequential (everything else). Parallel-safe tools run concurrently via `concurrent.futures.ThreadPoolExecutor`; sequential tools run one at a time in order. Results are placed into a pre-allocated list indexed by original position, preserving the API-expected ordering
- **PowerShell Safety** — Same two-tier regex-based guardrail system as SelfBot, plus a **PS Safety** dialog that allows individual confirm patterns to be disabled. Disabled patterns bypass the confirmation dialog and emit a `"warning"` queue message (always displayed, not gated by the Activity checkbox). Confirmation dialogs are dispatched to the main tkinter thread via `root.after()` while the worker thread waits on a `threading.Event`
- **Rate-Limit Retry** — Exponential backoff in `stream_worker` handles HTTP 429 and 529 errors with up to 10 retries. Rate-limit backoff capped at 60s; overload backoff capped at 90s
- **Auto-Save & Graceful Shutdown** — `_periodic_save()` runs every 5 seconds and triggers auto-save when new messages are detected, but only if the user has typed a name in the Save Chat entry (blank = no save). `_on_close()` stops the agentic loop, waits for streaming to finish via `_finish_close()` polling, saves state and chat (if named), cleans up browser connections, then destroys the window

---

## Account_Activity_WBC.py — Bank Transaction Extractor

A standalone browser automation utility that extracts transaction history from the Westpac (WBC) online banking account activity page. It connects to Microsoft Edge via CDP, clicks the "Display more" button repeatedly to load all transactions, then scrapes the transaction table and exports it as both raw HTML and a structured CSV file.

### How It Works

1. **Open Edge** — Launch Edge with remote debugging enabled: `& "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222`
2. **Navigate to the account activity page** in Edge and log in
3. **Run the app** — Launch `Account_Activity_WBC.py`. It connects to Edge via CDP on port 9222
4. **Configure** — Set the button text to match (default: "Display more"), number of clicks, and delay between clicks
5. **Press Start** — The app finds the button across all open tabs, clicks it the specified number of times, then extracts the transaction data

### Features

- **Auto-tab detection** — Searches all open Edge tabs for one containing the target button text, so you don't need to have the correct tab focused
- **Configurable parameters** — Button text, click count, and inter-click delay are all adjustable in the UI
- **Responsive cancellation** — The Stop button halts the click loop within 200ms by breaking the delay into small chunks
- **DOM stabilisation** — After all clicks, waits for the transaction row count to stabilise (up to 30 seconds) before extracting, ensuring all dynamically loaded rows are captured
- **Chunked HTML extraction** — Reads the transaction `<tbody>` in 50-row chunks via JavaScript to avoid Playwright's string truncation limits on large DOMs
- **Dual output** — Saves raw HTML to `Account_Activity_WBC.txt` and a parsed CSV to `Account_Activity_WBC.csv`
- **CSV format** — Five columns: Date, Description, Debit, Credit, Balance — parsed from WBC's Knockout.js-bound HTML using regex

### Output Files

| File | Description |
|---|---|
| `Account_Activity_WBC.txt` | Raw `<tbody>` HTML from the transaction table |
| `Account_Activity_WBC.csv` | Parsed transactions: Date, Description, Debit, Credit, Balance |

Both files are written to the project directory and are gitignored (they contain personal banking data).

### UI

A compact tkinter window with:

| Control | Description |
|---|---|
| **Button text** | The text of the "load more" button to click (default: "Display more") |
| **Clicks** | Number of times to click the button (default: 5) |
| **Delay (sec)** | Seconds to wait between clicks (default: 3) |
| **Start / Stop** | Begin or cancel the click-and-extract process |
| **Status log** | Color-coded log area: green for success, red for errors, grey for info |

### Prerequisites

- Microsoft Edge must be running with `--remote-debugging-port=9222`
- The Westpac account activity page must be open and logged in
- Python packages: `playwright` (connects via CDP — no `playwright install` needed)

### Running

```bash
# Activate the virtual environment
source .venv/Scripts/activate

# Run the application
python Account_Activity_WBC.py
```
