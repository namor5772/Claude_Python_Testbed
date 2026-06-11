# Claude Python Testbed

### CLONE IT INTO A VS CODE LOCAL REPO ##
A repo containing various Python scripts written using Claude Code. The two main applications are a full-featured Claude chatbot with dual-instance self-chatting (SelfBot.py) and a modular autonomous task agent that loops until a job is done (MyAgent.py + myagent/ package). There is also a standalone browser automation utility for extracting bank transaction data (Account_Activity_WBC.py).

## Contents

- **SelfBot.py** — Claude chatbot GUI application (see details below)
- **MyAgent.py** — Entry point (~170 lines) for the modular autonomous AI agent GUI application supporting **Anthropic, OpenAI, Gemini, and Ollama (local inference)** providers (see details below)
- **myagent/** — Package containing MyAgent's 19 mixin modules, constants, and helpers (see Architecture section below for full breakdown)
- **Account_Activity_WBC.py** — Browser automation utility for extracting Westpac bank transaction data (see details below)
- **CSVEditor.py** — Lightweight CSV editor GUI application (see details below)
- **[WHATIS_AI.md](WHATIS_AI.md)** — An essay exploring why AI tool use works so well, told through the story of a man trapped in a cell with only a terminal — a metaphor for how LLMs parse API messages and use tools to interact with the outside world
- **requirements.txt** — Python dependencies for pip install
- **MyAgent_Pricing.txt** — Reference document listing all API model pricing used by MyAgent's cost tracking feature (Anthropic, OpenAI, Gemini — Ollama inference is free and emits no cost line)
- **APICostLog.txt** — Append-only log of per-run API costs, written to the repo root after every MyAgent run (one `{timestamp};{provider};{model};{cost}` line per run, GUI and headless alike). **Gitignored** — per-machine runtime output (see the **API Cost Tracking** section under MyAgent)
- **Heartbeat.py** — Zero-token email-triggered agent dispatcher: a plain Python script (no LLM involved) that launchd runs every 10 minutes. An unread email with Subject "AGENT PROMPT" in the configured Gmail account rewrites a named instruction's prompt (below its `*****` marker) and spawns it headless via `MyAgent.py -l`. Replaces an equivalent AI-run polling instruction that cost ~$14/day in API tokens. See **Scheduling Background Runs** under MyAgent
- **UnreadSummary.py** — Zero-token unread-email summarizer: a plain Python script (no LLM involved) that scans every configured Gmail / Proton Bridge / IMAP / Outlook account for unread mail in Inbox and Spam/Junk, extracts and notes bill/receipt details from known senders and saves their PDF attachments to `~/Downloads` (mark-read / move-to-Trash actions exist behind flags, off by default — matched bills stay unread in place), and emails the numbered COMPREHENSIVE LIST from the Outlook account. Deterministic replacement for the AI-run `Email_AllUnreadSummary_Mac3` instruction (~$0.57/day on Haiku). See **Scheduling Background Runs** under MyAgent
- **Qwen25VL-tools.Modelfile**, **Llama32Vision-tools.Modelfile**, **Gemma3-tools.Modelfile** — Custom Ollama Modelfiles that graft Qwen3's tool-calling template onto three vision models, unlocking structured `tool_calls` that Ollama's default Modelfiles don't expose. See the **Ollama (Local Inference)** section for build instructions and the rationale
- **CLAUDE.md** — Top-level project instructions and conventions for Claude Code sessions. Imports the three per-app sub-files below via `@CLAUDE_SELFBOT.md` / `@CLAUDE_MYAGENT.md` / `@CLAUDE_ACCOUNT.md` so they load automatically without bloating the root file
- **CLAUDE_SELFBOT.md** — Architecture notes for SelfBot.py (threading model, dual geometry, skills, DPI handling, auto-save)
- **CLAUDE_MYAGENT.md** — Architecture notes for MyAgent.py and the `myagent/` package (mixin design, multi-provider message translation, MCP/Gmail/Proton integration, click-accuracy pipeline)
- **CLAUDE_ACCOUNT.md** — Architecture notes for Account_Activity_WBC.py (CDP connection, DOM stabilisation, CSV conversion)
- **system_prompts.json** — Saved system prompts for SelfBot (created at runtime)
- **agent_instructions.json** — Saved agent instructions for MyAgent, with embedded images. **Tracked in git** so the instruction library syncs across machines via push/pull (rather than each clone keeping its own divergent set)
- **mcp_servers.json** — Per-user MCP (Model Context Protocol) server configuration for MyAgent — JSON-RPC stdio servers (e.g. `@modelcontextprotocol/server-filesystem`) that expose external tool catalogs. Created manually, gitignored (may contain commands or env-stored secrets). See the **MCP Integration** section under MyAgent for setup
- **mcp_servers.example.json** — Tracked template for `mcp_servers.json`. On a new machine, copy this to `mcp_servers.json` and edit the placeholder filesystem path for your project. Never put real secrets (API tokens, OAuth client secrets) in either file — credentials always live in per-server config dirs outside the repo
- **saved_chats/** — Directory of saved chat conversations, one `.json` file per chat (created at runtime). A matching `.txt` export of the output window is always saved alongside each `.json` file. **Gitignored** — chats are local-only and never committed
- **app_state.json** — Persistent app settings for SelfBot instance 1 (created at runtime)
- **app_state_2.json** — Persistent settings for SelfBot instance 2 (created at runtime)
- **agent_state.json** — Persistent app settings for MyAgent instance 1 (created at runtime)
- **agent_state_N.json** — Persistent settings for MyAgent instance N (created at runtime when multiple instances run)
- **csv_editor_state.json** — Persistent settings for CSVEditor (created at runtime)
- **skills.json** — Saved skills with content and mode, shared by both apps (created at runtime)
- **selfbot.lock** — Lock file for SelfBot cleanup tracking (created/deleted at runtime)
- **selfbot_auto_msg.json** — Shared file for SelfBot cross-instance message injection (created/deleted at runtime)
- **LaunchSelfBot.bat** — One-click launcher that starts both SelfBot instances side by side (Windows)
- **LaunchMyAgent.bat** — One-click launcher for MyAgent (Windows)
- **myagent.ico** — Windows desktop-shortcut icon for MyAgent (multi-resolution: 16/24/32/48/64/128/256 px). Robot face on a deep-blue rounded square with cyan eyes and an amber antenna dot — readable at every Windows icon size. Generate or regenerate via `python make_icon.py`
- **make_icon.py** — Standalone PIL-based icon generator. Renders a supersampled 1024-px source, Lanczos-downsamples to 256, and saves a multi-size `.ico` plus a `MyAgent_preview.png`. Tweak the colour constants at the top to recolour without redrawing
- **My Agent.app** — macOS desktop shortcut for MyAgent (each click launches a new instance; blue/yellow icon)
- **My Agent.command** — Double-click launcher for MyAgent (macOS, opens Terminal). Exports Homebrew bin paths to `PATH` so MCP servers spawned via `npx` are reachable from GUI launches
- **LaunchMyAgent.sh** — Shell launcher for MyAgent (macOS)
- **selfbot_position.ps1** — PowerShell helper used by the SelfBot launcher to position and focus windows (Windows)

## Slash Commands (Claude Code Skills)

Project-scoped skills live in `.claude/skills/` and ship with the repo — clone this project on any machine and the slash commands below are immediately available inside Claude Code sessions opened from the project root. No per-machine setup.

| Command | What it does |
|---|---|
| `/sync-check` | Verifies the current local branch matches `origin/<branch>`. Always does a fresh `git fetch` (never stale cache), shows both tip hashes, reports ahead/behind/diverged counts, flags uncommitted working-tree changes. 5-second status check with no file exploration. |
| `/commit-push` | Stages modified tracked files, drafts a one-line subject + short body from `git diff --stat` matching the repo's commit style (`git log -5 --oneline`), commits with the standard `Co-Authored-By` trailer, and pushes to the current branch's origin. Explicitly skips `.DS_Store`, scratch experiments, and GUI-auto-modified state files (`agent_instructions.json`, `skills.json`) unless you say otherwise. Never force-pushes, never amends, never runs tests. |
| `/urp` | "Update README, commit, push" — rereads recent git history + diffs, updates `README.md` (and `CLAUDE.md` if needed) to reflect the current code, then commits and pushes. Useful after a feature lands to keep docs in sync. |
| `/launch-agent` | Kills any running Python processes (Windows `pythonw.exe`/`python.exe`, macOS `python`), then launches MyAgent.py in the background. |
| `/launch-selfbot` | Kills any running Python processes, then launches SelfBot.py in the background. |
| `/run script.py` | Activates the `.venv` and runs a Python script (takes the filename as an argument). |

All skills set `disable-model-invocation: true`, so Claude only invokes them when you explicitly type the slash command — they won't auto-fire based on context guesses.

Skills are defined as `SKILL.md` files with YAML frontmatter + markdown body; they load dynamically on next invocation (no Claude Code restart needed).

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
| **Adaptive** (Opus 4.6+, Sonnet 4.6) | `thinking: {type: "adaptive"}` | Effort level: low, medium, high (default), max (Opus only) |
| **Manual** (Opus 4.5, Sonnet 4.5, Haiku 4.5) | `thinking: {type: "enabled", budget_tokens: N}` | Token budget: 1K, 4K, 8K (default), 16K, 32K |

- When thinking is enabled, the **temperature controls are greyed out** (the API does not allow temperature with thinking)
- `max_tokens` is automatically raised from 8,192 to 32,768 when thinking is active. Models with lower output token limits (Claude 3 Haiku/Opus/Sonnet at 4,096) are automatically capped via the `MODEL_MAX_OUTPUT_TOKENS` lookup
- The strength combobox automatically switches between effort levels and budget presets when you change models
- Switching to a model that doesn't support thinking disables the checkbox and re-enables temperature
- Thinking settings (`thinking_enabled`, `thinking_effort`, `thinking_budget`) are persisted in `app_state.json` and saved/restored with each chat
- Thinking and redacted_thinking blocks are preserved during tool-use loops (required by the API for reasoning continuity) but stripped when serializing chats for persistence unless the **Save Thinking** checkbox is enabled (see below)

#### Chat Interface
- **Streaming responses** — Claude's replies are streamed token-by-token into the chat display for a real-time feel
- **Multi-turn conversation** — Full conversation history is maintained and sent with each request
- **Color-coded messages** — User messages appear in blue, assistant responses in green, errors in red, and tool activity in grey italics
- **Multi-line input** — The input field supports multiple lines; press **Enter** to send, **Shift+Enter** for a newline

#### Tool Use
The chatbot has twenty-nine tools (including 2 server-side and a dynamic `get_skill` tool) that Claude can invoke autonomously during a conversation, organised into four categories:

**Core Tools (always available):**
- **run_command** — Executes a shell command on the local machine and returns the output (stdout + stderr). On Windows this runs PowerShell; on macOS it runs bash. Commands have a 30-second timeout and output is truncated at 20,000 characters. On Windows, uses `CREATE_NO_WINDOW` to suppress console window flashes. The tool description instructs Claude to use `Start-Process` (Windows) or `open -a` (macOS) when launching GUI applications to avoid blocking the tool loop
- **csv_search** — Searches a delimited text file (CSV, TSV, TXT, or any delimited format) for records matching a value. The file must have a header row. Supports searching a specific column or all columns, with three match modes: `contains` (default), `exact`, and `starts_with` — all case-insensitive. The delimiter is auto-detected from file content using `csv.Sniffer` (sampling the first 8KB), or can be explicitly specified (`,`, `\t`, `|`, `;`). Results are returned as labelled key-value rows, capped at 50 matches by default (configurable via `max_results`). Output is truncated at 20,000 characters
- **read_document** *(MyAgent only)* — Extracts text from local files: PDF (via `pypdf`, with page-range support and metadata), DOCX (via `python-docx`, paragraphs + tables + core metadata), HTML (using the same `HTMLTextExtractor` as the mail tools), and plain-text formats (`.txt`/`.md`/`.json`/`.yaml`/`.csv`/`.log` + source code). Provider-agnostic — pairs naturally with `gmail_get_attachment` / `proton_get_attachment` / `fetch_webpage` / any path-producing tool. Returns JSON with `text` (truncated at 50,000 chars), `format`, `size_bytes`, `mime_type`, plus format-specific extras (`page_count`/`pages_extracted`/`metadata` for PDF; `paragraph_count`/`table_count`/`metadata` for DOCX). Encrypted PDFs detected and reported clearly. For formats not natively supported (XLSX/ZIP/RTF/audio/video), the tool description directs the agent to `run_command` with the right CLI tool

**Server-Side Tools (always available, Anthropic-native):**
- **web_search** (`web_search_20250305`) — Anthropic's native server-side web search. Replaces the previous local DuckDuckGo-based search. The API handles query execution, result extraction, and citation generation entirely server-side. No local schema sent — minimal token cost
- **code_execution** (`code_execution_20250825`) — Anthropic's native code execution sandbox. Allows Claude to write and run Python code server-side, producing text output and images (charts, plots, etc.). Code execution stdout is displayed in the chat; file outputs (images) are downloaded via `client.beta.files.download()`, saved to `saved_chats/ci_output_{timestamp}.png`, and displayed inline in the chat widget (scaled to max 600px). Uses `client.beta.messages.stream()` with beta flags `web-search-2025-03-05`, `code-execution-2025-08-25`, and `files-api-2025-04-14`

**Desktop Tools (enabled via Desktop checkbox):**
- **screenshot** — Captures individual displays or all displays as separate images. Supports a `display` parameter (0=primary, 1=secondary, etc.) and region capture (x, y, width, height) for pixel-accurate zooming on small targets. On macOS, uses Quartz `CGWindowListCreateImage` for per-display capture; on Windows, uses `ImageGrab.grab(all_screens=True)` via `EnumDisplayMonitors`. Images are resized to Anthropic API limits (1568px long edge, 1.15MP) and coordinates are automatically mapped back to screen space via `_screenshot_scale` and `_screenshot_offset`
- **mouse_click** — Clicks at the given image coordinates with configurable button (left/right/middle) and click count (single/double). Coordinates from the screenshot are automatically scaled to screen coordinates with bounds checking and clamping (out-of-bounds coordinates are pinned to the nearest edge pixel and a `⚠ clamped` warning is returned so Claude can self-correct on the next turn). Output shows image coords, screen coords, scale, offset, and image dimensions for diagnostics
- **type_text** — Types text at the current cursor position. Uses `pyautogui.write()` for ASCII and clipboard paste via `pyperclip` for Unicode characters
- **press_key** — Presses a key or key combination (e.g., `enter`, `ctrl+c`, `alt+tab`). Supports common aliases like `windows` → `win`
- **mouse_scroll** — Scrolls the mouse wheel up or down, optionally at a specific screen position
- **open_application** — Opens an application by common name (e.g., `chrome`, `notepad++`, `vscode`) using a built-in lookup table, or by full executable path. Accepts an optional `args` parameter to pass arguments (e.g., a file path to open in the application). Uses `subprocess.Popen` with `CREATE_NO_WINDOW` so it returns immediately without blocking the tool loop or flashing a console window
- **find_window** — Finds windows matching a title pattern using `pygetwindow`, returning titles, positions, and sizes. Can optionally activate (bring to foreground) the first match
- **clipboard_read** — Reads the current text contents of the Windows clipboard via tkinter's `clipboard_get()`. Returns an error message if the clipboard is empty or contains non-text data
- **clipboard_write** — Writes text to the Windows clipboard via tkinter's `clipboard_clear()` and `clipboard_append()`, replacing any current content
- **wait_for_window** — Polls `pygetwindow.getWindowsWithTitle()` every 0.5 seconds until a window matching the given title appears, or times out (default 10 seconds). Returns the window's title, position, and size once found
- **read_screen_text** — Captures a screen region and performs OCR using `winocr` on Windows (native `Windows.Media.Ocr`) or Vision framework on macOS (`VNRecognizeTextRequest`). Coordinates are scaled by `_screenshot_scale` and offset by `_screenshot_offset` to map image coordinates to screen space. No Tesseract installation needed
- **find_image_on_screen** — Locates a reference image file on the screen using `pyautogui.locateOnScreen()` with confidence-based matching (requires `opencv-python`). Returns both screen coordinates and scaled image coordinates for clicking
- **mouse_drag** — Drags the mouse from one point to another using `pyautogui.moveTo()`, `mouseDown()`, `moveTo()`, `mouseUp()`. Coordinates are scaled by `_screenshot_scale`. Useful for drag-and-drop, resizing, sliders, and drawing

**Browser Tools (enabled via Browser checkbox):**
- **browser_open** — Connects to Google Chrome or Microsoft Edge via Chrome DevTools Protocol (CDP) and navigates to a URL. Launches the browser automatically with a separate debug profile if it isn't running
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
- **NIP Generation** — A skill for producing FSANZ-compliant Australian Nutrition Information Panels in structured JSON format, using web search to source official product data with AFCD/NUTTAB fallback. After generating the panel the skill closes any open Notepad++ instance, writes the JSON to a relevantly-named `.txt` file under `c:\Temp\`, and reopens it in Notepad++ for review
- **Email Attachment Processing** — Reference workflow for handling email attachments. Locate the attachment via `*_read`, download via `*_get_attachment` (Gmail or Proton), extract content via `read_document` (PDF/DOCX/HTML/plain text), and fall back to `run_command` with CLI tools for unsupported formats (XLSX via `openpyxl`, ZIP via `unzip`, RTF/EPUB via `pandoc`, audio metadata via `ffprobe`, scanned PDFs via `tesseract`)
- **Reliable YouTube Music Playback** — Browser-based playback workflow that prefers Playwright + the HTML5 `<video>` element's JavaScript API (via `browser_run_js`) over brittle desktop screenshots. Handles consent banners, autoplay-policy fallback, mid-roll ad skipping (`.ytp-ad-skip-button`), accurate end-of-song detection via `video.ended`, and stalled-playback recovery
- **Schedule Agent Win** — Wraps Windows Task Scheduler (`Register-ScheduledTask`) to schedule recurring runs of any MyAgent instruction in headless mode. Lists instructions from `agent_instructions.json`, prompts for frequency (daily/weekly/monthly) and time, generates the PowerShell job using `pythonw.exe` (no console window), and offers list/delete/test-run management of all `MyAgent_*` scheduled tasks
- **Schedule Agent MacOS** — macOS counterpart to **Schedule Agent Win**. Generates `launchd` LaunchAgent plists in `~/Library/LaunchAgents/com.myagent.<slug>.plist` with `StartCalendarInterval` (daily / weekly with `Weekday` / monthly with `Day`), embeds API keys in `EnvironmentVariables` so launchd-fired runs authenticate without per-session setup, chmod 600s the plist to restrict read to owner, and uses `launchctl load/unload/start` for lifecycle. Documents both legacy (`load`/`unload`/`start`) and modern (`bootstrap`/`bootout`/`kickstart`) launchctl command sets, the four launchd directory scopes (system vs user, daemons vs agents), and the stripped-env caveat that catches first-time users — verified end-to-end by the **Test Schedule MacOS** + **ScheduleTest_Target** instruction pair (see Agent Instructions section)

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

**Cross-platform multi-display support** — On macOS, `_macos_display_screenshot()` uses Quartz `CGWindowListCreateImage` to capture individual displays; on Windows, `_get_windows_display_rects()` uses `EnumDisplayMonitors` and `ImageGrab.grab(bbox=..., all_screens=True)` for per-display capture. The `_get_display_rects()` unified wrapper works on both platforms. Displays are indexed with the primary monitor (origin 0,0) as display 0. The screenshot tool description dynamically lists all available displays and their resolutions.

**DPI-aware coordinate mapping** — On Windows, `SetProcessDpiAwareness(2)` (Per-Monitor DPI Aware) is set at startup — all coordinates are physical, no DPI alignment needed. On macOS, Quartz captures at physical pixel resolution and the image is resized to the display's logical dimensions (from `_get_display_rects()`). Screenshots are then resized to Anthropic API limits (1568px/1.15MP), and the resize ratio (`_screenshot_scale`) plus display origin (`_screenshot_offset`) are stored. All coordinate tools compute `round(int(img_coord) * scale) + offset`, with bounds checking and clamping against `_screenshot_dims`. Out-of-bounds coordinates are clamped to the nearest edge pixel with a `⚠ clamped` warning in the tool result, giving Claude explicit feedback to self-correct on the next turn. Region screenshots convert image coordinates to screen coordinates and update the offset for subsequent clicks.

`pyautogui.FAILSAFE` is enabled — moving the mouse to the top-left corner `(0, 0)` immediately aborts any automation in progress. A 0.1-second pause between actions provides a safety buffer.

#### Browser Automation

The eleven browser tools are gated behind a **Browser** checkbox, independent of the Desktop toggle. When disabled (the default), any attempt by Claude to use browser tools returns an error message. Browser tool schemas are only sent to the API when the checkbox is enabled, saving tokens and preventing Claude from attempting to use unavailable tools.

**How it works** — Playwright connects to Google Chrome or Microsoft Edge via the Chrome DevTools Protocol (CDP) on port 9222. When no browser with a debug port is running, the app launches one automatically using a separate `--user-data-dir` temp profile so it doesn't conflict with the user's existing browser sessions.

**Browser connection scenarios:**

| Scenario | What happens |
|---|---|
| No browser with debug port | App launches Chrome/Edge with `--remote-debugging-port=9222` and a separate temp profile |
| Browser running WITH debug port | App connects directly |
| Browser running WITHOUT debug port | Error message: close the browser and retry |
| Connection drops mid-session | Auto-detected and reconnected on next tool call |

**Lifecycle details:**
- `_ensure_browser()` handles the full connection lifecycle: probes port 9222, launches Chrome or Edge if needed (checking common install paths on both Windows and macOS), uses `--user-data-dir` with a temp directory to avoid conflicts with existing browser sessions, waits up to 15 seconds for the debug port, connects Playwright via CDP, and reuses the first open tab as the active page
- If the connection dies between tool calls (e.g., browser was closed), the next tool call auto-reconnects
- `browser_close` only disconnects Playwright — the browser stays open with all tabs intact
- Closing the app window automatically cleans up the Playwright connection via `WM_DELETE_WINDOW`

**No `playwright install` needed** — Since the app connects to the system-installed Chrome or Edge via CDP, it does not use Playwright's bundled browser binaries. Only the `playwright` Python package is required.

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

> **Note (MyAgent only):** The **Safety** button opens a dialog where individual Tier 2 patterns can be unchecked to bypass their confirmation dialog. Bypassed patterns still display a `⚠ Confirm bypassed (pattern: ...)` warning in the output window (always visible, regardless of the Activity checkbox). Disabled patterns are persisted per-instruction in `agent_instructions.json`. See the MyAgent section below for details.

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
- The full message history (serialised to JSON, with base64 image data stripped and replaced with `[Image was attached]` placeholders to keep file sizes small; thinking blocks are stripped during serialisation unless the **Save Thinking** checkbox is enabled)
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

#### Save Thinking
- Toggle the **Save Thinking** checkbox to include thinking and redacted_thinking blocks in saved chat JSON files
- When enabled, Anthropic thinking blocks (including signatures) are preserved in the saved chat, allowing loaded chats to continue with full reasoning context intact
- When disabled (the default), thinking blocks are stripped during serialisation to keep saved chat files smaller
- **OpenAI note:** OpenAI reasoning models only expose reasoning *summaries* (not the full internal reasoning), and these summaries are never sent back to the API on continuation. For OpenAI models, reasoning summaries are display-only — visible in the output window (and captured in the `.txt` export if Show Thinking is checked) but not stored in the messages. The Save Thinking toggle has no effect for OpenAI models

### Dual-Instance Self-Chatting

When a second instance is launched, SelfBot automatically enables dual-instance mode where two Claude instances converse autonomously.

#### How It Works

1. **Launch instance 1** — Run `python SelfBot.py`. It acquires a Windows named mutex and operates as the primary instance. When running solo, there is no send delay and auto-chat is disabled — it behaves like a normal chatbot
2. **Launch instance 2** — Run `python SelfBot.py` again. The mutex detects instance 1 is already running and configures this as the secondary instance
3. **Peer detection** — Instance 1 polls every 2 seconds for a peer SelfBot window. When instance 2 appears, auto-chat and the configurable send delay are automatically enabled; when instance 2 closes, they are disabled again
4. **Send a message in instance 1** — After the first response completes, the user's original message is injected into instance 2's output window (in assistant/green colour), and the reply body is written to a shared file for instance 2 to pick up
5. **Auto-conversation loop** — Each time either instance receives a reply, the response body is written to a shared JSON file (`selfbot_auto_msg.json`). The other instance polls for this file, reads the text into its own input field, and sends it internally — creating a continuous back-and-forth dialogue without any window switching or focus changes

#### Instance Detection

**Windows:** Uses a named mutex (`CreateMutexW`). The OS automatically releases the mutex when a process exits — even on crash or `taskkill` — so stale state is impossible. A `selfbot.lock` file is still created containing instance 1's PID, used by the launcher (`selfbot_position.ps1`) to identify which window is instance 1 for correct positioning.

- If the mutex is not held → this is instance 1; the mutex is acquired and the lock file is created
- If the mutex is already held → this is instance 2

**macOS:** Uses a lock file (`selfbot.lock`) containing the PID. On startup, the lock file is read and the PID is verified via `os.kill(pid, 0)` + `ps -p` to confirm it belongs to a running SelfBot process. Stale locks from crashed processes are automatically reclaimed.

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

Closing either SelfBot window stops the auto-chat conversation, waits for any in-flight API streaming to finish, auto-saves both instances' chats (`.json` + `.txt`), and then shuts down both instances cleanly via `WM_CLOSE` messages (Windows only; on macOS each instance closes independently). Instance 2's files are suffixed with `_` to avoid collisions. A periodic auto-save every 5 seconds on all instances also protects against force-kill (`taskkill /F`, `Stop-Process`) data loss.

#### Message Display Formatting

Both user and assistant messages display their content on the line below the label (e.g., "You:" on one line, message text on the next). This consistent below-label formatting improves readability during autonomous conversations.

#### Default Checkbox States

All checkboxes (Debug, Tool Calls, Activity, Show Thinking, Save Thinking, Desktop, Browser) default to **off** on startup.

### Requirements

- **Windows 10/11** or **macOS** (both fully supported from the same codebase)
- Python 3.10+ with tkinter (on macOS, install via `brew install python-tk@3.13` — the system Python's Tk is too old)
- At least one of: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY` environment variables, **OR** a running local Ollama server (MyAgent supports all four providers; SelfBot requires Anthropic). Ollama needs no API key — availability is probed by checking if `http://localhost:11434/api/tags` responds within 500 ms at app startup

#### Python Dependencies

**Core (in `requirements.txt`):**
```
anthropic
openai
google-genai
ollama        # MyAgent only (Ollama provider for local inference)
ddgs          # MyAgent only (Gemini/Ollama providers use local DuckDuckGo search)
httpx         # MyAgent only (Gemini provider uses httpx for fetch_webpage)
pyautogui
pygetwindow
Pillow
```

**Optional (installed separately when needed):**
```
playwright      # Browser tools — connects to Edge/Chrome via CDP, no `playwright install` needed
pyperclip       # Desktop tools — Unicode text input via clipboard paste
winocr          # Desktop tools — OCR via Windows.Media.Ocr (read_screen_text, Windows only)
opencv-python   # Desktop tools — image matching (find_image_on_screen)
mcp             # MCP (Model Context Protocol) client — required only if you want to connect external MCP servers (filesystem, GitHub, Slack, etc.) via mcp_servers.json. Pulls in starlette/uvicorn/jsonschema and ~14 transitive deps. See MyAgent's MCP Integration section
pywin32         # Windows-only — required by mcp for Job Object subprocess cleanup. Install if MCP server cleanup behaves oddly on Windows
```

> **Note:** `playwright install` is **not** required. The app connects to the system-installed Microsoft Edge (or Google Chrome on macOS) via CDP, so no bundled browser binaries are needed.

#### Cross-Platform Notes

Both SelfBot.py and MyAgent.py (via `myagent/constants.py`) use a runtime `IS_WINDOWS = sys.platform == "win32"` constant to branch between Windows and macOS code paths. All Windows behaviour is preserved exactly — macOS gets equivalent or gracefully degraded functionality:

| Feature | Windows | macOS |
|---|---|---|
| Shell tool | `run_powershell` (PowerShell) | `run_shell` (bash) |
| Desktop automation | Full (pyautogui + pygetwindow) | pyautogui works; pygetwindow may not — Desktop checkbox auto-disables if unavailable |
| Browser automation | Edge via CDP | Edge or Chrome via CDP |
| Instance detection (SelfBot) | Named mutex (`CreateMutexW`) | Lock file + PID verification |
| Duo peer detection (SelfBot) | `pygetwindow` window enumeration | Not available (each instance runs independently) |
| Monitor geometry (MyAgent) | Win32 `EnumDisplayMonitors` | CoreGraphics `CGGetActiveDisplayList` |
| DPI awareness | SelfBot: `SetProcessDpiAwareness(2)` (v1). MyAgent: `SetProcessDpiAwarenessContext(-4)` (v2 PER_MONITOR_AWARE_V2) — fixes broken multi-monitor behaviour at mixed DPIs | Not needed (macOS handles scaling natively) |
| Dialog multi-monitor | `transient(parent)` (works across screens) | `transient()` skipped (macOS restricts transient dialogs to parent's screen) |
| Monospace font | Consolas | Menlo |

### Setup (New Machine)

The project is fully portable — no hardcoded paths.

**Windows:**
```bash
# Clone the repository
git clone https://github.com/namor5772/Claude_Python_Testbed.git
cd Claude_Python_Testbed

# Create and activate the virtual environment
python -m venv .venv
source .venv/Scripts/activate   # Git Bash
# or: .venv\Scripts\activate    # CMD / PowerShell

# Install dependencies
pip install -r requirements.txt

# Set your API key(s) (or add to your environment permanently)
export ANTHROPIC_API_KEY="your-key-here"
export OPENAI_API_KEY="your-key-here"      # optional, for MyAgent OpenAI support
export GEMINI_API_KEY="your-key-here"      # optional, for MyAgent Gemini support
# Ollama local inference is auto-detected at localhost:11434 — no key required
# (override the server URL via OLLAMA_BASE_URL if you run Ollama remotely)

# Optional: external MCP server support (filesystem, GitHub, Slack, etc.)
pip install mcp pywin32                    # pywin32 needed on Windows for clean subprocess cleanup
# Then create mcp_servers.json at the project root with your server configs
# (gitignored — see MyAgent's "MCP Integration" section for the format)
```

**macOS:**
```bash
# Install Python 3.13 with tkinter support
brew install python-tk@3.13

# Clone the repository
git clone https://github.com/namor5772/Claude_Python_Testbed.git
cd Claude_Python_Testbed

# Create and activate the virtual environment
python3.13 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Set your API key(s) permanently
echo 'export ANTHROPIC_API_KEY="your-key-here"' >> ~/.zshrc
echo 'export OPENAI_API_KEY="your-key-here"' >> ~/.zshrc      # optional
echo 'export GEMINI_API_KEY="your-key-here"' >> ~/.zshrc      # optional
# Tip: paste API keys via `echo 'export ... >> ~/.zshrc'` (not by editing ~/.zshrc in a
# terminal-embedded editor) to avoid bracketed-paste-mode escape sequences leaking
# into the file. A corrupted key shows up as `\x1b[200~sk-proj-...~` and causes
# HTTP 400 errors on every API call.

# Optional: external MCP server support
pip install mcp                                                # adds filesystem / GitHub / Slack etc. via mcp_servers.json

# Ollama is auto-detected at localhost:11434 — no key needed.
# Optional tuning env vars for Ollama (see Ollama section below):
# echo 'export OLLAMA_BASE_URL="http://localhost:11434"' >> ~/.zshrc
# echo 'export OLLAMA_NUM_CTX_CAP="32768"' >> ~/.zshrc    # KV cache ceiling
# echo 'export OLLAMA_KEEP_ALIVE="24h"' >> ~/.zshrc       # keep models hot
source ~/.zshrc
```

The `.venv` directory is gitignored and must be recreated on each machine. All runtime files (`app_state.json`, `skills.json`, `saved_chats/`, etc.) are created automatically on first run.

### Running

**Solo mode:**
```bash
# Activate the virtual environment
source .venv/Scripts/activate   # Windows (Git Bash)
source .venv/bin/activate       # macOS

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

- **UI Layout** — Grid-based layout with 7 rows: model + temperature + thinking toolbar with DELETE/NEW CHAT buttons (row 0), chat save/load toolbar with SAVE button (row 1), chat display + scrollbar (row 2), input field (row 3), button bar with Attach Images, System Prompt, and Skills buttons (row 4), checkbox row with Debug/Tool Calls/Activity/Show Thinking/Save Thinking/Desktop/Browser toggles (row 5), and attachment indicator (row 6)
- **Threading** — API calls run in a background daemon thread (`stream_worker`) to keep the UI responsive. A `queue.Queue` passes events (text deltas, thinking deltas, labels, tool info, errors) back to the main thread. When thinking is enabled, the stream worker uses raw event iteration (`content_block_start`, `content_block_delta`, `content_block_stop`) instead of `text_stream` to handle both thinking and text blocks
- **Queue Polling** — The main thread polls the queue every 50ms via `root.after()` and updates the chat display accordingly. An `_ensure_newline()` helper guarantees each new output block (labels, tool info, thinking, warnings, errors) starts on a fresh line regardless of whether the previous block ended with a newline. An `ensure_newline` queue event is also emitted between agentic loop iterations so that consecutive response streams don't merge on the same line when Activity display is off
- **Persistence** — JSON-based storage handles different concerns: `system_prompts.json` for the prompt library, individual `.json` files in `saved_chats/` for conversation history (one file per chat), `app_state.json` for user preferences, and `skills.json` for the skills library
- **Skills System** — Skills are loaded from `skills.json` on startup. `_build_system_prompt()` assembles the final system prompt by appending enabled skill content and listing on-demand skill names. `_get_tools()` dynamically adds a `get_skill` tool when on-demand skills exist, with the skill names constrained via an `enum` in the input schema
- **Serialisation** — The `_serialize_messages()` method converts Anthropic SDK Pydantic objects (e.g., `ToolUseBlock`, `TextBlock`) to plain dicts via `model_dump()`, strips base64 image data, skips `thinking` and `redacted_thinking` blocks, and sanitises content blocks through `_clean_content_block()` to remove extra SDK fields (like `parsed_output`) that the API rejects on re-submission. `_clean_content_block()` preserves thinking/redacted_thinking blocks with their signatures for tool-use loop continuity
- **HTML Extraction** — The `HTMLTextExtractor` class (a `HTMLParser` subclass) strips HTML tags from fetched web pages, skipping `<script>`, `<style>`, and `<noscript>` blocks, and inserting newlines at block-level element boundaries
- **Command Safety** — Two-tier regex-based guardrail system (`COMMAND_BLOCKED` and `COMMAND_CONFIRM` pattern lists) checks commands before execution. Confirmation dialogs are dispatched to the main tkinter thread via `root.after()` while the worker thread waits on a `threading.Event`
- **Desktop Automation** — Thirteen tools (`do_screenshot`, `do_mouse_click`, `do_type_text`, `do_press_key`, `do_mouse_scroll`, `do_open_application`, `do_find_window`, `do_clipboard_read`, `do_clipboard_write`, `do_wait_for_window`, `do_read_screen_text`, `do_find_image_on_screen`, `do_mouse_drag`) built on `pyautogui`, `pygetwindow`, `winocr`, and `opencv-python`. Defined in a separate `DESKTOP_TOOLS` list and conditionally included via `_get_tools()` only when the `desktop_enabled` checkbox is enabled. The `screenshot` tool description is dynamically patched with the current screen resolution. Process-level DPI awareness (`SetProcessDpiAwareness(2)`) is set before window creation, and screenshot-to-screen coordinate scaling is handled automatically via `_screenshot_scale`
- **Browser Automation** — Eleven tools (`do_browser_open`, `do_browser_navigate`, `do_browser_click`, `do_browser_fill`, `do_browser_get_text`, `do_browser_run_js`, `do_browser_screenshot`, `do_browser_close`, `do_browser_wait_for`, `do_browser_select`, `do_browser_get_elements`) built on Playwright's CDP connection to a Chromium-family browser on port 9222. Gated behind a `browser_enabled` `BooleanVar` toggle; tool schemas are conditionally included via `_get_tools()` only when the checkbox is enabled. `_ensure_browser()` manages the full connection lifecycle: it probes port 9222 and, when nothing is listening, auto-launches the first installed browser it finds — on **macOS** the search order is **Brave Browser → Google Chrome → Microsoft Edge**; on **Windows**, **Chrome → Edge**. The launched browser gets a dedicated `--user-data-dir` profile that is **persistent on macOS** (`~/Library/Application Support/MyAgent/browser_profile`, so cookies/logins/history survive across runs) and an ephemeral temp dir on Windows, plus auto-reconnect on dead connections. A `WM_DELETE_WINDOW` protocol handler ensures clean Playwright disconnection on app close
- **Rate-Limit Retry** — Exponential backoff loop in `stream_worker` handles HTTP 429 (rate limit) and 529 (overload) errors with up to 5 retries before propagating the exception
- **Auto-Save & Graceful Shutdown** — `_auto_save_on_close()` silently saves the chat (`.json` + `.txt`) using the entry field name or an auto-generated name; instance 2's filenames are suffixed with `_` via `_save_name()` to avoid collisions. `_periodic_save()` runs every 5 seconds on all instances and triggers auto-save when new messages are detected. `_on_close()` stops auto-chat, waits for streaming to finish via `_finish_close()` polling, saves the current instance's chat, sends `WM_CLOSE` to peer windows, and cleans up lock files and browser connections. Re-entrancy is guarded by a `_closing` flag, and `_poll_auto_msg`/`_auto_msg_delayed_send`/`_poll_for_peer` all bail immediately when closing

---

## MyAgent.py — Autonomous AI Task Agent

A fire-and-forget autonomous task runner built with tkinter that supports **Anthropic** (Claude), **OpenAI** (GPT-4.1, GPT-5, o4-mini, etc.), **Gemini**, and **Ollama** (local inference) APIs. Unlike SelfBot (which is a conversational chatbot), MyAgent is designed for hands-off task execution: you configure an **Instruction** (a task description, optionally with images), select a **Provider** and **Model**, press **START**, and the AI autonomously loops — calling tools, interpreting results, calling more tools — until the task is complete. The user is a passive observer. The window title is **"My Agent"** (with provider/model info in the title bar).

**Modular architecture** — MyAgent uses a mixin-based modular design. The entry point `MyAgent.py` (~170 lines) contains only the `App` class shell and `__init__`, while all functionality is split across 19 mixin classes in the `myagent/` package. See the [Architecture](#architecture-1) section below for the full module breakdown.

**External tool integration** — In addition to its ~64 built-in tools (core, desktop, browser, meta, Gmail, Proton), MyAgent supports the **Model Context Protocol (MCP)** — connect to external MCP servers like filesystem, GitHub, Slack, or any of the ~100 community servers via a single `mcp_servers.json` config file. MCP tools flow through the same agent loop as native tools and work across all four providers. See the **MCP Integration** section under Features for full details.

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

### Scheduling Background Runs (Task Scheduler / launchd)

The **Schedule Agent Win** and **Schedule Agent MacOS** skills (see the Skills list above) wrap the OS scheduler to run any saved instruction unattended: on Windows via Task Scheduler (`pythonw.exe`), on macOS via a `launchd` LaunchAgent at `~/Library/LaunchAgents/com.myagent.<slug>.plist` that fires `python MyAgent.py -l "<Instruction>" --headless` on a `StartCalendarInterval`. (A working example is the daily unread-email-summary job, which searches several mail accounts headless and emails the digest.)

Those skills *create* the jobs; to *inspect, verify, and manage* them from the terminal on macOS:

```bash
# Every loaded launchd job (PID, last exit code, label)
launchctl list | grep myagent

# One job's runtime detail — ProgramArguments + log paths, but NOT the schedule
launchctl list com.myagent.<slug>

# The schedule lives in the plist, not launchctl — pretty-print it (binary plists too)
plutil -p ~/Library/LaunchAgents/com.myagent.<slug>.plist

# Find ALL time-scheduled jobs on the machine and what each one runs
for d in ~/Library/LaunchAgents /Library/LaunchAgents /Library/LaunchDaemons; do
  for f in "$d"/*.plist; do [ -e "$f" ] || continue
    plutil -p "$f" 2>/dev/null | grep -qE '"StartCalendarInterval"|"StartInterval"' \
      && { echo "── $f"; plutil -p "$f" | grep -E '"(Label|Hour|Minute|Weekday|Day)"|[0-9]+ => "'; }
  done
done
```

**Manage schedules conversationally — a "Schedule Manager" instruction.** Instead of hand-editing plists, save a dedicated instruction that enables the OS-appropriate skill (`Schedule Agent MacOS` or `Schedule Agent Win`) *in its own `skill_modes`*, turns every tool toggle off, and runs **conversationally** — leaving it just `run_command` + `user_prompt`. On launch it lists the installed `com.myagent.*` jobs read-only and asks what to do; you reply in plain English ("delete the 7am summary job", "move it to 08:30") and it generates, lints with `plutil -lint`, loads/unloads, and re-verifies — popping MyAgent's confirmation dialog for each `launchctl`/`rm` (keep the instruction's `disabled_confirm_patterns` **empty** so every destructive step asks first). Enabling the skill per-instruction leaves the global `skills.json` mode and every other instruction untouched. Run it in the **GUI only**, never `--headless`: it is conversational, so `user_prompt` would block forever with no window to answer it.

**Operational gotchas** (learned debugging a real job):
- **`launchctl` ≠ the schedule.** `launchctl list <label>` shows *what* a job runs and its exit status; the *when* (`StartCalendarInterval`) lives only in the plist — read it with `plutil -p`.
- **Missed runs fire on wake, not catch-up.** With `RunAtLoad=false`, if the Mac is asleep/off at the scheduled time launchd runs the job **once** at the next wake (a 07:00 job firing at 11:17 after the lid opens is expected) — it does not replay missed occurrences.
- **Don't log to `/tmp`.** macOS purges `/tmp` of files untouched for ~3 days, erasing early-crash diagnostics. Point `StandardOutPath`/`StandardErrorPath` at `~/Library/Logs/myagent/`, and use **absolute** paths — launchd does **not** expand `~` in plist strings (a literal `~` folder gets created instead).
- **Headless runs need confirm-bypass.** A scheduled instruction that calls a destructive tool (`proton_send`, `gmail_send`, `rm`, …) will hang forever on its Tk confirmation dialog with no GUI to click. Add those patterns to the instruction's **Safety** bypass list (stored per-instruction in `agent_instructions.json` as `disabled_confirm_patterns`) so unattended runs proceed.
- **Three layers prove a run worked:** `LastExitStatus` from `launchctl list <label>` (the process exited), the timestamped `saved_chats/<Instruction>_<ts>.txt` transcript (the agent did the work), and the real side effect (e.g. the email actually arrived). Exit 0 alone is **not** proof of delivery — the side effect can still fail silently.

**Email-triggered dispatch at zero token cost — `Heartbeat.py`.** Calendar scheduling covers *when*; `Heartbeat.py` covers *on demand, from anywhere*: email yourself a trigger and the machine launches the agent. It is a plain Python script — no LLM, no MyAgent window — that launchd runs every 10 minutes (`com.roman.myagent-heartbeat`, `StartInterval` 600, `RunAtLoad`). Each pass:

1. Checks the configured Gmail account (constants at the top of the script) for an **unread** Inbox email with the **exact** Subject `AGENT PROMPT` (Gmail's `subject:` search is word-based, so the header is re-verified literally).
2. Parses the body: line 1 = the name of a saved instruction; lines 3 onward = the new prompt core.
3. Rewrites that instruction's `text` in `agent_instructions.json`, replacing everything below its `*****` marker line (atomic temp-file-and-replace, same `json.dump` formatting as MyAgent, so diffs stay clean).
4. Spawns `python MyAgent.py -l "<name>" --headless` detached, then marks the email read.

It reuses MyAgent's stored Gmail token (`~/.config/myagent-google/<account>_token.json`, silent refresh) and **never** opens the interactive OAuth flow — if auth fails it logs and leaves the email unread so a later pass (or a manual MyAgent run) recovers. Malformed trigger emails (unknown instruction name, missing `*****` marker, empty first line) are treated as poison: marked read and logged rather than retrying every 10 minutes forever. One matching email is processed per pass (oldest first), so queued triggers drain at a controlled rate. Every pass appends one line to `~/Library/Logs/myagent/heartbeat.log` (`checked — nothing found`, `launched ... (PID ...)`, `POISON ...`, or `ERROR ...`), making the log a liveness record — a gap in timestamps means the machine was asleep or the job unloaded.

The rationale: this replaced an equivalent AI-run `Heartbeat_Instruction` (still in `agent_instructions.json` as the reference version) that cost ~10¢ per poll — ~$14/day at 10-minute ticks — to make decisions that require no judgment at all. The deterministic trigger is free; the spawned agent is where the intelligence (and the spend) belongs.

The script is cross-platform (process discovery uses `pgrep`/`ps` on macOS and a PowerShell CIM query on Windows; the log falls back to `heartbeat.log` in the repo root on Windows, gitignored). Two per-machine setup steps don't travel through git:

1. **Scheduler** — macOS uses the launchd plist described above. On Windows, register a Task Scheduler job (PowerShell, adjust the repo path):

   ```powershell
   $repo = "C:\path\to\Claude_Python_Testbed"
   Register-ScheduledTask -TaskName "MyAgent_Heartbeat" `
     -Action (New-ScheduledTaskAction -Execute "$repo\.venv\Scripts\pythonw.exe" -Argument "Heartbeat.py" -WorkingDirectory $repo) `
     -Trigger (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration ([TimeSpan]::MaxValue))
   ```

2. **API keys for spawned agents** — the children inherit the scheduler's environment, NOT your shell profile. On macOS the keys are embedded in the plist's `EnvironmentVariables` (chmod 600). On Windows, Task Scheduler tasks inherit *user environment variables* (`setx`-style), so keys set that way work with no extra step — but keys exported only in a shell rc are invisible, and spawned agents will warn `⚠ Instruction wants provider X but it is unavailable` and fall back to Ollama.

**Unread-email summary at zero token cost — `UnreadSummary.py`.** The same conversion applied to the daily summary job: a plain Python script (no LLM) that replaces the AI-run `Email_AllUnreadSummary_Mac3` instruction (kept in `agent_instructions.json` as the reference version). One pass:

1. Scans **all** configured accounts — Gmail (`~/.config/myagent-google/accounts.json`), Proton Bridge / WebCentral IMAP (`~/.config/myagent-protonmail/accounts.json`), and Outlook (`~/.config/myagent-msmail/accounts.json`) — for unread email in Inbox and Spam/Junk folders, and builds the COMPREHENSIVE LIST: one running number sequence across accounts, each entry showing Account (with a `[SPAM]`/`[JUNK]` tag), From, To (only when the mail was forwarded from another address), Subject, Date, and a summary. The "summary" is the first ~45 words of the cleaned body text — preheader boilerplate, URLs, and invisible padding characters stripped — which for bills and notifications is the gist; long-form newsletters lose nuance vs the AI version, the one deliberate trade-off.
2. Matches each unread email against the **SPECIFYING LIST** (a constant at the top of the script: nine known bill/receipt types — Origin Energy, PayPal Amaysim/Apple/Netflix, Anthropic, SKYONE, Klemzig storage, JB Hi-Fi, Ku-ring-gai Council). Matches get their Determine fields (amount, due date, invoice number, …) extracted by label-proximity regexes — label and value on the same line (Stripe's `Amount paid $38.60`) or the value on the next lines (PayPal's `Order ID` ↵ `65038…`) — with typed value patterns (money / date / code) so a label can never match prose, and an honest `(not found)` when a sender redesigns their template. The match and its fields are noted inline in the list and the bill's PDF attachments are saved to `~/Downloads` — idempotently: since matched emails stay unread and are re-seen by every later run, an identical already-downloaded file is reused rather than stacking up `name (1).pdf`, `name (2).pdf`. Mark-read and move-to-Trash exist behind the `MARK_MATCHES_READ` / `TRASH_MATCHES` flags (both off by default, so matched bills stay unread in place); setting them True restores the full Mac3 processing behaviour.
3. Sends the list from the Outlook account to the notification address (subject `Summary of Unread Emails - <timestamp>`), with a per-account `ERROR:` line for any account that couldn't be read and a TOTAL footer.

The safety property the AI version enforced by prompt ("building the list is STRICTLY READ-ONLY") holds here **by construction**: the listing phase only uses read-only primitives (IMAP `EXAMINE` + `BODY.PEEK` — a plain `BODY[]` fetch would set `\Seen` — Gmail `messages.get`, Graph `GET`), mailbox mutations are flag-disabled by default, and even when enabled the mutating code path is unreachable except for SPECIFYING matches. Auth is silent-only like Heartbeat (stored Gmail token refresh, MSAL silent acquisition; a missing/expired token becomes an ERROR line in the email — or a failed run if it's the sending account — repaired by running MyAgent once interactively). `python UnreadSummary.py --dry-run` prints the would-be email to stdout and guarantees zero mutations and no send. Each pass appends one line to `~/Library/Logs/myagent/unread_summary.log` (repo-root `unread_summary.log` on Windows, gitignored). Schedule it like the 7am email-summary job (launchd `StartCalendarInterval` / Task Scheduler daily trigger) — it needs no API keys at all, only the stored mail credentials.

### Features

#### Agent Instructions

Agent Instructions are pre-configured task descriptions that serve as the first (and only) user message. They are managed through a dedicated **Instruction Editor** window and stored in `agent_instructions.json`.

| Control | Description |
|---|---|
| **Instruction Name** entry | Name for saving/loading instructions |
| **SAVE** button | Save the instruction (text, images, tool toggles, provider, model parameters, skill modes) to disk and make it the active instruction |
| **DELETE** button | Remove the named instruction from disk |
| **CLEAR** button | Reset the editor — clears text, images, and tool toggles |
| **Load Instruction** dropdown | Select a previously saved instruction — populates the editor fields for preview |
| **Apply** button | Next to the Load dropdown — make the instruction active for this session (no disk write) and close the editor |
| **Text editor** | Multi-line area for writing the task description |
| **Attach Images** button | Select image files to attach to the instruction |
| **Remove Selected** button | Delete selected images from the image list |
| **Desktop** checkbox | Enable/disable the 13 desktop automation tools (plus the Gemini-only `find_element`) for this instruction |
| **Browser** checkbox | Enable/disable the 11 browser automation tools for this instruction |
| **Meta** checkbox | Enable/disable the 3 meta-agent tools (`manage_instructions`, `manage_skills`, `run_instruction`) for this instruction |
| **MCP** checkbox | Enable/disable external MCP (Model Context Protocol) tools loaded from `mcp_servers.json`. Disabled if the `mcp` Python package is not installed. See **MCP Integration** below |
| **Convo** checkbox | Enable Conversational mode — MyAgent enforces a chatbot loop by automatically invoking `user_prompt` whenever the model ends a turn without calling it. Designed for smaller open-weights models (Qwen3, Llama, gpt-oss) that don't reliably follow "always call user_prompt" meta-rules. See **Conversational Mode** below |
| **Skills** button | Open the Skills Manager to configure skills; the button label shows a count summary (e.g., `Skills (2+3)` = 2 enabled + 3 on-demand) |
| **Safety** button | Open the Safety dialog to selectively bypass individual confirmation patterns (shell commands AND Gmail destructive ops AND Proton destructive ops); the button label shows a count when patterns are bypassed (e.g., `Safety (3 bypassed)`) |
| **Image list** | Scrollable listbox showing attached image filenames (purple text, multi-select) |

**Draft/commit editing model** — The editor works on a temporary copy of all data (text, images, Desktop/Browser/Meta/MCP/Convo toggles). Loading an instruction or making edits only affects the editor's working copy. Changes are only committed when you explicitly press SAVE or Apply. Closing the editor with [X] discards all uncommitted changes.

| Action | Makes it active | Saves to disk | Closes editor |
|---|---|---|---|
| **Load Instruction** | No | No | No |
| **SAVE** | Yes | Yes | No |
| **Apply** | Yes | No (but snapshotted to `agent_state.json` — survives restart) | Yes |
| **Close [X]** | No | No | Yes |

**Apply survives restart** — Although Apply does not write to `agent_instructions.json`, MyAgent snapshots the full live instruction state (text, attached images, Desktop/Browser/Meta/MCP/Convo toggles, provider, model, temperature, all thinking parameters, text verbosity, and disabled Safety patterns) into `agent_state.json` under an `applied_instruction` key on every periodic auto-save and on close. (Skill modes are deliberately excluded from this snapshot — `skills.json` is their sticky source of truth, so a relaunch can never overwrite your global skill configuration; see **Skill modes persist with instructions** below.) On next launch, this snapshot is preferred over re-loading the disk entry by name. The practical effect: you can edit an instruction, hit Apply, restart MyAgent, and resume exactly where you left off — without needing to SAVE just to survive a restart. The on-disk entry in `agent_instructions.json` remains the canonical "named" version; the snapshot only restores what was actually live in your last session. Older `agent_state.json` files without the snapshot key still fall back to the by-name lookup, so nothing breaks on upgrade.

**Images persist with instructions** — When you save a named instruction, any attached images are embedded as base64 data inside `agent_instructions.json`. Loading that instruction later automatically re-attaches those images. This means a task like "analyse this screenshot and do X" can be saved as a reusable instruction that always includes its reference image.

**Tool toggles persist with instructions** — Each saved instruction stores its Desktop, Browser, Meta, MCP, and Convo checkbox states. Loading an instruction restores these toggles in the editor; SAVE or Apply commits them to the main window. The `python MyAgent.py -l "Name"` auto-launch path also restores all five toggles correctly so headless command-line runs behave identically to GUI-driven loads.

**Provider and model parameters persist with instructions** — Each saved instruction stores the provider (Anthropic, OpenAI, or Gemini), model, temperature, and thinking settings. Loading an instruction from the dropdown immediately restores the provider, refreshes the model list, and sets the model and thinking parameters on the main toolbar.

**Skill modes persist with instructions (session-only)** — Each saved instruction snapshots the current skill modes (disabled/enabled/on-demand for every skill). Loading an instruction applies these modes to the **live session** (driving the system prompt and the Skills button label) but does **not** write them back to `skills.json`. `skills.json` is the sticky global source of truth for skill modes — changed only by explicit Skills Manager / `manage_skills` edits — so loading an instruction, or simply relaunching MyAgent, never silently overwrites your global skill configuration. Skills that didn't exist when the instruction was saved default to disabled for that session.

**Safety patterns persist with instructions** — Each saved instruction stores its set of disabled confirmation patterns (shell command regex bypasses plus per-tool Gmail bypasses plus per-tool Proton bypasses). Loading an instruction restores these bypass settings, and the Safety button label updates to show how many patterns are bypassed. This effectively makes each instruction a self-contained task profile — text, images, tool categories, provider, model configuration, skills environment, and Safety overrides — so different tasks can target different providers, models, settings, and skill sets.

When a named instruction is applied, the window title updates to show it (e.g., `My Agent — Daily News Brief`).

A "Default" instruction is automatically created on first run if missing. Old-format instruction files (plain string values) are auto-migrated to the new dict format that includes image data.

**Example — the `Email_AllUnreadSummary_Mac` / `Email_AllUnreadSummary_Win` instruction pair** — A worked multi-account task: it sweeps every configured mailbox (Gmail ×2, Proton/IMAP, Outlook) for unread Inbox mail and builds one numbered **COMPREHENSIVE LIST** — a single running sequence that starts at 1 and continues unbroken across all accounts, with section dividers fixed at exactly 40 characters for stable, predictable formatting — then emails the summary from Outlook. A second **SPECIFYING LIST** step pulls key fields from up to 8 named bill/receipt types, downloads any PDF attachments, and marks-read + trashes **only those matched emails**. Because the `*_send` / `*_trash` confirmation dialogs are bypassed (via the Safety dialog) so the task can run headless/scheduled, the prompt carries an explicit **`*** SAFETY (READ-ONLY)` guard**: building the COMPREHENSIVE LIST must never mark-read, move, or trash anything, and if no SPECIFYING-LIST emails are found, nothing is trashed at all. The guard is stated twice — once as a top-level directive and again inline at the trash step — so the read-only boundary holds even deep into a long agentic loop. This is a deliberate prompt-safety pattern: a *negative* constraint ("never touch the rest") plus an explicit zero-match fallback, since an agent will not infer that everything outside the named targets is off-limits. The two instructions are identical except for the platform-specific attachment download directory (a macOS path vs a Windows path).

#### Provider Selection & Model Selection

A **Provider** combobox on the model toolbar switches between **Anthropic**, **OpenAI**, **Gemini**, and **Ollama** (local inference). Only providers with valid API keys — or, for Ollama, a reachable local server — are shown. The provider combobox is **locked (disabled) while the agent is running** to prevent mid-run changes.

When switching providers, the **Model** dropdown refreshes with available models for that provider:
- **Anthropic** — Fetches models live from the Anthropic API (falls back to Claude Sonnet 4.5, Opus 4.6, Haiku 4.5)
- **OpenAI** — Fetches models from the OpenAI API, filtered to Responses API compatible families only: `gpt-4o`, `gpt-4.1`, `gpt-4.5`, `gpt-5`, `o1`, `o3`, `o4` (falls back to GPT-5, GPT-5-mini, GPT-4.1, GPT-4.1-mini, o4-mini). Legacy models (gpt-3.5-turbo, base gpt-4, gpt-4-turbo) are excluded as they don't support the Responses API. `gpt-5.x-chat-*` "Instant" variants are non-reasoning models that support verbosity but not temperature
- **Gemini** — Fetches models from the Gemini API, filtering out non-generative (embedding, imagen) and deprecated (Gemini 2.0, 1.x) models (falls back to Gemini 2.5 Flash, 2.5 Pro). Uses the `google-genai` unified SDK. The floating `-latest` aliases the API returns (`gemini-pro-latest` → 3.x Pro, `gemini-flash-latest` → 3 Flash) are recognized as thinking-capable even though their version sits *after* the tier word (`gemini-flash-lite-latest` stays non-thinking via the "lite" check)
- **Ollama** — Fetches locally-installed models from the Ollama server (`/api/tags`). Whatever you've `ollama pull`-ed shows up. No filtering — all local models are listed (text, vision, thinking, tool-capable, etc.). Per-model capabilities (thinking, tool calling, vision, context length) are **auto-detected at runtime** by calling `/api/show` and caching the result — so the UI adapts per model without hand-coded prefix lists. See the **Ollama (Local Inference)** section below for full details

**Gemini tool-schema sanitization** — Gemini's `google-genai` SDK enforces a stricter JSON-Schema dialect than the Anthropic and OpenAI tool APIs. The canonical tool schemas are authored once (Anthropic style) and shared across all four providers, so `_clean_schema_for_gemini()` normalizes them at the Gemini conversion boundary: it drops dialect/metadata keys the validator rejects (`$schema`, `title`, `default`, `additionalProperties`, …) and strips **blank (`""` / whitespace-only) `enum` values**, which Gemini rejects with `enum[i]: cannot be empty`. If an `enum` ends up empty after stripping (e.g. a runtime-patched `account` enum with no configured accounts), the constraint is removed so the parameter degrades to a plain string. Anthropic and OpenAI keep the richer schema — a blank `enum` value such as `proton_create_label`'s top-level `parent` option is legal for them — so only the Gemini path is degraded.

A **Temp** spinbox controls temperature (0.0–1.0), and a **Thinking** checkbox with **Strength** combobox enables extended thinking/reasoning.

| Provider | Model type | Thinking mode | Strength control |
|---|---|---|---|
| Anthropic | **Adaptive** (Opus 4.6+, Sonnet 4.6+ — version-parsed, incl. dated snapshots) | `thinking: {type: "adaptive"}` | Thinking mode combobox: Off, Adaptive, Low, Medium, High, Max (Max only for Opus 4.6+) |
| Anthropic | **Manual** (Opus 4.5, Sonnet 4.5, Haiku 4.5) | `thinking: {type: "enabled", budget_tokens: N}` | Token budget: 1K, 4K, 8K (default), 16K, 32K |
| OpenAI | **Extended Reasoning** (GPT-5.1+) | `reasoning: {effort: ..., summary: "auto"}` | Reasoning mode combobox: None, Low, Medium, High, Xhigh (Xhigh for GPT-5.2+/codex-max) |
| OpenAI | **Reasoning** (GPT-5.0, o1, o3, o4) | `reasoning: {effort: ..., summary: "auto"}` | Effort level: minimal (GPT-5.0 only), low, medium, high |
| OpenAI | **Instant** (GPT-5.x-chat-*) | Not supported | Verbosity only (no temperature) |
| OpenAI | **Standard** (GPT-4o, GPT-4.1, etc.) | Not supported | N/A |
| Gemini | **Thinking** (Gemini 2.5 & 3.x, incl. `-latest` aliases) | `thinking_config: {thinking_budget: N}` | Effort level: low (1K), medium (8K), high (24K) |
| Gemini | **Standard** (Flash-Lite variants — any model with "lite") | Not supported | Temperature only |
| Ollama | **Thinking** (Qwen3, DeepSeek-R1, gpt-oss) | `think: true/false` on `/api/chat` | Boolean checkbox only — Ollama's `think` flag is boolean today, so the strength combo is hidden to avoid showing a control that does nothing. Effort granularity will return when upstream exposes a per-request thinking budget |
| Ollama | **Vision / Standard** (Qwen2.5-VL, Gemma 3, Llama 3.2 Vision, etc.) | Not supported | Temperature only |

**GPT-5.x extended reasoning** — GPT-5.1+ models use a **Reasoning** mode combobox (None/Low/Medium/High/Xhigh) instead of the checkbox+strength pattern. Selecting "None" sends `reasoning: {effort: "none"}`, any other sends the corresponding effort level. Xhigh is available for GPT-5.2+ and codex-max models, but **not** for mini/nano variants (which cap at High). All GPT-5 family models (including `-chat` Instant variants) show a **Verbosity** combobox (Low/Medium/High) that controls `text.verbosity` in the API, defaulting to Medium. GPT-5.4+ models show the Temp spinner when reasoning is set to "None" (the API accepts temperature in that mode); older GPT-5 models (5.0–5.3) keep temperature hidden at all times.

**Adaptive thinking mode** — For Anthropic adaptive models, the checkbox and strength combobox are replaced by a single **Thinking** mode combobox with values: Off, Adaptive, Low, Medium, High, Max. "Off" disables thinking entirely. "Adaptive" sends `thinking: {type: "adaptive"}` without an explicit effort level (the API decides). Low/Medium/High/Max send `output_config: {effort: ...}` alongside adaptive thinking. "Max" is only available for Opus 4.6 and later (Opus-only; version-parsed so future releases keep it). The adaptive-vs-manual classification is itself version-parsed (`_is_anthropic_adaptive_model`: Opus/Sonnet ≥ 4.6) in addition to an exact-match alias set, so a *dated* snapshot the API may return (e.g. `claude-sonnet-4-6-20260101`) still gets the adaptive UI instead of silently falling back to no-thinking — the API returns some Claude IDs dated and some undated, so exact-match alone is not enough. For manual and OpenAI models, the standard checkbox + strength controls are shown instead. The UI dynamically switches between these two control styles when changing models.

**Temperature and thinking controls are model-aware** — GPT-5.0–5.3 models have temperature fixed at 1.0 (the Temp spinner is hidden). GPT-5.4+ models show the Temp spinner only when reasoning effort is "None". `gpt-5.x-chat-*` Instant variants never show temperature (API rejects it). Other OpenAI reasoning models (o1/o3/o4) also hide temperature. Standard OpenAI models (gpt-4o, gpt-4.1) show the Temp spinner normally. Gemini accepts temperature even with thinking enabled, so the Temp spinner stays active for all Gemini models. For Anthropic, when thinking is enabled (any mode except Off), temperature controls are hidden. Additionally, **Opus 4.7 and later removed sampling parameters entirely** — sending a non-default `temperature` (0.0/0.5/etc; 1.0 is tolerated as the default) returns HTTP 400 — so the Temp spinbox is hidden for those models regardless of thinking state (`_anthropic_rejects_temperature()`). A reactive `BadRequestError` handler also strips `temperature`, caches the offending model in `_anthropic_no_temperature`, and retries once (mirroring the OpenAI temperature/tool-rejection fallbacks). This is enforced across all code paths: model selection, thinking toggle, and state restore.

Provider, model, temperature, thinking settings, and text verbosity are all persisted across sessions in `agent_state.json` and saved/restored per Agent Instruction.

#### Ollama (Local Inference)

Ollama runs LLMs locally on your machine — weights live in `~/.ollama/models/`, inference happens through llama.cpp under the hood, and the Ollama daemon exposes an HTTP API at `http://localhost:11434`. No API key, no cost, no network egress during inference. The tradeoff is speed: a 32B Q4 vision model on Apple Silicon runs at ~10-30 tokens/sec vs sub-second cloud latency, and spatial precision on UI elements is weaker than Gemini's trained pointing capability.

**Install & pull models:**

```bash
# Install Ollama from https://ollama.com/download, then:
ollama serve                           # starts the background daemon
ollama pull qwen3:32b-q4_K_M           # text + tool-calling + thinking (20 GB)
ollama pull qwen2.5vl:32b              # vision (21 GB, 128K context)
ollama pull llama3.2-vision:11b        # fast vision (8 GB, 128K context)
ollama pull gemma3:27b                 # strong vision (17 GB, 128K context)
```

MyAgent's model dropdown auto-populates from whatever is installed. No code changes needed when you pull a new model.

**Capability auto-detection** — `_get_ollama_model_caps()` in `myagent/ollama_mixin.py` queries `/api/show` on first use of each model and caches the response per-session. The response tells MyAgent:

- `capabilities` — whether the model supports `tools`, `vision`, `thinking`, `completion`. Each capability gates a distinct part of the pipeline:
  - `tools` present → `tools` parameter is passed to `/api/chat`; if absent, tools are silently dropped and a one-time `⚠` warning is surfaced
  - `vision` present → the `_is_ollama_vision_model()` / weak-combo warning suppresses the "text-only model can't see screenshots" warning when desktop tools are enabled
  - `thinking` present → `think: true/false` is passed explicitly on every call (omitting `think` falls back to the model's training default, which is thinking-**ON** for Qwen3; only explicit `false` reliably suppresses reasoning)
- `context_length` — extracted from `modelinfo["{arch}.context_length"]` and passed as `num_ctx` in the request. Capped at `OLLAMA_NUM_CTX_CAP` (default 32768) to prevent KV cache memory pressure on Mac mini 32 GB setups where the model's full advertised context (40K-128K) would push the system into disk swap

**Env-var tuning:**

| Variable | Default | Purpose |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where MyAgent looks for the Ollama server. Set to a remote host to use a networked Ollama instance |
| `OLLAMA_NUM_CTX_CAP` | `32768` | Maximum `num_ctx` MyAgent will send. Raise to 65536 or 131072 on 64 GB+ hardware; lower to 16384 if you hit swap pressure |
| `OLLAMA_KEEP_ALIVE` | `5m` (Ollama's default) | How long Ollama keeps a model resident after last use. Set to `24h` for an all-day working session to avoid repeat 10-15 second cold loads |

**Qwen3's `<message>` wrapper** — Qwen3's tool-calling chat template wraps plain-text replies in `<message>...</message>` tags when tools are present in the request. This is a protocol marker that Ollama's non-streaming code path strips but its streaming path leaks. `_stream_ollama_call` strips the wrapper in the streaming path via a small lookahead buffer — tags never reach the UI, and the buffer holds back only the last 10 chars (the length of `</message>`) so live streaming stays responsive.

**Custom Modelfiles (vision + tools)** — Ollama's default Modelfiles for multimodal models (Qwen2.5-VL, Llama 3.2 Vision, Gemma 3) ship with templates that expose only `[completion, vision]` — tool calling is **not** wired up even though the underlying model weights support it. This repo ships three custom Modelfiles that graft Qwen3's proven `{{ .Tools }}` preamble + `<tool_call>` XML marker format onto each vision model's chat tokens:

| Modelfile | Base model | Build command |
|---|---|---|
| `Qwen25VL-tools.Modelfile` | `qwen2.5vl:32b` | `ollama create qwen2.5vl-tools:32b -f Qwen25VL-tools.Modelfile` |
| `Llama32Vision-tools.Modelfile` | `llama3.2-vision:11b` | `ollama create llama3.2-vision-tools:11b -f Llama32Vision-tools.Modelfile` |
| `Gemma3-tools.Modelfile` | `gemma3:27b` | `ollama create gemma3-tools:27b -f Gemma3-tools.Modelfile` |

After `ollama create`, each variant's `/api/show` response advertises `[completion, vision, tools]`, MyAgent's caps auto-detection picks up the flip automatically, and tool calls return as structurally-parsed `tool_calls` entries (not text containing `<tool_call>` tags) — verified end-to-end for all three models. **Note:** the `FROM` line in each Modelfile references a specific blob SHA256 path under `~/.ollama/models/blobs/` — if you re-pull the base model after a major Ollama update, the blob path may change and you'll need to edit the `FROM` line to match the new path (check via `ollama show <base-model> --modelfile | head`).

**Performance expectations on Mac mini 32 GB:**

| Task | 32B model (Qwen3 / Qwen2.5-VL / Gemma 3) | 11B model (Llama 3.2 Vision) |
|---|---|---|
| First response after cold load | ~15-30 seconds | ~5-10 seconds |
| Text-only round trip (no vision) | ~10-40 seconds | ~3-10 seconds |
| Vision round trip (screenshot → describe) | ~40-90 seconds | ~15-30 seconds |
| Agentic loop (screenshot → click → screenshot → ...) | 2-5 min per iteration | 30-90 sec per iteration |

Use `llama3.2-vision-tools:11b` for iteration/testing, and the 32B variants when quality matters and the wait is acceptable.

#### Tool Use

MyAgent has roughly sixty-five built-in tools (including the Gemini-only `find_element`, the sixteen Gmail tools, the sixteen Proton Mail tools, and the local `read_document` tool) plus the dynamic `get_skill` tool, organised into seven categories (core, desktop, browser, MCP, Google/Gmail, Proton Mail, meta):

**Core Tools (always available):** `run_powershell`/`run_shell`, `csv_search`, `read_document` (PDF/DOCX/HTML/text), `user_prompt`, plus `web_search` and `fetch_webpage` (Gemini only — see below).

**Server-side tools (OpenAI and Anthropic):** When using **OpenAI** or **Anthropic**, the custom `web_search` and `fetch_webpage` tools are replaced by native server-side equivalents:

| Provider | Web Search | Code Execution |
|---|---|---|
| **OpenAI** | `web_search_preview` — server-side search with citations | `code_interpreter` — Python sandbox with auto container (`include: ["code_interpreter_call.outputs"]` for image data) |
| **Anthropic** | `web_search_20250305` — beta server-side search | `code_execution_20250825` — beta Bash/Python sandbox (requires `betas` flags and `files-api-2025-04-14` for file downloads via `beta.files.download()`) |
| **Gemini** | Local DuckDuckGo (`ddgs`) | Not available — Gemini API does not allow combining built-in tools with custom function declarations |
| **Ollama** | Local DuckDuckGo (`ddgs`) | Not available — local models rely on the same DuckDuckGo + fetch_webpage tools as Gemini |

Server-side code execution outputs (plots, charts) are displayed inline in the chat widget (scaled to max 600px) and saved to `saved_chats/` as PNG files. OpenAI returns images as base64 data URLs; Anthropic returns file IDs downloaded via the Files API

**Desktop Tools (enabled via Desktop checkbox):** `screenshot`, `mouse_click`, `type_text`, `press_key`, `mouse_scroll`, `open_application`, `find_window`, `clipboard_read`, `clipboard_write`, `wait_for_window`, `read_screen_text`, `find_image_on_screen`, `mouse_drag`, `find_element` (Gemini-only — uses Gemini's native pointing API to locate UI elements by description; see "Provider-specific coordinate handling" below)

**Browser Tools (enabled via Browser checkbox):** `browser_open`, `browser_navigate`, `browser_click`, `browser_fill`, `browser_get_text`, `browser_run_js`, `browser_screenshot`, `browser_close`, `browser_wait_for`, `browser_select`, `browser_get_elements`

**MCP Tools (enabled via MCP checkbox):** Dynamically loaded from any MCP servers configured in `mcp_servers.json`. Tool names are namespaced as `<server>__<tool>` (double underscore) — so a `filesystem` server contributes `filesystem__read_file`, `filesystem__list_directory`, etc. The set is empty when no servers are configured. See **MCP Integration** below for setup.

**Google (Gmail) Tools (enabled via Google checkbox):** Native multi-account Gmail integration via the official `google-api-python-client` library — no MCP server, no subprocess. Sixteen tools: `gmail_search`, `gmail_read` (always returns attachments[] metadata; `format` param for text/html/both body), `gmail_get_attachment` (downloads attachment bytes to a local path; refuses overwrite by default), `gmail_send`, `gmail_reply` (proper Gmail threading via In-Reply-To/References + threadId), `gmail_create_draft`, `gmail_list_drafts`, `gmail_send_draft`, `gmail_trash`, `gmail_untrash`, `gmail_list_labels`, `gmail_create_label`, `gmail_delete_label`, `gmail_modify_labels`, `gmail_mark_read`, `gmail_list_threads`. `gmail_send`, `gmail_reply`, and `gmail_create_draft` all accept an optional `attachments: [filepath, ...]` parameter (combined raw size capped at ~20 MB to stay under Gmail's 25 MB post-base64 ceiling; MIME types auto-detected from file extensions) and an optional `body_html` parameter (multipart/alternative with plain `body` as the fallback for non-HTML clients). Each tool takes an `account` parameter whose enum is patched at runtime from `~/.config/myagent-google/accounts.json`, so the model only sees actually-configured accounts. Destructive operations (`gmail_send`, `gmail_send_draft`, `gmail_trash`) pop a modal Tk confirmation dialog showing recipient/subject/IDs before proceeding. Disabled if `google-api-python-client` / `google-auth-oauthlib` aren't installed. See **Google Integration** below for setup.

**Proton Mail Tools (enabled via Proton checkbox):** Native multi-account Proton Mail integration via Proton Bridge over stdlib IMAP + SMTP — no MCP server, no reverse-engineered REST client. Sixteen tools mirroring the Gmail surface 1:1: `proton_search`, `proton_read` (text/html/both body + attachments[] metadata), `proton_get_attachment`, `proton_send`, `proton_reply` (proper In-Reply-To/References headers), `proton_create_draft`, `proton_list_drafts`, `proton_send_draft`, `proton_trash`, `proton_untrash`, `proton_list_labels`, `proton_create_label`, `proton_delete_label`, `proton_modify_labels`, `proton_mark_read`, `proton_list_threads`. Per-folder IMAP UIDs — every per-message tool takes a (`folder`, `uid`) pair; bulk ops take `folder` once + `uids: [int]`. `proton_send`, `proton_reply`, and `proton_create_draft` accept optional `body_html` and `attachments: [filepath, ...]` (same 20 MB cap as Gmail). Each tool takes an `account` parameter whose enum is patched at runtime from `~/.config/myagent-protonmail/accounts.json`. Destructive operations (`proton_send`, `proton_reply`, `proton_send_draft`, `proton_trash`, `proton_delete_label`) pop the same modal Tk confirmation dialog as Gmail's, with per-tool bypass via the Safety dialog. The `proton_modify_labels` tool transparently handles Bridge's label-removal eventual-consistency quirk via internal auto-retry (response includes `label_removal_retries: N` for observability). Requires Proton Bridge installed and running locally (paid Mail plan). See **Proton Mail Integration** below for setup.

**Meta Tools (enabled via Meta checkbox):** `manage_instructions`, `manage_skills`, `run_instruction` — tools for the agent to manage its own instruction library, shared skills, and launch other agents. `manage_instructions` lets the agent list, read, create, update, or delete saved instructions — including the currently-running instruction (changes are saved to disk and take effect the next time the instruction is loaded, without affecting the live session). Read/create/update actions include `skill_modes` (a map of skill names to disabled/enabled/on_demand modes), and update uses merge semantics so omitted skills keep their current mode. `manage_skills` lets the agent manage skills with mode control (disabled/enabled/on-demand). `run_instruction` launches a saved instruction as a separate MyAgent process (fire-and-forget via `subprocess.Popen`); defaults to headless mode, with an optional `headless=false` parameter to show the GUI window — the launched process runs independently and the PID is returned. None of these tools are parallel-safe since they modify shared state or spawn processes.

**User Interaction Tool:**
- **user_prompt** — Pauses the agentic loop and displays a modal dialog to the user with the agent's message, then waits for the user to type a response. This is the **only** way the agent can get user input mid-task (e.g., asking the user to log in, approve an action, or make a choice). The system prompt strongly instructs Claude to always use this tool rather than outputting a question as plain text (which would end the turn and exit the loop). The user types their response and presses **Enter** to submit (or **Ctrl+Enter** to insert a newline for multi-line responses), or dismisses the dialog (via [X]) to return a default "no response" message. **Submitting an empty response (pressing Enter with no text) immediately stops the agent** — this provides a quick way to end interactive sessions. The user's injected response is echoed in the chat display as "You: [text]" so the conversation flow is visible, and the agent's follow-up response gets a fresh "Agent:" heading

**Dynamic Tool:** `get_skill` — automatically added when on-demand skills exist

Most tool behaviour (DPI-aware coordinate mapping, PowerShell safety guardrails, image compression) is identical to SelfBot — see the SelfBot.py tool sections above for full details. The one browser difference: MyAgent additionally supports **Brave Browser** (preferred ahead of Chrome/Edge on macOS) and, on macOS, launches it with a **persistent** debug profile so logins survive across runs, whereas SelfBot uses an ephemeral temp profile.

**Provider-specific coordinate handling** — Unlike SelfBot (Anthropic-only), MyAgent routes desktop tools through three different provider back-ends. After extensive testing across Claude 4.x, gpt-5.2, and Gemini 2.5/3.x models, all providers now use the **same convention**: pixel coordinates as they appear in the screenshot image, with the system handling all scaling and offset translation internally. The only per-provider variation is the image resolution cap, which matches each provider's actual API limit:

- **Anthropic** — Screenshot image is embedded directly inside the `tool_result` block. Claude uses raw pixel coordinates from the image. Screenshots capped at **1568px long edge / 1.15 MP** (Anthropic's vision API limit; the API silently downscales above this anyway).
- **OpenAI** — Screenshot image is delivered as a separate `user` message following the `function_call_output` item (not embedded in the tool output). GPT models process images more reliably from user messages than from tool output content. Screenshots capped at **2048px long edge / 5 MP** — empirically verified as OpenAI's actual hard limit (we previously tried 2560 but discovered via gpt-5.2's `code_interpreter` PIL inspection that the API silently downscales above 2048, which broke the scale calculation).
- **Gemini** — Image is sent as a separate user `Content` block (Gemini doesn't reliably handle mixed image + function_response Parts in the same Content). Screenshots capped at **2048px long edge / 4 MP** — bumped above the Anthropic-matched 1568 because Gemini's tile system supports higher resolution, giving older models like Gemini 2.5 Pro more pixel density on small UI elements. Earlier versions used Google's documented [0, 1000] normalised convention but switched to pixels because the [0, 1000] abstraction forced the model to do mental arithmetic which introduced systematic ~1-pixel drift on Gemini 3 with reasoning enabled.

**Click accuracy improvements** — Several refinements landed across the coordinate pipeline to eliminate small-target miss patterns:

- **Round, don't truncate** — `do_mouse_click`/`do_mouse_scroll`/`do_mouse_drag`/`do_read_screen_text` use `round(float(x))` instead of `int(x)`, eliminating up-to-1-pixel truncation bias.
- **Pre-screenshot guard** — Click/scroll/drag/OCR refuse with a "Take a screenshot first" error when `_screenshot_dims == (0, 0)`, preventing silent misclicks before any capture.
- **Tiered out-of-bounds policy** — ≤2px overflow silently clamps (handles model rounding), >2% of image dimension refuses with a "re-take a screenshot" message, in-between clamps with a `⚠ clamped` warning. Replaces the prior always-clamp-and-click which masked perception errors.
- **Post-click settle** — A 50ms `time.sleep` after `pyautogui.click` lets the post-click UI settle before the next screenshot, preventing the model from thinking the click missed when it actually landed.
- **Region scale snapshot** — `_capture_single_display` snapshots `entry_scale`/`entry_offset` at function entry so chained region screenshots compute correctly without drifting through stacked offsets.
- **Per-display state tracking** — Two parallel dicts track per-display state: `_display_states[N]` (most recent capture, full or region) for `mouse_click`/`find_element`, and `_display_full_states[N]` (most recent FULL display capture) for region screenshot conversions. Without this two-slot separation, chained region screenshots on the same display drift through stacked offsets.
- **`display=N` parameter** — `mouse_click`, `mouse_scroll`, `mouse_drag`, `read_screen_text`, and `find_element` all accept an optional `display` parameter so the model can disambiguate which screen to act on without re-screenshotting. When omitted, falls back to the most recent capture's coordinate space.
- **DPI awareness v2** (Windows) — MyAgent uses `SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)` instead of v1 `SetProcessDpiAwareness(2)`. v2 fixes broken multi-monitor behavior under v1 when monitors have different DPI scaling (e.g. 100% primary + 225% secondary): v1 reports the secondary's rect as a logically-scaled smaller size, causing `ImageGrab` to return a low-res image and `pyautogui` clicks to land in the wrong place.
- **OpenAI code interpreter gating** — `code_interpreter` is stripped from OpenAI tool lists when desktop tools are enabled. Empirically, gpt-5.2 with code_interpreter access loads screenshot bytes via PIL, sees the API-resized image dimensions, and pre-scales coordinates ITSELF before calling `mouse_click` — collides with our scale calculation and produces double-scaled misclicks. CI remains available for non-desktop OpenAI tasks.

**`find_element` tool (Gemini-only)** — Uses Gemini's native spatial pointing API to locate UI elements by natural-language description. Implemented in `gemini_mixin.py:do_gemini_find_element` using Google's documented pointing prompt format (`"Point to the X. The answer should follow the json format: [{\"point\": <point>, \"label\": <label1>}, ...]. The points are in [y, x] format normalized to 0-1000."`) — using the exact official phrasing is critical because the trained pointing capability only activates with that prompt. Accepts an optional `display` parameter so the cached image lookup hits the right display via `_display_images[display]`; without this, find_element after a multi-display screenshot would always search whichever display was captured last in the loop. Returns pixel coordinates ready to pass directly to `mouse_click`. Filtered out of `_get_tools()` for non-Gemini providers since it requires the Google API.

**Grid overlay** — `screenshot` accepts an optional `grid=true` parameter that draws a 100-pixel coordinate grid (magenta gridlines + (x,y) labels) on top of the captured image after the API-limit resize but before PNG encoding. Drawn in `_draw_coord_grid` so the labels match the dimensions the model actually sees. Opt-in default off (gridlines obscure small UI text on regular screenshots); the tool description suggests using it for small/dense UI targets where pixel-level precision matters.

**Weak combo warning** — At agent start, `stream_worker` checks for known-weak provider/model combinations with desktop tools enabled and posts a `⚠` warning to the activity output. Currently warns for: gpt-5 family with reasoning effort = `none`/`minimal`, gpt-5 `-chat` Instant variants, any `gemini-2.x` model, and any Ollama model that is **not** a vision model (e.g. text-only Qwen3 cannot see screenshots even though Ollama won't error when images are sent). A second Ollama-specific warning fires from `_stream_ollama_call` when the selected model's `/api/show` response does not advertise the `tools` capability — the tools parameter is silently dropped and the user is informed once per model/session. Informational only — does not change behaviour. Helps catch user-error model picks early.

#### MCP Integration

MyAgent ships with a generic **Model Context Protocol (MCP)** client (`myagent/mcp_mixin.py`) that connects to external MCP servers — JSON-RPC stdio servers like filesystem, GitHub, Slack, Postgres, etc. — and exposes their tools through the same agent loop as native tools. The integration works across **all four providers** (Anthropic, OpenAI, Gemini, Ollama) since MCP tool schemas flow through MyAgent's existing `_get_tools()` assembler and each provider's translator.

**Architecture:**
- A dedicated asyncio event loop runs in a background thread (MCP's Python SDK is async-only; MyAgent is sync). Tool calls dispatch via `asyncio.run_coroutine_threadsafe`
- All server connections are held inside one `AsyncExitStack` owned by a long-lived **runner coroutine** (`_mcp_runner`) that connects, lists tools, then parks on a shutdown event for the whole app session. Splitting that lifecycle across multiple `run_coroutine_threadsafe` calls would let the connecting task end and take anyio's stdio reader/writer pumps with it (manifesting as `Connection closed` on the next `list_tools` call) — the runner pattern keeps every anyio cancel scope bound to a live task. Close-on-shutdown signals the runner, the stack unwinds in LIFO order, and the spawned subprocesses terminate cleanly
- **Connects run sequentially inside the runner**, not via `asyncio.gather`. anyio cancel scopes bind to whichever task entered them — `gather(_connect_one(...))` would spawn child tasks that enter `enter_async_context` calls and then finish, leaving orphaned scopes the runner can't cleanly exit at shutdown. macOS asyncio (`SelectorEventLoop` / kqueue) flags this as `Attempted to exit cancel scope in a different task than it was entered in`; Windows `ProactorEventLoop` happens to mask the orphan-scope hazard but the lifecycle is still wrong. A sequential `for` loop binds every cancel scope to the runner task itself, so the integration is consistent across both platforms. The cost is a small startup latency (each connect waits for the previous to complete) — measured in tens of ms per server for the typical 1-3 server config
- Tool names are namespaced as `<server>__<tool>` (double underscore — accepted by all four providers' tool-name regexes)
- `_HAS_MCP` availability flag mirrors `_HAS_OLLAMA`: if the `mcp` Python package is not installed, the entire mixin is a graceful no-op and the MCP checkbox in the editor is disabled

**Setup:**

1. **Install the MCP Python SDK:**
   ```bash
   pip install mcp
   ```

2. **Create `mcp_servers.json` at the project root.** The fastest path is to copy the tracked template:
   ```bash
   cp mcp_servers.example.json mcp_servers.json
   ```
   Then edit `mcp_servers.json` to replace the `<absolute-path-to-your-project-root>` placeholder with the real absolute path to your local clone (e.g. `C:/Users/you/projects/Claude_Python_Testbed` or `/Users/you/projects/Claude_Python_Testbed`). Add or remove server blocks as needed. Format mirrors Claude Desktop / Cursor:
   ```json
   {
     "servers": {
       "filesystem": {
         "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you/projects"]
       }
     }
   }
   ```
   `mcp_servers.json` is **gitignored**; `mcp_servers.example.json` is **tracked**. Never put real secrets (API tokens, OAuth client secrets) in either file — keep that boundary even if you're tempted to "just commit a quick edit" later. Credentials always live in per-server config dirs outside the repo.

3. **`${NAME}` env-var substitution for secrets** — Any value inside the `env` block of an entry in `mcp_servers.json` can use `${NAME}` placeholders that resolve at server-spawn time. This lets you keep tokens and other secrets in your shell environment (e.g. `~/.zshrc`, Windows User env vars, or a `.env` you source before launch) instead of committing them to `mcp_servers.json`. Example:
   ```json
   {
     "servers": {
       "github": {
         "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-github"],
         "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
       }
     }
   }
   ```
   With `export GITHUB_TOKEN=ghp_...` in your shell, the spawned server sees `GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...`. The JSON file itself stays free of secrets and is safe to track in version control if you want to (though it's still gitignored by default — see the **Tracking `mcp_servers.json`** section below). Multiple placeholders per value work (`postgres://${PG_USER}:${PG_PASS}@host/db`). Substitution applies **only** to values inside the `env` block — `command` and `args` stay literal so process listings don't leak secrets via `ps`/Task Manager. An unset reference substitutes empty (`GITHUB_PERSONAL_ACCESS_TOKEN=`, which fails noisily at the server) and emits a one-shot `⚠ ${NAME} referenced … but not set` warning to the activity output.

4. **`${RANDOM_PORT}` placeholder for multi-instance support** — Some MCP servers bind a fixed TCP port at startup. Without `${RANDOM_PORT}`, two simultaneous MyAgent instances would collide on the default port (`EADDRINUSE`). The mixin substitutes `${RANDOM_PORT}` with a fresh OS-assigned free port per occurrence at spawn time — independent per occurrence, so `"--listen=${RANDOM_PORT} --metrics=${RANDOM_PORT}"` produces two different ports. `RANDOM_PORT` is a **reserved name** that never consults `os.environ`, so a shell var literally named `RANDOM_PORT` cannot shadow it.

5. **macOS GUI launches** — Both `My Agent.command` (the macOS launcher) and `mcp_mixin.py:_connect_one()` augment the spawned subprocess's PATH with `/opt/homebrew/bin` and `/usr/local/bin` so `npx` is reachable. macOS GUI launches inherit a stripped-down PATH from `launchctl` that excludes Homebrew by default — without this fix, MCP server spawn fails with `[Errno 2]`.

**Per-instruction toggle** — The MCP checkbox is per-instruction, persisted in `agent_instructions.json` alongside Desktop/Browser/Meta. Each saved instruction can independently enable or disable MCP without affecting others.

**Token-budget awareness** — When MCP is on, every connected server's tool catalog is sent in the API request's `tools` parameter on every call. A large server catalog can add 5–10K input tokens per turn before the user's content. On 200K-context models this is a non-issue; on Ollama's 32K cap (Qwen3) it can matter for long agent loops. The MCP checkbox toggles all MCP tools at once — leave it off for tasks that don't need them.

**Cross-platform** — MyAgent's MCP integration works identically on macOS and Windows after `git pull` plus a per-machine setup of `pip install mcp` and `mcp_servers.json`. The MCP Python SDK handles Windows-specific subprocess quirks internally (resolving `npx` to `npx.cmd`, using Job Objects for cleanup).

**Per-machine config differs** — `mcp_servers.json` is gitignored *by design*: it can contain spawn commands and env-stored secrets that should never enter version control. Each machine maintains its own copy. Different machines can therefore have different MCP catalogs — the agent code is identical, but the runtime tool surface varies per host. Saved instructions persist the *MCP checkbox* state, not the *tool list*; loading an instruction with MCP=on uses whatever servers happen to be configured locally. Useful as a feature (machines can specialise), occasionally a footgun (an instruction that names `<server>__<tool>` will fail on a machine where that server isn't configured).

**Windows pythonw stderr fix** — The mcp Python SDK's `stdio_client` defaults `errlog=sys.stderr` and propagates that handle into the subprocess as its stderr. Under `pythonw.exe` (no console — every desktop shortcut, every silent `.bat`, anything launched without a redirect), `sys.stderr` is `None`. Asyncio's Windows ProactorEventLoop subprocess transport mishandles a `None` stderr handle, corrupting IOCP routing on the read pump and producing `ClosedResourceError` on the **first real RPC after `initialize()`**. Filesystem-server is the canary because its initialize→list_tools traffic is bursty enough to hit the corruption window before later RPCs would; slower-init servers usually slip through with the same broken setup. `mcp_mixin.py` opens `os.devnull` once in `_connect_mcp_servers` and passes it as `errlog=` on every `stdio_client` call, so subprocess stderr always has a valid sink regardless of how MyAgent was launched. The fix layers with three other safeguards in the mixin: **(a)** the asyncio loop is created inside the runner thread (not the main thread) so Windows IOCP ownership matches polling; **(b)** an explicit `list_roots_callback` returns `ListRootsResult(roots=[])` instead of the SDK default `ErrorData("List roots not supported")`, sidestepping any path through the error-response handling that some servers don't tolerate; **(c)** `_list_tools_for_server` catches transient stream-closed errors and reconnects via a fresh `stdio_client` + `ClientSession` swap before retrying. Belt-and-braces — only **(a)** and the **errlog fix** address concrete failure modes encountered in practice; the rest are insurance.

**Inline tool listing at connect** — Tool discovery is performed *immediately* after each server's `session.initialize()` completes, inside the same `for` loop iteration that entered its `stdio_client` context — not deferred to a single batch sweep after every server is connected. The motivation: when multiple `stdio_client` contexts are stacked on the same `AsyncExitStack`, entering a *later* server's context can nudge an *earlier* session's anyio cancel scope into a partial-close state, so `list_tools` against the older session raises `ClosedResourceError` even though every connect succeeded. Listing while each session is still the most-recently-set-up resource catches the catalog before any interference window opens. `_list_tools_for_server(name)` is the per-server helper called inline from `_connect_all`; the older `_refresh_mcp_tools_async` still exists for runtime catalog refresh but no longer participates in the startup path. A failure on a single server's list is logged but doesn't abort the rest of the connect loop, and `do_mcp_call` can recover later by re-listing on demand.

**Cold-cache startup timeout (5 min)** — `_connect_mcp_servers` blocks the calling thread on `_mcp_ready_event.wait(timeout=300)` so callers see a fully populated `MCP_TOOLS` list when the method returns. The 5-minute ceiling is intentional headroom for **first-run cold-cache `npx -y` downloads** of fat packages, which can take 30–90 s on broadband and longer on slower links — well past the original 30 s ceiling. Warm-cache launches still complete in 1–3 s, so the longer timeout costs nothing in practice and only fires when a server is genuinely stuck. The timed-out message reads `⚠ MCP startup timed out after 5 minutes` so the cause is unambiguous when it does fire.

**Vendoring MCP servers** — The repo's `.mcp-deps/` directory (gitignored) supports installing MCP servers locally via `npm install --prefix .mcp-deps <package>` and pointing `mcp_servers.json` at `dist/index.js` via `node` instead of `npx`. Two reasons to vendor: **(1)** patching — if you need to modify a server's source, patches in `npx`'s shared cache get wiped on cache refresh; vendored installs persist. **(2)** durability against npm — bypassing `npx -y` removes the cmd.exe → npx.cmd → node.exe shim chain on Windows, which is occasionally implicated in stdio handshake quirks. Cost: per-machine setup step (each clone re-runs `npm install`). Worthwhile only when you actually need to patch or have hit a reproducible npx-related bug.

**Debugging MCP** — Every `MCPMixin._mcp_log` call is dual-sinked to both MyAgent's queue (visible in the GUI activity widget) and `sys.stderr`. Under `pythonw.exe` stderr is silently discarded by the OS, so production behaviour is unchanged. For diagnostic launches that need to see the full MCP message stream from outside the GUI, redirect stderr at launch time:
```bash
# Windows (cmd.exe / PowerShell)
.venv\Scripts\pythonw.exe MyAgent.py 2> mcp.log

# macOS / Linux
./.venv/bin/python MyAgent.py 2> mcp.log
```
The `mcp.log` file then captures every `✓ MCP server '<name>' connected` / `⚠ MCP server '<name>' failed` / `⚠ MCP refresh failed` line as it happens — useful when a server hangs at handshake, when the GUI activity widget is buried under a long agent loop's tool output, or when validating a fix without manually reading the GUI.

#### Google Integration (Native Gmail Tools)

Native multi-account Gmail integration via the official `google-api-python-client` library, with **sixteen tools** spanning read, write, label management, and attachment download. Implemented in `myagent/gmail_mixin.py` — no MCP server, no subprocess, no JSON-RPC marshalling. Tools flow through MyAgent's existing `_get_tools()` and `_execute_tool()` paths exactly like the desktop/browser tool families.

**Tool inventory:**

| Tool | Purpose | Confirm? |
|---|---|---|
| `gmail_search` | Search messages by Gmail query syntax | |
| `gmail_read` | Fetch a message with body (text/html/both) + attachments[] metadata | |
| `gmail_get_attachment` | Download an attachment to a local file path | |
| `gmail_send` | Send a new email (text + optional HTML + optional attachments) | ✅ |
| `gmail_reply` | Reply with proper In-Reply-To / References / threadId so it nests in Gmail's UI | ✅ |
| `gmail_create_draft` | Create a draft (text + optional HTML + optional attachments) | |
| `gmail_list_drafts` | List drafts | |
| `gmail_send_draft` | Send an existing draft | ✅ |
| `gmail_trash` | Soft-delete to Trash (30-day recoverable) | ✅ |
| `gmail_untrash` | Restore from Trash | |
| `gmail_list_labels` | List labels (system + user) | |
| `gmail_create_label` | Create a new user label (nestable via `/`) | |
| `gmail_delete_label` | Delete a label (removes it from all messages — irreversible labelling loss) | ✅ |
| `gmail_modify_labels` | Add/remove labels on messages | |
| `gmail_mark_read` | Toggle UNREAD label | |
| `gmail_list_threads` | List threads matching a query | |

**Content support:**

- **Plain text + HTML emails** — `gmail_send`, `gmail_reply`, and `gmail_create_draft` all accept an optional `body_html` parameter. When provided, the message ships as `multipart/alternative` with the plain `body` as the fallback for clients that don't render HTML. The plain `body` stays required even when sending HTML — best practice for spam-filter pass-through and broad client compatibility
- **Outbound attachments** — same three send-style tools accept an optional `attachments: [filepath, ...]` parameter. Combined raw size capped at 20 MB (Gmail's hard ceiling is 25 MB after base64 encoding; the cap fails locally with a clear message rather than a 413 from Google). MIME types auto-detected from file extensions via `mimetypes.guess_type`
- **Inbound attachments** — `gmail_read` always includes an `attachments[]` array with metadata (filename, mime_type, size, attachment_id, part_id, inline flag). Pass the `attachment_id` to `gmail_get_attachment(save_to=...)` to download the bytes to disk. Inline attachments (data embedded directly in message body; rare) are flagged `inline=true` and require fetching the message body itself rather than a separate attachment fetch
- **Body format selection** — `gmail_read` accepts a `format` parameter: `"text"` (default, plain text or stripped HTML fallback), `"html"` (raw HTML only), or `"both"` (returns both `body` and `body_html` as separate fields). Each body is truncated at 50,000 chars with explicit `body_truncated` / `body_html_truncated` flags

**Architecture:**

- **Multi-account by parameter, not by process** — every tool takes an `account` string parameter; the `account` enum on each tool schema is patched at runtime in `_get_tools()` from `~/.config/myagent-google/accounts.json`, so the model only ever sees actually-configured accounts. Switching between accounts in a single instruction (e.g., "send a summary from namor5772 to romangroblicki") is one tool call, not two server connections
- **OAuth tokens cached per account** at `~/.config/myagent-google/{account}_token.json`; `_gmail_service(account)` runs the `InstalledAppFlow` once per account on first use (browser opens, user picks the right Google account, token saved), then refreshes silently from the refresh token forever after. Tokens are `chmod 600` automatically by MyAgent after write (the underlying library uses the default umask which is too permissive for credential files)
- **Scope: `gmail.modify`** — covers read, send, draft, label, trash. Does NOT cover permanent delete; trash is recoverable from Gmail's UI for 30 days, permanent delete requires emptying trash via the web UI. Deliberate safety boundary enforced at the OAuth-scope level — even a bug in MyAgent that allowed an unauthorised call to slip through would be rejected server-side with `403 insufficient scope`
- **Five destructive tools gated by modal confirmation** — `gmail_send`, `gmail_reply`, `gmail_send_draft`, `gmail_trash`, `gmail_delete_label` pop a Tk `messagebox.askyesno` dialog showing recipient/subject/preview before proceeding. Click No to cancel; the tool returns `"user denied: ..."` and the agent loop continues without retrying. Works in `--headless` mode because Tk dialogs float as standalone windows even when the main root is withdrawn
- **Per-tool confirmation bypass via Safety dialog** — the Safety dialog now has a "Gmail destructive tools" section listing all five confirmation-requiring tools as checkboxes. Uncheck any of them to bypass its confirmation for the current instruction; the bypass is persisted per-instruction in `agent_instructions.json` under the same `disabled_confirm_patterns` field that holds shell regex bypasses. When a bypass fires at runtime, a `⚠ Gmail confirm bypassed for <tool>` warning appears in the activity output as an audit trail (uses the `warning` queue type, which displays regardless of the Activity checkbox state)
- **`_HAS_GOOGLE` availability flag** mirrors `_HAS_MCP` and `_HAS_OLLAMA`: when the Google API libraries aren't installed, the Google checkbox in the editor is disabled, the GmailMixin methods are graceful no-ops, and behaviour is identical to before this mixin existed

**Setup:**

1. **Install the Google API Python dependencies:**
   ```bash
   pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
   ```
   (Already added to `requirements.txt`, so `pip install -r requirements.txt` on a fresh clone covers it.)

2. **Set up a Google Cloud project and OAuth client:**
   - In Google Cloud Console, create a project (or use existing), enable the **Gmail API**
   - Configure the OAuth consent screen (User Type: External; add yourself as a Test user). Add the `https://www.googleapis.com/auth/gmail.modify` scope explicitly when prompted
   - Create OAuth 2.0 credentials of type **Desktop app**, download the JSON file

3. **Create the MyAgent Google config directory:**
   ```bash
   mkdir -p ~/.config/myagent-google
   mv ~/Downloads/client_secret_*.json ~/.config/myagent-google/oauth_client.json
   ```

4. **List your accounts in `~/.config/myagent-google/accounts.json`:**
   ```json
   {
     "accounts": {
       "namor5772": { "email": "namor5772@gmail.com" },
       "romangroblicki": { "email": "romangroblicki@gmail.com" }
     }
   }
   ```
   The account key (e.g., `namor5772`) is what the agent uses as the `account` parameter; the `email` field is metadata for your reference. Add as many accounts as you want — each gets its own consent flow on first use.

5. **First-use OAuth flow** — When you first run an instruction that calls a Gmail tool, MyAgent opens your default browser to Google's OAuth consent page. Pick the right Google account (use the `prompt=select_account` URL parameter MyAgent passes, which forces the account chooser even when you're already signed in). Grant the requested scopes. The token saves automatically to `{account}_token.json` and is reused forever after — refresh tokens are long-lived.

**Multi-account workflow:**

In an instruction like _"Forward today's unread emails in namor5772 to romangroblicki"_, the agent would:
1. `gmail_search(account="namor5772", q="is:unread newer_than:1d")` — list unread
2. For each: `gmail_read(account="namor5772", message_id=...)` — get content and attachment metadata
3. (Optional) `gmail_get_attachment(account="namor5772", message_id=..., attachment_id=..., save_to="/tmp/x.pdf")` — pull each attachment to disk
4. `gmail_send(account="romangroblicki", to="romangroblicki@gmail.com", subject="...", body="...", body_html="...", attachments=["/tmp/x.pdf"])` — single confirmation dialog per send

The agent never confuses which account owns which token — that's enforced by the `account` parameter being on every tool, not by global state.

**Per-instruction toggle:** The Google checkbox is per-instruction, persisted in `agent_instructions.json` alongside Desktop/Browser/Meta/MCP/Convo. Each saved instruction can independently enable or disable Gmail tools without affecting others.

**Why native instead of MCP:** Trade-off discussion captured in commit history. Short version: native gives tight 16-tool catalog vs MCP's 55+ (matters for Ollama's 32K context), multi-account is one parameter instead of two subprocesses, destructive ops get a real confirmation dialog with per-tool bypass rather than fire-and-forget, OAuth scope is hard-locked to `gmail.modify` so permanent delete is impossible by design, and the OAuth + token plumbing is reusable for future Google services (Calendar, Drive, Sheets) without per-service MCP server setup.

**What the tools deliberately CAN'T do** (safety boundaries you should know about, in order of impact):

- **Permanently delete emails** — OAuth scope is `gmail.modify`, which excludes `users.messages.delete`. Trash (recoverable for 30 days) is the only delete the agent can perform. Permanent purge requires emptying Trash via Gmail's web UI
- **Send from a different `From:` address** (Send-As aliases) — `gmail_send` always sends from the authenticated account; no `from_alias` parameter
- **Settings management** — no tools for filters, vacation responder, signatures, auto-forwarding, delegates, or IMAP/POP settings (Gmail API exposes these but they're rarely managed programmatically — configure once in the web UI)
- **UI-only Gmail features that have no API equivalent** — Undo Send, Schedule Send, Snooze, Smart Compose suggestions, Confidential Mode UI (the API supports confidential mode but it's not exposed here)
- **Cross-account in a single API call** — every Gmail API call is scoped to one authenticated account; multi-account workflows make multiple sequential calls (which the agent does naturally via the `account` parameter)

**Reusing existing shinzo-labs Gmail MCP credentials (optional):** If you previously ran `@shinzolabs/gmail-mcp`, your existing `~/.gmail-mcp/credentials.json` (and `~/.gmail-mcp-*/credentials.json` for additional accounts) contain refresh tokens bound to the same OAuth scopes MyAgent requests. The MyAgent token format is slightly different but the underlying refresh token is interchangeable — re-running OAuth (step 5) is the cleanest path. If you'd rather avoid the consent dance, ask Claude Code to write a migration helper that translates shinzo format to MyAgent format; it's a ~20-line conversion.

#### Proton Mail Integration (Native Bridge Tools)

Native multi-account Proton Mail integration via **Proton Bridge** (Proton's official desktop app that decrypts mail locally and exposes a localhost IMAP + SMTP server), with **sixteen tools** mirroring the Gmail surface 1:1. Implemented in `myagent/protonmail_mixin.py` — no MCP server, no reverse-engineered REST client, no subprocess. Transport is stdlib `imaplib` + `smtplib` + `email`; tools flow through MyAgent's `_get_tools()` and `_execute_tool()` paths exactly like Gmail's.

**Why Bridge instead of a REST API:** Proton doesn't publish a public REST API — their E2E encryption model means decryption only happens client-side. Bridge is the officially supported integration path, used by every third-party mail client (Thunderbird, Outlook, Apple Mail). Reverse-engineered alternatives (e.g. `protonmail-api-client`) talk to Proton's internal web client API and break whenever Proton updates it; Bridge is stable across Proton API changes and authenticates via per-install app-passwords so MyAgent never touches your real Proton login or mailbox password.

**Tool inventory:**

| Tool | Purpose | Confirm? |
|---|---|---|
| `proton_search` | Search messages within a folder by IMAP SEARCH syntax | |
| `proton_read` | Fetch a message with body (text/html/both) + attachments[] metadata | |
| `proton_get_attachment` | Download an attachment to a local file path | |
| `proton_send` | Send a new email (text + optional HTML + optional attachments) | ✅ |
| `proton_reply` | Reply with proper In-Reply-To / References headers | ✅ |
| `proton_create_draft` | Create a draft (text + optional HTML + optional attachments) | |
| `proton_list_drafts` | List drafts | |
| `proton_send_draft` | Send an existing draft | ✅ |
| `proton_trash` | Move to Trash (recoverable from Proton's UI) | ✅ |
| `proton_untrash` | Restore from Trash to INBOX | |
| `proton_list_labels` | List folders (system + user labels under `Labels/`, user folders under `Folders/`) | |
| `proton_create_label` | Create a new folder/label (under `Labels/`, `Folders/`, or top-level) | |
| `proton_delete_label` | Delete a folder/label (removes it AND any messages stored only in it) | ✅ |
| `proton_modify_labels` | Apply or remove a label (additive for `Labels/` destinations, exclusive MOVE for system folders) | |
| `proton_mark_read` | Toggle the `\Seen` flag | |
| `proton_list_threads` | List conversation threads matching a query (IMAP THREAD REFERENCES) | |

**Per-folder UIDs (vs Gmail's global message IDs):** IMAP UIDs are per-folder, so every per-message tool takes a (`folder`, `uid`) pair and bulk ops take `folder` once + `uids: [int]`. Moving a message between folders gives it a fresh per-folder UID — UIDs are monotonically assigned and never reused, even after deletion (RFC 3501). A round-trip INBOX → Trash → INBOX produces three distinct UIDs for the same logical message.

**Content support:**

- **Plain text + HTML emails** — `proton_send`, `proton_reply`, and `proton_create_draft` accept an optional `body_html` parameter. When provided, the message ships as `multipart/alternative` with the plain `body` as the fallback for clients that don't render HTML
- **Outbound attachments** — same three send-style tools accept `attachments: [filepath, ...]`. Combined raw size capped at 20 MB (under SMTP's ~25 MB post-base64 ceiling). MIME types auto-detected via `mimetypes.guess_type`
- **Inbound attachments** — `proton_read` always includes an `attachments[]` array with `filename`, `mime_type`, `size`, `attachment_id` (format `"part:N"` — synthesised from the part's index in the message walk since IMAP has no native attachment-ID concept), `part_index`, `inline` flag. Pass `attachment_id` to `proton_get_attachment(save_to=...)` to download the bytes
- **Body format selection** — `proton_read` accepts `format`: `"text"` (default, plain text or stripped HTML fallback), `"html"` (raw HTML only), or `"both"`. Each body is truncated at 50,000 chars with `body_truncated` / `body_html_truncated` flags

**Four empirical Bridge / dovecot quirks discovered + mitigated (worth knowing):**

1. **SUBJECT search is reliable for single-word substrings, FRAGILE for multi-word substrings against Unicode-containing subjects.** Bridge's tokeniser breaks down on subjects with curly quotes, em-dashes, or accented letters — even when the substring you're searching for is pure ASCII. For example, against a subject `"I also long to be fictional"` (with U+201C/U+201D quotes), `SUBJECT "fictional"` matches but `SUBJECT "be fictional"` returns nothing. The `proton_search` tool description tells the model to use single-word substrings (or AND multiple SUBJECT predicates) for robust matching. A `_uid_search` helper transparently switches to IMAP `CHARSET UTF-8` byte-literal encoding for non-ASCII queries — though Bridge's index may still fail to match Unicode against subject text, so ASCII substrings remain the safer bet
2. **`Labels/X` destinations are ADDITIVE, not exclusive.** Bridge's IMAP MOVE has asymmetric semantics: moving to `Labels/Foo` applies the Foo label but the message STAYS in the source folder (Proton's label model treats labels as additive tags, not containers). Moving from `Labels/Foo` to INBOX REMOVES the Foo label and the message stays where it was. System folder destinations (INBOX, Sent, Trash, Archive, Spam, All Mail) and `Folders/<name>` destinations behave as true exclusive MOVE. The `proton_modify_labels` tool description spells out both branches so the agent knows what to expect
3. **Label removal sometimes leaves a transient new UID in the source** due to eventual-consistency between Bridge's local cache and Proton's server. `do_proton_modify_labels` auto-retries up to 2 times when the source folder is `Labels/X`: snapshots source UIDs before the MOVE, detects any unexpected new UIDs that appear afterward, and re-MOVEs them. The response includes `label_removal_retries: N` (0 = clean first try, >0 = quirk fired and was absorbed transparently). Discovered empirically during TEST3 development; the auto-retry means callers never see the quirk
4. **Search-syntax differences between Bridge and dovecot/cPanel servers** (matters once you add a WebCentral-style IMAP account alongside Bridge). Bridge accepts a bare-token query like `q="invoice"` as a loose full-text search across multiple fields; dovecot (WebCentral) REJECTS the same query with `BAD Unknown argument INVOICE` because the unprefixed token isn't a valid IMAP search keyword. The `proton_search` tool description was updated to tell the model to always wrap tokens in an explicit IMAP search key — `SUBJECT "..."`, `BODY "..."`, `TEXT "..."`, `FROM "..."`, `TO "..."` — which works on BOTH servers and is now the recommended default. Discovered empirically during the WebCentral cross-account test, where the agent's first poll attempt failed on bare-token syntax and self-corrected to the explicit-key form on retry; the tool-description and test-instruction patches eliminate the recovery cycle on future runs

**Architecture:**

- **Per-account Bridge credentials, no OAuth** — `accounts.json` lists one entry per account with `email`, `username`, `app_password` (Bridge-generated), IMAP/SMTP host+port (per-account on Bridge), and optional `ca_cert_path`. IMAP connections are cached per-account; SMTP is opened fresh per send
- **Multi-account by parameter, not by process** — every tool takes an `account` string parameter; the `account` enum on each tool schema is patched at runtime in `_get_tools()` from `accounts.json`. Adding a new account is `accounts.json` edit + MyAgent restart
- **Verified TLS optional** — Bridge's "Export TLS certificates" UI action dumps a `cert.pem` you can point `ca_cert_path` at for `ssl.CERT_REQUIRED` + `check_hostname=True`. Without it, the SSL context falls back to `CERT_NONE` — fine for localhost-only traffic where a MITM would already need code execution on the user's machine. Bridge's cert is bound to `127.0.0.1`, so keep `imap_host`/`smtp_host` as that IP (not `"localhost"`) or hostname verification fails
- **Five destructive tools gated by `_confirm_proton_action`** — `proton_send`, `proton_reply`, `proton_send_draft`, `proton_trash`, `proton_delete_label` pop the same Tk `messagebox.askyesno` dialog as Gmail's, with per-tool bypass via the Safety dialog. Denial returns `"user denied: ..."` so the agent loop continues without retrying
- **Per-tool confirmation bypass via Safety dialog** — the Safety dialog has an "IMAP mail destructive tools" section listing all five confirmation-requiring tools as checkboxes (the bypass applies regardless of which IMAP account routes the call — Proton Bridge, WebCentral, etc.). Same `disabled_confirm_patterns` set as shell regex and Gmail tool bypasses, persisted per-instruction
- **`_HAS_PROTONMAIL` availability flag** is always True on CPython (transport is stdlib), kept for parity with `_HAS_GOOGLE` / `_HAS_MCP`. Bridge presence/availability is detected at first-call time as a connection error, not a startup check — Bridge can restart while MyAgent is running and re-connects work transparently
- **Four colliding helpers renamed `_proton_*`** to avoid MRO shadowing by `GmailMixin`'s identically-named statics (`_format_proton_summary`, `_extract_proton_bodies`, `_extract_proton_attachments`, `_attach_proton_files`). Foundational footgun in mixin-based architectures: shared method-name flat namespace requires defensive prefixing

**Setup:**

1. **Install Proton Bridge** from [proton.me/mail/bridge](https://proton.me/mail/bridge) (requires Mail Plus or higher subscription). Sign into each Proton account you want to use — Bridge generates a unique IMAP/SMTP port pair + app-password per account
2. **(Optional) Export TLS certificates** for verified TLS via Bridge's Settings → Advanced → "Export TLS certificates". This writes `cert.pem` + `key.pem` to a folder of your choice. You only need `cert.pem`; `key.pem` is Bridge's private key and should never be shared
3. **Create the MyAgent Proton config directory:**
   ```bash
   mkdir -p ~/.config/myagent-protonmail
   ```
4. **List your accounts in `~/.config/myagent-protonmail/accounts.json`:**
   ```json
   {
     "accounts": {
       "personal": {
         "email": "you@proton.me",
         "username": "you@proton.me",
         "app_password": "<16-char Bridge token>",
         "imap_host": "127.0.0.1",
         "imap_port": 1143,
         "smtp_host": "127.0.0.1",
         "smtp_port": 1025,
         "ca_cert_path": "/path/to/exported/cert.pem"
       }
     }
   }
   ```
   `chmod 600` the file once filled in (it now holds a working credential). The account key (e.g., `personal`) is what the agent uses as the `account` parameter. Add as many accounts as Bridge has signed in
5. **No first-use OAuth dance** — unlike Gmail, Proton needs nothing extra at first use. As soon as Bridge is running and `accounts.json` is in place, the tools work

**Multi-account workflow:**

Same model as Gmail — the `account` parameter is on every tool. An instruction like _"Find unread mail in `personal`, summarise it, then save the summary as a draft in `work`"_ would:
1. `proton_search(account="personal", folder="INBOX", q="UNSEEN", max_results=10)`
2. `proton_read(account="personal", folder="INBOX", uid=...)` for each
3. `proton_create_draft(account="work", to="you@proton.me", subject="Inbox summary", body="...")`

**Per-instruction toggle:** The IMAP checkbox (formerly labelled "Proton" — relabelled because the same mixin and tools now serve any IMAP/SMTP server, not just Proton Bridge) is per-instruction, persisted in `agent_instructions.json` as `proton: true/false` for backward compatibility, alongside Desktop/Browser/Meta/MCP/Google/Convo. Each saved instruction can independently enable or disable all IMAP-routed mail tools (Proton Bridge + WebCentral + any other configured IMAP account).

**What the tools deliberately CAN'T do** (safety boundaries):

- **Permanently delete emails** — only `proton_trash` is exposed; permanent purge requires emptying Trash via Proton's web UI. Same boundary as Gmail
- **Settings management** — no tools for filters, vacation responder, signatures, or auto-forwarding (configure once in Proton's web UI)
- **Calendar / Drive / VPN** — out of scope; Proton's other products aren't IMAP-exposed
- **Cross-account in a single IMAP session** — each account uses its own Bridge IMAP/SMTP connection, but the `account` parameter on every tool makes multi-account workflows natural

**Test instructions:** Per-mailbox smoke tests live in `agent_instructions.json` (not enumerated here since they reference personal account names + credentials, and rotate as the integration evolves). Useful test shapes to author when validating a new IMAP account: a **read path** test (`proton_list_labels` + `proton_search` INBOX + `proton_read` of a recent message + `proton_list_drafts` to confirm folder auto-discovery resolves to the server's actual drafts folder), a **draft write path** test (`proton_create_draft` + `proton_list_drafts` to verify count delta and folder routing + `proton_read` body round-trip), and where credentials permit, a **cross-account send/receive cycle** (`proton_send` from A to B + poll B's INBOX with `SUBJECT "<unique-marker>"` syntax + `proton_read` to verify body/body_html/from/subject round-trip + `proton_trash` to clean up). Each test should report PASS/FAIL per step and either restore mailbox state or auto-clean its artifacts.

#### Outlook / Microsoft 365 Integration (Native Microsoft Graph Tools)

Native multi-account Outlook integration via the **Microsoft Graph API**, authenticated with **MSAL** (Microsoft's OAuth library), with **sixteen tools** mirroring the Gmail surface 1:1. Implemented in `myagent/outlook_mixin.py` — no MCP server, no IMAP. Tools flow through MyAgent's `_get_tools()` and `_execute_tool()` paths exactly like Gmail's and Proton's.

**Why Graph + OAuth, not IMAP/SMTP:** Microsoft disabled Basic Auth (username + app-password) for personal `outlook.com` IMAP/POP/SMTP in late 2024 — even IMAP now requires OAuth2/XOAUTH2. The modern, supported path is the Microsoft Graph REST API with MSAL OAuth, which maps almost 1:1 onto the Gmail mixin (OAuth dance, per-account token cache, REST calls) rather than the Proton/Bridge IMAP path.

**Tool inventory:**

| Tool | Purpose | Confirm? |
|---|---|---|
| `outlook_search` | Search messages (Graph `$search`; omit query for most-recent) | |
| `outlook_read` | Fetch a message with body (text/html/both) + attachments[] metadata | |
| `outlook_get_attachment` | Download a file attachment to a local path | |
| `outlook_send` | Send a new email (text or HTML + optional attachments) | ✅ |
| `outlook_reply` | Reply with proper conversation threading (Graph `createReply`) | ✅ |
| `outlook_create_draft` | Create a draft (text or HTML + optional attachments) | |
| `outlook_list_drafts` | List drafts | |
| `outlook_send_draft` | Send an existing draft by message ID | ✅ |
| `outlook_trash` | Move to Deleted Items (recoverable from Outlook's UI) | ✅ |
| `outlook_untrash` | Restore from Deleted Items to the Inbox | |
| `outlook_list_labels` | List categories (Outlook's label analogue) | |
| `outlook_create_label` | Create a category (color preset0..preset24) | |
| `outlook_delete_label` | Delete a category from the master list | ✅ |
| `outlook_modify_labels` | Add/remove categories on messages (by **name**, not id) | |
| `outlook_mark_read` | Toggle `isRead` | |
| `outlook_list_threads` | List conversations (grouped by `conversationId`) | |

**How Outlook differs from Gmail (mapping notes):**

- **Labels → categories.** Microsoft Graph has no Gmail-style labels. The closest analogue is *categories* (colored tags managed via `/me/outlook/masterCategories`). Crucially, `outlook_modify_labels` operates on category **display names**, not IDs, because Graph stores categories on a message as a `categories: [name, ...]` array. The tool description spells this out so the model passes names from `outlook_list_labels`, not the ids.
- **Trash → Deleted Items folder.** `outlook_trash` issues a Graph `move` to the well-known `deleteditems` folder rather than toggling a label. Each move yields a *new* message id in the destination (returned as `moved_ids`), so a round-trip Inbox → Deleted Items → Inbox produces three distinct ids for the same logical message (same conceptual gotcha as Proton's per-folder UIDs).
- **Single body, not multipart.** Graph messages have one body (`contentType: html|text`), not Gmail's `multipart/alternative`. When `body_html` is supplied it is sent as the body and the plain `body` is ignored; Graph renders a text fallback itself.
- **Drafts are messages.** A draft IS a message in Graph, so `outlook_send_draft` takes the draft's message id (what `outlook_create_draft`/`outlook_list_drafts` return as `draft_id`).

**Content support:** identical to Gmail/Proton — `format` selection (`text`/`html`/`both`) with 50,000-char truncation flags on `outlook_read`, an always-included `attachments[]` metadata array, and optional outbound `attachments: [filepath, ...]`. **Attachment cap is ~3 MB combined** (lower than Gmail's 20 MB) because Graph's single-request JSON body limit is ~4 MB and base64 inflates raw bytes by ~33%; larger attachments need a Graph upload session, which this tool does not yet implement (the error message says so).

**Architecture:**

- **OAuth via MSAL `PublicClientApplication`** — one app per account, cached in `self._outlook_apps`. First call per account opens the system browser for consent (`acquire_token_interactive` with `prompt="select_account"`); afterwards the refresh token in the per-account cache (`{account}_token.json`, chmod 600) is used silently (`acquire_token_silent`). A 401 forces one token re-acquire + retry.
- **Scopes are `Mail.ReadWrite` + `Mail.Send`** — covers read/send/draft/move/categories/mark-read but NOT permanent delete beyond Deleted Items, mirroring Gmail's deliberate "soft-delete only" boundary.
- **Multi-account by parameter** — every tool takes an `account` string; the `account` enum on each schema is patched at runtime in `_get_tools()` from `accounts.json`. Adding an account is an `accounts.json` edit + restart.
- **Five destructive tools gated by `_confirm_outlook_action`** — `outlook_send`, `outlook_reply`, `outlook_send_draft`, `outlook_trash`, `outlook_delete_label` pop the same Tk `askyesno` dialog as Gmail/Proton, with per-tool bypass via the Safety dialog's "Outlook destructive tools" section (same `disabled_confirm_patterns` set, persisted per-instruction). `outlook_reply` deletes its server-side draft on denial so nothing is left behind.
- **`_HAS_OUTLOOK` availability flag** mirrors `_HAS_GOOGLE`/`_HAS_PROTONMAIL`: missing `msal` → the Outlook checkbox is disabled and every method is a no-op.
- **All helpers prefixed `_outlook_`** — `OutlookMixin` sits after `GmailMixin`/`ProtonMailMixin` in the App's MRO, so every non-shared helper is prefixed to avoid the flat-namespace mixin shadowing documented for Proton.

**Setup:**

1. **Register an Azure app** (free) to get a client ID — the Microsoft equivalent of Gmail's `oauth_client.json`:
   - Go to [Azure Portal → App registrations → New registration](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade).
   - Supported account types: **"Personal Microsoft accounts only"** (for `outlook.com`/`hotmail`/`live`) or "...and organizational" if you also use a work/school account.
   - Under **Authentication → Add a platform → Mobile and desktop applications**, add the redirect URI `http://localhost`, and set **"Allow public client flows" = Yes**.
   - Under **API permissions → Add → Microsoft Graph → Delegated**, add `Mail.ReadWrite` and `Mail.Send` (and `offline_access`). No admin consent needed for personal accounts.
   - Copy the **Application (client) ID** from the app's Overview page.
