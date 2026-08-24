import sys
import os
import datetime
import subprocess

IS_WINDOWS = sys.platform == "win32"

_HAS_DESKTOP = True
try:
    # On macOS ARM64, rubicon-objc (used by mouseinfo) may fail to find
    # objc_msgSendSuper_stret. Pre-import mouseinfo with the error suppressed
    # so pyautogui falls back gracefully.
    if not IS_WINDOWS:
        try:
            import mouseinfo  # noqa: F401
        except Exception:
            sys.modules["mouseinfo"] = type(sys)("mouseinfo")
    import pyautogui
    from PIL import Image, ImageGrab  # noqa: F401 (capability probe — a missing Pillow must set _HAS_DESKTOP = False)
    # Desktop automation safety settings
    pyautogui.FAILSAFE = True   # move mouse to (0,0) to abort
    pyautogui.PAUSE = 0.1       # small delay between actions
except Exception:
    _HAS_DESKTOP = False
if IS_WINDOWS:
    try:
        import pygetwindow as gw  # noqa: F401
    except Exception:
        pass

# Ollama SDK for local-inference provider (Qwen3 etc). Optional — absence just
# hides the Ollama provider in the UI; import failure is not fatal.
_HAS_OLLAMA = True
try:
    import ollama  # noqa: F401
except Exception:
    _HAS_OLLAMA = False

# MCP (Model Context Protocol) client for connecting to external tool servers
# (filesystem, GitHub, Slack, etc.). Optional — absence hides the MCP checkbox
# and leaves MyAgent's behaviour unchanged. Install via `pip install mcp` to enable.
_HAS_MCP = True
try:
    import mcp  # noqa: F401
except Exception:
    _HAS_MCP = False

# Google API Python client for native Gmail (and future Calendar/Drive) tools.
# Optional — absence hides the Gmail checkbox and disables the gmail_* tools.
# Install via:
#   pip install google-api-python-client google-auth-oauthlib google-auth-httplib2
_HAS_GOOGLE = True
try:
    import googleapiclient  # noqa: F401
    import google_auth_oauthlib  # noqa: F401
except Exception:
    _HAS_GOOGLE = False

# Proton Mail integration via Proton Bridge (IMAP + SMTP over localhost).
# Transport is stdlib (imaplib + smtplib), so this flag is always True on
# CPython. Kept for parity with _HAS_GOOGLE / _HAS_MCP — if a future refactor
# swaps in an external Proton library, the flag flips to gate the checkbox.
# Bridge must be installed and running for the proton_* tools to succeed;
# that's detected at first-call time as a connection error, not a startup check.
_HAS_PROTONMAIL = True

# Outlook / Microsoft 365 mail via the Microsoft Graph API (OAuth through MSAL).
# Optional — absence hides the Outlook checkbox and disables the outlook_* tools.
# Install via:  pip install msal requests
# (requests is usually already present; msal is the only new dependency.)
_HAS_OUTLOOK = True
try:
    import msal  # noqa: F401
except Exception:
    _HAS_OUTLOOK = False

# Excel live-workbook automation via xlwings — drives the REAL Excel
# application (COM on Windows, AppleScript on macOS), so the user watches
# changes land in the open workbook, formulas recalculate, and VBA macros run.
# Optional — absence disables the Excel checkbox and the excel_* tools.
# Install via:  pip install xlwings
# Desktop Excel must also be installed; like Proton Bridge, that's detected
# at first-call time as a clear error, not a startup check.
_HAS_EXCEL = True
try:
    import xlwings  # noqa: F401
except Exception:
    _HAS_EXCEL = False


# ── Tool definitions for the Anthropic API ──────────────────────────────────

TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for information. Use this to find current information, answer questions about recent events, look up facts, or find relevant websites. Always prefer searching before guessing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "fetch_webpage",
        "description": "Fetch the full content of a specific webpage URL. Use this after web_search to read a page in detail, or when the user provides a specific URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to fetch",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Execute a command on the local machine and return its output. "
            "Use this for system tasks like listing files, checking processes, reading/writing files, "
            "getting system info, running scripts, installing software, or any other local operation. "
            "Commands run with the current user's permissions. On Windows this runs PowerShell; on macOS this runs bash. "
            "The command is killed after 'timeout' seconds (default 30) — pass a larger timeout for slow "
            "commands like compiles, installers, or test suites. "
            "IMPORTANT: When launching GUI applications, use Start-Process (Windows) or 'open -a' (macOS) "
            "so the command returns immediately instead of blocking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Seconds to wait before the command and its whole process tree are killed "
                        "(default 30, min 5, max 600). Use a larger value for builds, installs, or test runs."
                    ),
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "csv_search",
        "description": (
            "Search a delimited text file (CSV, TSV, TXT, etc.) for records matching a value. "
            "The file must have a header row. You can search a specific column or all columns. "
            "Returns matching rows as formatted text. Use this whenever the user asks to find, "
            "look up, or filter data in a CSV, TSV, or delimited text file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute path to the file (CSV, TSV, TXT, etc.)",
                },
                "delimiter": {
                    "type": "string",
                    "description": "Column delimiter character. Use ',' for CSV (default), '\\t' for tab-separated, '|' for pipe-separated, ';' for semicolons. If omitted, auto-detects from file content.",
                },
                "search_value": {
                    "type": "string",
                    "description": "The value to search for",
                },
                "column": {
                    "type": "string",
                    "description": "Column heading to search in. If omitted, searches all columns.",
                },
                "match_mode": {
                    "type": "string",
                    "enum": ["contains", "exact", "starts_with"],
                    "description": "How to match: 'contains' (default), 'exact', or 'starts_with'. All modes are case-insensitive.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matching rows to return (default 50).",
                },
            },
            "required": ["file_path", "search_value"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Extract text content from a locally-saved file. Provider-agnostic — "
            "operates on any local path regardless of where the file came from. "
            "Natively handles:\n"
            "• PDF (.pdf) — via pypdf; returns text per page, page_count, "
            "metadata (title/author/dates) when present. Pass optional 'pages' "
            "param (e.g. '1-5' or '3' or '1,3,5-7', 1-indexed) for partial reads.\n"
            "• DOCX (.docx) — via python-docx; captures paragraphs AND table "
            "cells in document order, with tables rendered as '|'-separated rows. "
            "Returns paragraph_count, table_count, and core metadata.\n"
            "• HTML (.html/.htm/.xhtml) — uses the same HTMLTextExtractor as the "
            "mail tools (strips script/style content, adds newlines at block "
            "tags, decodes entities).\n"
            "• Plain text formats (.txt/.md/.log/.json/.yaml/.csv/.tsv/.xml/"
            ".py/.js/.sh/etc.) — read directly with UTF-8.\n"
            "• Unknown extensions — tries UTF-8 first (catches mislabelled text "
            "files), falls back to a hex preview of the first 256 bytes.\n"
            "\n"
            "Common pairing: download an email attachment with "
            "proton_get_attachment / gmail_get_attachment (writes to save_to), "
            "then call read_document on that path. Output is JSON with text "
            "(truncated at max_chars, default 50000), text_truncated flag, "
            "format, size_bytes, mime_type, plus format-specific extras.\n"
            "\n"
            "For formats NOT handled natively (XLSX, ZIP archives, audio/video, "
            "RTF, EPUB, scanned-image PDFs needing OCR, etc.), fall back to "
            "run_command with the appropriate CLI tool: 'unzip -l <file>' for "
            "ZIPs, 'pandoc <file> -t plain' for RTF/EPUB/ODT, 'ffprobe <file>' "
            "for audio/video, 'file <file>' to sniff unknown binaries. "
            "Encrypted PDFs are detected and reported clearly rather than "
            "silently returning empty text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute path to the local file (e.g. '/tmp/invoice.pdf')",
                },
                "max_chars": {
                    "type": "integer",
                    "description": "Maximum characters to return in 'text' (default 50000). Reduce for previews; raise carefully — large bodies eat context window.",
                },
                "pages": {
                    "type": "string",
                    "description": "PDF only: page range to extract (1-indexed). Examples: '1-5', '3', '1,3,5-7'. Omit to read all pages.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "user_prompt",
        "description": (
            "Pause execution and display a message to the user, then wait for their "
            "response. You MUST use this tool whenever you need the user to do something "
            "(e.g., log into a website, approve something, make a choice) or when you need "
            "information only the user can provide. NEVER just output text asking the user "
            "something — that ends your turn and they cannot reply. This tool is the ONLY "
            "way to communicate with the user and receive a response. The user may "
            "attach images to their reply; they arrive in the tool result as image blocks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message or question to display to the user",
                }
            },
            "required": ["message"],
        },
    },
]

# Native file tools (file_mixin.py) — Claude-Code-style contracts for reliable
# code editing: exact-unique-match edits that fail loudly, read-before-edit
# tracking, CRLF/BOM round-trip preservation. Always included by _get_tools()
# (no checkbox), like read_document.
FILE_TOOLS = [
    {
        "name": "read_file",
        "description": (
            "Read a UTF-8 text file and return its contents with line numbers "
            "(cat -n style). Reads up to 1000 lines by default; use offset/limit "
            "to page through large files. You MUST read a file with this tool "
            "before editing it with edit_file or overwriting it with write_file. "
            "Prefer this over run_command cat/Get-Content — cheaper, numbered, "
            "and it unlocks editing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the text file (absolute preferred)",
                },
                "offset": {
                    "type": "integer",
                    "description": "1-based line number to start from (default 1)",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum lines to return (default 1000)",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Make a surgical edit to a text file by exact string replacement. "
            "old_string must match the current file content EXACTLY (including "
            "whitespace and indentation) and must be unique in the file — include "
            "a few surrounding lines to make it unique. Zero matches, or several "
            "matches without replace_all=true, fails with a clear error instead of "
            "guessing — fix old_string and retry. The file must have been read "
            "with read_file first. CRLF line endings and BOM are preserved. "
            "ALWAYS prefer this over rewriting files with write_file or editing "
            "via shell commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the file to edit",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to replace (must be unique in the file unless replace_all)",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text (must differ from old_string)",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace every occurrence instead of requiring uniqueness (default false)",
                },
            },
            "required": ["path", "old_string", "new_string"],
        },
    },
    {
        "name": "write_file",
        "description": (
            "Create a new file or fully overwrite an existing one with UTF-8 "
            "content. Overwriting requires the file to have been read with "
            "read_file first this session. Parent directories are created "
            "automatically. For partial changes to an existing file use "
            "edit_file instead — do not rewrite a whole file to change a few "
            "lines."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path for the file (absolute preferred)",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "glob_files",
        "description": (
            "Find files by glob pattern, newest first. Examples: '*.py', "
            "'src/**/*.ts', '**/test_*.py'. Pass 'path' to set the base "
            "directory (default: current working directory). Skips .git, "
            ".venv, node_modules, __pycache__ and similar. Returns absolute "
            "paths ready for read_file."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern; ** matches directories recursively",
                },
                "path": {
                    "type": "string",
                    "description": "Base directory to search under (default: cwd)",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_files",
        "description": (
            "Search file CONTENTS with a Python regular expression. Returns "
            "matching file paths by default; output_mode='content' returns the "
            "matching lines with line numbers, 'count' returns per-file match "
            "counts. Filter candidate files by filename with 'glob' (e.g. "
            "'*.py'). Searches recursively under 'path' (default: cwd; may also "
            "be a single file), skipping .git/.venv/node_modules and binary "
            "files. Prefer this over run_command findstr/Select-String."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Python regex to search for (line-by-line)",
                },
                "path": {
                    "type": "string",
                    "description": "Directory (or single file) to search (default: cwd)",
                },
                "glob": {
                    "type": "string",
                    "description": "Filename filter, e.g. '*.py' or 'test_*.json'",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["files_with_matches", "content", "count"],
                    "description": "What to return (default files_with_matches)",
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive matching (default false)",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Result cap (default 50 files / 100 content lines)",
                },
            },
            "required": ["pattern"],
        },
    },
]

