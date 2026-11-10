---
paths:
  - "MyAgent.py"
  - "myagent/**"
  - "tests/**"
  - "Heartbeat.py"
  - "UnreadSummary.py"
---

## MyAgent architecture — the rules most tasks need

The full contract of every subsystem (what it does, why, the date and the tests that pin it) lives in
`docs/claude/myagent/` — read the paragraph for the subsystem you are changing BEFORE changing it; the map is at the end.

**Mixin-based modular design** — The `App` class in `MyAgent.py` inherits from 27 mixin classes in the `myagent/` package, each grouping related methods by concern (UI, streaming, tools, persistence, etc.). Constants and tool schemas live in `myagent/constants.py`; helper classes in `myagent/helpers.py`. Only `__init__` and the entry point remain in `MyAgent.py`. All mixins share state through `self.*` — no inter-mixin imports; the code they do share lives in the non-mixin modules (`helpers.py`, `retry_util.py`, `mail_common.py`, `datapaths.py`, `keyboard.py`) and, since the 2026-10-19 refactor, in the one base class most of them inherit, `pane_notes.PaneNotesMixin`: `_tool_info(message)` (an Activity line) and `_warn(message)` (an always-shown ⚠ line), the two queue-message shapes nearly every pane line a worker posts takes — so every mixin that posts has them whatever host it is composed into, and `check_queue` stays the one consumer.

**Invariants that cut across the code:**
- **Tk only on the main thread.** Worker threads (the agentic loop, provider calls, tools) post to `self.queue`; `check_queue` (event_loop_mixin) is the ONE place messages become output-pane text. Activity and warning lines go through `_tool_info` / `_warn` (`PaneNotesMixin`), not an inline `queue.put`.
- **One builder per request shape.** Each provider's parameters come from one function shared by the live call and the Debug dump (`_openai_model_params`, `_xai_model_params`, `_gemini_thinking_kwargs`, the Ollama request builder) — change the builder, never one side.
- **SelfBot pins byte-identical copies** of several MyAgent methods (`_log_api_cost`, the run-progress record, the output cap, the geometry settle, the toolbar highlight, the system-prompt freeze, `_run_command_env`, the keyboard / focus-ring helpers, …). Changing one means changing SelfBot's copy too; `tests/test_selfbot_*` fail otherwise.
- **Windows are sized only through `_set_geometry` / `_place_window` / `_remember_geometry`** (state_mixin), never a bare `geometry()` — the Windows mixed-scale frame correction lives there.
- **The screenshot pipeline is frozen**: never change resolution caps or coordinate scaling unless explicitly asked.
- **`helpers.py` and `relay.py` stay stdlib-only** — the zero-token `Heartbeat.py` / `UnreadSummary.py` import them.
- **Stores** (`agent_instructions.json`, `system_prompts.json`, the skills tree, the cost logs) go through `datapaths.py` (`load_store` / `save_store`, atomic writes); a running MyAgent / SelfBot rewrites them from memory, so restart it after an outside edit.
- **Pricing**: `constants.py` and `MyAgent_Pricing.txt` change together; `ANTHROPIC_PRICING` stays plain tuples (SelfBot unpacks it); a model a run can use with no pricing row is warned about — give a new model its row the same day.
- **The system prompt is frozen per run** (`_freeze_system_prompt` / `_thaw_system_prompt`); never rebuild it mid-run.
- **Every change gets a test** in `tests/` (bare hosts via `tests/_util.py`; real widgets only where layout matters), and the suite plus `ruff check MyAgent.py myagent/` (NEW findings only) pass before a commit.

## Where each subsystem's contract lives
- `docs/claude/myagent/providers.md` — Providers, model wiring, retries and model audits: Multi-provider support; OpenAI model filtering; Gemini model filtering; xAI (Grok) integration; Moonshot (Kimi) integration; Temperature/thinking UI visibility; Code interpreter gating (OpenAI); OpenAI tool rejection cache; Responses reasoning round trip — OpenAI and xAI (2026-09-30); API retry logic; Context-overflow compaction (Anthropic); Claude Fable 5.1 harness surface (2026-09-02); Anthropic output ceiling (2026-10-11); Anthropic fast mode (2026-09-24); STOP during streaming; Anthropic prompt caching (`cache_control`); System prompt fixed per run (2026-09-29); Cached-input accounting (OpenAI / Gemini); 2026-08-25 audit — `DatedPrice` promo entries.; 2026-09-16 — Gemini 3.8 Flash row and the catch-all pricing-row warning.; OpenAI GPT-6 Astra wiring (2026-09-06); OpenAI GPT-5.6 terra audit (2026-09-06); Gemini audit (2026-09-23); Gemini audit (2026-10-17); Ollama audit (2026-10-18); Anthropic and Moonshot audits (2026-09-23); OpenAI GPT-6 Sol / Luna and the unpriced-model warnings (2026-09-23)
- `docs/claude/myagent/ui-and-windows.md` — UI, keyboard, the Instructions list, windows and state: Toolbar "last pressed" highlight (2026-09-08); Keyboard operation (2026-09-10); Instructions list (2026-09-11); Diag checkbox; Debug payload dump rendering (`_debug_render`); Other Setup — the window background colour (2026-10-04); Editor draft/commit model; State persistence; Window geometry persistence (main window + every dialog); External window-manager reclaim (FancyZones et al.); LaTeX to Unicode conversion; Multi-instance support; State restore fallback
- `docs/claude/myagent/loop-and-tools.md` — The agentic loop, the tool families, safety and launch modes: Agentic loop; Tool system; MCP integration; Gmail (Google) integration; Proton Mail integration; Outlook / Microsoft 365 integration; Local document reader; Native file tools; Excel live-workbook integration; Physical tools (2026-09-21); Click accuracy pipeline; Per-display state tracking; Image resolution caps per provider; find_element tool (Gemini-only); Grid overlay; Weak desktop combo warning; Parallel tool execution; Threading model; PowerShell Safety dialog; run_command timeout + process-tree kill; Per-instruction hard tool blocklist (`blocked_tools`); Command-line launch; Headless mode; Meta-agent tools; Server-side tools; Multi-display desktop tools; `view_image` (2026-10-13)
- `docs/claude/myagent/agent-request-voice-upgrade.md` — Agent Request (relay, pane, images), voice input, model upgrade, Convo mode: Agent Request over Telegram (2026-10-26); Agent Request in the output pane (2026-09-22); Agent Request image attachments; Voice input (2026-09-19); Model upgrade (2026-09-27); Conversational mode
- `docs/claude/myagent/stores-and-chats.md` — Shared stores, the skills tree, instructions and chats: Shared-store sync (`myagent/datapaths.py`); Skills tree (per-file store, 2026-08-07); Agent Instructions; Every run saves its chat (2026-10-07); Chats in the OneDrive share (2026-10-08)
- `docs/claude/myagent/cost-tracking.md` — API cost tracking: API cost tracking