2. **Install the dependency:** `pip install msal` (requests is usually already present).
3. **Create the config directory and drop in your client ID:**
   ```bash
   mkdir -p ~/.config/myagent-msmail
   ```
   `~/.config/myagent-msmail/msal_app.json`:
   ```json
   { "client_id": "<your-application-client-id>", "authority": "https://login.microsoftonline.com/consumers" }
   ```
   (Use `/consumers` for personal-only, or `/common` to accept both personal and work/school accounts. You can also set `OUTLOOK_CLIENT_ID` as an env var instead of the file.)
4. **List your accounts** in `~/.config/myagent-msmail/accounts.json`:
   ```json
   { "accounts": { "outlook": { "email": "grobliro@outlook.com" } } }
   ```
   The account key (e.g. `outlook`) is what the agent passes as the `account` parameter.
5. **First use** opens a browser to sign in and consent; the token cache is then reused silently across runs.

**Per-instruction toggle:** The **Outlook** checkbox sits alongside Desktop/Browser/Meta/MCP/Google/IMAP/Convo in the instruction editor, persisted in `agent_instructions.json` as `outlook: true/false` and in `agent_state.json`.

**What the tools deliberately CAN'T do** (safety boundaries): permanently delete (only soft-delete to Deleted Items, via the `Mail.ReadWrite` scope), manage settings/rules/signatures, or touch Calendar/OneDrive/Teams (out of scope; the OAuth/token plumbing is reusable for those later, same as Gmail's design).