# Meta-agent tools (manage instructions and skills on disk)
META_TOOLS = [
    {
        "name": "manage_instructions",
        "description": (
            "Manage the saved agent instruction library on disk. You CAN read and update "
            "the currently-running instruction — changes are saved to disk and take effect "
            "the next time it is loaded (the live session is not affected). Actions: list "
            "(show all), read (full detail), create (new), update (modify), delete (remove)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "create", "update", "delete"],
                    "description": "The operation to perform",
                },
                "name": {
                    "type": "string",
                    "description": "Instruction name (required for all except list)",
                },
                "text": {
                    "type": "string",
                    "description": "Instruction content (required for create, optional for update)",
                },
                "desktop": {
                    "type": "boolean",
                    "description": "Enable desktop tools (default false on create)",
                },
                "browser": {
                    "type": "boolean",
                    "description": "Enable browser tools (default false on create)",
                },
                "excel": {
                    "type": "boolean",
                    "description": "Enable Excel live-workbook tools — excel_open, excel_read, excel_write, etc. (via xlwings; drives the real Excel app). Default false on create.",
                },
                "meta": {
                    "type": "boolean",
                    "description": "Enable meta tools (default false on create)",
                },
                "mcp": {
                    "type": "boolean",
                    "description": "Enable MCP (Model Context Protocol) tools — external servers from mcp_servers.json (default false on create)",
                },
                "google": {
                    "type": "boolean",
                    "description": "Enable native Google (Gmail) tools — gmail_search, gmail_send, gmail_trash, etc. Requires ~/.config/myagent-google/ setup. Default false on create.",
                },
                "outlook": {
                    "type": "boolean",
                    "description": "Enable native Outlook / Microsoft 365 tools — outlook_search, outlook_send, outlook_trash, etc. (via Microsoft Graph). Requires ~/.config/myagent-msmail/ setup. Default false on create.",
                },
                "conversational": {
                    "type": "boolean",
                    "description": "Enable Conversational mode — MyAgent enforces a chatbot loop by invoking user_prompt automatically when the model ends a turn without it (default false on create). Useful for smaller models that don't reliably follow always-call-user_prompt rules.",
                },
                "provider": {
                    "type": "string",
                    "enum": ["Anthropic", "OpenAI", "Google", "xAI", "Moonshot", "Ollama"],
                    "description": "API provider (optional for update; create inherits current)",
                },
                "model": {
                    "type": "string",
                    "description": "Model name (optional for update; create inherits current)",
                },
                "temperature": {
                    "type": "number",
                    "description": "Temperature 0.0-1.0 (optional for update; create inherits current)",
                },
                "thinking_enabled": {
                    "type": "boolean",
                    "description": "Enable thinking/reasoning (optional for update; create inherits current)",
                },
                "thinking_effort": {
                    "type": "string",
                    "enum": ["low", "medium", "high", "xhigh", "max"],
                    "description": "Thinking effort level (optional for update; create inherits current). 'xhigh' needs Opus 4.7+/Fable 5 (or OpenAI gpt-5.2+); 'max' needs Anthropic Opus 4.6+/Fable 5.",
                },
                "thinking_budget": {
                    "type": "integer",
                    "description": "Thinking token budget (optional for update; create inherits current)",
                },
                "thinking_mode": {
                    "type": "string",
                    "enum": ["off", "none", "adaptive", "low", "medium", "high", "max", "xhigh"],
                    "description": (
                        "Thinking/reasoning mode (optional for update; create inherits current). "
                        "Anthropic adaptive: off/adaptive/low/medium/high/xhigh/max ('xhigh' Opus 4.7+/"
                        "Fable 5; 'max' Opus 4.6+/Fable 5). Fable 5 / Mythos 5 have ALWAYS-ON thinking — "
                        "'off' is invalid for them; use 'adaptive'. "
                        "OpenAI gpt-5.1+ reasoning: none/low/medium/high/xhigh. Lower-cased to "
                        "match the stored value."
                    ),
                },
                "blocked_tools": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Hard per-instruction tool blocklist: these tool names are stripped "
                        "from the tools offered to the model AND refused at dispatch if "
                        "called anyway — a deterministic guarantee for unattended runs "
                        "(e.g. [\"gmail_trash\", \"proton_trash\"]). On update the whole "
                        "list is replaced. Empty list clears it."
                    ),
                },
                "skill_modes": {
                    "type": "object",
                    "description": (
                        "Map of skill names to modes: 'disabled', 'enabled', or 'on_demand'. "
                        "On create, defaults to current skill modes. On update, only listed "
                        "skills are changed — omitted skills keep their current mode."
                    ),
                    "additionalProperties": {
                        "type": "string",
                        "enum": ["disabled", "enabled", "on_demand"],
                    },
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "manage_skills",
        "description": (
            "Manage the shared skills library on disk. Skills can be injected into system "
            "prompts (enabled), retrieved on demand (on_demand), or inactive (disabled). "
            "Each skill may carry a short description (what it does + when to use it), "
            "listed in the system prompt for on_demand skills as the trigger signal. "
            "Actions: list, read, create, update, delete."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "create", "update", "delete"],
                    "description": "The operation to perform",
                },
                "name": {
                    "type": "string",
                    "description": ("Skill name (required for all except list). On create it "
                                    "MUST be Agent-Skills kebab-case — lowercase letters/"
                                    "digits/hyphens, max 64 chars, e.g. 'westpac-login' "
                                    "(non-conforming creates are rejected)."),
                },
                "content": {
                    "type": "string",
                    "description": "Skill text content (required for create, optional for update)",
                },
                "description": {
                    "type": "string",
                    "description": (
                        "One or two sentences: WHAT the skill does and WHEN to use it — "
                        "shown in the system prompt for on_demand skills. Optional; on "
                        "update, an empty string clears it. Guideline: <=1024 chars."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["disabled", "enabled", "on_demand"],
                    "description": "Skill mode (default: disabled on create)",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "run_instruction",
        "description": (
            "Launch a saved agent instruction as a separate MyAgent process. "
            "By default the child runs independently (fire-and-forget) and this "
            "returns immediately. Set wait=true to BLOCK until the child agent "
            "finishes and get its final report text back as this tool's result — "
            "use that when you need the child's answer to continue your own task. "
            "PARALLEL FAN-OUT: several run_instruction calls issued in the SAME "
            "assistant turn run concurrently — even waited ones — and all their "
            "reports come back together. To work independent subtasks in "
            "parallel, issue all the spawns in one turn (each with its own "
            "extra_text and timeout) instead of one per turn; total wait is the "
            "slowest child, not the sum. Use manage_instructions(action='list') "
            "first to see available names."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Name of the saved instruction to launch",
                },
                "headless": {
                    "type": "boolean",
                    "description": "Run without a GUI window (default true). Set false to show the agent window while it works; a waited child (wait=true) still auto-closes when its run completes, so watching is safe.",
                },
                "wait": {
                    "type": "boolean",
                    "description": "Wait for the child to finish and return its final report (default false = fire-and-forget).",
                },
                "timeout_seconds": {
                    "type": "integer",
                    "description": "Max seconds to wait when wait=true (default 600). On timeout the child is terminated and an error is returned.",
                },
                "extra_text": {
                    "type": "string",
                    "description": (
                        "Optional task text appended to the child's saved instruction as an "
                        "ADDITIONAL TASK CONTEXT block, for this run only — use it to "
                        "parameterize a generic saved instruction per spawn (the specific "
                        "question, target, or data for THIS child). The saved instruction "
                        "on disk is unchanged."
                    ),
                },
            },
            "required": ["name"],
        },
    },
]

# Desktop automation tool definitions (pyautogui-based)
DESKTOP_TOOLS = [
    {
        "name": "screenshot",
        "description": "",  # patched at runtime with actual screen resolution
        "input_schema": {
            "type": "object",
            "properties": {
                "display": {
                    "type": "integer",
                    "description": "Which display to capture (0=primary, 1=secondary, ...). OMIT this parameter to capture ALL displays at once as separate images — that's the default and the recommended first step when you don't yet know which display has your target. When you DO know which display the target is on, pass display=N to capture only that one. For region screenshots (x/y/width/height), pass display=N to specify which display the region coordinates are relative to.",
                },
                "x": {"type": "integer", "description": "Left edge of region to capture (image-space coordinates from the screenshot of the specified display)"},
                "y": {"type": "integer", "description": "Top edge of region to capture (image-space coordinates from the screenshot of the specified display)"},
                "width": {"type": "integer", "description": "Width of region to capture (image-space pixels)"},
                "height": {"type": "integer", "description": "Height of region to capture (image-space pixels)"},
                "grid": {
                    "type": "boolean",
                    "description": "Overlay a coordinate grid every 100px with labels at intersections. Use this when clicking small or visually-dense targets to get pixel-accurate coordinates. Default: false.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "mouse_click",
        "description": (
            "Click the mouse at the given (x, y) position. Take a screenshot first to identify "
            "the correct coordinates. CRITICAL: pass the pixel coordinates EXACTLY as you read them "
            "from the screenshot image — do NOT scale, multiply, or convert them to a different "
            "resolution. Even if you used code_interpreter to inspect the image and noticed it has "
            "different dimensions than the physical screen, just pass the image-space coordinates. "
            "The system handles all scaling and offset translation internally to map image pixels "
            "to actual screen pixels. Supports left/right/middle button and single/double click."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate to click (in screenshot image space)"},
                "y": {"type": "integer", "description": "Y coordinate to click (in screenshot image space)"},
                "button": {
                    "type": "string", "enum": ["left", "right", "middle"],
                    "description": "Mouse button (default: left)",
                },
                "clicks": {
                    "type": "integer", "enum": [1, 2],
                    "description": "Number of clicks: 1=single, 2=double (default: 1)",
                },
                "display": {
                    "type": "integer",
                    "description": "Optional display index (0=primary, 1=secondary, ...). Use this when you took a multi-display screenshot and want to click on a target you saw in a non-primary display, without re-capturing it first. Omit to click on the most recently captured display/region.",
                },
            },
            "required": ["x", "y"],
        },
    },
    {
        "name": "type_text",
        "description": (
            "Type text at the current cursor position. Click on an input field first to focus it, "
            "then use this tool to type. Uses clipboard paste for non-ASCII characters."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "The text to type"},
                "interval": {
                    "type": "number",
                    "description": "Seconds between keystrokes (default: 0.02)",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "press_key",
        "description": (
            "Press a key or key combination. Use '+' to combine keys. "
            "Examples: 'enter', 'tab', 'escape', 'ctrl+c', 'ctrl+shift+s', 'alt+tab', "
            "'command+c', 'command+q'. Key names follow pyautogui naming. "
            "Use ctrl/alt/win on Windows, command/option on macOS."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "keys": {
                    "type": "string",
                    "description": "Key or combo to press, e.g. 'enter', 'ctrl+c', 'alt+tab'",
                }
            },
            "required": ["keys"],
        },
    },
    {
        "name": "mouse_scroll",
        "description": (
            "Scroll the mouse wheel. Positive clicks = scroll up, negative = scroll down. "
            "Optionally specify (x, y) — in screenshot image coordinates — to scroll at a specific position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "clicks": {
                    "type": "integer",
                    "description": "Scroll amount: positive=up, negative=down",
                },
                "x": {"type": "integer", "description": "X coordinate to scroll at (optional, in screenshot image space)"},
                "y": {"type": "integer", "description": "Y coordinate to scroll at (optional, in screenshot image space)"},
                "display": {
                    "type": "integer",
                    "description": "Optional display index for multi-display setups. See mouse_click for details.",
                },
            },
            "required": ["clicks"],
        },
    },
    {
        "name": "open_application",
        "description": (
            "Open an application by common name or full path. Known names: chrome, firefox, edge, "
            "safari, notepad, notepad++, calculator, terminal, finder, excel, word, vscode, "
            "spotify, discord, slack, teams. Or provide a full executable path. "
            "Use the optional 'args' parameter to pass arguments (e.g. a file path to open)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "App name (e.g. 'chrome', 'notepad++') or full path to executable",
                },
                "args": {
                    "type": "string",
                    "description": "Optional arguments to pass (e.g. a file path to open in the application)",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "find_window",
        "description": (
            "Find windows matching a title pattern. Returns window titles, positions, and sizes. "
            "Optionally activate (bring to foreground) the first matching window."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Window title or partial text to search for",
                },
                "activate": {
                    "type": "boolean",
                    "description": "If true, bring the first matching window to the foreground (default: false)",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "clipboard_read",
        "description": "Read the current text contents of the clipboard.",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "clipboard_write",
        "description": "Write text to the clipboard, replacing any current content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "The text to place on the clipboard",
                }
            },
            "required": ["text"],
        },
    },
    {
        "name": "wait_for_window",
        "description": (
            "Wait until a window with the given title appears, polling every 0.5 seconds. "
            "Returns the window info once found, or times out."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Window title or partial text to wait for",
                },
                "timeout": {
                    "type": "number",
                    "description": "Maximum seconds to wait (default: 10)",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "read_screen_text",
        "description": (
            "Read text from a region of the screen using OCR. "
            "Specify the region as x, y, width, height using coordinates from the screenshot image."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Left edge of region (screenshot image space)"},
                "y": {"type": "integer", "description": "Top edge of region (screenshot image space)"},
                "width": {"type": "integer", "description": "Width of region"},
                "height": {"type": "integer", "description": "Height of region"},
                "display": {
                    "type": "integer",
                    "description": "Optional display index for multi-display setups. See mouse_click for details.",
                },
            },
            "required": ["x", "y", "width", "height"],
        },
    },
    {
        "name": "find_image_on_screen",
        "description": (
            "Find an image on the screen by matching a reference image file. "
            "Returns the center coordinates if found. Useful for finding buttons or icons."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "image_path": {
                    "type": "string",
                    "description": "Absolute path to the reference image file (PNG, JPG, etc.)",
                },
                "confidence": {
                    "type": "number",
                    "description": "Match confidence threshold 0.0-1.0 (default: 0.8)",
                },
            },
            "required": ["image_path"],
        },
    },
    {
        "name": "mouse_drag",
        "description": (
            "Drag the mouse from one point to another. Useful for drag-and-drop, "
            "resizing windows, moving sliders, drawing, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "start_x": {"type": "integer", "description": "Starting X coordinate (screenshot image space)"},
                "start_y": {"type": "integer", "description": "Starting Y coordinate (screenshot image space)"},
                "end_x": {"type": "integer", "description": "Ending X coordinate (screenshot image space)"},
                "end_y": {"type": "integer", "description": "Ending Y coordinate (screenshot image space)"},
                "duration": {
                    "type": "number",
                    "description": "Duration of drag in seconds (default: 0.5)",
                },
                "button": {
                    "type": "string",
                    "description": "Mouse button: 'left', 'right', or 'middle' (default: 'left')",
                },
                "display": {
                    "type": "integer",
                    "description": "Optional display index for multi-display setups. See mouse_click for details.",
                },
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
        },
    },
    {
        "name": "find_element",
        "description": (
            "Locate a UI element on a captured screenshot by natural-language description, "
            "and return its image coordinates. Use this BEFORE mouse_click for any non-trivial "
            "target — it leverages Gemini's native spatial reasoning for higher accuracy than "
            "guessing coordinates by eye. Returns coordinates in the same image space mouse_click "
            "expects, ready to pass directly. Only available with the Google provider (Gemini models). "
            "CRITICAL for multi-display setups: ALWAYS pass the 'display' parameter to specify "
            "which display's screenshot to search — without it, find_element falls back to whichever "
            "display was captured most recently, which is often NOT where your target is. "
            "Requires a screenshot to have been taken in the current session."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {
                    "type": "string",
                    "description": "Natural-language description of the element to find. Be specific: 'the blue Submit button at the bottom of the form', 'the X close button in the top right corner', 'the search input field labelled Email'.",
                },
                "display": {
                    "type": "integer",
                    "description": "Which display to search (0=primary, 1=secondary, ...). Required for multi-display setups — pass the display index where the target is. When you pass display=N to find_element, also pass display=N to the resulting mouse_click.",
                },
            },
            "required": ["description"],
        },
    },
]

# Browser automation tool definitions (Playwright via CDP)
BROWSER_TOOLS = [
    {
        "name": "browser_open",
        "description": (
            "Open or connect to Google Chrome or Microsoft Edge and navigate to a URL. "
            "Uses the user's real browser profile with all cookies, logins, and extensions. "
            "If the browser isn't running, it will be launched automatically. "
            "Call this first before using any other browser tools."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to (e.g. 'https://google.com')",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_navigate",
        "description": "Navigate the current browser page to a new URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL to navigate to",
                }
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_click",
        "description": (
            "Click an element on the page. Use a CSS selector (e.g. '#submit-btn', 'a.nav-link') "
            "or provide visible text to find and click the element. Prefer selectors when possible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the element to click (e.g. '#login', 'button.submit')",
                },
                "text": {
                    "type": "string",
                    "description": "Visible text of the element to click (used if selector is not provided)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "browser_download",
        "description": (
            "Click an element that triggers a file download and save the file to a local "
            "path. REQUIRED for any in-page download (CSV/statement/PDF exports): a plain "
            "browser_click loses the file — the CDP-attached browser renames downloads to "
            "a random GUID in a temp folder and the download shows as failed unless it is "
            "captured with this tool. Identify the trigger element by CSS selector or "
            "visible text, like browser_click."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "save_path": {
                    "type": "string",
                    "description": (
                        "Where to save the file. A file path saves under that exact name "
                        "(overwriting); an existing directory keeps the server-suggested "
                        "filename inside it. Relative paths resolve against the app folder."
                    ),
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the element that starts the download",
                },
                "text": {
                    "type": "string",
                    "description": "Visible text of the element (used if selector is not provided)",
                },
                "timeout_s": {
                    "type": "integer",
                    "description": "Seconds to wait for the download to start and finish (default 60)",
                },
            },
            "required": ["save_path"],
        },
    },
    {
        "name": "browser_fill",
        "description": (
            "Fill a form field with text. This clears any existing value and types instantly "
            "(not character-by-character). Use a CSS selector to identify the input field."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the input field (e.g. 'input[name=q]', '#email')",
                },
                "value": {
                    "type": "string",
                    "description": "The text to fill into the field",
                },
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "browser_get_text",
        "description": (
            "Get the text content of the page or a specific element. "
            "Use this to read page content without taking a screenshot. "
            "If no selector is given, returns the full page text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector of the element to read (optional — omit for full page text)",
                },
            },
            "required": [],
        },
    },
    {
        "name": "browser_run_js",
        "description": (
            "Execute JavaScript code on the current page and return the result. "
            "Use for advanced interactions, extracting data, or manipulating the DOM. "
            "The code runs in the page context. Use 'return' to get a value back."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "JavaScript code to execute (e.g. \"return document.title\")",
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "browser_screenshot",
        "description": (
            "Take a screenshot of the current browser page. Returns an image. "
            "Use this to see what the page looks like visually."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "browser_close",
        "description": (
            "Disconnect from the browser. Edge stays open — only the automation connection is closed. "
            "Use this when you're done with browser tasks."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "browser_wait_for",
        "description": (
            "Wait for an element matching a CSS selector to appear on the page. "
            "Returns the element's text content once found, or times out."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to wait for (e.g. '#result', '.loaded')",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Maximum milliseconds to wait (default: 10000)",
                },
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_select",
        "description": (
            "Select an option from a <select> dropdown element. "
            "Specify the option by value attribute or visible label text."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the <select> element",
                },
                "value": {
                    "type": "string",
                    "description": "Option value attribute to select",
                },
                "label": {
                    "type": "string",
                    "description": "Visible text of the option to select",
                },
            },
            "required": ["selector"],
        },
    },
    {
        "name": "browser_get_elements",
        "description": (
            "Get information about elements matching a CSS selector. "
            "Returns tag name, text content, key attributes, and visibility for each match."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector to query (e.g. 'a', 'button', '.item')",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of elements to return (default: 10)",
                },
            },
            "required": ["selector"],
        },
    },
]

