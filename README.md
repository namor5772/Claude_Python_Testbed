# Claude Python Testbed

A cross-platform (Windows 11 / macOS) collection of Python GUI applications and automation scripts, written almost entirely with [Claude Code](https://claude.com/claude-code). The Python apps are tkinter, everything runs from one cloned folder with no hardcoded paths, and the repo doubles as a living testbed for agentic-AI patterns: tool use, multi-provider streaming, desktop / browser automation, native mail integrations, MCP, and zero-token scripts for the jobs that need no model at all.

The main residents:

| App | One-liner |
|---|---|
| **SelfBot.py** | Full-featured Claude chatbot (Anthropic-only) that can also run as *two instances talking to each other* |
| **MyAgent.py** + **myagent/** | Fire-and-forget autonomous task agent — Anthropic, OpenAI, Google (Gemini), xAI (Grok), Moonshot (Kimi) and Ollama (local) providers, 99 built-in tools, MCP, native Gmail / IMAP / Outlook mail, live Excel automation, a webcam and a microphone, dictated replies, and a mid-run model upgrade |
| **UnreadSummary.py** | Zero-token daily unread-email digest across Gmail / IMAP / Outlook accounts (a launchd / Task Scheduler job) |
| **Heartbeat.py** | Zero-token email-triggered agent dispatcher — email yourself `APD` / `APL` / `APM` and that machine launches MyAgent (launchd + Task Scheduler jobs) |
| **CSVEditor.py** | Spreadsheet-style CSV editor with filtering, sorting and dialect preservation |
| **TodoList.py** | Todo manager with priorities, categories, due dates and overdue highlighting — one OneDrive-synced list across all machines. `TodoList.mm` (macOS / Cocoa) and `TodoList.cpp` (Windows / Win32) are functionality-identical native C++ ports that round-trip the same synced file |
| **MyBackup.py** | Directory mirror backup — a FROM / TO table of directories, one **BACKUP** button that makes every TO an exact copy of its FROM (new and changed files copied, extras deleted), and a `--headless` mode for a scheduled run |

---

## Contents

- [Quick Start (new machine)](#quick-start-new-machine)
- [SelfBot.py](#selfbotpy--claude-chatbot--dual-instance-self-chat)
- [MyAgent.py](#myagentpy--autonomous-ai-task-agent)
  - [Agentic loop](#how-the-agentic-loop-works) · [Command line](#command-line-launch) · [Providers](#providers--model-controls) · [Ollama](#ollama-local-inference) · [Tools](#tool-catalog) · [Instructions](#agent-instructions) · [Skills](#skills) · [Store sync](#cross-machine-store-sync) · [MCP](#mcp-integration) · [Gmail](#gmail-integration-native-google-tools) · [Proton / IMAP](#proton-mail--imap-integration) · [Outlook](#outlook--microsoft-365-integration) · [Excel](#excel-live-workbook-integration) · [Physical](#physical-tools-camera-and-microphone) · [Voice input](#voice-input-agent-request-dialog) · [Model upgrade](#model-upgrade-agent-request-dialog) · [Cost tracking](#api-cost-tracking) · [Other niceties](#other-niceties) · [Keyboard](#keyboard-operation-no-mouse-needed) · [Architecture](#architecture-mixins)
- [Zero-token automation](#zero-token-automation-unreadsummarypy--heartbeatpy)
- [Scheduling background runs](#scheduling-background-runs-launchd--task-scheduler)
- [CSVEditor.py](#csveditorpy--lightweight-csv-editor)
- [TodoList.py](#todolistpy--todo-manager)
- [MyBackup.py](#mybackuppy--directory-mirror-backup)
- [Desktop launchers](#desktop-launchers)
- [Claude Code integration](#claude-code-integration)
- [Cross-platform notes](#cross-platform-notes)
- [OneDrive shared folders](#onedrive-shared-folders)
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
export GEMINI_API_KEY="..."               # optional — MyAgent Google (Gemini) provider
export XAI_API_KEY="xai-..."              # optional — MyAgent xAI (Grok) provider
export MOONSHOT_API_KEY="sk-..."          # optional — MyAgent Moonshot (Kimi) provider
```

**macOS:**
```bash
brew install python-tk@3.13     # the system Python's Tk is too old

git clone https://github.com/namor5772/Claude_Python_Testbed.git
cd Claude_Python_Testbed

python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Keys go in ~/.zshrc or ~/.zshenv — zsh terminals read both, and the Desktop
# .app launchers source both explicitly (AppleScript's `do shell script` runs
# /bin/sh, not zsh, so neither file is read automatically — see
# desktop_launchers/README.md). Append them with echo rather than a
# terminal-embedded editor: bracketed-paste escape sequences pasted into an
# editor can corrupt a key (it ends up as \x1b[200~sk-...~ and every API call 400s).
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.zshrc
source ~/.zshrc
```

**Requirements** (`requirements.txt`): `anthropic`, `openai`, `google-genai`, `google-api-python-client`, `google-auth-oauthlib`, `google-auth-httplib2`, `msal`, `requests`, `ollama`, `ddgs`, `httpx`, `pyautogui`, `pygetwindow`, `Pillow`, `pypdf`, `python-docx`, `xlwings`, `sounddevice` (microphone capture for MyAgent's [voice input](#voice-input-agent-request-dialog) and `microphone_listen`; its wheels bundle PortAudio on Windows and macOS, so there is nothing else to install — without it MyAgent still runs and the Mike button answers with the `pip install` hint).

**Optional extras**, installed only when you want the feature:

| Package | Enables |
|---|---|
| `playwright` | Browser tools (a CDP connection to Edge / Chrome / Brave — **no** `playwright install` needed, the system browser is used) |
| `mcp` (+ `pywin32` on Windows) | External MCP servers via `mcp_servers.json` |
| `pyperclip` | Unicode text input via clipboard paste (desktop tools) |
| `winocr` | `read_screen_text` OCR on Windows (macOS uses the built-in Vision framework) |
| `opencv-python` | `find_image_on_screen` confidence matching, and the Physical tools' `camera_capture` (webcam photos — see [Physical tools](#physical-tools-camera-and-microphone)) |

MyAgent needs at least one of `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY` / `GOOGLE_API_KEY`, `XAI_API_KEY` or `MOONSHOT_API_KEY` / `KIMI_API_KEY` — **or** a local [Ollama](https://ollama.com) server, auto-detected at `localhost:11434` with no key at all. SelfBot needs `ANTHROPIC_API_KEY`. The `.venv` is gitignored and recreated per machine; every runtime state file appears on first run.

Run anything with the venv active:

```bash
python SelfBot.py
python MyAgent.py
python CSVEditor.py
python TodoList.py
python MyBackup.py
python MyBackup.py --headless              # mirror the saved FROM / TO table with no window — the scheduled form
python UnreadSummary.py --dry-run          # the would-be digest: no mailbox changes, no email sent
python -m unittest discover -s tests -t .  # the test suite
```

---

## SelfBot.py — Claude Chatbot & Dual-Instance Self-Chat

A single-file (~7,400-line) tkinter chatbot for the Anthropic API — Anthropic-only by design — that works in two modes: a normal solo chatbot, or **two instances chatting with each other** via file-based message passing.

**Core features**

- **Model / temperature / thinking** — the model list is fetched live from the API with retiring ids filtered out, so new models appear on their own. Per-model parameters are version-parsed rather than hardcoded: adaptive models (Opus / Sonnet ≥ 4.6, Fable / Mythos 5 and 5.1) get a Thinking-mode combobox — the always-on models (Fable / Mythos 5 and 5.1, Opus 5.5) have no *Off*, and models that reject temperature never receive it — while the 4.5 family keeps the manual checkbox + token budget. The always-on models' requests carry the same preserved-thinking binding and server-side refusal fallbacks as MyAgent (see [Anthropic request surface](#anthropic-request-surface)): a mid-chat prompt, tool or model switch, a reloaded chat or the overflow trim cannot 400 on bound thinking blocks, a classifier decline is re-run on an Opus-tier model inside the same call (priced at that model's rates), and a decline with no fallback is shown as a ⚠ line rather than an empty reply. *Show Thinking* (display) and *Save Thinking* (keep signed thinking blocks in saved chats, so a reloaded conversation keeps its reasoning context) are independent toggles.
- **Tool use** — `run_command` and `csv_search` are always on, plus Anthropic's server-side web search, web fetch and code execution. A second checkbox row gates the rest: **Desktop** (13 pyautogui tools — screenshot, click, type, keys, scroll, drag, windows, clipboard, OCR, image search), **Browser** (11 Playwright / CDP tools), MyAgent's **MCP / Google (Gmail) / IMAP / Outlook** subsystems (reused *by inheritance* from the `myagent` mixins: stdio MCP servers and the three 16-tool mail families), a SelfBot-native **Meta** pair (`manage_skills`, including each skill's bundled resource files, and `manage_prompts`) and the **Pause** rest tool (below). `_get_tools()` assembles the list from the toggles on every call.
- **Skills** — the same OneDrive-synced `skills/` tree MyAgent uses (see [Skills](#skills)): one `SKILL.md` per skill, three modes (*disabled*, *enabled* — injected into the system prompt — and *on-demand* — fetched by the model through `get_skill`), an optional what + when description listed in the system prompt's on-demand index, and kebab-case names enforced on create. The Skills Manager shows the list under a fixed **SKILLS** band, cycles modes with Cycle Mode or Space, edits the description in its own box, asks before DELETE, and on a SAVE under a new name while another skill is loaded copies that skill's bundled resource files into the new folder.
- **System prompt library** — named prompts in `system_prompts.json` (OneDrive-synced; the title bar shows "— synced") with an editor dialog. Each prompt bundles a full main-screen *environment* — terminal names, model + thinking parameters, tool toggles, per-skill modes (applied for the session only) and Safety bypasses — restored whole when the prompt loads; the remembered prompt is authoritative at startup. The model can manage the library itself via `manage_prompts`, whose `apply` action switches the live session to a saved prompt.
- **Chat management** — save / load named chats (`saved_chats/*.json`, with a `.txt` transcript written alongside), image attachments, NEW CHAT, and DELETE (which asks first, default **No**). An unnamed chat is auto-named from its first user message, and typing `/exit`, `/quit` or `/bye` starts a new chat. The seven main-window buttons — DELETE, NEW CHAT, SAVE, Attach Images, System Prompt, Skills, Safety — show which one was pressed last (light blue with a bold label), the same cue as MyAgent's toolbar; the red / green Auto toggle keeps its own colours.
- **Command safety** — two-tier regex guardrails (`COMMAND_BLOCKED` rejects outright, `COMMAND_CONFIRM` asks) with the confirmation dialog marshalled onto the Tk main thread. The **Safety** dialog lists every confirm pattern and every destructive mail tool with a checkbox: unticked means bypassed — the action runs unasked and logs a red ⚠ audit line — and a command matching several patterns runs unasked only when all of them are unticked. The pattern lists are MyAgent's; the bypass set lives in `app_state.json` and in each saved system prompt.
- **Cost tracking** — every call is priced from its usage across the four token buckets (input / output / cache write / cache read) plus Anthropic's per-search web-search fee, shown per call and as a running session total (Activity toggle), and written on close as one line in the per-machine cost log MyAgent uses (see [API cost tracking](#api-cost-tracking)): the parameters in effect, an empty duration field (a chat's duration is window-open time, not a run metric), the active system prompt's name, the session's API-call count and its four token totals. SelfBot sets Anthropic prompt-cache breakpoints (the system prompt plus two rolling markers), which matters most for a duo self-chat: its history grows without bound and every turn re-sends all of it.
- **Resilience** — exponential-backoff retry (HTTP 429 capped at 60 s, 529 at 90 s, up to 10 attempts); **context-overflow recovery**: a `prompt is too long` 400 trims the oldest rounds — only at real user-turn boundaries, so a `tool_use` / `tool_result` pair is never orphaned, always keeping the last two rounds — and retries, so a duo keeps talking on a sliding window instead of stopping at the context limit; a 5-second periodic auto-save and an auto-save on close; and a close sequence that always completes (auto-save names are sanitized, and a failed save never blocks [X]).
- **Window geometry** — the main window and the three dialogs (System Prompt Editor, Skills Manager, Safety) remember size and position per instance (`app_state.json` / `app_state_2.json`) and per monitor layout, checked against every monitor (so a window on a monitor left of the primary comes back there); a dialog whose saved position no monitor shows keeps its saved size, centred on the main window. On Windows a window restored onto a screen whose scaling differs from the primary's comes back at exactly its saved size (left to Tk alone it grew by one frame width per launch).

**Dual-instance mode** — double-click `LaunchSelfBot.bat` (Windows) and two instances open side by side — note that it first force-kills every running `python.exe` / `pythonw.exe`, MyAgent windows and scheduled headless runs included — or press the SelfBot Desktop launcher twice (Windows or macOS; the second window cascades off the first). Instance 1's reply is injected into instance 2's input and vice versa, with a configurable send delay, an **Auto** chat toggle, name swapping (each instance shows the other's name as the "friend"), independent state files, and a paired shutdown: closing either window finishes any streaming reply, saves both chats and closes both. Windows pairs the instances by window title and closes the peer with `WM_CLOSE`; macOS pairs them by *process* (reading another app's window title needs Screen Recording consent) and closes the peer with `SIGTERM`, which SelfBot handles as a graceful close. A watchdog in the peer poll covers ungraceful death too — a crash, `taskkill /F`, or a model ending its own process via `run_command`: once paired, a survivor whose peer disappears without the close broadcast runs the same graceful close instead of lingering alone. A force-killed instance loses its own in-flight reply, so a model that wants a clean exit should use `taskkill` without `/F`, which runs the full paired shutdown.

The **Pause** toggle gives the model a `pause_conversation` tool — a hang-up that isn't a shutdown: it switches auto-chat off, so the conversation goes quiet with both windows open. The pause is a rest, not a latch: the next fresh message (typed, injected by the peer, or Auto: ON) resumes it, while a human's manual Auto: OFF is never resumed automatically. The tool's description is deliberately neutral about pausing versus closing, so the exit a duo takes is the model's own choice.

Architecture notes: [.claude/rules/CLAUDE_SELFBOT.md](.claude/rules/CLAUDE_SELFBOT.md). By convention SelfBot stays a single file — all changes go in `SelfBot.py` — and stays Anthropic-only.

---

## MyAgent.py — Autonomous AI Task Agent

A fire-and-forget task runner: you write an **Instruction** (the task), pick a **Provider + Model**, press **START**, and the agent loops — calling tools, reading results, calling more tools — until the task is done. You are a passive observer unless the agent asks for input through its **Agent Request** dialog (`user_prompt`) or a guarded action needs your confirmation. The toolbar reads **START · STOP · Instruction**; the button pressed last is shown light blue with a bold label, and STOP can be pressed only while a run is active.

### How the agentic loop works

1. **Configure** — write or load an Agent Instruction (optionally with attached reference images).
2. **START** (or `-l` from the command line) — the instruction becomes the first user message and a background thread runs the loop.
3. **Loop** — `stream_worker()` streams each response. On `tool_use` it executes the requested tools (the parallel-safe ones first, concurrently, then the rest in order), appends the results and calls the API again; on `end_turn` the task is complete. Once a run has called `user_prompt` two or more times, a turn that ends without asking is nudged back to it (up to three times), so an established question-and-answer loop doesn't end by accident; [Conversational mode](#agent-instructions) enforces the loop outright. On Anthropic, a `pause_turn` (a long server-tool turn paused by the API) resumes automatically, and a reply cut off at the output limit or the context limit posts a ⚠ (an unfinished tool call is not run) — a run that ends there reports `error` in its result file. Every Anthropic call sends the model's own output ceiling as `max_tokens` (since 2026-10-11: 128,000 tokens on every current Claude model, 64,000 on the dated 4.5 ids, read from the Models API at startup), thinking on or off — thinking counts against it, and the ⚠ says so; before, every thinking-on call was capped at 32,768, which a Fable 5.1 call at Max effort could spend entirely on reasoning and be cut off before writing a word, the whole output billed and lost. Kimi flags a truncated reply with its own warning.
4. **STOP** — cancels mid-stream, not just between calls; tools still queued in the turn are answered `[Not run: the user pressed STOP]`, so no click or command runs after it.

There is no fixed iteration limit; a **Call #N** counter (shown with Activity, Tool Calls or Debug on) counts the API round-trips.

### Command-line launch

```bash
python MyAgent.py                                                # normal GUI launch
python MyAgent.py -l "Weather_Agent_Skill_based"                 # auto-load an instruction + START
python MyAgent.py -l "Weather_Agent_Skill_based" --headless      # no main window; closes when the run ends
python MyAgent.py -l "Name" --headless --result-file out.json --extra-file brief.txt
```

- `-l` restores the instruction's whole environment — text, images, tool toggles, provider, model, thinking parameters, skill modes, Safety bypasses, blocklist — and fills "Save Chat as" with `{Name}_{timestamp}`, so the run's chat is saved.
- `--headless` withdraws the main window (dialogs still appear when needed), never writes the state file — a scheduled child's instruction and model are not the user's — and closes the process when the loop ends. It is the backbone of every scheduled job below.
- `--result-file PATH` writes a JSON outcome, `{instruction, status: completed|stopped|error, error, final_text}`, when the loop ends — the channel `run_instruction(wait=true)` uses, and handy for any script that needs the run's answer rather than just its exit code. A GUI run given a result file closes at the end too.
- `--extra-file PATH` (with `-l`) appends the file's text to the instruction as an *ADDITIONAL TASK CONTEXT* block for that run only, so one saved instruction can be parameterized per run.
- An unattended launch (`--headless` or `--result-file`) that cannot start — an unknown instruction name, an empty instruction — writes the error to its result file and exits instead of waiting on a dialog nobody will answer.

Several instances can run at once: each claims the lowest free number through PID-verified lock files (`agent_lock_N.lock`, atomic `O_CREAT|O_EXCL` claims; a stale lock from a crashed process is reclaimed under a per-slot mutex, so racing launches never share a slot) and keeps its own state file (`agent_state.json`, `agent_state_2.json`, …); the title bar shows `My Agent (N)` from the second instance on.

### Providers & model controls

A **Provider** combobox switches between **Anthropic**, **OpenAI**, **Google**, **xAI**, **Moonshot** and **Ollama**; only providers with an API key in the environment (or a reachable Ollama server) are listed. Provider labels are company names — Google serves the Gemini models, Moonshot the Kimi models. Model lists are fetched live and filtered to what works in an agent loop:

- **Anthropic** — the live `models.list()`, minus retired and retiring ids (Opus 4.1 / 4.0, Sonnet 4.0, Claude 3.x / 2.x). Default `claude-opus-5`.
- **OpenAI** — the Responses-API `gpt-4.1`, `gpt-5` and `gpt-6` families, minus non-chat types (embeddings, audio, realtime, transcribe, TTS, …), retiring ids (the GPT-5.0 base tiers, `gpt-5-chat*`, `gpt-5.1-chat*`, `gpt-5-codex*`, the bare `gpt-5.1-codex`) and the o-series. Default `gpt-5.6-terra`; `gpt-6-astra` is the flagship.
- **Google** — the generative Gemini 3.x tiers (with the floating `-latest` aliases and the `-customtools` variants), minus specialised-output models (TTS, image, music), speech-to-text, managed agents, the computer-use preview, gemma, and the retiring 2.5 family. Default `gemini-3.8-flash`.
- **xAI** — the grok chat and code tiers from `/v1/language-models` that can actually run MyAgent's loop, listed by canonical id. Image and video generation are excluded, and so is `grok-4.20-multi-agent`: it refuses client-side tools without xAI beta access ("Client-side tools for multi-agent models require beta access"), and every MyAgent request declares them. The seven other served models were each run through a real tool call and answer on 2026-09-30. An instruction pinned to a model that cannot run falls back to the default with a ⚠ line. Default `grok-4.3`. An alias such as `grok-latest` (which follows xAI's current flagship) works when an instruction names it.
- **Moonshot** — the live `kimi-k*` catalog (k3, k2.6, k2.7-code, k2.7-code-highspeed). Default `kimi-k2.6`.
- **Ollama** — whatever the local server has pulled, with capabilities auto-detected (see [Ollama](#ollama-local-inference)).

Models with an announced retirement are removed ahead of their shutdown date — hidden from the picker and unpriced — while their parameter wiring stays, so an instruction pinned to one keeps running (with a warning that it is hidden from the picker) until the provider pulls it. When an instruction's provider has no key on the launching machine, MyAgent stays on the current provider and uses its curated default model; when the saved model is no longer served, it uses the provider's curated default. Either way a ⚠ says so, re-posted at run start so an `-l` launch's screen clear cannot hide it.

The internal message format stays Anthropic-style; translation to each API happens only at the boundary. xAI rides on the OpenAI SDK pointed at `https://api.x.ai/v1` — its Responses API is OpenAI-compatible — and reuses the Responses translators with its own streaming caller; Moonshot rides the OpenAI SDK too, at `https://api.moonshot.ai/v1`, but is Chat Completions only and has its own translators ([below](#moonshot-kimi)).

The model controls live in the Instruction Editor — Provider, Model, Thinking / Reasoning, Temp (a 0.0–1.0 spinbox), Verbosity and Fast — and are saved with the instruction. They are **model-aware**: controls a model rejects are *hidden*, not just disabled, and the title bar shows the provider, the model and the thinking / temperature settings.

| Provider / family | Thinking control | Temperature |
|---|---|---|
| Anthropic always-on — Fable 5 / 5.1, Mythos 5 / 5.1, Opus 5.5 | Thinking-mode combobox **without Off** (an explicit disable is a 400): Adaptive / Low / Medium / High / Xhigh / Max. Adaptive sends no effort, so it runs at the model's default (Medium on Opus 5.5) | Never shown or sent |
| Anthropic adaptive — Opus / Sonnet ≥ 4.6, including Opus 5 and Sonnet 5 | Thinking-mode combobox: Off / Adaptive / Low / Medium / High, + Xhigh on Opus 4.7+ and Sonnet 5, + Max on Opus / Sonnet 4.6+. Opus 5 and Sonnet 5 think when the parameter is omitted, so *Off* sends an explicit `disabled` | Hidden while thinking is on; never sent to Opus 4.7+ or Sonnet 5 (a reactive 400 handler strips it and retries for any other model that rejects it) |
| Anthropic adaptive — Haiku 5.5 (since 2026-10-08, the day after it shipped) | Thinking-mode combobox: Off / Adaptive / Low / Medium / High / Xhigh / Max. It thinks when the parameter is omitted, so *Off* sends an explicit `disabled` (the API accepts that up to effort High, and Off sends no effort); Adaptive runs at the model's default, Medium; a token budget is a 400 on it, which is why it left the manual row below | Never shown or sent (any temperature but 1 is a 400) |
| Anthropic manual — the 4.5 family (Opus, Sonnet, Haiku) | Checkbox + token budget (1K / 4K / 8K / 16K / 32K) | Shown when thinking is off |
| OpenAI `gpt-6-astra`, `gpt-6.1-sol` | Reasoning combobox **without None** — always-reasoning: Low / Medium / High / Xhigh / Max. Reasoning summaries stream only when the model chooses to write one, so the Show Thinking pane can stay empty on short tasks | Never shown or sent |
| OpenAI `gpt-6-sol`, `gpt-6-luna` | Reasoning combobox: None / Low / Medium / High / Xhigh / Max | Shown at None only (the API accepts it at no other rung) |
| OpenAI GPT-5.1+ (including the 5.6 sol / terra / luna tiers) | Reasoning combobox: None / Low / Medium / High, + Xhigh on 5.2+ and `-codex-max` (mini / nano stop at High), + Max on the 5.6 tiers; the `-pro` tiers start at Medium | Shown at None on 5.4+ and the 5.6 tiers; never on older 5.x |
| OpenAI `gpt-5-pro` (GPT-5.0) and the o-series (hidden from the picker; kept for pinned instructions) | Thinking checkbox + effort combo (GPT-5.0 adds `minimal`) | Hidden |
| OpenAI `gpt-5.x-chat-*` "Instant" | None (non-reasoning) | Hidden (rejected by the API) |
| OpenAI `gpt-4.1` | None | Shown |
| Google Gemini 3.x | Thinking checkbox + level → `thinking_level` low / medium / high (a pinned 2.5 instruction gets the legacy `thinking_budget`: 1K / 8K / 24K). **Unticked, the box sends the tier's quietest setting**, because a Gemini 3.x model sent no thinking config thinks at its default and bills it silently: `thinking_level=minimal` where accepted, `thinking_budget=0` on 3.8 / 3.7, `thinking_level=low` on 3.1-pro (it cannot stop). The rung is learned per model from the API's own 400s and announced once in the Activity log; the title bar and cost log still say `thinking=off` | Always shown (the API accepts both) |
| xAI `grok-4.3` | Reasoning combobox: None / Low / Medium / High / Xhigh (Low is the API default) | Always shown |
| xAI `grok-4.5`, `grok-4.6`, `grok-4.7` | Reasoning combobox: Low / Medium / High / Xhigh — always-reasoning (None is a 400) | Always shown |
| xAI pinned `grok-4.20-*-reasoning` / `-non-reasoning`, `grok-build-0.1`, `grok-latest` | None — behaviour is baked into the model id (an alias of a model with levels, such as `grok-build-latest`, inherits them). Every xAI request asks for a reasoning summary, so the reasoning models stream their thinking to the Show Thinking pane when they choose to | Shown |
| Moonshot `kimi-k3` | Reasoning combobox: None / Low / High / Max — no Medium; Max is the API default and None is a real off switch | Hidden (every Kimi model fixes sampling server-side) |
| Moonshot `kimi-k2.6` | Boolean Thinking checkbox (`thinking: enabled / disabled`, with Preserved Thinking `keep: "all"` when on) | Hidden |
| Moonshot `kimi-k2.7-code` (+ `-highspeed`) | None — always thinks, no client knob; its reasoning still streams to the Show Thinking pane | Hidden |
| Ollama thinking models (qwen3, deepseek-r1, gpt-oss, muse-glimmer) | Boolean `think` checkbox | Shown |

The xAI ladders are read from xAI's `/v1/language-models` listing, which publishes each model's reasoning-effort levels, input modalities and aliases, so a new Grok tier gets its combobox the day it appears; a static table covers a failed fetch. A saved value a model does not accept — an instruction moved from one model to another — maps to the **nearest rung that model does accept**, in the combobox and in headless runs (and in OpenAI's reactive 400 retry): a Claude *Off* becomes None (or the lowest rung on an always-reasoning model), `minimal` becomes Low, a Max above a model's ceiling becomes its top rung, None on a `-pro` tier becomes Medium — and the value actually sent is what the title bar and the cost log report. On Anthropic's always-on models a saved *Off* becomes Adaptive. Every GPT-5 and GPT-6 model, the Instant variants included, also shows a **Verbosity** combobox (`text.verbosity` Low / Medium / High). For OpenAI, xAI, Moonshot and Google one builder produces both the live request parameters and the Debug payload dump, so the dump shows exactly what went over the wire.

**Anthropic fast mode** — Opus 5.5, Opus 5 and Opus 4.8 (a research preview) also show a **Fast** checkbox after the Thinking combo. Ticked, every call carries `speed: "fast"` under its beta for up to 2.5x output tokens per second at exactly **2x the per-token price** in every bucket (Opus 5.5 $8 / $40 with cache reads at $0.40 per MTok; Opus 5 and 4.8 $10 / $50). The setting is saved per instruction (`fast_mode`) and shows as `speed=fast` in the title bar and the cost log's PARAMETERS field. Each call is priced by the speed the API reports serving it (`usage.speed`), so a call served at standard speed bills standard; a fast-served model without a fast pricing row is unpriced with a warning, never priced from the standard table. An organisation without preview access gets a 429 with a fast-mode rate limit of zero — MyAgent recognises it, posts a ⚠ and runs the rest of the session at standard speed; a genuine fast-capacity 429 drops just that call to standard; a 400 naming `speed` switches the surface off for the session. Fast and standard requests do not share a prompt cache.

#### Anthropic request surface

- Adaptive thinking is sent as `{"type": "adaptive", "display": "summarized"}`, so the Show Thinking pane has readable text on the models whose default is to omit it.
- **Preserved thinking** — on the always-on models (Fable / Mythos, Opus 5.5) a thinking block's signature is bound to the conversation prefix that produced it, so editing earlier history — the context-overflow trim is such an edit — invalidates the later blocks. Their requests send `thinking.block_binding.prefix_mismatch_behavior: "drop_block"` (beta `thinking-binding-controls-2026-08-01`): the API drops the stale blocks unbilled, proceeds, and lists them in `input_transformations`, which MyAgent prints as an Activity line — worded by reason, and once per run per shape (since 2026-10-11): after a Model upgrade the earlier model's thinking blocks are dropped on every later call (`model_binding_mismatch` — only the model that wrote a block reads it), which is no fault: nothing is billed for them and the text and tool history is intact. Without the beta, a 400 naming a thinking-block `signature` strips the thinking blocks from history and retries once.
- **Server-side refusal fallbacks** — the Fable classifiers can decline a request (HTTP 200, `stop_reason: "refusal"`, possibly before any output). These models' requests send `fallbacks: "default"` (beta `server-side-fallback-2026-07-01`), so a declined request is re-run on an Opus-tier model inside the same call. The call is priced by the model that served it (`message.model`) and a ⚠ line names it; a switch mid-output follows the API's echo rule — only the declined partial's text and paired server-tool blocks stay in history, and its `tool_use` blocks are never executed. A decline with no fallback is shown as a ⚠ with its category.
- A 400 naming either beta switches that surface off for the rest of the session. MyAgent never sends a forced `tool_choice`.

#### OpenAI and xAI request surface

- **The model's reasoning survives the tool loop.** OpenAI and xAI requests go out with `store: false`, so the provider keeps nothing between calls. Each call asks for `reasoning.encrypted_content`, and the next call replays the previous response's output items verbatim: encrypted reasoning, server-side tool records (web search, X search, code interpreter) with whatever they carry, each message's `phase` and the function calls. Before 2026-09-30 only the text and function calls were kept. A reasoning model then met every tool result without the reasoning that asked for it and re-derived the situation from scratch; asked "Tell me what time is it now?", gpt-6-luna spoke the time five times in eight calls without handing the conversation back. The replayed reasoning also survives a [Model upgrade](#model-upgrade-agent-request-dialog): within GPT-6, and from one Grok to another, it is used, and older OpenAI families silently ignore it. On Grok it also saves money, because a model that remembers its reasoning stops redoing it (output tokens fell two- to six-fold in the live checks). If a provider ever refuses a replayed item, a ⚠ line says so, and turns are rebuilt the old way for the rest of the session. Saved chats keep these items without the ciphertext, and the reasoning items only when Save Thinking is on.
- **The web search tool is declared without a location.** OpenAI then tells the model the user is in the United States, which is how that run opened with "It's 1:52 PM Pacific time" on a PC in Sydney. Sending this machine's time zone as `user_location` fixed the belief but made the model trust OpenAI's own time lookup over the PC clock and repeat itself more often, so it was measured and left out. For local facts such as the time, the PC (`run_command`) is the source to trust.

#### Moonshot (Kimi)

Moonshot rides the OpenAI SDK at `https://api.moonshot.ai/v1` with `MOONSHOT_API_KEY` (or `KIMI_API_KEY`), but its surface is **Chat Completions only**, so it carries its own message / tool translators. The load-bearing detail is **`reasoning_content` round-tripping**: Kimi's thinking models return their reasoning in a non-standard field and are *trained* to have it sent back unmodified on assistant messages across tool-call loops — a naive OpenAI-compatible harness works, but silently underperforms without it. MyAgent stores the streamed reasoning as thinking blocks in its history and replays it per model: always for `kimi-k3`, `kimi-k2.7-code` and `kimi-k2.6`, never for `kimi-k2.5`, with a learn-once 400 fallback for a model that rejects it (a 400 complaining the field is *missing* never turns it off). Temperature is never sent (every Kimi model fixes sampling server-side), all Kimi-specific parameters travel in `extra_body`, and re-sent reasoning bills as input at Kimi's cheap cache-hit rate, which the exact cost tracking accounts for. Every served Kimi model takes images, the k2.7-code line included. The shared store's `Kimi_Feature_Test` instruction smoke-tests the surface: parallel tool fan-out, web search / fetch, a file round-trip, `run_command`, a desktop `find_window`, browser + vision, and a memory-across-tools check.

### Ollama (local inference)

No key, no cost, no egress — at the price of speed (a 30B-class Q4 model on Apple Silicon streams roughly 10–30 tok/s).

```bash
ollama serve
ollama pull muse-glimmer:30b-mlx    # tools + vision + thinking (21 GB, the Apple-Silicon build; muse-glimmer:30b elsewhere) — the default; Ollama ≥ 0.32.7
ollama pull qwen3:32b-q4_K_M        # text + tools + thinking (20 GB)
ollama pull qwen2.5vl:32b           # vision (21 GB)
ollama pull gemma3:27b              # strong vision (17 GB)
```

Ollama is detected by a quick probe when MyAgent starts, so start `ollama serve` first. The model dropdown auto-populates from the local server, and each model's **tool support and context length are read from `/api/show`** — no hand-coded model list; a model without tool support runs with no tools (one warning says so). The Thinking checkbox appears for the families in `OLLAMA_THINKING_PREFIXES` (qwen3, deepseek-r1, gpt-oss, muse-glimmer), and `OLLAMA_VISION_PREFIXES` decides only the text-only-model warnings — screenshots are sent either way. Context is passed as `num_ctx`, capped by `OLLAMA_NUM_CTX_CAP` (default 32768) to avoid KV-cache swap pressure on 32 GB machines. Environment variables: `OLLAMA_BASE_URL`, `OLLAMA_NUM_CTX_CAP`, and `OLLAMA_KEEP_ALIVE` (e.g. `24h`, sent with every request — a warm model skips the cold load and reuses the server's KV prefix cache for the system prompt and tool catalog, the dominant cost of a 30B-class local agent run; `ollama stop <model>` frees the RAM).

**Keep warm instances full-sized.** Ollama never reloads a loaded model for a larger `num_ctx` — it serves the request on the smaller instance, where the system prompt and tool catalog overflow the window and are silently shed mid-run. MyAgent checks `/api/ps` before each call and drops a smaller-than-needed warm instance (with a ⚠), so the call reloads it at the right size. Any warm-up or probe of your own should pass `num_ctx` 32768 or `keep_alive: 0` — a casual `ollama run` chat, with its tiny default context, sets the trap for the whole keep-alive window. `ollama ps` shows it: the CONTEXT column should read 32768 for the models MyAgent uses.

Model notes: `muse-glimmer` (Meta's 30B open-weights agentic model) advertises tools, vision and thinking, generates fastest of the 30B-class models on an M4 Mac Mini, and spends far fewer tokens per screenshot than qwen2.5vl; MyAgent sends it the boolean `think` flag. `qwen2.5vl` bakes a near-greedy `temperature=0.0001`, but MyAgent always sends the UI temperature — set Temp low for precise visual work with it.

The repo ships three **custom Modelfiles** that graft Qwen3's tool-calling template onto vision models whose stock templates don't expose `tools` (e.g. `ollama create gemma3-tools:27b -f Gemma3-tools.Modelfile`); capability detection then picks up the `tools` flag. Each Modelfile's `FROM` line names a local weight blob — point it at your own (`ollama show <model> --modelfile` shows the path) before `ollama create`, and again if a re-pull changes the blob. `gemma3-tools` is fully healthy — tool calls and vision alike. `qwen2.5vl-tools` can wrap its tool call in the wrong tag at low temperature, so prefer muse-glimmer, qwen3 or gemma3-tools for tool loops. `Llama32Vision-tools.Modelfile` is kept only as a recipe: the mllama architecture it targets does not load under Ollama 0.32.x. `ollama list` double-counts the grafts' shared weight blobs, so the model store uses less disk than the list suggests.

### Tool catalog

**99 built-in tools in ten families** — core 6 · file 5 · desktop 14 · browser 12 · meta 3 · mail 3 × 16 · Excel 9 · physical 2 — plus dynamic MCP tools and `get_skill` for on-demand skills. Core and file tools are always offered; every other family has its own checkbox in the Instruction Editor, and a per-instruction `blocked_tools` list can remove any tool by name (see [Other niceties](#other-niceties)).

- **Core (always on)**
  - `run_command` — PowerShell on Windows, bash on macOS, with an optional model-set `timeout` (default 30 s, clamped to 5–600). A timed-out command has its **whole process tree** killed, so a nested shell or build cannot keep the run waiting; output is read as UTF-8; every command passes the safety layer (blocked and confirm patterns — see *Safety dialog* under [Other niceties](#other-niceties)).
  - `csv_search` — find rows in a CSV file.
  - `read_document` — PDF (pypdf), DOCX (python-docx, with tables kept as `|`-separated rows), HTML and plain text, with optional PDF page ranges (`"1-5"`, `"1,3,5-7"`). Owner-password-only PDFs are read; a user-password PDF gets a clear message (and a `qpdf --decrypt` suggestion); a malformed page becomes a placeholder instead of an error.
  - `user_prompt` — the only way the agent can ask you something mid-task, through the **Agent Request** dialog; an empty reply stops the agent, a reply of `exit` stops it **and closes MyAgent** (see Conversational mode under [Agent Instructions](#agent-instructions)), while closing the dialog doesn't — the model is told you didn't respond. The request and your reply are written into the output pane under **Agent Request:** and **You:** headings, verbatim as the dialog showed them, so the pane and the saved `.txt` transcript keep the question as well as the answer. A reply can carry images — **Attach Images / Remove Selected**, or **Ctrl+V** to paste a clipboard image (a bitmap, files copied in Explorer / Finder, or on Windows a web app's copied `<img>` reference, downloaded best-effort) — which reach the model as image blocks; an image-only reply is sent as "[See attached image(s)]". A reply can also be **dictated** ([Voice input](#voice-input-agent-request-dialog)) and can move the rest of the run to a stronger model ([Model upgrade](#model-upgrade-agent-request-dialog)).
  - `web_search` + `fetch_webpage` (DuckDuckGo) — for the providers without server-side search; stripped for the others, so the model is never offered two search implementations.
- **File tools (always on)** — `read_file` / `edit_file` / `write_file` / `glob_files` / `grep_files`, with Claude-Code-style contracts for reliable code editing instead of shell round-trips. `edit_file` needs an exact, **unique** `old_string` match (overlapping occurrences count) and fails loudly otherwise; edits and overwrites require the file to have been `read_file`'d earlier in the same run; writes go to a temporary file that replaces the original only once complete, so a failed write never leaves it empty; CRLF line endings and a UTF-8 BOM round-trip byte-exactly; every path expands `~`. `read_file` numbers lines, pages 1,000 lines at a time, truncates lines at 500 characters and caps a window at 80 K characters on a whole line, and flags a file that is not valid UTF-8. `glob_files` and `grep_files` skip `.git`, `.venv` / `venv` / `env`, `node_modules`, `__pycache__`, `dist`, `build`, the lint and test caches and `.claude` while they walk, never wander from outside into a cloud-synced or network tree (OneDrive, iCloud Drive and `/Volumes` on macOS, the OneDrive folders on Windows) nor, on macOS, into another app's sandbox container (`~/Library/Containers`, `~/Library/Group Containers` — one stat in there is a permission dialog; pass any of these as `path` to search it), and return what they have, marked partial, if a walk runs past 60 seconds or STOP is pressed; `glob_files` returns up to 200 matches, newest first; `grep_files` skips binaries and files over 2 MB, and has `files_with_matches` / `content` / `count` modes and a `glob` filter that takes names (`*.py`) or paths (`src/**/*.py`).

  The shared store's **`Coding_Agent`** instruction pairs these with the Meta tools as a Claude-Code-style coding orchestrator: explore → read → surgical edit → verify by running tests. Delegation is policy, not an option: its **DELEGATION** block names eight least-privilege child instructions, each spawned as a waited subagent with a self-contained `extra_text` brief, and says when each is required — after the tests pass, `Code_Reviewer` and `Code_Simplifier`, then one `Finding_Verifier` per finding, with size-based triggers for the others (for example `Code_Planner` for work touching three or more files) — and the final summary must include a "Delegation:" line. The eight: `Code_Scout` (read-only research), `Code_Planner` (read-only implementation plans), `Test_Verifier` (runs tests and linters), `Code_Reviewer` (reviews a diff), `Finding_Verifier` (tries to *refute* one claimed defect), `Security_Reviewer` (a security-lens review), `Git_Historian` (read-only git archaeology) and `Code_Simplifier` (behaviour-preserving cleanup suggestions). Each child's `blocked_tools` enforces its role (`edit_file` / `write_file` blocked for all of them, `run_command` too for the scout and the planner), all are Meta-off and Convo-off (a child cannot ask questions — a headless `user_prompt` only waits for its timeout), and each ends with a structured FINAL REPORT that the parent receives as its tool result. A **PROJECT MEMORY** block has the parent read the target repo's `CLAUDE.md` (and relevant `.claude/rules/*.md`) as authoritative conventions and offer to record durable learnings back — Claude Code's own convention, so both tools share one per-repo memory file. MyAgent has no working-directory notion, so the `cd repo && claude` equivalent is a **pinned-repo variant** — a copy with a TARGET REPO block naming the repo root and Convo on (the base instruction has it off): `Coding_Agent_CodingAgent`, `Coding_Agent_CodingAgent_MAC` / `_WIN_Desktop` / `_WIN_Laptop` and `Coding_Agent_UsesMyAgent_WIN_Laptop`. The base instruction also steers multi-line checks to a throwaway `_verify_tmp.py` (write → run → delete) rather than fragile PowerShell here-strings.
- **Server-side tools (no checkbox)** — **Anthropic**: `web_search_20260209` + `web_fetch_20260209` (search with dynamic filtering, and a real page fetch) + `code_execution_20260521`; code execution is free while those web tools are declared, and Haiku 4.5 alone gets the older web-search / web-fetch variants instead (Haiku 5.5 takes the current set). **OpenAI**: `web_search_preview` + `code_interpreter` (see [OpenAI and xAI request surface](#openai-and-xai-request-surface)). **xAI**: `web_search` + `x_search` + `code_interpreter`, $0.005 per invocation, folded into xAI's exact per-call cost. **Google, Moonshot and Ollama** keep the local DuckDuckGo pair (Gemini's API cannot mix built-in tools with custom function declarations; Moonshot offers none). Server tools are declared on every call — their definitions cost input tokens each time, mostly absorbed by the prompt cache — while their usage fees apply only when one is invoked, so the instruction text is the real switch. Ticking **Desktop** removes the code sandbox on OpenAI and xAI: with it, the model inspects screenshots in code and pre-scales coordinates itself, producing double-scaled clicks. Code-execution images are shown inline (max 600 px) and saved beside the chats (`<OneDrive>/MyAppShare/saved_chats/` since 2026-10-08 — see [Every run saves its chat](#other-niceties)); Anthropic's other output files (exported reports, CSVs, …) are saved there as `ci_output_<timestamp>_<original name>`. When a model rejects a server tool, OpenAI learns it off for the session, Anthropic falls back to the older tool set, and xAI drops it from that request.
- **Desktop (checkbox)** — 13 pyautogui tools (`screenshot`, `mouse_click`, `mouse_scroll`, `mouse_drag`, `type_text`, `press_key`, `open_application`, `find_window`, `wait_for_window`, `clipboard_read` / `clipboard_write`, `read_screen_text` OCR, `find_image_on_screen`) plus the **Gemini-only `find_element`**, which uses Gemini's trained pointing API — with Google's exact documented prompt wording, the only phrasing that triggers it — to turn "the blue Save button" into pixel coordinates.
- **Browser (checkbox)** — 12 Playwright / CDP tools (`browser_open`, `_navigate`, `_click`, `_fill`, `_select`, `_get_text`, `_get_elements`, `_run_js`, `_screenshot`, `_wait_for`, `_download`, `_close`). The automation browser runs on its own persistent debug profile, so logins survive runs — Chrome, then Edge, on Windows (`%TEMP%\myagent_browser_debug`); Brave, then Chrome, then Edge on macOS. `browser_download` is the tool for in-page file downloads: the CDP attach renames every unmanaged download to a random GUID, so the tool wraps the click in `expect_download()` and `save_as()`es the file — given a folder, under the site's own file name and never over an existing file (`statement.pdf` lands as `statement (2).pdf` beside last month's); given a full file path, to exactly that path. `close_chrome.ps1` / `close_chrome.sh` close the automation browser — and only it — at the end of a browser instruction.
- **Meta (checkbox)** — `manage_instructions`, `manage_skills`, `run_instruction`:
  - `manage_instructions` (`list` / `read` / `create` / `update` / `delete`) manages the instruction library and doubles as the agent's **self-inspection channel**: `read` returns a saved instruction's settings — exact model id, thinking and temperature parameters, tool toggles, blocked tools — including the saved copy of the instruction currently running, and `create` snapshots the live session's model parameters. Writes are disk-only and take effect on the next load; a running instruction never modifies itself mid-run.
  - `manage_skills` manages skills, their what + when descriptions (an update may carry only a description; `""` clears it) and each skill's **bundled resource files** — `list_files` / `read_file` / `write_file` / `delete_file` by skill name and a relative path inside the skill folder (validated against traversal, absolute paths, drive letters and the SKILL.md family), so Meta access alone authors a complete Agent-Skills-style skill: SKILL.md plus `references/`, `scripts/`, …
  - `run_instruction` **spawns another instruction** as a separate process, headless by default: fire-and-forget, or `wait=true` to block until the child finishes and receive its final report as the tool result (a true subagent; `timeout_seconds` defaults to 600), with optional `extra_text` appended to the child's instruction as per-run task context. Several `run_instruction` calls in one assistant turn run **concurrently** — waited ones included — so a parent fans out independent subtasks and waits only for the slowest.
- **Mail (three checkboxes)** — 16 Gmail + 16 IMAP / Proton + 16 Outlook tools — see the integration sections below.
- **Excel (checkbox)** — 9 xlwings tools that drive the **running Excel application** — see [Excel](#excel-live-workbook-integration).
- **Physical (checkbox)** — `camera_capture` (a webcam photo, handed to the model the way a screenshot is) and `microphone_listen` (a timed recording, returned as a transcript) — see [Physical tools](#physical-tools-camera-and-microphone).
- **MCP (checkbox)** — every tool of every connected MCP server, namespaced `<server>__<tool>` — see [MCP](#mcp-integration).

**Desktop click accuracy** is a first-class concern: every provider receives plain pixel coordinates ("click what you see"); screenshots are sized to the API's real limit (OpenAI 2048 px / 5 MP and Gemini 2048 px / 4 MP on the long edge; 1568 px / 1.15 MP — Anthropic's limit — for everyone else); coordinates round rather than truncate; an out-of-bounds click clamps (≤ 2 px over) or is refused (> 2 % of the dimension); per-display state is tracked separately for full and region captures, so chained region screenshots never drift; `mouse_scroll` at a position moves the cursor there first; an optional `grid=true` overlay draws a labelled 100-px coordinate grid for dense UIs; and the **Diag** checkbox logs the whole capture → click coordinate-mapping trail. Omitting `display` captures every monitor as separate images (on macOS through Quartz per display, since pyautogui sees only the primary). With Activity on, a note at run start flags provider / model choices with known-poor click precision.

### Agent Instructions

Saved task profiles in `agent_instructions.json`, in `<OneDrive>/MyAppShare/` (OneDrive, not git, syncs the library across machines — see [Cross-machine store sync](#cross-machine-store-sync)). Each instruction stores its text, base64-embedded images, all ten toggles (Desktop / Browser / Excel / Physical / Meta / MCP / Gmail / IMAP / Outlook / Convo), provider + model + temperature / thinking / verbosity / Fast parameters, the Agent Request dialog's Auto-send tick, its upgrade model (set in [Model Setup](#model-upgrade-agent-request-dialog)), per-skill modes, Safety bypass patterns and the `blocked_tools` hard blocklist — a complete, self-contained environment per task.

The **Instruction Editor** works on a draft: **SAVE** writes the instruction to the shared store and makes it live, **Apply** makes it live for the session only (surviving a restart through the applied snapshot in `agent_state.json`) and closes the editor. Selecting a page applies its environment settings — model and thinking parameters, skill modes, Safety bypasses, blocklist, Auto-send tick and upgrade model — at once (they are live state, not draft), so [X] discards the draft *and* puts back the environment that was live when the editor opened (or at its last SAVE); closing MyAgent with the editor still open does the same. **DELETE** asks first (default **No**); a SAVE onto the name of a *different* existing instruction asks before replacing it; the list's layout changes (below) are written to the store at once; and while a run is streaming the editor refuses to switch pages or CLEAR, because both restore environment settings the running loop reads on every call. The `manage_instructions` tool's delete is deliberately unprompted: an unattended run has nobody to answer a dialog. Reference images are added with **Attach Images** or pasted with **Ctrl+V** (Cmd+V on macOS).

**The Instructions list** sits beside the instruction text under a fixed **INSTRUCTIONS** band, OneNote-style: every instruction is a *page* in a *section*, sections are bold grey bands with their pages beneath (alternating white / off-white rows), unfiled pages come last under an italic *Unfiled* header, and the loaded instruction is the highlighted row. Selecting a page loads it. Sections fold and unfold; ▲ / ▼ (or Alt+Up / Alt+Down) move the selected page or section one step, and a page at its section's edge steps into the neighbouring section; a row can be dragged onto another (a page dropped on a page lands beside it in that page's section, on a header as the section's first page; a section takes the target's place); and **Section…** files the selected page into an existing section, a new one or Unfiled, or renames the selected section (an existing name merges). A section exists exactly while an instruction names it: **to create one, select an instruction, press Section…, type a name that doesn't exist yet and press Enter** — it appears after the last section, and disappears once its last page is moved out or deleted. A newly SAVEd instruction is filed where the selection is. The layout is stored on the entries themselves (`"section"` / `"order"`, renumbered on every change by the pure `myagent/instruction_layout.py`), so it syncs with the instructions; an entry created without the two keys (by `manage_instructions`) is simply unfiled, listed last, alphabetically, and `Heartbeat.py`'s text rewrites keep an entry's place. The sash between the list and the text is draggable; the folded sections and the sash position are remembered per instance in `agent_state.json`.

Loading an instruction applies its skill modes to the session without writing the `skills/` tree, which holds each skill's global mode. A skill whose global mode is on or on-demand but which the instruction's snapshot omits runs disabled, and a ⚠ names it (re-posted at run start); re-save the instruction with the skill set to include it. The tree keeps each skill's global mode: a later skill save writes those, and only an explicit mode change — Cycle Mode in the Skills Manager, or `manage_skills` with a `mode` — changes one.

**Conversational mode** (Convo checkbox) — for small open-weights models that won't follow an "always call `user_prompt`" rule, MyAgent enforces the chatbot loop itself: when the model ends a turn with text and without asking, the app opens the Agent Request dialog and feeds the reply back in. An empty reply, `quit`, `exit` or `stop` ends the conversation — dictated as well as typed (a spoken "quit" lands in the reply box as the bare word; see [Voice input](#voice-input-agent-request-dialog)); closing the dialog does not, since the model is only told you didn't respond. **`exit` also closes MyAgent**: once the run has ended and its cost-log line is written, the app does what the main window's [X] does — saves its state file, autosaves the chat, shuts down the automation browser and the MCP servers, releases its instance slot — so a typed or spoken "exit" ends the session completely, while `quit` and `stop` leave the window open. `exit` means the same in a dialog the model opened with `user_prompt`, where `quit` and `stop` are ordinary replies the model reads.

### Skills

Reusable prompt fragments shared by MyAgent and SelfBot, one file per skill: **`<OneDrive>/MyAppShare/skills/<name>/SKILL.md`**, Anthropic-Agent-Skills-shaped — a small frontmatter block (`name` / `description` / `mode`) over the markdown content, editable in any editor and diffing cleanly. Per-file storage matches OneDrive's sync unit, so two machines editing different skills never conflict. **The folder is the skill**: it can carry bundled resource files (`references/`, `scripts/`, `tests/`, …) beside `SKILL.md`, which ride along on delete, sync and copy.

- **Modes** — *disabled*; *enabled* (injected into the system prompt); *on-demand* (listed in the system prompt's On-Demand Skills index, name + optional **description** — one or two sentences on what the skill does and when to use it, the Agent-Skills routing signal — and fetched by the model through `get_skill`).
- **A change reaches the next run** — the system prompt is built once per run (MyAgent: at START, for the whole run, Convo conversations included; SelfBot: per reply), so a skill created, edited, re-moded or deleted during a run — by `manage_skills` or in the Skills Manager — is in the prompt from the next run (SelfBot: the next message), while `get_skill` always returns the current text. The system prompt heads the prompt cache, so changing it mid-run would make the next call re-write the whole cache (on Fable 5.1 at $12.50 / MTok) and drop the model's earlier thinking on Fable 5.1, Mythos 5.1 and Opus 5.5.
- **Names are Agent-Skills kebab-case** (`westpac-login`, `process-payment-emails` — folder = name; H1 titles in the body stay human-readable), enforced on create: the Skills Manager offers a one-click converted name (`Test_skill` → *"Create as 'test-skill' instead?"*) and `manage_skills` rejects with the suggestion in the error. Existing non-conforming names stay editable.
- **The Skills Manager** (the editor's Skills button; its label counts always-on + on-demand skills) lists skills under a fixed **SKILLS** band with their modes as `[ON]` / `[OD]` prefixes, cycles a mode with Cycle Mode or Space, and edits the name, description and content. **DELETE** asks first (it removes the whole folder from every synced machine, with no undo); a **SAVE under a new name while another skill is loaded** is a save-as that also copies the source skill's bundled resource files (existing files at the destination are never overwritten; a dialog reports the count).
- **Saves are per-file, atomic, diff-aware and write-only** — unchanged skills are never rewritten (no OneDrive churn), and a stale in-memory copy can never delete a skill another machine synced in; deletion is always an explicit action.
- **OneDrive-hardened** — a `SKILL-<Computer>.md` conflict fork heals on load (identical → deleted; different → kept as a `<name>__<label>` skill, numbered when that name is taken; orphaned → promoted); deletion clears the ReadOnly attribute OneDrive stamps on stuck directories and hands a still-locked folder to a background sweeper; the tree is case-folding-aware (on Windows and macOS `test-skill` resolves into an existing `Test-skill`, so writes never land in a differently-cased folder, and the load-time scan heals mismatched or stranded folders); a SKILL.md saved in another encoding (UTF-16, cp1252) is still read.

### Cross-machine store sync

The authored-content stores — `agent_instructions.json`, SelfBot's `system_prompts.json` and the `skills/` tree — live in **`<OneDrive>/MyAppShare/`**, the suite-wide shared folder that also holds TodoList's `todos.json` and every machine's API cost log. `myagent/datapaths.py` locates it per platform (the Windows `OneDrive*` environment variables; macOS `~/Library/CloudStorage/OneDrive-*`), honours a `MYAGENT_DATA_DIR` override, and falls back to the repo root on a machine without OneDrive; MyAgent's and SelfBot's title bars show "— synced" when the shared folder is in use. Every OneDrive path the apps use — both trees, with each file's owner and its gitignored repo-root fallback — is listed under [OneDrive shared folders](#onedrive-shared-folders).

- **Writes are atomic** (a unique temp file + `os.replace`), so OneDrive never syncs a half-written file, and SelfBot and MyAgent can save a store at the same moment.
- **A store that exists but doesn't parse** (a half-synced cloud write) is served as a session-only Default, and both apps refuse to save over it, with a warning, so a save can't replace the whole library with one entry.
- **OneDrive conflict forks** (`<stem>-<Computer>.json`) are absorbed at each load by a key-level union and deleted; a genuinely different entry survives as `name__<label>` for reconciling in the UI.
- **Dropping a store file at the repo root imports it** — it is key-level-unioned into the shared store on the next launch (a differing entry survives as `name__<hostname>`) and renamed `*.migrated.bak`, so later deletions can't resurrect it.
- `Heartbeat.py` resolves the same path for its marker rewrites.

### MCP integration

A generic Model Context Protocol client (`myagent/mcp_mixin.py`) connects to stdio MCP servers (filesystem, GitHub, Slack, …) and pipes their tools through the same loop as the native tools, on all six providers.

```bash
pip install mcp            # + pywin32 on Windows
cp mcp_servers.example.json mcp_servers.json
# then edit the filesystem path; the example's "_example_with_secret_from_shell_env"
# server stays disabled until you remove its leading underscore
```

`mcp_servers.json` (gitignored) holds the servers under a top-level `servers` key — each entry has the same shape as in Claude Desktop / Cursor (a pasted `mcpServers` key loads nothing), and a server whose name starts with `_` is disabled:

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

- `${NAME}` placeholders in `env` values resolve from your environment at spawn time, so secrets never enter the JSON (substitution deliberately does **not** apply to `command` / `args`, where a process listing would show them); the reserved `${RANDOM_PORT}` yields a fresh OS-assigned port per occurrence, so several MyAgent instances don't collide.
- Servers connect in the background, one after another, right after the window opens (1–3 s warm; a cold `npx -y` first run can take minutes, capped at 5); a run that offers MCP tools while they are still connecting waits for them first (STOP ends the wait), so a `-l` or headless launch still gets them. They run on a dedicated asyncio thread inside one long-lived `AsyncExitStack` — the architecture that survives anyio's cancel-scope rules (details, including the Windows `pythonw.exe` stderr / IOCP hardening, in [.claude/rules/CLAUDE_MYAGENT.md](.claude/rules/CLAUDE_MYAGENT.md)).
- Tool names are namespaced `<server>__<tool>`, and tool results come back as text (an image result appears as a byte count). A call may take up to 120 s; STOP or the timeout cancels it on the MCP loop rather than leaving it running.
- The checkbox only gates whether the tools are *offered*. Every connected server's catalog rides along on each call (5–10 K tokens for a large server), so leave MCP off for tasks that don't need it — especially on Ollama's capped context. Debug the MCP stream with `python MyAgent.py 2> mcp.log`.

### Gmail integration (native Google tools)

Sixteen native multi-account Gmail tools (`gmail_search` / `read` / `get_attachment` / `send` / `reply` / `create_draft` / `list_drafts` / `send_draft` / `trash` / `untrash` / `list_labels` / `create_label` / `delete_label` / `modify_labels` / `mark_read` / `list_threads`) via the official Google API client — not MCP. (The editor checkbox is labelled **Gmail**; the plain "Google" label belongs to the Gemini provider.) Replies thread properly (In-Reply-To / References + threadId); the send-style tools take optional `body_html` (multipart / alternative) and `attachments` (20 MB combined); `gmail_read` returns text / html / both bodies plus attachment metadata.

Setup once:

1. `pip install google-api-python-client google-auth-oauthlib google-auth-httplib2` (already in `requirements.txt`).
2. Google Cloud Console → enable the **Gmail API** → OAuth consent screen (External, add yourself as a test user, scope `gmail.modify`) → create a **Desktop app** OAuth client and download its JSON.
3. Move the client JSON into MyAgent's config folder:
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
5. The first tool call per account opens the browser consent flow once (it waits at most 5 minutes, and is refused in a `--headless` run, where nobody could finish it); after that the token (`{account}_token.json`, chmod 600) refreshes silently. If the refresh token is ever revoked or expires, the tools return an error — delete that account's token file and the next call signs in again.

Every tool takes an `account` parameter whose enum is filled at runtime from `accounts.json` — multi-account work is one parameter, not several processes. The OAuth scope is **`gmail.modify` only**: trash is recoverable, and permanent delete is impossible *by scope*. Destructive tools — `gmail_send`, `gmail_reply`, `gmail_send_draft`, `gmail_trash`, `gmail_delete_label`, and a `gmail_modify_labels` that adds `TRASH` or `SPAM` (asked under `gmail_trash`) — pop a confirmation dialog that opens on **No**, individually bypassable per instruction in the Safety dialog (each bypass logs a ⚠ audit line). The confirmations appear in headless runs too, with no timeout, so an unattended instruction needs the bypasses for the actions it takes (this holds for all three mail integrations). The batch tools report each id's own outcome.

### Proton Mail / IMAP integration

Sixteen `proton_*` tools mirroring the Gmail surface 1:1, over **stdlib IMAP + SMTP**. Proton has no public REST API (E2E encryption — decryption is client-side), so the official path is [Proton Bridge](https://proton.me/mail/bridge), which exposes a localhost IMAP / SMTP server with per-account, per-install app-passwords (MyAgent never sees your real Proton login). The mixin is IMAP-generic: the same tools drive **any** IMAP / SMTP account — a WebCentral / cPanel (dovecot) mailbox runs through the identical code path, which is why the editor checkbox is labelled **IMAP**.

Configuration lives in `~/.config/myagent-protonmail/accounts.json` — per account: `email`, `username`, `app_password` (Bridge-generated, or the mailbox password for another server), `imap_host` / `imap_port` / `smtp_host` / `smtp_port`, and optionally `ca_cert_path` (Bridge's exported `cert.pem`, for verified TLS; without it localhost traffic uses `CERT_NONE`, acceptable when the only MITM position is your own machine). For a regular mail server, `imap_ssl` / `smtp_ssl` select implicit TLS instead of STARTTLS, `verify_tls` checks the server certificate, and `smtp_saves_to_sent` says whether the server files sent mail itself. `chmod 600` the file. No OAuth — if Bridge is running, the tools work. A failed STARTTLS falls back to a plain login only on a loopback host (Bridge); any other host refuses rather than send the password in clear.

IMAP realities the tools absorb for you: UIDs are **per-folder** (tools take `folder` + `uid`; a Trash round-trip yields three UIDs for one message); `Labels/X` moves are *additive* on Bridge (Proton labels are tags) while system folders are true moves; a label-removal eventual-consistency quirk is retried transparently (`label_removal_retries` in the response); moves use `UID MOVE`, and only a server that doesn't know the command gets a checked COPY + `UID EXPUNGE` of exactly those UIDs (a plain EXPUNGE only where UID EXPUNGE itself is unsupported) — a refused move never expunges anything; recipients are parsed properly (a quoted display name with a comma stays one address) and any the server refuses are reported as `refused_recipients`; and search is steered to explicit IMAP keys (`SUBJECT "..."`, `TEXT "..."`), because bare tokens work on Bridge but error on dovecot. `proton_send` / `reply` / `send_draft` / `trash` / `delete_label`, and a `proton_modify_labels` into Trash or Spam, ask first (bypassable per instruction, like Gmail's).

### Outlook / Microsoft 365 integration

Sixteen `outlook_*` tools, again mirroring Gmail 1:1, via the **Microsoft Graph API** with **MSAL** OAuth (personal outlook.com accounts don't allow Basic-Auth IMAP, so Graph is the supported path). Mapping notes: Gmail labels → Outlook **categories** (managed by display name); trash → a Graph *move* to `deleteditems` (which mints a new message id); Graph messages have a single body (`html` or `text`); a draft *is* a message. The attachment cap is ~3 MB combined (Graph's single-request JSON limit).

Setup once: register a free Azure app (personal accounts; platform *Mobile and desktop*, redirect `http://localhost`, "Allow public client flows" = Yes; delegated permissions `Mail.ReadWrite` + `Mail.Send` + `offline_access`), then:

```bash
pip install msal
mkdir -p ~/.config/myagent-msmail
# msal_app.json: { "client_id": "<app-id>", "authority": "https://login.microsoftonline.com/consumers" }
#   (or set OUTLOOK_CLIENT_ID / MS_CLIENT_ID; the authority defaults to /common)
# accounts.json: { "accounts": { "outlook": { "email": "you@outlook.com" } } }
```

The first use opens a browser sign-in (at most 5 minutes; refused in a `--headless` run); the token cache then refreshes silently, and a 401 triggers one fresh sign-in (again refused headless). The same Azure client_id works on every machine (a public client), so only the one-time consent is per machine. Destructive tools ask first with the same per-instruction bypass as Gmail and Proton (a denied `outlook_reply` deletes the draft it created), and the same soft-delete-only boundary applies.

### Excel live-workbook integration

Nine `excel_*` tools (Excel checkbox) drive the **running Excel application** through [xlwings](https://www.xlwings.org) — COM on Windows, AppleScript on macOS — rather than editing `.xlsx` files on disk: the agent attaches to the workbook you already have open, cells change live on screen, formulas recalculate, and existing VBA macros can run (a file-level library such as openpyxl stores formulas but never evaluates them).

- `excel_open` attaches to a workbook or launches Excel, and reports every open workbook; `create=true` makes a new one, and an optional `password` opens an open-protected file (attaching to a workbook you opened yourself needs no password at all, which keeps it out of the model pipeline).
- `excel_read` returns ranges as TSV labelled with the real row numbers and column letters (`formulas=true` shows formulas instead of values; `max_cells` defaults to 4,000).
- `excel_write` takes cells **exactly as you would type them** — `'42'` → number, `'2026-08-01'` → date, `'=SUM(B2:B9)'` → live formula (all-string cells also sidestep cross-provider JSON-schema quirks) — and, for up to 200 cells, echoes the recalculated result.
- `excel_format`, `excel_sheet` (list / add / rename / delete / activate / clear), `excel_find`, `excel_run_macro`, `excel_save`, and `excel_close` (which saves by default; a workbook never saved to disk is discarded).

Guard rails, because the agent may be attached to *your* Excel instance:

- `excel_close(quit_app=true)` quits Excel only when no other workbook remains open, and `excel_close` without a `workbook` refuses while several are open instead of closing — and saving — whichever one happens to be active.
- A workbook is found by its exact name first, and a different extension never matches (`Accounts.xlsm` never acts on an open `Accounts.xlsx`); `excel_open` refuses a file whose name is already open from another path, since Excel holds one workbook per name and the writes would land in the other file (a cloud-hosted workbook of that name is attached with a note instead).
- **Every write is verified.** A long-running Excel process can reach a state where reads, formatting and sheet operations keep working while every cell write is silently discarded — no error, and the document never even turns dirty (restarting Excel fixes it). Small writes are checked against their read-back echo, larger ones with a bounded first-row probe; the check fires only when everything written reads back empty, and never for a write holding a formula, whose result may legitimately be blank.
- **Read-only is reported up front** when `excel_open` opens a file from disk — it is silent otherwise and only bites at the first save — with a list of what to check (a sidecar `~$` lock file, a genuine write-reservation, disk writability, the file open elsewhere or checked out by OneDrive); `excel_close` never pretends it saved. Quitting Excel completely and retrying is the usual cure.
- **Protected workbooks** can carry two independent locks: `password` decrypts an open-protected file, `write_res_password` claims write access on a **write-reserved** one (without it Excel waits on a "reserved by …" prompt nothing can answer headlessly), and `ignore_read_only_recommended` skips the softer "open read-only?" recommendation.
- Nothing is cached between calls — each run's tools execute on a fresh thread and COM apartments don't cross threads — so every call re-attaches, probing the instance's liveness first (a just-quit Excel can linger in the Running Object Table). Currency-formatted cells arrive over COM as `decimal.Decimal`, which display and `excel_find` handle as numbers. On macOS `excel_run_macro` reports a macro as *dispatched* (AppleScript cannot tell a missing macro from one that returned nothing), and sheet names are validated against the real sheets, case-insensitively.

**Setup:** `pip install xlwings` (in `requirements.txt`) — Windows needs nothing else. macOS needs Excel for Mac, **activated** (an unactivated Excel fails every save with `OSERROR -50`), and a one-time Automation grant (System Settings → Privacy & Security → Automation). Since 2026-10-06 xlwings' per-user config is read from `~/.xlwings/xlwings.conf` rather than from inside Excel's sandbox container, so a OneDrive-hosted workbook no longer raises the per-process "access data from other apps" dialog (for the model's own commands into such folders, see the Full Disk Access note under **Desktop launchers**). Unattended macOS runs also need:

```bash
# 1. Essential: stop Excel's start-screen gallery from blocking the first call
defaults write com.microsoft.office ShowDocStageOnLaunch -bool FALSE
# 2. Only if the agent must open macro-bearing .xlsm files
defaults write com.microsoft.office VisualBasicMacroExecutionState -string EnabledWithoutWarnings
```

The gallery matters most: launched with no document, Excel shows its template gallery, which blocks its Apple Event queue completely — the open call never returns, the window can't be clicked away programmatically, and only killing Excel recovers; with the preference set, a launch with no workbook returns in half a second. The macro setting is a genuine security trade, not a default: it spans Word and PowerPoint and runs any macro-bearing file's macros silently (revert with `defaults delete`). Neither is set by the mixin — an agent should not reconfigure your Office install. Excel's sandbox **"Grant File Access"** prompt can stall the first save into an unvisited folder for minutes; it is per folder and granting once is permanent, so pre-grant the folders an unattended instruction saves into.

**A workbook's window position and size persist inside the file** (`workbookView` in `xl/workbook.xml`): position a window and save, and every later open — agent or human, on either OS — restores it there. Scripts that position Excel windows through AppleScript should note that `set top of active window` and reading `top` back use different vertical origins: set, read back, and correct.

### Physical tools (camera and microphone)

The **Physical** checkbox gives the agent two senses for the room the computer sits in, rather than the computer itself:

- **`camera_capture`** — a still photo from the webcam, returned to the model exactly as a screenshot is (a text block plus an image block in the tool result, so every provider carries it). *"Is the 3D printer still running?"*, *"read the label I'm holding up"*, *"take a photo and email it to me"* (pass `save_path` and hand the file to a mail tool's `attachments`).
- **`microphone_listen`** — `seconds` of sound through the microphone (default 5, ceiling 600), transcribed by the Voice Setup provider and model and returned as text: *"listen for ten seconds and do what I say"*.

**Why a separate set and not more Desktop tools:** *consent* — a camera sees the room and whoever is in it, and ticking Desktop to let an agent drive the mouse must not also switch a camera on (and `blocked_tools` can remove either tool by name); *coordinates* — Desktop tools share the click pipeline's scale / offset state, and a photo is not a click surface, so `camera_capture` reads and writes none of it (a photo taken between a screenshot and a click leaves the click where it would have landed); *dependency* — OpenCV rather than pyautogui, so a missing `opencv-python` affects only the camera.

How a photo is taken:

- **Auto-exposure is allowed to settle** — a webcam's first frames are wrong while exposure hunts, so frames are read until the brightness has held still for 0.75 s (every frame's mean brightness in that window within 1 level, on the 0–255 scale, of the others), capped at 4 s for a scene that never holds still (the result then says so). A photo takes about 3–4 s.
- **Opened, read and released inside the one call** — the indicator light is on only while a photo is taken, and another app can have the camera straight back. The grab runs on its own thread under a 25 s ceiling, so a wedged driver costs a timeout rather than the run; STOP ends it within a frame. On Windows Media Foundation is tried first and DirectShow second; macOS uses AVFoundation. 1920×1080 is requested and the driver answers with its nearest mode.
- **JPEG, sized to the provider's image limits** (the whole 1080p frame for OpenAI and Google; 1568 px / 1.15 MP for everyone else), since beyond them the API downscales anyway.
- **`save_path` keeps the full-resolution frame** (`.jpg` or `.png`; `.jpg` is added to a bare name), never overwrites an existing file, and is checked before the camera switches on; a failed save is a note on the result, not an error. A Windows-style path invented on a Mac is redirected to `~/Temp` with a note.
- **An almost-black photo comes back with a warning** naming the likely cause — a closed privacy shutter or lens cover — rather than leaving the model to describe darkness.
- The non-Anthropic providers receive tool-result images in a follow-up user message introduced as a screenshot "for mouse_click coordinates"; over a photo that wording is replaced, and in a turn holding both, the photo is named, so no model is invited to click at the furniture.
- `camera` (an index, default 0) is for machines with several cameras; a Windows Hello infrared camera is not exposed as an index. With Activity on, a text-only model (the xAI, Moonshot and Ollama ones without image input) gets a note at run start when Physical is on — unless Desktop is on too, whose own note covers it.
- **macOS** asks for camera permission on the first call — a throwaway open, no camera light — waits up to 60 s for the answer (STOP ends the wait) and takes the photo in the same call. The prompt names the app that launched MyAgent (`My Agent.app`, Terminal, or Python for a launchd job) and is asked once per app per machine; a denied app gets a hint pointing at System Settings → Privacy & Security → Camera.

**`delay_seconds` is a self-timer, and the way to pace a monitoring loop** (*"take a photo every 10 seconds and act on what you see"*). MyAgent has no clock — the model keeps a loop going itself, and a run ends on the first reply without a tool call. With the timer inside the tool, one call is *wait, then photo*: a quiet cycle is a single API call and every result the model reads is a fresh photo, where a separate sleep command costs a second call per cycle and hands the model a turn with nothing new in it — exactly where a model is likeliest to drop out of the loop. The wait comes before the camera opens (the light is on for the photo, not the wait), is capped at 600 s, and **STOP ends it within 0.1 s**. Wording still matters: tell the model the loop has no end, that it will never be told about STOP, and that every reply is one line plus exactly one tool call. Two limits no prompt removes: every photo stays in the conversation (~1,500 tokens each), so cost grows with the square of the run length and the run ends when the context fills; and waits over ~5 minutes outlive Anthropic's prompt cache. For checks minutes apart, a scheduled one-photo `--headless` run per check is the robust pattern.

**`microphone_listen`** reuses voice input end to end — the recorder, the Voice Setup settings (provider, model, language, vocabulary hint, microphone) and the transcription path — so whatever the Mike button hears, the tool hears. Listening *is* the wait, so a watch-and-listen loop alternates `camera_capture` and `microphone_listen` with no sleep command; STOP ends the listening at once. Silence is reported as such and sent nowhere (speech models invent words from silence), and exact digital silence adds a hint about mute and the microphone permission. The transcription's estimated cost appears in the result, not in the run's token accounting. Its PortAudio calls run on the Tk thread while the agent's worker only waits.

**Setup:** `pip install opencv-python` for the camera; `sounddevice` (in `requirements.txt`) and the transcription provider's API key for the microphone (Voice Setup's defaults — OpenAI `gpt-transcribe` and the system microphone — apply until you change them). The checkbox is enabled when either package is present, and each tool is offered only with its own package.

### Voice input (Agent Request dialog)

A reply to the agent can be dictated. Two controls, in two places:

- **Mike**, under the reply box of the **Agent Request** dialog with a status line beside it, is a toggle. Press it (or **Alt+M**) and it turns red — `■ Stop`, with a running timer and a level meter — which means the microphone is live: speak. Press it again (or press **Enter**, which ends a recording rather than sending) and the recording goes to a speech-to-text model; a second or two later the transcript is **inserted into the reply box at the cursor**, spaced from the text around it, exactly as if typed (a selection holding the cursor is replaced). Edit it, dictate more, type around it, attach images, and press **Enter** to send. Nothing is sent on its own — unless the **Auto-send** box right of Mike is ticked (**Alt+A**): then a transcript is sent the moment it lands, as if Enter had been pressed, with whatever the box already held in front of it — a hands-free reply. Only a transcript that lands is sent; silence, a slip or a failed transcription stops at the status line. With Auto-send ticked the dialog also **opens with the focus on Mike** instead of in the reply box **and rings the bell** (the system's alert sound — the cue to start speaking when a request arrives while you are looking elsewhere; silent if system sounds are muted), and Enter or Space presses the focused button — so a hands-free reply is Enter, speak, Enter, nothing else (Shift+Tab goes back to the reply box to type instead). Ticking the box while a dialog is open moves the focus to Mike as well. Auto-send is saved **with the applied instruction** (below). A spoken **"quit"**, **"exit"** or **"stop"** on its own lands as that bare word — a speech model writes it down as "Quit.", which the end-of-conversation check would not read — so saying it ends a Convo-mode conversation, and a spoken "exit" also closes MyAgent; inside a sentence, or after typed text, it lands as heard.
- **Voice Setup** is a button at the **bottom left of the main window** (**Alt+V**), with Model Setup and Other Setup beside it and the six display checkboxes centred on the window (on a window too narrow for the three buttons and the checkboxes side by side — under ~1,040 px at 150 % scaling — the checkboxes drop to the line below). It opens the settings dialog: **Provider** (OpenAI, xAI or Google, with a line saying whether that provider's key is set), **Model** (with **the audit's note on the chosen model** shown beneath it), **Language** (ISO codes such as `en` or `en, pl`; blank = detect; several codes are honoured by `gpt-transcribe` only — with several set, the other models detect the language themselves), **Vocabulary hint** (names and jargon to spell right: `Westpac, Proton Bridge`) and **Microphone** (the system default or a named device). **Test** runs the same record → transcribe round on the *unsaved* fields and shows the transcript, so a provider, model or microphone can be tried before **Save** keeps it.

**Which models, and why these.** The picker lists the models that passed a suitability audit — a test of *behaviour* more than accuracy, because a reply dictated to an agent is mostly a spoken **command**: is "stop transcribing and tell me a joke" written down, or obeyed? Does noise without speech come back as nothing? Does a long pause, or a ten-minute recording, lose words? Method, numbers and verdicts are in [docs/voice-stt-audit.md](docs/voice-stt-audit.md); re-run it with `python tests/check_voice_models_live.py make | run | report` (about US$0.50). The provider's live catalog tops the list up once per session: a model it adds that hasn't been audited shows *Not audited yet*, an audited-out model never returns, and a listed model the provider no longer serves drops out.

| Provider | Model | Verdict | What the audit found |
|---|---|---|---|
| **OpenAI** (default) | `gpt-transcribe` | **Recommended** | Writes down spoken commands rather than obeying them (7 of 7), returns nothing for noise, keeps every word of a 10.4-minute recording, honours the vocabulary hint, best capitalisation of names with no settings. Spells numbers in words. 0.8 s, $0.0045 / min. |
| **xAI** | `grok-voice-transcribe-2.0` | **Recommended** | The same clean sheet at about a third of the price ($0.0017 / min) and four times faster on long recordings (6.6 s for ten minutes). With exactly one **Language** code set it writes `$250 on 23 September … roman@example.com`. 25 languages (Polish included). |
| OpenAI | `gpt-4o-mini-transcribe` | caveat | Cheapest OpenAI model ($0.003 / min) and as accurate on ordinary dictation, but it **ignores the vocabulary hint** and — a documented 2,000-token output cap — **silently loses the end of recordings over ~9 minutes**. |
| OpenAI | `whisper-1` | caveat | Accurate and complete, but noise without speech comes back as **"Thank you for watching."**; the slowest and dearest of OpenAI's three. |
| **Google** | `gemini-3.5-transcribe` | caveat | Accurate, silent on noise, complete on long-form — but it **obeys some spoken commands** (2 of 7; asked for a joke, it told one); it ignores any text sent with the audio, so the Language and Vocabulary fields do nothing and nothing can harden it; and it is the slowest (~2 s). Not advised for dictating instructions. |
| — not offered | `gpt-4o-transcribe` | out | Drops everything after a 7-second pause (3 runs in 4), invents a word from noise every time, loses the end of long recordings — and is among the dearest ($0.006 / min, like `whisper-1`). |
| — not offered | `grok-voice-transcribe-1.0` | out | Superseded by 2.0 at the same price; drops short utterances (3 of 12). |
| — not offered | `gemini-3.5-flash-lite`, `gemini-3.8-flash` | out | Chat models used as transcribers: both invent a sentence from noise every time ("Hey Siri."); 3.8-flash also returns an *empty* transcript for a clear sentence and writes its own reasoning into a long one. |
| — n/a | any Claude model | n/a | **Anthropic has no audio input**: the Models API reports only image and PDF input for every Claude model, and an audio content block is a 400. |

An audited-out model stays wired — a hand-edited settings file keeps working — but never returns to the picker from a provider's live list. xAI's speech-to-text is **invisible to discovery-by-listing**: it lives at `POST /v1/stt` (not the OpenAI-compatible `/v1/audio/transcriptions`, which is a 404 there), and neither xAI listing endpoint names it. All three providers use an API key MyAgent already has — `OPENAI_API_KEY`, `XAI_API_KEY`, `GEMINI_API_KEY` (or `GOOGLE_API_KEY`).

**The settings belong to you and the machine, never to an instruction.** They live in one per-user file, `~/.config/myagent-voice/config.json` (beside the mail integrations' config folders) — not in `agent_instructions.json`, so loading, applying or saving an instruction never touches them, and not in `agent_state.json`, because every MyAgent instance keeps its own state file (and headless runs write none) while a microphone chosen once has to hold in all of them. The file is read afresh at every Mike press, so a Save takes effect at once, in every running instance. Keys stay in the environment. The one exception is **Auto-send**, which says what the dialog does with a transcript rather than how one is made: it is saved with the applied instruction — ticking or unticking it writes the value into that instruction's store entry at once (and into the applied snapshot), so the instruction starts the same way on every machine. It has no control in the Instruction Editor — the dialog is where it is set — and `manage_instructions` reads and sets it as `dictation_auto_send`.

What the status line tells you, and what it protects you from:

- After each dictation: `15 words · 5.6 s of audio · gpt-transcribe, 1.3 s · ≈ $0.0004`. The cost is an **estimate from the providers' published per-minute rates** and is shown only there — a transcription is deliberately *not* written to the API cost log, which accounts for chat-model calls only. Google's models show no cost rather than a guessed one.
- **Silence is never sent.** A recording with no signal — a muted microphone, a denied permission, or a quiet room — stops at *"Only silence was recorded, so nothing was sent"* (silence is precisely where speech models invent a "Thank you."); a recording under 0.4 s is treated as a slip.
- **Enter cannot lose your speech.** While recording, Enter ends the recording; while the transcript is on its way, Enter is ignored — press it again once the transcript lands. Closing the dialog mid-dictation releases the microphone and drops the pending transcript.
- The Agent Request dialog is modal, so while it is open the main window's Voice Setup button can't be pressed — set things up between requests (typing always works).
- A saved microphone that is unplugged falls back to the system default and says so. PortAudio is rescanned at every press (~20 ms), so "system default" follows a headset plugged in mid-session.
- Recordings are 16 kHz mono 16-bit (1.9 MB a minute), held in memory and never written to disk, and stop by themselves at 24 MB (12½ minutes), just under the 25 MB upload limit.

Two engineering notes. The microphone library is imported at first use — the first Mike press, Voice Setup or `microphone_listen` — never at startup: initialising PortAudio costs about half a second on Windows, which every launch, the scheduled headless ones included, would otherwise pay; the button turns red only once the stream is really open. And Google's dedicated model answers in a response field (`audioTranscription`) the installed `google-genai` SDK does not model, so the Google path reads the transcript from the raw response body.

**macOS:** expect the first recording to trigger a microphone permission prompt for the *launching* process (Terminal or the Desktop launcher applet — the same identity rule as screen capture; see the scheduling notes). Until it is granted the stream delivers pure silence, which the dialog reports as above; grant it under System Settings → Privacy & Security → Microphone.

### Model upgrade (Agent Request dialog)

A run can switch model half-way — within its provider, and normally upwards. The typical use: an instruction runs on a cheap model (Claude Sonnet 5, say) through a set of skills, with the dead ends and retries a weaker model has, and ends at the **Agent Request** dialog; the natural next request is *"look back over the run you just had and edit the skills you used so it goes more smoothly next time"* — a job for a stronger model, on the very context the weak one produced. So the dialog carries an **Upgrade** box (Alt+U), under the Mike / Auto-send row:

- **Tick it, type your request, press Enter** — from that reply to the end of the run every API call goes to the **upgrade model** at its own thinking level: the same conversation, tools, skills and system prompt; only the model and its thinking setting change. Temperature, verbosity and Fast keep the run's settings. The output pane says so at once (`⬆ Model upgraded for the rest of this run: claude-sonnet-5 → claude-fable-5-1 (Max), from call #13 on.`), the title bar shows the new model, and every cost line from then on is priced at the new model's rates.
- The box's **label always says what would happen**: *Upgrade to Claude Fable 5.1 (Max) for the rest of this run*; disabled with a reason when there is nothing to move to (*Upgrade: no upgrade model set for this instruction (Model Setup, on the main window)*) or the run is already on that model; ticked and greyed once the run has upgraded, in every later dialog of that run. A run upgrades once; an empty reply (which stops the agent), a Convo-mode end word, an `exit` in any Agent Request dialog (which stops the agent and closes MyAgent) and a dismissed dialog upgrade nothing.
- **The tick is never saved** — each run starts with the box unticked, and nothing about it reaches the instruction, the state file or any settings file, so a stronger model is always a choice made for one run.
- **The upgrade never outlives the run.** When the loop ends, the run's own model comes back for the next START, the title and the Instruction Editor. The switch is never written to the instruction, the store or `agent_state.json` — the periodic state save keeps recording the instruction's model while the upgrade is active — and an instruction SAVEd mid-run keeps its own model.
- The **cost log** records the run under the model it ended on, with `upgraded-from=claude-sonnet-5@call12` in its PARAMETERS field; its cost and token fields are the whole run's, every call priced at the rates of the model that made it, and a 13th field holds each model's own share, so the Cost Log viewers' **By model** blocks split the run between the two models (see [API cost tracking](#api-cost-tracking)). It stays one row per run — the run's own total, calls and time on one line.
- **An upgrade model the pricing tables cannot price is flagged at the switch**: with no pricing row, a warning under the upgrade line says its calls show tokens only and the run's cost line will count only the calls before the switch; a model priced by a family catch-all row gets the usual "may be wrong" warning.
- **The first upgraded call is the dear one** — a model cannot read another model's prompt cache, so that call writes the whole context into a fresh cache at the new model's write rate (Fable 5.1: $12.50 / MTok); the calls after it read that cache at the usual discount.

**Model Setup** is the button right of Voice Setup at the bottom left of the main window (Alt+M), and the upgrade model it sets **belongs to the instruction** — saved in its store entry, so every machine running that instruction upgrades to the same model and each instruction can have its own. The dialog names the instruction in its title and offers **Upgrade model** (every model of the instruction's provider, the same list the Instruction Editor shows; `(none — no upgrade for this instruction)` switches the box off for it) and **Thinking** (the rungs the Instruction Editor would offer for that model; a saved level a model lacks runs at its strongest). **Save writes it straight into the instruction** — no editor SAVE needed. Which instruction: the page the Instruction Editor shows while it is open (selecting a page loads its model settings at once), otherwise the applied one; an instruction not saved yet keeps the choice for the session until the editor's SAVE stores it. Like Voice Setup, the button can't be pressed while an Agent Request dialog is open; the dialog reads the setting afresh each time it opens.

A run upgrades **within its own provider only** (Anthropic → a bigger Claude, OpenAI → a bigger GPT): the switch edits no history, relying on each provider accepting the previous model's reasoning artefacts — replayed Claude thinking blocks, Gemini thought signatures and OpenAI function calls are accepted unchanged by another model of the same provider — with each provider's reactive 400 handling as the backstop. If an instruction's provider changes, its old upgrade model no longer applies. **An instruction with no upgrade model of its own gets its provider's default: Anthropic → `claude-fable-5-1` at Max, OpenAI → `gpt-6-astra` at Max**, and Model Setup says so when it opens on one; the other providers have no default, so their box stays disabled until one is set. `manage_instructions` reads and sets the setting as `upgrade_target` (`{model, level}`).

### API cost tracking

Every call is priced as it streams and shown as a blue cost line (with the Activity checkbox on): `$x this call | $y total` followed by the token buckets. Prices come from longest-prefix-matched tables in `myagent/constants.py` (`ANTHROPIC_PRICING`, `ANTHROPIC_FAST_PRICING`, `OPENAI_PRICING`, `GEMINI_PRICING`, `XAI_PRICING`, `KIMI_PRICING`), documented in `MyAgent_Pricing.txt`.

- **Four disjoint token buckets** — input (uncached), output (thinking / reasoning included), cache write and cache read, each priced at its own rate. OpenAI, Gemini, xAI and Kimi report cached tokens as a *subset* of their input total, so each provider's usage normaliser subtracts them first — otherwise every cached token would be charged twice. Gemini reports its thinking tokens separately from its output, so they are added to output, which is the rate they bill at.
- **Anthropic prompt caching is opt-in, and MyAgent opts in** — three breakpoints under Anthropic's ceiling of four: one on the system prompt (which covers the tool catalog, since the prefix is ordered tools → system → messages) and two rolling markers on the newest turns, so each call reads the previous call's prefix at 0.1x instead of re-paying full price for the whole growing history. The 5-minute TTL's 1.25x write rate matches the tables exactly. History is never mutated (only the breakpointed messages are copied), the markers are re-placed after a context-overflow trim, and the system prompt is fixed for the run, so a skill edited mid-run cannot invalidate the cache (see [Skills](#skills)). SelfBot does the same (its system prompt is fixed per reply).
- **Cache-write billing** — Anthropic bills 5-minute cache writes at 1.25x input; OpenAI bills writes on the GPT-5.6 tiers and GPT-6 (1.25x input — their pricing rows carry a write rate); on the older OpenAI families, Gemini, xAI and Kimi a written token is ordinary input.
- **xAI is exact** — the API reports the billed cost of each call (`usage.cost_in_usd_ticks`), including its cached-input discount and the $0.005 server-tool fees, and MyAgent uses it directly. **Moonshot is exact** from the other direction — usage says how many input tokens were cache hits, priced at each model's much lower cache-hit rate, which keeps Kimi's mandatory reasoning re-send cheap.
- **Anthropic's web-search fee** ($10 per 1,000 searches) is counted from `usage.server_tool_use.web_search_requests` (an errored search is not billed); OpenAI's `web_search_preview` fee never appears in its usage, so OpenAI runs that search read slightly low.
- **Dated prices** — a table entry can be a `DatedPrice` (an inclusive end date, the promo prices and the prices after it), resolved at each lookup, so a promotion ends by itself: the Gemini 3.6 / 3.7 / 3.8 Flash tiers bill $0.75 / $3.75 per MTok through 2026-12-31 and $1.50 / $7.50 from 2027-01-01.
- **Fast mode and fallbacks are priced by the server's answer** — a fast-served Anthropic call from `ANTHROPIC_FAST_PRICING`, a refusal-fallback call at the rates of the model that actually served it.
- **Unpriced is never silent** — a paid provider's model with no pricing row posts an always-shown ⚠ at run start, each call shows its tokens behind *unpriced this call | no pricing row*, and a second ⚠ at run end says the run is not logged (a `$0.0000` line would claim "free" when the truth is "unknown"). A Google model priced only by the family catch-all `gemini-3` row warns at run start that its costs may be wrong. Ollama calls read *local model — no charge*.

**The cost log.** When a run ends — GUI or headless, success or failure — one `;`-delimited line is appended to this machine's log, **`<OneDrive>/MyAppShare/APICostLog_<machine>.txt`** (each machine writes only its own file, so syncing never conflicts, and OneDrive mirrors every machine's log everywhere; the repo-root `APICostLog.txt` is the fallback without OneDrive). SelfBot writes one line per process, on close. Ollama runs are logged at `0.0000` whenever a call returned usage; a paid run with no price, or a STOP before the first result, is not logged. Haiku 5.5 has two rate cards, and each call is priced on the one its prompt size selects: once a call's input plus cache exceeds 100K tokens the run moves to $0.50 / $2.50 per MTok, and the output pane says so once. Lines are written LF-only on every OS (the files are read cross-platform), fields are only ever appended — a line with fewer fields is still valid, and a missing value shows as `-` in the viewers, distinct from a real `0` — and the file self-rotates: past ~100 KB (`APICOST_LOG_MAX_BYTES`) it becomes a one-slot `.old` archive and restarts with a marker line, checked only at an append. The viewers total the live files only, so once a machine rotates, the grand total stops being lifetime spend; raise the cap if that matters.

**A run killed from outside still gets its line** (since 2026-10-08). The line is written when the run's loop ends, so until then a reboot, a `taskkill` or a power cut left nothing — the run that prompted this finished its task, waited at the Agent Request prompt for a reply, and a Windows Update restart took the laptop while it waited: a complete transcript, no line. MyAgent now keeps an in-progress record, `agent_run_<N>.json` beside the instance's state file, rewritten after every call with what the line would say if the run ended now and removed when the run ends. A record still there at the next launch means that instance died mid-run, and the launch writes the line from it — the run's own model and settings, the time of its last completed call, `unfinished@call<N>` added to PARAMETERS — and says so with a ⚠ line in the output pane. A run that ends normally, or fails with an error, writes exactly the line it always did.

| # | Field | Meaning |
|---|---|---|
| 1 | `timestamp` | `YYYY-MM-DD HH:MM:SS`, local time |
| 2 | `provider` | `Anthropic` / `OpenAI` / `Google` / `xAI` / `Moonshot` / `Ollama` |
| 3 | `model` | model id as sent to the API (for an upgraded run, the model it ended on) |
| 4 | `cost` | USD, 4 decimals (`0.0000` for Ollama) |
| 5 | `params` | the thinking / temperature summary the title bar shows, comma-joined — e.g. `mode=Adaptive`, `reasoning=Medium, temp=1`, `thinking=16K`, `speed=fast`, `upgraded-from=claude-sonnet-5@call12` |
| 6 | `secs` | the run's duration in whole seconds, excluding time spent waiting on you (the Agent Request and every confirmation dialog pause the clock); empty on SelfBot lines |
| 7 | `instruction` | the saved Agent Instruction the run was launched from, snapshotted at run start (SelfBot: the active system prompt); blank for an ad-hoc run; a `;` in a name is written as `,` |
| 8 | `calls` | the run's `Call #N` API round-trip count, recorded on the error path too (SelfBot: the session total) — every call after the first is a tool-use round-trip |
| 9 | `in` | uncached input tokens |
| 10 | `out` | output tokens, thinking / reasoning included |
| 11 | `cache_write` | cache-write tokens (0 where writes are not billed) |
| 12 | `cache_read` | cache-read tokens — so `in + cache_write + cache_read` is the billed input volume |
| 13 | `split` | only on a run more than one model served — a [Model upgrade](#model-upgrade-agent-request-dialog), or an Anthropic refusal fallback — or one served entirely by a model other than the one logged: each model's own share, `model,cost,in,out,cache_write,cache_read`, first-used first, joined by `\|` (cost at six places, so the parts sum to field 4); always written, possibly blank, on a line that carries field 14 |
| 14 | `chat` | since 2026-10-07: the name the run's chat files are saved under in the shared `saved_chats/` folder (`<OneDrive>/MyAppShare/saved_chats/` since 2026-10-08, so the file opens from any machine) — `<chat>.json` + `<chat>.txt`, no extension — the "Save Chat as" name every run has; a `;` in it is written as `,`. Absent on SelfBot's lines |

```
2026-09-14 21:38:56;OpenAI;gpt-5.6-terra;0.1119;reasoning=Medium, verbosity=medium;44;Act_on_unread_emails;6;408;1447;27663;122743
2026-09-27 08:54:37;Anthropic;claude-fable-5-1;0.1674;mode=Max, upgraded-from=claude-sonnet-5@call1;7;ZZ_ModelUpgrade_Smoke;2;4;286;20696;0;claude-sonnet-5,0.026414,2,60,10324,0|claude-fable-5-1,0.140970,2,226,10372,0
2026-10-07 09:12:41;OpenAI;gpt-6-luna;0.0129;reasoning=Max, verbosity=low;117;Act_on_unread_emails;9;612;8138;48113;276910;;Act_on_unread_emails_2026-10-07_091044
```

**The API Cost Log viewers** (`CostLog_Win.ps1` on Windows, `view_costlog.command` behind `API Cost Log.app` on macOS) merge every machine's file and page the same report (shown here from the Windows viewer; the macOS one separates with `·` and `→`):

```
SUMMARY
  1234 runs - $456.7890 total
  span: 2026-07-01 08:00:00  ->  2026-09-22 17:59:00
  today (2026-09-22):      $0.0588
  Mon   (2026-09-21):      $3.2045
  Sun   (2026-09-20):      $2.5227
  Sat   (2026-09-19):     $14.5937
  Fri   (2026-09-18):      $1.4184
  Thu   (2026-09-17):     $23.8035
  Wed   (2026-09-16):     $15.4909
  Tue   (2026-09-15):      $2.2957
  this month (2026-09):  $168.2549
```

— the grand total and span; today and each of the seven days before it (every calendar day, `$0.0000` for a day without a run, in one decimal-aligned money column); this month; spend **By machine**, **By provider**, **By model** and **By instruction** (named runs only), highest first; **By model (tokens and effective blended rate)** — per-model IN / OUT / CACHE-W / CACHE-R totals, cost ÷ tokens as $/MTok, and **CACHE%** (cache reads as a share of the input side, the cache effect alone — the blended rate's denominator also carries output tokens, which bill several times input); then a **THIS MONTH (yyyy-MM)** block repeating the four rollups over the current month only; then the **FULL LOG**, most recent first, one run per row: `DATE/TIME · MACHINE · PROVIDER · COST(USD) · TIME(sec) · CALLS · TOK-IN · TOK-OUT · CACHE-W · CACHE-R · MODEL · PARAMETERS · INSTRUCTION · CHAT` (tokens compacted to k / M; CHAT, since 2026-10-07, is the name of the run's chat files in the shared `saved_chats/` folder, blank on older lines). The numeric columns come before the open-ended text ones (fixed-width on Windows, `column -t` on macOS), so the per-run cost stays on screen at any console width. The By-model blocks split a line carrying a split in field 13 between its models; every other block counts it as the one run it was.

### Other niceties

- **Parallel tool execution** — the parallel-safe tools (`web_search`, `fetch_webpage`, `csv_search`, `get_skill`, `read_document`, `read_file`, `glob_files`, `grep_files`, `run_instruction`) run first, concurrently; results are reinserted in API order.
- **Safety dialog** — the editor's **Safety** button lists every shell `COMMAND_CONFIRM` pattern and every destructive mail tool with a checkbox. Unticking one bypasses that confirmation at once for the session, and SAVE keeps it with the instruction (each bypassed action logs a ⚠ audit line); a command is confirmed while ANY pattern it matches is still ticked. `COMMAND_BLOCKED` refuses outright (registry deletes, `reg delete`, a `shutdown` other than `/a`, …); the confirm list covers PowerShell's command aliases too (`ri` / `erase`, `mi` / `move` / `mv`, `iwr … -OutFile`, `iex(`, …). A dialog that fails to build denies the command. Bypasses are essential for headless scheduled runs, which would otherwise wait forever on a dialog nobody can click. Patterns are stored as strings, so a cross-machine instruction can carry the **union** of Windows and macOS bypasses — each OS matches only its own list and the other's ride along inert — and the button counts the two kinds, e.g. `Safety (2 bypassed, 4 other-OS)`.
- **Hard tool blocklist** — an instruction's `blocked_tools` names are stripped from the tools offered to the model AND refused at dispatch if called anyway — the deterministic complement to confirm-bypass for unattended runs, where a prompt directive is otherwise the only guard. It suits tools an instruction NEVER needs (a name-level block cannot allow a tool the task legitimately uses sometimes). It has no dialog; edit it with `manage_instructions` or in the store. Provider-side server tools run in the provider's sandbox and are not covered.
- **Wrong-OS save-path guard** — the three mail `*_get_attachment` tools and `camera_capture` pass a model-supplied path through `normalize_save_path` (`myagent/helpers.py`): `~` expands, and on macOS a Windows drive-letter path is redirected to `~/Temp/<filename>` with a note in the result.
- **Window and field colours (Other Setup)** — the **Other Setup** button, right of Model Setup at the bottom left of the main window (Alt+O), opens a dialog with three independent colours. **Window colour** is the background of the main window and every dialog — and, on Windows 11, their title bars, with black or white title text as the colour's brightness asks, so two instances wearing different colours are told apart from the taskbar. **Field colour** is the background of everything that is white by default: the output pane and every other text box, the entries, the temperature spinbox, the lists, every dropdown and its list, and the Instructions list, whose zebra stripes and section header bands become shades of it. **Button colour** is the face of every button, the pressed and hovered face included; the toolbar's light-blue "last pressed" highlight stays what it is. For each, **Choose…** opens the system colour picker and **Default** goes back to the platform colour; either previews on the spot — every window open at the time changes at once, and every dialog opened later is born with the colours. **Save** keeps all three, written straight into this instance's `agent_state.json` beside the window positions, so they come back at the next launch and a second instance can have its own; **Cancel**, Escape or [X] put the opening colours back. Text is never recoloured, so a dark choice means dark text on dark. Windows 10 and macOS keep the system title bar, and on macOS the dropdown fields and the buttons keep their native look.
- **Display toggles** — the six checkboxes on the main window's bottom row: **Debug** (the exact payload each call sends, with multi-line strings rendered readably), **Tool Calls**, **Activity** (per-call cost lines and progress notes), **Show Thinking**, **Save Thinking** (thinking blocks kept in saved chats) and **Diag** (the coordinate-mapping trail).
- **Every run saves its chat** (since 2026-10-07; before, only a run with a name typed in "Save Chat as" did) — `<name>.json` (the conversation, tools, system prompt and model settings) plus `<name>.txt` (the output pane), written by the periodic auto-save while the run goes and again on close, into **`<OneDrive>/MyAppShare/saved_chats/`** since 2026-10-08: one folder for every machine's runs, so a transcript the cost log names can be opened from any of them (the repo-root `saved_chats/` on a machine without OneDrive). At launch MyAgent moves this machine's earlier chats into the share, in the background and never overwriting (SelfBot's chats and the older code-interpreter files stay in the repo-root folder). The name is whatever you typed in "Save Chat as"; with the box empty (the default) START fills it with `<Instruction>_<YYYY-MM-DD_HHMMSS>` (`Agent_…` for an ad-hoc run), the form `-l` launches have always used, and a later START replaces an auto name with a fresh one so each run has its own files (a name you typed is kept, and the next run overwrites it). The cost log's CHAT field names the files.
- **State persistence** — `agent_state.json` (`agent_state_2.json` and up for further instances) keeps the provider, model, thinking and display settings, the window, field and button colours (Other Setup), the applied instruction's snapshot, the Instructions list's folded sections and sash, and the geometry of the main window (including whether it is maximized), the Instruction Editor, the Skills Manager and the Safety, Agent Request and Confirm Command dialogs — saved **per monitor layout**, so docked, undocked and multi-display arrangements each restore their own positions. A layout with nothing saved borrows the most recent one whose position is still visible, and an off-screen or tiny window falls back to a sensible default (dialogs centre on their parent); Voice Setup, Model Setup, Other Setup and the Section… prompt always open centred on their parent. For ~1.5 s after launch the main window reclaims its restored monitor if an external window manager (PowerToys FancyZones, DisplayFusion, …) moves it to another one; a move you make is never fought. (To keep FancyZones away from MyAgent altogether, add `pythonw.exe` to its excluded apps — which also covers SelfBot, CSVEditor and TodoList.) On Windows, a window restored onto a screen whose scaling differs from the primary's comes back at exactly its saved size: Tk alone would grow it by one frame difference per launch, which is what made windows on a 100 % second screen creep right and down. State files are written atomically.
- **Retry and timeouts** — HTTP 429 / 5xx exponential backoff on every cloud provider (429 capped at 60 s, 529 at 90 s, up to 10 attempts; Ollama has its own); on OpenAI, xAI and Moonshot a 180 s first-content timeout, restarted at each attempt, with an elapsed-time ticker; OpenAI timeout retries; and session-learned rejections for models that refuse `temperature` or a specific server tool.
- **Context-overflow compaction (Anthropic)** — a 400 `prompt is too long` mid-task doesn't end the run: the oldest conversation rounds are dropped in place (only at real user-turn boundaries, always keeping the last two rounds) and the call retries. Only when even the recent context overflows does the run end — with an actionable message naming the 1M-context models and `error` in its result file.
- **LaTeX → Unicode** — assistant text is post-processed (`\frac{a}{b}` → `a/b`, Greek letters, arrows, braced super / subscripts, whole commands only) without touching tool output; the Agent Request text is shown verbatim.
- **Title bar** — `My Agent — <instruction> [<provider> / <model> | <parameters>]`, with "— synced" when the shared store is in use.

### Keyboard operation (no mouse needed)

Every MyAgent window — the main window and all eight dialogs — can be driven entirely from the keyboard. Tk already makes every control a Tab stop; `myagent/keyboard.py` adds what Tk leaves out (a way out of a text box, Enter on buttons, Alt+letter shortcuts, and traversal of the Safety dialog's scrolled checkboxes). Three rules cover everything:

1. **Tab / Shift+Tab move between controls**, in creation order — which is the visual order almost everywhere. The focused control is marked: buttons get a ring (on macOS the system's blue one; Windows also dots one around a focused checkbox), text fields show the blinking cursor, lists highlight the active row. Read-only panes (the main output pane, the confirm dialog's command box, the Agent Request's message) are Tab stops too, so they scroll and copy from the keyboard; scrollbars are not.
2. **Escape leaves the field you are in — the safe way out of a text box.** Inside a text box Tab *types* a tab, so Escape moves the focus to the next control instead; it never inserts, deletes, submits or closes anything (Ctrl+Tab / Ctrl+Shift+Tab do the same forwards / backwards). The same Escape leaves a single-line entry, the temperature spinbox, a dropdown or the Instructions list. Because Escape is reserved for that, **no window that holds a draft closes on Escape**: the Instruction Editor and Skills Manager close only via [X] or Alt+F4 (and the editor via Apply). The dialogs where nothing can be lost do close on Escape — the Safety dialog (every toggle takes effect as it happens), the command-confirmation dialog (Escape = Deny), and Voice Setup, Model Setup and Other Setup (Escape = Cancel once you are out of a field).
3. **Enter presses the focused button, Space toggles the focused checkbox, and Alt+letter presses a button or jumps to a field from anywhere in its window.** The letters are listed per window below; they are deliberately not underlined, to keep the plain look. A disabled button ignores Enter and its Alt+letter just as it ignores a click.

**Keys that work everywhere**

| Key | Does |
|---|---|
| Tab / Shift+Tab | next / previous control |
| Escape — in a text box, entry, spinbox, dropdown or the Instructions list | leave the field: focus moves to the next control, nothing changes |
| Ctrl+Tab / Ctrl+Shift+Tab — in a text box | the same, forwards / backwards |
| Enter | press the focused button |
| Space | press the focused button / toggle the focused checkbox |
| Alt+letter (see the per-window tables) | press that button / jump to that field |
| Down — on a dropdown | open the list; arrows move, Enter picks, Escape closes the list |
| Up / Down — on the temperature spinbox | step the value |
| Arrows, PgUp / PgDn, Ctrl+Home / End, Shift+arrows, Ctrl+C — in a read-only pane | scroll, select, copy |
| Alt+F4 | close the window, same as [X] |

**Main window** — the top row reads **START, STOP, Instruction**, and the window opens with the focus on Instruction — deliberately not on START: Enter presses the focused button, so a stray Enter on a freshly opened window opens the editor rather than starting a run. Tab order: START → Instruction → Save Chat as → output pane → Voice Setup → Model Setup → Other Setup → Debug … Diag; while a run is active START and Instruction are disabled and STOP takes their place.

| Key | Does |
|---|---|
| Alt+S | START (not during a run) |
| Alt+T | STOP (only during a run) |
| Alt+I | Instruction — opens the editor (not during a run) |
| Alt+C | jump to the "Save Chat as" name |
| Alt+V | Voice Setup — the speech-to-text settings behind the Agent Request dialog's Mike button |
| Alt+M | Model Setup — the instruction's upgrade model, behind the Agent Request dialog's Upgrade box |
| Alt+O | Other Setup — the window background colour, for the main window and every dialog |

**Instruction Editor** — opens with the focus in the instruction text, cursor at the end. Tab order: text → name → SAVE → DELETE → CLEAR → Apply → provider → model → the model-parameter controls (whichever the model shows) → the tool checkboxes → Skills → Safety → Attach Images → Remove Selected → image list → Instructions list → ▲ → ▼ → Section… → back to the text. Escape in the text lands on the name field (the text is the last stop, so it wraps); Shift+Tab from the name field gets back to the text.

| Key | Does |
|---|---|
| Alt+N | Save Instruction name |
| Alt+S, Ctrl+S | SAVE (to the shared store) |
| Alt+D | DELETE (asks first) |
| Alt+C | CLEAR — empties the editor for a fresh instruction and resets the environment settings (model parameters, Safety bypasses, blocklist, Auto-send, upgrade model) at once |
| Alt+L | the Instructions list — Up / Down browse (the selected instruction loads: its text, images, tool toggles and model settings come with it), Left / Right or Enter fold and unfold a section, Enter on an instruction jumps to the text |
| Alt+Up / Alt+Down — in the list | move the selected instruction (or section) one step; at a section's edge an instruction steps into the neighbouring section |
| Alt+T | Section… — file the selected instruction into a section (pick one, type a new name, or Unfiled), or rename the selected section. In the prompt: Enter = OK, Escape = Cancel, Alt+O / Alt+C |
| Alt+A, Ctrl+Enter | Apply — commits the draft to the session and closes the editor; Ctrl+Enter works even inside the text, where plain Enter is a newline |
| Alt+P / Alt+M | provider / model dropdowns |
| Alt+K / Alt+F | Skills / Safety |
| Alt+I / Alt+R | Attach Images / Remove Selected (arrows and Shift+arrows select in the image list); Ctrl+V in the text pastes a clipboard image |
| Alt+E | the instruction text |

**Skills Manager** — opens with the focus on the skill list. Tab order: list → skill text → name → SAVE → DELETE → NEW → description → Cycle Mode → list.

| Key | Does |
|---|---|
| Up / Down — in the list | browse; the selected skill loads into the name / description / text fields |
| Space — in the list | cycle the selected skill's mode (disabled → on → on-demand) |
| Alt+C | Cycle Mode (the same, from anywhere in the window) |
| Alt+K / Alt+E / Alt+T | name / description / skill text |
| Alt+L | the list |
| Alt+S, Ctrl+S | SAVE |
| Alt+D | DELETE (asks first) |
| Alt+N | NEW — clears the fields for a fresh skill |

**Safety dialog** — opens with the focus on the first checkbox.

| Key | Does |
|---|---|
| Tab / Shift+Tab, Down / Up | next / previous checkbox, scrolling it into view as needed |
| Space | toggle — takes effect at once; SAVE the instruction to keep it |
| Escape | close |

**Confirm Command** (a guarded shell command wants approval) — opens with the focus on **Deny**, so a reflexive Enter is the safe answer. Tab order: Deny → Allow → the command text (scrollable).

| Key | Does |
|---|---|
| Enter | press the focused button (Deny, unless you moved) |
| Alt+D / Alt+A | Deny / Allow |
| Escape (from the buttons) | Deny |

**Agent Request** (the agent asks you something via `user_prompt`) — opens with the focus in the reply box, or on **Mike**, with a bell, when Auto-send is ticked (then Enter or Space starts the recording and Enter or Space ends it, and the transcript is sent as it lands; Shift+Tab reaches the reply box to type instead). Tab order: reply → Mike → Auto-send → Upgrade → Attach Images → Remove Selected → image list → the agent's message (scrollable).

| Key | Does |
|---|---|
| Enter | send the reply — except mid-dictation: while Mike is recording, Enter ends the recording (the transcript then lands in the reply box); while it is transcribing, Enter does nothing |
| Ctrl+Enter | newline inside the reply |
| Space or Enter — on Mike | press Mike: start recording, or stop and transcribe (with Auto-send ticked the transcript is then sent at once) |
| Escape — in the reply | leave the reply box without sending (Tab on to the buttons; Shift+Tab back) |
| Alt+M | Mike — start / stop dictating (the focus stays in the reply box, so you can keep typing); its settings are the main window's Voice Setup |
| Alt+A | Auto-send — a dictated transcript is sent the moment it lands (Space toggles it when it has the focus); saved with the applied instruction; ticking it moves the focus to Mike, and with it ticked the dialog opens with the focus on Mike and a bell |
| Alt+U | Upgrade — this reply and the rest of the run go to the instruction's upgrade model (the label names it); disabled with a reason when there is nothing to move to or the run has already moved |
| Alt+I / Alt+R | Attach Images / Remove Selected; Ctrl+V pastes a clipboard image as an attachment |
| Alt+F4 | dismiss — the agent is told you did not respond, and the run goes on (a recording in progress is discarded) |

**Voice Setup** (the main window's Alt+V) — opens with the focus on Provider. Tab order: Provider → Model → Language → Vocabulary hint → Microphone → Test → the test transcript (scrollable) → Save → Cancel.

| Key | Does |
|---|---|
| Alt+P / Alt+M | Provider / Model dropdowns |
| Alt+L / Alt+V | Language / Vocabulary hint |
| Alt+I | Microphone dropdown |
| Alt+T | Test — start / stop a trial dictation with the settings as they stand, unsaved |
| Alt+S | Save and close |
| Alt+C, Escape (outside a field), Alt+F4 | Cancel — nothing is saved |

**Model Setup** (the main window's Alt+M) — edits the upgrade model of the instruction named in its title and opens with the focus on Upgrade model. Tab order: Upgrade model → Thinking → Save → Cancel.

| Key | Does |
|---|---|
| Alt+M / Alt+T | Upgrade model / Thinking dropdowns |
| Alt+S | Save — written into the instruction at once — and close |
| Alt+C, Escape (outside a field), Alt+F4 | Cancel — nothing is saved |

**Other Setup** (the main window's Alt+O) — the window, field and button colours; opens with the focus on the window colour's Choose…. Tab order: Choose… → Default (window colour) → Choose… → Default (field colour) → Choose… → Default (button colour) → Save → Cancel.

| Key | Does |
|---|---|
| Alt+B / Alt+D | Window colour — Choose… (the system colour picker; the pick is previewed on every open window at once) / Default (the platform's own grey, previewed the same way) |
| Alt+T / Alt+E | Field colour — Choose… / Default, for the text boxes, entries, lists and dropdowns |
| Alt+U / Alt+R | Button colour — Choose… / Default, for the face of every button |
| Alt+S | Save — keeps all three colours, written to the state file at once — and close |
| Alt+C, Escape, Alt+F4 | Cancel — the colours the dialog opened on come back |

The remaining questions MyAgent asks — the Yes / No confirmations for destructive mail actions and for DELETE, the skill-name prompt, and the warning boxes — are native message boxes: Tab or the arrows move between the buttons and Enter presses the highlighted one (on Windows, Y / N answer directly). The DELETE and mail-action confirmations start on **No**, so a reflexive Enter declines; the skill-name prompt starts on **Yes**. On Windows, Escape closes only a box with a single OK button.

**Walkthrough: run a saved instruction without touching the mouse**

1. **Alt+I** opens the Instruction Editor (focus lands in the text).
2. **Alt+L** jumps to the Instructions list; **Up / Down** pick an instruction (it loads as you go — **Left / Right** fold and unfold a section), **Enter** jumps into its text.
3. Optional: **Alt+E** to edit the text, **Escape** when done; **Tab** on to the tool checkboxes and **Space** to toggle one.
4. **Alt+A** (or **Ctrl+Enter**) applies and closes the editor.
5. **Alt+S** starts the run; **Alt+T** stops it. **Tab** to the output pane to scroll back through it while it runs.
6. If the agent asks something, type the answer — or **Alt+M**, say it, **Enter** to stop, and check the transcript — and press **Enter**. If it wants to run a guarded command, **Enter** denies and **Alt+A** allows.
7. **Alt+C** and type a name in "Save Chat as" if you want one: the chat is saved under it (left empty, START names it `<Instruction>_<timestamp>`), by the auto-save as the run goes and when you close MyAgent (Alt+F4).

### Architecture (mixins)

`MyAgent.py` (~390 lines) holds only `__init__` and the entry point; the `App` class inherits from **27 mixins** in `myagent/` (~27,800 lines in all), which share state through `self.*`:

| Module | Concern |
|---|---|
| `constants.py` (~4,000 lines) | Tool schemas for all ten families, safety patterns, model constants and capability tables, pricing tables, the voice tables, the UI colours, the upgrade defaults, file paths |
| `helpers.py` | `HTMLTextExtractor` / `extract_text_from_html`, `_ToolBlock` (gives OpenAI / Gemini / xAI / Moonshot / Ollama dict tool calls the same `.name` / `.id` / `.input` face as Anthropic's blocks), the Responses-API usage normaliser, the one-slot log rotation every runtime log uses, `normalize_save_path`, the camera-aware image hint, the context-overflow trim and the Fable thinking-block helpers — stdlib-only, because the zero-token jobs import it |
| `retry_util.py` / `mail_common.py` / `keyboard.py` / `instruction_layout.py` / `datapaths.py` | The five shared non-mixin modules: the 429 / 5xx backoff schedule of the five cloud providers' callers (Ollama keeps its own); the one destructive-action confirmation dialog of the three mail mixins (honours the per-instruction bypass list, posts the ⚠ audit line, pauses the run clock) and their per-id batch runner; the keyboard-operation helpers every window uses; the pure sections-and-order model behind the Instructions list; and the shared-store paths and IO both apps and Heartbeat use (OneDrive resolution, atomic saves, conflict-fork healing, the skills tree, the per-machine cost log) |
| `ui_mixin` / `state_mixin` / `event_loop_mixin` | Widget construction and model-parameter handlers; instance locks, geometry and state persistence; the queue polling that turns worker messages into output-pane text |
| `instructions_mixin` / `skills_mixin` | Instruction CRUD and the editor; skills CRUD, the Skills Manager and system-prompt assembly |
| `streaming_mixin` | The agentic loop, tool dispatch, pricing lookup, message translation, the cost-log writer |
| `anthropic_mixin` / `openai_mixin` / `gemini_mixin` / `xai_mixin` / `kimi_mixin` / `ollama_mixin` | One streaming caller per provider, with its parameter builder and usage normaliser (`kimi_mixin` also carries its own Chat-Completions translators) |
| `mcp_mixin` | The async MCP client on a background event loop |
| `gmail_mixin` / `protonmail_mixin` / `outlook_mixin` | The three 16-tool mail families |
| `document_mixin` / `file_mixin` | `read_document`; the native read / edit / write / glob / grep file tools |
| `desktop_mixin` / `browser_mixin` | pyautogui tools and the coordinate pipeline; Playwright tools |
| `excel_mixin` | Live-Excel tools via xlwings |
| `physical_mixin` | `camera_capture` (OpenCV, imported at first use) and `microphone_listen` (voice input's recorder and transcription) |
| `safety_mixin` / `chat_mixin` | Command guardrails, the confirmation and Agent Request dialogs, `run_powershell`; chat saving, image attachment and paste, LaTeX |
| `voice_mixin` | Voice input: microphone capture, the Mike state machine, the OpenAI / xAI / Google speech-to-text calls, the Voice Setup dialog |
| `model_upgrade_mixin` | The Agent Request dialog's Upgrade box, the switch and its restore at the end of the run, and the Model Setup dialog |
| `other_setup_mixin` | The Other Setup dialog and the window background colour it sets — every open window recoloured at once, every later one born with it |

Adding a static tool = a schema dict in `constants.py` + an `elif` in `_execute_tool()` + a `do_<name>()` in the right mixin (and optionally `PARALLEL_SAFE_TOOLS`). Non-shared helpers carry a module prefix (`_proton_*`, `_outlook_*`, `_excel_*`, …), because in a flat mixin namespace an identically named helper in an earlier mixin wins the MRO. The full architecture invariants are in [.claude/rules/CLAUDE_MYAGENT.md](.claude/rules/CLAUDE_MYAGENT.md).

---

## Zero-token automation (UnreadSummary.py & Heartbeat.py)

Two scheduled scripts that do in deterministic Python what would otherwise be an agent instruction: *polling and list-building need no judgment*, so the model spend belongs in the work, not the trigger.

**UnreadSummary.py** (~1,290 lines) — the daily unread-mail digest. One pass scans **every configured account** (Gmail, Proton Bridge / IMAP, Outlook) for unread mail in Inbox and Spam / Junk and builds one continuously numbered COMPREHENSIVE LIST (account, from, subject, date, the first ~45 words of the cleaned body), with a divider-framed `TOTAL:` section — unread count, SPECIFYING tally, any `ERRORS:` line — between the header and the first account block.

- **Bill matching** — known bill / receipt senders are matched against **`SpecifyingList.csv`**, which lives in the OneDrive folder `MyImportant/DeathFinances` shared by all three machines (found via `%OneDrive%` / `%OneDriveConsumer%`, `~/OneDrive` or `~/Library/CloudStorage/OneDrive-*`; a repo-root copy is the fallback, and the file is gitignored). The CSV is semicolon-delimited with every field quoted; columns `Type`, `Index`, `To`, `From`, `From email`, `Subject` (a *prefix* match) and `Determine` — `Type` and `Index` are echoed as one `Type=…, Index=…` line under the entry's `SPECIFYING LIST EMAIL` marker, `Determine` as a note under it, and `From email` records the sender's actual address for reference only (the parser matches by column name). Matched PDF attachments are saved idempotently to `MyImportant/DeathFinances/Attachments`, so the saved bills appear on every machine.
- **Send, then mark** — the digest is emailed from the Outlook account, and only then are the matches marked read, so a pass that fails before or during the send changes no mailbox and the next pass lists the same bills again.
- **Read-only by construction** — listing uses only read-only primitives (IMAP `EXAMINE` + `BODY.PEEK`, Gmail `messages.get`, Graph `GET`), and every mutation sits behind a flag (`SAVE_MATCH_PDFS` and `MARK_MATCHES_READ` on, `TRASH_MATCHES` off). `python UnreadSummary.py --dry-run` prints the would-be email with zero mutations and no send.
- **Failures are lines, not crashes** — auth is silent-only (a dead token becomes an `ERROR:` line in the digest, repaired by running MyAgent once interactively), an `accounts.json` that won't parse becomes an ERROR block, each failed account is also logged as its own `ACCOUNT ERROR <account>: <reason>` line (Proton Bridge not running shows as a `ConnectionRefusedError` on 127.0.0.1:1143), Outlook folders are read to the end however many are unread, and a STARTTLS failure falls back to a plain login only on loopback (Bridge). The one fatal case is the sending Outlook account itself: with its token dead, no digest can go out.
- **The digest opens on screen** — it is also written to `unread_summary.txt` beside the log, laid out 80 columns wide (the email keeps its narrow 50-character dividers), and opened in a text viewer on the machine that ran the pass: Notepad++ when installed (on PATH or in the usual folders), else Notepad, on Windows; the default text editor (`open -t`) on macOS; `xdg-open` elsewhere. `--dry-run` writes and opens it too, with a DRY RUN banner. The viewer is best-effort — a failed launch is a log line, never a failed pass — and `--no-view` keeps the file but skips it.
- **Logs** — a few lines per pass in `unread_summary.log` (the rules loaded, each match, each account error, the send), in `~/Library/Logs/myagent/` on macOS and the repo root on Windows, self-rotating past ~100 KB to a one-slot `.old` archive.

**Heartbeat.py** (~370 lines) — on-demand agent dispatch from anywhere you can send an email. launchd (the Mac Mini, a short `StartInterval`) and Task Scheduler (`MyAgent_Heartbeat_8min` on the desktop, `MyAgent_Heartbeat_5min` on the laptop) run it against the **same** Gmail account; each pass checks for an **unread** message whose subject is that machine's code — **`APD`** (the Windows desktop, DESKTOP-NAMOR), **`APL`** (any other Windows machine, i.e. the laptop) or **`APM`** (macOS), derived from `platform.system()` and the hostname so one file serves all three. The per-machine subject routes each trigger to exactly one executor.

- Body line 1 names a saved instruction; lines 3+ are the new prompt core.
- The instruction's text between its first and **last** `*****` marker lines is replaced (header and footer kept; the core may contain `*****` lines of its own), atomically, beside the OneDrive-shared `agent_instructions.json` the spawned MyAgent reads, in MyAgent-identical JSON formatting; fewer than two markers poison-pills the trigger instead of mangling the instruction.
- The email is marked read, then `python MyAgent.py -l "<name>" --headless` is spawned detached — a mark that fails launches nothing (the next tick retries), and a launch that fails puts the UNREAD label back.
- One trigger drains per pass; a malformed trigger is poison-pilled (marked read and logged) rather than retried forever; auth never goes interactive; every pass logs at least one line to `heartbeat.log`, so gaps in its timestamps are a liveness record (self-rotating past ~100 KB).
- A **watchdog** guards each spawn: if the previous run of that instruction is under 15 minutes old, the trigger stays unread and waits for the next tick; anything older is killed and replaced — which also reaps a run parked on a forgotten dialog. The rewrite happens before this check, so a waiting trigger's new text is already in place. The Windows process query is bounded at 60 s and handles instruction names with spaces.

Save trigger-target instructions with **Convo off** (and the confirm bypasses they need): headless hides only the main window, and a Convo-mode instruction finishes its work and then waits on an Agent Request dialog until the watchdog clears it. Spawned agents inherit the **scheduler's** environment, not your shell's: on macOS the plist must provide the API keys (source the dotfiles — below — or embed them in `EnvironmentVariables` with the plist chmod 600), and on Windows `setx` user variables work. Embedded keys must be maintained — **when a provider is added, add its key to every plist or task that embeds keys**, or instructions saved for that provider run on the current provider's default model instead (MyAgent warns, re-posting the warning at run start).

---

## Scheduling background runs (launchd / Task Scheduler)

Any saved instruction can run unattended: on macOS a LaunchAgent at `~/Library/LaunchAgents/com.myagent.<slug>.plist` firing `python MyAgent.py -l "<Instruction>" --headless` on a `StartCalendarInterval`; on Windows a Task Scheduler task running `pythonw.exe`. Add `--result-file <path>` for a JSON outcome (`{instruction, status, error, final_text}`) when the loop ends, and `--extra-file <path>` to append per-run task context to the instruction. The shared store's **Schedule Manager** instruction is a conversational front end for all this: with just `run_command` + `user_prompt` it lists, edits, lints (`plutil -lint`), loads and verifies the `com.myagent.*` jobs from plain-English requests — run it in the GUI, never headless.

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

Gotchas:

- **`launchctl` ≠ the schedule** — the *when* lives only in the plist.
- **Missed runs fire once on wake**, not as catch-up replays (a 07:00 job firing at 11:17 after the lid opens is expected).
- **Never log to `/tmp`** (purged every few days); use absolute paths under `~/Library/Logs/` — launchd does **not** expand `~` in plist strings.
- **Headless + destructive tools needs confirm-bypass** in the instruction's Safety list, or the run waits forever on a dialog nobody is there to answer. Likewise **save the scheduled variant with Convo off**: headless hides only the main window, so a Convo-mode instruction finishes its work and then parks on a reply dialog, never closing and never writing its `--result-file`.
- **TCC permissions follow the launch chain, not the script**: the same venv Python presents three identities depending on who spawned it — the `My Agent.app` applet, the terminal, or (under launchd) the bare Python interpreter. Grant Screen Recording / Automation / Camera / Microphone to the identity that will actually run it; the launchd identity is the Homebrew Python binary, so a major Python upgrade orphans its grants.
- **The "would like to access data from other apps" dialog is never remembered**: macOS 15+ guards other apps' sandbox containers (`~/Library/Containers`, `~/Library/Group Containers`) with a per-process consent, and every shell command the model runs is a new process, so each one that touches such a folder asks again — unattended, it hangs the run. Full Disk Access for the launcher is the one setting that ends it (see **Desktop launchers**); MyAgent's own file tools and Excel tools stay out of those folders since 2026-10-06.
- **Source the dotfiles instead of embedding keys**: a plist running `/bin/zsh -c 'source ~/.zshenv 2>/dev/null; source ~/.zshrc 2>/dev/null; exec <venv-python> MyAgent.py -l "<Instruction>" --headless …'` picks up **every** provider's key automatically — no per-provider plist maintenance and no silent model drift.
- **Ghost displays strand windows off-screen**: an unplugged monitor or TV leaves its Spaces record behind, and an app can restore its window onto that dead space — invisible, and the automation hangs. Purge with `defaults delete com.apple.spaces && killall Dock` (safe: the Dock regenerates it from the live displays).
- **Three layers prove a run worked**: `LastExitStatus`, the timestamped transcript in the shared `saved_chats/` folder, and the real-world side effect. Exit 0 alone proves nothing about delivery.

---

## CSVEditor.py — Lightweight CSV Editor

A single-file spreadsheet-style editor (~670 lines), used mostly on bank-statement CSV exports and `SpecifyingList.csv`:

- **Dialect preservation** — on open the delimiter (`,` `;` tab `|`) is sniffed and a quote-all heuristic detects "every field quoted" files; saves reproduce both, so a `;`-delimited, fully quoted file keeps its format (saved as UTF-8 with CRLF line endings).
- **Three independent filters** (column + value comboboxes, values populated from the data), with a live "Showing X of Y rows" status.
- **Sort by Date** toggle — recognises a `Date` column and parses `D/M/Y`, ISO, `D Mon Y` and friends; unparseable rows sink to the end. **Sort A-Z** sorts by the `To` column when the file has one, else by the first column.
- **Editing** — double-click any cell for an in-place entry (Enter commits, Esc cancels); insert row above / below, copy row, delete row, with the selection restored after each operation.
- **State** lives outside the repo at `~/.config/csveditor/state.json` (geometry, last file, filters, sorts, and user-dragged column widths).

On macOS, `CSVEditor.app` gives it a double-click launch that focuses the existing window instead of starting a second copy; on Windows a Desktop shortcut runs `desktop_launchers/CSVEditor_Win.ps1` for the same launch-or-focus behaviour.

---

## TodoList.py — Todo Manager

A single-file tkinter todo app (~660 lines): tasks have a **priority** (High / Medium / Low), a **category** (a default set, and any new name you type becomes a saved category), an optional **due date** (DD/MM/YYYY, validated), a creation date and a done flag. The list highlights High-priority tasks in red, greys out completed ones and gives **overdue** tasks a red background; click a column heading to sort (priority order is semantic, dates parse properly), filter by status / priority / category, and reorder with Move Up / Down. Double-click toggles done. `LaunchTodoList.bat` is the Windows one-click launcher (its Desktop shortcut uses `desktop_launchers/icon_todolist.ico`); on macOS `desktop_launchers/rebuild.sh` builds the matching `TodoList.app` (launch-or-focus, same icon).

**Cross-machine sync** — `todos.json` lives in **`<OneDrive>/MyAppShare/todos.json`**, auto-located per platform (Windows: the `OneDrive` / `OneDriveConsumer` / `OneDriveCommercial` environment variables; macOS: `~/Library/CloudStorage/OneDrive-*`, then `~/OneDrive`), overridable with a `TODOLIST_DATA_DIR` environment variable and falling back to the script's folder on a machine without OneDrive (the title bar shows "— synced" when the shared file is in use). The first run to find the shared slot empty seeds it from the local file. Concurrent use is handled several ways: a 5-second mtime poll adopts another machine's synced write as soon as it lands (safe because every action saves immediately; paused while the edit dialog is open); a save that would overwrite a file changed underneath asks first (a **Sync conflict** prompt: Yes saves anyway, No reloads); OneDrive conflict copies (`todos-<Computer>.json`) are merged back in at startup; and writes are atomic (temp file + `os.replace`). `todo_state.json` (geometry / filters / sort) is deliberately per machine and stays in the script's folder; both files are gitignored — OneDrive, not git, is the sync channel.

**Native C++ port (macOS)** — `TodoList.mm` is a functionality-identical Objective-C++ / Cocoa port; `./build_todolist_native.sh` compiles it to `TodoList.exe` (an arm64 Mach-O in the repo root, gitignored — the `.exe` is just a name) with nothing beyond the Xcode Command Line Tools. The data layer is Foundation-only C++ (the same path resolution, 5-second poll, conflict-fork absorption, Sync conflict prompt and atomic writes) and goes through `NSJSONSerialization`, so **both implementations round-trip the same synced `todos.json`** and share the per-machine `todo_state.json` schema. The build script first runs a headless characterization suite (`tests/test_todolist_native.mm`, ~90 checks: date-parsing strictness, Python sort stability including `reverse=True` semantics, fork merging, state files) and a Python ↔ native JSON interop round-trip. `TodoList (Native).app` is its launch-or-focus launcher, and it focuses a running instance of *either* implementation, since a native + Python pair would race the shared sync poll.

**Native C++ port (Windows)** — `TodoList.cpp` is the Windows twin: the same port as a single C++ / Win32 file (a ListView with the app's colours via custom draw, Per-Monitor-V2 DPI with live rescale, a modal edit dialog, the 5-second poll and the Sync conflict prompt), compiled by `.\build_todolist_native.ps1` to the same gitignored `TodoList.exe` name (an x64 PE; the build needs any Visual Studio edition with the C++ workload, located via `vswhere`). Win32 has no system JSON, so the port hand-rolls a reader / writer that reproduces Python's `json.dump(data, f, indent=2)` **byte for byte** — `\uXXXX` escapes, CRLF, insertion-ordered keys, unknown keys preserved, raw number tokens passed through — so a native save is indistinguishable from a Python one. The build runs `tests/test_todolist_native.cpp` (~113 checks, including a section locking the JSON format) and a Python ↔ native interop stage that asserts a **byte-identical** rewrite before compiling. `desktop_launchers/TodoListNative_Win.ps1` is its launch-or-focus launcher (it focuses a running `TodoList.exe` *or* `TodoList.py`, and shows a "build it first" dialog when the exe is missing). The ListView's six columns stretch proportionally on every resize like the Tk Treeview (a user-dragged column becomes the new basis), the status label is text-measured and right-anchored like Tk's `pack(side=RIGHT)`, and because Tk on Windows is DPI-unaware, the shared `todo_state.json` geometry is kept in Tk's 96-dpi units — the exe converts at the boundary, so both implementations restore the same visual window on a scaled display.

---

## MyBackup.py — Directory Mirror Backup

A single-file tkinter app (~1,220 lines) that keeps backup copies of whole directory trees — the OneDrive `MyAppShare` folder onto the desktop's HDD, a photo library onto a USB disk. The setup is a **table of two columns, FROM and TO**: each row names a directory to back up (its whole subtree) and the directory that must end up as an exact copy of it. The GUI follows CSVEditor — a toolbar, a Treeview with inline editing (double-click a cell; Enter commits, Esc cancels), Insert Row Above / Below, Copy Row, Delete Row — plus **Browse FROM… / Browse TO…** directory choosers for the selected row (with nothing selected they append a row first).

- **BACKUP** mirrors every row: missing directories are created, new and changed files are copied (a file is unchanged when its size matches and its modification time agrees within 2 s — the FAT32 resolution — so a repeat run copies only what changed), and anything in TO that is no longer in FROM is **deleted**, so afterwards each TO is identical to its FROM. The copy runs on a worker thread with a progress bar and a per-file status line; **Cancel** stops after the current file. Before a run that would delete anything the app asks once (default **No**) and lists the doomed items per row; a run that deletes nothing just goes. **Preview** plans the same run and reports it without touching a byte.
- **The log pane** shows, for each run, one line per row (what was found, what will be copied / created / deleted), a `Row N done` line with the counts and any per-item errors, and a green or red summary. The same lines go to `~/.config/mybackup/mybackup.log` (one-slot rotation at 1 MB).
- **Setup files and persistence** — the table is a plain CSV with the header `FROM,TO` (CSVEditor opens it too). **Open… / Save / Save As…** work as in CSVEditor; a table that was never saved lands in the default file, `~/.config/mybackup/backup_setup.csv`, and **BACKUP always saves the table first**, so the layout last backed up is the one a headless run mirrors. `state.json` beside it remembers the window geometry, the column widths and which setup file is current. All of it is **per machine** on purpose: the paths are machine-specific (`D:\` on the desktop, `/Volumes/…` on the Mac), so nothing of MyBackup's lives in OneDrive.
- **Safety rules**, checked for every row before anything is touched (a failing row is reported and the other rows still run — one unplugged backup drive should not stop the others): FROM must exist; TO may not be FROM, inside FROM, or contain FROM; TO may not be a drive / filesystem root or the home directory (a mirror deletes everything else there); **TO's parent folder must exist** — MyBackup creates only the last path component, so an unplugged `D:` or an unmounted `/Volumes/Backup` is an error rather than a copy onto the system disk; and no two rows may share a TO or nest one TO inside another (the second mirror would delete the first's copy). Symlinks and junctions are never followed or copied (a stray one in TO is removed as a link, never its target), and if the FROM scan was incomplete (a folder it could not list) that row is copied but **nothing is deleted** from its TO — a transient permission problem must not erase the backup copy. Read-only files in TO are overwritten and deleted as needed (Windows refuses both until the attribute is cleared).
- **Headless** — `python MyBackup.py --headless` mirrors the saved setup with no window and no questions, printing the same lines as the log pane; `--dry-run` prints the plan and changes nothing; `--setup FILE` picks another setup file (the default is the one last open in the GUI, else `backup_setup.csv`). Exit code 0 means every row mirrored cleanly, 1 that a row was refused or an item failed, 2 that there is no usable setup — so a Task Scheduler / launchd job can run `pythonw.exe MyBackup.py --headless` (or `python3 MyBackup.py --headless`) on a schedule, as the [zero-token jobs](#scheduling-background-runs-launchd--task-scheduler) do.

Known limits: files are compared by size and modification time, not content, so a file rewritten to the same size with its old timestamp preserved is not recopied (rare; `robocopy /MIR` makes the same choice); OneDrive Files On-Demand placeholders are downloaded when copied, so the first backup of a cloud-only folder is slow; and very long Windows paths need the system's long-path setting, as with any Python copy. The mirror engine is pure Python with no Tk in it and is pinned by `tests/test_mybackup.py`.

On macOS, `MyBackup.app` (built by `desktop_launchers/rebuild.sh`) gives it a double-click launch that focuses the existing window instead of starting a second copy; on Windows a Desktop shortcut runs `desktop_launchers/MyBackup_Win.ps1` for the same launch-or-focus behaviour, which matters here because two instances could mirror into the same TO at once. Its icon — two drive slabs with a down-arrow between them, the lower drive's light green — is the family's one deliberately sober design.

---

## Desktop launchers

**macOS** — `desktop_launchers/` holds the AppleScript sources of the double-clickable apps:

| App | What it does |
|---|---|
| `UnreadSummary.app` | Runs the digest on demand, with a success chime and a self-dismissing dialog showing the run's log line |
| `CSVEditor.app`, `TodoList.app`, `MyBackup.app` | Launch-or-focus |
| `TodoList (Native).app` | Runs the compiled `TodoList.exe`, focusing a running instance of either TodoList implementation |
| `My Agent.app`, `SelfBot.app` | Always launch a fresh instance — both apps are multi-instance by design (press SelfBot twice for the self-chat pair) |
| `Heartbeat Log.app`, `API Cost Log.app` | Viewers: page `heartbeat.log` (meaningful events first, idle ticks hidden) or the cost-log report in a Terminal `less` (`view_heartbeat.command` / `view_costlog.command`) |

Rebuild them per machine with `./desktop_launchers/rebuild.sh`, which patches in the local repo path, renders each 1024-px icon master into an iconset with `sips`, compiles with `osacompile`, gives each app a stable `CFBundleIdentifier` (`com.roman.launcher.<slug>`), signs it, and applies the icon with `NSWorkspace.setIconForFile`. **Signing uses a stable self-signed certificate ("Roman Launcher Signing")**, which makes each app's designated requirement `identifier + certificate leaf`, so TCC grants (Screen Recording, Automation, Camera, Microphone, …) survive rebuilds; on a machine without the certificate `rebuild.sh` falls back to ad-hoc signing, where every rebuild re-asks (the certificate recipe is in the launcher README). The built apps live in `~/Applications` with Finder **aliases** on the Desktop (an app running *from* a TCC-protected folder such as the Desktop triggers consent prompts). Completion feedback is a dialog + chime, which needs no permission and isn't swallowed by Focus modes. The MyAgent / SelfBot launch lines source `~/.zshenv` and `~/.zshrc` first — a GUI app starts with a bare environment and `do shell script` runs `/bin/sh` — and background the app with `cd X && (nohup … &)`, so the applet exits at once. `refresh_launcher_icons.command` re-applies the launcher aliases' custom icons in one go, for when an iCloud-synced Desktop strips one on launch.

**Windows** — each hidden launcher's Desktop shortcut runs its `*_Win.ps1` twin under `conhost.exe --headless powershell.exe …`, so no console window flashes (the batch-file `TodoList.lnk` wraps `LaunchTodoList.bat` in `conhost.exe --headless cmd.exe /c …`), while the two log viewers open a visible `powershell.exe`; the repo path is resolved from each script's own location, so any clone works unedited and only the `.lnk` is per machine.

| Script | What it does |
|---|---|
| `UnreadSummary_Win.ps1` | Runs the digest with the venv Python; a chime and a self-dismissing success dialog with the run's log line, or a blocking error dialog with the log tail; `-DryRun` passes `--dry-run` through |
| `CSVEditor_Win.ps1`, `MyBackup_Win.ps1` | Launch-or-focus with the venv `pythonw` |
| `TodoListNative_Win.ps1` | Launch-or-focus across both TodoList implementations, with a "build it first" dialog when `TodoList.exe` hasn't been compiled here |
| `MyAgent_Win.ps1`, `SelfBot_Win.ps1` | Always launch a fresh instance with the venv `pythonw` (SelfBot's second press opens the self-chatting peer, cascaded off the first) |
| `HeartbeatLog_Win.ps1`, `CostLog_Win.ps1` | The two viewers — a *visible* console paging `heartbeat.log` or the cost-log report, reading the logs as explicit UTF-8 so Windows PowerShell 5.1 doesn't garble them |
| `ProtonBridge_Watchdog_Win.ps1` | Not a shortcut: a Task Scheduler job (`ProtonBridge_Watchdog`, registered once with `-Register`; at logon + 2 min, then every 15 min) that starts Proton Bridge whenever no `bridge.exe` is running, so the IMAP server the Proton tools and the digest depend on survives a tray quit or a crash |

Every icon is an `icon_*.ico` rendered from a 1024-px `icon_*_master.png`, several of them generated by `make_*_icon.py` scripts in the same folder. Full details: [desktop_launchers/README.md](desktop_launchers/README.md).

---

## Claude Code integration

This repo is developed *with* Claude Code and configured *for* it:

- **CLAUDE.md** — the project's conventions and commands. The per-app architecture files live in `.claude/rules/` — `CLAUDE_SELFBOT.md`, `CLAUDE_MYAGENT.md` and `CLAUDE_CLOSE_CHROME.md` (the two browser-closing scripts) — with `paths` frontmatter, so each loads only when a session touches its files; a nested `desktop_launchers/CLAUDE.md` loads only when a session works in that folder.
- **Project-scoped slash commands** in `.claude/skills/` (plus one in `.claude/commands/`; all tracked, so they work from any fresh clone):

| Command | What it does |
|---|---|
| `/sync-check` | Fresh `git fetch`, compares local vs `origin/<branch>` tips, reports ahead / behind / diverged + uncommitted changes |
| `/commit-push` | Stages the changed tracked files by name, drafts a style-matched commit message, commits with the standard trailer, pushes. Skips `.DS_Store`, scratch files and GUI-auto-modified state files unless told otherwise |
| `/urp` | "Update README, commit, push" — re-reads recent history, refreshes the docs to match the code, then stages *everything* (`git add -A`), commits and pushes |
| `/launch-agent` | Kills running Python instances, launches MyAgent in the background |
| `/launch-selfbot` | Kills running Python instances, launches SelfBot in the background |
| `/run script.py` | Activates the venv and runs the named script |
| `/sync-refultra` | Fetches and hard-resets the local `refultra` branch to exactly match `origin/refultra` — the remote wins; it pauses only if substantial uncommitted source edits would be lost, and never touches untracked runtime state |

The six skills set `disable-model-invocation: true`, so they fire only when you type them; `/sync-refultra` lives in `.claude/commands/` without that flag, so it is the one command Claude may invoke itself.

- **WHATIS_AI.md** — an essay on *why* LLM tool use works, told as the story of a man in a cell with only a terminal: a metaphor for how a model "experiences" API messages and tools.

---

## Cross-platform notes

A runtime `IS_WINDOWS` constant branches platform behaviour; each OS gets its native mechanism, or graceful degradation where macOS has no equivalent:

| Feature | Windows | macOS |
|---|---|---|
| Shell tool | PowerShell | bash |
| Screenshots | `ImageGrab.grab(all_screens=True)` | Quartz `CGWindowListCreateImage` per display (pyautogui only sees the primary) |
| OCR (`read_screen_text`) | `winocr` (Windows.Media.Ocr) | Apple Vision framework via PyObjC |
| Camera | Media Foundation, then DirectShow | AVFoundation, with the permission requested on first use |
| DPI awareness | SelfBot: `SetProcessDpiAwareness(2)`; MyAgent: `PER_MONITOR_AWARE_V2` (correct coordinates on mixed-DPI multi-monitor setups) | Not needed |
| Monitor enumeration | `EnumDisplayMonitors` | `CGGetActiveDisplayList` |
| Instance detection | MyAgent: lock files + `OpenProcess` PID + executable-name and command-line checks; SelfBot: a named mutex | Lock files + PID + command-line check (`ps`) |
| Dialog placement | `transient(parent)` works across screens | `transient()` skipped (the WM confines transients to the parent's screen) |
| Button focus ring | Tk's own | A 3-px ring, so Aqua draws the system's blue one |
| Monospace font | Consolas | Menlo |
| Launchers | `.bat` + PowerShell twins under `conhost --headless` | `.app` bundles from AppleScript (sourcing the zsh dotfiles) |
| Scheduling | Task Scheduler (`pythonw.exe`) | launchd LaunchAgents |

---

## OneDrive shared folders

Everything the apps share between machines lives in two OneDrive trees, both **outside the repository** — so nothing in them can reach GitHub, and a clone on a machine without OneDrive runs on the repo-root fallbacks in the tables below, every one of them gitignored. The root is located per platform: on Windows the `OneDrive*` environment variables, on macOS `~/Library/CloudStorage/OneDrive-*` (preferring `-Personal`), then the legacy `~/OneDrive`. `MYAGENT_DATA_DIR` overrides the shared folder for MyAgent, SelfBot, `Heartbeat.py` and the Cost Log viewers, `TODOLIST_DATA_DIR` for TodoList. Two pre-2026-07-19 names are folded in automatically on sight: `<OneDrive>/MyAgent/` (renamed in place to `MyAppShare`) and `<OneDrive>/TodoList/todos.json` (moved across).

**`<OneDrive>/MyAppShare/`** — the suite-wide shared folder, resolved by `myagent/datapaths.py` (SelfBot try-imports it, with an in-file stub for a standalone copy; TodoList, its native ports and the Cost Log viewers carry their own copy of the same discovery):

| Path | What lives there | Read / written by | Repo-root fallback (gitignored) |
|---|---|---|---|
| `agent_instructions.json` | MyAgent's instruction library, reference images embedded as base64 | MyAgent; `Heartbeat.py` rewrites an instruction's `*****` core | `agent_instructions.json` |
| `system_prompts.json` | SelfBot's saved system prompts, each with its full main-screen environment | SelfBot | `system_prompts.json` |
| `skills/<name>/SKILL.md` | the skills tree — one folder per skill, bundled resource files beside the `SKILL.md` | MyAgent and SelfBot | `skills/` |
| `APICostLog_<machine>.txt` | one API cost log **per machine** (an append-only log cannot be merged, so machines never share one file) | MyAgent and SelfBot append their own; both Cost Log viewers read every machine's | `APICostLog.txt` |
| `todos.json` | the shared todo list | `TodoList.py` and the native `TodoList.mm` / `TodoList.cpp` ports | `todos.json` |
| `general/` | files agent runs save when a skill directs them there (the `physical-interface`, `westpac-recon` and `westpac-transaction-export` skills name it) — no code in the repository does | agent runs | none |
| `saved_chats/` | MyAgent's chat transcripts since 2026-10-08 — every run's `<Instruction>_<timestamp>.json` + `.txt` and the code-interpreter outputs (`ci_output_*`): one folder every machine's runs write to, so the file the cost log's CHAT column names opens anywhere (names carry a timestamp, so machines never write the same one). Each machine's earlier MyAgent chats are moved in at launch, in the background, never overwriting (a same-named file with other content keeps its name and the newcomer lands as `<name>__<machine>`). SelfBot's chats are NOT here — see below | MyAgent | `saved_chats/` |
| `*.bak`, `skills.json.migrated.bak` | leftovers of past store repoints and the 2026-08-07 `skills.json` → tree migration; nothing reads them | — | `*.bak` |

**`<OneDrive>/MyImportant/DeathFinances/`** — `UnreadSummary.py`'s folder, and the only other OneDrive location any app touches:

| Path | What lives there | Repo-root fallback (gitignored) |
|---|---|---|
| `SpecifyingList.csv` | the bill-matching rules, edited by hand and read at every pass | `SpecifyingList.csv` |
| `Attachments/` | each matched bill's PDF, saved idempotently so the saved bills appear on every machine | `Attachments/` |

**Deliberately per machine, outside OneDrive:** `agent_state*.json` (window geometry, the window background colour and the applied instruction, per instance), Voice Setup's `~/.config/myagent-voice/config.json`, the mail credentials under `~/.config/myagent-google/`, `~/.config/myagent-protonmail/` and `~/.config/myagent-msmail/`, CSVEditor's `~/.config/csveditor/state.json`, MyBackup's `~/.config/mybackup/` (its FROM / TO setup, state and log — the paths it holds are this machine's drive letters and mount points), TodoList's `todo_state.json`, `mcp_servers.json`, the repo-root `saved_chats/` (SelfBot's chats — MyAgent's moved to the share 2026-10-08 — and the `ci_output_*` files saved before that day), and the logs `heartbeat.log`, `unread_summary.log` and `unread_summary.txt` (repo root on Windows, `~/Library/Logs/myagent/` on macOS).

---

## Repository map

| Path | What it is |
|---|---|
| `SelfBot.py` | The chatbot (single file by convention) |
| `MyAgent.py`, `myagent/` | The agent entry point + the 26-mixin package |
| `CSVEditor.py`, `TodoList.py`, `MyBackup.py` | The three small single-file apps |
| `TodoList.mm`, `build_todolist_native.sh` | The native macOS port of TodoList — the script tests (including a Python ↔ native JSON interop round-trip) and then compiles to the gitignored `TodoList.exe` |
| `TodoList.cpp`, `build_todolist_native.ps1`, `TodoList.rc`, `TodoList.exe.manifest` | The native Windows port — the same test-then-compile flow (its interop stage asserts a byte-identical rewrite of Python's JSON); the rc / manifest embed the icon, comctl32 v6 and Per-Monitor-V2 DPI awareness |
| `UnreadSummary.py`, `Heartbeat.py` | The zero-token scheduled jobs (their bill-matching rules, `SpecifyingList.csv`, live in OneDrive `MyImportant/DeathFinances`, not in git) |
| `tests/` | The characterization suite (stdlib `unittest`, no extra dependencies; 88 modules, 1,519 tests) — pure and model-detection helpers, the pricing and cost-log layer, the provider parameter builders, the geometry and state layer, the OneDrive store sync and skills tree, keyboard operation, the Instructions list, voice input (a fake `sounddevice`), the Physical tools (a fake `cv2`), the Model upgrade, the Other Setup window, field and button colours on real widgets, the `exit` reply that ends a run and closes the app, MyBackup's mirror engine on real temp trees, Heartbeat and UnreadSummary, and the real Cost Log viewer against synthetic logs. The GUI tests build real widgets on transparent or withdrawn Tk roots and skip themselves where no display (or no focus) is available. Run it with `python -m unittest discover -s tests -t .`. Also here: the native TodoList ports' compiled suites (`test_todolist_native.mm` / `.cpp`, ~90 / ~113 checks, run by the build scripts) and two hand-run live checks outside the `test*.py` pattern — `check_excel_live.py` (drives a real Excel) and `check_voice_models_live.py` (the paid speech-to-text audit) |
| `desktop_launchers/` | The macOS AppleScript sources + `rebuild.sh` + `refresh_launcher_icons.command`, the Windows `*_Win.ps1` twins, the two log-viewer scripts, the icon masters, `.ico` files and icon generators (built `.app`s and `.lnk`s are per machine, not in git) |
| `close_chrome.ps1`, `close_chrome.sh` | Clean shutdown of the automation browser at the end of a browser instruction (called via `run_command`) — PowerShell for Windows, a bash twin for macOS. They match processes by the automation profile path in their command line, so a personal browser window open at the same time is never touched: graceful close → poll → force-kill only the matched leftovers → reset the profile's `exit_type`, so a force-killed run never leaves a "restore pages?" bar over the next run's page |
| `requirements.txt` | Core dependencies |
| `*.Modelfile` ×3 | Ollama tool-template grafts for vision models |
| `MyAgent_Pricing.txt` | The human-readable reference for the pricing tables in `myagent/constants.py` |
| `mcp_servers.example.json` | Tracked template for the gitignored `mcp_servers.json` |
| `docs/selfbot-tool-audit.html` | An HTML audit of SelfBot's tool system — every tool call traced schema → dispatch → handler |
| `docs/voice-stt-audit.md` | The speech-to-text suitability audit behind Voice Setup's model list (re-runnable with `tests/check_voice_models_live.py`) |
| `LaunchSelfBot.bat`, `selfbot_position.ps1`, `LaunchMyAgent.bat`, `LaunchMyAgent.sh`, `My Agent.command`, `LaunchTodoList.bat` | Per-platform command-line launchers (`LaunchSelfBot.bat` force-kills every running Python process, then opens and positions the SelfBot duo) |
| `CLAUDE.md`, `.claude/rules/`, `.claude/skills/`, `.claude/commands/` | Claude Code project instructions, per-app architecture docs and the slash commands above |
| `.gitattributes` | The line-ending policy: LF in the repo; CRLF on checkout only for `.bat` / `.ps1`; `.sh` / `.command` forced LF even on Windows, since a CRLF shebang is a macOS "bad interpreter" |
| `WHATIS_AI.md` | The tool-use essay |
| `comparison_*.json` ×5 | Final-report JSONs (the `--result-file` shape) from one run of the same `Weather_Agent_Skill_based` instruction on Anthropic, Google, Moonshot, OpenAI and xAI — a side-by-side sample of how each provider narrates the identical task |
| `miscSavedStuff/` | Artifacts the SelfBot duo produced outside the chat transcripts — the Shaun & Nigel essays, season finales and letters, *The Mirror Problem*, a SelfBot tools report, and a colophon |
| `saved_chats/` | SelfBot's chat transcripts, and the code-interpreter files saved before 2026-10-08 (gitignored; 39 early files — mostly the SelfBot duo's Shaun & Nigel conversations, plus a few solo chats — are tracked by choice). MyAgent's chats live in `<OneDrive>/MyAppShare/saved_chats/` since 2026-10-08, and the ones a machine still holds here are moved there at its next launch |
| `BirdFlying.html` | A self-contained browser animation: two Australian magpies (SMIL flap-and-glide wings, a Web Audio synthesized warble — click the scene to enable sound) flying over a stylized homestead and gum grove. No dependencies |
| `merge_system_prompts.py` | Standalone key-level union merger for the name-keyed JSON stores, for one-off manual merges |
| `make_icon.py`, `myagent.ico`, `selfbot.ico`, `selfbot_duo.ico`, `todolist.ico` | A Windows icon generator and its icons |
| `make_weather_pdf.py`, `make_weather_pdf_print.py`, `plot*.py`, `create_chart.py`, `move_window.py`, `agent_demo.py` (+ `plot.png`, `plot_negative.png`, `gaussian_plot.png`, `graph.png`, `population_chart.png`) | One-off agent-written scripts and their outputs, kept as testbed samples |
| `TOOLS_REFERENCE.txt`, `MyAgent_Tools_Reference.{txt,pdf}`, `Tools.txt`, `Tools_Windows.txt`, `Tools_macOS.txt` | Agent-written tool references from the early three-provider version — superseded; the live catalog is `myagent/constants.py` and the [Tool catalog](#tool-catalog) above |
| `README_old.md`, `Markdown_Cheat_Sheet_2.md`, `Launch.txt`, `MyTest_autostart.txt` | Archive: an earlier README, a GitHub-flavoured Markdown cheat sheet, and two saved launch-command snippets |
| `agent_instructions.json`, `system_prompts.json`, `skills/` | The instruction / prompt / skill libraries — in `<OneDrive>/MyAppShare/` (repo-root fallback without OneDrive; gitignored) |
| `app_state*.json`, `agent_state*.json`, `agent_run_*.json`, `todo_state.json`, `TodoList.exe`, `*.lock`, `APICostLog.txt*`, `heartbeat.log*`, `unread_summary.log*`, `unread_summary.txt` | Runtime state, build artifacts and logs — created automatically and gitignored (TodoList's `todos.json` and the live `APICostLog_<machine>.txt` files live in `<OneDrive>/MyAppShare/`) |

**Privacy note — `.gitignore` is not retroactive.** A gitignore rule only suppresses *untracked* files; anything committed before a rule stays in the repository's history. `saved_chats/` is gitignored, and only 39 early files remain tracked, by choice (mostly the SelfBot duo conversations) — but older commits still contain other chats and agent-run screenshots. `agent_instructions.json`, `skills.json` and `system_prompts.json` are gitignored and live in OneDrive, but older commits contain earlier versions of them, whose instruction text included personal email addresses. No API key has ever been committed (keys live in environment variables and `~/.config/` token files, and `mcp_servers.json` is gitignored with only the `.example` tracked). If you fork or clone this, audit with `git ls-files <path>` for what is tracked *now* and `git log --all -- <path>` for what was *ever* committed — a gitignore entry alone proves neither.

**Conventions:** this is a testbed — keep code simple and focused. SelfBot, CSVEditor, TodoList and MyBackup stay single-file (the native TodoList ports keep the convention as one file per platform); MyAgent changes go in the appropriate mixin. Tests exist where the logic is pure: the characterization suite under `tests/` (`python -m unittest discover -s tests -t .`), with `ruff check MyAgent.py myagent/` for linting — no mypy, and no build step for the Python apps (the only compiles are `build_todolist_native.sh` / `.ps1` for the native TodoList ports). After editing a `.py` file, re-run it (closing any running instance first) — for the GUI apps, the running app is still the real test.
