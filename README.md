# Claude Python Testbed

A cross-platform (Windows 11 / macOS) collection of Python GUI applications and automation scripts, written almost entirely with [Claude Code](https://claude.com/claude-code). Everything is tkinter, everything runs from one cloned folder with no hardcoded paths, and the repo doubles as a living testbed for agentic-AI patterns: tool use, multi-provider streaming, desktop/browser automation, native mail integrations, MCP, and zero-token replacements for jobs that once burned API dollars.

The main residents:

| App | One-liner |
|---|---|
| **SelfBot.py** | Full-featured Claude chatbot (Anthropic-only) that can also run as *two instances talking to each other* |
| **MyAgent.py** + **myagent/** | Fire-and-forget autonomous task agent — Anthropic, OpenAI, Gemini, and Ollama (local) providers, ~82 built-in tools, MCP, native Gmail/IMAP/Outlook mail |
| **Account_Activity_WBC.py** | Browser-automation utility that extracts Westpac bank transactions to HTML + CSV |
| **CSVEditor.py** | Spreadsheet-style CSV editor with filtering, date sort, and dialect preservation |
| **TodoList.py** | Todo manager with priorities, categories, due dates, and overdue highlighting |
| **UnreadSummary.py** | Zero-token daily unread-email digest across Gmail / IMAP / Outlook accounts (production launchd job) |
| **Heartbeat.py** | Zero-token email-triggered agent dispatcher — email yourself `AGENT PROMPT WIN` / `AGENT PROMPT MAC` and that machine launches MyAgent (production launchd + Task Scheduler jobs) |

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
```

**macOS:**
```bash
brew install python-tk@3.13     # system Python's Tk is too old

git clone https://github.com/namor5772/Claude_Python_Testbed.git
cd Claude_Python_Testbed

python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Keys go in ~/.zshrc. Append them with echo rather than a terminal-embedded
# editor — bracketed-paste escape sequences pasted into an editor can corrupt
# the key (it ends up as \x1b[200~sk-...~ and every API call 400s).
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

At least one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`/`GOOGLE_API_KEY` must be set for MyAgent — **or** a local [Ollama](https://ollama.com) server, which is auto-detected at `localhost:11434` with no key at all. SelfBot requires `ANTHROPIC_API_KEY` specifically. The `.venv` is gitignored and recreated per machine; all runtime state files appear on first run.

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

A single-file (~4,250-line) tkinter chatbot for the Anthropic API that works in two modes: a normal solo chatbot, or **two instances chatting with each other** via file-based message passing.

**Core features**

- **Model / temperature / extended thinking** toolbar — model list fetched live from the API; thinking budget control; *Show Thinking* (display) and *Save Thinking* (persist signed thinking blocks into saved chats so reloaded conversations keep their reasoning context) are independent toggles.
- **Tool use** — always-on core tools (`web_search`, `fetch_webpage`, `run_command`, `csv_search`), plus **13 desktop-automation tools** (pyautogui: screenshot, click, type, OCR, clipboard, window management…) behind a Desktop checkbox and **11 browser tools** (Playwright over CDP) behind a Browser checkbox. `_get_tools()` assembles the list dynamically from the toggles.
- **Skills system** — reusable prompt fragments in `skills.json` with three modes per skill: *disabled*, *enabled* (injected into the system prompt), *on-demand* (fetched by the model via a `get_skill` tool). Shared with MyAgent.
- **System prompt library** — named prompts in `system_prompts.json` with an editor dialog.
- **Chat management** — save/load named chats (`saved_chats/*.json` + a `.txt` transcript always written alongside), image attachments, DELETE/NEW CHAT, auto-naming from the first user message.
- **Command safety** — two-tier regex guardrails (`COMMAND_BLOCKED` / `COMMAND_CONFIRM`) with a confirmation dialog marshalled onto the Tk main thread.
- **Resilience** — exponential-backoff retry (HTTP 429 capped at 60 s, 529 at 90 s, up to 10 attempts), periodic 5-second auto-save, auto-save on close, graceful paired shutdown.

**Dual-instance mode** — double-click `LaunchSelfBot.bat` (Windows) and two instances open side by side. Instance 1's reply is injected into instance 2's input and vice versa, with a configurable send delay, an Auto-chat toggle, pause/resume via pending-injection, name swapping (each instance shows the other's name as the "friend"), independent geometry/state files (`app_state.json` / `app_state_2.json`), and a paired shutdown that saves both chats and closes both windows from either [X]. Watching two Claudes interview each other is the original reason this repo exists.

Architecture notes live in [CLAUDE_SELFBOT.md](CLAUDE_SELFBOT.md). By convention SelfBot stays a single file — all changes go in `SelfBot.py`.

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

A **Provider** combobox switches between **Anthropic**, **OpenAI**, **Gemini**, and **Ollama**; only providers with a key (or a reachable Ollama server) appear. Model lists are fetched live per provider and filtered to what actually works (OpenAI: Responses-API families only; Gemini: generative, non-deprecated models). The internal message format stays Anthropic-style — translation to each API happens only at the boundary.

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
- **Server-side (replaces local search):** OpenAI gets `web_search_preview` + `code_interpreter`; Anthropic gets `web_search` + `code_execution` betas with Files-API downloads. Generated charts render inline (max 600 px) and save to `saved_chats/`. Gemini/Ollama keep local DuckDuckGo (Gemini's API can't mix built-in tools with custom function declarations).
- **Desktop (checkbox):** 13 pyautogui tools + the **Gemini-only `find_element`**, which uses Gemini's trained pointing API (with Google's exact documented prompt phrasing — the capability only fires on that wording) to turn "the blue Save button" into pixel coordinates.
- **Browser (checkbox):** 11 Playwright/CDP tools. On macOS the auto-launch search order is Brave → Chrome → Edge, with a persistent debug profile so logins survive runs.
- **Meta (checkbox):** `manage_instructions`, `manage_skills`, `run_instruction` — the agent can manage its own instruction/skill libraries and spawn other instructions as fire-and-forget headless processes (orchestrator pattern).
- **Mail (three checkboxes):** 16 Gmail + 16 IMAP/Proton + 16 Outlook tools — see the integration sections below.

**Desktop click accuracy** is a first-class concern: all providers receive plain pixel coordinates ("click what you see"), resolution caps match each API's real limit (Anthropic 1568 px / OpenAI 2048 px / Gemini 2048 px long edge), coordinates round rather than truncate, out-of-bounds clicks clamp (≤2 px) or refuse (>2 % of the dimension), per-display state is tracked separately for full vs region captures so chained region screenshots don't drift, an optional `grid=true` overlay draws a labelled 100-px coordinate grid for dense UIs, and a **Diag** checkbox logs the entire capture/click coordinate-mapping trail. OpenAI's `code_interpreter` is stripped whenever desktop tools are on — gpt-5.x otherwise inspects the image with PIL and "helpfully" pre-scales coordinates, producing double-scaled misclicks. A weak-combo warning flags provider/model choices with known-poor click precision at agent start.

### Agent Instructions

Saved task profiles in `agent_instructions.json` (tracked in git so the library syncs across machines). Each instruction stores: text, base64-embedded images, all tool toggles (Desktop/Browser/Meta/MCP/Google/IMAP/Outlook/Convo), provider + model + thinking/verbosity params, per-skill modes, and Safety bypass patterns — a complete self-contained environment per task. The editor uses a **draft/commit model** (SAVE writes to disk, Apply makes it live for the session only, [X] discards), and Apply survives restarts via a snapshot in `agent_state.json`. Skill-mode restores are deliberately session-only: `skills.json` remains the sticky global source of truth and is never silently overwritten by loading an instruction.

**Conversational mode** (Convo checkbox): for small open-weights models that won't follow "always call `user_prompt`" meta-rules, MyAgent enforces the chatbot loop itself — when the model ends a turn without asking, the app pops the prompt dialog directly and feeds the reply back in. Empty/`quit`/`exit`/`stop` ends cleanly.

### MCP Integration

A generic Model Context Protocol client (`myagent/mcp_mixin.py`) connects to stdio MCP servers (filesystem, GitHub, Slack, …) and pipes their tools through the same loop as native tools, on **all four providers**.

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

Highlights: `${NAME}` placeholders in `env` values resolve from your shell environment at spawn time (secrets never enter the JSON; substitution deliberately does **not** apply to `command`/`args`, so `ps` can't leak them); the reserved `${RANDOM_PORT}` yields a fresh OS-assigned port per occurrence so multiple MyAgent instances don't collide; tool names are namespaced `<server>__<tool>`; servers connect on a dedicated asyncio thread inside one long-lived `AsyncExitStack` (the architecture that survives anyio's cancel-scope rules — see [CLAUDE_MYAGENT.md](CLAUDE_MYAGENT.md) for the full war story, including the Windows `pythonw.exe` stderr/IOCP fix); cold-cache `npx -y` first runs get a generous 5-minute startup ceiling, warm launches connect in 1–3 s. Debug the stream with `python MyAgent.py 2> mcp.log`.

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

Live cost accounting across Anthropic / OpenAI / Gemini: each streamed call's usage (including cache write/read tokens) is priced against the longest-prefix-matched tables in `myagent/constants.py` (documented in `MyAgent_Pricing.txt`) and shown as a running blue cost line. When a run ends — GUI or headless, success or failure — one semicolon-delimited line `{timestamp};{provider};{model};{cost}` is appended to `APICostLog.txt` (gitignored, per-machine). Ollama runs are free and skip the log.

### Other niceties

- **Parallel tool execution** — parallel-safe tools (`web_search`, `fetch_webpage`, `csv_search`, `get_skill`, `read_document`) run concurrently; results are reinserted in API order.
- **LaTeX → Unicode** — assistant text is post-processed (`\frac{a}{b}` → `a/b`, Greek letters, arrows, braced super/subscripts) without touching tool output.
- **Safety dialog** — every shell `COMMAND_CONFIRM` regex and every destructive mail tool is listed with a checkbox; unchecking bypasses that confirmation *for the current instruction only* (persisted per-instruction, with `⚠` audit lines at runtime). Essential for headless scheduled runs, which would otherwise hang forever on a dialog nobody can click.
- **State persistence** — `agent_state.json` keeps provider/model/thinking/display toggles and per-monitor-configuration window geometries (dock/undock and multi-display arrangements restore correctly; off-screen or tiny windows are sanitized back on-screen).
- **Retry & timeouts** — same 429/529 exponential backoff as SelfBot, plus a 180-s first-content timeout with elapsed-time ticker, OpenAI timeout retries, reactive caches for models that reject `temperature` or specific server-side tools.

### Architecture (mixins)

`MyAgent.py` (~270 lines) holds only `__init__` and the entry point; the `App` class inherits from **19 mixins** in `myagent/` (~13,400 lines total), all sharing state through `self.*`:

| Module | Concern |
|---|---|
| `constants.py` (2.3K lines) | Tool schemas (TOOLS / DESKTOP / BROWSER / META), safety patterns, model constants, pricing tables, capability flags |
| `helpers.py` | `HTMLTextExtractor`, `_ToolBlock` (gives OpenAI/Gemini dict tool-calls the same `.name/.id/.input` face as Anthropic's Pydantic blocks) |
| `ui_mixin` / `state_mixin` / `event_loop_mixin` | Widget construction, instance locks + geometry persistence, queue polling |
| `instructions_mixin` / `skills_mixin` | Instruction CRUD + editor, skills CRUD + system-prompt assembly |
| `streaming_mixin` | The agentic loop, tool dispatch, pricing lookup, message translation |
| `anthropic_mixin` / `openai_mixin` / `gemini_mixin` / `ollama_mixin` | One streaming caller per provider |
| `mcp_mixin` | Async MCP client on a background event loop |
| `gmail_mixin` / `protonmail_mixin` / `outlook_mixin` | The three 16-tool mail families |
| `document_mixin` / `desktop_mixin` / `browser_mixin` | read_document, pyautogui tools + coordinate pipeline, Playwright tools |
| `safety_mixin` / `chat_mixin` | Command guardrails + dialogs, chat saving / LaTeX |

Adding a static tool = schema dict in `constants.py` + an `elif` in `_execute_tool()` + a `do_<name>()` in the right mixin. Full architecture invariants (MRO-shadowing rules for the mail mixins, MCP lifecycle, DPI v2 rationale, etc.) are in [CLAUDE_MYAGENT.md](CLAUDE_MYAGENT.md).

---

## Zero-token automation (UnreadSummary.py & Heartbeat.py)

Two production scripts that replaced AI-run agent instructions with deterministic Python, after the realisation that *polling and list-building need no judgment* — the LLM spend belongs in the work, not the trigger.

**UnreadSummary.py** (~870 lines) — the daily unread-mail digest. One pass scans **every configured account** (Gmail, Proton Bridge / IMAP, Outlook) for unread mail in Inbox and Spam/Junk, builds one continuously-numbered COMPREHENSIVE LIST (account, from, subject, date, first ~45 words of cleaned body), matches known bill/receipt senders against the rules in **`SpecifyingList.csv`** (semicolon-delimited, all fields quoted; columns `To`, `From`, `Subject` — a *prefix* match — and `Determine`, a display-only reference note echoed under the entry), saves matched PDF attachments to `~/Downloads` idempotently, marks matches read, and emails the digest from the Outlook account. Replaced a ~$0.57/day Haiku instruction.

The AI version's "building the list is STRICTLY READ-ONLY" prompt guard holds here **by construction**: listing uses only read-only primitives (IMAP `EXAMINE` + `BODY.PEEK`, Gmail `messages.get`, Graph `GET`), and every mutation sits behind a flag (`SAVE_MATCH_PDFS` / `MARK_MATCHES_READ` on, `TRASH_MATCHES` off by default). `python UnreadSummary.py --dry-run` prints the would-be email with zero mutations and no send. Auth is silent-only — a dead token becomes an `ERROR:` line in the digest, repaired by running MyAgent once interactively. Logs: one line per pass to `~/Library/Logs/myagent/unread_summary.log` (repo-root fallback on Windows).

**Heartbeat.py** (~280 lines) — on-demand agent dispatch from anywhere you can send an email. launchd (Mac mini, short `StartInterval`) and Task Scheduler (Windows, `MyAgent_Heartbeat_5min` every 5 min) both run it against the **same** Gmail account; each pass checks for an **unread** message with that machine's exact subject — **`AGENT PROMPT WIN`** (Windows) or **`AGENT PROMPT MAC`** (macOS), derived from `platform.system()` so the same file serves both. The suffix routes each trigger to exactly one executor — no tick-timing race, no machine-specific instruction landing on the wrong box; a bare `AGENT PROMPT` is answered by nobody:

- body line 1 → the name of a saved instruction; lines 3+ → the new prompt core
- the instruction's text between its **two** `*****` marker lines is rewritten (header and footer preserved; atomic temp-file replace, MyAgent-identical JSON formatting); fewer than two markers poison-pills the trigger instead of mangling the instruction
- `python MyAgent.py -l "<name>" --headless` is spawned detached, the email is marked read

Replaced an AI polling instruction that cost ~$14/day to decide, every 10 minutes, that there was nothing to do. Malformed triggers are poison-pilled (marked read + logged) rather than retried forever; one trigger drains per pass; auth never goes interactive; each pass logs one line to `heartbeat.log`, making timestamp gaps a liveness record. A **watchdog** guards each spawn: if the previous run is under 15 minutes old the trigger defers to the next tick; anything older is killed and replaced — which also reaps runs parked on a forgotten dialog. On that note, **save trigger-target instructions with Convo off** (plus the confirm-bypasses they need): headless hides only the main window, dialogs deliberately stay visible, so a Convo-mode instruction finishes its work and then sits on a `user_prompt` dialog until the watchdog clears it. Windows port included (Task Scheduler + CIM process discovery) — note that spawned agents inherit the **scheduler's** environment, not your shell rc: on macOS the API keys must be embedded in the plist's `EnvironmentVariables` (chmod 600), on Windows `setx`-style user env vars work.

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

A single-file spreadsheet-style editor (~580 lines) used mostly on the bank CSV and `SpecifyingList.csv`:

- **Dialect preservation** — on open, the delimiter (`,` `;` tab `|`) is sniffed and a quote-all heuristic detects "every field quoted" files; saves reproduce both, so a `;`-delimited fully-quoted file round-trips byte-faithfully.
- **Three independent filters** (column + value comboboxes, values populated from the data), with a live "Showing X of Y rows" status.
- **Sort by Date** toggle — recognises a `Date` column and parses `D/M/Y`, ISO, `D Mon Y` and friends; unparseable rows sink to the end.
- **Editing** — double-click any cell for an in-place entry (Enter commits, Esc cancels); insert row above/below, copy row, delete row, with selection restored after each operation.
- **State** lives *outside* the repo at `~/.config/csveditor/state.json` (geometry, last file, filters, sort — a legacy repo-root state file migrates there automatically on first run).

On macOS, `CSVEditor.app` (see desktop launchers) gives it a Dock-less double-click launch that focuses the existing window instead of starting a second copy; on Windows, a Desktop shortcut runs `desktop_launchers/CSVEditor_Win.ps1` (icon `icon_csv.ico`) for the same launch-or-focus behaviour.

---

## TodoList.py — Todo Manager

A single-file tkinter todo app (~450 lines): tasks have **priority** (High/Medium/Low), **category** (default set + any new name you type becomes a saved category), optional **due date** (DD/MM/YYYY validated), creation date, and a done flag. The list view highlights High-priority tasks in red, greys out completed ones, and gives **overdue** tasks a red background; click any column heading to sort (priority order is semantic, dates parse properly), filter by status/priority/category, and reorder with Move Up/Down. Double-click toggles done. Data persists to `todos.json`, window/filter state to `todo_state.json`; `LaunchTodoList.bat` is the Windows one-click launcher.

---

## Desktop launchers

**macOS** — `desktop_launchers/` holds the sources for two double-clickable apps: `UnreadSummary.app` (runs the digest on demand with a success chime + self-dismissing dialog showing the run's log line) and `CSVEditor.app` (launch-or-focus). Rebuild per machine with:

```bash
./desktop_launchers/rebuild.sh
```

The script patches in the local repo path, renders each 1024-px icon master into an iconset with `sips`, compiles with `osacompile`, ad-hoc signs, and pastes the icon on via `NSWorkspace.setIconForFile` (which outranks macOS's stubborn IconServices cache). The built apps live in `~/Applications` with Finder **aliases** on the Desktop — an app *running from* a TCC-protected folder (Desktop/Documents/Downloads) triggers a consent prompt after every rebuild, aliases don't. Completion feedback is deliberately a **dialog + chime, not a notification**: Notification Center permission is per-code-hash, so every rebuild would re-ask, while `display dialog` needs no permission and isn't swallowed by Focus modes.

**Windows** — `UnreadSummary_Win.ps1` is the AppleScript's twin: a desktop shortcut runs it via `powershell -WindowStyle Hidden`, the venv python executes the digest, and the feedback mirrors the Mac (chime + self-dismissing success dialog with the run's log line; blocking error dialog with the log tail on failure). The repo path is resolved from the script's own location, so any clone works unedited — only the `.lnk` shortcut is per-machine (icon: `icon_unread.ico`). A `-DryRun` switch passes `--dry-run` through for end-to-end plumbing tests. `CSVEditor_Win.ps1` is the matching twin of `CSVEditor_launcher.applescript`: a `powershell -WindowStyle Hidden` shortcut launches the editor with the venv **pythonw** (no console window) and focuses an already-open window instead of starting a second copy (icon `icon_csv.ico`, rendered from `icon_csv_master.png` — a sunglasses-wearing semicolon dethroning the comma over a spreadsheet).

Full details in [desktop_launchers/README.md](desktop_launchers/README.md).

---

## Claude Code integration

This repo is developed *with* Claude Code and configured *for* it:

- **CLAUDE.md** — project conventions and commands, importing three per-app architecture files (`CLAUDE_SELFBOT.md`, `CLAUDE_MYAGENT.md`, `CLAUDE_ACCOUNT.md`) via `@`-includes.
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
| `MyAgent.py`, `myagent/` | The agent entry point + 19-mixin package |
| `Account_Activity_WBC.py`, `CSVEditor.py`, `TodoList.py` | The three small single-file apps |
| `UnreadSummary.py`, `Heartbeat.py`, `SpecifyingList.csv` | Zero-token production jobs + the bill-matching rules |
| `desktop_launchers/` | macOS launcher sources + `rebuild.sh`, plus the Windows `UnreadSummary_Win.ps1` / `CSVEditor_Win.ps1` twins and their `icon_*.ico` (built `.app`s / `.lnk`s are per-machine, not in git) |
| `requirements.txt` | Core dependencies |
| `*.Modelfile` ×3 | Ollama vision+tools template grafts |
| `MyAgent_Pricing.txt` | Reference for the cost-tracking pricing tables |
| `make_icon.py`, `*.ico` | Windows icon generator + generated icons |
| `LaunchSelfBot.bat`, `LaunchMyAgent.bat`, `LaunchTodoList.bat`, `LaunchMyAgent.sh`, `My Agent.command`, `selfbot_position.ps1` | Per-platform launchers |
| `CLAUDE.md` + `CLAUDE_*.md` | Claude Code project instructions / per-app architecture docs |
| `.claude/skills/` | The slash commands above |
| `WHATIS_AI.md` | The tool-use essay |
| `agent_instructions.json`, `skills.json`, `system_prompts.json` | Instruction / skill / prompt libraries (`agent_instructions.json` is tracked so instructions sync across machines) |
| `mcp_servers.example.json` | Tracked template for the gitignored `mcp_servers.json` |
| `make_weather_pdf.py` / `make_weather_pdf_print.py`, `plot*.py`, `create_chart.py`, `move_window.py`, `agent_demo.py` | One-off agent-written scripts kept as testbed artifacts |
| `BirdFlying.html` | Self-contained browser animation: two Australian magpies (SMIL flap-and-glide wings with white covert patches, Web Audio synthesized warble — click the scene to enable sound) flying over a stylized black-clad homestead and gum grove. No dependencies; open directly in any browser |
| `app_state*.json`, `agent_state*.json`, `*.lock`, `saved_chats/`, `APICostLog.txt`, `Account_Activity_WBC.{txt,csv}` | Runtime state and output — created automatically, mostly gitignored |

**Conventions:** this is a testbed — keep code simple and focused. SelfBot, CSVEditor, TodoList and the bank extractor stay single-file; MyAgent changes go in the appropriate mixin. There are no tests, linters, or build steps. After editing a `.py` file, re-run it (closing any running instance first) — the apps are their own test suite.