# ── Command safety guardrails ───────��──────────────────────────────────────
# Tier 1: Hard-blocked patterns (rejected outright, never run)
# Tier 2: Confirmation-required patterns (user must approve via dialog)
# Patterns are platform-specific — Windows sees PowerShell patterns,
# macOS sees bash/Unix patterns.

if IS_WINDOWS:
    COMMAND_BLOCKED = [
        r"\bFormat-Volume\b",
        r"\bFormat-Disk\b",
        r"\bClear-Disk\b",
        r"\bInitialize-Disk\b",
        r"\bStop-Computer\b",
        r"\bRestart-Computer\b",
        r"\bSet-ExecutionPolicy\b",
        r"\breg\s+delete\b",
        r"\bRemove-ItemProperty\b.*\\\\HKLM",
        r"\bRemove-ItemProperty\b.*\\\\HKCU",
        r"\bRemove-Item\b.*\\\\HKLM",
        r"\bRemove-Item\b.*\\\\HKCU",
        r"\bbcdedit\b",
        r"\bdiskpart\b",
        r"\bnet\s+user\b.*(/add|/delete)",
        r"\bDisable-LocalUser\b",
        r"\bRemove-LocalUser\b",
        r"\bClear-EventLog\b",
        r"\bwmic\b.*delete",
    ]
    COMMAND_CONFIRM = [
        r"\bRemove-Item\b",
        r"\bdel\b",
        r"\brmdir\b",
        r"\brm\b\s",
        r"\brd\b\s",
        r"\bClear-Content\b",
        r"\bClear-RecycleBin\b",
        r"\bStop-Process\b",
        r"\bkill\b\s",
        r"\btaskkill\b",
        r"\bStop-Service\b",
        r"\bRemove-Service\b",
        r"\bUninstall-Package\b",
        r"\bMove-Item\b",
        r"\bRename-Item\b",
        r"\bSet-Content\b",
        r"\bOut-File\b",
        r"\bInvoke-Expression\b",
        r"\biex\b\s",
        r"\bInvoke-WebRequest\b.*-OutFile",
        r"\bStart-Process\b",
        r"\bNew-Service\b",
        # Bare switches: a leading \b never matches before '-' when it's preceded by a
        # space (both non-word chars = no boundary), so anchor with (?<!\S) instead —
        # the flag must sit at a line start or after whitespace.
        r"(?<!\S)-Recurse\b",
        r"(?<!\S)-Force\b",
    ]
else:
    COMMAND_BLOCKED = [
        r"\bsudo\s+rm\s+-rf\s+/\s*$",
        r"\bmkfs\b",
        r"\bdd\b.*\bof=/dev/",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bdiskutil\s+eraseDisk\b",
        r"\bdiskutil\s+partitionDisk\b",
        r"\bnewfs\b",
        r"\bcsrutil\s+disable\b",
        r"\bdscl\b.*-delete",
        r"\bsysadminctl\b.*-deleteUser",
    ]
    COMMAND_CONFIRM = [
        r"\brm\b",
        r"\bmv\b",
        r"\bkill\b",
        r"\bkillall\b",
        r"\bchmod\b",
        r"\bchown\b",
        r"\bsudo\b",
        r"\bcurl\b.*-o",
        r"\bwget\b",
        r"\blaunchctl\b",
        r"\bdefaults\s+write\b",
        r"\bdefaults\s+delete\b",
        r"\bbrew\s+(install|uninstall|remove)\b",
        r"\bpip\s+install\b",
        r"\bpip\s+uninstall\b",
        r"\bnpm\s+(install|uninstall)\b",
        r"\bopen\s+-a\b",
        r"\bdiskutil\b",
        r"\bnetworksetup\b",
        r"\bpmset\b",
    ]

# ── Constants ─────────────��─────────────────────────────────────────────────

FALLBACK_MODELS = [
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-fable-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]
DEFAULT_MODEL = FALLBACK_MODELS[0]
MAX_TOKENS = 8192
MAX_TOKENS_THINKING = 32768
# Models with lower max output token limits than MAX_TOKENS. Empty since the
# 2026 retirements: the Claude 3 generation (the only 4K-cap models) is fully
# retired (claude-3-sonnet 2025-07, claude-3-opus 2026-01, claude-3-haiku
# 2026-04-19). Kept as a dict because the streaming paths .get() it per call —
# repopulate if a low-cap model ever ships again.
MODEL_MAX_OUTPUT_TOKENS = {}
# Deprecated / soon-to-be-retired Anthropic id prefixes hidden from the model
# picker (2026-07 audit; same filter SelfBot has carried). _fetch_available_models
# drops live models.list() entries matching these, so new models appear
# automatically and only retiring ones fall out. Opus/Sonnet/Haiku 4.5 stay —
# still active. The dated 4.0 ids are claude-(opus|sonnet)-4-20250514, matched
# by the "-4-20" prefix (a real "-4-20" minor is implausible — minors run
# 5, 6, 7, 8…). A pinned instruction can still RUN a hidden id until Anthropic
# actually shuts it down; this only removes them from the picker.
ANTHROPIC_DEPRECATED_MODEL_PREFIXES = (
    "claude-opus-4-1",       # Opus 4.1 — retired 2026-08-05 (gone from models.list())
    "claude-opus-4-0",       # Opus 4.0 alias — retired 2026-06-15
    "claude-opus-4-20",      # Opus 4.0 dated id (claude-opus-4-20250514)
    "claude-sonnet-4-0",     # Sonnet 4.0 alias — retired 2026-06-15
    "claude-sonnet-4-20",    # Sonnet 4.0 dated id (claude-sonnet-4-20250514)
    "claude-3",              # every Claude 3.x — fully retired (last 2026-04-19)
    "claude-2",              # Claude 2.x — retired
)
ADAPTIVE_THINKING_MODELS = {"claude-fable-5", "claude-mythos-5",
                            "claude-opus-5",
                            "claude-opus-4-8", "claude-opus-4-7", "claude-opus-4-6",
                            "claude-sonnet-5", "claude-sonnet-4-6"}
# Claude 5 Mythos-class models (Fable 5 / Mythos 5): thinking is ALWAYS ON.
# The API rejects thinking={"type": "disabled"} and budget_tokens with HTTP 400 —
# only omitting the param or {"type": "adaptive"} is accepted, and sampling
# params (temperature/top_p/top_k) are rejected unconditionally. The UI drops
# the "Off" mode for these and the streaming path always takes the thinking branch.
ALWAYS_ON_THINKING_PREFIXES = ("claude-fable-", "claude-mythos-")
# Budget-based ("manual") extended thinking. Claude 3.5 Sonnet is intentionally
# excluded: extended thinking arrived with Claude 3.7 / Claude 4, so sending a
# thinking block to a 3.5 model returns HTTP 400. (Opus 4 / 4.1 / Sonnet 4 also
# support extended thinking but are currently omitted — add their prefixes here
# to expose the manual thinking UI for them.)
MANUAL_THINKING_PREFIXES = ("claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5")
EFFORT_LEVELS = ["low", "medium", "high", "max"]
# Static superset used as the editor combobox placeholder; _anthropic_mode_values()
# builds the real per-model list (drops "Off" for always-on models, gates Xhigh/Max).
ADAPTIVE_MODE_VALUES = ["Off", "Adaptive", "Low", "Medium", "High", "Xhigh", "Max"]
BUDGET_PRESETS = {"1K": 1024, "4K": 4096, "8K": 8192, "16K": 16384, "32K": 32768}
# GPT-5.6 (2026-07-09) ships as three durable capability tiers: sol
# (flagship, $4/$20), terra (balanced everyday, $2/$12), luna (fast/cheap,
# $0.20/$1.20) — rates per OPENAI_PRICING, re-trued 2026-08-25. Terra
# default. All three accept reasoning.effort none..xhigh AND "max" (probed
# live 2026-08-25; gpt-5.5 / 5.4 reject "max") — see _has_reasoning_max.
OPENAI_FALLBACK_MODELS = ["gpt-5.6-terra", "gpt-5.6-sol", "gpt-5.6-luna",
                          "gpt-5.5", "gpt-5.4"]
OPENAI_DEFAULT_MODEL = OPENAI_FALLBACK_MODELS[0]
# o-series prefixes stay HERE (params wiring) even though the picker no longer
# lists them — a saved instruction pinning o3 etc. keeps correct reasoning
# params until the actual API shutdowns (see OPENAI_RESPONSES_PREFIXES).
OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")
# Model families that support the Responses API AND are still worth offering.
# 2026-07 audit: gpt-4.5 left the API 2025-07; gpt-4o retired 2026-02-16; the
# entire o-series is scheduled out (o1 / o1-pro / o3-mini / o4-mini shut down
# 2026-10-23, o3 / o3-pro 2026-12-11 — all replaced by gpt-5.6) so it is
# dropped from the picker. gpt-3.5 / gpt-4 base / gpt-4-turbo never supported
# the Responses API and retire 2026-10-23 anyway.
OPENAI_RESPONSES_PREFIXES = ("gpt-4.1", "gpt-5")
# Scheduled-retirement ids that still pass the family prefixes above —
# _fetch_openai_models drops them from the picker even though the API still
# serves some of them (policy: retiring models are removed ahead of their
# shutdown date). The GPT-5.0 base/mini/nano tiers retire 2026-12-11 →
# gpt-5.6; the -chat / -codex ids already retired 2026-07-23. Two ids must be
# EXACT matches (OPENAI_DEPRECATED_MODEL_IDS) because as prefixes they would
# also kill surviving longer ids: bare "gpt-5" (would match everything) and
# bare "gpt-5.1-codex" (would match the still-served -codex-max / -codex-mini).
# gpt-5-pro and gpt-4.1 have no announced retirement and stay.
OPENAI_DEPRECATED_MODEL_PREFIXES = ("gpt-5-mini", "gpt-5-nano", "gpt-5-chat",
                                    "gpt-5-codex", "gpt-5-2025", "gpt-5.1-chat")
OPENAI_DEPRECATED_MODEL_IDS = {"gpt-5", "gpt-5.1-codex"}
# Gemini 2.5 is scheduled for shutdown (no earlier than 2026-10-16; Google
# will give 6 months' notice once Gemini 3 is GA) and is replaced by the 3.x
# tiers — per the retiring-models-removed-early policy it is dropped from the
# picker (_fetch_gemini_models) and unpriced, and the fallback list carries
# only current 3.x models. The 2.5 thinking_budget PARAM wiring stays
# (GEMINI_THINKING_PREFIXES + _gemini_uses_thinking_level) so a pinned
# instruction that still names a 2.5 id keeps working until Google pulls it.
# gemini-3.7-flash (stable, 2026-08) leads: the current Flash tier — what the
# floating gemini-flash-latest alias resolves to (verified live 2026-08-25) —
# and cheaper than 3.5-flash at either its promo or sticker rate; it accepts
# thinking_level low/medium/high like the rest of 3.x (probed live the same
# day). 3.5-flash stays listed because instructions were pinned to it while
# it was the default; gemini-flash-lite-latest now resolves to 3.5-flash-lite.
GEMINI_FALLBACK_MODELS = ["gemini-3.7-flash", "gemini-3.1-pro-preview",
                          "gemini-3.5-flash", "gemini-3.5-flash-lite"]
GEMINI_DEFAULT_MODEL = GEMINI_FALLBACK_MODELS[0]
# Models that support thinking via ThinkingConfig. EVERY current Gemini text
# tier is thinking-capable, including Flash-Lite (2.5-flash-lite ships thinking
# off by default but accepts a budget; 3.1-flash-lite accepts thinking_level —
# both verified live 2026-07), so there is no "lite" carve-out. The
# version-pinned prefixes cover dated/preview IDs like gemini-3-pro-preview and
# gemini-3.5-flash. The floating "-latest" aliases that models.list() returns
# carry the version AFTER the tier word (gemini-pro-latest, not gemini-3.1-pro),
# so no version-pinned prefix matches them — all three are listed explicitly so
# their thinking UI isn't hidden. Whether the config ships as the legacy
# thinking_budget (2.5) or thinking_level (3+) is decided per-model by
# _gemini_uses_thinking_level.
GEMINI_THINKING_PREFIXES = ("gemini-2.5", "gemini-3", "gemini-pro-latest",
                            "gemini-flash-latest", "gemini-flash-lite-latest")
