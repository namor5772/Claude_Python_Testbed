# Claude Python Testbed

A repo containing various Python scripts written using Claude Code. The main application is a full-featured Claude chatbot with a tkinter GUI.

## Contents

- **app.py** — Claude chatbot GUI application (see details below)
- **CLAUDE.md** — Project instructions and conventions for Claude Code sessions
- **system_prompts.json** — Saved system prompts (created at runtime)
- **saved_chats.json** — Saved chat conversations (created at runtime)
- **app_state.json** — Persistent app settings such as last-used system prompt (created at runtime)

## app.py — Claude Chatbot

A desktop chatbot application built with tkinter that connects to the Anthropic API. It supports streaming responses, tool use, image attachments, conversation management, model selection, and customisable system prompts.

### Features

#### Model Selection
- A **Model** dropdown at the top of the window lists all available Claude models, fetched live from the Anthropic API on startup
- Models are shown by display name and the selected model is persisted across sessions via `app_state.json`
- Falls back to a hardcoded list (Sonnet 4.5, Opus 4.6, Haiku 4.5) if the API is unreachable
- Saved chats remember which model was used; loading a chat restores the model if still available

#### Chat Interface
- **Streaming responses** — Claude's replies are streamed token-by-token into the chat display for a real-time feel
- **Multi-turn conversation** — Full conversation history is maintained and sent with each request
- **Color-coded messages** — User messages appear in blue, assistant responses in green, errors in red, and tool activity in grey italics
- **Multi-line input** — The input field supports multiple lines; press **Enter** to send, **Shift+Enter** for a newline

#### Tool Use
The chatbot has three built-in tools that Claude can invoke autonomously during a conversation:

- **web_search** — Searches the web via DuckDuckGo (`ddgs` library) and returns the top 5 results with titles, URLs, and snippets
- **fetch_webpage** — Fetches the full content of a URL using `httpx`, extracts readable text from HTML (stripping scripts, styles, and tags), and truncates to 20,000 characters
- **run_powershell** — Executes a PowerShell command on the local Windows PC and returns the output (stdout + stderr). Commands have a 30-second timeout and output is truncated at 20,000 characters

When Claude decides to use a tool, the app automatically executes it, feeds the result back, and lets Claude continue — this can loop multiple times in a single turn (e.g., search then fetch a result page).

#### PowerShell Safety Guardrails

The `run_powershell` tool uses a two-tier safety system to prevent accidental damage:

**Tier 1 — Hard Blocked** (rejected outright, never executed):
- Disk formatting (`Format-Volume`, `Format-Disk`, `diskpart`)
- Shutdown/restart (`Stop-Computer`, `Restart-Computer`)
- Security policy changes (`Set-ExecutionPolicy`, `bcdedit`)
- Registry mass-deletion (`reg delete`, `Remove-ItemProperty` on HKLM/HKCU)
- User account manipulation (`net user /add`, `Disable-LocalUser`, `Remove-LocalUser`)
- Event log clearing (`Clear-EventLog`)

**Tier 2 — Confirmation Required** (a Yes/No dialog appears, defaulting to No):
- File deletion/modification (`Remove-Item`, `rm`, `del`, `Move-Item`, `Set-Content`, `Out-File`)
- Process/service control (`Stop-Process`, `kill`, `Stop-Service`, `Remove-Service`)
- Package removal (`Uninstall-Package`)
- Code execution (`Invoke-Expression`, `iex`, `Start-Process`)
- Risky flags (`-Recurse`, `-Force`)

**Safe commands** (e.g., `Get-Process`, `Get-ChildItem`, `hostname`, `dir`) run freely without interruption.

#### Image Attachments
- Click **Attach Images** to select one or more image files (PNG, JPG, JPEG, GIF, WEBP)
- Attached images are shown as a purple indicator below the input field (click to clear)
- Images are sent to Claude as base64-encoded content blocks alongside your text message
- If you send images without text, the app defaults to asking "What's in this image?"

#### Chat Management (Toolbar)
A toolbar at the top of the window provides full conversation management:

