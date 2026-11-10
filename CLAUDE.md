# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.
It is kept short on purpose: the detail lives in `docs/claude/` and `.claude/rules/` — read those when a task needs them.

## Environment
- OS: Windows 11 or macOS (cross-platform)
- Python: Activate the `.venv` before running Python commands
  - Windows: `source .venv/Scripts/activate`
  - macOS: `source .venv/bin/activate`
- After activation, use `python` to run scripts (the venv maps it correctly)
- Shell: bash (Git Bash on Windows, zsh/bash on macOS)

## Commands
```bash
python MyAgent.py -l "Instruction Name"            # auto-launch a saved instruction
python MyAgent.py -l "Instruction Name" --headless # no main window, auto-closes
python Heartbeat.py --listen                       # the resident Telegram listener
python Heartbeat.py                                # one pass, then exit
python UnreadSummary.py --dry-run                  # a real run EMAILS a digest — never run it as a test
python MyBackup.py [--headless | --dry-run]
# Kill before relaunching — Windows:
taskkill //F //IM pythonw.exe 2>/dev/null; taskkill //F //IM python.exe 2>/dev/null
# macOS:
pkill -f "python.*MyAgent.py" 2>/dev/null; pkill -f "python.*SelfBot.py" 2>/dev/null
python -m unittest discover -s tests -t .          # the characterization suite (stdlib unittest)
ruff check MyAgent.py myagent/                     # baseline 42 findings, all E402 — judge NEW findings only
```
Tests: `tests/_util.py` builds bare mixin hosts via `__new__` (no Tk, keys or network); widget tests build real
widgets on a withdrawn or transparent root and skip without a display; `tests/check_*_live.py` are hand-run, paid
live checks outside discovery. Every command's flags, the ruff baseline's makeup and the module-by-module test
history: `docs/claude/commands-and-tests.md`.

## Project Structure
One line per item; each item's full contract and history: `docs/claude/project-structure.md`.
- `MyAgent.py` — entry point (`__init__` + main) for the tkinter autonomous agent; `App` inherits 27 mixins from `myagent/`; providers Anthropic, OpenAI, Google, xAI, Moonshot, Ollama
- `myagent/` — the mixins, one module per concern: `constants.py` (tool schemas, safety patterns, models, pricing tables), the provider callers (`anthropic_` / `openai_` / `gemini_` / `xai_` / `kimi_` / `ollama_mixin.py`), `streaming_mixin.py` (the agentic loop, `_execute_tool`, cost log), `ui_` / `state_` / `instructions_` / `skills_` / `chat_` / `event_loop_mixin.py`, mail (`gmail_` / `protonmail_` / `outlook_mixin.py`, `mail_common.py`), tools (`file_` / `document_` / `desktop_` / `browser_` / `excel_` / `physical_` / `voice_mixin.py`), `safety_mixin.py`, `model_upgrade_mixin.py`, `other_setup_mixin.py`, `mcp_mixin.py`, plus `helpers.py` (stdlib-only), `retry_util.py`, `datapaths.py` (shared-store IO), `keyboard.py`, `relay.py`, `speech.py`, `pane_notes.py`, `instruction_layout.py`, `sectioned_list.py`
- `SelfBot.py` — single-file Anthropic-only chatbot (never add other providers); inherits MyAgent's tool mixins; several helpers are byte-identical copies pinned by tests
- `Heartbeat.py` — zero-token Telegram dispatcher / resident listener; `UnreadSummary.py` — zero-token daily mail digest
- `MyBackup.py`, `CSVEditor.py`, `TodoList.py` (+ native `TodoList.cpp` / `.mm` ports) — single-file tkinter apps
- `claude_code_mcp/` — MyAgent's tools and skills served to Claude Code over MCP (deny-by-default confirms)
- `desktop_launchers/` — Desktop shortcuts and icons; `close_chrome.ps1` / `.sh` — close the automation browser
- `tests/` — the characterization suite; `docs/` — dated write-ups; `skills/` — repo-root fallback of the OneDrive skills tree
- Historical / sample files (`TOOLS_REFERENCE.txt`, `Tools*.txt`, `plot*.py`, …) are never cited for current behaviour — the live tool catalogue is `myagent/constants.py`


Per-app architecture deep-dives live in `.claude/rules/` and load automatically (via their `paths` frontmatter) whenever the matching files are touched — read them explicitly when discussing an app without editing it:
- `.claude/rules/CLAUDE_SELFBOT.md` — SelfBot.py architecture (loads with SelfBot.py / LaunchSelfBot.bat / selfbot_position.ps1)
- `.claude/rules/CLAUDE_MYAGENT.md` — MyAgent + `myagent/` package architecture (loads with MyAgent.py, myagent/, tests/, Heartbeat.py, UnreadSummary.py)
- `.claude/rules/CLAUDE_CLOSE_CHROME.md` — close_chrome.ps1 / close_chrome.sh invariants and live-test notes (loads with either script)
- `desktop_launchers/CLAUDE.md` — launcher conventions (a nested file: loads whenever work touches `desktop_launchers/`)

## Shared data
Authored stores (`agent_instructions.json`, `system_prompts.json`, the `skills/` tree, the per-machine API cost logs,
`todos.json`, MyAgent's `saved_chats/`) live in `<OneDrive>/MyAppShare/`, outside this public repo, with gitignored
repo-root fallbacks; per-machine state (`agent_state*.json`, `~/.config/myagent-*`) stays local. Full tables:
`docs/claude/onedrive-shared-folders.md` (kept in step with the README's **OneDrive shared folders** section).

## Anthropic model work
For routine edits to model ids, pricing rows or model wiring in this repo, do NOT load the `claude-api` skill:
read `myagent/constants.py`, `MyAgent_Pricing.txt` and the relevant `docs/*` write-up instead. Load `claude-api`
only for a genuine Anthropic API / SDK question the repo cannot answer (a new parameter, an error's meaning, SDK usage).

## Portability
- No hardcoded paths — the project works when cloned to any directory on any Windows PC or Mac
- `LaunchSelfBot.bat` uses `%~dp0` (resolves to its own directory at runtime)
- Python files use relative paths for all runtime file I/O
- The `.venv` is gitignored and must be recreated on each machine (`python -m venv .venv` + `pip install` dependencies)

## Workflow
- After editing or changing a .py file, always re-run it automatically — close any currently running instance first if necessary
- A session's outcome goes into the repo, not the conversation: CLAUDE.md / the rules for the contract, and for a day's significant change a dated write-up `docs/<topic>-YYYY-MM-DD.md` linked from here (e.g. `docs/bank-credentials-vault-2026-11-09.md`) — transcripts and the Claude memory folder are per machine, and the user continues work on the Mac Mini and the desktop from a `git pull` (the README's **Claude Code integration** section has the handoff notes)

## Conventions
- Keep code simple and focused — this is a testbed for experimentation
- Use tkinter for GUI work
- SelfBot uses single-file architecture: all changes go in `SelfBot.py`
- MyBackup is single-file too (`MyBackup.py`): keep its mirror engine free of Tk so `tests/test_mybackup.py` can drive it on temp trees, and keep the GUI's worker thread off Tk (events go through the queue; `_poll_queue` is the one place they become widgets)
- MyAgent uses a mixin-based modular architecture: the `App` class in `MyAgent.py` inherits from 27 mixin classes in the `myagent/` package. Add new methods to the appropriate mixin module by concern. `MyAgent.py` itself contains only `__init__` and the entry point.