# models.list() entries that can't serve MyAgent's agentic loop (text chat +
# custom function declarations on generateContent), dropped by substring in
# _fetch_gemini_models. Groups: wrong output modality (TTS, image generation —
# the gemini-*-image / Nano Banana lines, lyria music, veo video, omni AV,
# bidi-only live/audio); different API surface (deep-research and antigravity
# are managed agents on the Interactions API; computer-use needs its own
# predefined tool protocol); no function calling (gemma open models); and
# niche/legacy (robotics-er spatial models, embedding, imagen, aqa).
GEMINI_NON_AGENTIC_SUBSTRINGS = (
    "embedding", "imagen", "aqa", "bisheng", "text-", "-tts", "-image",
    "nano-banana", "lyria", "veo", "-live", "-audio", "omni", "gemma",
    "robotics", "computer-use", "deep-research", "antigravity",
)
# Ollama (local inference) — no API key needed; availability probed at startup.
# Order is the curated preference: [0] is OLLAMA_DEFAULT_MODEL (initial model,
# instruction-create default, and the drift-fallback target when a saved model
# is missing from the live list); the full list is served only when the model
# fetch fails. muse-glimmer first per the 2026-08-14 live-fire audit (only
# all-four-capability model, fastest, reliable tool parse); qwen3 is the
# text-only second. Ollama is used on macOS only, so defaulting to the
# Apple-Silicon MLX build is safe.
OLLAMA_FALLBACK_MODELS = ["muse-glimmer:30b-mlx", "qwen3:32b-q4_K_M"]
OLLAMA_DEFAULT_MODEL = OLLAMA_FALLBACK_MODELS[0]
OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434"
# Pragmatic ceiling on num_ctx for Ollama calls. Without this we'd pass the
# model's full advertised context (128K for vision variants, 40K for Qwen3),
# which forces Ollama to pre-allocate huge KV cache blocks that dominate
# memory on unified-memory Macs — causing disk swap and 3-5x slowdowns.
# 32K fits comfortably on a 32 GB Mac mini alongside a 32B Q4 model and
# leaves plenty of headroom for long agent conversations. Override via
# the OLLAMA_NUM_CTX_CAP env var if you have more RAM.
OLLAMA_NUM_CTX_CAP = int(os.environ.get("OLLAMA_NUM_CTX_CAP", "32768"))
# How long the Ollama server keeps the model loaded after each call, passed
# per-request (e.g. "24h", "30m", "0" to unload immediately). The server
# default is only 5m, so every fresh agent run pays the model load AND the
# full multi-minute prompt prefill again; a warm model also reuses the
# server's KV prefix cache, so a repeat run of the same instruction skips
# most of the system-prompt/tool-catalog prefill. Opt-in: unset sends
# nothing (server default applies) — a pinned 21 GB model is a real RAM
# trade-off on 32 GB machines. Reclaim manually with `ollama stop <model>`.
OLLAMA_KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "")
# Models that accept `think: true` on /api/chat and emit reasoning in the
# separate `thinking` stream field. Ollama's `think` flag is boolean-only
# today — all effort levels (low/medium/high) map to `think: true`.
OLLAMA_THINKING_PREFIXES = ("qwen3", "deepseek-r1", "gpt-oss", "muse-glimmer")
# Vision-capable local models. When a non-vision Ollama model is paired with
# the Desktop/Browser tool checkboxes, the weak-combo warning surfaces.
# Ollama's library uses no dash between the base name and "vl" (e.g.
# "qwen2.5vl:32b"), but some external references use a dash — both covered.
# "gemma3" covers the family incl. the -tools graft; caveat: upstream
# gemma3:1b is text-only, so its warning would be wrongly suppressed if
# that variant were ever pulled.
OLLAMA_VISION_PREFIXES = ("qwen2.5vl", "qwen2.5-vl", "qwen3vl", "qwen3-vl",
                          "llava", "llama3.2-vision", "bakllava",
                          "moondream", "minicpm-v", "granite3.2-vision",
                          "muse-glimmer", "gemma3")
# ── xAI (Grok) ────────────────────────────────────────────────────────────────
# Reached with the openai SDK pointed at XAI_DEFAULT_BASE_URL (the API is
# OpenAI-compatible; the primary surface is the Responses endpoint, same
# input/tool shapes as OpenAI's — so xAI reuses _messages_to_responses /
# _tools_to_responses). Requires XAI_API_KEY. Catalog, capabilities and
# reasoning matrix verified against docs.x.ai 2026-07; grok-4.6 (released
# 2026-08-12) added after a live /v1/models + reasoning probe on 2026-08-18.
XAI_DEFAULT_BASE_URL = "https://api.x.ai/v1"
XAI_FALLBACK_MODELS = ["grok-4.3", "grok-4.5", "grok-4.6",
                       "grok-4.20-0309-reasoning",
                       "grok-4.20-0309-non-reasoning",
                       "grok-4.20-multi-agent-0309", "grok-build-0.1"]
XAI_DEFAULT_MODEL = XAI_FALLBACK_MODELS[0]
# models.list() entries that can't serve the agentic loop (image/video
# generation, embeddings, TTS) — dropped by substring in _fetch_xai_models.
XAI_NON_AGENTIC_SUBSTRINGS = ("-image", "imagine", "embed", "-video", "-tts")
# reasoning_effort support by model family (longest prefix wins; families
# absent here have no client-side knob and are sent no reasoning param).
# grok-4.3: none/low/medium/high — low is the API default, "none" disables
# reasoning. grok-4.5: low/medium/high/xhigh — always-reasoning, "none" is
# HTTP 400 (verified live 2026-07-17; aliases grok-4.5-latest +
# grok-build-latest). grok-4.6 (the 2026-08 flagship, 500K context, vision,
# no aliases of its own): the same low..xhigh always-reasoning matrix —
# "none" is HTTP 400 "This model does not support `reasoning_effort` value
# `none`", every other value accepted, verified live 2026-08-18.
# grok-4.20-multi-agent: the knob sets agent collaboration count rather than
# depth — no "none". The pinned grok-4.20-*-reasoning / -non-reasoning
# variants have no knob at all, and neither do the aliases (bare grok-4.20 →
# the pinned reasoning variant; grok-latest → whatever xAI currently calls
# latest, grok-4.6 since 2026-08, running its server-side default effort —
# a floating alias never gets a knob, so a re-point can't strand a saved
# effort value).
XAI_REASONING_EFFORT = {
    "grok-4.20-multi-agent": ["low", "medium", "high", "xhigh"],
    "grok-4.3": ["none", "low", "medium", "high"],
    "grok-4.5": ["low", "medium", "high", "xhigh"],
    "grok-4.6": ["low", "medium", "high", "xhigh"],
}
# Text-only Grok families (no image input) — the weak-desktop-combo warning
# fires for these. Every current chat tier (grok-4.3 / grok-4.5 / grok-4.6 /
# grok-4.20) is vision-capable; grok-build-0.x (docs list it text-only) and its
# grok-code-fast alias are not. The prefix is "grok-build-0", NOT
# "grok-build": grok-build-latest re-aliased to grok-4.5 (vision) in the
# 2026-07 catalog, so the shorter prefix would false-flag it.
XAI_NON_VISION_PREFIXES = ("grok-build-0", "grok-code")
# ── Moonshot AI (Kimi models) ─────────────────────────────────────────────────
# Provider label in the UI/state: "Moonshot" (the company, matching the
# Anthropic/OpenAI/xAI convention) — the constants and mixin keep the KIMI_*/
# _kimi_* names because the MODELS are branded kimi-*. A legacy saved provider
# value "Kimi" (2026-07-25 initial wiring) normalizes to "Moonshot" in
# _restore_model_params.
# Reached with the openai SDK pointed at KIMI_DEFAULT_BASE_URL. Kimi's API is
# OpenAI-compatible but Chat-Completions-ONLY (no Responses endpoint), so the
# provider does NOT reuse _messages_to_responses / _tools_to_responses — it has
# its own translators in myagent/kimi_mixin.py. Requires MOONSHOT_API_KEY
# (KIMI_API_KEY also accepted). Catalog, parameters, and the reasoning_content
# round-trip contract verified against platform.kimi.ai docs 2026-07-25.
KIMI_DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
# Matches the LIVE /v1/models catalog 1:1 (verified 2026-07-25, re-verified
# 2026-08-25 with a real key): kimi-k2.5 is documented but NOT served to this
# account, and the models page now gives it a full platform sunset on
# 2026-08-31 — so it is omitted here and unpriced (the retiring-models-
# unpriced policy); its thinking-toggle / no-round-trip PARAM wiring below
# stays so a pinned instruction keeps correct params until the shutdown.
KIMI_FALLBACK_MODELS = ["kimi-k2.6", "kimi-k3",
                        "kimi-k2.7-code", "kimi-k2.7-code-highspeed"]
KIMI_DEFAULT_MODEL = KIMI_FALLBACK_MODELS[0]
# reasoning_effort support by family (longest prefix wins) — kimi-k3 only.
# NOTE the sparse ladder: low/high/max, NO medium (the API default is max);
# _kimi_reasoning_effort() coerces stale saved efforts from other providers.
# k2.6/k2.5 use the thinking on/off checkbox instead; k2.7-code has no knob.
KIMI_REASONING_EFFORT = {
    "kimi-k3": ["low", "high", "max"],
}
# Models whose thinking can be toggled via {"thinking": {"type": "enabled" |
# "disabled"}} (thinking is ON by default server-side for both).
KIMI_THINKING_TOGGLE_PREFIXES = ("kimi-k2.6", "kimi-k2.5")
# Models that ALWAYS think — no thinking param accepted (k2.7-code errors on
# any value but its baked-in one; k3 uses reasoning_effort instead).
KIMI_ALWAYS_THINKING_PREFIXES = ("kimi-k3", "kimi-k2.7-code")
# Models that do NOT support Preserved Thinking: reasoning_content must NOT be
# sent back on assistant messages (kimi-k2.5 is documented as unsupported).
# Every other kimi model REQUIRES the round-trip during tool-call loops —
# see _messages_to_kimi. Future unknown models default to round-tripping
# (the docs' direction of travel), backstopped by the 400 ladder.
KIMI_NO_REASONING_ROUNDTRIP_PREFIXES = ("kimi-k2.5",)
# Text-only Kimi families (no image input) — the weak-desktop-combo warning
# fires for these. k3 / k2.6 / k2.5 are all vision-capable; the k2.7-code
# coding line (incl. -highspeed) is text-only per the models doc.
KIMI_NON_VISION_PREFIXES = ("kimi-k2.7-code",)
PARALLEL_SAFE_TOOLS = {"web_search", "fetch_webpage", "csv_search", "get_skill", "read_document",
                       "read_file", "glob_files", "grep_files",
                       # run_instruction: each spawn is an independent child PROCESS, and a
                       # waited call blocks only its own executor thread — so several
                       # run_instruction calls in ONE assistant turn become concurrent
                       # waited children (parallel fan-out). The shared-state prologue is
                       # serialized by skills_mixin._SPAWN_LOCK, and simultaneous children
                       # can't collide on an instance slot (O_EXCL claim in state_mixin).
                       # manage_instructions/manage_skills stay sequential.
                       "run_instruction"}

# ── Excel live-workbook tools ────────────────────────────────────────────────
# Native tools that drive the running Excel application via xlwings (COM on
# Windows, AppleScript on macOS) — NOT file-level xlsx editing: changes appear
# live in the open workbook, formulas recalculate, and existing VBA macros can
# run. Attaches to the user's already-open Excel instance when there is one.
# Conditionally included in _get_tools() only when self.excel_enabled.get()
# is True AND _HAS_EXCEL is True. Dispatch is the namespaced excel_* pattern
# (myagent/excel_mixin.py).

# excel_sheet's action set — single source of truth for the schema enum below
# AND the mixin's guard/error message (excel_mixin imports it as
# SHEET_ACTIONS), so a new action can't reach one list and miss the other.
EXCEL_SHEET_ACTIONS = ("list", "add", "rename", "delete", "activate", "clear")

EXCEL_TOOLS = [
    {
        "name": "excel_open",
        "description": (
            "Connect to Excel and report what is open, optionally opening or creating a "
            "workbook first. Attaches to the user's running Excel instance when there is "
            "one (so you can work inside a workbook the user already has open), otherwise "
            "launches Excel visibly. Call this FIRST before other excel_* tools. Returns "
            "the open workbooks, their sheets, and the active sheet's used range."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Workbook file to open (.xlsx/.xlsm/.csv). Omit to just attach "
                        "and look around. If that file is already open, attaches to it."
                    ),
                },
                "create": {
                    "type": "boolean",
                    "description": "If true and 'path' does not exist, create a new workbook and save it there.",
                },
                "password": {
                    "type": "string",
                    "description": (
                        "Password for an open-protected workbook (only used when "
                        "opening from disk). Attaching to an already-open workbook "
                        "never needs it — if the user opened the file themselves, "
                        "omit this."
                    ),
                },
                "write_res_password": {
                    "type": "string",
                    "description": (
                        "Password for WRITE ACCESS to a write-reserved workbook — a "
                        "second, separate password from 'password'. Without it Excel "
                        "stops on a 'reserved by ... enter password for write access, "
                        "or open read only' dialog that nothing can answer in an "
                        "unattended run. Supply it whenever the workbook is "
                        "write-reserved and the run needs to make changes."
                    ),
                },
                "ignore_read_only_recommended": {
                    "type": "boolean",
                    "description": (
                        "Set true to mute the 'author would like you to open this "
                        "read-only' prompt, which otherwise blocks an unattended open. "
                        "Unrelated to 'write_res_password' — that one is a real "
                        "password, this is only a recommendation flag."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "excel_read",
        "description": (
            "Read a cell range as a table labeled with real row numbers and column "
            "letters. Values are the CURRENT calculated results; set formulas=true to "
            "see formula text instead. Defaults to the used range of the active sheet "
            "of the active workbook."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workbook": {
                    "type": "string",
                    "description": "Workbook name (e.g. 'Budget.xlsx'). Default: the active workbook.",
                },
                "sheet": {
                    "type": "string",
                    "description": "Sheet name. Default: the active sheet.",
                },
                "range": {
                    "type": "string",
                    "description": "A1-style range like 'A1:D20', or a named range. Default: the sheet's used range.",
                },
                "formulas": {
                    "type": "boolean",
                    "description": "Return formula text (e.g. '=SUM(B2:B9)') instead of calculated values.",
                },
                "max_cells": {
                    "type": "number",
                    "description": "Cap on cells returned (default 4000). Larger ranges are truncated with a notice.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "excel_write",
        "description": (
            "Write values or formulas into cells, starting at start_cell and filling "
            "right/down from the 2D 'values' array. Each cell is a string EXACTLY as you "
            "would type it into Excel: '42' becomes a number, 'Revenue' text, "
            "'2026-08-01' a date (use ISO dates), '=SUM(B2:B9)' a live formula. Empty "
            "string = empty cell (short rows are padded with empty cells, clearing "
            "them). The recalculated result of the written range is returned when small."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workbook": {
                    "type": "string",
                    "description": "Workbook name. Default: the active workbook.",
                },
                "sheet": {
                    "type": "string",
                    "description": "Sheet name. Default: the active sheet.",
                },
                "start_cell": {
                    "type": "string",
                    "description": "Top-left target cell, e.g. 'B2'.",
                },
                "values": {
                    "type": "array",
                    "items": {"type": "array", "items": {"type": "string"}},
                    "description": (
                        "2D array of rows of cell strings. A single row is "
                        "[[\"a\", \"b\", \"c\"]]; a single column is [[\"a\"], [\"b\"], [\"c\"]]."
                    ),
                },
            },
            "required": ["start_cell", "values"],
        },
    },
    {
        "name": "excel_format",
        "description": (
            "Apply formatting to a range: bold/italic, font size/color, fill color, "
            "number format, column width, autofit. Only the provided properties change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workbook": {
                    "type": "string",
                    "description": "Workbook name. Default: the active workbook.",
                },
                "sheet": {
                    "type": "string",
                    "description": "Sheet name. Default: the active sheet.",
                },
                "range": {
                    "type": "string",
                    "description": "A1-style range, e.g. 'A1:D1', or whole columns like 'C:C'.",
                },
                "bold": {"type": "boolean", "description": "Bold on/off."},
                "italic": {"type": "boolean", "description": "Italic on/off."},
                "font_size": {"type": "number", "description": "Font size in points."},
                "font_color": {"type": "string", "description": "Font color as hex '#RRGGBB'."},
                "fill_color": {
                    "type": "string",
                    "description": "Cell background as hex '#RRGGBB', or 'none' to clear the fill.",
                },
                "number_format": {
                    "type": "string",
                    "description": "Excel format code, e.g. '0.00', '$#,##0.00', 'dd/mm/yyyy', '0%'.",
                },
                "column_width": {"type": "number", "description": "Column width in Excel character units."},
                "autofit": {"type": "boolean", "description": "Auto-size the range's columns and rows to their content."},
            },
            "required": ["range"],
        },
    },
    {
        "name": "excel_sheet",
        "description": (
            "Manage worksheets: list (names + used-range sizes), add, rename, delete, "
            "activate, or clear one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(EXCEL_SHEET_ACTIONS),
                    "description": "The operation to perform.",
                },
                "workbook": {
                    "type": "string",
                    "description": "Workbook name. Default: the active workbook.",
                },
                "name": {
                    "type": "string",
                    "description": "Sheet name (required for every action except list).",
                },
                "new_name": {
                    "type": "string",
                    "description": "New sheet name (rename only).",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "excel_find",
        "description": (
            "Search cells for text (case-insensitive substring) or a number (exact "
            "match). Searches every sheet of the workbook unless 'sheet' is given. "
            "Returns matching cell addresses with their values."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Text or number to search for.",
                },
                "workbook": {
                    "type": "string",
                    "description": "Workbook name. Default: the active workbook.",
                },
                "sheet": {
                    "type": "string",
                    "description": "Limit the search to one sheet.",
                },
                "max_results": {
                    "type": "number",
                    "description": "Stop after this many matches (default 50).",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "excel_run_macro",
        "description": (
            "Run an existing VBA macro from a workbook (e.g. 'RefreshAll' or "
            "'Module1.UpdateReport'). Macros must already exist in the workbook — this "
            "cannot create VBA code."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "macro": {
                    "type": "string",
                    "description": "Macro name, optionally module-qualified.",
                },
                "workbook": {
                    "type": "string",
                    "description": "Workbook containing the macro. Default: the active workbook.",
                },
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Positional arguments passed to the macro.",
                },
            },
            "required": ["macro"],
        },
    },
    {
        "name": "excel_save",
        "description": "Save a workbook (or Save As when 'path' is given).",
        "input_schema": {
            "type": "object",
            "properties": {
                "workbook": {
                    "type": "string",
                    "description": "Workbook name. Default: the active workbook.",
                },
                "path": {
                    "type": "string",
                    "description": "Save As target path. Required for a workbook that has never been saved.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "excel_close",
        "description": (
            "Close a workbook (saving first by default). A workbook that has never been "
            "saved to disk is discarded unless you excel_save it with a path first. "
            "quit_app=true additionally quits Excel, but ONLY if no other workbooks "
            "remain open — the user's own open workbooks are never closed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "workbook": {
                    "type": "string",
                    "description": "Workbook to close. Default: the active workbook.",
                },
                "save": {
                    "type": "boolean",
                    "description": "Save before closing (default true).",
                },
                "quit_app": {
                    "type": "boolean",
                    "description": "Also quit Excel afterwards — only honored when no other workbooks remain open.",
                },
            },
            "required": [],
        },
    },
]

