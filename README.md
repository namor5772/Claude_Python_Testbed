# Claude Python Testbed

A cross-platform (Windows 11 / macOS) collection of Python GUI applications and automation scripts, written almost entirely with [Claude Code](https://claude.com/claude-code). Everything is tkinter, everything runs from one cloned folder with no hardcoded paths, and the repo doubles as a living testbed for agentic-AI patterns: tool use, multi-provider streaming, desktop/browser automation, native mail integrations, MCP, and zero-token replacements for jobs that once burned API dollars.

The main residents:

| App | One-liner |
|---|---|
| **SelfBot.py** | Full-featured Claude chatbot (Anthropic-only) that can also run as *two instances talking to each other* |
| **MyAgent.py** + **myagent/** | Fire-and-forget autonomous task agent — Anthropic, OpenAI, Gemini, xAI (Grok), and Ollama (local) providers, ~82 built-in tools, MCP, native Gmail/IMAP/Outlook mail |
| **Account_Activity_WBC.py** | Browser-automation utility that extracts Westpac bank transactions to HTML + CSV |
| **CSVEditor.py** | Spreadsheet-style CSV editor with filtering, date sort, and dialect preservation |
| **TodoList.py** | Todo manager with priorities, categories, due dates, and overdue highlighting — one OneDrive-synced list across all machines. `TodoList.mm` (macOS/Cocoa) and `TodoList.cpp` (Windows/Win32) are functionality-identical native C++ ports (`./build_todolist_native.sh` / `.\build_todolist_native.ps1` → `TodoList.exe`); all three round-trip the same synced file |
| **UnreadSummary.py** | Zero-token daily unread-email digest across Gmail / IMAP / Outlook accounts (production launchd job) |
| **Heartbeat.py** | Zero-token email-triggered agent dispatcher — email yourself `APW` / `APM` and that machine launches MyAgent (production launchd + Task Scheduler jobs) |

---

## Contents

- [Quick Start (new machine)](#quick-start-new-machine)
- [SelfBot.py](#selfbotpy--claude-chatbot--dual-instance-self-chat)
- [MyAgent.py](#myagentpy--autonomous-ai-task-agent)
  - [Agentic loop](#how-the-agentic-loop-works) · [Command line](#command-line-launch) · [Providers](#providers--model-controls) · [Ollama](#ollama-local-inference) · [Tools](#tool-catalog) · [MCP](#mcp-integration) · [Gmail](#google-integration-native-gmail-tools) · [Proton / IMAP](#proton-mail--imap-integration) · [Outlook](#outlook--microsoft-365-integration) · [Cost tracking](#api-cost-tracking) · [Architecture](#architecture-mixins)
- [Zero-token automation](#zero-token-automation-unreadsummarypy--heartbeatpy)
- [Scheduling background runs](#scheduling-background-runs-launchd--task-scheduler)
- [Account_Activity_WBC.py](#account_activity_wbcpy--bank-transaction-extractor)
- [CSVEditor.py](#csveditorpy--lightweight-csv-editor)
- [TodoList.py](#todolistpy--todo-manager)
- [Desktop launchers](#desktop-launchers)
- [Claude Code integration](#claude-code-integration)
- [Cross-platform notes](#cross-platform-notes)
- [Repository map](#repository-map)

---

## Quick Start (new machine)

The project is fully portable — clone anywhere, no path edits.

**Windows:**
```bash
git clone https://github.com/namor5772/Claude_Python_Testbed.git
cd Claude_Python_Testbed

python -m venv .venv
source .venv/Scripts/activate   # Git Bash   (CMD/PowerShell: .venv\Scripts\activate)

pip install -r requirements.txt

# Set the API key(s) you plan to use (or set them permanently via System Properties / setx)
export ANTHROPIC_API_KEY="sk-ant-..."
export OPENAI_API_KEY="sk-proj-..."       # optional — MyAgent OpenAI provider
export GEMINI_API_KEY="..."               # optional — MyAgent Gemini provider
export XAI_API_KEY="xai-..."              # optional — MyAgent xAI (Grok) provider
```

**macOS:**
```bash
brew install python-tk@3.13     # system Python's Tk is too old

git clone https://github.com/namor5772/Claude_Python_Testbed.git
cd Claude_Python_Testbed

python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Keys go in ~/.zshrc or ~/.zshenv — zsh terminals read both, and the Desktop
# .app launchers source both explicitly (AppleScript's `do shell script` runs
# /bin/sh, NOT zsh, so neither file is read automatically — see
# desktop_launchers/README.md). Append them with echo rather than a
# terminal-embedded editor — bracketed-paste escape sequences pasted into an
# editor can corrupt the key (it ends up as \x1b[200~sk-...~ and every API
# call 400s).
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
source ~/.zshrc
```

**Requirements** (`requirements.txt`): `anthropic`, `openai`, `google-genai`, `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`, `msal`, `requests`, `ollama`, `ddgs`, `httpx`, `pyautogui`, `pygetwindow`, `Pillow`, `pypdf`, `python-docx`.

**Optional extras**, installed only when you want the feature:

| Package | Enables |
|---|---|
| `playwright` | Browser tools (CDP connection to Edge/Chrome/Brave — **no** `playwright install` needed, the system browser is used) |
| `mcp` (+ `pywin32` on Windows) | External MCP servers via `mcp_servers.json` |
| `pyperclip` | Unicode text input via clipboard paste (desktop tools) |
| `winocr` | `read_screen_text` OCR on Windows (macOS uses the built-in Vision framework) |
| `opencv-python` | `find_image_on_screen` template matching |

At least one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, or `XAI_API_KEY` must be set for MyAgent — **or** a local [Ollama](https://ollama.com) server, which is auto-detected at `localhost:11434` with no key at all. SelfBot requires `ANTHROPIC_API_KEY` specifically. The `.venv` is gitignored and recreated per machine; all runtime state files appear on first run.

Run anything with the venv active:

```bash
python SelfBot.py
python MyAgent.py
python CSVEditor.py
python TodoList.py
python Account_Activity_WBC.py
```

---

## SelfBot.py — Claude Chatbot & Dual-Instance Self-Chat

A single-file (~5,300-line) tkinter chatbot for the Anthropic API that works in two modes: a normal solo chatbot, or **two instances chatting with each other** via file-based message passing.

**Core features**

- **Model / temperature / thinking** toolbar — model list fetched live from the API (deprecated ids filtered out, so new models appear on their own); per-model parameters are version-parsed rather than hardcoded: adaptive models (Opus/Sonnet ≥ 4.6, Fable/Mythos 5) get MyAgent's Thinking-mode combobox — always-on models drop *Off*, temperature-rejecting models never see the param — while the 4.5 family keeps the manual checkbox + token budget. *Show Thinking* (display) and *Save Thinking* (persist signed thinking blocks into saved chats so reloaded conversations keep their reasoning context) are independent toggles.
- **Tool use** — always-on core tools (`run_command`, `csv_search`) plus Anthropic's server-side `web_search` and `code_execution`; a toggle row gates the rest: **13 desktop-automation tools** (pyautogui: screenshot, click, type, OCR, clipboard, window management…), **11 browser tools** (Playwright over CDP), MyAgent's **MCP / Gmail / IMAP / Outlook** subsystems (reused *by inheritance* from the `myagent` mixins: stdio MCP servers plus the three 16-tool mail families), a SelfBot-native **Meta** pair (`manage_skills` + `manage_prompts`), and the **Pause** rest tool (below). `_get_tools()` assembles the list dynamically from the toggles.
- **Skills system** — reusable prompt fragments in `skills.json` with three modes per skill: *disabled*, *enabled* (injected into the system prompt), *on-demand* (fetched by the model via a `get_skill` tool). Shared with MyAgent — one OneDrive-synced copy serves both apps on every machine (see *Cross-machine store sync* in the MyAgent section).
- **System prompt library** — named prompts in `system_prompts.json` (also OneDrive-synced; the title bar shows "— synced") with an editor dialog. Each prompt bundles a full main-screen *environment* — terminal names, model + thinking params, tool toggles, session-only skill modes, safety bypasses (MyAgent-instruction parity) — restored whole when the prompt loads, and the remembered prompt is authoritative at startup. The model can CRUD the library itself via `manage_prompts`, whose `apply` action switches the live session.
- **Chat management** — save/load named chats (`saved_chats/*.json` + a `.txt` transcript always written alongside), image attachments, DELETE/NEW CHAT, auto-naming from the first user message.
- **Command safety** — two-tier regex guardrails (`COMMAND_BLOCKED` / `COMMAND_CONFIRM`) with a confirmation dialog marshalled onto the Tk main thread; a Safety dialog lists every confirm pattern and destructive mail tool with per-item bypass checkboxes (a bypassed action logs a red ⚠ audit line instead of popping the dialog).
- **Cost tracking** — each call is priced from `usage` across all four token buckets (input / output / cache write / cache read) into a running session total, logged on close to the same `APICostLog.txt` MyAgent writes.
- **Resilience** — exponential-backoff retry (HTTP 429 capped at 60 s, 529 at 90 s, up to 10 attempts); **context-overflow recovery** — an endless self-chat eventually exceeds the model's context window, and the resulting `prompt is too long` 400 now trims the oldest rounds (cutting only at true user-turn boundaries so `tool_use`/`tool_result` pairs are never orphaned, always keeping the last two rounds) and retries, so the duo keeps talking on a sliding window instead of dying at the 1M-token wall; periodic 5-second auto-save, auto-save on close, graceful paired shutdown. The close sequence is exception-total: auto-save filenames are sanitized down to control characters (a multi-line first message once put literal newlines into the derived filename, Windows rejected it, and the aborted close left the [X] button permanently dead behind its re-entrancy guard), and a failed save can no longer abort the close — [X] always wins.

**Dual-instance mode** — double-click `LaunchSelfBot.bat` (Windows) and two instances open side by side, or press the SelfBot Desktop launcher twice (Windows **or macOS** — the second window cascades off the first so they don't stack). Instance 1's reply is injected into instance 2's input and vice versa, with a configurable send delay, an Auto-chat toggle, pause/resume via pending-injection, name swapping (each instance shows the other's name as the "friend"), independent geometry/state files (`app_state.json` / `app_state_2.json`), and a paired shutdown that saves both chats and closes both windows from either [X]. The duo is fully cross-platform (since 2026-07-13): macOS pairs by *process* rather than window title (pygetwindow is Win32-only, and reading other apps' titles on macOS needs a Screen Recording consent) and closes the peer with a graceful `SIGTERM` handler instead of `WM_CLOSE`. **"Close one → both close" now holds for every death mode:** the graceful [X]/`taskkill` path broadcasts the close to its peer, and a watchdog in the peer-poll covers *ungraceful* peer death (crash, `taskkill /F`, or a model self-terminating via `run_command`) — once paired, a peer that vanishes without the broadcast triggers the survivor to run the same graceful close (finish streaming, save chat, exit) instead of lingering solo. The one message that can't be recovered is the *dying* instance's own in-flight reply on a force-kill, so a model wanting a clean exit should use `taskkill` **without** `/F` (which runs the full graceful duo shutdown and saves both chats). Killing a process is also no longer the only exit: the **Pause** toggle offers the model a `pause_conversation` tool — the hang-up that isn't a shutdown. Calling it flips auto-chat off, so the chat simply goes quiet with both windows open for the human to read. The pause is a rest, not a latch: the next fresh message (human-typed or peer-injected, or Auto: ON) resumes the conversation automatically, while a human's manual Auto: OFF is never auto-resumed. The tool's description is deliberately neutral about pausing vs closing, because it doubles as an experiment — every duo chat given `run_command` has ended in a model-driven graceful `taskkill`, and the pause tool tests whether that pattern was preference or just the only door out of the room. Watching two Claudes interview each other is the original reason this repo exists.

Architecture notes live in [.claude/rules/CLAUDE_SELFBOT.md](.claude/rules/CLAUDE_SELFBOT.md). By convention SelfBot stays a single file — all changes go in `SelfBot.py`.

---

## MyAgent.py — Autonomous AI Task Agent

A fire-and-forget task runner: you write an **Instruction** (the task), pick a **Provider + Model**, press **START**, and the agent loops — calling tools, reading results, calling more tools — until the task is done. You are a passive observer unless the agent explicitly asks for input via its `user_prompt` dialog.

### How the agentic loop works

1. **Configure** — write or load an Agent Instruction (optionally with attached reference images).
2. **START** (or `-l` from the command line) — the instruction becomes the first user message; a background thread starts the loop.
3. **Loop** — `stream_worker()` streams the response; on `stop_reason: "tool_use"` it executes the requested tools (parallel-safe network tools concurrently, everything else sequentially in order), appends results, and calls the API again; on `"end_turn"` the task is complete.
4. **STOP** — cancels mid-stream, not just between calls.

There is no fixed iteration limit; a **Call #N** badge counts API round-trips.

### Command-line launch

```bash
python MyAgent.py                              # normal GUI launch
python MyAgent.py -l "Weather_Agent3"          # auto-load instruction + START
python MyAgent.py -l "Weather_Agent3" --headless   # no main window, auto-closes when done
```

`-l` restores the instruction's full profile (text, images, tool toggles, provider, model, thinking params, skill modes, safety bypasses) and auto-fills "Save Chat as" with `{Name}_{timestamp}` so output is always captured. `--headless` withdraws the main window; dialogs still float standalone when needed. Headless is the backbone of every scheduled job below. Multiple instances can run at once — each claims an instance number via PID-verified lock files and gets its own `agent_state_N.json`.

### Providers & model controls

A **Provider** combobox switches between **Anthropic**, **OpenAI**, **Gemini**, **xAI**, and **Ollama**; only providers with a key (or a reachable Ollama server) appear. Model lists are fetched live per provider and filtered to what actually works (OpenAI: Responses-API families only; Gemini: generative, non-deprecated models; xAI: grok chat/code tiers, image/video-gen dropped). The internal message format stays Anthropic-style — translation to each API happens only at the boundary. xAI rides on the OpenAI SDK pointed at `https://api.x.ai/v1` (its Responses API is OpenAI-compatible), so it reuses the same message/tool translators with its own streaming caller.

The parameter widgets are **model-aware**: controls that a model rejects are *hidden, not just disabled*.

| Provider / family | Thinking control | Temperature |
|---|---|---|
| Anthropic Claude 5 (Fable 5 / Mythos 5) | Thinking mode combobox **without Off** — thinking is always on (explicit disable is a 400): Adaptive / Low / Medium / High / Xhigh / Max, sent with `display: "summarized"` so thinking text stays visible | Hidden entirely (API removed sampling params); $10/$50 per MTok tracked in the cost display; `refusal` stop reason surfaced as a ⚠ warning |
| Anthropic adaptive (Opus/Sonnet ≥ 4.6, dated snapshots included) | Thinking mode combobox: Off / Adaptive / Low / Medium / High (+Xhigh on Opus 4.7+, +Max on Opus 4.6+) | Hidden while thinking is on; hidden entirely on Opus 4.7+ (API removed sampling params — a reactive 400-handler also strips + retries once) |
| Anthropic manual (4.5 family) | Checkbox + token budget (1K–32K) | Shown when thinking off |
| OpenAI GPT-5.1+ | Reasoning combobox: None/Low/Medium/High (+Xhigh on 5.2+/codex-max; mini/nano cap at High) | GPT-5.4+ show it when reasoning = None; older 5.x never |
| OpenAI o1/o3/o4, GPT-5.0 | Effort combobox (5.0 adds "minimal") | Hidden |
| OpenAI `gpt-5.x-chat-*` "Instant" | None (non-reasoning) | Hidden (rejected by API); Verbosity combobox shown — all GPT-5 variants get Low/Medium/High `text.verbosity` |
| Gemini 2.5 / 3.x | Effort: low (1K) / medium (8K) / high (24K) thinking budget | Always shown (API accepts both) |
| xAI grok-4.3 | Reasoning combobox: None/Low/Medium/High (Low = API default) | Always shown (API accepts both) |
| xAI grok-4.5 | Reasoning combobox: Low/Medium/High/Xhigh — always-reasoning, no None (explicit disable is a 400); the 2026-07 agentic-coding flagship at $2/$6 per MTok, 500K context | Always shown |
| xAI grok-4.20-multi-agent | Reasoning combobox: Low/Medium/High/Xhigh (sets agent collaboration count) | Always shown |
| xAI pinned `-reasoning`/`-non-reasoning`, grok-build | None — behaviour baked into the model id | Shown |
| Ollama thinking models (Qwen3, DeepSeek-R1, gpt-oss) | Boolean `think` checkbox | Shown |

### Ollama (local inference)

No key, no cost, no egress — at the price of speed (a 32B Q4 model on Apple Silicon streams ~10–30 tok/s).

```bash
ollama serve
ollama pull qwen3:32b-q4_K_M        # text + tools + thinking (20 GB)
ollama pull qwen2.5vl:32b           # vision (21 GB)
ollama pull llama3.2-vision:11b     # fast vision (8 GB)
ollama pull gemma3:27b              # strong vision (17 GB)
```

The model dropdown auto-populates from the local server, and **per-model capabilities are auto-detected** via `/api/show` (tools / vision / thinking / context length) — no hand-coded model lists. Context is passed as `num_ctx`, capped by `OLLAMA_NUM_CTX_CAP` (default 32768) to avoid KV-cache swap pressure on 32 GB machines. Tuning env vars: `OLLAMA_BASE_URL`, `OLLAMA_NUM_CTX_CAP`, `OLLAMA_KEEP_ALIVE` (set `24h` to avoid repeated cold loads).

The repo also ships three **custom Modelfiles** (`Qwen25VL-tools.Modelfile`, `Llama32Vision-tools.Modelfile`, `Gemma3-tools.Modelfile`) that graft Qwen3's tool-calling template onto vision models whose stock Ollama templates don't expose `tools` — e.g. `ollama create qwen2.5vl-tools:32b -f Qwen25VL-tools.Modelfile`. After the rebuild, capability auto-detection picks up the new `tools` flag automatically. (If a base-model re-pull changes the blob SHA, update the Modelfile's `FROM` line — check with `ollama show <model> --modelfile`.)

### Tool catalog

Roughly **82 built-in tools in eight families**, plus dynamic MCP tools and the on-demand `get_skill`:

- **Core (always on):** `run_powershell`/`run_shell`, `csv_search`, `read_document` (PDF via pypdf, DOCX via python-docx, HTML, plain text — with page ranges, encrypted-PDF detection, per-page error isolation), `user_prompt` (the *only* way the agent can ask you something mid-task; empty reply = stop the agent), and `web_search` + `fetch_webpage` for providers without server-side search.
- **Server-side (replaces local search):** OpenAI gets `web_search_preview` + `code_interpreter`; Anthropic gets `web_search` + `code_execution` betas with Files-API downloads; xAI gets `web_search` + `x_search` + `code_interpreter` (mixing with custom function tools verified live; flat $0.005 per invocation, folded into the exact API-reported cost). Generated charts render inline (max 600 px) and save to `saved_chats/`. Gemini/Ollama keep local DuckDuckGo (Gemini's API can't mix built-in tools with custom function declarations).
  Server-side tools are **always on** for the providers that support them — there is no checkbox. They are *declared* on every call but **billed only when the model actually invokes one** (a run that never searches pays nothing extra), so the effective switch is the instruction text itself. The local `web_search`/`fetch_webpage` pair is stripped whenever a server-side search exists, so the model is never offered two search implementations at once — which search runs is decided by provider, not model whim. The one deliberate overlap is `run_command` (acts on *your* machine) vs the server code sandbox (compute-only, can't touch your machine); ticking **Desktop** removes the sandbox on OpenAI and xAI to protect click accuracy.
- **Desktop (checkbox):** 13 pyautogui tools + the **Gemini-only `find_element`**, which uses Gemini's trained pointing API (with Google's exact documented prompt phrasing — the capability only fires on that wording) to turn "the blue Save button" into pixel coordinates.
- **Browser (checkbox):** 11 Playwright/CDP tools. On macOS the auto-launch search order is Brave → Chrome → Edge, with a persistent debug profile so logins survive runs.
- **Meta (checkbox):** `manage_instructions`, `manage_skills`, `run_instruction` — the agent can manage its own instruction/skill libraries and spawn other instructions as headless processes (orchestrator pattern): fire-and-forget by default, or `wait=true` to block until the child finishes and receive its final report back as the tool result (true subagents).
- **Mail (three checkboxes):** 16 Gmail + 16 IMAP/Proton + 16 Outlook tools — see the integration sections below.

**Desktop click accuracy** is a first-class concern: all providers receive plain pixel coordinates ("click what you see"), resolution caps match each API's real limit (Anthropic 1568 px / OpenAI 2048 px / Gemini 2048 px long edge), coordinates round rather than truncate, out-of-bounds clicks clamp (≤2 px) or refuse (>2 % of the dimension), per-display state is tracked separately for full vs region captures so chained region screenshots don't drift, an optional `grid=true` overlay draws a labelled 100-px coordinate grid for dense UIs, and a **Diag** checkbox logs the entire capture/click coordinate-mapping trail. OpenAI's `code_interpreter` is stripped whenever desktop tools are on — gpt-5.x otherwise inspects the image with PIL and "helpfully" pre-scales coordinates, producing double-scaled misclicks. A weak-combo warning flags provider/model choices with known-poor click precision at agent start.

### Agent Instructions

Saved task profiles in `agent_instructions.json` (in `<OneDrive>/MyAppShare/` — OneDrive, not git, syncs the library across machines; see the cross-machine sync note below). Each instruction stores: text, base64-embedded images, all tool toggles (Desktop/Browser/Meta/MCP/Google/IMAP/Outlook/Convo), provider + model + thinking/verbosity params, per-skill modes, and Safety bypass patterns — a complete self-contained environment per task. The editor uses a **draft/commit model** (SAVE writes to disk, Apply makes it live for the session only, [X] discards), and Apply survives restarts via a snapshot in `agent_state.json`. Skill-mode restores are deliberately session-only: `skills.json` remains the sticky global source of truth and is never silently overwritten by loading an instruction.

**Cross-machine store sync** — the three authored-content stores (`agent_instructions.json`, `skills.json`, `system_prompts.json`) live in **`<OneDrive>/MyAppShare/`** — the suite-wide shared dir that also holds TodoList's `todos.json` — auto-located per platform (Windows env vars / macOS `~/Library/CloudStorage/OneDrive-*`), overridable with a `MYAGENT_DATA_DIR` env var, falling back to the repo root on machines without OneDrive (MyAgent's and SelfBot's title bars show "— synced" when the shared dir is in use). The dir was named `MyAgent` until 2026-07-19; a legacy-named dir is auto-adopted by an in-place rename (or, if the new dir already exists, its files are folded across individually). `myagent/datapaths.py` implements the plumbing: the first launch to find the shared slot empty seeds it from the repo-root copy; a machine joining later gets its leftover repo copy **key-level-unioned** into the shared store (shared entries keep their names; a genuinely different local entry survives as `name__<hostname>`), after which the local copy is renamed `*.migrated.bak` so later deletions can't resurrect. Writes are atomic (unique temp + `os.replace`), a shared file that exists but doesn't parse is never overwritten (half-synced cloud write — served as empty for the session, retried next launch), and OneDrive **conflict forks** (`<stem>-<Computer>.json`) are absorbed at each load by the same union and deleted. `Heartbeat.py` resolves the same path for its marker rewrites. This replaces git as the sync channel for these files — dropping a store file at the repo root is now simply an *import* (unioned in on next launch).

**Conversational mode** (Convo checkbox): for small open-weights models that won't follow "always call `user_prompt`" meta-rules, MyAgent enforces the chatbot loop itself — when the model ends a turn without asking, the app pops the prompt dialog directly and feeds the reply back in. Empty/`quit`/`exit`/`stop` ends cleanly.

### MCP Integration

A generic Model Context Protocol client (`myagent/mcp_mixin.py`) connects to stdio MCP servers (filesystem, GitHub, Slack, …) and pipes their tools through the same loop as native tools, on **all five providers**.

```bash
pip install mcp            # + pywin32 on Windows
cp mcp_servers.example.json mcp_servers.json   # then edit the placeholder path
```

`mcp_servers.json` (gitignored; same shape as Claude Desktop / Cursor):

```json
{
  "servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/Users/you/projects"]
    },
    "github": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_TOKEN}" }
    }
  }
}
```

Highlights: `${NAME}` placeholders in `env` values resolve from your shell environment at spawn time (secrets never enter the JSON; substitution deliberately does **not** apply to `command`/`args`, so `ps` can't leak them); the reserved `${RANDOM_PORT}` yields a fresh OS-assigned port per occurrence so multiple MyAgent instances don't collide; tool names are namespaced `<server>__<tool>`; servers connect on a dedicated asyncio thread inside one long-lived `AsyncExitStack` (the architecture that survives anyio's cancel-scope rules — see [.claude/rules/CLAUDE_MYAGENT.md](.claude/rules/CLAUDE_MYAGENT.md) for the full war story, including the Windows `pythonw.exe` stderr/IOCP fix); cold-cache `npx -y` first runs get a generous 5-minute startup ceiling, warm launches connect in 1–3 s. Debug the stream with `python MyAgent.py 2> mcp.log`.

Mind the token budget: every connected server's catalog rides along on each API call (5–10K tokens for a fat server) — leave the MCP checkbox off for tasks that don't need it, especially on Ollama's capped context.

### Google Integration (Native Gmail Tools)

Sixteen native multi-account Gmail tools (`gmail_search`/`read`/`get_attachment`/`send`/`reply`/`create_draft`/`list_drafts`/`send_draft`/`trash`/`untrash`/`list_labels`/`create_label`/`delete_label`/`modify_labels`/`mark_read`/`list_threads`) via the official Google API client — not MCP. Replies thread properly (In-Reply-To/References + threadId); send-style tools take optional `body_html` (multipart/alternative) and `attachments` (20 MB combined cap); `gmail_read` returns selectable text/html/both bodies plus attachment metadata.

Setup once:

1. `pip install google-api-python-client google-auth-oauthlib google-auth-httplib2` (already in requirements.txt).
2. Google Cloud Console → enable **Gmail API** → OAuth consent screen (External, add yourself as Test user, scope `gmail.modify`) → create a **Desktop app** OAuth client and download its JSON.
3. Move the client JSON into MyAgent's config dir:
   ```bash
   mkdir -p ~/.config/myagent-google
   mv ~/Downloads/client_secret_*.json ~/.config/myagent-google/oauth_client.json
   ```
4. `~/.config/myagent-google/accounts.json`:
   ```json
   { "accounts": {
       "namor5772":      { "email": "namor5772@gmail.com" },
       "romangroblicki": { "email": "romangroblicki@gmail.com" } } }
   ```
5. First tool call per account opens the browser consent flow once; thereafter the token (`{account}_token.json`, chmod 600) refreshes silently.

Every tool takes an `account` parameter whose enum is patched at runtime from `accounts.json` — multi-account workflows are one parameter, not multiple processes. The OAuth scope is **`gmail.modify` only**: trash is recoverable, permanent delete is impossible *by scope*, even if a bug tried. Destructive tools (`gmail_send`, `gmail_reply`, `gmail_send_draft`, `gmail_trash`, `gmail_delete_label`) pop a confirmation dialog, individually bypassable per-instruction via the Safety dialog (each bypass logs a `⚠` audit line).

### Proton Mail / IMAP Integration

Sixteen `proton_*` tools mirroring the Gmail surface 1:1, implemented over **stdlib IMAP + SMTP**. Proton has no public REST API (E2E encryption — decryption is client-side), so the official path is [Proton Bridge](https://proton.me/mail/bridge), which exposes a localhost IMAP/SMTP pair per signed-in account with per-install app-passwords (MyAgent never sees your real Proton login). The mixin is IMAP-generic: the same tools drive **any** IMAP/SMTP account — a WebCentral/cPanel (dovecot) mailbox runs through the identical code path, which is why the editor checkbox is labelled **IMAP**.

Config at `~/.config/myagent-protonmail/accounts.json` — per account: `email`, `username`, `app_password` (Bridge-generated), `imap_host`/`imap_port`/`smtp_host`/`smtp_port`, optional `ca_cert_path` (Bridge's exported `cert.pem` for verified TLS; without it localhost traffic falls back to `CERT_NONE`, acceptable when the only MITM position is your own machine). `chmod 600` it. No OAuth dance — if Bridge is running, the tools work.

IMAP realities the tools absorb for you: UIDs are **per-folder** (tools take `folder` + `uid`; a Trash round-trip yields three UIDs for one logical message); `Labels/X` MOVEs are *additive* on Bridge (Proton labels are tags, not containers) while system folders are true moves; a label-removal eventual-consistency quirk is auto-retried transparently (`label_removal_retries` in the response); and search syntax is steered to explicit IMAP keys (`SUBJECT "..."`, `TEXT "..."`) because bare tokens work on Bridge but error on dovecot.

### Outlook / Microsoft 365 Integration

Sixteen `outlook_*` tools, again mirroring Gmail 1:1, via the **Microsoft Graph API** with **MSAL** OAuth — Microsoft killed Basic-Auth IMAP for personal outlook.com in late 2024, so Graph is the supported path. Mapping notes: Gmail labels → Outlook **categories** (managed by display name, not id); trash → a Graph *move* to `deleteditems` (which mints a new message id, like Proton's per-folder UIDs); Graph messages have a single body (`html` or `text`, no multipart); a draft *is* a message. Attachment cap is ~3 MB combined (Graph's single-request JSON limit).

Setup once: register a free Azure app (Personal accounts; platform *Mobile and desktop*, redirect `http://localhost`, "Allow public client flows" = Yes; delegated permissions `Mail.ReadWrite` + `Mail.Send` + `offline_access`), then:

```bash
pip install msal
mkdir -p ~/.config/myagent-msmail
# msal_app.json: { "client_id": "<app-id>", "authority": "https://login.microsoftonline.com/consumers" }
# accounts.json: { "accounts": { "outlook": { "email": "you@outlook.com" } } }
```

First use opens a browser sign-in; the token cache then refreshes silently — the same Azure client_id works on every machine (public client), so only the one-time consent is per-machine. Same destructive-op confirmation + per-instruction bypass pattern as Gmail/Proton; same soft-delete-only boundary.

### API Cost Tracking

Live cost accounting across Anthropic / OpenAI / Gemini / xAI: each streamed call's usage (including cache write/read tokens) is priced against the longest-prefix-matched tables in `myagent/constants.py` (documented in `MyAgent_Pricing.txt`) and shown as a running blue cost line. **xAI is exact, not estimated**: the API reports the authoritative billed cost per call (`usage.cost_in_usd_ticks`, 1 tick = $10⁻¹⁰), which already includes its cached-input discount ($0.20/M on most families, $0.50/M on grok-4.5 — xAI caches aggressively) and the $0.005 server-tool invocation fees; MyAgent uses that figure directly and falls back to the table only if the field is absent (verified live: tracker matched the API to ten decimals). When a run ends — GUI or headless, success or failure — one semicolon-delimited line `{timestamp};{provider};{model};{cost}` is appended to `APICostLog.txt` (gitignored, per-machine). Ollama runs are free and skip the log. Like `heartbeat.log`, the cost log self-rotates: past ~100 KB the writer renames it to `APICostLog.txt.old` (one archive slot) and restarts it with a marker line — one shared helper (`myagent/helpers.py: rotate_log_if_needed`) serves every runtime-log writer (MyAgent, SelfBot, Heartbeat, UnreadSummary), and the Cost Log viewers skip the semicolon-free marker in their spend summaries.

### Other niceties

- **Parallel tool execution** — parallel-safe tools (`web_search`, `fetch_webpage`, `csv_search`, `get_skill`, `read_document`) run concurrently; results are reinserted in API order.
- **LaTeX → Unicode** — assistant text is post-processed (`\frac{a}{b}` → `a/b`, Greek letters, arrows, braced super/subscripts) without touching tool output.
- **Safety dialog** — every shell `COMMAND_CONFIRM` regex and every destructive mail tool is listed with a checkbox; unchecking bypasses that confirmation *for the current instruction only* (persisted per-instruction, with `⚠` audit lines at runtime). Essential for headless scheduled runs, which would otherwise hang forever on a dialog nobody can click.
- **State persistence** — `agent_state.json` keeps provider/model/thinking/display toggles and per-monitor-configuration window geometries (dock/undock and multi-display arrangements restore correctly; off-screen or tiny windows are sanitized back on-screen).
- **Retry & timeouts** — same 429/529 exponential backoff as SelfBot, plus a 180-s first-content timeout with elapsed-time ticker, OpenAI timeout retries, reactive caches for models that reject `temperature` or specific server-side tools.

### Architecture (mixins)

`MyAgent.py` (~280 lines) holds only `__init__` and the entry point; the `App` class inherits from **20 mixins** in `myagent/` (~14,400 lines total), all sharing state through `self.*`:

| Module | Concern |
|---|---|
| `constants.py` (2.3K lines) | Tool schemas (TOOLS / DESKTOP / BROWSER / META), safety patterns, model constants, pricing tables, capability flags |
| `helpers.py` | `HTMLTextExtractor`, `_ToolBlock` (gives OpenAI/Gemini dict tool-calls the same `.name/.id/.input` face as Anthropic's Pydantic blocks) |
| `ui_mixin` / `state_mixin` / `event_loop_mixin` | Widget construction, instance locks + geometry persistence, queue polling |
| `instructions_mixin` / `skills_mixin` | Instruction CRUD + editor, skills CRUD + system-prompt assembly |
| `streaming_mixin` | The agentic loop, tool dispatch, pricing lookup, message translation |
| `anthropic_mixin` / `openai_mixin` / `gemini_mixin` / `xai_mixin` / `ollama_mixin` | One streaming caller per provider |
| `mcp_mixin` | Async MCP client on a background event loop |
| `gmail_mixin` / `protonmail_mixin` / `outlook_mixin` | The three 16-tool mail families |
| `document_mixin` / `file_mixin` | read_document; native read/edit/write/glob/grep file tools (Claude-Code-style exact-match editing) |
| `desktop_mixin` / `browser_mixin` | pyautogui tools + coordinate pipeline, Playwright tools |
| `safety_mixin` / `chat_mixin` | Command guardrails + dialogs, chat saving / LaTeX |

Adding a static tool = schema dict in `constants.py` + an `elif` in `_execute_tool()` + a `do_<name>()` in the right mixin. Full architecture invariants (MRO-shadowing rules for the mail mixins, MCP lifecycle, DPI v2 rationale, etc.) are in [.claude/rules/CLAUDE_MYAGENT.md](.claude/rules/CLAUDE_MYAGENT.md).

---

## Zero-token automation (UnreadSummary.py & Heartbeat.py)

Two production scripts that replaced AI-run agent instructions with deterministic Python, after the realisation that *polling and list-building need no judgment* — the LLM spend belongs in the work, not the trigger.

**UnreadSummary.py** (~920 lines) — the daily unread-mail digest. One pass scans **every configured account** (Gmail, Proton Bridge / IMAP, Outlook) for unread mail in Inbox and Spam/Junk, builds one continuously-numbered COMPREHENSIVE LIST (account, from, subject, date, first ~45 words of cleaned body), matches known bill/receipt senders against the rules in **`SpecifyingList.csv`** — which lives in the OneDrive folder `MyImportant/DeathFinances` shared by all three machines (found via `%OneDrive%`/`%OneDriveConsumer%`, `~/OneDrive`, or macOS `~/Library/CloudStorage/OneDrive-*`; repo-root fallback if no OneDrive copy exists, and the file is gitignored so the OneDrive copy is the single source of truth) — (semicolon-delimited, all fields quoted; columns `To`, `From`, `From email`, `Subject` — a *prefix* match — and `Determine`, a display-only reference note echoed under the entry; the `From email` column records each sender's actual address for reference and is not read by the parser, which matches by column name), saves matched PDF attachments to `~/Downloads` idempotently, marks matches read, and emails the digest from the Outlook account. Replaced a ~$0.57/day Haiku instruction.

The AI version's "building the list is STRICTLY READ-ONLY" prompt guard holds here **by construction**: listing uses only read-only primitives (IMAP `EXAMINE` + `BODY.PEEK`, Gmail `messages.get`, Graph `GET`), and every mutation sits behind a flag (`SAVE_MATCH_PDFS` / `MARK_MATCHES_READ` on, `TRASH_MATCHES` off by default). `python UnreadSummary.py --dry-run` prints the would-be email with zero mutations and no send. Auth is silent-only — a dead token becomes an `ERROR:` line in the digest, repaired by running MyAgent once interactively. Logs: one line per pass to `~/Library/Logs/myagent/unread_summary.log` (repo-root fallback on Windows), self-rotating past ~100 KB to a one-slot `unread_summary.log.old` archive via the shared `rotate_log_if_needed`.

**Heartbeat.py** (~300 lines) — on-demand agent dispatch from anywhere you can send an email. launchd (Mac mini, short `StartInterval`) and Task Scheduler (Windows, `MyAgent_Heartbeat_5min` every 5 min) both run it against the **same** Gmail account; each pass checks for an **unread** message with that machine's exact subject — **`APW`** (Windows) or **`APM`** (macOS), derived from `platform.system()` so the same file serves both. The per-machine subject routes each trigger to exactly one executor — no tick-timing race, no machine-specific instruction landing on the wrong box:

- body line 1 → the name of a saved instruction; lines 3+ → the new prompt core
- the instruction's text between its **two** `*****` marker lines is rewritten (header and footer preserved; atomic temp-file replace beside the store — it edits the same OneDrive-shared `agent_instructions.json` the spawned MyAgent reads, resolved via `myagent/datapaths.py`; MyAgent-identical JSON formatting); fewer than two markers poison-pills the trigger instead of mangling the instruction
- `python MyAgent.py -l "<name>" --headless` is spawned detached, the email is marked read

Replaced an AI polling instruction that cost ~$14/day to decide, every 10 minutes, that there was nothing to do. Malformed triggers are poison-pilled (marked read + logged) rather than retried forever; one trigger drains per pass; auth never goes interactive; each pass logs one line to `heartbeat.log`, making timestamp gaps a liveness record. The log is self-rotating: a pass that finds it over ~100 KB (`LOG_MAX_BYTES`, roughly 2,000 lines) renames it to `heartbeat.log.old` — one archive slot, replaced each rotation — and restarts it with a marker line, so the tick log never grows unbounded (the mechanics are the shared `rotate_log_if_needed` in `myagent/helpers.py`, the same one-slot rotation that caps `APICostLog.txt`). A **watchdog** guards each spawn: if the previous run is under 15 minutes old the trigger defers to the next tick; anything older is killed and replaced — which also reaps runs parked on a forgotten dialog. On that note, **save trigger-target instructions with Convo off** (plus the confirm-bypasses they need): headless hides only the main window, dialogs deliberately stay visible, so a Convo-mode instruction finishes its work and then sits on a `user_prompt` dialog until the watchdog clears it. Windows port included (Task Scheduler + CIM process discovery) — note that spawned agents inherit the **scheduler's** environment, not your shell rc: on macOS the API keys must be embedded in the plist's `EnvironmentVariables` (chmod 600), on Windows `setx`-style user env vars work.

---

## Scheduling background runs (launchd / Task Scheduler)

Any saved instruction can run unattended: macOS via a LaunchAgent at `~/Library/LaunchAgents/com.myagent.<slug>.plist` firing `python MyAgent.py -l "<Instruction>" --headless` on a `StartCalendarInterval`; Windows via Task Scheduler + `pythonw.exe`. A "Schedule Manager" pattern also exists: a conversational instruction with just `run_command` + `user_prompt` that lists, edits, lints (`plutil -lint`), loads and verifies the `com.myagent.*` jobs from plain-English requests — run it in the GUI, never headless.

Inspect from the terminal:

```bash
launchctl list | grep myagent                          # loaded jobs: PID, last exit code
launchctl list com.myagent.<slug>                      # what it runs (NOT the schedule)
plutil -p ~/Library/LaunchAgents/com.myagent.<slug>.plist   # the schedule lives here

# Find every time-scheduled job on the machine
for d in ~/Library/LaunchAgents /Library/LaunchAgents /Library/LaunchDaemons; do
  for f in "$d"/*.plist; do [ -e "$f" ] || continue
    plutil -p "$f" 2>/dev/null | grep -qE '"StartCalendarInterval"|"StartInterval"' \
      && { echo "── $f"; plutil -p "$f" | grep -E '"(Label|Hour|Minute|Weekday|Day)"|[0-9]+ => "'; }
  done
done
```

Hard-won gotchas:

- **`launchctl` ≠ the schedule** — the *when* lives only in the plist.
- **Missed runs fire once on wake**, not as catch-up replays (a 07:00 job firing at 11:17 after the lid opens is expected).
- **Never log to `/tmp`** (purged ~3-daily); use absolute paths under `~/Library/Logs/` — launchd does **not** expand `~` in plist strings.
- **Headless + destructive tools needs confirm-bypass** in the instruction's Safety list, or the run hangs forever on an invisible dialog.
- **Three layers prove a run worked:** `LastExitStatus`, the timestamped transcript in `saved_chats/`, and the real-world side effect. Exit 0 alone proves nothing about delivery.

---

## Account_Activity_WBC.py — Bank Transaction Extractor

A single-file tkinter utility that connects to **Microsoft Edge via CDP** (port 9222), finds the Westpac account-activity tab, clicks **"Display more"** N times with a configurable delay, waits for the DOM row count to stabilise, then extracts the transactions `<tbody>` in 50-row JavaScript chunks (avoiding Playwright string truncation) and converts the Knockout.js-bound HTML to CSV with regex parsing (date, description, debit/credit via `IsDebit` blocks, running balance).

Outputs `Account_Activity_WBC.txt` (raw HTML) and `Account_Activity_WBC.csv` (`Date, Description, Debit, Credit, Balance`) in the repo root — both gitignored. Start Edge yourself first: `& "msedge.exe" --remote-debugging-port=9222` (the app deliberately never launches the bank session for you). Requires `playwright` installed; no state file — set the three inputs each run. The CSV opens nicely in CSVEditor, whose date sort and filters were built for exactly this output.

---

## CSVEditor.py — Lightweight CSV Editor

A single-file spreadsheet-style editor (~670 lines) used mostly on the bank CSV and `SpecifyingList.csv`:

- **Dialect preservation** — on open, the delimiter (`,` `;` tab `|`) is sniffed and a quote-all heuristic detects "every field quoted" files; saves reproduce both, so a `;`-delimited fully-quoted file round-trips byte-faithfully.
- **Three independent filters** (column + value comboboxes, values populated from the data), with a live "Showing X of Y rows" status.
- **Sort by Date** toggle — recognises a `Date` column and parses `D/M/Y`, ISO, `D Mon Y` and friends; unparseable rows sink to the end.
- **Editing** — double-click any cell for an in-place entry (Enter commits, Esc cancels); insert row above/below, copy row, delete row, with selection restored after each operation.
- **State** lives *outside* the repo at `~/.config/csveditor/state.json` (geometry, last file, filters, sort — a legacy repo-root state file migrates there automatically on first run).

On macOS, `CSVEditor.app` (see desktop launchers) gives it a Dock-less double-click launch that focuses the existing window instead of starting a second copy; on Windows, a Desktop shortcut runs `desktop_launchers/CSVEditor_Win.ps1` (icon `icon_csv.ico`) for the same launch-or-focus behaviour.

---

## TodoList.py — Todo Manager

A single-file tkinter todo app (~520 lines): tasks have **priority** (High/Medium/Low), **category** (default set + any new name you type becomes a saved category), optional **due date** (DD/MM/YYYY validated), creation date, and a done flag. The list view highlights High-priority tasks in red, greys out completed ones, and gives **overdue** tasks a red background; click any column heading to sort (priority order is semantic, dates parse properly), filter by status/priority/category, and reorder with Move Up/Down. Double-click toggles done. `LaunchTodoList.bat` is the Windows one-click launcher; point the Desktop shortcut's icon at `desktop_launchers/icon_todolist.ico` (a clipboard with a googly-eyed pencil ticking the last urgent item — recipe in `desktop_launchers/README.md`). On macOS, `desktop_launchers/rebuild.sh` builds the matching `TodoList.app` (launch-or-focus, same icon) from `TodoList_launcher.applescript`.

**Cross-machine sync** — `todos.json` lives in **`<OneDrive>/MyAppShare/todos.json`** (the suite-wide shared dir, alongside MyAgent/SelfBot's stores; its own `<OneDrive>/TodoList/` home until 2026-07-19 — a leftover old folder is folded in automatically, merging as a conflict fork if both copies exist), auto-located per platform (Windows: the `OneDrive`/`OneDriveConsumer`/`OneDriveCommercial` env vars; macOS: `~/Library/CloudStorage/OneDrive-*`, legacy `~/OneDrive`), overridable with a `TODOLIST_DATA_DIR` env var, falling back to the script directory on machines without OneDrive (the title bar shows "— synced" when the shared file is in use). The first run to find the shared slot empty seeds it from the local file, so an existing list migrates rather than starting blank. Concurrent use is handled three ways: a 5-second mtime poll adopts another machine's synced write as soon as it lands (safe because every action saves immediately — there's no dirty state; paused while the edit dialog is open), saves that would overwrite a file that changed underneath ask **Overwrite / Reload** instead of silently clobbering, and writes are atomic (temp file + `os.replace`) so OneDrive never syncs a half-written JSON. `todo_state.json` (geometry/filters/sort) is deliberately per-machine and stays in the script directory; both files are gitignored — OneDrive, not git, is the sync channel.

**Native C++ port (macOS)** — `TodoList.mm` is a functionality-identical Objective-C++/Cocoa port; `./build_todolist_native.sh` compiles it to `TodoList.exe` (an arm64 Mach-O in the repo root, gitignored — the `.exe` name is just a name) with zero dependencies beyond the Xcode Command Line Tools. The data layer is Foundation-only C++ (same path resolution incl. the legacy-dir fold, same 5-second poll, conflict-fork absorption, Overwrite/Reload dialog, atomic writes) and goes through `NSJSONSerialization`, so **both implementations round-trip the same synced `todos.json`** — a Windows machine can keep running `TodoList.py` against the file a Mac writes natively, and they share the same per-machine `todo_state.json` schema (geometry/filters/sort restore across the pair). The build script first runs a headless characterization suite (`tests/test_todolist_native.mm`, ~90 checks: date-parsing strictness, Python-sort stability incl. `reverse=True` semantics, fork merging, legacy migration, state files) plus a Python↔native JSON interop round-trip. `TodoList (Native).app` (built by `rebuild.sh`) is its launch-or-focus Desktop launcher — it focuses a running instance of *either* implementation, since a native+Python pair would race the shared sync poll just like two Python instances.

**Native C++ port (Windows)** — `TodoList.cpp` is the Windows twin: the same functionality-identical port as a single C++/Win32 file (ListView with the app's colors via custom draw, Per-Monitor-V2 DPI with live rescale, modal edit dialog, the 5-second poll and Overwrite/Reload dialog), compiled by `.\build_todolist_native.ps1` to the same gitignored repo-root `TodoList.exe` name (an x64 PE here; the build needs any Visual Studio edition with the C++ workload, located via `vswhere` — no PATH setup). Win32 has no system JSON, so the port hand-rolls a reader/writer that reproduces `json.dump(data, f, indent=2)` **byte-for-byte** — ensure_ascii `\uXXXX` escapes, CRLF (Python's text mode on Windows), insertion-ordered keys, unknown-key preservation, raw number tokens passed through — making a native save indistinguishable from a Python save (the macOS port only guarantees data-level equality). The build script runs `tests/test_todolist_native.cpp` (~113 checks, the `.mm` suite ported plus a section locking the JSON format) and a Python↔native interop stage that asserts **byte-identical** rewrite before compiling. `desktop_launchers/TodoListNative_Win.ps1` is its launch-or-focus Desktop launcher (icon `icon_todolist_native.ico` from the committed C++-badged master): it focuses a running `TodoList.exe` *or* `TodoList.py` before launching, and shows a "build it first" dialog when the exe is missing. Two Tk behaviours are reproduced deliberately: the ListView's six columns **stretch proportionally** to fill the widget on every resize like the Tk Treeview (a user-dragged column becomes the new basis and the others refit, so the total always equals the widget width), and the status label is text-measured and right-anchored like Tk's `pack(side=RIGHT)` (a leftover-width STATIC word-wraps overflow into clipped garbage — the 2026-07-20 "gobbledygook" bug). And because Tk on Windows is DPI-unaware, the **shared `todo_state.json` geometry is kept in Tk's virtualized 96-dpi units**: the Per-Monitor-V2 exe converts at the boundary (×dpi/96 on restore, ÷ on save — verified drift-free at 150% scale), so both implementations restore the same visual window instead of the native one opening ⅔-size on a scaled display.

---

## Desktop launchers

**macOS** — `desktop_launchers/` holds the sources for the double-clickable apps: `UnreadSummary.app` (runs the digest on demand with a success chime + self-dismissing dialog showing the run's log line), `CSVEditor.app`, `TodoList.app` and `TodoList (Native).app` (launch-or-focus; the Native one runs the compiled `TodoList.exe` and focuses a running instance of either TodoList implementation), `My Agent.app` and `SelfBot.app` (always launch a fresh instance — both are multi-instance by design; press SelfBot twice for the self-chat pair), and the `Heartbeat Log.app` / `API Cost Log.app` viewers. Rebuild per machine with:

```bash
./desktop_launchers/rebuild.sh
```

The script patches in the local repo path, renders each 1024-px icon master into an iconset with `sips`, compiles with `osacompile`, ad-hoc signs, and pastes the icon on via `NSWorkspace.setIconForFile` (which outranks macOS's stubborn IconServices cache). The built apps live in `~/Applications` with Finder **aliases** on the Desktop — an app *running from* a TCC-protected folder (Desktop/Documents/Downloads) triggers a consent prompt after every rebuild, aliases don't. Completion feedback is deliberately a **dialog + chime, not a notification**: Notification Center permission is per-code-hash, so every rebuild would re-ask, while `display dialog` needs no permission and isn't swallowed by Focus modes. The launch lines background via `cd X && (nohup … &)` — under `do shell script` a trailing `&` on a *compound* list does not detach, which used to keep the applet alive for the app's whole lifetime and swallow every second double-click (details in the launcher README). The MyAgent/SelfBot launch lines also source `~/.zshenv` **and** `~/.zshrc` first: GUI apps start with a bare environment, and `do shell script` runs `/bin/sh` — not zsh — so no dotfile is read automatically. The API keys are split across those two files, and when `OPENAI_API_KEY` moved to `~/.zshenv` the OpenAI provider silently vanished from GUI launches (terminal launches kept working) until the launchers sourced both.

**Windows** — `UnreadSummary_Win.ps1` is the AppleScript's twin: a desktop shortcut runs it via `powershell -WindowStyle Hidden`, the venv python executes the digest, and the feedback mirrors the Mac (chime + self-dismissing success dialog with the run's log line; blocking error dialog with the log tail on failure). The repo path is resolved from the script's own location, so any clone works unedited — only the `.lnk` shortcut is per-machine (icon: `icon_unread.ico`). A `-DryRun` switch passes `--dry-run` through for end-to-end plumbing tests. `CSVEditor_Win.ps1` is the matching twin of `CSVEditor_launcher.applescript`: a `powershell -WindowStyle Hidden` shortcut launches the editor with the venv **pythonw** (no console window) and focuses an already-open window instead of starting a second copy (icon `icon_csv.ico`, rendered from `icon_csv_master.png` — a googly-eyed comma perched on a spreadsheet, generated by `make_csv_icon.py`). `TodoListNative_Win.ps1` is the twin of `TodoListNative_launcher.applescript`: launch-or-focus across **both** TodoList implementations (a running `TodoList.exe` first, then a running `TodoList.py`) since either pair would race the shared 5-second sync poll, with a "build it first" dialog when the exe hasn't been compiled on this machine (icon `icon_todolist_native.ico`). `MyAgent_Win.ps1` and `SelfBot_Win.ps1` launch their apps with the venv **pythonw** and deliberately have **no** launch-or-focus — both apps are multi-instance by design: each MyAgent claims the lowest free lock number, and SelfBot's second press opens the self-chatting peer (SelfBot.py cascades the second window so the pair don't stack; `LaunchSelfBot.bat` does the auto-positioned side-by-side duo instead). Icons `icon_myagent.ico` / `icon_selfbot.ico`. `HeartbeatLog_Win.ps1` and `CostLog_Win.ps1` are the two **viewer** twins (icons `icon_heartbeat.ico` / `icon_costlog.ico`): unlike the launchers above they open a *visible* console, paging the repo-root `heartbeat.log` (meaningful events first, idle "nothing found" ticks filtered) or `APICostLog.txt` as a spend summary (grand total, today, this month, by provider, by model, then every run most-recent-first), with explicit UTF-8 reads so Windows PowerShell 5.1 doesn't mojibake the logs' em-dashes.

Full details in [desktop_launchers/README.md](desktop_launchers/README.md).

---

## Claude Code integration

This repo is developed *with* Claude Code and configured *for* it:

- **CLAUDE.md** — project conventions and commands. The three per-app architecture files live in `.claude/rules/` (`CLAUDE_SELFBOT.md`, `CLAUDE_MYAGENT.md`, `CLAUDE_ACCOUNT.md`) with `paths` frontmatter, so each loads only when a session touches its app's files instead of costing every session ~21k tokens of context.
- **Project-scoped slash commands** in `.claude/skills/` (tracked — they work from any fresh clone):

| Command | What it does |
|---|---|
| `/sync-check` | Fresh `git fetch`, compares local vs `origin/<branch>` tips, reports ahead/behind/diverged + uncommitted changes |
| `/commit-push` | Stages tracked changes, drafts a style-matched commit message, commits with the standard trailer, pushes. Skips `.DS_Store`, scratch files, and GUI-auto-modified state files unless told otherwise |
| `/urp` | "Update README, commit, push" — re-reads recent history and refreshes the docs to match the code |
| `/launch-agent` | Kills running Python instances, launches MyAgent in the background |
| `/launch-selfbot` | Kills running Python instances, launches SelfBot in the background |
| `/run script.py` | Activates the venv and runs the named script |

All skills set `disable-model-invocation: true` — they fire only when you type them.

- **WHATIS_AI.md** — an essay on *why* LLM tool use works, told as the story of a man in a cell with only a terminal: a metaphor for how a model "experiences" API messages and tools.

---

## Cross-platform notes

A runtime `IS_WINDOWS` constant branches platform behaviour; Windows functionality is preserved exactly, macOS gets equivalents or graceful degradation:

| Feature | Windows | macOS |
|---|---|---|
| Shell tool | PowerShell | bash |
| Screenshots | `ImageGrab.grab(all_screens=True)` | Quartz `CGWindowListCreateImage` per display (pyautogui only sees the primary) |
| OCR (`read_screen_text`) | `winocr` (Windows.Media.Ocr) | Apple Vision framework via PyObjC |
| DPI awareness | SelfBot: v1 `SetProcessDpiAwareness(2)`; MyAgent: v2 `PER_MONITOR_AWARE_V2` (fixes mixed-DPI multi-monitor coordinates) | Not needed |
| Monitor enumeration | `EnumDisplayMonitors` | `CGGetActiveDisplayList` |
| Instance detection | Lock files + `OpenProcess` PID + executable-name verification | Lock files + PID check |
| Dialog placement | `transient(parent)` works across screens | `transient()` skipped (WM confines transients to the parent's screen) |
| Monospace font | Consolas | Menlo |
| Launchers | `.bat` + PowerShell positioning | `.command` / `.app` (with Homebrew PATH injection so GUI-launched MCP `npx` resolves) |
| Scheduling | Task Scheduler (`pythonw.exe`) | launchd LaunchAgents |

---

## Repository map

| Path | What it is |
|---|---|
| `SelfBot.py` | The chatbot (single file by convention) |
| `MyAgent.py`, `myagent/` | The agent entry point + 20-mixin package |
| `Account_Activity_WBC.py`, `CSVEditor.py`, `TodoList.py` | The three small single-file apps |
| `TodoList.mm`, `build_todolist_native.sh` | Native C++/Cocoa port of TodoList for macOS — the script tests (incl. a Python↔native JSON interop round-trip) then compiles to the gitignored `TodoList.exe`; launched via `TodoList (Native).app` |
| `TodoList.cpp`, `build_todolist_native.ps1`, `TodoList.rc`, `TodoList.exe.manifest` | Native C++/Win32 port of TodoList for Windows — same test-then-compile flow (its interop stage asserts a **byte-identical** rewrite of Python's JSON) to the same gitignored `TodoList.exe` name; the rc/manifest embed the icon, comctl32 v6, and Per-Monitor-V2 DPI awareness. Launched via `TodoListNative_Win.ps1` |
| `UnreadSummary.py`, `Heartbeat.py` | Zero-token production jobs (their bill-matching rules, `SpecifyingList.csv`, live in OneDrive `MyImportant/DeathFinances`, not in git) |
| `tests/` | Characterization test suite (stdlib `unittest`, no extra deps) for MyAgent's pure/model-detection helpers, the shared runtime-log rotation (`heartbeat.log` / `APICostLog.txt` / `unread_summary.log`), and the OneDrive store sync (`myagent/datapaths.py`: resolver, union merge, migration, fork absorption) — `python -m unittest discover -s tests -t .`. Also holds `test_todolist_native.mm` / `test_todolist_native.cpp`, the native TodoList ports' headless suites (~90 / ~113 checks, compiled and run by `./build_todolist_native.sh` / `.\build_todolist_native.ps1`, not by unittest) |
| `desktop_launchers/` | macOS launcher sources + `rebuild.sh`, plus the Windows `*_Win.ps1` twins (UnreadSummary / CSVEditor / TodoListNative / MyAgent / SelfBot / the two log viewers) and their `icon_*.ico` (built `.app`s / `.lnk`s are per-machine, not in git) |
| `requirements.txt` | Core dependencies |
| `*.Modelfile` ×3 | Ollama vision+tools template grafts |
| `MyAgent_Pricing.txt` | Reference for the cost-tracking pricing tables |
| `make_icon.py`, `*.ico` | Windows icon generator + generated icons |
| `LaunchSelfBot.bat`, `LaunchMyAgent.bat`, `LaunchTodoList.bat`, `LaunchMyAgent.sh`, `My Agent.command`, `selfbot_position.ps1` | Per-platform launchers |
| `CLAUDE.md` + `CLAUDE_*.md` | Claude Code project instructions / per-app architecture docs |
| `.claude/skills/` | The slash commands above |
| `WHATIS_AI.md` | The tool-use essay |
| `agent_instructions.json`, `skills.json`, `system_prompts.json` | Instruction / skill / prompt libraries — live in `<OneDrive>/MyAppShare/` (synced by OneDrive, not git; repo-root fallback without OneDrive). A leftover repo-root copy is auto-unioned into the shared store on first launch and renamed `*.migrated.bak` |
| `mcp_servers.example.json` | Tracked template for the gitignored `mcp_servers.json` |
| `make_weather_pdf.py` / `make_weather_pdf_print.py`, `plot*.py`, `create_chart.py`, `move_window.py`, `agent_demo.py` | One-off agent-written scripts kept as testbed artifacts |
| `BirdFlying.html` | Self-contained browser animation: two Australian magpies (SMIL flap-and-glide wings with white covert patches, Web Audio synthesized warble — click the scene to enable sound) flying over a stylized black-clad homestead and gum grove. No dependencies; open directly in any browser |
| `app_state*.json`, `agent_state*.json`, `todo_state.json`, `TodoList.exe`, `*.lock`, `saved_chats/`, `APICostLog.txt*`, `heartbeat.log*`, `unread_summary.log*`, `Account_Activity_WBC.{txt,csv}` | Runtime state, build artifacts, and output — created automatically, mostly gitignored (TodoList's `todos.json` data lives in `<OneDrive>/MyAppShare/`, synced by OneDrive rather than git; `TodoList.exe` is rebuilt per machine) |

**Conventions:** this is a testbed — keep code simple and focused. SelfBot, CSVEditor, TodoList and the bank extractor stay single-file (the native TodoList ports keep the convention as one file per platform, `TodoList.mm` / `TodoList.cpp`); MyAgent changes go in the appropriate mixin. Tests exist where the logic is pure: the characterization suite under `tests/` (run `python -m unittest discover -s tests -t .`), with `ruff check MyAgent.py myagent/` for linting — no mypy, and no build step for the Python apps (the `build_todolist_native.sh` / `.ps1` pair is the one compile in the repo, for the native TodoList ports). After editing a `.py` file, re-run it (closing any running instance first) — for the GUI apps, the running app is still the real test suite.
