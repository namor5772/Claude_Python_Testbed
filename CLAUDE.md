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

# Run SelfBot / MyAgent / CSVEditor / TodoList
python SelfBot.py
python MyAgent.py
python CSVEditor.py
python TodoList.py

# Run MyAgent with auto-launch instruction
python MyAgent.py -l "Instruction Name"

# Run MyAgent headless (no main window, auto-closes on completion)
python MyAgent.py -l "Instruction Name" --headless

# Run Account Activity extractor
python Account_Activity_WBC.py

# Zero-token jobs (normally fired by launchd / Task Scheduler)
python UnreadSummary.py --dry-run   # print the would-be digest: zero mutations, no send
python Heartbeat.py                 # one pass: drain one APD/APL/APM trigger email, if any

# Kill any running instances before relaunching
# Windows:
taskkill //F //IM pythonw.exe 2>/dev/null; taskkill //F //IM python.exe 2>/dev/null
# macOS:
pkill -f "python.*MyAgent.py" 2>/dev/null; pkill -f "python.*SelfBot.py" 2>/dev/null

# Run the MyAgent characterization test suite (stdlib unittest, no extra deps)
python -m unittest discover -s tests -t .

# Lint with ruff (no mypy or build step). Baseline is 47 findings — 35 E402 (MyAgent.py's imports
# deliberately follow the DPI-awareness bootstrap, which must run before anything touches Tk),
# 7 E401, 5 E741 — so judge a change by NEW findings against that baseline, don't chase it to zero
ruff check MyAgent.py myagent/
```
MyAgent has a characterization test suite under `tests/` (stdlib `unittest`, no extra dependencies; 43 modules / 453 tests as of 2026-09-02) covering its pure / model-detection helpers, the cost-tracking layer, the window-geometry persistence layer, the OneDrive store sync, and `Heartbeat.py` / `UnreadSummary.py` — run it with `python -m unittest discover -s tests -t .`. `tests/_util.py` builds bare mixin instances via `__new__` so helpers run without Tk, keys or network; `tests/check_excel_live.py` is a hand-run live-Excel check deliberately outside the `test*.py` discovery pattern. `ruff` is available for linting (`ruff check MyAgent.py myagent/`, baseline above); there is no `mypy` or build step. SelfBot has one characterization module (`tests/test_selfbot_delete_confirm.py` — it imports `SelfBot` in-process, which works because module import builds no Tk root, and stubs `App` via `__new__` like the mixins); the other single-file Python apps (CSVEditor, TodoList, Account_Activity_WBC) have no tests; the native TodoList ports carry their own compiled suites (`tests/test_todolist_native.{cpp,mm}`, run by the `build_todolist_native.*` scripts).

## Project Structure
- `SelfBot.py` — Single-file tkinter GUI chatbot (~6500 lines); works as a solo chatbot or as a dual-instance self-chatting bot via file-based message passing. Anthropic-only; besides its own desktop/browser tools it reuses MyAgent's MCP / Gmail / Proton(IMAP) / Outlook mixins by inheritance plus SelfBot-native Meta (`manage_skills` + `manage_prompts`) and Pause (`pause_conversation` — rest the self-chat instead of closing it) tools, toggled from a second checkbox row (see `.claude/rules/CLAUDE_SELFBOT.md`)
- `MyAgent.py` — Entry point (~350 lines) for the modular tkinter GUI autonomous agent; fire-and-forget task runner with an agentic tool-use loop, supports Anthropic, OpenAI, Google (Gemini models), xAI, and Moonshot (Kimi models) providers, supports `-l` argument for command-line auto-launch of saved instructions
- `myagent/` — Package containing MyAgent's mixin modules (split from the original single-file architecture):
  - `constants.py` — Tool schemas (TOOLS, META_TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS), safety patterns, model constants, API pricing tables (ANTHROPIC_PRICING, OPENAI_PRICING, GEMINI_PRICING, XAI_PRICING, KIMI_PRICING — an entry may be a `DatedPrice` promo/sticker pair resolved per lookup by `resolve_price`, e.g. Gemini 3.6/3.7 Flash through 2026-12-31; ANTHROPIC_PRICING stays plain tuples because SelfBot unpacks it directly), file paths
  - `helpers.py` — HTMLTextExtractor, extract_text_from_html, _ToolBlock, rotate_log_if_needed (shared one-slot size-cap rotation for the runtime logs: APICostLog.txt here and in SelfBot, heartbeat.log via Heartbeat.py, unread_summary.log via UnreadSummary.py), the context-overflow compaction pair parse_overflow_counts / trim_history_for_context, and the Claude Fable 5.1 history helpers strip_thinking_blocks (no-beta recovery for a signature-bound thinking-block 400) / strip_pre_fallback_blocks (the echo rule after a mid-output server-side refusal fallback)
  - `retry_util.py` — shared 429 / 5xx exponential-backoff schedule for the Anthropic, OpenAI and Gemini streaming callers (Ollama keeps its own inline schedule)
  - `mail_common.py` — `confirm_action`, the one destructive-action confirmation dialog shared by the Gmail / Proton / Outlook mixins (honours the per-instruction confirm-bypass list, posts a `⚠ … confirm bypassed` audit line when skipped, suspends the run clock via `input_wait_timer`)
  - `datapaths.py` — Shared-store path resolution + IO for the authored-content stores (agent_instructions.json / system_prompts.json / the `skills/` SKILL.md tree): `<OneDrive>/MyAppShare/` when OneDrive is present (`MYAGENT_DATA_DIR` override, repo-root fallback), one-shot repo→OneDrive migration (leftover repo copy is key-level-unioned in, then renamed `.migrated.bak`), atomic mkstemp+replace saves, and OneDrive conflict-fork absorption (`<stem>-<Computer>.json` siblings unioned back and deleted; conflicting entries preserved as `name__label`). The skills library is per-file (since 2026-08-07): `resolve_skills_dir()` → `<share>/skills/`, `load_skills_tree()` (one-shot skills.json→tree migration + per-file `SKILL-<Computer>.md` fork healing), diff-aware write-only `save_skills_tree()`, explicit `delete_skill_tree_entry()` (removes the skill's folder incl. bundled resources). Also resolves the per-run API cost log (`resolve_costlog()` → `APICostLog_<machine>.txt` in the same share): an append-only log can't be key-level-unioned, so each machine writes its OWN file — appends never conflict, yet every machine's spend syncs everywhere for the Cost Log viewers to aggregate; lines are `timestamp;provider;model;cost;params;secs;instruction;calls` — params (5th field, 2026-08-10) is the run's compact thinking/temperature summary, secs (6th field, 2026-08-12) is MyAgent's wall-clock run duration in whole seconds, minus time spent waiting on user input — user_prompt and confirmation dialogs suspend the clock (2026-08-13) — instruction (7th field, 2026-08-16) is the name of the saved Agent Instruction the run was launched from (SelfBot writes its active system prompt's name; blank for an ad-hoc run) and calls (8th field, 2026-08-16) is the run's API-call count — the "Call #N" counter, one per round-trip of the agentic loop, so beyond the first they are tool-use round-trips (SelfBot: the process's total) — (older 4-/5-/6-field lines stay valid; SelfBot's lines carry an empty secs so the two new fields land in the same positions) — with the legacy repo-root `APICostLog.txt` folded in at first resolve (rename-claimed, so concurrently-launching apps migrate exactly once) and used as the no-OneDrive fallback. `resolve_store()` and `resolve_costlog()` share the single `_ensured_shared_dir()` use-the-share-else-repo-root fallback helper, so the stores and the cost log can never diverge on where they land. The shared dir was named `MyAgent` until 2026-07-19: `_shared_dir` auto-adopts a legacy-named dir by renaming it in place (and keeps serving the legacy dir if the rename fails, retrying next launch). Used by both apps and Heartbeat.py; SelfBot stubs it when the package is absent
  - `ui_mixin.py` — setup_ui(), model/provider/thinking widget handlers
  - `state_mixin.py` — Instance management, display geometry, state persistence (incl. the one geometry-persistence mechanism every window and dialog goes through: `_remember_geometry` / `_place_window`, saved per monitor layout and per instance)
  - `instructions_mixin.py` — Instruction CRUD, editor Toplevel dialog
  - `skills_mixin.py` — Skills CRUD, editor dialog, system prompt building
  - `streaming_mixin.py` — stream_worker (agentic loop), _execute_tool, _get_tools, _get_pricing (cost lookup), message translation
  - `anthropic_mixin.py` — _stream_anthropic_call
  - `openai_mixin.py` — OpenAI helpers, _stream_responses, _stream_responses_call
  - `gemini_mixin.py` — Gemini helpers, _tools_to_gemini, _messages_to_gemini, _stream_gemini_call
  - `xai_mixin.py` — xAI (Grok) provider via the OpenAI SDK against https://api.x.ai/v1 (Responses API), _stream_xai_call, reasoning-effort matrix, model fetch
  - `kimi_mixin.py` — Moonshot AI (Kimi) provider via the OpenAI SDK against https://api.moonshot.ai/v1 (Chat Completions only — own translators, not the Responses ones), _stream_kimi_call, per-model reasoning_content round-trip policy (required for k3/k2.7-code/k2.6, forbidden for k2.5), thinking/reasoning_effort matrices, exact cache-hit cost, model fetch
  - `ollama_mixin.py` — Ollama local-inference provider, per-model capability auto-detection
  - `mcp_mixin.py` — Model Context Protocol client (async stdio servers from `mcp_servers.json`)
  - `gmail_mixin.py` — Native multi-account Gmail tools (Google API client, per-account OAuth)
  - `protonmail_mixin.py` — Native multi-account IMAP/SMTP mail tools (Proton Bridge + any IMAP account)
  - `outlook_mixin.py` — Native multi-account Outlook / Microsoft 365 tools (Microsoft Graph + MSAL OAuth)
  - `document_mixin.py` — Local document text extraction (`read_document`: PDF/DOCX/HTML/text)
  - `file_mixin.py` — Native file tools (`read_file`/`edit_file`/`write_file`/`glob_files`/`grep_files`): Claude-Code-style exact-unique-match editing that fails loudly, read-before-edit tracking, CRLF/BOM-preserving round-trips
  - `desktop_mixin.py` — Desktop automation tools (pyautogui): screenshot, mouse, keyboard, clipboard, OCR
  - `browser_mixin.py` — Browser automation tools (Playwright): open, navigate, click, fill, screenshot, download (`browser_download` wraps the click in `expect_download()` + `save_as()` — required because the CDP attach GUID-renames unmanaged downloads)
  - `excel_mixin.py` — Excel live-workbook automation tools (xlwings): `excel_open`/`excel_read`/`excel_write`/`excel_format`/`excel_sheet`/`excel_find`/`excel_run_macro`/`excel_save`/`excel_close` — drives the running Excel application (COM on Windows, AppleScript on macOS) so formulas recalculate and macros run; attaches to the user's open instance via the single `_excel_app` chokepoint (which also holds the per-thread COM init and the xlwings-missing guard for all nine tools); `quit_app` refuses to quit while other workbooks remain open (see `.claude/rules/CLAUDE_MYAGENT.md` for the invariants)
  - `safety_mixin.py` — Command safety, confirmation dialog, user_prompt, run_powershell, agent control
  - `chat_mixin.py` — Chat save/serialize, image attachment, LaTeX processing
  - `event_loop_mixin.py` — check_queue, _on_close, _finish_close
- `Account_Activity_WBC.py` — Single-file tkinter GUI browser automation utility (~340 lines); connects to Edge via CDP, clicks "Display more" on the Westpac account activity page, and exports transactions as HTML + CSV
- `CSVEditor.py` — Single-file tkinter GUI CSV editor (~670 lines); open, edit, filter, and save CSV files with a spreadsheet-style treeview interface
- `TodoList.py` — Single-file tkinter todo manager (~660 lines): priorities, categories, due dates, overdue highlighting; its one `todos.json` lives in `<OneDrive>/MyAppShare/` so every machine shares one list. `TodoList.mm` (macOS/Cocoa) and `TodoList.cpp` (Windows/Win32) are functionality-identical native C++ ports built by `build_todolist_native.sh` / `.ps1` (test-then-compile, to the gitignored `TodoList.exe`); `LaunchTodoList.bat` launches the Python one
- `UnreadSummary.py` — Zero-token daily unread-mail digest across every configured Gmail / IMAP / Outlook account (~940 lines; production launchd / Task Scheduler job): read-only by construction, every mutation behind a flag, `--dry-run` prints the digest without sending; each failed account is logged as its own `ACCOUNT ERROR <account>: <reason>` line ahead of the pass summary (Proton Bridge not running is the usual one). Bill-matching rules come from `SpecifyingList.csv` in OneDrive `MyImportant/DeathFinances` (not in git)
- `Heartbeat.py` — Zero-token email-triggered dispatcher (~320 lines): each scheduled pass looks for an unread Gmail message whose subject is this machine's code (`APD` desktop / `APL` laptop / `APM` Mac), rewrites the named instruction's `*****`-delimited core from the body, and spawns `MyAgent.py -l "<name>" --headless`; one line per pass in `heartbeat.log` with one-slot rotation
- `tests/` — MyAgent's characterization suite (see Commands above) plus the native TodoList ports' compiled suites
- `BirdFlying.html` — Self-contained HTML/SVG animation (no dependencies, opens in any browser): two Australian magpies with desynchronized SMIL flap-and-glide wing cycles and a Web Audio synthesized warble (armed by first click, browser autoplay policy), flying over a stylized homestead scene with a gum grove
- `skills/` — User-defined skills as one file per skill: `skills/<name>/SKILL.md` (Agent-Skills-shaped since 2026-08-07), a frontmatter block (`name` / `description` / `mode`) over the markdown content. Names follow the Agent-Skills convention — lowercase letters/digits/hyphens, ≤64 chars (e.g. `westpac-login`); folder = name; H1 body titles stay human-readable. ENFORCED on create since 2026-08-07: `manage_skills` rejects non-conforming names (suggesting the kebabized form), and the Skills Manager offers a one-click auto-conversion; existing legacy-named skills stay editable. The optional description (one or two sentences: what the skill does + when to use it) is listed per-skill in the system prompt's On-Demand Skills index as the trigger signal. Shared by both apps; lives in `<OneDrive>/MyAppShare/skills/` when a OneDrive client is present (repo-root `skills/` fallback, gitignored) — OneDrive, not git, syncs it across machines. A legacy `skills.json` migrates into the tree on first load (then parks as `.migrated.bak`). Saves are per-file, diff-aware, and write-only — deletion is an explicit UI/tool action that removes the skill's folder; `SKILL-<Computer>.md` conflict forks heal on load (identical → deleted, different → preserved as `<name>__<label>`, orphaned → promoted). See `myagent/datapaths.py`
- `system_prompts.json` — Saved system prompts for SelfBot (same `<OneDrive>/MyAppShare/` home as skills.json); each entry now bundles a full main-screen environment (terminal user / chatting-with names, model + thinking params, the tool-row toggles, per-skill modes, and Safety confirm-bypass patterns), analogous to MyAgent's instructions — see `.claude/rules/CLAUDE_SELFBOT.md`. Legacy flat `{name: "text"}` files migrate to the dict form on launch
- `agent_instructions.json` — Saved agent instructions for MyAgent, with embedded images (same `<OneDrive>/MyAppShare/` home as skills.json; `Heartbeat.py` resolves the same path for its marker rewrites)
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
- `close_chrome.ps1` / `close_chrome.sh` — cross-platform pair (PowerShell for Windows, bash for macOS) for closing the browser cleanly after a browser-automation run, called via `run_command` by browser instructions. The macOS twin defaults to `browser_mixin.py`'s macOS profile (`~/Library/Application Support/MyAgent/browser_profile`, not the Windows `%TEMP%` one), matches Brave/Chrome/Edge, and uses `SIGTERM` to the non-`--type=` top-level process as its `CloseMainWindow` equivalent (the browser treats SIGTERM as a clean shutdown); the `exit_type` rewrite is BSD `sed` (`[[:space:]]`, not GNU `\s`; `-i ''`) with no BOM hazard to work around. `close_chrome.sh` was **live-tested on the M4 Mac Mini on 2026-07-31**: graceful close takes ~0.8s for a full 8-process Chrome instance (the force-kill fallback never triggers), a personal Chrome window running alongside is untouched, and the `sed` handles a spaced-colon `"exit_type" : "Crashed"` while leaving `Preferences` valid BOM-free JSON. The `-ww` concern does not apply — piped `ps` on macOS emits untruncated command lines. That run also found and fixed a port bug: matching a browser by grepping the whole `ps` command line meant any process whose *arguments* merely mentioned both the profile path and a browser name matched, so with no browser running at all the script SIGTERMed its own caller shell and still reported success. The Windows twin never had this, because `Win32_Process -Filter "Name='chrome.exe'..."` tests executable identity *before* the command-line match; the macOS port had collapsed both predicates into one grep. `matched_lines` now applies the browser test to `comm` (the executable path, arguments stripped — the true analogue of `Win32_Process.Name`), keeping the two-predicate structure. Windows behaviour: **Targets only MyAgent's automation instance**: every process it touches is matched on the automation profile path in its command line (`--user-data-dir=%TEMP%\myagent_browser_debug`, the profile `browser_mixin.py` launches with — `-UserDataDir` overrides it), so a personal Chrome/Edge window open at the same time is never closed. Asks the matched windows to close, polls for exit, force-kills only the matched leftovers, then resets `exit_type` to `"Normal"` in that profile's `Preferences` so the next run doesn't get a "restore pages?" bar over the page it needs to click. Two non-obvious details are load-bearing: the graceful-close calls are individually try/caught (closing the parent kills the children mid-loop, so a benign "Process has exited" race would otherwise abort the script before the `exit_type` reset), and `Preferences` is written with `[System.IO.File]::WriteAllText` + no-BOM UTF8 rather than `Set-Content -Encoding UTF8` (PS 5.1 writes a BOM, which makes Chrome discard the file as corrupt and reset the profile)
- `desktop_launchers/` — Cross-platform "Desktop shortcut with a custom icon" sources: macOS AppleScript apps (compiled per-machine by `rebuild.sh`) and their Windows twins `UnreadSummary_Win.ps1` / `CSVEditor_Win.ps1` / `MyAgent_Win.ps1` / `SelfBot_Win.ps1` (plus `ProtonBridge_Watchdog_Win.ps1`, a Task Scheduler job rather than a shortcut — `-Register` once; restarts Proton Bridge's launcher if `bridge.exe` is gone, at logon +2 min and every 15 min; its action runs under `conhost.exe --headless`, not `powershell -WindowStyle Hidden`, which flashed a Windows Terminal window on every check because a console program's window exists before PowerShell parses the switch), with `icon_*.ico` rendered from the 1024px `icon_*_master.png` artwork. `CSVEditor_Win.ps1` is launch-or-focus (brings an already-open editor to the front instead of starting a second copy); `MyAgent_Win.ps1` and `SelfBot_Win.ps1` always launch a fresh instance (both are multi-instance by design — SelfBot's second instance self-chats with the first, and SelfBot.py cascades it by `CASCADE_OFFSET` px so the two windows don't stack on the same saved geometry). The `My Agent.app` / `MyAgent_Win.ps1` robot icon (`icon_myagent_master.png`) is the macOS/Windows-matched twin of the legacy repo-root `myagent.ico`. The `SelfBot` icon (`icon_selfbot_master.png`, from `make_selfbot_icon.py`) is an anxious cross-eyed googly robot recursively contemplating a smaller copy of itself inside its own thought bubble — SelfBot's duo self-chat rendered as a self-referential Droste. Built `.app`s and Desktop `.lnk`s are per-machine and NOT committed; full details in `desktop_launchers/README.md`
- `requirements.txt` — core dependencies (`pip install -r requirements.txt`); optional feature extras (playwright, mcp, xlwings, winocr, …) are listed in the README
- `MyAgent_Pricing.txt` — human-readable reference for the pricing tables in `myagent/constants.py`; the two are updated together at each model/pricing audit
- `comparison_*.json` ×5 — final-report JSONs (`--result-file` shape) from the 2026-08-18 five-provider `Weather_Agent_Skill_based` comparison run
- `docs/selfbot-tool-audit.html` — checked-in HTML audit of SelfBot's tool system (schema → dispatch → handler)
- `miscSavedStuff/` — artifacts produced by the SelfBot duo (Shaun & Nigel essays, finales, letters, a tools report, a colophon)
- `TOOLS_REFERENCE.txt`, `MyAgent_Tools_Reference.{txt,pdf}`, `Tools*.txt`, `README_old.md`, `Markdown_Cheat_Sheet_2.md`, `Launch.txt`, `MyTest_autostart.txt` — historical snapshots and archives (the tool references date from the three-provider era, before the mail / file / Excel tools). Never cite them for current behaviour — the live tool catalog is `myagent/constants.py`
- `merge_system_prompts.py` — standalone key-level union merger for the JSON stores, the manual predecessor of `datapaths.py`'s automatic union; kept for one-off merges
- `make_icon.py` + the root `.ico` files (`myagent.ico`, `selfbot.ico`, `selfbot_duo.ico`, `todolist.ico`) — the original 2026-03 Windows icon generator and its outputs, predating the `desktop_launchers/` subsystem (whose `icon_*.ico` are rendered from 1024px master PNGs); `selfbot_duo.ico` was the SelfBot Duo shortcut icon and nothing in the repo references it any more
- `make_weather_pdf.py` / `make_weather_pdf_print.py`, `plot.py` / `plot_gaussian.py` / `plot_negative.py` (+ their `plot.png` / `plot_negative.png` outputs), `create_chart.py`, `move_window.py`, `agent_demo.py` — one-off agent-written scripts from the 2026-03/04 sessions, kept as samples of early tool use; not part of any app
- `.gitattributes` — LF in the repo; CRLF on checkout only for `.bat` / `.ps1`; `.sh` / `.command` forced LF even on Windows (a CRLF shebang is a macOS "bad interpreter")
- `.claude/skills/` (six `disable-model-invocation` slash commands: `/sync-check`, `/commit-push`, `/urp`, `/launch-agent`, `/launch-selfbot`, `/run`) and `.claude/commands/sync-refultra.md` — see the README's Claude Code integration section

Per-app architecture deep-dives live in `.claude/rules/` and load automatically (via their `paths` frontmatter) whenever the matching files are touched — read them explicitly when discussing an app without editing it:
- `.claude/rules/CLAUDE_SELFBOT.md` — SelfBot.py architecture (loads with SelfBot.py / LaunchSelfBot.bat / selfbot_position.ps1)
- `.claude/rules/CLAUDE_MYAGENT.md` — MyAgent + `myagent/` package architecture (loads with MyAgent.py, myagent/, tests/, Heartbeat.py, UnreadSummary.py)
- `.claude/rules/CLAUDE_ACCOUNT.md` — Account_Activity_WBC.py architecture (loads with Account_Activity_WBC.py)

## Portability
- No hardcoded paths — the project works when cloned to any directory on any Windows PC or Mac
- `LaunchSelfBot.bat` uses `%~dp0` (resolves to its own directory at runtime)
- Python files use relative paths for all runtime file I/O
- The `.venv` is gitignored and must be recreated on each machine (`python -m venv .venv` + `pip install` dependencies)

## Workflow
- After editing or changing a .py file, always re-run it automatically — close any currently running instance first if necessary

## Conventions
- Keep code simple and focused — this is a testbed for experimentation
- Use tkinter for GUI work
- SelfBot uses single-file architecture: all changes go in `SelfBot.py`
- MyAgent uses a mixin-based modular architecture: the `App` class in `MyAgent.py` inherits from 23 mixin classes in the `myagent/` package. Add new methods to the appropriate mixin module by concern. `MyAgent.py` itself contains only `__init__` and the entry point.
- Bank extractor changes go in `Account_Activity_WBC.py`