# ── MCP (Model Context Protocol) ─────────────────────────────────────────────
# MCP_TOOLS is populated at runtime by MCPMixin._refresh_mcp_tools() once the
# configured MCP servers have been connected. Tool names are namespaced with
# the server name (e.g. "filesystem__read_file") so dispatch can route to the
# right server in _execute_tool. Empty by default — appended to _get_tools()
# output only when self.mcp_enabled.get() is True AND _HAS_MCP is True.
MCP_TOOLS = []

# Per-user MCP server configuration. JSON shape mirrors Claude Desktop / Cursor:
#   {"servers": {"<name>": {"command": "<bin>", "args": [...], "env": {...}}, ...}}
# Allows existing community MCP servers (filesystem, github, slack, etc.) to
# drop in by config alone, no Python changes required.
MCP_SERVERS_PATH = "mcp_servers.json"

# Tool-name separator used to namespace MCP tools. The model sees
# "<server>__<tool>" (double underscore) which is allowed by all four
# providers' tool name regexes and unambiguously splittable.
MCP_NAME_SEP = "__"

# ── Google (Gmail) native tools ──────────────────────────────────────────────
# Native MyAgent tools that wrap the Gmail API directly via google-api-python-client
# (not MCP — see myagent/gmail_mixin.py for the rationale). The `account` parameter
# on every tool is a placeholder enum here; `_get_tools()` patches in the real
# enum at runtime from `~/.config/myagent-google/accounts.json` so the model only
# ever sees actually-configured accounts. Conditionally included in _get_tools()
# only when self.google_enabled.get() is True AND _HAS_GOOGLE is True.
GOOGLE_TOOLS = [
    {
        "name": "gmail_search",
        "description": (
            "Search Gmail messages using Gmail's standard query syntax. Examples: "
            "'from:alice@example.com is:unread', 'subject:invoice newer_than:7d', "
            "'has:attachment larger:1M'. Returns id, threadId, snippet, subject, "
            "from, to, date for each match. Use gmail_read on a specific id to get "
            "the full body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Gmail account to search"},
                "q": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "description": "Maximum results (default 25, max 500)"},
            },
            "required": ["account", "q"],
        },
    },
    {
        "name": "gmail_read",
        "description": (
            "Fetch the full content of a single message by ID, including headers, "
            "body, snippet, labelIds, AND an attachments array (always included — "
            "small payload). Use the format parameter to control body "
            "representation: 'text' (default, plain-text body if present, "
            "otherwise a structural HTML-to-text conversion that drops "
            "<script>/<style> CONTENT, adds newlines at block-level tags like "
            "<p>/<br>/<div>/<h1-6>/<li>/<tr>, and decodes HTML entities like "
            "&amp;/&nbsp; — much cleaner than naive tag stripping on marketing "
            "emails), 'html' (raw HTML only — empty if message is text-only), "
            "or 'both' (returns body AND body_html as separate fields). Bodies "
            "are truncated at 50,000 chars with body_truncated / "
            "body_html_truncated flags. Each attachment entry has filename, "
            "mime_type, size, attachment_id, part_id, inline — use attachment_id "
            "with gmail_get_attachment to download the bytes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Gmail account"},
                "message_id": {"type": "string", "description": "Gmail message ID (from gmail_search results)"},
                "format": {
                    "type": "string", "enum": ["text", "html", "both"],
                    "description": "Body representation to return (default 'text')",
                },
            },
            "required": ["account", "message_id"],
        },
    },
    {
        "name": "gmail_get_attachment",
        "description": (
            "Download a single attachment from a Gmail message and save it to "
            "a local file path. First call gmail_read on the message to get "
            "the attachments[] array; pick the entry you want and pass its "
            "attachment_id here along with message_id. Non-destructive — "
            "creates a local file, doesn't modify Gmail state. Refuses to "
            "overwrite an existing file unless overwrite=true; on refusal "
            "returns an error string so the agent can choose a different path. "
            "For inline attachments (inline=true in the attachments[] list), "
            "the bytes are already in the message body; this tool doesn't "
            "apply — re-fetch via gmail_read with format='both' instead."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_id": {"type": "string", "description": "Message ID containing the attachment"},
                "attachment_id": {"type": "string", "description": "Attachment ID from gmail_read's attachments[] field"},
                "save_to": {"type": "string", "description": "Local file path to save the bytes to (absolute path recommended; parent dirs auto-created)"},
                "overwrite": {"type": "boolean", "description": "If true, overwrite an existing file at save_to (default false)"},
            },
            "required": ["account", "message_id", "attachment_id", "save_to"],
        },
    },
    {
        "name": "gmail_send",
        "description": (
            "Send a new email. ALWAYS prompts the user with a modal confirmation "
            "dialog showing recipient/subject/body preview before sending. The user "
            "can deny — if so the tool returns 'user denied'. Use this for any "
            "outbound message; for safer review-then-send flows, use gmail_create_draft "
            "first and let the user inspect the draft. Supports optional file "
            "attachments (combined raw size up to ~20 MB; Gmail's hard ceiling is "
            "25 MB after base64 encoding)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Gmail account to send from"},
                "to": {"type": "string", "description": "Recipient email address (comma-separated for multiple)"},
                "subject": {"type": "string", "description": "Subject line"},
                "body": {"type": "string", "description": "Plain-text email body (always required even when sending HTML — used as the fallback for clients that don't render HTML)"},
                "body_html": {"type": "string", "description": "Optional HTML body. When provided, sends as multipart/alternative — clients render the HTML version, plain-text body is the fallback. Both should convey the same content."},
                "cc": {"type": "string", "description": "Optional CC recipients (comma-separated)"},
                "bcc": {"type": "string", "description": "Optional BCC recipients (comma-separated)"},
                "attachments": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional list of absolute file paths to attach. MIME type auto-detected from extension.",
                },
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "gmail_reply",
        "description": (
            "Reply to an existing message with PROPER GMAIL THREADING. Use this "
            "(not gmail_send) when replying to a message you've already fetched "
            "via gmail_search/gmail_read — it sets the In-Reply-To and References "
            "headers and passes the original's threadId so the reply nests inside "
            "the existing conversation in Gmail's UI. Defaults the To: to the "
            "original sender; pass an explicit 'to' to override (e.g., for "
            "replying to a list address rather than the original poster). "
            "Prepends 'Re: ' to the subject only if not already present. "
            "Requires confirmation; supports attachments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Gmail account to reply from"},
                "message_id": {"type": "string", "description": "ID of the message being replied to (from gmail_search/gmail_read)"},
                "body": {"type": "string", "description": "Plain-text reply body (always required even when sending HTML — used as fallback for non-HTML clients)"},
                "body_html": {"type": "string", "description": "Optional HTML reply body. When provided, sends as multipart/alternative."},
                "to": {"type": "string", "description": "Optional override of reply target (default: original sender)"},
                "cc": {"type": "string", "description": "Optional CC"},
                "bcc": {"type": "string", "description": "Optional BCC"},
                "attachments": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional file paths to attach",
                },
            },
            "required": ["account", "message_id", "body"],
        },
    },
    {
        "name": "gmail_create_draft",
        "description": (
            "Create a draft (does NOT send). No confirmation dialog — drafts are "
            "non-destructive. Useful for letting the user inspect a proposed email "
            "in Gmail's UI before authorising send_draft. Supports attachments "
            "(same ~20 MB combined limit as gmail_send)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Gmail account"},
                "to": {"type": "string", "description": "Recipient email address"},
                "subject": {"type": "string", "description": "Subject line"},
                "body": {"type": "string", "description": "Plain-text email body (always required; fallback for HTML)"},
                "body_html": {"type": "string", "description": "Optional HTML body. Sends as multipart/alternative when provided."},
                "cc": {"type": "string", "description": "Optional CC"},
                "bcc": {"type": "string", "description": "Optional BCC"},
                "attachments": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional file paths to attach",
                },
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "gmail_list_drafts",
        "description": "List recent drafts in an account with id, to, subject, snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "max_results": {"type": "integer", "description": "Maximum drafts (default 25, max 100)"},
            },
            "required": ["account"],
        },
    },
    {
        "name": "gmail_send_draft",
        "description": (
            "Send an existing draft by ID. Prompts the user with a modal "
            "confirmation showing the recipient and subject before sending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "draft_id": {"type": "string", "description": "Draft ID from gmail_list_drafts"},
            },
            "required": ["account", "draft_id"],
        },
    },
    {
        "name": "gmail_trash",
        "description": (
            "Move one or more messages to Trash (soft delete; recoverable from "
            "Gmail's UI for 30 days). Prompts the user with a modal confirmation "
            "showing the count and IDs before trashing. Pass message_ids as a list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Message IDs to trash",
                },
            },
            "required": ["account", "message_ids"],
        },
    },
    {
        "name": "gmail_untrash",
        "description": "Restore one or more messages from Trash back to the inbox/labels they had.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Message IDs to untrash",
                },
            },
            "required": ["account", "message_ids"],
        },
    },
    {
        "name": "gmail_list_labels",
        "description": "List all labels (system + user-created) for an account. Returns id, name, type.",
        "input_schema": {
            "type": "object",
            "properties": {"account": {"type": "string"}},
            "required": ["account"],
        },
    },
    {
        "name": "gmail_create_label",
        "description": (
            "Create a new user-defined label. Non-destructive — no confirmation. "
            "Returns the new label's id and name. Errors with 409 if a label with "
            "this name already exists in the account."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "name": {"type": "string", "description": "Label name (can be nested with '/' e.g. 'Work/Projects/Q1')"},
                "label_list_visibility": {
                    "type": "string", "enum": ["labelShow", "labelShowIfUnread", "labelHide"],
                    "description": "Visibility in the label list sidebar (default: labelShow)",
                },
                "message_list_visibility": {
                    "type": "string", "enum": ["show", "hide"],
                    "description": "Visibility of the label tag in message lists (default: show)",
                },
            },
            "required": ["account", "name"],
        },
    },
    {
        "name": "gmail_delete_label",
        "description": (
            "Delete a user label. DESTRUCTIVE — removes the label from EVERY "
            "message that has it (messages themselves are not deleted, but the "
            "labelling is gone permanently — recreating the label does not "
            "re-apply it to previously-labelled messages). Requires user "
            "confirmation via the standard dialog. System labels (INBOX, SENT, "
            "TRASH, STARRED, etc.) cannot be deleted; Gmail returns 400."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "label_id": {"type": "string", "description": "Label ID from gmail_list_labels"},
            },
            "required": ["account", "label_id"],
        },
    },
    {
        "name": "gmail_modify_labels",
        "description": (
            "Add and/or remove labels on one or more messages. Use label IDs from "
            "gmail_list_labels (system labels: INBOX, UNREAD, STARRED, IMPORTANT, "
            "TRASH, SPAM, SENT, DRAFT, CATEGORY_*)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_ids": {"type": "array", "items": {"type": "string"}},
                "add_labels": {"type": "array", "items": {"type": "string"}, "description": "Label IDs to add"},
                "remove_labels": {"type": "array", "items": {"type": "string"}, "description": "Label IDs to remove"},
            },
            "required": ["account", "message_ids"],
        },
    },
    {
        "name": "gmail_mark_read",
        "description": "Mark one or more messages as read (read=true) or unread (read=false).",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_ids": {"type": "array", "items": {"type": "string"}},
                "read": {"type": "boolean", "description": "true to mark read (removes UNREAD), false to mark unread"},
            },
            "required": ["account", "message_ids"],
        },
    },
    {
        "name": "gmail_list_threads",
        "description": (
            "List conversation threads matching a query. Useful when you want to "
            "operate on whole threads rather than individual messages. Returns "
            "thread_id, snippet, history_id."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "q": {"type": "string", "description": "Gmail search query"},
                "max_results": {"type": "integer", "description": "Maximum threads (default 25, max 500)"},
            },
            "required": ["account", "q"],
        },
    },
]

# Config paths for the Google integration. See myagent/gmail_mixin.py for OAuth flow.
GOOGLE_CONFIG_DIR = os.path.expanduser("~/.config/myagent-google")
GOOGLE_ACCOUNTS_FILE = os.path.join(GOOGLE_CONFIG_DIR, "accounts.json")

# Gmail tools whose destructive nature warrants a modal confirmation dialog by
# default. The PS Safety / Shell Safety dialog exposes one checkbox per entry
# here so the user can selectively bypass confirmations per-instruction. Bypass
# state is stored in the same `_disabled_confirm_patterns` set as the shell
# regex patterns — tool names and patterns coexist there as opaque strings,
# distinguished by which code path looks them up.
GMAIL_CONFIRM_TOOLS = [
    "gmail_send", "gmail_reply", "gmail_send_draft",
    "gmail_trash", "gmail_delete_label",
]