#### Conversational Mode

Smaller open-weights models (Qwen3:32B, Llama 3.x, gpt-oss) don't reliably follow "ALWAYS call `user_prompt` after every response" meta-rules — they often treat task completion as their own permission to end the turn, regardless of what the instruction says. The pre-existing `user_prompt` nudge in `stream_worker` only kicks in once the model has *already* called `user_prompt` 2+ times to "establish" chatbot mode, which means it never helps when the model never starts.

The **Convo checkbox** in the instruction editor enables a stronger fallback: when the model ends a turn without calling `user_prompt` AND `conversational_enabled` is on, MyAgent itself invokes `do_user_prompt` directly, appends the user's response as a regular user message, and continues the loop. The model's compliance no longer matters — the chatbot loop is enforced at the agent-loop level.

**Behaviour summary:**

| Convo state | Behaviour when model ends turn without calling `user_prompt` |
|---|---|
| **Off (default)** | The existing 2+-calls nudge fires only if the model has already called `user_prompt` twice in the conversation — otherwise the loop exits cleanly (single-shot task semantics) |
| **On** | MyAgent invokes `do_user_prompt` directly. The user's reply is appended as a user message and the loop continues. Empty / `quit` / `exit` / `stop` replies end the conversation cleanly |

**When to use it:**
- Long-running chatbot conversations with Ollama models (especially Qwen3, gpt-oss, Llama 3.x)
- Any instruction where the agent should always wait for the next user input rather than terminate
- Combined with MCP (e.g. filesystem) for an open-ended chatbot that can take real actions

