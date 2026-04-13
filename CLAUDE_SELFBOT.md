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