# ── Outlook / Microsoft 365 native tools (via Microsoft Graph) ───────────────
# Native MyAgent tools that wrap the Microsoft Graph mail API directly via MSAL
# OAuth (not MCP — see myagent/outlook_mixin.py for the rationale). The `account`
# parameter on every tool is a placeholder enum here; `_get_tools()` patches in
# the real enum at runtime from `~/.config/myagent-msmail/accounts.json`.
# Conditionally included in _get_tools() only when self.outlook_enabled.get() is
# True AND _HAS_OUTLOOK is True. Gmail "labels" map to Outlook "categories";
# trash maps to the Deleted Items folder.
OUTLOOK_TOOLS = [
    {
        "name": "outlook_search",
        "description": (
            "Search Outlook messages. With a query, uses Microsoft Graph "
            "$search (KQL-style free text, e.g. 'from:alice@example.com', "
            "'subject:invoice', 'hasAttachments:true', or just keywords) and "
            "returns matches by relevance. With no query, returns the most "
            "recent messages (newest first). Each result has id, conversationId, "
            "subject, from, to, date, snippet, hasAttachments, isRead. Use "
            "outlook_read on a specific id to get the full body."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Outlook account to search"},
                "q": {"type": "string", "description": "Graph $search query (optional; omit for most-recent)"},
                "max_results": {"type": "integer", "description": "Maximum results (default 25, max 250)"},
            },
            "required": ["account"],
        },
    },
    {
        "name": "outlook_read",
        "description": (
            "Fetch the full content of a single message by ID, including "
            "headers, body, snippet, categories, isRead, AND an attachments "
            "array (metadata only — always included). The format parameter "
            "controls the body representation: 'text' (default — plain text, "
            "or a structural HTML-to-text conversion if the message is HTML), "
            "'html' (raw HTML only — empty if message is text), or 'both' "
            "(body AND body_html as separate fields). Bodies truncate at "
            "50,000 chars with body_truncated / body_html_truncated flags. "
            "Each attachment entry has filename, mime_type, size, "
            "attachment_id, inline — use attachment_id with "
            "outlook_get_attachment to download the bytes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Outlook account"},
                "message_id": {"type": "string", "description": "Message ID (from outlook_search results)"},
                "format": {
                    "type": "string", "enum": ["text", "html", "both"],
                    "description": "Body representation to return (default 'text')",
                },
            },
            "required": ["account", "message_id"],
        },
    },
    {
        "name": "outlook_get_attachment",
        "description": (
            "Download a single file attachment from an Outlook message and save "
            "it to a local file path. First call outlook_read to get the "
            "attachments[] array, then pass an entry's attachment_id with "
            "message_id. Non-destructive. Refuses to overwrite an existing file "
            "unless overwrite=true. Only file attachments are supported (item "
            "and reference attachments error out)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_id": {"type": "string", "description": "Message ID containing the attachment"},
                "attachment_id": {"type": "string", "description": "Attachment ID from outlook_read's attachments[] field"},
                "save_to": {"type": "string", "description": "Local file path to save to (absolute recommended; parent dirs auto-created)"},
                "overwrite": {"type": "boolean", "description": "If true, overwrite an existing file at save_to (default false)"},
            },
            "required": ["account", "message_id", "attachment_id", "save_to"],
        },
    },
    {
        "name": "outlook_send",
        "description": (
            "Send a new email. ALWAYS prompts the user with a modal confirmation "
            "showing recipient/subject/body preview before sending. The user can "
            "deny — if so the tool returns 'user denied'. Supports optional file "
            "attachments (combined raw size up to ~3 MB; Graph's single-request "
            "limit is ~4 MB after base64). Unlike Gmail, Outlook has a single "
            "body — if body_html is given it is sent as the HTML body and the "
            "plain-text body is ignored."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Outlook account to send from"},
                "to": {"type": "string", "description": "Recipient email address(es), comma or semicolon separated"},
                "subject": {"type": "string", "description": "Subject line"},
                "body": {"type": "string", "description": "Plain-text email body (used when body_html is not provided)"},
                "body_html": {"type": "string", "description": "Optional HTML body. When provided, it is sent as the message body instead of the plain text."},
                "cc": {"type": "string", "description": "Optional CC recipients (comma/semicolon separated)"},
                "bcc": {"type": "string", "description": "Optional BCC recipients"},
                "attachments": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional list of absolute file paths to attach. MIME type auto-detected.",
                },
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "outlook_reply",
        "description": (
            "Reply to an existing message with PROPER OUTLOOK THREADING. Use "
            "this (not outlook_send) when replying to a message you've fetched "
            "via outlook_search/outlook_read — it uses Graph createReply so the "
            "reply nests in the same conversation. Defaults the To: to the "
            "original sender; pass an explicit 'to' to override. Sends only the "
            "new body (no quoted original), mirroring gmail_reply. Requires "
            "confirmation; supports attachments. On denial the draft is deleted."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Outlook account to reply from"},
                "message_id": {"type": "string", "description": "ID of the message being replied to"},
                "body": {"type": "string", "description": "Plain-text reply body (used when body_html is not provided)"},
                "body_html": {"type": "string", "description": "Optional HTML reply body (sent instead of plain text when provided)"},
                "to": {"type": "string", "description": "Optional override of reply target (default: original sender)"},
                "cc": {"type": "string", "description": "Optional CC"},
                "bcc": {"type": "string", "description": "Optional BCC"},
                "attachments": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional file paths to attach",
                },
            },
            "required": ["account", "message_id", "body"],
        },
    },
    {
        "name": "outlook_create_draft",
        "description": (
            "Create a draft (does NOT send). No confirmation dialog — drafts are "
            "non-destructive. Useful for letting the user inspect a proposed "
            "email in Outlook before authorising send_draft. Supports "
            "attachments (same ~3 MB combined limit as outlook_send)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "to": {"type": "string", "description": "Recipient email address(es)"},
                "subject": {"type": "string", "description": "Subject line"},
                "body": {"type": "string", "description": "Plain-text body (used when body_html is not provided)"},
                "body_html": {"type": "string", "description": "Optional HTML body (sent instead of plain text when provided)"},
                "cc": {"type": "string", "description": "Optional CC"},
                "bcc": {"type": "string", "description": "Optional BCC"},
                "attachments": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional file paths to attach",
                },
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "outlook_list_drafts",
        "description": "List recent drafts in an account with draft_id, to, subject, snippet.",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "max_results": {"type": "integer", "description": "Maximum drafts (default 25, max 100)"},
            },
            "required": ["account"],
        },
    },
    {
        "name": "outlook_send_draft",
        "description": (
            "Send an existing draft by its message ID (what outlook_create_draft "
            "and outlook_list_drafts return as draft_id). Prompts the user with a "
            "modal confirmation showing recipient and subject before sending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "draft_id": {"type": "string", "description": "Draft message ID from outlook_list_drafts / outlook_create_draft"},
            },
            "required": ["account", "draft_id"],
        },
    },
    {
        "name": "outlook_trash",
        "description": (
            "Move one or more messages to Deleted Items (soft delete; "
            "recoverable from Outlook's UI). Prompts the user with a modal "
            "confirmation showing the count and IDs. Pass message_ids as a list. "
            "Each move yields a new message id in Deleted Items, returned in "
            "moved_ids (pass those to outlook_untrash to restore)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Message IDs to move to Deleted Items",
                },
            },
            "required": ["account", "message_ids"],
        },
    },
    {
        "name": "outlook_untrash",
        "description": (
            "Restore one or more messages from Deleted Items back to the Inbox. "
            "Pass the IDs as they exist in Deleted Items (e.g. moved_ids from "
            "outlook_trash)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_ids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Message IDs (in Deleted Items) to restore to the Inbox",
                },
            },
            "required": ["account", "message_ids"],
        },
    },
    {
        "name": "outlook_list_labels",
        "description": (
            "List Outlook categories (the closest analogue to Gmail labels). "
            "Returns id, name (displayName), and color preset for each. Use the "
            "NAME with outlook_modify_labels to tag messages."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"account": {"type": "string"}},
            "required": ["account"],
        },
    },
    {
        "name": "outlook_create_label",
        "description": (
            "Create a new Outlook category (≈ a Gmail label). Non-destructive — "
            "no confirmation. color is a preset string preset0..preset24 (or "
            "'none'), default preset0. Errors if a category with this name "
            "already exists."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "name": {"type": "string", "description": "Category display name"},
                "color": {
                    "type": "string",
                    "description": "Color preset: preset0..preset24, or 'none' (default preset0)",
                },
            },
            "required": ["account", "name"],
        },
    },
    {
        "name": "outlook_delete_label",
        "description": (
            "Delete an Outlook category by ID. DESTRUCTIVE — requires "
            "confirmation. Removes the category from the master list; messages "
            "already carrying the NAME keep it until cleared via "
            "outlook_modify_labels. Get label_id from outlook_list_labels."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "label_id": {"type": "string", "description": "Category ID from outlook_list_labels"},
            },
            "required": ["account", "label_id"],
        },
    },
    {
        "name": "outlook_modify_labels",
        "description": (
            "Add and/or remove categories on one or more messages. IMPORTANT: "
            "Outlook stores categories on a message by display NAME (not id), so "
            "add_labels / remove_labels are category NAMES (the 'name' field from "
            "outlook_list_labels), NOT ids. Reads each message's current "
            "categories and applies the diff."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_ids": {"type": "array", "items": {"type": "string"}},
                "add_labels": {"type": "array", "items": {"type": "string"}, "description": "Category NAMES to add"},
                "remove_labels": {"type": "array", "items": {"type": "string"}, "description": "Category NAMES to remove"},
            },
            "required": ["account", "message_ids"],
        },
    },
    {
        "name": "outlook_mark_read",
        "description": "Mark one or more messages as read (read=true) or unread (read=false).",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "message_ids": {"type": "array", "items": {"type": "string"}},
                "read": {"type": "boolean", "description": "true to mark read, false to mark unread"},
            },
            "required": ["account", "message_ids"],
        },
    },
    {
        "name": "outlook_list_threads",
        "description": (
            "List conversations (Outlook's thread equivalent) matching a query. "
            "Messages are grouped by conversationId; the newest message in each "
            "conversation represents it. Returns conversation_id, subject, date, "
            "from, snippet. Omit q for the most recent conversations."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "q": {"type": "string", "description": "Graph $search query (optional)"},
                "max_results": {"type": "integer", "description": "Maximum threads (default 25, max 250)"},
            },
            "required": ["account"],
        },
    },
]

# Config paths for the Outlook integration. See myagent/outlook_mixin.py for the
# MSAL OAuth flow and Azure app-registration prerequisites.
OUTLOOK_CONFIG_DIR = os.path.expanduser("~/.config/myagent-msmail")
OUTLOOK_ACCOUNTS_FILE = os.path.join(OUTLOOK_CONFIG_DIR, "accounts.json")

# Outlook tools whose destructive nature warrants a modal confirmation by
# default. Exposed as checkboxes in the PS/Shell Safety dialog (per-instruction
# bypass), sharing the same `_disabled_confirm_patterns` set as Gmail/shell.
OUTLOOK_CONFIRM_TOOLS = [
    "outlook_send", "outlook_reply", "outlook_send_draft",
    "outlook_trash", "outlook_delete_label",
]