| Control | Description |
|---|---|
| **Save Chat as** | Type a name in the entry field and click **SAVE** (or press Enter) to save the current conversation |
| **Load Chat** | Select a previously saved chat from the dropdown — instantly restores the conversation with color-coded formatting, the associated system prompt, and the prompt name |
| **DELETE** | Deletes the selected or named chat from disk |
| **NEW CHAT** | Clears the current conversation and display, but keeps the active system prompt |

Saved chats include:
- The full message history (serialised to JSON, with base64 image data stripped and replaced with `[Image was attached]` placeholders to keep file sizes small)
- The system prompt text that was active during the chat
- The system prompt name for easy identification
- The model that was in use

Messages are sanitised on both save and load — extra fields from the Anthropic SDK (e.g. `parsed_output`) are stripped to prevent API rejection errors when continuing a reloaded conversation.

#### System Prompt Editor
Click **System Prompt** to open a dedicated editor window with:

- **Save** — Save the current prompt text under a name for reuse
- **Load** — Select from previously saved prompts via a dropdown
- **Delete** — Remove a saved prompt from disk
- **Clear** — Reset the editor fields
- **Apply to Chat** — Set the editor's prompt as the active system prompt and close the editor

When a named system prompt is applied, the window title updates to show it (e.g., `Claude Chatbot — My Prompt`).

#### App State Persistence
- The last-used system prompt name and selected model are saved to `app_state.json`
- On startup, the app restores both the last system prompt and model automatically
- The app starts in a "new chat" state (empty conversation) with the last system prompt and model pre-loaded

#### Debug Mode
- Toggle the **Debug** checkbox to show/hide the full API payload sent with each request
- When enabled, each API call displays:
  - A red **Call #N** counter badge
  - The complete JSON payload (model, system prompt, tools, messages) with base64 image data truncated for readability
  - Clear `--- PAYLOAD SENT TO API ---` / `--- END PAYLOAD ---` delimiters in orange
- When disabled, call counters still appear (in a subtler style) but payloads are hidden

#### Tool Call Display
- Toggle the **Tool Calls** checkbox independently of Debug to show/hide tool call details
- When enabled, each tool invocation displays the full JSON with tool name, call ID, and input arguments in teal-coloured `--- TOOL CALL ---` blocks
- This is separate from the Debug payload view, so you can see just tool calls without the full API payload, or vice versa

### Requirements

- Python 3 with tkinter (included in standard library)
- An Anthropic API key set as the `ANTHROPIC_API_KEY` environment variable

#### Python Dependencies

```
anthropic
ddgs
httpx
```

### Running

```bash
# Activate the virtual environment
source .venv/Scripts/activate

# Run the application
python app.py
```

### Architecture

The application is a single-file tkinter app structured around the `App` class:

- **UI Layout** — Grid-based layout with 6 rows: model toolbar (row 0), chat toolbar (row 1), chat display + scrollbar (row 2), input field (row 3), button bar (row 4), and attachment indicator (row 5)
- **Threading** — API calls run in a background daemon thread (`stream_worker`) to keep the UI responsive. A `queue.Queue` passes events (text deltas, labels, tool info, errors) back to the main thread
- **Queue Polling** — The main thread polls the queue every 50ms via `root.after()` and updates the chat display accordingly
- **Persistence** — Three JSON files handle different concerns: `system_prompts.json` for the prompt library, `saved_chats.json` for conversation history, and `app_state.json` for user preferences
- **Serialisation** — The `_serialize_messages()` method converts Anthropic SDK Pydantic objects (e.g., `ToolUseBlock`, `TextBlock`) to plain dicts via `model_dump()`, strips base64 image data, and sanitises content blocks through `_clean_content_block()` to remove extra SDK fields (like `parsed_output`) that the API rejects on re-submission
- **HTML Extraction** — The `HTMLTextExtractor` class (a `HTMLParser` subclass) strips HTML tags from fetched web pages, skipping `<script>`, `<style>`, and `<noscript>` blocks, and inserting newlines at block-level element boundaries
- **PowerShell Safety** — Two-tier regex-based guardrail system (`POWERSHELL_BLOCKED` and `POWERSHELL_CONFIRM` pattern lists) checks commands before execution. Confirmation dialogs are dispatched to the main tkinter thread via `root.after()` while the worker thread waits on a `threading.Event`