**When to leave it off:**
- Single-shot task instructions (e.g. "Search the web for X and summarise") where ending on completion is correct
- Frontier cloud models (Claude, GPT-5, Gemini 2.5+) that already follow always-call-user_prompt rules reliably — Convo mode is unnecessary overhead there but not harmful

The Convo checkbox is per-instruction, persisted in `agent_instructions.json` alongside the other tool toggles. The two recovery layers (existing nudge + Convo mode) coexist: large frontier models follow the meta-rule and trigger neither; mid-tier models drift after a while and the nudge catches them; small models that don't even try get the Convo-mode hard fallback.

#### Parallel Tool Execution

When Claude requests multiple tools in a single turn, MyAgent automatically classifies each tool as **parallel-safe** or **sequential** and executes them accordingly:

**Parallel-safe tools** (`web_search`, `fetch_webpage`, `csv_search`, `read_document`, `get_skill`) run concurrently via `ThreadPoolExecutor` (MyAgent keeps local `web_search`/`fetch_webpage` for Gemini). SelfBot's parallel-safe set is `csv_search` and `get_skill` (since web tools are server-side). A status message ("Running N tools in parallel...") appears in the Activity output when multiple parallel tools fire.

**Sequential tools** (all desktop, browser, `run_command`, and `user_prompt` tools) run one at a time in their original order, since they interact with shared state (screen, browser session, filesystem, user attention).