# ── Proton Mail native tools (via Proton Bridge) ─────────────────────────────
# Native MyAgent tools that wrap IMAP/SMTP against a locally-running Proton
# Bridge instance (see myagent/protonmail_mixin.py for the architecture).
# The `account` parameter on every tool is patched in at runtime by
# _get_tools() from ~/.config/myagent-protonmail/accounts.json so the model
# only ever sees actually-configured accounts. Conditionally included in
# _get_tools() only when self.proton_enabled.get() is True AND _HAS_PROTONMAIL
# is True. IMAP UIDs are per-folder, so every per-message tool takes a
# (folder, uid) pair — see the protonmail_mixin docstring for the rationale.
PROTON_TOOLS = [
    {
        "name": "proton_search",
        "description": (
            "Search Proton Mail messages within a folder using IMAP SEARCH "
            "syntax. Examples: 'UNSEEN', 'FROM \"alice@proton.me\"', "
            "'SUBJECT \"invoice\" SINCE 1-Jan-2026', 'BODY \"keyword\" LARGER 100000'. "
            "Combine predicates with spaces (implicit AND). Defaults to folder "
            "'INBOX'; pass folder='All Mail' to search across all folders. Returns "
            "uid, folder, subject, from, to, date, message_id_header for each "
            "match. Use proton_read with the (folder, uid) pair to get the full body. "
            "\n\nSERVER-SPECIFIC SEARCH NOTES — important to avoid wasted retries:"
            "\n- ALWAYS WRAP YOUR TOKEN IN AN IMAP SEARCH KEY like SUBJECT, BODY, "
            "TEXT, FROM, or TO. Dovecot/cPanel servers (e.g. WebCentral) REJECT "
            "bare tokens (q=\"foo\") with 'Unknown argument FOO' because the "
            "unprefixed token is interpreted as an unknown IMAP keyword. Proton "
            "Bridge accepts bare tokens as a loose full-text search, but the "
            "explicit-key form works on BOTH servers and is the safe default. "
            "Examples that work everywhere: 'SUBJECT \"invoice\"', "
            "'BODY \"WC_XACCT_a3f7c19d\"', 'TEXT \"alice\"', "
            "'FROM \"x@example.com\"'."
            "\n- Results are sorted NEWEST-FIRST and TRUNCATED to max_results. If "
            "you're hunting a specific older UID under a broad predicate like "
            "'SEEN' or 'FROM \"x\"', bump max_results to 100+ or add a SUBJECT "
            "substring to narrow."
            "\n- SUBJECT searches are reliable for SINGLE-WORD substrings (e.g. "
            "SUBJECT \"invoice\") but FRAGILE on Bridge for multi-word substrings "
            "against subjects containing non-ASCII characters (curly quotes, "
            "em-dashes, accented letters). For best results: use one word, or AND "
            "multiple SUBJECT predicates ('SUBJECT \"foo\" SUBJECT \"bar\"' rather "
            "than 'SUBJECT \"foo bar\"')."
            "\n- The tool transparently switches to CHARSET UTF-8 encoding when "
            "the query string contains non-ASCII characters, so you can pass them, "
            "but Bridge's index may still not match them against subject text — "
            "ASCII substrings are the safer bet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Proton account to search"},
                "q": {"type": "string", "description": "IMAP SEARCH query (default 'ALL')"},
                "folder": {"type": "string", "description": "Folder to search (default 'INBOX'; try 'All Mail' for everywhere)"},
                "max_results": {"type": "integer", "description": "Maximum results (default 25, max 500)"},
            },
            "required": ["account", "q"],
        },
    },
    {
        "name": "proton_read",
        "description": (
            "Fetch the full content of a single Proton message by (folder, uid), "
            "including headers, body, snippet, and an attachments array. Use the "
            "format parameter to control body representation: 'text' (default, "
            "plain-text body if present, otherwise a structural HTML-to-text "
            "conversion that drops <script>/<style> CONTENT, adds newlines at "
            "block-level tags like <p>/<br>/<div>/<h1-6>/<li>/<tr>, and decodes "
            "HTML entities like &amp;/&nbsp; — much cleaner than naive tag "
            "stripping on marketing emails), 'html' (raw HTML only — empty if "
            "message is text-only), or 'both' (returns body AND body_html as "
            "separate fields). Bodies are truncated at 50,000 chars with "
            "body_truncated / body_html_truncated flags. Always returns a "
            "'snippet' field — the first 200 chars of the cleaned text body with "
            "whitespace collapsed — for quick previews without rendering full body. "
            "Each attachment entry has filename, mime_type, size, attachment_id "
            "('part:N'), part_index, inline — pass attachment_id to "
            "proton_get_attachment to download bytes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Proton account"},
                "folder": {"type": "string", "description": "Folder containing the message (default 'INBOX')"},
                "uid": {"type": "string", "description": "IMAP UID (from proton_search results)"},
                "format": {
                    "type": "string", "enum": ["text", "html", "both"],
                    "description": "Body representation to return (default 'text')",
                },
            },
            "required": ["account", "uid"],
        },
    },
    {
        "name": "proton_get_attachment",
        "description": (
            "Download a single attachment from a Proton message to a local file "
            "path. First call proton_read on the message to get the attachments[] "
            "array; pick the entry you want and pass its attachment_id (format "
            "'part:N') along with folder + uid. Non-destructive — creates a "
            "local file, doesn't modify the mailbox. Refuses to overwrite an "
            "existing file unless overwrite=true."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "folder": {"type": "string", "description": "Folder containing the message"},
                "uid": {"type": "string"},
                "attachment_id": {"type": "string", "description": "From proton_read's attachments[]"},
                "save_to": {"type": "string", "description": "Local file path (absolute path recommended)"},
                "overwrite": {"type": "boolean", "description": "If true, overwrite existing file (default false)"},
            },
            "required": ["account", "uid", "attachment_id", "save_to"],
        },
    },
    {
        "name": "proton_send",
        "description": (
            "Send a new Proton Mail email via SMTP through Bridge. ALWAYS prompts "
            "the user with a modal confirmation dialog showing recipient/subject "
            "before sending. The user can deny — if so the tool returns 'user "
            "denied'. After sending, the message is APPEND'd to the 'Sent' folder "
            "so it shows up in Proton's UI. Supports optional file attachments "
            "(combined raw size up to ~20 MB)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string", "description": "Which Proton account to send from"},
                "to": {"type": "string", "description": "Recipient email address (comma-separated for multiple)"},
                "subject": {"type": "string", "description": "Subject line"},
                "body": {"type": "string", "description": "Plain-text email body (always required, used as HTML fallback)"},
                "body_html": {"type": "string", "description": "Optional HTML body — sends as multipart/alternative"},
                "cc": {"type": "string", "description": "Optional CC recipients (comma-separated)"},
                "bcc": {"type": "string", "description": "Optional BCC recipients (comma-separated)"},
                "attachments": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Optional list of absolute file paths to attach",
                },
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "proton_reply",
        "description": (
            "Reply to an existing Proton message with proper threading headers "
            "(In-Reply-To and References derived from the original). Defaults "
            "the To: to the original sender; pass an explicit 'to' to override. "
            "Prepends 'Re: ' to the subject only if not already present. "
            "Requires confirmation; supports attachments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "folder": {"type": "string", "description": "Folder of the message being replied to (default 'INBOX')"},
                "uid": {"type": "string", "description": "UID of the message being replied to"},
                "body": {"type": "string", "description": "Plain-text reply body"},
                "body_html": {"type": "string", "description": "Optional HTML reply body"},
                "to": {"type": "string", "description": "Optional override of reply target (default: original sender)"},
                "cc": {"type": "string"},
                "bcc": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["account", "uid", "body"],
        },
    },
    {
        "name": "proton_create_draft",
        "description": (
            "Create a Proton draft (APPEND to the Drafts folder). Does NOT send. "
            "No confirmation dialog — drafts are non-destructive. Useful for "
            "letting the user inspect a proposed email in Proton's UI before "
            "authorising proton_send_draft. Supports attachments."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "body_html": {"type": "string"},
                "cc": {"type": "string"},
                "bcc": {"type": "string"},
                "attachments": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["account", "to", "subject", "body"],
        },
    },
    {
        "name": "proton_list_drafts",
        "description": "List recent drafts in an account with uid, subject, to, snippet (alias for proton_search with folder='Drafts').",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "max_results": {"type": "integer", "description": "Maximum drafts (default 25, max 500)"},
            },
            "required": ["account"],
        },
    },
    {
        "name": "proton_send_draft",
        "description": (
            "Send an existing draft by UID. Pulls the draft from the Drafts "
            "folder, SMTP-sends it, APPENDs to Sent, then deletes from Drafts. "
            "Prompts the user with a modal confirmation showing recipient + "
            "subject before sending."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "uid": {"type": "string", "description": "UID of the draft in the Drafts folder"},
            },
            "required": ["account", "uid"],
        },
    },
    {
        "name": "proton_trash",
        "description": (
            "Move one or more messages to Trash (IMAP MOVE; soft delete — "
            "recoverable from Proton's UI until Trash is emptied). Prompts the "
            "user with a modal confirmation showing the count and folder before "
            "trashing. Operates within a single source folder per call — to "
            "trash messages from multiple folders, call once per folder."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "folder": {"type": "string", "description": "Source folder of the messages (default 'INBOX')"},
                "uids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "UIDs (within folder) to trash",
                },
            },
            "required": ["account", "uids"],
        },
    },
    {
        "name": "proton_untrash",
        "description": (
            "Restore messages from Trash to a target folder (default 'INBOX'). "
            "IMAP MOVE from Trash → to_folder."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "uids": {
                    "type": "array", "items": {"type": "string"},
                    "description": "UIDs in Trash to restore",
                },
                "to_folder": {"type": "string", "description": "Destination folder (default 'INBOX')"},
            },
            "required": ["account", "uids"],
        },
    },
    {
        "name": "proton_list_labels",
        "description": (
            "List all IMAP folders Bridge exposes for this account. Returns "
            "names and flags. Proton system folders include INBOX, Sent, Drafts, "
            "Trash, Archive, Spam, All Mail, Starred. User-created folders appear "
            "as 'Folders/<name>' and user-created labels as 'Labels/<name>'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"account": {"type": "string"}},
            "required": ["account"],
        },
    },
    {
        "name": "proton_create_label",
        "description": (
            "Create a new IMAP folder. Defaults to creating under 'Labels/' "
            "(Proton labels); pass parent='Folders' to create a Proton folder "
            "instead, or parent='' for a top-level name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "name": {"type": "string", "description": "Label/folder name (without the parent prefix)"},
                "parent": {
                    "type": "string", "enum": ["Labels", "Folders", ""],
                    "description": "Parent folder ('Labels' default; 'Folders' for a Proton folder; '' for top-level)",
                },
            },
            "required": ["account", "name"],
        },
    },
    {
        "name": "proton_delete_label",
        "description": (
            "Delete a Proton folder/label by full IMAP path (e.g. 'Labels/Work'). "
            "DESTRUCTIVE — removes the folder AND any messages stored only in it. "
            "Requires user confirmation via the standard dialog. System folders "
            "(INBOX, Sent, Trash, etc.) cannot be deleted; Bridge will reject."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "name": {"type": "string", "description": "Full folder path (from proton_list_labels)"},
            },
            "required": ["account", "name"],
        },
    },
    {
        "name": "proton_modify_labels",
        "description": (
            "Apply or move messages between Proton folders/labels. Bridge has "
            "ASYMMETRIC semantics depending on the destination — important to "
            "understand:\n"
            "\n"
            "• If 'add_to' starts with 'Labels/' (e.g. 'Labels/Work'): the "
            "operation is ADDITIVE. The label is added to the message but the "
            "message REMAINS in the source folder. Proton labels are tags, not "
            "containers — a message can have many labels and still live in INBOX. "
            "To remove a label, call this tool with 'folder' = the label "
            "(Labels/X) and 'add_to' = INBOX (or any folder); Bridge translates "
            "that as 'remove the X label'.\n"
            "\n"
            "• If 'add_to' is a system folder (INBOX, Sent, Drafts, Trash, "
            "Archive, Spam, All Mail) or 'Folders/<name>': the operation is a "
            "TRUE MOVE. The message is removed from the source folder and "
            "appears only in the destination. Proton folders are mutually "
            "exclusive — a message lives in exactly one folder at a time.\n"
            "\n"
            "After any move/label operation, the message gets a NEW per-folder "
            "UID in the destination — IMAP UIDs aren't preserved across moves. "
            "Use proton_search with a SUBJECT substring to find the new UID. "
            "To verify a label was added but the message stayed in INBOX, "
            "search INBOX after the call — the message should still be there.\n"
            "\n"
            "LABEL-REMOVAL AUTO-RETRY: When the source folder starts with "
            "'Labels/' (i.e. you're removing a label), Bridge sometimes leaves "
            "a transient new UID in the source after the initial MOVE due to "
            "eventual-consistency between its local cache and Proton's server. "
            "The tool transparently detects this and retries up to 2 times, "
            "MOVEing any unexpected new UIDs that appear after the primary "
            "operation. The response includes 'label_removal_retries': N so "
            "you can see whether retries fired (N=0 means clean first try; "
            "N>0 means Bridge's quirk triggered and was handled). You should "
            "NOT need to manually retry — that's done for you. If a single "
            "verifying search after the call still shows the message in "
            "Labels/X, treat it as a true failure (>2 retries needed is rare "
            "and likely indicates a Bridge state issue)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "folder": {"type": "string", "description": "Source folder (default 'INBOX')"},
                "uids": {"type": "array", "items": {"type": "string"}},
                "add_to": {"type": "string", "description": "Destination folder (full path)"},
            },
            "required": ["account", "uids", "add_to"],
        },
    },
    {
        "name": "proton_mark_read",
        "description": "Mark one or more messages as read (read=true sets \\Seen) or unread (read=false removes \\Seen).",
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "folder": {"type": "string", "description": "Folder containing the messages (default 'INBOX')"},
                "uids": {"type": "array", "items": {"type": "string"}},
                "read": {"type": "boolean", "description": "true → mark read; false → mark unread (default true)"},
            },
            "required": ["account", "uids"],
        },
    },
    {
        "name": "proton_list_threads",
        "description": (
            "List conversation threads matching an IMAP SEARCH query within a "
            "folder. Uses Bridge's IMAP THREAD REFERENCES if available, else "
            "falls back to singleton threads (one per matching message). "
            "Returns thread groups with the root subject, sender, and the UIDs "
            "in the thread (root first)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "account": {"type": "string"},
                "q": {"type": "string", "description": "IMAP SEARCH query (default 'ALL')"},
                "folder": {"type": "string", "description": "Folder to search (default 'INBOX')"},
                "max_results": {"type": "integer", "description": "Maximum threads (default 25, max 200)"},
            },
            "required": ["account", "q"],
        },
    },
]

# Config paths for the Proton Mail integration. See myagent/protonmail_mixin.py.
PROTON_CONFIG_DIR = os.path.expanduser("~/.config/myagent-protonmail")
PROTON_ACCOUNTS_FILE = os.path.join(PROTON_CONFIG_DIR, "accounts.json")

# Proton tools whose destructive nature warrants a modal confirmation dialog
# by default. Same Safety-dialog bypass semantics as GMAIL_CONFIRM_TOOLS.
PROTON_CONFIRM_TOOLS = [
    "proton_send", "proton_reply", "proton_send_draft",
    "proton_trash", "proton_delete_label",
]

class DatedPrice:
    """A pricing-table entry whose rate changes on a known calendar date —
    a launch promo that reverts to the sticker price. ``promo`` applies
    through ``until`` (a datetime.date, INCLUSIVE — the vendors phrase it
    "through December 31"), ``then`` from the next day. Resolved at lookup
    time, not import time, so a MyAgent left running across the boundary
    prices each call at the rate that call is actually billed at. Both
    tuples must have the same shape as the table's plain entries."""
    __slots__ = ("until", "promo", "then")

    def __init__(self, until, promo, then):
        self.until = until
        self.promo = promo
        self.then = then

    def resolve(self, today=None):
        today = today or datetime.date.today()
        return self.promo if today <= self.until else self.then

    def __repr__(self):
        return (f"DatedPrice(until={self.until!r}, promo={self.promo!r}, "
                f"then={self.then!r})")


def resolve_price(entry, today=None):
    """The concrete per-MTok tuple for a pricing-table entry: a DatedPrice
    picks promo-or-sticker by date, a plain tuple passes through. Every
    consumer of the tables must go through this (or a DatedPrice would leak
    into the arithmetic) — streaming_mixin._get_pricing does; ANTHROPIC_PRICING,
    which SelfBot unpacks directly, deliberately holds no DatedPrice."""
    if isinstance(entry, DatedPrice):
        return entry.resolve(today)
    return entry


