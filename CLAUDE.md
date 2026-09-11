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

# Run MyAgent with auto-launch instruction
python MyAgent.py -l "Instruction Name"

# Run MyAgent headless (no main window, auto-closes on completion)
python MyAgent.py -l "Instruction Name" --headless

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
MyAgent has a characterization test suite under `tests/` (stdlib `unittest`, no extra dependencies; 51 modules / 567 tests as of 2026-09-12) covering its pure / model-detection helpers, the cost-tracking layer, the window-geometry persistence layer, the OneDrive store sync, the keyboard-operation helpers, the Instructions list's layout model and widget helpers, and `Heartbeat.py` / `UnreadSummary.py` — run it with `python -m unittest discover -s tests -t .`. `tests/_util.py` builds bare mixin instances via `__new__` so helpers run without Tk, keys or network (the exceptions are `tests/test_toolbar_highlight.py`, `tests/test_selfbot_toolbar_highlight.py` and `tests/test_instruction_list.py`, which build real widgets on a withdrawn Tk root because the toolbar highlight and the Instructions tree are pure widget configuration, and `tests/test_keyboard.py`, whose root must actually HOLD the keyboard focus for synthetic key events to arrive, so it is a transparent focus-forced window — all three skip themselves where no display exists or grants focus); `tests/check_excel_live.py` is a hand-run live-Excel check deliberately outside the `test*.py` discovery pattern. `ruff` is available for linting (`ruff check MyAgent.py myagent/`, baseline above); there is no `mypy` or build step. SelfBot has one characterization module (`tests/test_selfbot_delete_confirm.py` — it imports `SelfBot` in-process, which works because module import builds no Tk root, and stubs `App` via `__new__` like the mixins); the other single-file Python apps (CSVEditor, TodoList, Account_Activity_WBC) have no tests; the native TodoList ports carry their own compiled suites (`tests/test_todolist_native.{cpp,mm}`, run by the `build_todolist_native.*` scripts).