Results are slotted back into their original API-requested order regardless of execution order, so the model always sees responses in the sequence it expects. Tool dispatch is handled by the `_execute_tool()` helper method, which is thread-safe for parallel-safe tools.

#### Skills System

Shared with SelfBot — both apps read from the same `skills.json` file. The three-mode system (disabled, enabled, on-demand) works identically. See the SelfBot.py Skills System section above for full details.

The **Skills** button is located in the **Instruction Editor** (not on the main window), since skill modes are saved and restored per-instruction. Opening the Skills Manager from the editor makes it clear that the skills configuration is part of the instruction's environment.

#### Image Attachments

- Image management is integrated into the **Instruction Editor** — click **Attach Images** to select files (PNG, JPG, JPEG, GIF, WEBP)
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

Six checkboxes on the main window control what is shown in the output display. All persist across sessions via `agent_state.json`. Most default to **off** on first run; **Diag** defaults to **on** (toggle off when not actively debugging coordinate issues).

| Checkbox | Default | What it controls |
|---|---|---|
| **Debug** | off | Full API payload JSON with each request |
| **Tool Calls** | off | Tool name, call ID, and input arguments in teal `--- TOOL CALL ---` blocks |
| **Activity** | off | Tool activity status lines (e.g., "Searching: ...", "Fetching: ...", "Taking screenshot...") |
| **Show Thinking** | off | Extended thinking blocks in amber/gold italic text |
| **Save Thinking** | off | Preserve thinking blocks in saved chat JSON for reasoning continuity on reload |
| **Diag** | on | `[DIAG capture]` and `[DIAG click]` lines showing the full coordinate-mapping trail (display rects, physical/logical/sent_to_model dims, scale, offset, raw input, computed screen pixels). Independent of Debug — Diag focuses purely on the desktop coordinate pipeline so you can verify clicks are landing where the model intended |

