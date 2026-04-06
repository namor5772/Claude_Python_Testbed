import sys
import os
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
    from PIL import Image, ImageGrab
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
            "IMPORTANT: When launching GUI applications, use Start-Process (Windows) or 'open -a' (macOS) "
            "so the command returns immediately instead of blocking."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The command to execute",
                }
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
        "name": "user_prompt",
        "description": (
            "Pause execution and display a message to the user, then wait for their "
            "response. You MUST use this tool whenever you need the user to do something "
            "(e.g., log into a website, approve something, make a choice) or when you need "
            "information only the user can provide. NEVER just output text asking the user "
            "something — that ends your turn and they cannot reply. This tool is the ONLY "
            "way to communicate with the user and receive a response."
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
                "meta": {
                    "type": "boolean",
                    "description": "Enable meta tools (default false on create)",
                },
                "provider": {
                    "type": "string",
                    "enum": ["Anthropic", "OpenAI", "Gemini"],
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
                    "enum": ["low", "medium", "high", "max"],
                    "description": "Thinking effort level (optional for update; create inherits current). 'max' only valid for Anthropic Opus.",
                },
                "thinking_budget": {
                    "type": "integer",
                    "description": "Thinking token budget (optional for update; create inherits current)",
                },
                "thinking_mode": {
                    "type": "string",
                    "enum": ["off", "adaptive_low", "adaptive_medium", "adaptive_high", "adaptive_max"],
                    "description": "Anthropic adaptive thinking mode (optional for update; create inherits current)",
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
                    "description": "Skill name (required for all except list)",
                },
                "content": {
                    "type": "string",
                    "description": "Skill text content (required for create, optional for update)",
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
            "The child process runs independently and returns immediately. "
            "Use manage_instructions(action='list') first to see available names."
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
                    "description": "Run without a GUI window (default true). Set false to show the agent window.",
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
                    "description": "Which display to capture (0 = primary, 1 = secondary, etc.). Default: 0 (primary). Use this to see and interact with windows on different monitors.",
                },
                "x": {"type": "integer", "description": "Left edge of region to capture (use coordinates from the screenshot image)"},
                "y": {"type": "integer", "description": "Top edge of region to capture (use coordinates from the screenshot image)"},
                "width": {"type": "integer", "description": "Width of region to capture"},
                "height": {"type": "integer", "description": "Height of region to capture"},
            },
            "required": [],
        },
    },
    {
        "name": "mouse_click",
        "description": (
            "Click the mouse at the given (x, y) position. Take a screenshot first to identify "
            "the correct coordinates — use pixel positions as seen in the screenshot image. "
            "Coordinates are automatically mapped to the actual screen. "
            "Supports left/right/middle button and single/double click."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "X coordinate to click"},
                "y": {"type": "integer", "description": "Y coordinate to click"},
                "button": {
                    "type": "string", "enum": ["left", "right", "middle"],
                    "description": "Mouse button (default: left)",
                },
                "clicks": {
                    "type": "integer", "enum": [1, 2],
                    "description": "Number of clicks: 1=single, 2=double (default: 1)",
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
            "Optionally specify (x, y) to scroll at a specific position."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "clicks": {
                    "type": "integer",
                    "description": "Scroll amount: positive=up, negative=down",
                },
                "x": {"type": "integer", "description": "X coordinate to scroll at (optional)"},
                "y": {"type": "integer", "description": "Y coordinate to scroll at (optional)"},
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
                "x": {"type": "integer", "description": "Left edge of region"},
                "y": {"type": "integer", "description": "Top edge of region"},
                "width": {"type": "integer", "description": "Width of region"},
                "height": {"type": "integer", "description": "Height of region"},
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
                "start_x": {"type": "integer", "description": "Starting X coordinate"},
                "start_y": {"type": "integer", "description": "Starting Y coordinate"},
                "end_x": {"type": "integer", "description": "Ending X coordinate"},
                "end_y": {"type": "integer", "description": "Ending Y coordinate"},
                "duration": {
                    "type": "number",
                    "description": "Duration of drag in seconds (default: 0.5)",
                },
                "button": {
                    "type": "string",
                    "description": "Mouse button: 'left', 'right', or 'middle' (default: 'left')",
                },
            },
            "required": ["start_x", "start_y", "end_x", "end_y"],
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
        r"\b-Recurse\b",
        r"\b-Force\b",
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
    "claude-sonnet-4-5-20250929",
    "claude-opus-4-6",
    "claude-haiku-4-5-20251001",
]
DEFAULT_MODEL = FALLBACK_MODELS[0]
MAX_TOKENS = 8192
MAX_TOKENS_THINKING = 32768
# Models with lower max output token limits than MAX_TOKENS
MODEL_MAX_OUTPUT_TOKENS = {
    "claude-3-haiku-20240307": 4096,
    "claude-3-opus-20240229": 4096,
    "claude-3-sonnet-20240229": 4096,
}
ADAPTIVE_THINKING_MODELS = {"claude-opus-4-6", "claude-sonnet-4-6"}
MANUAL_THINKING_PREFIXES = ("claude-3-5-sonnet", "claude-sonnet-4-5", "claude-haiku-4-5", "claude-opus-4-5")
EFFORT_LEVELS = ["low", "medium", "high", "max"]
ADAPTIVE_MODE_VALUES = ["Off", "Adaptive", "Low", "Medium", "High", "Max"]
ADAPTIVE_MODE_VALUES_NO_MAX = ["Off", "Adaptive", "Low", "Medium", "High"]
BUDGET_PRESETS = {"1K": 1024, "4K": 4096, "8K": 8192, "16K": 16384, "32K": 32768}
OPENAI_FALLBACK_MODELS = ["gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4.1-mini", "o4-mini"]
OPENAI_DEFAULT_MODEL = OPENAI_FALLBACK_MODELS[0]
OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")
# Model families that support the Responses API (gpt-3.5, gpt-4 base/turbo do not)
OPENAI_RESPONSES_PREFIXES = ("gpt-4o", "gpt-4.1", "gpt-4.5", "gpt-5",
                             "o1", "o3", "o4")