## Project Structure
- `SelfBot.py` — Single-file tkinter GUI chatbot (~6500 lines); works as a solo chatbot or as a dual-instance self-chatting bot via file-based message passing. Anthropic-only BY DESIGN (the user's decision, reaffirmed 2026-09-06 when a GPT-6 path was built and reverted — do not add other providers to SelfBot); besides its own desktop/browser tools it reuses MyAgent's MCP / Gmail / Proton(IMAP) / Outlook mixins by inheritance plus SelfBot-native Meta (`manage_skills` + `manage_prompts`) and Pause (`pause_conversation` — rest the self-chat instead of closing it) tools, toggled from a second checkbox row (see `.claude/rules/CLAUDE_SELFBOT.md`); its seven main-window buttons (DELETE / NEW CHAT / SAVE / Attach Images / System Prompt / Skills / Safety) share one Arial 10 font and form a "last pressed" highlight group like MyAgent's toolbar (an in-file copy of `_track_toolbar_presses`, since 2026-09-08; the red/green Auto toggle stays out of it)
- `MyAgent.py` — Entry point (~350 lines) for the modular tkinter GUI autonomous agent; fire-and-forget task runner with an agentic tool-use loop, supports Anthropic, OpenAI, Google (Gemini models), xAI, and Moonshot (Kimi models) providers, supports `-l` argument for command-line auto-launch of saved instructions
- `myagent/` — Package containing MyAgent's mixin modules (split from the original single-file architecture):
  - `constants.py` — Tool schemas (TOOLS, META_TOOLS, DESKTOP_TOOLS, BROWSER_TOOLS), safety patterns, model constants, API pricing tables (ANTHROPIC_PRICING, OPENAI_PRICING, GEMINI_PRICING, XAI_PRICING, KIMI_PRICING — an entry may be a `DatedPrice` promo/sticker pair resolved per lookup by `resolve_price`, e.g. Gemini 3.6/3.7 Flash through 2026-12-31; ANTHROPIC_PRICING stays plain tuples because SelfBot unpacks it directly; OPENAI_PRICING rows are `(input, output, cached_input)` or, where the model bills cache writes — the GPT-5.6 tiers and `gpt-6-astra`, 1.25x input, since 2026-09-06 — a 4-tuple with the write rate), file paths
  - `helpers.py` — HTMLTextExtractor, extract_text_from_html, _ToolBlock, rotate_log_if_needed (shared one-slot size-cap rotation for the runtime logs: APICostLog.txt here and in SelfBot, heartbeat.log via Heartbeat.py, unread_summary.log via UnreadSummary.py), the context-overflow compaction pair parse_overflow_counts / trim_history_for_context, and the Claude Fable 5.1 history helpers strip_thinking_blocks (no-beta recovery for a signature-bound thinking-block 400) / strip_pre_fallback_blocks (the echo rule after a mid-output server-side refusal fallback)
  - `retry_util.py` — shared 429 / 5xx exponential-backoff schedule for the Anthropic, OpenAI and Gemini streaming callers (Ollama keeps its own inline schedule)
  - `mail_common.py` — `confirm_action`, the one destructive-action confirmation dialog shared by the Gmail / Proton / Outlook mixins (honours the per-instruction confirm-bypass list, posts a `⚠ … confirm bypassed` audit line when skipped, suspends the run clock via `input_wait_timer`)
  - `keyboard.py` — Keyboard operation (2026-09-10: MyAgent needs no mouse — Escape leaves a text box (or entry / spinbox / dropdown / the Instructions tree list) for the next control, Return presses the focused button, Alt+letter mnemonics per window with no underline cue, Tab / Shift+Tab / Up / Down walk the Safety dialog's embedded checkboxes; no window holding a draft closes on Escape); the full contract is the **Keyboard operation** paragraph in `.claude/rules/CLAUDE_MYAGENT.md`, pinned in `tests/test_keyboard.py`
  - `datapaths.py` — Shared-store path resolution + IO for the authored-content stores (agent_instructions.json / system_prompts.json / the `skills/` SKILL.md tree) and the per-machine API cost log, all under `<OneDrive>/MyAppShare/` (`MYAGENT_DATA_DIR` override, repo-root fallback): one-shot repo→OneDrive migration, atomic saves, OneDrive conflict-fork healing, the per-file skills tree, and `resolve_costlog()` → `APICostLog_<machine>.txt`; used by both apps and Heartbeat.py, stubbed by SelfBot when the package is absent — full contract in the **Shared-store sync**, **Skills tree** and **API cost tracking** paragraphs of `.claude/rules/CLAUDE_MYAGENT.md`
  - `ui_mixin.py` — setup_ui(), model/provider/thinking widget handlers, and the toolbar's Instruction / START / STOP "last pressed" highlight (`_track_toolbar_presses`, since 2026-09-08: the pressed button turns light blue + bold, the other two revert to their creation-time look at regular weight; nothing is repainted at startup, all three are created with one shared `("Arial", 10)` font so they start out identical, and STOP is unpressable / unfocusable whenever it can't be used — disabled while idle, enabled by `_start_agent`, re-disabled on press and at run end — yet never looks disabled, because its `disabledforeground` is its normal text colour, so it matches the other two at all times)
  - `state_mixin.py` — Instance management, display geometry, state persistence (incl. the one geometry-persistence mechanism every window and dialog goes through: `_remember_geometry` / `_place_window`, saved per monitor layout and per instance)
  - `instructions_mixin.py` — Instruction CRUD, editor Toplevel dialog (grid rows top-down: Save row incl. Apply / model params / tool toggles / Skills + Safety / images, then the BOTTOM, only-stretchy row: a two-pane split of the **Instructions list** (left, since 2026-09-11, under a fixed INSTRUCTIONS band since 2026-09-12 — a ttk.Treeview of section headers with their pages beneath, OneNote-style, replacing the old combobox; selecting loads, ▲ / ▼ / Alt+Up / Alt+Down move, drag-and-drop moves, Section… files or renames, folded sections and the sash position persist in agent_state.json) and the instruction text (right); the list's model is `instruction_layout.py`, its invariants the **Instructions list** paragraph in `.claude/rules/CLAUDE_MYAGENT.md`)
  - `instruction_layout.py` — the pure sections-and-order model behind that list: every store entry may carry `"section"` / `"order"` (absent = unfiled, listed last, alphabetical), sections exist while a page names them and appear in first-page order, and every edit (`move_page` / `move_section` / `file_page` / `rename_section` / `drop`) renumbers all pages 0..n-1 in display order — no Tk, no IO, tested in `tests/test_instruction_layout.py`; the `manage_instructions` tool and `Heartbeat.py` are untouched by it
  - `skills_mixin.py` — Skills CRUD, editor dialog, system prompt building
  - `streaming_mixin.py` — stream_worker (agentic loop), _execute_tool, _get_tools, _get_pricing (cost lookup), message translation
  - `anthropic_mixin.py` — _stream_anthropic_call
  - `openai_mixin.py` — OpenAI helpers, _stream_responses, _stream_responses_call, the usage normalizer (`_openai_usage_dict`, billed cache writes gated by `_openai_bills_cache_writes`), the gpt-5 / gpt-6 detection helpers (`_openai_always_reasoning`: the GPT-6 family — effort low..max, no none, never temperature; `_openai_reasoning_values`: the Reasoning-combobox rungs per family; `_openai_nearest_effort` / `_openai_effective_effort`: a stale saved effort → the nearest rung the model accepts, shared by the builder, the combobox and the reactive 400 rung), and `_openai_model_params` — the ONE per-family reasoning / temperature / text.verbosity builder behind both the live request and the Debug payload (since 2026-09-06, so the dump cannot drift from the wire)
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
- `UnreadSummary.py` — Zero-token daily unread-mail digest across every configured Gmail / IMAP / Outlook account (~940 lines; production launchd / Task Scheduler job): read-only by construction, every mutation behind a flag, `--dry-run` prints the digest without sending; the divider-framed `TOTAL:` section (unread count, SPECIFYING tally, any `ERRORS:` line) sits between the header and the first account block — moved up from the footer 2026-09-10, so the enumeration closes on a bare divider (`build_body`, layout pinned in `tests/test_unread_summary.py`); each failed account is logged as its own `ACCOUNT ERROR <account>: <reason>` line ahead of the pass summary (Proton Bridge not running is the usual one). Bill-matching rules come from `SpecifyingList.csv` in OneDrive `MyImportant/DeathFinances` (not in git)
- `Heartbeat.py` — Zero-token email-triggered dispatcher (~320 lines): each scheduled pass looks for an unread Gmail message whose subject is this machine's code (`APD` desktop / `APL` laptop / `APM` Mac), rewrites the named instruction's `*****`-delimited core from the body, and spawns `MyAgent.py -l "<name>" --headless`; one line per pass in `heartbeat.log` with one-slot rotation
- `tests/` — MyAgent's characterization suite (see Commands above) plus the native TodoList ports' compiled suites
- `BirdFlying.html` — Self-contained HTML/SVG animation (no dependencies, opens in any browser): two Australian magpies with desynchronized SMIL flap-and-glide wing cycles and a Web Audio synthesized warble (armed by first click, browser autoplay policy), flying over a stylized homestead scene with a gum grove
- `skills/` — User-defined skills as one file per skill: `skills/<name>/SKILL.md` (Agent-Skills-shaped since 2026-08-07), a frontmatter block (`name` / `description` / `mode`) over the markdown content. Names follow the Agent-Skills convention — lowercase letters/digits/hyphens, ≤64 chars (e.g. `westpac-login`); folder = name; H1 body titles stay human-readable. ENFORCED on create since 2026-08-07: `manage_skills` rejects non-conforming names (suggesting the kebabized form), and the Skills Manager offers a one-click auto-conversion; existing legacy-named skills stay editable. The optional description (one or two sentences: what the skill does + when to use it) is listed per-skill in the system prompt's On-Demand Skills index as the trigger signal. Shared by both apps; lives in `<OneDrive>/MyAppShare/skills/` when a OneDrive client is present (repo-root `skills/` fallback, gitignored) — OneDrive, not git, syncs it across machines. A legacy `skills.json` migrates into the tree on first load (then parks as `.migrated.bak`). Saves are per-file, diff-aware, and write-only — deletion is an explicit UI/tool action that removes the skill's folder; `SKILL-<Computer>.md` conflict forks heal on load (identical → deleted, different → preserved as `<name>__<label>`, orphaned → promoted). See `myagent/datapaths.py`
- `system_prompts.json` — Saved system prompts for SelfBot (same `<OneDrive>/MyAppShare/` home as skills.json); each entry now bundles a full main-screen environment (terminal user / chatting-with names, model + thinking params, the tool-row toggles, per-skill modes, and Safety confirm-bypass patterns), analogous to MyAgent's instructions — see `.claude/rules/CLAUDE_SELFBOT.md`. Legacy flat `{name: "text"}` files migrate to the dict form on launch
- `agent_instructions.json` — Saved agent instructions for MyAgent, with embedded images (same `<OneDrive>/MyAppShare/` home as skills.json; `Heartbeat.py` resolves the same path for its marker rewrites)
- CSVEditor state lives at `~/.config/csveditor/state.json` (outside the repo; a legacy `csv_editor_state.json` in the repo root is migrated there on first run)
- `close_chrome.ps1` / `close_chrome.sh` — cross-platform pair (PowerShell for Windows, bash for macOS) that closes MyAgent's automation browser cleanly after a browser-automation run, called via `run_command` by browser instructions; both target only the automation profile, never a personal browser window — invariants and the 2026-07-31 macOS live-test notes in `.claude/rules/CLAUDE_CLOSE_CHROME.md` (loads with either script)
- `desktop_launchers/` — Cross-platform "Desktop shortcut with a custom icon" sources: macOS AppleScript apps (compiled per-machine by `rebuild.sh`), their Windows `*_Win.ps1` twins, the `ProtonBridge_Watchdog_Win.ps1` Task Scheduler job, and the 1024px `icon_*_master.png` artwork behind every icon; built `.app`s and Desktop `.lnk`s are per-machine and NOT committed — conventions (why hidden launchers run under `conhost.exe --headless`, launch-or-focus vs always-fresh) in `desktop_launchers/CLAUDE.md`, full details in `desktop_launchers/README.md`
- `requirements.txt` — core dependencies (`pip install -r requirements.txt`); optional feature extras (playwright, mcp, xlwings, winocr, …) are listed in the README
- `MyAgent_Pricing.txt` — human-readable reference for the pricing tables in `myagent/constants.py`; the two are updated together at each model/pricing audit
- `comparison_*.json` ×5 — final-report JSONs (`--result-file` shape) from the 2026-08-18 five-provider `Weather_Agent_Skill_based` comparison run
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
- `.claude/rules/CLAUDE_CLOSE_CHROME.md` — close_chrome.ps1 / close_chrome.sh invariants and live-test notes (loads with either script)
- `desktop_launchers/CLAUDE.md` — launcher conventions (a nested file: loads whenever work touches `desktop_launchers/`)

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