Desktop/Browser tool toggles, Safety, and Skills are managed per-instruction inside the Instruction Editor.

The **Call #N** counter badges are hidden only when all of Activity, Debug, Tool Calls, and Diag are unchecked.

#### API Cost Tracking

MyAgent tracks and displays **real-time API costs** for all three cloud providers (Anthropic, OpenAI, Gemini) during agentic runs. Ollama is local inference — no cost is incurred and no cost line is emitted (the `OLLAMA_PRICING` table is deliberately empty, so `_get_pricing` returns `None` and the accumulator silently skips — keeping the activity pane clean for local runs). After each cloud-provider API call, a blue cost line appears in the output window (gated by the **Activity** checkbox) showing per-call cost, running total, and token breakdown:

```
  $0.0023 this call  |  $0.0023 total  (in:312  out:45)
  $0.0051 this call  |  $0.0074 total  (in:498  out:112  cache_read:312)
```

**How it works:**
1. Each provider's streaming method extracts token usage data from the API response:
   - **Anthropic** — `final_message.usage` provides `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`
   - **OpenAI** — `stream.get_final_response().usage` provides `input_tokens`, `output_tokens` (reasoning tokens included in output)
   - **Gemini** — Last streaming chunk's `usage_metadata` provides `prompt_token_count`, `candidates_token_count`
