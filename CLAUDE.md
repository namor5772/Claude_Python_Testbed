# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Environment
- OS: Windows 11
- Python: Activate the `.venv` before running Python commands: `source .venv/Scripts/activate`
- After activation, use `python` to run scripts (the venv maps it correctly)
- Shell: bash (Git Bash)

## Commands
```bash
# Activate venv and run the app
source .venv/Scripts/activate && python SelfBot.py

# Kill any running instances before relaunching (Windows)
taskkill //F //IM pythonw.exe 2>/dev/null; taskkill //F //IM python.exe 2>/dev/null
```
There are no tests, linter, or build steps — this is a single-file testbed app.

## Project Structure
- `SelfBot.py` — Single-file tkinter GUI application (~3300 lines); works as a solo chatbot or as a dual-instance self-chatting bot via file-based message passing
- `skills.json` — User-defined skills with content and mode (created at runtime)
- `system_prompts.json` — Saved system prompts (created at runtime)
- `saved_chats/` — Directory of saved chat conversations, one `.json` file per chat; a matching `.txt` export of the output window is always saved alongside each `.json` file
- `app_state.json` — Persistent settings for SelfBot instance 1 (created at runtime)
- `app_state_2.json` — Persistent settings for SelfBot instance 2 (created at runtime)
- `selfbot.lock` — Lock file for SelfBot instance detection (created/deleted at runtime)
- `selfbot_auto_msg.json` — Shared file for SelfBot cross-instance message injection (created/deleted at runtime)
- `LaunchSelfBot.bat` — Launcher that starts both SelfBot instances side by side with focus on instance 1
- `selfbot_position.ps1` — PowerShell helper used by the launcher to position and focus windows

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
- Single-file architecture: all changes go in `SelfBot.py` unless there's a strong reason to split
