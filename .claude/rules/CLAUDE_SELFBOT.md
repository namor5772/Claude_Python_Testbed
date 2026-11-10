---
paths:
  - "SelfBot.py"
  - "LaunchSelfBot.bat"
  - "selfbot_position.ps1"
---

## SelfBot architecture — the rules most tasks need

The full contract of every subsystem (what it does, why, the date and the tests that pin it) lives in
`docs/claude/selfbot/` — read the paragraph for the subsystem you are changing BEFORE changing it; the map is at the end.

**Single class design** — The `App` class contains all UI, API, tool execution, and persistence logic. No separate modules.

**Invariants that cut across the code:**
- **Single file**: every SelfBot change goes in `SelfBot.py`. It inherits `myagent/` mixins (MCP, Gmail, Proton / IMAP, Outlook and, since 2026-10-14, the Document / File / Desktop / Browser / Excel / Physical / Voice tool mixins) while its Meta (`manage_skills` / `manage_prompts`) and Exit tools are its own and stubs `datapaths` when the package is absent.
- **Anthropic-only by design** — never add another provider (the user's decision, reaffirmed 2026-09-06); provider work goes in MyAgent.
- **Byte-identical copies are pinned**: SelfBot carries in-file copies of MyAgent helpers (the toolbar highlight, list title band, focus-ring / scrollbar / quit-handler installers, dialog geometry, the system-prompt freeze, `_log_api_cost` and the cost record, the output cap, `_run_command_env`, …). `tests/test_selfbot_*` pin them to the originals — change both sides together.
- **Tk only on the main thread**: `stream_worker` runs on a daemon thread and talks to the UI through the queue.
- **`ANTHROPIC_PRICING` is unpacked directly here**, so its rows stay plain tuples in `myagent/constants.py`.
- **The system prompt is frozen per reply**; a saved system prompt is a full environment bundle (names, model + params, tool toggles, skill modes, Safety bypasses) — a new main-screen setting is added to the bundle on save and load.
- **The duo talks through files** (`AUTO_MSG_FILE`, the opener file, `selfbot_busy_<1|2>.flag`); a change to one instance's side of the protocol is tested with both instances (`tests/test_selfbot_duo_turns.py`, `tests/test_selfbot_opener_handoff.py`).
- **Every change gets a test** (`tests/test_selfbot_*.py` import SelfBot in-process and stub `App` via `__new__`), and the suite passes before a commit.

## Where each subsystem's contract lives
- `docs/claude/selfbot/models-and-api.md` — Model picker, the Anthropic call path, caching, retries and MyAgent parity: Model picker & per-model parameters; Prompt caching (`cache_control`); System prompt fixed per reply (2026-09-29); API retry logic; Context-overflow recovery (self-chat sliding window); Claude Fable 5.1 harness surface (2026-09-02); MyAgent parity — the October 2026 Anthropic-path changes (2026-10-14)
- `docs/claude/selfbot/tools-and-safety.md` — Tools, MyAgent tool parity, the Exit tool and command safety: Tool system; Adding a new tool; MyAgent tool subsystems (Meta / MCP / Google / IMAP / Outlook — and, since 2026-10-14, Desktop / Browser / Excel / Physical / file / document); Exit tool (`exit_conversation`); Command safety / confirm patterns; Tool parity with MyAgent (2026-10-14)
- `docs/claude/selfbot/ui-windows-state.md` — Threading, thinking display, geometry, DPI, close, keyboard / focus fixes, voice input: Threading model; Thinking accumulator lifecycle; Show Thinking checkbox; Save Thinking checkbox; Dual geometry persistence; Dialog geometry persistence — per instance, per monitor layout (2026-09-16); DPI handling (SelfBot); Why V2; Auto-save on close; Toolbar "last pressed" highlight (2026-09-08); Skills Manager list band (2026-09-12); Button focus ring on macOS (2026-09-15); Scrollbars out of the Tab order (2026-09-26); Cmd+Q is the same close as [X] on macOS (2026-10-31); Voice input (2026-10-14)
- `docs/claude/selfbot/duo.md` — The two-instance duo: peer detection, shutdown, opener handoff, turn-taking: Peer detection (cross-platform); Graceful duo shutdown; Opener handoff (2026-10-14); Duo turn-taking (2026-10-14)
- `docs/claude/selfbot/stores-and-prompts.md` — Shared stores, skills, and system prompts as environment bundles: Shared-store sync; Skills system; System prompt = environment bundle
- `docs/claude/selfbot/cost-tracking.md` — API cost tracking: API cost tracking