GEMINI_FALLBACK_MODELS = ["gemini-2.5-flash", "gemini-2.5-pro"]
GEMINI_DEFAULT_MODEL = GEMINI_FALLBACK_MODELS[0]
GEMINI_THINKING_PREFIXES = ("gemini-2.5", "gemini-3",)
PARALLEL_SAFE_TOOLS = {"web_search", "fetch_webpage", "csv_search", "get_skill"}
PROVIDERS = ["Anthropic", "OpenAI", "Gemini"]
DEFAULT_GEOMETRY = "1050x930"
MONO_FONT = "Consolas" if IS_WINDOWS else "Menlo"
_SUBPROCESS_NOWND = {"creationflags": subprocess.CREATE_NO_WINDOW} if IS_WINDOWS else {}

# _BASE_DIR points to the project root (parent of the myagent/ package)
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTRUCTIONS_FILE = os.path.join(_BASE_DIR, "agent_instructions.json")
CHATS_DIR = os.path.join(_BASE_DIR, "saved_chats")
AGENT_STATE_FILE = os.path.join(_BASE_DIR, "agent_state.json")  # instance 1 default
AGENT_LOCK_PREFIX = os.path.join(_BASE_DIR, "agent_lock_")
SKILLS_FILE = os.path.join(_BASE_DIR, "skills.json")

DEFAULT_SYSTEM_PROMPT = (
    "You are an autonomous AI agent with access to a rich set of tools. "
    "Your task is given in the first user message — execute it fully and proactively.\n\n"

    "GUIDELINES:\n"
    "• Execute the task autonomously — chain tools together without hesitation.\n"
    "• When multiple tools can achieve a goal, chain them together without asking.\n"
    "• For desktop automation: screenshot first, then act on what you see.\n"
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
