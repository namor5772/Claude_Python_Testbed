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
source .venv/Scripts/activate && python app.py

# Kill any running instances before relaunching (Windows)
taskkill //F //IM pythonw.exe 2>/dev/null; taskkill //F //IM python.exe 2>/dev/null
```
There are no tests, linter, or build steps — this is a single-file testbed app.

## Project Structure
- `app.py` — Single-file tkinter GUI application (~2900 lines), the entire chatbot lives here
- `SelfBot.py` — Dual-instance self-chatting variant of app.py; two instances auto-converse via file-based message passing
- `skills.json` — User-defined skills with content and mode (created at runtime)
- `system_prompts.json` — Saved system prompts (created at runtime)
- `saved_chats/` — Directory of saved chat conversations, one `.json` file per chat (migrated from old `saved_chats.json`)
- `app_state.json` — Persistent settings for app.py / SelfBot instance 1 (created at runtime)
- `app_state_2.json` — Persistent settings for SelfBot instance 2 (created at runtime)
- `selfbot.lock` — Lock file for SelfBot instance detection (created/deleted at runtime)
- `selfbot_auto_msg.json` — Shared file for SelfBot cross-instance message injection (created/deleted at runtime)

## Architecture (app.py)

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

**Skills system** — Three modes: disabled, enabled (injected into system prompt), on-demand (retrieved via `get_skill` tool). Managed through `_build_system_prompt()` and `_get_tools()`.

**DPI handling** — `SetProcessDpiAwareness(2)` is called before any window creation. Screenshot coordinates are scaled via `_screenshot_scale` for mouse click mapping.

## Workflow
- After editing or changing a .py file, always re-run it automatically — close any currently running instance first if necessary

## Conventions
- Keep code simple and focused — this is a testbed for experimentation
- Use tkinter for GUI work
- Single-file architecture: all changes go in `app.py` unless there's a strong reason to split