2. Token counts are multiplied by per-model pricing from hardcoded tables in `myagent/constants.py` (`ANTHROPIC_PRICING`, `OPENAI_PRICING`, `GEMINI_PRICING`)
3. Costs accumulate across all API calls within a single agentic run

**Pricing lookup** — Model names are matched by **longest prefix**. For example, `claude-opus-4-6-20250414` matches the `claude-opus-4-6` entry ($5/$25) rather than the shorter `claude-opus-4` entry ($15/$75). Models with no matching prefix show no cost line. See `MyAgent_Pricing.txt` for the complete pricing reference.

**Key details:**
- Cost lines are styled in blue monospace (`cost_info` tag) and appear after each **Call #N** counter
- Anthropic cost lines include cache token breakdowns when prompt caching is active
- Cost data is **not** stored in the `.json` chat file (it's display-only), but it **is** captured in the `.txt` export since that file is a verbatim copy of the output window
- If the API stream is interrupted (STOP button or incomplete stream), no cost line appears for that call
- Cost precision adapts: 4 decimal places when total is under $0.01, 2 decimal places above

**Persistent cost log (`APICostLog.txt`)** — Beyond the live display, MyAgent appends the **final cumulative cost of each run** to `APICostLog.txt` in the project root. The write happens once, when the agentic loop ends (`_log_api_cost` in `streaming_mixin.py`), so it fires in **both GUI and headless (`--headless`) runs** — making it the durable cost record for unattended/scheduled jobs that have no output window to read. One line per run:

```
2026-06-08 11:14:12;Anthropic;claude-sonnet-4-6;0.5880
2026-06-08 11:22:35;Anthropic;claude-sonnet-4-6;0.6219
```

- **Format** — `{timestamp};{provider};{model};{cost}`, semicolon-delimited. The `;` separator (rather than `,`) keeps the fields unambiguous even if a model name itself contains a comma. The cost is a plain 4-decimal number (no `$`), so the file imports cleanly into a spreadsheet for summing.
- **Location** — the repo root, via the same `_BASE_DIR` anchor used for `agent_instructions.json` / `skills.json` (derived from the package's `__file__`, **not** the working directory). The path resolves correctly on any platform and regardless of where the app is launched from — including scheduled launchd / Task Scheduler jobs that run with an arbitrary cwd.
- **Only logs when relevant** — runs that recorded no priced cost write nothing: Ollama (free), a model with no matching pricing prefix, or a STOP before the first API result. This mirrors the live display's skip behaviour.
- **Gitignored** — the log is per-machine, append-only runtime output, so it is excluded from git; otherwise it would dirty the working tree on every run and conflict when syncing the branch across machines.
- **Best-effort** — a write failure emits a one-line warning but never interrupts the run; observability must never break the thing it observes.

#### LaTeX to Unicode Conversion

Assistant responses containing LaTeX math notation are automatically converted to Unicode after each streaming segment completes. The raw LaTeX streams in real-time for visual feedback, then a post-processing pass converts it in-place. All common delimiter styles are handled:

| Delimiter | Style | Example |
|---|---|---|
| `\( ... \)` | Inline (OpenAI) | `\(x^2\)` → `x²` |
| `$ ... $` | Inline (Gemini) | `$\alpha + \beta$` → `α + β` |
| `\[ ... \]` | Display (OpenAI) | Delimiters stripped |
| `$$ ... $$` | Display (Gemini) | Delimiters stripped |

Conversions include: superscripts (`x^2` → `x²`), subscripts (`x_0` → `x₀`), Greek letters (`\alpha` → `α`), operators (`\times` → `×`, `\le` → `≤`, `\infty` → `∞`), functions (`\sin` → `sin`), fractions (`\frac{a}{b}` → `a/b`), set notation (`\in` → `∈`), arrows (`\to` → `→`), and more. Unrecognised `\command` patterns have their backslash stripped as a fallback.

#### Safety — Deselectable Confirm Patterns

The **Safety** button (inside the Instruction Editor, next to the Skills button) opens a dialog listing all `COMMAND_CONFIRM` patterns as checkboxes, plus a "Gmail destructive tools" section listing each Gmail tool that pops a confirmation:

- **Checked** (default) — the pattern requires a confirmation dialog before execution, as normal
- **Unchecked** — the confirmation dialog is bypassed; the command runs immediately and a `⚠ Confirm bypassed (pattern: ...)` warning is displayed in the output window

The bypass warning always appears regardless of the Activity checkbox state. Disabled patterns are saved per-instruction in `agent_instructions.json` (so different tasks can have different safety overrides). The dialog's position/size is persisted in `agent_state.json` across restarts. The Safety button label shows a count when patterns are bypassed (e.g., `Safety (3 bypassed)`). The button was previously labelled `PS Safety` on Windows and `Shell Safety` on macOS — renamed to just `Safety` since the dialog now covers both shell-command patterns and Gmail destructive ops.

#### App State Persistence

- **Multi-instance state** — Each instance claims the lowest available instance number via lock files (`agent_lock_N.lock`). Instance 1 saves to `agent_state.json`, instance 2+ to `agent_state_N.json`. All settings (provider, model, geometry, dialog positions, display checkboxes) are independent per instance. Stale locks from crashed processes are detected via Windows `OpenProcess` with executable name verification (or `os.kill` + `ps` on macOS) — confirms the PID belongs to a running MyAgent.py process, not a recycled PID from an unrelated process. The title bar shows `My Agent (N)` for instance 2+
- Provider, last-used instruction name, model, temperature, thinking settings, display checkbox states (Debug, Tool Calls, Activity, Show Thinking, Save Thinking), main window geometry, and dialog geometries are saved per instance
- On startup, the app restores all settings and the last instruction (including its images, Desktop/Browser/Meta toggles, provider, and model parameters) automatically. If the saved model doesn't exist in the saved provider's model list (e.g., provider/model mismatch from a corrupted state file), it falls back to the first available model for that provider
- **Persistent dialog geometry** — The **Instruction Editor**, **Agent Request** (user_prompt), **Command Confirm**, **Safety**, and **Skills Manager** dialog windows all remember their size and position across sessions. Resizing or moving any dialog persists to the instance's state file and is restored the next time that dialog is opened. All dialogs use a withdraw/deiconify pattern to prevent the window manager from overriding saved positions. The periodic auto-save (every 5 seconds) captures **live geometry** from all currently open dialogs, so positions are saved even if the app is closed without closing dialogs first
- **Multi-monitor geometry persistence** — Window and dialog geometries are stored **per monitor configuration** in `agent_state.json` under a `geometries` dict keyed by the current monitor layout (detected via Win32 `EnumDisplayMonitors` on Windows, CoreGraphics `CGGetActiveDisplayList` on macOS). Switching between different setups (e.g., docked with dual monitors vs undocked laptop) automatically restores the correct positions for each configuration. Works with any number of monitors in any arrangement on both platforms
- **Geometry sanitization** — All persisted window and dialog geometries (main window, editor, prompt dialog, confirm dialog, Safety dialog, Skills Manager dialog) are validated on restore via `_sanitize_geometry()` against the full virtual desktop bounds spanning all monitors (Win32 `GetSystemMetrics` on Windows, CoreGraphics `CGDisplayBounds` on macOS). Windows that are too small (below 200x150), positioned entirely off-screen, or have fewer than 50 visible pixels on any monitor are reset to defaults. This prevents windows from becoming invisible after monitor changes or corrupted state files. Old state files with flat geometry fields are automatically migrated to the per-config format on first load

#### Rate-Limit Retry

API calls automatically retry up to 10 times on transient errors with exponential backoff. Rate-limit errors (HTTP 429) use backoff capped at 60 seconds. Overload errors (HTTP 529) use backoff capped at 90 seconds. Retry status messages appear in the output as grey italicised lines.

**OpenAI stream timeout** — The OpenAI client is configured with a 120-second read timeout (`httpx.Timeout(600.0, connect=10.0, read=120.0)`). If no data arrives for 2 minutes during streaming, the connection is aborted and retried. This prevents the app from hanging indefinitely on unresponsive models. Timeout errors (`APITimeoutError`) are retried immediately (no backoff) since the issue is typically a dropped connection rather than server overload.

#### Graceful Shutdown

Closing the window stops the agentic loop, waits for any in-flight API streaming to finish (polling every 200ms), auto-saves the chat, cleans up any browser connection, then destroys the window. `SIGINT` (Ctrl+C) is suppressed — the only way to stop is via the STOP button or closing the window.

### UI Layout

The window is 1050x930 (default). Grid layout with 4 rows:

| Row | Contents |
|---|---|
| **Row 0** | Chat toolbar: START button, STOP button, Instruction button, model info label, Save Chat as entry (fills remaining space) |
| **Row 1** | Chat display: read-only text area with scrollbar, colour-coded output |
| **Row 2** | Checkbox row: Debug, Tool Calls, Activity, Show Thinking, Save Thinking, Diag |

**Colour coding:** User/instruction text in blue, agent responses in green, errors in red, tool activity in grey italics, cost tracking in blue monospace, debug payloads in amber monospace, tool call details in teal monospace, call counters as white-on-red badges, thinking blocks in gold italic on pale yellow.

### Key Differences from SelfBot.py

| Aspect | SelfBot.py | MyAgent.py |
|---|---|---|
| **Architecture** | Single-file (~4,100 lines) | Modular mixin package (~6,400 lines across 17 files in `myagent/`) |
| **Paradigm** | Interactive chatbot — user sends messages, gets replies | Autonomous agent — configure a task, press START, observe |
| **User input** | Multi-line text input field for typing messages | No input field — task is defined via Instruction Editor; mid-task input via `user_prompt` tool dialog |
| **Controls** | Send button (Enter key) | START / STOP buttons |
| **Conversation** | Multi-turn back-and-forth with user | Single task instruction, then autonomous tool-use loop |
| **Multi-instance** | Yes — two instances can self-chat autonomously | Yes — unlimited instances with independent state via lock files |
| **System prompt editor** | Full editor with save/load/delete/apply | No user-facing editor — system prompt is built internally |
| **Task config** | System prompts (reusable prompt text) | Agent Instructions (reusable task descriptions with embedded images) |
| **State file** | `app_state.json` / `app_state_2.json` | `agent_state.json` / `agent_state_N.json` (per instance) |
| **Instruction file** | `system_prompts.json` | `agent_instructions.json` |
| **Chat loading** | Save and load chats | Save only (no load-back into UI) |
| **API providers** | Anthropic only | Anthropic + OpenAI + Gemini + Ollama (switchable via Provider combobox) |
| **Window title** | "Claude SelfBot" | "My Agent" with provider/model info in title bar |

### Running

```bash
# Activate the virtual environment
source .venv/Scripts/activate   # Windows (Git Bash)
source .venv/bin/activate       # macOS

# Run the application
python MyAgent.py
```

Or double-click `LaunchMyAgent.bat` on Windows, or the `My Agent.app` desktop shortcut on macOS (each click launches a new instance). The `.command` and `.sh` launchers are also available.

### Architecture

MyAgent uses a **mixin-based modular architecture**. The `App` class in `MyAgent.py` (~170 lines) inherits from 19 mixin classes in the `myagent/` package, each grouping related methods by concern. Constants and tool schemas live in `myagent/constants.py`; helper classes in `myagent/helpers.py`. The `__init__` method and entry point remain in `MyAgent.py`. All mixins share state through `self.*` — no inter-mixin imports are needed; cross-mixin method calls resolve through Python's MRO (Method Resolution Order).

#### Module Breakdown

| Module | Lines | Responsibility |
|---|---:|---|
| **`MyAgent.py`** | 257 | Entry point: DPI setup, `App` class with `__init__`, mixin inheritance, argparse CLI |
| **`myagent/constants.py`** | 1477 | Tool schemas (`TOOLS`, `META_TOOLS`, `DESKTOP_TOOLS`, `BROWSER_TOOLS`, `GMAIL_TOOLS`, `MCP_TOOLS` — runtime-populated), safety patterns (`COMMAND_BLOCKED`, `COMMAND_CONFIRM`), model constants, API pricing tables (`ANTHROPIC_PRICING`, `OPENAI_PRICING`, `GEMINI_PRICING`, `OLLAMA_PRICING` — empty, local is free), Ollama prefix lists (`OLLAMA_THINKING_PREFIXES`, `OLLAMA_VISION_PREFIXES`), `OLLAMA_NUM_CTX_CAP` KV-cache ceiling (env-overridable), MCP and Google feature flags (`_HAS_MCP`, `_HAS_GOOGLE`), `MCP_SERVERS_PATH`, `MCP_NAME_SEP`, file paths, default prompts |
| **`myagent/helpers.py`** | 44 | `HTMLTextExtractor` (HTML→text), `extract_text_from_html()`, `_ToolBlock` (provider-neutral tool wrapper) |
| **`myagent/ui_mixin.py`** | 592 | `setup_ui()`, model/provider/thinking widget handlers, `_update_title()` |
| **`myagent/state_mixin.py`** | 479 | Instance lock management, multi-monitor geometry detection, state persistence (`_save_last_state`, `_load_last_state`), auto-launch |
| **`myagent/instructions_mixin.py`** | 631 | Instruction CRUD, the Instruction Editor Toplevel dialog, `do_manage_instructions()` tool |
| **`myagent/skills_mixin.py`** | 358 | Skills CRUD, Skills Manager dialog, `_build_system_prompt()`, `do_manage_skills()` and `do_run_instruction()` tools |
| **`myagent/streaming_mixin.py`** | 895 | `stream_worker()` (the agentic loop), `_execute_tool()` (tool dispatcher), `_get_tools()`, `_get_pricing()` (cost lookup), message translation (`_messages_to_responses`, `_tools_to_responses`) |
| **`myagent/anthropic_mixin.py`** | 148 | `_stream_anthropic_call()` — Anthropic API streaming with server-side tools, thinking, and usage extraction |
| **`myagent/openai_mixin.py`** | 437 | `_stream_responses()`, `_stream_responses_call()`, OpenAI model detection helpers, usage extraction, `_fetch_models_for_provider()` |
| **`myagent/gemini_mixin.py`** | 597 | `_stream_gemini_call()`, `_messages_to_gemini()`, `_tools_to_gemini()`, `_clean_schema_for_gemini()` (sanitizes tool schemas for Gemini's strict `Schema` validator — drops unsupported JSON-Schema fields and blank/empty `enum` values), Gemini coordinate hints, usage extraction |
| **`myagent/ollama_mixin.py`** | 470 | `_stream_ollama_call()` (native `/api/chat` streaming with `think` flag, tool-capability gating, Qwen3 `<message>` wrapper stripping), `_messages_to_ollama()`, `_tools_to_ollama()`, `_fetch_ollama_models()`, `_get_ollama_model_caps()` (caches per-model `/api/show` result for capabilities + `context_length`), `_is_ollama_thinking_model()`, `_is_ollama_vision_model()` |
| **`myagent/mcp_mixin.py`** | 570 | MCP (Model Context Protocol) client — `_connect_mcp_servers()`, `_disconnect_mcp_servers()`, `_refresh_mcp_tools()`, `do_mcp_call()`. Runs a dedicated asyncio loop in a background thread (MCP SDK is async-only), holds all server connections inside one `AsyncExitStack`, augments subprocess PATH for macOS GUI launches, substitutes `${RANDOM_PORT}` placeholders for multi-instance support |
| **`myagent/gmail_mixin.py`** | 906 | Native multi-account Gmail integration — 16 tools (search/read/send/reply/draft/trash/label/attachment), per-account OAuth via `InstalledAppFlow`, token cache at `~/.config/myagent-google/`, `_confirm_gmail_action()` modal Tk confirmation dialog, account enum patched at runtime in `_get_tools()`. `_HAS_GOOGLE` feature-flag gating |
| **`myagent/protonmail_mixin.py`** | 1206 | Native multi-account Proton Mail integration via Proton Bridge over stdlib IMAP+SMTP — 16 tools mirroring the Gmail surface 1:1. Per-account credentials in `~/.config/myagent-protonmail/accounts.json`, optional `ca_cert_path` for verified TLS (Bridge cert export), `_confirm_proton_action()` modal Tk confirmation dialog, account enum patched at runtime in `_get_tools()`. `_uid_search` helper switches to `CHARSET UTF-8` for non-ASCII queries; `do_proton_modify_labels` auto-retries Bridge's label-removal eventual-consistency quirk and surfaces `label_removal_retries: N` in the response. Four helpers (`_format_proton_summary`/`_extract_proton_bodies`/`_extract_proton_attachments`/`_attach_proton_files`) explicitly prefixed to avoid MRO shadowing by `GmailMixin`'s identically-named statics. `_HAS_PROTONMAIL` feature-flag gating |
| **`myagent/outlook_mixin.py`** | 760 | Native multi-account Outlook / Microsoft 365 integration via the Microsoft Graph API — 16 tools mirroring the Gmail surface 1:1. OAuth through MSAL (`PublicClientApplication`, interactive browser flow + silent refresh), per-account token cache at `~/.config/myagent-msmail/{account}_token.json`, Azure client ID in `msal_app.json`, account enum patched at runtime in `_get_tools()`. Gmail "labels" map to Outlook "categories" (by display name), trash maps to the Deleted Items folder. `_confirm_outlook_action()` modal Tk confirmation; all helpers prefixed `_outlook_` to avoid MRO shadowing. `_HAS_OUTLOOK` feature-flag gating |
| **`myagent/document_mixin.py`** | 280 | Local document reader — single `read_document(path, max_chars?, pages?)` tool that extracts text from PDF (via `pypdf`), DOCX (via `python-docx`), HTML (via `extract_text_from_html`), and most plain-text formats (`.txt`/`.md`/`.json`/`.yaml`/`.csv`/`.log`/source code). Provider-agnostic: pairs with `gmail_get_attachment` / `proton_get_attachment` / `fetch_webpage` / any other path-producing tool. PDF metadata extraction (title/author/dates/producer), DOCX metadata + paragraph/table counts, encrypted-PDF detection with empty-password fallback, per-page error isolation. `_HAS_PYPDF` / `_HAS_DOCX` feature-flag gating |
| **`myagent/desktop_mixin.py`** | 730 | All `do_*` desktop methods (screenshot, mouse, keyboard, clipboard, OCR, window management), `KNOWN_APPS` |
| **`myagent/browser_mixin.py`** | 272 | `_ensure_browser()`, `_cleanup_browser()`, all `do_browser_*` methods (Playwright CDP) |
| **`myagent/safety_mixin.py`** | 466 | `_start_agent()`, `_stop_agent()`, Safety dialog, command safety checks, `_request_confirmation()`, `do_user_prompt()`, `run_powershell()`, `search_web()`, `fetch_url()` |
| **`myagent/chat_mixin.py`** | 332 | Chat save/serialize, image attachment/compression, LaTeX→Unicode post-processing |
| **`myagent/event_loop_mixin.py`** | 252 | `check_queue()` (main event loop with cost display handler), `_on_close()`, `_finish_close()` |

**Total: ~12,780 lines across 22 files** (the original single-file was ~6,200 lines).

#### How the Mixin Pattern Works

```python
# MyAgent.py — the App class inherits from all 19 mixins (Outlook added in latest)
class App(UIMixin, StateMixin, InstructionsMixin, SkillsMixin,
          StreamingMixin, AnthropicMixin, OpenAIMixin, GeminiMixin,
          OllamaMixin, MCPMixin, GmailMixin, ProtonMailMixin,
          DocumentMixin, DesktopMixin, BrowserMixin, SafetyMixin,
          ChatMixin, EventLoopMixin):
    def __init__(self, root, launch_instruction=None, headless=False):
        # ... initializes all shared state (self.queue, self.messages, etc.)
```

Every mixin method becomes a method on `App` through inheritance. When `_execute_tool()` (in `streaming_mixin.py`) calls `self.do_screenshot()`, Python's MRO resolves it to `DesktopMixin.do_screenshot()` — no cross-module imports needed. This means method bodies are identical to the original single-file version; only the physical file location changed.

#### Adding a New Tool

Adding a new tool to MyAgent requires changes in up to 4 files:

1. **Schema** — Add the tool's JSON schema dict to the appropriate list (`TOOLS`, `DESKTOP_TOOLS`, `BROWSER_TOOLS`, or `META_TOOLS`) in `myagent/constants.py`
2. **Dispatch** — Add an `elif block.name == "new_tool":` branch in `_execute_tool()` in `myagent/streaming_mixin.py`
3. **Implementation** — Add a `do_new_tool()` method in the appropriate mixin (e.g., `desktop_mixin.py` for desktop tools, `browser_mixin.py` for browser tools, `safety_mixin.py` for system tools)
4. **Parallel safety** (optional) — Add the tool name to `PARALLEL_SAFE_TOOLS` in `constants.py` if it is thread-safe and stateless

#### Key Architecture Details

- **Threading** — API calls run in a background daemon thread (`stream_worker` in `streaming_mixin.py`) to keep the UI responsive. A `queue.Queue` passes events (text deltas, thinking deltas, call counters, tool info, errors, completion) back to the main thread, polled every 50ms via `check_queue()` in `event_loop_mixin.py`. An `_ensure_newline()` helper in `chat_mixin.py` guarantees each new output block starts on a fresh line
- **Multi-Provider Support** — The internal message format stays Anthropic-style; translation to/from other formats happens at the API boundary. OpenAI translation via `_messages_to_responses()` / `_tools_to_responses()` in `streaming_mixin.py`; Gemini translation via `_messages_to_gemini()` / `_tools_to_gemini()` in `gemini_mixin.py`. The `_ToolBlock` wrapper (in `helpers.py`) normalises OpenAI/Gemini dict-based tool responses to match Anthropic's `.name`/`.id`/`.input` interface, so `_execute_tool()` works identically for all providers
- **Agentic Loop** — `stream_worker()` in `streaming_mixin.py` runs a `while True:` loop that dispatches to `_stream_anthropic_call()`, `_stream_responses_call()`, or `_stream_gemini_call()` (each in their own mixin), processes the response, executes any requested tools via `_execute_tool()`, appends results, and loops again. Exits on `end_turn` or when `stop_requested` is set via the STOP button
- **Parallel Tool Execution** — When Claude requests multiple tools in one turn, `stream_worker()` partitions them into parallel-safe (`csv_search`, `read_document`, `get_skill`, plus `web_search`/`fetch_webpage` for Gemini) and sequential (everything else). Parallel-safe tools run concurrently via `ThreadPoolExecutor`; sequential tools run one at a time. Results are placed into a pre-allocated list indexed by original position, preserving API-expected ordering
- **Persistence** — JSON-based storage: `agent_instructions.json` for the instruction library (with embedded images, all six tool toggles — Desktop/Browser/Meta/MCP/Google/Convo — provider, model parameters, skill modes, and Safety overrides), `.json` + `.txt` files in `saved_chats/` for completed runs, `agent_state.json` / `agent_state_N.json` for per-instance preferences (including the `diag_enabled` toggle) and dialog geometries, `skills.json` (shared with SelfBot) for the skills library, and `mcp_servers.json` for per-user MCP server configuration (gitignored)
- **Per-display coordinate state** — `_display_states[N]` and `_display_images[N]` track each display's most-recent capture (full or region) for `mouse_click` and `find_element` lookups; `_display_full_states[N]` and `_display_full_images[N]` track each display's most-recent FULL display capture for region screenshot conversions. The two-dict design prevents chained region screenshots from drifting through stacked offsets while still letting clicks reference whatever the model most recently saw of each display
- **Command Safety** — Two-tier regex-based guardrail system (patterns in `constants.py`, checks in `safety_mixin.py`), plus a Safety dialog for selectively bypassing individual confirm patterns (shell-command regexes plus per-Gmail-tool bypasses). Confirmation dialogs are dispatched to the main tkinter thread via `root.after()` while the worker thread waits on a `threading.Event`
- **Rate-Limit Retry** — Exponential backoff in the provider-specific streaming methods handles HTTP 429 and 529 errors with up to 10 retries. Rate-limit backoff capped at 60s; overload backoff capped at 90s
- **Auto-Save & Graceful Shutdown** — `_periodic_save()` (in `state_mixin.py`) runs every 5 seconds and triggers auto-save when new messages are detected. `_on_close()` / `_finish_close()` (in `event_loop_mixin.py`) stop the agentic loop, wait for streaming to finish, save state and chat, clean up browser connections, then destroy the window

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
source .venv/Scripts/activate   # Windows (Git Bash)
source .venv/bin/activate       # macOS

# Run the application
python Account_Activity_WBC.py
```

---

## CSVEditor.py — Lightweight CSV Editor

A simple desktop CSV editor built with tkinter. Open, edit, filter, and save CSV files with a spreadsheet-style interface using a `ttk.Treeview` widget.

### Features

- **Open and save CSV files** — Open any CSV file (auto-detects UTF-8 with BOM), edit in-place, or Save As to a new file
- **Inline cell editing** — Double-click any cell to edit its value directly in the treeview
- **Row operations** — Insert Row Above, Insert Row Below, Copy Row, and Delete Row buttons on the toolbar
- **3 independent filters** — Three filter rows, each with column and value comboboxes. Filters are ANDed together so you can narrow down by up to 3 columns simultaneously. A "Show All" button clears all filters. Filter status shows "Showing N of M rows (K filters active)"
- **Date sorting** — A "Sort by Date" toggle button sorts rows by a column named "Date", auto-detecting common date formats (dd/mm/yyyy, yyyy-mm-dd, mm/dd/yyyy, etc.). Disabled when no Date column exists
- **Unsaved changes tracking** — The title bar and status bar show a `*` indicator when changes are unsaved. Closing or opening a new file prompts to save
- **Styled display** — Light blue row background and light yellow column headings using the clam ttk theme
- **State persistence** — Window geometry, last opened file path, all 3 filter states, and date sort toggle are saved to `csv_editor_state.json` and restored on next launch

### UI

A compact tkinter window (default 1000x600) with:

| Control | Description |
|---|---|
| **Open CSV** | Open a CSV file |
| **Save** | Save to the current file (or Save As if no file loaded) |
| **Save As…** | Save to a new file path |
| **Insert Row Above/Below** | Insert an empty row relative to the selection (inserts at top/end if no selection) |
| **Copy Row** | Duplicate the selected row below it |
| **Delete Row** | Remove the selected row |
| **Filter 1/2/3** | Three independent column + value combobox pairs to filter visible rows (ANDed) |
| **Show All** | Clear all active filters |
| **Sort by Date** | Toggle date-column sorting on/off |
| **Status bar** | Shows filename, modification indicator, row count, and column count |

### Architecture

**Single class design** — Same as the other apps: the `App` class contains all UI, file I/O, editing, filtering, and persistence logic in a single file (~520 lines).

- **Treeview-based spreadsheet** — Uses `ttk.Treeview` with `show="headings"` to display the CSV as a sortable, scrollable table with horizontal and vertical scrollbars
- **Visible index mapping** — `_visible_indices` maps tree positions to real row indices in `self.rows`, so row operations work correctly even when a filter is active
- **No threading** — All operations are synchronous (file I/O is fast for CSV files), so no background threads are needed

### Running

```bash
# Activate the virtual environment
source .venv/Scripts/activate   # Windows (Git Bash)
source .venv/bin/activate       # macOS

# Run the application
python CSVEditor.py
```
