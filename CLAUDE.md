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

# Run the MyAgent characterization test suite (stdlib unittest, no extra deps)
python -m unittest discover -s tests -t .

# Lint with ruff (no mypy or build step)
ruff check MyAgent.py myagent/
```
MyAgent has a characterization test suite under `tests/` (stdlib `unittest`, no extra dependencies) covering its pure / model-detection helpers — run it with `python -m unittest discover -s tests -t .`. `ruff` is available for linting (`ruff check MyAgent.py myagent/`); there is no `mypy` or build step. The single-file apps (SelfBot, CSVEditor, Account_Activity_WBC) have no tests.

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
  - `ollama_mixin.py` — Ollama local-inference provider, per-model capability auto-detection
  - `mcp_mixin.py` — Model Context Protocol client (async stdio servers from `mcp_servers.json`)
  - `gmail_mixin.py` — Native multi-account Gmail tools (Google API client, per-account OAuth)
  - `protonmail_mixin.py` — Native multi-account IMAP/SMTP mail tools (Proton Bridge + any IMAP account)
  - `outlook_mixin.py` — Native multi-account Outlook / Microsoft 365 tools (Microsoft Graph + MSAL OAuth)
  - `document_mixin.py` — Local document text extraction (`read_document`: PDF/DOCX/HTML/text)
  - `desktop_mixin.py` — Desktop automation tools (pyautogui): screenshot, mouse, keyboard, clipboard, OCR
  - `browser_mixin.py` — Browser automation tools (Playwright): open, navigate, click, fill, screenshot
  - `safety_mixin.py` — Command safety, confirmation dialog, user_prompt, run_powershell, agent control
  - `chat_mixin.py` — Chat save/serialize, image attachment, LaTeX processing
  - `event_loop_mixin.py` — check_queue, _on_close, _finish_close
- `Account_Activity_WBC.py` — Single-file tkinter GUI browser automation utility (~340 lines); connects to Edge via CDP, clicks "Display more" on the Westpac account activity page, and exports transactions as HTML + CSV
- `CSVEditor.py` — Single-file tkinter GUI CSV editor (~520 lines); open, edit, filter, and save CSV files with a spreadsheet-style treeview interface
- `BirdFlying.html` — Self-contained HTML/SVG animation (no dependencies, opens in any browser): two Australian magpies with desynchronized SMIL flap-and-glide wing cycles and a Web Audio synthesized warble (armed by first click, browser autoplay policy), flying over a stylized homestead scene with a gum grove
- `skills.json` — User-defined skills with content and mode, shared by both apps (created at runtime)
- `system_prompts.json` — Saved system prompts for SelfBot (created at runtime)
- `agent_instructions.json` — Saved agent instructions for MyAgent, with embedded images (created at runtime)
- `saved_chats/` — Directory of saved chat conversations, one `.json` file per chat; a matching `.txt` export of the output window is always saved alongside each `.json` file
- `app_state.json` — Persistent settings for SelfBot instance 1 (created at runtime)
- `app_state_2.json` — Persistent settings for SelfBot instance 2 (created at runtime)
- `agent_state.json` — Persistent settings for MyAgent (created at runtime)
- CSVEditor state lives at `~/.config/csveditor/state.json` (outside the repo; a legacy `csv_editor_state.json` in the repo root is migrated there on first run)
- `selfbot.lock` — Lock file for SelfBot instance detection (created/deleted at runtime)
- `selfbot_auto_msg.json` — Shared file for SelfBot cross-instance message injection (created/deleted at runtime)
- `Account_Activity_WBC.txt` — Raw transaction HTML extracted by Account_Activity_WBC.py (created at runtime, gitignored)
- `Account_Activity_WBC.csv` — Parsed transaction CSV exported by Account_Activity_WBC.py (created at runtime, gitignored)
- `LaunchSelfBot.bat` — Launcher that starts both SelfBot instances side by side with focus on instance 1
- `LaunchMyAgent.bat` — Launcher for MyAgent
- `selfbot_position.ps1` — PowerShell helper used by the SelfBot launcher to position and focus windows
- `desktop_launchers/` — Cross-platform "Desktop shortcut with a custom icon" sources: macOS AppleScript apps (compiled per-machine by `rebuild.sh`) and their Windows twins `UnreadSummary_Win.ps1` / `CSVEditor_Win.ps1` / `MyAgent_Win.ps1`, with `icon_*.ico` rendered from the 1024px `icon_*_master.png` artwork. `CSVEditor_Win.ps1` is launch-or-focus (brings an already-open editor to the front instead of starting a second copy); `MyAgent_Win.ps1` always launches a fresh instance (MyAgent is multi-instance by design). The `My Agent.app` / `MyAgent_Win.ps1` robot icon (`icon_myagent_master.png`) is the macOS/Windows-matched twin of the legacy repo-root `myagent.ico`. Built `.app`s and Desktop `.lnk`s are per-machine and NOT committed; full details in `desktop_launchers/README.md`

@CLAUDE_SELFBOT.md
@CLAUDE_MYAGENT.md
@CLAUDE_ACCOUNT.md

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
- MyAgent uses a mixin-based modular architecture: the `App` class in `MyAgent.py` inherits from 19 mixin classes in the `myagent/` package. Add new methods to the appropriate mixin module by concern. `MyAgent.py` itself contains only `__init__` and the entry point.
- Bank extractor changes go in `Account_Activity_WBC.py`
