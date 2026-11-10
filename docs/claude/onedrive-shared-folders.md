# OneDrive shared folders (detail)

<!-- Moved verbatim from CLAUDE.md on 2026-11-10 to keep the always-loaded file small. CLAUDE.md points here; update this file, not CLAUDE.md, with detail. -->

## OneDrive shared folders
Everything the apps share between machines lives in two OneDrive trees, both OUTSIDE the repository — nothing in them can reach GitHub (this repo is public), and a clone on a machine without OneDrive runs on the repo-root fallbacks in the tables below, every one of them gitignored. The root is located per platform (Windows: the `OneDrive*` environment variables; macOS: `~/Library/CloudStorage/OneDrive-*`, preferring `-Personal`, then the legacy `~/OneDrive`); `MYAGENT_DATA_DIR` overrides the shared folder for MyAgent, SelfBot, `Heartbeat.py` and the Cost Log viewers, `TODOLIST_DATA_DIR` for TodoList. Two pre-2026-07-19 names are folded in automatically on sight: `<OneDrive>/MyAgent/` (renamed in place to `MyAppShare`) and `<OneDrive>/TodoList/todos.json` (moved across). The same list, for users, is the README's **OneDrive shared folders** section — keep the two in step.

**`<OneDrive>/MyAppShare/`** — the suite-wide shared folder, resolved by `myagent/datapaths.py` (SelfBot try-imports it, with an in-file stub for a standalone copy; TodoList, its native ports and the Cost Log viewers carry their own copy of the same discovery):

| Path | What lives there | Read / written by | Repo-root fallback (gitignored) |
|---|---|---|---|
| `agent_instructions.json` | MyAgent's instruction library, reference images embedded as base64 | MyAgent; `Heartbeat.py` rewrites an instruction's `*****` core (an instruction without one is left as is — the prompt rides with the run as `--prepend-file`) | `agent_instructions.json` |
| `system_prompts.json` | SelfBot's saved system prompts, each with its full main-screen environment | SelfBot | `system_prompts.json` |
| `skills/<name>/SKILL.md` | the skills tree — one folder per skill, bundled resource files beside the `SKILL.md` | MyAgent and SelfBot | `skills/` |
| `APICostLog_<machine>.txt` | one API cost log PER MACHINE (an append-only log cannot be merged, so machines never share one file), never rotated since 2026-10-25 | MyAgent and SelfBot append their own; both Cost Log viewers read every machine's | `APICostLog.txt` |
| `todos.json` | the shared todo list | `TodoList.py` and the native `TodoList.mm` / `TodoList.cpp` ports | `todos.json` |
| `general/` | files agent runs save when a skill directs them there (the `physical-interface`, `westpac-recon`, `westpac-transaction-export` and, since 2026-10-20, the `anz-transaction-export` / `bank-recon` skills name it — the ANZ exports land here as one headerless `ANZ_export_<CODE>_<timestamp>.csv` per account) — no code in the repository does, so a path inventory of the code misses it; the `physical-interface` skill saves its photos here as `photo_<yyyymmdd_hhmmss>.jpg` and, since 2026-10-17, shows one for N seconds in a single self-closing `run_command` — a topmost WinForms viewer on Windows, Preview open + sleep + AppleScript close on macOS (not yet run on the Mac) — so a timed show costs one call instead of the old open / screenshot / sleep / Alt+F4 / screenshot sequence | agent runs | none |
| `saved_chats/` | MyAgent's chat transcripts since 2026-10-08 — every run's `<Instruction>_<timestamp>.json` + `.txt` and the code-interpreter outputs (`ci_output_*`): one folder every machine's runs write to, so the file the cost log's CHAT column names opens anywhere (names carry a timestamp, so machines never write the same one); each machine's earlier MyAgent chats are moved in at launch, in the background, never overwriting (`chat_mixin._fold_local_chats_in` → `datapaths.migrate_local_chats`). SelfBot's chats are NOT here — see below | MyAgent | `saved_chats/` |
| `*.bak`, `skills.json.migrated.bak` | leftovers of past store repoints and the 2026-08-07 `skills.json` → tree migration; nothing reads them | — | `*.bak` |

**`<OneDrive>/MyImportant/DeathFinances/`** — `UnreadSummary.py`'s folder, and the only other OneDrive location any app touches:

| Path | What lives there | Repo-root fallback (gitignored) |
|---|---|---|
| `SpecifyingList.csv` | the bill-matching rules, edited by hand and read at every pass | `SpecifyingList.csv` |
| `Attachments/` | each matched bill's PDF, saved idempotently so the saved bills appear on every machine | `Attachments/` |

**Deliberately per machine, outside OneDrive:** `agent_state*.json` (window geometry, the window background colour and the applied instruction, per instance), `agent_run_*.json` (a running instance's in-progress cost record, gone when its run ends), Voice Setup's `~/.config/myagent-voice/config.json`, the mail credentials under `~/.config/myagent-google/`, `~/.config/myagent-protonmail/` and `~/.config/myagent-msmail/`, Heartbeat's Telegram bot under `~/.config/myagent-telegram/` (each machine's own bot token and paired chat, plus the records of runs awaiting their reply), CSVEditor's `~/.config/csveditor/state.json`, MyBackup's `~/.config/mybackup/` (its FROM / TO setup, state and log — the paths it holds are this machine's drive letters and mount points), TodoList's `todo_state.json`, `mcp_servers.json`, the repo-root `saved_chats/` (SelfBot's chats — MyAgent's moved to the share 2026-10-08 — and the `ci_output_*` files saved before that day), and the logs `heartbeat.log`, `unread_summary.log` and `unread_summary.txt` (repo root on Windows, `~/Library/Logs/myagent/` on macOS).