# ── Anthropic API pricing (USD per million tokens) ────────────────────────────
# Each entry: (input_price, output_price, cache_write_price, cache_read_price)
# Prefixes are matched longest-first against model names. Re-verified against
# platform.claude.com/docs/en/about-claude/pricing on 2026-08-25 (every row
# matches). Entries here must stay plain 4-tuples: SelfBot imports this table
# and unpacks it directly, without the DatedPrice resolver GEMINI_PRICING uses.
ANTHROPIC_PRICING = {
    # (input, output, 5min_cache_write, cache_read) per million tokens
    # Retired generations (Claude 2.x/3.x/3.5, Opus 4.0/4.1, Sonnet 4.0) were
    # dropped in the 2026-07 audit — the API rejects their ids, so they can
    # never bill (Opus 4.1 retired 2026-08-05 and has left models.list()).
    # Claude 5 family (Mythos-class tier above Opus)
    "claude-fable-5":      (10.00, 50.00, 12.50, 1.00),
    "claude-mythos-5":     (10.00, 50.00, 12.50, 1.00),
    # Opus 5 — drop-in successor to Opus 4.8 at the same rates
    "claude-opus-5":       (5.00, 25.00, 6.25, 0.50),
    # Claude 4.5+ family (new lower pricing)
    "claude-opus-4-8":     (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-7":     (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-6":     (5.00, 25.00, 6.25, 0.50),
    "claude-opus-4-5":     (5.00, 25.00, 6.25, 0.50),
    # Sonnet 5 launched at a $3/$15 sticker with INTRO pricing $2/$10 through
    # 2026-08-31 — but the pricing page (checked 2026-08-25) now states the
    # $2/$10 rate "is now the standard price" and that the September 1
    # increase "will not occur", so this entry is permanent: do NOT flip it.
    "claude-sonnet-5":     (2.00, 10.00, 2.50, 0.20),
    "claude-sonnet-4-6":   (3.00, 15.00, 3.75, 0.30),
    "claude-sonnet-4-5":   (3.00, 15.00, 3.75, 0.30),
    # Haiku 4.5 (prefix also covers the dated claude-haiku-4-5-20251001 id)
    "claude-haiku-4":      (1.00, 5.00, 1.25, 0.10),
}

# OpenAI API pricing (USD per million tokens)
# Each entry: (input_price, output_price)
# Reasoning/thinking tokens are billed at output rate.
# Prefix-matched longest-first, same as Anthropic.
OPENAI_PRICING = {
    # (input, output, cached_input) per million tokens. OpenAI caches
    # AUTOMATICALLY above ~1024 tokens — no client opt-in (unlike Anthropic,
    # see anthropic_mixin's cache_control block) — so the discount was always
    # being applied to the BILL; before 2026-07-31 it just wasn't reflected
    # here, which overstated every OpenAI line in APICostLog.txt. The cached
    # rate is 1/10 of input across the GPT-5 families and 1/4 on GPT-4.1;
    # `None` means the model has no cached tier at all (the -pro tiers).
    # Base rates re-verified against the live Standard-tier table 2026-07-31
    # (developers.openai.com/api/docs/pricing) — eight entries were WRONG:
    # gpt-5.2, gpt-5.2-pro, gpt-5.1, gpt-4.1-mini, gpt-4.1-nano and codex-mini
    # sat at exactly HALF the standard rate (Batch-tier numbers), gpt-5.6-terra
    # carried gpt-5.4's numbers, and gpt-5.6-luna was 5x too high. Re-verified
    # 2026-08-25 (pricing table + the per-model pages): gpt-5.6-sol had been
    # entered at gpt-5.5's $5/$30 — it is $4/$20 (cached $0.40). The 5.5/5.6
    # tiers bill 2x input / 1.5x output above 272K input tokens; the table
    # keeps the ≤272K tier (same convention as gemini-3.1-pro / grok >200K).
    # GPT-5.6 family — no bare "gpt-5.6" id exists (only the three tiers), so
    # there is deliberately no family fallback row: an unknown future 5.6 id
    # gets no cost line rather than a wrong one.
    "gpt-5.6-sol":         (4.00, 20.00, 0.40),
    "gpt-5.6-terra":       (2.00, 12.00, 0.20),
    "gpt-5.6-luna":        (0.20, 1.20, 0.02),
    # GPT-5.5 family
    "gpt-5.5-pro":         (30.00, 180.00, None),
    "gpt-5.5":             (5.00, 30.00, 0.50),
    # GPT-5.4 family
    "gpt-5.4-pro":         (30.00, 180.00, None),
    "gpt-5.4-mini":        (0.75, 4.50, 0.075),
    "gpt-5.4-nano":        (0.20, 1.25, 0.02),
    "gpt-5.4":             (2.50, 15.00, 0.25),
    # GPT-5.3 family
    "gpt-5.3-chat":        (1.75, 14.00, 0.175),
    "gpt-5.3-codex":       (1.75, 14.00, 0.175),
    "gpt-5.3":             (1.75, 14.00, 0.175),
    # GPT-5.2 family
    "gpt-5.2-pro":         (21.00, 168.00, None),
    "gpt-5.2-chat":        (1.75, 14.00, 0.175),
    "gpt-5.2-codex":       (1.75, 14.00, 0.175),
    "gpt-5.2":             (1.75, 14.00, 0.175),
    # GPT-5.1 family (gpt-5.1-chat-latest and bare gpt-5.1-codex retired
    # 2026-07-23 → gpt-5.6-sol; the surviving -codex-max/-codex-mini ids
    # keep their own entries)
    "gpt-5.1-codex-mini":  (0.25, 2.00, 0.025),
    "gpt-5.1-codex-max":   (1.25, 10.00, 0.125),
    "gpt-5.1":             (1.25, 10.00, 0.125),
    # GPT-5.0 family — only gpt-5-pro survives (no announced retirement).
    # The bare gpt-5 / -mini / -nano tiers (retire 2026-12-11), the -chat /
    # -codex ids (retired 2026-07-23), the whole o-series (o1 / o1-pro /
    # o3-mini / o4-mini shut down 2026-10-23, o3 / o3-pro 2026-12-11), plus
    # the already-retired gpt-4o / gpt-4.5 / o1-mini were all unpriced in the
    # 2026-07 audit — retiring models are removed ahead of their shutdown
    # date, so a pinned instruction running one gets no cost line.
    "gpt-5-pro":           (15.00, 120.00, None),
    # GPT-4.1 family — still served (no announced API shutdown). Note the
    # cached discount here is 1/4, not the GPT-5 families' 1/10.
    "gpt-4.1-mini":        (0.40, 1.60, 0.10),
    "gpt-4.1-nano":        (0.10, 0.40, 0.025),
    "gpt-4.1":             (2.00, 8.00, 0.50),
    # Codex (no announced retirement) — listed as codex-mini-latest
    "codex-mini":          (1.50, 6.00, 0.375),
}

# Gemini API pricing (USD per million tokens)
# Each entry: (input_price, output_price, cached_input_price) — or a
# DatedPrice holding a promo tuple and the sticker tuple it reverts to.
# Note: Gemini has a free tier (under rate limits) — these are paid-tier prices.
# Gemini's IMPLICIT caching is automatic — no client opt-in — so the discount
# was always hitting the bill; before 2026-07-31 it just wasn't reflected here,
# which overstated every Google line in APICostLog.txt. Verified against the
# live table 2026-07-31 (ai.google.dev/gemini-api/docs/pricing): the cached
# rate is exactly 1/10 of input across the whole Gemini 3 family. Re-verified
# 2026-08-25: gemini-3.7-flash (new, stable) added, and Google is running a
# launch promo on BOTH 3.6 Flash and 3.7 Flash — $0.75/$3.75 (cached $0.075)
# "through December 31, 2026", $1.50/$7.50 ($0.15) "starting January 1,
# 2027" — modelled as a DatedPrice so the tracker flips itself on New Year's
# Day instead of overstating every Flash line by 2x until someone edits this.
# (Context-cache STORAGE, $1.00/M tokens/hour, is not modelled — it applies to
# EXPLICIT CachedContent objects, which MyAgent never creates.)
_GEMINI_FLASH_37_PROMO = DatedPrice(
    until=datetime.date(2026, 12, 31),
    promo=(0.75, 3.75, 0.075),
    then=(1.50, 7.50, 0.15),
)
GEMINI_PRICING = {
    # Floating "-latest" aliases that models.list() returns. The version sits
    # AFTER the tier word (gemini-pro-latest, not gemini-3.1-pro), so none of the
    # version-pinned prefixes below match them — without explicit entries they
    # get no cost line. Priced at the model each alias resolves to, verified
    # live via response.model_version on 2026-08-25: gemini-flash-latest moved
    # from 3.5-flash to 3.7-flash and gemini-flash-lite-latest from
    # 3.1-flash-lite to 3.5-flash-lite (pro-latest is still 3.1-pro-preview).
    # Re-verify the targets whenever a new Gemini tier ships.
    "gemini-pro-latest":        (2.00, 12.00, 0.20),    # -> gemini-3.1-pro-preview
    "gemini-flash-latest":      _GEMINI_FLASH_37_PROMO,  # -> gemini-3.7-flash
    "gemini-flash-lite-latest": (0.30, 2.50, 0.03),     # -> gemini-3.5-flash-lite
    # Gemini 3.7 family (added 2026-08-25 — until then it fell through to the
    # bare "gemini-3" entry at $0.50/$3.00). Same promo/sticker as 3.6.
    "gemini-3.7-flash":    _GEMINI_FLASH_37_PROMO,
    # Gemini 3.6 family (added 2026-07-31 at the $1.50/$7.50 sticker — the
    # bare "gemini-3" fallback had been pricing it at $0.50/$3.00; the
    # 2026-08-25 re-check found the sticker itself suspended by the promo)
    "gemini-3.6-flash":    _GEMINI_FLASH_37_PROMO,
    # Gemini 3.5 family (-lite is a LONGER prefix, so it must be listed for
    # gemini-3.5-flash-lite not to match the pricier gemini-3.5-flash entry).
    # The pricing page lists no context-caching rate for 3.5 Flash-Lite; the
    # 1/10 slot is kept rather than None so that, should cached tokens ever be
    # reported, they are priced by the family rule instead of silently free.
    "gemini-3.5-flash-lite": (0.30, 2.50, 0.03),
    "gemini-3.5-flash":    (1.50, 9.00, 0.15),
    # Gemini 3.1 family  (3.1-pro doubles input above 200k tokens — the table
    # keeps the ≤200k tier, matching the 2.5-pro entry's convention)
    "gemini-3.1-flash-lite": (0.25, 1.50, 0.025),
    "gemini-3.1-pro":      (2.00, 12.00, 0.20),
    # Gemini 3 family
    "gemini-3-pro-image":  (2.00, 12.00, 0.20),
    "gemini-3-pro":        (2.00, 12.00, 0.20),
    "gemini-3-flash":      (0.50, 3.00, 0.05),
    "gemini-3":            (0.50, 3.00, 0.05),
    # Gemini 2.5 (sunset ≥ 2026-10-16) was unpriced in the 2026-07 audit —
    # retiring models are removed ahead of their shutdown date.
}
# xAI API pricing (USD per million tokens)
# Each entry: (input_price, output_price) — reasoning tokens bill as output.
# Verified LIVE 2026-07-17 (re-verified 2026-08-18 and 2026-08-25) against /v1/models' own
# price fields (unit = $1/10000 per MTok: grok-4.3 reports 12500/25000 =
# $1.25/$2.50, grok-4.5 and grok-4.6 both report 20000/60000 = $2/$6). The
# legacy families (grok-4 / -fast, grok-3, grok-2) are fully retired — the
# API no longer serves them, and unknown ids are rejected, so they can never
# bill. grok-code-fast survives only as an ALIAS of grok-build-0.1 at
# grok-build's price. Cached input bills at $0.20/M ($0.30/M for grok-4.5,
# $0.50/M for grok-4.6) — not tracked, the 2-tuple treats all input at full
# rate, a slight overestimate; input above 200K tokens bills double (table
# keeps the ≤200K tier, same convention as gemini-3.1-pro). The floating
# grok-latest alias moved from grok-4.3 to grok-4.6 with the 2026-08
# catalog — its row tracks the current target (the table is only the
# fallback: xAI's per-call cost_in_usd_ticks is authoritative regardless).
XAI_PRICING = {
    "grok-4.3":          (1.25, 2.50),
    "grok-4.5":          (2.00, 6.00),
    "grok-4.6":          (2.00, 6.00),   # 2026-08 flagship; cached $0.50/M, x2 above 200K
    "grok-4.20":         (1.25, 2.50),   # covers all three 4.20 variants + bare alias
    "grok-build-latest": (2.00, 6.00),   # re-aliased to grok-4.5 in the 2026-07 catalog
    "grok-build":        (1.00, 2.00),
    "grok-code-fast":    (1.00, 2.00),   # alias of grok-build-0.1
    "grok-latest":       (2.00, 6.00),   # alias of grok-4.6 since 2026-08 (was grok-4.3)
}
# Moonshot AI (Kimi) pricing (USD per million tokens)
# Each entry: (input_price, output_price) — reasoning tokens bill as output,
# and re-sent reasoning_content (the required round-trip on thinking models)
# bills again as input. Verified against platform.kimi.ai/docs/pricing
# 2026-07-25, re-verified 2026-08-25 against the per-model pages
# (pricing/chat-k3 / -k27-code / -k26: unchanged). The 2-tuple treats all
# input at the cache-MISS rate; the mixin computes an exact cost_usd from the
# reported cached tokens using KIMI_CACHE_HIT_PRICING below (stream_worker
# prefers cost_usd when present). Unpriced under the retiring-models policy:
# the legacy dash-family (kimi-k2-thinking / -0905 / -0711 / -turbo,
# discontinued 2026-05-25), moonshot-v1 (dies 2026-08-31), and kimi-k2.5
# (full platform sunset 2026-08-31 per the models page) — none of them is
# served by _fetch_kimi_models.
KIMI_PRICING = {
    "kimi-k3":                    (3.00, 15.00),
    "kimi-k2.7-code-highspeed":   (1.90, 8.00),
    "kimi-k2.7-code":             (0.95, 4.00),
    "kimi-k2.6":                  (0.95, 4.00),
}
# Cache-HIT input rate (USD per million tokens), used only by kimi_mixin's
# exact-cost computation. Longest prefix wins (so -highspeed outranks
# kimi-k2.7-code, same convention as every other table).
KIMI_CACHE_HIT_PRICING = {
    "kimi-k3":                    0.30,
    "kimi-k2.7-code-highspeed":   0.38,
    "kimi-k2.7-code":             0.19,
    "kimi-k2.6":                  0.16,
}
# Local inference is free — empty table makes _get_pricing return None and the
# cost line is silently skipped by the accumulator.
OLLAMA_PRICING = {}
PROVIDERS = ["Anthropic", "OpenAI", "Google", "xAI", "Moonshot", "Ollama"]
DEFAULT_GEOMETRY = "1050x930"
MONO_FONT = "Consolas" if IS_WINDOWS else "Menlo"
_SUBPROCESS_NOWND = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}

# _BASE_DIR points to the project root (parent of the myagent/ package)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The two authored-content stores live in <OneDrive>/MyAgent when a OneDrive
# client is present (one copy follows the user across machines — OneDrive, not
# git, is the sync channel; see myagent/datapaths.py), falling back to the
# repo root on solo machines. State files below stay per-machine at the root.
from myagent.datapaths import resolve_store, resolve_costlog, resolve_skills_dir  # noqa: E402  (needs os already imported)
INSTRUCTIONS_FILE = resolve_store("agent_instructions.json")
CHATS_DIR = os.path.join(_BASE_DIR, "saved_chats")
AGENT_STATE_FILE = os.path.join(_BASE_DIR, "agent_state.json")  # instance 1 default
AGENT_LOCK_PREFIX = os.path.join(_BASE_DIR, "agent_lock_")
SKILLS_DIR = resolve_skills_dir()  # per-skill SKILL.md tree; a legacy skills.json migrates in on first load
STORES_SYNCED = os.path.dirname(INSTRUCTIONS_FILE) != _BASE_DIR  # shared dir in use
# Per-run cost log: APICostLog_<machine>.txt in the OneDrive share (one file
# per machine — appends never conflict, yet every machine's spend syncs
# everywhere and the Cost Log viewers total ALL of them). Repo-root
# APICostLog.txt fallback on solo machines; both locations gitignored.
APICOST_LOG_FILE = resolve_costlog()
APICOST_LOG_MAX_BYTES = 100_000  # one-slot rotation cap (helpers.rotate_log_if_needed), same as heartbeat.log's

DEFAULT_SYSTEM_PROMPT = (
    "You are an autonomous AI agent with access to a rich set of tools. "
    "Your task is given in the first user message — execute it fully and proactively.\n\n"

    "GUIDELINES:\n"
    "• Execute the task autonomously — chain tools together without hesitation.\n"
    "• When multiple tools can achieve a goal, chain them together without asking.\n"
    "• For desktop automation: take a screenshot first, then act on what you see. Re-screenshot after any UI change before clicking again.\n"
    "• For browser tasks: use browser tools (not desktop tools) for precision.\n"
    "• CRITICAL: If you need user input, confirmation, or action, or your output ends with a question — you MUST call the user_prompt tool. "
    "NEVER just output text and stop. Outputting a question or request as plain text ends your turn "
    "and the user has no way to respond. The ONLY way to get a reply from the user is user_prompt.\n"
    "• If you genuinely can't complete a step, explain what went wrong.\n"
    "• When the task is complete, summarise what you did."
)

DEFAULT_INSTRUCTION = (
    "Search the web for today's top 3 technology news headlines and summarise them."
)
